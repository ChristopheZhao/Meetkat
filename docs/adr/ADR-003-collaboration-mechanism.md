# ADR-003: LLM-based MAS Collaboration Mechanism for Decision Room

- Status: Proposed (Draft)
- Date: 2026-03-10
- Owners: Agent Runtime + LLM Algorithm + Product

## Context

通信方案不能单独设计。

对这个会议室产品来说，真正决定质量的是：

1. 谁在何时发言。
2. 谁可以 handoff、challenge、summary、vote。
3. 哪些上下文进入共享黑板，哪些保留为私有上下文。
4. 如何让讨论逐步收敛，而不是持续发散。

此外，传统 `RL/MARL` 与 `LLM-based MAS` 的协作机制存在本质差异：

1. `RL/MARL` 更偏 `state / action / reward / policy`。
2. `LLM-based MAS` 更偏 `message / role / context / tool / artifact`。
3. 本项目一期是运行时协作系统，不是训练系统。

## Decision

一期采用 **4-role minimal collaboration mechanism**：

1. `host`
2. `pro`
3. `con`
4. `recorder`

并采用 **structured intermediate artifacts** 驱动收敛，而不是依赖自由聊天本身。

## Collaboration Model

### 1. Identity, Role, Capability

三者必须分离：

1. **identity**
- `human`
- `agent`
- `system`

2. **role**
- `host`
- `pro`
- `con`
- `recorder`
- 后续可扩展 `judge`

3. **capability**
- `speak`
- `handoff`
- `challenge`
- `summarize`
- `vote`
- `override`
- `tool_call`

角色切换采用 **role lease**：

1. 带 TTL
2. 带生效范围
3. 不做永久角色切换

### 2. Round Structure

一期最小回合结构：

1. `host` 给出下一轮焦点
2. `pro` 输出支持性论点
3. `con` 输出反驳或风险
4. `recorder` 输出结构化沉淀
5. 运行时执行 `consensus.check`
6. 必要时允许 `human.message` 或 `human.override`

### 3. Required Intermediate Artifacts

每轮至少产出：

1. `claim`
2. `evidence`
3. `open_question`
4. `decision_candidate`

`recorder` 负责聚合出：

1. `agreement`
2. `disagreement`
3. `open_questions`
4. `candidate_decision`
5. `action_item_draft`

## Handoff Design

`handoff` 必须是显式事件，不允许只存在于 prompt 文本中。

最小字段建议：

1. `from_role`
2. `to_role`
3. `reason`
4. `context_slice`
5. `artifact_refs`
6. `expected_output`

默认策略：

1. handoff 只传摘要，不传全量 transcript。
2. handoff 只传必要 artifact refs。
3. 全量 transcript 只用于回放、审计、派生视图，不直接作为下一 agent 的默认上下文。

## Transcript Strategy

采用双层：

1. **Event Log**
- 给 runtime、回放、恢复、调试使用

2. **Derived Transcript**
- 给前端展示和人类阅读使用

transcript 不只记录 message，还需要可挂接：

1. `claim`
2. `decision`
3. `action_item`
4. `disagreement`

## Convergence Policy

一期不采用复杂规则引擎，也不采用训练型 reward 机制。

采用最小门控：

1. 分歧是否下降
2. `candidate_decision` 是否稳定
3. `open_questions` 是否减少
4. 是否需要 `human_override`

`host` 推进节奏，`recorder` 负责沉淀，收敛判断由独立 `Consensus Service` 负责。

## What We Borrow From RL/MARL

可借鉴：

1. `CTDE` 结构思想
2. role decomposition
3. termination / option 思想
4. credit assignment 作为后续评估视角

不直接采用：

1. reward engineering 作为一期核心
2. self-play / policy optimization
3. 低维固定通信信道
4. value decomposition runtime 化

## Rejected Options

### 1. Fully Free-form Chat

不采用。

原因：

1. 容易漂移
2. 难以回放
3. 难以前端结构化展示
4. 难以收敛

### 2. Prompt-only Hidden Handoff

不采用。

原因：

1. 无法追踪 agent 协作路径
2. 无法做 transcript 与前端可视化
3. 无法精细控制上下文传播

### 3. Training-first MARL Mechanism

一期不采用。

原因：

1. 当前目标是 runtime collaboration，不是训练系统。
2. 工程复杂度与验证周期不适合一期。

## Consequences

正向影响：

1. 多 agent 协作可见、可控、可回放。
2. 前端可以基于结构化产物构建会议流与结果面板。
3. 后续可以更换模型，而不推翻协作协议。

代价：

1. 需要为每个角色定义稳定输出 contract。
2. 需要维护黑板、私有上下文、handoff 摘要三层信息。

## Phase-1 Non-goals

一期明确不做：

1. 动态角色生成
2. agent marketplace
3. 分布式 agent mesh
4. 长期记忆编排
5. 训练型协作优化

## References

1. OpenAI Agents SDK handoffs: https://openai.github.io/openai-agents-python/handoffs/
2. Anthropic multi-agent research system: https://www.anthropic.com/engineering/multi-agent-research-system
3. Anthropic subagents: https://docs.anthropic.com/en/docs/claude-code/sub-agents
4. LangChain handoffs: https://docs.langchain.com/oss/python/langchain/multi-agent/handoffs
5. IJCAI 2024 survey on LLM-based multi-agents: https://www.ijcai.org/proceedings/2024/0890.pdf
6. Survey on self-play and population-based MARL: https://arxiv.org/abs/2408.01072
