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
    agent_scope,
    default_long_term_store,
    default_room_memory_store,
    mas_scope,
    memory_recall_for_role,
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
        self._supervisor = supervisor or LLMSupervisor(
            registry=registry,
            router=router,
            transient_max_attempts=transient_max_attempts,
        )
        self._coordination = coordination or HybridCoordinationStrategy()
        self._consensus = consensus or HybridConsensusStrategy()
        self._planner = planner or HybridPlanningStrategy()
        # Internal specialist machinery is owned by an LLMRoomExecutor so all
        # specialist prompts, retries, and parsers stay in one place.
        self._specialist_runner = LLMRoomExecutor(
            registry=registry,
            router=router,
            planner=self._planner,
            coordination=self._coordination,
            consensus=self._consensus,
            use_background_threads=use_background_threads,
            transient_max_attempts=transient_max_attempts,
        )
        self._transient_max_attempts = max(1, transient_max_attempts)
        self._room_memory = room_memory_store or default_room_memory_store()
        self._long_term = long_term_store or default_long_term_store()

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

    async def build_round(self, snapshot: Any, round_index: int) -> RoomRound:
        runner = self._specialist_runner
        phase = runner._phase_for_round(snapshot, round_index)  # noqa: SLF001
        routing_signals = runner._signals_for_round(snapshot, round_index)  # noqa: SLF001
        topic = snapshot.topic or "centralized MAS decision room"
        ctx = DecisionContext(
            room_id=snapshot.room_id,
            phase=phase,
            signals=routing_signals,
            metadata={"topic": topic, "topology": "centralized_supervisor"},
        )
        plan_decision = self._planner.plan(ctx)
        next_focus = runner._resolve_focus(snapshot, plan_decision.next_focus)  # noqa: SLF001
        route_ctx = ctx
        route = self._router.route(route_ctx)

        supervisor_plan, route_ctx, route = await self._supervisor.plan_round(
            snapshot=snapshot,
            round_index=round_index,
            phase=phase,
            next_focus=next_focus,
            route_ctx=route_ctx,
            route=route,
        )
        host_agenda = supervisor_plan_to_host_agenda(supervisor_plan)
        runnable = supervisor_plan.runnable_speakers()
        if not runnable:
            raise RuntimeError("supervisor returned no runnable speaker slots")
        specialist_pairs = _resolve_speaker_specialists(snapshot, runnable)
        if not specialist_pairs:
            raise RuntimeError(
                "supervisor speakers did not resolve to any planned specialist"
            )
        coordination = self._coordination_for_speakers(ctx, specialist_pairs)

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
            recall = memory_recall_for_role(
                room_id=room_id,
                role=specialist.role,
                room_store=self._room_memory,
                long_term_store=self._long_term,
            )
            # The supervisor only provides an optional ``focus_angle`` hint —
            # the specialist authors its own claim, evidence, and confidence.
            # We pass the hint through ``turn_task`` for backward signature
            # compatibility with the host-led specialist generator.
            output, route_ctx, turn_route = await runner._generate_argument(  # noqa: SLF001
                route_ctx=route_ctx,
                route=route,
                specialist=specialist,
                turn_task=slot.focus_angle,
                snapshot=snapshot,
                phase=phase,
                round_index=round_index,
                next_focus=next_focus,
                host_agenda=host_agenda,
                target_claim_ref=target_claim_ref,
                memory_recall=recall,
            )
            route = turn_route
            turn_results.append(
                SpecialistTurnResult(
                    specialist=specialist,
                    task=slot.focus_angle,
                    output=output,
                    route=turn_route,
                )
            )
            target_claim_ref = output.claim
            # Persist this specialist's claim to shared room memory and to
            # the agent's own scratchpad so subsequent rounds see it as part
            # of memory_recall.
            self._record_specialist_memory(
                room_id=room_id,
                role=specialist.role,
                round_index=round_index,
                output=output,
            )

        synthesis_output, route_ctx, synthesis_route = await runner._generate_synthesis(  # noqa: SLF001
            route_ctx=route_ctx,
            route=route,
            snapshot=snapshot,
            phase=phase,
            round_index=round_index,
            next_focus=next_focus,
            host_agenda=host_agenda,
            turn_results=turn_results,
        )

        open_questions = _dedupe_strings(
            [
                *_visible_open_questions(snapshot),
                *host_agenda.open_questions,
                *_filter_answered_questions(snapshot, synthesis_output.open_questions),
            ],
            limit=6,
        )
        post_signals = runner._signals_after_round(  # noqa: SLF001
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
                "route": _route_artifact(synthesis_route),
                "central_mas_state_ref": "host.artifacts.central_mas.supervisor_state",
            },
        )
        summary_text = (
            f"{synthesis_output.text} Conclusion: {synthesis_output.conclusion_type}. "
            f"{synthesis_output.conclusion_reason} Consensus score {consensus.score:.2f}. "
            f"{consensus.reason}."
        )
        should_end, end_reason = runner._resolve_round_end_signal(  # noqa: SLF001
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

    def _record_specialist_memory(
        self,
        *,
        room_id: str,
        role: str,
        round_index: int,
        output: Any,
    ) -> None:
        shared_scope = mas_scope(room_id)
        agent_scope_id = agent_scope(room_id, role)
        claim = getattr(output, "claim", "")
        evidence = list(getattr(output, "evidence", []) or [])
        confidence = float(getattr(output, "confidence", 0.0) or 0.0)
        fact_key = f"latest_claim.{role}"
        self._room_memory.write_fact(
            room_id,
            shared_scope,
            fact_key,
            {
                "round_index": round_index,
                "claim": claim,
                "evidence": evidence,
                "confidence": confidence,
            },
        )
        self._room_memory.record_event(
            room_id,
            shared_scope,
            "specialist.claim",
            {
                "role": role,
                "round_index": round_index,
                "claim": claim,
                "confidence": confidence,
            },
        )
        self._room_memory.write_fact(
            room_id,
            agent_scope_id,
            "last_self_claim",
            {
                "round_index": round_index,
                "claim": claim,
                "evidence": evidence[:3],
                "confidence": confidence,
            },
        )

    def _coordination_for_speakers(
        self,
        ctx: DecisionContext,
        specialist_pairs: list[tuple[Any, SpeakerSlot]],
    ) -> CoordinationAction:
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
