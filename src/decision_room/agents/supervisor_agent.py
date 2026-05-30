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
from decision_room.providers import ProviderRegistry
from decision_room.routing.model_router import HybridModelRouter

from .base import BaseLLMAgent, TurnContext


@dataclass(frozen=True)
class SupervisorTurnResult:
    plan: Any  # SupervisorPlan — Any to avoid module-level cycle
    route: RoutingDecision
    ctx: Any  # DecisionContext


class SupervisorAgent(BaseLLMAgent):
    """Single-iteration supervisor agent wrapping ``LLMSupervisor``."""

    def __init__(
        self,
        *,
        registry: ProviderRegistry,
        router: HybridModelRouter,
        use_background_threads: bool = True,
        transient_max_attempts: int = 2,
    ) -> None:
        super().__init__(
            registry=registry,
            router=router,
            use_background_threads=use_background_threads,
            transient_max_attempts=transient_max_attempts,
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
        return SupervisorTurnResult(plan=plan, route=new_route, ctx=new_ctx)

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
