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
class SynthesisTurnResult:
    output: Any  # SynthesisOutput — Any to avoid cycle
    route: RoutingDecision
    ctx: Any  # DecisionContext


class SynthesisAgent(BaseLLMAgent):
    """Single-iteration synthesis agent.

    Phase 3 wires memory: reads its own recall before composing the
    summary, then persists the candidate decision + conclusion type back
    to the shared + agent-local scopes so the next round's agents see
    where the room landed.
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
        room_id = ctx.snapshot.room_id

        system_prompt, user_prompt = _build_synthesis_prompts(
            snapshot=ctx.snapshot,
            phase=ctx.phase,
            round_index=ctx.round_index,
            next_focus=ctx.next_focus,
            host_agenda=host_agenda,
            turn_results=turn_results,
            route=ctx.route,
        )
        recall = self.read_memory_recall(room_id, "synthesis")
        recall_section = format_memory_recall_section(recall)
        if recall_section:
            user_prompt = f"{recall_section}{user_prompt}"
        execution: RouteExecution = await self.generate_text(
            route_ctx=ctx.route_ctx,
            route=ctx.route,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            role="synthesis",
        )
        output = _parse_synthesis_output(execution.text)
        await self._persist_synthesis(
            publish=ctx.publish,
            room_id=room_id,
            round_index=ctx.round_index,
            output=output,
        )
        return SynthesisTurnResult(
            output=output, route=execution.route, ctx=execution.ctx
        )

    async def _persist_synthesis(
        self,
        *,
        publish: Any,
        room_id: str,
        round_index: int,
        output: Any,
    ) -> None:
        synthesis_payload = {
            "round_index": round_index,
            "decision_candidate": output.decision_candidate,
            "conclusion_type": output.conclusion_type,
            "conclusion_reason": output.conclusion_reason,
            "action_item_draft": list(output.action_item_draft),
            "open_questions": list(output.open_questions),
            "agreement": list(output.agreement),
            "disagreement": list(output.disagreement),
            "should_end_meeting": bool(output.should_end_meeting),
        }
        await self.emit_memory_write(
            publish=publish,
            room_id=room_id,
            scope=mas_scope(room_id),
            fact_key="latest_synthesis",
            fact_value=synthesis_payload,
        )
        await self.emit_memory_write(
            publish=publish,
            room_id=room_id,
            scope=mas_scope(room_id),
            event_type="synthesis.summary",
            event_payload={
                "round_index": round_index,
                "decision_candidate": output.decision_candidate,
                "conclusion_type": output.conclusion_type,
            },
        )
        await self.emit_memory_write(
            publish=publish,
            room_id=room_id,
            scope=agent_scope(room_id, "synthesis"),
            fact_key="last_self_synthesis",
            fact_value=synthesis_payload,
        )
