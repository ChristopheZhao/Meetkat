"""Tests for the decisions-index generator (`decision_room.runtime.decision_index`).

These tests cover the two pure functions of the module — `parse_decision_record`
and `render_index` — plus a smoke test that the actual on-disk decision records
under `docs/decisions/` parse cleanly.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from decision_room.runtime.decision_index import (
    DecisionRecordSummary,
    parse_decision_record,
    render_index,
)


SAMPLE_RECORD = """# 决策会议记录 · 示例议题

- 会议 ID：`room_abc123`
- 状态：ended（共 3 轮）
- 决策类型：consensus
- 生成时间：2026-05-20 12:34:56 CST

## 原始需求

> 我们是否应该把代码审查从同步改成异步？

**会议目标**：在不影响交付节奏的前提下评估异步代码审查的引入。

## 决策结论

**候选决策**：从下个 sprint 开始切换到异步代码审查。

## 行动项

- [ ] 运维负责发布新流程。
- [ ] 风险控制师监控前两个 sprint。
- [x] 工程团队更新 CONTRIBUTING.md。

## 各角色立场摘要

### 产品策略师 · `product_specialist`

- **核心主张**：异步评审能解锁交付节奏。
"""


REPO_ROOT = Path(__file__).resolve().parents[1]


class ParseDecisionRecordTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.tmp = Path(self.tmpdir.name)

    def _write(self, name: str, body: str) -> Path:
        path = self.tmp / name
        path.write_text(body, encoding="utf-8")
        return path

    def test_parses_complete_record(self) -> None:
        path = self._write("2026-05-20-sample.md", SAMPLE_RECORD)
        summary = parse_decision_record(path)
        assert summary is not None
        self.assertEqual(summary.title, "示例议题")
        self.assertEqual(summary.room_id, "room_abc123")
        self.assertEqual(summary.rounds, 3)
        self.assertEqual(summary.conclusion_type, "consensus")
        self.assertEqual(summary.generated_at, "2026-05-20 12:34:56 CST")
        self.assertEqual(summary.date, "2026-05-20")
        self.assertEqual(
            summary.requirement, "我们是否应该把代码审查从同步改成异步？"
        )
        self.assertIn("评估异步代码审查", summary.goal)

    def test_action_items_count_includes_both_checked_and_unchecked(self) -> None:
        path = self._write("2026-05-20-sample.md", SAMPLE_RECORD)
        summary = parse_decision_record(path)
        assert summary is not None
        # SAMPLE_RECORD has 2 unchecked + 1 checked under 行动项.
        self.assertEqual(summary.action_items_count, 3)

    def test_action_items_only_counted_under_行动项_section(self) -> None:
        body = (
            SAMPLE_RECORD
            + "\n\n## 决策备忘\n\n- [ ] 不应当被计入行动项。\n- [ ] 也不应当被计入。\n"
        )
        path = self._write("2026-05-20-sample.md", body)
        summary = parse_decision_record(path)
        assert summary is not None
        # Still 3 — the checklist outside 行动项 must not leak into the count.
        self.assertEqual(summary.action_items_count, 3)

    def test_returns_none_for_non_decision_record(self) -> None:
        path = self._write(
            "not-a-record.md",
            "# 一篇普通笔记\n\n这并不是决策会议记录。\n",
        )
        self.assertIsNone(parse_decision_record(path))

    def test_missing_optional_fields_default_to_empty(self) -> None:
        body = "# 决策会议记录 · 最小记录\n\n（什么字段都没有。）\n"
        path = self._write("2026-05-20-minimal.md", body)
        summary = parse_decision_record(path)
        assert summary is not None
        self.assertEqual(summary.title, "最小记录")
        self.assertEqual(summary.room_id, "")
        self.assertEqual(summary.rounds, 0)
        self.assertEqual(summary.conclusion_type, "")
        self.assertEqual(summary.requirement, "")
        self.assertEqual(summary.goal, "")
        self.assertEqual(summary.action_items_count, 0)

    def test_filename_without_date_prefix_yields_empty_date(self) -> None:
        path = self._write("undated-record.md", SAMPLE_RECORD)
        summary = parse_decision_record(path)
        assert summary is not None
        self.assertEqual(summary.date, "")

    def test_real_records_parse(self) -> None:
        decisions_dir = REPO_ROOT / "docs" / "decisions"
        records = [
            p
            for p in sorted(decisions_dir.glob("*.md"))
            if p.name not in {"INDEX.md", "README.md"}
        ]
        # Sanity: at least one real record exists in the repo.
        self.assertGreater(len(records), 0)
        for path in records:
            summary = parse_decision_record(path)
            self.assertIsNotNone(
                summary,
                msg=f"failed to parse real record {path.name}",
            )
            assert summary is not None
            self.assertTrue(summary.title, msg=path.name)
            self.assertTrue(summary.room_id, msg=path.name)
            self.assertGreater(summary.rounds, 0, msg=path.name)
            self.assertTrue(summary.conclusion_type, msg=path.name)
            self.assertTrue(summary.date, msg=path.name)


class RenderIndexTests(unittest.TestCase):
    def _summary(
        self,
        *,
        date: str = "2026-05-20",
        filename: str = "2026-05-20-sample.md",
        title: str = "示例议题",
        conclusion: str = "consensus",
        rounds: int = 3,
        action_items: int = 2,
        requirement: str = "我们是否应该把代码审查从同步改成异步？",
        room_id: str = "room_abc",
    ) -> DecisionRecordSummary:
        return DecisionRecordSummary(
            path=Path(filename),
            filename=filename,
            date=date,
            title=title,
            room_id=room_id,
            rounds=rounds,
            conclusion_type=conclusion,
            generated_at=f"{date} 00:00:00 CST",
            requirement=requirement,
            goal="在不影响交付节奏的前提下评估异步代码审查的引入。",
            action_items_count=action_items,
        )

    def test_empty_input_produces_placeholder(self) -> None:
        rendered = render_index([])
        self.assertIn("# 决策记录索引", rendered)
        self.assertIn("暂无决策记录", rendered)
        self.assertNotIn("| 日期 |", rendered)

    def test_renders_main_table_and_grouping(self) -> None:
        items = [
            self._summary(
                date="2026-05-20",
                filename="2026-05-20-a.md",
                title="A",
            ),
            self._summary(
                date="2026-05-22",
                filename="2026-05-22-b.md",
                title="B",
                conclusion="follow_up_required",
            ),
        ]
        rendered = render_index(items)
        # Main table contains both rows
        self.assertIn("| 2026-05-22 | B |", rendered)
        self.assertIn("| 2026-05-20 | A |", rendered)
        # Grouped by conclusion_type, alphabetically
        self.assertIn("### `consensus`", rendered)
        self.assertIn("### `follow_up_required`", rendered)
        # B (2026-05-22) appears before A (2026-05-20) in the main table
        b_idx = rendered.index("| 2026-05-22 | B |")
        a_idx = rendered.index("| 2026-05-20 | A |")
        self.assertLess(b_idx, a_idx)

    def test_byte_identical_on_repeat(self) -> None:
        items = [
            self._summary(filename="2026-05-20-a.md", title="A"),
            self._summary(
                date="2026-05-22", filename="2026-05-22-b.md", title="B"
            ),
        ]
        first = render_index(items)
        # Re-call with the same input in reversed order — the renderer's
        # internal sort must produce identical bytes.
        second = render_index(list(reversed(items)))
        self.assertEqual(first, second)

    def test_pipe_in_title_is_escaped(self) -> None:
        items = [
            self._summary(
                date="2026-05-20",
                filename="2026-05-20-a.md",
                title="A | B",
            ),
        ]
        rendered = render_index(items)
        self.assertIn("A \\| B", rendered)

    def test_long_requirement_is_truncated_in_group(self) -> None:
        long_req = "需求" * 200
        items = [self._summary(requirement=long_req)]
        rendered = render_index(items)
        # Truncated body should appear in the grouped section
        self.assertIn("…", rendered)


if __name__ == "__main__":
    unittest.main()
