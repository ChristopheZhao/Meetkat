import unittest

from decision_room.orchestration.brief_planner import (
    HeuristicRequirementPlanner,
    MeetingBrief,
    RequirementPlanningError,
    RequirementPlanningService,
    build_meeting_brief_from_requirement,
)


class MeetingBriefPlannerTests(unittest.TestCase):
    def test_diagnostic_stub_is_transparent(self) -> None:
        brief = build_meeting_brief_from_requirement(
            "Build a multi-agent meeting room MVP for engineering design reviews."
        )
        self.assertEqual(brief.brief_source, "fallback")
        self.assertIn("diagnostic stub", brief.brief_source_reason)
        self.assertIn("Primary requirement planner is unavailable", brief.goal)
        self.assertIn("explicit fallback stub", brief.constraints[0])
        self.assertGreater(len(brief.open_questions), 0)

    def test_service_raises_planner_error_by_default(self) -> None:
        class FailingPlanner:
            def plan_requirement(self, requirement: str) -> MeetingBrief:
                raise RequirementPlanningError(
                    "provider network error",
                    error_code="planner_upstream_error",
                    status_code=502,
                    can_fallback=True,
                )

        service = RequirementPlanningService(
            primary_planner=FailingPlanner(),
            fallback_planner=HeuristicRequirementPlanner(),
        )

        with self.assertRaises(RequirementPlanningError) as ctx:
            service.plan_requirement("Need an agentic meeting room for PM and engineering.")
        self.assertEqual(ctx.exception.error_code, "planner_upstream_error")

    def test_service_uses_fallback_only_when_explicitly_enabled(self) -> None:
        class FailingPlanner:
            def plan_requirement(self, requirement: str) -> MeetingBrief:
                raise RequirementPlanningError(
                    "provider network error",
                    error_code="planner_upstream_error",
                    status_code=502,
                    can_fallback=True,
                )

        service = RequirementPlanningService(
            primary_planner=FailingPlanner(),
            fallback_planner=HeuristicRequirementPlanner(),
        )
        brief = service.plan_requirement(
            "Need an agentic meeting room for PM and engineering.",
            allow_fallback=True,
        )
        self.assertEqual(brief.brief_source, "fallback")
        self.assertIn("explicit fallback", brief.brief_source_reason)


if __name__ == "__main__":
    unittest.main()
