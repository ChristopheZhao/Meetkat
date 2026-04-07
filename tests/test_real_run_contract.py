import unittest

from decision_room.orchestration.real_run_contract import (
    build_host_prompts,
    build_meeting_brief,
    extract_json_object,
    parse_host_agenda,
)


class RealRunContractTests(unittest.TestCase):
    def test_build_host_prompts_contains_brief_constraints(self) -> None:
        brief = build_meeting_brief(
            topic="multi-agent meeting room MVP",
            next_focus="resolve top disagreement cluster",
        )
        system_prompt, user_prompt = build_host_prompts(brief)
        self.assertIn("Return exactly one JSON object", system_prompt)
        self.assertIn('"constraints"', user_prompt)
        self.assertIn('"C1"', user_prompt)

    def test_extract_json_object_supports_code_fence(self) -> None:
        raw = """```json
{"focus_points":[],"open_questions":[],"no_new_constraints":true}
```"""
        self.assertEqual(
            extract_json_object(raw),
            '{"focus_points":[],"open_questions":[],"no_new_constraints":true}',
        )

    def test_parse_host_agenda_accepts_valid_payload(self) -> None:
        raw = """
        {
          "focus_points": [
            {
              "title": "Pin down MVP scope",
              "reason": "The brief asks for an MVP direction and visible meeting flow.",
              "constraint_ids": ["C1", "C3"]
            },
            {
              "title": "Keep agent-led flow first",
              "reason": "The early phase is automation first even though humans can join.",
              "constraint_ids": ["C2"]
            },
            {
              "title": "Preserve architecture boundaries",
              "reason": "Hybrid MAS and backend decoupling are fixed constraints in the brief.",
              "constraint_ids": ["C4", "C5", "C6"]
            }
          ],
          "turns": [
            {
              "role": "implementation_specialist",
              "task": "Propose the minimum runtime contract that keeps replay authoritative."
            },
            {
              "role": "risk_specialist",
              "task": "Stress-test the proposal against override and fallback failure paths."
            }
          ],
          "open_questions": ["How much real-time interaction is needed in the first demo?"],
          "no_new_constraints": true
        }
        """
        agenda = parse_host_agenda(
            raw,
            {"C1", "C2", "C3", "C4", "C5", "C6"},
            {"implementation_specialist", "risk_specialist"},
        )
        self.assertEqual(len(agenda.focus_points), 3)
        self.assertEqual([turn.role for turn in agenda.turns], ["implementation_specialist", "risk_specialist"])
        self.assertTrue(agenda.no_new_constraints)

    def test_parse_host_agenda_rejects_unknown_constraint_id(self) -> None:
        raw = """
        {
          "focus_points": [
            {"title": "A", "reason": "B", "constraint_ids": ["C1"]},
            {"title": "C", "reason": "D", "constraint_ids": ["C9"]},
            {"title": "E", "reason": "F", "constraint_ids": ["C2"]}
          ],
          "turns": [{"role": "implementation_specialist", "task": "Do the implementation pass."}],
          "open_questions": [],
          "no_new_constraints": true
        }
        """
        with self.assertRaises(ValueError):
            parse_host_agenda(raw, {"C1", "C2"}, {"implementation_specialist"})

    def test_parse_host_agenda_rejects_unknown_specialist_role(self) -> None:
        raw = """
        {
          "focus_points": [
            {"title": "A", "reason": "B", "constraint_ids": ["C1"]},
            {"title": "C", "reason": "D", "constraint_ids": ["C2"]},
            {"title": "E", "reason": "F", "constraint_ids": ["C2"]}
          ],
          "turns": [{"role": "unknown_specialist", "task": "Do something."}],
          "open_questions": [],
          "no_new_constraints": true
        }
        """
        with self.assertRaises(ValueError):
            parse_host_agenda(raw, {"C1", "C2"}, {"implementation_specialist"})


if __name__ == "__main__":
    unittest.main()
