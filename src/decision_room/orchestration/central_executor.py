"""Centralized MAS executor — real LLM supervisor + LLM specialists.

This executor produces RoomRound outputs by:

1. Calling ``LLMSupervisor.plan_round`` to choose specialists and emit one
   assignment contract per role.
2. Calling the existing ``LLMRoomExecutor`` specialist machinery
   (``_generate_argument`` / ``_generate_synthesis``) for each contract so
   the same provider, routing, retry, and JSON-parsing rules apply to both
   topologies.
3. Reusing ``HybridConsensusStrategy`` for convergence — convergence stays
   signal-gated, never a round counter.

If the provider env is missing the factory returns an ``UnavailableRoomExecutor``
just like ``LLMRoomExecutor.from_mapping``; the centralized topology never
silently emits stub output as a default.
"""

from __future__ import annotations

import os
from typing import Any, Mapping

from decision_room.mas.hybrid import (
    HybridConsensusStrategy,
    HybridCoordinationStrategy,
    HybridPlanningStrategy,
)
from decision_room.mas.types import CoordinationAction, DecisionContext
from decision_room.memory import (
    LongTermLessonStore,
    RoomMemoryStore,
    default_long_term_store,
    default_room_memory_store,
)
from decision_room.policies.fallback import DisasterOnlyFallbackPolicy
from decision_room.policies.routing_control import RoutingControlPolicy
from decision_room.providers import ProviderRegistry
from decision_room.routing.model_router import HybridModelRouter, RouterTargets

from .central_mas import LLMSupervisor, SpeakerSlot
from .room_executor import (
    RoomExecutor,
    RoomRound,
    UnavailableRoomExecutor,
    _env,
    _env_int_optional,
    _provider_config,
    _target,
)


class CentralizedMASExecutor:
    """LLM-driven centralized supervisor executor."""

    def __init__(
        self,
        registry: ProviderRegistry,
        router: HybridModelRouter,
        *,
        supervisor: LLMSupervisor | None = None,
        coordination: HybridCoordinationStrategy | None = None,
        consensus: HybridConsensusStrategy | None = None,
        planner: HybridPlanningStrategy | None = None,
        use_background_threads: bool = True,
        transient_max_attempts: int = 2,
        room_memory_store: RoomMemoryStore | None = None,
        long_term_store: LongTermLessonStore | None = None,
    ) -> None:
        self._registry = registry
        self._router = router
        # ``supervisor`` is a back-compat injection point — callers that
        # still construct an LLMSupervisor directly can pass it in. The
        # supervisor-led strategy honors it via its legacy_supervisor arg.
        self._legacy_supervisor = supervisor
        self._coordination = coordination or HybridCoordinationStrategy()
        self._consensus = consensus or HybridConsensusStrategy()
        self._planner = planner or HybridPlanningStrategy()
        # Phase 2-4: every role runs as an Agent and the round build lives
        # in a single RoundOrchestrator. This class is a back-compat
        # wrapper that wires the supervisor-led strategy.
        from decision_room.agents import SupervisorAgent
        from .round_orchestrator import RoundOrchestrator
        from .speaker_strategies import SupervisorLedSpeakerStrategy

        self._room_memory = room_memory_store or default_room_memory_store()
        self._long_term = long_term_store or default_long_term_store()
        agent_kwargs = dict(
            registry=registry,
            router=router,
            use_background_threads=use_background_threads,
            transient_max_attempts=transient_max_attempts,
            room_memory_store=self._room_memory,
            long_term_store=self._long_term,
        )
        self._supervisor_agent = SupervisorAgent(**agent_kwargs)
        self._orchestrator = RoundOrchestrator(
            registry=registry,
            router=router,
            strategy=SupervisorLedSpeakerStrategy(
                self._supervisor_agent,
                legacy_supervisor=self._legacy_supervisor,
            ),
            planner=self._planner,
            coordination=self._coordination,
            consensus=self._consensus,
            use_background_threads=use_background_threads,
            transient_max_attempts=transient_max_attempts,
            room_memory_store=self._room_memory,
            long_term_store=self._long_term,
        )
        # Expose specialist/synthesis agents for back-compat introspection.
        self._specialist_agent = self._orchestrator._specialist_agent  # noqa: SLF001
        self._synthesis_agent = self._orchestrator._synthesis_agent  # noqa: SLF001
        self._transient_max_attempts = max(1, transient_max_attempts)

    @classmethod
    def from_env(cls) -> RoomExecutor:
        return cls.from_mapping(os.environ)

    @classmethod
    def from_mapping(cls, env: Mapping[str, str]) -> RoomExecutor:
        try:
            default_supplier = _env("MODEL_DEFAULT_SUPPLIER", env)
            default_model = _env("MODEL_DEFAULT_MODEL", env)
            escalation_supplier = _env("MODEL_ESCALATION_SUPPLIER", env)
            escalation_model = _env("MODEL_ESCALATION_MODEL", env)
            fallback_supplier = _env("MODEL_FALLBACK_SUPPLIER", env)
            fallback_model = _env("MODEL_FALLBACK_MODEL", env)
            supplier_ids = {default_supplier, escalation_supplier, fallback_supplier}
            registry = ProviderRegistry.from_openai_compatible_configs(
                {supplier: _provider_config(supplier, env) for supplier in supplier_ids}
            )
            router = HybridModelRouter(
                RoutingControlPolicy(DisasterOnlyFallbackPolicy()),
                targets=RouterTargets(
                    default_target=_target(default_supplier, default_model),
                    escalation_target=_target(escalation_supplier, escalation_model),
                    disaster_fallback_target=_target(fallback_supplier, fallback_model),
                ),
            )
        except Exception as exc:
            return UnavailableRoomExecutor(str(exc))
        return cls(
            registry=registry,
            router=router,
            transient_max_attempts=_env_int_optional(
                "MODEL_REQUEST_MAX_ATTEMPTS", 2, env
            ),
        )

    async def build_round(
        self,
        snapshot: Any,
        round_index: int,
        *,
        publish: Any = None,
    ) -> RoomRound:
        # Phase 4: the supervisor-led build loop is now identical to the
        # host-led one apart from speaker selection. Delegate to the
        # RoundOrchestrator wired with SupervisorLedSpeakerStrategy.
        return await self._orchestrator.build_round(
            snapshot, round_index, publish=publish
        )

    def _coordination_for_speakers(
        self,
        ctx: DecisionContext,
        specialist_pairs: list[tuple[Any, "SpeakerSlot"]],
        *,
        snapshot: Any | None = None,
    ) -> CoordinationAction:
        """Back-compat shim. Phase 4 moved the canonical implementation
        to ``RoundOrchestrator._coordination_for_assignments``; external
        callers (the central_mas regression suite still exercises this
        path directly) get the same precedence rule through here.
        """
        from .speaker_strategies import SpecialistAssignment

        assignments = [
            SpecialistAssignment(specialist=specialist, task=slot.focus_angle, speaker_slot=slot)
            for specialist, slot in specialist_pairs
        ]
        return self._orchestrator._coordination_for_assignments(  # noqa: SLF001
            ctx, assignments, snapshot=snapshot
        )
