# Codex 审批交互对齐设计与实施计划

> 复核基线：2026-08-22  
> Codex 参照：`D:\codex-main\codex-rs\tui\src\bottom_pane\approval_overlay.rs`  
> ProxyMind 参照：`D:\PycharmProjects\ProxyMind\mind_app\tui\core\approval.py`  
> 文档性质：审批交互表面、队列和生命周期的独立设计；本文件不直接修改运行代码。

## 1. 文档边界

本文件只处理“审批卡如何对齐 Codex 并重构为独立 ApprovalOverlay”这一件事：

- 当前 `TuiApproval` 专用 card 的替换。
- Codex `ApprovalOverlay` 的 current/queue 生命周期对齐。
- Exec、Permissions、ApplyPatch、MCP elicitation 等审批请求的统一投影。
- 审批选项、快捷键、过期、取消、TUI close 和 activity handoff。
- 审批相关 runtime、screen、layout、port 和测试迁移。

以下内容不在本文件内：

- `Approve for me` 的 reviewer、Guardian、自动审核和权限 preset 语义。
- 审批策略、Hook allow/deny、ApprovalStore 的业务规则重写。
- Native Coding 协议字段和远端审批协议升级。

`Approve for me` 的独立 reviewer 方案继续维护在：
`codex_approve_for_me_alignment.md`。

## 2. 结论

ProxyMind 不需要保留自制审批 card，也不需要额外创建一个 Application。目标是：

```text
当前：ApprovalCoordinator -> TuiApproval card -> input key bindings
目标：ApprovalCoordinator -> Approval queue -> ApprovalOverlay -> decision future
```

业务决策和审批一致性继续复用现有实现；只替换交互表面和排队方式。

Codex 的关键经验不是“做一个更漂亮的卡片”，而是：

- current request 和 waiting queue 有明确所有权。
- 请求类型统一进入同一个 overlay 生命周期。
- 选择结果通过稳定 request identity 回写，不依赖当前屏幕对象。
- 请求完成后自动推进下一项。
- 过期、关闭和外部已解决事件都能使当前请求安全出队。

## 3. 当前实现复核

### 3.1 ProxyMind 当前链路

| 阶段 | 当前实现 | 差异 |
| --- | --- | --- |
| 请求入口 | `mind_app/tui/core/runtime.py:1891` | runtime 直接操作 `screen.approval` |
| 审批状态 | `mind_app/tui/core/approval.py:21` | 只有一个 `ApprovalState` |
| 并发协调 | `mind_app/approval/coordinator.py:13` | `asyncio.Lock` 串行化，等待请求不展示在 UI 队列 |
| 展示 | `approval_render.py` | 专用 card、专用 footer、专用样式 |
| 布局 | `screen.py`、`rendering/screen/layout.py` | `approval` 是独立 BottomSurface |
| 决策 | `ApprovalDecisionValue` | 业务值完整，可以继续复用 |
| 过期 | `TuiApproval._expire()` | 独立 expiry task，每次刷新 card |
| 关闭 | `TuiApproval.dismiss/close()` | runtime 直接依赖 approval 对象 |

### 3.2 Codex 当前链路

Codex 使用 `ApprovalOverlay`，它仍然是一个暂时的 modal，但行为上是一个带队列的选择
视图，而不是 ProxyMind 的静态 card：

| 能力 | Codex 实现 |
| --- | --- |
| 当前请求 | `current_request: Option<ApprovalRequest>` |
| 等待队列 | `queue: Vec<ApprovalRequest>` |
| 请求类型 | Exec、Permissions、ApplyPatch、McpElicitation |
| 选项 | `ApprovalOption` + 类型化 decision |
| 请求完成 | `current_complete` 后 `advance_queue()` |
| 外部解决 | 按 request id/call id 匹配并出队 |
| 展示 | header、body、options、footer 组成统一 selection view |
| 输入 | list keymap + approval keymap |
| 关闭 | 当前请求和队列都能安全清理 |

## 4. 目标交互行为

### 4.1 单条 Shell 审批

目标显示结构使用 Codex overlay 的 header/body/options/footer：

```text
Would you like to run the following command?

Environment: workspace
Reason: The model wants to run this command

  $ adb logcat -v time

› 1. Yes, proceed (y)
  2. Yes, for this session (s)
  3. Yes, and don't ask again for this command prefix (p)
  4. No, and tell {APP_DESC} what to do differently (n/esc)

Press enter to confirm or esc to go back · expires in 30s
```

具体标题和选项文本继续由 `approval_policy.py` 的决策模型提供，产品名使用
`mind_nova.const.APP_DESC`；不在 `TuiMenu` 中写死 Shell 分支。

### 4.2 连续审批

当第一个审批尚未完成时，第二个请求不能丢弃或静默阻塞：

```text
当前审批：apply_patch
等待中：2 个请求

› 1. Yes, proceed
  2. No, and tell {APP_DESC} what to do differently

2 approvals waiting · Enter to confirm · Esc to decline current
```

当前请求完成后：

1. 先完成当前 future。
2. 记录 decision/source/elapsed。
3. 从队列取下一项。
4. 用同一个 approval overlay id 替换内容。
5. 不关闭主 TUI、不恢复输入焦点、不重置 Thinking 状态。

### 4.3 过期、取消和关闭

| 事件 | 当前请求结果 | 队列行为 | 输入焦点 |
| --- | --- | --- | --- |
| approval 已过期 | `expired` | 继续下一项 | 保持审批 overlay |
| Esc/n | `decline` | 继续下一项 | 保持审批 overlay |
| Ctrl-C | `cancel` | 当前请求取消，队列按关闭策略处理 | 恢复原 surface |
| TUI close | `decline` 或 `cancel`，按现有契约 | 全部 future 收束 | 恢复输入/退出 |
| 外部已解决 | 当前 request 出队 | 不重复提交 | 保持当前 queue |

不会把“审批过期”渲染成普通失败，也不会在过期时追加第二个 approval card。

## 5. 目标状态模型

### 5.1 请求身份

每个审批请求都必须有稳定的匹配身份：

```text
ApprovalRequestKey
├── approval_id
├── call_id
├── tool
└── request_kind
```

如果远端只提供部分字段，适配层生成稳定的本地 key；不得使用选项数组下标作为身份。

### 5.2 队列状态

```text
EMPTY
  │ request
  ▼
CURRENT_VISIBLE
  ├─ enqueue        -> CURRENT_VISIBLE + QUEUED[n]
  ├─ decision       -> ADVANCING
  ├─ expiry         -> ADVANCING
  ├─ external_done  -> ADVANCING
  └─ close          -> CLOSED

ADVANCING
  ├─ queue nonempty -> CURRENT_VISIBLE(next)
  └─ queue empty    -> EMPTY

CLOSED
  └─ all unresolved futures receive safe terminal result
```

### 5.3 队列所有权

队列只能有一个所有者。推荐让 `ApprovalCoordinator` 拥有顺序和 future，交互端只负责
展示当前请求和报告用户动作：

```text
ApprovalCoordinator
├── current request
├── pending deque
├── request futures
└── decision source

ApprovalOverlay interaction
├── current view model
├── queue count/status
├── overlay request identity
└── user action -> decision
```

现有 `asyncio.Lock` 不能继续作为唯一队列实现，因为它会把等待请求隐藏在锁后面。可以
保留串行决策保证，但必须改成可观察的 current/deque 模型：

- 新请求先进入 coordinator 队列。
- 当前没有请求时启动交互 worker。
- 当前请求完成后 worker 自动处理下一项。
- UI 收到 queue count 或完整 queue snapshot，用于更新 menu status。
- worker 关闭时所有未决 future 都返回一致的安全结果。

## 6. 独立 ApprovalOverlay 设计

ApprovalOverlay 是一个独立的审批交互 surface，但不拥有审批业务决策。它复用现有
BottomPane、activity 和 Screen 生命周期，视觉和行为对齐 Codex，不复用普通 TuiMenu 的
导航栈。

### 6.1 Overlay 请求

推荐的稳定请求身份：

```text
overlay_id = "approval:active"
generation = coordinator queue revision
```

请求字段职责：

| 字段 | 用途 |
| --- | --- |
| `title` | Codex 对齐问题标题 |
| `body_fragments` | 环境、原因、来源、命令和结构化详情 |
| `options` | 当前 approval decisions |
| `status` | 队列数量、过期时间、来源 |
| `footer_hint` | Enter/Esc/快捷键提示 |
| `overlay_id` | 防止陈旧异步更新覆盖当前请求 |
| `generation` | 当前 queue revision |
| `body_wrap` | 长命令和长路径宽度适配 |
| `allow_cancel` | Ctrl-C/Esc 行为 |

不要把 approval dict 原样交给 overlay renderer。先转换成中立的 ApprovalViewModel，再生成
ApprovalOverlayRequest。

### 6.2 选项映射

现有 decision value 保持不变：

| decision | Codex 风格展示 | 快捷键 |
| --- | --- | --- |
| `accept` | Yes, proceed | `y` |
| `acceptForSession` | Yes, for this session | `s` |
| `acceptWithExecpolicyAmendment` | Yes, and don't ask again for this command prefix | `p` |
| `decline` | No, and tell `{APP_DESC}` what to do differently | `n`/`Esc` |
| `cancel` | 不作为普通 option 展示 | `Ctrl-C` |
| `expired` | 不作为普通 option 展示 | timer |

如果某个请求不支持某个 decision，必须从模型生成选项，而不是显示后在 callback 中拒绝。

### 6.3 内容投影

现有 `approval_render.py` 中有价值的内容提取逻辑继续复用：

- approval question
- Agent/source
- Environment
- Reason/justification
- command preview
- expiry label
- canonical arguments 或 MCP 字段

需要迁移的是表面 token 和布局边界：

- 删除旧 `approval-card` 的装饰性背景和自定义卡片边框语义。
- 使用 Codex overlay 的标题、body、option、status、footer token。
- 命令预览沿用统一命令高亮，但不重复输出 `$` 或 tool 名。
- overlay 内容区域使用当前终端宽度，命令和 reason 使用同一个 wrap width。
- 窄终端时选项 detail 堆叠在 label 下方，overlay 高度随内容重新测量。
- 不引入新的 Application、输入 buffer 或焦点系统。

## 7. Runtime 与 activity 生命周期

### 7.1 请求入口

`TuiRuntime.request_approval()` 保留为稳定 runtime 入口，但内部顺序调整为：

```text
request_approval(approval)
  -> coordinator.enqueue(approval)
  -> activity.pause_wait()
  -> ApprovalOverlay worker presents current request
  -> await request future
  -> activity.resume_wait()
  -> return decision
```

暂停范围必须覆盖等待审批的整个过程，包括队列切换；不能在 current decision 后、下一条
审批显示前恢复 Thinking 再马上暂停。

### 7.2 Overlay session 防护

每次 current request 替换都增加 generation：

1. `request_key` 不匹配的异步结果丢弃。
2. 旧 expiry task 不能结束新请求。
3. 旧 overlay 关闭回调不能恢复主输入焦点。
4. 当前请求完成后，只能由 coordinator 推进下一项。
5. TUI close 取消全部 pending overlay actions。

复用现有 `BottomPane` surface stack、generation 和 runtime handoff；不把审批请求塞进
`TuiMenu` 的 view stack。

### 7.3 不再使用的生命周期

迁移完成后，审批路径不能再依赖旧 card 实现：

- `TuiApproval` 的单一 `ApprovalState`。
- card window 是否存在来判断 activity 是否暂停。
- card 是否可见来判断请求是否已处理。
- 独立 input buffer 或第二套焦点系统。

目标 API 可以保留 `screen.approval_overlay.begin/wait/resolve/settle`，但状态必须由
current/deque overlay coordinator 拥有。

## 8. 架构调整清单

### 8.1 保留模块

- `mind_app/approval/models.py`：决策值和审批记录。
- `mind_app/approval/policy.py`：决策集合、标签、过期和策略。
- `mind_app/approval/coordinator.py`：改为可观察队列协调器。
- `mind_app/stream_events/approval_trace.py`：请求内容和命令预览提取。
- `mind_app/stream_events/command_preview.py`：命令片段解析。
- `mind_app/tui/core/bottom_pane.py`：审批 overlay surface 栈和焦点恢复。
- `mind_app/tui/core/screen.py`：审批 overlay 的控件树和高度预算协调。

### 8.2 新增或迁移模块

建议按职责增加窄模块，不创建宽型 facade：

| 模块 | 职责 |
| --- | --- |
| `mind_app/tui/contracts/approval.py` | ApprovalRequestKey、ApprovalViewModel、queue snapshot |
| `mind_app/tui/features/approval.py` | ApprovalViewModel -> ApprovalOverlayRequest、decision mapping |
| `mind_app/runtime/approval_queue.py` 或等价 runtime 协作者 | current/deque/future/expiry 顺序 |
| `mind_app/tui/rendering/approval.py` | Codex overlay 的纯 Fragment 内容投影，不能读取 TuiScreen |
| `mind_app/tui/core/approval_overlay.py` | overlay 控件、输入绑定、current request 和 settle 生命周期 |

如果现有模块边界已经能承载职责，不强制创建所有文件；禁止为了拆文件增加只转发一次
调用的 facade。

### 8.3 重构模块耦合

保留并重构：

- `BottomSurface = "approval"`，但 surface 对象改为 `ApprovalOverlay`。
- `screen.approval_control`、`approval_window`、`approval_footer_window`，改为 Codex
  overlay 的 header/body/options/footer 控件组合。
- `allocate_approval_view_layout`，改为 Codex overlay 的统一内容高度预算。
- `TuiRuntime.request_approval()`，改为调用 queue-aware ApprovalOverlay。

删除或替换：

- `TuiApproval` 的单请求 card 状态和单独 key binding 实现。
- `approval-card` 的装饰性背景、卡片边框和非 Codex spacing。
- runtime 通过 `screen.approval.state` 直接读取审批业务状态的耦合。

审批领域模型、`ApprovalCoordinator` 公共入口和审批 surface 本身不能删除或移动到
rendering 层；目标是把旧 card 重构为 Codex 风格 overlay。

## 9. Codex 对齐与 ProxyMind 保留项

### 9.1 必须对齐 Codex

- current + queue 的可观察生命周期，使用独立 ApprovalOverlay 承载。
- 一个请求一个稳定身份。
- header/body/options/footer 的选择视图布局。
- 完成后自动推进下一项。
- 快捷键、取消、过期和关闭的语义。
- 长命令、长 reason、窄宽度下的换行。
- stale result 防护。
- 审批等待期间不重复启动 Thinking/动画。

### 9.2 可以保留的 ProxyMind 能力

- Hook allow/deny 优先级。
- `ApprovalStore` 的 approval id、tool、canonical arguments 校验。
- `ApprovalDecisionValue` 和现有 decision source telemetry。
- 子执行线程 agent source 展示。
- `ApprovalCoordinator` 的跨请求串行决策保证。
- 当前项目已有的 activity lease、transcript 和菜单 session 机制。

### 9.3 不在本计划中偷换的语义

- `Approve for me` 不能因为改了审批 overlay 就假装具备自动 reviewer。
- `cancel` 不能被 overlay Esc 自动改写成 `decline`，除非当前业务契约明确要求。
- Hook 拒绝不能经过 UI overlay 二次批准。
- `approval_policy=never` 不应打开审批 overlay。
- 已经发起的审批不能因为用户修改下一轮权限而改变 reviewer/决策上下文。

## 10. 分阶段实施计划

### Phase A：审批行为基线

- [ ] 固定当前 `ApprovalDecisionValue`、source、expiry 和 close 行为测试。
- [ ] 增加两个并发审批请求的顺序测试。
- [ ] 增加请求在第一条审批等待时进入 queue 的测试。
- [ ] 增加不同 tool/kind 的 request identity 测试。
- [ ] 增加外部 resolved、重复 resolved 和 stale result 测试。

验收：不改 UI 的前提下，先证明队列顺序和 future 收束规则。

### Phase B：中立 ViewModel 和 Overlay 渲染

- [ ] 定义 `ApprovalRequestKey` 和 `ApprovalViewModel`。
- [ ] 将 `approval_render.py` 的内容提取逻辑转换为纯 view model/fragment 生成。
- [ ] 生成 `ApprovalOverlayRequest`，使用稳定 `overlay_id` 和 generation。
- [ ] 对齐 Codex 标题、选项、快捷键、footer 和窄宽布局。
- [ ] 增加 40、60、80、120 列快照测试。

验收：overlay 显示与 Codex 业务内容和布局一致，但不依赖旧 card style。

### Phase C：Queue worker 与 runtime 接入

- [ ] 将 lock-only coordinator 改为 current/deque/future worker。
- [ ] 将 queue count/status 传递给 overlay view model。
- [ ] `TuiRuntime.request_approval()` 改为 enqueue + await future。
- [ ] 将 pause/resume wait 包在整个 queue 生命周期中。
- [ ] 增加 overlay session/generation 的 stale callback 防护。

验收：连续审批只存在一个 ApprovalOverlay，当前完成后自动显示下一项，Thinking 不重复启动。

### Phase D：重构为 Codex ApprovalOverlay

- [ ] 将 stream approval event 改为调用 queue-aware ApprovalOverlay。
- [ ] 保留 `screen.approval` surface，替换为 `ApprovalOverlay` 控件组合。
- [ ] 删除旧 card 的 layout/window/control/key binding 专用分支。
- [ ] 关闭 runtime 时通过 coordinator 收束全部 approval futures。
- [ ] 迁移 activity、bottom pane、spacing 和 approval 测试。

验收：代码中不再创建旧 `TuiApproval` card；审批只能通过 Codex 风格 ApprovalOverlay 呈现。

### Phase E：全链路与性能复核

- [ ] 审批和 MCP/Helix 状态同时更新时只产生一个稳定 active surface。
- [ ] 审批完成后不重启 Thinking/启动动画。
- [ ] 长命令和长 reason 不触发全屏重排抖动。
- [ ] 过期倒计时按秒更新，不以高频 timer 重建整棵控件树。
- [ ] 执行 TUI 全量回归、resize、窄终端、Ctrl-C、close 和异常路径测试。

验收：审批卡迁移不会复现“Thinking 停止时卡住”或两个状态落版时等待不返回的问题。

## 11. 测试迁移矩阵

### 11.1 必须新增

| 场景 | 必须断言 |
| --- | --- |
| 单条审批 | 一个 ApprovalOverlayRequest，一个 decision future |
| 连续两条审批 | FIFO，第二条可见 queue 状态 |
| 三种 tool kind | Exec/ApplyPatch/MCP 使用同一生命周期，内容投影不同 |
| Enter | 选择当前 option 并只提交一次 |
| y/s/p/n/Esc | 映射到正确 decision value |
| Ctrl-C | 返回 cancel，队列按关闭策略收束 |
| expiry | 返回 expired，自动推进下一项 |
| stale update | 旧 request 不能覆盖新 overlay |
| external resolve | 不重复提交 decision |
| TUI close | 所有 future 都可收束，不遗留 expiry task |
| narrow width | 标题、命令、reason、options 和 footer 不溢出 |
| activity handoff | 审批等待期间 Thinking 不恢复，完成后只恢复一次 |

### 11.2 需要迁移的现有测试

- `tests/test_tui_approval.py`：从旧 card/style/window 断言迁移为 ApprovalOverlay 和行为断言。
- `tests/test_approval_coordinator.py`：增加 FIFO queue、future 收束和 stale request。
- `tests/test_tui_activity.py`：从 `screen.approval.active` 改为 overlay session/queue 状态。
- `tests/test_tui_bottom_pane.py`：验证 approval overlay surface 和焦点恢复。
- `tests/test_tui_spacing.py`：保留 overlay 高度、窄宽和恢复焦点断言。
- `tests/test_run_result.py`：保留 Hook/policy/store 决策回归，不绑定 card 实现。

旧测试不能直接删除；凡是验证用户可观察行为的断言都要保留，只替换实现细节断言。

## 12. 风险和禁止事项

### 12.1 主要风险

| 风险 | 控制措施 |
| --- | --- |
| coordinator lock 隐藏等待请求 | 先落地 current/deque，再迁移 overlay |
| overlay close 误恢复输入 | 统一由 queue worker 拥有焦点恢复 |
| expiry task 结束新请求 | 每个请求绑定 key + generation |
| decision 重复提交 | future done 检查和 request key 校验 |
| 审批暂停期间动画恢复 | pause lease 覆盖整个 queue worker 生命周期 |
| card 样式删除导致内容丢失 | 先建立 ApprovalViewModel 快照，再替换旧 surface |
| reviewer 语义混入 UI 迁移 | 与 `codex_approve_for_me_alignment.md` 分离 |

### 12.2 禁止事项

- 不在 `ApprovalOverlay` 内判断 tool 名或 approval policy。
- 不把审批 dict 直接拼成字符串传给 renderer。
- 不新增第二个 Application 或第二套焦点系统。
- 不让 `ApprovalCoordinator` 和 `ApprovalOverlay` 同时拥有 queue。
- 不通过全局变量或隐式单例保存当前审批。
- 不用 overlay 选项下标作为 approval identity。
- 不以删除测试来证明 card 已移除。

## 13. 完成定义

审批卡对齐完成必须同时满足：

- 运行期不再创建旧 `TuiApproval` card。
- 所有审批类型都通过 Codex 风格独立 `ApprovalOverlay` 呈现。
- current/queue 有明确单一所有者，连续请求 FIFO 且可观察。
- decision、source、expiry、cancel 和 Hook/policy 优先级保持正确。
- stale menu、重复 resolved、TUI close 和进程异常都不会遗留 future/task。
- 长命令、长 reason、窄终端和 resize 不造成重叠或截断错误。
- 审批等待、完成和下一条请求之间不重复启动 Thinking 或动画。
- 相关 approval、coordinator、activity、menu、turn execution 测试通过。

## 14. 首个实施批次

第一批只建立不改变展示表面的基础：

1. 给现有审批请求补充稳定 `ApprovalRequestKey`。
2. 将 `ApprovalCoordinator` 从 lock-only 模型改为可测试的 current/deque/future 模型。
3. 增加连续审批、过期、取消、close 和 stale result 测试。
4. 实现 ApprovalViewModel 到 ApprovalOverlayRequest 的纯转换，并增加快照。
5. 暂时保留旧 `TuiApproval` 作为适配入口，确认 overlay 数据和队列行为稳定后再替换旧 card。

这样可以先验证审批顺序和决策安全，再进行高风险的 Screen/BottomSurface 删除。
