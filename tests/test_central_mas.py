import json
import unittest

from decision_room.orchestration.central_executor import CentralizedMASExecutor
from decision_room.orchestration.central_mas import (
    LLMSupervisor,
    SpeakerSlot,
    parse_supervisor_plan,
    role_catalog_from_snapshot,
)
from decision_room.orchestration.room_executor import UnavailableRoomExecutor
from decision_room.mas.types import MeetingPhase
from decision_room.policies.fallback import DisasterOnlyFallbackPolicy
from decision_room.policies.routing_control import RoutingControlPolicy
from decision_room.providers import GenerateResponse, ProviderRegistry
from decision_room.routing.model_router import HybridModelRouter, RouterTargets
from decision_room.runtime.room_models import RoomSnapshot


SPECIALIST_ROSTER = [
    {
        "role": "implementation_specialist",
        "display_name": "Implementation Specialist",
        "capability_profile": "Evaluates feasibility and integration shape.",
        "prompt_contract": "Stay concrete and implementation-grounded.",
        "join_reason": "Runtime work needs engineering judgment.",
        "focus_areas": ["feasibility"],
        "ttl_rounds": 2,
        "turn_budget": 1,
    },
    {
        "role": "risk_specialist",
        "display_name": "Risk Specialist",
        "capability_profile": "Surfaces failure and recovery risk.",
        "prompt_contract": "Keep unresolved runtime risks explicit.",
        "join_reason": "Need challenge-oriented analysis.",
        "focus_areas": ["recovery"],
        "ttl_rounds": 2,
        "turn_budget": 1,
    },
    {
        "role": "product_specialist",
        "display_name": "Product Specialist",
        "capability_profile": "Keeps the room aligned with user goals.",
        "prompt_contract": "Argue from user impact and workflow visibility.",
        "join_reason": "Need product grounding.",
        "focus_areas": ["workflow"],
        "ttl_rounds": 2,
        "turn_budget": 1,
    },
]


def _snapshot(
    *,
    requirement: str = "Build a centralized multi-agent decision room with real LLM agents.",
    topic: str = "Centralized MAS decision room",
    goal: str = "Reach an executable decision with explicit role contracts.",
    current_focus: str = "kick off supervisor planning",
    constraints: list[str] | None = None,
) -> RoomSnapshot:
    return RoomSnapshot(
        room_id="room_test",
        requirement=requirement,
        topic=topic,
        goal=goal,
        current_focus=current_focus,
        constraints=list(constraints or ["WebSocket primary, SSE fallback."]),
        planning_artifacts={"candidate_specialist_roster": SPECIALIST_ROSTER},
    )


def _target(supplier: str = "qwen", model: str = "test-model"):
    from decision_room.mas.types import ModelTarget

    return ModelTarget(supplier=supplier, model=model)


def _router() -> HybridModelRouter:
    return HybridModelRouter(
        RoutingControlPolicy(DisasterOnlyFallbackPolicy()),
        targets=RouterTargets(
            default_target=_target(),
            escalation_target=_target(),
            disaster_fallback_target=_target(),
        ),
    )


def _supervisor_payload(
    *,
    requirement_hint: str,
    selected_roles: list[str],
) -> str:
    speakers = [
        {
            "agent": role,
            "run": True,
            "order": index + 1,
            # The supervisor MAY emit a focus_angle hint, but never a mission /
            # deliverable / constraints. Tests use focus_angle when present.
            "focus_angle": "" if index % 2 else f"lean into the {role} angle for this round",
        }
        for index, role in enumerate(selected_roles)
    ]
    return json.dumps(
        {
            "current_focus": f"close gaps in '{requirement_hint[:30]}'",
            "decision_focus": f"converge on direction for '{requirement_hint[:30]}'",
            "phase": "explore",
            "reason": "supervisor selected specialists relevant to this round",
            "open_questions": [],
            "speakers": speakers,
        }
    )


class SupervisorScriptedProvider:
    """Returns supervisor JSON keyed by the user prompt, plus reuses the
    existing FakeProvider-style canned responses for specialist + synthesis.
    """

    def __init__(self, selected_roles: list[str]) -> None:
        self.selected_roles = selected_roles
        self.supervisor_user_prompts: list[str] = []
        self.specialist_user_prompts: list[str] = []

    def generate(self, req):
        if "central supervisor" in req.system_prompt:
            self.supervisor_user_prompts.append(req.user_prompt)
            requirement_hint = ""
            if "requirement" in req.user_prompt:
                marker = '"requirement": "'
                start = req.user_prompt.find(marker)
                if start >= 0:
                    end = req.user_prompt.find('"', start + len(marker))
                    if end > start:
                        requirement_hint = req.user_prompt[start + len(marker) : end]
            return GenerateResponse(
                text=_supervisor_payload(
                    requirement_hint=requirement_hint or "central decision",
                    selected_roles=self.selected_roles,
                ),
                raw_response="",
            )
        if "Implementation Specialist" in req.system_prompt:
            self.specialist_user_prompts.append(req.user_prompt)
            return GenerateResponse(
                text=(
                    '{"title": "Implementation readout", "text": "Implementation feasibility argument.", '
                    '"claim": "Adopt the supervisor-driven topology with replayable journal events.", '
                    '"evidence": ["Journal stays SoT.", "Supervisor handles role assignment."], '
                    '"confidence": 0.74, "target_claim_ref": ""}'
                ),
                raw_response="",
            )
        if "Risk Specialist" in req.system_prompt:
            return GenerateResponse(
                text=(
                    '{"title": "Risk readout", "text": "Failure mode analysis.", '
                    '"claim": "Override and replay must stay first-class.", '
                    '"evidence": ["Without override the supervisor cannot be stopped.", "Replay covers crash recovery."], '
                    '"confidence": 0.66, "target_claim_ref": "Adopt the supervisor-driven topology with replayable journal events."}'
                ),
                raw_response="",
            )
        if "Product Specialist" in req.system_prompt:
            return GenerateResponse(
                text=(
                    '{"title": "Product readout", "text": "Operator workflow consideration.", '
                    '"claim": "Operators need visible assignment contracts in the room UI.", '
                    '"evidence": ["UI must show contracts.", "Decision focus must be visible."], '
                    '"confidence": 0.71, "target_claim_ref": "Override and replay must stay first-class."}'
                ),
                raw_response="",
            )
        return GenerateResponse(
            text=(
                '{"title": "Synthesis", "text": "Synthesis aligns on supervisor-driven direction.", '
                '"agreement": ["Journal stays SoT."], '
                '"disagreement": [], '
                '"open_questions": ["What metric closes the decision?"], '
                '"decision_candidate": "Adopt LLM supervisor with assignment contracts.", '
                '"action_item_draft": ["Wire frontend allowlist for central_mas."], '
                '"conclusion_type": "follow_up_required", '
                '"conclusion_reason": "Direction confirmed, follow-up implementation needed.", '
                '"should_end_meeting": false}'
            ),
            raw_response="",
        )


class ParseSupervisorPlanTests(unittest.TestCase):
    def test_parse_rejects_roles_outside_catalog(self) -> None:
        snapshot = _snapshot()
        catalog = role_catalog_from_snapshot(snapshot)
        payload = json.dumps(
            {
                "current_focus": "ignored",
                "decision_focus": "ignored",
                "phase": "explore",
                "reason": "test",
                "open_questions": [],
                "speakers": [
                    {"agent": "implementation_specialist", "run": True, "order": 1, "focus_angle": ""},
                    {"agent": "rogue_role", "run": True, "order": 2, "focus_angle": ""},
                ],
            }
        )
        plan = parse_supervisor_plan(
            payload,
            role_catalog=catalog,
            phase=MeetingPhase.EXPLORE,
            fallback_focus="fallback",
        )
        self.assertEqual([s.agent for s in plan.speakers], ["implementation_specialist"])

    def test_parse_drops_duplicate_speaker_entries(self) -> None:
        snapshot = _snapshot()
        catalog = role_catalog_from_snapshot(snapshot)
        payload = json.dumps(
            {
                "current_focus": "",
                "phase": "explore",
                "speakers": [
                    {"agent": "implementation_specialist", "order": 1, "focus_angle": ""},
                    {"agent": "implementation_specialist", "order": 2, "focus_angle": "duplicate"},
                    {"agent": "product_specialist", "order": 3, "focus_angle": ""},
                ],
            }
        )
        plan = parse_supervisor_plan(
            payload,
            role_catalog=catalog,
            phase=MeetingPhase.EXPLORE,
            fallback_focus="fallback",
        )
        self.assertEqual(
            [s.agent for s in plan.speakers],
            ["implementation_specialist", "product_specialist"],
        )

    def test_parse_accepts_legacy_assignment_contracts_key(self) -> None:
        """One-window backward compatibility: older supervisor prompts may still
        emit the legacy ``assignment_contracts`` field name; parser tolerates
        it but treats each entry as a speaker slot (focus_angle only)."""
        snapshot = _snapshot()
        catalog = role_catalog_from_snapshot(snapshot)
        payload = json.dumps(
            {
                "phase": "explore",
                "assignment_contracts": [
                    {"agent": "implementation_specialist", "order": 1, "focus_angle": "legacy"},
                ],
            }
        )
        plan = parse_supervisor_plan(
            payload,
            role_catalog=catalog,
            phase=MeetingPhase.EXPLORE,
            fallback_focus="fallback",
        )
        self.assertEqual([s.agent for s in plan.speakers], ["implementation_specialist"])

    def test_parse_raises_on_empty_speakers_list(self) -> None:
        snapshot = _snapshot()
        catalog = role_catalog_from_snapshot(snapshot)
        payload = json.dumps({"speakers": []})
        with self.assertRaises(ValueError):
            parse_supervisor_plan(
                payload,
                role_catalog=catalog,
                phase=MeetingPhase.EXPLORE,
                fallback_focus="fallback",
            )


class LLMSupervisorTests(unittest.IsolatedAsyncioTestCase):
    async def test_plan_round_returns_plan_with_real_contracts(self) -> None:
        provider = SupervisorScriptedProvider(
            selected_roles=["implementation_specialist", "risk_specialist", "product_specialist"]
        )
        registry = ProviderRegistry({"qwen": provider})
        supervisor = LLMSupervisor(registry=registry, router=_router())
        snapshot = _snapshot()
        from decision_room.mas.types import DecisionContext, DecisionSignals

        ctx = DecisionContext(
            room_id=snapshot.room_id,
            phase=MeetingPhase.EXPLORE,
            signals=DecisionSignals(),
        )
        route = _router().route(ctx)

        plan, _new_ctx, _new_route = await supervisor.plan_round(
            snapshot=snapshot,
            round_index=1,
            phase=MeetingPhase.EXPLORE,
            next_focus="kick off",
            route_ctx=ctx,
            route=route,
        )
        self.assertEqual(
            [s.agent for s in plan.speakers],
            ["implementation_specialist", "risk_specialist", "product_specialist"],
        )
        for slot in plan.speakers:
            self.assertIsInstance(slot, SpeakerSlot)
            self.assertTrue(slot.run)
            # focus_angle is optional — may be empty by design.
            self.assertIsInstance(slot.focus_angle, str)
        self.assertTrue(plan.decision_focus.strip())


class CentralizedMASExecutorTests(unittest.IsolatedAsyncioTestCase):
    async def _build_executor(
        self,
        selected_roles: list[str],
    ) -> tuple[CentralizedMASExecutor, SupervisorScriptedProvider]:
        provider = SupervisorScriptedProvider(selected_roles=selected_roles)
        registry = ProviderRegistry({"qwen": provider})
        executor = CentralizedMASExecutor(
            registry=registry,
            router=_router(),
            use_background_threads=False,
        )
        return executor, provider

    async def test_build_round_emits_central_mas_artifact_and_real_specialist_messages(
        self,
    ) -> None:
        executor, _provider = await self._build_executor(
            ["implementation_specialist", "risk_specialist", "product_specialist"]
        )
        snapshot = _snapshot()
        round_data = await executor.build_round(snapshot, round_index=1)

        self.assertEqual(round_data.messages[0].role, "host")
        self.assertIn("central_mas", round_data.messages[0].artifacts)
        bundle = round_data.messages[0].artifacts["central_mas"]
        self.assertEqual(bundle["topology"], "single_supervisor_shared_memory")
        self.assertEqual(
            [s["agent"] for s in bundle["speakers"]],
            ["implementation_specialist", "risk_specialist", "product_specialist"],
        )
        # Backward-compat legacy key still populated with same shape.
        self.assertEqual(bundle["assignment_contracts"], bundle["speakers"])
        self.assertEqual(
            [item["role"] for item in bundle["role_catalog"]],
            ["implementation_specialist", "risk_specialist", "product_specialist"],
        )
        # Crucially: speaker payload contains NO content fields. Supervisor
        # only orders; specialists author content.
        for speaker in bundle["speakers"]:
            self.assertNotIn("mission", speaker)
            self.assertNotIn("deliverable", speaker)
            self.assertNotIn("constraints", speaker)
        self.assertEqual(round_data.messages[1].role, "implementation_specialist")
        self.assertIn("speaker_slot", round_data.messages[1].artifacts)
        slot_payload = round_data.messages[1].artifacts["speaker_slot"]
        self.assertEqual(slot_payload["agent"], "implementation_specialist")
        # focus_angle is optional — may be empty string.
        self.assertIn("focus_angle", slot_payload)
        self.assertEqual(round_data.synthesis_message.role, "synthesis")

    async def test_supervisor_assignment_changes_when_requirement_changes(self) -> None:
        executor_a, provider_a = await self._build_executor(
            ["implementation_specialist", "risk_specialist"]
        )
        executor_b, provider_b = await self._build_executor(
            ["product_specialist", "implementation_specialist"]
        )

        snapshot_a = _snapshot(
            requirement="Architecture decision for runtime topology",
            topic="Architecture",
        )
        snapshot_b = _snapshot(
            requirement="Product positioning for the operator workflow",
            topic="Product",
        )

        round_a = await executor_a.build_round(snapshot_a, round_index=1)
        round_b = await executor_b.build_round(snapshot_b, round_index=1)
        roles_a = [s["agent"] for s in round_a.messages[0].artifacts["central_mas"]["speakers"]]
        roles_b = [s["agent"] for s in round_b.messages[0].artifacts["central_mas"]["speakers"]]
        self.assertNotEqual(roles_a, roles_b)
        # Each supervisor prompt should contain the requirement string verbatim
        self.assertTrue(any("Architecture decision" in prompt for prompt in provider_a.supervisor_user_prompts))
        self.assertTrue(any("Product positioning" in prompt for prompt in provider_b.supervisor_user_prompts))

    async def test_convergence_should_end_comes_from_consensus_strategy(self) -> None:
        executor, _provider = await self._build_executor(
            ["implementation_specialist", "risk_specialist", "product_specialist"]
        )
        snapshot = _snapshot()
        round_data = await executor.build_round(snapshot, round_index=1)
        # Synthesis fake returns "follow_up_required" + should_end_meeting=false,
        # so the executor must report should_end=False — i.e., not gated on a
        # round counter dressed up as a signal threshold.
        self.assertFalse(round_data.should_end)
        self.assertFalse(round_data.consensus_should_end)
        self.assertEqual(round_data.conclusion_type, "follow_up_required")

    async def test_from_mapping_returns_unavailable_when_provider_env_missing(self) -> None:
        executor = CentralizedMASExecutor.from_mapping({})
        self.assertIsInstance(executor, UnavailableRoomExecutor)


class BriefPlannerRegressionTests(unittest.TestCase):
    def test_centralized_requirement_planner_is_removed(self) -> None:
        import decision_room.orchestration.brief_planner as brief_planner

        self.assertFalse(hasattr(brief_planner, "CentralizedRequirementPlanner"))
        self.assertFalse(hasattr(brief_planner, "_central_constraints"))
        self.assertFalse(hasattr(brief_planner, "_central_open_questions"))
        self.assertFalse(hasattr(brief_planner, "_decision_object"))

    def test_orchestration_init_does_not_export_centralized_planner(self) -> None:
        import decision_room.orchestration as orch

        self.assertFalse(hasattr(orch, "CentralizedRequirementPlanner"))
        # New canonical exports:
        self.assertTrue(hasattr(orch, "LLMSupervisor"))
        self.assertTrue(hasattr(orch, "SupervisorPlan"))
        self.assertTrue(hasattr(orch, "AssignmentContract"))


class NativeAgentClarificationContractTests(unittest.TestCase):
    """Lock in the architecture decision: clarification is in-meeting agent
    dialogue, not a pre-room slot-filling form. These tests guard against
    the deprecated preflight-gate UX silently coming back."""

    def test_planner_prompt_forbids_operator_required_inputs(self) -> None:
        from decision_room.orchestration.brief_planner import (
            build_requirement_planner_prompts,
        )

        system_prompt, user_prompt = build_requirement_planner_prompts(
            "Should we adopt event-sourcing?"
        )
        combined = system_prompt + "\n" + user_prompt
        # Schema example must not include the deprecated slot-filling key.
        self.assertNotIn(
            '"operator_required_inputs"',
            user_prompt,
            "operator_required_inputs must not appear as a JSON schema key the planner is asked to fill",
        )
        # Prose must explicitly forbid it and explain the in-meeting alternative.
        self.assertIn("Never emit operator_required_inputs", user_prompt)
        self.assertIn("conversation-first", system_prompt)
        self.assertIn("human-message channel", system_prompt)

    def test_room_start_contract_draft_discards_operator_required_inputs(self) -> None:
        from decision_room.orchestration.brief_planner import RoomStartContractDraft

        draft = RoomStartContractDraft.from_payload(
            {
                "operator_required_inputs": [
                    "Define the target cohort",
                    "Specify the budget",
                ],
                "contextual_open_questions": ["What is the success metric?"],
            }
        )
        self.assertEqual(draft.operator_required_inputs, [])
        self.assertEqual(draft.contextual_open_questions, ["What is the success metric?"])

    def test_supervisor_prompt_grants_clarification_license(self) -> None:
        from decision_room.mas.types import MeetingPhase
        from decision_room.orchestration.central_mas import (
            build_supervisor_prompts,
            role_catalog_from_snapshot,
        )

        snapshot = _snapshot()
        system_prompt, _user_prompt = build_supervisor_prompts(
            snapshot=snapshot,
            round_index=1,
            role_catalog=role_catalog_from_snapshot(snapshot),
            phase=MeetingPhase.EXPLORE,
            next_focus="kick off",
        )
        self.assertIn("Clarification protocol", system_prompt)
        self.assertIn("[Awaiting operator clarification]", system_prompt)
        self.assertIn("human-message channel", system_prompt)
        self.assertIn("last_human_message", system_prompt)

    def test_supervisor_prompt_does_not_prescribe_specialist_content(self) -> None:
        """Conductor model: supervisor MUST NOT ask the LLM to fill per-specialist
        mission/deliverable/constraints. Those are the specialist's authorship."""
        from decision_room.mas.types import MeetingPhase
        from decision_room.orchestration.central_mas import (
            build_supervisor_prompts,
            role_catalog_from_snapshot,
        )

        snapshot = _snapshot()
        system_prompt, user_prompt = build_supervisor_prompts(
            snapshot=snapshot,
            round_index=1,
            role_catalog=role_catalog_from_snapshot(snapshot),
            phase=MeetingPhase.EXPLORE,
            next_focus="kick off",
        )
        combined = system_prompt + "\n" + user_prompt
        # Schema example must NOT contain content-prescription slots as fillable
        # keys. (The prose may negate them — that's fine.)
        self.assertNotIn('"mission"', user_prompt.split("Output schema example")[-1])
        self.assertNotIn('"deliverable"', user_prompt.split("Output schema example")[-1])
        self.assertNotIn('"constraints"', user_prompt.split("Output schema example")[-1])
        # Prose must explicitly forbid content prescription.
        self.assertIn("WHO speaks WHEN, not WHAT they say", system_prompt)
        self.assertIn(
            "MUST NOT include `mission`, `deliverable`, or `constraints`",
            user_prompt,
        )
        # New positive contract: speaker shape only.
        self.assertIn('"speakers"', user_prompt)
        self.assertIn('"focus_angle"', user_prompt)

    def test_specialist_prompt_does_not_prescribe_turn_task(self) -> None:
        """Conductor model: specialist prompt MUST NOT carry the host's old
        'Execute this host-assigned turn task' clause. The specialist is the
        author."""
        from decision_room.mas.types import MeetingPhase, ModelTarget, ModelTier, RoutingDecision
        from decision_room.orchestration.pre_room_planning import CandidateSpecialist
        from decision_room.orchestration.real_run_contract import HostAgenda
        from decision_room.orchestration.room_executor import _build_argument_prompts

        specialist = CandidateSpecialist(
            role="implementation_specialist",
            display_name="Implementation Specialist",
            capability_profile="Evaluates feasibility.",
            prompt_contract="Stay concrete.",
            join_reason="Need engineering judgment.",
        )
        route = RoutingDecision(
            tier=ModelTier.DEFAULT,
            target=ModelTarget(supplier="qwen", model="test"),
            reason="test",
        )
        system_prompt, user_prompt = _build_argument_prompts(
            specialist=specialist,
            turn_task="",
            snapshot=_snapshot(),
            phase=MeetingPhase.EXPLORE,
            round_index=1,
            next_focus="kick off",
            host_agenda=HostAgenda(
                focus_points=[],
                turns=[],
                open_questions=[],
                no_new_constraints=True,
            ),
            target_claim_ref="",
            route=route,
        )
        combined = system_prompt + "\n" + user_prompt
        self.assertNotIn("Execute this host-assigned turn task", combined)
        self.assertIn("author of this round's contribution", user_prompt)
        self.assertIn("autonomous specialist agent", system_prompt)

    def test_specialist_prompt_grants_clarification_license(self) -> None:
        from decision_room.mas.types import MeetingPhase, ModelTarget, ModelTier, RoutingDecision
        from decision_room.orchestration.pre_room_planning import CandidateSpecialist
        from decision_room.orchestration.real_run_contract import HostAgenda
        from decision_room.orchestration.room_executor import _build_argument_prompts

        specialist = CandidateSpecialist(
            role="implementation_specialist",
            display_name="Implementation Specialist",
            capability_profile="Evaluates feasibility.",
            prompt_contract="Stay concrete.",
            join_reason="Need engineering judgment.",
        )
        route = RoutingDecision(
            tier=ModelTier.DEFAULT,
            target=ModelTarget(supplier="qwen", model="test"),
            reason="test",
        )
        system_prompt, _user_prompt = _build_argument_prompts(
            specialist=specialist,
            turn_task="evaluate feasibility",
            snapshot=_snapshot(),
            phase=MeetingPhase.EXPLORE,
            round_index=1,
            next_focus="kick off",
            host_agenda=HostAgenda(
                focus_points=[],
                turns=[],
                open_questions=[],
                no_new_constraints=True,
            ),
            target_claim_ref="",
            route=route,
        )
        self.assertIn("Clarification protocol", system_prompt)
        self.assertIn("[Awaiting operator clarification]", system_prompt)
        self.assertIn("human-message channel", system_prompt)
        self.assertIn("room_state.last_human_message", system_prompt)


if __name__ == "__main__":
    unittest.main()
