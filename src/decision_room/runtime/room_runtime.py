from __future__ import annotations

import asyncio
import copy
import uuid
from dataclasses import dataclass, field
from typing import Any

from decision_room.orchestration import (
    CentralizedMASExecutor,
    RequirementPlanningService,
    LLMRoomExecutor,
    PreRoomPlanningWorkflow,
    RoomStartContract,
    build_room_start_contract,
    planned_agent_profile_for_role,
    resolve_operator_context,
    RoomExecutor,
    RoomOrchestrator,
    RoomOrchestratorConfig,
)
from decision_room.policies.room_control import (
    RoomControlConfig,
    RoomControlPolicy,
    RoomStateError,
)

from .room_event_journal import RoomEventJournal
from .room_projector import RoomProjector


@dataclass
class RuntimeConfig:
    message_chunk_delay_sec: float = 0.12
    between_turn_delay_sec: float = 0.25
    between_round_delay_sec: float = 5.0
    max_rounds: int = 6


@dataclass
class RoomSession:
    room_id: str
    journal: RoomEventJournal
    projector: RoomProjector
    subscribers: set[asyncio.Queue[dict[str, Any]]] = field(default_factory=set)
    task: asyncio.Task[None] | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class RoomPreflightError(RuntimeError):
    def __init__(self, preflight_payload: dict[str, Any]) -> None:
        super().__init__("room preflight blocked normal room start")
        self.preflight_payload = preflight_payload

class RoomRuntime:
    def __init__(
        self,
        executor: RoomExecutor | None = None,
        config: RuntimeConfig | None = None,
        requirement_planner: RequirementPlanningService | None = None,
        planning_workflow: PreRoomPlanningWorkflow | None = None,
        control_policy: RoomControlPolicy | None = None,
        runtime_readiness: dict[str, Any] | None = None,
    ) -> None:
        self._config = config or RuntimeConfig()
        self._planning_workflow = planning_workflow or PreRoomPlanningWorkflow(
            requirement_planner or RequirementPlanningService.from_env()
        )
        self._control = control_policy or RoomControlPolicy(
            RoomControlConfig(max_rounds=self._config.max_rounds)
        )
        self._orchestrator = RoomOrchestrator(
            executor or CentralizedMASExecutor(),
            RoomOrchestratorConfig(
                message_chunk_delay_sec=self._config.message_chunk_delay_sec,
                between_turn_delay_sec=self._config.between_turn_delay_sec,
            ),
        )
        self._runtime_readiness = copy.deepcopy(runtime_readiness or {})
        self._sessions: dict[str, RoomSession] = {}
        self._lock = asyncio.Lock()

    async def create_room(
        self,
        requirement: str,
        mode: str = "agent_first",
        allow_planner_fallback: bool = False,
        require_preflight_ready: bool = False,
        entry_scope: str | None = None,
        operator_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        resolved_operator_context = resolve_operator_context(
            entry_scope=entry_scope,
            operator_context=operator_context,
        )
        plan, room_start_contract = self._build_room_start_contract(
            requirement,
            allow_planner_fallback=allow_planner_fallback,
            operator_context=resolved_operator_context,
        )
        if require_preflight_ready and not room_start_contract.room_start_ready:
            raise RoomPreflightError(
                self._preflight_payload(plan, room_start_contract, resolved_operator_context)
            )
        room_id = f"room_{uuid.uuid4().hex[:8]}"
        session = RoomSession(
            room_id=room_id,
            journal=RoomEventJournal(room_id),
            projector=RoomProjector(room_id),
        )
        async with self._lock:
            self._sessions[room_id] = session

        planning_payload = self._planning_payload(
            plan,
            room_start_contract,
            resolved_operator_context,
        )
        await self.publish(
            room_id,
            producer_id="system.planning",
            role="system",
            event_type="planning.completed",
            payload=planning_payload,
        )
        await self.publish(
            room_id,
            producer_id="system.runtime",
            role="system",
            event_type="room.started",
            payload={
                "requirement": plan.requirement,
                "topic": plan.topic,
                "goal": plan.meeting_objective,
                "constraints": plan.constraints,
                "open_questions": room_start_contract.contextual_open_questions,
                "mode": mode,
                "phase": "explore",
                "status": "running",
                "current_focus": plan.initial_focus,
                "brief_source": plan.brief_source,
                "brief_source_reason": plan.brief_source_reason,
                "resume_token": room_id,
            },
        )

        for participant in plan.active_agents:
            await self.publish(
                room_id,
                producer_id=participant.participant_id,
                role=participant.role,
                event_type="agent.joined",
                payload={
                    "participant_id": participant.participant_id,
                    "identity": participant.identity,
                    "display_name": participant.display_name,
                    "activation": participant.activation,
                    "speaking": participant.speaking,
                    "capability_profile": participant.capability_profile,
                    "join_reason": participant.join_reason,
                    "focus_areas": participant.focus_areas,
                },
            )

        session.task = asyncio.create_task(self._drive_room(room_id))
        return self.get_snapshot(room_id)

    def preflight_room(
        self,
        requirement: str,
        *,
        allow_planner_fallback: bool = False,
        entry_scope: str | None = None,
        operator_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        resolved_operator_context = resolve_operator_context(
            entry_scope=entry_scope,
            operator_context=operator_context,
        )
        plan, room_start_contract = self._build_room_start_contract(
            requirement,
            allow_planner_fallback=allow_planner_fallback,
            operator_context=resolved_operator_context,
        )
        return self._preflight_payload(plan, room_start_contract, resolved_operator_context)

    def list_rooms(self) -> list[dict[str, Any]]:
        rooms = [session.projector.snapshot.room_summary() for session in self._sessions.values()]
        return sorted(rooms, key=lambda item: item["updated_at_ms"], reverse=True)

    def get_snapshot(self, room_id: str) -> dict[str, Any]:
        return copy.deepcopy(self._require_session(room_id).projector.snapshot.public_dict())

    def replay(self, room_id: str, after_seq: int = 0) -> list[dict[str, Any]]:
        session = self._require_session(room_id)
        return [event.to_dict() for event in session.journal.replay(after_seq)]

    def runtime_readiness(self) -> dict[str, Any]:
        return copy.deepcopy(self._runtime_readiness)

    def _build_room_start_contract(
        self,
        requirement: str,
        *,
        allow_planner_fallback: bool,
        operator_context: dict[str, Any] | None,
    ) -> tuple[Any, RoomStartContract]:
        plan = self._planning_workflow.plan_room(
            requirement,
            allow_fallback=allow_planner_fallback,
        )
        room_start_contract = build_room_start_contract(
            operator_required_inputs=plan.room_start_contract_draft.operator_required_inputs,
            contextual_open_questions=plan.room_start_contract_draft.contextual_open_questions,
            runtime_readiness=self._runtime_readiness,
            operator_context=operator_context,
            allow_planner_fallback=allow_planner_fallback,
        )
        return plan, room_start_contract

    def _planning_payload(
        self,
        plan: Any,
        room_start_contract: RoomStartContract,
        operator_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = plan.planning_payload()
        payload["room_start_contract"] = room_start_contract.to_payload()
        payload["runtime_context"] = _planning_runtime_context(self._runtime_readiness)
        if operator_context:
            payload["operator_context"] = copy.deepcopy(operator_context)
        return payload

    def _preflight_payload(
        self,
        plan: Any,
        room_start_contract: RoomStartContract,
        operator_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            **self._planning_payload(plan, room_start_contract, operator_context),
            "runtime_readiness": self.runtime_readiness(),
        }

    async def register_subscriber(self, room_id: str) -> asyncio.Queue[dict[str, Any]]:
        session = self._require_session(room_id)
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=256)
        async with session.lock:
            session.subscribers.add(queue)
        return queue

    async def unregister_subscriber(
        self, room_id: str, queue: asyncio.Queue[dict[str, Any]]
    ) -> None:
        async with self._lock:
            session = self._sessions.get(room_id)
        if session is None:
            return
        async with session.lock:
            session.subscribers.discard(queue)

    async def publish(
        self,
        room_id: str,
        *,
        producer_id: str,
        role: str,
        event_type: str,
        payload: dict[str, Any],
        reject_if_ended: bool = False,
    ) -> dict[str, Any]:
        session = self._require_session(room_id)
        async with session.lock:
            serialized, subscribers = self._append_event(
                session,
                producer_id=producer_id,
                role=role,
                event_type=event_type,
                payload=payload,
                reject_if_ended=reject_if_ended,
            )
        for queue in subscribers:
            self._offer(queue, serialized)
        return serialized

    async def post_human_message(self, room_id: str, text: str) -> dict[str, Any]:
        message_id = f"msg_{uuid.uuid4().hex[:8]}"
        await self.publish(
            room_id,
            producer_id="human.user.1",
            role="human",
            event_type="human.message",
            payload={
                "message_id": message_id,
                "text": text.strip(),
                "title": "Human intervention",
                "artifacts": {"source": "operator"},
            },
            reject_if_ended=True,
        )
        return self.get_snapshot(room_id)

    async def post_human_override(self, room_id: str, text: str) -> dict[str, Any]:
        session = self._require_session(room_id)
        message_id = f"msg_{uuid.uuid4().hex[:8]}"
        async with session.lock:
            override_event, override_subscribers = self._append_event(
                session,
                producer_id="human.user.1",
                role="human",
                event_type="human.override",
                payload={
                    "message_id": message_id,
                    "text": text.strip(),
                    "title": "Human override",
                    "force_end": True,
                },
                reject_if_ended=True,
            )
            snapshot = session.projector.snapshot
            ended_event, ended_subscribers = self._append_event(
                session,
                producer_id="system.runtime",
                role="system",
                event_type="meeting.ended",
                payload=self._control.end_payload_from_override(snapshot, text=text),
            )
        for queue in override_subscribers:
            self._offer(queue, override_event)
        for queue in ended_subscribers:
            self._offer(queue, ended_event)
        await self._cancel_room_task(room_id)
        return self.get_snapshot(room_id)

    async def close(self) -> None:
        tasks: list[asyncio.Task[None]] = []
        async with self._lock:
            for session in self._sessions.values():
                if session.task is not None and not session.task.done():
                    tasks.append(session.task)
            self._sessions.clear()

        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _drive_room(self, room_id: str) -> None:
        try:
            for round_index in range(1, self._control.max_rounds + 1):
                snapshot = self._require_session(room_id).projector.snapshot
                if not self._control.should_start_round(snapshot, round_index=round_index):
                    return
                if round_index > 1:
                    await asyncio.sleep(self._config.between_round_delay_sec)

                round_data = await self._orchestrator.build_round(snapshot, round_index)
                await self._ensure_round_participants_joined(
                    room_id=room_id,
                    round_data=round_data,
                )
                await self._orchestrator.emit_round(
                    room_id=room_id,
                    round_index=round_index,
                    round_data=round_data,
                    publish=self.publish,
                )

                snapshot = self._require_session(room_id).projector.snapshot
                if not self._control.should_continue(snapshot):
                    return
                if self._control.should_publish_orchestration_end(
                    snapshot,
                    orchestration_should_end=round_data.should_end,
                ):
                    await self.publish(
                        room_id,
                        producer_id="system.runtime",
                        role="system",
                        event_type="meeting.ended",
                        payload=self._control.end_payload_from_orchestration(
                            snapshot,
                            orchestration_end_reason=round_data.end_reason
                            or round_data.consensus_reason
                            or "orchestration signaled meeting completion",
                            decision_candidate=round_data.decision_candidate,
                            action_items=round_data.action_items,
                            open_questions=round_data.open_questions,
                            conclusion_type=round_data.conclusion_type,
                            conclusion_reason=round_data.conclusion_reason,
                        ),
                    )
                    return

            snapshot = self._require_session(room_id).projector.snapshot
            if not self._control.should_publish_budget_end(snapshot):
                return
            await self.publish(
                room_id,
                producer_id="system.runtime",
                role="system",
                event_type="meeting.ended",
                payload=self._control.end_payload_from_budget(snapshot),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            snapshot = self._require_session(room_id).projector.snapshot
            if not self._control.should_publish_runtime_failure(snapshot):
                return
            await self.publish(
                room_id,
                producer_id="system.runtime",
                role="system",
                event_type="meeting.ended",
                payload=self._control.end_payload_from_runtime_failure(exc),
            )

    async def _ensure_round_participants_joined(self, *, room_id: str, round_data: Any) -> None:
        snapshot = self._require_session(room_id).projector.snapshot
        joined_roles = {participant.role for participant in snapshot.participants}
        for message in round_data.messages:
            role = str(message.role).strip().lower()
            if not role or role in {"host", "system"} or role in joined_roles:
                continue
            profile = planned_agent_profile_for_role(snapshot, role)
            payload = {
                "participant_id": profile.participant_id if profile else f"agent.{role}.1",
                "identity": profile.identity if profile else "agent",
                "display_name": profile.display_name if profile else role.replace("_", " ").title(),
                "activation": profile.activation if profile else "on_demand",
                "speaking": profile.speaking if profile else True,
                "capability_profile": (
                    profile.capability_profile
                    if profile
                    else str(message.artifacts.get("capability_profile", ""))
                ),
                "join_reason": (
                    profile.join_reason
                    if profile
                    else f"activated dynamically for round {self._require_session(room_id).projector.snapshot.round_index + 1}"
                ),
                "focus_areas": profile.focus_areas if profile else [],
            }
            await self.publish(
                room_id,
                producer_id=payload["participant_id"],
                role=role,
                event_type="agent.joined",
                payload=payload,
            )
            joined_roles.add(role)

    async def _cancel_room_task(self, room_id: str) -> None:
        session = self._require_session(room_id)
        task = session.task
        if task is None or task.done():
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    def _append_event(
        self,
        session: RoomSession,
        *,
        producer_id: str,
        role: str,
        event_type: str,
        payload: dict[str, Any],
        reject_if_ended: bool = False,
    ) -> tuple[dict[str, Any], list[asyncio.Queue[dict[str, Any]]]]:
        snapshot = session.projector.snapshot
        if reject_if_ended:
            self._control.assert_accepts_writes(snapshot)

        event = session.journal.append(
            producer_id=producer_id,
            role=role,
            event_type=event_type,
            payload=payload,
        )
        session.projector.apply(event)
        return event.to_dict(), list(session.subscribers)

    def _require_session(self, room_id: str) -> RoomSession:
        try:
            return self._sessions[room_id]
        except KeyError as exc:
            raise KeyError(f"unknown room: {room_id}") from exc

    def _offer(self, queue: asyncio.Queue[dict[str, Any]], event: dict[str, Any]) -> None:
        try:
            queue.put_nowait(copy.deepcopy(event))
        except asyncio.QueueFull:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            queue.put_nowait(copy.deepcopy(event))


def _planning_runtime_context(runtime_readiness: dict[str, Any]) -> dict[str, Any]:
    context: dict[str, Any] = {}
    for key in ("planner_mode", "executor_mode"):
        value = str(runtime_readiness.get(key, "")).strip()
        if value:
            context[key] = value

    planner_target = _normalize_target_identity(runtime_readiness.get("planner_target"))
    if planner_target:
        context["planner_target"] = planner_target

    executor_targets_raw = runtime_readiness.get("executor_targets")
    if isinstance(executor_targets_raw, dict):
        executor_targets: dict[str, dict[str, str]] = {}
        for key, value in executor_targets_raw.items():
            normalized = _normalize_target_identity(value)
            if normalized:
                executor_targets[str(key)] = normalized
        if executor_targets:
            context["executor_targets"] = executor_targets

    executor_guardrails = runtime_readiness.get("executor_guardrails")
    if isinstance(executor_guardrails, dict):
        context["executor_guardrails"] = copy.deepcopy(executor_guardrails)
    return context


def _normalize_target_identity(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    normalized: dict[str, str] = {}
    for key in ("supplier", "model"):
        field_value = str(value.get(key, "")).strip()
        if field_value:
            normalized[key] = field_value
    return normalized
