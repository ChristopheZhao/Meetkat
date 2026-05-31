"""Scan `docs/decisions/*.md` and project the directory into a single
`INDEX.md` cross-reference page.

Parsing is anchored on the header block that `decision_record.render_decision_record`
always emits — H1 `# 决策会议记录 · <title>`, the `- 会议 ID` / `- 状态` /
`- 决策类型` / `- 生成时间` metadata lines, the `## 原始需求` blockquote,
`**会议目标**：…`, and the `## 行动项` checklist. No frontmatter is required;
existing records work as-is.

Like the decision-record renderer, this module is pure projection — it
reads files and returns strings; it never mutates state and never calls
any LLM provider.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class DecisionRecordSummary:
    """One parsed decision record, distilled to the fields the index needs."""

    path: Path
    filename: str
    date: str
    title: str
    room_id: str
    rounds: int
    conclusion_type: str
    generated_at: str
    requirement: str
    goal: str
    action_items_count: int


_TITLE_RE = re.compile(r"^#\s+决策会议记录\s*·\s*(.+?)\s*$", re.M)
_ROOM_RE = re.compile(r"^-\s+会议\s*ID：\s*`([^`]+)`", re.M)
_STATUS_RE = re.compile(r"^-\s+状态：[^（]*（共\s*(\d+)\s*轮）", re.M)
_CONCLUSION_RE = re.compile(r"^-\s+决策类型：\s*(.+?)\s*$", re.M)
_GENERATED_RE = re.compile(r"^-\s+生成时间：\s*(.+?)\s*$", re.M)
_REQUIREMENT_RE = re.compile(
    r"^##\s+原始需求\s*\n\s*\n>\s*(.+?)\s*\n", re.M | re.S
)
_GOAL_RE = re.compile(r"\*\*会议目标\*\*：\s*(.+?)(?=\n\n|\Z)", re.S)
_FILENAME_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-")
_ACTION_ITEM_RE = re.compile(r"^-\s+\[[ xX]\]\s+", re.M)


def parse_decision_record(path: Path) -> DecisionRecordSummary | None:
    """Parse one decision-record markdown file.

    Returns ``None`` when the file does not look like a decision record
    (no matching H1 header). Files that match the H1 but miss optional
    fields still produce a summary — the missing fields stay empty/zero.
    """

    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None

    title_m = _TITLE_RE.search(text)
    if not title_m:
        return None

    def _grab(regex: re.Pattern[str]) -> str:
        m = regex.search(text)
        return m.group(1).strip() if m else ""

    rounds_match = _STATUS_RE.search(text)
    rounds = int(rounds_match.group(1)) if rounds_match else 0

    filename_date_m = _FILENAME_DATE_RE.match(path.name)
    date = filename_date_m.group(1) if filename_date_m else ""

    action_section = _extract_section(text, "行动项")
    action_count = (
        len(_ACTION_ITEM_RE.findall(action_section)) if action_section else 0
    )

    return DecisionRecordSummary(
        path=path,
        filename=path.name,
        date=date,
        title=title_m.group(1).strip(),
        room_id=_grab(_ROOM_RE),
        rounds=rounds,
        conclusion_type=_grab(_CONCLUSION_RE),
        generated_at=_grab(_GENERATED_RE),
        requirement=_grab(_REQUIREMENT_RE),
        goal=_grab(_GOAL_RE),
        action_items_count=action_count,
    )


def _extract_section(text: str, heading: str) -> str:
    """Return the body between a `## <heading>` line and the next `## ` line."""

    pattern = re.compile(
        rf"^##\s+{re.escape(heading)}\s*\n(.+?)(?=^##\s|\Z)",
        re.M | re.S,
    )
    m = pattern.search(text)
    return m.group(1).strip() if m else ""


def render_index(
    summaries: Iterable[DecisionRecordSummary],
    *,
    link_prefix: str = "",
) -> str:
    """Render `docs/decisions/INDEX.md` content from a sequence of summaries.

    Output is deterministic: summaries are sorted by ``(date, filename)``
    descending so calling repeatedly with the same input is byte-identical.
    `link_prefix` lets callers point links at a different root (default
    relative-to-INDEX, which means just the filename).
    """

    sorted_items = sorted(
        summaries, key=lambda s: (s.date, s.filename), reverse=True
    )

    lines: list[str] = []
    lines.append("# 决策记录索引")
    lines.append("")
    lines.append(f"> 共 {len(sorted_items)} 份决策记录，按日期倒序排列。")
    lines.append(">")
    lines.append(
        "> 本文件由 `scripts/generate_decisions_index.py` 从 "
        "`docs/decisions/*.md` 自动生成，请勿手动编辑。"
    )
    lines.append(
        "> 数据源是各决策文件的元信息块（H1 标题、`会议 ID`、`状态`、"
        "`决策类型`、`生成时间`、`原始需求`、`会议目标`、`## 行动项`）。"
    )
    lines.append("")

    if not sorted_items:
        lines.append("（暂无决策记录。）")
        return "\n".join(lines).rstrip() + "\n"

    lines.append("## 按日期")
    lines.append("")
    lines.append("| 日期 | 标题 | 决策类型 | 轮次 | 行动项 | 链接 |")
    lines.append("|---|---|---|---|---|---|")
    for s in sorted_items:
        link = f"{link_prefix}{s.filename}"
        title = _escape_md_cell(s.title)
        conclusion = s.conclusion_type or "（未给出）"
        lines.append(
            f"| {s.date or '（未知）'} "
            f"| {title} "
            f"| `{conclusion}` "
            f"| {s.rounds} "
            f"| {s.action_items_count} "
            f"| [{s.filename}]({link}) |"
        )
    lines.append("")

    lines.append("## 按决策类型分组")
    lines.append("")
    groups: dict[str, list[DecisionRecordSummary]] = {}
    for s in sorted_items:
        groups.setdefault(s.conclusion_type or "（未给出）", []).append(s)
    for conclusion in sorted(groups):
        bucket = groups[conclusion]
        lines.append(f"### `{conclusion}` · {len(bucket)} 份")
        lines.append("")
        for s in bucket:
            link = f"{link_prefix}{s.filename}"
            lines.append(f"- [{s.date} · {s.title}]({link})")
            if s.requirement:
                snippet = s.requirement.replace("\n", " ").strip()
                if len(snippet) > 90:
                    snippet = snippet[:90] + "…"
                lines.append(f"  - 原始需求：{snippet}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _escape_md_cell(text: str) -> str:
    """Make a string safe to drop inside a markdown table cell."""

    return text.replace("|", "\\|").replace("\n", " ").strip()
