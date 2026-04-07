import unittest

from decision_room.mas.hybrid import HybridConsensusStrategy
from decision_room.mas.types import DecisionContext, DecisionSignals, MeetingPhase


class ConsensusTests(unittest.TestCase):
    def test_should_end_when_thresholds_met(self) -> None:
        ctx = DecisionContext(
            room_id="r1",
            phase=MeetingPhase.DECIDE,
            signals=DecisionSignals(
                support=0.95,
                confidence=0.9,
                risk_penalty=0.0,
                margin_top1_top2=0.2,
                disagreement_index=0.2,
            ),
        )
        result = HybridConsensusStrategy().evaluate(ctx)
        self.assertTrue(result.should_end)

    def test_continue_when_margin_low(self) -> None:
        ctx = DecisionContext(
            room_id="r1",
            phase=MeetingPhase.DECIDE,
            signals=DecisionSignals(
                support=0.9,
                confidence=0.85,
                risk_penalty=0.05,
                margin_top1_top2=0.05,
                disagreement_index=0.2,
            ),
        )
        result = HybridConsensusStrategy().evaluate(ctx)
        self.assertFalse(result.should_end)


if __name__ == "__main__":
    unittest.main()
