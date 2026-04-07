# P1 Host-Led Meeting Topology Baseline

- Date: 2026-04-05
- Scope: Meetkat P1 MAS 会议内核
- Status: active-baseline
- Applies to: P1 后续会议编排、角色实例化、控制边界、运行验证

## 1. Goal

本文件用于冻结 P1 阶段的会议编排主线，纠正当前实现中“固定轮次 + 固定 roster + 固定顺序”的模板化偏差。

后续实现必须满足：

1. P1 仍然是 multi-agent 系统，不允许用规则模板替代编排层。
2. `Control` 只做运行约束，不做会议语义决策。
3. `Host` 是唯一常驻 active agent。
4. specialist agents 必须按会议议题动态实例化与加入。
5. `Recorder` 优先降为 memory-backed synthesis capability，而不是常驻 speaking agent。

## 2. Binding Rules

以下规则为 P1 会议内核约束，不是建议：

1. 会中常驻 active agent 只允许 `Host`。
2. `MeetingPlanner / RolePlanner / RoleValidator / AgentFactory` 属于会前工作流，不属于会中常驻 speaker roster。
3. 会中 specialist roster 必须由会前工作流与会中 Host 编排共同决定，不得固定为 `host / pro / con / recorder` 常驻阵容。
4. `Recorder` 不得默认作为常驻 speaking role；若需要显式出声，必须由后续验证证明其必要性。
5. 会议结束必须产出显式结论类型，不得以“跑满固定轮次”直接充当会议语义。
6. 不允许再以固定 `4` 轮、固定角色顺序、固定 `critical_decision_round=round>=4` 作为编排主线。
7. P1 采用 `Host-led moderated hub-and-spoke topology`，不采用纯中心化硬工作流，也不采用去中心化 mesh。
8. `Control` 只允许管理 lifecycle、timeout、retry、cancel、override、resume、fallback eligibility、termination gate。
9. “谁发言、回答什么、是否继续辩论、是否需要专家、是否已收敛”必须由 `Orchestration` 决定。
10. `Memory` 仍为唯一事实基底；`state` 仍只作为 projection 存在。
11. P1 的 room-start 产品主线只有一条；validation / smoke / acceptance 只能复用该主线，不得在核心中并列固化第二种 entry mainline。
12. operator-visible 会前合同只负责声明 room-start 所需前提、缺失输入与进入条件，不负责引入 validation-specific 产品入口语义。
13. “缺什么信息、是否需要更多专家、是否继续辩论、是否已收敛”仍然是 agent-owned orchestration 决策，不得被会前合同裁定机制接管。

## 3. Topology

P1 拓扑定义为：

`pre-room planning workflow -> Host -> dynamic specialists -> memory-backed synthesis`

说明：

1. `Host` 是唯一中心协调者。
2. specialists 不直接形成自由 mesh。
3. specialists 可通过 handoff/challenge/blackboard 间接协作。
4. 后续若演进到半中心化或 mesh，必须作为下一阶段能力，不得在 P1 里偷渡。
5. validation / smoke / acceptance harness 必须通过显式 `operator_context` 复用同一 `pre-room planning -> room-start contract assessment -> room start` 主线，不得在 P1 核心中长出并列的 validation-specific scope。

## 4. Agent Model

### 4.1 Pre-room workflow

会前工作流包含：

1. `RequirementPlanner`
2. `RolePlanner`
3. `RoleValidator`
4. `AgentFactory`

输出：

1. meeting objective
2. initial focus
3. initial constraints
4. open questions
5. candidate specialist roster
6. agent instance profile（avatar / capability profile / prompt contract / TTL / budget / join reason）

补充约束：

1. 会前允许存在声明式的 room-start contract assessment，用于区分 `hard prerequisite`、`contextual open question`、`missing operator inputs`。
2. 该裁定输出只服务于进入 room 前的合同清理，不得扩张成第二个编排模块或产品 IA 中的第二类会议入口。
3. validation-specific 默认合同若需要存在，应位于 evaluation/test harness，通过显式 `operator_context` 注入，不应成为 P1 核心拓扑的一部分。

### 4.2 In-room active agents

会中 active agents：

1. `Host`
2. `SpecialistAgent[]`

`Host` 负责：

1. 读取当前 memory/projection
2. 决定下一步需要什么信息
3. 决定是否需要新增 specialist
4. 决定下一位谁发言
5. 决定是继续 challenge、补信息、总结，还是产出结论

`SpecialistAgent` 负责：

1. 仅围绕自己的 capability profile 发言
2. 在预算/TTL 内完成针对性分析
3. 通过显式 handoff/challenge 向 Host 回流结果

## 5. Recorder Position

P1 默认不将 `Recorder` 实现为常驻 speaking agent。

P1 采用：

`memory-backed synthesis capability`

它必须稳定产出：

1. candidate decision
2. action items
3. open questions
4. agreement summary
5. disagreement summary
6. explicit conclusion type

说明：

1. 这些产物仍是一等产物，不得退化成会后临时拼接文本。
2. 若后续验证表明“显式 Recorder 发言”对可解释性或用户体验是必要的，可在后续阶段再升格。

## 6. Control Boundary

`Control` 负责：

1. node/task/session lifecycle
2. timeout/retry/cancel
3. override/resume
4. fallback eligibility
5. termination gate

`Control` 不负责：

1. 硬编码固定角色顺序
2. 直接规定某一轮必须 escalation
3. 用固定轮次直接定义“会议语义完成”
4. 判断语义层面的“是否已经得出结论”

## 7. Conclusion Contract

会议结束时，必须输出显式结论类型。P1 至少支持：

1. `decision_reached`
2. `blocked_missing_information`
3. `human_escalation_required`
4. `runtime_failure`

禁止：

1. 直接用“meeting reached the maximum MVP round budget”充当会议完成语义
2. 直接用固定轮数作为“已进入决策阶段”的代理信号

## 8. Non-Goals For P1

P1 不做：

1. 完全自由群聊
2. 分布式 agent mesh
3. 无限角色市场
4. 基于固定 4 轮的模板编排

## 9. Implementation Consequences

当前实现中以下假设已失效，后续应停止沿用：

1. 固定 `host / pro / con / recorder` 常驻 roster
2. 固定 `host -> pro -> con -> recorder` 发言顺序
3. 固定 `max_rounds=4` 作为会议语义
4. 固定 `critical_decision_round=round>=4`

后续重构主线应围绕：

1. 会前规划工作流落地
2. Host 常驻编排
3. 动态 specialist 实例化
4. synthesis capability 下沉到 memory/projection 路径
5. Control-Orchestration contract 重写
