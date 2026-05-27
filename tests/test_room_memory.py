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
            def __init__(self, role: str) -> None:
                self.role = role

        class _Snap:
            candidate_decision = "Adopt a phased rollout starting with cohort A."
            conclusion_type = "decision_ready"
            conclusion_reason = "Cross-role evidence converges on the phased rollout."
            current_focus = "rollout phasing"
            goal = "decide rollout"
            transcript = [
                _Entry("host"),
                _Entry("implementation_specialist"),
                _Entry("risk_specialist"),
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
        impl = self.long_store.recent("implementation_specialist")
        self.assertEqual(len(impl), 1)
        self.assertIn("phased rollout", impl[0].text)

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
