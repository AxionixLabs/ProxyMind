# Codex 全面对齐设计与实施计划

> 复核基线：2026-08-22  
> 参照源码：`D:\codex-main\codex-rs`  
> 目标源码：`D:\PycharmProjects\ProxyMind`  
> 文档性质：实现设计、行为契约和分阶段实施计划；本文件不直接修改运行代码。

## 1. 目标与边界

本计划把 ProxyMind 的手动 Shell、前台命令、后台进程、`/ps`、输出截断、停止控制、
审批交互和 TUI 动态渲染生命周期统一对齐 Codex。

目标不是把 Rust 代码逐文件翻译成 Python，而是对齐以下可观察契约：

- 同一条命令只有一个稳定的执行 cell，不因开始、输出、完成事件产生重复块。
- `!shell` 使用 Codex 的用户 Shell 输入和队列语义。
- 前台 Shell 使用 ExecCell 风格展示，不再使用独立 ProcessViewer。
- 命令转后台后才进入进程查看菜单；`/ps` 不重新执行命令，也不打开 Viewer。
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
| Shell 启动 | `mind_app/tui/features/shell.py:56` | 简单命令可能绕过 Shell，固定 root/idle timeout，使用 Viewer 启动 |
| 进程会话 | `mind_app/native_coding/exec/process_session.py` | 只有 running/exited，没有前台/后台状态 |
| 前台展示 | `mind_app/tui/features/processes.py:544` | 依赖 `TuiProcessViewer`，每 0.12 秒轮询并更新动态块 |
| `/ps` | `mind_app/tui/features/processes.py:129` | 运行项选择后再次进入 Viewer |
| 进程状态 | `mind_app/tui/core/process_status.py` | 只展示第一条命令和 `+N`，未统一 `/stop` 契约 |
| 审批 | `mind_app/tui/core/approval.py` | 自制独立 card，单一当前状态，队列由 coordinator 锁隐藏处理 |

## 3. 目标用户行为

### 3.1 前台 `!adb logcat`

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
        ├─ 用户输入 /ps：先提交当前 Shell cell，再打开后台菜单
        │
        └─ Ctrl+C：中断该进程并提交完成/失败状态
```

前台 cell 的目标标题：

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

### 3.2 从前台转后台

前台进程只能通过以下事件进入后台：

- 用户提交 `/ps` 或其他会结束当前前台展示的本地命令。
- 用户执行显式的 detach/background action。
- 当前 Shell cell 完成后仍需要保留后台会话的情况。

转后台必须是一次状态迁移，而不是关闭 Viewer 后启动另一个隐式 watcher：

```text
FOREGROUND_RUNNING
        │ detach
        ▼
BACKGROUND_RUNNING
        │ process exit
        ▼
BACKGROUND_COMPLETED
        │ acknowledge
        ▼
RETAINED/REMOVED
```

### 3.3 `/ps`

空闲状态下 `/ps` 使用现有 `MenuRequest`，但菜单只展示后台进程和已完成记录：

```text
/ps · Background terminals

› adb logcat                         Shell · pid=1234
  npm run dev                        Exec  · pid=5678
  adb logcat completed               exit=0
  Stop all background terminals
```

选择运行中的进程后进入“进程详情菜单”，不是 ProcessViewer：

```text
Background terminal

Command   adb logcat
PID       1234
Status    running
Output    最近 50 个显示行

› Back
  Stop process
  Interrupt process
```

详情菜单允许动态刷新快照，但不夺取主输入区、不创建独立动态正文、不重新启动进程。

流式期间的 `/ps` 保持 Codex 风格的一次性历史摘要：

```text
/ps · Background terminals

  • adb logcat
    ↳ latest output line
  • npm run dev
    ↳ latest output line
```

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
CREATED
  └─ start accepted -> FOREGROUND_RUNNING

FOREGROUND_RUNNING
  ├─ output delta     -> FOREGROUND_RUNNING
  ├─ interrupt        -> FOREGROUND_STOPPING
  ├─ command exit     -> FOREGROUND_COMPLETED
  └─ detach           -> BACKGROUND_RUNNING

FOREGROUND_STOPPING
  ├─ exited           -> FOREGROUND_COMPLETED
  └─ grace timeout    -> FORCE_STOPPING

BACKGROUND_RUNNING
  ├─ output delta     -> BACKGROUND_RUNNING
  ├─ stop             -> BACKGROUND_STOPPING
  └─ command exit     -> BACKGROUND_COMPLETED

BACKGROUND_COMPLETED
  ├─ same conversation -> queued history result
  └─ other conversation -> retained completion store
```

状态所有权放在 TUI runtime 的进程生命周期协作者中；`ProcessSessionManager` 只负责实际
进程和输出，不判断当前展示表面。

### 4.3 前台唯一性

每个 TUI 会话最多一个 `foreground_session_id`。后台可以有多个会话。

```text
foreground_session_id: str | None
background_session_ids: tuple[str, ...]
```

禁止再通过 `process_viewer.active` 或 `input_passthrough` 推断前台状态。

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
- `Ctrl+C` 统一走进程控制端口。
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
| 用户 Shell 前台 cell | 50 个终端显示行 |
| 普通工具前台 cell | 5 个终端显示行 |
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
- 前台 cell 和后台摘要分别计算，不重复构造完整 transcript 文本。
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
- `TuiMenu`：承载 `/ps` 根菜单、进程详情和停止确认。
- `ApprovalOverlay`：承载 Codex 风格的审批请求、审批队列和决策快捷键。
- process status footer：展示后台终端数量、`/ps` 和 `/stop`。
- 现有 activity handoff：只保留一次稳定的输入/输出交接。

### 7.3 菜单动态刷新

复用现有 `MenuRequest.view_id` 和 `replace_present_menu_if_id`。后台详情菜单的刷新规则：

1. 菜单打开时加载一次 snapshot。
2. 仅当 `status`、输出摘要或控制结果变化时替换 request。
3. 取消/返回时停止该 session 的详情轮询。
4. 进程退出时提交完成状态并回到根菜单或关闭菜单。
5. 不创建 `active_renderable`，不进入 process viewer surface。

## 8. 分阶段实施计划

每个阶段都必须独立可验证，完成后再进入下一阶段。阶段之间不保留只转发一次调用的
兼容 facade；稳定外部入口可以保留兼容导入，内部生命周期不能双轨运行。

### Phase 0：基线冻结和行为测试

- [ ] 新增 `!adb logcat`、`!echo hi`、`!`、活动轮次 Shell、普通文本排队的行为测试。
- [ ] 新增前台/后台状态迁移测试。
- [ ] 新增 `/ps` 不打开 Viewer、不重复启动进程的测试。
- [ ] 新增 50 行前台输出、3 片段后台摘要、长行换行和省略数量测试。
- [ ] 记录当前旧 Viewer/审批卡测试，迁移前不删除。

验收：测试能明确区分“当前实现行为”和“目标行为”，后续失败不会被旧快照掩盖。

### Phase 1：命令来源和生命周期状态

- [ ] 定义 `ProcessOrigin`/来源适配和 `ProcessLifecycleState`。
- [ ] 在 TUI runtime 增加唯一前台 session 和后台 session 集合。
- [ ] 将 detach、stop、interrupt、exit 统一为状态迁移事件。
- [ ] `running_snapshot()` 不再承担前台/后台判断。
- [ ] 保留 `ProcessSessionManager` 的启动、输出和终止能力。

验收：任何时刻能通过结构化状态判断 `/ps` 是否可展示某进程。

### Phase 2：Codex Shell 输入和执行语义

- [ ] 活动模型轮次中 `!command` 立即执行辅助 Shell。
- [ ] 普通文本在“只有用户 Shell 运行”时排队。
- [ ] 队列中的 Shell 使用具名 `RunShell` 动作，不依赖布尔 `shell_mode` 推断执行意图。
- [ ] 空 `!` 对齐 Codex 帮助行为。
- [ ] 默认传递完整 Shell 脚本，移除默认 `direct_command_args()` 快速路径。
- [ ] cwd、环境、timeout 和交互限制形成显式执行策略。

验收：Codex 对应的 `bang_shell_enter_while_task_running`、普通文本排队和 Shell 历史行为全部
有 ProxyMind 等价测试。

### Phase 3：ExecCell 前台展示

- [ ] 新增结构化 `ExecCellState`，保存 call/session、命令、状态、输出、退出码和耗时。
- [ ] 实现 `Running`、`You ran`、失败、中断和无输出标题。
- [ ] 前台用户 Shell 显示最多 50 个屏幕行。
- [ ] 输出按宽度换行后 head/tail 截断，带省略信息。
- [ ] 前台输出只更新 active cell，不更新完整 transcript 快照。
- [ ] `Ctrl+C` 走统一进程控制端口。

验收：`!adb logcat` 始终只有一个动态 cell，没有 Viewer、没有重复 `$ command` 块，
高频输出不会触发全屏动画重启。

### Phase 4：移除 ProcessViewer，迁移 `/ps`

- [ ] `/ps` 根菜单只读取后台会话和已完成记录。
- [ ] 运行中进程进入动态详情菜单，不进入 Viewer。
- [ ] 详情菜单提供 Back、Stop、Interrupt。
- [ ] `/ps` 不重新执行、不创建新的 ProcessSession。
- [ ] 前台 Shell 在 `/ps` 提交边界完成 foreground -> background 迁移。
- [ ] 删除 Viewer runtime port、screen window、layout 和专用样式。
- [ ] 删除旧 Viewer 测试，替换为菜单和状态迁移测试。

验收：用户执行 `!adb logcat` 后提交 `/ps`，看到的是后台菜单；选择进程后仍是菜单详情，
可以停止或返回，输入区不会被 Viewer 替换。

### Phase 5：Codex 后台 footer 和 `/ps` 摘要

- [ ] footer 统一为 `N background terminal(s) running · /ps to view · /stop to close`。
- [ ] 统一使用 `Background terminals`，删除 `Background Commands` 分叉文案。
- [ ] 每个后台进程保存最多 3 个 recent chunks。
- [ ] `/ps` 流式路径和空闲路径使用同一份摘要模型。
- [ ] 保留 ProxyMind 的跨会话完成记录能力，但投影对齐 Codex。
- [ ] `/stop` 与菜单 Stop All 使用同一控制服务。

验收：中断模型轮次、完成模型轮次后，后台进程仍能通过 `/ps` 查看；完成状态只出现一次。

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
| 前台输出 | 最多 50 个显示行，头尾保留 |
| Shell detach | 状态变为 BACKGROUND_RUNNING |
| `/ps` | 只列后台，不打开 Viewer、不启动新进程 |
| 进程详情 | 菜单动态刷新，可 Back/Stop/Interrupt |
| 后台完成 | 只提交一次完成摘要 |
| `/stop` | 与菜单 Stop All 结果一致 |
| 连续审批 | 当前完成后自动显示下一条 |

### 9.2 需要替换的旧测试

- `tests/test_tui_shell.py` 中直接断言 `TuiProcessViewer` 的测试。
- `tests/test_tui_spacing.py` 中 process viewer window、padding、focus 的测试。
- `tests/test_tui_bottom_pane.py` 中 `process_viewer` surface 和审批 overlay 焦点恢复的测试。
- `tests/test_tui_approval.py` 中审批 card 的实现细节测试，按独立审批文档迁移为 overlay 行为测试。
- `tests/test_tui_activity.py` 中 approval/process handoff 的测试，按独立审批文档保留 overlay 生命周期断言。

旧测试不能简单删除，必须转化为行为断言：状态、菜单 view id、稳定正文、焦点恢复、输入
是否可见以及后台任务是否清理。

## 10. 兼容和风险控制

### 10.1 不采用的方案

- 不保留“前台 Viewer + 新 ExecCell”双轨展示。
- 不让 `/ps` 通过重新调用 `start_user_shell_session()` 查看输出。
- 不把所有进程输出直接追加到 transcript。
- 不用 `process_viewer.active` 作为后台状态源。
- 不通过新增宽型 `TuiRuntime` facade 解决端口依赖。
- 不一次性重写 `ProcessSessionManager` 和模型事件协议。

### 10.2 主要风险

| 风险 | 控制措施 |
| --- | --- |
| 移除 Viewer 后丢失单进程停止能力 | Phase 4 先完成详情菜单控制，再删除 Viewer |
| Shell 语义改变导致 Windows 命令回归 | 分平台 Shell adapter 和命令语义测试 |
| 高频输出继续触发渲染抖动 | active cell revision、有界行缓冲、快照去重 |
| 审批队列改变决策顺序 | 保留 coordinator lock，先补队列行为测试 |
| 旧 spacing 测试阻碍迁移 | 从窗口几何断言改为可观察行为断言 |
| 跨会话完成状态丢失 | 保留 `ProcessCompletionStore`，只替换投影层 |

## 11. 完成定义

只有同时满足以下条件，才算完成全面对齐：

- `!shell` 的输入、历史、活动轮次和队列行为与 Codex 一致。
- 前台 Shell 使用单一 ExecCell，不存在 ProcessViewer。
- 前台/后台状态是显式结构化状态，不由 UI surface 推断。
- `/ps` 只查看后台进程和完成记录，不重新执行、不打开 Viewer。
- 后台详情、Stop、Interrupt、Stop All 全部通过统一菜单完成。
- 前台输出最多 50 个显示行，后台摘要最多 3 个片段，长输出有明确省略信息。
- footer、`/ps` 标题、命令状态和颜色语义统一使用 Codex 词汇。
- 审批使用 Codex 风格独立 ApprovalOverlay，但不分叉业务审批协调、决策和过期语义。
- MCP、Helix、Thinking、Shell 和后台进程不会因多个动态 surface 争夺同一帧而卡住。
- 相关行为测试、TUI 回归、`python -m py_compile` 和长输出压力验证全部通过。

## 12. 首个实施批次

实际开始编码时，第一批只做以下内容，避免同时改变 Shell 语义和审批交互：

1. 新增结构化进程状态和前台唯一性测试。
2. 将 `!shell` 活动轮次输入改为即时 Shell action。
3. 引入 ExecCell 风格的有界前台输出模型，但暂时保留旧 Viewer 作为兼容实现。
4. 把 `/ps` 的运行项改为后台状态筛选，并先实现详情菜单的只读快照。
5. 验证 `!adb logcat -> /ps -> 详情 -> Back` 全链路后，再删除 Viewer。

ApprovalOverlay 迁移放在进程链路稳定之后单独实施，避免一次变更同时影响 Shell 输出、活动
handoff 和审批暂停恢复。
