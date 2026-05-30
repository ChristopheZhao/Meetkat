"""Agent boundary for decision-room MAS.

Phase 2 of the native-agent refactor. Before Phase 2 the orchestrator
(`room_executor.py`, 1570 lines) owned prompt assembly, JSON parsing,
provider retry, and route fallback for every specialist. Phase 2 makes
each role a real agent: it owns its own prompt builder, its own parser,
and (for specialists) its own ReAct-style iteration loop bounded by
``CandidateSpecialist.turn_budget`` — which until now was dead metadata.

Surface:

- ``Agent``                  — protocol every agent satisfies
- ``TurnContext``            — value object the orchestrator hands to ``run``
- ``RouteExecution``         — text + route + route_ctx returned by one LLM call
- ``BaseLLMAgent``           — shared retry + disaster-fallback logic for any agent
- ``AgentBudgetExhausted``   — raised when a specialist runs out of ReAct iterations
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Mapping, Protocol

from decision_room.mas.types import DecisionContext, DecisionSignals, RoutingDecision
from decision_room.memory import (
    LongTermLessonStore,
    RoomMemoryStore,
    default_long_term_store,
    default_room_memory_store,
    memory_recall_for_role,
)
from decision_room.providers import (
    GenerateRequest,
    ProviderHTTPError,
    ProviderNetworkError,
    ProviderRegistry,
    ProviderTimeoutError,
)
from decision_room.routing.model_router import HybridModelRouter


PublishCallable = Callable[..., Awaitable[Mapping[str, Any]]]


class AgentBudgetExhausted(RuntimeError):
    """Raised when an agent's iteration loop exits without an emit decision.

    Carrying the role + budget makes it easy for ``RoomControlPolicy`` to
    surface this as a runtime-failure end payload without having to parse a
    free-form error string.
    """

    def __init__(self, role: str, budget: int, last_action: str) -> None:
        super().__init__(
            f"agent {role!r} exhausted turn_budget={budget} "
            f"without emit (last_action={last_action!r})"
        )
        self.role = role
        self.budget = budget
        self.last_action = last_action


@dataclass(frozen=True)
class RouteExecution:
    """One successful LLM call: raw text, the route that produced it, and
    the route context after any disaster-fallback signal updates.

    Returned by ``BaseLLMAgent.generate_text`` so callers can thread the
    updated route/ctx into the next call without re-deriving them.
    """

    text: str
    route: RoutingDecision
    ctx: DecisionContext


@dataclass(frozen=True)
class TurnContext:
    """Snapshot of everything an agent needs to run one turn.

    Built by the orchestrator from ``RoomSnapshot`` + the per-round routing
    state. Kept frozen so an agent cannot mutate orchestrator state
    in-place — any state update flows back through the returned
    ``RouteExecution``/``TurnOutput`` objects.

    ``publish`` is optional: tests and in-process replays can run agents
    without a journal sink, in which case tool events are dispatched
    locally but never journaled. Production wiring always passes the
    runtime's ``publish``.
    """

    snapshot: Any
    round_index: int
    phase: Any  # MeetingPhase — kept as Any to avoid a cycle through orchestration
    next_focus: str
    route_ctx: DecisionContext
    route: RoutingDecision
    publish: PublishCallable | None = None
    extras: Mapping[str, Any] = field(default_factory=dict)

    def with_route(self, ctx: DecisionContext, route: RoutingDecision) -> "TurnContext":
        return TurnContext(
            snapshot=self.snapshot,
            round_index=self.round_index,
            phase=self.phase,
            next_focus=self.next_focus,
            route_ctx=ctx,
            route=route,
            publish=self.publish,
            extras=self.extras,
        )


class Agent(Protocol):
    """Async agent contract: take a ``TurnContext``, return a role-specific
    output. The orchestrator only needs ``run`` — everything else (prompt
    building, parsing, retries) is the agent's private concern.
    """

    @property
    def role(self) -> str:
        ...

    async def run(self, ctx: TurnContext) -> Any:
        ...


class BaseLLMAgent:
    """Shared LLM-call infrastructure for every agent role.

    Owns provider lookup, same-route transient retry, and disaster-fallback
    reroute. Before Phase 2 this logic existed twice — once in
    ``room_executor._generate_text`` for specialist/host/synthesis, and once
    in ``central_mas._generate_with_retries`` for the supervisor — with
    drift risk on every change. Phase 2 collapses both into this base.
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
        self._registry = registry
        self._router = router
        self._use_background_threads = use_background_threads
        self._transient_max_attempts = max(1, transient_max_attempts)
        self._room_memory = room_memory_store or default_room_memory_store()
        self._long_term = long_term_store or default_long_term_store()

    @property
    def room_memory(self) -> RoomMemoryStore:
        return self._room_memory

    @property
    def long_term_memory(self) -> LongTermLessonStore:
        return self._long_term

    def read_memory_recall(self, room_id: str, role: str) -> dict[str, Any]:
        """Pull this agent's recall block (shared + agent-local + lessons)."""
        return memory_recall_for_role(
            room_id=room_id,
            role=role,
            room_store=self._room_memory,
            long_term_store=self._long_term,
        )

    async def emit_memory_write(
        self,
        *,
        publish: PublishCallable | None,
        room_id: str,
        scope: str,
        fact_key: str | None = None,
        fact_value: Any = None,
        event_type: str | None = None,
        event_payload: dict[str, Any] | None = None,
    ) -> None:
        """Publish a journal ``memory.write`` event so projection rebuilds
        from journal replay alone (single-SoT discipline). Falls back to a
        direct store write when ``publish`` is None (tests + standalone
        harnesses without a runtime sink).
        """
        payload: dict[str, Any] = {"scope": scope}
        if fact_key:
            payload["fact_key"] = fact_key
            payload["fact_value"] = fact_value
        if event_type:
            payload["memory_event_type"] = event_type
            payload["memory_event_payload"] = event_payload or {}
        if publish is not None:
            await publish(
                room_id,
                producer_id="memory.writer.1",
                role="system",
                event_type="memory.write",
                payload=payload,
            )
            return
        if fact_key:
            self._room_memory.write_fact(room_id, scope, fact_key, fact_value)
        if event_type:
            self._room_memory.record_event(
                room_id, scope, event_type, event_payload or {}
            )

    async def generate_text(
        self,
        *,
        route_ctx: DecisionContext,
        route: RoutingDecision,
        system_prompt: str,
        user_prompt: str,
        role: str,
        temperature: float = 0.2,
    ) -> RouteExecution:
        """Run one LLM call with retry + disaster-fallback semantics.

        Same shape as the pre-Phase-2 ``_generate_text``: try the primary
        route up to ``transient_max_attempts``; on persistent transient
        failure, update the signals so the routing control policy can
        recommend the disaster fallback; if it does and the fallback route
        is different, retry once on the new route. Persistent failure
        surfaces as a ``RuntimeError`` carrying both attempted route
        identities and the original failure reason.
        """
        request = GenerateRequest(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model=route.target.model,
            temperature=temperature,
        )
        try:
            text = await self._call_with_retries(route, request)
            return RouteExecution(text=text, route=route, ctx=route_ctx)
        except (ProviderTimeoutError, ProviderNetworkError, ProviderHTTPError) as exc:
            fallback_ctx = self._ctx_after_provider_failure(route_ctx, exc)
            fallback_route = self._router.route(fallback_ctx)
            if fallback_route.target == route.target:
                raise RuntimeError(
                    "agent model request failed: "
                    f"role={role}; "
                    f"supplier={route.target.supplier}; "
                    f"model={route.target.model}; "
                    f"attempts={self._transient_max_attempts}; "
                    f"reason={exc}"
                ) from exc
            fallback_request = GenerateRequest(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                model=fallback_route.target.model,
                temperature=temperature,
            )
            try:
                text = await self._call_with_retries(fallback_route, fallback_request)
                return RouteExecution(
                    text=text, route=fallback_route, ctx=fallback_ctx
                )
            except Exception as fallback_exc:
                raise RuntimeError(
                    "agent model request failed after control reroute: "
                    f"role={role}; "
                    f"primary={route.target.supplier}/{route.target.model}; "
                    f"rerouted={fallback_route.target.supplier}/{fallback_route.target.model}; "
                    f"primary_reason={exc}; "
                    f"reroute_reason={fallback_exc}"
                ) from fallback_exc
        except Exception as exc:
            raise RuntimeError(
                "agent model request failed: "
                f"role={role}; "
                f"supplier={route.target.supplier}; "
                f"model={route.target.model}; "
                f"reason={exc}"
            ) from exc

    async def _call_with_retries(
        self, route: RoutingDecision, request: GenerateRequest
    ) -> str:
        provider = self._registry.get(route.target.supplier)
        last_exc: Exception | None = None
        for attempt in range(1, self._transient_max_attempts + 1):
            try:
                if self._use_background_threads:
                    response = await asyncio.to_thread(provider.generate, request)
                else:
                    response = provider.generate(request)
                return response.text
            except (
                ProviderTimeoutError,
                ProviderNetworkError,
                ProviderHTTPError,
            ) as exc:
                last_exc = exc
                if attempt >= self._transient_max_attempts:
                    break
                await asyncio.sleep(0.35 * attempt)
        assert last_exc is not None
        raise last_exc

    def _ctx_after_provider_failure(
        self, ctx: DecisionContext, exc: Exception
    ) -> DecisionContext:
        signals = ctx.signals
        next_signals = DecisionSignals(
            support=signals.support,
            confidence=signals.confidence,
            risk_penalty=signals.risk_penalty,
            margin_top1_top2=signals.margin_top1_top2,
            disagreement_index=signals.disagreement_index,
            rounds_without_progress=signals.rounds_without_progress,
            tool_failure_rate=signals.tool_failure_rate,
            api_unreachable=signals.api_unreachable,
            timeout_count=signals.timeout_count,
            rate_limited_count=signals.rate_limited_count,
            missing_required_fields_after_retry=signals.missing_required_fields_after_retry,
            human_force_complete=signals.human_force_complete,
        )
        if isinstance(exc, ProviderTimeoutError):
            next_signals.timeout_count = max(
                next_signals.timeout_count, self._transient_max_attempts
            )
        elif isinstance(exc, ProviderNetworkError):
            next_signals.api_unreachable = True
        elif isinstance(exc, ProviderHTTPError):
            if exc.status_code == 429:
                next_signals.rate_limited_count = max(
                    next_signals.rate_limited_count, self._transient_max_attempts
                )
            elif exc.status_code is not None and exc.status_code >= 500:
                next_signals.api_unreachable = True
        return DecisionContext(
            room_id=ctx.room_id,
            phase=ctx.phase,
            signals=next_signals,
            metadata=dict(ctx.metadata),
        )
