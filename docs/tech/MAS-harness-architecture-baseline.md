# MAS Harness Architecture Baseline

- Date: 2026-03-29
- Scope: Meetkat MAS 内核
- Status: active-baseline
- Applies to: 后续所有 MAS 内核实现、重构、接口扩展

## 1. Goal

本文件用于冻结 Meetkat 当前阶段的 MAS harness 架构主线，避免后续实现继续在 `runtime`、`state`、`adapter` 等术语上漂移。

后续实现必须满足：

1. 主线清晰，只有一条事实链与一条执行链。
2. SoT 来源唯一且无歧义。
3. `memory` 是基底层，`state` 只是基于 memory 的映射。
4. `adapter` 只能作为边界扩展，不得膨胀为竞争主线。
5. `runtime/harness` 只能做执行线束，不得重新长成业务层。

## 2. Binding Rules

以下规则为架构约束，不是建议：

1. 顶层主架构只允许：
   - `Memory`
   - `Orchestration`
   - `Control`
   - `Governance`
2. `Runtime` 不是层名。
3. `State` 不是层名。
4. `Adapter` 不是层名。
5. 在 `room/session` 这个 bounded context 内，唯一 SoT 是 `RoomEventJournal`。
6. `snapshot / transcript / blackboard / summary / working set` 一律视为 projection，不得提升为事实源。
7. `guardrail/gating` 归 `Control` 子域，不单独升格为顶层主层。
8. `execution` 是 `Orchestration` 的执行臂，不得并入 `Control` 作为新的决策主线。
9. 任何层的 adapter/helper 都不得缓存或维护第二条事实链。
10. 未写入 `RoomEventJournal` 的信息，不得被视为当前 room 的正式事实。
11. room-start 之前允许存在声明式的入口合同裁定能力，但它只能服务于同一编排主线，不得演化成产品主线之外的第二条入口主线。
12. evaluation / smoke / acceptance harness 必须复用同一核心会议主线，不得在 MAS 核心内长期固化 validation-specific defaults、entry semantics 或平行 owner。

## 3. Core Mental Model

Meetkat 的 MAS harness 采用以下主线：

`Runtime/Harness -> read Memory -> invoke Orchestration -> pass Control gate -> execute -> append back to Memory -> emit Governance signals`

说明：

1. `Runtime/Harness` 是执行线束，不是架构层。
2. `Memory` 是基底层。
3. `Orchestration` 是智能主线。
4. `Control` 是运行约束主线。
5. `Governance` 是横切观察与治理。

一句话定稿：

**Meetkat MAS 内核采用 `Memory-centered, Orchestration-led, Control-gated, Governance-observed, Runtime-as-harness`。**

## 4. Layer Definition

### 4.1 Memory

`Memory` 是当前 MAS 内核的基底层。

它负责：

1. `RoomEventJournal`
2. replay / checkpoint / resume 所需的基础记录
3. room 级 working memory
4. long-term memory
5. 所有 projection 的生成基础

它不负责：

1. 下一步做什么
2. 是否允许继续执行
3. 终止/超时/回退判定
4. 策略治理

关键约束：

1. `RoomEventJournal` 是 room/session bounded context 内唯一 SoT。
2. 所有 projection 都必须从 journal 派生。
3. long-term memory 可以提供上下文，但不能改写当前 room 事实。

### 4.2 State Is Not A Layer

`state` 在本项目中只表示某个时刻的当前视图，不是顶层架构层。

本项目里，`state` 只允许以以下形式出现：

1. `RoomSnapshot`
2. `TranscriptView`
3. `BlackboardView`
4. `WorkingSet`
5. checkpointed session view

统一定义：

`state = projection(memory_at_time_t)`

因此：

1. 不建立 `State Plane`
2. 不建立 `State Store` 作为独立事实源
3. 不允许 `state` 与 `memory` 并列争夺架构边界

### 4.3 Orchestration

`Orchestration` 是 MAS 的智能主线。

它负责：

1. `plan`
2. `delegation`
3. `handoff`
4. `role sequencing`
5. 角色间协作策略
6. 候选结论与内容生成
7. 会前入口合同裁定与 `validated_context` 生成

它不负责：

1. budget/timeout/override/fallback gate
2. journal append
3. projection 存储
4. tracing/audit

约束：

1. execution 是编排层的执行臂，不单独升格成顶层层。
2. 编排产物只有在写入 journal 后，才成为 room 正式事实。
3. 会前入口合同裁定的职责是声明“进入 room 需要哪些已知前提”和“哪些输入仍缺失”，不是定义第二套会议语义或第二个编排 owner。
4. validation-specific operator context 可以由外部 harness 注入，但其默认合同不应继续沉淀为核心入口 owner。

### 4.4 Control

`Control` 是运行约束主线。

它负责：

1. lifecycle
2. phase transition
3. budget
4. timeout
5. retry
6. cancel
7. override
8. stop condition
9. fallback eligibility
10. runtime guardrails / gating

它不负责：

1. 智能内容生成
2. 候选方案优劣判断
3. 长期记忆建模
4. tracing/eval/audit

说明：

1. `Guardrail/Gating` 是 `Control` 子域。
2. `Control` 负责“当前执行能不能继续”，不负责“内容应该是什么”。

### 4.5 Governance

`Governance` 是横切治理层。

它负责：

1. tracing
2. evals
3. audit
4. policy version
5. observable trajectory
6. release gate evidence
7. evaluation / smoke / acceptance harness

它不负责：

1. 当前 step 的实时执行准入
2. 编排内容生成
3. journal 事实写入

说明：

1. 运行时门控归 `Control`
2. 门控规则的版本化、审计、评测归 `Governance`
3. evaluation harness 可以复用核心 room path 做验收，但不应把 validation-specific 入口语义反向固化进核心架构

## 5. Runtime/Harness Position

`Runtime/Harness` 不是层，而是执行线束。

它只负责：

1. 驱动主循环
2. 协调层间调用顺序
3. 管理恢复入口
4. 组装依赖

它不负责拥有：

1. 事实源
2. 策略判断
3. 业务规则
4. 决策权

因此：

1. 正式架构中不允许出现 `Runtime Plane`
2. `runtime` 只能作为实现容器术语出现
3. 后续重构目标是把 `runtime` 壳持续变薄

## 6. Ports / Adapters Rule

`Ports/Adapters` 是边界模式，不是顶层主层。

典型示例：

1. model provider adapter
2. tool adapter
3. journal storage adapter
4. memory backend adapter
5. transport adapter

强约束：

1. adapter 只能映射边界协议，不得承载业务主逻辑
2. adapter 不得维护独立事实状态
3. adapter 不得实现第二套 fallback / control / orchestration 主线
4. helper 不得通过缓存、旁路写入、隐式恢复逻辑制造第二事实源

## 7. SoT Contract

在 `room/session` 这个 bounded context 内：

1. 唯一 SoT：`RoomEventJournal`
2. projection：
   - `RoomSnapshot`
   - `TranscriptView`
   - `BlackboardView`
   - `SummaryView`
3. 不是 SoT：
   - provider raw output
   - in-memory convenience cache
   - UI state
   - long-term semantic memory
   - generated summary

判定原则：

1. 能 replay
2. 可审计
3. 可重建 projection
4. 全部写入路径单一

缺一不可。

## 8. Current Repo Mapping

当前仓库按本基线映射如下：

1. `Orchestration`
   - `src/decision_room/runtime/room_executor.py`
   - `src/decision_room/mas/hybrid.py`
   - `src/decision_room/mas/interfaces.py`

2. `Control`
   - `src/decision_room/policies/guardrail.py`
   - `src/decision_room/policies/fallback.py`
   - `src/decision_room/routing/model_router.py` 的 gate-related 部分

3. `Memory`
   - `src/decision_room/runtime/events.py`
   - `src/decision_room/runtime/room_projector.py`
   - `src/decision_room/runtime/room_models.py`
   - `src/decision_room/runtime/room_runtime.py` 中 journal/projection 相关部分

4. `Governance`
   - 当前基本未正式落地

5. `Runtime/Harness`
   - `src/decision_room/runtime/room_runtime.py` 当前承担薄 harness 之外的多种职责，需要继续拆薄

## 9. Current Architectural Debt

当前最大的混层点是 `src/decision_room/runtime/room_runtime.py`。

它当前同时承担：

1. harness 循环驱动
2. journal append
3. projection 更新
4. control 判定
5. subscriber fanout
6. 局部 orchestration 触发

这违反了本基线。

后续重构方向必须是：

1. 先固化 `Memory`
2. 再收口 `Control`
3. 再把 `Orchestration` 从 runtime 壳里抽清
4. 最后把 runtime 壳压到最薄

## 10. Implementation Guardrails

后续所有实现必须遵守：

1. 不得新增 `State Plane`
2. 不得新增 `Runtime Plane`
3. 不得新增 `Adapter Plane`
4. 不得新增第二条事实链
5. 不得让 helper/adapter 变成竞争主线
6. 不得把 fallback 变成常态 else 路径
7. 不得让未入 journal 的信息成为正式 room 事实
8. 不得把 `Control` 扩张为智能主线
9. 不得把 `Governance` 拉入主执行环

## 11. Relation To Existing Docs

本文件是当前 MAS harness 总架构基线。

与现有文档关系如下：

1. `docs/adr/ADR-001-hybrid-mas-governance.md`
   - 保留其 `Hybrid MAS + Guardrail Control` 的核心方向
   - 但其中的 `运行时层` 仅作为历史术语理解，不再作为顶层层名

2. `docs/tech/P0-1-mas-runtime-interface-freeze.md`
   - 其中 `Runtime` 和 `Guardrail Control` 的接口冻结继续有效
   - 但后续实现应以本文件的顶层分层为准

若出现术语冲突，以本文件为准。
