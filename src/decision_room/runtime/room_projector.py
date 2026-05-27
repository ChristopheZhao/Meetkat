from __future__ import annotations

from typing import Any, Protocol

from .events import EventEnvelope
from .room_models import ConsensusState, Participant, RoomSnapshot, TranscriptEntry


class _MemoryStoreLike(Protocol):
    def apply_journal_event(self, payload: dict[str, Any], room_id: str) -> None:
        ...


class RoomProjector:
    """Projects the room event log into a frontend-friendly snapshot.

    Optionally also routes ``memory.write`` journal events into a
    ``RoomMemoryStore`` so the store stays a true projection of the journal
    (single SoT discipline from MAS-harness-architecture-baseline §2.5).
    """

    def __init__(
        self,
        room_id: str,
        memory_store: _MemoryStoreLike | None = None,
    ) -> None:
        self.snapshot = RoomSnapshot(room_id=room_id)
        self._seen_event_ids: set[str] = set()
        self._pending_chunks: dict[str, str] = {}
        self._memory_store = memory_store
        self._room_id = room_id

    def apply(self, event: EventEnvelope) -> RoomSnapshot:
        if event.event_id in self._seen_event_ids:
            return self.snapshot
        self._seen_event_ids.add(event.event_id)

        snapshot = self.snapshot
        snapshot.last_seq = max(snapshot.last_seq, event.room_seq)
        snapshot.updated_at_ms = event.ts_ms

        if event.event_type == "room.started":
            self._apply_room_started(event)
        elif event.event_type == "planning.completed":
            self._apply_planning_completed(event)
        elif event.event_type == "agent.joined":
            self._apply_agent_joined(event)
        elif event.event_type == "message.chunk":
            self._apply_message_chunk(event)
        elif event.event_type == "message.commit":
            self._apply_message_commit(event)
        elif event.event_type in {"agent.message", "human.message", "human.override"}:
            self._apply_message_event(event)
        elif event.event_type == "agent.summary":
            self._apply_agent_summary(event)
        elif event.event_type == "consensus.check":
            self._apply_consensus(event)
        elif event.event_type in {"agent.handoff", "agent.challenge"}:
            self._apply_structured_note(event)
        elif event.event_type == "meeting.ended":
            self._apply_meeting_ended(event)
        elif event.event_type == "memory.write":
            self._apply_memory_write(event)

        return snapshot

    def _apply_memory_write(self, event: EventEnvelope) -> None:
        if self._memory_store is None:
            return
        try:
            self._memory_store.apply_journal_event(event.payload, self._room_id)
        except Exception:
            # Memory projection is a best-effort denormalization; do not let
            # store errors poison snapshot projection.
            return

    def _apply_room_started(self, event: EventEnvelope) -> None:
        payload = event.payload
        snapshot = self.snapshot
        snapshot.requirement = str(payload.get("requirement", ""))
        snapshot.topic = str(payload.get("topic", ""))
        snapshot.goal = str(payload.get("goal", ""))
        snapshot.brief_source = str(payload.get("brief_source", snapshot.brief_source))
        snapshot.brief_source_reason = str(
            payload.get("brief_source_reason", snapshot.brief_source_reason)
        )
        snapshot.mode = str(payload.get("mode", "agent_first"))
        snapshot.phase = str(payload.get("phase", "explore"))
        snapshot.constraints = self._as_string_list(payload.get("constraints"))
        snapshot.open_questions = self._as_string_list(payload.get("open_questions"))
        snapshot.status = str(payload.get("status", "running"))
        snapshot.current_focus = str(payload.get("current_focus", ""))
        snapshot.created_at_ms = event.ts_ms
        snapshot.updated_at_ms = event.ts_ms
        snapshot.resume_token = str(payload.get("resume_token", snapshot.room_id))

    def _apply_planning_completed(self, event: EventEnvelope) -> None:
        payload = event.payload
        self.snapshot.planning_artifacts = payload if isinstance(payload, dict) else {}

    def _apply_agent_joined(self, event: EventEnvelope) -> None:
        payload = event.payload
        participant_id = str(payload.get("participant_id", event.producer_id))
        if any(item.participant_id == participant_id for item in self.snapshot.participants):
            return

        self.snapshot.participants.append(
            Participant(
                participant_id=participant_id,
                role=event.role,
                identity=str(payload.get("identity", "agent")),
                display_name=str(payload.get("display_name", event.role.title())),
                activation=str(payload.get("activation", "")),
                speaking=bool(payload.get("speaking", True)),
                capability_profile=str(payload.get("capability_profile", "")),
                join_reason=str(payload.get("join_reason", "")),
                focus_areas=self._as_string_list(payload.get("focus_areas")),
            )
        )

    def _apply_message_chunk(self, event: EventEnvelope) -> None:
        message_id = str(event.payload.get("message_id", event.event_id))
        chunk = str(event.payload.get("text_chunk", ""))
        self._pending_chunks[message_id] = self._pending_chunks.get(message_id, "") + chunk
        self.snapshot.live_chunks[message_id] = self._pending_chunks[message_id]

    def _apply_message_commit(self, event: EventEnvelope) -> None:
        message_id = str(event.payload.get("message_id", event.event_id))
        text = str(event.payload.get("text", ""))
        if text:
            self._pending_chunks[message_id] = text
            self.snapshot.live_chunks[message_id] = text

    def _apply_message_event(self, event: EventEnvelope) -> None:
        payload = event.payload
        message_id = str(payload.get("message_id", event.event_id))
        text = str(payload.get("text", ""))
        title = str(payload.get("title", ""))
        artifacts = payload.get("artifacts", {})

        self.snapshot.transcript.append(
            TranscriptEntry(
                message_id=message_id,
                seq=event.room_seq,
                ts_ms=event.ts_ms,
                role=event.role,
                producer_id=event.producer_id,
                event_type=event.event_type,
                text=text,
                title=title,
                artifacts=artifacts if isinstance(artifacts, dict) else {},
            )
        )
        self._pending_chunks.pop(message_id, None)
        self.snapshot.live_chunks.pop(message_id, None)

        if event.event_type == "agent.message":
            round_index = payload.get("round_index")
            if round_index is not None:
                self.snapshot.round_index = int(round_index)
            phase = payload.get("phase")
            if phase:
                self.snapshot.phase = str(phase)
            current_focus = payload.get("current_focus")
            if current_focus:
                self.snapshot.current_focus = str(current_focus)
            if event.role == "host":
                turns = artifacts.get("turns", {}) if isinstance(artifacts, dict) else {}
                self.snapshot.current_turns = self._as_turns(turns)
        elif event.event_type == "human.message":
            self.snapshot.last_human_message = text
        elif event.event_type == "human.override":
            self.snapshot.last_override = text

    def _apply_agent_summary(self, event: EventEnvelope) -> None:
        payload = event.payload
        self.snapshot.candidate_decision = str(payload.get("decision_candidate", ""))
        self.snapshot.action_items = self._as_string_list(payload.get("action_items"))
        self.snapshot.open_questions = self._as_string_list(payload.get("open_questions"))
        self.snapshot.conclusion_type = str(payload.get("conclusion_type", ""))
        self.snapshot.conclusion_reason = str(payload.get("conclusion_reason", ""))
        # LLM-recommended next-phase override (set on snapshot so the next
        # round's ``_phase_for_round`` can honor it before falling back to
        # the rule-based derivation).
        recommended = str(payload.get("recommended_next_phase", "")).strip().lower()
        if recommended in {"explore", "debate", "synthesize", "decide"}:
            self.snapshot.recommended_next_phase = recommended
        else:
            # Stale recommendation must not persist across rounds; clear it
            # so the rule-based path takes over when the LLM doesn't recommend.
            self.snapshot.recommended_next_phase = ""
        recommended_action = str(
            payload.get("recommended_next_action", "")
        ).strip().lower().replace("-", "_")
        if recommended_action in {
            "speak",
            "handoff",
            "check_consensus",
            "end_meeting",
            "noop",
        }:
            self.snapshot.recommended_next_action = recommended_action
        else:
            self.snapshot.recommended_next_action = ""

    def _apply_consensus(self, event: EventEnvelope) -> None:
        payload = event.payload
        self.snapshot.consensus = ConsensusState(
            score=float(payload.get("score", 0.0)),
            should_end=bool(payload.get("should_end", False)),
            reason=str(payload.get("reason", "")),
            support=float(payload.get("support", 0.0)),
            confidence=float(payload.get("confidence", 0.0)),
            disagreement_index=float(payload.get("disagreement_index", 1.0)),
            margin_top1_top2=float(payload.get("margin_top1_top2", 0.0)),
        )

    def _apply_structured_note(self, event: EventEnvelope) -> None:
        payload = event.payload
        if event.event_type == "agent.handoff":
            text = (
                f"Handoff to {payload.get('to_role', 'next role')}: "
                f"{payload.get('reason', 'continue the discussion')}"
            )
        else:
            text = (
                f"Challenge raised against {payload.get('target_role', 'proposal')}: "
                f"{payload.get('reason', 'needs closer review')}"
            )

        self.snapshot.transcript.append(
            TranscriptEntry(
                message_id=str(payload.get("message_id", event.event_id)),
                seq=event.room_seq,
                ts_ms=event.ts_ms,
                role=event.role,
                producer_id=event.producer_id,
                event_type=event.event_type,
                text=text,
                title=str(payload.get("title", "")),
                artifacts=payload if isinstance(payload, dict) else {},
            )
        )

    def _apply_meeting_ended(self, event: EventEnvelope) -> None:
        payload = event.payload
        self.snapshot.status = "ended"
        self.snapshot.ended_reason = str(payload.get("reason", "meeting ended"))
        self.snapshot.control_reason = str(
            payload.get("control_reason", self.snapshot.control_reason)
        )
        self.snapshot.orchestration_end_reason = str(
            payload.get(
                "orchestration_end_reason",
                self.snapshot.orchestration_end_reason,
            )
        )
        self.snapshot.conclusion_type = str(
            payload.get("conclusion_type", self.snapshot.conclusion_type)
        )
        self.snapshot.conclusion_reason = str(
            payload.get("conclusion_reason", self.snapshot.conclusion_reason)
        )
        if payload.get("decision_candidate"):
            self.snapshot.candidate_decision = str(payload.get("decision_candidate"))
        if payload.get("action_items"):
            self.snapshot.action_items = self._as_string_list(payload.get("action_items"))
        if payload.get("open_questions"):
            self.snapshot.open_questions = self._as_string_list(payload.get("open_questions"))

    def _as_string_list(self, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item) for item in value if str(item).strip()]

    def _as_turns(self, value: Any) -> list[dict[str, str]]:
        if not isinstance(value, list):
            return []
        turns: list[dict[str, str]] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role", "")).strip()
            task = str(item.get("task", "")).strip()
            if not role or not task:
                continue
            turns.append({"role": role, "task": task})
        return turns
