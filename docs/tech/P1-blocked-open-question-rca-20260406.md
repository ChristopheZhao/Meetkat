# P1 Blocked Open-Question RCA

- Date: 2026-04-06
- Scope: post-B5 real-provider smoke results that repeatedly surface `blocked` / high-friction open-question outcomes
- Status: active-analysis

## 1. Question

为什么真实 smoke 里会反复出现如下模式：

1. 会议在会中识别出 `provider identity` / `success criteria` / `credential context` / 外部依赖类问题
2. 相似问题有时被判为 `blocked`
3. 相似问题有时又被判为 `candidate_ready`，但仍保留 open questions

是否需要先做根因分析，再决定后续改动边界？

结论：**需要。**

## 2. High-Confidence Findings

### 2.1 当前系统没有在会前结构化地区分两类问题

当前实现只有一个松散的 `open_questions` 通道，但没有显式区分：

1. `hard external prerequisite`
2. `contextual open question`

代码证据：

1. [`build_requirement_planner_prompts()`](/mnt/d/code/opensource/agent/meetkat/src/decision_room/orchestration/brief_planner.py) 只要求 planner “如果缺信息，就写入 `open_questions`”，没有分类字段、没有 blocker taxonomy、没有 resolvability 标注。
2. [`parse_requirement_planner_response()`](/mnt/d/code/opensource/agent/meetkat/src/decision_room/orchestration/brief_planner.py) 只把 `open_questions` 解析成 `list[str]`，没有结构化属性。
3. [`PreRoomPlanningWorkflow.plan_room()`](/mnt/d/code/opensource/agent/meetkat/src/decision_room/orchestration/pre_room_planning.py) 只是把 `brief.open_questions` 原样写入 planning artifacts。
4. [`HeuristicRolePlanner.plan_roles()`](/mnt/d/code/opensource/agent/meetkat/src/decision_room/orchestration/pre_room_planning.py) 只把 `open_questions` 当作 role keyword corpus 的一部分，不判断其是否属于会前 blocker。

这意味着系统把所有“未知项”都扔进同一个袋子里，后续只能依赖会中编排和模型自行解释其严重性。

### 2.2 当前 readiness 只回答“能不能跑”，不回答“是否具备本次房间所需外部前提”

代码证据：

1. [`_planner_readiness()`](/mnt/d/code/opensource/agent/meetkat/src/decision_room/runtime/http_api.py) 只暴露 planner 模式和 env 是否齐全。
2. [`_executor_missing_env()`](/mnt/d/code/opensource/agent/meetkat/src/decision_room/runtime/http_api.py) 只检查模型 target/env 缺失。
3. [`build_runtime_from_env()`](/mnt/d/code/opensource/agent/meetkat/src/decision_room/runtime/http_api.py) 生成的 `runtime_readiness` 不包含 scenario-specific prerequisite，例如：
   - 这次验证针对哪个 provider
   - “通过”到底如何判定
   - 需要哪些凭据/环境上下文
   - 是否存在 operator-supplied external dependency

因此，系统只知道“runtime bootstrap ready”，但不知道“这场会议是否已经具备开始讨论的外部前置条件”。

### 2.3 blocked 模式反复出现的根因，不是 Control，也不主要是结束语义

这个方向已经被后续修复验证过，不再是主因：

1. `configured round budget` 直接关会的问题已经修复。
2. `control_reason` / `orchestration_end_reason` 的职责已经拆分。
3. smoke 已能证明结束 gate 不再混入会议语义。

因此，当前反复出现的 `blocked`，根因不在 Control 边界，而在 **会前缺少“外部依赖预检 + blocker 分类”**。

## 3. New Evidence From Smoke

新增 smoke 诊断已经支持输出：

1. `meeting_end`
2. `round_diagnostics`
3. `blocked_dependency_analysis`

这带来两个关键证据：

### 3.1 blocked 原因会稳定落到外部依赖类

在真实 blocked 样本中，反复出现的信号集中在：

1. `provider_identity`
2. `success_criteria`
3. `credential_context`

这类信号更像 operator-visible preflight prerequisite，而不是需要 specialists 在会中继续辩论的知识分歧。

### 3.2 相同主题有时会被判为 contextual question，而不是 blocker

最新真实 smoke 样本也出现了：

1. `candidate_ready`
2. 但仍保留若干 open questions

这说明真正缺的不是“去掉 open questions”，而是：

1. 在 room start 前标定哪些问题是 `hard prerequisite`
2. 哪些问题只是 `contextual follow-up`

目前这一步完全依赖会中模型自行判断，所以同类问题会在不同 run 中表现为：

1. `blocked`
2. `follow_up_required`
3. `candidate_ready with contextual questions`

这正是系统边界未结构化，而不是单次模型输出异常。

## 4. Root Cause Statement

**当前 blocked open-question 模式反复出现的根因，是 Meetkat 在会前只有 requirement brief 和 runtime env readiness，却没有一层显式的 external-dependency / preflight diagnostic contract。**

更具体地说：

1. planner 只能生成非结构化 `open_questions`
2. pre-room workflow 不区分 blocker vs contextual unknown
3. runtime readiness 不携带 scenario-specific prerequisites
4. 于是同类外部依赖只能在会中被 specialists 和 synthesis 反复重提
5. 最终是否 `blocked` 取决于会中模型的临场解释，而不是系统级前置分类

## 5. Non-Root Causes

以下问题存在过，但不是当前 blocked 模式反复出现的根因：

1. fixed round / budget 直接充当会议语义
2. `control_reason` 与 `conclusion_reason` 混淆
3. 前端 fallback 或旧 fixed-role UI 假设
4. `Recorder` speaking role 残留

这些边界已经被收敛，不解释当前“同类外部依赖在会中重复暴露”的现象。

## 6. Consequence For Next Change

在 RCA 完成前，不应直接：

1. 压低 `blocked` 结果
2. 强行让 synthesis 更偏向 `candidate_ready`
3. 用 prompt 调整掩盖外部依赖

下一步更合理的改动边界应该是：

1. 新增 operator-visible preflight diagnostic contract
2. 在 room start 前区分：
   - `hard external prerequisite`
   - `contextual open question`
3. 只把后者留给 in-room orchestration

## 7. Recommended Next Slice

建议下一切片不是改会中语义，而是新增一个最小 preflight 层：

1. 输入：
   - user requirement
   - runtime readiness
   - operator-provided scenario context
2. 输出：
   - `ready_to_enter_room: bool`
   - `hard_prerequisites[]`
   - `contextual_open_questions[]`
   - `recommended_surface`
3. 行为：
   - `hard_prerequisites` 未满足时，不直接进入正常会议主线
   - `contextual_open_questions` 继续进入 planning / room

一句话总结：

**问题不是模型为什么老说 blocked，而是系统为什么没有在 room start 前把“硬前置条件”结构化地拦出来。**
