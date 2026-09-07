# Codex PTY 与 TUI 分阶段对齐清单

## 1. 目标与边界

本文用于把 ProxyMind 的 PTY 工程成熟度、输入交互、TUI 渲染和颜色能力对齐到本地
`codex-main` 参考实现，并给出可执行、可验收、可回退的实施顺序。

对齐以可观察行为和工程准出为单位，不复制 Rust 内部结构，不追求品牌文案或像素级相同：

- `ARCHITECTURE.md` 仍是客户端内部职责与依赖方向的唯一权威；
- Codex 只提供 PTY、终端协商、交互和渲染的成熟实现证据；
- PTY 是 Agent 工具进程的正式运行时能力；`tests/support/pty` 只负责黑盒验收，不得成为产品实现；
- Windows、macOS 是必过平台，Linux 复用 POSIX 实现并作为低成本回归平台；
- 当前 Enter、Tab、Ctrl-C 的业务语义是不可回退基线，PTY 用例验证真实终端字节不会改变这些语义；
- 任何与 Codex 不同的产品语义都必须在本文显式记录原因，不能以实现困难作为偏离理由。

### 成熟度口径

| 等级 | 含义                                                           |
|------|----------------------------------------------------------------|
| M0   | 没有该能力或只靠人工观察                                       |
| M1   | 纯函数或组件单元测试覆盖                                       |
| M2   | 内存输入输出、虚拟 Screen 或快照覆盖                           |
| M3   | 单平台真实跨进程 PTY 覆盖，具备超时和清理                      |
| M4   | Windows/macOS 对称覆盖，失败产物、进程树清理和本地验收门禁完整 |

## 2. 当前结论

| 领域                   | Codex 参考成熟度 | ProxyMind 当前成熟度       | 剩余门禁                              | 目标 |
|------------------------|-----------------:|----------------------------:|---------------------------------------|-----:|
| PTY 进程与 I/O         |               M4 | Windows M4 / macOS 待实机   | macOS sidecar 产物与目标机实测        |   M4 |
| Enter/Tab/Ctrl-C 逻辑  |            M2-M3 | Windows M4 / macOS 待实机   | macOS 真实按键链复验                  |   M4 |
| TUI 布局与流式渲染     |            M2-M3 | Windows M4 / macOS 待实机   | macOS Screen golden 复验              |   M4 |
| 色深、主题与语义颜色   |            M2-M3 | Windows M3 / macOS 待实机   | 双平台真实色深矩阵                    |   M4 |
| resize、focus、paste   |               M3 | Windows M4 / macOS 待实机   | macOS 竞态复验                        |   M4 |
| 退出、崩溃与进程树回收 |               M4 | Windows M4 / macOS 待实机   | macOS process group 与 sidecar 回收   |   M4 |
| cold-resume            | 不作为 PTY 基线  | 独立恢复契约已覆盖          | 不纳入 Codex runtime PTY 成熟度结论   | 不适用 |

判断：Phase 0-12 已把 PTY 提升为正式 runtime capability，并经统一 `exec_command` / `write_stdin`
接入 `ProcessSessionManager`。Windows 生产路径达到 M4；POSIX adapter、依赖和发布校验已就绪，
但当前 Windows 主机无法替代 macOS sidecar 产物与实机结论，因此跨平台总成熟度仍不能标为 M4。

## 3. 已确认的现有行为基线

以下行为必须先原样固化，后续对齐不得把它们混成同一种“发送”：

| 状态           | 输入          | 当前契约                                            | Codex 对应行为                          | PTY 验收结果 |
|----------------|---------------|-----------------------------------------------------|-----------------------------------------|--------------|
| 空闲、有正文   | Enter         | 创建下一 Turn                                       | 提交用户输入                            | [x]          |
| 空闲、有正文   | Tab           | 无候选时提交；有候选时优先补全                      | 同类优先级                              | [x]          |
| 执行中、有正文 | Enter         | 优先作为当前 Turn steer；不可 steer 时排到下一 Turn | `Submitted` 进入活动 Turn，拒绝后转队列 | [x]          |
| 执行中、有正文 | Tab           | 明确 `queue_only`，只排到下一 Turn                  | `Queued`，不作为 steer                  | [x]          |
| 有草稿         | Ctrl-C        | 第一次只清草稿                                      | 先取消当前局部输入/弹层                 | [x]          |
| 执行中、空草稿 | Ctrl-C        | 请求当前 Turn 中断并进入退出确认                    | 中断活动任务                            | [x]          |
| 已进入退出确认 | Ctrl-C        | 第二次请求退出，不等待远端结算阻塞 UI               | 二次退出保护                            | [x]          |
| 中断结算       | pending steer | 按确认结果提交、恢复或延后，不能丢失或重复          | 恢复/重交 pending steer                 | [x]          |
| 队列存在       | 编辑快捷键    | 恢复最后一条队列消息到编辑器                        | 按终端能力选择可用快捷键                | [x]          |

优先级也属于契约：弹层/补全 > 局部编辑 > 发送或排队 > Turn 中断 > 应用退出。测试不得只发送
文本后直接调用 handler，必须最终覆盖真实终端按键字节。

## 4. 推荐技术方案

### 4.1 测试侧统一端口

在 `tests/support/pty/` 建立测试专用实现，向场景测试只暴露一个 `PtySession` 契约：

```text
spawn(argv, cwd, env, TerminalSize)
write_text(text)
send_key(Enter | Tab | CtrlC | Escape | Backspace | ...)
paste(text)
resize(TerminalSize)
read_raw(deadline)
screen()
wait_screen(predicate, deadline)
wait_exit(deadline)
terminate() / kill() / close()
```

统一契约必须同时拥有原始字节日志和 VT Screen 投影。原始字节用于验证控制序列、颜色和协议边界，
Screen 用于验证用户实际看到的文本、光标和布局。禁止用正则剥离 ANSI 后把字符串当作最终渲染。

### 4.2 平台 adapter

| 平台          | 推荐实现                                    | 原因                                                                             |
|---------------|---------------------------------------------|----------------------------------------------------------------------------------|
| macOS/Linux   | `pexpect`/`ptyprocess` 的原生 PTY           | 成熟处理 controlling TTY、窗口尺寸、EOF 与 wait，避免重复手写 `forkpty` 生命周期 |
| Windows       | 基于 `ctypes` 的系统 ConPTY adapter         | 直接拥有同步管道、HPCON、Job Object 和 Win32 输入归一化，不依赖 Rust             |
| Screen oracle | `pyte` 或通过 spike 选出的等价 VT100 解析器 | 独立于 prompt_toolkit，可解析真实输出并保留光标/样式状态                         |

验收依赖合并进现有 `requirements.txt`，不维护第二份 requirements。POSIX 使用锁定版本的
`pexpect`/`ptyprocess`，Screen oracle 使用锁定版本的 `pyte`；Windows adapter 只使用标准库
`ctypes` 和系统 ConPTY。任何平台不支持时必须明确失败，不得静默降级为普通 pipe。

### 4.3 场景目录与启动器

```text
.cache/pty-runs/<scenario-id>/
  manifest.json
  raw-output.bin
  screen.txt
  events.jsonl
  process.json
  state/                 # MIND_STATE_HOME
  logs/
```

- 每个场景创建唯一目录；同一 cold-resume 场景的所有重启必须复用同一目录；
- 启动器显式传递相同的 `MIND_STATE_HOME`、`HELIX_HOME`、`HELIX_STORAGE_ROOT`；
- `MIND_HOME` 只引用原配置，不复制、不输出 config 内容或 API key；
- 成功后默认清理，失败保留 manifest、原始输出、Screen、SQLite 和日志；
- Helix 仍使用固定端口时，带 Helix 的场景串行运行；其他场景可按状态目录隔离并发；
- 所有 wait 都使用单调时钟和显式截止时间，禁止固定长 sleep 作为同步条件。

## 5. 分阶段实施清单

### Phase 0：冻结基线与最小技术 spike

目标：先证明推荐依赖和测试结构能跨平台工作，不触碰 TUI 业务逻辑。

- [ ] 记录采用的 `codex-main` 快照来源或上游 commit，避免参考代码继续漂移。
- [x] 将 PTY 验收依赖合并进 `requirements.txt`，锁定 POSIX 和 VT parser 依赖。
- [ ] 在 macOS 和 Windows 分别启动最小 Python REPL，完成 UTF-8 输入、Enter 回显和正常退出。
- [x] Windows 使用标准库 `ctypes` 直连系统 ConPTY，不需要第三方 wheel 或 Rust。
- [ ] 验证 parser 能处理 prompt_toolkit 实际输出、宽字符、256 色、truecolor、OSC 8 和同步更新序列。
- [x] PTY 实现位于 `tests/support/pty/`，不进入产品包或产品运行时依赖。
- [x] 记录依赖决策：采用 `pexpect`/`ptyprocess`、`pyte` 和原生 ConPTY，拒绝存在输出截断、EOF 与清理缺陷的 `pywinpty 3.0.5`。

准出条件：Windows 与 macOS 的最小 spike 都成功；任何一端失败都不得进入正式 TUI 场景编写。

### Phase 1：PTY 生命周期与进程工程

目标：达到 Codex `utils/pty` 的核心工程能力，再把 TUI 接进来。

- [x] 定义不可变 `TerminalSize(rows, columns)` 和平台无关 `PtySession` 契约。
- [x] POSIX adapter 创建真实 controlling TTY，并让 stdin/stdout/stderr 共享 PTY slave。
- [x] Windows adapter 使用 ConPTY，统一处理 CR/LF、CRLF、Backspace 和 UTF-8 字节。
- [x] I/O reader 持续排空输出；高水位双向 I/O 不反向阻塞子进程退出。
- [x] 输入写入支持短写、关闭输入、EOF、并发 terminate 和幂等 close。
- [x] resize adapter 在两平台实现；Windows 已由子进程终端尺寸 API 实机验证。
- [x] Ctrl-C 按平台终端模式编码：POSIX 发送 `0x03`，Windows Win32 Input Mode 发送保留 Ctrl 修饰状态的输入记录，不退化为 Python handler。
- [x] POSIX 控制字符交给 controlling TTY 前台进程组；Windows 中断与强杀分别建模。
- [x] 正常根进程退出后完整排空尾部输出，再返回最终退出码。
- [x] terminate 超时后升级为 kill，并始终 wait/reap 根进程。
- [x] 关闭失败或测试取消时清理子进程树，不残留 shell、Python 或 Mind 子进程。
- [x] Windows 通过后代进程泄漏测试，并用原子 Job List/Job Object 拥有整棵进程树。
- [x] adapter 缺失或平台不支持时明确失败，不存在 silent skip/fallback。

定向用例：

- [x] Python 输入回显、Unicode、Enter、Backspace、EOF；
- [x] 256KiB 输入与大量输出并发，输出 channel 不产生死锁；
- [x] 子进程先退出但后台后代持有 PTY，驱动仍可在截止时间内收敛；
- [x] Ctrl-C 到达 raw-mode 等待进程，进程处理后可继续完成；
- [x] resize 前后子进程报告准确尺寸；
- [x] 正常退出、terminate、kill、测试取消四条路径均无进程泄漏。

准出条件：Phase 1 用例在 Windows、macOS 连续运行 20 次零失败、零残留进程，失败时必有原始日志。

#### 阶段进展（本文件不入库）

- Phase 0 已于 2026-09-06 完成并推送：`6a54d839`。依赖按实施指令合并到
  `requirements.txt`，未保留第二份 requirements，也未增加 CI。
- Phase 1 已于 2026-09-06 完成 Windows 原生 ConPTY 验收并推送：`c56ba031`、
  `20c6926a`、`203355f8`、`19d269bf`。
  Windows adapter 使用同步管道、`STARTUPINFOEX`、原子 Job List 和 Job Object 清理进程树；
  POSIX adapter 使用 `pexpect`/`ptyprocess` controlling TTY 契约。
- Phase 1 最终定向结果：`12 passed, 1 skipped`；Windows 十一类真实 PTY 用例连续 20 轮零失败，
  `pip check`、`compileall`、`git diff --check` 均通过。跳过项是 Windows 主机上的 POSIX smoke。
- macOS 实机 20 轮复测受当前 Windows 执行主机限制，保留为最终跨平台实机验收项；不以 CI 代替。

### Phase 2：终端协商、输入回放与 Screen oracle

目标：真实模拟 Codex TUI 启动时面对的终端，而不是把 PTY 当成无响应字节管道。

- [x] 固定每个场景的 `TERM`、`COLORTERM`、`NO_COLOR`、终端身份变量和初始尺寸。
- [x] 启动器能回答 OSC 10/11 默认前景/背景查询。
- [x] 启动器能回答光标位置查询，并识别键盘增强模式的启用/恢复序列。
- [x] 每类查询设置 100ms 量级共享截止时间和最大字节上限。
- [x] 探测响应与用户输入交错时，响应只被消费一次，用户输入完整回放一次。
- [x] 未知、截断、超长控制序列不会导致启动挂死或无界缓存。
- [x] 默认颜色探测成功和失败都只缓存一次；focus 切换不得重复查询。
- [x] Screen oracle 暴露可见文本、光标、单元格前景/背景/属性和 scrollback。
- [x] 每个断言先等待稳定条件，再截取 Screen；禁止依赖机器速度猜测帧时序。
- [x] 失败产物同时保存 raw output 和最终 Screen，能够区分“没输出”和“parser 没解析”。

准出条件：启动探测期间注入字符、focus 和 resize，字符不丢失、不重复，TUI 在截止时间内可交互。

#### 阶段进展（本文件不入库）

- Phase 2 已于 2026-09-06 完成并提交：`c54c9c6f`。Windows ConPTY 下的终端协议、输入日志
  和 VT Screen oracle 使用 100ms 共享截止时间和 64 字节缓存上限，失败产物分别保存 raw output
  与 Screen。
- 真实 PTY 场景覆盖查询响应、focus、启动阶段 resize、Unicode 用户输入和模式恢复；
  `tests/test_pty_terminal.py` 连续 20 轮零失败，终端定向集 `60 passed`。
- Windows ConPTY 会在宿主层消费 DSR、OSC 10/11 和 PDA，因此 Windows 跨进程用例以键盘增强
  查询验证响应回路，全部五类查询由平台无关分片解析用例覆盖；Mind 本体交互从 Phase 3 接入。

### Phase 3：真实输入交互对齐

目标：用真实 PTY 字节闭环验收第 3 节全部输入语义。

- [x] 空闲 Enter 提交一次且只创建一个 Turn。
- [x] 空闲 Tab 在无候选普通文本下提交；有 slash/file/skill 候选时优先补全。
- [x] 执行中 Enter 进入 pending steer，并在服务端确认后只出现一次。
- [x] 不可 steer 状态下 Enter 转为下一轮队列，不丢失附件、粘贴内容或命令 provenance。
- [x] 执行中 Tab 明确进入 queued messages，不误发 steer，不提前执行 `!` 命令。
- [x] slash completion 优先于 Tab queue；完整 slash 命令按既有 dispatch policy 处理。
- [x] Ctrl-C 有草稿时只清草稿，不中断 Turn，不退出应用。
- [x] Ctrl-C 空草稿且 Turn 活动时只发一个幂等中断请求。
- [x] 第二次 Ctrl-C 在确认窗口内退出；超时后恢复为第一次语义。
- [x] Ctrl-C 早于 `turn.started`、晚于完成事件、断流期间到达时都能收敛。
- [x] 中断后 pending steer、rejected steer、queued message 和现有草稿按规定顺序恢复。
- [x] 编辑最后一条队列消息后，原条目删除且编辑器内容只恢复一次。
- [x] Bracketed paste、CRLF、多行、宽字符、emoji、组合字符和超大粘贴不被拆成快捷键。
- [x] 弹层、补全、审批和 transcript overlay 打开时，按键由最内层活动表面消费。
- [x] focus gained/lost 与按键相邻到达时，不吞掉已排队按键。

准出条件：每个场景同时断言 Screen、业务事件/存储事实和最终进程状态；只断言其中一层不算通过。

#### 阶段进展（本文件不入库）

- Phase 3 已于 2026-09-06 完成并推送：`337bcf40`。Windows 原生 ConPTY 真实按键覆盖 Enter、Tab、Ctrl-C、
  completion、审批、transcript、focus、Bracketed paste 和中断恢复；每个场景同时核对可见 Screen、
  跨进程业务事实与退出状态。
- 定向交互集 `320 passed`；核心 16 场景连续 20 轮零失败，completion/transcript 与 approval
  所有权场景连续 10 轮零失败。

### Phase 4：TUI 渲染、流式更新与 resize 对齐

目标：把当前丰富的内存帧测试提升为真实终端可见结果。

- [x] 启动首帧在 80x24、120x32、窄屏和短屏下无重叠、无越界、无空白画面。
- [x] 输入框、状态区、队列区和 transcript 的高度变化不会遮挡相邻区域。
- [x] history/scrollback 与活动 live tail 分离；提交后历史不重复，现有终端 scrollback 不丢失。
- [x] 流式 delta 只形成一个活动 assistant block，最终提交不重复正文或前缀。
- [x] Provider 断流后重新生成时，旧 attempt、重试提示和新 attempt 顺序稳定，无空白帧。
- [x] tool、approval、effect 和 shell 的开始/更新/完成状态在原位置收敛，不重复追加最终块。
- [x] prompt_toolkit 的同步输出边界成对出现；取消、异常和 resize 后不会遗留同步模式。
- [x] resize storm 合并为稳定重排；最终宽度下文本、markdown、diff 和工具块都可重建。
- [x] 中文、emoji、组合字符、超长 URL 和无空格长词按终端列宽计算，不截断真实内容。
- [x] 可见光标始终位于编辑位置；隐藏/显示/形状恢复顺序正确，退出后恢复用户终端状态。
- [x] transcript overlay 的滚动位置、搜索结果和 raw/rich 切换在活动输出及 resize 后保持稳定。
- [x] 退出、异常、审批取消和 Ctrl-C 后终端模式、光标、标题和 alternate screen 全部恢复。

准出条件：核心场景具备跨平台 Screen golden；平台合理差异单独存放，禁止用宽泛归一化隐藏布局错误。

#### 阶段进展（本文件不入库）

- Phase 4 已于 2026-09-06 完成 Windows 原生 ConPTY 实机验收。共享 Screen golden 覆盖
  80x24、120x32、40x12 和 80x8；其余场景同时断言 raw output、Screen、文档/事件事实与退出码。
- 完整 11 场景连续 20 轮零失败（220 个跨进程场景）；resize storm 专项 20 轮、取消/异常收尾
  专项 20 轮均零失败。受影响回归为 `443 passed`、`35 passed`、PTY 综合集 `34 passed`。
- Windows ConPTY 的标题事件存在异步交付，验收先等待工作区标题真实到达，再触发退出或异常；
  产品侧保证 Application 收尾后由标题管理器执行最后一次清理。macOS 实机复测保留为最终外部门禁。
- Phase 4 已提交并推送：`0702b655`；清单本身继续保持未跟踪，不进入提交。

### Phase 5：生产 PTY 端口与平台 adapter

目标：把真实 PTY 从测试能力提升为产品运行时能力，同时保持 pipe 能力契约不变。

- [x] 在 `agent/ports` 定义独立 `InteractiveProcessCapability`、handle、spec 和 `TerminalSize`；契约覆盖输出读取、stdin、Ctrl-C、EOF、resize、wait、terminate、kill 和幂等关闭。
- [x] `ProcessCapability` 继续表示 stdout/stderr 分离的 pipe 进程，不把两种真实语义混入同一 handle。
- [x] 在 `infrastructure/platform` 提供 Windows ConPTY 与 POSIX controlling-TTY adapter；产品代码不得导入 `tests`，测试驱动也不得被生产模块调用。
- [x] Windows 由 ConPTY + Job Object 拥有整棵进程树；POSIX 由 session/process group 拥有。
- [x] reader 在异步运行时持续排空，阻塞原生 I/O 只能留在 adapter 的受控线程边界。
- [x] adapter 不可用、创建失败或不支持当前平台时明确报错，不静默回退为 pipe。
- [x] 根组合层显式创建并注入 capability；关闭顺序仍由现有资源 owner 管理。
- [x] 复用 Phase 1 的 Unicode、高水位 I/O、Ctrl-C、resize、EOF、reap 和后代清理用例验收生产 adapter。

准出条件：生产模块可直接启动真实交互终端；Windows 定向用例与 20 轮稳定性通过，POSIX 契约
和可执行测试通过；普通 `ProcessCapability` 行为无回归。通过后单独提交并推送。

#### 阶段进展（本文件不入库）

- Phase 5 已把 ConPTY/POSIX adapter 从 `tests` 提升到 `infrastructure/platform/pty`，测试侧只保留
  黑盒 session 和断言驱动；Windows 不安装 `pexpect` 也能导入完整生产 PTY 包。
- `LocalInteractiveProcessCapability` 已覆盖单消费者原始输出、串行输入、终端 Ctrl-C、resize、
  尾部排空、terminate/kill 与幂等回收，并由根组合层显式注入、由工作区 owner 关闭。
- Windows 生产 capability 与底层生命周期 14 项用例连续 20 轮零失败；相关回归 `58 passed,
  1 skipped`，架构审计 `121 passed`。跳过项为当前 Windows 主机不适用的 POSIX 实机 smoke；
  macOS 实机仍是最终外部门禁。
- Phase 5 已于 2026-09-06 提交并推送：`33ef7a4b`；清单本身未进入提交。

### Phase 6：统一工具执行与后台会话接入

目标：让模型和 TUI 可通过现有工具入口使用 PTY，不另建第二套进程会话系统。

- [x] `exec_command` 增加显式 `tty`，默认 `false`，并允许声明初始 `rows` / `columns`。
- [x] `tty=false` 保持现有 stdout/stderr pipe 行为；`tty=true` 通过生产 PTY capability 启动。
- [x] `ProcessSessionManager` 继续唯一拥有 session id、输出 buffer、revision、cursor、wait 和清理。
- [x] PTY 合并 stdout/stderr，并在结果中准确返回 `pty: true`、`pty_fallback: false`。
- [x] `write_stdin` 对 PTY 支持文本、Enter、Ctrl-C、EOF；pipe 的现有输入契约不扩大。
- [x] 提供同一 session 上的 resize 控制；尺寸校验和并发写/resize/终止由会话锁串行化。
- [x] PTY 的 interrupt 发送终端 Ctrl-C，不等同于 terminate；terminate/kill 仍清理完整进程树。
- [x] 前台超时转后台、重复轮询、输出 cursor、截断和最终退出 exactly-once 复用现有语义。
- [x] approval/policy 的 `tty` 事实贯穿 schema、规范化参数、指纹和执行，不再在执行前被删除。
- [x] sandbox sidecar 只有证明 `tty=true` 为真实 PTY 时才标记成功；否则明确拒绝，不伪报 PTY。

准出条件：同一公开 `exec_command` / `write_stdin` 路径可完成 REPL、交互 shell、Ctrl-C 后继续、
resize、后台回读和关闭；结果字段与真实能力一致，无 session、cursor 或进程泄漏。通过后单独提交并推送。

#### 阶段进展（本文件不入库）

- Phase 6 已通过公开 `exec_command` / `write_stdin` 接入生产 PTY；全权限模式使用本机
  ConPTY/POSIX capability，受限模式使用已验证为真实 PTY 的 sandbox sidecar，普通 pipe 路径不变。
- 工具结果、后台摘要、输出快照和 delta 均报告真实 `pty`、终端尺寸且不声明 fallback；本机 PTY
  支持输入、Ctrl-C、EOF 与 resize，sidecar 不支持 resize 时返回明确错误。
- 定向回归 `362 passed`，架构审计 `121 passed`；公开 PTY 工具链 6 项场景连续 20 轮共
  120 次通过，`compileall` 与 `git diff --check` 通过。Phase 6 已提交并推送：`0a2a2fdb`。

### Phase 7：统一 PTY 会话的资源与输出边界

目标：补齐 Codex unified exec 的生产稳健性，不改变 TUI 配色或 Turn 业务逻辑。

- [x] 并行启动按统一上限计数；达到上限时先回收已退出会话，仍无容量则明确拒绝。
- [x] 启动占位与正式注册处于同一 admission 契约，多个并行 tool call 不得越过上限。
- [x] stdout/stderr 各自使用 1 MiB 有界首尾缓冲，超限保留稳定 head 和最新 tail。
- [x] 被省略的中段输出插入明确字节数标记，不把截断伪装成完整输出。
- [x] 增量事件按 8 KiB 上限分块，revision 单调；旧 cursor 超出窗口时返回 `reset=true`。
- [x] 同一 PTY 的 write、Ctrl-C、EOF、resize、terminate 继续由交互锁串行化；不同会话允许并行。
- [x] Turn 取消不丢失已注册的后台 PTY；应用关闭仍终止并 reap 全部根进程与后代。

准出条件：真实 PTY 高水位输出、并行 admission、旧 cursor reset、取消后继续轮询和最终回收均通过；
普通 pipe 使用相同资源边界但保持 stdout/stderr 分离。通过后单独提交并推送。

#### 阶段进展（本文件不入库）

- Phase 7 已增加 64 会话 admission、每流 1 MiB 首尾缓冲、明确省略计数和 8 KiB UTF-8
  安全增量分块；旧 revision 超窗后返回 reset，工具调用取消后已登记 PTY 仍可继续交互。
- 定向回归 `366 passed`，关键资源边界 4 场景连续 20 轮共 80 次通过，架构审计
  `121 passed`，`compileall` 和 `git diff --check` 通过。Phase 7 已提交并推送：`3659f95f`。

### Phase 8：平台与 sandbox PTY 能力闭环

目标：明确本机 adapter 与外置 sandbox sidecar 的真实能力，不用错误标记掩盖协议差异。

- [x] Windows 全权限路径使用 ConPTY，POSIX 全权限路径使用 controlling TTY。
- [x] sandbox sidecar 的 `tty=true` 由子进程三路 `isatty` 事实证明，不只检查请求字段。
- [x] 本机 adapter 支持初始尺寸和同会话 resize；失败返回稳定能力错误。
- [x] sidecar 未提供 resize 方法时继续明确返回 `exec_resize_unavailable`，不伪造成功。
- [x] PTY 输出只进入 stdout，pipe 输出保持 stdout/stderr 分离。
- [x] Windows 已覆盖输入、Ctrl-C、EOF、退出排空、强杀和后代清理；macOS 保留同契约用例，当前主机未实测。
- [x] 缺少平台依赖或 sidecar 时明确失败；Windows 不要求安装 `pexpect`。

准出条件：当前平台真实 PTY 连续 20 轮零失败；另一平台保留可直接执行的同契约验收，并明确记录
尚未实机完成的项目。sidecar 协议不在本仓源码内时不得宣称其 resize 已对齐。

#### 阶段进展（本文件不入库）

- Phase 8 已从 Mind 公开 `exec_command` / `write_stdin` 入口验证本机 ConPTY 和 sandbox sidecar；
  两条路径中的子进程 stdin/stdout/stderr 均报告 `isatty=True`，本机 EOF 与 sidecar 输入正常收敛。
- 输入、resize、Ctrl-C、EOF 和 sandbox PTY 四场景在 Windows 连续 20 轮共 80 次通过；相关回归
  `315 passed`。sidecar resize 按实际协议返回 `exec_resize_unavailable`，macOS 实机仍为外部门禁。

### Phase 9：运行时 PTY 最终验收

目标：只验证 Mind 的生产 PTY 工具能力及既有 TUI 工具展示，不开展 TUI 颜色专项改造。

- [x] 模型公开工具 schema 能选择 `tty`，且 approval/policy 指纹保留该事实。
- [x] `exec_command(tty=true)` 返回真实会话，`write_stdin` 可持续输入、轮询和控制。
- [x] TUI 既有工具投影能显示等待、交互和完成状态，不重复结果块。
- [x] 源码入口与打包资源解析各有 smoke，原生 PTY 不可用时不降级为 pipe。
- [x] 本机 50 轮真实公开工具链无 flaky、无 reader/thread/process 泄漏。
- [x] 定向测试、架构审计、`compileall` 和 `git diff --check` 全部通过。

准出条件：可从同一公开工具链完成交互式 REPL 的启动、输入、Ctrl-C、resize、退出和回读，
结果字段与底层能力一致；按项目决定不新增 CI 配置。

#### 阶段进展（本文件不入库）

- Phase 9 新增真实生产结果到 TUI session 的跨边界验收：空轮询显示一次等待，stdin 交互后显示
  一次交互完成，实际子进程输出和退出码同步收敛。
- 本机输入/resize/cursor、EOF、Ctrl-C 后继续和 TUI 投影四场景连续 50 轮共 200 次通过；扩大
  回归 `859 passed`，架构审计 `121 passed, 1 warning`，`compileall` 和 `git diff --check` 通过。
- Windows 运行时 PTY 已完成实机准出；POSIX/macOS adapter 与同契约测试已就绪，但当前 Windows
  主机不能替代 macOS 实机结论。未新增 CI，根目录清单继续保持未跟踪。

### Phase 10：PTY 生命周期最终收口

目标：消除长期运行中的 handle 累积，并让声明的会话期限不依赖下一次工具调用才生效。

- [x] 原生 handle 完成 `aclose()` 后从 capability owner 注销；重复关闭保持幂等。
- [x] 大量顺序创建并关闭 PTY 后，capability 注册表保持有界且最终为空。
- [x] manager 自主执行 timeout/idle 清理，不依赖 TUI 状态轮询、下一次工具调用或工作区关闭。
- [x] 生命周期判断使用单调时钟；面向展示的 epoch 时间继续保持现有字段契约。
- [x] reaper 由 manager 唯一拥有，启动惰性、唤醒可重算、关闭可等待，不遗留后台 task 警告。
- [x] 新会话、输入、输出和 resize 更新 idle deadline；总 timeout 不被活动延长。
- [x] 会话正常退出、被回收、启动失败和 manager close 均能唤醒并收束 reaper。

准出条件：公开 PTY 和 manager 定向测试覆盖顺序 100 个会话零注册表增长、无后续 API 调用的
timeout/idle 自动终止、墙钟跳变不影响截止时间、关闭无 pending task；连续 20 轮零失败后独立推送。

#### 阶段进展（本文件不入库）

- Phase 10 已完成 Windows ConPTY 实机验收。原生 handle 在关闭时从 owner 注销，顺序创建关闭
  100 个会话后注册表持续归零；manager 的惰性 reaper 使用单调时钟自主执行总超时与 idle 回收。
- 输入、resize 和持续输出均会续期 idle deadline，但不会延长总 timeout；manager close 可等待 reaper
  收束并拒绝后续启动。生命周期核心 6 场景连续 20 轮共 120 次通过。
- 自主 reaper 写入稳定终止原因，公开 `exec_command` 在进程已经被回收后仍返回
  `timed_out=true` 与 `command_timed_out`；结果判断不再二次依赖墙钟。
- 相关回归重跑 `127 passed`；底层 Ctrl-C 场景在首轮扩大回归发生一次 5 秒启动探测超时，随后
  单项连续 20 次及完整重跑均通过。架构审计 `121 passed, 1 warning`，`compileall` 与
  `git diff --check` 通过。

### Phase 11：Codex 等待与后台交互节奏

目标：对齐命令何时留在前台、何时返回后台 session，以及空轮询和真实输入的等待边界。

- [x] `exec_command` 默认等待改为 10000ms，有效范围统一为 250-30000ms。
- [x] Windows 初始等待强制下限 10000ms；POSIX 保留 250ms 下限。
- [x] `write_stdin` 默认真实输入等待 250ms，范围为 250-30000ms。
- [x] 空 stdin 且无 control 的后台轮询最少等待 5000ms，默认上限为 300000ms。
- [x] interrupt、EOF、resize、terminate、kill 控制不被空轮询下限强制延迟。
- [x] schema、执行参数规范化、JS 嵌套工具、approval/policy 指纹和测试期望同步更新。
- [x] TUI 等待投影仍 exactly-once，快速退出命令不被错误展示成后台会话。

准出条件：边界值和平台分支具备契约测试；真实 PTY 输入、Ctrl-C、后台轮询和 TUI 投影连续
20 轮零失败，相关工具/权限/TUI 回归通过后独立推送。

#### 阶段进展（本文件不入库）

- Phase 11 已按 `codex-main/codex-rs/core/src/unified_exec/mod.rs` 与
  `process_manager.rs` 的当前实现对齐。公共端口集中持有默认值和边界，schema、工作区运行时、
  原生执行器及 JS 嵌套 canonical arguments 使用同一契约。
- Windows 实测：请求 100ms 的首次等待被规范化为 10000ms；纯空轮询请求 0ms 被规范化为
  5000ms；输入与控制使用 250-30000ms。快速 PTY 命令退出后立即返回最终结果，不保留后台会话。
- 快速退出、后台空轮询/TUI、真实输入、Ctrl-C 后继续及 clamp 边界连续 20 轮共 80 次通过；
  合并相关回归 `284 passed`，架构审计 `121 passed, 1 warning`，`compileall` 与
  `git diff --check` 通过。

### Phase 12：外部 PTY 能力边界与最终结论

目标：准确区分本仓可完成项与必须由 sidecar 产物或目标平台提供的实机证据。

- [x] 复核 Codex unified exec 公共工具是否要求 sandbox resize；不扩大超过参考契约的模型工具语义。
- [x] sidecar 不支持 resize 时继续稳定返回 `exec_resize_unavailable`，不降级、不伪报成功。
- [x] Windows sandbox PTY 保持三路 `isatty=True`，输入、Ctrl-C、EOF 和退出回收可用。
- [x] POSIX/macOS 原生 adapter 的依赖、导入、controlling TTY、process group 与打包校验保持可执行。
- [x] macOS sidecar 产物缺失时明确阻止发布包，不以 skip 结果宣称实机通过。
- [x] 当前主机完成全部可执行门禁；macOS 实机作为唯一外部门禁单独记录。

准出条件：源码能力无未处理缺口，Windows 全链路复验通过；若没有 macOS 主机或 sidecar 产物，
最终结论必须写为“实现完成、macOS 实机待验”，不能写成双平台 M4 已完成。

#### 阶段进展（本文件不入库）

- Codex 当前统一执行工具未向模型暴露 sandbox resize；Mind 保持 sidecar resize 明确失败，不扩张
  参考工具契约。Windows sidecar PTY 的 interrupt 已改为终端 Ctrl-C 输入，EOF 改为终端 Ctrl-Z，
  普通 pipe 仍使用进程信号和协议 EOF。
- Windows sidecar 的三路 `isatty=True`、输入、resize 明确拒绝、Ctrl-C 后继续、EOF 后正文补写及
  正常退出三场景连续 20 轮共 60 次通过；sandbox、公开 PTY 与打包定向回归 `45 passed`。
- POSIX 依赖仍只在非 Windows 安装；macOS sidecar 缺失或不可执行会阻止构建。当前源码能力无已知
  未处理缺口，结论为“实现完成、Windows M4、macOS 实机待验”。
- 首轮全量在 nested-surfaces 验收中发现瞬态 stage 会被 `complete` 立即覆盖；增加跨进程 ack
  barrier 后该真实 TUI 场景连续 20 轮通过。最终全量 `4026 passed, 13 skipped, 1 warning`，
  架构审计 `121 passed, 1 warning`，`compileall` 和 `git diff --check` 通过。
- Phase 12 已提交并推送：`a1d423b4`；本清单继续保持未跟踪，不进入提交。

## 6. 必须覆盖的跨平台场景矩阵

| 场景                               | Windows | macOS | Raw bytes | Screen | 业务/持久事实 |
|------------------------------------|---------|-------|-----------|--------|---------------|
| 启动、探色、首帧、正常退出         | [ ]     | [ ]   | [ ]       | [ ]    | [ ]           |
| Unicode、Enter、Backspace、paste   | [ ]     | [ ]   | [ ]       | [ ]    | [ ]           |
| 空闲 Tab/Enter                     | [ ]     | [ ]   | [ ]       | [ ]    | [ ]           |
| 活动 Turn：Enter steer             | [ ]     | [ ]   | [ ]       | [ ]    | [ ]           |
| 活动 Turn：Tab queue               | [ ]     | [ ]   | [ ]       | [ ]    | [ ]           |
| 草稿/活动 Turn/二次退出 Ctrl-C     | [ ]     | [ ]   | [ ]       | [ ]    | [ ]           |
| pending/rejected/queued 恢复顺序   | [ ]     | [ ]   | [ ]       | [ ]    | [ ]           |
| focus 与探测响应交错               | [ ]     | [ ]   | [ ]       | [ ]    | 不适用        |
| resize during stream/approval/tool | [ ]     | [ ]   | [ ]       | [ ]    | [ ]           |
| Provider 断流与 attempt supersede  | [ ]     | [ ]   | [ ]       | [ ]    | [ ]           |
| 16/256/truecolor/no-color          | [ ]     | [ ]   | [ ]       | [ ]    | 不适用        |
| interrupting 退出恢复              | [ ]     | [ ]   | [ ]       | [ ]    | [ ]           |
| finalizing 退出恢复                | [ ]     | [ ]   | [ ]       | [ ]    | [ ]           |
| 后代进程、强杀和资源回收           | [ ]     | [ ]   | [ ]       | 不适用 | [ ]           |

## 7. 总体准出条件

全部满足后，才可声明“PTY/TUI 对齐完成”：

- [ ] Windows 与 macOS 使用真实原生 PTY/ConPTY，不使用普通 pipe 冒充。
- [ ] 第 3 节交互基线全部通过真实按键测试，Enter 与 Tab 在 Ctrl-C 前后的所有权不混淆。
- [ ] TUI 断言同时覆盖 raw output、最终 Screen 和业务事实，三者可相互定位失败。
- [ ] resize、focus、paste、断流重试和 cold-resume 有跨进程证据。
- [ ] 颜色在无色、16、256、truecolor 与深浅主题下都可读且无样式泄漏。
- [ ] 每条退出路径回收根进程与后代进程，并恢复终端模式、光标和样式。
- [ ] 所有状态测试使用独立 `.cache/pty-runs`，恢复场景只复用自己的状态根。
- [ ] 失败保留完整且脱敏的证据；成功不遗留 SQLite、日志或进程。
- [ ] Windows 与 macOS 本机连续 50 次无 flaky、无进程泄漏。
- [ ] 稳定文档只记录最终契约，不记录临时兼容分支或迁移流水账。

## 8. 推荐提交拆分

1. **已完成：验收基础设施与现有 TUI 行为**

   Phase 0-4 已建立原生 PTY 驱动、Screen oracle、输入交互和渲染证据。
2. **提交 A：生产 PTY capability**

   Phase 5。新增独立端口、平台 adapter、组合注入和生产 adapter 测试。
3. **已完成：统一执行接入**

   Phase 6。接入工具 schema、策略、`ProcessSessionManager`、输出 cursor 与交互控制。
4. **已完成：资源边界**

   Phase 7。补齐统一会话上限、有界输出、cursor reset 与取消后的后台续接。
5. **提交 C：平台能力证明**

   Phase 8。从公开工具入口验证 native/sandbox PTY 事实和明确能力差异。
6. **最终门禁：运行时 PTY 验收**

   Phase 9。验证公开交互链、TUI 工具状态投影和本机稳定性，不增加 CI。

每个提交必须能独立回退；不得在同一提交同时引入 PTY 驱动、重写 Turn 状态机和重构 TUI renderer。

## 9. 源码证据

### Codex PTY 工程

- PTY 公共契约与导出：[`codex-main/codex-rs/utils/pty/src/lib.rs`](codex-main/codex-rs/utils/pty/src/lib.rs#L20)
- Windows ConPTY 与 POSIX native PTY 选择：[`pty.rs`](codex-main/codex-rs/utils/pty/src/pty.rs#L114)
- 精确环境、PTY pair、I/O channel 与 wait：[`pty.rs`](codex-main/codex-rs/utils/pty/src/pty.rs#L128)
- resize、关闭 stdin、terminate 与 Drop：[`process.rs`](codex-main/codex-rs/utils/pty/src/process.rs#L110)
- POSIX 非阻塞 I/O 与取消安全：[`unix_io.rs`](codex-main/codex-rs/utils/pty/src/unix_io.rs#L20)
- Windows ConPTY 实现：[`conpty.rs`](codex-main/codex-rs/utils/pty/src/win/conpty.rs#L40)
- Windows Job Object 进程树所有权：[`job.rs`](codex-main/codex-rs/utils/pty/src/win/job.rs#L34)
- Windows 输入归一化：[`windows_input.rs`](codex-main/codex-rs/utils/pty/src/windows_input.rs#L1)
- Windows Unicode、Enter、Backspace、Ctrl-C：[`windows_tests.rs`](codex-main/codex-rs/utils/pty/src/windows_tests.rs#L303)
- 跨平台 PTY、排空、EOF、reap、后代与 resize 测试：[`tests.rs`](codex-main/codex-rs/utils/pty/src/tests.rs#L343)

### Codex TUI、终端协商与颜色

- 真实 TUI PTY、OSC 响应、focus 与输入不丢失：[`focus_palette.rs`](codex-main/codex-rs/tui/tests/suite/focus_palette.rs#L24)
- 当前真实 TUI PTY suite 的 Unix 限定：[`suite/mod.rs`](codex-main/codex-rs/tui/tests/suite/mod.rs#L1)
- 终端查询截止时间、字节上限与输入回放：[`terminal_probe.rs`](codex-main/codex-rs/tui/src/terminal_probe.rs#L1)
- 色深、默认颜色与进程级缓存：[`terminal_palette.rs`](codex-main/codex-rs/tui/src/terminal_palette.rs#L6)
- focus 与已排队按键共存：[`event_stream.rs`](codex-main/codex-rs/tui/src/tui/event_stream.rs#L438)
- scrollback/history 保留：[`scrollback_tests.rs`](codex-main/codex-rs/tui/src/tui/scrollback_tests.rs#L54)
- Enter 提交与 Tab 显式队列分型：[`input_flow.rs`](codex-main/codex-rs/tui/src/chatwidget/input_flow.rs#L15)
- 活动 Turn 输入登记为 pending steer：[`input_submission.rs`](codex-main/codex-rs/tui/src/chatwidget/input_submission.rs#L328)
- 中断后的 pending steer/queued 恢复：[`input_restore.rs`](codex-main/codex-rs/tui/src/chatwidget/input_restore.rs#L228)
- 执行中 Tab 队列与 completion 优先级测试：[`chat_composer.rs`](codex-main/codex-rs/tui/src/bottom_pane/chat_composer.rs#L9875)

### ProxyMind 当前基础

- 平台差异、依赖边界与 TUI 能力快照：[`ARCHITECTURE.md`](ARCHITECTURE.md#L20)
- Enter、Tab、补全和 bracketed paste 路由：[`frontends/tui/core/input.py`](frontends/tui/core/input.py#L1210)
- Ctrl-C 草稿、中断与二次退出优先级：[`frontends/tui/core/submission.py`](frontends/tui/core/submission.py#L610)
- Enter steer 与 Tab queue-only 分流：[`frontends/tui/session/turn_input.py`](frontends/tui/session/turn_input.py#L257)
- 终端能力不可变快照与探测缓存：[`frontends/terminal/capabilities.py`](frontends/terminal/capabilities.py#L67)
- Unix OSC 探测、字节上限和用户输入回放：[`frontends/terminal/probe_unix.py`](frontends/terminal/probe_unix.py#L17)
- 色深与终端身份：[`frontends/terminal/color_support.py`](frontends/terminal/color_support.py#L16)
- 语义颜色解析：[`frontends/terminal/semantic_styles.py`](frontends/terminal/semantic_styles.py)
- 现有 pipe 型进程契约：[`agent/ports/capabilities.py`](agent/ports/capabilities.py#L286)
- 现有 asyncio pipe 实现：[`infrastructure/platform/process_sessions.py`](infrastructure/platform/process_sessions.py#L299)
- 子进程状态根传播：[`infrastructure/config/runtime_paths.py`](infrastructure/config/runtime_paths.py#L193)
- TUI 帧、resize、scrollback 与流式布局回归：[`tests/test_tui_spacing.py`](tests/test_tui_spacing.py)
- 终端能力与语义样式回归：[`tests/test_terminal_capabilities.py`](tests/test_terminal_capabilities.py)

## 10. 实施前最后确认

已收到实施指令。Phase 0-4 的测试基础已完成；从 Phase 5 开始，第一优先级是生产 PTY
capability，而不是继续扩写测试目录。每阶段先通过准出，再更新本清单、提交并推送。
