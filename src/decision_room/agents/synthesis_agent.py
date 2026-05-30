"""Synthesis agent — owns the per-round structured summarization turn.

The synthesis "capability" is marked ``speaking=False`` in the planning
artifacts but actually drives important orchestration signals: it emits
``recommended_next_phase`` and ``recommended_next_action`` which the
runtime honors before the FSM fires. Phase 2 makes that ownership
visible by giving synthesis its own ``Agent`` class instead of leaving
it as a method on the executor.

Single-iteration in v1 — Phase 5 of the plan moves the orchestration
hints out of ``SynthesisOutput`` into a dedicated ``OrchestrationAgent``
or back onto the speaker-selection agent (host/supervisor).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from decision_room.mas.types import RoutingDecision
from decision_room.providers import ProviderRegistry
from decision_room.routing.model_router import HybridModelRouter

from .base import BaseLLMAgent, RouteExecution, TurnContext


@dataclass(frozen=True)
class SynthesisTurnResult:
    output: Any  # SynthesisOutput — Any to avoid cycle
    route: RoutingDecision
    ctx: Any  # DecisionContext


class SynthesisAgent(BaseLLMAgent):
    """Single-iteration synthesis agent."""

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

    @property
    def role(self) -> str:
        return "synthesis"

    async def run(self, ctx: TurnContext) -> SynthesisTurnResult:
        # Local imports avoid module-level cycles.
        from decision_room.orchestration.room_executor import (
            _build_synthesis_prompts,
            _parse_synthesis_output,
        )

        extras = ctx.extras
        host_agenda = extras["host_agenda"]
        turn_results = extras["turn_results"]

        system_prompt, user_prompt = _build_synthesis_prompts(
            snapshot=ctx.snapshot,
            phase=ctx.phase,
            round_index=ctx.round_index,
            next_focus=ctx.next_focus,
            host_agenda=host_agenda,
            turn_results=turn_results,
            route=ctx.route,
        )
        execution: RouteExecution = await self.generate_text(
            route_ctx=ctx.route_ctx,
            route=ctx.route,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            role="synthesis",
        )
        output = _parse_synthesis_output(execution.text)
        return SynthesisTurnResult(
            output=output, route=execution.route, ctx=execution.ctx
        )
