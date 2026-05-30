"""Phase 2 tests — agent boundary + ReAct loop + tool dispatch.

What these tests cover:

1. ``SpecialistAgent`` with ``turn_budget=1`` is byte-equivalent to the
   pre-Phase-2 single-shot path. The FakeProvider only sees the
   pre-Phase-2 prompt shape (no ReAct addendum) and a missing
   ``next_action`` is implicitly treated as ``emit``.
2. ``SpecialistAgent`` with ``turn_budget=3`` runs the ReAct loop:
   think → call_tool → emit. The tool call dispatches through the
   registry and emits ``agent.tool_call`` + ``agent.tool_result`` events
   through the ``publish`` callback.
3. Exhausting ``turn_budget`` without an emit raises
   ``AgentBudgetExhausted`` with the offending role + budget + last action.
4. The parser falls back to ``emit`` when a future-schema ``next_action``
   value sneaks in, keeping the round build resilient.
"""

from __future__ import annotations

import unittest
from typing import Any

from decision_room.agents import (
    AgentBudgetExhausted,
    SpecialistAgent,
    TurnContext,
)
from decision_room.mas.types import (
    DecisionContext,
    DecisionSignals,
    MeetingPhase,
    ModelTarget,
    ModelTier,
    RoutingDecision,
)
from decision_room.orchestration.pre_room_planning import CandidateSpecialist
from decision_room.orchestration.real_run_contract import (
    AgendaFocusPoint,
    AgendaTurn,
    HostAgenda,
)
from decision_room.policies.fallback import DisasterOnlyFallbackPolicy
from decision_room.policies.routing_control import RoutingControlPolicy
from decision_room.providers import GenerateResponse, ProviderRegistry
from decision_room.routing.model_router import HybridModelRouter, RouterTargets
from decision_room.runtime.room_models import RoomSnapshot
from decision_room.tools import Tool, ToolRegistry, ToolResult


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


def _ctx() -> DecisionContext:
    return DecisionContext(
        room_id="room_test",
        phase=MeetingPhase.EXPLORE,
        signals=DecisionSignals(),
        metadata={"topic": "agent boundary"},
    )


def _route() -> RoutingDecision:
    return RoutingDecision(
        tier=ModelTier.DEFAULT, target=_target(), reason="default"
    )


def _snapshot() -> RoomSnapshot:
    return RoomSnapshot(
        room_id="room_test",
        requirement="Phase 2 agent boundary test.",
        topic="Agent boundary",
        goal="Validate the ReAct loop.",
        current_focus="run the loop end to end",
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
            ]
        },
    )


def _specialist(turn_budget: int = 1) -> CandidateSpecialist:
    return CandidateSpecialist(
        role="implementation_specialist",
        display_name="Implementation Specialist",
        capability_profile="Evaluates feasibility.",
        prompt_contract="Stay concrete.",
        join_reason="Need engineering judgment.",
        focus_areas=["feasibility"],
        ttl_rounds=2,
        turn_budget=turn_budget,
    )


def _empty_agenda() -> HostAgenda:
    return HostAgenda(
        focus_points=[
            AgendaFocusPoint(
                title="Run the loop", reason="cover", constraint_ids=["C1"]
            )
        ],
        turns=[AgendaTurn(role="implementation_specialist", task="")],
        open_questions=[],
        no_new_constraints=True,
    )


def _turn_context(
    *,
    specialist: CandidateSpecialist,
    publish: Any = None,
    target_claim_ref: str = "",
) -> TurnContext:
    return TurnContext(
        snapshot=_snapshot(),
        round_index=1,
        phase=MeetingPhase.EXPLORE,
        next_focus="run the loop",
        route_ctx=_ctx(),
        route=_route(),
        publish=publish,
        extras={
            "specialist": specialist,
            "turn_task": "",
            "host_agenda": _empty_agenda(),
            "target_claim_ref": target_claim_ref,
        },
    )


# -- Backward-compat providers ------------------------------------------------


class LegacyEmitProvider:
    """Pre-Phase-2 provider: emits one ArgumentOutput-shaped JSON without
    a ``next_action`` field. The agent must treat the response as an
    implicit ``emit`` so the legacy single-shot path stays working.
    """

    def __init__(self) -> None:
        self.prompts_seen: list[str] = []

    def generate(self, req: Any) -> GenerateResponse:
        self.prompts_seen.append(req.user_prompt)
        return GenerateResponse(
            text=(
                '{"title": "Legacy emit", '
                '"text": "Single-shot legacy response.", '
                '"claim": "Backward compatibility is required.", '
                '"evidence": ["Phase 2 must not break existing providers."], '
                '"confidence": 0.81, "target_claim_ref": ""}'
            ),
            raw_response="",
        )


# -- ReAct providers ----------------------------------------------------------


class _ScriptedReactProvider:
    """Replays a scripted sequence of JSON responses, one per turn.

    Used to exercise the ReAct loop deterministically: think on call 1,
    call_tool on call 2, emit on call 3. ``calls`` records each
    invocation so tests can assert what the agent actually sent.
    """

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls: list[Any] = []

    def generate(self, req: Any) -> GenerateResponse:
        self.calls.append(req)
        if not self._responses:
            raise AssertionError("scripted provider exhausted")
        return GenerateResponse(text=self._responses.pop(0), raw_response="")


class _EchoTool:
    name = "echo"
    description = "Echoes the value back"
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"value": {"type": "string"}},
    }

    def run(self, args: dict[str, Any]) -> ToolResult:
        return ToolResult(ok=True, result={"echoed": args.get("value")})


class _RecordingPublish:
    """Records every event the agent emits through publish so tests can
    assert tool-event journalling without a real RoomEventJournal.
    """

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def __call__(
        self,
        room_id: str,
        *,
        producer_id: str,
        role: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        record = {
            "room_id": room_id,
            "producer_id": producer_id,
            "role": role,
            "event_type": event_type,
            "payload": dict(payload),
        }
        self.events.append(record)
        return record


# -- Tests --------------------------------------------------------------------


class LegacyEmitBackwardCompatTests(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_emit_response_short_circuits_the_loop(self) -> None:
        provider = LegacyEmitProvider()
        agent = SpecialistAgent(
            registry=ProviderRegistry({"qwen": provider}),
            router=_router(),
            use_background_threads=False,
        )
        result = await agent.run(_turn_context(specialist=_specialist(1)))
        self.assertEqual(result.iterations, 1)
        self.assertEqual(result.scratchpad, [])
        self.assertEqual(result.output.claim, "Backward compatibility is required.")
        self.assertEqual(len(provider.prompts_seen), 1)
        # ReAct addendum is suppressed when turn_budget=1 and no tools
        # are registered — pre-Phase-2 providers see the original prompt.
        # The addendum is identifiable by its opening header, distinct
        # from any user-supplied "ReAct loop" wording in the brief.
        self.assertNotIn(
            "ReAct loop (active because you have a multi-step budget",
            provider.prompts_seen[0],
        )
        self.assertNotIn("Scratchpad", provider.prompts_seen[0])


class ReactLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_react_loop_runs_think_then_tool_then_emit(self) -> None:
        responses = [
            (
                '{"next_action": "think", '
                '"thought": "I should consult the echo tool before committing."}'
            ),
            (
                '{"next_action": "call_tool", '
                '"tool_call": {"name": "echo", "args": {"value": "phase 2"}}}'
            ),
            (
                '{"next_action": "emit", '
                '"title": "ReAct verdict", '
                '"text": "Echo confirmed the loop body runs.", '
                '"claim": "ReAct loop reaches emit after think + tool.", '
                '"evidence": ["echo tool returned the expected payload.", "iterations were bounded."], '
                '"confidence": 0.84, "target_claim_ref": ""}'
            ),
        ]
        provider = _ScriptedReactProvider(responses)
        tool_registry = ToolRegistry()
        tool_registry.register(_EchoTool())
        publish = _RecordingPublish()
        agent = SpecialistAgent(
            registry=ProviderRegistry({"qwen": provider}),
            router=_router(),
            tool_registry=tool_registry,
            use_background_threads=False,
        )

        result = await agent.run(
            _turn_context(specialist=_specialist(3), publish=publish)
        )

        self.assertEqual(result.iterations, 3)
        self.assertEqual(
            result.output.claim,
            "ReAct loop reaches emit after think + tool.",
        )
        self.assertEqual([entry.kind for entry in result.scratchpad], ["think", "tool"])
        self.assertEqual(
            result.scratchpad[0].payload["thought"],
            "I should consult the echo tool before committing.",
        )
        self.assertEqual(result.scratchpad[1].payload["tool_name"], "echo")
        self.assertTrue(result.scratchpad[1].payload["ok"])

        # Two journal events: tool_call (pending) + tool_result (settled).
        tool_events = [
            event
            for event in publish.events
            if event["event_type"].startswith("agent.tool_")
        ]
        self.assertEqual(len(tool_events), 2)
        self.assertEqual(tool_events[0]["event_type"], "agent.tool_call")
        self.assertEqual(tool_events[0]["payload"]["tool_name"], "echo")
        self.assertEqual(
            tool_events[0]["payload"]["agent_role"],
            "implementation_specialist",
        )
        self.assertEqual(tool_events[1]["event_type"], "agent.tool_result")
        self.assertEqual(
            tool_events[1]["payload"]["call_id"],
            tool_events[0]["payload"]["call_id"],
        )
        self.assertTrue(tool_events[1]["payload"]["ok"])

        # The second LLM call (after the think step) must include the
        # scratchpad in its prompt so the model can build on its prior step.
        second_user_prompt = provider.calls[1].user_prompt
        self.assertIn("Scratchpad", second_user_prompt)
        self.assertIn("consult the echo tool", second_user_prompt)

    async def test_react_loop_passes_tool_descriptors_to_prompt(self) -> None:
        provider = _ScriptedReactProvider(
            [
                (
                    '{"next_action": "emit", '
                    '"title": "Tool catalog visible", '
                    '"text": "The prompt advertised the registered tools.", '
                    '"claim": "Tool catalog was advertised.", '
                    '"evidence": ["The system listed available tools."], '
                    '"confidence": 0.7, "target_claim_ref": ""}'
                )
            ]
        )
        tool_registry = ToolRegistry()
        tool_registry.register(_EchoTool())
        agent = SpecialistAgent(
            registry=ProviderRegistry({"qwen": provider}),
            router=_router(),
            tool_registry=tool_registry,
            use_background_threads=False,
        )
        # turn_budget=1 still surfaces tools because the registry is non-empty
        await agent.run(_turn_context(specialist=_specialist(1)))
        first_prompt = provider.calls[0].user_prompt
        self.assertIn("Available tools", first_prompt)
        self.assertIn("echo", first_prompt)


class BudgetExhaustionTests(unittest.IsolatedAsyncioTestCase):
    async def test_exhausting_turn_budget_without_emit_raises(self) -> None:
        provider = _ScriptedReactProvider(
            [
                '{"next_action": "think", "thought": "still deliberating"}',
                '{"next_action": "think", "thought": "still still deliberating"}',
            ]
        )
        agent = SpecialistAgent(
            registry=ProviderRegistry({"qwen": provider}),
            router=_router(),
            use_background_threads=False,
        )
        with self.assertRaises(AgentBudgetExhausted) as captured:
            await agent.run(_turn_context(specialist=_specialist(2)))
        self.assertEqual(captured.exception.role, "implementation_specialist")
        self.assertEqual(captured.exception.budget, 2)
        self.assertEqual(captured.exception.last_action, "think")


class ParserResilienceTests(unittest.IsolatedAsyncioTestCase):
    async def test_unknown_next_action_falls_back_to_emit_when_fields_present(
        self,
    ) -> None:
        provider = _ScriptedReactProvider(
            [
                (
                    '{"next_action": "future_action_we_have_not_invented_yet", '
                    '"title": "Forward compat", '
                    '"text": "Default to emit when a future schema slips in.", '
                    '"claim": "Unknown next_action falls back to emit.", '
                    '"evidence": ["Future schema drift must not break round build."], '
                    '"confidence": 0.65, "target_claim_ref": ""}'
                )
            ]
        )
        agent = SpecialistAgent(
            registry=ProviderRegistry({"qwen": provider}),
            router=_router(),
            use_background_threads=False,
        )
        result = await agent.run(_turn_context(specialist=_specialist(1)))
        self.assertEqual(result.output.claim, "Unknown next_action falls back to emit.")
        self.assertEqual(result.iterations, 1)

    async def test_think_without_thought_field_raises_value_error(self) -> None:
        provider = _ScriptedReactProvider(['{"next_action": "think"}'])
        agent = SpecialistAgent(
            registry=ProviderRegistry({"qwen": provider}),
            router=_router(),
            use_background_threads=False,
        )
        with self.assertRaisesRegex(ValueError, "non-empty 'thought'"):
            await agent.run(_turn_context(specialist=_specialist(2)))

    async def test_call_tool_without_tool_call_object_raises(self) -> None:
        provider = _ScriptedReactProvider(['{"next_action": "call_tool"}'])
        agent = SpecialistAgent(
            registry=ProviderRegistry({"qwen": provider}),
            router=_router(),
            use_background_threads=False,
        )
        with self.assertRaisesRegex(ValueError, "requires a tool_call object"):
            await agent.run(_turn_context(specialist=_specialist(2)))


if __name__ == "__main__":
    unittest.main()
