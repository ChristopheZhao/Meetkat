# 决策记录索引

> 共 3 份决策记录，按日期倒序排列。
>
> 本文件由 `scripts/generate_decisions_index.py` 从 `docs/decisions/*.md` 自动生成，请勿手动编辑。
> 数据源是各决策文件的元信息块（H1 标题、`会议 ID`、`状态`、`决策类型`、`生成时间`、`原始需求`、`会议目标`、`## 行动项`）。

## 按日期

| 日期 | 标题 | 决策类型 | 轮次 | 行动项 | 链接 |
|---|---|---|---|---|---|
| 2026-05-31 | Feasibility and scope of the proposed system改造 | `human_decision_needed` | 1 | 4 | [2026-05-31-Feasibility-and-scope-of-the-proposed-system改造.md](2026-05-31-Feasibility-and-scope-of-the-proposed-system改造.md) |
| 2026-05-28 | Strategic microservice decomposition for delivery continuity | `follow_up_required` | 3 | 4 | [2026-05-28-微服务边界拆分决策.md](2026-05-28-微服务边界拆分决策.md) |
| 2026-05-28 | AI编程助手选型与落地策略决策会 | `follow_up_required` | 3 | 3 | [2026-05-28-AI编程助手引入策略.md](2026-05-28-AI编程助手引入策略.md) |

## 按决策类型分组

### `follow_up_required` · 2 份

- [2026-05-28 · Strategic microservice decomposition for delivery continuity](2026-05-28-微服务边界拆分决策.md)
  - 原始需求：我们一个 18 人的全栈团队正在快速增长，准备把单体应用按业务能力拆出 2-3 个微服务。需要决定：本季度先拆哪条边界、如何切迁移节奏、用什么 IPC 协议、数据库/事件队列怎么分…
- [2026-05-28 · AI编程助手选型与落地策略决策会](2026-05-28-AI编程助手引入策略.md)
  - 原始需求：我们是一家国内 30 人规模的后端 SaaS 公司研发团队（Go + Python，Kubernetes 部署），正在评估给所有工程师引入 AI 编程助手（Cursor / Con…

### `human_decision_needed` · 1 份

- [2026-05-31 · Feasibility and scope of the proposed system改造](2026-05-31-Feasibility-and-scope-of-the-proposed-system改造.md)
  - 原始需求：我们要不要做这个改造
