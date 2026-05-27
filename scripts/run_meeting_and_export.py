"""Run a real-LLM centralized MAS meeting end-to-end and export the final
decision record to docs/decisions/.

Usage::

    PYTHONPATH=src .venv/bin/python scripts/run_meeting_and_export.py \\
        --requirement "<requirement text>" \\
        --max-rounds 3 \\
        --topic-slug optional-explicit-slug

Reads .env at the project root. Requires the same provider env as
``LLMRoomExecutor``. Memory writes go to ``$MEETKAT_MEMORY_DIR`` (default
``~/.meetkat/memory``). The decision record is written to::

    docs/decisions/{YYYY-MM-DD}-{slug-from-topic-or-arg}.md

Stdout summarizes the run and prints the path of the generated record.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from decision_room.dev_env import load_local_dotenv
from decision_room.memory import default_long_term_store, default_room_memory_store
from decision_room.runtime.decision_record import render_decision_record, slugify
from decision_room.runtime.http_api import build_runtime_from_env
from decision_room.runtime.room_runtime import RuntimeConfig


DEFAULT_REQUIREMENT = (
    "我们一个 18 人的全栈团队正在快速增长，准备把单体应用按业务能力拆出 2-3 "
    "个微服务。需要决定：本季度先拆哪条边界、如何切迁移节奏、用什么 IPC 协议、"
    "数据库/事件队列怎么分，以及为团队准备什么样的运维与可观测性能力，才能在不影响交付的前提下完成。"
)


async def _wait_until(predicate, timeout: float) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.5)
    raise TimeoutError("会议未在预期时间内结束")


def _collect_role_lessons(participating_roles: list[str]) -> dict[str, list[dict[str, Any]]]:
    long_store = default_long_term_store()
    out: dict[str, list[dict[str, Any]]] = {}
    for role in participating_roles:
        lessons = long_store.recent(role, limit=5)
        out[role] = [lesson.to_payload() for lesson in lessons]
    return out


def _participating_roles(snapshot: dict[str, Any]) -> list[str]:
    seen: list[str] = []
    for entry in snapshot.get("transcript", []) or []:
        role = entry.get("role", "")
        if role in {"host", "supervisor", "synthesis", "system", "human"} or not role:
            continue
        if role not in seen:
            seen.append(role)
    return seen


async def run(args: argparse.Namespace) -> int:
    load_local_dotenv(ROOT / ".env")
    env = dict(os.environ)
    env.setdefault("DECISION_ROOM_EXECUTOR", "centralized")
    env.setdefault("DECISION_ROOM_PLANNER_MODE", "primary_with_fallback")
    runtime = build_runtime_from_env(
        env,
        config=RuntimeConfig(
            message_chunk_delay_sec=0.0,
            between_turn_delay_sec=0.0,
            between_round_delay_sec=0.0,
            max_rounds=args.max_rounds,
        ),
    )
    try:
        readiness = runtime.runtime_readiness()
        print("=== 运行时就绪 ===")
        print(json.dumps(
            {
                "planner_mode": readiness.get("planner_mode"),
                "executor_mode": readiness.get("executor_mode"),
                "role_planner_kind": readiness.get("role_planner_kind"),
                "role_planner_degraded": readiness.get("role_planner_degraded"),
                "executor_ready": readiness.get("executor_ready"),
                "primary_planner_ready": readiness.get("primary_planner_ready"),
            },
            indent=2,
            ensure_ascii=False,
        ))
        if not readiness.get("executor_ready"):
            print("[exit] 执行器不可用，无法跑 LLM；请检查 .env 的 provider 配置")
            return 2

        print(f"\n=== 创建会议室 ===\n需求：{args.requirement}\n")
        snapshot = await runtime.create_room(
            requirement=args.requirement,
            require_preflight_ready=False,
            allow_planner_fallback=True,
            entry_scope="interactive_room_start",
        )
        room_id = snapshot["room_id"]
        print(f"room_id = {room_id}\n")

        await _wait_until(
            lambda: runtime.get_snapshot(room_id)["status"] == "ended",
            timeout=args.timeout,
        )
        final = runtime.get_snapshot(room_id)
        print(f"会议结束 · 轮次 {final['round_index']} · 结论 {final['conclusion_type']!r}")

        participating = _participating_roles(final)
        memory_snapshot = default_room_memory_store().snapshot(room_id)
        role_lessons = _collect_role_lessons(participating)

        record = render_decision_record(
            final,
            room_memory_snapshot=memory_snapshot,
            role_lessons=role_lessons,
        )

        topic_slug = args.topic_slug or slugify(final.get("topic", ""), fallback="decision")
        date_prefix = datetime.now().strftime("%Y-%m-%d")
        out_dir = ROOT / "docs" / "decisions"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{date_prefix}-{topic_slug}.md"
        out_path.write_text(record, encoding="utf-8")
        print(f"\n=== 决策文档已生成 ===\n{out_path}")
        print(f"大小：{out_path.stat().st_size} 字节 / {record.count(chr(10))} 行")
        return 0
    finally:
        await runtime.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--requirement", default=DEFAULT_REQUIREMENT)
    parser.add_argument("--max-rounds", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=360.0)
    parser.add_argument("--topic-slug", default="")
    args = parser.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
