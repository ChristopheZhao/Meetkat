import unittest

from decision_room.mas.types import DecisionContext, DecisionSignals, MeetingPhase, ModelTier
from decision_room.policies.fallback import DisasterOnlyFallbackPolicy
from decision_room.policies.routing_control import RoutingControlPolicy


class RoutingControlPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = RoutingControlPolicy(DisasterOnlyFallbackPolicy())

    def test_default_tier_remains_default_when_signals_are_healthy(self) -> None:
        decision = self.policy.decide_tier(
            DecisionContext(
                room_id="room_test",
                phase=MeetingPhase.EXPLORE,
                signals=DecisionSignals(confidence=0.82, tool_failure_rate=0.01),
            )
        )

        self.assertEqual(decision.tier, ModelTier.DEFAULT)

    def test_fallback_tier_is_controlled_by_disaster_signals(self) -> None:
        decision = self.policy.decide_tier(
            DecisionContext(
                room_id="room_test",
                phase=MeetingPhase.DEBATE,
                signals=DecisionSignals(api_unreachable=True),
            )
        )

        self.assertEqual(decision.tier, ModelTier.DISASTER_FALLBACK)
        self.assertIn("fallback", decision.reason)


if __name__ == "__main__":
    unittest.main()
