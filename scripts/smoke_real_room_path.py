from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from decision_room.orchestration import classify_blocked_dependency_texts
from decision_room.runtime.http_api import build_runtime_from_env
from decision_room.runtime.room_runtime import RoomRuntime, RuntimeConfig

DEFAULT_REQUIREMENT = (
    "Need a real provider-backed room run that validates the new host-led topology "
    "using the runtime's currently configured primary planner and executor providers, "
    "without falling back to demo mode. Success means brief_source=agent, a non-empty "
    "current_turns projection, authoritative snapshot/replay evidence, and an explicit "
    "meeting conclusion contract with conclusion_type and conclusion_reason."
)

REQUIRED_EVENT_TYPES = {
    "planning.completed",
    "room.started",
    "agent.summary",
    "meeting.ended",
}

DEFAULT_ENTRY_SCOPE = "interactive_room_start"
DEFAULT_OPERATOR_CONTEXT = {
    "brief_source": "agent",
    "success_criteria": [
        "brief_source=agent",
        "non-empty current_turns projection",
        "authoritative snapshot/replay evidence",
        "explicit conclusion_type and conclusion_reason",
    ],
    "validation_scenario": [
        "start one real non-demo room from the current requirement and allow the host-led topology to run until meeting.ended",
        "use the room's own authoritative snapshot and replay surfaces as the validation evidence source",
        "treat the default smoke requirement text as the trigger payload for the provider-backed validation scenario",
    ],
    "binding_readiness_contract": [
        "the authoritative pre-room binding readiness signal is the combination of runtime_readiness and room_start_contract.room_start_ready before create_room",
        "for smoke validation, provider binding readiness is established before planning.completed by the gated runtime bootstrap and must not be rediscovered in-room",
    ],
    "transport_contract": [
        "the smoke harness assumes the runtime transport contract remains WebSocket-primary with SSE read-only fallback, but it does not execute a live transport degradation test",
        "silent SSE fallback or zombie WebSocket detection is out of scope for this smoke harness and is covered by headed browser transport verification",
        "this smoke harness only validates backend-authoritative snapshot/replay and room event contracts, not live browser transport telemetry",
    ],
    "projection_contract": [
        "current_turns success means snapshot.current_turns is non-empty and each item contains non-empty role and task fields",
        "the authoritative source for current_turns is the host agent.message artifacts.turns projection recorded in replay and snapshot",
    ],
    "evidence_contract": [
        "authoritative snapshot and replay evidence means the smoke run must observe the authoritative snapshot/replay surfaces directly",
        "required event types: planning.completed, room.started, agent.summary, meeting.ended",
    ],
    "conclusion_contract": [
        "meeting conclusion contract requires non-empty conclusion_type",
        "meeting conclusion contract requires non-empty conclusion_reason",
    ],
}


def _event_type_counts(replay: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for event in replay:
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("event_type", "")).strip()
        if not event_type:
            continue
        counts[event_type] = counts.get(event_type, 0) + 1
    return counts


def _route_from_payload(payload: dict[str, Any]) -> dict[str, str]:
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, dict):
        return {}
    route = artifacts.get("route")
    if not isinstance(route, dict):
        return {}
    normalized: dict[str, str] = {}
    for key in ("tier", "supplier", "model", "reason"):
        value = str(route.get(key, "")).strip()
        if value:
            normalized[key] = value
    return normalized


def _turns_from_payload(payload: dict[str, Any]) -> list[dict[str, str]]:
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, dict):
        return []
    turns = artifacts.get("turns")
    if not isinstance(turns, list):
        return []
    normalized: list[dict[str, str]] = []
    for turn in turns:
        if not isinstance(turn, dict):
            continue
        role = str(turn.get("role", "")).strip()
        task = str(turn.get("task", "")).strip()
        if role and task:
            normalized.append({"role": role, "task": task})
    return normalized


def _build_round_diagnostics(replay: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rounds: dict[int, dict[str, Any]] = {}
    for event in replay:
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("event_type", "")).strip()
        role = str(event.get("role", "")).strip()
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue

        if event_type == "agent.message":
            round_index = payload.get("round_index")
            if round_index is None:
                continue
            round_number = int(round_index)
            round_entry = rounds.setdefault(
                round_number,
                {
                    "round_index": round_number,
                    "phase": str(payload.get("phase", "")).strip(),
                    "host_route": {},
                    "host_turns": [],
                    "specialist_routes": [],
                    "synthesis_route": {},
                    "consensus": {},
                },
            )
            round_entry["phase"] = str(payload.get("phase", round_entry["phase"])).strip()
            if role == "host":
                round_entry["host_route"] = _route_from_payload(payload)
                round_entry["host_turns"] = _turns_from_payload(payload)
            else:
                route = _route_from_payload(payload)
                turn_index = payload.get("artifacts", {}).get("turn_index") if isinstance(payload.get("artifacts"), dict) else None
                round_entry["specialist_routes"].append(
                    {
                        "role": role,
                        "turn_index": int(turn_index) if turn_index is not None else 0,
                        "title": str(payload.get("title", "")).strip(),
                        "route": route,
                    }
                )
        elif event_type == "agent.summary":
            round_index = payload.get("round_index")
            if round_index is None:
                continue
            round_number = int(round_index)
            round_entry = rounds.setdefault(
                round_number,
                {
                    "round_index": round_number,
                    "phase": str(payload.get("phase", "")).strip(),
                    "host_route": {},
                    "host_turns": [],
                    "specialist_routes": [],
                    "synthesis_route": {},
                    "consensus": {},
                },
            )
            round_entry["phase"] = str(payload.get("phase", round_entry["phase"])).strip()
            round_entry["synthesis_route"] = _route_from_payload(payload)
            round_entry["conclusion_type"] = str(payload.get("conclusion_type", "")).strip()
            round_entry["conclusion_reason"] = str(payload.get("conclusion_reason", "")).strip()
        elif event_type == "consensus.check":
            pending_rounds = sorted(index for index in rounds if not rounds[index].get("consensus"))
            if not pending_rounds:
                continue
            round_entry = rounds[pending_rounds[-1]]
            round_entry["consensus"] = {
                "score": float(payload.get("score", 0.0)),
                "should_end": bool(payload.get("should_end", False)),
                "meeting_should_end": bool(payload.get("meeting_should_end", False)),
                "meeting_end_reason": str(payload.get("meeting_end_reason", "")).strip(),
                "reason": str(payload.get("reason", "")).strip(),
            }

    diagnostics = [rounds[index] for index in sorted(rounds)]
    for entry in diagnostics:
        specialist_routes = entry.get("specialist_routes", [])
        specialist_routes.sort(key=lambda item: (item.get("turn_index", 0), item.get("role", "")))
    return diagnostics


def _meeting_end_diagnostics(replay: list[dict[str, Any]]) -> dict[str, str]:
    ended_events = [
        event for event in replay if isinstance(event, dict) and event.get("event_type") == "meeting.ended"
    ]
    if not ended_events:
        return {}
    payload = ended_events[-1].get("payload")
    if not isinstance(payload, dict):
        return {}
    diagnostics: dict[str, str] = {}
    for key in (
        "reason",
        "conclusion_type",
        "conclusion_reason",
        "control_reason",
        "orchestration_end_reason",
    ):
        value = str(payload.get(key, "")).strip()
        if value:
            diagnostics[key] = value
    return diagnostics


def _blocked_dependency_analysis(
    snapshot: dict[str, Any],
    meeting_end: dict[str, str],
) -> dict[str, Any]:
    conclusion_type = str(snapshot.get("conclusion_type", "")).strip().lower()
    open_questions = snapshot.get("open_questions")
    normalized_questions = (
        [str(item).strip() for item in open_questions if str(item).strip()]
        if isinstance(open_questions, list)
        else []
    )
    text_sources = [
        str(snapshot.get("conclusion_reason", "")).strip(),
        str(meeting_end.get("conclusion_reason", "")).strip(),
        str(meeting_end.get("orchestration_end_reason", "")).strip(),
        *normalized_questions,
    ]
    combined_text = " \n".join(item for item in text_sources if item).lower()
    if conclusion_type != "blocked" or not combined_text:
        return {
            "has_blocked_dependencies": False,
            "categories": [],
            "recommended_surface": "",
            "root_cause_hypothesis": "",
        }
    return classify_blocked_dependency_texts(text_sources)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run a real planner/executor smoke against the in-process room runtime and "
            "assert the new host-led topology reaches an explicit meeting conclusion."
        )
    )
    parser.add_argument(
        "--requirement",
        default=DEFAULT_REQUIREMENT,
        help="requirement string used to create the room",
    )
    parser.add_argument(
        "--timeout-sec",
        type=float,
        default=300.0,
        help="max seconds to wait for the room to reach meeting.ended",
    )
    parser.add_argument(
        "--poll-interval-sec",
        type=float,
        default=0.5,
        help="poll interval while waiting for the room to end",
    )
    parser.add_argument(
        "--max-rounds",
        type=int,
        default=4,
        help="runtime round budget for the smoke run",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print a compact JSON summary on success",
    )
    return parser


def _ensure_readiness(readiness: dict[str, Any]) -> None:
    if not readiness.get("primary_planner_ready"):
        reason = str(readiness.get("primary_unavailable_reason", "")).strip()
        raise RuntimeError(
            "real-path smoke requires a ready primary planner"
            + (f": {reason}" if reason else "")
        )
    if not readiness.get("executor_ready"):
        reason = str(readiness.get("executor_reason", "")).strip()
        raise RuntimeError(
            "real-path smoke requires a ready executor"
            + (f": {reason}" if reason else "")
        )


def _validate_initial_snapshot(snapshot: dict[str, Any]) -> None:
    brief_source = str(snapshot.get("brief_source", "")).strip().lower()
    if brief_source != "agent":
        raise RuntimeError(
            f"real-path smoke expected brief_source=agent, got {brief_source or '<empty>'}"
        )
    if str(snapshot.get("status", "")).strip().lower() != "running":
        raise RuntimeError("real-path smoke expected room to start in running state")


def _validate_final_snapshot(
    snapshot: dict[str, Any],
    replay: list[dict[str, Any]],
) -> None:
    if str(snapshot.get("status", "")).strip().lower() != "ended":
        raise RuntimeError("real-path smoke expected room to end with meeting.ended")
    current_turns = snapshot.get("current_turns")
    if not isinstance(current_turns, list) or not current_turns:
        raise RuntimeError("real-path smoke expected a non-empty current_turns projection")
    conclusion_type = str(snapshot.get("conclusion_type", "")).strip()
    conclusion_reason = str(snapshot.get("conclusion_reason", "")).strip()
    if not conclusion_type:
        raise RuntimeError("real-path smoke expected a non-empty conclusion_type")
    if not conclusion_reason:
        raise RuntimeError("real-path smoke expected a non-empty conclusion_reason")
    transcript = snapshot.get("transcript")
    if not isinstance(transcript, list) or not transcript:
        raise RuntimeError("real-path smoke expected a non-empty transcript")

    event_types = {
        str(event.get("event_type", "")).strip() for event in replay if isinstance(event, dict)
    }
    missing = sorted(REQUIRED_EVENT_TYPES - event_types)
    if missing:
        raise RuntimeError(
            "real-path smoke replay missing required event types: " + ", ".join(missing)
        )


def _validate_preflight(preflight_report: dict[str, Any]) -> None:
    room_start_contract = preflight_report.get("room_start_contract")
    if not isinstance(room_start_contract, dict):
        raise RuntimeError("real-path smoke expected a room_start_contract payload before room start")

    system_blockers = room_start_contract.get("system_blockers")
    normalized = (
        [str(item).strip() for item in system_blockers if str(item).strip()]
        if isinstance(system_blockers, list)
        else []
    )
    if normalized:
        raise RuntimeError(
            "real-path smoke blocked by infra blockers: " + "; ".join(normalized[:3])
        )


async def run_real_path_smoke(
    runtime: RoomRuntime,
    *,
    requirement: str,
    timeout_sec: float,
    poll_interval_sec: float,
) -> dict[str, Any]:
    readiness = runtime.runtime_readiness()
    _ensure_readiness(readiness)
    preflight_report = runtime.preflight_room(
        requirement=requirement,
        allow_planner_fallback=False,
        entry_scope=DEFAULT_ENTRY_SCOPE,
        operator_context=DEFAULT_OPERATOR_CONTEXT,
    )
    _validate_preflight(preflight_report)

    initial_snapshot = await runtime.create_room(
        requirement=requirement,
        allow_planner_fallback=False,
        entry_scope=DEFAULT_ENTRY_SCOPE,
        operator_context=DEFAULT_OPERATOR_CONTEXT,
    )
    _validate_initial_snapshot(initial_snapshot)

    room_id = str(initial_snapshot["room_id"])
    deadline = asyncio.get_running_loop().time() + timeout_sec
    final_snapshot = initial_snapshot
    while asyncio.get_running_loop().time() < deadline:
        final_snapshot = runtime.get_snapshot(room_id)
        if str(final_snapshot.get("status", "")).strip().lower() == "ended":
            break
        await asyncio.sleep(poll_interval_sec)
    else:
        raise TimeoutError(
            f"room {room_id} did not reach meeting.ended within {timeout_sec:.1f}s"
        )

    replay = runtime.replay(room_id)
    _validate_final_snapshot(final_snapshot, replay)
    meeting_end = _meeting_end_diagnostics(replay)

    return {
        "room_id": room_id,
        "readiness": readiness,
        "room_start_contract": preflight_report["room_start_contract"],
        "brief_source": final_snapshot["brief_source"],
        "status": final_snapshot["status"],
        "phase": final_snapshot["phase"],
        "round_index": final_snapshot["round_index"],
        "conclusion_type": final_snapshot["conclusion_type"],
        "conclusion_reason": final_snapshot["conclusion_reason"],
        "open_questions": final_snapshot.get("open_questions", []),
        "event_count": len(replay),
        "event_type_counts": _event_type_counts(replay),
        "meeting_end": meeting_end,
        "blocked_dependency_analysis": _blocked_dependency_analysis(
            final_snapshot,
            meeting_end,
        ),
        "round_diagnostics": _build_round_diagnostics(replay),
        "last_seq": final_snapshot["last_seq"],
    }


async def execute_real_path_smoke(args: argparse.Namespace) -> dict[str, Any]:
    runtime = build_runtime_from_env(
        config=RuntimeConfig(
            message_chunk_delay_sec=0.0,
            between_turn_delay_sec=0.0,
            between_round_delay_sec=0.0,
            max_rounds=args.max_rounds,
        )
    )
    try:
        return await run_real_path_smoke(
            runtime,
            requirement=args.requirement,
            timeout_sec=args.timeout_sec,
            poll_interval_sec=args.poll_interval_sec,
        )
    finally:
        await runtime.close()


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        result = asyncio.run(execute_real_path_smoke(args))
    except Exception as exc:  # pragma: no cover - exercised via CLI
        print(f"real-path smoke failed: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(
            "real-path smoke passed: "
            f"{result['room_id']} ended as {result['conclusion_type']} "
            f"after round {result['round_index']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
