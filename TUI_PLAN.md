# TUI Turn 展示实施清单

状态：执行前
当前阶段：S0 契约冻结（未开始）
完成阶段：0 / 6
基线日期：2026-09-02
Codex 参考版本：`codex-main` revision `0bd2a23916a19e998ed28c0166fbcb405738ed79`

本清单只记录 TUI 单轮展示状态、视觉交接、活动提示和输出会话生命周期的实施任务、
阶段出口与验收证据。全局职责和依赖方向以 `ARCHITECTURE.md` 为准；线上事件、字段和
终态语义以服务端正式契约及 `protocol/` 为准。本计划不修改服务端协议，不让 TUI 成为
Turn、Tool、Approval 或 Effect 的权威状态所有者。

## 标记规则

- `[ ]` 未开始
- `[~]` 进行中
- `[x]` 已完成
- `[!]` 阻塞，必须在记录中说明原因和解除条件

每个阶段必须同时记录状态、完成日期、提交、验证命令或测试结果和遗留风险。只有阶段出口
全部完成并有证据时，阶段标题才能改为 `[x]`；不得通过勾选父项掩盖未完成任务。

## 当前结论

- 已完成的正文首帧交接方向正确：可见 assistant 正文必须在同一次视觉更新中移除等待动画。
- `TuiStreamStatusControl` 当前忽略全部事件级状态请求，仍依赖“整轮等待动画不消失”的旧假设。
- 正文接管等待区域后，工具执行、工具结果回灌、下一轮采样、provider 重试和 Stop Hook 续跑
  不能可靠恢复活动提示。
- 不能直接启用现有 `begin_reply_wait_status()`：通用 idle timer 会在正文尚未结束但 delta 暂停时
  重新点亮状态，形成正文与动画并存。
- 无身份的 `begin/end` 不能正确处理工具批次、嵌套工具、并发工具或连续审批；旧调用必须由
  具名事件和 lease 替代。
- Codex 的可观察规则是：正文流接管时隐藏状态；commentary 完成、工具开始或仍有后续工作时
  恢复状态；恢复必须同时满足 Turn 仍活动且正文队列已经稳定。

## 目标结构

```text
protocol events + local tool/approval lifecycle
                     |
                     v
          typed OutputActivityEvent
                     |
                     v
          TuiTurnSurfaceCoordinator
          - deterministic reducer
          - one generation timer
          - named tool/approval leases
          - output-session scope
                     |
                     v
             SurfaceProjection
                     |
                     v
          one visual_update() commit
          - transcript active content
          - foreground activity slot
          - approval/modal ownership
```

### 职责分配

| 位置 | 唯一职责 | 不得拥有 |
| --- | --- | --- |
| `agent/ports/output.py` | UI 无关的 typed activity 事件、sink 和输出会话生命周期契约 | TUI 计时、布局、颜色、Prompt Toolkit 对象 |
| `agent/adapters/protocol/` | 把正式事件和本地执行事实投影为 activity 事件 | TUI 状态、动画计时、画布操作 |
| `frontends/tui/runtime/turn_surface.py` | TUI 展示 reducer、generation timer、lease 和派生投影 | 线上 Turn 权威状态、工具执行、协议解析 |
| `frontends/tui/core/activity.py` | 根据派生投影渲染动画帧 | Turn、Tool、Approval 状态机 |
| `frontends/tui/adapters/` | 把正文实际可见性和 PresentationView 接入同一 coordinator | 第二套状态所有权或独立 timer |
| `OutputSession` 生命周期 | 打开、绑定 Turn scope、关闭 timer/lease/输出资源 | 进程级资源或跨 Session 全局状态 |

不得新增只转发一次调用的 facade。新链路接入后，同次删除被替代的空实现、无身份状态切换、
跨层延时参数和不可达恢复分支。

## 状态模型

Reducer 使用正交状态，避免把所有组合塞入单一枚举：

| 维度 | 取值/内容 |
| --- | --- |
| `lifecycle` | `inactive`、`active`、`finalizing`、`terminal` |
| `content` | 无活动正文、正文缓冲中、正文已可见、正文已稳定 |
| `model_wait` | 是否存在等待模型继续的语义请求及请求 revision |
| `tools` | 按 `batch_id/call_id/builtin_call_id` 保存的具名活动集合 |
| `approval` | 当前 `approval_id + call_id` 及恢复前活动来源 |
| `retry` | `none`、`transport`、`provider` 及所属 Attempt |
| `recovery` | `live`、`replaying`、`caught_up`、`gap` |
| `timer` | 唯一 generation、deadline 和预期状态 revision |

派生展示优先级固定为：

1. `terminal/finalizing`：不显示活动动画。
2. 审批或其他独占交互：由交互表面接管，暂停活动动画。
3. assistant 正文实际可见：正文接管，不显示活动动画。
4. retry：显示 `Retrying`。
5. 活动工具或后台终端：显示 `Working`、工具专属文案或 `Terminal`。
6. model wait：按 TUI 本地策略延迟显示 `Thinking`。
7. 其他情况：不显示活动动画。

所有延时只存在于 TUI coordinator 的策略中。上游事件不得携带 `delay_sec` 或
`animate_after_sec` 等前端时间参数。初始等待和 retry 可以立即显示；正文结束、工具准备和
工具结果后的等待使用独立策略，但都受同一个 generation timer 管理。

## 阶段总览

- [ ] S0 契约冻结与基线
- [ ] S1 Typed 事件、Reducer 与输出会话生命周期
- [ ] S2 正文流与等待状态原子交接
- [ ] S3 工具、审批与结果回灌闭环
- [ ] S4 重试、恢复、续跑与终态收束
- [ ] S5 清理旧路径与最终验收

## [ ] S0 契约冻结与基线

### 任务

- [ ] 盘点 `turn.*`、`text.*`、`tool.*`、`presentation.*`、`lifecycle.*` 和 `stream.*`
  对活动展示的影响，确认没有由异常文本或 provider 原始载荷推断阶段。
- [ ] 盘点本地工具、计划工具、嵌套工具、后台终端、审批、Hook、Stop Hook continuation 和
  输出 finalizer 的调用顺序。
- [ ] 冻结 typed `OutputActivityEvent` 判别联合、必需 identity、幂等键和非法顺序处理规则。
- [ ] 冻结本地 execution identity 与线上 `turn_id/presentation_epoch/round/attempt` 的映射；
  两类 identity 不得互相替代。
- [ ] 定义 replay 模式：历史事件只更新 reducer，不播放短暂动画；追平后只派生一次当前状态。
- [ ] 记录 Codex 对照用例：正文接管、commentary 恢复、工具开始恢复、最终答案不闪回状态。
- [ ] 为现有实现建立可重复的失败基线，至少证明工具后无状态、retry 后无状态和 idle 恢复风险。

### 出口

- [ ] 状态表能回答每个事件由谁投影、由谁持有、何时释放以及重放时如何处理。
- [ ] identity、timer、lease、输出会话和交互表面的所有者唯一。
- [ ] 服务端协议、Canonical Item reducer 和 TUI 展示 reducer 的边界无重叠。
- [ ] 后续阶段所需测试场景、删除项和目标文件已经确定。

### 记录

- 状态：未开始
- 完成日期：
- 提交：
- 验证：
- 遗留风险/决策：

## [ ] S1 Typed 事件、Reducer 与输出会话生命周期

### 任务

- [ ] 在 `agent/ports/output.py` 建立 UI 无关的 activity 事件和 sink 契约，使用 enum、dataclass
  或判别联合，不使用动态字典和含义不清的布尔位置参数。
- [ ] 事件覆盖 execution/Turn 开始、模型等待、正文边界、工具/批次开始结束、审批开始结束、
  retry 变化、恢复追平和终态。
- [ ] 为工具、审批、批次、Attempt 和输出会话定义准确 identity；重复事件幂等，不同语义冲突。
- [ ] 建立纯 `TuiTurnSurfaceReducer`，输入旧状态和事件后返回新状态及派生投影，不直接执行 IO。
- [ ] 建立 `TuiTurnSurfaceCoordinator`，作为 timer、lease 和画布提交的唯一 owner。
- [ ] 为 `OutputSessionFactory` 增加具名上下文，使输出会话获得 execution/Turn scope，而不是从
  前端全局对象或字符串猜测身份。
- [ ] 让 `OutputSession` 负责完整 open/close；close 必须幂等取消 timer、清除 scope 并继续关闭
  其他输出资源，即使其中一个清理步骤失败。
- [ ] 为 text、JSONL、silent 和 TUI 输出提供明确实现，不保留旧端口兼容 facade。

### 出口

- [ ] Reducer 对合法状态转换、重复事件、过期 generation、陈旧 lease 和非法身份有单元测试。
- [ ] 一个 OutputSession 关闭后不存在 timer task、活动 lease 或可继续修改画布的回调。
- [ ] `agent` 契约不导入 TUI 或 Prompt Toolkit，TUI reducer 不导入 protocol transport。
- [ ] 架构边界审计通过，且旧调用尚未接入前不会形成第二个生产状态所有者。

### 记录

- 状态：未开始
- 完成日期：
- 提交：
- 验证：
- 遗留风险/决策：

## [ ] S2 正文流与等待状态原子交接

### 任务

- [ ] Turn 提交后立即建立 execution scope；每个正式 `turn_id` 到达时绑定新的 Turn scope。
- [ ] 区分“收到文本 delta”和“正文实际可见”；只有渲染器确认可见内容时才能让正文接管动画。
- [ ] 把等待移除、assistant 活动块写入和 invalidate 合并到同一次 `visual_update()`。
- [ ] 正文尚无完整可见行时保留等待；`text.done` 必须揭示尾部并完成同一原子交接。
- [ ] 正文可见期间禁止 idle timer、retry 清理回调或陈旧工具 lease 重新点亮状态。
- [ ] `text.done` 只登记“可能继续”，通过 generation timer 延迟恢复；紧随其后的终态或新正文
  必须取消该恢复，最终答案不得闪回 `Thinking`。
- [ ] commentary/中间正文稳定且 Turn 仍活动时恢复 `Working/Thinking`，并保持输入区行位稳定。
- [ ] Stop Hook 创建新 `turn_id` 时关闭旧 Turn scope，但保留同一 execution scope 和连续展示。

### 出口

- [ ] 简单最终答案全过程不存在动画与正文重叠、空白过渡帧或终态前状态闪回。
- [ ] 中间正文完成后能恢复状态，下一段正文出现时再次原子接管。
- [ ] 50ms 帧级捕获覆盖换行正文、无换行尾部、Markdown 重排和大文本异步终结。
- [ ] 终端宽度变化和 resize debounce 不改变状态所有权或重新创建已经释放的 lease。

### 记录

- 状态：未开始
- 完成日期：
- 提交：
- 验证：
- 遗留风险/决策：

## [ ] S3 工具、审批与结果回灌闭环

### 任务

- [ ] `tool.calls.start/done` 使用 `batch_id` 建立和关闭批次活动，不在收到单个 `tool.call` 时
  提前改变批次完成语义。
- [ ] 客户端工具按 `call_id`、内置工具按 `builtin_call_id` 获取具名 lease；并发、嵌套和顺序
  工具互不清除对方状态。
- [ ] 工具开始时在稳定提交前序正文后显示 `Working` 或工具专属文案，不错误显示为模型思考。
- [ ] 后台终端等待从工具 lease 派生 `Terminal + command`，不能依赖一个可能已经被正文释放的
  旧 wait slot。
- [ ] 工具进度和结果 PresentationView 只提供结构化内容；coordinator 决定活动提示是否可见。
- [ ] 工具结果必须先完成本地展示和可靠投递，再登记 model wait；未知投递进入 reconciliation，
  不得显示普通 `Thinking`。
- [ ] 审批开始时保存恢复来源并让审批表面独占；批准后恢复工具状态，拒绝后进入结果投递，
  cancel 后进入中断收束。
- [ ] 连续审批和审批快照按 `approval_id + call_id` 合并，旧 pending 事件不得重新打开已收束审批。
- [ ] 工具快速完成时由统一 timer 抑制闪烁；长工具必须提供持续可见且可中断的状态。

### 出口

- [ ] `中间正文 -> 工具 -> 结果 -> 下一轮正文 -> 完成` 的真实帧序列稳定。
- [ ] 单工具、两工具批次、嵌套工具、并发结束次序和 duplicate replay 均通过测试。
- [ ] 批准、拒绝、cancel、审批恢复和审批展示失败都有确定状态收束。
- [ ] 本地工具、托管工具、provider built-in tool 和后台终端使用同一 reducer，但保留各自身份。
- [ ] 任一工具结束都不能清除仍活动的其他工具、审批或 retry 状态。

### 记录

- 状态：未开始
- 完成日期：
- 提交：
- 验证：
- 遗留风险/决策：

## [ ] S4 重试、恢复、续跑与终态收束

### 任务

- [ ] transport retry 和 provider retry 作为独立来源合并；transport 优先级、最短可见时间和清理
  由 coordinator 管理，不通过同步回调直接修改活动渲染器。
- [ ] `turn.retrying` 先完成旧 Attempt 正文审计和替代边界，再显示 `Retrying`；新 Attempt 正文
  可见后原子接管。
- [ ] `presentation.superseded` 使用新的 `presentation_epoch` scope，陈旧事件和 timer 不得影响当前代次。
- [ ] attach/replay 期间禁用瞬时动画；按 `event_seq` 重建状态，追平后结合活动 Turn 和 pending Item
  派生一次当前状态。
- [ ] `stream.gap`、连接恢复和审批 snapshot 不推进错误的展示确认水位。
- [ ] lifecycle display 提交稳定块后登记 model wait，不把提示文本当作阶段状态来源。
- [ ] `turn.done/failed/interrupted/cancelled/reconciliation_required` 立即取消 timer 和活动 lease，
  再提交对应终态展示。
- [ ] `turn.logical_settled` 只关闭逻辑交互和输入屏障；不得重新解释或覆盖已经显示的终态。
- [ ] 本地异常、应用关闭、用户 Ctrl-C、输出关闭失败和清理异常均保证状态收敛，并继续执行后续清理。
- [ ] Stop Hook continuation 使用新 Turn scope；超过续跑预算或续跑被拒绝时关闭 execution scope。

### 出口

- [ ] partial text 后 provider retry、transport reconnect、Worker presentation supersede 均有逐帧测试。
- [ ] replay 终态历史不会播放动画，活动 Turn 追平后只显示一个正确状态。
- [ ] 中断、失败、对账、连接缺口和 continuation 都不存在状态复活或悬挂 task。
- [ ] Turn 终态、`turn.logical_settled` 和 TUI execution 完成三个边界保持职责分离。

### 记录

- 状态：未开始
- 完成日期：
- 提交：
- 验证：
- 遗留风险/决策：

## [ ] S5 清理旧路径与最终验收

### 任务

- [ ] 删除 `TuiStreamStatusControl` 的空实现及其旧行为测试；保留的 adapter 必须只投递 typed event。
- [ ] 删除无身份的 tool/status begin/end、跨层 `delay_sec/animate_after_sec` 和重复 retry 展示回调。
- [ ] 删除或职责化通用 `IdleStatusTimer`；不得保留可绕过 reducer 的 TUI 状态恢复路径。
- [ ] 收口 `TuiRuntime.finish_turn_wait()`、审批 pause/resume、terminal wait 和 assistant handoff 的直接调用面。
- [ ] 更新 `ARCHITECTURE.md` 中稳定的 TUI Presentation 所有权和 OutputSession 生命周期，只记录最终决策。
- [ ] 确认 text、JSONL、silent、stdio MCP 和 Subagent 输出没有行为回归或 TUI 依赖。
- [ ] 审核所有新增公开入口和 `__all__`，删除测试专用生产 API、一次性 facade 和动态属性判断。
- [ ] 运行受影响测试、架构边界审计、语法检查和 `git diff --check`，记录准确结果。

### 最终场景矩阵

- [ ] 简单最终答案：Thinking 被正文原子替换，终态前不闪回。
- [ ] Commentary + 本地工具 + 最终答案：每次交接状态正确，输入区不抖动。
- [ ] 两工具批次：批次和单调用身份独立，最后一个工具结束前状态不消失。
- [ ] Provider built-in tool：调用中有 Working，结束后等待下一轮正文。
- [ ] 审批批准、拒绝、cancel：焦点、状态恢复和终态分别正确。
- [ ] 后台终端等待：Terminal 状态、命令摘要、恢复和中断正确。
- [ ] Provider retry 与 transport retry：Retrying 可见、旧正文隔离、无陈旧 timer。
- [ ] Presentation supersede：旧 epoch 只保留审计，新 epoch 独立展示。
- [ ] attach/replay：历史无动画，活动尾部恢复一次且游标不受展示影响。
- [ ] failure/interruption/reconciliation：不显示虚假 Thinking，所有资源收束。
- [ ] Stop Hook continuation：新 Turn scope 正确建立，旧 scope 不复活。
- [ ] `animate=False`、窄终端、宽终端、resize 和大文本终结行为稳定。

### 验证门槛

- [ ] Reducer 单元测试覆盖全部合法转换和关键非法转换。
- [ ] animate=True 的真实 Prompt Toolkit 帧捕获覆盖所有核心场景。
- [ ] 每个交接帧不会同时出现旧活动提示和新 assistant 正文，也不会产生由交接导致的空白帧。
- [ ] 输入窗口绝对行位在状态/正文/工具交接期间保持稳定，除非真实新增历史内容需要向上增长。
- [ ] 终态后无 pending timer、活动 lease、approval session、stream render handle 或后台 task。
- [ ] `python -m pytest` 的受影响测试集合通过。
- [ ] `python -m pytest tests/test_package_architecture.py -q` 通过。
- [ ] `python -m compileall agent protocol frontends infrastructure observability metadata` 通过。
- [ ] `git diff --check` 通过。

### 记录

- 状态：未开始
- 完成日期：
- 提交：
- 验证：
- 遗留风险/决策：

## [ ] 最终完成

- [ ] S0-S5 全部完成并有提交与验证证据。
- [ ] 所有活动展示只有一个 reducer 和一个 timer owner。
- [ ] TUI 只投影事实，不拥有协议、Turn、Tool、Approval 或 Effect 权威状态。
- [ ] Codex 对照行为已经通过 ProxyMind 自有契约和帧测试证明，而不是依赖人工观感判断。
- [ ] 旧状态路径、空实现、兼容 facade 和重复生命周期已经删除。
- [ ] `ARCHITECTURE.md`、实现和测试描述同一套最终架构。

最终验收完成后，把总进度、各阶段和本节标记为 `[x]`，并记录最终提交、测试结果和仍需
外部系统验证的风险。
