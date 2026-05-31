"""Speaker selection strategies for ``RoundOrchestrator``.

Before Phase 4 the round loop was duplicated across two executors —
``LLMRoomExecutor.build_round`` (host-led) and
``CentralizedMASExecutor.build_round`` (supervisor-led) — and the only
real difference between them was who decided which specialists speak in
what order. Phase 4 lifts that decision into a ``SpeakerSelectionStrategy``
that the single ``RoundOrchestrator`` consults.

Two concrete strategies ship:

- ``HostLedSpeakerStrategy`` — runs a ``HostAgent`` to produce an
  agenda, then maps the agenda's turns to candidate specialists.
- ``SupervisorLedSpeakerStrategy`` — runs a ``SupervisorAgent`` (or a
  pre-injected ``LLMSupervisor`` for back-compat), then maps the
  supervisor's speaker slots to candidate specialists.

Each strategy also owns the format of the "host" room message it
emits (host-led uses an English round-focus paragraph; supervisor-led
uses a Chinese 主持人 paragraph) and any extras it wants threaded into
the synthesis message (centralized adds a ``central_mas_state_ref``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from decision_room.agents import HostAgent, SupervisorAgent, TurnContext
from decision_room.mas.types import MeetingPhase, RoutingDecision

from .central_mas import (
    SpeakerSlot,
    build_supervisor_state,
    central_mas_artifact_bundle,
    supervisor_plan_to_host_agenda,
)
from .pre_room_planning import (
    CandidateSpecialist,
    resolve_turn_specialists,
)
from .real_run_contract import HostAgenda


@dataclass(frozen=True)
class SpecialistAssignment:
    """One specialist scheduled to speak this round.

    ``task`` is the optional ``focus_angle`` hint emitted by the
    speaker-selection layer; the specialist still authors its own claim.
    ``speaker_slot`` carries supervisor-led metadata when present (the
    host-led strategy leaves it None).
    """

    specialist: CandidateSpecialist
    task: str
    speaker_slot: SpeakerSlot | None = None


@dataclass(frozen=True)
class SpeakerPlan:
    """Result of ``SpeakerSelectionStrategy.plan_speakers``.

    Carries the "host" room message (the selection-layer narrative), the
    ``HostAgenda`` shape that the synthesis prompt builder consumes, the
    ordered specialist assignments, and any extras the strategy wants
    appended to the synthesis message's artifacts. ``route_ctx`` and
    ``route`` reflect any routing updates from the strategy's LLM call.
    """

    host_message: Any  # RoomMessage — typed Any to avoid an import cycle
    host_agenda: HostAgenda
    assignments: list[SpecialistAssignment]
    route_ctx: Any  # DecisionContext
    route: RoutingDecision
    extra_synthesis_artifacts: dict[str, Any] = field(default_factory=dict)


class SpeakerSelectionStrategy(Protocol):
    """Protocol every speaker-selection strategy satisfies.

    ``name`` is used for logging + readiness reporting. ``topic_default``
    fills ``DecisionContext.metadata['topic']`` when the snapshot has no
    explicit topic. ``metadata_overrides`` lets a topology mark itself
    (e.g., supervisor-led adds ``topology: centralized_supervisor``).
    """

    @property
    def name(self) -> str:
        ...

    @property
    def topic_default(self) -> str:
        ...

    @property
    def metadata_overrides(self) -> dict[str, str]:
        ...

    async def plan_speakers(
        self,
        *,
        snapshot: Any,
        round_index: int,
        phase: MeetingPhase,
        next_focus: str,
        route_ctx: Any,
        route: RoutingDecision,
        publish: Any | None,
    ) -> SpeakerPlan:
        ...


class HostLedSpeakerStrategy:
    """Host-led: the ``HostAgent`` builds the agenda + speaker order."""

    name = "host_led"
    topic_default = "multi-agent meeting room"
    metadata_overrides: dict[str, str] = {}

    def __init__(self, host_agent: HostAgent) -> None:
        self._host_agent = host_agent

    async def plan_speakers(
        self,
        *,
        snapshot: Any,
        round_index: int,
        phase: MeetingPhase,
        next_focus: str,
        route_ctx: Any,
        route: RoutingDecision,
        publish: Any | None,
    ) -> SpeakerPlan:
        from decision_room.orchestration.room_executor import (
            RoomMessage,
            _route_artifact,
        )

        host_ctx = TurnContext(
            snapshot=snapshot,
            round_index=round_index,
            phase=phase,
            next_focus=next_focus,
            route_ctx=route_ctx,
            route=route,
            publish=publish,
        )
        host_result = await self._host_agent.run(host_ctx)
        host_agenda = host_result.agenda

        assignments: list[SpecialistAssignment] = []
        for specialist, turn in zip(
            resolve_turn_specialists(
                snapshot,
                [turn.role for turn in host_agenda.turns],
            ),
            host_agenda.turns,
        ):
            if specialist.role == turn.role:
                assignments.append(
                    SpecialistAssignment(specialist=specialist, task=turn.task)
                )
        if not assignments:
            raise ValueError(
                "host agenda did not resolve to any valid specialist turns"
            )

        host_message = _build_host_message(
            snapshot=snapshot,
            phase=phase,
            round_index=round_index,
            next_focus=next_focus,
            route=host_result.route,
            host_agenda=host_agenda,
            target_roles=[a.specialist.role for a in assignments],
            room_message_cls=RoomMessage,
            route_artifact_fn=_route_artifact,
        )
        return SpeakerPlan(
            host_message=host_message,
            host_agenda=host_agenda,
            assignments=assignments,
            route_ctx=host_result.ctx,
            route=host_result.route,
            extra_synthesis_artifacts={},
        )


class SupervisorLedSpeakerStrategy:
    """Supervisor-led: the ``SupervisorAgent`` picks speakers + focus angle."""

    name = "supervisor_led"
    topic_default = "centralized MAS decision room"
    metadata_overrides: dict[str, str] = {"topology": "centralized_supervisor"}

    def __init__(
        self,
        supervisor_agent: SupervisorAgent,
        *,
        legacy_supervisor: Any | None = None,
    ) -> None:
        self._supervisor_agent = supervisor_agent
        # ``legacy_supervisor`` preserves the back-compat injection point:
        # callers that constructed a custom LLMSupervisor (pre-Phase-3
        # path) keep working. Persistence still flows through the agent.
        self._legacy_supervisor = legacy_supervisor

    async def plan_speakers(
        self,
        *,
        snapshot: Any,
        round_index: int,
        phase: MeetingPhase,
        next_focus: str,
        route_ctx: Any,
        route: RoutingDecision,
        publish: Any | None,
    ) -> SpeakerPlan:
        from decision_room.orchestration.room_executor import (
            RoomMessage,
            _route_artifact,
        )

        if self._legacy_supervisor is not None:
            supervisor_plan, new_ctx, new_route = await self._legacy_supervisor.plan_round(
                snapshot=snapshot,
                round_index=round_index,
                phase=phase,
                next_focus=next_focus,
                route_ctx=route_ctx,
                route=route,
            )
            await self._supervisor_agent._persist_plan(  # noqa: SLF001
                publish=publish,
                room_id=snapshot.room_id,
                round_index=round_index,
                plan=supervisor_plan,
            )
        else:
            supervisor_ctx = TurnContext(
                snapshot=snapshot,
                round_index=round_index,
                phase=phase,
                next_focus=next_focus,
                route_ctx=route_ctx,
                route=route,
                publish=publish,
            )
            supervisor_result = await self._supervisor_agent.run(supervisor_ctx)
            supervisor_plan = supervisor_result.plan
            new_ctx = supervisor_result.ctx
            new_route = supervisor_result.route

        host_agenda = supervisor_plan_to_host_agenda(supervisor_plan)
        runnable = supervisor_plan.runnable_speakers()
        if not runnable:
            raise RuntimeError("supervisor returned no runnable speaker slots")
        specialist_pairs = _resolve_speaker_specialists(snapshot, runnable)
        if not specialist_pairs:
            raise RuntimeError(
                "supervisor speakers did not resolve to any planned specialist"
            )

        assignments = [
            SpecialistAssignment(
                specialist=specialist,
                task=slot.focus_angle,
                speaker_slot=slot,
            )
            for specialist, slot in specialist_pairs
        ]

        host_message = _build_supervisor_message(
            snapshot=snapshot,
            phase=phase,
            round_index=round_index,
            next_focus=next_focus,
            route=new_route,
            plan=supervisor_plan,
            target_roles=[a.specialist.role for a in assignments],
            room_message_cls=RoomMessage,
            route_artifact_fn=_route_artifact,
        )
        return SpeakerPlan(
            host_message=host_message,
            host_agenda=host_agenda,
            assignments=assignments,
            route_ctx=new_ctx,
            route=new_route,
            extra_synthesis_artifacts={
                "central_mas_state_ref": "host.artifacts.central_mas.supervisor_state",
            },
        )


# -- Message builders ---------------------------------------------------------


def _build_host_message(
    *,
    snapshot: Any,
    phase: MeetingPhase,
    round_index: int,
    next_focus: str,
    route: RoutingDecision,
    host_agenda: HostAgenda,
    target_roles: list[str],
    room_message_cls: Any,
    route_artifact_fn: Any,
) -> Any:
    """Format the host-led "host" RoomMessage. Lifted from the pre-Phase-4
    private ``LLMRoomExecutor._build_host_message`` so the strategy owns
    the narrative shape.
    """
    focus_lines = [
        f"{idx}. {point.title}: {point.reason}"
        for idx, point in enumerate(host_agenda.focus_points, start=1)
    ]
    text = (
        f"Round {round_index} enters {phase.value}. "
        f"Meeting focus: {next_focus}\n"
        + "\n".join(focus_lines)
    )
    if snapshot.last_human_message:
        text += f"\nHuman input to incorporate: {snapshot.last_human_message}"
    if host_agenda.open_questions:
        text += "\nOpen questions: " + "; ".join(host_agenda.open_questions)
    return room_message_cls(
        role="host",
        title="Round focus",
        text=text,
        artifacts={
            "next_focus": next_focus,
            "round_goal": snapshot.goal,
            "target_roles": target_roles,
            "focus_points": [
                {
                    "title": point.title,
                    "reason": point.reason,
                    "constraint_ids": point.constraint_ids,
                }
                for point in host_agenda.focus_points
            ],
            "turns": [
                {"role": turn.role, "task": turn.task}
                for turn in host_agenda.turns
            ],
            "open_questions": host_agenda.open_questions,
            "route": route_artifact_fn(route),
        },
    )


def _build_supervisor_message(
    *,
    snapshot: Any,
    phase: MeetingPhase,
    round_index: int,
    next_focus: str,
    route: RoutingDecision,
    plan: Any,  # SupervisorPlan
    target_roles: list[str],
    room_message_cls: Any,
    route_artifact_fn: Any,
) -> Any:
    """Format the supervisor-led "host" RoomMessage. Lifted from the
    pre-Phase-4 ``CentralizedMASExecutor._build_supervisor_message``.
    """
    supervisor_state = build_supervisor_state(
        snapshot=snapshot,
        round_index=round_index,
        plan=plan,
    )
    speaker_lines = [
        f"{slot.agent}" + (f" — {slot.focus_angle}" if slot.focus_angle else "")
        for slot in plan.runnable_speakers()
    ]
    text = (
        f"主持人本轮 {round_index} 阶段 ({phase.value})。 "
        f"决策焦点：{plan.decision_focus or next_focus}。 "
        f"安排理由：{plan.reason}。 "
        f"发言顺序：{'; '.join(speaker_lines)}"
    )
    if snapshot.last_human_message:
        text += f"\n来自人类的输入：{snapshot.last_human_message}"
    return room_message_cls(
        role="host",
        title="主持人调度",
        text=text,
        artifacts={
            "next_focus": next_focus,
            "round_goal": snapshot.goal,
            "target_roles": target_roles,
            "focus_points": [
                {
                    "title": plan.decision_focus or next_focus,
                    "reason": plan.reason,
                    "constraint_ids": [],
                }
            ],
            "speakers": [
                {"role": slot.agent, "focus_angle": slot.focus_angle}
                for slot in plan.runnable_speakers()
            ],
            "turns": [
                {"role": slot.agent, "task": slot.focus_angle}
                for slot in plan.runnable_speakers()
            ],
            "open_questions": list(plan.open_questions),
            "route": route_artifact_fn(route),
            "central_mas": central_mas_artifact_bundle(
                state=supervisor_state,
                plan=plan,
                route=route,
            ),
        },
    )


def _resolve_speaker_specialists(
    snapshot: Any,
    speakers: list[SpeakerSlot],
) -> list[tuple[Any, SpeakerSlot]]:
    """Map supervisor-emitted speaker slots to the planned specialists.

    Lifted from the pre-Phase-4 ``central_executor._resolve_speaker_specialists``
    so the strategy owns the mapping logic.
    """
    requested_roles = [slot.agent for slot in speakers]
    resolved = resolve_turn_specialists(snapshot, requested_roles)
    pairs: list[tuple[Any, SpeakerSlot]] = []
    for specialist, slot in zip(resolved, speakers):
        if specialist.role == slot.agent:
            pairs.append((specialist, slot))
    return pairs
