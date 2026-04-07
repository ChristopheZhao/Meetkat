# P0-1 Interface Freeze: MAS Core <-> Runtime

- Date: 2026-03-01
- Stage: P0
- Status: draft-freeze

术语说明：

1. 本文中的 `Runtime` 属于接口冻结阶段的历史表达。
2. 当前 MAS 内核顶层分层与 SoT 约束以后续基线文档 `docs/tech/MAS-harness-architecture-baseline.md` 为准。
3. 后续实现不得再将 `State` 或 `Runtime` 作为顶层架构层名。

## 1. Goal

冻结 MAS 算法层与后端运行时之间的最小稳定接口，确保：

1. 算法可独立迭代。
2. 运行时可后续生产化替换。
3. 前期 Demo 不被后期重构推翻。

## 2. Layer Boundary

1. MAS Core
- 负责：规划、协作、收敛判断。
- 不负责：连接管理、事件重放、鉴权、落库。

2. Runtime
- 负责：WS/SSE、EventBus、会话状态、回放恢复、持久化。
- 不负责：方案内容生成、推理路径控制、评分算法细节。

3. Guardrail Control
- 负责：预算、超时、权限、人工介入、终止门控。
- 不负责：智能决策主路径。

运行时最小组件：

1. `Room Runtime`
2. `Agent Executor`
3. `Event Bus`
4. `Shared Blackboard`
5. `Transcript Store`
6. `Consensus Service`

## 3. Stable Strategy SPI

```ts
interface PlanningStrategy {
  plan(ctx: DecisionContext): Promise<PlanResult>
}

interface CoordinationStrategy {
  nextAction(ctx: DecisionContext): Promise<CoordinationAction>
}

interface ConsensusStrategy {
  evaluate(ctx: DecisionContext): Promise<ConsensusResult>
}

interface GuardrailPolicy {
  check(ctx: DecisionContext): Promise<GuardrailDecision>
}

interface FallbackPolicy {
  check(ctx: DecisionContext): Promise<FallbackDecision> // only disaster path
}
```

## 4. Stable Event Envelope (v1)

```json
{
  "schema_version": "1.0",
  "event_id": "evt_xxx",
  "room_id": "room_xxx",
  "room_seq": 123,
  "producer_id": "agent.host.1",
  "role": "host",
  "event_type": "agent.message",
  "ts_ms": 1760000000000,
  "idempotency_key": "room_xxx:producer:nonce",
  "payload": {}
}
```

说明：
1. `room_seq` 仅由 Runtime 分配，房间内全序。
2. MAS Core 仅发布语义事件，不管理序列号。

## 5. Required Event Types (v1)

1. `room.started`
2. `agent.joined`
3. `agent.message`
4. `message.chunk`
5. `message.commit`
6. `agent.handoff`
7. `agent.challenge`
8. `agent.summary`
9. `human.message`
10. `human.override`
11. `consensus.check`
12. `meeting.ended`

## 6. Room-level Topic Contract

1. 订阅键：`room_id`
2. 生产建议：`room.{room_id}.events`（或共享 topic + key=room_id）
3. 客户端只消费本 room 事件流

## 7. Runtime Recovery Contract

1. 语义：`at-least-once + idempotency`
2. 重连参数：`resume_token + last_acked_seq`
3. 恢复流程：先 replay gap，再切回实时流

## 8. Non-goals in P0-1

1. 不冻结具体模型 SDK。
2. 不冻结具体消息中间件实现（Redis/Kafka 可替换）。
3. 不冻结收敛权重具体数值（先经验值，后评测校准）。
4. 不冻结完全自由角色生成机制，一期只支持固定角色语义 + 动态实例组合。

## 9. Acceptance for P0-1

1. Hybrid 作为唯一主算法可通过同一 SPI 稳定运行。
2. 更换 EventBus 实现不影响 MAS Core 代码。
3. Demo 可展示 agent 自动化讨论实时实况，并支持人类加入与介入。
4. 核心会议链路验证必须使用真实模型调用；mock 仅用于基础设施单测。

注：
1. 自由讨论、Debate+Judge 仅作为评测基线，不作为一期并行实现目标。
2. 详细通信拓扑与协作机制见 `ADR-002` 和 `ADR-003`。
