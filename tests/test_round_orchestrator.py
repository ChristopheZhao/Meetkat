"""Phase 4 tests — single ``RoundOrchestrator`` with pluggable speaker
selection.

What these tests cover:

1. The orchestrator drives the same loop regardless of strategy — same
   prompt builder, same SpecialistAgent, same SynthesisAgent, same
   consensus + end-signal helpers.
2. ``HostLedSpeakerStrategy`` and ``SupervisorLedSpeakerStrategy`` pick
   different first speakers for the same snapshot when the host vs.
   supervisor LLM returns different orderings.
3. The supervisor-led path adds the topology metadata + central_mas
   synthesis artifact extras; the host-led path does not.
4. LLM ``recommended_next_action`` wins over the FSM (the dual SoT
   precedence is codified in the orchestrator now, not duplicated
   across two executors).
5. The back-compat wrappers (``LLMRoomExecutor`` /
   ``CentralizedMASExecutor``) still build rooms through the
   orchestrator — every pre-Phase-4 test in the suite passes unchanged.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from decision_room.agents import HostAgent, SupervisorAgent
from decision_room.mas.types import (
    ActionType,
    DecisionContext,
    DecisionSignals,
    MeetingPhase,
    ModelTarget,
    ModelTier,
    RoutingDecision,
)
from decision_room.memory import LongTermLessonStore, RoomMemoryStore
from decision_room.orchestration.round_orchestrator import RoundOrchestrator
from decision_room.orchestration.speaker_strategies import (
    HostLedSpeakerStrategy,
    SpecialistAssignment,
    SupervisorLedSpeakerStrategy,
)
from decision_room.policies.fallback import DisasterOnlyFallbackPolicy
from decision_room.policies.routing_control import RoutingControlPolicy
from decision_room.providers import GenerateResponse, ProviderRegistry
from decision_room.routing.model_router import HybridModelRouter, RouterTargets
from decision_room.runtime.room_models import RoomSnapshot


def _target(supplier: str = "qwen", model: str = "test-model") -> ModelTarget:
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


def _route() -> RoutingDecision:
    return RoutingDecision(
        tier=ModelTier.DEFAULT, target=_target(), reason="default"
    )


def _snapshot(room_id: str = "room_round_orchestrator") -> RoomSnapshot:
    return RoomSnapshot(
        room_id=room_id,
        requirement="Phase 4 RoundOrchestrator test.",
        topic="Round orchestrator",
        goal="Validate strategy swap.",
        current_focus="exercise both topologies",
        constraints=["Replay must be authoritative."],
        planning_artifacts={
            "candidate_specialist_roster": [
                {
                    "role": "implementation_specialist",
                    "display_name": "Implementation Specialist",
                    "capability_profile": "Evaluates feasibility.",
                    "prompt_contract": "Stay concrete.",
                    "join_reason": "Need engineering judgment.",
                    "focus_areas": ["feasibility"],
                    "ttl_rounds": 2,
                    "turn_budget": 1,
                },
                {
                    "role": "risk_specialist",
                    "display_name": "Risk Specialist",
                    "capability_profile": "Surfaces failure modes.",
                    "prompt_contract": "Stay risk-focused.",
                    "join_reason": "Need risk grounding.",
                    "focus_areas": ["recovery"],
                    "ttl_rounds": 2,
                    "turn_budget": 1,
                },
            ]
        },
    )


def _stores(tmp_path: Path) -> tuple[RoomMemoryStore, LongTermLessonStore]:
    return (
        RoomMemoryStore(storage_dir=tmp_path),
        LongTermLessonStore(storage_dir=tmp_path),
    )


# -- Providers ---------------------------------------------------------------


_SPECIALIST_TEMPLATE = (
    '{{"title": "{role}", "text": "specialist contribution", '
    '"claim": "claim from {role}", "evidence": ["e1", "e2"], '
    '"confidence": 0.7, "target_claim_ref": ""}}'
)


_SYNTHESIS_RESPONSE = (
    '{"title": "syn", "text": "summary", '
    '"agreement": ["roles aligned"], "disagreement": [], '
    '"open_questions": [], '
    '"decision_candidate": "ship it", '
    '"action_item_draft": ["ship"], '
    '"conclusion_type": "follow_up_required", '
    '"conclusion_reason": "tests do not exercise meeting end here", '
    '"should_end_meeting": false}'
)


def _host_response(turn_order: list[str]) -> str:
    turn_objs = ",".join(
        '{"role": "' + role + '", "task": ""}' for role in turn_order
    )
    return (
        '{"focus_points": ['
        '{"title": "Run", "reason": "Phase 4.", "constraint_ids": ["C1"]},'
        '{"title": "Again", "reason": "Two focus points.", "constraint_ids": ["C1"]}'
        '], "turns": ['
        f"{turn_objs}"
        '], "open_questions": [], "no_new_constraints": true}'
    )


def _supervisor_response(speaker_order: list[str]) -> str:
    speakers = ",".join(
        '{"agent": "' + role + '", "run": true, "order": ' + str(i + 1) + ', "focus_angle": ""}'
        for i, role in enumerate(speaker_order)
    )
    return (
        '{"current_focus": "phase 4", '
        '"decision_focus": "phase 4 decision", '
        '"phase": "explore", '
        '"open_questions": [], '
        '"reason": "supervisor-led test", '
        '"speakers": [' + speakers + ']}'
    )


class _ScriptedProvider:
    """Routes requests to the right canned response based on the system
    prompt's role marker. Lets one provider satisfy host/supervisor/
    specialists/synthesis in one round.
    """

    def __init__(
        self,
        *,
        host_response: str | None = None,
        supervisor_response: str | None = None,
    ) -> None:
        self._host_response = host_response
        self._supervisor_response = supervisor_response
        self.calls: list[Any] = []

    def generate(self, req: Any) -> GenerateResponse:
        self.calls.append(req)
        if "host agent" in req.system_prompt and self._host_response is not None:
            return GenerateResponse(text=self._host_response, raw_response="")
        if "central supervisor" in req.system_prompt and self._supervisor_response is not None:
            return GenerateResponse(text=self._supervisor_response, raw_response="")
        if "synthesis capability" in req.system_prompt:
            return GenerateResponse(text=_SYNTHESIS_RESPONSE, raw_response="")
        # Specialist: detect the display_name from the system prompt to
        # echo back a role-specific claim.
        if "Implementation Specialist" in req.system_prompt:
            return GenerateResponse(
                text=_SPECIALIST_TEMPLATE.format(role="implementation_specialist"),
                raw_response="",
            )
        if "Risk Specialist" in req.system_prompt:
            return GenerateResponse(
                text=_SPECIALIST_TEMPLATE.format(role="risk_specialist"),
                raw_response="",
            )
        raise AssertionError(f"unexpected prompt:\n{req.system_prompt!r}")


# -- Tests --------------------------------------------------------------------


class StrategySwapTests(unittest.IsolatedAsyncioTestCase):
    async def test_host_led_and_supervisor_led_pick_different_first_speakers(
        self,
    ) -> None:
        """Same snapshot, two strategies, different speaker orderings.

        The host LLM returns implementation_specialist first; the
        supervisor LLM returns risk_specialist first. The orchestrator
        loop is identical in both cases — only the speaker selection
        changes — so the first non-host message role differs.
        """
        with tempfile.TemporaryDirectory() as tmp:
            room_store, long_store = _stores(Path(tmp))
            host_provider = _ScriptedProvider(
                host_response=_host_response(
                    ["implementation_specialist", "risk_specialist"]
                )
            )
            host_agent = HostAgent(
                registry=ProviderRegistry({"qwen": host_provider}),
                router=_router(),
                use_background_threads=False,
                room_memory_store=room_store,
                long_term_store=long_store,
            )
            host_orchestrator = RoundOrchestrator(
                registry=ProviderRegistry({"qwen": host_provider}),
                router=_router(),
                strategy=HostLedSpeakerStrategy(host_agent),
                use_background_threads=False,
                room_memory_store=room_store,
                long_term_store=long_store,
            )
            host_round = await host_orchestrator.build_round(_snapshot(), 1)
            host_first_specialist = host_round.messages[1].role

        with tempfile.TemporaryDirectory() as tmp:
            room_store, long_store = _stores(Path(tmp))
            supervisor_provider = _ScriptedProvider(
                supervisor_response=_supervisor_response(
                    ["risk_specialist", "implementation_specialist"]
                )
            )
            supervisor_agent = SupervisorAgent(
                registry=ProviderRegistry({"qwen": supervisor_provider}),
                router=_router(),
                use_background_threads=False,
                room_memory_store=room_store,
                long_term_store=long_store,
            )
            supervisor_orchestrator = RoundOrchestrator(
                registry=ProviderRegistry({"qwen": supervisor_provider}),
                router=_router(),
                strategy=SupervisorLedSpeakerStrategy(supervisor_agent),
                use_background_threads=False,
                room_memory_store=room_store,
                long_term_store=long_store,
            )
            supervisor_round = await supervisor_orchestrator.build_round(
                _snapshot(), 1
            )
            supervisor_first_specialist = supervisor_round.messages[1].role

        self.assertEqual(host_first_specialist, "implementation_specialist")
        self.assertEqual(supervisor_first_specialist, "risk_specialist")

    async def test_supervisor_led_adds_topology_metadata_and_central_mas_ref(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            room_store, long_store = _stores(Path(tmp))
            provider = _ScriptedProvider(
                supervisor_response=_supervisor_response(["implementation_specialist"])
            )
            supervisor_agent = SupervisorAgent(
                registry=ProviderRegistry({"qwen": provider}),
                router=_router(),
                use_background_threads=False,
                room_memory_store=room_store,
                long_term_store=long_store,
            )
            orchestrator = RoundOrchestrator(
                registry=ProviderRegistry({"qwen": provider}),
                router=_router(),
                strategy=SupervisorLedSpeakerStrategy(supervisor_agent),
                use_background_threads=False,
                room_memory_store=room_store,
                long_term_store=long_store,
            )
            result = await orchestrator.build_round(_snapshot(), 1)
            self.assertIn(
                "central_mas_state_ref",
                result.synthesis_message.artifacts,
            )
            # Host (supervisor) message carries the central_mas bundle
            self.assertIn("central_mas", result.messages[0].artifacts)
            # Speaker_slot artifact present on specialist message
            self.assertIn("speaker_slot", result.messages[1].artifacts)

    async def test_host_led_omits_central_mas_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            room_store, long_store = _stores(Path(tmp))
            provider = _ScriptedProvider(
                host_response=_host_response(["implementation_specialist"])
            )
            host_agent = HostAgent(
                registry=ProviderRegistry({"qwen": provider}),
                router=_router(),
                use_background_threads=False,
                room_memory_store=room_store,
                long_term_store=long_store,
            )
            orchestrator = RoundOrchestrator(
                registry=ProviderRegistry({"qwen": provider}),
                router=_router(),
                strategy=HostLedSpeakerStrategy(host_agent),
                use_background_threads=False,
                room_memory_store=room_store,
                long_term_store=long_store,
            )
            result = await orchestrator.build_round(_snapshot(), 1)
            self.assertNotIn(
                "central_mas_state_ref",
                result.synthesis_message.artifacts,
            )
            self.assertNotIn("central_mas", result.messages[0].artifacts)
            self.assertNotIn("speaker_slot", result.messages[1].artifacts)


class CoordinationPrecedenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_llm_recommended_next_action_wins_over_fsm(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            room_store, long_store = _stores(Path(tmp))
            provider = _ScriptedProvider(
                host_response=_host_response(["implementation_specialist"])
            )
            host_agent = HostAgent(
                registry=ProviderRegistry({"qwen": provider}),
                router=_router(),
                use_background_threads=False,
                room_memory_store=room_store,
                long_term_store=long_store,
            )
            orchestrator = RoundOrchestrator(
                registry=ProviderRegistry({"qwen": provider}),
                router=_router(),
                strategy=HostLedSpeakerStrategy(host_agent),
                use_background_threads=False,
                room_memory_store=room_store,
                long_term_store=long_store,
            )
            snapshot = _snapshot()
            snapshot.recommended_next_action = "end_meeting"
            result = await orchestrator.build_round(snapshot, 1)
            self.assertEqual(
                result.coordination.action_type, ActionType.END_MEETING
            )
            self.assertIn(
                "LLM synthesis recommended", result.coordination.reason
            )


class BackwardCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_llm_room_executor_still_drives_a_round(self) -> None:
        from decision_room.orchestration.room_executor import LLMRoomExecutor

        with tempfile.TemporaryDirectory() as tmp:
            room_store, long_store = _stores(Path(tmp))
            provider = _ScriptedProvider(
                host_response=_host_response(["implementation_specialist"])
            )
            executor = LLMRoomExecutor(
                registry=ProviderRegistry({"qwen": provider}),
                router=_router(),
                use_background_threads=False,
                room_memory_store=room_store,
                long_term_store=long_store,
            )
            result = await executor.build_round(_snapshot(), 1)
            self.assertEqual(result.messages[0].role, "host")
            self.assertEqual(
                result.messages[1].role, "implementation_specialist"
            )
            self.assertEqual(result.synthesis_message.role, "synthesis")

    async def test_centralized_mas_executor_still_drives_a_round(self) -> None:
        from decision_room.orchestration.central_executor import (
            CentralizedMASExecutor,
        )

        with tempfile.TemporaryDirectory() as tmp:
            room_store, long_store = _stores(Path(tmp))
            provider = _ScriptedProvider(
                supervisor_response=_supervisor_response(
                    ["implementation_specialist"]
                )
            )
            executor = CentralizedMASExecutor(
                registry=ProviderRegistry({"qwen": provider}),
                router=_router(),
                use_background_threads=False,
                room_memory_store=room_store,
                long_term_store=long_store,
            )
            result = await executor.build_round(_snapshot(), 1)
            self.assertEqual(result.messages[0].role, "host")
            self.assertIn("central_mas", result.messages[0].artifacts)
            self.assertEqual(
                result.synthesis_message.artifacts.get("central_mas_state_ref"),
                "host.artifacts.central_mas.supervisor_state",
            )


if __name__ == "__main__":
    unittest.main()
