from __future__ import annotations

from typing import Any

from decision_room.mas.hybrid import HybridCoordinationStrategy
from decision_room.mas.types import ActionType, CoordinationAction

from .central_mas import CentralizedMeetingSupervisor
from .room_executor import RoomMessage, RoomRound


class CentralizedMASExecutor:
    """Local online MAS executor with a single supervisor and typed role nodes."""

    def __init__(self, supervisor: CentralizedMeetingSupervisor | None = None) -> None:
        self._supervisor = supervisor or CentralizedMeetingSupervisor()
        self._coordination = HybridCoordinationStrategy()

    async def build_round(self, snapshot: Any, round_index: int) -> RoomRound:
        round_model = self._supervisor.build_round(snapshot, round_index)
        host_message = RoomMessage(
            role="host",
            title="Supervisor dispatch",
            text=self._host_text(round_model),
            artifacts={
                "central_mas": {
                    "topology": "single_supervisor_shared_memory",
                    "supervisor_state": round_model.supervisor_state.to_payload(),
                    "role_catalog": [
                        item.to_payload() for item in round_model.role_catalog
                    ],
                    "assignment_contracts": [
                        item.to_payload()
                        for item in round_model.supervisor_state.assignment_contracts
                    ],
                },
                "next_focus": round_model.next_focus,
                "round_goal": "collect specialist evidence and preserve one owner for task routing",
                "target_roles": [
                    product.role.role for product in round_model.work_products
                ],
                "turns": [
                    {
                        "role": contract.agent,
                        "task": contract.mission,
                    }
                    for contract in round_model.supervisor_state.assignment_contracts
                    if contract.run
                ],
            },
        )
        specialist_messages = [
            RoomMessage(
                role=product.role.role,
                title=product.title,
                text=product.text,
                artifacts={
                    "claim": product.claim,
                    "evidence": list(product.evidence),
                    "confidence": product.confidence,
                    "target_claim_ref": product.target_claim_ref,
                    "turn_task": round_model.supervisor_state.assignment_contracts[
                        index
                    ].mission,
                    "agent_role_contract": product.role.to_payload(),
                },
            )
            for index, product in enumerate(round_model.work_products)
        ]
        synthesis_message = RoomMessage(
            role="synthesis",
            title="Decision synthesis",
            text=self._synthesis_text(round_model),
            artifacts={
                "agreement": list(round_model.agreement),
                "disagreement": list(round_model.disagreement),
                "decision_candidate": round_model.decision_candidate,
                "action_item_draft": list(round_model.action_items),
                "central_mas_state_ref": "host.artifacts.central_mas.supervisor_state",
            },
        )
        coordination = self._coordination.next_action(
            self._coordination_context(snapshot, round_model)
        )
        if specialist_messages:
            coordination = CoordinationAction(
                action_type=ActionType.HANDOFF,
                target_role=specialist_messages[0].role,
                reason="central supervisor assigned the first specialist turn from shared memory",
            )
        return RoomRound(
            phase=round_model.phase,
            signals=round_model.signals,
            plan_topic=getattr(snapshot, "topic", "") or "centralized MAS decision room",
            next_focus=round_model.next_focus,
            coordination=coordination,
            consensus_score=round_model.signals.support,
            consensus_should_end=round_model.should_end,
            should_end=round_model.should_end,
            consensus_reason=(
                "central supervisor reached a working decision threshold"
                if round_model.should_end
                else "central supervisor needs another evidence pass"
            ),
            end_reason=round_model.conclusion_reason if round_model.should_end else "",
            messages=[host_message, *specialist_messages],
            decision_candidate=round_model.decision_candidate,
            action_items=round_model.action_items,
            open_questions=round_model.open_questions,
            summary_text=self._synthesis_text(round_model),
            conclusion_type=round_model.conclusion_type,
            conclusion_reason=round_model.conclusion_reason,
            synthesis_message=synthesis_message,
        )

    def _host_text(self, round_model: Any) -> str:
        assignments = round_model.supervisor_state.assignment_contracts
        assignment_text = " ".join(
            f"{item.agent}: {item.deliverable}" for item in assignments if item.run
        )
        return (
            f"Central supervisor round {round_model.supervisor_state.round_index} "
            f"is in {round_model.phase.value}. Focus: {round_model.next_focus}. "
            f"I am assigning typed role nodes from shared room memory. {assignment_text}"
        )

    def _synthesis_text(self, round_model: Any) -> str:
        return (
            f"Decision candidate: {round_model.decision_candidate} "
            f"Agreement: {' '.join(round_model.agreement)} "
            f"Remaining disagreement: {' '.join(round_model.disagreement)}"
        )

    def _coordination_context(self, snapshot: Any, round_model: Any) -> Any:
        from decision_room.mas.types import DecisionContext

        return DecisionContext(
            room_id=getattr(snapshot, "room_id", ""),
            phase=round_model.phase,
            signals=round_model.signals,
            metadata={"topology": "centralized_supervisor"},
        )
