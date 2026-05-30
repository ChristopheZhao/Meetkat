"""Supervisor agent — owns supervisor-led speaker selection.

Wraps the existing ``LLMSupervisor`` from ``orchestration.central_mas``
in the ``Agent`` protocol. Phase 2 stops at the wrapper: the prompt
builder + parser + retry semantics already live in ``central_mas`` and
work, so this agent's job is just to surface them through the same
``run(turn_ctx)`` entrypoint as every other agent.

Two side benefits land in Phase 2 via this wrapper:

1. The orchestrator (``central_executor``) no longer needs the duplicate
   round-loop logic that re-implemented host-led signal derivation via
   private cross-calls — it can call ``SupervisorAgent.run`` instead.
2. The supervisor retry/route-fallback path now goes through the same
   ``BaseLLMAgent.generate_text`` infrastructure as specialists and
   synthesis (via ``LLMSupervisor`` itself, which we keep intact in this
   phase). Collapsing the two retry helpers fully is Phase 4 work.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from decision_room.mas.types import RoutingDecision
from decision_room.memory import LongTermLessonStore, RoomMemoryStore, mas_scope
from decision_room.providers import ProviderRegistry
from decision_room.routing.model_router import HybridModelRouter

from .base import BaseLLMAgent, TurnContext


@dataclass(frozen=True)
class SupervisorTurnResult:
    plan: Any  # SupervisorPlan — Any to avoid module-level cycle
    route: RoutingDecision
    ctx: Any  # DecisionContext


class SupervisorAgent(BaseLLMAgent):
    """Single-iteration supervisor agent wrapping ``LLMSupervisor``.

    Phase 3 moves the supervisor-plan persistence (previously inline in
    ``CentralizedMASExecutor._record_supervisor_plan``) into this agent
    so every agent owns its own memory write path uniformly. The
    underlying ``LLMSupervisor`` already builds the plan-room prompt with
    enough room state; recall integration goes through that path in
    Phase 4 when the supervisor prompt builder gains a recall parameter.
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
        # Build the underlying supervisor lazily on first use so the
        # circular-import surface stays small.
        self._supervisor: Any = None

    @property
    def role(self) -> str:
        return "supervisor"

    async def run(self, ctx: TurnContext) -> SupervisorTurnResult:
        supervisor = self._get_supervisor()
        plan, new_ctx, new_route = await supervisor.plan_round(
            snapshot=ctx.snapshot,
            round_index=ctx.round_index,
            phase=ctx.phase,
            next_focus=ctx.next_focus,
            route_ctx=ctx.route_ctx,
            route=ctx.route,
        )
        await self._persist_plan(
            publish=ctx.publish,
            room_id=ctx.snapshot.room_id,
            round_index=ctx.round_index,
            plan=plan,
        )
        return SupervisorTurnResult(plan=plan, route=new_route, ctx=new_ctx)

    async def _persist_plan(
        self,
        *,
        publish: Any,
        room_id: str,
        round_index: int,
        plan: Any,
    ) -> None:
        shared = mas_scope(room_id)
        plan_payload = {
            "round_index": round_index,
            "phase": plan.phase.value,
            "decision_focus": plan.decision_focus,
            "current_focus": plan.current_focus,
            "reason": plan.reason,
            "open_questions": list(plan.open_questions),
            "speakers": [item.to_payload() for item in plan.speakers],
        }
        await self.emit_memory_write(
            publish=publish,
            room_id=room_id,
            scope=shared,
            fact_key="latest_supervisor_plan",
            fact_value=plan_payload,
        )
        await self.emit_memory_write(
            publish=publish,
            room_id=room_id,
            scope=shared,
            event_type="supervisor.plan",
            event_payload={
                "round_index": round_index,
                "decision_focus": plan.decision_focus,
                "speakers": [item.agent for item in plan.runnable_speakers()],
            },
        )

    def _get_supervisor(self) -> Any:
        if self._supervisor is None:
            # Imported here to avoid a module-level cycle through
            # orchestration.central_mas during agents package init.
            from decision_room.orchestration.central_mas import LLMSupervisor

            self._supervisor = LLMSupervisor(
                registry=self._registry,
                router=self._router,
                transient_max_attempts=self._transient_max_attempts,
            )
        return self._supervisor
