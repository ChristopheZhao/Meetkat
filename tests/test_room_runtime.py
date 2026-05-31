import unittest
import asyncio

from decision_room.policies.room_control import RoomControlConfig, RoomControlPolicy
from decision_room.orchestration.brief_planner import (
    MeetingBrief,
    RequirementPlanningError,
    RequirementPlanningService,
    RoomStartContractDraft,
)
from decision_room.orchestration.room_executor import RoomMessage, RoomRound
from decision_room.runtime.room_projector import RoomProjector
from decision_room.runtime.room_runtime import (
    RoomRuntime,
    RoomStateError,
    RuntimeConfig,
)
from decision_room.mas.types import ActionType, CoordinationAction, DecisionSignals, MeetingPhase


class StaticPlanner:
    def plan_requirement(self, requirement: str) -> MeetingBrief:
        return MeetingBrief(
            requirement=requirement,
            topic="Engineering review room",
            goal="Reach a stable candidate decision with clear action items.",
            constraints=["Human override must remain available."],
            open_questions=[],
            current_focus="stabilize the first candidate proposal",
            room_start_contract=RoomStartContractDraft(),
        )


class PreflightPlanner:
    def plan_requirement(self, requirement: str) -> MeetingBrief:
        return MeetingBrief(
            requirement=requirement,
            topic="Real-provider validation room",
            goal="Validate the real provider-backed room path without demo fallback.",
            constraints=["Real provider execution must stay observable."],
            open_questions=[
                "Which specific provider is expected to back this room?",
                "What counts as success for this validation run?",
                "Is there an existing host agent implementation that already enforces topology leadership?",
            ],
            current_focus="separate hard external prerequisites from contextual follow-up questions before the room starts",
            room_start_contract=RoomStartContractDraft(
                contextual_open_questions=[
                    "whether an existing host agent implementation already enforces topology leadership can stay a contextual in-room question"
                ],
            ),
        )


class ContextAwarePreflightPlanner:
    def plan_requirement(self, requirement: str) -> MeetingBrief:
        return MeetingBrief(
            requirement=requirement,
            topic="Validation contract room",
            goal="Validate the provider-backed room path against an explicit operator contract.",
            constraints=["Validation contract must stay explicit."],
            open_questions=[
                "What is the current identity or configuration identifier of the primary planner provider?",
                "What is the current value of brief_source in the active room context?",
                "Is there a specific test scenario or input payload required to trigger and validate the host-led topology behavior?",
                "What observable signal confirms the planner is bound and ready before planning.completed?",
                "How is WebSocket transport fidelity validated in real time and how do we rule out silent SSE fallback?",
                "Is there a required minimum length or structure for current_turns projection (e.g., ≥1 turn, specific fields)?",
                "What constitutes authoritative snapshot/replay evidence in this context?",
                "What are the valid values for conclusion_type and conclusion_reason in the meeting conclusion contract?",
            ],
            current_focus="answer runtime-known versus operator-supplied validation questions before room start",
            room_start_contract=RoomStartContractDraft(
                contextual_open_questions=[
                    "planner binding readiness signal should stay explicit for this room",
                    "transport fidelity contract should stay explicit for this room",
                    "current_turns projection contract should stay explicit for this room",
                ],
            ),
        )


class FailingPlanner:
    def plan_requirement(self, requirement: str) -> MeetingBrief:
        raise RequirementPlanningError(
            "provider network error",
            error_code="planner_upstream_error",
            status_code=502,
            can_fallback=False,
        )


class StubRoomExecutor:
    async def build_round(self, snapshot, round_index: int) -> RoomRound:
        return RoomRound(
            phase=MeetingPhase.EXPLORE,
            signals=DecisionSignals(
                support=0.62,
                confidence=0.70,
                risk_penalty=0.12,
                margin_top1_top2=0.09,
                disagreement_index=0.42,
            ),
            plan_topic=snapshot.topic,
            next_focus=snapshot.current_focus or "stabilize the first candidate proposal",
            coordination=CoordinationAction(
                action_type=ActionType.SPEAK,
                reason="continue evidence collection",
                target_role="implementation_specialist",
            ),
            consensus_score=0.71,
            consensus_should_end=False,
            should_end=False,
            consensus_reason="continue meeting; convergence conditions unmet",
            end_reason="",
            messages=[
                RoomMessage(
                    role="host",
                    title="Round focus",
                    text="Focus on the highest-risk unknown for this requirement.",
                    artifacts={
                        "next_focus": "stabilize the first candidate proposal",
                        "round_goal": "reach a concrete candidate decision",
                        "target_roles": [
                            "implementation_specialist",
                            "risk_specialist",
                        ],
                        "turns": [
                            {
                                "role": "implementation_specialist",
                                "task": "Propose the minimum runtime contract that preserves replay and override.",
                            },
                            {
                                "role": "risk_specialist",
                                "task": "Stress-test the proposal against transcript/runtime divergence.",
                            },
                        ],
                        "focus_points": [],
                    },
                ),
                RoomMessage(
                    role="implementation_specialist",
                    title="Implementation readout",
                    text="The runtime should keep replay and human override as first-class behavior.",
                    artifacts={
                        "claim": "Keep replay and human override first-class.",
                        "evidence": ["Visible transcript", "Replay consistency"],
                        "confidence": 0.72,
                        "target_claim_ref": "",
                    },
                ),
                RoomMessage(
                    role="risk_specialist",
                    title="Risk readout",
                    text="The room will drift if transcript visibility diverges from the execution state.",
                    artifacts={
                        "claim": "Avoid transcript/runtime divergence.",
                        "evidence": ["Room events must be authoritative"],
                        "confidence": 0.66,
                        "target_claim_ref": "Keep replay and human override first-class.",
                    },
                ),
            ],
            decision_candidate="Ship a runtime-first room with real events.",
            action_items=["Wire the real executor.", "Keep browser verification deferred until B1-B4 close."],
            open_questions=["How much automation before override?"],
            summary_text="Synthesis summary for the current round.",
            conclusion_type="follow_up_required",
            conclusion_reason="The runtime-first direction is viable but still needs follow-up implementation work.",
            synthesis_message=RoomMessage(
                role="synthesis",
                title="Synthesis note",
                text="Synthesis summary for the current round.",
                artifacts={
                    "agreement": ["Room events drive the UI."],
                    "disagreement": ["How much automation to allow before override."],
                    "decision_candidate": "Ship a runtime-first room with real events.",
                    "action_item_draft": ["Wire the real executor.", "Keep browser verification deferred until B1-B4 close."],
                    "conclusion_type": "follow_up_required",
                    "conclusion_reason": "The runtime-first direction is viable but still needs follow-up implementation work.",
                    "should_end_meeting": False,
                },
            ),
        )


class BlockingRoomExecutor:
    def __init__(self) -> None:
        self._gate = None

    async def build_round(self, snapshot, round_index: int) -> RoomRound:
        if self._gate is None:
            import asyncio

            self._gate = asyncio.Event()
        await self._gate.wait()
        raise AssertionError("blocking executor should never be released in this test")


class DynamicRoleRoomExecutor:
    async def build_round(self, snapshot, round_index: int) -> RoomRound:
        return RoomRound(
            phase=MeetingPhase.SYNTHESIZE,
            signals=DecisionSignals(
                support=0.81,
                confidence=0.84,
                risk_penalty=0.06,
                margin_top1_top2=0.14,
                disagreement_index=0.28,
            ),
            plan_topic=snapshot.topic,
            next_focus="merge the planned specialist inputs into a conclusion",
            coordination=CoordinationAction(
                action_type=ActionType.CHECK_CONSENSUS,
                reason="the host has enough specialist input to evaluate convergence",
                target_role="risk_specialist",
            ),
            consensus_score=0.82,
            consensus_should_end=True,
            should_end=True,
            consensus_reason="consensus threshold reached",
            end_reason="consensus threshold reached",
            messages=[
                RoomMessage(
                    role="host",
                    title="Round focus",
                    text="Host requests the planned specialists to converge on the final MVP direction.",
                    artifacts={
                        "next_focus": "merge the planned specialist inputs into a conclusion",
                        "round_goal": "reach a concrete candidate decision",
                        "target_roles": ["implementation_specialist", "risk_specialist"],
                        "turns": [
                            {
                                "role": "implementation_specialist",
                                "task": "Confirm the host-led topology is implementable with event-driven specialist activation.",
                            },
                            {
                                "role": "risk_specialist",
                                "task": "Stress-test the topology against fixed-round semantic leakage.",
                            },
                        ],
                        "focus_points": [],
                    },
                ),
                RoomMessage(
                    role="implementation_specialist",
                    title="Implementation readout",
                    text="The host-led topology is implementable if specialist activation stays event-driven.",
                    artifacts={
                        "claim": "Keep specialist activation event-driven.",
                        "evidence": ["Planning output already identifies candidate specialists."],
                        "confidence": 0.81,
                        "capability_profile": "Evaluates feasibility and integration shape.",
                        "target_claim_ref": "",
                    },
                ),
                RoomMessage(
                    role="risk_specialist",
                    title="Risk readout",
                    text="Control must not infer meeting semantics from fixed rounds.",
                    artifacts={
                        "claim": "Remove fixed-round semantic shortcuts.",
                        "evidence": ["Legacy decision-round shortcuts still leak into routing assumptions."],
                        "confidence": 0.78,
                        "capability_profile": "Surfaces failure and recovery risk.",
                        "target_claim_ref": "Keep specialist activation event-driven.",
                    },
                ),
            ],
            decision_candidate="Continue replacing fixed roster assumptions with planned dynamic specialists.",
            action_items=["Remove fixed four-message shape.", "Keep synthesis non-speaking and memory-backed."],
            open_questions=["How should replay expose structured synthesis alongside dynamic turns?"],
            summary_text="Structured synthesis for the current round.",
            conclusion_type="candidate_ready",
            conclusion_reason="The host-led topology is coherent enough to advance into the next implementation slice.",
            synthesis_message=RoomMessage(
                role="synthesis",
                title="Synthesis note",
                text="Structured synthesis for the current round.",
                artifacts={
                    "agreement": ["Host stays persistent.", "Specialists should join on demand."],
                    "disagreement": [],
                    "decision_candidate": "Continue replacing fixed roster assumptions with planned dynamic specialists.",
                    "action_item_draft": ["Remove fixed four-message shape.", "Keep synthesis non-speaking and memory-backed."],
                    "conclusion_type": "candidate_ready",
                    "conclusion_reason": "The host-led topology is coherent enough to advance into the next implementation slice.",
                    "should_end_meeting": True,
                },
            ),
        )


class OrchestrationEndedRoomExecutor:
    async def build_round(self, snapshot, round_index: int) -> RoomRound:
        return RoomRound(
            phase=MeetingPhase.SYNTHESIZE,
            signals=DecisionSignals(
                support=0.69,
                confidence=0.73,
                risk_penalty=0.11,
                margin_top1_top2=0.09,
                disagreement_index=0.34,
            ),
            plan_topic=snapshot.topic,
            next_focus="close the room and move remaining work outside the meeting",
            coordination=CoordinationAction(
                action_type=ActionType.CHECK_CONSENSUS,
                reason="the synthesis output has reached a stable follow-up conclusion",
                target_role="implementation_specialist",
            ),
            consensus_score=0.67,
            consensus_should_end=False,
            should_end=True,
            consensus_reason="continue meeting; convergence conditions unmet",
            end_reason="orchestration signaled a stable follow-up handoff",
            messages=[
                RoomMessage(
                    role="host",
                    title="Round focus",
                    text="Host requests a final synthesis and external follow-up handoff.",
                    artifacts={
                        "next_focus": "close the room and move remaining work outside the meeting",
                        "round_goal": "produce an explicit follow-up conclusion",
                        "target_roles": ["implementation_specialist"],
                        "turns": [
                            {
                                "role": "implementation_specialist",
                                "task": "Confirm the remaining work belongs outside the room loop.",
                            }
                        ],
                        "focus_points": [],
                    },
                ),
                RoomMessage(
                    role="implementation_specialist",
                    title="Implementation readout",
                    text="The remaining work is implementation follow-through, not another in-room debate.",
                    artifacts={
                        "claim": "Close the room and move follow-up into implementation.",
                        "evidence": ["The room has an explicit candidate and action items."],
                        "confidence": 0.75,
                        "target_claim_ref": "",
                    },
                ),
            ],
            decision_candidate="Close the room and execute the follow-up work outside the meeting.",
            action_items=["Implement the remaining runtime work.", "Re-open a room only if new evidence appears."],
            open_questions=["Which concrete implementation slice should ship first?"],
            summary_text="Structured synthesis for an external follow-up handoff.",
            conclusion_type="follow_up_required",
            conclusion_reason="The room has reached a stable follow-up outcome and should hand remaining work to implementation outside the meeting.",
            synthesis_message=RoomMessage(
                role="synthesis",
                title="Synthesis note",
                text="Structured synthesis for an external follow-up handoff.",
                artifacts={
                    "agreement": ["The next step is implementation work outside the room."],
                    "disagreement": [],
                    "decision_candidate": "Close the room and execute the follow-up work outside the meeting.",
                    "action_item_draft": ["Implement the remaining runtime work.", "Re-open a room only if new evidence appears."],
                    "conclusion_type": "follow_up_required",
                    "conclusion_reason": "The room has reached a stable follow-up outcome and should hand remaining work to implementation outside the meeting.",
                    "should_end_meeting": True,
                },
            ),
        )


class RoomRuntimeTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _fast_runtime_config():
        return RuntimeConfig(
            message_chunk_delay_sec=0.0,
            between_turn_delay_sec=0.0,
            between_round_delay_sec=0.0,
            max_rounds=4,
        )

    async def _wait_until(self, predicate, timeout: float = 1.0) -> None:
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            if predicate():
                return
            await asyncio.sleep(0.01)
        self.fail("timed out waiting for room state transition")

    async def asyncTearDown(self) -> None:
        runtime = getattr(self, "runtime", None)
        if runtime is not None:
            await runtime.close()

    async def test_create_room_starts_runtime_and_emits_core_events(self) -> None:
        self.runtime = RoomRuntime(
            executor=StubRoomExecutor(),
            requirement_planner=RequirementPlanningService(primary_planner=StaticPlanner()),
        )
        snapshot = await self.runtime.create_room(
            requirement=(
                "We need a real-time decision room MVP for engineering review. "
                "It must keep replay, visible transcript, and human override."
            ),
        )
        self.assertEqual(snapshot["topic"], "Engineering review room")
        self.assertIn("human override", " ".join(snapshot["constraints"]).lower())
        self.assertTrue(snapshot["requirement"].startswith("We need a real-time"))
        self.assertEqual(snapshot["status"], "running")
        self.assertEqual(snapshot["brief_source"], "agent")
        self.assertEqual([item["role"] for item in snapshot["participants"]], ["host"])
        self.assertGreater(
            len(snapshot["planning_artifacts"]["candidate_specialist_roster"]),
            0,
        )

        replay = self.runtime.replay(snapshot["room_id"])
        event_types = [item["event_type"] for item in replay]
        self.assertEqual(event_types[0], "planning.completed")
        self.assertEqual(event_types[1], "room.started")
        self.assertIn("agent.joined", event_types)

    async def test_create_room_surfaces_planner_failure(self) -> None:
        self.runtime = RoomRuntime(
            executor=StubRoomExecutor(),
            requirement_planner=RequirementPlanningService(primary_planner=FailingPlanner()),
        )
        with self.assertRaises(RequirementPlanningError) as ctx:
            await self.runtime.create_room(
                requirement="We need a deterministic close path for the room runtime."
            )
        self.assertEqual(ctx.exception.error_code, "planner_upstream_error")

    async def test_human_override_ends_room(self) -> None:
        self.runtime = RoomRuntime(
            executor=StubRoomExecutor(),
            requirement_planner=RequirementPlanningService(primary_planner=StaticPlanner()),
        )
        snapshot = await self.runtime.create_room(
            requirement="We need a deterministic close path for the room runtime."
        )
        ended = await self.runtime.post_human_override(
            snapshot["room_id"], "Stop the meeting and lock the current draft."
        )
        self.assertEqual(ended["status"], "ended")
        self.assertIn("Stop the meeting", ended["ended_reason"])

    async def test_ended_room_rejects_further_human_writes(self) -> None:
        self.runtime = RoomRuntime(
            executor=StubRoomExecutor(),
            requirement_planner=RequirementPlanningService(primary_planner=StaticPlanner()),
        )
        snapshot = await self.runtime.create_room(
            requirement="We need a deterministic close path for the room runtime."
        )
        ended = await self.runtime.post_human_override(
            snapshot["room_id"], "Stop the meeting and lock the current draft."
        )

        with self.assertRaises(RoomStateError):
            await self.runtime.post_human_message(
                snapshot["room_id"], "Try to reopen the room after it ended."
            )
        with self.assertRaises(RoomStateError):
            await self.runtime.post_human_override(
                snapshot["room_id"], "Try to override an already ended room."
            )

        current = self.runtime.get_snapshot(snapshot["room_id"])
        self.assertEqual(current["ended_reason"], ended["ended_reason"])

    async def test_runtime_snapshot_can_be_rebuilt_from_journal(self) -> None:
        self.runtime = RoomRuntime(
            executor=BlockingRoomExecutor(),
            requirement_planner=RequirementPlanningService(primary_planner=StaticPlanner()),
        )
        snapshot = await self.runtime.create_room(
            requirement=(
                "We need a replayable engineering review room where the journal is the only "
                "fact source."
            ),
        )
        await self.runtime.post_human_message(
            snapshot["room_id"], "Keep the event journal as the only source of room facts."
        )

        session = self.runtime._require_session(snapshot["room_id"])
        rebuilt = session.journal.rebuild(RoomProjector(snapshot["room_id"])).snapshot.public_dict()
        current = session.projector.snapshot.public_dict()

        self.assertEqual(rebuilt, current)

    async def test_human_override_does_not_emit_duplicate_meeting_end(self) -> None:
        self.runtime = RoomRuntime(
            executor=BlockingRoomExecutor(),
            requirement_planner=RequirementPlanningService(primary_planner=StaticPlanner()),
        )
        snapshot = await self.runtime.create_room(
            requirement="Need a room that closes through a single control exit."
        )
        await self.runtime.post_human_override(
            snapshot["room_id"], "Stop now and keep the current decision draft."
        )

        replay = self.runtime.replay(snapshot["room_id"])
        ended_events = [event for event in replay if event["event_type"] == "meeting.ended"]

        self.assertEqual(len(ended_events), 1)
        self.assertIn("human override", ended_events[0]["payload"]["reason"])

    async def test_unregister_subscriber_after_runtime_close_is_noop(self) -> None:
        self.runtime = RoomRuntime(
            executor=BlockingRoomExecutor(),
            requirement_planner=RequirementPlanningService(primary_planner=StaticPlanner()),
        )
        snapshot = await self.runtime.create_room(
            requirement="Need transport cleanup to tolerate runtime shutdown races."
        )
        queue = await self.runtime.register_subscriber(snapshot["room_id"])

        await self.runtime.close()
        await self.runtime.unregister_subscriber(snapshot["room_id"], queue)

    async def test_round_budget_is_controlled_by_room_control_policy(self) -> None:
        self.runtime = RoomRuntime(
            executor=StubRoomExecutor(),
            config=self._fast_runtime_config(),
            control_policy=RoomControlPolicy(RoomControlConfig(max_rounds=1)),
            requirement_planner=RequirementPlanningService(primary_planner=StaticPlanner()),
        )
        snapshot = await self.runtime.create_room(
            requirement="Need the room round budget to be controlled through control policy."
        )

        await self._wait_until(
            lambda: self.runtime.get_snapshot(snapshot["room_id"])["status"] == "ended"
        )
        current = self.runtime.get_snapshot(snapshot["room_id"])

        self.assertEqual(current["status"], "ended")
        self.assertEqual(current["conclusion_type"], "follow_up_required")
        self.assertEqual(
            current["ended_reason"],
            "The runtime-first direction is viable but still needs follow-up implementation work.",
        )

    async def test_runtime_prefers_orchestration_end_signal_before_budget_gate(self) -> None:
        self.runtime = RoomRuntime(
            executor=OrchestrationEndedRoomExecutor(),
            config=self._fast_runtime_config(),
            control_policy=RoomControlPolicy(RoomControlConfig(max_rounds=4)),
            requirement_planner=RequirementPlanningService(primary_planner=StaticPlanner()),
        )
        snapshot = await self.runtime.create_room(
            requirement="Need orchestration to decide when a stable follow-up outcome should close the room."
        )

        await self._wait_until(
            lambda: self.runtime.get_snapshot(snapshot["room_id"])["status"] == "ended"
        )
        current = self.runtime.get_snapshot(snapshot["room_id"])
        replay = self.runtime.replay(snapshot["room_id"])
        ended_event = [event for event in replay if event["event_type"] == "meeting.ended"][-1]

        self.assertEqual(current["status"], "ended")
        self.assertEqual(current["round_index"], 1)
        self.assertEqual(current["conclusion_type"], "follow_up_required")
        self.assertEqual(
            ended_event["payload"]["control_reason"],
            "control gate accepted orchestration end signal",
        )
        self.assertEqual(
            ended_event["payload"]["orchestration_end_reason"],
            "orchestration signaled a stable follow-up handoff",
        )
        self.assertEqual(
            ended_event["payload"]["reason"],
            "The room has reached a stable follow-up outcome and should hand remaining work to implementation outside the meeting.",
        )
        self.assertEqual(
            current["control_reason"],
            "control gate accepted orchestration end signal",
        )
        self.assertEqual(
            current["orchestration_end_reason"],
            "orchestration signaled a stable follow-up handoff",
        )

    async def test_preflight_room_surfaces_planner_open_questions_as_contextual_only(self) -> None:
        self.runtime = RoomRuntime(
            executor=BlockingRoomExecutor(),
            config=self._fast_runtime_config(),
            requirement_planner=RequirementPlanningService(primary_planner=PreflightPlanner()),
            runtime_readiness={
                "primary_planner_ready": True,
                "executor_ready": True,
                "primary_unavailable_reason": "",
                "executor_reason": "",
            },
        )

        preflight = self.runtime.preflight_room(
            "Need a real provider-backed room run that validates the topology without demo fallback."
        )

        room_start_contract = preflight["room_start_contract"]
        # Infra preflight is the only gate. Planner-emitted open questions never
        # block room start — they surface as contextual signals for the in-meeting
        # supervisor / specialists to address.
        self.assertTrue(room_start_contract["room_start_ready"])
        self.assertTrue(room_start_contract["runtime_bootstrap_ready"])
        self.assertEqual(room_start_contract["system_blockers"], [])
        self.assertEqual(
            room_start_contract["contextual_open_questions"],
            [
                "whether an existing host agent implementation already enforces topology leadership can stay a contextual in-room question",
            ],
        )
        self.assertNotIn("missing_operator_inputs", room_start_contract)

    async def test_preflight_room_marks_runtime_bootstrap_as_blocker(self) -> None:
        self.runtime = RoomRuntime(
            executor=BlockingRoomExecutor(),
            config=self._fast_runtime_config(),
            requirement_planner=RequirementPlanningService(primary_planner=PreflightPlanner()),
            runtime_readiness={
                "primary_planner_ready": False,
                "executor_ready": True,
                "primary_unavailable_reason": "missing planner env",
                "executor_reason": "",
            },
        )

        preflight = self.runtime.preflight_room(
            "Need a real provider-backed room run that validates the topology without demo fallback."
        )

        room_start_contract = preflight["room_start_contract"]
        self.assertFalse(room_start_contract["room_start_ready"])
        self.assertFalse(room_start_contract["runtime_bootstrap_ready"])
        self.assertEqual(room_start_contract["system_blockers"], ["missing planner env"])
        self.assertEqual(room_start_contract["recommended_surface"], "runtime_readiness")

    async def test_preflight_room_uses_operator_context_to_answer_validation_contract_questions(self) -> None:
        self.runtime = RoomRuntime(
            executor=BlockingRoomExecutor(),
            config=self._fast_runtime_config(),
            requirement_planner=RequirementPlanningService(
                primary_planner=ContextAwarePreflightPlanner()
            ),
            runtime_readiness={
                "primary_planner_ready": True,
                "executor_ready": True,
                "primary_unavailable_reason": "",
                "executor_reason": "",
                "planner_target": {"supplier": "openai", "model": "gpt-default"},
                "executor_targets": {
                    "default": {"supplier": "openai", "model": "gpt-default"},
                    "escalation": {"supplier": "openai", "model": "gpt-escalation"},
                    "fallback": {"supplier": "openai", "model": "gpt-fallback"},
                },
            },
        )

        preflight = self.runtime.preflight_room(
            "Need a real provider-backed room run that validates the topology without demo fallback.",
            operator_context={
                "brief_source": "agent",
                "success_criteria": ["brief_source=agent", "explicit conclusion contract"],
                "validation_scenario": [
                    "use the default validation requirement as the trigger payload",
                    "run the room until meeting.ended and inspect snapshot/replay directly",
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
                "evidence_contract": ["snapshot/replay evidence must come from authoritative surfaces"],
                "conclusion_contract": [
                    "meeting conclusion contract requires conclusion_type and conclusion_reason"
                ],
            },
        )

        self.assertTrue(preflight["room_start_contract"]["room_start_ready"])
        self.assertEqual(preflight["room_start_contract"]["contextual_open_questions"], [])
        self.assertEqual(preflight["room_start_contract"]["system_blockers"], [])
        self.assertNotIn("missing_operator_inputs", preflight["room_start_contract"])
        self.assertEqual(preflight["operator_context"]["brief_source"], "agent")
        self.assertEqual(
            preflight["runtime_context"]["planner_target"]["supplier"],
            "openai",
        )
        self.assertEqual(
            preflight["runtime_context"]["executor_targets"]["default"]["model"],
            "gpt-default",
        )

    async def test_preflight_room_resolves_interactive_entry_scope_contract(self) -> None:
        self.runtime = RoomRuntime(
            executor=BlockingRoomExecutor(),
            config=self._fast_runtime_config(),
            requirement_planner=RequirementPlanningService(primary_planner=StaticPlanner()),
            runtime_readiness={
                "primary_planner_ready": True,
                "executor_ready": True,
                "primary_unavailable_reason": "",
                "executor_reason": "",
            },
        )

        preflight = self.runtime.preflight_room(
            "Need a normal agent-led room start with human control still available after the room opens.",
            entry_scope="interactive_room_start",
        )

        self.assertEqual(preflight["operator_context"]["entry_scope"], "interactive_room_start")
        self.assertTrue(preflight["operator_context"]["entry_contract"])
        self.assertTrue(preflight["operator_context"]["auto_resolved_context"])
        self.assertTrue(preflight["operator_context"]["operator_required_inputs"])
        self.assertTrue(preflight["operator_context"]["human_control_contract"])
        self.assertTrue(preflight["operator_context"]["transport_contract"])

    async def test_preflight_room_rejects_validation_specific_core_entry_scope(self) -> None:
        self.runtime = RoomRuntime(
            executor=BlockingRoomExecutor(),
            config=self._fast_runtime_config(),
            requirement_planner=RequirementPlanningService(primary_planner=StaticPlanner()),
            runtime_readiness={
                "primary_planner_ready": True,
                "executor_ready": True,
                "primary_unavailable_reason": "",
                "executor_reason": "",
            },
        )

        with self.assertRaisesRegex(ValueError, "unsupported entry_scope"):
            self.runtime.preflight_room(
                "Need a provider validation smoke run.",
                entry_scope="provider_validation_smoke",
            )

    async def test_create_room_opens_even_when_planner_left_open_questions(self) -> None:
        # Architectural assertion: the legacy preflight-as-clarification-gate is
        # gone. A room with planner-emitted contextual open questions still opens
        # immediately; clarification happens inside the meeting.
        self.runtime = RoomRuntime(
            executor=BlockingRoomExecutor(),
            config=self._fast_runtime_config(),
            requirement_planner=RequirementPlanningService(primary_planner=PreflightPlanner()),
            runtime_readiness={
                "primary_planner_ready": True,
                "executor_ready": True,
                "primary_unavailable_reason": "",
                "executor_reason": "",
            },
        )

        snapshot = await self.runtime.create_room(
            requirement="Need a real provider-backed room run that validates the topology without demo fallback.",
        )

        self.assertEqual(snapshot["status"], "running")
        self.assertEqual(len(self.runtime.list_rooms()), 1)

    async def test_create_room_persists_preflight_payload_in_planning_artifacts(self) -> None:
        self.runtime = RoomRuntime(
            executor=BlockingRoomExecutor(),
            config=self._fast_runtime_config(),
            requirement_planner=RequirementPlanningService(primary_planner=PreflightPlanner()),
            runtime_readiness={
                "primary_planner_ready": True,
                "executor_ready": True,
                "primary_unavailable_reason": "",
                "executor_reason": "",
            },
        )

        snapshot = await self.runtime.create_room(
            requirement="Need a real provider-backed room run that validates the topology without demo fallback."
        )

        room_start_contract = snapshot["planning_artifacts"]["room_start_contract"]
        self.assertTrue(room_start_contract["room_start_ready"])
        self.assertNotIn("missing_operator_inputs", room_start_contract)
        self.assertIn("runtime_context", snapshot["planning_artifacts"])
        self.assertNotIn("room_start_contract_draft", snapshot["planning_artifacts"])
        self.assertNotIn("open_questions", snapshot["planning_artifacts"])

    async def test_create_room_persists_interactive_entry_scope_contract(self) -> None:
        self.runtime = RoomRuntime(
            executor=BlockingRoomExecutor(),
            config=self._fast_runtime_config(),
            requirement_planner=RequirementPlanningService(primary_planner=StaticPlanner()),
            runtime_readiness={
                "primary_planner_ready": True,
                "executor_ready": True,
                "primary_unavailable_reason": "",
                "executor_reason": "",
                "planner_target": {"supplier": "openai", "model": "gpt-5.4"},
                "executor_targets": {
                    "default": {"supplier": "openai", "model": "gpt-5.4"},
                    "escalation": {"supplier": "openai", "model": "gpt-5.4"},
                },
            },
        )

        snapshot = await self.runtime.create_room(
            requirement="Need a normal interactive room that keeps the entry contract visible after room creation.",
            entry_scope="interactive_room_start",
        )

        self.assertEqual(
            snapshot["planning_artifacts"]["operator_context"]["entry_scope"],
            "interactive_room_start",
        )
        self.assertTrue(snapshot["planning_artifacts"]["operator_context"]["entry_contract"])
        self.assertTrue(snapshot["planning_artifacts"]["operator_context"]["auto_resolved_context"])
        self.assertTrue(snapshot["planning_artifacts"]["operator_context"]["operator_required_inputs"])
        self.assertTrue(
            snapshot["planning_artifacts"]["operator_context"]["human_control_contract"]
        )
        self.assertTrue(snapshot["planning_artifacts"]["operator_context"]["transport_contract"])
        self.assertEqual(
            snapshot["planning_artifacts"]["runtime_context"]["planner_target"]["model"],
            "gpt-5.4",
        )
        self.assertEqual(
            snapshot["planning_artifacts"]["runtime_context"]["executor_targets"]["default"][
                "supplier"
            ],
            "openai",
        )

    async def test_runtime_joins_dynamic_specialists_from_round_messages(self) -> None:
        self.runtime = RoomRuntime(
            executor=DynamicRoleRoomExecutor(),
            config=self._fast_runtime_config(),
            requirement_planner=RequirementPlanningService(primary_planner=StaticPlanner()),
        )
        snapshot = await self.runtime.create_room(
            requirement=(
                "Need a host-led engineering review room where specialists join on demand "
                "and control stays out of meeting semantics."
            ),
        )
        self.assertEqual([item["role"] for item in snapshot["participants"]], ["host"])

        await self._wait_until(
            lambda: self.runtime.get_snapshot(snapshot["room_id"])["status"] == "ended"
        )
        current = self.runtime.get_snapshot(snapshot["room_id"])
        roles = [item["role"] for item in current["participants"]]
        self.assertEqual(
            set(roles),
            {"host", "implementation_specialist", "risk_specialist"},
        )
        self.assertNotIn("synthesis", roles)

        implementation = next(
            item for item in current["participants"] if item["role"] == "implementation_specialist"
        )
        self.assertEqual(implementation["activation"], "on_demand")
        self.assertIn("Evaluates technical feasibility", implementation["capability_profile"])

    async def test_snapshot_and_replay_expose_turn_plan_and_conclusion_contract(self) -> None:
        self.runtime = RoomRuntime(
            executor=DynamicRoleRoomExecutor(),
            config=self._fast_runtime_config(),
            requirement_planner=RequirementPlanningService(primary_planner=StaticPlanner()),
        )
        snapshot = await self.runtime.create_room(
            requirement=(
                "Need a host-led engineering review room where the journal itself proves "
                "the active turn plan and explicit conclusion contract."
            ),
        )

        await self._wait_until(
            lambda: self.runtime.get_snapshot(snapshot["room_id"])["status"] == "ended"
        )
        current = self.runtime.get_snapshot(snapshot["room_id"])
        replay = self.runtime.replay(snapshot["room_id"])

        self.assertEqual(
            [item["role"] for item in current["current_turns"]],
            ["implementation_specialist", "risk_specialist"],
        )
        self.assertEqual(current["conclusion_type"], "candidate_ready")

        host_messages = [
            event for event in replay
            if event["event_type"] == "agent.message" and event["role"] == "host"
        ]
        self.assertGreater(len(host_messages), 0)
        self.assertEqual(
            [item["role"] for item in host_messages[-1]["payload"]["artifacts"]["turns"]],
            ["implementation_specialist", "risk_specialist"],
        )

        ended_events = [event for event in replay if event["event_type"] == "meeting.ended"]
        self.assertEqual(len(ended_events), 1)
        self.assertEqual(
            ended_events[0]["payload"]["conclusion_type"],
            "candidate_ready",
        )
        self.assertIn(
            "advance into the next implementation slice",
            ended_events[0]["payload"]["conclusion_reason"],
        )

    async def test_vague_requirement_opens_room_and_clarifies_in_meeting(self) -> None:
        # PLAN-012 architectural assertion: a deliberately vague requirement
        # must open the room immediately (no preflight clarification gate), let
        # the supervisor surface a clarification through the in-meeting
        # human-message channel, accept the human's reply, converge, and end
        # with an exportable decision record.
        from decision_room.runtime.decision_record import render_decision_record

        clarifying_question = (
            "[Awaiting operator clarification] which cohort and budget should "
            "we plan around?"
        )

        class ClarificationThenConvergeExecutor:
            def __init__(self) -> None:
                self.rounds_seen = 0
                self.last_human_messages: list[str] = []
                self.human_reply_received = asyncio.Event()

            async def build_round(self, snapshot, round_index: int) -> RoomRound:
                self.rounds_seen = round_index
                if round_index >= 2:
                    # Block converging round until the operator's reply has been
                    # observed in the snapshot — that's the architectural point:
                    # the next round only proceeds after in-meeting clarification.
                    await self.human_reply_received.wait()
                self.last_human_messages.append(
                    str(getattr(snapshot, "last_human_message", "") or "")
                )
                if round_index == 1:
                    return RoomRound(
                        phase=MeetingPhase.EXPLORE,
                        signals=DecisionSignals(
                            support=0.30,
                            confidence=0.35,
                            risk_penalty=0.20,
                            margin_top1_top2=0.05,
                            disagreement_index=0.55,
                        ),
                        plan_topic=snapshot.topic,
                        next_focus=clarifying_question,
                        coordination=CoordinationAction(
                            action_type=ActionType.SPEAK,
                            reason="ask the operator a clarifying question before recruiting specialists",
                            target_role="host",
                        ),
                        consensus_score=0.30,
                        consensus_should_end=False,
                        should_end=False,
                        consensus_reason="awaiting operator clarification before evidence collection",
                        end_reason="",
                        messages=[
                            RoomMessage(
                                role="host",
                                title="Awaiting operator clarification",
                                text=clarifying_question,
                                artifacts={
                                    "decision_focus": clarifying_question,
                                    "next_focus": clarifying_question,
                                    "round_goal": "resolve the requirement ambiguity in-meeting",
                                    "target_roles": [],
                                    "turns": [],
                                    "focus_points": [],
                                    "awaiting_human_clarification": True,
                                },
                            ),
                        ],
                        decision_candidate="",
                        action_items=[],
                        open_questions=[clarifying_question],
                        summary_text="awaiting operator clarification; no synthesis yet",
                        conclusion_type="",
                        conclusion_reason="",
                        synthesis_message=RoomMessage(
                            role="synthesis",
                            title="Awaiting clarification",
                            text="No synthesis yet — round is paused on operator clarification.",
                            artifacts={
                                "agreement": [],
                                "disagreement": [],
                                "decision_candidate": "",
                                "action_item_draft": [],
                                "conclusion_type": "",
                                "conclusion_reason": "",
                                "should_end_meeting": False,
                            },
                        ),
                    )
                return RoomRound(
                    phase=MeetingPhase.SYNTHESIZE,
                    signals=DecisionSignals(
                        support=0.82,
                        confidence=0.84,
                        risk_penalty=0.08,
                        margin_top1_top2=0.16,
                        disagreement_index=0.20,
                    ),
                    plan_topic=snapshot.topic,
                    next_focus="commit to the cohort/budget the operator clarified",
                    coordination=CoordinationAction(
                        action_type=ActionType.CHECK_CONSENSUS,
                        reason="operator clarified scope; converge",
                        target_role="implementation_specialist",
                    ),
                    consensus_score=0.83,
                    consensus_should_end=True,
                    should_end=True,
                    consensus_reason="operator clarified ambiguity in-meeting",
                    end_reason="operator clarification accepted",
                    messages=[
                        RoomMessage(
                            role="host",
                            title="Round focus",
                            text="Operator clarified scope; specialists converge on the recommendation.",
                            artifacts={
                                "next_focus": "commit to the cohort/budget the operator clarified",
                                "round_goal": "produce a candidate decision grounded in the operator clarification",
                                "target_roles": ["implementation_specialist"],
                                "turns": [
                                    {
                                        "role": "implementation_specialist",
                                        "task": "Lock in the recommendation against the cohort/budget the operator named.",
                                    }
                                ],
                                "focus_points": [],
                            },
                        ),
                        RoomMessage(
                            role="implementation_specialist",
                            title="Implementation readout",
                            text="Recommendation is feasible under the clarified cohort/budget.",
                            artifacts={
                                "claim": "Proceed with the change for the clarified cohort within the stated budget.",
                                "evidence": ["operator-supplied scope", "stated budget envelope"],
                                "confidence": 0.82,
                                "target_claim_ref": "",
                            },
                        ),
                    ],
                    decision_candidate="Proceed with the change for the operator-clarified cohort within the stated budget.",
                    action_items=["Document the cohort/budget that drove the decision."],
                    open_questions=[],
                    summary_text="Operator clarified the cohort and budget; the room converged.",
                    conclusion_type="candidate_ready",
                    conclusion_reason="Operator clarified the ambiguity in-meeting and the room converged on a candidate decision.",
                    synthesis_message=RoomMessage(
                        role="synthesis",
                        title="Synthesis note",
                        text="Operator clarified the cohort and budget; the room converged.",
                        artifacts={
                            "agreement": ["Scope and budget are now explicit."],
                            "disagreement": [],
                            "decision_candidate": "Proceed with the change for the operator-clarified cohort within the stated budget.",
                            "action_item_draft": ["Document the cohort/budget that drove the decision."],
                            "conclusion_type": "candidate_ready",
                            "conclusion_reason": "Operator clarified the ambiguity in-meeting and the room converged on a candidate decision.",
                            "should_end_meeting": True,
                        },
                    ),
                )

        executor = ClarificationThenConvergeExecutor()
        self.runtime = RoomRuntime(
            executor=executor,
            config=self._fast_runtime_config(),
            requirement_planner=RequirementPlanningService(primary_planner=StaticPlanner()),
            runtime_readiness={
                "primary_planner_ready": True,
                "executor_ready": True,
                "primary_unavailable_reason": "",
                "executor_reason": "",
            },
        )

        snapshot = await self.runtime.create_room(
            requirement="我们要不要做这个改造",
            entry_scope="interactive_room_start",
        )
        room_id = snapshot["room_id"]
        # No clarification gate exists anymore: the room opens immediately.
        self.assertEqual(snapshot["status"], "running")

        await self._wait_until(
            lambda: any(
                event["event_type"] == "agent.message"
                and event["role"] == "host"
                and event["payload"].get("artifacts", {}).get("awaiting_human_clarification") is True
                for event in self.runtime.replay(room_id)
            ),
            timeout=2.0,
        )

        human_reply = "针对企业客户群体，本年度预算 200k 人民币。"
        await self.runtime.post_human_message(room_id, human_reply)
        executor.human_reply_received.set()

        await self._wait_until(
            lambda: self.runtime.get_snapshot(room_id)["status"] == "ended",
            timeout=2.0,
        )

        final = self.runtime.get_snapshot(room_id)
        self.assertEqual(final["conclusion_type"], "candidate_ready")
        self.assertEqual(final["last_human_message"], human_reply)
        # The executor saw the human reply between round 1 and round 2.
        self.assertEqual(executor.last_human_messages[0], "")
        self.assertEqual(executor.last_human_messages[1], human_reply)

        record = render_decision_record(final)
        self.assertIn("决策会议记录", record)
        self.assertIn("Proceed with the change", record)


if __name__ == "__main__":
    unittest.main()
