"""File-backed JSON storage for short-term + long-term memory.

The in-memory dict/deque is the live working copy; the JSON file is a
denormalized snapshot kept consistent at every write so a restart can
rehydrate. The room journal (``RoomEventJournal``) remains the single
source of truth — the on-disk JSON file is a projection and may be
regenerated from journal replay if the file is lost.
"""

from __future__ import annotations

import json
import os
import re
import threading
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Deque, Iterable

from .types import LongTermLesson, MemoryEvent, MemoryFact


def _default_memory_dir() -> Path:
    env = os.environ.get("MEETKAT_MEMORY_DIR", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return Path.home() / ".meetkat" / "memory"


_SAFE_NAME = re.compile(r"[^A-Za-z0-9_.\-]+")


def _safe_filename(name: str, fallback: str = "unknown") -> str:
    cleaned = _SAFE_NAME.sub("_", str(name or "").strip())
    return cleaned or fallback


@dataclass
class MemoryScopeState:
    """In-memory state for one (room_id, scope) tuple."""

    facts: dict[str, MemoryFact] = field(default_factory=dict)
    events: Deque[MemoryEvent] = field(default_factory=lambda: deque(maxlen=64))

    def to_payload(self) -> dict[str, Any]:
        return {
            "facts": {key: fact.to_payload() for key, fact in self.facts.items()},
            "events": [event.to_payload() for event in self.events],
        }


class RoomMemoryStore:
    """Short-term memory store keyed by (room_id, scope).

    Thread-safe for concurrent writes from the room runtime loop. Persists
    one JSON file per (room_id, scope) under ``{storage_dir}/rooms/``.
    """

    def __init__(self, storage_dir: Path | None = None) -> None:
        self._storage_dir = (storage_dir or _default_memory_dir()) / "rooms"
        self._lock = threading.Lock()
        self._state: dict[tuple[str, str], MemoryScopeState] = defaultdict(MemoryScopeState)

    @property
    def storage_dir(self) -> Path:
        return self._storage_dir

    def _path(self, room_id: str, scope: str) -> Path:
        return self._storage_dir / _safe_filename(room_id) / f"{_safe_filename(scope)}.json"

    def _persist(self, room_id: str, scope: str) -> None:
        state = self._state[(room_id, scope)]
        path = self._path(room_id, scope)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(state.to_payload(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(path)

    def write_fact(self, room_id: str, scope: str, key: str, value: Any) -> MemoryFact:
        fact = MemoryFact(key=str(key), value=value)
        with self._lock:
            self._state[(room_id, scope)].facts[fact.key] = fact
            self._persist(room_id, scope)
        return fact

    def read_fact(self, room_id: str, scope: str, key: str, default: Any = None) -> Any:
        with self._lock:
            state = self._state.get((room_id, scope))
            if state is None or key not in state.facts:
                return default
            return state.facts[key].value

    def all_facts(self, room_id: str, scope: str) -> dict[str, Any]:
        with self._lock:
            state = self._state.get((room_id, scope))
            if state is None:
                return {}
            return {key: fact.value for key, fact in state.facts.items()}

    def record_event(
        self,
        room_id: str,
        scope: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> MemoryEvent:
        event = MemoryEvent(event_type=str(event_type), payload=dict(payload or {}))
        with self._lock:
            self._state[(room_id, scope)].events.append(event)
            self._persist(room_id, scope)
        return event

    def recent_events(self, room_id: str, scope: str, limit: int = 10) -> list[MemoryEvent]:
        with self._lock:
            state = self._state.get((room_id, scope))
            if state is None:
                return []
            return list(state.events)[-limit:]

    def snapshot(self, room_id: str) -> dict[str, Any]:
        with self._lock:
            return {
                scope: state.to_payload()
                for (rid, scope), state in self._state.items()
                if rid == room_id
            }

    def reset_room(self, room_id: str) -> None:
        """Drop in-memory state for a room; leaves disk files in place."""
        with self._lock:
            keys = [key for key in self._state if key[0] == room_id]
            for key in keys:
                del self._state[key]


class LongTermLessonStore:
    """Append-only per-role lesson store, file-per-role JSON."""

    def __init__(self, storage_dir: Path | None = None, max_lessons_per_role: int = 50) -> None:
        self._storage_dir = (storage_dir or _default_memory_dir()) / "long_term"
        self._max_lessons = max(1, int(max_lessons_per_role))
        self._lock = threading.Lock()
        self._cache: dict[str, list[LongTermLesson]] = {}

    @property
    def storage_dir(self) -> Path:
        return self._storage_dir

    def _path(self, role: str) -> Path:
        return self._storage_dir / f"{_safe_filename(role)}.json"

    def _load(self, role: str) -> list[LongTermLesson]:
        if role in self._cache:
            return self._cache[role]
        path = self._path(role)
        if not path.exists():
            self._cache[role] = []
            return self._cache[role]
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            self._cache[role] = []
            return self._cache[role]
        lessons: list[LongTermLesson] = []
        if isinstance(raw, list):
            for item in raw:
                if not isinstance(item, dict):
                    continue
                lessons.append(
                    LongTermLesson(
                        role=str(item.get("role", role)),
                        text=str(item.get("text", "")).strip(),
                        room_id=str(item.get("room_id", "")),
                        decision_focus=str(item.get("decision_focus", "")),
                        decision_candidate=str(item.get("decision_candidate", "")),
                        conclusion_type=str(item.get("conclusion_type", "")),
                        ts=float(item.get("ts", 0.0)),
                    )
                )
        self._cache[role] = lessons
        return lessons

    def append(self, lesson: LongTermLesson) -> None:
        if not lesson.text.strip():
            return
        role_key = _safe_filename(lesson.role, fallback="role")
        with self._lock:
            lessons = self._load(role_key)
            lessons.append(lesson)
            # Cap the per-role history to keep file sizes bounded.
            if len(lessons) > self._max_lessons:
                lessons[:] = lessons[-self._max_lessons :]
            self._cache[role_key] = lessons
            path = self._path(role_key)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(
                json.dumps([item.to_payload() for item in lessons], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp.replace(path)

    def recent(self, role: str, limit: int = 5) -> list[LongTermLesson]:
        role_key = _safe_filename(role, fallback="role")
        with self._lock:
            lessons = self._load(role_key)
            if limit <= 0:
                return list(lessons)
            return list(lessons[-limit:])

    def append_many(self, lessons: Iterable[LongTermLesson]) -> None:
        for lesson in lessons:
            self.append(lesson)


_default_room_store: RoomMemoryStore | None = None
_default_long_term: LongTermLessonStore | None = None
_default_lock = threading.Lock()


def default_room_memory_store() -> RoomMemoryStore:
    global _default_room_store
    with _default_lock:
        if _default_room_store is None:
            _default_room_store = RoomMemoryStore()
        return _default_room_store


def default_long_term_store() -> LongTermLessonStore:
    global _default_long_term
    with _default_lock:
        if _default_long_term is None:
            _default_long_term = LongTermLessonStore()
        return _default_long_term


def reset_default_stores_for_tests() -> None:
    """Test helper — drop the cached singletons so tests can rebind paths."""
    global _default_room_store, _default_long_term
    with _default_lock:
        _default_room_store = None
        _default_long_term = None
