import unittest

from decision_room.orchestration.brief_planner import MeetingBrief, RequirementPlanningService
from decision_room.orchestration.pre_room_planning import (
    PreRoomPlanningWorkflow,
    resolve_turn_specialists,
)


class StaticRequirementPlanner:
    def plan_requirement(self, requirement: str) -> MeetingBrief:
        return MeetingBrief(
            requirement=requirement,
            topic="Replayable engineering review room",
            goal="Reach a host-led decision flow with explicit runtime and product tradeoffs.",
            constraints=[
                "Replay and live state must stay aligned.",
                "Human override must remain available.",
                "The room should keep UI behavior grounded in room events.",
            ],
            open_questions=["Which specialist should be invited first when runtime risk is high?"],
            current_focus="decide which technical and product specialists should be available before the room starts",
        )


class PreRoomPlanningWorkflowTests(unittest.TestCase):
    def test_workflow_builds_host_led_plan_with_dynamic_specialists(self) -> None:
        workflow = PreRoomPlanningWorkflow(
            requirement_planner=RequirementPlanningService(
                primary_planner=StaticRequirementPlanner()
            )
        )

        plan = workflow.plan_room(
            "Need a replayable engineering review room with strong override semantics."
        )

        self.assertEqual(plan.meeting_objective, "Reach a host-led decision flow with explicit runtime and product tradeoffs.")
        self.assertEqual([profile.role for profile in plan.active_agents], ["host"])

        roles = [item.role for item in plan.candidate_specialist_roster]
        self.assertEqual(len(roles), len(set(roles)))
        self.assertTrue(set(roles).issubset({"implementation_specialist", "risk_specialist", "product_specialist", "operations_specialist"}))
        self.assertIn("implementation_specialist", roles)
        self.assertIn("risk_specialist", roles)

        synthesis_profiles = [
            profile for profile in plan.agent_profiles if profile.role == "synthesis"
        ]
        self.assertEqual(len(synthesis_profiles), 1)
        self.assertFalse(synthesis_profiles[0].speaking)
        self.assertEqual(synthesis_profiles[0].activation, "memory_backed")

        selected = resolve_turn_specialists(
            type("Snapshot", (), {"planning_artifacts": plan.planning_payload()})(),
            ["risk_specialist", "implementation_specialist"],
        )
        self.assertEqual(
            [item.role for item in selected],
            ["risk_specialist", "implementation_specialist"],
        )


if __name__ == "__main__":
    unittest.main()
