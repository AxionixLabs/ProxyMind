# Codex 审批行为对齐设计与实施记录

> 复核基线：2026-08-23  
> Codex 参照：`codex-main/codex-rs/tui/src/bottom_pane/approval_overlay.rs`  
> ProxyMind 实现：`mind_app/tui/core/approval.py`、`mind_app/tui/core/runtime.py`

## 1. 结论

当前阶段只对齐 Codex 的审批行为，不重写 ProxyMind 的审批视觉结构，也不把
`TuiApproval` 改名为 `ApprovalOverlay`。

ProxyMind 当前审批卡在交互语义上是模态表面：审批激活后，`BottomPane` 的
`BottomSurface="approval"` 成为唯一活动表面，审批按键绑定接管输入，原输入区和其他
bottom surface 暂时失活。它不是第二个 prompt-toolkit `Application`，也不是覆盖整个终端的
全屏 overlay。

Codex 的 `ApprovalOverlay` 同样位于 bottom pane，源码称其为 modal overlay。两者的核心
差异主要是请求排队和生命周期，而不是是否叫 “card” 或 “overlay”。因此没有必要仅为名称
或控件形态重建现有审批卡。

本次对齐保留：

- `TuiApproval` 及现有 prompt-toolkit control tree。
- `BottomSurface = "approval"` 和现有焦点恢复机制。
- `approval_render.py` 的内容投影、样式和尺寸预算。
- 现有 `ApprovalDecisionValue`、审批过期语义和策略模型。

本次对齐改变：

- 活动审批可以接收并保存并发请求。
- 当前请求完成后，在同一审批表面直接推进下一项。
- 整批审批只暂停、恢复一次 Thinking 和终端进度。
- Ctrl-C 和 TUI close 能收束全部未决 future。
- 调用方取消只移除属于该调用方的请求。

## 2. Codex 源码事实

`ApprovalOverlay` 自己持有：

```text
current_request: Option<ApprovalRequest>
queue: Vec<ApprovalRequest>
current_complete: bool
done: bool
```

其关键行为为：

1. 活动 overlay 通过 `enqueue_request()` 吸收后续请求。
2. 当前请求完成后调用 `advance_queue()`，不先关闭再创建新 overlay。
3. Ctrl-C 调用 `cancel_current_request()`，并清空等待队列。
4. 外部已解决事件可以按请求身份移除 current 或 queued request。
5. current 和 queue 都为空后，overlay 才进入完成状态。

需要明确的实现细节：Codex 当前使用 `Vec::push()` 加 `Vec::pop()`，实际等待顺序为 LIFO；
源码没有展示 queue count，也没有 ProxyMind 的审批倒计时。ProxyMind 不机械复制这两点。

## 3. ProxyMind 对齐后的状态所有权

### 3.1 `TuiApproval`

交互前端拥有实际审批队列：

```text
TuiApproval
├── state: ApprovalState | None
├── _pending: deque[ApprovalState]
├── selected_index
└── expiry_task
```

每个 `ApprovalState` 都有独立 future。`request()`/`wait(state=...)` 只等待该请求的 future，
不会因为屏幕已经推进到下一项而读取错误的决策。

### 3.2 `ApprovalCoordinator`

`ApprovalCoordinator` 不再用 `asyncio.Lock` 把等待请求隐藏在交互前端之外。它保留稳定业务
入口，直接把并发请求转交给实现 `InteractionPort` 的前端。

这是有意的单一所有权：队列只存在于负责展示和完成请求的交互实现中，不在 coordinator
和 TUI 两边各维护一份。

### 3.3 `TuiRuntime`

runtime 只拥有整批审批的 activity 生命周期：

```text
first request
  -> activate approval session
  -> terminal progress warning
  -> pause Thinking once
  -> wait until all current/pending approvals settle
  -> resume Thinking or terminal progress once
```

批次初始化使用共享 ready event；并发请求在首个 `pause_wait()` 完成前不会越过初始化。
批次切换和 close 使用局部异步锁保护，防止取消或关闭在初始化中途提前恢复 activity。

## 4. 用户可观察行为

| 事件 | 当前请求 | 等待请求 | 表面与 activity |
| --- | --- | --- | --- |
| 新请求到达空闲状态 | 立即显示 | 无 | 激活审批表面，暂停一次 |
| 新请求到达活动状态 | 保持显示 | FIFO 入队 | 表面和 activity 不切换 |
| accept/decline | 返回对应决策 | 展示下一项 | 队列非空时不恢复输入 |
| 当前请求过期 | 返回 `expired` | 展示下一项 | 队列非空时保持审批表面 |
| 排队请求等待时过期 | 推进时跳过 | 返回 `expired` | 继续寻找下一项 |
| Ctrl-C | 返回 `cancel` | 全部返回 `cancel` | 结束整批并恢复原表面 |
| TUI close/dismiss | 返回 `decline` | 全部返回 `decline` | 安全关闭，不重启进度 |
| 当前调用方取消 | 取消当前 future | 推进下一项 | 队列非空时保持审批表面 |
| 排队调用方取消 | 当前不变 | 只移除该项 | 不影响其他请求 |

Esc/n 仍表示拒绝当前请求并继续队列；Ctrl-C 才取消整批。这与现有
`ApprovalDecisionValue` 契约一致，不引入 Codex 中更细的 deny/decline 类型。

## 5. 有意保留的差异

### FIFO 顺序

ProxyMind 使用 `deque.popleft()`，按请求到达顺序处理。虽然 Codex 当前 `Vec::pop()` 是
LIFO，但 FIFO 更符合并发工具调用的时间顺序，也保留 ProxyMind 已有的串行直觉。

### 审批过期

ProxyMind 保留 `expires_at_ms`、当前倒计时和排队期间过期检查。expiry task 绑定具体
`ApprovalState`，旧任务不能结束已经切换后的新请求。

### 展示结构

当前 card/footer、bottom-pane 高度计算和样式继续使用。行为对齐不要求删除
`approval-card` 样式，也不要求增加 overlay id、generation 或 queue-count UI。

### 决策模型

继续使用 `accept`、`acceptForSession`、`acceptWithExecpolicyAmendment`、`decline`、
`cancel` 和 `expired`。不因为 Codex 的 Rust 枚举不同而扩大本次业务协议。

## 6. 关闭与异常规则

1. `TuiApproval.close()` 和 `dismiss()` 必须完成 current 与全部 pending future。
2. `TuiRuntime.close()` 先阻止新审批进入，再等待批次初始化落稳，然后收束审批。
3. close 期间不恢复 Thinking，也不重新启动 terminal progress。
4. `pause_wait()` 失败时，整批请求统一结束；首请求保留原异常，已排队请求返回安全的
   `decline`。
5. 调用方取消时按 `ApprovalState` 对象身份移除请求，不依赖当前屏幕内容或选项下标。
6. 已完成 future 的重复 finish/cancel 不得覆盖原决策。

## 7. 已实施范围

- [x] `TuiApproval` 增加 current + pending deque。
- [x] 并发请求各自等待独立 future。
- [x] 当前请求完成后在同一表面 FIFO 推进。
- [x] Ctrl-C 取消 current 和全部 pending。
- [x] dismiss/close 拒绝并收束全部未决请求。
- [x] 调用方取消可以区分 current 与 queued request。
- [x] 当前及排队请求过期后安全推进。
- [x] `ApprovalCoordinator` 移除 lock-only 串行化。
- [x] `InteractionPort` 明确前端的排队、取消和关闭责任。
- [x] runtime 用一个 session 覆盖整批审批的 Thinking/终端进度切换。
- [x] close 和初始化失败路径具有确定的批次收束行为。

## 8. 测试覆盖

行为测试覆盖：

- FIFO 推进时审批表面连续存在。
- 第二条请求出现前不恢复输入或 Thinking。
- Ctrl-C 对 current/pending 返回一致的 `cancel`。
- close 对 current/pending 返回一致的 `decline`。
- 当前调用方取消后推进 queued request。
- queued 调用方取消不改变 current request。
- 排队期间过期的请求被跳过并返回 `expired`。
- `pause_wait()` 失败时并发请求全部收束。
- 一批审批只调用一次 terminal warning 和一次恢复。
- runtime close 不遗留审批 future 或 session 状态。

主要测试文件：

- `tests/test_tui_approval.py`
- `tests/test_tui_activity.py`
- `tests/test_terminal_progress.py`
- `tests/test_approval_coordinator.py`
- `tests/test_tui_bottom_pane.py`

## 9. 后续可选对齐项

以下不是本次“先对齐行为”的完成条件：

- 按 request id/call id 接收外部 resolved 事件并移除队列项。
- Codex 的跨 thread 来源展示、打开 thread 和全屏查看动作。
- Exec、Permissions、ApplyPatch、MCP elicitation 的类型化 view model。
- Codex selection view 的精确视觉、快捷键配置和历史 decision cell。

如果后续实施这些能力，应继续复用现有 `BottomSurface="approval"`；只有确有布局或交互
能力缺口时再调整控件结构，不以重命名为 `ApprovalOverlay` 作为对齐目标。

## 10. 完成定义

本阶段完成需同时满足：

- 并发审批不会因 coordinator lock 而隐藏在 UI 生命周期之外。
- current 完成后连续显示 pending，所有请求 future 与自身决策一一对应。
- Ctrl-C、调用方取消、expiry、pause 失败和 TUI close 均不会遗留 future/task。
- 整批审批只暂停和恢复一次 activity/terminal progress。
- 保留现有审批 card、bottom pane、决策模型和 FIFO/expiry 产品语义。
- 相关定向测试、受影响回归测试和语法检查全部通过。
