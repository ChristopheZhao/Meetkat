# P0 Real Run Protocol (Non-Mock Core Path)

- Date: 2026-03-01
- Purpose: 验证 Hybrid 主线在真实模型调用下可运行

## 1. Mandatory Rule

1. 核心链路（计划、协作、收敛）必须调用真实模型。
2. mock 仅允许出现在基础设施单测。
3. 当前 `run_real_small_flow.py` 验证的是真实模型主线与结构化 contract，不等价于完整 4 角色会议 runtime。

## 2. Environment

Required env vars:

1. `MODEL_DEFAULT_SUPPLIER`
2. `MODEL_DEFAULT_MODEL`
3. `MODEL_ESCALATION_SUPPLIER`
4. `MODEL_ESCALATION_MODEL`
5. `MODEL_FALLBACK_SUPPLIER`
6. `MODEL_FALLBACK_MODEL`
7. `<SUPPLIER>_BASE_URL`
8. `<SUPPLIER>_API_KEY`
9. Optional: `MODEL_TIMEOUT_SEC`
10. Optional: `<SUPPLIER>_TIMEOUT_SEC`
11. Optional: `REAL_RUN_ROUTE=auto|default|escalation|fallback`

示例：

```bash
export MODEL_DEFAULT_SUPPLIER="qwen"
export MODEL_DEFAULT_MODEL="qwen-plus"
export MODEL_ESCALATION_SUPPLIER="glm"
export MODEL_ESCALATION_MODEL="glm-5"
export MODEL_FALLBACK_SUPPLIER="minimax"
export MODEL_FALLBACK_MODEL="MiniMax-M2.5"
export MODEL_TIMEOUT_SEC="45"
export REAL_RUN_ROUTE="auto"

export QWEN_BASE_URL="https://dashscope.aliyuncs.com/compatible-mode/v1"
export QWEN_API_KEY="***"
export GLM_BASE_URL="https://api.z.ai/api/paas/v4"
export GLM_API_KEY="***"
export GLM_TIMEOUT_SEC="60"
export MINIMAX_BASE_URL="https://api.minimax.io/v1"
export MINIMAX_API_KEY="***"
```

## 3. Run Command

```bash
uv run --env-file .env python3 scripts/run_real_small_flow.py
```

本地开发补充：

- `scripts/run_room_runtime.py` 会在启动时尝试自动加载项目根目录 `.env`，但不会覆盖已经注入到进程中的环境变量。
- 生产部署仍应通过外部环境注入配置，不应依赖 `.env` 文件自动加载。
- 如需禁用本地自动加载，可设置 `DOTENV_DISABLE_AUTOLOAD=1`。
- 当前本地 `room runtime` 为了绕开 `glm-4.6` 在第 4 轮 escalation 上的稳定性问题，默认把 `MODEL_ESCALATION_*` 也指向 `qwen/qwen-plus`。这只是本地止血配置，不代表长期模型分层策略已经冻结。
- 该本地止血配置的差异性、证据、可接受范围与退出条件，见 [P1-provider-escalation-diagnostic-20260405.md](/mnt/d/code/opensource/agent/meetkat/docs/tech/P1-provider-escalation-diagnostic-20260405.md)。

## 4. Required Evidence

1. route 决策日志（default/escalation/fallback）
2. 实际模型返回内容（非 mock）
3. 主持 Agent 结构化议程输出（JSON contract passed）
4. 协调动作与收敛评估输出
5. 至少 1 个失败样本（如限流或超时）及处理结果

## 5. Pass Criteria for P0 Real Run

1. 脚本成功执行并返回模型文本输出。
2. route 与 action 输出符合 Hybrid 预期。
3. fallback 未被常态触发，仅在硬故障触发。
4. 主持 Agent 输出通过结构化 contract 校验。

## 6. Follow-up Boundary

P0 real-run 通过后，下一阶段不再继续堆叠单脚本逻辑，而是进入：

1. `host / pro / con / recorder` 多角色真实回合执行器
2. room event protocol
3. transcript + blackboard + consensus 闭环
