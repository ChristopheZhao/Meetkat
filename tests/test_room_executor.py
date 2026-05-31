import tempfile
import unittest
from pathlib import Path

from decision_room.memory import LongTermLessonStore, RoomMemoryStore
from decision_room.orchestration.room_executor import LLMRoomExecutor, _parse_synthesis_output
from decision_room.policies.fallback import DisasterOnlyFallbackPolicy
from decision_room.policies.routing_control import RoutingControlPolicy
from decision_room.providers import (
    GenerateResponse,
    ProviderNetworkError,
    ProviderRegistry,
    ProviderTimeoutError,
)
from decision_room.routing.model_router import HybridModelRouter, RouterTargets
from decision_room.runtime.room_models import RoomSnapshot


class FakeProvider:
    def generate(self, req):
        if "host agent" in req.system_prompt:
            turns = [
                '{"role": "implementation_specialist", "task": "Propose the minimum runtime shape that keeps room events authoritative."}',
                '{"role": "risk_specialist", "task": "Stress-test the proposal against override, timeout, and fallback failure paths."}',
            ]
            if "product_specialist" in req.user_prompt:
                turns.append(
                    '{"role": "product_specialist", "task": "Confirm the room still preserves visible operator workflow outcomes."}'
                )
            return GenerateResponse(
                text=(
                    '{"focus_points": ['
                    '{"title": "Clarify override semantics", "reason": "Human override must remain a first-class runtime event.", "constraint_ids": ["C1"]},'
                    '{"title": "Keep replay authoritative", "reason": "Replay and live transcript must reflect the same room event source.", "constraint_ids": ["C2"]},'
                    '{"title": "Preserve room transport boundary", "reason": "WebSocket remains the primary path with SSE as read-only fallback.", "constraint_ids": ["C3"]}'
                    '], "turns": ['
                    + ",".join(turns)
                    + '], "open_questions": ["Should override interrupt only the next turn or the whole round?"], "no_new_constraints": true}'
                ),
                raw_response="",
            )
        if "Implementation Specialist" in req.system_prompt:
            return GenerateResponse(
                text=(
                    '{"title": "Support runtime-first direction", "text": "A runtime-first room keeps the UI honest because every visible state change is grounded in room events.", '
                    '"claim": "Drive the room UI directly from room events.", '
                    '"evidence": ["Replay and live state stay aligned.", "Human override can terminate the real meeting rather than a mock view."], '
                    '"confidence": 0.78, "target_claim_ref": ""}'
                ),
                raw_response="",
            )
        if "Risk Specialist" in req.system_prompt:
            return GenerateResponse(
                text=(
                    '{"title": "Stress integration risk", "text": "The room can still drift if the synthesis summary hides unresolved execution semantics.", '
                    '"claim": "Synthesis output must keep unresolved execution risks visible.", '
                    '"evidence": ["Open questions should stay explicit.", "Transport and room semantics can diverge if hidden in prompts."], '
                    '"confidence": 0.68, "target_claim_ref": "Drive the room UI directly from room events."}'
                ),
                raw_response="",
            )
        if "Product Specialist" in req.system_prompt:
            return GenerateResponse(
                text=(
                    '{"title": "Keep operator workflow visible", "text": "The room only helps if operator-visible workflow outcomes stay explicit in the transcript and replay.", '
                    '"claim": "Visible operator outcomes must stay in the room loop.", '
                    '"evidence": ["The meeting is a product workflow, not just a backend run.", "Operators need to see unresolved state before closing the room."], '
                    '"confidence": 0.74, "target_claim_ref": "Synthesis output must keep unresolved execution risks visible."}'
                ),
                raw_response="",
            )
        return GenerateResponse(
            text=(
                '{"title": "Synthesis note", "text": "Synthesis summary: use a host-led multi-specialist executor while keeping room events authoritative.", '
                '"agreement": ["Room events are the source of truth.", "Human override must affect the real runtime."], '
                '"disagreement": [], '
                '"open_questions": ["Should synthesis expose summarized handoff state in the UI?"], '
                '"decision_candidate": "Promote the host-led dynamic specialist executor as the primary runtime path.", '
                '"action_item_draft": ["Wire the role prompts to the provider registry.", "Delay browser verification until B1-B4 are done."], '
                '"conclusion_type": "follow_up_required", '
                '"conclusion_reason": "The room has a viable candidate direction but still needs follow-up implementation work before closure.", '
                '"should_end_meeting": false}'
            ),
            raw_response="",
        )


class FlakyProvider(FakeProvider):
    def __init__(self) -> None:
        self._seen_system_prompts: dict[str, int] = {}

    def generate(self, req):
        count = self._seen_system_prompts.get(req.system_prompt, 0)
        self._seen_system_prompts[req.system_prompt] = count + 1
        if "synthesis capability" in req.system_prompt and count == 0:
            raise ProviderNetworkError("provider network error: simulated remote disconnect")
        return super().generate(req)


class AlwaysTimeoutProvider:
    def generate(self, req):
        raise ProviderTimeoutError(
            f"provider timeout: model={req.model}; url=https://example.invalid; timeout_sec=45; reason=simulated timeout"
        )


class ContextAwareProvider(FakeProvider):
    def __init__(self) -> None:
        self.host_user_prompt = ""
        self.synthesis_user_prompt = ""

    def generate(self, req):
        if "host agent" in req.system_prompt:
            self.host_user_prompt = req.user_prompt
            return GenerateResponse(
                text=(
                    '{"focus_points": ['
                    '{"title": "Use preflight facts", "reason": "Validated context should stay authoritative inside the room.", "constraint_ids": ["C1"]},'
                    '{"title": "Avoid rediscovery", "reason": "Answered validation details should not reappear as missing inputs.", "constraint_ids": ["C2"]},'
                    '{"title": "Keep execution grounded", "reason": "Provider targets and timeout guardrails are already known before the round starts.", "constraint_ids": ["C3"]}'
                    '], "turns": ['
                    '{"role": "implementation_specialist", "task": "Confirm the validated context is sufficient to keep the room moving."}'
                    '], "open_questions": ['
                    '"Which provider identifiers are active for planner and executor?", '
                    '"How is executor escalation timeout duration configured and guarded against silent minimax fallback?", '
                    '"What observable signal confirms the planner is bound and ready before planning.completed?"'
                    '], "no_new_constraints": true}'
                ),
                raw_response="",
            )
        if "synthesis capability" in req.system_prompt:
            self.synthesis_user_prompt = req.user_prompt
            return GenerateResponse(
                text=(
                    '{"title": "Synthesis note", "text": "The room should rely on validated context instead of rediscovering the same validation contract.", '
                    '"agreement": ["Validated operator context is already available before the round starts."], '
                    '"disagreement": [], '
                    '"open_questions": ['
                    '"Which provider identifiers are active for planner and executor?", '
                    '"What counts as authoritative snapshot/replay evidence for this validation run?", '
                    '"What conclusion_type and conclusion_reason fields are required before closing the room?", '
                    '"How is executor escalation timeout duration configured and guarded against silent minimax fallback?", '
                    '"What observable signal confirms the planner is bound and ready before planning.completed?", '
                    '"What guardrail or telemetry mechanism prevents or detects stalled-but-open WebSocket connections before SSE fallback occurs?"'
                    '], '
                    '"decision_candidate": "Proceed with the provider-backed room run using the preflight-validated contract.", '
                    '"action_item_draft": ["Use the validated context inside the room prompts."], '
                    '"conclusion_type": "follow_up_required", '
                    '"conclusion_reason": "The room still needs to confirm the same validation contract questions.", '
                    '"should_end_meeting": false}'
                ),
                raw_response="",
            )
        if "Implementation Specialist" in req.system_prompt:
            return GenerateResponse(
                text=(
                    '{"title": "Implementation readout", "text": "The validated context already defines the provider and evidence contract.", '
                    '"claim": "Treat validated context as resolved input.", '
                    '"evidence": ["The preflight step captured operator and runtime context before the room started."], '
                    '"confidence": 0.81, "target_claim_ref": ""}'
                ),
                raw_response="",
            )
        return super().generate(req)


class LLMRoomExecutorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        # Phase 3 wires every agent through memory recall. Without explicit
        # per-test stores, the default ``RoomMemoryStore`` singleton would
        # leak state between tests that re-use ``room_id="room_test"`` and
        # the FakeProvider's keyword-driven branch ("if 'product_specialist'
        # in req.user_prompt") would fire on stale recall content.
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._room_memory = RoomMemoryStore(storage_dir=Path(self._tmp.name))
        self._long_term = LongTermLessonStore(storage_dir=Path(self._tmp.name))

    async def test_build_round_returns_real_role_messages(self) -> None:
        registry = ProviderRegistry({"qwen": FakeProvider()})
        router = HybridModelRouter(
            RoutingControlPolicy(DisasterOnlyFallbackPolicy()),
            targets=RouterTargets(
                default_target=_target(),
                escalation_target=_target(),
                disaster_fallback_target=_target(),
            ),
        )
        executor = LLMRoomExecutor(
            registry=registry,
            router=router,
            use_background_threads=False,
            room_memory_store=self._room_memory,
            long_term_store=self._long_term,
        )
        snapshot = RoomSnapshot(
            room_id="room_test",
            requirement="Need a runtime-first multi-agent decision room.",
            topic="Runtime-first room",
            goal="Reach a concrete room runtime direction.",
            current_focus="stabilize the room contract",
            constraints=[
                "Human override must remain available.",
                "Replay must be authoritative.",
                "WebSocket primary, SSE fallback.",
            ],
            planning_artifacts={
                "candidate_specialist_roster": [
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
                        "capability_profile": "Keeps the room aligned with user goals and operator-visible workflow outcomes.",
                        "prompt_contract": "Argue from user impact and workflow visibility.",
                        "join_reason": "Need product grounding for the operator workflow.",
                        "focus_areas": ["workflow"],
                        "ttl_rounds": 2,
                        "turn_budget": 1,
                    },
                ]
            },
        )

        round_data = await executor.build_round(snapshot, round_index=1)

        self.assertEqual(len(round_data.messages), 4)
        self.assertEqual(round_data.messages[0].role, "host")
        self.assertEqual(round_data.messages[1].role, "implementation_specialist")
        self.assertEqual(round_data.messages[2].role, "risk_specialist")
        self.assertEqual(round_data.messages[3].role, "product_specialist")
        self.assertEqual(round_data.synthesis_message.role, "synthesis")
        self.assertEqual(
            round_data.messages[0].artifacts["target_roles"],
            ["implementation_specialist", "risk_specialist", "product_specialist"],
        )
        self.assertEqual(
            [item["role"] for item in round_data.messages[0].artifacts["turns"]],
            ["implementation_specialist", "risk_specialist", "product_specialist"],
        )
        self.assertIn("route", round_data.messages[1].artifacts)
        self.assertEqual(round_data.conclusion_type, "follow_up_required")
        self.assertIn("viable candidate direction", round_data.conclusion_reason)
        self.assertFalse(round_data.consensus_should_end)
        self.assertFalse(round_data.should_end)
        self.assertTrue(round_data.decision_candidate.startswith("Promote the host-led dynamic specialist executor"))
        self.assertGreater(len(round_data.action_items), 0)
        self.assertGreater(len(round_data.open_questions), 0)

    async def test_build_round_retries_transient_provider_network_error(self) -> None:
        registry = ProviderRegistry({"qwen": FlakyProvider()})
        router = HybridModelRouter(
            RoutingControlPolicy(DisasterOnlyFallbackPolicy()),
            targets=RouterTargets(
                default_target=_target(),
                escalation_target=_target(),
                disaster_fallback_target=_target(),
            ),
        )
        executor = LLMRoomExecutor(
            registry=registry,
            router=router,
            use_background_threads=False,
            transient_max_attempts=2,
            room_memory_store=self._room_memory,
            long_term_store=self._long_term,
        )
        snapshot = RoomSnapshot(
            room_id="room_test",
            requirement="Need a runtime-first multi-agent decision room.",
            topic="Runtime-first room",
            goal="Reach a concrete room runtime direction.",
            current_focus="stabilize the room contract",
            constraints=[
                "Human override must remain available.",
                "Replay must be authoritative.",
                "WebSocket primary, SSE fallback.",
            ],
            planning_artifacts={
                "candidate_specialist_roster": [
                    {
                        "role": "implementation_specialist",
                        "display_name": "Implementation Specialist",
                        "capability_profile": "Evaluates feasibility and integration shape.",
                        "prompt_contract": "Stay concrete and implementation-grounded.",
                        "join_reason": "Runtime work needs engineering judgment.",
                    },
                    {
                        "role": "risk_specialist",
                        "display_name": "Risk Specialist",
                        "capability_profile": "Surfaces failure and recovery risk.",
                        "prompt_contract": "Keep unresolved runtime risks explicit.",
                        "join_reason": "Need challenge-oriented analysis.",
                    },
                ]
            },
        )

        round_data = await executor.build_round(snapshot, round_index=1)

        self.assertEqual(round_data.synthesis_message.role, "synthesis")
        self.assertEqual(round_data.conclusion_type, "follow_up_required")
        self.assertFalse(round_data.should_end)
        self.assertGreater(len(round_data.action_items), 0)

    async def test_build_round_reroutes_to_disaster_fallback_after_repeated_timeout(self) -> None:
        registry = ProviderRegistry({"qwen": AlwaysTimeoutProvider(), "minimax": FakeProvider()})
        router = HybridModelRouter(
            RoutingControlPolicy(DisasterOnlyFallbackPolicy()),
            targets=RouterTargets(
                default_target=_target(),
                escalation_target=_target(),
                disaster_fallback_target=_target("minimax", "fallback-model"),
            ),
        )
        executor = LLMRoomExecutor(
            registry=registry,
            router=router,
            use_background_threads=False,
            transient_max_attempts=2,
            room_memory_store=self._room_memory,
            long_term_store=self._long_term,
        )
        snapshot = RoomSnapshot(
            room_id="room_test",
            requirement="Need a runtime-first multi-agent decision room.",
            topic="Runtime-first room",
            goal="Reach a concrete room runtime direction.",
            current_focus="stabilize the room contract",
            constraints=[
                "Human override must remain available.",
                "Replay must be authoritative.",
                "WebSocket primary, SSE fallback.",
            ],
            planning_artifacts={
                "candidate_specialist_roster": [
                    {
                        "role": "implementation_specialist",
                        "display_name": "Implementation Specialist",
                        "capability_profile": "Evaluates feasibility and integration shape.",
                        "prompt_contract": "Stay concrete and implementation-grounded.",
                        "join_reason": "Runtime work needs engineering judgment.",
                    },
                    {
                        "role": "risk_specialist",
                        "display_name": "Risk Specialist",
                        "capability_profile": "Surfaces failure and recovery risk.",
                        "prompt_contract": "Keep unresolved runtime risks explicit.",
                        "join_reason": "Need challenge-oriented analysis.",
                    },
                ]
            },
        )

        round_data = await executor.build_round(snapshot, round_index=1)

        self.assertEqual(
            round_data.messages[0].artifacts["route"]["tier"],
            "disaster_fallback",
        )
        self.assertEqual(
            round_data.messages[0].artifacts["route"]["supplier"],
            "minimax",
        )
        self.assertGreater(len(round_data.action_items), 0)

    async def test_build_round_infers_stable_follow_up_end_from_snapshot(self) -> None:
        registry = ProviderRegistry({"qwen": FakeProvider()})
        router = HybridModelRouter(
            RoutingControlPolicy(DisasterOnlyFallbackPolicy()),
            targets=RouterTargets(
                default_target=_target(),
                escalation_target=_target(),
                disaster_fallback_target=_target(),
            ),
        )
        executor = LLMRoomExecutor(
            registry=registry,
            router=router,
            use_background_threads=False,
            room_memory_store=self._room_memory,
            long_term_store=self._long_term,
        )
        snapshot = RoomSnapshot(
            room_id="room_test",
            requirement="Need a runtime-first multi-agent decision room.",
            topic="Runtime-first room",
            goal="Reach a concrete room runtime direction.",
            current_focus="stabilize the room contract",
            candidate_decision="Promote the host-led dynamic specialist executor as the primary runtime path.",
            conclusion_type="follow_up_required",
            constraints=[
                "Human override must remain available.",
                "Replay must be authoritative.",
                "WebSocket primary, SSE fallback.",
            ],
            planning_artifacts={
                "candidate_specialist_roster": [
                    {
                        "role": "implementation_specialist",
                        "display_name": "Implementation Specialist",
                        "capability_profile": "Evaluates feasibility and integration shape.",
                        "prompt_contract": "Stay concrete and implementation-grounded.",
                        "join_reason": "Runtime work needs engineering judgment.",
                    },
                    {
                        "role": "risk_specialist",
                        "display_name": "Risk Specialist",
                        "capability_profile": "Surfaces failure and recovery risk.",
                        "prompt_contract": "Keep unresolved runtime risks explicit.",
                        "join_reason": "Need challenge-oriented analysis.",
                    },
                ]
            },
        )

        round_data = await executor.build_round(snapshot, round_index=2)

        self.assertTrue(round_data.should_end)
        self.assertFalse(round_data.consensus_should_end)
        self.assertEqual(
            round_data.end_reason,
            "orchestration detected a stable follow-up outcome across consecutive rounds",
        )

    def test_parse_synthesis_output_rejects_empty_state_summary(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "at least one agreement, disagreement, or open question",
        ):
            _parse_synthesis_output(
                '{"title":"Synthesis note","text":"Summary","agreement":[],"disagreement":[],"open_questions":[],"decision_candidate":"Ship it","action_item_draft":["Delay browser verification until the topology is complete."],"conclusion_type":"follow_up_required","conclusion_reason":"Need more work.","should_end_meeting":false}'
            )

    async def test_build_round_treats_validated_context_as_resolved_input(self) -> None:
        provider = ContextAwareProvider()
        registry = ProviderRegistry({"qwen": provider})
        router = HybridModelRouter(
            RoutingControlPolicy(DisasterOnlyFallbackPolicy()),
            targets=RouterTargets(
                default_target=_target(),
                escalation_target=_target(),
                disaster_fallback_target=_target(),
            ),
        )
        executor = LLMRoomExecutor(
            registry=registry,
            router=router,
            use_background_threads=False,
            room_memory_store=self._room_memory,
            long_term_store=self._long_term,
        )
        snapshot = RoomSnapshot(
            room_id="room_test",
            requirement="Need a real provider-backed room run that validates the new host-led topology.",
            topic="Provider-backed validation",
            goal="Close the room without rediscovering preflight-approved validation context.",
            current_focus="use preflight-validated context inside the room",
            constraints=[
                "Snapshot and replay remain authoritative.",
                "Meeting must emit explicit conclusion_type and conclusion_reason.",
                "Use the configured real planner/executor providers.",
            ],
            open_questions=[
                "Which provider identifiers are active for planner and executor?",
                "What counts as authoritative snapshot/replay evidence for this validation run?",
            ],
            planning_artifacts={
                "candidate_specialist_roster": [
                    {
                        "role": "implementation_specialist",
                        "display_name": "Implementation Specialist",
                        "capability_profile": "Evaluates feasibility and integration shape.",
                        "prompt_contract": "Stay concrete and implementation-grounded.",
                        "join_reason": "Runtime work needs engineering judgment.",
                    },
                ],
                "room_start_contract": {
                    "room_start_ready": True,
                    "runtime_bootstrap_ready": True,
                    "missing_operator_inputs": [],
                    "contextual_open_questions": [],
                    "system_blockers": [],
                    "known_context": [
                        "planner and default executor target identities are known from runtime_context"
                    ],
                    "recommended_surface": "room_start",
                    "root_cause_hypothesis": "room-start contract is already clear",
                },
                "operator_context": {
                    "brief_source": "agent",
                    "validation_scenario": [
                        "use the default validation requirement as the trigger payload"
                    ],
                    "binding_readiness_contract": [
                        "runtime_readiness plus preflight.room_start_ready are the authoritative pre-room binding signal"
                    ],
                    "transport_contract": [
                        "the smoke entry does not execute live WebSocket degradation testing",
                        "silent SSE fallback or zombie WebSocket detection is covered by headed browser transport verification"
                    ],
                    "projection_contract": [
                        "current_turns must be non-empty and each item must include non-empty role and task fields"
                    ],
                    "evidence_contract": [
                        "authoritative snapshot/replay evidence must come from the room runtime surfaces"
                    ],
                    "conclusion_contract": [
                        "meeting conclusion contract requires non-empty conclusion_type and conclusion_reason"
                    ],
                },
                "runtime_context": {
                    "planner_mode": "primary",
                    "executor_mode": "llm",
                    "planner_target": {"supplier": "openai", "model": "gpt-default"},
                    "executor_targets": {
                        "default": {"supplier": "openai", "model": "gpt-default"},
                        "escalation": {"supplier": "openai", "model": "gpt-escalation"},
                        "fallback": {"supplier": "openai", "model": "gpt-fallback"},
                    },
                    "executor_guardrails": {
                        "request_timeout_default_sec": 45,
                        "provider_timeouts": {"openai": {"timeout_sec": 45}},
                        "transient_max_attempts": 2,
                        "disaster_fallback_policy": {
                            "policy": "disaster_only",
                            "max_timeouts_before_fallback": 2,
                            "max_rate_limits_before_fallback": 2,
                            "target": {"supplier": "openai", "model": "gpt-fallback"},
                        },
                        "route_visibility": "agent.message and agent.summary artifacts expose route tier, supplier, model, and reason",
                    },
                },
            },
        )

        round_data = await executor.build_round(snapshot, round_index=1)

        self.assertEqual(round_data.messages[0].artifacts["open_questions"], [])
        self.assertEqual(round_data.open_questions, [])
        self.assertIn("validated_context", provider.host_user_prompt)
        self.assertIn("room_start_contract", provider.host_user_prompt)
        self.assertIn("known_context", provider.host_user_prompt)
        self.assertIn('"planner_target"', provider.host_user_prompt)
        self.assertNotIn("validation_scenario", provider.host_user_prompt)
        self.assertNotIn("conclusion_contract", provider.synthesis_user_prompt)
        self.assertIn("request_timeout_default_sec", provider.synthesis_user_prompt)
        self.assertNotIn("projection_contract", provider.synthesis_user_prompt)


def _target(supplier: str = "qwen", model: str = "test-model"):
    from decision_room.mas.types import ModelTarget

    return ModelTarget(supplier=supplier, model=model)


if __name__ == "__main__":
    unittest.main()
