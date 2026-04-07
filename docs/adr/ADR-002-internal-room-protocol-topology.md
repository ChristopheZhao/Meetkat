# ADR-002: Internal Room Protocol and Hybrid Communication Topology

- Status: Proposed (Draft)
- Date: 2026-03-10
- Owners: Architecture + Agent Runtime + Frontend

## Context

目标产品是一个人类与多 Agent 共同参与的在线会议室。

已确认前提：

1. 一期以 agent 自动化讨论为主，但人类必须可以加入、插话、追问、投票、裁决。
2. 会议过程必须可见，可回放，可恢复。
3. `FSM` 仅做护栏控制，不做 MAS 智能核心。
4. 一期优先验证 LLM-based MAS 的运行时协作，而不是训练型 MARL。

当前争议点：

1. 内部通信拓扑应该选择中心化、去中心化，还是混合式。
2. 一期内部运行时是否直接采用 `A2A`。
3. `MCP`、`A2A`、内部房间协议之间的边界如何划分。
4. 房间内是否应该全量广播所有上下文。

## Decision

一期采用 **Hybrid Communication Topology + Internal Room Protocol**。

### 1. Hybrid 拓扑

1. **控制面（Centralized Control Plane）**
- 管阶段、预算、超时、人工介入、终止。
- 不参与内容推理。

2. **执行面（Event-driven Execution Plane）**
- Agent 通过房间级事件流发言、handoff、challenge、summary。
- 允许受控自由协作，不采用纯工作流顺序编排。

3. **收敛面（Centralized Convergence Plane）**
- 独立执行 `consensus.check`、结束判定、人工 override 接入。
- 不把收敛逻辑塞进某个具体 agent。

### 2. Internal Room Protocol

一期内部主体协议定义为 `room event protocol`，服务房间内实时协作与前端展示。

核心原则：

1. 房间是第一聚合单元，所有实时协作按 `room_id` 隔离。
2. 事件日志与用户可读 transcript 分层保存。
3. 协议优先服务“可见、可回放、可恢复”，而不是先追求复杂分布式自治。

### 3. Protocol Boundary

1. **Internal Room Protocol**
- 一期主协议。
- 负责房间内 agent/human 协作、实时事件、回放、状态同步。

2. **MCP**
- 仅做工具与资源访问。
- 不承担 agent-agent 通信语义。

3. **A2A**
- 作为未来跨系统、跨框架、跨信任边界的外部互操作层。
- 一期不作为内部 runtime 内核。

## Minimal Event Model (Phase 1)

### Stable Envelope

每条事件至少包含：

1. `event_id`
2. `room_id`
3. `room_seq`
4. `producer_id`
5. `role`
6. `event_type`
7. `ts_ms`
8. `idempotency_key`

### Required Semantics

一期最小事件语义集：

1. `room.started`
2. `agent.message`
3. `agent.handoff`
4. `agent.challenge`
5. `agent.summary`
6. `human.message`
7. `human.override`
8. `consensus.check`
9. `meeting.ended`

前端流式展示补充语义：

1. `message.chunk`
2. `message.commit`

## Context Propagation Policy

默认不做全量广播。

采用 **shared blackboard + per-agent private context + summarized handoff**：

1. **Shared Blackboard**
- 当前议题
- 当前阶段
- 最近结论
- 争议点
- 行动项草稿
- artifact 引用

2. **Per-agent Private Context**
- 角色私有指令
- 本轮局部推理上下文
- 工具调用细节

3. **Summarized Handoff**
- 只传目标角色所需摘要
- 只传必要的 transcript slice
- 只传关联 claim / artifact refs

## Rejected Options

### 1. Pure Centralized Orchestrator

不采用。

原因：

1. 会把协作压成硬工作流。
2. 会降低 agent 间 challenge 与 handoff 自由度。
3. 后续扩展为更自主的 MAS 时重构成本高。

### 2. Pure Decentralized Peer Mesh

不采用。

原因：

1. 不利于会议状态回放与前端一致性。
2. 人类裁决与终止条件难统一。
3. 实时会议产品需要清晰的阶段、收敛、恢复语义。

### 3. A2A-as-Internal-Core

一期不采用。

原因：

1. A2A 更适合跨系统互操作，不是房间内最小可控 runtime 的最佳起点。
2. 一期内部更需要稳定、可裁剪、可前端消费的 room event schema。

## Consequences

正向影响：

1. 保持 MAS 自主协作空间，同时保留产品级可控性。
2. 前端可以基于稳定事件模型构建实时会议室。
3. 未来可在边界层映射到 `A2A`，而不必一开始绑定外部协议。

代价：

1. 需要自行定义内部协议与映射层。
2. 需要显式设计事件语义、上下文传播、恢复机制。

## Implementation Notes

一期执行要求：

1. 房间内使用单一 `room_seq` 做全序。
2. 采用 `at-least-once + idempotency_key` 去重。
3. 支持 `resume_token + last_acked_seq + replay`。
4. 协议字段与事件语义先冻结，再推进前端与运行时并行开发。

## References

1. OpenAI Agents SDK multi-agent: https://openai.github.io/openai-agents-python/multi_agent/
2. OpenAI Agents SDK handoffs: https://openai.github.io/openai-agents-python/handoffs/
3. Anthropic multi-agent research system: https://www.anthropic.com/engineering/multi-agent-research-system
4. LangChain handoffs: https://docs.langchain.com/oss/python/langchain/multi-agent/handoffs
5. Google A2A announcement: https://developers.googleblog.com/en/a2a-a-new-era-of-agent-interoperability/
6. A2A specification v0.3.0: https://a2a-protocol.org/v0.3.0/specification/
7. MCP architecture: https://modelcontextprotocol.io/docs/learn/architecture
