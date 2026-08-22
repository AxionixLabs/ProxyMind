# Codex 全面对齐设计与实施计划

> 复核基线：2026-08-23
> 参照源码：工作区内置副本 `codex-main/codex-rs`
> 目标源码：当前 ProxyMind 工作区
> 文档性质：实现设计、行为契约和分阶段实施计划；本文件不直接修改运行代码。

## 1. 目标与边界

本计划把 ProxyMind 的手动 Shell、前台命令、后台进程、`/ps`、输出截断、停止控制、
审批交互和 TUI 动态渲染生命周期统一对齐 Codex。

目标不是把 Rust 代码逐文件翻译成 Python，而是对齐以下可观察契约：

- 同一条命令只有一个稳定的执行 cell，不因开始、输出、完成事件产生重复块。
- `!shell` 使用 Codex 的用户 Shell 输入和队列语义。
- 用户 Shell 使用 ExecCell 风格展示，不再借用 ProcessViewer 生命周期。
- 用户 Shell 不转为后台终端；`/ps` 只投影 unified-exec 后台进程，并写入一次性历史摘要。
- 长输出按终端显示行限制，保留头尾并明确显示省略信息，不能持续刷屏。
- 后台状态由一个明确的生命周期模型拥有，不能通过 Viewer 是否存在来推断。
- 审批保留现有决策契约，并重构为 Codex 风格的独立 ApprovalOverlay。
- MCP、Helix、Thinking、Shell 和后台进程更新共享同一套稳定的帧和活动交接规则。

本计划只覆盖本地 TUI 手动链路及其共享交互层，不改变：

- Native Coding 协议和后端工具的外部调用契约。
- 模型工具的业务权限判断、审批策略和持久化协议。
- `/diff`、`apply_patch` 等已有独立对齐项目的领域语义。
- 既有包路径、CLI 入口和外部 API。

## 2. 参照实现与当前实现

### 2.1 Codex 参照位置

| 能力 | Codex 参照 |
| --- | --- |
| `!` 输入和队列 | `codex-rs/tui/src/chatwidget/input_submission.rs`、`input_flow.rs` |
| TUI 到 app-server | `codex-rs/tui/src/app/thread_routing.rs`、`app_server_session.rs` |
| app-server Shell 路由 | `codex-rs/app-server/src/request_processors/thread_processor.rs` |
| 用户 Shell 执行 | `codex-rs/core/src/tasks/user_shell.rs`、`session/handlers.rs` |
| 前台命令 cell | `codex-rs/tui/src/exec_cell/render.rs`、`exec_cell/model.rs` |
| 命令输出增量生命周期 | `codex-rs/tui/src/chatwidget/command_lifecycle.rs` |
| 后台状态 footer | `codex-rs/tui/src/bottom_pane/unified_exec_footer.rs` |
| `/ps` 历史单元 | `codex-rs/tui/src/history_cell/exec.rs`、`chatwidget.rs` |
| 审批队列和选项 | `codex-rs/tui/src/bottom_pane/approval_overlay.rs` |
| 长输出限制 | `codex-rs/tui/src/exec_cell/live_output.rs`、`exec_cell/render.rs` |

### 2.2 ProxyMind 当前入口

| 能力 | 当前入口 | 当前问题 |
| --- | --- | --- |
| `!` 输入 | `mind_app/tui/core/input.py`、`core/submission.py` | Shell 在活动轮次中被延迟，缺少 Codex 的即时辅助动作 |
| Shell 启动 | `mind_app/tui/features/shell.py:56` | 简单命令可能绕过 Shell，固定 timeout，并启动 Viewer-backed watcher |
| 进程会话 | `mind_app/native_coding/exec/process_session.py` | 只有 running/exited，没有前台/后台状态 |
| 前台展示 | `mind_app/tui/features/processes.py:544` | 内部依赖 `TuiProcessViewer`，但以 inline active block 展示并保留主输入 |
| `/ps` | `mind_app/tui/features/processes.py:129` | 使用可交互菜单，运行项选择后再次进入 Viewer；与 Codex 历史摘要不同 |
| 进程状态 | `mind_app/tui/core/process_status.py` | 只展示第一条命令和 `+N`，未统一 `/stop` 契约 |
| 审批 | `mind_app/tui/core/approval.py` | 自制独立 card，单一当前状态，队列由 coordinator 锁隐藏处理 |

### 2.3 手动 Shell 表面复核结论

ProxyMind 当前的 `!command` 不能简单归类为“走模态框”，准确描述是：

1. `run_shell_escape()` 调用 `watch_exec_session(..., viewer_mode="inline",
   capture_input=False)`。
2. `TuiProcessViewer.begin()` 仍创建独占 viewer state/future，因此执行生命周期和单实例约束
   依赖 Viewer。
3. `capture_input=False` 时不会调用 `focus_viewer()`，`BottomSurface` 也不会激活
   `process_viewer`；对应 card 的条件不成立，主输入和 footer 保持可见。
4. 命令输出实际写入 transcript/document 的 `active_renderable`，不是显示在可见的
   process viewer card 中。

所以当前实现是 **Viewer-backed inline cell**：实现层走 Viewer，用户可见层不是 modal。
`FormattedTextControl(modal=True)` 只说明该 control 激活时会成为 prompt_toolkit modal；不能据此
判定 `capture_input=False` 的手动 Shell 正在显示模态框。

Codex 不走这条路径：

```text
!command
  -> AppCommand::RunUserShellCommand
  -> thread/shellCommand
  -> Op::RunUserShellCommand
  -> CommandExecution(source=UserShell) start/delta/completed
  -> transcript.active_cell: ExecCell
```

- `thread/shellCommand` 是用户主动的 full-access、unsandboxed Shell escape，不继承线程
  sandbox policy；它不是模型 `command/exec`。
- TUI 收到 `CommandExecution` 后创建或更新 transcript 的 active `ExecCell`，没有
  ProcessViewer、dialog、popup 或 approval modal。
- 运行中标题为 `Running`，结束后 UserShell 标题为 `You ran`；输出增量只 bump active-cell
  revision。
- UserShell 事件的 `process_id` 为 `None`，不会进入 `unified_exec_processes`，因此不会被
  `/ps` 管理或 detach 到后台。
- Codex `/ps` 直接插入 `UnifiedExecProcessesCell` 历史单元，只列正在运行的 unified-exec
  进程及最近 3 个输出片段；它不是菜单，也没有进程详情 Viewer。

## 3. 目标用户行为

### 3.1 用户 Shell `!adb logcat`

```text
用户输入 !adb logcat
        │
        ▼
创建一个 UserShell/ExecCell，状态 Running
        │
        ├─ 输出增量：原地更新同一个 cell，最多 50 个显示行
        │
        ├─ 用户输入普通文本：按活动状态排队，不打断 Shell
        │
        ├─ 用户输入另一个 !shell：立即作为辅助 Shell 执行
        │
        ├─ 用户输入 /ps：插入 unified-exec 后台终端快照，不 detach 该 UserShell
        │
        └─ Ctrl+C：通过当前 turn cancellation 中断命令并提交失败状态
```

运行中 cell 的目标标题：

```text
• Running adb logcat
```

完成后的目标标题：

```text
• You ran adb logcat
```

输出必须是有界的。终端宽度不足时按显示行而不是逻辑行计算，长输出保留头尾并显示：

```text
  └ first visible line
    … +N lines
  └ latest visible line
```

### 3.2 UserShell 与后台终端分流

Codex 不把 `!command` 从前台 detach 到后台。UserShell 和 background terminal 是两个来源
不同、生命周期不同的集合：

```text
USER_SHELL_CREATED
        │ CommandExecution started
        ▼
USER_SHELL_RUNNING
        ├─ output delta -> USER_SHELL_RUNNING
        ├─ exit         -> USER_SHELL_COMPLETED/FAILED
        └─ Ctrl+C       -> USER_SHELL_CANCELLED

UNIFIED_EXEC_STARTED
        │ process_id available
        ▼
BACKGROUND_RUNNING
        ├─ output delta -> update recent chunks
        ├─ exit         -> remove from running set
        └─ /stop        -> terminate all background terminals
```

`/ps` 只读取第二个集合。若 ProxyMind 继续保留“把用户 Shell detach 到后台”或“单进程详情
菜单”，应明确标记为 ProxyMind 扩展，不能作为 Codex 对齐验收条件。本计划的默认验收采用
Codex 严格语义。

### 3.3 `/ps`

`/ps` 在空闲或流式期间都插入一次性历史摘要，只展示当前正在运行的 unified-exec 后台
进程，不展示 UserShell，也不展示已完成记录：

```text
/ps
Background terminals

  • npm run dev
    ↳ ready on http://localhost:3000
  • python worker.py
    ↳ processing job 42
```

每次 `/ps` 都只创建一个稳定历史 cell：不打开 `MenuRequest`，不激活 modal，不启动或重新
执行进程，也不建立持续刷新 watcher。`/stop` 负责停止全部后台终端。Codex TUI 当前没有从
`/ps` 选择单个进程进入详情或终止单个进程的交互。

### 3.4 审批

审批 overlay 的独立设计、队列模型、快捷键、过期和迁移步骤见
`CODEX_APPROVAL_CARD_ALIGNMENT_DESIGN_AND_PLAN.md`。本总计划只保留一个约束：审批使用
Codex 风格的独立 overlay，但必须复用统一的审批业务协调器；`Approve for me` reviewer
语义继续由 `codex_approve_for_me_alignment.md` 单独负责。

## 4. 统一状态模型

### 4.1 命令来源

ProxyMind 需要把现有 `origin` 字符串收敛为稳定来源值，至少包括：

```text
USER_SHELL       用户输入的 !command
TOOL_EXEC        模型工具启动的后台/前台命令
UNIFIED_EXEC     可进入后台 footer 的统一执行命令
```

后端可以继续保存字符串，但 TUI feature 必须通过具名判断函数或枚举适配，不能在多处
比较裸字符串。

### 4.2 执行状态

```text
USER_SHELL_CREATED
  └─ start accepted -> USER_SHELL_RUNNING

USER_SHELL_RUNNING
  ├─ output delta -> USER_SHELL_RUNNING
  ├─ command exit -> USER_SHELL_COMPLETED/FAILED
  └─ cancellation -> USER_SHELL_CANCELLED

BACKGROUND_RUNNING
  ├─ output delta -> BACKGROUND_RUNNING
  ├─ stop all     -> BACKGROUND_STOPPING
  └─ command exit -> REMOVED
```

UserShell 的展示状态由 active `ExecCell` 拥有；后台集合按 unified-exec 的 `process_id`
维护。`ProcessSessionManager` 只负责实际进程和输出，不用 Viewer 是否存在推断来源或状态。

### 4.3 来源隔离

Codex 允许活动模型轮次中启动辅助 UserShell，连续提交 `!command` 时可以有多个运行中的
UserShell call；不能引入“全局唯一前台 session”约束。目标状态至少分开保存：

```text
active_user_shell_calls: dict[call_id, ExecCallState]
background_terminals: dict[process_id, BackgroundTerminalState]
```

UserShell call 不进入 `background_terminals`，后台进程也不能借用 UserShell cell 的取消或完成
语义。禁止再通过 `process_viewer.active`、`input_passthrough` 或当前焦点推断任何执行状态。

## 5. 执行语义设计

### 5.1 Shell 命令解析

默认行为采用 Codex：用户输入的 Shell 文本作为完整脚本传给会话 Shell，保留：

- 管道、重定向、`&&`、`;`、变量和命令替换。
- Shell built-in、函数、别名和当前 Shell 环境。
- 当前会话选择的 cwd 和派生环境。

`direct_command_args()` 不再作为默认执行路径。若保留，只能作为显式性能优化，并且必须
满足以下条件：

1. 只优化已证明无 Shell 语义差异的命令。
2. 失败时自动回退完整 Shell 脚本。
3. 增加 alias、builtin、引号、重定向、Windows cmd/PowerShell 的回归测试。

### 5.2 交互命令

继续禁止无法在当前 TUI 中安全输入的交互程序，但策略不能散落在命令名称集合中。改为：

- 通过 Shell capability 判断是否支持 stdin/PTY。
- 不支持交互时给出统一的 `interactive command unavailable` 结果。
- UserShell 的 `Ctrl+C` 走当前 session/turn cancellation；后台 `/stop` 走独立终止接口。
- 不把启动失败、交互不支持和用户中断混成同一个文本状态。

## 6. 输出与性能设计

### 6.1 两级输出缓冲

保留后端完整审计缓冲和 TUI 有界显示缓冲，但职责分离：

```text
ProcessSession
├── audit_output       原始有限字节缓冲，用于结果和审计
├── display_lines      有界 head/tail 行缓冲
└── recent_chunks      后台 footer 的最多 3 个摘要片段
```

目标上限：

| 用途 | 上限 |
| --- | ---: |
| 用户 Shell cell | 50 个终端显示行 |
| 普通工具 ExecCell | 5 个终端显示行 |
| 后台 footer/`/ps` | 每个进程 3 个摘要片段 |
| 完整快照 | 保持现有审计上限，不能直接灌入屏幕 |

显示计算必须：

- 先按终端宽度换行，再按屏幕行截断。
- 保留头尾，不能只保留尾部而丢失命令初始上下文。
- 显示省略数量。
- 输出变化只更新 active cell revision，不重建整个 transcript。

### 6.2 事件与轮询

后端暂时无法提供完整输出增量事件时，可以保留轮询作为兼容层，但必须：

- 轮询只生成结构化 `ProcessSnapshot`。
- snapshot 与上次相同则不触发渲染。
- UserShell cell 和后台摘要分别计算，不重复构造完整 transcript 文本。
- 进程退出只提交一次完成事件。
- 高频 `adb logcat` 输出不能触发全屏 layout/animation 重启。

后续可将 `ProcessSessionManager` 的输出读取改为事件订阅，但不在第一阶段强制重写后端。

## 7. TUI 表面设计

### 7.1 删除的表面

完成迁移后删除以下专用交互表面和专用布局：

- `TuiProcessViewer`
- `ProcessViewerRequest`
- `BottomSurface = "process_viewer"`
- `allocate_process_viewer_layout`
- `TuiScreen.process_viewer_*`
- `TuiRuntime.begin/update/resolve/dismiss/commit_process_viewer`
- `ProcessRuntimePort` 中对应 Viewer 方法
- Viewer 专用样式、窗口和 spacing 分支

审批不会删除独立 surface，而是按
`CODEX_APPROVAL_CARD_ALIGNMENT_DESIGN_AND_PLAN.md` 的“架构调整清单”和 Phase D 重构为
Codex 风格 `ApprovalOverlay`；不删除审批领域模型、审批策略、决策值和 coordinator 的业务契约。

### 7.2 保留和增强的表面

- 主 transcript/document：承载稳定 ExecCell 风格命令记录。
- `TuiMenu`：继续承载其他既有选择交互，不参与 Codex 严格语义下的 `/ps`。
- `/ps` history cell：承载 unified-exec 后台进程的一次性快照。
- `ApprovalOverlay`：承载 Codex 风格的审批请求、审批队列和决策快捷键。
- process status footer：展示后台终端数量、`/ps` 和 `/stop`。
- 现有 activity handoff：只保留一次稳定的输入/输出交接。

### 7.3 `/ps` 稳定投影

`/ps` 直接从 `background_terminals` 生成一个历史 cell：

1. 提交命令时读取一次当前快照。
2. 每个进程只投影命令和最多 3 个 recent chunks。
3. 空集合显示 `No background terminals running.`。
4. cell 提交后保持稳定，不建立菜单 session、轮询或替换任务。
5. 不创建 process viewer surface，也不混入 UserShell 和已完成记录。

## 8. 分阶段实施计划

每个阶段都必须独立可验证，完成后再进入下一阶段。阶段之间不保留只转发一次调用的
兼容 facade；稳定外部入口可以保留兼容导入，内部生命周期不能双轨运行。

### Phase 0：基线冻结和行为测试

- [ ] 新增 `!adb logcat`、`!echo hi`、`!`、活动轮次 Shell、普通文本排队的行为测试。
- [ ] 固化当前 `capture_input=False` 路径：Viewer state 活跃，但 bottom-pane modal 未激活且输入可见。
- [ ] 新增 UserShell 与 unified-exec 后台来源隔离测试。
- [ ] 新增 `/ps` 不打开 Viewer/Menu、不启动进程且不包含 UserShell 的目标测试。
- [ ] 新增 50 行 UserShell 输出、3 片段后台摘要、长行换行和省略数量测试。
- [ ] 记录当前旧 Viewer/审批卡测试，迁移前不删除。

验收：测试能明确区分“当前实现行为”和“目标行为”，后续失败不会被旧快照掩盖。

### Phase 1：命令来源和生命周期状态

- [ ] 定义 `ProcessOrigin`/来源适配和 `ProcessLifecycleState`。
- [ ] 以 call/session id 保存可并发 UserShell call，以 process id 保存 unified-exec 后台集合。
- [ ] 将 UserShell cancellation/exit 与后台 stop/exit 分成独立状态迁移。
- [ ] `running_snapshot()` 不再把所有运行会话默认视为 `/ps` 后台项。
- [ ] 保留 `ProcessSessionManager` 的启动、输出和终止能力。

验收：任何时刻能通过来源和 process id 判断 `/ps` 是否可展示某进程；UserShell 永不进入
后台集合。

### Phase 2：Codex Shell 输入和执行语义

- [ ] 活动模型轮次中 `!command` 立即执行辅助 Shell。
- [ ] 普通文本在“只有用户 Shell 运行”时排队。
- [ ] 队列中的 Shell 使用具名 `RunShell` 动作，不依赖布尔 `shell_mode` 推断执行意图。
- [ ] 空 `!` 对齐 Codex 帮助行为。
- [ ] 默认传递完整 Shell 脚本，移除默认 `direct_command_args()` 快速路径。
- [ ] cwd、环境、timeout 和交互限制形成显式执行策略。

验收：Codex 对应的 `bang_shell_enter_while_task_running`、普通文本排队和 Shell 历史行为全部
有 ProxyMind 等价测试。

### Phase 3：UserShell ExecCell 展示

- [ ] 新增结构化 `ExecCellState`，保存 call/session、命令、状态、输出、退出码和耗时。
- [ ] 实现 `Running`、`You ran`、失败、中断和无输出标题。
- [ ] UserShell 显示最多 50 个屏幕行。
- [ ] 输出按宽度换行后 head/tail 截断，带省略信息。
- [ ] UserShell 输出只更新 active cell revision，不重建完整 transcript 快照。
- [ ] `Ctrl+C` 走 UserShell 所属 session/turn cancellation，不复用后台 `/stop`。

验收：`!adb logcat` 始终只有一个动态 cell，没有 Viewer、没有重复 `$ command` 块，
高频输出不会触发全屏动画重启。

### Phase 4：移除 ProcessViewer，迁移 `/ps`

- [ ] `/ps` 只读取正在运行的 unified-exec 后台集合，不读取 UserShell 或已完成记录。
- [ ] `/ps` 直接提交一个 `UnifiedExecProcessesCell` 等价历史块，不打开菜单或 Viewer。
- [ ] 空后台集合显示 `No background terminals running.`。
- [ ] `/ps` 不重新执行、不创建新的 ProcessSession。
- [ ] `/stop` 与统一后台控制服务绑定，并停止全部后台终端。
- [ ] 删除 Viewer runtime port、screen window、layout 和专用样式。
- [ ] 删除旧 Viewer 测试，替换为 ExecCell 和稳定历史摘要测试。

验收：用户执行 `!adb logcat` 后提交 `/ps`，UserShell 继续独立运行；`/ps` 只增加一条后台
终端历史摘要，输入区不被替换，也不存在详情选择交互。

### Phase 5：Codex 后台 footer 和 `/ps` 摘要

- [ ] footer 统一为 `N background terminal(s) running · /ps to view · /stop to close`。
- [ ] 统一使用 `Background terminals`，删除 `Background Commands` 分叉文案。
- [ ] 每个后台进程保存最多 3 个 recent chunks。
- [ ] `/ps` 流式路径和空闲路径使用同一份摘要模型。
- [ ] ProxyMind 的跨会话完成记录如需保留，使用独立入口，不混入 Codex `/ps` 投影。
- [ ] `/stop` 与后台终端集合使用同一控制服务。

验收：中断模型轮次、完成模型轮次后，仍在运行的后台进程能通过 `/ps` 查看；已退出进程
从下一次 `/ps` 快照中移除。

### Phase 6：Codex ApprovalOverlay

审批 overlay 迁移不在本总计划中重复拆解，按独立文档执行：
`CODEX_APPROVAL_CARD_ALIGNMENT_DESIGN_AND_PLAN.md` 的 Phase A-E。

总体验收：审批决策、暂停/恢复、过期、取消、TUI close 和 activity handoff 保持原业务语义，
视觉表面为 Codex 风格独立 ApprovalOverlay。

### Phase 7：动画和全链路收敛

- [ ] 将 Shell、MCP、Helix、审批、后台进程的动态更新接入统一 activity handoff。
- [ ] 同一事件只能拥有一个 active renderable 更新者。
- [ ] 完成态只负责提交稳定 cell，不重启 Thinking/启动动画。
- [ ] 消除 ProcessViewer 专属 `synchronize_next_render` 路径；ApprovalOverlay 使用统一的
      activity handoff 和 frame 提交边界。
- [ ] 检查窗口高度、滚屏、resize、窄终端和退出清理。
- [ ] 执行 TUI 全量回归和长时间 `adb logcat` 压力测试。

验收：MCP/Helix 状态落版、Thinking 停止、Shell 输出收束和 `/ps` 打开之间没有卡帧、重复
动画或残留动态块。

## 9. 测试迁移矩阵

### 9.1 必须新增

| 场景 | 断言 |
| --- | --- |
| 空 `!` | 显示帮助，不创建进程 |
| `!echo hi` | 一个 UserShell cell，历史文本保留 `!echo hi` |
| 活动轮次输入 `!echo hi` | 立即提交 Shell auxiliary action |
| Shell 运行中普通文本 | 排队，不 steering 当前 Shell |
| Shell 运行中第二条 `!` | 立即执行第二个 Shell |
| UserShell 输出 | 最多 50 个显示行，头尾保留 |
| UserShell 与后台隔离 | UserShell 无 `process_id`，不进入 `/ps` |
| `/ps` | 只列运行中 unified-exec，不打开 Viewer/Menu、不启动进程 |
| `/ps` 重复执行 | 每次生成独立稳定快照，不建立持续 watcher |
| 后台完成 | 从后续 `/ps` 快照和 footer 中移除 |
| `/stop` | 停止全部 unified-exec 后台终端 |
| 连续审批 | 当前完成后自动显示下一条 |

### 9.2 需要替换的旧测试

- `tests/test_tui_shell.py` 中直接断言 `TuiProcessViewer` 的测试。
- `tests/test_tui_spacing.py` 中 process viewer window、padding、focus 的测试。
- `tests/test_tui_bottom_pane.py` 中 `process_viewer` surface 和审批 overlay 焦点恢复的测试。
- `tests/test_tui_approval.py` 中审批 card 的实现细节测试，按独立审批文档迁移为 overlay 行为测试。
- `tests/test_tui_activity.py` 中 approval/process handoff 的测试，按独立审批文档保留 overlay 生命周期断言。

旧测试不能简单删除，必须转化为行为断言：来源状态、稳定正文、焦点恢复、输入是否可见以及
后台任务是否清理。`/ps` 测试不再断言菜单 view id。

## 10. 兼容和风险控制

### 10.1 不采用的方案

- 不保留“前台 Viewer + 新 ExecCell”双轨展示。
- 不让 `/ps` 通过重新调用 `start_user_shell_session()` 查看输出。
- 不把 UserShell detach 后伪装成 unified-exec 后台终端。
- 不为 `/ps` 增加 Codex 不存在的单进程详情菜单作为默认对齐目标。
- 不把所有进程输出直接追加到 transcript。
- 不用 `process_viewer.active` 作为后台状态源。
- 不通过新增宽型 `TuiRuntime` facade 解决端口依赖。
- 不一次性重写 `ProcessSessionManager` 和模型事件协议。

### 10.2 主要风险

| 风险 | 控制措施 |
| --- | --- |
| 移除 Viewer 后改变 ProxyMind 既有单进程详情能力 | 若产品确认保留，明确放到 Codex 对齐范围外的扩展入口 |
| Shell 语义改变导致 Windows 命令回归 | 分平台 Shell adapter 和命令语义测试 |
| 高频输出继续触发渲染抖动 | active cell revision、有界行缓冲、快照去重 |
| 审批队列改变决策顺序 | 由交互前端 FIFO 队列拥有顺序，先补队列行为测试 |
| 旧 spacing 测试阻碍迁移 | 从窗口几何断言改为可观察行为断言 |
| 跨会话完成状态丢失 | 保留 `ProcessCompletionStore`，只替换投影层 |

## 11. 完成定义

只有同时满足以下条件，才算完成全面对齐：

- `!shell` 的输入、历史、活动轮次和队列行为与 Codex 一致。
- UserShell 使用 active ExecCell，不存在 ProcessViewer 或可见 modal。
- UserShell call 与 unified-exec 后台集合显式隔离，不由 UI surface 推断。
- `/ps` 只生成运行中 unified-exec 的稳定历史摘要，不重新执行、不打开 Viewer/Menu。
- `/stop` 停止全部后台终端；单进程详情/终止不属于 Codex 严格对齐范围。
- UserShell 输出最多 50 个显示行，后台摘要每个进程最多 3 个片段，长输出有明确省略信息。
- footer、`/ps` 标题、命令状态和颜色语义统一使用 Codex 词汇。
- 审批使用 Codex 风格独立 ApprovalOverlay，但不分叉业务审批协调、决策和过期语义。
- MCP、Helix、Thinking、Shell 和后台进程不会因多个动态 surface 争夺同一帧而卡住。
- 相关行为测试、TUI 回归、`python -m py_compile` 和长输出压力验证全部通过。

## 12. 首个实施批次

实际开始编码时，第一批只做以下内容，避免同时改变 Shell 语义和审批交互：

1. 新增 UserShell call 与 unified-exec 后台集合的来源隔离测试。
2. 将 `!shell` 活动轮次输入改为即时 Shell action。
3. 引入 ExecCell 风格的有界 UserShell 输出模型，但暂时保留旧 Viewer 作为兼容实现。
4. 把 `/ps` 改为 unified-exec 后台集合的一次性历史快照，不再打开详情菜单。
5. 验证 `!adb logcat` 与 `/ps` 互不改变对方状态后，再删除 Viewer。

ApprovalOverlay 迁移放在进程链路稳定之后单独实施，避免一次变更同时影响 Shell 输出、活动
handoff 和审批暂停恢复。
