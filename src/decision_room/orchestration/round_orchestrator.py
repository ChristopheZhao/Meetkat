"""Single round orchestrator with pluggable speaker selection.

Phase 4 of the native-agent refactor. Before this PR the round loop was
duplicated across ``LLMRoomExecutor.build_round`` and
``CentralizedMASExecutor.build_round`` — same setup, same specialist
loop, same synthesis call, same consensus + end-signal handling. The
only meaningful difference was who picked the speakers. Phase 4 lifts
the speaker decision into a ``SpeakerSelectionStrategy`` so the loop
itself lives in exactly one place.

Strategy precedence over the FSM (resolved in this phase):

    LLM synthesis recommended_next_phase  > rule-based phase derivation
    LLM synthesis recommended_next_action > HybridCoordinationStrategy FSM

Both already behaved this way pre-Phase-4; this orchestrator codifies
the precedence in a single call site so the two paths can't drift.
"""

from __future__ import annotations

from typing import Any

from decision_room.agents import SpecialistAgent, SynthesisAgent, TurnContext
from decision_room.mas.hybrid import (
    HybridConsensusStrategy,
    HybridCoordinationStrategy,
    HybridPlanningStrategy,
)
from decision_room.mas.types import (
    ActionType,
    CoordinationAction,
    DecisionContext,
)
from decision_room.memory import LongTermLessonStore, RoomMemoryStore
from decision_room.providers import ProviderRegistry
from decision_room.routing.model_router import HybridModelRouter
from decision_room.tools import ToolRegistry, default_tool_registry

from .speaker_strategies import (
    SpeakerSelectionStrategy,
    SpecialistAssignment,
)


class RoundOrchestrator:
    """Drives one round of the meeting using a ``SpeakerSelectionStrategy``.

    The strategy decides WHO speaks WHEN; the orchestrator owns
    everything else: phase + signal computation, the specialist
    iteration loop, synthesis, consensus, end-signal resolution, and
    message assembly. Both the host-led and the supervisor-led
    topologies now run through this class — Phase 4 deletes the
    duplicated bodies that used to live on each executor.
    """

    def __init__(
        self,
        *,
        registry: ProviderRegistry,
        router: HybridModelRouter,
        strategy: SpeakerSelectionStrategy,
        planner: HybridPlanningStrategy | None = None,
        coordination: HybridCoordinationStrategy | None = None,
        consensus: HybridConsensusStrategy | None = None,
        use_background_threads: bool = True,
        transient_max_attempts: int = 2,
        tool_registry: ToolRegistry | None = None,
        room_memory_store: RoomMemoryStore | None = None,
        long_term_store: LongTermLessonStore | None = None,
    ) -> None:
        self._registry = registry
        self._router = router
        self._strategy = strategy
        self._planner = planner or HybridPlanningStrategy()
        self._coordination = coordination or HybridCoordinationStrategy()
        self._consensus = consensus or HybridConsensusStrategy()
        self._tool_registry = tool_registry or default_tool_registry()
        agent_kwargs = dict(
            registry=registry,
            router=router,
            use_background_threads=use_background_threads,
            transient_max_attempts=max(1, transient_max_attempts),
            room_memory_store=room_memory_store,
            long_term_store=long_term_store,
        )
        self._specialist_agent = SpecialistAgent(
            tool_registry=self._tool_registry, **agent_kwargs
        )
        self._synthesis_agent = SynthesisAgent(**agent_kwargs)

    @property
    def strategy(self) -> SpeakerSelectionStrategy:
        return self._strategy

    async def build_round(
        self,
        snapshot: Any,
        round_index: int,
        *,
        publish: Any = None,
    ) -> "RoomRound":
        # Local imports to avoid a module-level cycle through orchestration/.
        from decision_room.orchestration.room_executor import (
            RoomMessage,
            RoomRound,
            SpecialistTurnResult,
            _dedupe_strings,
            _filter_answered_questions,
            _route_artifact,
            _visible_open_questions,
            phase_for_round,
            resolve_focus,
            resolve_round_end_signal,
            signals_after_round,
            signals_for_round,
        )

        phase = phase_for_round(snapshot, round_index)
        routing_signals = signals_for_round(snapshot, round_index)
        topic = snapshot.topic or self._strategy.topic_default
        metadata = {"topic": topic, **dict(self._strategy.metadata_overrides)}
        ctx = DecisionContext(
            room_id=snapshot.room_id,
            phase=phase,
            signals=routing_signals,
            metadata=metadata,
        )

        plan_decision = self._planner.plan(ctx)
        next_focus = resolve_focus(snapshot, plan_decision.next_focus)
        route_ctx = ctx
        route = self._router.route(route_ctx)

        speaker_plan = await self._strategy.plan_speakers(
            snapshot=snapshot,
            round_index=round_index,
            phase=phase,
            next_focus=next_focus,
            route_ctx=route_ctx,
            route=route,
            publish=publish,
        )
        route_ctx = speaker_plan.route_ctx
        route = speaker_plan.route
        if not speaker_plan.assignments:
            raise RuntimeError(
                f"{self._strategy.name} produced no specialist assignments"
            )

        coordination = self._coordination_for_assignments(
            ctx, speaker_plan.assignments, snapshot=snapshot
        )

        turn_results: list[SpecialistTurnResult] = []
        target_claim_ref = ""
        for assignment in speaker_plan.assignments:
            specialist_ctx = TurnContext(
                snapshot=snapshot,
                round_index=round_index,
                phase=phase,
                next_focus=next_focus,
                route_ctx=route_ctx,
                route=route,
                publish=publish,
                extras={
                    "specialist": assignment.specialist,
                    "turn_task": assignment.task,
                    "host_agenda": speaker_plan.host_agenda,
                    "target_claim_ref": target_claim_ref,
                },
            )
            specialist_result = await self._specialist_agent.run(specialist_ctx)
            route_ctx = specialist_result.ctx
            route = specialist_result.route
            output = specialist_result.output
            turn_results.append(
                SpecialistTurnResult(
                    specialist=assignment.specialist,
                    task=assignment.task,
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
            extras={
                "host_agenda": speaker_plan.host_agenda,
                "turn_results": turn_results,
            },
        )
        synthesis_result = await self._synthesis_agent.run(synthesis_ctx)
        route_ctx = synthesis_result.ctx
        synthesis_route = synthesis_result.route
        synthesis_output = synthesis_result.output

        open_questions = _dedupe_strings(
            [
                *_visible_open_questions(snapshot),
                *speaker_plan.host_agenda.open_questions,
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
                metadata=metadata,
            )
        )

        messages: list[RoomMessage] = [speaker_plan.host_message]
        for turn_index, turn_result in enumerate(turn_results, start=1):
            assignment = speaker_plan.assignments[turn_index - 1]
            artifacts: dict[str, Any] = {
                "claim": turn_result.output.claim,
                "evidence": turn_result.output.evidence,
                "confidence": turn_result.output.confidence,
                "target_claim_ref": turn_result.output.target_claim_ref,
                "specialist_display_name": turn_result.specialist.display_name,
                "capability_profile": turn_result.specialist.capability_profile,
                "turn_index": turn_index,
                "route": _route_artifact(turn_result.route),
            }
            # Both topologies populate ``turn_task`` so consumers that
            # read either field still work; supervisor-led additionally
            # exposes ``focus_angle`` + ``speaker_slot`` for parity with
            # the pre-Phase-4 CentralizedMASExecutor output.
            artifacts["turn_task"] = assignment.task
            if assignment.speaker_slot is not None:
                artifacts["focus_angle"] = assignment.task
                artifacts["speaker_slot"] = assignment.speaker_slot.to_payload()
            messages.append(
                RoomMessage(
                    role=turn_result.specialist.role,
                    title=turn_result.output.title,
                    text=turn_result.output.text,
                    artifacts=artifacts,
                )
            )

        synthesis_artifacts: dict[str, Any] = {
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
            **speaker_plan.extra_synthesis_artifacts,
        }
        synthesis_message = RoomMessage(
            role="synthesis",
            title=synthesis_output.title,
            text=synthesis_output.text,
            artifacts=synthesis_artifacts,
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

    def _coordination_for_assignments(
        self,
        ctx: DecisionContext,
        assignments: list[SpecialistAssignment],
        *,
        snapshot: Any | None = None,
    ) -> CoordinationAction:
        """LLM synthesis recommendation wins; FSM is the fallback.

        Replaces the previously-duplicated ``_coordination_for_turns``
        (host-led) and ``_coordination_for_speakers`` (supervisor-led)
        with a single implementation. Both pre-Phase-4 methods had the
        same precedence rule — the canonical version lives here now.
        """
        # Local import to avoid the orchestration package cycle.
        from decision_room.orchestration.room_executor import (
            _RECOMMENDED_ACTION_TO_TYPE,
        )

        recommended = (
            str(getattr(snapshot, "recommended_next_action", "") or "")
            .strip()
            .lower()
            .replace("-", "_")
        )
        if recommended:
            recommended_action_type = _RECOMMENDED_ACTION_TO_TYPE.get(recommended)
            if recommended_action_type is not None:
                target_role = (
                    assignments[0].specialist.role
                    if assignments
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
        if not assignments:
            return CoordinationAction(
                action_type=coordination.action_type,
                reason=coordination.reason,
            )
        target_role = assignments[0].specialist.role
        return CoordinationAction(
            action_type=coordination.action_type,
            reason=coordination.reason,
            target_role=target_role,
            payload=dict(coordination.payload),
        )
