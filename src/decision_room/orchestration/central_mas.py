"""Centralized supervisor MAS — value types + LLM-driven plan owner.

The supervisor is a single LLM call per round. It emits a ranked role
selection and one assignment contract per role (mission, deliverable,
constraints, runtime_hints). Specialist execution and synthesis stay in
``room_executor`` so the centralized topology reuses the same provider /
routing / parsing machinery as the host-led topology.

No deterministic role output lives here. A scripted offline path is
intentionally not provided as a default — environments without provider
env should surface ``UnavailableRoomExecutor`` from ``CentralizedMASExecutor``
instead of silently emitting stub paragraphs.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from decision_room.mas.types import (
    DecisionContext,
    DecisionSignals,
    MeetingPhase,
    RoutingDecision,
)
from decision_room.providers import (
    GenerateRequest,
    ProviderHTTPError,
    ProviderNetworkError,
    ProviderRegistry,
    ProviderTimeoutError,
)
from decision_room.routing.model_router import HybridModelRouter

from .pre_room_planning import CandidateSpecialist, planned_specialists_from_snapshot
from .real_run_contract import AgendaFocusPoint, AgendaTurn, HostAgenda, extract_json_object


@dataclass(frozen=True)
class CentralAgentRole:
    role: str
    display_name: str
    mission: str
    deliverable: str
    focus_areas: list[str]
    stance: str

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AssignmentContract:
    agent: str
    run: bool
    mission: str
    deliverable: str
    constraints: list[str] = field(default_factory=list)
    order: int = 0
    runtime_hints: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SupervisorState:
    run_id: str
    round_index: int
    phase: str
    current_focus: str
    memory_projection: dict[str, Any]
    assignment_contracts: list[AssignmentContract]
    next_node: str
    gate_status: str

    def to_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["assignment_contracts"] = [
            item.to_payload() for item in self.assignment_contracts
        ]
        return payload


@dataclass(frozen=True)
class SupervisorPlan:
    role_catalog: list[CentralAgentRole]
    assignment_contracts: list[AssignmentContract]
    current_focus: str
    phase: MeetingPhase
    decision_focus: str
    open_questions: list[str]
    reason: str

    def runnable_contracts(self) -> list[AssignmentContract]:
        return [item for item in self.assignment_contracts if item.run]


_STANCE_BY_ROLE = {
    "implementation_specialist": "feasibility",
    "risk_specialist": "risk",
    "product_specialist": "product",
    "operations_specialist": "synthesis",
}


def central_agent_role_from_specialist(specialist: CandidateSpecialist) -> CentralAgentRole:
    return CentralAgentRole(
        role=specialist.role,
        display_name=specialist.display_name,
        mission=specialist.capability_profile,
        deliverable=specialist.prompt_contract,
        focus_areas=list(specialist.focus_areas),
        stance=_STANCE_BY_ROLE.get(specialist.role, "specialist"),
    )


def role_catalog_from_snapshot(snapshot: Any) -> list[CentralAgentRole]:
    return [
        central_agent_role_from_specialist(item)
        for item in planned_specialists_from_snapshot(snapshot)
    ]


def supervisor_plan_to_host_agenda(plan: SupervisorPlan) -> HostAgenda:
    """Adapt a SupervisorPlan to a HostAgenda shape so existing specialist
    and synthesis prompt builders in ``room_executor`` can be reused without
    duplication. The product / journal still see the richer central_mas
    artifact bundle on the host message.
    """
    runnable = plan.runnable_contracts()
    focus_points = [
        AgendaFocusPoint(
            title=plan.decision_focus or plan.current_focus or "round focus",
            reason=plan.reason or plan.current_focus or "supervisor focus for the round",
            constraint_ids=[],
        )
    ]
    turns = [
        AgendaTurn(role=contract.agent, task=contract.mission)
        for contract in runnable
    ]
    return HostAgenda(
        focus_points=focus_points,
        turns=turns,
        open_questions=list(plan.open_questions),
        no_new_constraints=True,
    )


def build_supervisor_prompts(
    *,
    snapshot: Any,
    round_index: int,
    role_catalog: list[CentralAgentRole],
    phase: MeetingPhase,
    next_focus: str,
) -> tuple[str, str]:
    schema = {
        "current_focus": "what this round should decide, short and grounded",
        "decision_focus": "specific decision being pushed forward in this round",
        "phase": phase.value,
        "open_questions": ["question worth tracking, optional"],
        "reason": "short rationale for the role selection",
        "assignment_contracts": [
            {
                "agent": "<role from role_catalog>",
                "run": True,
                "mission": "what this specialist should produce for THIS round",
                "deliverable": "concrete output the specialist returns",
                "constraints": ["bounded condition the specialist must respect"],
                "order": 1,
                "runtime_hints": {"phase": phase.value},
            }
        ],
    }
    catalog_payload = [item.to_payload() for item in role_catalog]
    state_payload = _supervisor_room_state(snapshot, round_index, phase, next_focus)
    system_prompt = (
        "You are the central supervisor of a multi-agent decision room. "
        "Your job is to decide which specialist roles should speak in this round "
        "and to issue one assignment contract per role. "
        "Stay grounded in the room state and the role catalog. "
        "Choose 2-4 roles from the catalog only. "
        "Do not invent roles. Do not invent constraints that are not implied by "
        "the requirement or the visible state. "
        "Clarification protocol: if the requirement is materially ambiguous in a "
        "way that would change which specialists belong in this round (e.g., the "
        "target user cohort, the scope boundary, or a key trade-off axis is "
        "unclear), surface the ambiguity to the operator instead of guessing. "
        "Write a concrete one-line question in `decision_focus` prefixed with "
        "'[Awaiting operator clarification]', explain why in `reason`, and issue "
        "a minimal assignment contract list (one role is fine) whose mission is to "
        "frame the choice for the operator. The operator answers via the room "
        "human-message channel; their reply appears as `last_human_message` in the "
        "next round and you re-plan with that context. "
        "Return exactly one JSON object and nothing else."
    )
    user_prompt = (
        "Room state:\n"
        f"{json.dumps(state_payload, ensure_ascii=False, indent=2)}\n\n"
        "Role catalog (you may select from these only):\n"
        f"{json.dumps(catalog_payload, ensure_ascii=False, indent=2)}\n\n"
        "Task:\n"
        f"- Choose 2-4 specialist roles for round {round_index}.\n"
        "- For each selected role, produce a concrete mission, deliverable, "
        "constraints list, runtime_hints, and order.\n"
        "- Set run=true for selected roles; omit non-selected roles entirely.\n"
        "- Each mission must reference the actual room requirement and decision focus.\n"
        "- Constraints must be specific to THIS round, not generic platitudes.\n"
        "- decision_focus must be the single decision being pushed this round.\n"
        "- Keep open_questions empty unless something visible is missing.\n\n"
        "Output schema example (values are placeholders):\n"
        f"{json.dumps(schema, ensure_ascii=False, indent=2)}"
    )
    return system_prompt, user_prompt


def parse_supervisor_plan(
    raw: str,
    *,
    role_catalog: list[CentralAgentRole],
    phase: MeetingPhase,
    fallback_focus: str,
) -> SupervisorPlan:
    try:
        payload = json.loads(extract_json_object(raw))
    except Exception as exc:
        raise ValueError(f"supervisor response must contain one valid JSON object: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("supervisor response must be a JSON object")

    allowed_roles = {item.role for item in role_catalog}
    raw_contracts = payload.get("assignment_contracts")
    if not isinstance(raw_contracts, list) or not raw_contracts:
        raise ValueError("supervisor.assignment_contracts must be a non-empty list")

    contracts: list[AssignmentContract] = []
    seen: set[str] = set()
    for index, item in enumerate(raw_contracts):
        if not isinstance(item, dict):
            continue
        agent = str(item.get("agent", "")).strip().lower()
        if not agent or agent not in allowed_roles or agent in seen:
            continue
        run = bool(item.get("run", True))
        mission = str(item.get("mission", "")).strip()
        deliverable = str(item.get("deliverable", "")).strip()
        constraints = [
            str(value).strip()
            for value in item.get("constraints", []) or []
            if str(value).strip()
        ]
        runtime_hints_payload = item.get("runtime_hints")
        runtime_hints = (
            dict(runtime_hints_payload)
            if isinstance(runtime_hints_payload, dict)
            else {}
        )
        order_value = item.get("order")
        try:
            order = int(order_value) if order_value is not None else index
        except (TypeError, ValueError):
            order = index
        if not mission or not deliverable:
            continue
        contracts.append(
            AssignmentContract(
                agent=agent,
                run=run,
                mission=mission,
                deliverable=deliverable,
                constraints=constraints[:6],
                order=order,
                runtime_hints=runtime_hints,
            )
        )
        seen.add(agent)
    if not contracts:
        raise ValueError(
            "supervisor.assignment_contracts contained no valid role assignments"
        )
    contracts.sort(key=lambda contract: contract.order)
    current_focus = str(payload.get("current_focus", "")).strip() or fallback_focus
    decision_focus = str(payload.get("decision_focus", "")).strip() or current_focus
    reason = str(payload.get("reason", "")).strip() or (
        "supervisor selected specialists from shared room memory"
    )
    open_questions_raw = payload.get("open_questions", [])
    open_questions = [
        str(value).strip()
        for value in (open_questions_raw if isinstance(open_questions_raw, list) else [])
        if str(value).strip()
    ][:4]
    return SupervisorPlan(
        role_catalog=list(role_catalog),
        assignment_contracts=contracts,
        current_focus=current_focus,
        phase=phase,
        decision_focus=decision_focus,
        open_questions=open_questions,
        reason=reason,
    )


def build_supervisor_state(
    *,
    snapshot: Any,
    round_index: int,
    plan: SupervisorPlan,
) -> SupervisorState:
    return SupervisorState(
        run_id=str(getattr(snapshot, "room_id", "")),
        round_index=round_index,
        phase=plan.phase.value,
        current_focus=plan.current_focus,
        memory_projection=_memory_projection(snapshot),
        assignment_contracts=list(plan.assignment_contracts),
        next_node=plan.assignment_contracts[0].agent if plan.assignment_contracts else "synthesis",
        gate_status="passed",
    )


def central_mas_artifact_bundle(
    *,
    state: SupervisorState,
    plan: SupervisorPlan,
    route: RoutingDecision,
) -> dict[str, Any]:
    return {
        "topology": "single_supervisor_shared_memory",
        "supervisor_state": state.to_payload(),
        "role_catalog": [item.to_payload() for item in plan.role_catalog],
        "assignment_contracts": [item.to_payload() for item in plan.assignment_contracts],
        "decision_focus": plan.decision_focus,
        "reason": plan.reason,
        "route": {
            "tier": getattr(route.tier, "value", str(route.tier)),
            "supplier": route.target.supplier,
            "model": route.target.model,
        },
    }


class LLMSupervisor:
    """Single LLM call that owns role selection and assignment contracts."""

    def __init__(
        self,
        registry: ProviderRegistry,
        router: HybridModelRouter,
        *,
        transient_max_attempts: int = 2,
    ) -> None:
        self._registry = registry
        self._router = router
        self._transient_max_attempts = max(1, transient_max_attempts)

    async def plan_round(
        self,
        *,
        snapshot: Any,
        round_index: int,
        phase: MeetingPhase,
        next_focus: str,
        route_ctx: DecisionContext,
        route: RoutingDecision,
    ) -> tuple[SupervisorPlan, DecisionContext, RoutingDecision]:
        role_catalog = role_catalog_from_snapshot(snapshot)
        if not role_catalog:
            raise RuntimeError(
                "central supervisor cannot plan a round without a candidate specialist roster"
            )
        system_prompt, user_prompt = build_supervisor_prompts(
            snapshot=snapshot,
            round_index=round_index,
            role_catalog=role_catalog,
            phase=phase,
            next_focus=next_focus,
        )
        text, new_route_ctx, new_route = await _generate_with_retries(
            registry=self._registry,
            router=self._router,
            route_ctx=route_ctx,
            route=route,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            role="supervisor",
            transient_max_attempts=self._transient_max_attempts,
        )
        plan = parse_supervisor_plan(
            text,
            role_catalog=role_catalog,
            phase=phase,
            fallback_focus=next_focus,
        )
        return plan, new_route_ctx, new_route


def _supervisor_room_state(
    snapshot: Any,
    round_index: int,
    phase: MeetingPhase,
    next_focus: str,
) -> dict[str, Any]:
    transcript = getattr(snapshot, "transcript", []) or []
    transcript_slice = [
        {
            "role": getattr(entry, "role", ""),
            "title": getattr(entry, "title", ""),
            "text": _truncate(getattr(entry, "text", ""), 200),
        }
        for entry in transcript[-4:]
    ]
    return {
        "requirement": getattr(snapshot, "requirement", ""),
        "topic": getattr(snapshot, "topic", ""),
        "goal": getattr(snapshot, "goal", ""),
        "phase": phase.value,
        "round_index": round_index,
        "current_focus": next_focus,
        "constraints": list(getattr(snapshot, "constraints", []) or []),
        "open_questions": list(getattr(snapshot, "open_questions", []) or []),
        "candidate_decision": getattr(snapshot, "candidate_decision", ""),
        "last_human_message": getattr(snapshot, "last_human_message", ""),
        "recent_transcript": transcript_slice,
    }


def _memory_projection(snapshot: Any) -> dict[str, Any]:
    return {
        "requirement": getattr(snapshot, "requirement", ""),
        "topic": getattr(snapshot, "topic", ""),
        "goal": getattr(snapshot, "goal", ""),
        "constraints": list(getattr(snapshot, "constraints", []) or [])[:6],
        "open_questions": list(getattr(snapshot, "open_questions", []) or [])[:4],
        "transcript_depth": len(getattr(snapshot, "transcript", []) or []),
        "last_human_message": getattr(snapshot, "last_human_message", ""),
        "candidate_decision": getattr(snapshot, "candidate_decision", ""),
    }


def _truncate(value: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


async def _generate_with_retries(
    *,
    registry: ProviderRegistry,
    router: HybridModelRouter,
    route_ctx: DecisionContext,
    route: RoutingDecision,
    system_prompt: str,
    user_prompt: str,
    role: str,
    transient_max_attempts: int,
) -> tuple[str, DecisionContext, RoutingDecision]:
    """Local copy of room_executor._generate_text retry/fallback semantics,
    scoped to one LLM call for the supervisor. Kept narrow so the central
    path stays under its own control boundary while reusing identical
    provider error semantics.
    """
    import asyncio

    request = GenerateRequest(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        model=route.target.model,
        temperature=0.2,
    )
    provider = registry.get(route.target.supplier)
    last_exc: Exception | None = None
    for attempt in range(1, transient_max_attempts + 1):
        try:
            response = await asyncio.to_thread(provider.generate, request)
            return response.text, route_ctx, route
        except (ProviderTimeoutError, ProviderNetworkError, ProviderHTTPError) as exc:
            last_exc = exc
            if attempt >= transient_max_attempts:
                break
            await asyncio.sleep(0.35 * attempt)
        except Exception as exc:
            raise RuntimeError(
                f"central supervisor request failed: role={role}; "
                f"supplier={route.target.supplier}; model={route.target.model}; reason={exc}"
            ) from exc

    assert last_exc is not None
    next_ctx = _ctx_after_provider_failure(route_ctx, last_exc, transient_max_attempts)
    fallback_route = router.route(next_ctx)
    if fallback_route.target == route.target:
        raise RuntimeError(
            "central supervisor request failed: "
            f"role={role}; supplier={route.target.supplier}; "
            f"model={route.target.model}; attempts={transient_max_attempts}; reason={last_exc}"
        ) from last_exc
    fallback_request = GenerateRequest(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        model=fallback_route.target.model,
        temperature=0.2,
    )
    provider = registry.get(fallback_route.target.supplier)
    try:
        response = await asyncio.to_thread(provider.generate, fallback_request)
        return response.text, next_ctx, fallback_route
    except Exception as fallback_exc:
        raise RuntimeError(
            "central supervisor request failed after control reroute: "
            f"role={role}; "
            f"primary={route.target.supplier}/{route.target.model}; "
            f"rerouted={fallback_route.target.supplier}/{fallback_route.target.model}; "
            f"primary_reason={last_exc}; reroute_reason={fallback_exc}"
        ) from fallback_exc


def _ctx_after_provider_failure(
    ctx: DecisionContext,
    exc: Exception,
    transient_max_attempts: int,
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
        next_signals.timeout_count = max(next_signals.timeout_count, transient_max_attempts)
    elif isinstance(exc, ProviderNetworkError):
        next_signals.api_unreachable = True
    elif isinstance(exc, ProviderHTTPError):
        if exc.status_code == 429:
            next_signals.rate_limited_count = max(
                next_signals.rate_limited_count, transient_max_attempts
            )
        elif exc.status_code is not None and exc.status_code >= 500:
            next_signals.api_unreachable = True
    return DecisionContext(
        room_id=ctx.room_id,
        phase=ctx.phase,
        signals=next_signals,
        metadata=dict(ctx.metadata),
    )
