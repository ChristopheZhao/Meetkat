"""Helpers wrapping ``RoomMemoryStore`` + ``LongTermLessonStore`` for use
from the room runtime, supervisor, and specialist prompt builders."""

from __future__ import annotations

import re
from typing import Any, Iterable

from .store import LongTermLessonStore, RoomMemoryStore
from .types import LongTermLesson


def mas_scope(room_id: str) -> str:
    return f"mas:{room_id}"


def agent_scope(room_id: str, role: str) -> str:
    return f"agent:{room_id}:{role}"


def memory_recall_for_role(
    *,
    room_id: str,
    role: str,
    room_store: RoomMemoryStore,
    long_term_store: LongTermLessonStore,
    recent_event_limit: int = 6,
    role_lesson_limit: int = 5,
) -> dict[str, Any]:
    """Build a serializable ``memory_recall`` block for a specialist prompt.

    Shape::

        {
          "shared_facts": {...},
          "agent_local_facts": {...},
          "recent_shared_events": [...],
          "role_lessons": [
            {"text": ..., "decision_focus": ..., "decision_candidate": ..., "ts": ...},
            ...
          ]
        }
    """
    shared_facts = room_store.all_facts(room_id, mas_scope(room_id))
    agent_facts = room_store.all_facts(room_id, agent_scope(room_id, role))
    recent_events = [
        event.to_payload()
        for event in room_store.recent_events(room_id, mas_scope(room_id), recent_event_limit)
    ]
    lessons = [
        {
            "text": lesson.text,
            "decision_focus": lesson.decision_focus,
            "decision_candidate": lesson.decision_candidate,
            "conclusion_type": lesson.conclusion_type,
            "room_id": lesson.room_id,
            "ts": lesson.ts,
        }
        for lesson in long_term_store.recent(role, role_lesson_limit)
    ]
    return {
        "shared_facts": shared_facts,
        "agent_local_facts": agent_facts,
        "recent_shared_events": recent_events,
        "role_lessons": lessons,
    }


def _normalize_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def persist_meeting_lessons_from_snapshot(
    *,
    room_id: str,
    snapshot: Any,
    speakers: Iterable[str] | None = None,
    long_term_store: LongTermLessonStore,
    max_lesson_chars: int = 400,
) -> list[LongTermLesson]:
    """Write one lesson per participating specialist role from a closed room.

    Heuristic: lesson text = decision candidate (truncated) + one-line
    conclusion reason. Each lesson is tagged with the room_id, decision
    focus, candidate text, and conclusion_type for future recall ranking.
    Returns the persisted lessons (may be empty if there is no decision
    candidate or no speakers).
    """
    candidate = _normalize_text(getattr(snapshot, "candidate_decision", ""))
    if not candidate:
        return []
    conclusion_type = _normalize_text(getattr(snapshot, "conclusion_type", ""))
    conclusion_reason = _normalize_text(getattr(snapshot, "conclusion_reason", ""))
    focus = _normalize_text(getattr(snapshot, "current_focus", "")) or _normalize_text(
        getattr(snapshot, "goal", "")
    )
    if not speakers:
        # Fall back to transcript participants of role kind != host/synthesis/system/human.
        transcript = getattr(snapshot, "transcript", []) or []
        seen: list[str] = []
        for entry in transcript:
            role = getattr(entry, "role", "") or ""
            if role in {"host", "synthesis", "system", "human"} or not role:
                continue
            if role in seen:
                continue
            seen.append(role)
        speakers = seen
    persisted: list[LongTermLesson] = []
    text = candidate
    if conclusion_reason:
        text = f"{candidate} — {conclusion_reason}"
    if len(text) > max_lesson_chars:
        text = text[: max_lesson_chars - 1].rstrip() + "…"
    for role in speakers:
        if not role or not role.strip():
            continue
        lesson = LongTermLesson(
            role=role,
            text=text,
            room_id=room_id,
            decision_focus=focus,
            decision_candidate=candidate,
            conclusion_type=conclusion_type,
        )
        long_term_store.append(lesson)
        persisted.append(lesson)
    return persisted
