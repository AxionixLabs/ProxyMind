# TUI Inline Viewport 分阶段改造清单

> 状态：阶段 3 已完成；阶段 4 待实施。
> 基准日期：2026-09-09。
> 性质：临时实施与验收清单，不定义长期架构事实。
> 完成后处理：全部阶段验收通过后，由用户手动删除本文档。

本文用于把 Mind 主 TUI 从 prompt_toolkit 的相对 inline 输出提交方式，迁移为由 Mind
持有物理视口、使用绝对坐标提交的稳定 inline viewport。输入缓冲、按键、补全和逻辑布局仍由
prompt_toolkit 提供；终端视口、帧差异、物理光标、扩容滚动和提交事务由 Mind 统一负责。

客户端职责、依赖方向、状态所有权和生命周期以 `ARCHITECTURE.md` 为准；测试执行方式以
`AGENTS.md` 和 `tests/README.md` 为准。本文只记录阶段任务、源码对照和验收门槛。

## 1. 问题与目标

### 1.1 已确认事实

- [x] Mind 主界面使用 `Application(full_screen=False)`，正常输入运行在 inline 模式。
- [x] footer 是一行独立布局，不是中文输入时动态创建的额外行。
- [x] 输入法预编辑文本没有进入 prompt_toolkit Buffer，Mind 无法从输入内容可靠识别预编辑状态。
- [x] prompt_toolkit inline renderer 向下移动时使用相对 `CRLF`，并维护相对逻辑光标。
- [x] PyCharm Reworked Terminal 中开始中文预编辑时，Mind footer 会被宿主临时视觉行向下推移。
- [x] 相同环境切换 Classic Terminal Engine 后不再出现该问题。
- [x] 当前 Codex 主会话同样支持 inline viewport，但由自有 renderer 显式管理视口和绝对坐标，
      未复现相同 footer 位移。

### 1.1.1 阶段 0 基线记录

- 记录环境：Windows、Python 3.11.8、prompt_toolkit 3.0.52、PyCharm 2026.2.1，
  `TERMINAL_EMULATOR=JetBrains-JediTerm`。
- 复现步骤：在 PyCharm Reworked Terminal 启动 Mind，聚焦主输入框，使用微软拼音开始中文预编辑；
  拼音预编辑期间观察输入区下方 footer。切换同一 PyCharm 的 Classic Terminal Engine 后重复。
- 观察结果：Reworked 会把 footer 向下推一行；Classic 不推移；当前 `codex-main` 在同类终端场景中不推移。
- 自动基线命令：`python -m pytest tests/frontends/tui/rendering/test_tui_inline_viewport_baseline.py -q`。
- 自动基线结果：`6 passed`；记录了 Buffer/逻辑 footer 行、单行/宽字符/多行输入和旧 renderer 的相对
  `CRLF` 垂直输出。

### 1.2 最终目标

- [ ] 中文输入法开始预编辑、更新拼音、取消和提交期间，输入表面与 footer 的物理行稳定。
- [ ] 保留正常终端 scrollback，不把主会话改成长期 alternate screen。
- [ ] 保留 prompt_toolkit 的 Buffer、KeyBindings、completion、layout 和焦点语义。
- [ ] 物理 viewport 只有一个状态所有者；renderer、scrollback、resize 和 overlay 不再各自推断位置。
- [ ] 帧内纵向移动使用绝对坐标；只有明确的 viewport 扩容和 history 提交可以产生终端滚动。
- [ ] 普通输入、流式输出、resize reflow 和 overlay 切换都在完整的同步输出事务中收敛。
- [ ] Windows、Linux、macOS 使用同一渲染语义，平台差异只位于职责明确的 terminal adapter。

### 1.3 禁止方案

- [ ] 不通过增加 footer 空行、改变输入框 padding 或永久预留“IME 行”掩盖位移。
- [ ] 不依据中文字符、输入法名称、候选词文本或时间窗口猜测预编辑状态。
- [ ] 不用 `TerminalKind.JETBRAINS_JEDITERM` 直接切换整套行为；身份不能替代输出能力事实。
- [ ] 不把正常主会话强制切换到 alternate screen。
- [ ] 不 monkey patch prompt_toolkit 模块级私有函数，不在运行时替换全局 renderer 行为。
- [ ] 不创建 `support`、`utils` 或其他职责模糊的模块。
- [ ] 不并存两套长期 inline viewport Authority；新路径接管后，同阶段删除被替代旧路径。

## 2. 目标状态所有权

| 状态                                     | 唯一所有者                | 约束                                       |
|------------------------------------------|---------------------------|--------------------------------------------|
| 输入文本、选择区和编辑光标               | prompt_toolkit Buffer     | 只包含已提交输入，不伪造 IME 预编辑文本    |
| composer、菜单、footer 的逻辑尺寸        | `TuiScreen` 与纯布局函数  | 不读取终端环境，不维护物理行号             |
| 终端能力快照                             | platform terminal adapter | 输入开始前探测一次，renderer 只消费结果    |
| inline viewport 的物理起点、宽高和上一帧 | 新的具体 inline renderer  | 唯一允许把逻辑坐标映射到物理终端坐标的组件 |
| transcript 分页和稳定正文范围            | `TuiTranscriptViewport`   | 不直接成为物理光标或 raster Authority      |
| alternate screen 进入与恢复              | TUI terminal lifecycle    | 保存并恢复同一个 inline viewport 状态      |

预期数据流：

```text
prompt_toolkit Buffer / KeyBindings / Completer
  -> TuiScreen layout
  -> prompt_toolkit Screen raster
  -> Mind inline viewport diff
  -> absolute terminal writes + final absolute cursor
```

## 3. 阶段 0：证据与回归契约冻结

### 改造项

- [x] 记录 PyCharm Reworked、PyCharm Classic 和同终端内 Codex 的基线版本、终端尺寸及复现步骤。
- [x] 增加可记录原始输出命令的测试 Output，区分内容写入、相对移动、绝对移动、滚动和清除。
- [x] 固定单行输入、中文宽字符、多行输入、软换行、footer、slash menu 和文件补全的当前 raster；
      菜单和补全继续由现有定向测试作为未改造基线。
- [x] 固定 viewport 位于终端中部、紧贴底部以及 terminal resize 后的三类坐标基线；现有 resize/reflow
      测试与本阶段输入宽度矩阵共同冻结该契约。
- [x] 建立“宿主在输入光标处临时加入一条外部视觉行”的测试模型；该模型不写入 Buffer。
- [x] 记录现有失败：外部视觉行出现时，逻辑输入高度不变，但 footer 物理位置发生变化。
- [x] 本阶段不修改生产渲染行为。

### Codex 源码对照

- [自有终端以启动光标初始化空 viewport](./codex-main/codex-rs/tui/src/custom_terminal.rs#L177)
- [viewport_area 是终端对象持有的显式状态](./codex-main/codex-rs/tui/src/custom_terminal.rs#L218)
- [应用按组件 desired_height 请求一帧](./codex-main/codex-rs/tui/src/app.rs#L850)
- [composer 高度只由已提交文本和可见组件计算](./codex-main/codex-rs/tui/src/bottom_pane/chat_composer.rs#L4550)
- [非 ASCII 路径只处理输入法提交后的字符](./codex-main/codex-rs/tui/src/bottom_pane/chat_composer.rs#L2007)

### 阶段 0 验收

- [x] 自动测试可以证明预编辑模拟不会改变 Buffer 文本和逻辑 composer 高度。
- [x] 自动测试可以稳定记录旧相对输出路径的垂直 `CRLF`，而不是只做截图字符串比较；宿主视觉位移由手工基线确认。
- [x] 手工基线明确记录：Reworked 复现、Classic 不复现、当前 Codex 不复现。
- [x] 测试不修改进程环境来伪造终端能力，能力和 Output 均从上层注入。
- [x] `git diff --check` 通过，且暂存区只包含本阶段测试和基线材料。

## 4. 阶段 1：终端输出能力契约

### 改造项

- [x] 在现有不可变终端能力快照中表达本改造实际需要的输出事实：绝对光标寻址、同步输出、视口尺寸和启动光标位置是否可获得。
- [x] 由 platform adapter 在读取用户输入前完成探测；renderer 不读取环境变量或识别终端品牌。
- [x] Windows Console/ConPTY、VT/PTY 和无交互 Output 分别在 adapter 边界转换为同一具名能力。
- [x] 明确探测失败语义，不把未知能力静默提升为已支持，也不从颜色能力推断光标能力。
- [x] 保持现有颜色、主题和终端身份字段职责不变。
- [x] 为测试 Output 提供显式能力 fixture，不让测试替身扩大生产公开面。

### Codex 源码对照

- [启动阶段探测并选定初始 cursor position](./codex-main/codex-rs/tui/src/tui.rs#L442)
- [Windows 使用平台终端 API 获取初始 cursor position](./codex-main/codex-rs/tui/src/tui.rs#L490)
- [自有终端保存 last_known_screen_size 与 cursor position](./codex-main/codex-rs/tui/src/custom_terminal.rs#L224)
- [resize 只更新明确的终端尺寸事实](./codex-main/codex-rs/tui/src/custom_terminal.rs#L295)

### 阶段 1 验收

- [x] capability 测试覆盖支持、明确不支持和探测失败三种结果。
- [x] JetBrains 身份不会自动等价于某项输出能力，Windows Terminal 身份也不会被误用为能力证明。
- [x] renderer 和逻辑布局中不存在新增的 `os.environ`、平台字符串或终端品牌判断。
- [x] Windows、Unix 和 Dummy Output 的契约测试通过。
- [x] 现有终端身份、颜色与主题测试无回归。

阶段 1 复核结果：`tests/frontends/terminal/test_terminal_capabilities.py` 为 `56 passed`；CLI
定向测试为 `98 passed`；运行时类型与阶段 0 基线为 `24 passed`；架构审计为 `138 passed`；
`compileall` 与 `git diff --check` 通过。

## 5. 阶段 2：自有绝对坐标 Inline Renderer

### 改造项

- [x] 在 `frontends/tui/rendering/screen/` 下建立职责明确的具体 inline renderer，不建立通用
      `support` 模块。
- [x] renderer 接收 prompt_toolkit 已完成布局的 `Screen` raster，不接收业务事件或 Turn 状态。
- [x] renderer 显式保存 viewport 起点、宽高、上一帧 raster、最终光标和终端尺寸 generation。
- [x] viewport 高度增长超过可见终端底部时，仅滚动所需行数，然后重新固定物理起点。
- [x] viewport 缩小、宽度变化和 generation 变化时明确清理失效区域，不依赖残留终端内容。
- [x] diff 对每个变化单元使用绝对坐标；帧内向下移动不得使用 `CRLF` 创建隐式行。
- [x] 正确处理双宽字符、组合字符、尾随空白清理、背景色、超链接和 autowrap 边界。
- [x] 内容 diff、viewport 调整和最终光标在一次 synchronized output 事务中提交。
- [x] 同步输出不受支持时使用同一绝对坐标语义，只失去原子可见性，不切回相对 renderer。

### Codex 源码对照

- [设置 viewport 时同步调整前后帧 buffer](./codex-main/codex-rs/tui/src/custom_terminal.rs#L304)
- [每帧完整绘制后再计算 buffer diff](./codex-main/codex-rs/tui/src/custom_terminal.rs#L321)
- [diff 保留宽字符和强制重绘语义](./codex-main/codex-rs/tui/src/custom_terminal.rs#L578)
- [终端写入使用绝对 MoveTo](./codex-main/codex-rs/tui/src/custom_terminal.rs#L689)
- [内容 flush 后设置最终绝对光标](./codex-main/codex-rs/tui/src/custom_terminal.rs#L419)
- [失效 viewport 会强制重绘默认空白单元](./codex-main/codex-rs/tui/src/custom_terminal.rs#L501)

### 阶段 2 验收

- [x] 稳定 viewport 内的普通帧输出不包含用于纵向寻址的 `CRLF`。
- [x] 模拟外部视觉行插入后，下一次 diff 和最终光标仍落在原 viewport 的绝对坐标。
- [x] viewport 只有在明确扩容时滚动，扩容行数与新增高度完全一致。
- [x] 单宽、双宽、组合字符和行尾宽字符缩短测试均无陈旧单元。
- [x] 连续相同帧不产生内容写入；单单元变化只提交必要 diff。
- [x] 同步输出开始、内容/坐标命令、最终光标和同步输出结束的顺序可由测试证明。

## 6. 阶段 3：主输入与 Footer 接管

### 改造项

- [x] 在唯一 TUI 组合位置安装新 renderer，保持 prompt_toolkit Application、Buffer 和 Layout 生命周期。
- [x] composer、completion、menu 和 footer 全部进入同一个 raster 与 viewport，不单独移动 footer。
- [x] 输入文本变化只改变逻辑 raster；物理 viewport 高度只依据稳定布局结果调整。
- [x] 删除被替代的相对 inline 光标/高度推断路径，不保留 JetBrains 专用旁路。
- [x] 收敛当前对 prompt_toolkit renderer 私有位置状态的直接依赖；保留确有必要的固定版本边界时，
      由新 renderer 内部集中隔离并写明删除条件。
- [x] footer 的显示规则、输入表面 padding 和补全布局保持原有产品语义。

### Codex 源码对照

- [composer 在一个区域内统一分配输入、popup 和 footer](./codex-main/codex-rs/tui/src/bottom_pane/chat_composer.rs#L991)
- [输入光标由 composer 根据同一 area 返回](./codex-main/codex-rs/tui/src/bottom_pane/chat_composer.rs#L1055)
- [composer、附件和 TextArea 写入同一个 frame buffer](./codex-main/codex-rs/tui/src/bottom_pane/chat_composer.rs#L4585)
- [应用在渲染同一帧后设置其 cursor position](./codex-main/codex-rs/tui/src/app.rs#L869)

### 阶段 3 验收

- [x] 单行与多行输入、左右移动、Home/End、删除、历史输入和撤销行为保持不变。
- [x] 中文、日文、韩文以及 emoji 已提交文本的宽度、换行和光标位置正确。
- [x] slash、文件、skill 和 mention popup 打开、过滤、选择、关闭时不改变 footer 之外的无关行。
- [x] popup 隐藏时 footer 恢复到确定物理行，不出现残留或额外空行。
- [x] 输入达到软换行阈值时 viewport 只增长一次，删除回单行时正确清除旧行。
- [x] 既有 TUI 输入、completion、menu 和布局定向测试全部通过。

## 7. 阶段 4：Scrollback、流式输出与 Resize Reflow 统一

### 改造项

- [ ] `TuiTranscriptViewport` 继续拥有正文分页和稳定提交范围，但通过新 renderer 请求物理插入。
- [ ] 删除 transcript viewport、prompt_toolkit renderer 对物理 cursor/viewport 的重复推断。
- [ ] history 插入前保存 inline viewport，插入后按明确行数更新物理起点并使受影响 raster 失效。
- [ ] resize reflow 使用一次终端尺寸 generation 重建 scrollback 和 inline frame，不交叉使用旧坐标。
- [ ] 流式正文批量提交、状态行交接和 composer 重绘在同一视觉事务中保持顺序。
- [ ] 终端高度缩小时不先隐式滚动再 replay，终端增长时仅在原 viewport 底部对齐时重新对齐。
- [ ] 超链接 metadata 随内容重排，不通过不可见转义序列污染宽度和物理列计算。

### Codex 源码对照

- [resize-reflow 路径集中调整 inline viewport](./codex-main/codex-rs/tui/src/tui.rs#L887)
- [viewport 超过终端底部时只增长所需空间](./codex-main/codex-rs/tui/src/tui.rs#L904)
- [history 在 frame 绘制前由统一入口提交](./codex-main/codex-rs/tui/src/tui.rs#L929)
- [resize-reflow 跳过旧 cursor-position heuristic](./codex-main/codex-rs/tui/src/tui.rs#L1088)
- [resize 时只有明确条件会重新定位 viewport](./codex-main/codex-rs/tui/src/tui.rs#L1156)

### 阶段 4 验收

- [ ] 流式输出期间输入内容、光标和 footer 不抖动、不丢失、不重复绘制。
- [ ] 宽度 resize storm 最终只按最后稳定尺寸完成一次权威 reflow。
- [ ] 高度缩小和增长后，viewport 不越界，footer 可见性符合逻辑预算。
- [ ] history 插入数量、scrollback 内容和 viewport 起点变化可以逐项对账。
- [ ] resize 后不存在旧宽度残留、重复 transcript 行或超链接错位。
- [ ] 既有 document scrollback、resize reflow、stream rendering 和 hyperlink 测试全部通过。

## 8. 阶段 5：Overlay、挂起和关闭生命周期

### 改造项

- [ ] 进入 alternate screen 前保存完整 inline viewport 状态，退出后原位恢复并强制必要重绘。
- [ ] transcript、mailbox、static pager、resume picker 和审批 overlay 复用同一进入/退出契约。
- [ ] 外部编辑器、Shell、进程挂起和恢复不遗留同步输出模式、隐藏光标或错误 autowrap 状态。
- [ ] renderer 关闭顺序明确恢复 cursor style、光标可见性、autowrap、同步输出和 alternate screen。
- [ ] 删除旧 `_InlineRendererState` 与新 renderer 重复保存的字段和恢复分支。
- [ ] 任一进入、绘制或退出异常仍执行剩余终端恢复动作，不吞掉原始异常。

### Codex 源码对照

- [进入 alternate screen 时保存 inline viewport](./codex-main/codex-rs/tui/src/tui.rs#L810)
- [退出 alternate screen 时恢复原 viewport](./codex-main/codex-rs/tui/src/tui.rs#L834)
- [Terminal Drop 尽力恢复 cursor style 和可见性](./codex-main/codex-rs/tui/src/custom_terminal.rs#L152)
- [clear viewport 后重置 diff buffer](./codex-main/codex-rs/tui/src/custom_terminal.rs#L484)

### 阶段 5 验收

- [ ] 每个 overlay 连续开关至少三次，退出后输入文本、光标、viewport 起点和 footer 均恢复。
- [ ] overlay 打开或关闭失败时，下一帧仍可正常输入且终端状态完整恢复。
- [ ] 外部命令、外部编辑器和 suspend/resume 返回后无多余空行或 scrollback 丢失。
- [ ] 正常退出、异常退出和取消启动均不会遗留 alternate screen 或 synchronized output。
- [ ] 既有 transcript overlay、resume picker、approval、process 和 lifecycle 测试全部通过。

## 9. 阶段 6：真实终端与 IME 准出

### 自动验证

- [ ] Windows ConPTY、Unix PTY 和内存终端运行同构 inline viewport 场景。
- [ ] 覆盖 viewport 位于顶部、中部、底部，终端高度不足，宽度临界换行和连续 resize。
- [ ] 覆盖同步输出支持与不支持的 Output；两者最终 raster 和物理坐标一致。
- [ ] 运行至少 10,000 次单字符编辑和重绘，确认 viewport 状态、buffer 数量和内存不持续增长。
- [ ] 对比改造前后稳定帧输出量，普通单字符输入不得退化为无条件全屏重绘。

### 手工 IME 矩阵

- [ ] PyCharm Reworked Terminal：微软拼音开始、连续更新、取消、选词和提交。
- [ ] PyCharm Reworked Terminal：输入框中部编辑、行尾编辑、临界软换行、多行输入和长拼音。
- [ ] PyCharm Reworked Terminal：slash menu、文件补全和运行中状态行存在时进行中文预编辑。
- [ ] PyCharm Classic Terminal：执行同构场景，确认没有新回归。
- [ ] Windows Terminal：执行同构场景，确认普通 ConPTY 行为稳定。
- [ ] 当前 Codex：使用相同终端尺寸和输入法作为行为对照，不把其截图作为 Mind 的自动测试替代品。

### 阶段 6 验收

- [ ] 预编辑开始、更新和取消期间，footer 的物理行号保持不变。
- [ ] 预编辑期间无额外空行、重复 border、候选词遮挡或输入光标跳行。
- [ ] 提交中文后只按真实文本宽度触发必要的输入换行和 viewport 增长。
- [ ] 取消预编辑后 raster 与开始前一致，不需要额外按键或 resize 才能恢复。
- [ ] Reworked、Classic 和 Windows Terminal 全部通过；任何一个真实场景失败都不得关闭问题。
- [ ] PTY、性能和资源稳定性结果可重复，并记录所用终端版本与测试命令。

## 10. 阶段 7：旧路径删除与发布收口

### 改造项

- [ ] 删除被替代的相对 inline 物理位置推断、重复 cursor 缓存和兼容分支。
- [ ] 删除阶段 0 的临时诊断输出，只保留可长期维护的契约测试和终端测试工具。
- [ ] 搜索确认不存在为 IME、中文、PyCharm 或 JediTerm 添加的布局补丁。
- [ ] 更新 `ARCHITECTURE.md` 中已经稳定成立的 viewport Authority 与 terminal capability 事实。
- [ ] 更新受影响的测试说明；不把实施流水账、排障过程或临时方案写入长期文档。
- [ ] 复核 prompt_toolkit 固定版本边界；若仍使用其私有接口，集中说明职责、版本约束和升级测试。
- [ ] 全部验收后勾选本文档，由用户手动删除本文档。

### Codex 源码对照

- [自有 Terminal 把 raster、viewport 和 cursor 统一在一个生命周期](./codex-main/codex-rs/tui/src/custom_terminal.rs#L130)
- [主绘制入口在同步事务中依次调整 viewport、history 和 frame](./codex-main/codex-rs/tui/src/tui.rs#L954)
- [composer footer spacing 是明确布局常量而非终端补丁](./codex-main/codex-rs/tui/src/bottom_pane/chat_composer.rs#L1047)

### 阶段 7 验收

- [ ] `rg` 证明旧物理位置路径和临时兼容分支已经删除，不存在两套 viewport Authority。
- [ ] 定向 TUI 测试、终端 PTY 测试、架构审计、`compileall` 和 `git diff --check` 全部通过。
- [ ] 受影响测试无新增无条件 skip；真实平台缺失只能如实记录，不能计为通过。
- [ ] 文档只保留稳定架构事实，本文档仍作为唯一临时实施清单等待用户删除。
- [ ] 最终提交不混入 `/review`、menu、Sandbox 或其他现有用户改动。

## 11. 每阶段提交与复核纪律

- [ ] 每个阶段开始前重新检查 `git status --short`，保留用户现有改动。
- [ ] 每个阶段只暂存本阶段文件，先完成代码复核和验收，再独立提交并推送。
- [ ] 阶段验收失败时不提交、不推送，也不提前勾选准出项。
- [ ] 每次提交后在本节记录提交哈希、验证命令、结果和未覆盖的真实终端条件。
- [ ] 下一阶段开始前复核上一阶段没有引入第二个状态所有者或未声明兼容路径。

| 阶段                | 状态   | 提交 | 自动验证 | 手工验证 | 复核结论 |
|---------------------|--------|------|----------|----------|----------|
| 0 证据冻结          | 已完成 | `0f6ce987` | 289 passed | 已记录 | 通过 |
| 1 能力契约          | 已完成 | `2629d349` | 178 passed | 未涉及 | 通过 |
| 2 Inline Renderer   | 已完成 | `4d9274f7` | 11 passed | 未涉及 | 通过 |
| 3 主输入接管        | 已完成 | `9646c596` | 283 passed | 未涉及 | 通过 |
| 4 Scrollback/Resize | 待开始 | -    | -        | -        | -        |
| 5 Overlay/Lifecycle | 待开始 | -    | -        | -        | -        |
| 6 平台与 IME        | 待开始 | -    | -        | -        | -        |
| 7 发布收口          | 待开始 | -    | -        | -        | -        |

阶段 1 提交后复核：`2629d349` 已推送到 `origin/main`。自动验证还包括架构审计 `138 passed`、
`compileall` 和 `git diff --check`；真实 PyCharm Reworked/Classic、ConPTY 和 IME 交互仍留在
阶段 6 准出，不在本阶段声称已验证。

阶段 2 提交后复核：`4d9274f7` 已推送到 `origin/main`。renderer 的绝对坐标、viewport 滚动、
尺寸 generation、宽字符清理和同步事务均由内存 Output 契约测试覆盖；真实终端接入和 IME 交互
仍留在阶段 6。

阶段 3 提交后复核：`9646c596` 已推送到 `origin/main`。主 Application 组合点已安装自有
renderer，输入、completion、menu、footer 与 overlay 均由同一 Screen raster 提交；输入与布局
定向测试为 `283 passed`，并通过架构审计 `138 passed`。不具备 VT 控制或绝对寻址的输出继续由
能力边界保护，Windows 原生 Console 滚动 adapter 与真实 IME 交互留在阶段 6。

## 12. 最终完成定义

- [ ] 阶段 0 至阶段 7 的全部准出项通过。
- [ ] PyCharm Reworked Terminal 的真实中文预编辑问题不再复现。
- [ ] 正常 inline scrollback、输入、completion、stream、resize 和 overlay 行为无回归。
- [ ] 物理 viewport、raster diff 和最终 cursor 只有一个可由代码确认的状态所有者。
- [ ] 没有 JetBrains/IME/中文专用布局分支，没有永久备用 renderer。
- [ ] 稳定事实已经进入正式架构与测试文档，临时实施信息没有进入长期契约。
- [ ] 最终提交已经推送，本文档等待用户手动删除。
