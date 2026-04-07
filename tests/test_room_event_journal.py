import unittest

from decision_room.runtime.room_event_journal import RoomEventJournal
from decision_room.runtime.room_projector import RoomProjector


class RoomEventJournalTests(unittest.TestCase):
    def test_append_assigns_monotonic_seq_and_replay_filters_suffix(self) -> None:
        journal = RoomEventJournal("room_test")

        event1 = journal.append(
            producer_id="system.runtime",
            role="system",
            event_type="room.started",
            payload={"topic": "Architecture review", "goal": "Freeze the room contract."},
        )
        event2 = journal.append(
            producer_id="agent.host.1",
            role="host",
            event_type="agent.message",
            payload={
                "message_id": "msg_1",
                "title": "Round focus",
                "text": "Focus on the event journal boundary.",
            },
        )

        self.assertEqual(event1.room_seq, 1)
        self.assertEqual(event2.room_seq, 2)
        self.assertEqual(journal.last_seq, 2)
        self.assertEqual([item.room_seq for item in journal.replay(after_seq=1)], [2])

    def test_rebuild_reconstructs_projection_from_journal(self) -> None:
        journal = RoomEventJournal("room_test")
        journal.append(
            producer_id="system.runtime",
            role="system",
            event_type="room.started",
            payload={
                "requirement": "Need a replayable room event model.",
                "topic": "Runtime review",
                "goal": "Freeze the journal boundary.",
                "constraints": ["Replay must be authoritative."],
                "open_questions": ["Should checkpoints be explicit?"],
                "mode": "agent_first",
                "phase": "explore",
                "status": "running",
                "current_focus": "lock the SoT",
                "brief_source": "agent",
                "brief_source_reason": "planner output",
                "resume_token": "room_test",
            },
        )
        journal.append(
            producer_id="agent.host.1",
            role="host",
            event_type="agent.joined",
            payload={
                "participant_id": "agent.host.1",
                "identity": "agent",
                "display_name": "Host",
                "activation": "persistent",
                "speaking": True,
                "capability_profile": "Moderates the room.",
                "join_reason": "Host is the only persistent active agent.",
                "focus_areas": ["moderation"],
            },
        )
        journal.append(
            producer_id="agent.host.1",
            role="host",
            event_type="agent.message",
            payload={
                "message_id": "msg_1",
                "title": "Round focus",
                "text": "Treat the event journal as the only room fact source.",
                "round_index": 1,
                "phase": "explore",
                "current_focus": "lock the SoT",
                "artifacts": {
                    "turns": [
                        {
                            "role": "implementation_specialist",
                            "task": "Propose the minimum room contract that preserves replay.",
                        },
                        {
                            "role": "risk_specialist",
                            "task": "Stress-test the contract against hidden runtime drift.",
                        },
                    ]
                },
            },
        )
        journal.append(
            producer_id="capability.synthesis.1",
            role="synthesis",
            event_type="agent.summary",
            payload={
                "summary": "Structured synthesis for the current round.",
                "decision_candidate": "Keep the host-led topology.",
                "action_items": ["Keep synthesis structured."],
                "open_questions": ["How should replay expose dynamic turns?"],
                "conclusion_type": "follow_up_required",
                "conclusion_reason": "The candidate direction is viable but still needs follow-up.",
            },
        )

        rebuilt = journal.rebuild(RoomProjector("room_test")).snapshot.public_dict()

        self.assertEqual(rebuilt["topic"], "Runtime review")
        self.assertEqual(rebuilt["participants"][0]["display_name"], "Host")
        self.assertEqual(rebuilt["participants"][0]["activation"], "persistent")
        self.assertEqual(rebuilt["transcript"][0]["text"], "Treat the event journal as the only room fact source.")
        self.assertEqual(
            [item["role"] for item in rebuilt["current_turns"]],
            ["implementation_specialist", "risk_specialist"],
        )
        self.assertEqual(rebuilt["conclusion_type"], "follow_up_required")
        self.assertEqual(rebuilt["last_seq"], 4)


if __name__ == "__main__":
    unittest.main()
