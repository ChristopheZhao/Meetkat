from __future__ import annotations

from dataclasses import dataclass

from decision_room.mas.types import DecisionContext, FallbackDecision


@dataclass
class FallbackConfig:
    max_timeouts_before_fallback: int = 2
    max_rate_limits_before_fallback: int = 2


class DisasterOnlyFallbackPolicy:
    """Fallback is disaster-only, never the normal else branch."""

    def __init__(self, cfg: FallbackConfig | None = None) -> None:
        self.cfg = cfg or FallbackConfig()

    def check(self, ctx: DecisionContext) -> FallbackDecision:
        s = ctx.signals

        if s.api_unreachable:
            return FallbackDecision(True, "primary model API unreachable")

        if s.timeout_count >= self.cfg.max_timeouts_before_fallback:
            return FallbackDecision(True, "persistent timeout on primary path")

        if s.rate_limited_count >= self.cfg.max_rate_limits_before_fallback:
            return FallbackDecision(True, "persistent rate limit on primary path")

        if s.missing_required_fields_after_retry:
            return FallbackDecision(True, "quality redline after retry and same-tier substitute")

        if s.human_force_complete:
            return FallbackDecision(True, "human requested disaster fallback completion")

        return FallbackDecision(False, "primary path remains available")
