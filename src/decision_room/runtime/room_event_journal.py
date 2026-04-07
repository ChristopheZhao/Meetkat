from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

from .events import EventEnvelope
from .room_projector import RoomProjector


def _ts_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class RoomEventJournal:
    room_id: str
    _events: list[EventEnvelope] = field(default_factory=list)
    _last_seq: int = 0

    @property
    def last_seq(self) -> int:
        return self._last_seq

    def append(
        self,
        *,
        producer_id: str,
        role: str,
        event_type: str,
        payload: dict[str, object],
    ) -> EventEnvelope:
        next_seq = self._last_seq + 1
        event = EventEnvelope(
            schema_version="1.0",
            event_id=f"evt_{self.room_id}_{next_seq}_{uuid.uuid4().hex[:6]}",
            room_id=self.room_id,
            room_seq=next_seq,
            producer_id=producer_id,
            role=role,
            event_type=event_type,
            ts_ms=_ts_ms(),
            idempotency_key=f"{self.room_id}:{producer_id}:{next_seq}",
            payload=payload,
        )
        event.validate()
        self._last_seq = next_seq
        self._events.append(event)
        return event

    def replay(self, after_seq: int = 0) -> list[EventEnvelope]:
        return [event for event in self._events if event.room_seq > after_seq]

    def rebuild(self, projector: RoomProjector | None = None) -> RoomProjector:
        target = projector or RoomProjector(self.room_id)
        for event in self._events:
            target.apply(event)
        return target
