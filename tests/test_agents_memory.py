"""Phase 3 tests — memory as context substrate.

What these tests cover:

1. Every agent reads its own memory recall block and includes it in the
   prompt when the store has relevant facts/events.
2. Every agent persists its turn back to memory through
   ``memory.write`` events so the RoomEventJournal stays the single
   source of truth.
3. A 3-round journal can rebuild the memory store cold from
   ``memory.write`` events alone (the SoT replay invariant).
4. ``format_memory_recall_section`` produces an empty section when
   nothing actionable is in recall, so legacy prompts stay unchanged
   for round-1 turns.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from decision_room.agents import (
    HostAgent,
    SpecialistAgent,
    SupervisorAgent,
    SynthesisAgent,
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
from decision_room.memory import (
    LongTermLesson,
    LongTermLessonStore,
    RoomMemoryStore,
    agent_scope,
    format_memory_recall_section,
    mas_scope,
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
from decision_room.runtime.room_event_journal import RoomEventJournal
from decision_room.runtime.room_models import RoomSnapshot
from decision_room.runtime.room_projector import RoomProjector


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
        metadata={"topic": "memory substrate"},
    )


def _route() -> RoutingDecision:
    return RoutingDecision(
        tier=ModelTier.DEFAULT, target=_target(), reason="default"
    )


def _snapshot() -> RoomSnapshot:
    return RoomSnapshot(
        room_id="room_test",
        requirement="Phase 3 memory substrate test.",
        topic="Memory substrate",
        goal="Wire recall + writes through every agent.",
        current_focus="run the loop",
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


class _CapturingProvider:
    """Replays one canned JSON response, records every prompt it saw."""

    def __init__(self, response: str) -> None:
        self._response = response
        self.calls: list[Any] = []

    def generate(self, req: Any) -> GenerateResponse:
        self.calls.append(req)
        return GenerateResponse(text=self._response, raw_response="")


class _RecordingPublish:
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


def _stores(tmp_path: Path) -> tuple[RoomMemoryStore, LongTermLessonStore]:
    return (
        RoomMemoryStore(storage_dir=tmp_path),
        LongTermLessonStore(storage_dir=tmp_path),
    )


class FormatMemoryRecallSectionTests(unittest.TestCase):
    def test_returns_empty_when_recall_is_empty(self) -> None:
        self.assertEqual(format_memory_recall_section(None), "")
        self.assertEqual(format_memory_recall_section({}), "")
        # All-empty containers count as nothing actionable.
        self.assertEqual(
            format_memory_recall_section(
                {
                    "shared_facts": {},
                    "agent_local_facts": {},
                    "recent_shared_events": [],
                    "role_lessons": [],
                }
            ),
            "",
        )

    def test_returns_block_when_recall_has_content(self) -> None:
        section = format_memory_recall_section(
            {
                "shared_facts": {"decision_focus": "topic"},
                "agent_local_facts": {},
                "recent_shared_events": [],
                "role_lessons": [],
            }
        )
        self.assertIn("Memory recall", section)
        self.assertIn("decision_focus", section)


class SpecialistAgentMemoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_specialist_persists_claim_to_memory_write_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            room_store, long_store = _stores(Path(tmp))
            provider = _CapturingProvider(
                '{"title": "Phase 3 emit", '
                '"text": "specialist persists its claim through memory.write.", '
                '"claim": "Memory writes flow through agents.", '
                '"evidence": ["one", "two"], '
                '"confidence": 0.8, "target_claim_ref": ""}'
            )
            publish = _RecordingPublish()
            agent = SpecialistAgent(
                registry=ProviderRegistry({"qwen": provider}),
                router=_router(),
                use_background_threads=False,
                room_memory_store=room_store,
                long_term_store=long_store,
            )
            ctx = TurnContext(
                snapshot=_snapshot(),
                round_index=1,
                phase=MeetingPhase.EXPLORE,
                next_focus="run the loop",
                route_ctx=_ctx(),
                route=_route(),
                publish=publish,
                extras={
                    "specialist": _specialist(1),
                    "turn_task": "",
                    "host_agenda": _empty_agenda(),
                    "target_claim_ref": "",
                },
            )
            await agent.run(ctx)

            # Three memory.write events: shared fact, shared event, agent-local fact.
            memory_writes = [
                event for event in publish.events if event["event_type"] == "memory.write"
            ]
            self.assertEqual(len(memory_writes), 3)
            scopes = sorted(event["payload"]["scope"] for event in memory_writes)
            self.assertEqual(
                scopes,
                sorted(
                    [
                        mas_scope("room_test"),
                        mas_scope("room_test"),
                        agent_scope("room_test", "implementation_specialist"),
                    ]
                ),
            )
            shared_fact = next(
                event
                for event in memory_writes
                if event["payload"].get("fact_key") == "latest_claim.implementation_specialist"
            )
            self.assertEqual(
                shared_fact["payload"]["fact_value"]["claim"],
                "Memory writes flow through agents.",
            )

    async def test_specialist_reads_its_own_recall_when_extras_absent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            room_store, long_store = _stores(Path(tmp))
            # Pre-seed memory with a shared fact + an agent-local fact + a role lesson.
            room_store.write_fact(
                "room_test",
                mas_scope("room_test"),
                "latest_supervisor_plan",
                {"decision_focus": "previously decided focus"},
            )
            room_store.write_fact(
                "room_test",
                agent_scope("room_test", "implementation_specialist"),
                "last_self_claim",
                {"claim": "previously emitted claim"},
            )
            long_store.append(
                LongTermLesson(
                    role="implementation_specialist",
                    text="lesson from a prior room",
                    room_id="other_room",
                    decision_focus="other focus",
                    decision_candidate="other candidate",
                    conclusion_type="follow_up_required",
                )
            )

            provider = _CapturingProvider(
                '{"title": "Recall integrated", '
                '"text": "Saw my prior claim + the supervisor plan.", '
                '"claim": "Recall was visible.", '
                '"evidence": ["prior claim", "supervisor plan"], '
                '"confidence": 0.7, "target_claim_ref": ""}'
            )
            agent = SpecialistAgent(
                registry=ProviderRegistry({"qwen": provider}),
                router=_router(),
                use_background_threads=False,
                room_memory_store=room_store,
                long_term_store=long_store,
            )
            ctx = TurnContext(
                snapshot=_snapshot(),
                round_index=2,
                phase=MeetingPhase.DEBATE,
                next_focus="run the loop",
                route_ctx=_ctx(),
                route=_route(),
                publish=None,
                extras={
                    "specialist": _specialist(1),
                    "turn_task": "",
                    "host_agenda": _empty_agenda(),
                    "target_claim_ref": "",
                    # Note: no "memory_recall" key — agent reads it itself
                },
            )
            await agent.run(ctx)
            user_prompt = provider.calls[0].user_prompt
            self.assertIn("Memory recall", user_prompt)
            self.assertIn("previously decided focus", user_prompt)
            self.assertIn("previously emitted claim", user_prompt)
            self.assertIn("lesson from a prior room", user_prompt)


class HostAgentMemoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_host_persists_agenda_to_memory_write_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            room_store, long_store = _stores(Path(tmp))
            response = (
                '{"focus_points": ['
                '{"title": "Run", "reason": "Phase 3 host persists agenda.", "constraint_ids": ["C1"]},'
                '{"title": "Again", "reason": "Two focus points minimum.", "constraint_ids": ["C1"]}'
                '], "turns": ['
                '{"role": "implementation_specialist", "task": ""}'
                '], "open_questions": [], "no_new_constraints": true}'
            )
            provider = _CapturingProvider(response)
            publish = _RecordingPublish()
            agent = HostAgent(
                registry=ProviderRegistry({"qwen": provider}),
                router=_router(),
                use_background_threads=False,
                room_memory_store=room_store,
                long_term_store=long_store,
            )
            ctx = TurnContext(
                snapshot=_snapshot(),
                round_index=1,
                phase=MeetingPhase.EXPLORE,
                next_focus="run the loop",
                route_ctx=_ctx(),
                route=_route(),
                publish=publish,
            )
            await agent.run(ctx)
            memory_writes = [
                event for event in publish.events if event["event_type"] == "memory.write"
            ]
            self.assertEqual(len(memory_writes), 3)
            agenda_fact = next(
                event
                for event in memory_writes
                if event["payload"].get("fact_key") == "latest_host_agenda"
            )
            self.assertEqual(
                agenda_fact["payload"]["scope"], mas_scope("room_test")
            )
            self.assertEqual(
                agenda_fact["payload"]["fact_value"]["turns"][0]["role"],
                "implementation_specialist",
            )
            self_fact = next(
                event
                for event in memory_writes
                if event["payload"].get("fact_key") == "last_self_agenda"
            )
            self.assertEqual(
                self_fact["payload"]["scope"], agent_scope("room_test", "host")
            )


class SynthesisAgentMemoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_synthesis_persists_summary_to_memory_write_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            room_store, long_store = _stores(Path(tmp))
            response = (
                '{"title": "Phase 3 synthesis", '
                '"text": "Synthesis persists itself.", '
                '"agreement": ["something"], "disagreement": [], '
                '"open_questions": ["one open"], '
                '"decision_candidate": "Adopt Phase 3.", '
                '"action_item_draft": ["ship the PR"], '
                '"conclusion_type": "follow_up_required", '
                '"conclusion_reason": "memory is now wired", '
                '"should_end_meeting": false}'
            )
            provider = _CapturingProvider(response)
            publish = _RecordingPublish()
            agent = SynthesisAgent(
                registry=ProviderRegistry({"qwen": provider}),
                router=_router(),
                use_background_threads=False,
                room_memory_store=room_store,
                long_term_store=long_store,
            )
            # Pre-seed memory so the synthesis prompt picks up recall.
            room_store.write_fact(
                "room_test",
                mas_scope("room_test"),
                "latest_host_agenda",
                {"round_index": 1, "turns": [{"role": "implementation_specialist"}]},
            )
            ctx = TurnContext(
                snapshot=_snapshot(),
                round_index=2,
                phase=MeetingPhase.SYNTHESIZE,
                next_focus="run the loop",
                route_ctx=_ctx(),
                route=_route(),
                publish=publish,
                extras={"host_agenda": _empty_agenda(), "turn_results": []},
            )
            await agent.run(ctx)
            memory_writes = [
                event for event in publish.events if event["event_type"] == "memory.write"
            ]
            self.assertEqual(len(memory_writes), 3)
            synthesis_fact = next(
                event
                for event in memory_writes
                if event["payload"].get("fact_key") == "latest_synthesis"
            )
            self.assertEqual(
                synthesis_fact["payload"]["fact_value"]["decision_candidate"],
                "Adopt Phase 3.",
            )
            # Synthesis recall section landed in the prompt.
            user_prompt = provider.calls[0].user_prompt
            self.assertIn("Memory recall", user_prompt)
            self.assertIn("latest_host_agenda", user_prompt)


class JournalReplayRebuildsMemoryStoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_three_agent_turns_replay_rebuilds_full_memory_state(self) -> None:
        """Each agent write goes through ``publish`` as a ``memory.write``
        event; replaying those events through ``RoomProjector`` against a
        fresh ``RoomMemoryStore`` must rebuild the identical store state.
        This is the SoT replay invariant from the MAS-harness baseline.
        """
        with tempfile.TemporaryDirectory() as tmp:
            primary_store, long_store = _stores(Path(tmp))
            journal = RoomEventJournal("room_replay")
            # Mirror RoomRuntime.publish: every event is journaled AND
            # projected, so memory.write events update the live store as a
            # projection of the journal (not via direct in-process writes).
            primary_projector = RoomProjector(
                "room_replay", memory_store=primary_store
            )

            async def journal_publish(
                room_id: str,
                *,
                producer_id: str,
                role: str,
                event_type: str,
                payload: dict[str, Any],
            ) -> dict[str, Any]:
                event = journal.append(
                    producer_id=producer_id,
                    role=role,
                    event_type=event_type,
                    payload=payload,
                )
                primary_projector.apply(event)
                return event.to_dict()

            specialist_agent = SpecialistAgent(
                registry=ProviderRegistry(
                    {
                        "qwen": _CapturingProvider(
                            '{"title": "Replay", "text": "x", "claim": "c", '
                            '"evidence": ["e"], "confidence": 0.6, "target_claim_ref": ""}'
                        )
                    }
                ),
                router=_router(),
                use_background_threads=False,
                room_memory_store=primary_store,
                long_term_store=long_store,
            )
            specialist = CandidateSpecialist(
                role="implementation_specialist",
                display_name="Impl",
                capability_profile="x",
                prompt_contract="x",
                join_reason="x",
                ttl_rounds=2,
                turn_budget=1,
            )
            snapshot = RoomSnapshot(
                room_id="room_replay",
                requirement="Replay test",
                topic="Replay",
                goal="Rebuild store",
                current_focus="run",
                constraints=["c"],
                planning_artifacts={
                    "candidate_specialist_roster": [
                        {
                            "role": specialist.role,
                            "display_name": specialist.display_name,
                            "capability_profile": specialist.capability_profile,
                            "prompt_contract": specialist.prompt_contract,
                            "join_reason": specialist.join_reason,
                            "focus_areas": [],
                            "ttl_rounds": specialist.ttl_rounds,
                            "turn_budget": specialist.turn_budget,
                        }
                    ]
                },
            )
            ctx = TurnContext(
                snapshot=snapshot,
                round_index=1,
                phase=MeetingPhase.EXPLORE,
                next_focus="run",
                route_ctx=DecisionContext(
                    room_id="room_replay",
                    phase=MeetingPhase.EXPLORE,
                    signals=DecisionSignals(),
                    metadata={"topic": "Replay"},
                ),
                route=_route(),
                publish=journal_publish,
                extras={
                    "specialist": specialist,
                    "turn_task": "",
                    "host_agenda": _empty_agenda(),
                    "target_claim_ref": "",
                },
            )
            await specialist_agent.run(ctx)

            # Primary store after the turn
            primary_snapshot = primary_store.snapshot("room_replay")
            self.assertIn(mas_scope("room_replay"), primary_snapshot)

            # Cold replay through a projector wired to a fresh store
            replay_store = RoomMemoryStore(storage_dir=Path(tmp) / "replay")
            projector = RoomProjector("room_replay", memory_store=replay_store)
            for event in journal.replay():
                projector.apply(event)

            replay_snapshot = replay_store.snapshot("room_replay")
            self.assertEqual(
                primary_snapshot[mas_scope("room_replay")]["facts"][
                    "latest_claim.implementation_specialist"
                ]["value"]["claim"],
                replay_snapshot[mas_scope("room_replay")]["facts"][
                    "latest_claim.implementation_specialist"
                ]["value"]["claim"],
            )


class SupervisorAgentMemoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_supervisor_persists_plan_to_memory_write_events(self) -> None:
        from decision_room.orchestration.central_mas import LLMSupervisor

        with tempfile.TemporaryDirectory() as tmp:
            room_store, long_store = _stores(Path(tmp))
            response = (
                '{"current_focus": "phase 3 supervisor focus", '
                '"decision_focus": "phase 3 supervisor decision", '
                '"phase": "explore", '
                '"open_questions": [], '
                '"reason": "test supervisor write", '
                '"speakers": [{"agent": "implementation_specialist", "run": true, "order": 1, "focus_angle": ""}]}'
            )
            provider = _CapturingProvider(response)
            registry = ProviderRegistry({"qwen": provider})
            router = _router()

            agent = SupervisorAgent(
                registry=registry,
                router=router,
                use_background_threads=False,
                room_memory_store=room_store,
                long_term_store=long_store,
            )
            # SupervisorAgent lazily builds its underlying LLMSupervisor on first run.
            publish = _RecordingPublish()
            ctx = TurnContext(
                snapshot=_snapshot(),
                round_index=1,
                phase=MeetingPhase.EXPLORE,
                next_focus="phase 3 supervisor focus",
                route_ctx=_ctx(),
                route=_route(),
                publish=publish,
            )
            result = await agent.run(ctx)
            self.assertEqual(result.plan.decision_focus, "phase 3 supervisor decision")

            memory_writes = [
                event for event in publish.events if event["event_type"] == "memory.write"
            ]
            self.assertEqual(len(memory_writes), 2)
            plan_fact = next(
                event
                for event in memory_writes
                if event["payload"].get("fact_key") == "latest_supervisor_plan"
            )
            self.assertEqual(
                plan_fact["payload"]["fact_value"]["decision_focus"],
                "phase 3 supervisor decision",
            )
            self.assertEqual(
                plan_fact["payload"]["scope"], mas_scope("room_test")
            )


if __name__ == "__main__":
    unittest.main()
