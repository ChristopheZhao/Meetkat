"""Host agent — owns the host-led speaker selection turn.

The host runs one LLM call per round to produce the agenda (focus points
+ turns). Pre-Phase-2 this lived inline in
``LLMRoomExecutor.build_round``; after Phase 2 the host is its own agent
with the same prompt builder/parser/retry path as every other agent.

Single-iteration in v1 — the host doesn't need tools yet, but the loop
scaffold is here so Phase 3+ can add memory recall or tool affordances
without restructuring the call site.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from decision_room.mas.types import RoutingDecision
from decision_room.memory import (
    LongTermLessonStore,
    RoomMemoryStore,
    agent_scope,
    format_memory_recall_section,
    mas_scope,
)
from decision_room.providers import ProviderRegistry
from decision_room.routing.model_router import HybridModelRouter

from .base import BaseLLMAgent, RouteExecution, TurnContext


@dataclass(frozen=True)
class HostTurnResult:
    agenda: Any  # HostAgenda
    route: RoutingDecision
    ctx: Any  # DecisionContext


class HostAgent(BaseLLMAgent):
    """Single-iteration host agent: produces a ``HostAgenda``.

    Phase 3 wires memory through the host: it reads its own recall block
    before building the agenda, and persists the agenda back to the shared
    + agent-local scopes so subsequent agents (and the next round's host)
    can build on it.
    """

    def __init__(
        self,
        *,
        registry: ProviderRegistry,
        router: HybridModelRouter,
        use_background_threads: bool = True,
        transient_max_attempts: int = 2,
        room_memory_store: RoomMemoryStore | None = None,
        long_term_store: LongTermLessonStore | None = None,
    ) -> None:
        super().__init__(
            registry=registry,
            router=router,
            use_background_threads=use_background_threads,
            transient_max_attempts=transient_max_attempts,
            room_memory_store=room_memory_store,
            long_term_store=long_term_store,
        )

    @property
    def role(self) -> str:
        return "host"

    async def run(self, ctx: TurnContext) -> HostTurnResult:
        # Local imports avoid a module-level cycle: the host prompt builder
        # lives in real_run_contract / room_executor.
        from decision_room.orchestration.real_run_contract import (
            build_host_prompts,
            parse_host_agenda,
        )
        from decision_room.orchestration.room_executor import (
            _build_runtime_meeting_brief,
            _filter_host_agenda_open_questions,
        )

        next_focus = ctx.next_focus
        room_id = ctx.snapshot.room_id
        brief = _build_runtime_meeting_brief(ctx.snapshot, next_focus)
        allowed_constraint_ids = {item["id"] for item in brief["constraints"]}
        allowed_specialist_roles = {
            item["role"]
            for item in brief.get("candidate_specialists", [])
            if item.get("role")
        }
        system_prompt, user_prompt = build_host_prompts(brief)
        recall = self.read_memory_recall(room_id, "host")
        recall_section = format_memory_recall_section(recall)
        if recall_section:
            user_prompt = f"{recall_section}{user_prompt}"
        execution: RouteExecution = await self.generate_text(
            route_ctx=ctx.route_ctx,
            route=ctx.route,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            role="host",
        )
        agenda = parse_host_agenda(
            execution.text,
            allowed_constraint_ids,
            allowed_specialist_roles,
        )
        agenda = _filter_host_agenda_open_questions(ctx.snapshot, agenda)
        await self._persist_agenda(
            publish=ctx.publish,
            room_id=room_id,
            round_index=ctx.round_index,
            agenda=agenda,
        )
        return HostTurnResult(agenda=agenda, route=execution.route, ctx=execution.ctx)

    async def _persist_agenda(
        self,
        *,
        publish: Any,
        room_id: str,
        round_index: int,
        agenda: Any,
    ) -> None:
        agenda_payload = {
            "round_index": round_index,
            "focus_points": [asdict(point) for point in agenda.focus_points],
            "turns": [asdict(turn) for turn in agenda.turns],
            "open_questions": list(agenda.open_questions),
        }
        await self.emit_memory_write(
            publish=publish,
            room_id=room_id,
            scope=mas_scope(room_id),
            fact_key="latest_host_agenda",
            fact_value=agenda_payload,
        )
        await self.emit_memory_write(
            publish=publish,
            room_id=room_id,
            scope=mas_scope(room_id),
            event_type="host.agenda",
            event_payload={
                "round_index": round_index,
                "target_roles": [turn.role for turn in agenda.turns],
                "open_questions": list(agenda.open_questions),
            },
        )
        await self.emit_memory_write(
            publish=publish,
            room_id=room_id,
            scope=agent_scope(room_id, "host"),
            fact_key="last_self_agenda",
            fact_value=agenda_payload,
        )
