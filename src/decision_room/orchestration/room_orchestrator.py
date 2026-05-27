from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from decision_room.mas.types import ActionType

if TYPE_CHECKING:
    from .room_executor import RoomExecutor, RoomMessage, RoomRound
    from decision_room.runtime.room_models import RoomSnapshot

PublishCallable = Callable[..., Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class RoomOrchestratorConfig:
    message_chunk_delay_sec: float = 0.10
    between_turn_delay_sec: float = 0.18


class RoomOrchestrator:
    """Owns room-level collaboration semantics and round emission order."""

    def __init__(
        self,
        executor: RoomExecutor,
        config: RoomOrchestratorConfig | None = None,
    ) -> None:
        self._executor = executor
        self._config = config or RoomOrchestratorConfig()

    async def build_round(
        self,
        snapshot: RoomSnapshot,
        round_index: int,
        *,
        publish: PublishCallable | None = None,
    ) -> RoomRound:
        executor = self._executor
        build = executor.build_round
        # Executors that opt into journal-anchored memory accept ``publish``;
        # legacy executors are kept compatible via TypeError fallback.
        try:
            return await build(snapshot, round_index, publish=publish)
        except TypeError:
            return await build(snapshot, round_index)

    async def emit_round(
        self,
        *,
        room_id: str,
        round_index: int,
        round_data: RoomRound,
        publish: PublishCallable,
    ) -> None:
        host_message, specialist_messages, synthesis_message = self._classify_round_messages(
            round_data
        )
        await self._stream_agent_message(
            room_id=room_id,
            message=host_message,
            round_index=round_index,
            phase=round_data.phase.value,
            current_focus=round_data.next_focus,
            publish=publish,
        )

        if round_data.coordination.action_type == ActionType.HANDOFF:
            await publish(
                room_id,
                producer_id="agent.host.1",
                role="host",
                event_type="agent.handoff",
                payload={
                    "to_role": round_data.coordination.target_role
                    or (specialist_messages[0].role if specialist_messages else "synthesis"),
                    "reason": round_data.coordination.reason,
                    "expected_output": str(
                        host_message.artifacts.get(
                            "round_goal",
                            "challenge the strongest claim and surface risk",
                        )
                    ),
                    "context_slice": round_data.next_focus,
                    "artifact_refs": ["host.next_focus", "host.focus_points"],
                },
            )
            await asyncio.sleep(self._config.between_turn_delay_sec)

        previous_message: RoomMessage | None = None
        for message in specialist_messages:
            target_claim_ref = str(message.artifacts.get("target_claim_ref", "")).strip()
            if previous_message is not None and target_claim_ref:
                await publish(
                    room_id,
                    producer_id=f"agent.{message.role}.1",
                    role=message.role,
                    event_type="agent.challenge",
                    payload={
                        "target_role": previous_message.role,
                        "reason": "stress-test the strongest optimistic claim before convergence",
                        "claim_ref": target_claim_ref,
                    },
                )
                await asyncio.sleep(self._config.between_turn_delay_sec)

            await self._stream_agent_message(
                room_id=room_id,
                message=message,
                round_index=round_index,
                phase=round_data.phase.value,
                current_focus=round_data.next_focus,
                publish=publish,
            )
            await asyncio.sleep(self._config.between_turn_delay_sec)
            previous_message = message

        synthesis_artifacts = (
            synthesis_message.artifacts
            if isinstance(synthesis_message.artifacts, dict)
            else {}
        )
        await publish(
            room_id,
            producer_id="capability.synthesis.1",
            role="synthesis",
            event_type="agent.summary",
            payload={
                "summary": round_data.summary_text,
                "decision_candidate": round_data.decision_candidate,
                "action_items": round_data.action_items,
                "open_questions": round_data.open_questions,
                "conclusion_type": round_data.conclusion_type,
                "conclusion_reason": round_data.conclusion_reason,
                "round_index": round_index,
                "phase": round_data.phase.value,
                "recommended_next_phase": str(
                    synthesis_artifacts.get("recommended_next_phase", "") or ""
                ).strip().lower(),
                "recommended_next_action": str(
                    synthesis_artifacts.get("recommended_next_action", "") or ""
                ).strip().lower(),
                "artifacts": synthesis_artifacts,
            },
        )
        await publish(
            room_id,
            producer_id="system.consensus.1",
            role="system",
            event_type="consensus.check",
            payload={
                "score": round_data.consensus_score,
                "should_end": round_data.consensus_should_end,
                "meeting_should_end": round_data.should_end,
                "meeting_end_reason": round_data.end_reason,
                "reason": round_data.consensus_reason,
                "support": round_data.signals.support,
                "confidence": round_data.signals.confidence,
                "disagreement_index": round_data.signals.disagreement_index,
                "margin_top1_top2": round_data.signals.margin_top1_top2,
            },
        )

    def _classify_round_messages(
        self,
        round_data: RoomRound,
    ) -> tuple[RoomMessage, list[RoomMessage], RoomMessage]:
        if not round_data.messages:
            raise ValueError("round_data.messages must contain at least the host message")
        if round_data.synthesis_message is None:
            raise ValueError("round_data.synthesis_message is required for host-led topology")
        if round_data.synthesis_message.role != "synthesis":
            raise ValueError("round_data.synthesis_message.role must be 'synthesis'")

        host_message = round_data.messages[0]
        specialist_messages = round_data.messages[1:]
        return host_message, specialist_messages, round_data.synthesis_message

    async def _stream_agent_message(
        self,
        *,
        room_id: str,
        message: RoomMessage,
        round_index: int,
        phase: str,
        current_focus: str,
        publish: PublishCallable,
    ) -> None:
        message_id = f"msg_{uuid.uuid4().hex[:8]}"
        producer_id = f"agent.{message.role}.1"
        for chunk in self._split_chunks(message.text):
            await publish(
                room_id,
                producer_id=producer_id,
                role=message.role,
                event_type="message.chunk",
                payload={
                    "message_id": message_id,
                    "text_chunk": chunk,
                    "title": message.title,
                },
            )
            await asyncio.sleep(self._config.message_chunk_delay_sec)

        await publish(
            room_id,
            producer_id=producer_id,
            role=message.role,
            event_type="message.commit",
            payload={"message_id": message_id, "text": message.text, "title": message.title},
        )
        await publish(
            room_id,
            producer_id=producer_id,
            role=message.role,
            event_type="agent.message",
            payload={
                "message_id": message_id,
                "title": message.title,
                "text": message.text,
                "round_index": round_index,
                "phase": phase,
                "current_focus": current_focus,
                "artifacts": message.artifacts,
            },
        )

    def _split_chunks(self, text: str) -> list[str]:
        words = text.split()
        if len(words) <= 6:
            return [text]

        chunk_size = max(4, len(words) // 3)
        return [" ".join(words[idx : idx + chunk_size]) for idx in range(0, len(words), chunk_size)]
