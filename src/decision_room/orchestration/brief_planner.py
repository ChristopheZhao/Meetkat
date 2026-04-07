from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Mapping, Protocol

from decision_room.providers import (
    GenerateRequest,
    ProviderConfig,
    ProviderHTTPError,
    ProviderNetworkError,
    ProviderRegistry,
    ProviderTimeoutError,
)

from .real_run_contract import extract_json_object

SYSTEM_CONSTRAINTS = [
    "Meeting UI must be driven by real room events rather than local mock state.",
    "Room runtime must preserve replay, resume, and human override semantics.",
    "WebSocket is the primary transport and SSE is the read-only fallback.",
]


@dataclass(frozen=True)
class RoomStartContractDraft:
    operator_required_inputs: list[str] = field(default_factory=list)
    contextual_open_questions: list[str] = field(default_factory=list)

    def to_payload(self) -> dict[str, object]:
        return {
            "operator_required_inputs": list(self.operator_required_inputs),
            "contextual_open_questions": list(self.contextual_open_questions),
        }

    @classmethod
    def from_payload(cls, payload: object) -> "RoomStartContractDraft":
        if not isinstance(payload, dict):
            raise RequirementPlanningError(
                "room_start_contract must be an object",
                error_code="planner_invalid_schema",
                status_code=502,
                can_fallback=True,
            )
        return cls(
            operator_required_inputs=_require_string_list(
                payload.get("operator_required_inputs", []),
                "room_start_contract.operator_required_inputs",
                allow_empty=True,
            ),
            contextual_open_questions=_require_string_list(
                payload.get("contextual_open_questions", []),
                "room_start_contract.contextual_open_questions",
                allow_empty=True,
            ),
        )


@dataclass(frozen=True)
class MeetingBrief:
    requirement: str
    topic: str
    goal: str
    constraints: list[str]
    open_questions: list[str]
    current_focus: str
    room_start_contract: RoomStartContractDraft = field(default_factory=RoomStartContractDraft)
    brief_source: str = "agent"
    brief_source_reason: str = ""


class RequirementPlanningError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        error_code: str,
        status_code: int,
        can_fallback: bool = False,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.status_code = status_code
        self.can_fallback = can_fallback


class RequirementPlanner(Protocol):
    def plan_requirement(self, requirement: str) -> MeetingBrief:
        ...


class RequirementPlannerFallbackPolicy:
    """Fallback can run only when the caller explicitly enables it."""

    def should_fallback(self, exc: RequirementPlanningError) -> bool:
        return exc.can_fallback


class RequirementPlanningService:
    def __init__(
        self,
        primary_planner: RequirementPlanner | None,
        fallback_planner: RequirementPlanner | None = None,
        fallback_policy: RequirementPlannerFallbackPolicy | None = None,
        primary_unavailable_reason: str = "",
    ) -> None:
        self._primary_planner = primary_planner
        self._fallback_planner = fallback_planner
        self._fallback_policy = fallback_policy or RequirementPlannerFallbackPolicy()
        self._primary_unavailable_reason = primary_unavailable_reason

    @classmethod
    def from_env(cls) -> "RequirementPlanningService":
        return cls.from_mapping(os.environ)

    @classmethod
    def from_mapping(cls, env: Mapping[str, str]) -> "RequirementPlanningService":
        try:
            primary = LLMRequirementPlanner.from_mapping(env)
        except RequirementPlanningError as exc:
            return cls(
                primary_planner=None,
                primary_unavailable_reason=str(exc),
            )
        return cls(primary_planner=primary)

    def plan_requirement(
        self, requirement: str, *, allow_fallback: bool = False
    ) -> MeetingBrief:
        normalized = _normalize(requirement)

        if self._primary_planner is None:
            return self._use_primary_failure(
                normalized,
                RequirementPlanningError(
                    self._primary_unavailable_reason or "primary planner unavailable",
                    error_code="planner_unavailable",
                    status_code=503,
                    can_fallback=self._fallback_planner is not None,
                ),
                allow_fallback=allow_fallback,
            )

        try:
            brief = self._primary_planner.plan_requirement(normalized)
        except RequirementPlanningError as exc:
            return self._use_primary_failure(
                normalized,
                exc,
                allow_fallback=allow_fallback,
            )
        except Exception as exc:
            planner_error = RequirementPlanningError(
                f"requirement planner failed: {exc}",
                error_code="planner_failure",
                status_code=502,
                can_fallback=self._fallback_planner is not None,
            )
            return self._use_primary_failure(
                normalized,
                planner_error,
                allow_fallback=allow_fallback,
            )

        return _merge_system_constraints(
            _with_source(brief, brief_source="agent", brief_source_reason="")
        )

    def status(self) -> dict[str, object]:
        return {
            "primary_planner_ready": self._primary_planner is not None,
            "fallback_planner_ready": self._fallback_planner is not None,
            "primary_unavailable_reason": self._primary_unavailable_reason,
        }

    def _use_primary_failure(
        self,
        requirement: str,
        exc: RequirementPlanningError,
        *,
        allow_fallback: bool,
    ) -> MeetingBrief:
        if not allow_fallback or self._fallback_planner is None:
            raise exc
        if not self._fallback_policy.should_fallback(exc):
            raise exc
        fallback_reason = f"explicit fallback after planner failure: {exc}"
        return _merge_system_constraints(
            _with_source(
                self._fallback_planner.plan_requirement(requirement),
                brief_source="fallback",
                brief_source_reason=fallback_reason,
            )
        )


class LLMRequirementPlanner:
    def __init__(self, registry: ProviderRegistry, supplier: str, model: str) -> None:
        self._registry = registry
        self._supplier = supplier
        self._model = model

    @classmethod
    def from_env(cls) -> "LLMRequirementPlanner":
        return cls.from_mapping(os.environ)

    @classmethod
    def from_mapping(cls, env: Mapping[str, str]) -> "LLMRequirementPlanner":
        supplier = _env("MODEL_DEFAULT_SUPPLIER", env)
        model = _env("MODEL_DEFAULT_MODEL", env)
        registry = ProviderRegistry.from_openai_compatible_configs(
            {supplier: _provider_config(supplier, env)}
        )
        return cls(registry=registry, supplier=supplier, model=model)

    def plan_requirement(self, requirement: str) -> MeetingBrief:
        system_prompt, user_prompt = build_requirement_planner_prompts(requirement)
        provider = self._registry.get(self._supplier)
        try:
            response = provider.generate(
                GenerateRequest(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    model=self._model,
                    temperature=0.1,
                )
            )
        except ProviderTimeoutError as exc:
            raise RequirementPlanningError(
                f"requirement planner timed out: {exc}",
                error_code="planner_timeout",
                status_code=504,
                can_fallback=True,
            ) from exc
        except (ProviderHTTPError, ProviderNetworkError) as exc:
            raise RequirementPlanningError(
                f"requirement planner request failed: {exc}",
                error_code="planner_upstream_error",
                status_code=502,
                can_fallback=True,
            ) from exc

        try:
            return parse_requirement_planner_response(requirement, response.text)
        except RequirementPlanningError:
            raise
        except Exception as exc:
            raise RequirementPlanningError(
                f"requirement planner returned invalid output: {exc}",
                error_code="planner_invalid_output",
                status_code=502,
                can_fallback=True,
            ) from exc


class HeuristicRequirementPlanner:
    """Transparent stub fallback for local debugging.

    This planner does not attempt to understand the requirement. It only
    creates a visible placeholder brief so fallback mode is explicit rather
    than pretending the system performed semantic decomposition.
    """

    def plan_requirement(self, requirement: str) -> MeetingBrief:
        normalized = _normalize(requirement)
        return MeetingBrief(
            requirement=normalized,
            topic=_truncate_requirement(normalized),
            goal=(
                "Primary requirement planner is unavailable. Restore the agent planner "
                "before trusting any derived meeting brief."
            ),
            constraints=[
                "This room is running on an explicit fallback stub and must not be treated as a validated plan."
            ],
            open_questions=[
                "What blocked the primary requirement planner, and should the room creation be retried after fixing it?"
            ],
            current_focus="diagnose the planner failure before proceeding with agent-led discussion",
            room_start_contract=RoomStartContractDraft(
                operator_required_inputs=[],
                contextual_open_questions=[
                    "What blocked the primary requirement planner, and should the room creation be retried after fixing it?"
                ],
            ),
        )


def build_meeting_brief_from_requirement(requirement: str) -> MeetingBrief:
    """Build a diagnostic fallback brief without semantic inference."""
    return _merge_system_constraints(
        _with_source(
            HeuristicRequirementPlanner().plan_requirement(requirement),
            brief_source="fallback",
            brief_source_reason="explicit diagnostic stub",
        )
    )


def build_requirement_planner_prompts(requirement: str) -> tuple[str, str]:
    schema = {
        "topic": "meeting topic",
        "goal": "what the meeting should produce",
        "constraints": ["hard boundary grounded in the requirement or system invariants"],
        "open_questions": ["question to ask when information is missing"],
        "current_focus": "what the first round should focus on",
        "room_start_contract": {
            "operator_required_inputs": [
                "input the operator must provide before room start can proceed"
            ],
            "contextual_open_questions": [
                "question the room can keep exploring after room start"
            ],
        },
    }
    system_prompt = (
        "You are the requirement planning agent for an agentic multi-agent decision room. "
        "Turn one user requirement into a meeting brief for downstream agents. "
        "The meeting brief is the intelligent entry point; do not reduce the requirement to a rigid workflow. "
        "Do not invent hard constraints that are not grounded in the requirement or the system invariants provided. "
        "If information is missing, put it into open_questions instead of fabricating assumptions. "
        "Return exactly one JSON object and nothing else."
    )
    user_prompt = (
        "User requirement:\n"
        f"{requirement}\n\n"
        "System invariants that must remain true:\n"
        f"{json.dumps(SYSTEM_CONSTRAINTS, ensure_ascii=False, indent=2)}\n\n"
        "Task:\n"
        "- Produce a concise topic.\n"
        "- Produce a concrete meeting goal.\n"
        "- Extract only real hard constraints from the requirement and invariants.\n"
        "- If the requirement is underspecified, list up to 3 raw open_questions worth tracking.\n"
        "- Classify room-start unknowns into room_start_contract.operator_required_inputs versus room_start_contract.contextual_open_questions.\n"
        "- Express each room_start_contract item as a short declarative prerequisite or contextual unknown, not as a conversational question.\n"
        "- Put only true pre-room blockers into operator_required_inputs.\n"
        "- Put questions that can stay inside the meeting into contextual_open_questions.\n"
        "- Produce the first-round current_focus for the host agent.\n\n"
        "Output schema example:\n"
        f"{json.dumps(schema, ensure_ascii=False, indent=2)}"
    )
    return system_prompt, user_prompt


def parse_requirement_planner_response(requirement: str, raw: str) -> MeetingBrief:
    try:
        payload = json.loads(extract_json_object(raw))
    except Exception as exc:
        raise RequirementPlanningError(
            f"requirement planner response does not contain valid JSON: {exc}",
            error_code="planner_invalid_json",
            status_code=502,
            can_fallback=True,
        ) from exc

    topic = _require_non_empty(payload.get("topic"), "topic")
    goal = _require_non_empty(payload.get("goal"), "goal")
    current_focus = _require_non_empty(payload.get("current_focus"), "current_focus")
    constraints = _require_string_list(payload.get("constraints"), "constraints")
    open_questions = _require_string_list(
        payload.get("open_questions", []),
        "open_questions",
        allow_empty=True,
    )
    room_start_contract = RoomStartContractDraft.from_payload(
        payload.get("room_start_contract", {})
    )
    if len(open_questions) > 3:
        raise RequirementPlanningError(
            "open_questions must contain at most 3 items",
            error_code="planner_invalid_schema",
            status_code=502,
            can_fallback=True,
        )
    if len(room_start_contract.operator_required_inputs) > 3:
        raise RequirementPlanningError(
            "room_start_contract.operator_required_inputs must contain at most 3 items",
            error_code="planner_invalid_schema",
            status_code=502,
            can_fallback=True,
        )
    if len(room_start_contract.contextual_open_questions) > 3:
        raise RequirementPlanningError(
            "room_start_contract.contextual_open_questions must contain at most 3 items",
            error_code="planner_invalid_schema",
            status_code=502,
            can_fallback=True,
        )

    return MeetingBrief(
        requirement=_normalize(requirement),
        topic=topic,
        goal=goal,
        constraints=constraints,
        open_questions=open_questions,
        current_focus=current_focus,
        room_start_contract=room_start_contract,
    )


def _merge_system_constraints(brief: MeetingBrief) -> MeetingBrief:
    merged: list[str] = []
    for item in [*brief.constraints, *SYSTEM_CONSTRAINTS]:
        normalized = item.strip()
        if normalized and normalized not in merged:
            merged.append(normalized)
        if len(merged) >= 6:
            break
    return MeetingBrief(
        requirement=brief.requirement,
        topic=brief.topic,
        goal=brief.goal,
        constraints=merged,
        open_questions=brief.open_questions,
        current_focus=brief.current_focus,
        room_start_contract=brief.room_start_contract,
        brief_source=brief.brief_source,
        brief_source_reason=brief.brief_source_reason,
    )


def _with_source(
    brief: MeetingBrief, *, brief_source: str, brief_source_reason: str
) -> MeetingBrief:
    return MeetingBrief(
        requirement=brief.requirement,
        topic=brief.topic,
        goal=brief.goal,
        constraints=brief.constraints,
        open_questions=brief.open_questions,
        current_focus=brief.current_focus,
        room_start_contract=brief.room_start_contract,
        brief_source=brief_source,
        brief_source_reason=brief_source_reason,
    )


def _normalize(text: str) -> str:
    normalized = re.sub(r"\s+", " ", text or "").strip()
    if not normalized:
        raise RequirementPlanningError(
            "requirement must be non-empty",
            error_code="invalid_requirement",
            status_code=400,
            can_fallback=False,
        )
    return normalized


def _truncate_requirement(text: str, limit: int = 80) -> str:
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3].rstrip()}..."


def _require_non_empty(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RequirementPlanningError(
            f"{field_name} must be non-empty",
            error_code="planner_invalid_schema",
            status_code=502,
            can_fallback=True,
        )
    return value.strip()


def _require_string_list(
    value: object, field_name: str, *, allow_empty: bool = False
) -> list[str]:
    if not isinstance(value, list):
        raise RequirementPlanningError(
            f"{field_name} must be a list",
            error_code="planner_invalid_schema",
            status_code=502,
            can_fallback=True,
        )

    items: list[str] = []
    for raw_item in value:
        if not isinstance(raw_item, str) or not raw_item.strip():
            raise RequirementPlanningError(
                f"{field_name} contains an invalid item",
                error_code="planner_invalid_schema",
                status_code=502,
                can_fallback=True,
            )
        items.append(raw_item.strip())

    if not allow_empty and not items:
        raise RequirementPlanningError(
            f"{field_name} must contain at least one item",
            error_code="planner_invalid_schema",
            status_code=502,
            can_fallback=True,
        )
    return items


def _env(name: str, env: Mapping[str, str] | None = None) -> str:
    value = (os.getenv(name) if env is None else env.get(name))
    if value:
        return value
    raise RequirementPlanningError(
        f"missing env: {name}",
        error_code="planner_config_missing",
        status_code=503,
        can_fallback=True,
    )


def _env_int_optional(name: str, default: int, env: Mapping[str, str] | None = None) -> int:
    value = (os.getenv(name) if env is None else env.get(name))
    if not value:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise RequirementPlanningError(
            f"invalid int env: {name}={value}",
            error_code="planner_config_invalid",
            status_code=503,
            can_fallback=True,
        ) from exc


def _provider_config(
    supplier: str, env: Mapping[str, str] | None = None
) -> ProviderConfig:
    prefix = supplier.upper()
    timeout_sec = _env_int_optional(
        f"{prefix}_TIMEOUT_SEC",
        _env_int_optional("MODEL_TIMEOUT_SEC", 45, env),
        env,
    )
    return ProviderConfig(
        supplier=supplier,
        base_url=_env(f"{prefix}_BASE_URL", env),
        api_key=_env(f"{prefix}_API_KEY", env),
        timeout_sec=timeout_sec,
    )
