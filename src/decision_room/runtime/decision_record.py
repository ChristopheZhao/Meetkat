"""Render a finished room's snapshot + memory state as a structured
Chinese-language Markdown decision record.

The renderer is intentionally projection-only — it never mutates state
and does not call providers. The journal stays the single source of
truth (baseline §2.5); the markdown is just a denormalized human-readable
view derived from the snapshot dict, the room-scoped memory snapshot,
and the per-role long-term lessons.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Iterable


_ROLE_DISPLAY = {
    "host": "主持人",
    "supervisor": "中心化主持人",
    "synthesis": "决策记录员",
    "human": "操作员（你）",
    "system": "系统",
    "implementation_specialist": "系统架构师",
    "risk_specialist": "风险控制师",
    "product_specialist": "产品策略师",
    "operations_specialist": "运营观察员",
}


def render_decision_record(
    snapshot: dict[str, Any],
    *,
    room_memory_snapshot: dict[str, Any] | None = None,
    role_lessons: dict[str, list[dict[str, Any]]] | None = None,
    generated_at: datetime | None = None,
) -> str:
    """Build the full Markdown decision record."""
    when = (generated_at or datetime.now(timezone.utc)).astimezone()
    room_id = str(snapshot.get("room_id", "unknown"))
    topic = str(snapshot.get("topic", "")).strip() or "（未命名讨论）"
    requirement = str(snapshot.get("requirement", "")).strip()
    candidate = str(snapshot.get("candidate_decision", "")).strip()
    conclusion_type = str(snapshot.get("conclusion_type", "")).strip()
    conclusion_reason = str(snapshot.get("conclusion_reason", "")).strip()
    goal = str(snapshot.get("goal", "")).strip()
    rounds = int(snapshot.get("round_index", 0))
    status = str(snapshot.get("status", "")).strip()
    transcript = snapshot.get("transcript", []) or []

    lines: list[str] = []

    # Title + metadata block
    lines.append(f"# 决策会议记录 · {topic}")
    lines.append("")
    lines.append(f"- 会议 ID：`{room_id}`")
    lines.append(f"- 状态：{status}（共 {rounds} 轮）")
    lines.append(f"- 决策类型：{conclusion_type or '（未给出）'}")
    lines.append(f"- 生成时间：{when:%Y-%m-%d %H:%M:%S %Z}")
    if requirement:
        lines.append("")
        lines.append("## 原始需求")
        lines.append("")
        lines.append(_quote_block(requirement))
    if goal:
        lines.append("")
        lines.append(f"**会议目标**：{goal}")

    # Conclusion
    lines.append("")
    lines.append("## 决策结论")
    lines.append("")
    lines.append(f"**候选决策**：{candidate or '（本会议未形成候选决策）'}")
    if conclusion_reason:
        lines.append("")
        lines.append(f"**结论原因**：{conclusion_reason}")

    # Action items + open questions
    action_items = list(snapshot.get("action_items") or [])
    if action_items:
        lines.append("")
        lines.append("## 行动项")
        lines.append("")
        for item in action_items:
            lines.append(f"- [ ] {item}")
    open_questions = list(snapshot.get("open_questions") or [])
    if open_questions:
        lines.append("")
        lines.append("## 待办的开放问题")
        lines.append("")
        for question in open_questions:
            lines.append(f"- {question}")

    # Per-role claim summary (latest claim per role from transcript)
    role_claims = _collect_role_claims(transcript)
    if role_claims:
        lines.append("")
        lines.append("## 各角色立场摘要")
        lines.append("")
        for role, info in role_claims.items():
            display = _ROLE_DISPLAY.get(role, role)
            lines.append(f"### {display} · `{role}`")
            lines.append("")
            lines.append(f"- **核心主张**：{info['claim']}")
            confidence = info.get("confidence")
            if isinstance(confidence, (int, float)) and confidence:
                lines.append(f"- **信心**：{float(confidence):.2f}")
            evidence = info.get("evidence") or []
            if evidence:
                lines.append("- **证据**：")
                for piece in evidence:
                    lines.append(f"  - {piece}")

    # Supervisor self-memory: what the supervisor decided round by round
    if room_memory_snapshot:
        plan_entries = _collect_supervisor_plans(room_memory_snapshot)
        if plan_entries:
            lines.append("")
            lines.append("## 主持人选角与焦点（按轮次）")
            lines.append("")
            for entry in plan_entries:
                lines.append(
                    f"### 第 {entry.get('round_index', '?')} 轮 · "
                    f"焦点：{entry.get('decision_focus', '（未给出）')}"
                )
                lines.append("")
                reason = entry.get("reason", "")
                if reason:
                    lines.append(f"- **选角理由**：{reason}")
                speakers = entry.get("speakers", []) or []
                if speakers:
                    lines.append("- **发言顺序**：")
                    for index, speaker in enumerate(speakers, start=1):
                        if isinstance(speaker, dict):
                            agent = str(speaker.get("agent", "")).strip()
                            angle = str(speaker.get("focus_angle", "")).strip()
                            order = speaker.get("order", index)
                        else:
                            agent = str(speaker).strip()
                            angle = ""
                            order = index
                        if not agent:
                            continue
                        display = _ROLE_DISPLAY.get(agent, agent)
                        order_text = f"#{order} "
                        if angle:
                            lines.append(
                                f"  - {order_text}{display}（`{agent}`）— 角度提示：{angle}"
                            )
                        else:
                            lines.append(
                                f"  - {order_text}{display}（`{agent}`）— 由角色契约自主决定内容"
                            )

    # Long-term role lessons (this run's takeaway per role)
    if role_lessons:
        meaningful = {role: lessons for role, lessons in role_lessons.items() if lessons}
        if meaningful:
            lines.append("")
            lines.append("## 角色长期 Lesson 摘要")
            lines.append("")
            lines.append(
                "_以下是每个参会角色在本会议结束后写入长期记忆库的 lesson。下次会议同一角色登场时会通过 `memory_recall.role_lessons` 自动召回。_"
            )
            lines.append("")
            for role, lessons in meaningful.items():
                display = _ROLE_DISPLAY.get(role, role)
                lines.append(f"### {display} · `{role}`")
                lines.append("")
                for lesson in lessons[-3:]:
                    text = str(lesson.get("text", "")).strip()
                    if text:
                        lines.append(f"- {text}")

    # Full transcript
    if transcript:
        lines.append("")
        lines.append("## 完整对话记录")
        lines.append("")
        for entry in transcript:
            role = str(entry.get("role", ""))
            event_type = str(entry.get("event_type", ""))
            if event_type not in {"agent.message", "human.message", "human.override"}:
                continue
            display = _ROLE_DISPLAY.get(role, role) or role or "未知"
            title = str(entry.get("title", "")).strip()
            text = str(entry.get("text", "")).strip()
            header = f"### {display}"
            if title:
                header += f" · {title}"
            lines.append(header)
            lines.append("")
            if text:
                lines.append(text)
            artifacts = entry.get("artifacts") or {}
            if isinstance(artifacts, dict):
                claim = str(artifacts.get("claim", "")).strip()
                if claim and not claim.startswith("[Awaiting"):
                    lines.append("")
                    lines.append(f"_主张：{claim}_")
            lines.append("")
            lines.append("---")
            lines.append("")

    # Footer
    lines.append("")
    lines.append(
        "_本文件由 `decision_room.runtime.decision_record.render_decision_record` "
        "从权威会议事件流（`RoomEventJournal`）+ 短期/长期记忆投影生成；"
        "可由同一 snapshot 完全重建。_"
    )
    return "\n".join(lines).strip() + "\n"


def _quote_block(text: str) -> str:
    return "\n".join(f"> {line}" if line else ">" for line in text.splitlines())


def _collect_role_claims(transcript: Iterable[Any]) -> dict[str, dict[str, Any]]:
    """Walk the transcript and pick the most-recent claim for each
    non-host/synthesis/system/human role."""
    role_info: dict[str, dict[str, Any]] = {}
    for entry in transcript:
        if not isinstance(entry, dict):
            continue
        role = str(entry.get("role", ""))
        if role in {"host", "supervisor", "synthesis", "system", "human"} or not role:
            continue
        if entry.get("event_type") != "agent.message":
            continue
        artifacts = entry.get("artifacts") or {}
        if not isinstance(artifacts, dict):
            continue
        claim = str(artifacts.get("claim", "")).strip()
        if not claim:
            continue
        try:
            confidence = float(artifacts.get("confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        evidence_raw = artifacts.get("evidence") or []
        evidence = [str(item).strip() for item in evidence_raw if str(item).strip()]
        role_info[role] = {
            "claim": claim,
            "confidence": confidence,
            "evidence": evidence,
        }
    return role_info


def _collect_supervisor_plans(memory_snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Pull every supervisor.plan event out of the MAS shared scope, plus
    the latest_supervisor_plan fact for richer per-round context."""
    plans: list[dict[str, Any]] = []
    for scope, payload in memory_snapshot.items():
        if not isinstance(payload, dict):
            continue
        if not scope.startswith("mas:"):
            continue
        events = payload.get("events", []) or []
        for event in events:
            if not isinstance(event, dict):
                continue
            if event.get("event_type") != "supervisor.plan":
                continue
            plan_payload = event.get("payload") or {}
            if isinstance(plan_payload, dict):
                plans.append(plan_payload)
        # Latest supervisor plan fact may contain richer per-round info than
        # the slimmed-down event payload; merge by round_index.
        facts = payload.get("facts", {}) or {}
        latest = facts.get("latest_supervisor_plan")
        if isinstance(latest, dict):
            latest_value = latest.get("value")
            if isinstance(latest_value, dict):
                round_index = latest_value.get("round_index")
                if round_index is not None:
                    for plan in plans:
                        if plan.get("round_index") == round_index:
                            plan.setdefault("reason", latest_value.get("reason", ""))
                            existing_speakers = plan.get("speakers") or []
                            if not existing_speakers or all(
                                isinstance(s, str) for s in existing_speakers
                            ):
                                plan["speakers"] = latest_value.get("speakers", [])
                            break
                    else:
                        plans.append(
                            {
                                "round_index": round_index,
                                "decision_focus": latest_value.get("decision_focus", ""),
                                "reason": latest_value.get("reason", ""),
                                "speakers": latest_value.get("speakers", []),
                            }
                        )
    plans.sort(key=lambda item: item.get("round_index", 0) or 0)
    return plans


def slugify(value: str, *, fallback: str = "decision") -> str:
    """Make a filesystem-safe slug from a topic title."""
    cleaned = re.sub(r"[^A-Za-z0-9一-鿿_\- ]+", "", value or "").strip()
    cleaned = re.sub(r"\s+", "-", cleaned)
    cleaned = cleaned[:60].strip("-")
    return cleaned or fallback
