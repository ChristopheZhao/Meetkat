import unittest

from decision_room.policies.room_control import (
    RoomControlConfig,
    RoomControlPolicy,
    RoomStateError,
)
from decision_room.runtime.room_models import RoomSnapshot


class RoomControlPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = RoomControlPolicy()

    def test_assert_accepts_writes_rejects_ended_room(self) -> None:
        snapshot = RoomSnapshot(room_id="room_test", status="ended", ended_reason="locked")

        with self.assertRaisesRegex(RoomStateError, "locked"):
            self.policy.assert_accepts_writes(snapshot)

    def test_end_payload_from_consensus_falls_back_to_snapshot_values(self) -> None:
        snapshot = RoomSnapshot(
            room_id="room_test",
            candidate_decision="Keep the journal authoritative.",
            action_items=["Prove replay rebuild."],
            open_questions=["How explicit should checkpoints be?"],
        )

        payload = self.policy.end_payload_from_consensus(
            snapshot,
            reason="consensus reached",
            decision_candidate="",
            action_items=[],
            open_questions=[],
            conclusion_type="candidate_ready",
            conclusion_reason="The current candidate is ready for closure.",
        )

        self.assertEqual(payload["reason"], "The current candidate is ready for closure.")
        self.assertEqual(payload["decision_candidate"], "Keep the journal authoritative.")
        self.assertEqual(payload["action_items"], ["Prove replay rebuild."])
        self.assertEqual(payload["open_questions"], ["How explicit should checkpoints be?"])
        self.assertEqual(payload["conclusion_type"], "candidate_ready")
        self.assertEqual(
            payload["control_reason"],
            "control gate accepted orchestration end signal",
        )
        self.assertEqual(payload["orchestration_end_reason"], "consensus reached")

    def test_should_start_round_respects_control_budget(self) -> None:
        policy = RoomControlPolicy(RoomControlConfig(max_rounds=2))
        snapshot = RoomSnapshot(room_id="room_test", status="running")

        self.assertTrue(policy.should_start_round(snapshot, round_index=1))
        self.assertTrue(policy.should_start_round(snapshot, round_index=2))
        self.assertFalse(policy.should_start_round(snapshot, round_index=3))


if __name__ == "__main__":
    unittest.main()
