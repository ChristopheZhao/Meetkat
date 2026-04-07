from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from decision_room.mas.hybrid import (
    HybridConsensusStrategy,
    HybridCoordinationStrategy,
    HybridPlanningStrategy,
)
from decision_room.mas.types import (
    CoordinationAction,
    DecisionContext,
    DecisionSignals,
    MeetingPhase,
)
if TYPE_CHECKING:
    from decision_room.runtime.room_models import RoomSnapshot


@dataclass(frozen=True)
class DemoMessage:
    role: str
    title: str
    text: str
    artifacts: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DemoRound:
    phase: MeetingPhase
    signals: DecisionSignals
    plan_topic: str
    next_focus: str
    coordination: CoordinationAction
    consensus_score: float
    consensus_should_end: bool
    should_end: bool
    consensus_reason: str
    end_reason: str
    messages: list[DemoMessage]
    decision_candidate: str
    action_items: list[str]
    open_questions: list[str]
    summary_text: str
    conclusion_type: str
    conclusion_reason: str
    synthesis_message: DemoMessage


class DemoAgentExecutor:
    """Deterministic room driver used until real-model runtime wiring is available."""

    def __init__(self) -> None:
        self._planner = HybridPlanningStrategy()
        self._coordination = HybridCoordinationStrategy()
        self._consensus = HybridConsensusStrategy()

    def build_round(self, snapshot: RoomSnapshot, round_index: int) -> DemoRound:
        phase = self._phase_for_snapshot(snapshot, round_index)
        signals = self._signals_for_snapshot(snapshot, round_index, bool(snapshot.last_human_message))
        topic = snapshot.topic or "multi-agent meeting room MVP"
        requirement = snapshot.requirement or topic
        goal = snapshot.goal or "produce a concrete meeting outcome"
        ctx = DecisionContext(
            room_id=snapshot.room_id,
            phase=phase,
            signals=signals,
            metadata={"topic": topic},
        )

        plan = self._planner.plan(ctx)
        focus = plan.next_focus
        if snapshot.last_human_message:
            focus = (
                "address the latest human intervention while preserving room-level "
                "replay, convergence, and visible discussion flow"
            )

        coordination = self._coordination.next_action(ctx)
        consensus = self._consensus.evaluate(ctx)
        decision_candidate = (
            f"Move forward with a requirement-driven meeting around '{topic}': "
            "translate the ask into a replayable room runtime, a visible transcript, "
            "and explicit intervention points instead of manual form configuration."
        )
        action_items = [
            f"Refine the requirement into a room plan that clearly serves the goal: {goal}",
            "Bind the meeting UI directly to room events over WebSocket with SSE fallback.",
            "Keep role contracts stable so the executor can switch from demo to real LLM calls.",
        ]
        open_questions = list(snapshot.open_questions)
        if round_index == 1 and not open_questions:
            open_questions.append(
                "How much moderation should be automatic before a human override is required?"
            )

        host_text = (
            f"Round {round_index} is now in {phase.value}. Requirement: {requirement}. "
            f"Focus on {focus}. "
            f"Current coordination signal: {coordination.reason}."
        )
        if snapshot.last_human_message:
            host_text += f" Human input to incorporate: {snapshot.last_human_message}"

        implementation_text = (
            "The room should decompose the raw requirement into a visible meeting brief first. "
            "That keeps the entry agentic while grounding execution in replayable runtime events."
        )
        risk_text = (
            "Automatic decomposition cannot stay implicit. "
            "If derived goals, constraints, and missing-information questions are not surfaced, "
            "the room will look smart while hiding planning assumptions from operators."
        )
        synthesis_text = (
            "Synthesis summary: the room should accept one requirement, publish the derived meeting "
            "brief, and then run the discussion on top of the same replayable event protocol."
        )

        messages = [
            DemoMessage(
                role="host",
                title="Round focus",
                text=host_text,
                artifacts={
                    "next_focus": focus,
                    "round_goal": "reduce disagreement while keeping the meeting visible",
                    "target_roles": [
                        "implementation_specialist",
                        "risk_specialist",
                    ],
                    "turns": [
                        {
                            "role": "implementation_specialist",
                            "task": "Propose the minimum visible room contract for this requirement.",
                        },
                        {
                            "role": "risk_specialist",
                            "task": "Stress-test the proposal against hidden planning and runtime drift.",
                        },
                    ],
                },
            ),
            DemoMessage(
                role="implementation_specialist",
                title="Implementation readout",
                text=implementation_text,
                artifacts={
                    "claim": "The UI should be driven by room events, not local mock state.",
                    "evidence": [
                        "The requirement should be transformed into explicit meeting semantics.",
                        "The meeting page needs authoritative phase and consensus updates.",
                    ],
                    "confidence": round(signals.confidence, 2),
                    "target_claim_ref": "",
                    "turn_task": "Propose the minimum visible room contract for this requirement.",
                },
            ),
            DemoMessage(
                role="risk_specialist",
                title="Risk readout",
                text=risk_text,
                artifacts={
                    "claim": "Hidden transport state will create front/back divergence.",
                    "evidence": [
                        "Missing-information questions should be visible before convergence.",
                        "Human override must terminate the actual room, not just the UI.",
                    ],
                    "confidence": round(max(0.55, signals.confidence - 0.08), 2),
                    "target_claim_ref": "The UI should be driven by room events, not local mock state.",
                    "turn_task": "Stress-test the proposal against hidden planning and runtime drift.",
                },
            ),
        ]

        summary_text = (
            f"Consensus score is {consensus.score:.2f}. "
            f"{consensus.reason}. Candidate direction: {decision_candidate}"
        )

        return DemoRound(
            phase=phase,
            signals=signals,
            plan_topic=plan.topic,
            next_focus=focus,
            coordination=coordination,
            consensus_score=consensus.score,
            consensus_should_end=consensus.should_end,
            should_end=consensus.should_end,
            consensus_reason=consensus.reason,
            end_reason=consensus.reason if consensus.should_end else "",
            messages=messages,
            decision_candidate=decision_candidate,
            action_items=action_items,
            open_questions=open_questions,
            summary_text=summary_text,
            conclusion_type="follow_up_required",
            conclusion_reason=(
                "The demo path still provides a provisional candidate and requires follow-up "
                "before the room can claim production readiness."
            ),
            synthesis_message=DemoMessage(
                role="synthesis",
                title="Synthesis note",
                text=synthesis_text,
                artifacts={
                    "agreement": [
                        "The user should enter one requirement, not a manually prepared workflow.",
                        "Meeting state must be replayable and visible.",
                    ],
                    "disagreement": [
                        "How much automation is appropriate before human intervention."
                    ],
                    "decision_candidate": decision_candidate,
                    "action_item_draft": action_items,
                },
            ),
        )

    def _phase_for_snapshot(self, snapshot: RoomSnapshot, round_index: int) -> MeetingPhase:
        if round_index <= 1 and not snapshot.transcript:
            return MeetingPhase.EXPLORE
        if snapshot.candidate_decision:
            if snapshot.open_questions or snapshot.consensus.disagreement_index > 0.35:
                return MeetingPhase.SYNTHESIZE
            return MeetingPhase.DECIDE
        if snapshot.last_human_message:
            return MeetingPhase.DEBATE
        if len(snapshot.transcript) >= 4 or snapshot.consensus.support >= 0.70:
            return MeetingPhase.SYNTHESIZE
        return MeetingPhase.DEBATE

    def _signals_for_snapshot(
        self,
        snapshot: RoomSnapshot,
        round_index: int,
        has_human_message: bool,
    ) -> DecisionSignals:
        transcript_depth = len(snapshot.transcript)
        open_question_count = len(snapshot.open_questions)
        support = snapshot.consensus.support if snapshot.consensus.support > 0 else 0.56
        confidence = snapshot.consensus.confidence if snapshot.consensus.confidence > 0 else 0.61
        disagreement = (
            snapshot.consensus.disagreement_index
            if transcript_depth > 0
            else 0.58
        )
        margin = (
            snapshot.consensus.margin_top1_top2
            if snapshot.consensus.margin_top1_top2 > 0
            else 0.05
        )

        support = min(0.94, support + 0.03 + min(0.06, transcript_depth * 0.01))
        confidence = min(0.94, confidence + 0.02 + min(0.05, transcript_depth * 0.008))
        disagreement = max(0.18, disagreement - min(0.10, transcript_depth * 0.015))
        margin = min(0.24, margin + 0.02 + min(0.05, transcript_depth * 0.008))

        if snapshot.candidate_decision:
            support = min(0.96, support + 0.05)
            confidence = min(0.96, confidence + 0.04)
            disagreement = max(0.15, disagreement - 0.05)
            margin = min(0.28, margin + 0.03)
        if open_question_count:
            confidence = max(0.42, confidence - min(0.10, open_question_count * 0.02))
            disagreement = min(0.90, disagreement + min(0.10, open_question_count * 0.015))
        if has_human_message:
            support = min(0.96, support + 0.02)
            confidence = min(0.96, confidence + 0.03)
            disagreement = max(0.16, disagreement - 0.03)

        return DecisionSignals(
            support=support,
            confidence=confidence,
            risk_penalty=min(0.32, 0.08 + open_question_count * 0.03),
            margin_top1_top2=margin,
            disagreement_index=disagreement,
            rounds_without_progress=1 if snapshot.candidate_decision and round_index > 1 else 0,
            tool_failure_rate=0.02,
        )
