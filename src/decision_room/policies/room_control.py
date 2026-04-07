from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class RoomStateError(RuntimeError):
    pass


class RoomSnapshotView(Protocol):
    status: str
    ended_reason: str
    candidate_decision: str
    action_items: list[str]
    open_questions: list[str]
    conclusion_type: str
    conclusion_reason: str


@dataclass(frozen=True)
class RoomControlConfig:
    max_rounds: int = 4


class RoomControlPolicy:
    """Room-level control policy for termination and write admission.

    This policy owns runtime gating decisions. It does not generate meeting
    content and it does not own the room fact ledger.
    """

    def __init__(self, cfg: RoomControlConfig | None = None) -> None:
        self.cfg = cfg or RoomControlConfig()

    @property
    def max_rounds(self) -> int:
        return self.cfg.max_rounds

    def is_ended(self, snapshot: RoomSnapshotView) -> bool:
        return snapshot.status == "ended"

    def should_continue(self, snapshot: RoomSnapshotView) -> bool:
        return not self.is_ended(snapshot)

    def should_start_round(self, snapshot: RoomSnapshotView, *, round_index: int) -> bool:
        return self.should_continue(snapshot) and round_index <= self.cfg.max_rounds

    def should_publish_consensus_end(
        self, snapshot: RoomSnapshotView, *, consensus_should_end: bool
    ) -> bool:
        return self.should_publish_orchestration_end(
            snapshot,
            orchestration_should_end=consensus_should_end,
        )

    def should_publish_orchestration_end(
        self, snapshot: RoomSnapshotView, *, orchestration_should_end: bool
    ) -> bool:
        return self.should_continue(snapshot) and orchestration_should_end

    def should_publish_budget_end(self, snapshot: RoomSnapshotView) -> bool:
        return self.should_continue(snapshot)

    def should_publish_runtime_failure(self, snapshot: RoomSnapshotView) -> bool:
        return self.should_continue(snapshot)

    def assert_accepts_writes(self, snapshot: RoomSnapshotView) -> None:
        if not self.is_ended(snapshot):
            return
        reason = snapshot.ended_reason or "room already ended"
        raise RoomStateError(f"room no longer accepts writes: {reason}")

    def end_payload_from_consensus(
        self,
        snapshot: RoomSnapshotView,
        *,
        reason: str,
        decision_candidate: str,
        action_items: list[str],
        open_questions: list[str],
        conclusion_type: str,
        conclusion_reason: str,
    ) -> dict[str, Any]:
        return self.end_payload_from_orchestration(
            snapshot,
            orchestration_end_reason=reason,
            decision_candidate=decision_candidate,
            action_items=action_items,
            open_questions=open_questions,
            conclusion_type=conclusion_type,
            conclusion_reason=conclusion_reason,
        )

    def end_payload_from_orchestration(
        self,
        snapshot: RoomSnapshotView,
        *,
        orchestration_end_reason: str,
        decision_candidate: str,
        action_items: list[str],
        open_questions: list[str],
        conclusion_type: str,
        conclusion_reason: str,
    ) -> dict[str, Any]:
        return {
            "reason": conclusion_reason
            or snapshot.conclusion_reason
            or orchestration_end_reason
            or "meeting ended",
            "decision_candidate": decision_candidate or snapshot.candidate_decision,
            "action_items": action_items or snapshot.action_items,
            "open_questions": open_questions or snapshot.open_questions,
            "conclusion_type": conclusion_type or snapshot.conclusion_type,
            "conclusion_reason": conclusion_reason
            or snapshot.conclusion_reason
            or orchestration_end_reason,
            "control_reason": "control gate accepted orchestration end signal",
            "orchestration_end_reason": orchestration_end_reason,
        }

    def end_payload_from_budget(self, snapshot: RoomSnapshotView) -> dict[str, Any]:
        conclusion_type = snapshot.conclusion_type or "follow_up_required"
        conclusion_reason = snapshot.conclusion_reason or (
            "The meeting closed without enough evidence to mark the candidate decision ready."
        )
        return {
            "reason": conclusion_reason,
            "decision_candidate": snapshot.candidate_decision,
            "action_items": snapshot.action_items,
            "open_questions": snapshot.open_questions,
            "conclusion_type": conclusion_type,
            "conclusion_reason": conclusion_reason,
            "control_reason": "control gate closed the meeting after the configured round budget",
        }

    def end_payload_from_override(
        self, snapshot: RoomSnapshotView, *, text: str
    ) -> dict[str, Any]:
        return {
            "reason": f"human override: {text.strip()}",
            "decision_candidate": snapshot.candidate_decision,
            "action_items": snapshot.action_items,
            "open_questions": snapshot.open_questions,
            "conclusion_type": "human_override",
            "conclusion_reason": f"human override: {text.strip()}",
            "control_reason": "human override",
        }

    def end_payload_from_runtime_failure(self, exc: Exception) -> dict[str, Any]:
        reason = f"runtime failure: {exc}"
        return {
            "reason": reason,
            "conclusion_type": "runtime_failure",
            "conclusion_reason": reason,
            "control_reason": "runtime failure",
        }
