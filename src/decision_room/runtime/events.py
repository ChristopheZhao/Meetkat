from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict


@dataclass
class EventEnvelope:
    schema_version: str
    event_id: str
    room_id: str
    room_seq: int
    producer_id: str
    role: str
    event_type: str
    ts_ms: int
    idempotency_key: str
    payload: Dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if self.schema_version != "1.0":
            raise ValueError("unsupported schema version")

        if not self.event_id or not self.room_id or not self.producer_id:
            raise ValueError("event_id/room_id/producer_id must be non-empty")

        if not self.role or not self.event_type:
            raise ValueError("role/event_type must be non-empty")

        if self.room_seq < 0:
            raise ValueError("room_seq must be >= 0")

        if not self.idempotency_key:
            raise ValueError("idempotency_key must be non-empty")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def room_topic(room_id: str) -> str:
    return f"room.{room_id}.events"
