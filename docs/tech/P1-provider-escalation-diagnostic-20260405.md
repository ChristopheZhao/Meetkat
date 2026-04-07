# P1 Provider Escalation Diagnostic (2026-04-05)

- Date: 2026-04-05
- Scope: MAS harness real-room execution stability
- Question: 当前 `escalation` 路径上的不稳定，是否来自 harness 主线，还是来自特定 provider/model 的长尾与抖动

## 1. Decision Summary

结论分两层：

1. 已定位的 **直接工程根因**：
   - 第 4 轮会稳定进入 `escalation`。
   - `build_round()` 当前按 `host -> pro -> con -> recorder` 串行执行。
   - `glm-4.6` 在真实第 4 轮 prompt 上的角色级调用明显慢于 `qwen-plus`，导致整轮墙钟时间显著拉长。
   - 之前房间以 `runtime failure` 结束，不是前端、WS/SSE 或 SoT 边界错误，而是“慢路径 + provider 抖动/超时”叠加造成的执行链路失稳。

2. 尚未定位的 **供应商内部根因**：
   - 还没有证据说明 `glm-4.6` 为什么更慢：可能是模型本身、供应链、网关、或当前 prompt/token profile 导致。
   - 这不影响当前 harness 侧的工程决策。

## 2. Evidence

### 2.1 Role-Level Round-4 Prompt Benchmark

基于真实房间 `room_661e9451` 的第 4 轮 prompt，分别对四个角色做同题对照：

| Provider | Host | Pro | Con | Recorder | Total |
| --- | ---: | ---: | ---: | ---: | ---: |
| `qwen/qwen-plus` | `6.30s` | `6.70s` | `13.05s` | `16.15s` | `42.20s` |
| `glm/glm-4.6` | `19.75s` | `23.08s` | `19.43s` | `15.75s` | `78.01s` |

说明：

- `glm-4.6` 不是“固定坏掉”；四个角色都能成功返回。
- 但 `host / pro / con` 都进入了 `19s~23s` 区间，导致整轮总耗时接近 `qwen-plus` 的 `1.85x`。
- 问题不是单个角色爆炸，而是 **长尾在串行执行中被放大**。

### 2.1.1 Repeated Samples (Host / Pro / Con)

为排除单次采样噪音，又对同一条真实第 4 轮 prompt 补了两轮 `host / pro / con` 重复样本：

| Provider | Sample | Host | Pro | Con | Total |
| --- | --- | ---: | ---: | ---: | ---: |
| `qwen/qwen-plus` | `rep-1` | `7.25s` | `9.10s` | `9.10s` | `25.45s` |
| `qwen/qwen-plus` | `rep-2` | `12.03s` | `7.22s` | `10.36s` | `29.61s` |
| `glm/glm-4.6` | `rep-1` | `13.02s` | `16.01s` | `19.93s` | `48.96s` |
| `glm/glm-4.6` | `rep-2` | `16.49s` | `14.74s` | `21.79s` | `53.02s` |

说明：

- `glm-4.6` 的慢不是一次偶然波动；在重复样本里仍然稳定高于 `qwen-plus`。
- `host / pro / con` 三个角色均偏慢，尤其 `con` 持续落在 `20s` 左右。
- 就 `host/pro/con` 这三段串行链而言，`glm-4.6` 平均约为 `50.99s`，`qwen-plus` 平均约为 `27.53s`，倍率约 `1.85x`。

### 2.2 Real-Room Round-4 Wall Clock Gap

当前 orchestrator 先完整执行 `build_round()`，然后才开始发这一轮事件；因此“上一轮 recorder message 到下一轮 host message”的时间差，可以近似看作本轮真实构建时间。

对照房间：

- `room_661e9451` (`8010`, escalation=`qwen/qwen-plus`)
  - 第 4 轮 gap: `43.74s`
  - 结束原因: `consensus threshold reached`
- `room_20407d01` (`8011`, escalation=`glm/glm-4.6`)
  - 第 4 轮 gap: `72.66s`
  - 结束原因: `meeting reached the maximum MVP round budget`

说明：

- 真实房间轨迹与角色级 benchmark 一致。
- `glm-4.6` 的问题已经在真实 room 执行里被复现成明显更长的第 4 轮长尾。

### 2.3 Failure Path Status

当前代码状态下：

- provider 的 `timeout/network/http` 异常已先由 executor 吸收，并通过 control/fallback 路径尝试 reroute。
- 因此 `glm-4.6` 路径在 2026-04-05 的诊断中 **不再必然** 以 `runtime failure` 结束。
- 但这不代表问题消失；它从“必炸”变成了“长尾显著、稳定性边际差”。

## 3. Root Cause Statement

当前可成立的根因表述是：

1. `RoutingControlPolicy` 在第 4 轮会稳定进入 `escalation`。
2. `LLMRoomExecutor.build_round()` 会串行执行四个远程角色调用。
3. `glm-4.6` 在该轮真实 prompt 组合上的尾延迟显著高于 `qwen-plus`。
4. 因为这一轮是串行执行，所以 provider 慢路径被直接放大成 room-level stall。
5. 在旧错误路径上，这种长尾再叠加单次 provider 抖动，会穿透成 `runtime failure`；当前补过 retry/reroute 后，问题主要表现为 round-4 延迟过长。

这条根因判断已经同时满足：

- 角色级单次对照
- 角色级重复样本
- 真实房间第 4 轮墙钟对照

因此，对 **Meetkat 当前工程问题** 而言，根因已经定位到足够支持后续方案比较；不需要继续在同一层反复采集同类 benchmark。

## 4. Stopgap Decision Record

### Option A

- Keep `MODEL_ESCALATION_* = glm/glm-4.6` in local development runtime.

问题：

- 会稳定引入更长的第 4 轮长尾。
- 之前已经真实出现过 `runtime failure`。
- 不利于前端联调、浏览器级验证、和 demo 观感。

### Option B

- Keep `escalation` tier semantics, but temporarily point local dev runtime to `qwen/qwen-plus`.

可接受范围：

- **仅本地开发 / 联调 / demo 止血**
- 不代表长期模型分层策略冻结
- 不代表放弃 escalation tier

为什么当前可接受：

1. 仍然保留真实非 mock 链路。
2. 仍然保留 `default/escalation/fallback` 路由语义，只是本地 `escalation` target 暂时替换成已验证更稳的 target。
3. 当前重点是验证 harness、事件链、前端联调和浏览器闭环，不是做最终模型分层定型。
4. 证据表明 `qwen-plus` 在同题 round-4 路径上明显更稳、更短。

### Decision

- **接受 Option B 作为本地止血方案**
- **不接受** 把该配置包装成长期策略结论

## 5. Non-Accepted Next Steps

以下动作当前不直接执行：

1. 直接修改长期 routing strategy
   - 原因：现有证据已经足够支持止血，但还不足以冻结最终模型分层策略。

2. 直接删除 escalation tier
   - 原因：这会破坏路由层语义，无法保留真实 tier behavior。

3. 在未补足观测的情况下继续大改执行模型
   - 原因：当前已能定位直接工程根因，下一步更合理的是补齐观测埋点后再调整策略。

## 6. Exit Criteria For Reverting The Stopgap

满足任一条件后，可重新评估是否恢复 `glm` 作为本地 escalation target：

1. 有新的 provider-level 证据表明 `glm-4.6` 在 round-4 角色链路上的长尾已经收敛。
2. routing/control 策略调整后，不再在第 4 轮无条件把完整四角色链路推入高尾延迟 target。
3. execution model 调整后，整轮不再受四角色串行总耗时主导。
4. 新增 per-role route/attempt/latency 可观测事件后，确认 `glm` 路径在真实 room 中可稳定满足当前 demo 验收窗口。

## 7. Diagnostic Closure For This Layer

当前这一层诊断已收口：

- 已经证明问题不在前端、WS/SSE、SoT 边界或 room harness 主线。
- 已经证明 `glm-4.6` 在当前第 4 轮 escalation prompt 组合上存在稳定的角色级长尾。
- 已经证明串行四角色执行会把该长尾放大成 room-level stall。

后续若继续工作，优先级应转向：

1. 正式修复方案比较（routing / execution model / observability）。
2. 如确有需要，再单独立项继续深挖 provider 内部原因。

## 8. Implementation Constraint

在正式退出本地止血方案之前：

- 保持 `Memory` 为基底、`Runtime/Harness` 不成层、`state` 仅为映射 的 MAS harness 基线不变。
- 不得让 provider adapter、routing helper 或 fallback helper 膨胀成竞争主线。
- 不得把本地止血配置误写成长期产品策略。
