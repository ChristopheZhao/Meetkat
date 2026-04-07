from __future__ import annotations

from dataclasses import dataclass

from .types import (
    ActionType,
    ConsensusResult,
    CoordinationAction,
    DecisionContext,
    MeetingPhase,
    PlanResult,
)


@dataclass
class HybridConsensusConfig:
    support_weight: float = 0.50
    confidence_weight: float = 0.35
    risk_weight: float = 0.15

    end_score_threshold: float = 0.75
    end_margin_threshold: float = 0.12
    end_disagreement_threshold: float = 0.35


class HybridPlanningStrategy:
    """Lightweight planner: keeps the meeting focused on the highest-risk unknowns."""

    def plan(self, ctx: DecisionContext) -> PlanResult:
        if ctx.phase in (MeetingPhase.EXPLORE, MeetingPhase.DEBATE):
            focus = "resolve top disagreement cluster"
        elif ctx.phase == MeetingPhase.SYNTHESIZE:
            focus = "merge proposals and remove duplicates"
        else:
            focus = "prepare final decision package"

        return PlanResult(
            topic=ctx.metadata.get("topic", "meeting_topic"),
            next_focus=focus,
            notes="hybrid planning strategy",
        )


class HybridCoordinationStrategy:
    """Hybrid = free collaboration inside phase, guarded transition across phases."""

    def next_action(self, ctx: DecisionContext) -> CoordinationAction:
        s = ctx.signals

        if s.human_force_complete:
            return CoordinationAction(
                action_type=ActionType.HUMAN_OVERRIDE,
                reason="human explicitly requested completion",
            )

        if s.rounds_without_progress >= 2:
            return CoordinationAction(
                action_type=ActionType.CHECK_CONSENSUS,
                reason="no progress for two consecutive rounds",
            )

        if ctx.phase in (MeetingPhase.EXPLORE, MeetingPhase.DEBATE):
            if s.disagreement_index >= 0.5:
                return CoordinationAction(
                    action_type=ActionType.HANDOFF,
                    reason="high disagreement; trigger cross-role challenge",
                )
            return CoordinationAction(
                action_type=ActionType.SPEAK,
                reason="continue evidence collection",
            )

        if ctx.phase == MeetingPhase.SYNTHESIZE:
            return CoordinationAction(
                action_type=ActionType.CHECK_CONSENSUS,
                reason="synthesis stage should evaluate convergence frequently",
            )

        if ctx.phase == MeetingPhase.DECIDE:
            return CoordinationAction(
                action_type=ActionType.CHECK_CONSENSUS,
                reason="decision stage requires explicit closure check",
            )

        return CoordinationAction(action_type=ActionType.NOOP, reason="no action")


class HybridConsensusStrategy:
    def __init__(self, cfg: HybridConsensusConfig | None = None) -> None:
        self.cfg = cfg or HybridConsensusConfig()

    def evaluate(self, ctx: DecisionContext) -> ConsensusResult:
        s = ctx.signals
        score = (
            self.cfg.support_weight * s.support
            + self.cfg.confidence_weight * s.confidence
            - self.cfg.risk_weight * s.risk_penalty
        )

        should_end = (
            score >= self.cfg.end_score_threshold
            and s.margin_top1_top2 >= self.cfg.end_margin_threshold
            and s.disagreement_index <= self.cfg.end_disagreement_threshold
        )

        if should_end:
            reason = "consensus threshold reached"
        else:
            reason = "continue meeting; convergence conditions unmet"

        return ConsensusResult(score=score, should_end=should_end, reason=reason)
