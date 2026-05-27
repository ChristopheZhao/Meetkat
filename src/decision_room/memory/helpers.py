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
    max_lesson_chars: int = 480,
) -> list[LongTermLesson]:
    """Write one lesson per participating specialist role from a closed room.

    Per-role lesson semantics: lesson text is anchored to that role's most
    recent claim found in the transcript, plus a short reference to the
    meeting decision and conclusion type. This makes ``role_lessons`` in
    a future ``memory_recall`` block represent "what this role actually
    argued and how it landed" rather than just rebroadcasting the shared
    decision record under N filenames.

    Falls back to the decision-record broadcast (the older behaviour) only
    when the transcript does not expose a per-role claim — e.g., a meeting
    that ended before any specialist spoke.
    """
    candidate = _normalize_text(getattr(snapshot, "candidate_decision", ""))
    if not candidate:
        return []
    conclusion_type = _normalize_text(getattr(snapshot, "conclusion_type", ""))
    conclusion_reason = _normalize_text(getattr(snapshot, "conclusion_reason", ""))
    focus = _normalize_text(getattr(snapshot, "current_focus", "")) or _normalize_text(
        getattr(snapshot, "goal", "")
    )

    transcript = getattr(snapshot, "transcript", []) or []
    role_claims: dict[str, dict[str, Any]] = {}
    for entry in transcript:
        role = getattr(entry, "role", "") or ""
        if role in {"host", "synthesis", "system", "human"} or not role:
            continue
        artifacts = getattr(entry, "artifacts", None)
        if not isinstance(artifacts, dict):
            continue
        claim = _normalize_text(artifacts.get("claim"))
        if not claim:
            continue
        try:
            confidence = float(artifacts.get("confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        # Keep the LAST claim per role — later iterations overwrite.
        role_claims[role] = {"claim": claim, "confidence": confidence}

    if speakers is None:
        target_roles = list(role_claims.keys())
        # Cover roles that participated but did not get a claim parsed:
        # walk transcript order so output ordering is deterministic.
        for entry in transcript:
            role = getattr(entry, "role", "") or ""
            if (
                role
                and role not in {"host", "synthesis", "system", "human"}
                and role not in target_roles
            ):
                target_roles.append(role)
    else:
        target_roles = [role for role in speakers if role]

    persisted: list[LongTermLesson] = []
    broadcast_fallback = candidate
    if conclusion_reason:
        broadcast_fallback = f"{candidate} — {conclusion_reason}"
    for role in target_roles:
        if not role or not role.strip():
            continue
        role_info = role_claims.get(role)
        if role_info:
            text = (
                f"作为 {role}，我主张：{role_info['claim']}"
                f"（信心 {role_info['confidence']:.2f}）。"
                f"本会议候选决策：{candidate}"
            )
        else:
            text = broadcast_fallback
        if len(text) > max_lesson_chars:
            text = text[: max_lesson_chars - 1].rstrip() + "…"
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
