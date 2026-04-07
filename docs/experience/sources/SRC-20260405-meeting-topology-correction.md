# Source Retrospective: P1 Meeting Topology Correction

## Background

本轮纠偏前，P1 会议主线已经具备了 `Memory / Orchestration / Control / Governance` 的大架构基线，但在具体会议语义上仍残留了强模板化实现：

1. 固定 `max_rounds=4`
2. 固定 `host / pro / con / recorder`
3. 固定 `host -> pro -> con -> recorder`
4. 固定 `critical_decision_round=round>=4`

这使系统虽然名义上是 multi-agent room runtime，实际上却越来越接近“规则驱动模板工作流”。

## Key Turning Points

1. 明确了 `Control` 和 `Orchestration` 的边界：
   - `Control` 只该负责 lifecycle、retry、timeout、override、resume、fallback gate
   - “谁发言、回答什么、是否继续、是否收敛”必须回到 `Orchestration`
2. 结合 PRD/ADR 收口后确认：
   - 一期不做 distributed mesh
   - 但也不能继续接受固定 roster + 固定顺序冒充 agentic 编排
3. 围绕 P1 agent model 重新定稿：
   - 会前：`RequirementPlanner -> RolePlanner -> RoleValidator -> AgentFactory`
   - 会中：只有 `Host` 常驻
   - specialists 按议题动态实例化
   - `Recorder` 优先降为 `memory-backed synthesis capability`

## Achieved State

1. 新基线文档已冻结：
   - `docs/tech/P1-host-led-meeting-topology-baseline.md`
2. 父计划已将旧的 `P1-3b/P1-3c` 延续路径标记为 `superseded`
3. 新子计划已建立：
   - `docs/plans/active/PLAN-20260405-003.md`

## Why This Matters

这次经验的核心不是“改了一个实现细节”，而是防止项目在 MVP 阶段从 agent harness 漂回规则工作流。

可复用的判断是：

- MVP 可以轻，但不能假
- 中心化 orchestrator 可以接受，但必须是 agentic 决策，不是固定脚本
- roster 可以受控，但必须动态实例化，不能把默认阵容写成唯一阵容
- `Recorder` 不一定非要做成常驻 speaker，但其结构化产物必须保留为一等结果
