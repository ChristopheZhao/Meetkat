from __future__ import annotations

import asyncio
import json
import os
from typing import Annotated, Any, Mapping

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from decision_room.dev_env import PROJECT_ROOT, load_local_dotenv
from decision_room.orchestration import (
    CentralizedMASExecutor,
    DemoAgentExecutor,
    HeuristicRequirementPlanner,
    LLMRequirementPlanner,
    LLMRoomExecutor,
    PreRoomPlanningWorkflow,
    RequirementPlanningError,
    RequirementPlanningService,
    UnavailableRoomExecutor,
)
from decision_room.policies.fallback import FallbackConfig
from .room_runtime import RoomPreflightError, RoomRuntime, RoomStateError, RuntimeConfig


class AsyncDemoExecutor:
    """Async adapter so the demo topology can drive the real room runtime."""

    def __init__(self) -> None:
        self._delegate = DemoAgentExecutor()

    async def build_round(self, snapshot: Any, round_index: int):
        return self._delegate.build_round(snapshot, round_index)


def _build_requirement_planning_service(
    env: Mapping[str, str],
) -> RequirementPlanningService | None:
    planner_mode = env.get("DECISION_ROOM_PLANNER_MODE", "primary_with_fallback").strip().lower()
    if planner_mode == "primary":
        return RequirementPlanningService.from_mapping(env)
    if planner_mode == "fallback_only":
        return RequirementPlanningService(
            primary_planner=None,
            fallback_planner=HeuristicRequirementPlanner(),
            primary_unavailable_reason=(
                "primary requirement planner disabled by "
                "DECISION_ROOM_PLANNER_MODE=fallback_only"
            ),
        )
    if planner_mode == "primary_with_fallback":
        try:
            primary = LLMRequirementPlanner.from_mapping(env)
            primary_unavailable_reason = ""
        except RequirementPlanningError as exc:
            primary = None
            primary_unavailable_reason = str(exc)
        return RequirementPlanningService(
            primary_planner=primary,
            fallback_planner=HeuristicRequirementPlanner(),
            primary_unavailable_reason=primary_unavailable_reason,
        )
    raise RuntimeError(
        "unsupported DECISION_ROOM_PLANNER_MODE; expected one of "
        "'primary', 'fallback_only', or 'primary_with_fallback'"
    )


def _planner_readiness(
    planner_mode: str,
    requirement_planner: RequirementPlanningService | None,
    env: Mapping[str, str],
) -> dict[str, Any]:
    status = (
        requirement_planner.status()
        if requirement_planner is not None
        else {
            "primary_planner_ready": True,
            "fallback_planner_ready": False,
            "primary_unavailable_reason": "",
        }
    )
    return {
        "planner_mode": planner_mode,
        "planner_missing_env": _planner_missing_env(planner_mode, env),
        "planner_target": _planner_target_identity(planner_mode, env),
        **status,
    }


def _build_executor(env: Mapping[str, str]) -> tuple[Any, dict[str, Any]]:
    executor_mode = env.get("DECISION_ROOM_EXECUTOR", "llm").strip().lower()
    if executor_mode == "centralized":
        executor = CentralizedMASExecutor.from_mapping(env)
        topology_guardrails = {
            "route_visibility": (
                "central supervisor state, assignment contracts, and role work "
                "products are emitted through room events"
            ),
            "topology": "single_supervisor_shared_memory",
            **_executor_guardrails(env),
        }
        if isinstance(executor, UnavailableRoomExecutor):
            return executor, {
                "executor_mode": executor_mode,
                "executor_ready": False,
                "executor_reason": executor.reason,
                "executor_missing_env": _executor_missing_env("llm", env),
                "executor_targets": _executor_target_identities(env),
                "executor_guardrails": topology_guardrails,
            }
        return executor, {
            "executor_mode": executor_mode,
            "executor_ready": True,
            "executor_reason": "",
            "executor_missing_env": [],
            "executor_targets": _executor_target_identities(env),
            "executor_guardrails": topology_guardrails,
        }
    if executor_mode == "llm":
        executor = LLMRoomExecutor.from_mapping(env)
        if isinstance(executor, UnavailableRoomExecutor):
            return executor, {
                "executor_mode": executor_mode,
                "executor_ready": False,
                "executor_reason": executor.reason,
                "executor_missing_env": _executor_missing_env(executor_mode, env),
                "executor_targets": _executor_target_identities(env),
                "executor_guardrails": _executor_guardrails(env),
            }
        return executor, {
            "executor_mode": executor_mode,
            "executor_ready": True,
            "executor_reason": "",
            "executor_missing_env": [],
            "executor_targets": _executor_target_identities(env),
            "executor_guardrails": _executor_guardrails(env),
        }
    if executor_mode == "demo":
        return AsyncDemoExecutor(), {
            "executor_mode": executor_mode,
            "executor_ready": True,
            "executor_reason": "",
            "executor_missing_env": [],
            "executor_targets": {},
            "executor_guardrails": {},
        }
    raise RuntimeError(
        "unsupported DECISION_ROOM_EXECUTOR; expected 'centralized', 'llm', or 'demo'"
    )


def _planner_missing_env(planner_mode: str, env: Mapping[str, str]) -> list[str]:
    if planner_mode == "fallback_only":
        return []
    return _missing_target_env(env, "MODEL_DEFAULT")


def _planner_target_identity(
    planner_mode: str, env: Mapping[str, str]
) -> dict[str, str]:
    if planner_mode == "fallback_only":
        return {}
    return _target_identity(env, "MODEL_DEFAULT")


def _executor_missing_env(executor_mode: str, env: Mapping[str, str]) -> list[str]:
    if executor_mode not in {"llm", "centralized"}:
        return []
    missing = [
        *_missing_target_env(env, "MODEL_DEFAULT"),
        *_missing_target_env(env, "MODEL_ESCALATION"),
        *_missing_target_env(env, "MODEL_FALLBACK"),
    ]
    deduped: list[str] = []
    for item in missing:
        if item not in deduped:
            deduped.append(item)
    return deduped


def _missing_target_env(env: Mapping[str, str], target_prefix: str) -> list[str]:
    supplier_key = f"{target_prefix}_SUPPLIER"
    model_key = f"{target_prefix}_MODEL"
    missing: list[str] = []
    supplier = env.get(supplier_key, "").strip()
    model = env.get(model_key, "").strip()
    if not supplier:
        missing.append(supplier_key)
    if not model:
        missing.append(model_key)
    if not supplier:
        return missing
    supplier_prefix = supplier.upper()
    for key in (f"{supplier_prefix}_BASE_URL", f"{supplier_prefix}_API_KEY"):
        if not env.get(key, "").strip():
            missing.append(key)
    return missing


def _target_identity(env: Mapping[str, str], target_prefix: str) -> dict[str, str]:
    supplier = env.get(f"{target_prefix}_SUPPLIER", "").strip()
    model = env.get(f"{target_prefix}_MODEL", "").strip()
    identity: dict[str, str] = {}
    if supplier:
        identity["supplier"] = supplier
    if model:
        identity["model"] = model
    return identity


def _executor_target_identities(env: Mapping[str, str]) -> dict[str, dict[str, str]]:
    return {
        "default": _target_identity(env, "MODEL_DEFAULT"),
        "escalation": _target_identity(env, "MODEL_ESCALATION"),
        "fallback": _target_identity(env, "MODEL_FALLBACK"),
    }


def _executor_guardrails(env: Mapping[str, str]) -> dict[str, Any]:
    fallback_cfg = FallbackConfig()
    return {
        "request_timeout_default_sec": _env_int_optional("MODEL_TIMEOUT_SEC", 45, env),
        "provider_timeouts": _provider_timeout_identities(env),
        "transient_max_attempts": _env_int_optional("MODEL_REQUEST_MAX_ATTEMPTS", 2, env),
        "disaster_fallback_policy": {
            "policy": "disaster_only",
            "max_timeouts_before_fallback": fallback_cfg.max_timeouts_before_fallback,
            "max_rate_limits_before_fallback": fallback_cfg.max_rate_limits_before_fallback,
            "target": _target_identity(env, "MODEL_FALLBACK"),
        },
        "route_visibility": (
            "agent.message and agent.summary artifacts expose route tier, supplier, model, and reason"
        ),
    }


def _provider_timeout_identities(env: Mapping[str, str]) -> dict[str, dict[str, Any]]:
    providers: dict[str, dict[str, Any]] = {}
    for prefix in ("MODEL_DEFAULT", "MODEL_ESCALATION", "MODEL_FALLBACK"):
        supplier = env.get(f"{prefix}_SUPPLIER", "").strip()
        if not supplier:
            continue
        providers[supplier] = {
            "timeout_sec": _env_int_optional(
                f"{supplier.upper()}_TIMEOUT_SEC",
                _env_int_optional("MODEL_TIMEOUT_SEC", 45, env),
                env,
            )
        }
    return providers


def _env_int_optional(name: str, default: int, env: Mapping[str, str]) -> int:
    value = env.get(name, "").strip()
    if not value:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise RuntimeError(f"invalid int env: {name}={value}") from exc


def build_runtime_from_env(
    env: Mapping[str, str] | None = None,
    *,
    config: RuntimeConfig | None = None,
) -> RoomRuntime:
    if env is None:
        load_local_dotenv(PROJECT_ROOT / ".env")
    env_map = dict(os.environ if env is None else env)
    planner_mode = env_map.get("DECISION_ROOM_PLANNER_MODE", "primary_with_fallback").strip().lower()
    requirement_planner = _build_requirement_planning_service(env_map)
    executor, executor_status = _build_executor(env_map)
    readiness = {
        **_planner_readiness(planner_mode, requirement_planner, env_map),
        **executor_status,
    }
    # Use the env-aware workflow so LLMRolePlanner gets wired when MODEL_DEFAULT_*
    # is configured. Falls back to HeuristicRolePlanner only when env is missing
    # — readiness surfaces role_planner_kind so the operator can see the choice.
    planning_workflow = PreRoomPlanningWorkflow.from_mapping(env_map)
    if requirement_planner is not None:
        planning_workflow = PreRoomPlanningWorkflow(
            requirement_planner=requirement_planner,
            role_planner=planning_workflow._role_planner  # noqa: SLF001
            if planning_workflow.role_planner_kind == "llm"
            else None,
        )
    return RoomRuntime(
        executor=executor,
        config=config,
        requirement_planner=requirement_planner,
        planning_workflow=planning_workflow,
        runtime_readiness=readiness,
    )


runtime = build_runtime_from_env()

app = FastAPI(
    title="Decision Room Runtime",
    version="0.1.0",
    summary="Minimal room runtime with HTTP, SSE, and WebSocket transports.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class CreateRoomRequest(BaseModel):
    requirement: str = Field(min_length=8)
    mode: str = "agent_first"
    allow_planner_fallback: bool = False
    require_preflight_ready: bool = False
    entry_scope: str = ""
    operator_context: dict[str, Any] = Field(default_factory=dict)


class PreflightRoomRequest(BaseModel):
    requirement: str = Field(min_length=8)
    allow_planner_fallback: bool = False
    entry_scope: str = ""
    operator_context: dict[str, Any] = Field(default_factory=dict)


class HumanMessageRequest(BaseModel):
    text: str = Field(min_length=1)


def _get_room_or_404(room_id: str) -> dict:
    try:
        return runtime.get_snapshot(room_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _sse_payload(event: dict) -> str:
    data = json.dumps(event, ensure_ascii=False)
    return f"id: {event['room_seq']}\nevent: {event['event_type']}\ndata: {data}\n\n"


@app.on_event("shutdown")
async def shutdown_runtime() -> None:
    await runtime.close()


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/runtime-readiness")
async def runtime_readiness() -> dict[str, Any]:
    return runtime.runtime_readiness()


@app.get("/api/rooms")
async def list_rooms() -> list[dict]:
    return runtime.list_rooms()


@app.post("/api/rooms")
async def create_room(payload: CreateRoomRequest) -> dict:
    try:
        return await runtime.create_room(
            requirement=payload.requirement,
            mode=payload.mode,
            allow_planner_fallback=payload.allow_planner_fallback,
            require_preflight_ready=payload.require_preflight_ready,
            entry_scope=payload.entry_scope or None,
            operator_context=payload.operator_context,
        )
    except RequirementPlanningError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={
                "error": exc.error_code,
                "message": str(exc),
                "can_fallback": exc.can_fallback,
            },
        ) from exc
    except RoomPreflightError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "room_preflight_blocked",
                "message": str(exc),
                "preflight": exc.preflight_payload,
            },
        ) from exc


@app.post("/api/rooms/preflight")
async def preflight_room(payload: PreflightRoomRequest) -> dict:
    try:
        return runtime.preflight_room(
            requirement=payload.requirement,
            allow_planner_fallback=payload.allow_planner_fallback,
            entry_scope=payload.entry_scope or None,
            operator_context=payload.operator_context,
        )
    except RequirementPlanningError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={
                "error": exc.error_code,
                "message": str(exc),
                "can_fallback": exc.can_fallback,
            },
        ) from exc


@app.get("/api/rooms/{room_id}")
async def get_room(room_id: str) -> dict:
    return _get_room_or_404(room_id)


@app.get("/api/rooms/{room_id}/snapshot")
async def get_room_snapshot(room_id: str) -> dict:
    return _get_room_or_404(room_id)


@app.get("/api/rooms/{room_id}/replay")
async def replay_room(
    room_id: str, after_seq: Annotated[int, Query(ge=0)] = 0
) -> list[dict]:
    _get_room_or_404(room_id)
    return runtime.replay(room_id, after_seq)


@app.post("/api/rooms/{room_id}/human-message")
async def post_human_message(room_id: str, payload: HumanMessageRequest) -> dict:
    _get_room_or_404(room_id)
    try:
        return await runtime.post_human_message(room_id, payload.text)
    except RoomStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/rooms/{room_id}/human-override")
async def post_human_override(room_id: str, payload: HumanMessageRequest) -> dict:
    _get_room_or_404(room_id)
    try:
        return await runtime.post_human_override(room_id, payload.text)
    except RoomStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/rooms/{room_id}/events")
async def stream_room_events(
    room_id: str, after_seq: Annotated[int, Query(ge=0)] = 0
) -> StreamingResponse:
    _get_room_or_404(room_id)

    async def event_stream() -> asyncio.AsyncIterator[str]:
        for event in runtime.replay(room_id, after_seq):
            yield _sse_payload(event)

        queue = await runtime.register_subscriber(room_id)
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                    yield _sse_payload(event)
                except TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            await runtime.unregister_subscriber(room_id, queue)

    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    return StreamingResponse(event_stream(), media_type="text/event-stream", headers=headers)


@app.websocket("/ws/rooms/{room_id}")
async def websocket_room(
    websocket: WebSocket, room_id: str, last_acked_seq: int = 0
) -> None:
    try:
        _get_room_or_404(room_id)
    except HTTPException:
        await websocket.close(code=4404)
        return

    await websocket.accept()
    for event in runtime.replay(room_id, last_acked_seq):
        await websocket.send_json(event)

    queue = await runtime.register_subscriber(room_id)
    try:
        while True:
            event = await queue.get()
            await websocket.send_json(event)
    except WebSocketDisconnect:
        pass
    finally:
        await runtime.unregister_subscriber(room_id, queue)
