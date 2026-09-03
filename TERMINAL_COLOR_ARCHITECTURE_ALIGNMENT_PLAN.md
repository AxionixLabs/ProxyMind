# 终端与颜色检测架构对齐计划

> 版本：V1；制定日期：2026-09-03；架构权威：`ARCHITECTURE.md`；Codex 参考基线：
> `codex-main/codex-rs` revision `608f4a8a98feff0889cbfc9ed691efbf42d34cc6`；实施范围：
> Windows、macOS、Linux，真实终端验收由对应平台完成。

## 1. 目标

本计划一次性治理 ProxyMind 的终端识别、颜色能力检测、默认前后景探测、语义样式、
富色表面和跨平台降级，不再通过单个组件内追加 RGB、ANSI 色或终端名称分支修补显示。

完成后应满足：

- 终端身份只描述事实，不直接代表颜色、滚动、键盘或动态表面能力。
- 每个 TUI 会话只生成一个不可变终端能力快照；渲染期间不读取环境、不访问操作系统、
  不重新探测终端。
- 普通交互只消费语义样式；审批卡、选择项、输入框、状态、正文和菜单不得各自维护强调色。
- TrueColor、ANSI 256、ANSI 16、禁用颜色和未知背景均有明确降级结果。
- PyCharm/JediTerm 是正式验收目标，但不通过固定 RGB 或“识别到 JetBrains 就强制真彩”实现。
- diff、patch 和 syntax theme 作为富渲染例外独立管理，不污染普通 TUI 语义调色板。
- 新增颜色只能进入批准的调色板或富渲染边界，并由定向契约测试约束。

## 2. 范围与非目标

### 2.1 本轮范围

- 终端与 multiplexer 身份检测及信号来源。
- stdout 是否为 TTY、显式颜色开关、有效色深与判定来源。
- Unix OSC 10/11 探测、Windows Console palette 探测及启动输入回放。
- 深色、浅色、未知背景下的语义颜色解析。
- prompt_toolkit 样式组合和所有 TUI 常规组件的语义化迁移。
- 审批卡、选中行、输入提示、状态、markdown、活动视图和历史视图的颜色一致性。
- patch/diff/syntax highlight 的富色上下文、ANSI 降级和主题边界。
- Windows、macOS、Linux 的自动化契约测试及真实终端验收清单。

### 2.2 非目标

- 不移植 Ratatui、crossterm 或 Codex 的 Rust 内部类型。
- 不把终端颜色或 UI token 放入 `agent`、`protocol` 或 `infrastructure`。
- 不把终端名称映射成整套配色主题。
- 不在本计划内引入 Codex theme picker；先保留可扩展边界，后续有明确产品需求再评估。
- 不同时改造滚动历史、图片协议、键盘增强或 resize 算法；这里只确保终端身份可被这些能力
  独立消费，避免继续增加总开关。
- 不新增仓库级检查脚本，也不把 `tests/test_package_architecture.py` 作为日常颜色迭代门槛。

## 3. Codex 参考架构

### 3.1 参考文件

以下路径以固定 revision 为准。当前 `codex-main/` 是 vendored 快照，不带独立 `.git` 元数据；
若更新快照，必须同时更新 revision、文件清单和差异结论，不能静默跟随目录内容。

| 职责 | Codex 参考文件 |
| --- | --- |
| 终端身份与 multiplexer | `codex-main/codex-rs/terminal-detection/src/lib.rs` |
| 颜色原则 | `codex-main/codex-rs/tui/styles.md` |
| 启动编排与缓存预热 | `codex-main/codex-rs/tui/src/tui.rs` |
| Unix/Windows 默认色探测 | `codex-main/codex-rs/tui/src/terminal_probe.rs` |
| 启动输入过滤与回放 | `codex-main/codex-rs/tui/src/terminal_probe/startup_replay.rs` |
| 色深、默认色缓存、RGB 降级 | `codex-main/codex-rs/tui/src/terminal_palette.rs` |
| 颜色混合与感知距离 | `codex-main/codex-rs/tui/src/color.rs` |
| 通用语义样式 | `codex-main/codex-rs/tui/src/style.rs` |
| 选中行统一强调样式 | `codex-main/codex-rs/tui/src/bottom_pane/selection_popup_common.rs` |
| 审批交互样式 | `codex-main/codex-rs/tui/src/bottom_pane/approval_overlay.rs` |
| diff 每帧样式上下文 | `codex-main/codex-rs/tui/src/diff_render.rs` |
| syntax scope 与 diff 背景 | `codex-main/codex-rs/tui/src/render/highlight.rs` |
| syntax theme 选择边界 | `codex-main/codex-rs/tui/src/theme_picker.rs` |
| 超链接输出边界 | `codex-main/codex-rs/tui/src/terminal_hyperlinks.rs` |
| 终端身份的其他消费者 | `codex-main/codex-rs/tui/src/tui/scrollback.rs`、`codex-main/codex-rs/tui/src/tui/keyboard_modes.rs`、`codex-main/codex-rs/tui/src/resize_reflow_cap.rs`、`codex-main/codex-rs/tui/src/pets/image_protocol.rs` |
| 启动后不重复探测 | `codex-main/codex-rs/tui/tests/suite/focus_palette.rs` |

### 3.2 Codex 的职责分层

Codex 的关键设计不是一组固定色值，而是以下数据流：

```text
环境变量 + multiplexer 查询
        -> TerminalInfo（身份事实，进程级缓存）

stdout + supports-color + 少量身份修正
        -> effective color level

Unix 启动探测 / Windows Console API
        -> default foreground + default background

color level + default colors
        -> 通用语义样式 / 富色表面上下文
        -> 组件渲染
```

具体行为：

- 身份检测按 multiplexer、`TERM_PROGRAM`、终端特定变量和 `TERM` 回退分层处理，并保存名称、
  版本、原始信号和 multiplexer 信息。
- 原始色深交给成熟的 `supports-color` 判定；有效色深再处理 Windows Terminal 等已知修正。
- Unix 在启动时以一个共享的 100ms deadline 合并颜色、光标和键盘探测；stdin/stdout 不可用时
  回退 `/dev/tty`，并恢复文件描述符状态。
- Unix 探测会过滤终端响应并回放用户提前输入的按键、bracketed paste 和有界的不完整序列；
  Windows 不读取 stdin，而是通过 Console API 读取 palette。
- 默认前后景必须成对有效；探测尝试和无结果也会缓存，focus 恢复时不重新发 OSC 10/11。
- 普通强调色优先 ANSI cyan；成功/新增使用 green，失败/删除使用 red，品牌使用 magenta。
  浅色背景的强调色使用更深的 cyan。常规样式避免任意 RGB、black/white 前景和无审计的
  blue/yellow。
- `best_color` 只在 TrueColor/ANSI 256 输出目标 RGB 或感知距离最近的 xterm 色；ANSI 16 和
  Unknown 不伪造 RGB。直接使用的 ANSI 语义色与 `best_color` 是两条清晰路径。
- diff 在一次渲染开始时建立 `DiffRenderStyleContext`；只有 TrueColor/ANSI 256 才使用背景，
  ANSI 16 退化为前景 green/red。syntax theme 的 diff scope 只覆盖富色背景，不进入终端探测模型。
- 终端身份也被滚动、键盘、resize 和图片协议分别消费，不存在统一的“高能力终端”总开关。

### 3.3 不直接照搬的部分

- Codex 尚未显式识别 JetBrains/JediTerm，ProxyMind 需要补充该事实信号和测试矩阵。
- Codex 对原始 `WT_SESSION` 的真彩提升可能受继承环境变量影响。ProxyMind 应保留真实
  Windows Terminal 的行为，但将提升绑定到已解析身份和判定来源，避免把嵌入式终端误判为 WT。
- ProxyMind 需要显式区分“禁止颜色”和“无法确定”，保证 `NO_COLOR` 不会通过组件 fallback
  再次产生颜色。
- Rust 侧用 clippy 限制直接颜色。Python 侧采用定向 pytest 样式所有权契约，不增加独立守卫脚本。

## 4. ProxyMind 当前架构与缺口

### 4.1 当前数据流

```text
frontends/cli/frontend.py
  -> detect_terminal_capabilities(...)
     -> frontends/terminal/capabilities.py
        （身份 + 色深 + Unix/Windows 探测 + 缓存 + 解析）
  -> TuiRuntime / Screen / renderer
     -> frontends/terminal/palette.py
     -> frontends/tui/core/styles.py + 多个组件局部 style
     -> patch renderer 独立富色路径
```

已有基础是可复用的：CLI 在 TUI 启动前构造一次能力对象，Unix 已提供输入回放回调，Windows
探测不读取 stdin，颜色量化采用感知距离，动态背景也已经根据终端背景混合。这些应迁移而不是重写。

### 4.2 缺口优先级

| 优先级 | 当前缺口 | 影响 | 对齐目标 |
| --- | --- | --- | --- |
| P0 | `frontends/terminal/capabilities.py` 同时拥有身份、色深、探测、解析、缓存和平台 API | 生命周期与平台边界难以验证 | 拆成纯解析、平台探测和会话编排，保留单一公开入口 |
| P0 | 常规颜色散落在 `core/styles.py`、`terminal/styles.py`、input、menu、approval、summary、markdown 和活动组件 | 同一状态出现不同蓝色，合并顺序决定结果 | 一个语义 token 目录、一个 palette resolver、组件只引用 token |
| P0 | `semantic_color(..., fallback=...)` 对 ANSI 16 与 Unknown 使用同一路径 | `NO_COLOR` 或未知能力仍可能泄漏颜色 | 显式无色语义；ANSI 16 只用命名色；未知保守退化 |
| P0 | Unix probe 缓冲无明确总上限，回放只剔除完整 OSC 10/11 | 不完整响应可能进入输入解析器，异常输出可扩大内存 | 共享 deadline、有界缓冲、语法感知过滤、完整 typeahead 回放 |
| P1 | `HIGH_CAPABILITY_TERMINALS`、`DYNAMIC_SURFACE_TERMINALS` 与实际 `dynamic_surfaces` 判定不一致且主要只被测试消费 | 存在两套能力策略和失效分支 | 删除总开关；各能力由独立 resolver 基于事实计算 |
| P1 | `TerminalTheme.scope_backgrounds` 混入默认终端主题，生产路径未赋值 | syntax theme 与终端探测耦合，契约名存实亡 | 从终端快照删除，迁入 diff/syntax 渲染上下文 |
| P1 | JetBrains/JediTerm 没有正式身份和组合测试 | PyCharm 中只能靠零散色值掩盖误判 | 识别 `TERMINAL_EMULATOR=JetBrains-JediTerm`，能力仍由色深信号决定 |
| P1 | `WT_SESSION` 可独立把嵌入式终端提升为 TrueColor | PyCharm 等继承环境可能得到错误能力 | 保存信号来源；仅在真实 WT 身份成立时应用平台修正 |
| P1 | input、menu、approval 与总样式表重复声明 active/current 样式 | 修改一处无法保证最终结果 | 组件表仅提供布局类，交互状态统一由 semantic catalog 投影 |
| P1 | 渲染路径仍能读取具体 `TerminalCapabilities` 的内部字段并自行决策 | 新组件会重复能力逻辑 | 渲染器只接收已解析 palette/style context |
| P2 | patch 独有富色逻辑与通用 palette 接口边界不完整 | scope 背景来源不清，逐行重复解析 | 每次 patch 渲染只创建一次不可变 rich-style context |
| P2 | 组件测试大量断言各自硬编码色值 | 测试固化重复实现而不是语义 | 断言 token 映射、能力矩阵和关键可视快照 |
| P2 | `TerminalKind` 中部分枚举与检测行为不一致 | 声明支持但无法到达 | 每个枚举必须有检测用例，或删除未实现枚举 |

## 5. 目标架构

### 5.1 状态所有权和生命周期

```text
CLI composition root（唯一创建者）
  |
  +-- TerminalIdentityResolver：纯环境/查询输入 -> 身份事实
  +-- TerminalColorSupportResolver：TTY/开关/色深信号 -> 原始与有效色深
  +-- TerminalProbeAdapter：平台 I/O -> 默认前后景
  |
  `-- TerminalCapabilities（冻结的会话快照）
         |
         +-- TerminalPaletteResolver -> 语义 token 到具体样式
         `-- RichStyleContextFactory -> diff/syntax 专用上下文
                    |
                    `-- prompt_toolkit / terminal renderer
```

约束：

- `frontends/cli/frontend.py` 在事件读取器启动前解析一次快照并注入 `TuiRuntime`。
- 只有启动输入所有者可以执行 Unix probe 和回放；其他模块不得直接读取 stdin。
- Windows adapter 只使用 Console API，不读取输入队列；Unix adapter 负责 fd、`/dev/tty`、
  nonblocking 和恢复。
- probe 结果按会话缓存，包括“已尝试但无结果”；focus、resize 和重绘不得触发探测。
- renderer 不读取 `os.environ`、平台 API 或全局 cache，只消费不可变输入。
- `agent` 侧展示模型保持颜色无关；终端语义属于 `frontends/terminal`，prompt_toolkit 格式投影属于
  `frontends/tui`。

### 5.2 目标模块边界

| 模块 | 唯一职责 |
| --- | --- |
| `frontends/terminal/identity.py` | `TerminalIdentity`、multiplexer 信息、纯检测规则和信号来源 |
| `frontends/terminal/color_support.py` | stdout TTY、显式开关、原始/有效色深和判定来源 |
| `frontends/terminal/probe.py` | `TerminalDefaultColors`、统一 timeout/上限和平台 adapter 协议 |
| `frontends/terminal/probe_unix.py` | OSC 查询、有限状态解析、输入过滤/回放、fd 生命周期 |
| `frontends/terminal/probe_windows.py` | Windows Console palette/attribute 读取 |
| `frontends/terminal/capabilities.py` | 组合上述结果并返回冻结快照；不再包含平台实现 |
| `frontends/terminal/palette.py` | 色彩量化、混合、明暗判断和语义 token 解析 |
| `frontends/terminal/semantic_styles.py` | 常规终端语义 token 及各能力级别的唯一映射 |
| `frontends/tui/core/styles.py` | 将已解析语义样式组合为 prompt_toolkit `BaseStyle` |
| `frontends/terminal/renderers/patch.py` | patch/diff 专用上下文和 syntax scope 覆盖，不定义常规 UI 色 |

只有当对应消费者和旧路径能在同一迭代迁移、删除时才创建模块；不得先加空 facade 或长期兼容别名。

### 5.3 终端能力模型

目标快照至少包含：

- 身份：终端种类、版本、`TERM_PROGRAM`、`TERM`、multiplexer、判定来源。
- 输出：是否 TTY、是否显式禁色、原始色深、有效色深、色深判定来源。
- 默认色：前景和背景成对存在或共同缺失、探测方式、是否已尝试。
- 派生主题：`dark`、`light`、`unknown`，只由默认背景解析。

以下信息不进入快照：syntax scope、diff 背景、prompt_toolkit style 字符串、审批卡状态和任何
业务权限。`TerminalTheme.scope_backgrounds` 在 rich context 接通后删除。

色深优先级固定为：

1. 显式禁色，包括 `NO_COLOR` 和等价的 `FORCE_COLOR=0`。
2. 显式强制色深。
3. 非 TTY 输出的保守无色结果。
4. 标准色深信号。
5. 有身份和来源约束的平台修正。
6. 无法确认时不输出自定义颜色。

### 5.4 语义样式目录

普通 TUI 只允许消费下列角色，不直接消费 RGB：

| 角色 | 深色/未知背景 | 浅色背景 | ANSI 16 | 无色 |
| --- | --- | --- | --- | --- |
| `accent` | cyan + bold | 深 cyan + bold | cyan + bold | bold |
| `selected` | 与 `accent` 相同 | 与 `accent` 相同 | cyan + bold | reverse 或 bold |
| `success` / `addition` | green | green | green | bold |
| `failure` / `deletion` | red | red | red | bold |
| `attention` | 经对比度验证的 yellow | default + bold | yellow 或 bold | bold |
| `brand` | magenta | magenta | magenta | bold |
| `secondary` | dim/default | dim/default | dim/default | dim/default |
| `surface.user` | 基于背景混合 | 基于背景混合 | 无背景 | 无背景 |
| `surface.approval` | 基于背景混合 | 基于背景混合 | 无背景 | 无背景 |

审批卡的标题、风险、当前选项、快捷键、边框/分隔、允许/拒绝状态必须从这些角色组合，不能维护
审批专用蓝色。网络、MCP、shell、patch 和未来 skill 审批共享同一套视觉语义，差异只来自可信的
审批数据与风险 tone。

允许保留具体颜色的边界只有：

1. `semantic_styles.py` 的集中语义定义。
2. syntax highlight 的语言 token 表。
3. patch/diff 的富色 fallback 和 theme scope 解析。
4. 测试 fixture 与快照期望。

定向 pytest 测试通过 AST/常量清单检查 `frontends/tui` 常规组件没有新增十六进制颜色或直接
`ansiblue`/`ansiyellow`。例外模块必须显式列出并说明职责；该测试是样式所有权契约，不是新的
包架构守卫。

## 6. 五个实施迭代

每个迭代完成后先检查 diff、运行本迭代定向测试、复核删除项和跨平台契约；无问题后只提交该
迭代涉及的文件并推送。不得把用户工作区中的无关改动带入提交。

### 迭代 1：身份与色深契约

范围：

- 从 `capabilities.py` 提取纯 `identity.py` 和 `color_support.py`。
- 保留一个 composition 入口，快照改为冻结数据；记录每个判定的来源。
- 增加 JetBrains/JediTerm 身份、tmux/zellij 透传及 Windows Terminal 身份组合。
- 用正式优先级替代手写分支叠加；先保持除已确认误判外的现有可观察颜色行为。
- 删除 `HIGH_CAPABILITY_TERMINALS`、`DYNAMIC_SURFACE_TERMINALS`、`high_capability` 和
  `supports_dynamic_surfaces` 等总开关及对应旧测试。

关键测试：

- Windows/macOS/Linux 环境表驱动测试。
- PyCharm/JediTerm 与 `TERM=xterm-256color`、`TERM=dumb`、`COLORTERM=truecolor`、
  `NO_COLOR`、`FORCE_COLOR`、继承 `WT_SESSION` 的笛卡尔关键组合。
- tmux client identity 优先级、zellij、未知终端和重定向输出。

退出条件：每个 `TerminalKind` 可由测试到达或被删除；身份变化不再隐式改变所有终端能力。

### 迭代 2：一次性安全探测

范围：

- 抽出 Unix/Windows adapter，公开边界只返回具名 `TerminalDefaultColors`。
- Unix 探测使用共享 100ms deadline、64KiB 总上限和 1024-byte 不完整 OSC 前缀上限。
- 同时发送 OSC 10/11，只有完整前后景对才提交。
- 用有限状态解析保留普通按键、UTF-8、bracketed paste 和有界不完整输入，剔除完整终端响应。
- 确保所有成功、超时和异常路径都恢复 fd flags；用明确的可调用/协议边界消除
  `# type: ignore[attr-defined]`。
- Windows 只读 Console API；失败返回“已尝试、无结果”。
- 快照创建后禁止 focus/resize/repaint 再探测。

关键测试：完整/分片/乱序响应、提前输入、paste、无效 RGB、超长输入、不完整 OSC、超时、
非 TTY、`/dev/tty` 回退、fd 恢复和 Windows stdin 零读取。

退出条件：probe 的平台 I/O 不再存在于 `capabilities.py`，所有输入字节只有“响应消费”或“原样回放”
两种可证明结果。

### 迭代 3：统一 palette 与语义目录

范围：

- 统一 TrueColor、ANSI 256 感知量化、ANSI 16 命名色和无色解析。
- 将深/浅/未知背景、对比度规则、surface 混合和语义 token 放入唯一 resolver。
- 将 `frontends/tui/core/styles.py` 缩减为 prompt_toolkit 样式组合层。
- 先迁移共享基础状态：accent、selected、success、failure、attention、brand、secondary、
  user surface 和 approval surface。
- 增加常规组件颜色所有权测试，阻止新的散落 RGB/blue/yellow。

退出条件：同一语义在任意组件解析为相同样式；`NO_COLOR` 下无自定义前景/背景；ANSI 16 下无
RGB 或 256 色背景。

### 迭代 4：全量组件迁移

范围：

- 一次性迁移 input、menu、approval、network/MCP approval card、summary、markdown、history、
  activity、status、download/upload、trace 和 overlay。
- 选中行的文字、快捷键和标记统一使用 `selected`，不再局部覆盖为蓝色。
- 删除组件主题字典中重复的 active/current/selected 色值和失效的 merge override。
- 审批卡只按语义 token 和业务 tone 组合；审批类型不得选择具体颜色。
- 更新测试从“组件硬编码色值”转为“组件使用语义角色 + 最终能力矩阵输出”。

退出条件：除允许清单外，常规 TUI 模块无十六进制颜色、直接 ANSI blue/yellow 或组件专属强调色；
PyCharm dark 主题的选中与审批强调呈 cyan 系而不是旧 blue，浅色主题使用可读的深 cyan。

### 迭代 5：富渲染、三平台验收与收口

范围：

- patch/diff 每次渲染只创建一次不可变 rich-style context。
- 将 syntax scope 背景从 `TerminalTheme` 移到该上下文；删除 `scope_backgrounds` 旧字段和回退。
- TrueColor/ANSI 256 使用背景和 scope override；ANSI 16/无色只保留前景或修饰符。
- 核对 OSC 8 超链接、键盘、scrollback、resize 等身份消费者只读取所需事实，不复用颜色能力。
- 在 `ARCHITECTURE.md` 补充稳定的前端终端快照、样式所有权和富渲染边界；删除本计划中已转为
  稳定规则的临时实施说明。
- 完成自动化矩阵和 Windows/macOS/Linux 真实终端验收记录。

退出条件：`TerminalTheme.scope_backgrounds` 与旧颜色 fallback 全部删除；三平台验收无阻断问题；
稳定文档、实现和测试使用同一职责边界。

## 7. 验证矩阵

### 7.1 自动化矩阵

| 维度 | 必测值 |
| --- | --- |
| 平台 | Windows、macOS、Linux 的 adapter 契约 |
| 身份 | Windows Terminal、PyCharm/JediTerm、VS Code、Apple Terminal、iTerm2、WezTerm、Kitty、常见 Linux terminal、unknown |
| multiplexer | none、tmux、zellij；外层和 client 信号冲突 |
| 输出 | TTY、redirected、stdout 不可查询、stdin 不可查询 |
| 色深 | TrueColor、ANSI 256、ANSI 16、显式无色、未知 |
| 主题 | dark、light、unknown、只收到一个默认色响应 |
| 环境冲突 | `NO_COLOR`、`FORCE_COLOR`、`COLORTERM`、`TERM`、`WT_SESSION` 的优先级 |
| 输入回放 | 普通按键、UTF-8、bracketed paste、分片 OSC、无效/超长/不完整序列、timeout |
| 组件 | 选择、输入、菜单、网络/MCP/shell/patch 审批卡、状态、markdown、diff、overlay |

### 7.2 真实终端验收

- Windows：Windows Terminal PowerShell、Windows Terminal cmd、PyCharm/JediTerm PowerShell、
  PyCharm/JediTerm cmd；重点复核继承 `WT_SESSION`、`NO_COLOR` 和重定向。
- macOS：Apple Terminal、iTerm2、PyCharm/JediTerm；各测 dark/light 与 tmux。
- Linux：常见 VTE/GNOME terminal、Konsole 或 Kitty、PyCharm/JediTerm；各测 256 色、真彩、tmux/zellij。
- 每个终端至少检查：启动不吞输入、审批卡初始/切换/允许/拒绝、长内容窄宽换行、选中行、
  user/approval surface、markdown、patch 新增/删除和 focus 恢复不重复发 OSC。

PyCharm/JediTerm 的通过标准不是固定得到某个 RGB，而是：

- 有真彩证据时使用 cyan 语义的真彩/深浅变体。
- 只有 256 色证据时稳定量化。
- ANSI 16 时使用终端自身 cyan/green/red palette。
- `NO_COLOR` 或非 TTY 时不通过 fallback 泄漏颜色。
- 继承 `WT_SESSION` 但身份仍为 JediTerm 时不强制提升为 Windows Terminal TrueColor。

### 7.3 每迭代命令

先激活仓库虚拟环境，再按影响范围选择定向测试：

```shell
python -m pytest tests/test_terminal_capabilities.py tests/test_terminal_palette.py -q
python -m pytest tests/test_tui_approval.py tests/test_tui_menu_alignment.py tests/test_tui_input_history.py tests/test_tui_input_completion.py -q
python -m pytest tests/test_tool_presentation.py -q
python -m compileall frontends/terminal frontends/tui
git diff --check
```

实际测试文件名在迭代中可随职责拆分调整，但不得减少上述行为覆盖。遵照当前开发约定，普通迭代
不运行 `tests/test_package_architecture.py`；只有明确的发布收口另行决定。

## 8. 提交、复核与回退

- 每个迭代一个独立提交；提交前只暂存该迭代文件，先检查 `git diff --cached --name-only`。
- 复核顺序固定为：职责边界、旧路径删除、定向测试、三种色深降级、`git diff --check`。
- 不接受“新旧两套 palette 并存后以后再删”。一个 token 的最后一个消费者迁移时同步删除旧定义、
  fallback 和测试。
- 迭代 1、2 主要改变检测内部结构，应保持可观察样式；迭代 3、4 才集中改变普通 TUI 颜色。
- 迭代 4 是唯一常规组件颜色迁移窗口。其后颜色需求先定义语义角色和全矩阵行为，再修改 resolver，
  不直接编辑组件色值。
- 迭代失败时按提交边界回退，不保留半接通 facade、重复字段或终端专用临时颜色。

## 9. 完成定义

以下条件全部满足才算升级完成：

- 终端身份、色深、平台探测、palette、语义样式和 rich context 各有唯一所有者。
- 一个会话只探测一次，Windows 不读 stdin，Unix 输入完整回放且有明确内存上限。
- `NO_COLOR`、ANSI 16、ANSI 256、TrueColor、dark/light/unknown 全部有契约测试。
- PyCharm/JediTerm 在三平台进入正式身份与验收矩阵，不存在 JetBrains 专属固定色值。
- 网络、MCP、shell、patch 和 skill 来源审批使用同一审批视觉语义。
- 常规 TUI 组件不存在允许清单外的直接颜色；新增散落颜色会由定向 pytest 失败阻止。
- syntax/diff theme 与终端默认色模型解耦，富色背景只在能力允许时启用。
- 被替代的总开关、重复 style、`scope_backgrounds` 和旧 fallback 已删除。
- 稳定规则进入 `ARCHITECTURE.md`，实现计划不再承担长期架构权威。
