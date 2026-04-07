import unittest

from decision_room.mas.types import ActionType, CoordinationAction, DecisionSignals, MeetingPhase
from decision_room.orchestration import RoomOrchestrator, RoomOrchestratorConfig
from decision_room.orchestration.room_executor import RoomMessage, RoomRound
from decision_room.runtime.room_models import RoomSnapshot


class StubRoomExecutor:
    async def build_round(self, snapshot: RoomSnapshot, round_index: int) -> RoomRound:
        return RoomRound(
            phase=MeetingPhase.EXPLORE,
            signals=DecisionSignals(
                support=0.6,
                confidence=0.7,
                risk_penalty=0.1,
                margin_top1_top2=0.08,
                disagreement_index=0.4,
            ),
            plan_topic=snapshot.topic,
            next_focus="stabilize the event protocol",
            coordination=CoordinationAction(
                action_type=ActionType.HANDOFF,
                reason="handoff to evidence review",
                target_role="risk_specialist",
            ),
            consensus_score=0.74,
            consensus_should_end=False,
            should_end=False,
            consensus_reason="continue meeting",
            end_reason="",
            messages=[
                RoomMessage("host", "Host", "host focus", {"round_goal": "lock the contract"}),
                RoomMessage(
                    "implementation_specialist",
                    "Implementation Specialist",
                    "implementation claim",
                    {"claim": "claim.a", "target_claim_ref": ""},
                ),
                RoomMessage(
                    "risk_specialist",
                    "Risk Specialist",
                    "risk challenge",
                    {"claim": "claim.b", "target_claim_ref": "claim.a"},
                ),
            ],
            decision_candidate="Keep the room event protocol stable.",
            action_items=["Extract orchestrator."],
            open_questions=["Should round pacing be configurable here?"],
            summary_text="Synthesis summary",
            conclusion_type="follow_up_required",
            conclusion_reason="The room still needs follow-up work on pacing and control semantics.",
            synthesis_message=RoomMessage(
                "synthesis",
                "Synthesis",
                "synthesis summary",
                {
                    "route": {
                        "tier": "escalation",
                        "supplier": "qwen",
                        "model": "qwen-plus",
                        "reason": "synthesis route",
                    }
                },
            ),
        )


class DynamicRoleExecutor:
    async def build_round(self, snapshot: RoomSnapshot, round_index: int) -> RoomRound:
        return RoomRound(
            phase=MeetingPhase.SYNTHESIZE,
            signals=DecisionSignals(
                support=0.78,
                confidence=0.82,
                risk_penalty=0.07,
                margin_top1_top2=0.13,
                disagreement_index=0.24,
            ),
            plan_topic=snapshot.topic,
            next_focus="merge dynamic specialist views",
            coordination=CoordinationAction(
                action_type=ActionType.HANDOFF,
                reason="handoff to the planned challenger",
                target_role="risk_specialist",
            ),
            consensus_score=0.80,
            consensus_should_end=True,
            should_end=True,
            consensus_reason="consensus threshold reached",
            end_reason="consensus threshold reached",
            messages=[
                RoomMessage("host", "Host", "host focus", {"round_goal": "lock the contract"}),
                RoomMessage(
                    "implementation_specialist",
                    "Implementation Specialist",
                    "implementation view",
                    {"claim": "claim.impl", "target_claim_ref": ""},
                ),
                RoomMessage(
                    "risk_specialist",
                    "Risk Specialist",
                    "risk challenge",
                    {"claim": "claim.risk", "target_claim_ref": "claim.impl"},
                ),
            ],
            decision_candidate="Keep the host-led topology.",
            action_items=["Remove fixed roster dependencies."],
            open_questions=["How should replay expose structured synthesis alongside dynamic turns?"],
            summary_text="Synthesis summary",
            conclusion_type="candidate_ready",
            conclusion_reason="The host-led topology is coherent enough to move into the next implementation slice.",
            synthesis_message=RoomMessage("synthesis", "Synthesis", "synthesis note", {}),
        )


class RoomOrchestratorTests(unittest.IsolatedAsyncioTestCase):
    async def test_emit_round_owns_role_event_sequence(self) -> None:
        orchestrator = RoomOrchestrator(
            StubRoomExecutor(),
            RoomOrchestratorConfig(
                message_chunk_delay_sec=0.0,
                between_turn_delay_sec=0.0,
            ),
        )
        round_data = await orchestrator.build_round(
            RoomSnapshot(
                room_id="room_test",
                topic="Architecture review",
                goal="Extract orchestration from runtime.",
            ),
            round_index=1,
        )
        emitted: list[tuple[str, str]] = []

        async def publish(
            room_id: str,
            *,
            producer_id: str,
            role: str,
            event_type: str,
            payload: dict,
            reject_if_ended: bool = False,
        ) -> dict:
            emitted.append((role, event_type))
            return {
                "room_id": room_id,
                "producer_id": producer_id,
                "role": role,
                "event_type": event_type,
                "payload": payload,
                "reject_if_ended": reject_if_ended,
            }

        await orchestrator.emit_round(
            room_id="room_test",
            round_index=1,
            round_data=round_data,
            publish=publish,
        )

        self.assertEqual(
            emitted,
            [
                ("host", "message.chunk"),
                ("host", "message.commit"),
                ("host", "agent.message"),
                ("host", "agent.handoff"),
                ("implementation_specialist", "message.chunk"),
                ("implementation_specialist", "message.commit"),
                ("implementation_specialist", "agent.message"),
                ("risk_specialist", "agent.challenge"),
                ("risk_specialist", "message.chunk"),
                ("risk_specialist", "message.commit"),
                ("risk_specialist", "agent.message"),
                ("synthesis", "agent.summary"),
                ("system", "consensus.check"),
            ],
        )

    async def test_emit_round_includes_round_and_route_diagnostics_in_summary(self) -> None:
        orchestrator = RoomOrchestrator(
            StubRoomExecutor(),
            RoomOrchestratorConfig(
                message_chunk_delay_sec=0.0,
                between_turn_delay_sec=0.0,
            ),
        )
        round_data = await orchestrator.build_round(
            RoomSnapshot(
                room_id="room_test",
                topic="Architecture review",
                goal="Extract orchestration from runtime.",
            ),
            round_index=2,
        )
        published_payloads: list[dict] = []

        async def publish(
            room_id: str,
            *,
            producer_id: str,
            role: str,
            event_type: str,
            payload: dict,
            reject_if_ended: bool = False,
        ) -> dict:
            published_payloads.append(
                {
                    "role": role,
                    "event_type": event_type,
                    "payload": payload,
                }
            )
            return {
                "room_id": room_id,
                "producer_id": producer_id,
                "role": role,
                "event_type": event_type,
                "payload": payload,
                "reject_if_ended": reject_if_ended,
            }

        await orchestrator.emit_round(
            room_id="room_test",
            round_index=2,
            round_data=round_data,
            publish=publish,
        )

        summary_event = next(
            item
            for item in published_payloads
            if item["role"] == "synthesis" and item["event_type"] == "agent.summary"
        )
        self.assertEqual(summary_event["payload"]["round_index"], 2)
        self.assertEqual(summary_event["payload"]["phase"], "explore")
        self.assertEqual(
            summary_event["payload"]["artifacts"]["route"]["supplier"],
            "qwen",
        )
        consensus_event = next(
            item
            for item in published_payloads
            if item["role"] == "system" and item["event_type"] == "consensus.check"
        )
        self.assertFalse(consensus_event["payload"]["should_end"])
        self.assertFalse(consensus_event["payload"]["meeting_should_end"])
        self.assertEqual(consensus_event["payload"]["meeting_end_reason"], "")

    async def test_emit_round_rejects_missing_synthesis_message(self) -> None:
        orchestrator = RoomOrchestrator(
            StubRoomExecutor(),
            RoomOrchestratorConfig(
                message_chunk_delay_sec=0.0,
                between_turn_delay_sec=0.0,
            ),
        )
        round_data = RoomRound(
            phase=MeetingPhase.EXPLORE,
            signals=DecisionSignals(
                support=0.6,
                confidence=0.7,
                risk_penalty=0.1,
                margin_top1_top2=0.08,
                disagreement_index=0.4,
            ),
            plan_topic="Architecture review",
            next_focus="stabilize the event protocol",
            coordination=CoordinationAction(
                action_type=ActionType.HANDOFF,
                reason="handoff to evidence review",
                target_role="risk_specialist",
            ),
            consensus_score=0.74,
            consensus_should_end=False,
            should_end=False,
            consensus_reason="continue meeting",
            end_reason="",
            messages=[
                RoomMessage("host", "Host", "host focus", {"round_goal": "lock the contract"}),
                RoomMessage(
                    "implementation_specialist",
                    "Implementation Specialist",
                    "implementation claim",
                    {"claim": "claim.a", "target_claim_ref": ""},
                ),
            ],
            decision_candidate="Keep the room event protocol stable.",
            action_items=["Extract orchestrator."],
            open_questions=["Should round pacing be configurable here?"],
            summary_text="Synthesis summary",
            conclusion_type="follow_up_required",
            conclusion_reason="The room still needs follow-up work on pacing and control semantics.",
            synthesis_message=None,  # type: ignore[arg-type]
        )

        async def publish(*args, **kwargs):
            raise AssertionError("publish should not be called when synthesis is missing")

        with self.assertRaisesRegex(ValueError, "synthesis_message is required"):
            await orchestrator.emit_round(
                room_id="room_test",
                round_index=1,
                round_data=round_data,
                publish=publish,
            )

    async def test_emit_round_uses_dynamic_message_roles(self) -> None:
        orchestrator = RoomOrchestrator(
            DynamicRoleExecutor(),
            RoomOrchestratorConfig(
                message_chunk_delay_sec=0.0,
                between_turn_delay_sec=0.0,
            ),
        )
        round_data = await orchestrator.build_round(
            RoomSnapshot(
                room_id="room_test",
                topic="Architecture review",
                goal="Extract orchestration from runtime.",
            ),
            round_index=1,
        )
        emitted: list[tuple[str, str]] = []

        async def publish(
            room_id: str,
            *,
            producer_id: str,
            role: str,
            event_type: str,
            payload: dict,
            reject_if_ended: bool = False,
        ) -> dict:
            emitted.append((role, event_type))
            return {
                "room_id": room_id,
                "producer_id": producer_id,
                "role": role,
                "event_type": event_type,
                "payload": payload,
                "reject_if_ended": reject_if_ended,
            }

        await orchestrator.emit_round(
            room_id="room_test",
            round_index=1,
            round_data=round_data,
            publish=publish,
        )

        self.assertIn(("implementation_specialist", "agent.message"), emitted)
        self.assertIn(("risk_specialist", "agent.challenge"), emitted)
        self.assertIn(("risk_specialist", "agent.message"), emitted)
        self.assertIn(("synthesis", "agent.summary"), emitted)


if __name__ == "__main__":
    unittest.main()
