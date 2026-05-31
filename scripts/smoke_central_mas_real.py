"""Smoke run: real LLM-driven centralized MAS supervisor end-to-end.

Drives a room through ``CentralizedMASExecutor`` against the providers
configured in ``.env``. Prints the supervisor's assignment contracts and
each specialist's first-round output so a human can confirm the central
topology is producing requirement-sensitive LLM output (not the deleted
scripted stub).

Usage::

    PYTHONPATH=src .venv/bin/python scripts/smoke_central_mas_real.py \
        --requirement "..." --max-rounds 2

Requires the same provider env as ``LLMRoomExecutor`` (MODEL_DEFAULT_*,
MODEL_ESCALATION_*, MODEL_FALLBACK_*, plus per-supplier BASE_URL/API_KEY).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from decision_room.dev_env import load_local_dotenv
from decision_room.runtime.http_api import build_runtime_from_env
from decision_room.runtime.room_runtime import RuntimeConfig


DEFAULT_REQUIREMENT = (
    "Design a centralized multi-agent system that can hold an online "
    "decision meeting room for a deep engineering trade-off. The room "
    "needs real-time supervisor assignment contracts, live specialist "
    "agent communication via WebSocket with SSE replay fallback, human "
    "override, and a decision record that captures rationale, risks, and "
    "action items."
)


async def _wait_until(predicate, timeout: float = 180.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.5)
    raise TimeoutError("room did not reach a terminal state in time")


def _print_central_mas(snapshot: dict[str, Any]) -> None:
    transcript = snapshot.get("transcript", [])
    host_entries = [
        item
        for item in transcript
        if item.get("role") == "host" and item.get("event_type") == "agent.message"
    ]
    if not host_entries:
        print("[smoke] no host message recorded yet")
        return
    bundle = host_entries[0].get("artifacts", {}).get("central_mas")
    if not bundle:
        print("[smoke] host message had no central_mas artifact — refactor regression!")
        return
    print("\n=== Central MAS supervisor (round 1) ===")
    print(f"topology      = {bundle.get('topology')}")
    print(f"decision_focus= {bundle.get('decision_focus')}")
    print(f"reason        = {bundle.get('reason')}")
    print("\nrole_catalog:")
    for role in bundle.get("role_catalog", []):
        print(f"  - {role.get('role'):<25} {role.get('display_name')}")
    print("\nassignment_contracts:")
    for contract in bundle.get("assignment_contracts", []):
        print(f"  * {contract.get('agent')} (order {contract.get('order')})")
        print(f"      mission     : {contract.get('mission')}")
        print(f"      deliverable : {contract.get('deliverable')}")
        for c in contract.get("constraints", [])[:3]:
            print(f"      constraint  : {c}")


def _print_specialist_outputs(snapshot: dict[str, Any]) -> None:
    transcript = snapshot.get("transcript", [])
    seen_roles: set[str] = set()
    print("\n=== Specialist outputs (round 1) ===")
    for item in transcript:
        if item.get("event_type") != "agent.message":
            continue
        role = item.get("role", "")
        if role in {"host", "synthesis", "system", "human"}:
            continue
        if role in seen_roles:
            continue
        seen_roles.add(role)
        artifacts = item.get("artifacts", {})
        text = item.get("text", "")
        claim = artifacts.get("claim", "")
        evidence = artifacts.get("evidence", []) or []
        confidence = artifacts.get("confidence")
        print(f"\n[{role}] confidence={confidence}")
        print(f"  claim    : {claim}")
        for ev in evidence[:3]:
            print(f"  evidence : {ev}")
        print(f"  text     : {text[:240]}")


def _print_synthesis(snapshot: dict[str, Any]) -> None:
    """``agent.summary`` events project onto snapshot top-level fields rather
    than transcript entries, so read them straight off the snapshot."""
    print("\n=== Synthesis (latest projection) ===")
    print(f"decision_candidate : {snapshot.get('candidate_decision')}")
    print(f"conclusion_type    : {snapshot.get('conclusion_type')}")
    print(f"conclusion_reason  : {snapshot.get('conclusion_reason')}")
    consensus = snapshot.get("consensus", {}) or {}
    print(
        "consensus          : "
        f"score={consensus.get('score'):.2f} support={consensus.get('support'):.2f} "
        f"confidence={consensus.get('confidence'):.2f} "
        f"disagreement_index={consensus.get('disagreement_index'):.2f} "
        f"should_end={consensus.get('should_end')}"
    )
    print(f"action_items       : {snapshot.get('action_items')}")
    print(f"open_questions     : {snapshot.get('open_questions')}")


async def run(requirement: str, max_rounds: int) -> int:
    load_local_dotenv(ROOT / ".env")
    env = dict(os.environ)
    env["DECISION_ROOM_EXECUTOR"] = "centralized"
    env.setdefault("DECISION_ROOM_PLANNER_MODE", "primary_with_fallback")
    runtime = build_runtime_from_env(
        env,
        config=RuntimeConfig(
            message_chunk_delay_sec=0.0,
            between_turn_delay_sec=0.0,
            between_round_delay_sec=0.0,
            max_rounds=max_rounds,
        ),
    )
    try:
        readiness = runtime.runtime_readiness()
        print("=== Runtime readiness ===")
        print(json.dumps(
            {
                "planner_mode": readiness["planner_mode"],
                "executor_mode": readiness["executor_mode"],
                "primary_planner_ready": readiness["primary_planner_ready"],
                "executor_ready": readiness["executor_ready"],
                "executor_targets": readiness.get("executor_targets"),
                "executor_reason": readiness.get("executor_reason"),
            },
            indent=2,
            ensure_ascii=False,
        ))
        if not readiness["executor_ready"]:
            print("[smoke] executor not ready; aborting")
            return 2

        print(f"\n=== Creating room ===\nrequirement = {requirement}\n")
        snapshot = await runtime.create_room(
            requirement=requirement,
            entry_scope="interactive_room_start",
            allow_planner_fallback=True,
        )
        room_id = snapshot["room_id"]
        print(f"room_id = {room_id}")

        await _wait_until(
            lambda: runtime.get_snapshot(room_id)["status"] == "ended",
            timeout=240.0,
        )
        current = runtime.get_snapshot(room_id)
        print(f"\nstatus     = {current['status']}")
        print(f"brief_source = {current['brief_source']}")
        print(f"rounds       = {current['round_index']}")
        _print_central_mas(current)
        _print_specialist_outputs(current)
        _print_synthesis(current)
        return 0
    finally:
        await runtime.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--requirement", default=DEFAULT_REQUIREMENT)
    parser.add_argument("--max-rounds", type=int, default=2)
    args = parser.parse_args()
    return asyncio.run(run(args.requirement, args.max_rounds))


if __name__ == "__main__":
    raise SystemExit(main())
