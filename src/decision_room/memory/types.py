from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class MemoryScope:
    """A namespace key for memory writes/reads.

    ``mas:{room_id}`` is read/write by every agent in the same room — the
    shared scratchpad. ``agent:{room_id}:{role}`` is the agent's local
    scratchpad and is read by that agent only.
    """

    scope: str

    @property
    def is_shared(self) -> bool:
        return self.scope.startswith("mas:")

    @property
    def is_agent_local(self) -> bool:
        return self.scope.startswith("agent:")


@dataclass(frozen=True)
class MemoryFact:
    """A single key/value fact recorded under a scope."""

    key: str
    value: Any
    ts: float = field(default_factory=lambda: time.time())

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MemoryEvent:
    """A timestamped event in the per-scope event log."""

    event_type: str
    payload: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=lambda: time.time())

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LongTermLesson:
    """A short distilled lesson stored per specialist role across rooms."""

    role: str
    text: str
    room_id: str = ""
    decision_focus: str = ""
    decision_candidate: str = ""
    conclusion_type: str = ""
    ts: float = field(default_factory=lambda: time.time())

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)
