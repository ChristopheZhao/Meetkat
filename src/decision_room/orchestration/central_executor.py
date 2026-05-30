"""Centralized MAS executor — real LLM supervisor + LLM specialists.

This executor produces RoomRound outputs by:

1. Calling ``LLMSupervisor.plan_round`` to choose specialists and emit one
   assignment contract per role.
2. Calling the existing ``LLMRoomExecutor`` specialist machinery
   (``_generate_argument`` / ``_generate_synthesis``) for each contract so
   the same provider, routing, retry, and JSON-parsing rules apply to both
   topologies.
3. Reusing ``HybridConsensusStrategy`` for convergence — convergence stays
   signal-gated, never a round counter.

If the provider env is missing the factory returns an ``UnavailableRoomExecutor``
just like ``LLMRoomExecutor.from_mapping``; the centralized topology never
silently emits stub output as a default.
"""

from __future__ import annotations

import os
from typing import Any, Mapping

from decision_room.mas.hybrid import (
    HybridConsensusStrategy,
    HybridCoordinationStrategy,
    HybridPlanningStrategy,
)
from decision_room.mas.types import (
    ActionType,
    CoordinationAction,
    DecisionContext,
    MeetingPhase,
)
from decision_room.memory import (
    LongTermLessonStore,
    RoomMemoryStore,
    default_long_term_store,
    default_room_memory_store,
)
from decision_room.policies.fallback import DisasterOnlyFallbackPolicy
from decision_room.policies.routing_control import RoutingControlPolicy
from decision_room.providers import ProviderRegistry
from decision_room.routing.model_router import HybridModelRouter, RouterTargets

from .central_mas import (
    LLMSupervisor,
    SpeakerSlot,
    SupervisorPlan,
    build_supervisor_state,
    central_mas_artifact_bundle,
    supervisor_plan_to_host_agenda,
)
from .pre_room_planning import resolve_turn_specialists
from .room_executor import (
    LLMRoomExecutor,
    RoomExecutor,
    RoomMessage,
    RoomRound,
    SpecialistTurnResult,
    UnavailableRoomExecutor,
    _dedupe_strings,
    _env,
    _env_int_optional,
    _filter_answered_questions,
    _provider_config,
    _route_artifact,
    _target,
    _visible_open_questions,
    phase_for_round,
    resolve_focus,
    resolve_round_end_signal,
    signals_after_round,
    signals_for_round,
)


class CentralizedMASExecutor:
    """LLM-driven centralized supervisor executor."""

    def __init__(
        self,
        registry: ProviderRegistry,
        router: HybridModelRouter,
        *,
        supervisor: LLMSupervisor | None = None,
        coordination: HybridCoordinationStrategy | None = None,
        consensus: HybridConsensusStrategy | None = None,
        planner: HybridPlanningStrategy | None = None,
        use_background_threads: bool = True,
        transient_max_attempts: int = 2,
        room_memory_store: RoomMemoryStore | None = None,
        long_term_store: LongTermLessonStore | None = None,
    ) -> None:
        self._registry = registry
        self._router = router
        # ``supervisor`` is a back-compat injection point — callers that
        # still construct an LLMSupervisor directly can pass it in. Otherwise
        # the SupervisorAgent below owns plan_round + persistence uniformly.
        self._legacy_supervisor = supervisor
        self._coordination = coordination or HybridCoordinationStrategy()
        self._consensus = consensus or HybridConsensusStrategy()
        self._planner = planner or HybridPlanningStrategy()
        # Phase 2: specialist + synthesis run as Agent instances.
        # Phase 3: supervisor joins them so plan persistence + recall live
        # inside the agent rather than inline on the executor. Memory
        # stores are shared so every agent's recall block sees the same
        # state as the orchestrator-side preflight code.
        from decision_room.agents import (
            SpecialistAgent,
            SupervisorAgent,
            SynthesisAgent,
        )

        self._room_memory = room_memory_store or default_room_memory_store()
        self._long_term = long_term_store or default_long_term_store()
        agent_kwargs = dict(
            registry=registry,
            router=router,
            use_background_threads=use_background_threads,
            transient_max_attempts=transient_max_attempts,
            room_memory_store=self._room_memory,
            long_term_store=self._long_term,
        )
        self._specialist_agent = SpecialistAgent(**agent_kwargs)
        self._synthesis_agent = SynthesisAgent(**agent_kwargs)
        self._supervisor_agent = SupervisorAgent(**agent_kwargs)
        self._transient_max_attempts = max(1, transient_max_attempts)

    @classmethod
    def from_env(cls) -> RoomExecutor:
        return cls.from_mapping(os.environ)

    @classmethod
    def from_mapping(cls, env: Mapping[str, str]) -> RoomExecutor:
        try:
            default_supplier = _env("MODEL_DEFAULT_SUPPLIER", env)
            default_model = _env("MODEL_DEFAULT_MODEL", env)
            escalation_supplier = _env("MODEL_ESCALATION_SUPPLIER", env)
            escalation_model = _env("MODEL_ESCALATION_MODEL", env)
            fallback_supplier = _env("MODEL_FALLBACK_SUPPLIER", env)
            fallback_model = _env("MODEL_FALLBACK_MODEL", env)
            supplier_ids = {default_supplier, escalation_supplier, fallback_supplier}
            registry = ProviderRegistry.from_openai_compatible_configs(
                {supplier: _provider_config(supplier, env) for supplier in supplier_ids}
            )
            router = HybridModelRouter(
                RoutingControlPolicy(DisasterOnlyFallbackPolicy()),
                targets=RouterTargets(
                    default_target=_target(default_supplier, default_model),
                    escalation_target=_target(escalation_supplier, escalation_model),
                    disaster_fallback_target=_target(fallback_supplier, fallback_model),
                ),
            )
        except Exception as exc:
            return UnavailableRoomExecutor(str(exc))
        return cls(
            registry=registry,
            router=router,
            transient_max_attempts=_env_int_optional(
                "MODEL_REQUEST_MAX_ATTEMPTS", 2, env
            ),
        )

    async def build_round(
        self,
        snapshot: Any,
        round_index: int,
        *,
        publish: Any = None,
    ) -> RoomRound:
        from decision_room.agents import TurnContext

        phase = phase_for_round(snapshot, round_index)
        routing_signals = signals_for_round(snapshot, round_index)
        topic = snapshot.topic or "centralized MAS decision room"
        ctx = DecisionContext(
            room_id=snapshot.room_id,
            phase=phase,
            signals=routing_signals,
            metadata={"topic": topic, "topology": "centralized_supervisor"},
        )
        plan_decision = self._planner.plan(ctx)
        next_focus = resolve_focus(snapshot, plan_decision.next_focus)
        route_ctx = ctx
        route = self._router.route(route_ctx)

        if self._legacy_supervisor is not None:
            # Back-compat path: a caller injected a custom LLMSupervisor.
            # Drive it directly but still let SupervisorAgent's persistence
            # cover the journal write so behavior stays uniform.
            supervisor_plan, route_ctx, route = await self._legacy_supervisor.plan_round(
                snapshot=snapshot,
                round_index=round_index,
                phase=phase,
                next_focus=next_focus,
                route_ctx=route_ctx,
                route=route,
            )
            await self._supervisor_agent._persist_plan(  # noqa: SLF001
                publish=publish,
                room_id=snapshot.room_id,
                round_index=round_index,
                plan=supervisor_plan,
            )
        else:
            supervisor_ctx = TurnContext(
                snapshot=snapshot,
                round_index=round_index,
                phase=phase,
                next_focus=next_focus,
                route_ctx=route_ctx,
                route=route,
                publish=publish,
            )
            supervisor_result = await self._supervisor_agent.run(supervisor_ctx)
            supervisor_plan = supervisor_result.plan
            route_ctx = supervisor_result.ctx
            route = supervisor_result.route
        host_agenda = supervisor_plan_to_host_agenda(supervisor_plan)
        runnable = supervisor_plan.runnable_speakers()
        if not runnable:
            raise RuntimeError("supervisor returned no runnable speaker slots")
        specialist_pairs = _resolve_speaker_specialists(snapshot, runnable)
        if not specialist_pairs:
            raise RuntimeError(
                "supervisor speakers did not resolve to any planned specialist"
            )
        coordination = self._coordination_for_speakers(
            ctx, specialist_pairs, snapshot=snapshot
        )

        host_message = self._build_supervisor_message(
            snapshot=snapshot,
            phase=phase,
            round_index=round_index,
            next_focus=next_focus,
            route=route,
            plan=supervisor_plan,
            target_roles=[specialist.role for specialist, _slot in specialist_pairs],
        )

        turn_results: list[SpecialistTurnResult] = []
        target_claim_ref = ""
        room_id = snapshot.room_id
        for specialist, slot in specialist_pairs:
            # Phase 3: the SpecialistAgent now reads its own recall and
            # persists its own claim through memory.write events. The
            # orchestrator no longer reaches into the memory store directly.
            specialist_ctx = TurnContext(
                snapshot=snapshot,
                round_index=round_index,
                phase=phase,
                next_focus=next_focus,
                route_ctx=route_ctx,
                route=route,
                publish=publish,
                extras={
                    "specialist": specialist,
                    "turn_task": slot.focus_angle,
                    "host_agenda": host_agenda,
                    "target_claim_ref": target_claim_ref,
                },
            )
            specialist_result = await self._specialist_agent.run(specialist_ctx)
            route_ctx = specialist_result.ctx
            route = specialist_result.route
            output = specialist_result.output
            turn_results.append(
                SpecialistTurnResult(
                    specialist=specialist,
                    task=slot.focus_angle,
                    output=output,
                    route=route,
                )
            )
            target_claim_ref = output.claim

        synthesis_ctx = TurnContext(
            snapshot=snapshot,
            round_index=round_index,
            phase=phase,
            next_focus=next_focus,
            route_ctx=route_ctx,
            route=route,
            publish=publish,
            extras={"host_agenda": host_agenda, "turn_results": turn_results},
        )
        synthesis_result = await self._synthesis_agent.run(synthesis_ctx)
        route_ctx = synthesis_result.ctx
        synthesis_route = synthesis_result.route
        synthesis_output = synthesis_result.output

        open_questions = _dedupe_strings(
            [
                *_visible_open_questions(snapshot),
                *host_agenda.open_questions,
                *_filter_answered_questions(snapshot, synthesis_output.open_questions),
            ],
            limit=6,
        )
        post_signals = signals_after_round(
            snapshot=snapshot,
            round_index=round_index,
            open_questions=open_questions,
            synthesis_output=synthesis_output,
        )
        consensus = self._consensus.evaluate(
            DecisionContext(
                room_id=snapshot.room_id,
                phase=phase,
                signals=post_signals,
                metadata={"topic": topic, "topology": "centralized_supervisor"},
            )
        )

        messages: list[RoomMessage] = [host_message]
        for turn_index, turn_result in enumerate(turn_results, start=1):
            slot = runnable[turn_index - 1]
            messages.append(
                RoomMessage(
                    role=turn_result.specialist.role,
                    title=turn_result.output.title,
                    text=turn_result.output.text,
                    artifacts={
                        "claim": turn_result.output.claim,
                        "evidence": turn_result.output.evidence,
                        "confidence": turn_result.output.confidence,
                        "target_claim_ref": turn_result.output.target_claim_ref,
                        "specialist_display_name": turn_result.specialist.display_name,
                        "capability_profile": turn_result.specialist.capability_profile,
                        "focus_angle": turn_result.task,
                        "turn_index": turn_index,
                        "route": _route_artifact(turn_result.route),
                        "speaker_slot": slot.to_payload(),
                    },
                )
            )
        synthesis_message = RoomMessage(
            role="synthesis",
            title=synthesis_output.title,
            text=synthesis_output.text,
            artifacts={
                "agreement": synthesis_output.agreement,
                "disagreement": synthesis_output.disagreement,
                "open_questions": open_questions,
                "decision_candidate": synthesis_output.decision_candidate,
                "action_item_draft": synthesis_output.action_item_draft,
                "conclusion_type": synthesis_output.conclusion_type,
                "conclusion_reason": synthesis_output.conclusion_reason,
                "recommended_next_phase": synthesis_output.recommended_next_phase,
                "recommended_next_action": synthesis_output.recommended_next_action,
                "route": _route_artifact(synthesis_route),
                "central_mas_state_ref": "host.artifacts.central_mas.supervisor_state",
            },
        )
        summary_text = (
            f"{synthesis_output.text} Conclusion: {synthesis_output.conclusion_type}. "
            f"{synthesis_output.conclusion_reason} Consensus score {consensus.score:.2f}. "
            f"{consensus.reason}."
        )
        should_end, end_reason = resolve_round_end_signal(
            snapshot=snapshot,
            round_index=round_index,
            synthesis_output=synthesis_output,
            consensus=consensus,
        )
        return RoomRound(
            phase=phase,
            signals=post_signals,
            plan_topic=plan_decision.topic,
            next_focus=next_focus,
            coordination=coordination,
            consensus_score=consensus.score,
            consensus_should_end=consensus.should_end,
            should_end=should_end,
            consensus_reason=consensus.reason,
            end_reason=end_reason,
            messages=messages,
            decision_candidate=synthesis_output.decision_candidate,
            action_items=synthesis_output.action_item_draft,
            open_questions=open_questions,
            summary_text=summary_text,
            conclusion_type=synthesis_output.conclusion_type,
            conclusion_reason=synthesis_output.conclusion_reason,
            synthesis_message=synthesis_message,
        )

    # _record_specialist_memory / _record_supervisor_plan / _emit_memory_write
    # were deleted in Phase 3. SpecialistAgent.run, SupervisorAgent.run, and
    # SynthesisAgent.run now own their own memory.write events through
    # BaseLLMAgent.emit_memory_write.

    def _coordination_for_speakers(
        self,
        ctx: DecisionContext,
        specialist_pairs: list[tuple[Any, SpeakerSlot]],
        *,
        snapshot: Any | None = None,
    ) -> CoordinationAction:
        recommended = (
            str(getattr(snapshot, "recommended_next_action", "") or "")
            .strip()
            .lower()
            .replace("-", "_")
        )
        if recommended:
            from .room_executor import _RECOMMENDED_ACTION_TO_TYPE

            recommended_action_type = _RECOMMENDED_ACTION_TO_TYPE.get(recommended)
            if recommended_action_type is not None:
                target_role = (
                    specialist_pairs[0][0].role
                    if specialist_pairs
                    and recommended_action_type in {ActionType.HANDOFF, ActionType.SPEAK}
                    else None
                )
                return CoordinationAction(
                    action_type=recommended_action_type,
                    reason=f"LLM synthesis recommended next action: {recommended}",
                    target_role=target_role,
                )
        coordination = self._coordination.next_action(ctx)
        if coordination.action_type not in {ActionType.HANDOFF, ActionType.SPEAK}:
            return coordination
        if not specialist_pairs:
            return CoordinationAction(
                action_type=coordination.action_type,
                reason=coordination.reason,
            )
        target_role = specialist_pairs[0][0].role
        return CoordinationAction(
            action_type=coordination.action_type,
            reason=coordination.reason,
            target_role=target_role,
            payload=dict(coordination.payload),
        )

    def _build_supervisor_message(
        self,
        *,
        snapshot: Any,
        phase: MeetingPhase,
        round_index: int,
        next_focus: str,
        route: Any,
        plan: SupervisorPlan,
        target_roles: list[str],
    ) -> RoomMessage:
        supervisor_state = build_supervisor_state(
            snapshot=snapshot,
            round_index=round_index,
            plan=plan,
        )
        speaker_lines = [
            f"{slot.agent}" + (f" — {slot.focus_angle}" if slot.focus_angle else "")
            for slot in plan.runnable_speakers()
        ]
        text = (
            f"主持人本轮 {round_index} 阶段 ({phase.value})。 "
            f"决策焦点：{plan.decision_focus or next_focus}。 "
            f"安排理由：{plan.reason}。 "
            f"发言顺序：{'; '.join(speaker_lines)}"
        )
        if snapshot.last_human_message:
            text += f"\n来自人类的输入：{snapshot.last_human_message}"
        return RoomMessage(
            role="host",
            title="主持人调度",
            text=text,
            artifacts={
                "next_focus": next_focus,
                "round_goal": snapshot.goal,
                "target_roles": target_roles,
                "focus_points": [
                    {
                        "title": plan.decision_focus or next_focus,
                        "reason": plan.reason,
                        "constraint_ids": [],
                    }
                ],
                "speakers": [
                    {"role": slot.agent, "focus_angle": slot.focus_angle}
                    for slot in plan.runnable_speakers()
                ],
                # Backward-compat for older readers that look for turns[].
                "turns": [
                    {"role": slot.agent, "task": slot.focus_angle}
                    for slot in plan.runnable_speakers()
                ],
                "open_questions": list(plan.open_questions),
                "route": _route_artifact(route),
                "central_mas": central_mas_artifact_bundle(
                    state=supervisor_state,
                    plan=plan,
                    route=route,
                ),
            },
        )


def _resolve_speaker_specialists(
    snapshot: Any,
    speakers: list[SpeakerSlot],
) -> list[tuple[Any, SpeakerSlot]]:
    requested_roles = [slot.agent for slot in speakers]
    resolved = resolve_turn_specialists(snapshot, requested_roles)
    pairs: list[tuple[Any, SpeakerSlot]] = []
    for specialist, slot in zip(resolved, speakers):
        if specialist.role == slot.agent:
            pairs.append((specialist, slot))
    return pairs
