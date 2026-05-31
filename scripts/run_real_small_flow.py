"""
Run a tiny real-model hybrid meeting flow.

Required env vars:
- MODEL_DEFAULT_SUPPLIER / MODEL_DEFAULT_MODEL
- MODEL_ESCALATION_SUPPLIER / MODEL_ESCALATION_MODEL
- MODEL_FALLBACK_SUPPLIER / MODEL_FALLBACK_MODEL
- <SUPPLIER>_BASE_URL / <SUPPLIER>_API_KEY for each referenced supplier

Optional env vars:
- MODEL_TIMEOUT_SEC
- <SUPPLIER>_TIMEOUT_SEC
- REAL_RUN_ROUTE=auto|default|escalation|fallback
"""

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from decision_room.mas.hybrid import (
    HybridConsensusStrategy,
    HybridCoordinationStrategy,
    HybridPlanningStrategy,
)
from decision_room.mas.types import (
    DecisionContext,
    DecisionSignals,
    MeetingPhase,
    ModelTier,
    ModelTarget,
    RoutingDecision,
)
from decision_room.policies.fallback import DisasterOnlyFallbackPolicy
from decision_room.providers import GenerateRequest, ProviderConfig, ProviderRegistry
from decision_room.routing.model_router import HybridModelRouter, RouterTargets
from decision_room.orchestration.real_run_contract import (
    build_host_prompts,
    build_meeting_brief,
    parse_host_agenda,
)


def _env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"missing env: {name}")
    return value


def _env_int_optional(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise RuntimeError(f"invalid int env: {name}={value}") from exc


def _env_optional(name: str, default: str) -> str:
    value = os.getenv(name)
    if not value:
        return default
    return value


def _supplier_config(supplier: str) -> ProviderConfig:
    prefix = supplier.upper()
    timeout_sec = _env_int_optional(
        f"{prefix}_TIMEOUT_SEC",
        _env_int_optional("MODEL_TIMEOUT_SEC", 45),
    )
    return ProviderConfig(
        supplier=supplier,
        base_url=_env(f"{prefix}_BASE_URL"),
        api_key=_env(f"{prefix}_API_KEY"),
        timeout_sec=timeout_sec,
    )


def _forced_route_decision(
    route_mode: str,
    targets: RouterTargets,
) -> RoutingDecision | None:
    normalized = route_mode.strip().lower()
    if normalized in ("", "auto"):
        return None
    if normalized == "default":
        return RoutingDecision(
            tier=ModelTier.DEFAULT,
            target=targets.default_target,
            reason=(
                "forced route override; "
                f"target={targets.default_target.supplier}/{targets.default_target.model}"
            ),
        )
    if normalized == "escalation":
        return RoutingDecision(
            tier=ModelTier.ESCALATION,
            target=targets.escalation_target,
            reason=(
                "forced route override; "
                f"target={targets.escalation_target.supplier}/{targets.escalation_target.model}"
            ),
        )
    if normalized == "fallback":
        return RoutingDecision(
            tier=ModelTier.DISASTER_FALLBACK,
            target=targets.disaster_fallback_target,
            reason=(
                "forced route override; "
                "used for disaster-path verification only; "
                f"target={targets.disaster_fallback_target.supplier}/"
                f"{targets.disaster_fallback_target.model}"
            ),
        )
    raise RuntimeError(f"invalid REAL_RUN_ROUTE: {route_mode}")


def main() -> int:
    try:
        default_supplier = _env("MODEL_DEFAULT_SUPPLIER")
        default_model = _env("MODEL_DEFAULT_MODEL")
        escalation_supplier = _env("MODEL_ESCALATION_SUPPLIER")
        escalation_model = _env("MODEL_ESCALATION_MODEL")
        fallback_supplier = _env("MODEL_FALLBACK_SUPPLIER")
        fallback_model = _env("MODEL_FALLBACK_MODEL")
        route_mode = _env_optional("REAL_RUN_ROUTE", "auto")
    except RuntimeError as exc:
        print(exc)
        return 2

    supplier_ids = {default_supplier, escalation_supplier, fallback_supplier}
    registry = ProviderRegistry.from_openai_compatible_configs(
        {supplier: _supplier_config(supplier) for supplier in supplier_ids}
    )

    topic = "讨论一个面向技术团队的multi-agent会议室MVP方案"
    ctx = DecisionContext(
        room_id="room_real_001",
        phase=MeetingPhase.DEBATE,
        signals=DecisionSignals(
            support=0.55,
            confidence=0.75,
            risk_penalty=0.10,
            margin_top1_top2=0.08,
            disagreement_index=0.42,
            rounds_without_progress=0,
            tool_failure_rate=0.02,
        ),
        metadata={"topic": topic},
    )

    planner = HybridPlanningStrategy()
    coordination = HybridCoordinationStrategy()
    consensus = HybridConsensusStrategy()
    router = HybridModelRouter(
        DisasterOnlyFallbackPolicy(),
        targets=RouterTargets(
            default_target=ModelTarget(default_supplier, default_model),
            escalation_target=ModelTarget(escalation_supplier, escalation_model),
            disaster_fallback_target=ModelTarget(fallback_supplier, fallback_model),
        ),
    )

    plan = planner.plan(ctx)
    route = _forced_route_decision(route_mode, router.targets) or router.route(ctx)
    provider = registry.get(route.target.supplier)
    model_name = route.target.model
    provider_base_url = getattr(provider, "base_url", "unknown")
    brief = build_meeting_brief(plan.topic, plan.next_focus)
    allowed_constraint_ids = {item["id"] for item in brief["constraints"]}
    system_prompt, user_prompt = build_host_prompts(brief)

    print("route_mode:", route_mode, flush=True)
    print("route:", route.tier.value, route.reason, flush=True)
    print("supplier:", route.target.supplier, flush=True)
    print("model:", model_name, flush=True)
    print("base_url:", provider_base_url, flush=True)

    try:
        host_resp = provider.generate(
            GenerateRequest(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                model=model_name,
            )
        )
        agenda = parse_host_agenda(host_resp.text, allowed_constraint_ids)
    except Exception as exc:
        raise RuntimeError(
            "real-run request failed: "
            f"supplier={route.target.supplier}; "
            f"model={model_name}; "
            f"base_url={provider_base_url}; "
            f"reason={exc}"
        ) from exc

    action = coordination.next_action(ctx)
    result = consensus.evaluate(ctx)

    print("host_action:", action.action_type.value, action.reason)
    print("host_raw_output:\n", host_resp.text)
    print(
        "host_agenda:\n",
        json.dumps(
            {
                "focus_points": [
                    {
                        "title": item.title,
                        "reason": item.reason,
                        "constraint_ids": item.constraint_ids,
                    }
                    for item in agenda.focus_points
                ],
                "open_questions": agenda.open_questions,
                "no_new_constraints": agenda.no_new_constraints,
            },
            ensure_ascii=False,
            indent=2,
        ),
    )
    print("consensus:", result.score, result.should_end, result.reason)
    return 0


if __name__ == "__main__":
    sys.exit(main())
