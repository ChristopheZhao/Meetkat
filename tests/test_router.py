import unittest

from decision_room.mas.types import (
    DecisionContext,
    DecisionSignals,
    MeetingPhase,
    ModelTarget,
    ModelTier,
)
from decision_room.policies.fallback import DisasterOnlyFallbackPolicy
from decision_room.policies.routing_control import RoutingControlPolicy
from decision_room.routing.model_router import HybridModelRouter, RouterTargets


class RouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.targets = RouterTargets(
            default_target=ModelTarget("qwen", "qwen-plus"),
            escalation_target=ModelTarget("glm", "glm-5"),
            disaster_fallback_target=ModelTarget("minimax", "MiniMax-M2.5"),
        )

    def test_default_path(self) -> None:
        ctx = DecisionContext(
            room_id="r1",
            phase=MeetingPhase.EXPLORE,
            signals=DecisionSignals(confidence=0.8, tool_failure_rate=0.01),
        )
        dec = HybridModelRouter(
            RoutingControlPolicy(DisasterOnlyFallbackPolicy()), targets=self.targets
        ).route(ctx)
        self.assertEqual(dec.tier, ModelTier.DEFAULT)
        self.assertEqual(dec.target.supplier, "qwen")
        self.assertEqual(dec.target.model, "qwen-plus")

    def test_escalation_path(self) -> None:
        ctx = DecisionContext(
            room_id="r1",
            phase=MeetingPhase.DEBATE,
            signals=DecisionSignals(confidence=0.4, tool_failure_rate=0.01),
        )
        dec = HybridModelRouter(
            RoutingControlPolicy(DisasterOnlyFallbackPolicy()), targets=self.targets
        ).route(ctx)
        self.assertEqual(dec.tier, ModelTier.ESCALATION)
        self.assertEqual(dec.target.supplier, "glm")
        self.assertEqual(dec.target.model, "glm-5")

    def test_disaster_fallback_path(self) -> None:
        ctx = DecisionContext(
            room_id="r1",
            phase=MeetingPhase.DEBATE,
            signals=DecisionSignals(api_unreachable=True),
        )
        dec = HybridModelRouter(
            RoutingControlPolicy(DisasterOnlyFallbackPolicy()), targets=self.targets
        ).route(ctx)
        self.assertEqual(dec.tier, ModelTier.DISASTER_FALLBACK)
        self.assertEqual(dec.target.supplier, "minimax")
        self.assertEqual(dec.target.model, "MiniMax-M2.5")


if __name__ == "__main__":
    unittest.main()
