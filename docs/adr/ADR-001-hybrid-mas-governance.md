# ADR-001: Hybrid MAS Architecture With Guardrail FSM

- Status: Accepted (Draft)
- Date: 2026-03-01
- Owners: Product + Architecture

术语说明：

1. 本文中的 `运行时层（Runtime）` 属于历史表达。
2. 当前 MAS 内核顶层分层与 SoT 约束以后续基线文档 `docs/tech/MAS-harness-architecture-baseline.md` 为准。
3. 后续实现不得再将 `Runtime` 作为顶层架构层名。

## Context

目标产品是一个人类与多 Agent 混合讨论的在线会议室，核心目标是让讨论从“发散”走向“可执行共识”。

争议点：是否将 FSM（规则/状态机）作为 multi-agent 技术核心。

结论前提：

1. 规则驱动适合边界治理，不适合作为智能核心。
2. 纯去中心化难以保证收敛质量与会话可控性。
3. 纯中心化会限制 Agent 自主协作与扩展性。

## Decision

采用 **Hybrid MAS**：

1. **智能核心层（Model-driven MAS Core）**
- 由模型驱动规划、辩论、角色协作、任务拆分。
- 支持动态 handoff、并行子任务、角色临时切换（租约）。

2. **运行时层（Event-driven Runtime）**
- 统一事件协议、消息总线、上下文与工具调用。
- 支持 WebSocket 主链路 + SSE 降级。

3. **治理控制层（Guardrail Control Plane）**
- FSM 仅用于门控：预算、超时、权限、人工介入、终止条件。
- FSM 不参与方案优劣判断，不决定推理路径。

## Decoupling Constraints (Mandatory)

1. 后端运行时不得直接依赖某个 MAS 算法实现类，必须通过策略接口调用。
2. MAS 算法不得直接写数据库表结构，必须通过 Runtime 提供的存储与事件接口。
3. 控制层（FSM/Policy）不读取模型内部推理细节，只消费标准化事件与指标。
4. 通信层不感知算法类型（Debate、Swarm、Planner-Critic），只传输统一事件契约。
5. 替换模型或算法时，不允许修改 WebSocket 协议和核心持久化 schema。

## What FSM Controls

1. 生命周期与阶段门控：Explore -> Debate -> Synthesize -> Decide。
2. 资源边界：时间、token、轮次、并发上限。
3. 权限边界：谁可发言、谁可切角色、谁可裁决。
4. 风险边界：僵局/跑偏/失败时触发升级或人工仲裁。
5. 结束边界：满足收敛条件自动结束，或超时结束。

## What FSM Must Not Control

1. Agent 的内部推理路径。
2. 固定化“谁先说谁后说”的硬流程。
3. 多 Agent 间 handoff 与并行协作策略。

## Rationale

1. 保持未来模型升级弹性：核心策略可替换，不被流程代码绑定。
2. 降低重构成本：治理层与智能层解耦，演进只替换策略模块。
3. 兼顾自治与可控：允许自由讨论，同时保证收敛与资源可控。

## Consequences

正向影响：

1. 更强扩展性：可快速接入新模型与新协作策略。
2. 更低技术债：避免把产品做成硬编码工作流。
3. 更稳上线：门控层保证可运营与可回放。

代价：

1. 需要更清晰的事件协议和策略接口设计。
2. 需要建立模型评测体系来替代规则判断。

## Implementation Notes

1. 所有核心决策通过策略接口输出（Planner/Coordinator/Consensus）。
2. FSM 状态与事件 schema 均版本化。
3. 统一审计事件：role.switch、handoff、consensus.check、human.override。
4. 需要实现最小 SPI：
   - `PlanningStrategy`
   - `CoordinationStrategy`
   - `ConsensusStrategy`
   - `GuardrailPolicy`
   - `FallbackPolicy`（仅在硬故障或红线触发时启用）
5. 运行时与策略层通过 `DecisionContext` + `DecisionResult` 交互，禁止直接透传模型 SDK 对象。

Fallback 使用原则：

1. Fallback 不是主路径，不参与常态决策。
2. 正常路径优先顺序：主模型路由 -> 重试/限流/降载 -> 同层替代。
3. 仅当出现硬故障（不可达、持续超时、持续限流）或质量红线（关键输出缺失且重试后仍失败）时，才允许进入 fallback。

## Delivery Strategy

采用分阶段落地，避免一期过度建设：

1. P0（算法优先）
- 先完成 MAS 算法设计与实验闭环（收敛策略、评测、路由）。
- 输出稳定策略接口，不绑定生产级基础设施。

2. P1（Demo 可运行）
- 最小后端运行时 + 前端交互，形成端到端可演示闭环。
- 目标是验证“讨论质量与收敛质量”，不是高并发与企业治理。

3. P2（生产化）
- 在不改策略接口的前提下替换/增强后端基础设施（分布式 EventBus、鉴权、观测、SLA）。
- 算法层保持相对独立，按 A/B 渐进切换。

## Execution Focus (MVP)

MVP 阶段研发重心明确为 MAS 能力实现，而不是规则体系和维护体系建设：

1. 约 70% 精力投入 MAS 核心（协作策略、收敛机制、模型路由、评测闭环）。
2. 约 20% 精力投入 Demo 运行时（最小通信与状态管理，确保可演示）。
3. 约 10% 精力投入护栏与运维必需项（最小门控、基础日志、最小可恢复）。

约束：

1. 规则仅用于门控，不扩展为复杂规则引擎。
2. 非必要的生产化维护能力（重治理、重运维）不进入一期范围。
