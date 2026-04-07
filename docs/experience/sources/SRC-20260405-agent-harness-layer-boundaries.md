# Source Retrospective: Agent Harness Layer Boundary Correction

## Background

本轮讨论里，除了 `P1` 会议拓扑本身，还多次出现了更底层的架构漂移风险：

1. 把 `Runtime` 当成顶层层名
2. 把 `State` 当成与 `Memory` 并列的层
3. 让 `Control` 越界承担编排语义
4. 让 `Adapter`/helper 长成竞争主线

这些漂移如果不及时收口，后续实现即使局部看起来“能跑”，也会不断把 MAS harness 拉回到混层结构。

## Key Turning Points

1. 明确了顶层只允许：
   - `Memory`
   - `Orchestration`
   - `Control`
   - `Governance`
2. 明确了：
   - `Runtime/Harness` 不是层，只是执行线束/宿主循环
   - `State` 不是层，`state = projection(memory_at_time_t)`
   - `RoomEventJournal` 是 bounded context 内唯一 SoT
3. 明确了：
   - `Control` 只负责 lifecycle、retry、timeout、override、resume、fallback gate
   - “谁发言、回答什么、是否继续、是否收敛”必须回到 `Orchestration`
4. 明确了：
   - adapter/helper 只能做边界映射，不能缓存第二事实链、不能内嵌第二套 control/orchestration 语义

## Achieved State

1. 已有总基线文档：
   - `docs/tech/MAS-harness-architecture-baseline.md`
2. 已有 P1 会议拓扑基线：
   - `docs/tech/P1-host-led-meeting-topology-baseline.md`
3. 当前子计划已按这些边界重规划：
   - `docs/plans/active/PLAN-20260405-003.md`

## Why This Matters

这条经验和具体的会议拓扑无关，它适用于所有 agent harness 重构：

- 不要用 `Runtime`、`State`、`Adapter` 这种实现/边界词冒充顶层架构层
- `Memory` 是基底，`state` 只是映射
- `Control` 可以 gate，但不能偷走编排语义
- `Orchestration` 必须真正拥有任务推进和语义收敛判断
- adapter/helper 只能贴边，不得长成第二主线
