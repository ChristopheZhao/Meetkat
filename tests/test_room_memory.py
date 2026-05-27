import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from decision_room.memory import (
    LongTermLesson,
    LongTermLessonStore,
    RoomMemoryStore,
    agent_scope,
    mas_scope,
    memory_recall_for_role,
    persist_meeting_lessons_from_snapshot,
)
from decision_room.memory.store import reset_default_stores_for_tests


class MemoryScopeIsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = RoomMemoryStore(storage_dir=Path(self._tmp.name))

    def test_agent_scope_facts_do_not_leak_into_shared(self) -> None:
        room_id = "room_iso"
        shared = mas_scope(room_id)
        impl = agent_scope(room_id, "implementation_specialist")
        risk = agent_scope(room_id, "risk_specialist")

        self.store.write_fact(room_id, impl, "draft", {"text": "impl draft"})
        self.store.write_fact(room_id, risk, "draft", {"text": "risk draft"})
        self.store.write_fact(room_id, shared, "decision_focus", "shared focus")

        self.assertEqual(self.store.read_fact(room_id, impl, "draft")["text"], "impl draft")
        self.assertEqual(self.store.read_fact(room_id, risk, "draft")["text"], "risk draft")
        # Reading via the WRONG scope must not return the other agent's draft.
        self.assertIsNone(self.store.read_fact(room_id, shared, "draft"))
        self.assertIsNone(self.store.read_fact(room_id, impl, "decision_focus"))
        self.assertEqual(
            self.store.read_fact(room_id, shared, "decision_focus"),
            "shared focus",
        )

    def test_recent_events_returns_only_requested_scope(self) -> None:
        room_id = "room_events"
        shared = mas_scope(room_id)
        local = agent_scope(room_id, "product_specialist")

        for index in range(5):
            self.store.record_event(room_id, shared, "specialist.claim", {"i": index})
        for index in range(3):
            self.store.record_event(room_id, local, "agent.note", {"i": index})

        shared_events = self.store.recent_events(room_id, shared, limit=10)
        local_events = self.store.recent_events(room_id, local, limit=10)
        self.assertEqual(len(shared_events), 5)
        self.assertEqual(len(local_events), 3)
        self.assertEqual(shared_events[0].event_type, "specialist.claim")
        self.assertEqual(local_events[0].event_type, "agent.note")

    def test_writes_are_persisted_to_disk_per_scope(self) -> None:
        room_id = "room_disk"
        self.store.write_fact(room_id, mas_scope(room_id), "k", {"v": 1})
        path = (
            Path(self._tmp.name)
            / "rooms"
            / "room_disk"
            / "mas_room_disk.json"
        )
        self.assertTrue(path.exists(), f"expected {path} on disk after write")
        data = json.loads(path.read_text())
        self.assertEqual(data["facts"]["k"]["value"], {"v": 1})


class LongTermLessonStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = LongTermLessonStore(
            storage_dir=Path(self._tmp.name),
            max_lessons_per_role=3,
        )

    def test_append_and_recent_return_last_n_lessons(self) -> None:
        for index in range(5):
            self.store.append(
                LongTermLesson(
                    role="implementation_specialist",
                    text=f"lesson #{index}",
                    room_id=f"room_{index}",
                    decision_focus="focus",
                    decision_candidate=f"candidate {index}",
                    conclusion_type="follow_up_required",
                )
            )
        recent = self.store.recent("implementation_specialist", limit=5)
        # max_lessons_per_role=3 so only the last 3 survive.
        self.assertEqual(len(recent), 3)
        self.assertEqual(recent[-1].text, "lesson #4")
        self.assertEqual(recent[0].text, "lesson #2")

    def test_append_ignores_empty_text(self) -> None:
        self.store.append(LongTermLesson(role="risk_specialist", text=""))
        self.assertEqual(self.store.recent("risk_specialist"), [])

    def test_file_per_role(self) -> None:
        self.store.append(LongTermLesson(role="risk_specialist", text="risk lesson"))
        self.store.append(
            LongTermLesson(role="implementation_specialist", text="impl lesson")
        )
        risk_path = Path(self._tmp.name) / "long_term" / "risk_specialist.json"
        impl_path = (
            Path(self._tmp.name) / "long_term" / "implementation_specialist.json"
        )
        self.assertTrue(risk_path.exists())
        self.assertTrue(impl_path.exists())
        self.assertEqual(len(json.loads(risk_path.read_text())), 1)
        self.assertEqual(len(json.loads(impl_path.read_text())), 1)


class MemoryRecallForRoleTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.room_store = RoomMemoryStore(storage_dir=Path(self._tmp.name))
        self.long_store = LongTermLessonStore(storage_dir=Path(self._tmp.name))

    def test_recall_pulls_shared_facts_agent_locals_and_role_lessons(self) -> None:
        room_id = "room_recall"
        self.room_store.write_fact(room_id, mas_scope(room_id), "decision_focus", "topic A")
        self.room_store.write_fact(
            room_id,
            agent_scope(room_id, "implementation_specialist"),
            "last_self_claim",
            {"claim": "impl claim"},
        )
        self.room_store.record_event(
            room_id,
            mas_scope(room_id),
            "specialist.claim",
            {"role": "risk_specialist", "round_index": 1, "claim": "watch out for X"},
        )
        self.long_store.append(
            LongTermLesson(
                role="implementation_specialist",
                text="prior lesson",
                room_id="room_prev",
                decision_focus="topic B",
                decision_candidate="prior candidate",
                conclusion_type="decision_ready",
            )
        )

        recall = memory_recall_for_role(
            room_id=room_id,
            role="implementation_specialist",
            room_store=self.room_store,
            long_term_store=self.long_store,
        )
        self.assertEqual(recall["shared_facts"]["decision_focus"], "topic A")
        self.assertEqual(
            recall["agent_local_facts"]["last_self_claim"]["claim"],
            "impl claim",
        )
        self.assertEqual(len(recall["recent_shared_events"]), 1)
        self.assertEqual(recall["recent_shared_events"][0]["event_type"], "specialist.claim")
        self.assertEqual(len(recall["role_lessons"]), 1)
        self.assertEqual(recall["role_lessons"][0]["text"], "prior lesson")

    def test_recall_returns_empty_shapes_when_nothing_recorded(self) -> None:
        recall = memory_recall_for_role(
            room_id="room_empty",
            role="risk_specialist",
            room_store=self.room_store,
            long_term_store=self.long_store,
        )
        self.assertEqual(recall["shared_facts"], {})
        self.assertEqual(recall["agent_local_facts"], {})
        self.assertEqual(recall["recent_shared_events"], [])
        self.assertEqual(recall["role_lessons"], [])


class PersistMeetingLessonsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.long_store = LongTermLessonStore(storage_dir=Path(self._tmp.name))

    def test_persists_one_lesson_per_specialist_role_in_transcript(self) -> None:
        class _Entry:
            def __init__(self, role: str, artifacts: dict | None = None) -> None:
                self.role = role
                self.artifacts = artifacts or {}

        class _Snap:
            candidate_decision = "Adopt a phased rollout starting with cohort A."
            conclusion_type = "decision_ready"
            conclusion_reason = "Cross-role evidence converges on the phased rollout."
            current_focus = "rollout phasing"
            goal = "decide rollout"
            transcript = [
                _Entry("host"),
                _Entry(
                    "implementation_specialist",
                    {"claim": "phase by cohort to limit blast radius", "confidence": 0.82},
                ),
                _Entry(
                    "risk_specialist",
                    {"claim": "rollback path must be auto-validated per phase", "confidence": 0.74},
                ),
                _Entry("synthesis"),
                _Entry("system"),
            ]

        persisted = persist_meeting_lessons_from_snapshot(
            room_id="room_persist",
            snapshot=_Snap(),
            long_term_store=self.long_store,
        )
        roles = sorted(lesson.role for lesson in persisted)
        self.assertEqual(roles, ["implementation_specialist", "risk_specialist"])

    def test_skips_when_no_decision_candidate(self) -> None:
        class _Snap:
            candidate_decision = ""
            transcript: list = []

        persisted = persist_meeting_lessons_from_snapshot(
            room_id="room_empty",
            snapshot=_Snap(),
            long_term_store=self.long_store,
        )
        self.assertEqual(persisted, [])

    def test_lesson_text_is_per_role_not_broadcast(self) -> None:
        """Regression: lesson text used to be identical across all roles
        (the decision-record broadcast). Each role should carry ITS OWN
        claim so future recall represents per-role learning."""

        class _Entry:
            def __init__(self, role: str, artifacts: dict | None = None) -> None:
                self.role = role
                self.artifacts = artifacts or {}

        class _Snap:
            candidate_decision = "Decide rollout phasing"
            conclusion_type = "decision_ready"
            conclusion_reason = "Specialists aligned on phasing"
            current_focus = ""
            goal = ""
            transcript = [
                _Entry(
                    "implementation_specialist",
                    {"claim": "implementation favors per-cohort rollout", "confidence": 0.81},
                ),
                _Entry(
                    "risk_specialist",
                    {"claim": "risk demands an auto-rollback validator", "confidence": 0.73},
                ),
            ]

        persisted = persist_meeting_lessons_from_snapshot(
            room_id="room_per_role",
            snapshot=_Snap(),
            long_term_store=self.long_store,
        )
        by_role = {lesson.role: lesson.text for lesson in persisted}
        self.assertIn("per-cohort rollout", by_role["implementation_specialist"])
        self.assertIn("auto-rollback validator", by_role["risk_specialist"])
        self.assertNotEqual(
            by_role["implementation_specialist"],
            by_role["risk_specialist"],
            "per-role lesson text must differ — no broadcast",
        )

    def test_lesson_text_falls_back_to_broadcast_when_no_claim(self) -> None:
        class _Entry:
            def __init__(self, role: str) -> None:
                self.role = role
                self.artifacts = {}

        class _Snap:
            candidate_decision = "Defer decision until inputs arrive"
            conclusion_type = "follow_up_required"
            conclusion_reason = "missing operator inputs"
            current_focus = ""
            goal = ""
            transcript = [_Entry("implementation_specialist")]

        persisted = persist_meeting_lessons_from_snapshot(
            room_id="room_fb",
            snapshot=_Snap(),
            long_term_store=self.long_store,
            speakers=["implementation_specialist"],
        )
        self.assertEqual(len(persisted), 1)
        self.assertIn("Defer decision", persisted[0].text)


class ApplyJournalEventTests(unittest.TestCase):
    """B1 regression: RoomMemoryStore.apply_journal_event must update local
    state from a journal-shaped payload so the store can be rebuilt by
    replaying the RoomEventJournal (single SoT discipline)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = RoomMemoryStore(storage_dir=Path(self._tmp.name))

    def test_apply_fact_payload_updates_state(self) -> None:
        self.store.apply_journal_event(
            {
                "scope": mas_scope("room_apply"),
                "fact_key": "latest_supervisor_plan",
                "fact_value": {"round_index": 1, "decision_focus": "x"},
            },
            "room_apply",
        )
        value = self.store.read_fact(
            "room_apply", mas_scope("room_apply"), "latest_supervisor_plan"
        )
        self.assertEqual(value["decision_focus"], "x")

    def test_apply_event_payload_appends_event(self) -> None:
        for index in range(3):
            self.store.apply_journal_event(
                {
                    "scope": mas_scope("room_apply"),
                    "memory_event_type": "specialist.claim",
                    "memory_event_payload": {"role": "risk_specialist", "i": index},
                },
                "room_apply",
            )
        events = self.store.recent_events("room_apply", mas_scope("room_apply"))
        self.assertEqual(len(events), 3)
        self.assertEqual(events[0].event_type, "specialist.claim")

    def test_apply_skips_payload_without_scope(self) -> None:
        # No-op, no exception
        self.store.apply_journal_event({}, "room_apply")
        self.store.apply_journal_event({"scope": ""}, "room_apply")
        self.assertEqual(self.store.all_facts("room_apply", mas_scope("room_apply")), {})

    def test_replay_via_apply_reconstructs_identical_state(self) -> None:
        # Original write path
        self.store.write_fact("room_r", mas_scope("room_r"), "k1", {"v": 1})
        self.store.record_event(
            "room_r", mas_scope("room_r"), "specialist.claim", {"role": "x"}
        )
        original = self.store.snapshot("room_r")

        # Rebuild a fresh store from journal-shaped payloads only
        replay = RoomMemoryStore(storage_dir=Path(self._tmp.name) / "replay")
        replay.apply_journal_event(
            {
                "scope": mas_scope("room_r"),
                "fact_key": "k1",
                "fact_value": {"v": 1},
            },
            "room_r",
        )
        replay.apply_journal_event(
            {
                "scope": mas_scope("room_r"),
                "memory_event_type": "specialist.claim",
                "memory_event_payload": {"role": "x"},
            },
            "room_r",
        )
        rebuilt = replay.snapshot("room_r")
        # Fact dict matches
        self.assertEqual(
            original[mas_scope("room_r")]["facts"]["k1"]["value"],
            rebuilt[mas_scope("room_r")]["facts"]["k1"]["value"],
        )
        # Event type matches
        self.assertEqual(
            original[mas_scope("room_r")]["events"][0]["event_type"],
            rebuilt[mas_scope("room_r")]["events"][0]["event_type"],
        )


class MemoryEnvOverrideTests(unittest.TestCase):
    def test_env_var_overrides_default_storage_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"MEETKAT_MEMORY_DIR": tmp}, clear=False):
                reset_default_stores_for_tests()
                try:
                    from decision_room.memory.store import default_room_memory_store

                    store = default_room_memory_store()
                    self.assertEqual(
                        store.storage_dir.resolve(),
                        (Path(tmp) / "rooms").resolve(),
                    )
                finally:
                    reset_default_stores_for_tests()


if __name__ == "__main__":
    unittest.main()
