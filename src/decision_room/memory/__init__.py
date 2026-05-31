"""Decision-room memory system.

Two tiers, both file-backed JSON for the MVP:

- Short-term: ``RoomMemoryStore`` holds per-room facts and a bounded event
  deque. Scoped by ``mas:{room_id}`` (shared across all agents in this room)
  and ``agent:{room_id}:{role}`` (per-agent scratchpad). Every write also
  publishes a ``memory.write`` event onto the room journal so the
  ``RoomEventJournal`` stays the single source of truth and the file is a
  denormalized projection.

- Long-term: ``LongTermLessonStore`` keeps an append-only JSON list of
  lessons keyed by specialist role. Synthesis writeback at meeting end
  produces one short lesson per role; specialist prompts read the most
  recent ``N`` lessons for their role as ``memory_recall.role_lessons``.

Storage path defaults to ``$MEETKAT_MEMORY_DIR`` or ``~/.meetkat/memory``.
"""

from .helpers import (
    agent_scope,
    format_memory_recall_section,
    mas_scope,
    memory_recall_for_role,
    persist_meeting_lessons_from_snapshot,
)
from .store import (
    LongTermLessonStore,
    RoomMemoryStore,
    default_long_term_store,
    default_room_memory_store,
)
from .types import LongTermLesson, MemoryEvent, MemoryFact, MemoryScope

__all__ = [
    "LongTermLesson",
    "LongTermLessonStore",
    "MemoryEvent",
    "MemoryFact",
    "MemoryScope",
    "RoomMemoryStore",
    "agent_scope",
    "default_long_term_store",
    "default_room_memory_store",
    "format_memory_recall_section",
    "mas_scope",
    "memory_recall_for_role",
    "persist_meeting_lessons_from_snapshot",
]
