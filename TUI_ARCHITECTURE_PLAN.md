# TUI 架构拆分与迭代计划

本文是 `mind_app/tui` 的长期拆分方案和执行记录。目标是降低超大模块的职责耦合，同时保持 TUI 行为、异步生命周期和现有稳定入口不变。

## 1. 当前基线

| 模块 | 当前规模 | 主要混合职责 | 优先级 |
| --- | ---: | --- | --- |
| `mind_app/tui/core/screen.py` | 约 113 KB / 3194 行 | prompt_toolkit Application、控件树、动态状态读取、overlay 交互、快捷键和 frame 协调 | P0，已收敛 |
| `mind_app/tui/core/menu.py` | 约 30 KB / 922 行 | 菜单栈、Future、状态应用、选择提交、焦点和按键协调 | P0，已收敛 |
| `mind_app/tui/core/runtime.py` | 约 66 KB / 1933 行 | 运行时组合、生命周期桥接、临时 surface、transcript、后台任务和异常边界 | P0，已收敛 |
| `mind_app/tui/adapters/markdown.py` | 约 61 KB / 2093 行 | Markdown 解析、流式稳定前缀、表格、Fragment 转换和最终渲染 | P1，可选 |
| `mind_app/tui/core/input.py` | 约 51 KB / 1489 行 | 输入状态、历史、completion、skill、paste、shell 模式和快捷键 | P1，可选 |
| `mind_app/tui/core/document.py` | 约 40 KB / 1201 行 | transcript 数据、稳定行索引、流式尾部、提交/回退和渲染 | P1，可选 |
| `mind_app/tui/core/viewport.py` | 约 36 KB / 1065 行 | 滚动状态、异步 reflow、native scrollback 和几何重算 | P1，可选 |
| `mind_app/tui/core/transcript_overlay.py` | 约 32 KB / 1000 行 | overlay 生命周期、搜索、回退选择、缓存和渲染 | P1，可选 |
| `mind_app/tui/adapters/output.py` | 约 29 KB / 885 行 | 流式输出、调度、宽度重排、最终提交和 Markdown 调用 | P1，可选 |
| `mind_app/tui/core/activity.py` / `approval_render.py` | 约 22-27 KB | 状态生命周期和展示渲染耦合 | P2，按需求 |
| `mind_app/tui/features/processes.py` / `hooks.py` / `conversation.py` | 约 16-38 KB | 领域操作、菜单构建、异步执行和状态展示 | P2，按需求 |

现有测试中有一部分直接访问 `TuiScreen`、`TuiMenu` 的私有方法。迁移期间保留稳定类和行为，逐步把私有实现测试改成协作者的行为测试，不把测试辅助需求扩大为新的生产 API。

## 2. 目标目录（分阶段，非一次性迁移清单）

以下目录用于约束职责方向，不代表当前迭代必须创建所有文件。已经落地的模块以仓库实际目录和 Iteration 记录为准；未落地的 `application.py`、`controller.py`、`contracts/runtime.py` 及 Markdown/Input/Transcript 候选模块只有在触发 P1 条件时才创建。

最终目录按职责分成契约、渲染、交互、运行时和领域功能：

```text
mind_app/tui/
├── contracts/
│   ├── text.py
│   ├── menu.py
│   ├── transcript.py
│   ├── views.py
│   └── runtime.py
├── rendering/
│   ├── screen/
│   │   ├── controller.py
│   │   ├── geometry.py
│   │   ├── terminal.py
│   │   ├── layout.py
│   │   ├── surfaces.py
│   │   ├── overlays.py
│   │   └── application.py
│   ├── menu/
│   │   ├── controller.py
│   │   ├── state.py
│   │   ├── selection.py
│   │   ├── layout.py
│   │   ├── renderer.py
│   │   └── sanitize.py
│   ├── transcript/
│   │   ├── document.py
│   │   ├── lines.py
│   │   ├── viewport.py
│   │   └── overlay.py
│   └── markdown/
│       ├── parser.py
│       ├── stream.py
│       └── fragments.py
├── interaction/
│   ├── input/
│   │   ├── controller.py
│   │   ├── history.py
│   │   ├── completion.py
│   │   └── bindings.py
│   └── transient/
│       ├── approval.py
│       ├── directory_trust.py
│       ├── process_viewer.py
│       └── mailbox.py
├── runtime/
│   ├── controller.py
│   ├── state.py
│   ├── lifecycle.py
│   ├── surfaces.py
│   ├── transcript.py
│   ├── errors.py
│   └── ports.py
├── session/
├── features/
├── adapters/
└── prompting/
```

## 3. 依赖规则

```text
contracts
    ↑
rendering / interaction
    ↑
runtime.ports
    ↑
features / session
    ↑
CLI / composition root
```

- `contracts` 只放数据结构、枚举、类型别名和窄 Protocol，不依赖具体 TUI 控件。
- `rendering` 可以依赖 `prompt_toolkit` 和 `contracts`，不得导入 `features` 或 `session`。
- `features` 和 `session` 通过 `runtime.ports` 使用运行时能力，不直接依赖具体渲染器。
- CLI 负责最终组装；运行时不根据输出前端做分支判断。
- 不新增没有职责边界的 `utils.py`、`helpers.py` 或只转发一次调用的 facade。

## 4. 迭代计划

每个迭代都必须能独立运行定向测试和 `python -m py_compile`；PyCharm 离线检查脚本按需使用。每个阶段只迁移一个职责边界，避免一次性重写 TUI。

### Iteration 0：基线与约束（已完成）

- [x] 盘点 TUI 大模块、调用链和测试耦合。
- [x] 确认 `screen.py`、`menu.py`、`runtime.py` 是 P0 核心热点。
- [x] 确认 `markdown.py`、`output.py` 的流式性能边界。
- [x] 建立本文档作为后续迭代记录。

### Iteration 1：公共契约与 Menu 基础拆分（已完成）

- [x] 将 `core.models` 中的文本、菜单、transcript、view 契约按职责迁移到 `contracts`。
- [x] 将 `menu.py` 的请求清洗迁移到 `rendering/menu/sanitize.py`。
- [x] 将 `MenuState` 迁移到 `rendering/menu/state.py`；`MenuView` 在后续 Menu 迭代中同步迁移。
- [x] 保留现有 `mind_app.tui.core.models`、`mind_app.tui.core.menu` 的稳定导入入口。
- [x] 运行菜单 alignment、query、bottom pane 和 MCP 定向测试。

验收：菜单行为不变；新契约不依赖 `TuiRuntime`；旧导入路径仍可用；不得新增业务逻辑。

验证结果：`python -m pytest -q tests/test_tui_menu_alignment.py tests/test_tui_bottom_pane.py tests/test_tui_query_block.py tests/test_tui_mcp.py`，112 passed；`python -m compileall -q mind_app/tui` 通过；`inspect-ide-warnings.ps1 mind_app/tui/core/menu.py` 报告 0 个代码警告。全量测试为 2306 passed、2 skipped、4 failed，失败均位于未修改的 `tests/test_config_session.py` hooks 配置路径。

### Iteration 2：Menu 渲染与交互拆分（已完成）

- [x] 提取 selection/filter/generation 的纯状态算法。
- [x] 提取页签请求构造和循环切换算法；协调器只应用新状态并触发重绘。
- [x] 将菜单 view 状态适配对象迁移到 `rendering/menu/state.py`。
- [x] 提取标题、正文、页签、footer 包裹和 surface section 的纯 Fragment 辅助渲染。
- [x] 提取 option row 渲染、列宽分配和 row wrap。
- [x] 提取可见窗口、选中项移动/定位和过滤归一化算法。
- [x] 提取通用 Fragment 行数测量；菜单高度组合仍由协调器调用。
- [x] 消除 `rendering` 对 `core` 的运行时和类型检查反向依赖，并加入 import-layer 架构测试。
- [x] 合并显示与高度测量的重复渲染路径，使用同一个指定宽度的 surface 结果。
- [x] 删除迁移期单次转发 facade，`TuiMenu` 仅保留生命周期、栈管理、状态应用和协调职责。

验收：菜单快照、搜索、页签、禁用项、嵌套菜单和取消行为全部保持。

完成结果：新增 `rendering/menu/selection.py`、`layout.py`、`renderer.py`、`rows.py`、`surface.py`、`measure.py`、`tabs.py`，并将 view 适配迁移到 `state.py`；`core/menu.py` 从约 2323 行降至约 922 行。可见窗口、移动、定位、查询归一化和页签切换均为显式状态算法；完整 surface/footer 由不可变 `MenuRenderConfig` 驱动，显示与高度测量不再存在两套拼装逻辑。`rendering/fragments.py` 承载组合字符、裁剪、换行、填充、显示行数和光标几何等纯文本原语，`rendering/text_sanitize.py` 承载终端文本清洗；旧 `core.render` 只保留 5 行兼容导出。最终菜单、bottom pane、query、MCP 和架构定向回归为 113 passed。

### Iteration 3：Screen 终端与几何拆分（P0 已完成，残余保留）

- [x] 抽取终端 VT 控制和同步输出生命周期。
- [x] 抽取 `FrameGeometry`、`ComposerLayout`、`ActiveViewLayout`、`BottomPaneLayout`。
- [x] 抽取底部面板输入/completion 的高度测量和状态区辅助高度分配。
- [x] 抽取 approval/menu/process viewer 三类 active view 的高度预算策略。
- [x] 保留 `TuiScreen` 作为公开协调入口。

验收：终端 resize、inline/full-screen、同步输出和 degraded terminal 行为不变。

阶段结果：已新增 `rendering/screen/terminal.py`、`geometry.py` 和 `layout.py`；`core/screen.py` 从约 3543 行降至约 3194 行。终端 VT 控制、组合几何对象、输入/completion 预算、状态/进程/排队消息分配，以及 approval/menu/process viewer 三类 active view 的裁剪策略已从 Screen 流程代码中抽出。Screen 仍持有动态内容读取、运行时状态和 frame cache。active view 相关回归为 133 passed；该阶段全量 TUI 基线为 1208 passed。Iteration 4 后续完成 overlay/surface 纯渲染迁移，剩余内容渲染和控件树保持独立边界。

### Iteration 4：Screen surface、overlay 与 Application tree 拆分（P0 已完成，残余保留）

- [x] 统一 transcript/mailbox overlay 的 header/content/footer 几何测量。
- [x] 抽取 transcript/mailbox 的标题和进度分隔线纯 Fragment 渲染。
- [x] 抽取 queued 行预算/拼接、输入 footer 优先级和通用 footer 渲染。
- [x] 抽取 input prefix、placeholder、completion 提示/空结果/单候选片段渲染。
- [ ] 抽取 transcript/status 及剩余动态 input 内容渲染。
- [ ] 抽取 transcript overlay 和 mailbox overlay。
- [ ] 抽取 prompt_toolkit 控件树构建。
- [ ] 将 screen 私有状态集中到明确的状态对象，不再由布局函数隐式修改状态。

验收：spacing、query block、startup、mailbox、transcript overlay、approval 和 process viewer 测试通过。

阶段结果：新增 `rendering/screen/overlays.py`、`surfaces.py` 和 `OverlayLayout`，两个全屏 overlay 共用 `measure_overlay_layout`；标题与进度分隔线不再读取 Screen 对象。queued、footer、input prefix、placeholder 和 completion 的纯 Fragment 规则也已迁出，Screen 保留动态状态判定和领域对象调用。`core/screen.py` 当前约 3194 行；queued/footer 回归为 566 passed，input/completion 回归为 589 passed；该阶段及后续 Runtime 端口迁移前的全量 TUI 回归为 1211 passed。transcript/status 的简单适配保留在 Screen，复杂状态继续由已有 document/viewport/overlay 对象拥有，不再为单行转发函数新建模块。

### Iteration 5：Runtime 拆分（P0 已完成，端口按需扩展）

最新验证（2026-08-21）：完成 transcript/mailbox 协作者、窄 Screen ports、`ProcessRuntimePort`、`ForegroundRuntimePort`、`TurnRuntimePort`、`TurnInputRuntimePort`、`MenuSelectionPort` 和 `SkillRuntimePort` 后，完整 `test_tui_*.py` 回归为 `1213 passed`；上一处记录中的 `1202 passed` 是 Runtime 状态拆分前的基线。

- [x] 抽取 runtime state 的进程完成缓存、command/activity handoff 和延迟正文队列。
- [x] 抽取后台任务创建、会话去重、异常回报和关闭清理。
- [x] 抽取 Application lifecycle 和致命 error boundary。
- [x] 抽取 startup presentation 队列，保持动画与 Screen startup gate 解耦。
- [x] 抽取动态正文 surface 的 set/commit/stream-prefix/clear 与 transcript/viewport 通知顺序。
- [x] 抽取 transcript replacement、transcript overlay 生命周期和滚屏边界。
- [x] 抽取 mailbox overlay 生命周期，避免与 Screen 控件树同刀迁移。
- [x] 为 transcript/mailbox 建立第一批按能力划分的窄 Screen Protocol。
- [x] 定义 `ProcessRuntimePort` 并迁移 `features/processes.py` 的 Runtime 类型依赖。
- [x] 定义 `ForegroundRuntimePort` 并迁移 `session/barriers.py` 的前台任务屏障。
- [x] 定义 `TurnRuntimePort` 并迁移 `session/turn.py` 的单轮执行生命周期。
- [x] 完成 session 中低风险、调用集合稳定模块的端口迁移；其余模块按实际需求再扩展，不做宽型全量端口化。
- [x] 为 `history.py`、`model.py`、`skills.py` 提供只读菜单/输入能力端口，清除其具体 Runtime 类型依赖。
- [x] 保持菜单、审批、进程查看器和后台任务的异步所有权不变。

验收：runtime 生命周期、异常传播、后台任务清理、startup gate 和 turn execution 测试通过。

阶段基线（Runtime state/lifecycle 完成时）：新增 `runtime/background.py`、`runtime/state.py` 和 `runtime/lifecycle.py`。`BackgroundTaskManager` 拥有后台任务集合、按会话任务索引、完成回收和统一取消；`DeferredBlockBuffer` 以 drain 批次转移延迟正文；`ProcessCompletionStore` 统一进程快照的深拷贝和确认；`CommandLayoutState`、`ActivityHandoffState` 负责各异步上下文的 handoff 隔离；`ApplicationLifecycle` 拥有 prompt_toolkit Application task、失败事件和停止清理。当时 `core/runtime.py` 约 68 KB / 1978 行，runtime、stream、shell、process status、startup 定向回归为 95 passed，生命周期定向回归为 68 passed。后续已完成 startup presentation、surface/transcript 协作者以及 session capability ports 的迁移；当前状态见下方生命周期进度。

生命周期进度：新增 `runtime/lifecycle.py`、`runtime/startup.py`、`runtime/transcript.py`、`runtime/mailbox.py` 和 `runtime/ports.py`。`ApplicationLifecycle` 独立拥有 prompt_toolkit Application task、事件循环异常处理、失败事件、首个错误和停止清理；`StartupPresentationQueue` 独立拥有一次性动画/最终帧的注册、播放和结算；`TranscriptCoordinator` 独立拥有正文替换、动态正文 set/commit/stream-prefix/clear 及 transcript/viewport 通知顺序；`TranscriptOverlayCoordinator` 与 `MailboxOverlayCoordinator` 分别拥有两类全屏画面的打开/关闭、等待、backtrack 和原生滚屏恢复；`TranscriptScreenPort`、`MailboxScreenPort`、`ProcessRuntimePort`、`ForegroundRuntimePort`、`TurnRuntimePort`、`TurnInputRuntimePort`、`MenuSelectionPort` 和 `SkillRuntimePort` 只声明实际调用集合所需的窄能力，协作者不导入具体 `TuiScreen` 或 `TuiRuntime`。`features/processes.py`、`session/barriers.py`、`session/turn.py`、`session/turn_input.py`、`features/history.py`、`features/model.py` 和 `features/skills.py` 已迁移到对应端口，不再类型依赖 `core.runtime`。`core/runtime.py` 当前约 66 KB / 1933 行；进程相关回归为 162 passed，mailbox/transcript/overlay/scrollback 专项为 176 passed，前台屏障定向回归为 89 passed、单轮执行定向回归为 51 passed、轮次输入对账定向回归为 47 passed、只读菜单 feature 定向回归为 106 passed（均含架构守卫），最新完整 TUI 回归为 1213 passed。P0 端口迁移到此收敛；后续只在出现稳定、可验证的窄能力集合时新增端口，不创建宽型 Session facade。

架构调整记录（2026-08-21）：先迁移有明确状态所有权的组件，再迁移需要 Screen/terminal 协调的生命周期流程；对 `_status_fragments` 等单行适配不单独建模块；`runtime.ports` 延后到实际使用集合稳定后按能力拆分，避免用一个宽 Protocol 复制 `TuiRuntime`。本轮补充 `TurnInputRuntimePort`、`MenuSelectionPort` 和 `SkillRuntimePort`，完成 session 低风险输入生命周期及只读菜单 feature 端口化；`session/loop.py` 仍是组合根，复杂 feature 和适配器仍保留具体 Runtime 参数，因为它们跨越菜单、正文、配置和领域操作，当前拆分会制造宽型协议而非降低耦合。

### Iteration 5.1：Archive 生命周期接入（2026-08-22，已完成）

- [x] 将 archive status 的持久化和迁移留在 `mind_app/history/store.py`，不把 SQLite 细节带入 TUI。
- [x] 通过 `ResumePickerRequest` 的窄异步回调接入 archive/unarchive；picker 只拥有 Pending/Restoring 状态、行移除和失败展示。
- [x] 当前会话 `/archive` 由 controller 编排：先迁移 status，生命周期结束失败时回滚，避免写库失败后留下已结束但仍在前台的会话。
- [x] CLI archive/unarchive 在组合根直接复用 history store，按 action 限定 active/archived 集合，不启动完整 TUI runtime。
- [x] 补充 archived touch 不回 active、重复迁移幂等、CLI 全局标题解析和 picker 异步顺序测试。

架构结论：本轮没有新增宽型 Session facade 或渲染层 helper；跨层行为只通过已有 controller 和 `ResumePickerRequest` 回调传递。`resume --last` 与直接会话 id 只选择 active，Archived 行必须完成 unarchive 后才进入既有 Resume 流程。

### Iteration 5.2：当前会话归档确认菜单（2026-08-22，已完成）

- [x] 以 `conversation.confirm_archive_session()` 持有归档确认菜单的文案和选项值，通过 `MenuSelectionPort` 调用运行时。
- [x] dispatcher 只编排确认结果和 controller 调用：取消返回 `HANDLED`，确认后成功退出，失败映射为 Codex 对齐的 thread 错误提示。
- [x] 复用现有 `TUI_MENU_STYLE` 的 bold/cyan/dim/red 语义；由 `MENU_SURFACE_HORIZONTAL_INSET` 统一计算正文、详情和 footer 的 surface 内缩，不在 `MenuRequest` 上增加菜单级间距分支。
- [x] 补充菜单契约、完整 Fragment 文本、取消不变更和关键失败路径测试。

架构结论：没有把确认菜单下沉到 rendering，也没有为单个命令增加宽型 runtime facade。菜单请求仍属于 feature，渲染层只消费共享的 surface 内缩参数；archive 的领域迁移仍由 controller 所有。

### Iteration 5.3：菜单间距统一与 Hooks 对齐（2026-08-22，已完成）

- [x] 用 `MENU_SURFACE_HORIZONTAL_INSET = 2` 作为通用菜单 surface 的单一横向计算源，标题、正文、表格、选中详情和 footer 只在 surface 边界应用一次。
- [x] 移除 `MenuRequest` 上的正文和 footer 间距开关；Hooks 根菜单、Hook 详情菜单和 archive 确认菜单不再通过请求字段分叉布局。
- [x] 对齐 Codex Hooks 的表头、审核提示、固定列和详情字段可见间距，补充根菜单与详情菜单的前导空格回归断言。
- [x] 更新菜单快照，使正文和 footer 的两格内缩由渲染器统一产生，并保留选项 gutter、描述换行和 footer 宽度约束。

架构结论：菜单请求只描述内容和行为，`rendering/menu/layout.py` 持有 surface 几何原语，`rendering/menu/surface.py` 负责一次性应用内缩；`TuiMenu` 继续只拥有菜单生命周期和状态协调。全套菜单定向回归为 228 passed，未新增 feature facade 或菜单专用计算分支。


复核结论：当前 `D:\codex-main\codex-rs\tui\src\snapshots\codex_tui__resume_picker__tests__resume_picker_search_line_sort_filter_tabs.snap` 的 Resume 快照仍包含 `Status: [Active] Archived`；宽屏两控件画面不能由这份源码的 Resume 分支生成，不能据此全局移除 ProxyMind 的归档筛选。

前次复核（2026-08-21）：修复 `CommandLayoutState` 跨异步上下文 reset token 的潜在异常；为 `core/render.py` 兼容入口声明显式导出集合。`runtime.ports` 对 Core 数据类型仍保留运行时导入，以维持协议注解可通过 `typing.get_type_hints()` 观测；该依赖作为后续可选债务，不在本轮扩大迁移范围。定向架构测试 12 passed，完整 TUI 回归保持 1213 passed；以上收敛不改变既有功能行为。

### Iteration 6：Markdown 与流式 Output 拆分（P1 候选，不纳入当前 P0）

- [ ] 分离 Markdown parser、stream state 和 Fragment conversion。
- [ ] 分离 output scheduling、width reflow 和 final commit。
- [ ] 明确工作线程渲染与主线程状态提交的边界。

验收：流式输出、Markdown 表格、宽度变化、Ctrl-C 收束和大文本性能回归通过。

### Iteration 7：Input、Document、Viewport、Overlay（P1 候选，不纳入当前 P0）

- [ ] 拆分 input history/completion/bindings。
- [ ] 拆分 document store、stable lines 和 transcript rendering。
- [ ] 拆分 viewport scroll state 和异步 scrollback scheduler。
- [ ] 拆分 transcript overlay search/backtrack/cache。

验收：输入 completion、paste、history、transcript backtrack、scrollback 和 overlay 搜索测试通过。

### Iteration 8：Activity、Approval 与领域 Feature（P2 候选，不纳入当前 P0）

- [ ] 分离 activity lifecycle 和 status renderer。
- [ ] 分离 approval 数据提取、布局和 Fragment renderer。
- [ ] 拆分 processes、hooks、conversation 等大 feature 文件。
- [ ] 拆分 `session.dispatch` 的命令路由和领域 handler。

验收：所有 TUI 定向测试和全量测试通过；feature 不再直接构造底层渲染控件。

### Iteration 9：兼容入口收缩与架构检查（收尾检查）

- [ ] 将旧 `core/screen.py`、`core/menu.py`、`core/runtime.py`、`core/models.py` 收缩为迁移期入口。
- [ ] 检查仓库内导入方已经迁移到新目录。
- [x] 增加第一阶段 import-layer 检查，禁止 rendering 反向依赖 core；feature/session 规则随 ports 落地后补齐。
- [ ] 删除没有调用方的兼容入口和重复类型定义。

验收：公共入口清晰、依赖方向稳定、无循环导入、无重复实现。

## 5. 收尾评估与剩余范围

截至 2026-08-21，P0 的核心拆分已经完成，当前剩余不是“必须把所有大文件拆完”，而是按风险保留的候选工作：

### 已完成并稳定

- `core/menu.py` 已收敛为菜单生命周期和协调器；纯选择、布局、渲染和清洗逻辑已迁出。
- `core/screen.py` 已完成终端、几何、surface 和纯 Fragment 规则拆分；剩余部分集中在 prompt_toolkit 控件树、frame cache、动态状态读取和快捷键协调，这些职责共享同一 Application 生命周期，继续拆分会增加状态同步成本。
- `core/runtime.py` 已完成 state、后台任务、lifecycle、startup、transcript/mailbox 协作者和首批窄端口迁移；当前作为 TUI 组合根保留是有意设计。
- `features/processes.py`、`session/barriers.py`、`session/turn.py`、`session/turn_input.py` 已不再把具体 `TuiRuntime` 作为类型依赖。

### 当前仍保留的规模

- P0 组合根：`screen.py` 约 3194 行、`runtime.py` 约 1933 行、`menu.py` 约 922 行；它们仍大，但职责已经按边界分层，不再要求继续机械切片。
- P1 候选：`markdown.py`、`output.py`、`input.py`、`document.py`、`viewport.py`、`transcript_overlay.py`，共约 7.7k 行；只有出现性能问题、独立生命周期或测试隔离需求时才启动对应迭代。
- P2 候选：`hooks.py`、`activity.py`、`approval_render.py`、`dispatch.py` 及其他 feature；当前仍有 14 个模块保留具体 `TuiRuntime` 参数，主要集中在组合根、适配器和跨领域 feature。优先通过端口和测试边界改善，不以文件行数作为单独拆分理由。

### 明确不做的改造

- 不为 `session/dispatch.py` 建立覆盖全部菜单和 feature 的宽型 `SessionRuntimePort`。
- 不把 `TuiScreen` 的 prompt_toolkit 控件树拆成多个只转发一次调用的 facade。
- 不在没有行为或性能收益的情况下拆分 Markdown 表格、输入快捷键和 scrollback 算法。

### 重新启动 P1 的条件

只有在新增功能需要独立测试/生命周期、性能 profiling 指向具体热点，或模块出现新的跨层依赖时，才开启对应 P1 迭代；启动时必须先补行为基线和端口边界。

## 6. 测试与验证策略

- P0 使用现有菜单、screen、spacing、query、startup、overlay 和 runtime 测试作为行为基线。
- 新模块优先增加核心主流程和关键失败路径测试，不为静态定义值机械增加测试。
- 迁移私有方法测试时，优先改为完整 Fragment、完整 layout 或完整 state 对象比较。
- 每个迭代执行受影响测试和 `python -m py_compile`；需要排查 IDE warning 时可按需执行：

  ```powershell
  .\inspect-ide-warnings.ps1 mind_app/tui
  ```

- 大型流式模块拆分后，额外记录事件循环停顿、渲染耗时和 scrollback 重排行为。

## 7. 完成定义

- `screen.py`、`menu.py`、`runtime.py` 不再同时承载状态、布局和所有渲染算法。
- 新模块通常控制在 10-25 KB；超过范围必须有明确职责说明。
- rendering 不依赖 feature/session；feature/session 不依赖具体屏幕实现。
- 现有 TUI 行为、异步生命周期、终端兼容性和公共入口保持稳定。
- 每个迭代都能单独回滚，不依赖未完成的后续迁移。
