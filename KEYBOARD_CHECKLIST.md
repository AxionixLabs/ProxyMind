# Codex 键盘交互对齐清单

## 范围与基线

本清单只评估 TUI 键盘输入、路由优先级、展示提示和按键配置，不改线上协议、Durable Turn
或服务端状态机。对照基线为仓库内 `codex-main` 当前源码快照，重点依据：

- `codex-main/codex-rs/tui/src/keymap.rs`
- `codex-main/codex-rs/config/src/tui_keymap.rs`
- `codex-main/codex-rs/tui/src/app/input.rs`
- `codex-main/codex-rs/tui/src/chatwidget/interaction.rs`
- `codex-main/codex-rs/tui/src/bottom_pane/chat_composer.rs`
- `codex-main/codex-rs/tui/src/bottom_pane/chat_composer/slash_input.rs`
- `codex-main/codex-rs/tui/src/bottom_pane/chat_composer/history_search.rs`
- `codex-main/codex-rs/tui/src/bottom_pane/approval_overlay.rs`

Mind 当前实现主要依据：

- `frontends/tui/core/keymap.py`
- `frontends/tui/core/input.py`
- `frontends/tui/core/submission.py`
- `frontends/tui/core/screen.py`
- `frontends/tui/core/menu.py`
- `frontends/tui/core/approval.py`
- `frontends/tui/rendering/screen/surfaces.py`

状态说明：

- `[x]`：现有行为已经与 Codex 的核心语义一致。
- `[ ]`：可以对齐，尚未实施或尚缺完整验证。
- `保留差异`：已经确认的 Mind 产品约束，不应在本轮悄悄修改。
- `暂不适用`：依赖 Mind 当前不存在的产品能力，不属于单纯键位改造。

## 结论

Mind 的核心 Turn 输入已经对齐了 Enter、Tab、Esc、队尾取回、Ctrl+O 和 Ctrl+T 等高风险
路径，但键位架构尚未对齐。Codex 使用一个不可变、按上下文解析的 Runtime Keymap，同时驱动
事件分派、冲突检查和界面提示；Mind 目前只有 `global.open_transcript` 与 `pager` 进入统一
keymap，其余键位仍分散在 Input、Screen、Menu 和 Approval 中。

最优实施方向不是逐个增加 `@bindings.add(...)`，而是先统一动作身份和上下文，再在该架构上
迁移现有行为并补齐差异。共享能力的对齐均可由客户端完成，不需要服务端新增接口。只有 Agents、
Side Conversation、图片粘贴等 Mind 尚不存在的产品能力，未来若决定引入才需要另行评估协议。

## A. 路由不变量

- [x] 活动补全菜单优先消费 Esc、方向键、Tab 和 Enter，不把这些键泄漏给 Turn 中断或提交。
- [x] 活动模态表面优先于主输入框消费按键，审批、菜单、记录页不会改写底层草稿。
- [x] 非空草稿上的第一次 Ctrl+C 只清空草稿，不同时中断 Turn。
- [x] 活动 Turn 的 Esc 是专用中断动作；有 pending steer 时只由 Esc 设置终态后立即提交意图。
- [x] Ctrl+C 中断不设置 Esc 专属自动提交意图；普通 pending/queued 输入在中断后恢复编辑框。
- [x] 空闲空草稿的双 Esc 进入历史回溯；任意非 Esc 编辑动作取消已预备的回溯状态。
- [x] 活动 Turn 中 Enter 尝试 steer，Tab 排到下一 Turn；空闲普通文本的 Enter/Tab 都提交。
- [x] Slash、Shell 和普通文本在 Tab 排队时保留类型，出队后再解析，不在入队时执行。
- [x] 把“当前活动上下文集合”建成显式值，统一表达 global/chat/composer/editor/list/approval/pager 的重叠关系，禁止依赖 Prompt Toolkit 的注册先后顺序隐式决定所有权。
- [x] 为固定安全键建立不可覆盖表：Ctrl+C、Ctrl+D、Bracketed Paste、历史回溯 Esc，以及平台保留键。
- [ ] 对所有按键只处理 press/repeat，忽略 release；通过 Windows Terminal、ConPTY、POSIX PTY 和 macOS 终端验证修饰键不会在 key-up 时重复输入。

## B. 统一 Keymap 架构

- [x] 扩展 `TuiRuntimeKeymap`，至少包含 `global`、`chat`、`composer`、`editor`、`pager`、
  `list` 和 `approval` 七个上下文；暂不为不存在的功能建立空动作。
- [x] 为每个动作定义稳定身份，例如 `global.copy`、`chat.interrupt_turn`、
  `composer.queue`、`editor.insert_newline`、`list.accept`。
- [x] Runtime Keymap 是启动时解析完成的不可变快照；Input、Screen、Menu、Approval 只消费该快照，
  不再各自声明同一按键事实。
- [x] 解析优先级固定为“上下文配置 -> 合法的 global fallback -> 内置默认值”；空数组表示显式解绑。
- [x] 支持 Codex 已使用的单键、组合修饰键、F1-F24 和多键 chord；chord 等待窗口为 1 秒，
  Esc 必须取消未完成 chord。
- [x] 拒绝相同上下文内的重复绑定、跨重叠上下文的遮蔽、固定安全键覆盖、AltGr 文本键占用，
  以及一个 chord 是另一个 chord 前缀的歧义。
- [x] Plain printable key 只有在不会吞掉文本输入的上下文中才允许配置；搜索型菜单输入期间不得用
  `j/k` 抢占查询文本。
- [x] 所有 footer、帮助、排队提示和审批选项的快捷键标签均由同一个 Runtime Keymap 生成，
  禁止显示文案与实际绑定分别维护。
- [x] 配置 schema、默认配置、Profile 合并、冲突错误和显式解绑测试覆盖全部已支持上下文。
- [x] 先把现有行为原样迁入统一 Keymap，再改变默认键；架构迁移提交不得夹带产品语义变化。

## C. 主界面与 Turn 控制

| 动作                  | Codex 默认键          | Mind 当前状态                    | 对齐决定                                  |
|-----------------------|-----------------------|----------------------------------|-------------------------------------------|
| 打开完整记录          | Ctrl+T                | 已一致且可配置                   | `[x]` 保持                                |
| 复制最近回复          | Ctrl+O                | 行为已一致                       | `[x]` 纳入 `global.copy_last_response`    |
| 清空可见终端          | Ctrl+L                | 行为已一致                       | `[x]` 纳入 `global.clear_terminal`        |
| 中断活动 Turn         | Esc                   | 已一致                           | `[x]` 纳入 `chat.interrupt_turn`          |
| 编辑最近 queued 输入  | Alt+Up / Shift+Left   | 已一致，按终端选择提示           | `[x]` 纳入 `chat.edit_queued_message`     |
| 降低 reasoning effort | Alt+, / Shift+Down    | 仅有 `/effort` 菜单              | `[ ]` 客户端直接切换并显示结果            |
| 提高 reasoning effort | Alt+. / Shift+Up      | 仅有 `/effort` 菜单              | `[ ]` 客户端直接切换并显示结果            |
| 外部编辑器            | Ctrl+G                | Mind 无对应能力                  | `暂不适用`                                |
| 原始输出模式          | Alt+R                 | Mind 只在记录页内用 R 切换       | `[ ]` 先定义主界面 raw 语义再决定是否对齐 |
| Agents 总览           | Alt+A                 | Mind 无 Codex daemon Agents 总览 | `暂不适用`                                |
| Side Conversation     | Ctrl+/（兼容 Ctrl+7） | Mind 无对应会话模型              | `暂不适用`                                |
| 图片粘贴              | Ctrl+V / Alt+V        | Mind 无对应附件能力              | `暂不适用`                                |

### Ctrl+C / Ctrl+D

- [x] Ctrl+C 的局部优先级与 Codex 核心路径一致：活动模态 -> 历史/草稿取消 -> 活动任务中断 -> 退出。
- [x] Ctrl+D 只在空草稿且允许退出时结束前台；非空草稿中仍保留向前删除语义。
- `保留差异`：仓库内 Codex 当前关闭双击退出，空闲 Ctrl+C 会直接退出；Mind 已明确采用 2 秒内
  连续两次 Ctrl+C 退出，第一次显示确认，同时在活动 Turn 中登记中断。除非重新批准产品变更，
  本轮不得改回 Codex 的单击退出。
- [ ] 为上述保留差异增加配置/帮助中的明确说明，并确保任何普通编辑键、提交键或超时都会撤销
  Ctrl+C 退出预备状态。
- [ ] 真机覆盖：非空草稿、Thinking、正文流、Tool、Approval、Retry、断线恢复和终态等待期间的
  Ctrl+C/Ctrl+D 优先级，不允许一次按键触发两个动作。

## D. Composer 与文本编辑

| 动作           | Codex 默认键                                                | Mind 当前状态                              | 对齐决定                                |
|----------------|-------------------------------------------------------------|--------------------------------------------|-----------------------------------------|
| 提交           | Enter                                                       | 已一致                                     | `[x]` 纳入 `composer.submit`            |
| 活动 Turn 排队 | Tab                                                         | 已一致                                     | `[x]` 纳入 `composer.queue`             |
| 插入换行       | Ctrl+J、Ctrl+M、Shift+Enter、Alt+Enter                      | 增强协议及旧式降级路径均已接入             | `[x]` 补齐并真机验证 Ctrl+M/Shift+Enter |
| 快捷键面板     | `?`（仅空草稿）                                             | 已实现，非空草稿仍输入 `?`                 | `[x]` 增加只读快捷键面板                |
| 反向历史搜索   | Ctrl+R                                                      | 已实现 footer-owned 搜索状态               | `[x]` 冻结并可靠恢复完整草稿            |
| 历史搜索向前   | Ctrl+S                                                      | 已实现                                     | `[x]` 与 Ctrl+R 同阶段完成              |
| 普通历史导航   | Up/Down、Ctrl+P/Ctrl+N                                      | Up/Down 已一致；Ctrl+P/N 主要用于候选      | `[ ]` 明确无候选时的编辑/历史语义       |
| 行首/行尾      | Home/Ctrl+A、End/Ctrl+E                                     | 已显式实现，含 Ctrl+A/E 边界跨行           | `[x]` 纳入显式 editor 契约              |
| 字符移动       | Left/Ctrl+B、Right/Ctrl+F                                   | 已显式实现                                 | `[x]` 纳入显式 editor 契约              |
| 单词移动       | Alt+B/F、Alt/Ctrl+Left/Right                                | 依赖 Toolkit 和终端编码                    | `[ ]` 统一终端适配并加 PTY 测试         |
| 向后删词       | Alt+Backspace、Ctrl+Backspace、Ctrl+Shift+Backspace、Ctrl+W | 仅 Ctrl+W 显式                             | `[ ]` 补齐 Codex 兼容别名               |
| 向前删词       | Alt+Delete、Ctrl+Delete、Ctrl+Shift+Delete、Alt+D           | 未形成 Mind 显式契约                       | `[ ]` 补齐 Codex 兼容别名               |
| 删至行首       | Ctrl+U                                                      | 已对齐，行首时继续删除前一换行             | `[x]` 保留文本及折叠粘贴 kill/yank 事实 |
| 删至行尾       | Ctrl+K                                                      | 已对齐，行尾时继续删除后一换行             | `[x]` 纳入显式 editor 契约              |
| 粘回 kill 内容 | Ctrl+Y                                                      | 已对齐，处理折叠粘贴占位冲突               | `[x]` 纳入显式 editor 契约              |
| Undo / Suspend | Ctrl+Z                                                      | Mind 全平台 Undo；Codex 在 Unix 保留给挂起 | `[ ]` 需要产品决策后再改                |

历史搜索必须保持 Codex 的数据所有权：打开搜索时冻结完整草稿；查询文字属于 footer，匹配项只作
预览；Enter 接受匹配但不提交；Esc/Ctrl+C 恢复原草稿；无匹配也不得丢失原草稿。

## E. 补全、Slash 与 Shell

- [x] Slash、文件和 Skill 候选打开时，Up/Down 与 Ctrl+P/Ctrl+N 选择候选。
- [x] Esc 只关闭候选并保留草稿，不中断活动 Turn。
- [x] Tab 接受候选；完整 Slash 命令在活动 Turn 中可形成 queued `ParseSlash`，不会被补全层吞掉。
- [x] Enter 对需要参数的 Slash/Skill/File 先应用候选，不意外提交不完整输入。
- [x] `!` 只在空草稿进入 Shell 模式；Shell 模式空输入上的 Esc/Backspace 返回普通输入。
- [x] Bracketed Paste 作为一个输入事实处理，多行粘贴不会被 Enter/Tab 快捷键拆开。
- [ ] 把 popup 优先级写成统一 dispatcher 的契约测试：popup > composer action > editor action。
- [ ] 补齐 Shift+Tab 在不同上下文中的确定语义。Mind 当前用于反向选择候选；Codex 在启用 collaboration mode 时可在空闲主界面切换模式，因产品能力不同，不应直接覆盖 Mind 行为。

## F. 菜单与列表

| 动作      | Codex 默认键            | Mind 当前状态 | 对齐决定                      |
|-----------|-------------------------|---------------|-------------------------------|
| 上移      | Up、Ctrl+P、Ctrl+K、k   | 已对齐        | `[x]` 非搜索输入时补 Ctrl+K/k |
| 下移      | Down、Ctrl+N、Ctrl+J、j | 已对齐        | `[x]` 非搜索输入时补 Ctrl+J/j |
| 左移      | Left、Ctrl+H            | 已对齐        | `[x]` 补 Ctrl+H               |
| 右移      | Right、Ctrl+L           | 已对齐        | `[x]` 补 Ctrl+L               |
| 上翻页    | PageUp、Ctrl+B          | 已对齐        | `[x]` 补 Ctrl+B               |
| 下翻页    | PageDown、Ctrl+F        | 已对齐        | `[x]` 补 Ctrl+F               |
| 首项/末项 | Home/End                | 已一致        | `[x]` 保持                    |
| 接受/取消 | Enter/Esc               | 已一致        | `[x]` 保持                    |

- [x] List 动作统一供普通菜单、模型、权限、Provider、Skills、Copy Picker、Hooks 和其他选择器复用。
- [x] 搜索型菜单中的 printable `j/k` 必须进入查询文本；只有非搜索菜单或明确导航模式才能消费。
- [x] 数字直达、Space toggle、左右 Tab 是 Mind 扩展，保留但也必须进入具名动作和冲突检查。
- [x] Ctrl+C 继续作为局部取消入口，不得因可配置 list.cancel 而绕过审批/菜单清理生命周期。

## G. Approval

审批键不仅是显示差异，`decline` 与 `cancel` 会导致不同 Turn 生命周期，必须以结构化 decision
映射验证，不能只改字母。

| 动作                | Codex 默认键              | Mind 当前状态                    | 对齐决定                          |
|---------------------|---------------------------|----------------------------------|-----------------------------------|
| 当前选项确认        | Enter                     | 已一致                           | `[x]` 保持                        |
| 选择上/下           | Up/Ctrl+P、Down/Ctrl+N    | 已一致                           | `[x]` 保持                        |
| 展开详情            | Ctrl+A、Ctrl+Shift+A      | 已对齐                           | `[x]` 改为 Codex 修饰键集合       |
| 单次允许            | y                         | 已一致                           | `[x]` 保持                        |
| Session 允许        | a                         | 已对齐 a，未保留 s 别名          | `[x]` 对齐                        |
| 前缀/规则允许       | p                         | 已一致                           | `[x]` 保持                        |
| 拒绝但继续 Turn     | d                         | 已按审批类型映射 decline         | `[x]` 对齐 d -> decline           |
| 取消请求/中止当前链 | Esc/n/c（依审批类型裁决） | 已按审批类型及可用 decision 裁决 | `[x]` 对齐 Codex 的 decision 语义 |
| 严格自动审查        | r                         | Mind 已支持对应权限决定          | `[x]` 保持                        |
| 打开来源线程        | o                         | Mind 无同构多线程审批来源        | `暂不适用`                        |

- [x] Esc、n、d、c、Ctrl+C 必须按审批类型映射正式协议 decision，并由选项可用性决定是否生效。
- [x] Exec、Permissions、Patch、Network、MCP elicitation 分别建立按键矩阵；尤其锁定“decline 后继续”与“cancel 后中断”的差异。
- [x] 审批 footer 从 Runtime Keymap 和当前可用 decision 派生，不显示无效快捷键。
- [ ] 所有审批按键使用真实 PTY 验证：按键只提交一次 decision，底层草稿不变，终态前不启动下一轮。

## H. Transcript / Pager

- [x] Up/k、Down/j、PageUp/Ctrl+B、PageDown/Space/Ctrl+F、Ctrl+U/D、Home/End 与 Codex 一致。
- [x] q/Ctrl+C 关闭页面，Ctrl+T 关闭完整记录；Mind 额外支持搜索、raw 和导出。
- [x] 完整记录中的 Esc/Left/Right/Enter 负责历史回溯，优先于普通 pager 取消。
- [x] 增加 Shift+Space 作为 PageUp 的 Codex 兼容键。
- [ ] Codex 的 raw 输出是全局 Alt+R；Mind 的记录页 R、搜索 `/ n N`、导出 e 是产品扩展，在未定义全局 raw 生命周期前继续保留现状。
- [x] 所有 pager 帮助文字继续从解析后的按键生成，覆盖重绑定和显式解绑。

## I. 暂不直接照搬的 Codex 能力

- `Vim mode`：Mind 当前以 Prompt Toolkit/Emacs 风格为主。若新增 Vim，需独立的 normal、operator、
  text-object 状态机和完整编辑测试，不能只增加 `hjkl`。
- `/keymap` 可视化编辑器：可在统一 Runtime Keymap 稳定后实现；首阶段只需可靠配置解析，避免
  同时引入在线编辑、持久化和 chord 捕获三类风险。
- `Alt+A Agents`、`Ctrl+/ Side Conversation`、`Shift+Tab Collaboration Mode`：依赖不同产品模型。
- `Ctrl+G External Editor`、`Ctrl+V/Alt+V Image Paste`：需要新能力与资源生命周期，不属于键位层。
- Unix `Ctrl+Z` Job Control：需要终端离开/恢复和渲染重对齐，不能用删除 Undo 绑定代替完整实现。

## J. 分阶段实施与准出

### 阶段 1：统一 Keymap，不改变行为

- [x] 建立七个现有上下文、动作身份、解析器和冲突验证。
- [x] 把 Input、Screen、Menu、Approval 的既有绑定迁入 Runtime Keymap。
- [x] 所有现有单元测试和真实 PTY 测试保持通过，默认键行为逐字节不变。
- [x] 复核通过后形成独立提交。

### 阶段 2：高价值共享语义

- [x] 完成 Approval decision 键位对齐。
- [x] 完成 Ctrl+R/Ctrl+S 历史搜索及草稿恢复。
- [x] 完成 `?` 快捷键面板，并从 Runtime Keymap 生成内容。
- [x] 完成旧式输入解码器可表达的 editor/list/pager 兼容别名；增强修饰键转入阶段 4。
- [x] 每一类行为分别复核并提交，不与无关布局重构混合（`9a519f36`）。

阶段 2 当前已完成 Prompt Toolkit 现有解码器可无歧义表达的 editor/list 别名。以下输入在旧式
终端字节流中与其他键相同，或不被 Prompt Toolkit 表达，必须留到阶段 4 的增强终端输入适配，
不得伪装成已支持：Ctrl+M/Enter、Ctrl+H/Backspace、Shift+Space/Space、Shift+Enter、
Ctrl+Shift+A、Ctrl+Shift+Backspace 和 Ctrl+Shift+Delete。Ctrl+R/Ctrl+S、Esc/Ctrl+C 取消、
Enter 只接受不提交、无匹配恢复、kill/yank 折叠粘贴恢复均已有真实输入路径测试。

### 当前验证记录（阶段 2）

- 受影响单元、交互、审批与状态测试：1157 passed；一次既有 scrollback 1 秒等待超时，单独复跑通过。
- TUI 全套：1741 passed；一次既有 Ctrl+C expiry 真机等待超时，单独复跑 3.57 秒通过。
- 全仓：4090 passed、14 skipped；Hooks 的字符串键事件规范化缺口已修复，Skill 补全的负载超时单独复跑通过。
- 真实 PTY 定向：29 passed；架构边界：129 passed（1 个第三方 Nuitka 弃用警告）。
- `compileall` 与 `git diff --check` 通过；根目录本清单保持未跟踪，不进入提交。

### 阶段 3：配置体验

- [x] 除固定安全生命周期入口外，全部现有动作支持在 `config.toml` / Profile 中重绑定或显式解绑。
- [x] 支持 1 秒 chord、前缀冲突检测、AltGr、旧终端别名和平台保留键诊断。
- [x] 只读诊断复用现有 `?` Keyboard shortcuts 页面；不新增重复的 `/keymap`。交互编辑和运行时热更新不纳入本阶段，Runtime Keymap 继续保持启动时冻结，配置在下次启动原子生效。
- [x] 显示提示、实际路由和持久配置由同一 Runtime Keymap 快照生成。

### 当前验证记录（阶段 3）

- 配置、chord、菜单、队列、审批定向：294 passed；相关组合复核：609 passed。
- 完整 TUI：1758 passed。
- 全仓：4087 passed、14 skipped；仅有 1 个第三方 Nuitka 弃用警告。
- 架构边界：120 passed；`compileall` 与 `git diff --check` 通过。
- 阶段 4 已用增强按键事件打开 Ctrl+M/I/H/[/@、Shift 修饰键和 chord 第二键 Alt；旧式终端
  继续只使用可区分的传输表示，不把普通 Enter/Tab/Backspace 误判为其 Ctrl 别名。
- 根目录本清单保持未跟踪，不进入提交。

### 阶段 4：真机验收

- [x] Windows Terminal + ConPTY。
- [ ] Linux PTY，包含 Ctrl+Z、Alt 组合键和 Ctrl+S 流控风险。
- [ ] macOS Terminal/iTerm2，包含 Option 键、Shift+Enter 和 Alt+Enter。
- [ ] SSH、tmux、WSL，验证 Esc 前缀、Alt+Up、Ctrl+M 和 chord 超时。
- [ ] 主输入、补全、搜索、菜单、审批、记录页、Thinking、Tool、Retry、断线恢复各运行一轮按键矩阵。
- [ ] 任一场景均满足：一次按键最多一个动作、模态不泄漏、草稿不丢、queued FIFO 不变、terminal 前不开放下一 Turn、提示文案与真实绑定一致。

### 当前验证记录（阶段 4A）

- Terminal adapter 已对齐 Codex 的 flags 7、iTerm2/Ghostty/tmux xterm flags 5、tmux csi-u
  modifyOtherKeys 2、Press/Repeat/Release 和 WSL + VS Code 禁用策略。
- 增强事件矩阵覆盖换行、字符删除、前后删词、列表左移、审批详情与 Pager Shift+Space；
  Bracketed Paste 内部的 CSI-u 保持文本，单独 Esc 在 flush 后仍按原动作交付。
- Windows Terminal + 真实 ConPTY 已验证 Ctrl+M、Shift+Enter、模式启用和退出恢复；Linux、
  macOS、SSH、tmux 与 WSL 的真机项仍保持未勾选，不用模拟测试冒充真机结论。
- 完整 TUI：1704 passed；真实 PTY：88 passed、2 skipped；架构边界随全仓复核通过。
- 全仓：4112 passed、14 skipped；基础 PTY drain 与既有 scrollback 等待各出现一次负载超时，
  分别独立复跑通过。`compileall` 与 `git diff --check` 通过。

## K. 建议执行顺序

1. 先做阶段 1，解决“键位事实分散”这一架构问题。
2. 再做 Approval 与历史搜索；这两项分别影响 Turn 生命周期和草稿所有权，风险最高。
3. 然后补 editor/list/pager 别名和快捷键面板。
4. 最后再决定 `/keymap` 在线编辑、Vim、External Editor 等扩展能力。

在阶段 1 完成前，不建议继续以单个 `@bindings.add(...)` 的方式追平 Codex；这会继续扩大实际
路由、冲突校验和 footer 文案之间的差异。
