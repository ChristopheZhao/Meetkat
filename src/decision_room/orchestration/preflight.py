from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping


BLOCKED_DEPENDENCY_PATTERNS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "provider_identity",
        "operator_preflight",
        (
            "provider identity",
            "which provider",
            "provider selection",
            "specific provider",
            "real provider",
            "provider identifier",
            "planner provider",
            "executor provider",
        ),
    ),
    (
        "success_criteria",
        "operator_preflight",
        (
            "success criteria",
            "success definition",
            "validation success",
            "pass/fail",
            "assertion outcome",
            "what counts as success",
            "validation run",
        ),
    ),
    (
        "validation_scenario",
        "operator_preflight",
        (
            "test scenario",
            "input payload",
            "trigger payload",
            "specific scenario",
            "specific test",
            "host-led topology behavior",
            "trigger and validate",
        ),
    ),
    (
        "binding_readiness",
        "operator_preflight",
        (
            "binding readiness",
            "bound and ready",
            "pre-planning binding",
            "before planning.completed",
            "readiness signal",
            "health check response",
        ),
    ),
    (
        "transport_contract",
        "operator_preflight",
        (
            "websocket fidelity",
            "transport fidelity",
            "silent sse fallback",
            "websocket-to-sse",
            "primary transport",
            "c4 compliance",
            "websocket appears connected",
        ),
    ),
    (
        "projection_contract",
        "operator_preflight",
        (
            "current_turns projection",
            "current_turns",
            "projection structure",
            "specific fields",
            "role/task fields",
        ),
    ),
    (
        "credential_context",
        "operator_preflight",
        (
            "credential",
            "api key",
            "authentication",
            "auth context",
            "credential context",
        ),
    ),
    (
        "environment_readiness",
        "operator_preflight",
        (
            "environment provisioning",
            "environment readiness",
            "runtime configuration",
            "network configuration",
            "transport configuration",
            "base url",
        ),
    ),
    (
        "external_dependency",
        "planning_or_preflight",
        (
            "external dependency",
            "external input",
            "prerequisite",
            "cannot begin without",
            "hard prerequisite",
        ),
    ),
    (
        "runtime_guardrails",
        "operator_preflight",
        (
            "timeout duration",
            "timeout configuration",
            "timeout guardrail",
            "escalation timeout",
            "request timeout",
            "silent fallback",
            "silent minimax fallback",
            "minimax fallback",
            "fallback guardrail",
            "fallback threshold",
        ),
    ),
)

VALIDATION_SENSITIVE_KEYWORDS = (
    "validate",
    "validation",
    "verify",
    "verification",
    "smoke",
    "real provider",
    "provider-backed",
    "demo mode",
    "fallback",
    "readiness",
    "confirm",
    "prove",
)

ALWAYS_HARD_CATEGORIES = {
    "credential_context",
    "environment_readiness",
}
VALIDATION_HARD_CATEGORIES = {
    "provider_identity",
    "success_criteria",
    "external_dependency",
}

CATEGORY_OPERATOR_INPUT_GAPS: dict[str, str] = {
    "provider_identity": "specific provider identity or target supplier/model expected for this room",
    "success_criteria": "explicit pass/fail or success criteria for this validation run",
    "validation_scenario": (
        "specific trigger payload or validation scenario when the default entry-scope scenario is not sufficient"
    ),
    "binding_readiness": (
        "explicit pre-room binding readiness contract if this entry scope cannot rely on the default readiness signal"
    ),
    "transport_contract": (
        "explicit browser transport verification requirement if this room must prove live transport degradation behavior"
    ),
    "projection_contract": (
        "explicit projection acceptance rule if current_turns must satisfy stricter shape requirements than the default contract"
    ),
    "credential_context": "credential or authentication context required by this room",
    "external_dependency": "external prerequisite or dependency that must be available before room start",
    "runtime_guardrails": (
        "explicit timeout/retry/fallback guardrail override if the default runtime guardrails are not sufficient"
    ),
}


@dataclass(frozen=True)
class DependencyCategoryMatch:
    id: str
    matched_signal: str
    recommended_surface: str
    hard_prerequisite: bool

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExternalDependencyPreflight:
    room_start_ready: bool
    validation_sensitive: bool
    runtime_bootstrap_ready: bool
    hard_prerequisites: list[str] = field(default_factory=list)
    contextual_open_questions: list[str] = field(default_factory=list)
    missing_operator_inputs: list[str] = field(default_factory=list)
    categories: list[DependencyCategoryMatch] = field(default_factory=list)
    recommended_surface: str = ""
    root_cause_hypothesis: str = ""

    def to_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["categories"] = [item.to_payload() for item in self.categories]
        return payload


@dataclass(frozen=True)
class RoomStartContract:
    room_start_ready: bool
    runtime_bootstrap_ready: bool
    contextual_open_questions: list[str] = field(default_factory=list)
    system_blockers: list[str] = field(default_factory=list)
    known_context: list[str] = field(default_factory=list)
    recommended_surface: str = ""
    root_cause_hypothesis: str = ""

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


def build_room_start_contract(
    *,
    contextual_open_questions: list[str],
    runtime_readiness: Mapping[str, Any] | None = None,
    operator_context: Mapping[str, Any] | None = None,
    allow_planner_fallback: bool = False,
) -> RoomStartContract:
    runtime_payload = runtime_readiness or {}
    operator_payload = operator_context or {}
    runtime_bootstrap_ready = _runtime_bootstrap_ready(
        runtime_payload,
        allow_planner_fallback=allow_planner_fallback,
    )
    system_blockers = _runtime_readiness_prerequisites(
        runtime_payload,
        allow_planner_fallback=allow_planner_fallback,
    )
    remaining_contextual_questions = [
        item
        for item in _normalized_items(contextual_open_questions)
        if not question_answered_by_context(
            item,
            runtime_context=runtime_payload,
            operator_context=operator_payload,
        )
    ]
    room_start_ready = runtime_bootstrap_ready and not system_blockers
    if system_blockers:
        root_cause_hypothesis = (
            "runtime bootstrap is not ready; fix planner/executor readiness before treating "
            "the room-start contract as clear"
        )
        recommended_surface = "runtime_readiness"
    else:
        root_cause_hypothesis = (
            "room-start contract is clear; remaining questions are contextual and the in-meeting "
            "supervisor/specialists resolve them via the human-message channel"
        )
        recommended_surface = "room_start"
    return RoomStartContract(
        room_start_ready=room_start_ready,
        runtime_bootstrap_ready=runtime_bootstrap_ready,
        contextual_open_questions=remaining_contextual_questions,
        system_blockers=system_blockers,
        known_context=_known_context_lines(runtime_payload, operator_payload),
        recommended_surface=recommended_surface,
        root_cause_hypothesis=root_cause_hypothesis,
    )


def assess_external_dependency_preflight(
    *,
    requirement: str,
    meeting_objective: str,
    open_questions: list[str],
    runtime_readiness: Mapping[str, Any] | None = None,
    operator_context: Mapping[str, Any] | None = None,
    allow_planner_fallback: bool = False,
) -> ExternalDependencyPreflight:
    validation_sensitive = _is_validation_sensitive(requirement, meeting_objective)
    runtime_bootstrap_ready = _runtime_bootstrap_ready(
        runtime_readiness or {},
        allow_planner_fallback=allow_planner_fallback,
    )

    categories: list[DependencyCategoryMatch] = []
    hard_prerequisites: list[str] = []
    contextual_open_questions: list[str] = []

    runtime_blockers = _runtime_readiness_prerequisites(
        runtime_readiness or {},
        allow_planner_fallback=allow_planner_fallback,
    )
    if runtime_blockers:
        hard_prerequisites.extend(runtime_blockers)
        categories.append(
            DependencyCategoryMatch(
                id="runtime_bootstrap",
                matched_signal="runtime_readiness",
                recommended_surface="operator_preflight",
                hard_prerequisite=True,
            )
        )

    for question in open_questions:
        normalized_question = str(question).strip()
        if not normalized_question:
            continue
        question_categories = _classify_dependency_texts(
            [normalized_question],
            hard_prerequisite_default=validation_sensitive,
        )
        if _question_answered_by_context(
            normalized_question,
            runtime_readiness or {},
            operator_context or {},
        ):
            continue
        question_categories = _apply_runtime_context(
            question_categories,
            runtime_readiness or {},
        )
        if any(item.hard_prerequisite for item in question_categories):
            hard_prerequisites.append(normalized_question)
        else:
            contextual_open_questions.append(normalized_question)
        categories = _merge_categories(categories, question_categories)

    missing_operator_inputs = _missing_operator_inputs(categories)

    if not hard_prerequisites and not contextual_open_questions:
        return ExternalDependencyPreflight(
            room_start_ready=runtime_bootstrap_ready,
            validation_sensitive=validation_sensitive,
            runtime_bootstrap_ready=runtime_bootstrap_ready,
            missing_operator_inputs=missing_operator_inputs,
            recommended_surface="room_start" if runtime_bootstrap_ready else "operator_preflight",
            root_cause_hypothesis=(
                ""
                if runtime_bootstrap_ready
                else "runtime bootstrap is not ready; fix planner/executor env before room creation"
            ),
        )

    if hard_prerequisites:
        root_cause_hypothesis = (
            "hard external prerequisites exist before room start; expose them as an "
            "operator-visible preflight gate instead of rediscovering them in-room"
        )
        recommended_surface = "operator_preflight"
    else:
        root_cause_hypothesis = (
            "open questions exist, but they currently look contextual rather than blocking; "
            "they can stay inside the room planning surface"
        )
        recommended_surface = "room_start"

    return ExternalDependencyPreflight(
        room_start_ready=runtime_bootstrap_ready and not hard_prerequisites,
        validation_sensitive=validation_sensitive,
        runtime_bootstrap_ready=runtime_bootstrap_ready,
        hard_prerequisites=hard_prerequisites,
        contextual_open_questions=contextual_open_questions,
        missing_operator_inputs=missing_operator_inputs,
        categories=categories,
        recommended_surface=recommended_surface,
        root_cause_hypothesis=root_cause_hypothesis,
    )


def question_answered_by_context(
    question: str,
    *,
    runtime_context: Mapping[str, Any] | None = None,
    operator_context: Mapping[str, Any] | None = None,
) -> bool:
    # Legitimate callers: build_room_start_contract / assess_external_dependency_preflight
    # (infra preflight) and room_executor's in-meeting question filtering. Not a
    # clarification gate — clarifications happen inside the meeting.
    normalized_question = str(question).strip()
    if not normalized_question:
        return False
    return _question_answered_by_context(
        normalized_question,
        runtime_context or {},
        operator_context or {},
    )


def classify_blocked_dependency_texts(text_sources: list[str]) -> dict[str, Any]:
    categories = _classify_dependency_texts(
        text_sources,
        hard_prerequisite_default=True,
    )
    recommended_surface = (
        "operator_preflight"
        if any(item.recommended_surface == "operator_preflight" for item in categories)
        else ",".join(
            item.recommended_surface
            for item in categories
            if item.recommended_surface
        )
    )
    if categories:
        root_cause_hypothesis = (
            "blocked dependencies are dominated by external prerequisites that should be "
            "surfaced before room creation through an operator-visible preflight gate"
        )
    else:
        root_cause_hypothesis = (
            "the meeting ended blocked, but the reason text did not match the current "
            "dependency taxonomy; extend the taxonomy before changing behavior"
        )
    return {
        "has_blocked_dependencies": bool(categories),
        "categories": [item.to_payload() for item in categories],
        "recommended_surface": recommended_surface,
        "root_cause_hypothesis": root_cause_hypothesis if categories else root_cause_hypothesis,
    }


def _classify_dependency_texts(
    text_sources: list[str],
    *,
    hard_prerequisite_default: bool,
) -> list[DependencyCategoryMatch]:
    combined_text = " \n".join(str(item).strip() for item in text_sources if str(item).strip()).lower()
    if not combined_text:
        return []

    categories: list[DependencyCategoryMatch] = []
    for category_id, recommended_surface, patterns in BLOCKED_DEPENDENCY_PATTERNS:
        if category_id == "external_dependency" and categories:
            continue
        matched_pattern = next((pattern for pattern in patterns if pattern in combined_text), "")
        if not matched_pattern:
            continue
        hard_prerequisite = (
            category_id in ALWAYS_HARD_CATEGORIES
            or (
                hard_prerequisite_default
                and category_id in VALIDATION_HARD_CATEGORIES
            )
        )
        categories.append(
            DependencyCategoryMatch(
                id=category_id,
                matched_signal=matched_pattern,
                recommended_surface=recommended_surface,
                hard_prerequisite=hard_prerequisite,
            )
        )
    return categories


def _merge_categories(
    existing: list[DependencyCategoryMatch],
    incoming: list[DependencyCategoryMatch],
) -> list[DependencyCategoryMatch]:
    merged = list(existing)
    seen = {item.id for item in existing}
    for item in incoming:
        if item.id in seen:
            continue
        merged.append(item)
        seen.add(item.id)
    return merged


def _missing_operator_inputs(
    categories: list[DependencyCategoryMatch],
) -> list[str]:
    missing: list[str] = []
    for item in categories:
        detail = CATEGORY_OPERATOR_INPUT_GAPS.get(item.id, "").strip()
        if detail and detail not in missing:
            missing.append(detail)
    return missing


def _is_validation_sensitive(requirement: str, meeting_objective: str) -> bool:
    corpus = f"{requirement} {meeting_objective}".lower()
    return any(keyword in corpus for keyword in VALIDATION_SENSITIVE_KEYWORDS)


def _question_answered_by_context(
    question: str,
    runtime_readiness: Mapping[str, Any],
    operator_context: Mapping[str, Any],
) -> bool:
    evidence_lines = _context_evidence_lines(runtime_readiness, operator_context)
    normalized_question = _normalize_text(question)
    if any(normalized_question == _normalize_text(item) for item in evidence_lines):
        return True
    question_tokens = _semantic_tokens(question)
    if not question_tokens:
        return False
    required_overlap = 1 if len(question_tokens) <= 2 else 2
    for item in evidence_lines:
        evidence_tokens = _semantic_tokens(item)
        if len(question_tokens & evidence_tokens) >= required_overlap:
            return True
    return False


def _apply_runtime_context(
    categories: list[DependencyCategoryMatch],
    runtime_readiness: Mapping[str, Any],
) -> list[DependencyCategoryMatch]:
    adjusted: list[DependencyCategoryMatch] = []
    provider_context_known = _runtime_provider_identity_known(runtime_readiness)
    for item in categories:
        if item.id == "provider_identity" and provider_context_known:
            adjusted.append(
                DependencyCategoryMatch(
                    id=item.id,
                    matched_signal=item.matched_signal,
                    recommended_surface="room_start",
                    hard_prerequisite=False,
                )
            )
            continue
        adjusted.append(item)
    return adjusted


def _runtime_bootstrap_ready(
    runtime_readiness: Mapping[str, Any],
    *,
    allow_planner_fallback: bool,
) -> bool:
    planner_ready = bool(runtime_readiness.get("primary_planner_ready", True))
    if not planner_ready and allow_planner_fallback:
        planner_ready = bool(runtime_readiness.get("fallback_planner_ready", False))
    executor_ready = bool(runtime_readiness.get("executor_ready", True))
    return planner_ready and executor_ready


def _runtime_provider_identity_known(runtime_readiness: Mapping[str, Any]) -> bool:
    planner_target = runtime_readiness.get("planner_target")
    executor_targets = runtime_readiness.get("executor_targets")
    planner_known = _target_identity_known(planner_target)
    if not planner_known:
        return False
    if not isinstance(executor_targets, Mapping):
        return False
    return _target_identity_known(executor_targets.get("default"))


def _runtime_guardrails_known(runtime_readiness: Mapping[str, Any]) -> bool:
    guardrails = runtime_readiness.get("executor_guardrails")
    if not isinstance(guardrails, Mapping):
        return False
    provider_timeouts = guardrails.get("provider_timeouts")
    if isinstance(provider_timeouts, Mapping):
        for payload in provider_timeouts.values():
            if isinstance(payload, Mapping) and _context_value_present(payload.get("timeout_sec")):
                return True
    if _context_value_present(guardrails.get("request_timeout_default_sec")):
        return True
    if _context_value_present(guardrails.get("transient_max_attempts")):
        return True
    fallback_policy = guardrails.get("disaster_fallback_policy")
    if isinstance(fallback_policy, Mapping) and (
        _context_value_present(fallback_policy.get("policy"))
        or _context_value_present(fallback_policy.get("max_timeouts_before_fallback"))
        or _context_value_present(fallback_policy.get("max_rate_limits_before_fallback"))
    ):
        return True
    return _context_value_present(guardrails.get("route_visibility"))


def _target_identity_known(payload: Any) -> bool:
    return (
        isinstance(payload, Mapping)
        and bool(str(payload.get("supplier", "")).strip())
        and bool(str(payload.get("model", "")).strip())
    )


def _context_value_present(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, Mapping):
        return any(_context_value_present(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_context_value_present(item) for item in value)
    return bool(value)


def _normalized_items(values: list[str]) -> list[str]:
    normalized: list[str] = []
    for item in values:
        text = str(item).strip()
        if text and text not in normalized:
            normalized.append(text)
    return normalized


def _known_context_lines(
    runtime_readiness: Mapping[str, Any],
    operator_context: Mapping[str, Any],
) -> list[str]:
    known: list[str] = []
    auto_resolved = operator_context.get("auto_resolved_context")
    if isinstance(auto_resolved, list):
        known.extend(_normalized_items([str(item) for item in auto_resolved]))
    if _runtime_provider_identity_known(runtime_readiness):
        known.append("planner and default executor target identities are known from runtime_context")
    if _runtime_guardrails_known(runtime_readiness):
        known.append("executor guardrails are known from runtime_context")
    if _context_value_present(operator_context.get("human_control_contract")):
        known.append("human control surface is already fixed before room start")
    return known


def _context_evidence_lines(
    runtime_readiness: Mapping[str, Any],
    operator_context: Mapping[str, Any],
) -> list[str]:
    evidence: list[str] = []
    explicit_resolved = operator_context.get("resolved_room_start_inputs")
    if isinstance(explicit_resolved, list):
        evidence.extend(_normalized_items([str(item) for item in explicit_resolved]))
    evidence.extend(_operator_context_evidence_lines(operator_context))
    evidence.extend(_runtime_context_evidence_lines(runtime_readiness))
    evidence.extend(_known_context_lines(runtime_readiness, operator_context))
    return _normalized_items(evidence)


def _operator_context_evidence_lines(operator_context: Mapping[str, Any]) -> list[str]:
    evidence: list[str] = []
    for key, value in operator_context.items():
        if key in {"entry_scope", "resolved_room_start_inputs"}:
            continue
        label = key.replace("_", " ").strip()
        if isinstance(value, str) and value.strip():
            evidence.append(f"{label} {value.strip()}")
            continue
        if isinstance(value, list):
            for item in value:
                normalized = str(item).strip()
                if normalized:
                    evidence.append(f"{label} {normalized}")
            continue
        if isinstance(value, Mapping):
            for nested_key, nested_value in value.items():
                normalized = str(nested_value).strip()
                if normalized:
                    evidence.append(
                        f"{label} {str(nested_key).replace('_', ' ').strip()} {normalized}"
                    )
    return evidence


def _runtime_context_evidence_lines(runtime_readiness: Mapping[str, Any]) -> list[str]:
    evidence: list[str] = []
    planner_target = runtime_readiness.get("planner_target")
    if isinstance(planner_target, Mapping) and _target_identity_known(planner_target):
        supplier = str(planner_target.get("supplier", "")).strip()
        model = str(planner_target.get("model", "")).strip()
        evidence.append(
            "primary planner provider identity is known from runtime readiness "
            f"supplier {supplier} model {model}"
        )
        evidence.append(
            "planner target identity is known before room start"
        )
    executor_targets = runtime_readiness.get("executor_targets")
    if isinstance(executor_targets, Mapping):
        default_target = executor_targets.get("default")
        if isinstance(default_target, Mapping) and _target_identity_known(default_target):
            supplier = str(default_target.get("supplier", "")).strip()
            model = str(default_target.get("model", "")).strip()
            evidence.append(
                "default executor provider identity is known from runtime readiness "
                f"supplier {supplier} model {model}"
            )
            evidence.append(
                "specific provider identity for the room is known before room start"
            )
    if _runtime_guardrails_known(runtime_readiness):
        evidence.append("executor guardrails are known from runtime readiness")
    return evidence


def _semantic_tokens(value: str) -> set[str]:
    return {
        token
        for token in _normalize_text(value).split()
        if token and token not in _STOPWORDS
    }


def _normalize_text(value: str) -> str:
    normalized = "".join(
        character.lower() if character.isalnum() else " "
        for character in str(value)
    )
    return " ".join(normalized.split())


_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "before",
    "by",
    "can",
    "counts",
    "current",
    "do",
    "does",
    "for",
    "how",
    "in",
    "is",
    "it",
    "known",
    "must",
    "of",
    "on",
    "or",
    "out",
    "remain",
    "room",
    "should",
    "start",
    "the",
    "this",
    "to",
    "value",
    "what",
    "which",
}


def _runtime_readiness_prerequisites(
    runtime_readiness: Mapping[str, Any],
    *,
    allow_planner_fallback: bool,
) -> list[str]:
    blockers: list[str] = []
    planner_ready = bool(runtime_readiness.get("primary_planner_ready", True))
    if not planner_ready and allow_planner_fallback:
        planner_ready = bool(runtime_readiness.get("fallback_planner_ready", False))
    if not planner_ready:
        reason = str(runtime_readiness.get("primary_unavailable_reason", "")).strip()
        blockers.append(
            reason or "primary planner is not ready for room creation"
        )
    if not bool(runtime_readiness.get("executor_ready", True)):
        reason = str(runtime_readiness.get("executor_reason", "")).strip()
        blockers.append(
            reason or "room executor is not ready for room creation"
        )
    return blockers
