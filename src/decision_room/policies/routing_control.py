from __future__ import annotations

from dataclasses import dataclass

from decision_room.mas.types import DecisionContext, ModelTier

from .fallback import DisasterOnlyFallbackPolicy


@dataclass(frozen=True)
class RoutingControlConfig:
    rounds_without_progress: int = 2
    tool_failure_rate: float = 0.20
    low_confidence: float = 0.60


@dataclass(frozen=True)
class RoutingControlDecision:
    tier: ModelTier
    reason: str


class RoutingControlPolicy:
    """Owns route-tier gating before the router maps a tier to a concrete target."""

    def __init__(
        self,
        fallback_policy: DisasterOnlyFallbackPolicy,
        cfg: RoutingControlConfig | None = None,
    ) -> None:
        self.fallback_policy = fallback_policy
        self.cfg = cfg or RoutingControlConfig()

    def decide_tier(self, ctx: DecisionContext) -> RoutingControlDecision:
        fallback = self.fallback_policy.check(ctx)
        if fallback.should_fallback:
            return RoutingControlDecision(
                tier=ModelTier.DISASTER_FALLBACK,
                reason=f"fallback: {fallback.reason}",
            )

        s = ctx.signals
        if (
            s.rounds_without_progress >= self.cfg.rounds_without_progress
            or s.tool_failure_rate >= self.cfg.tool_failure_rate
            or s.confidence <= self.cfg.low_confidence
        ):
            return RoutingControlDecision(
                tier=ModelTier.ESCALATION,
                reason="escalate based on meeting signals",
            )

        return RoutingControlDecision(
            tier=ModelTier.DEFAULT,
            reason="default path",
        )
