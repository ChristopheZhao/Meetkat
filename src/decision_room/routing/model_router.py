from __future__ import annotations

from dataclasses import dataclass, field

from decision_room.mas.types import (
    DecisionContext,
    ModelTarget,
    ModelTier,
    RoutingDecision,
)
from decision_room.policies.routing_control import RoutingControlPolicy


@dataclass
class RouterTargets:
    default_target: ModelTarget = field(
        default_factory=lambda: ModelTarget("qwen", "qwen-plus")
    )
    escalation_target: ModelTarget = field(
        default_factory=lambda: ModelTarget("glm", "glm-5")
    )
    disaster_fallback_target: ModelTarget = field(
        default_factory=lambda: ModelTarget("minimax", "MiniMax-M2.5")
    )


class HybridModelRouter:
    def __init__(
        self,
        control_policy: RoutingControlPolicy,
        targets: RouterTargets | None = None,
    ) -> None:
        self.control_policy = control_policy
        self.targets = targets or RouterTargets()

    def route(self, ctx: DecisionContext) -> RoutingDecision:
        decision = self.control_policy.decide_tier(ctx)
        if decision.tier == ModelTier.DISASTER_FALLBACK:
            target = self.targets.disaster_fallback_target
        elif decision.tier == ModelTier.ESCALATION:
            target = self.targets.escalation_target
        else:
            target = self.targets.default_target

        return RoutingDecision(
            tier=decision.tier,
            target=target,
            reason=f"{decision.reason}; target={target.supplier}/{target.model}",
        )
