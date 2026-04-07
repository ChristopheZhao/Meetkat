import unittest

from decision_room.runtime.events import EventEnvelope, room_topic


class EventContractTests(unittest.TestCase):
    def test_event_envelope_validation(self) -> None:
        event = EventEnvelope(
            schema_version="1.0",
            event_id="evt1",
            room_id="room1",
            room_seq=1,
            producer_id="agent.host.1",
            role="host",
            event_type="agent.message",
            ts_ms=1,
            idempotency_key="room1:agent.host.1:1",
            payload={"k": "v"},
        )
        event.validate()

    def test_room_topic(self) -> None:
        self.assertEqual(room_topic("abc"), "room.abc.events")


if __name__ == "__main__":
    unittest.main()
