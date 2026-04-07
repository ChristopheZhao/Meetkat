from __future__ import annotations

import copy
from typing import Any, Mapping


SUPPORTED_ENTRY_SCOPES = {
    "interactive_room_start",
}


def resolve_operator_context(
    *,
    entry_scope: str | None = None,
    operator_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_scope = str(entry_scope or "").strip().lower()
    base = _entry_scope_defaults(normalized_scope) if normalized_scope else {}
    if normalized_scope:
        base["entry_scope"] = normalized_scope
    if not operator_context:
        return base
    return _merge_context_dicts(base, operator_context)


def _entry_scope_defaults(entry_scope: str) -> dict[str, Any]:
    if entry_scope == "interactive_room_start":
        return {
            "entry_contract": [
                "start a normal agent-led room from the operator requirement and allow the host-led topology to drive the discussion",
                "human message and human override remain the operator control surface after room start",
                "room-start preflight validates external prerequisites, but it is not a substitute for separate validation or browser transport verification flows",
            ],
            "auto_resolved_context": [
                "runtime preflight status and recommended surface are persisted before room creation rather than rediscovered in-room",
                "planner/executor target identity is taken from runtime_readiness when the local runtime exposes those targets",
                "the human control surface and normal room transport scope are fixed by this entry scope and do not need to be re-negotiated during the meeting",
            ],
            "operator_required_inputs": [
                "the requirement statement that should drive planning and discussion",
                "any hard business or technical constraint that must shape planning before room start",
                "any known external prerequisite or dependency the room should respect before specialists start",
            ],
            "human_control_contract": [
                "operators can inject human messages during the meeting",
                "operators can force a room-ending override through the runtime control surface",
            ],
            "transport_contract": [
                "this entry relies on the established runtime transport contract and authoritative snapshot/replay surfaces",
                "live browser transport degradation checks belong to the separate headed browser transport verification path rather than normal room-start preflight",
            ],
        }
    if not entry_scope:
        return {}
    raise ValueError(
        "unsupported entry_scope; expected one of: "
        + ", ".join(sorted(SUPPORTED_ENTRY_SCOPES))
    )


def _merge_context_dicts(
    base: Mapping[str, Any],
    override: Mapping[str, Any],
) -> dict[str, Any]:
    merged = copy.deepcopy(dict(base))
    for key, override_value in override.items():
        merged[key] = _merge_context_value(merged.get(key), override_value)
    return merged


def _merge_context_value(base_value: Any, override_value: Any) -> Any:
    if isinstance(base_value, Mapping) and isinstance(override_value, Mapping):
        return _merge_context_dicts(base_value, override_value)
    if isinstance(base_value, list) and isinstance(override_value, list):
        combined: list[Any] = []
        for item in [*base_value, *override_value]:
            if item not in combined:
                combined.append(copy.deepcopy(item))
        return combined
    return copy.deepcopy(override_value)
