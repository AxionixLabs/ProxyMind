# `/review` 分阶段对齐计划

> 状态：实施中；阶段 0、1、2、3、4 门禁已通过。
> 基准日期：2026-09-08。
> 客户端基准：当前仓库 `codex-main/` 中的 `/review` 实现。
> 服务端基准：`D:\PycharmProjects\AppServer` 当前提交
> `3acc5b1b3ca7826b04c2fccb37264fe70602d2ed`，协议与 prompt 标识为 `mind-review/1`。

本文是实施清单，不定义新的架构事实。客户端职责和依赖方向以 `ARCHITECTURE.md` 为准，
跨系统 Authority 以 `ARCHITECTURE_SYSTEM.md` 为准，线上字段、事件和错误以 AppServer 正式契约
及最终落入 `protocol/schema/`、`protocol/client/` 的客户端契约为准。

## 1. 目标与完成定义

目标是在 ProxyMind TUI 中提供与当前 `codex-main` 行为一致的 `/review` 用户体验，并通过
AppServer 已有的持久 Review Turn 协议完成提交、观察、恢复、中断和结果展示。

完成必须同时满足：

- [ ] `/review` 在命令菜单中的位置、文案、可用性和参数行为与基准一致。
- [ ] 四种预设入口、两级菜单返回关系、搜索、提交和取消行为与基准一致。
- [ ] 菜单尺寸、选中态、说明文字、输入框、页脚和窄终端布局通过快照对比。
- [ ] Git 目标解析和不可变工作区快照跨 Windows、Linux、macOS 可复现。
- [ ] Review 请求在首次网络提交前冻结并持久化，恢复时不重复创建 Turn。
- [ ] AppServer 的结构化 Review 事件进入统一 Item reducer，不由 TUI 猜测协议语义。
- [ ] 成功、失败、取消、断线恢复、冲突和 reconciliation 都收敛到权威终态。
- [ ] 定向测试、协议契约测试、架构审计、编译和 diff 检查全部通过。

## 2. Codex 对齐基准

### 2.1 命令目录

- [ ] 命令名为 `/review`。
- [ ] 描述精确为 `review my current changes and find issues`。
- [ ] 展示顺序位于 `/hooks` 之后、`/rename` 之前。
- [ ] 支持 `/review <instructions>` 行内参数。
- [ ] 活动 Turn 期间不可再次启动 `/review`，沿用现有命令拒绝策略，不新增旁路。
- [ ] 裸 `/review` 打开预设菜单；带参数时跳过菜单并按 `custom` 目标提交去除首尾空白后的指令。

基准文件：

- `codex-main/codex-rs/tui/src/slash_command.rs`
- `codex-main/codex-rs/tui/src/chatwidget/review_popups.rs`

### 2.2 根预设菜单

- [ ] 标题精确为 `Select a review preset`。
- [ ] 使用标准选择菜单页脚和现有 TUI 语义样式。
- [ ] 选项顺序和文字精确为：
  - [ ] `Review against a base branch`，右侧说明为 `(PR Style)`。
  - [ ] `Review uncommitted changes`。
  - [ ] `Review a commit`。
  - [ ] `Custom review instructions`。
- [ ] 未提交改动可直接接受并关闭菜单。
- [ ] 其余三项打开子视图，选择成功后同时关闭子视图和父菜单。
- [ ] 子视图第一次 `Esc` 返回根预设菜单，第二次 `Esc` 关闭根菜单。

### 2.3 基础分支选择

- [ ] 标题精确为 `Select a base branch`。
- [ ] 搜索占位符精确为 `Type to search branches`。
- [ ] 只列本地分支，按名称稳定排序；检测到默认分支时置顶。
- [ ] 当前分支为空时使用 `(detached HEAD)`。
- [ ] 展示标签为 `<current branch> -> <base branch>`。
- [ ] 搜索值只使用基础分支名称，不把展示箭头文本并入搜索语义。
- [ ] 选中后产生 `base_branch` 目标并保留原始分支名。

### 2.4 提交选择

- [ ] 标题精确为 `Select a commit to review`。
- [ ] 搜索占位符精确为 `Type to search commits`。
- [ ] 最多列出最近 100 个提交。
- [ ] 行内只显示提交 subject，不显示时间戳或额外元数据。
- [ ] 搜索值由 `<subject> <sha>` 组成。
- [ ] 选中后产生含 `sha` 和可选 `title` 的 `commit` 目标。

### 2.5 自定义指令

- [ ] 标题精确为 `Custom review instructions`。
- [ ] 输入占位符精确为 `Type instructions and press Enter`。
- [ ] 空输入或纯空白输入不提交。
- [ ] 提交前去除首尾空白，内部换行保持不变。
- [ ] `Esc` 返回根预设菜单并保留父菜单生命周期。

### 2.6 运行和结果展示

- [ ] `review.started` 后展示与目标对应的 review hint：
  `current changes`、`changes against '<branch>'`、
  `commit <short sha>: <title>` 或自定义指令。
- [ ] Review 期间不把隐藏的审查提示或原始 JSON 当作普通用户消息渲染。
- [ ] Review 结果先展示 `overall_explanation`。
- [ ] 单条 finding 使用 `Review comment:`，多条使用 `Full review comments:`。
- [ ] finding 标题行格式为 `- <title> — <path>:<start>-<end>`，正文每行缩进两个空格。
- [ ] 没有 finding 时不渲染空标题块。
- [ ] 中断、失败和取消使用各自正式终态，不把连接 EOF 当作完成。
- [ ] Review 期间的普通输入行为沿用 ProxyMind 当前 pending input/Turn gate 规则；若与 Codex
      的 review-mode 队列行为不同，先补契约测试并记录明确差异，不在 TUI 内创建第二套队列。

### 2.7 菜单视觉对齐设计

以下不是示意性的“类似布局”，而是 Review 菜单应遵守的终端表面规格。实际颜色通过终端语义
token 解析，不写死 ANSI 色值。

#### 列表共享表面

本表只适用于 preset、base branch 和 commit 三种选择列表。custom prompt 使用后文单独定义的
透明编辑表面。

| 元素              | Codex 基准                                       | ProxyMind 对齐方式                                               |
|-------------------|--------------------------------------------------|------------------------------------------------------------------|
| 容器              | 无边框，使用 user-message surface 背景           | 复用 `class:menu-card` / `user_surface`                          |
| 内边距            | 上下各 1 行，左右各 2 列                         | 复用 `SURFACE_VERTICAL_INSET=1`、水平 inset 2                    |
| 最大可见项        | 8 项                                             | 复用 `TuiMenu.VISIBLE_ROWS=8`                                    |
| 标题              | primary foreground + bold                        | 修正共享 `tui-menu.title` 映射，不能只依赖静态 fallback style    |
| 普通标签          | primary foreground                               | 复用 `tui-menu.label`                                            |
| 普通说明          | secondary/dim                                    | 复用 `tui-menu.detail`                                           |
| 选中项            | `›`，整行 accent + bold                          | 复用 selected token；marker、序号、标签和说明必须同一选中语义    |
| 搜索占位符        | secondary/dim                                    | 复用 `tui-menu.search.placeholder`                               |
| 无结果            | `no matches`，secondary/dim + italic             | 设置 `search_empty_text="no matches"`                            |
| 列表 footer       | 表面外透明背景，整行 secondary/dim               | 为 `MenuRequest` 增加具名 footer tone，Review 列表使用 secondary |
| 自定义输入 footer | 表面外透明背景；说明 primary、按键 secondary/dim | 复用现有 `STANDARD_MENU_FOOTER_HINT` 默认 tone                   |

这里有三项不能直接声称“现有能力已对齐”：

- [ ] 当前动态主题会覆盖 `tui-menu.title` 的 bold，需要修正共享语义样式并回归全部菜单快照。
- [ ] 当前列表 footer 的说明文字不是整体 dim，需要增加语义化 footer tone，不能写 Review 专用
      ANSI 样式。
- [ ] 当前无说明的长菜单标签只会截断，而 Codex 的 wrapped row 会按候选前缀续行；需要增加
      通用 `MenuRowDisplay.WRAPPED`，Review 列表显式使用该模式。

#### 根预设菜单线框

默认宽度下的文本结构如下；空行、序号、marker 和 footer 都属于验收内容：

```text
  Select a review preset

› 1. Review against a base branch  (PR Style)
  2. Review uncommitted changes
  3. Review a commit
  4. Custom review instructions

  Press enter to confirm or esc to go back
```

样式要求：

- `Select a review preset` 为 primary + bold。
- 初始选择固定为第一项；第一行的 marker、序号、label 和 `(PR Style)` 全部为 accent + bold。
- 未选中的 label 为 primary，未选中的 `(PR Style)` 为 secondary/dim。
- 非搜索菜单显示可执行序号 `1.` 至 `4.`，数字键按当前 List Keymap 的既有语义直接选择。
- footer 与 menu surface 之间保留一行视觉间隔，footer 不继承 surface 背景。

根菜单的目标请求参数：

| 字段                | 值                                        |
|---------------------|-------------------------------------------|
| `view_id`           | `review:preset`                           |
| `title`             | `Select a review preset`                  |
| `selected`          | `0`                                       |
| `searchable`        | `False`                                   |
| `row_display`       | `WRAPPED`                                 |
| `column_width_mode` | `AUTO_VISIBLE`，与 Codex 默认测量范围一致 |
| `footer_hint`       | `STANDARD_MENU_FOOTER_HINT`               |
| `footer_tone`       | `SECONDARY`                               |
| `selection_marker`  | `›`                                       |

#### 基础分支和提交菜单线框

可搜索菜单不显示数字序号，键入的数字属于查询文本：

```text
  Select a base branch

  Type to search branches
› feature/review -> main
  feature/review -> release/next

  Press enter to confirm or esc to go back
```

```text
  Select a commit to review

  Type to search commits
› Reject stale review terminal events
  Preserve review request identity during replay

  Press enter to confirm or esc to go back
```

两类菜单的共同请求参数：

| 字段                   | 值                                                  |
|------------------------|-----------------------------------------------------|
| `view_id`              | `review:base-branch` / `review:commit`              |
| `searchable`           | `True`                                              |
| `search_prompt_prefix` | 空字符串；左右缩进由 surface 提供，不显示 `Search:` |
| `search_placeholder`   | 使用第 2.3、2.4 节的精确文案                        |
| `search_empty_text`    | `no matches`                                        |
| `show_option_gutter`   | `True`，搜索模式只显示 `› `，不显示数字             |
| `row_display`          | `WRAPPED`                                           |
| `column_width_mode`    | `AUTO_VISIBLE`                                      |
| `footer_hint`          | `STANDARD_MENU_FOOTER_HINT`                         |
| `footer_tone`          | `SECONDARY`                                         |

长分支名和长 commit subject 在候选前缀之后续行，不改变后续项的索引；宽字符按终端 cell
宽度测量。过滤使用 `search_value.casefold()` 的包含匹配，过滤后选择定位到第一个可执行项，
滚动窗口始终包含当前项。

#### 自定义指令输入线框

Codex 的 `CustomPromptView` 是多行编辑器，不是单行搜索框。目标结构如下：

```text
▌ Custom review instructions
▌
▌ Type instructions and press Enter

Press enter to confirm or esc to go back
```

输入增加到多行后，每个可见编辑行保留 `▌ ` accent gutter；输入区高度按内容从 1 行增长，
最多显示 8 行，超出后滚动并保持光标可见。placeholder 为 secondary/dim，实际输入为 primary，
终端光标位于真实编辑位置。

custom prompt 不使用 `menu-card` / user-message surface 背景，也不使用列表的左右 2 列 inset；
标题、空 gutter 行和编辑区均从第 0 列开始，由 `▌ ` 自身建立 2 列内容缩进。目标请求参数为：

| 字段                       | 值                                       |
|----------------------------|------------------------------------------|
| `view_id`                  | `review:custom`                          |
| `surface_style`            | 空/透明语义 surface                      |
| `surface_horizontal_inset` | `0`                                      |
| `text_input_mode`          | `MULTILINE`                              |
| `text_input_max_rows`      | `8`                                      |
| `text_input_gutter`        | `▌`，accent                              |
| `search_placeholder`       | `Type instructions and press Enter`      |
| `show_option_gutter`       | `False`                                  |
| `footer_hint`              | `STANDARD_MENU_FOOTER_HINT`              |
| `footer_tone`              | 默认 primary description + secondary key |

现有 `MenuRequest.text_input` 只支持单行、会把粘贴内容清理为单行，因此不能直接复用。对齐时
扩展通用菜单输入契约，而不是新增 Review 专用编辑器：

- [ ] 用具名 `MenuTextInputMode.NONE/SINGLE_LINE/MULTILINE` 替代布尔 `text_input`，同一次改造
      迁移现有 transcript export 调用方，不保留双字段兼容路径。
- [ ] `MULTILINE` 使用 `text_input_max_rows=8`，规范化 CRLF/LF，但保留合法内部换行。
- [ ] 粘贴保留多行文本；控制字符仍在 TUI 输入边界清理。
- [ ] 复用 runtime editor keymap 的移动、按词移动、删除、行首/行尾和 `insert_newline` 动作。
- [ ] 裸 `Enter` 明确优先执行 accept；multiline 模式从 editor `insert_newline` 的有效绑定中
      排除裸 `Enter`，默认由 `Shift+Enter`、`Alt+Enter` 或 `Ctrl+J` 插入换行。
- [ ] 自定义 keymap 若把 submit 和有效 newline 绑定到同一 chord，配置校验必须拒绝歧义，不能
      依据 handler 注册顺序偶然决定行为。
- [ ] 空白文本执行 accept 时保持视图和光标不变，不产生错误提示或远端请求。
- [ ] 提交结果只在完成边界做一次 `strip()`，内部换行不变。
- [ ] ProxyMind 当前没有全局 Vim mode，因此首期按非 Vim Codex 行为对齐；若未来加入全局 Vim，
      第一次 `Esc` 的 insert-to-normal 转换必须由通用编辑器拥有，不能写进 Review feature。

### 2.8 菜单对象与职责设计

Review feature 只组装类型化目标和菜单请求，不拥有菜单栈、Git 子进程、HTTP 或 Turn 状态。

```text
TuiCommandDispatcher
  -> ReviewMenuController
      -> TuiRuntime.select_menu(root MenuRequest)
          -> navigation action
              -> WorkspaceReviewCatalogPort
              -> TuiRuntime.push_menu(child MenuRequest)
      -> ReviewTarget | cancelled
  -> WorkspaceReviewSnapshotPort.freeze(target)
  -> immutable Review command
```

对象职责：

| 对象                          | 拥有                                                  | 不拥有                    |
|-------------------------------|-------------------------------------------------------|---------------------------|
| `ReviewMenuController`        | 单次菜单 session、异步 catalog 请求关联、目标选择结果 | Git 命令、HTTP、持久 Turn |
| `WorkspaceReviewCatalogPort`  | 已校验的分支/提交查询契约                             | TUI view、选择状态        |
| `WorkspaceReviewSnapshotPort` | 目标到不可变 client snapshot 的转换                   | 菜单和远端生命周期        |
| `TuiMenu` / view stack        | focus、选择、query、cursor、父子完成传播              | Review 领域含义           |
| `TuiCommandDispatcher`        | 命令可用性、取消或提交分流                            | 原始 wire payload         |

根菜单选项映射必须精确为：

| 选项        | `value`                   | `on_select`                 | `dismiss_on_select` | `dismiss_parent_on_child_accept` |
|-------------|---------------------------|-----------------------------|---------------------|----------------------------------|
| base branch | 内部 navigation token     | 异步请求并压入 branch child | `False`             | `True`                           |
| uncommitted | `ReviewUncommittedTarget` | 无                          | `True`              | `False`                          |
| commit      | 内部 navigation token     | 异步请求并压入 commit child | `False`             | `True`                           |
| custom      | 内部 navigation token     | 压入 multiline prompt child | `False`             | `True`                           |

分支、提交查询不能阻塞 TUI，也不能由未归属任务更新已关闭菜单：

- [ ] catalog 操作通过 `runtime.start_background_task` 进入现有资源 owner。
- [ ] 每个操作捕获根菜单 `session_id` 和单调 `generation`；完成后同时校验 session 仍活动、
      `review:preset` 仍存在且 generation 当前。
- [ ] 根菜单在加载期间保持可见，不新增偏离 Codex 的 loading popup。
- [ ] 用户在加载期间关闭根菜单时，结果静默丢弃；不得重新打开菜单。
- [ ] 多次触发导航只允许最新 generation 压入子菜单，旧结果不得覆盖新选择。
- [ ] 查询失败压入可返回的 failure child；`Esc` 或 `Back` 以 cancelled 完成 child，从而清除
      父级 `dismiss_after_child_accept`，不能误关根菜单。

### 2.9 交互状态机

菜单交互只通过 view completion 产生一个 `ReviewTarget`，不得在选项回调内直接提交网络请求。

默认按键行为如下；运行时发生合法重映射时，footer 必须显示实际 primary binding，而不是写死
`enter` / `esc`：

| 上下文           | 动作      | 默认输入                                 | 规则                                        |
|------------------|-----------|------------------------------------------|---------------------------------------------|
| 非搜索列表       | 上下移动  | `Up/Down`、`Ctrl+P/N`、`Ctrl+K/J`、`k/j` | 到边界后循环，跳过禁用项                    |
| 非搜索列表       | 翻页/首尾 | `PgUp/PgDn`、`Ctrl+B/F`、`Home/End`      | 翻页和首尾不循环                            |
| 非搜索列表       | 数字直达  | `1` 至 `9`                               | 按可执行项序号选择并立即执行                |
| 搜索列表         | 输入过滤  | 所有可打印字符，包括数字和 `j/k`         | 不触发数字直达或字母导航                    |
| 搜索列表         | 编辑查询  | `Backspace`、`Ctrl+U`、`Ctrl+W`          | 删除字符、清空、删除前一词                  |
| 搜索列表         | 选择移动  | 方向键及不与查询输入冲突的 List binding  | 始终基于过滤后的实际索引                    |
| 所有列表         | 接受/返回 | `Enter` / `Esc`                          | 接受当前项；子视图取消只返回父级            |
| multiline prompt | 提交      | 裸 `Enter`                               | 仅非空时 accepted                           |
| multiline prompt | 换行      | `Shift+Enter`、`Alt+Enter`、`Ctrl+J`     | 插入 `\n` 并保持编辑状态                    |
| multiline prompt | 取消      | `Esc`                                    | 默认非 Vim 模式下返回父级                   |
| 所有菜单         | 中断      | `Ctrl+C`                                 | 作为当前菜单取消处理，不发送 Turn interrupt |

```text
COMPOSER
  | bare /review
  v
PRESET ---------------- Esc ----------------> COMPOSER(cancelled)
  | Enter uncommitted
  +-----------------------------------------> PREPARING(target)
  |
  | Enter base/commit            catalog ready
  +--> PRESET_LOADING ----------------------> CHILD_PICKER
  |       | Esc/root closed                    | Esc
  |       +-----------------> COMPOSER         +--> PRESET
  |                                             |
  |                                             | Enter target
  |                                             v
  |                                           PREPARING
  |
  | Enter custom
  v
CUSTOM_PROMPT -------- Esc -----------------> PRESET
  | empty Enter: stay
  | insert-newline action: edit in place
  | accept with non-empty text
  v
PREPARING -> SUBMITTING -> REVIEW_RUNNING -> AUTHORITY_TERMINAL
```

逐事件转换：

| 当前状态       | 输入/事件                 | guard                     | 动作                                            | 下一状态       |
|----------------|---------------------------|---------------------------|-------------------------------------------------|----------------|
| composer       | `/review`                 | 无活动 Turn               | 打开根菜单，选择索引 0                          | preset         |
| composer       | `/review <text>`          | trim 后非空               | 直接构造 custom target                          | preparing      |
| composer       | `/review`                 | 有活动 Turn               | 使用命令目录统一拒绝展示                        | composer       |
| preset         | move/page/number          | 目标可执行                | 更新 selection，保持 session                    | preset         |
| preset         | `Esc` / cancel            | allow_cancel              | cancelled 完成根 view、恢复 composer focus      | composer       |
| preset         | Enter uncommitted         | 当前项匹配                | accepted 完成根 view                            | preparing      |
| preset         | Enter base/commit         | 无同 generation loader    | 标记父级 child-accept 传播并启动 catalog        | preset-loading |
| preset-loading | catalog success           | session + generation 当前 | 压入 searchable child                           | child-picker   |
| preset-loading | catalog failure           | session + generation 当前 | 压入 failure child                              | child-failure  |
| preset-loading | root cancelled            | 无                        | 后台结果作废                                    | composer       |
| child-picker   | printable/paste/backspace | searchable                | 更新 query、过滤和可见 selection                | child-picker   |
| child-picker   | `Esc`                     | allow_cancel              | cancelled 弹出 child，清父级传播标记            | preset         |
| child-picker   | Enter                     | 有过滤后的可执行项        | accepted target；自动 accepted 父级             | preparing      |
| child-picker   | Enter                     | 无匹配项                  | 不完成、不提交                                  | child-picker   |
| custom-prompt  | editor action             | 合法编辑动作              | 更新文本、光标和滚动窗口                        | custom-prompt  |
| custom-prompt  | accept                    | `query.strip()` 为空      | 不完成、不提交                                  | custom-prompt  |
| custom-prompt  | accept                    | `query.strip()` 非空      | accepted custom target；自动 accepted 父级      | preparing      |
| custom-prompt  | `Esc`                     | 非 Vim 默认模式           | cancelled 弹出 child，清父级传播标记            | preset         |
| preparing      | snapshot success          | 限制和 digest 全部通过    | 冻结本地 Review command                         | submitting     |
| preparing      | snapshot failure          | 具名本地错误              | 展示错误并保留 composer 可用                    | composer       |
| review-running | 普通用户输入              | Review Turn 活动          | 进入现有本地 pending input，不调用 Review steer | review-running |
| review-running | interrupt                 | Turn 可中断               | 发送正式 interrupt 并等待权威终态               | review-running |
| review-running | EOF/retry                 | 未见 `turn.completed`     | attach/replay，保持执行门                       | review-running |
| review-running | review terminal           | item 终态合法             | 完成 Review Item，仍等待 Turn 终态              | review-running |
| review-running | `turn.completed`          | 坐标和序列合法            | 释放执行门并恢复 pending input                  | composer       |
| review-running | reconciliation            | effect 状态未知           | 暂停并进入显式对账                              | reconciliation |

焦点和完成传播不变量：

- [ ] 根菜单打开时 focus 从 composer 转到 menu；所有根 view 退出后才恢复 composer focus。
- [ ] 子 view 取消只弹出自己；父 view 的 query、selection 和 session identity 原样保留。
- [ ] 子 view accepted 时，同一个类型化 target 依次完成 child 和已标记父 view，只触发一次快照冻结。
- [ ] completion callback、后台 catalog 返回和按键事件竞争时，以 view stack 当前 identity 为准。
- [ ] 任意取消路径都不创建本地 Command、`request_id` 或远端 Turn。
- [ ] 提交后的菜单不得因 HTTP 失败自动重开；错误属于 Turn/command presentation，不属于旧菜单。

## 3. AppServer 协议就绪度

当前结论：**AppServer 源码中的 v1 协议已满足客户端主链实施前提。** 新会话首个 Review 和干净
工作区 custom 两个协议缺口已经关闭；目标部署环境是否已经上线该提交仍需 smoke test 验证。

| 能力                                                        | 当前状态   | 对齐判断                                                 |
|-------------------------------------------------------------|------------|----------------------------------------------------------|
| `POST /mind-review`、HTTP `202`                             | 已具备     | 可作为唯一 Review 创建入口                               |
| `uncommitted_changes` / `base_branch` / `commit` / `custom` | 已具备     | 与菜单目标可一一映射                                     |
| `inline` / `detached` delivery                              | 已具备     | 首期使用 `inline`，不模拟 detached                       |
| `client` / `server` / `reference` workspace                 | 已具备     | 本地 TUI 首期使用 `client` 快照                          |
| revision 与文件 SHA-256 校验                                | 已具备     | 客户端必须复现服务端规范摘要算法                         |
| 只读 execution 约束                                         | 已具备     | 强制 read-only、无附件、无 skills、非流式 HTTP           |
| `review.started/completed/failed/cancelled`                 | 已具备     | 纳入 Canonical Item reducer                              |
| `review.reconciliation_required`                            | 已具备     | 必须暂停并显式对账                                       |
| status / attach / replay / interrupt                        | 已具备     | 复用现有 Turn 生命周期                                   |
| 结构化 `ReviewOutput`                                       | 已具备     | 客户端严格解析后再生成展示投影                           |
| 幂等和 Turn/Session 冲突                                    | 已具备     | 复用稳定 `request_id` 和冻结请求                         |
| 新会话首个动作直接 `/review`                                | 已具备     | 原子创建源 Session、空 transcript 和目标 Review Turn     |
| 干净工作区上的 custom review                                | 已具备     | 允许空 patch/files，仍校验空内容的规范 revision          |
| 目标部署环境实际可用性                                      | **未验证** | 需要真实鉴权环境的 smoke test                            |

v1 收口证据：

- [x] `runtime_reserve_review` 在同一事务中确保源 Session 和空 transcript 存在，并继续完成
      inline Turn 或 detached Session + Turn 登记。
- [x] `/mind-review` 路由不再声明或映射 `review_source_missing`。
- [x] 请求模型只允许 `custom` 使用空 patch/files；另外三种 target 在请求边界拒绝空快照。
- [x] 空快照仍按规范 JSON 计算 revision，不以“无内容”为由绕过摘要校验。
- [x] 非空快照 finding 仍受快照路径集合约束；空快照 custom finding 仍须通过规范相对路径校验。
- [x] 协议、prompt 和持久执行上下文继续使用 `mind-review/1`，未引入无必要的 v2 分支。
- [x] OpenAPI 与相关契约测试已经同步。
- [x] 服务端提交验证记录：`984 passed, 63 skipped`，`compileall` 与 `git diff --check` 通过；
      skipped 项依赖外部环境，不作为本次单元验收失败。
- [x] 本次未保留一次性 smoke 脚本；目标部署验证应使用可复核的命令输出或 CI 记录，不要求把
      临时脚本写入长期源码。

服务端现有关键约束：

- 请求必须包含 `request_id`、`cid`、`sid`、`turn_id`、`target`、`delivery`、`workspace`、
  `execution`，未声明字段拒绝。
- 客户端 patch 最大 1,500,000 字符；最多 256 个文件；单文件最大 1,000,000 字符；
  快照正文总计最大 4,000,000 字节。
- `uncommitted_changes`、`base_branch` 和 `commit` 必须携带非空 patch 或 files；`custom` 可以
  携带空 patch/files，但必须提交空内容对应的正确 revision，并通过只读客户端工具取得所需代码。
- `workspace.revision` 必须是对规范 JSON `{patch, files}` 计算的 `sha256:<digest>`；规范 JSON
  使用 ASCII 转义、紧凑分隔符和键排序。
- Review execution 固定 `sandbox_mode=read-only`、`system_message=""`、
  `streaming=false`，禁止 hosted tools、skills 和 attachments；若携带客户端工具，则每个工具
  必须声明 `annotations.readOnlyHint=true`。
- 终态顺序为 `turn.started -> review.started -> review terminal -> turn.completed`；
  reconciliation 是暂停事实，不得伪装成失败后自动重试。

服务端基准文件：

- `D:\PycharmProjects\AppServer\schemas\mind.py`
- `D:\PycharmProjects\AppServer\schemas\mind_events.py`
- `D:\PycharmProjects\AppServer\routers\rt_turn_commands.py`
- `D:\PycharmProjects\AppServer\services\llm\PROTOCOL.md`

## 4. 分阶段实施

### 阶段 0：冻结决策和服务端门禁

- [x] 记录 Codex 菜单、交互、目标和结果格式基准。
- [x] 核对 AppServer 路由、请求、事件、终态和恢复契约。
- [x] 新会话首个 `/review` 由服务端登记事务原子创建源 Session 和空 transcript；客户端禁止发送空 `/mind-chat` 伪造源 Turn。
- [x] 干净工作区 custom 使用指令驱动语义，允许空 patch/files，仍提交规范 revision；其他 target 继续要求非空快照。
- [x] 首期只交付 `inline`，与 Codex 当前会话 Review 语义一致；TUI 不提供 `detached` 入口。
- [x] 确认目标 AppServer 部署版本包含上述契约并完成鉴权 smoke test。

阶段门槛：服务端协议缺口已关闭，客户端主链可以开始；delivery 产品范围须在 TUI 提交入口前
冻结，真实部署 smoke test 仍是发布门槛。

### 阶段 1：协议 schema 与 Review client

责任目录：`protocol/schema/`、`protocol/client/`。

- [x] 建立严格的 Review target、workspace、execution、receipt 和 output 类型，不复用松散字典。
- [x] 实现与服务端完全一致的路径校验、大小上限、文件摘要和 workspace revision 算法。
- [x] 请求联合严格表达“仅 custom 可为空”，并覆盖空 patch/files 的固定规范 revision。
- [x] 将五类 `review.*` 事件加入正式判别联合，并校验 `review_item_id == item_id`、状态与事件类型一致。
- [x] 建立 Review 提交用例：可靠 `POST /mind-review` 后只 attach/replay 已登记 Turn。
- [x] 对 HTTP 回执只认 `accepted` / `idempotent`，严格映射 403、404、409、503 错误。
- [x] Review 恢复复用 `/turn/status`、`/mind-attach`、`/mind-replay`；不新增同义端点。
- [x] 为请求、摘要、响应、事件、未知字段、边界大小和错误码添加契约测试。

阶段门槛：协议层可以在不导入 `agent`、`frontends`、`infrastructure` 的条件下独立通过测试。

### 阶段 2：Git 目录与不可变快照

责任目录：`infrastructure/platform/`，通过窄契约供上层使用。

- [x] 提供当前分支、本地分支、默认分支和最近 100 个提交的类型化查询结果。
- [x] 所有 Git 子进程关闭交互输入，设置超时和输出上限，并隔离 hooks、filter、pager、颜色及平台差异。
- [x] 未提交目标覆盖 staged、unstaged 和 untracked；二进制内容使用可传输 patch 表达。
- [x] 基础分支目标先解析与 HEAD 的 merge base，再冻结相对该 SHA 的 diff。
- [x] commit 目标冻结指定提交自身的 diff，并验证 SHA 与所选条目一致。
- [x] 生成 `source=client` 的不可变快照，在提交前完成限制检查和 revision 计算。
- [x] 非 custom 目标的无有效 diff、非 Git 目录、分支消失、提交消失、超限和编码失败均返回具名错误；custom 干净工作区生成合法空快照。
- [x] 使用临时 Git 仓库覆盖 staged/unstaged/untracked、detached HEAD、默认分支置顶、merge base、root commit、二进制文件和跨平台路径测试。

阶段门槛：相同输入生成稳定快照；失败不会产生部分请求或启动远端 Turn。

### 阶段 3：本地 Command、持久化与 Turn 生命周期

责任目录：`agent/protocol/`、`agent/application/`、`agent/harness/`、`agent/stores/`、
`agent/adapters/`。

- [x] 用具名 Review 请求进入本地 Command 链，不把 `/review` 伪装成普通 message。
- [x] 在首次网络操作前冻结 target、workspace、execution、request identity 和 environment。
- [x] 扩展 `ModelStreamRequest` 或建立职责更窄的正式请求联合；选型前验证 store、queue、恢复和 adapter 的完整影响面。
- [x] 持久化后才允许远端提交；相同本地 Run 恢复时复用原 `request_id`、`turn_id` 和快照。
- [x] 收到 accepted/idempotent 后只观察既有 Turn，未知提交结果进入现有恢复流程。
- [x] Review 的 interrupt、终态和执行门释放遵循普通 Turn 权威规则。
- [x] Review 不携带普通 prompt attachments、skills 或可写工具；已有待输入保持原 owner。
- [x] 审计客户端工具 annotations，为空快照 custom 建立最小只读代码检索 allowlist；若当前环境没有足够的只读工具，则在提交前要求文件快照或返回具名能力错误，不能发送无代码上下文的空 Review。
- [x] 覆盖 store round-trip、崩溃点、重复提交、重启 attach、冲突和终态唯一性测试。

阶段门槛：故障注入证明“网络前有本地事实、确认后不重复创建、EOF 不释放执行门”。

阶段 3 复核证据：`tests/agent` 754 项通过；架构边界 138 项通过；
Review 定向用例覆盖网络前持久化、重启恢复、未知提交、终态冲突、EOF 和 interrupt。
当前客户端没有同时满足正式 `readOnlyHint` 和代码检索语义的工具，因此不建立伪
allowlist；空快照 custom 在 HTTP 前稳定返回 `review_code_context_unavailable`。

### 阶段 4：TUI 命令、菜单和交互

责任目录：`frontends/tui/prompting/`、`frontends/tui/features/`、`frontends/tui/session/`。

- [x] 按第 2.1 至 2.5 节精确加入命令目录和四项预设菜单。
- [x] 按第 2.7 节实现共享表面、语义样式、线框和窄终端布局。
- [x] 补齐 `MenuRowDisplay`、footer tone 和 multiline text input 三项通用菜单能力；不得用 Review 私有 renderer 绕过现有 view stack。
- [x] 按第 2.8 节组装 `ReviewMenuController` 与 catalog/snapshot 窄契约。
- [x] 按第 2.9 节实现父子完成传播、异步 generation guard 和全部取消路径。
- [x] 分支、提交查询异步执行时保持输入响应；失败 child 可返回稳定父视图。
- [x] 裸命令、行内 custom、Enter、Esc、搜索、空输入和活动 Turn 拒绝均有行为测试。
- [x] 提交前显示的目标摘要与最终 `review.started` hint 使用同一类型化来源。
- [x] 对 40/80/120 列及长分支名、长提交 subject、宽字符进行渲染快照验证。
- [x] 对 root、branch、commit、custom、no matches、catalog failure 分别建立 golden snapshot。
- [x] 样式测试检查语义 token，而不只比较去除 ANSI 后的纯文本。

阶段门槛：第 2.7 至 2.9 节的视觉与状态转换契约全部通过，TUI 层不包含 HTTP 字段拼装或
Git 命令字符串。

阶段 4 复核证据：`tests/frontends/tui` 1826 项通过、1 项按环境跳过；Review 持久执行相关
13 项通过；架构边界 138 项通过。共享菜单以持久 `scroll_top` 对齐 Codex
`ScrollState.ensure_visible`：前 8 项保持起始窗口，第 9 项才推动滚动；选择 marker 使用独立
`tui-menu.selection-marker` 语义并断言 `bold + nodim`。commit picker 的标题后空行、搜索行、
8 项可见窗口和 footer 间隔均有精确文本测试。

### 阶段 5：事件归约与结果呈现

责任目录：`protocol/schema/stream_events.py`、Canonical Item reducer、application presentation、
TUI renderer。

- [ ] 将 `item_kind=review` 纳入 Item 投影，使用稳定 `item_id + event_seq` 归约。
- [ ] `review.started` 建立运行态和 hint；replay 不重复展示启动横幅。
- [ ] `review.completed.output` 先严格解析为 `ReviewOutput`，再生成纯展示值。
- [ ] 按第 2.6 节实现 explanation 和 findings 文本格式，不从 JSON 文本反向猜测。
- [ ] `review.failed`、`review.cancelled` 和 `review.reconciliation_required` 进入独立投影。
- [ ] 只有 `turn.completed` 释放生命周期；Review Item 完成不等于 Turn 完成。
- [ ] active/audit revision、replay 去重、迟到事件和 terminal 冲突沿用现有 reducer 规则。
- [ ] 覆盖无 finding、单 finding、多 finding、多行 body、路径/行号、失败、取消、reconciliation、replay 和断线恢复测试。

阶段门槛：最终 assistant 正文只从 active Review Item 派生，且重放不会产生重复可见内容。

### 阶段 6：端到端联调与发布收口

- [ ] AppServer 定向运行 Review route、schema、submission、event projection、worker resume 测试。
- [ ] ProxyMind 使用 fake transport 覆盖 submit -> attach -> completed 全链。
- [ ] 使用真实临时 Git 仓库和本地 TUI 场景覆盖四种目标。
- [ ] 在已存在 Session 和全新 Session 中分别执行 `/review` smoke test。
- [ ] 在 `review.started` 后断开并重连，验证 status/replay 水位与唯一终态。
- [ ] 在 Review 运行中执行 interrupt，验证 `review.cancelled` 与 `turn.completed` 顺序。
- [ ] 校验 403、`request_id_conflict`、`turn_already_active`、`turn_id_reused`、503 和快照超限的用户可见错误；新 Session 路径必须成功，不再期待 `review_source_missing`。
- [ ] 同步受影响的稳定协议文档和契约测试，不把本计划中的临时决策复制进架构权威。

最终验证命令：

```shell
python -m pytest <review-targets> -q
python -m pytest tests/test_package_architecture.py tests/architecture -q
python -m compileall agent protocol frontends infrastructure observability metadata
git diff --check
```

发布门槛：所有完成定义打勾，阶段 0 的服务端缺口关闭，真实环境 smoke test 留有可复核证据。

## 5. 明确不采用的路径

- [ ] 不把 `/review` 翻译成普通 `/mind-chat` 文本提示。
- [ ] 不允许 AppServer 根据客户端绝对路径读取本地工作区。
- [ ] 不在 TUI 中解析服务端原始 JSON 或维护第二套 Review 状态机。
- [ ] 不因提交结果未知而生成新 `request_id` 或重复创建 Turn。
- [ ] 不用串行 `/fork` 加 `/mind-chat` 模拟 `detached` Review。
- [ ] 不为旧的未声明字段、端点或载荷增加兼容别名和静默回退。
- [ ] 不在本次对齐中顺手重构无关的菜单、Git、Turn 或展示代码。

## 6. 变更面预估

| 边界                        | 预期变更                                            | 核心验证                       |
|-----------------------------|-----------------------------------------------------|--------------------------------|
| `protocol/schema/`          | Review 请求、响应、输出和事件类型                   | 严格解析、摘要、限制、未知字段 |
| `protocol/client/`          | submit + attach/replay Review 用例                  | 幂等、错误分类、恢复水位       |
| `infrastructure/platform/`  | Git catalog、target diff、snapshot                  | 跨平台临时仓库测试             |
| `agent/protocol/` 与 stores | 冻结 Review Command/请求事实                        | round-trip、重启、故障注入     |
| reducer 与 presentation     | Review Item 和结果投影                              | replay、revision、唯一正文     |
| `frontends/tui/`            | 命令、嵌套菜单、运行态和渲染                        | 键盘交互、窄终端、快照         |
| AppServer                   | v1 协议缺口已完成；仅在契约测试暴露新缺口时继续变更 | route/schema/worker/持久化测试 |
