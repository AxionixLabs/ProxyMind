# Hook 架构与能力对齐方案

- 计划版本：V1
- 状态：基线评估完成，待进入迭代 1
- 基线日期：2026-09-02
- Codex 源码基线：工作区 `codex-main` 快照；实施迭代 1 时必须补录并校验准确 revision
- 目标平台：Windows、macOS、Linux
- 架构权威：`ARCHITECTURE.md`
- 配置与用户语义说明：`docs/hooks.md`

参考实现：

- `codex-main/codex-rs/config/src/hook_config.rs`
- `codex-main/codex-rs/hooks/src/registry.rs`
- `codex-main/codex-rs/hooks/src/engine/discovery.rs`
- `codex-main/codex-rs/hooks/src/engine/dispatcher.rs`
- `codex-main/codex-rs/hooks/src/engine/command_runner.rs`
- `codex-main/codex-rs/hooks/src/engine/mcp_runner.rs`
- `codex-main/codex-rs/hooks/src/events`
- `codex-main/codex-rs/hooks/src/schema.rs`
- `codex-main/codex-rs/hooks/schema/generated`
- `codex-main/codex-rs/protocol/src/protocol.rs`

## 目标与结论

本计划对齐的是固定 Codex 基线下可观察的 Hook 契约、执行能力、生命周期和管理状态，
不是把 Rust 模块逐文件翻译为 Python，也不是为了显示相同菜单而保留不能执行的声明。

实施顺序固定为：

```text
冻结上游契约与本地差距
  -> 收紧可执行类型和配置边界
  -> 补齐 Interrupt
  -> 接入真实 MCP Hook 执行
  -> 接入异步命令与任务所有权
  -> 收口状态、兼容层和失败语义
  -> 三平台与 CI 验收
```

完成本计划后可以声明：

1. 12 个 Codex Hook 事件在 ProxyMind 中都有明确的配置、输入、执行和生命周期入口；
2. `command` 与 `mcp_tool` 是真实可执行处理器，`prompt` 与 `agent` 不伪装成已安装能力；
3. 同步、异步、取消、关闭、输出溢写和状态投影具有确定行为；
4. 保留的兼容名称都有当前上游契约或明确产品需求，其他兼容层和动态回退已经删除；
5. 对齐结论由固定基线、生成清单、契约测试和三平台执行证据共同支持。

不能据此声明 ProxyMind 与 Codex 的内部实现、transcript 格式、插件系统或全部企业配置
来源完全相同。

## 当前基线

### 已经对齐的部分

- Hook 已按 `agent.domain`、`agent.application`、`agent.harness`、`agent.ports`、
  `infrastructure` 和 `frontends` 分层，没有依赖退役源码包或 `backend`。
- 已实现 PreToolUse、PermissionRequest、PostToolUse、PreCompact、PostCompact、
  SessionStart、SessionEnd、UserPromptSubmit、SubagentStart、SubagentStop 和 Stop。
- 命令 Hook 已覆盖 JSON stdin、事件化输出、退出码 2、超时、并发匹配、取消、状态展示、
  大输出溢写和会话清理。
- matcher 同时支持本地 canonical 工具名和 `Bash`、`Edit`、`Write`、`Agent` 上游名称，
  且一个 handler 不会因多个别名重复执行。
- 用户、profile、project、CLI 和独立 `hooks.json` 已进入统一配置解析与内容哈希信任流程。
- 现有 Hook 定向测试基线为 190 个用例通过。

### 尚未对齐的部分

| 维度 | Codex 基线 | ProxyMind 当前状态 | 结论 |
| --- | --- | --- | --- |
| 事件全集 | 12 个，包含 `Interrupt` | 11 个 | 缺失能力 |
| `command` 同步执行 | 支持 | 支持 | 基本对齐 |
| `command` 异步执行 | 后台调度、完成通知、关闭收束 | discovery 阶段跳过 | 缺失能力 |
| `mcp_tool` | 支持 `server`、`tool`、`input` 并真实调用 | 可解析、可展示，但不执行且缺 `input` | 伪支持 |
| `prompt` / `agent` | 识别后在 discovery 阶段警告并跳过 | 保留为 installed/active 管理项 | 伪目录项 |
| Interrupt 输出 | 只接受 `systemMessage`，短超时 | 无 schema、runtime 和调用点 | 缺失能力 |
| 运行摘要 | handler、mode、scope、source、order、状态和输出 | 缺少多项身份和执行元数据 | 契约不完整 |
| 配置失败 | 无效可选项局部跳过；组合错误不伪装为空运行时 | 整体 scope 解析异常可退回 empty | 失败边界过宽 |
| 输出模型 | wire 输出与内部 effect 分离 | 同时维护 effect 和兼容 snake_case 字典 | 重复状态 |
| schema 锁定 | 类型生成并提交 JSON Schema fixture | 手写 schema，仅校验本地目录自洽 | 无上游漂移检测 |

当前测试会主动断言“异步 Hook 被跳过”和“非 command Hook 只进入目录不进入运行时”。
因此现有测试全绿只能证明当前行为稳定，不能作为 Codex 能力等价证据。

## 对齐边界

### 必须行为等价

- 事件名称、稳定顺序、matcher 是否生效和 matcher 输入值；
- 各事件 command stdin 的字段、必填性、可空性和枚举；
- 各事件 stdout 的允许字段、保留字段、非法组合和聚合优先级；
- 退出码、stderr、超时、同步/异步控制效果和失败后是否继续；
- `commandWindows`、`command_windows` 和平台 shell 选择；
- `mcp_tool` 的静态 input 与事件 input 合并、调用、超时和结果解析；
- Hook key、内容哈希、enabled、trusted/modified/untrusted/managed 状态；
- started/completed、handler type、execution mode、scope、source、display order 和输出条目；
- Session、Turn、Subagent、Tool、Compact、Interrupt 和进程关闭时的触发与清理语义。

### 明确保留的产品差异

- ProxyMind 使用自身应用目录、`.mind` 项目目录和配置层，不改名为 `.codex`。
- transcript 文件内容仍是 ProxyMind 契约，只对齐 Hook 输入需要的路径、刷新时机和生命周期，
  不复制 Codex rollout 私有格式。
- Hook 展示继续通过中立 PresentationView 接入现有前端，不复制 Rust app-server 内部通道。
- 只有真实存在的 managed 或 plugin 来源接入后才扩展来源枚举；不为了菜单字段预建空实现。
- 本计划不引入 Codex 的 legacy `notify` 路径。

### 需要保留的上游兼容契约

以下内容在固定 Codex 基线中仍是有效契约，不属于待清理兼容层：

- `commandWindows` 是规范 JSON 字段，`command_windows` 是上游仍接受的 alias；
- `Bash`、`Edit`、`Write`、`Agent` 是 Hook matcher 和 stdin 使用的工具名称映射；
- `SessionEnd.reason = "other"`、compact trigger 的 `manual/auto`、SessionStart source 的
  `startup/resume/clear/compact`；
- PermissionRequest 和 PostToolUse 中尚未实现但需要 fail closed 的上游保留字段。

这些契约必须集中在配置或协议边界，不得扩散成内部双字段、双状态或多条执行路径。

## 能力决定

| 能力 | 决定 |
| --- | --- |
| `Interrupt` | 纳入事件全集并接到 Root Turn 中断确认边界；Subagent 中断不触发 Root Interrupt Hook |
| `mcp_tool` | 完整实现，不再以 warning 代替执行；配置支持 `input` |
| `prompt` / `agent` | 仅在 discovery 边界识别并产生“不支持”告警，不创建定义、信任状态或目录项 |
| 异步 command | 完整实现后台任务、并发上限、状态发布、配置刷新存续和关闭收束 |
| `replacementResult` | 默认删除；它不属于固定 Codex wire 契约，也没有独立产品命名空间和所有权 |
| `updatedMCPToolOutput` | 保持上游保留字段并 fail closed，直到 Codex 基线正式实现其语义 |
| legacy `notify` | 不引入 |
| managed/plugin hooks | 不预建 facade；出现真实产品来源时按同一 discovery/registry 端口接入 |
| fail-open scope | 删除整体 empty fallback；无效可选 handler 局部告警，组合和类型错误显式失败 |

若产品决定继续提供结果替换能力，必须先把它定义为显式 ProxyMind 扩展，例如独立命名空间、
版本、支持事件和安全语义，并增加与 Codex 模式互斥的配置。不能继续把
`replacementResult` 放在上游 command output 顶层并宣称协议等价。

## 目标架构

### 职责链

```text
config.toml / hooks.json
  -> infrastructure Hook discovery 与边界校验
  -> executable HookDefinition（command | mcp_tool）
  -> domain matcher、trust 和纯状态规则
  -> HookRegistry 构建固定 Session/Turn snapshot
  -> event owner 构造具名 HookRequest
  -> HookRuntime 选择 handler、执行模式和控制能力
       -> HookCommandRunner -> platform process
       -> HookMcpRunner -> existing MCP session adapter
       -> HookAsyncTaskOwner -> background task lifecycle
  -> event-specific output parser / aggregator
  -> typed outcome 返回 Tool/Turn/Session owner
  -> HookRunSummary -> PresentationView / transcript
```

### 架构归属

| 位置 | 职责 |
| --- | --- |
| `agent/domain/hooks.py` | 事件、可执行 handler 判别联合、信任值和稳定纯规则 |
| `agent/domain/hook_matching.py` | matcher 编译、canonical 名称和上游名称映射 |
| `agent/application/hooks/protocol.py` | 具名输入输出契约、边界验证和 wire/effect 转换 |
| `agent/application/hooks/models.py` | typed request、handler result、event outcome 和运行摘要 |
| `agent/application/hooks/catalog.py` | 只投影真实发现的 handler，不计算执行状态 |
| `agent/harness/hooks/runtime.py` | handler 分发、并发聚合、控制效果和状态发布 |
| `agent/harness/hooks/registry.py` | 配置快照、trust 激活、runner 注入和运行时构建 |
| `agent/harness/hooks/*_lifecycle.py` | Turn、Tool、Subagent、Compact、Session、Interrupt 触发顺序 |
| `agent/harness/hooks/async_tasks.py` | 异步 Hook 任务、并发上限、配置刷新存续和最终 join/cancel |
| `agent/ports/hooks.py` | command、MCP、状态、spill 和 task owner 的最小准确端口 |
| `infrastructure/hooks/discovery.py` | JSON/TOML 解析、局部 warning、key/hash 和来源映射 |
| `infrastructure/platform/hook_command.py` | 三平台命令创建、stdin/stdout/stderr、超时和进程终止 |
| `infrastructure/mcp` | Hook MCP adapter，复用现有 session，但不递归进入 Tool Hook 链 |
| `infrastructure/config/hooks.py` | 配置快照、信任/启用更新和工作区解析 |
| `frontends` | 目录、信任操作、运行状态和输出展示，不决定 Hook 语义 |
| `mind.py` / `composition.py` | 选择具体 runner，注入同一 MCP owner，声明关闭顺序 |

### 状态所有权

| 状态 | 所有者 | 生命周期 |
| --- | --- | --- |
| 原始配置与 warning | configuration/discovery | 每次配置解析 |
| key、hash、enabled、trust | HookRegistry 输入快照 | 配置版本 |
| 匹配后的 handler 集合 | HookRuntime | 固定 Session/Turn snapshot |
| 同步 handler 任务 | HookRuntime | 单次事件分发 |
| 异步 handler 任务 | HookAsyncTaskOwner | Session/进程，允许跨 Turn |
| command 子进程 | HookCommandRunner | 单次 handler 或后台任务 |
| MCP 连接 | 既有 McpRuntimeOwner | 进程/前端会话 |
| Hook MCP 调用 | HookMcpRunner adapter | 单次 handler |
| spill 文件 | Hook output store | Session，SessionEnd 后清理 |
| Hook 运行摘要 | Hook status/presentation 通道 | started 到 completed |

HookRuntime 不创建进程、MCP session、配置文件或 UI；MCP adapter 不重新执行
PreToolUse/PermissionRequest/PostToolUse，避免 Hook 调用自身形成递归。

## 类型与协议契约

### Handler 类型

生产运行时只接收可执行判别联合：

```text
CommandHookHandler(command, command_windows, timeout, async, ...)
McpToolHookHandler(server, tool, input, timeout, ...)
```

- 不再用一个 dataclass 加大量 `None` 字段表示所有 handler。
- `prompt` 和 `agent` 只存在于 infrastructure 的原始配置识别分支；它们不能进入 domain
  executable definition、catalog、trust 或 runtime。
- command runner 和 MCP runner 返回各自的具名结果；runtime 不使用 `getattr`、`hasattr`、
  `Any` 扩大或默认字段猜测。
- `HookRegistry` 的生产构造必须注入全部必需 runner。显式 empty runtime 可以没有 handler，
  但不能携带一个等待运行时报错的“未配置 runner”。

### 事件契约

稳定事件顺序为：

```text
PreToolUse
PermissionRequest
PostToolUse
PreCompact
PostCompact
SessionStart
SessionEnd
UserPromptSubmit
SubagentStart
SubagentStop
Stop
Interrupt
```

事件 scope 与 Codex 基线保持一致：

- Thread：SessionStart、SessionEnd、SubagentStart；
- Turn：PreToolUse、PermissionRequest、PostToolUse、PreCompact、PostCompact、
  UserPromptSubmit、SubagentStop、Stop、Interrupt。

Interrupt 是用户或协议已确认中断 Root Turn 后的 best-effort 通知事件：先刷新 transcript，
再并发执行匹配 Hook；其输出只产生 warning/error，不允许阻止或恢复已经发生的中断。
默认超时 1 秒，最大 3 秒。应用关闭导致的普通资源取消不能冒充用户 Interrupt。

### MCP Hook 契约

- 静态 `input` 必须是 JSON object，并在配置边界验证为可稳定哈希的 JSON 值。
- 调用参数由静态 `input` 与当前事件输入组成，合并优先级和 metadata 形状锁定为固定基线。
- server 不存在、tool 不存在、MCP 未启动、超时、协议错误和无效结果都形成 failed Hook run；
  不把 handler 从 active count 中静默删除。
- SessionEnd 不支持 MCP Hook 时在 discovery 阶段跳过并告警，与固定基线一致。
- Hook MCP 调用复用已有 McpRuntimeOwner 连接和关闭顺序，但使用专用 HookMcpRunner 端口；
  不绕到模型工具协调器，也不触发审批或第二轮 Hook。

### 异步 Hook 契约

- 仅 command handler 可以异步；MCP Hook 保持同步。
- 异步 handler 立即返回主流程且不能应用 allow/deny/block/updatedInput 等控制效果。
- 后台完成后仍发布 completed/failed 摘要；只保留 warning、context 和 error 输出。
- 配置刷新不能取消已经开始的后台任务，新事件使用新快照。
- SessionEnd 即使配置 `async = true` 也同步执行并产生 warning。
- Session 关闭先停止接受新任务，再等待或按明确超时取消；进程关闭必须 join 所有任务并终止
  残留子进程。

### 运行摘要

`HookRunSummary` 至少包含：

- `id`、`hook_key`、`event_name`；
- `handler_type`、`execution_mode`、`scope`；
- `source_path`、`source`、`display_order`；
- `status`、`status_message`、`started_at`、`completed_at`、`duration_ms`；
- `entries`。

application view 可以裁剪字段，但 domain/runtime 的事实不能因当前 TUI 未展示而丢失。

## 同次删除清单

| 当前结构 | 处理方式 | 删除时机 |
| --- | --- | --- |
| 非 command handler 进入 catalog、随后 registry 过滤 | MCP 改为真实 runner；prompt/agent 在 discovery 跳过 | 迭代 1、3 |
| runtime 与 registry 各自的 `_UnconfiguredHookCommandRunner` | 生产构造强制注入；empty runtime 不持有 runner | 迭代 1 |
| `getattr(result, ..., default)` | 改为准确 Protocol/具名结果的直接字段访问 | 迭代 1 |
| `HookDispatcherPort.for_turn` 与真实实现职责不符 | 从 dispatcher 端口删除，保留在 execution scope | 迭代 1 |
| scope provider 异常后整体返回 empty | 局部配置错误转 warning；组合/类型错误显式传播 | 迭代 1 |
| `_normalized_output` 复制内部 snake_case 字段 | 消费方只读取 typed effect；删除重复 output 状态 | 迭代 5 |
| 顶层 `replacementResult` | 删除 schema、normalizer、result apply、文档和测试 | 迭代 5 |
| “支持所有 Codex handler type”的误导测试命名 | 改为 executable/unsupported discovery 契约测试 | 迭代 1 |
| “异步 Hook 被跳过”的现状测试 | 替换为后台执行、控制效果忽略和关闭收束测试 | 迭代 4 |
| `.mind/hooks.json` 只有 11 个事件 | 增加 Interrupt，并由清单生成/校验 | 迭代 2、6 |

迁移中不保留旧字段别名、双写 effect、旧 runner 入口或“以后删除”的 facade。某项新路径合入
时，表中对应旧路径必须在同一次改造删除。

## 迭代计划

### 迭代 1：冻结契约并收紧运行边界

**交付用例**：固定 Codex revision、事件/handler/status 清单和 schema fixture；生产运行时只接收
真实可执行 handler，组合错误不再退回空 Hook 系统。

实施范围：

- 记录 `codex-main` 的准确 revision、上游文件树摘要和 12 事件清单。
- 增加 checked-in Hook contract manifest，记录事件、scope、matcher、handler 和 schema 摘要。
- 增加 `scripts/check_hook_alignment.py`，只比较结构化清单，不用正则解析 Rust 源码。
- 拆分 command 与 MCP handler 具名类型，收紧 Hook runner result。
- prompt/agent 在 discovery 阶段告警并跳过，不进入 catalog 或 trust。
- 删除两个 unconfigured runner、动态 `getattr`、错误的 dispatcher 端口方法和整体 empty fallback。
- 保留合法 handler 的局部容错；配置文件级 JSON/类型错误继续形成来源化 warning。

出口证据：

- manifest 与固定上游 fixture 一致，任意删除 Interrupt 或改变 schema 都会使检查失败。
- 生产 HookRegistry 缺少 runner 时在组合阶段失败。
- prompt/agent 不计入 installed/active/review 数量。
- handler result 缺失字段由类型/边界测试拒绝，不被默认值吞掉。
- 既有 11 事件 command 主流程保持通过。

### 迭代 2：补齐 Interrupt 生命周期

**交付用例**：Root Turn 被用户或协议确认中断时，使用该 Turn 的固定 Hook snapshot 分发一次
Interrupt，并发布 started/completed 状态。

实施范围：

- 扩展事件目录、输入/输出 schema、event spec、catalog 和 `.mind/hooks.json`。
- 在 Root Turn 中断确认后的 Harness 生命周期点接入 Interrupt，不由 frontend 直接 dispatch。
- 中断前刷新/提交 transcript；刷新失败记录 warning，但仍尝试 Hook。
- 对齐无 matcher、只允许 `systemMessage`、1 秒默认/3 秒最大超时。
- 区分用户中断、协议中断、应用关闭、队列 cancel 和 Subagent interrupt。

出口证据：

- TUI、CLI、Subscription/MCP 入站的 Root Turn 中断各触发一次。
- 无活动 Turn、重复确认、应用关闭和 Subagent 中断不误触发。
- 非 JSON stdout、非零退出、超时和取消形成确定 failed/stopped 状态。
- 12 事件目录、schema、文档和测试 Hook 配置由同一清单验证。

### 迭代 3：实现 MCP Hook 执行

**交付用例**：受信任的 `mcp_tool` handler 通过现有 MCP runtime 调用指定 server/tool，返回值按
当前事件输出契约参与聚合。

实施范围：

- 配置加入 `input` 并进入 key/hash；拒绝非 object 和不可稳定序列化值。
- 定义 HookMcpRunner 端口和具名结果；实现 infrastructure MCP adapter。
- 复用 McpRuntimeOwner 当前连接，建立 server/tool 精确查找和超时。
- 固定静态 input、事件 input 和 metadata 的合并规则。
- 阻止 Hook MCP 调用递归进入工具 Hook、普通工具审批和模型工具展示。
- 删除 registry 对 `mcp_tool` 的过滤与“不支持” warning。

出口证据：

- fake MCP server 覆盖成功、server/tool 缺失、超时、协议错误和无效输出。
- command 与 MCP handler 在同一事件中并发，聚合顺序与配置/完成顺序契约一致。
- MCP Hook 出现在 active count、运行摘要和 TUI 详情中，实际调用次数可证明。
- SessionEnd MCP handler 在 discovery 阶段跳过并告警。
- MCP restart、配置刷新和应用关闭无重复 session owner 或悬挂调用。

### 迭代 4：实现异步命令与任务所有权

**交付用例**：`async = true` 的 command Hook 不阻塞主流程，在后台完成并正确发布状态；关闭时
所有任务和子进程收敛。

实施范围：

- 在 Harness 建立 HookAsyncTaskOwner，而不是在 platform runner 中创建无主任务。
- 限制后台并发数并定义超额排队策略。
- 异步结果过滤控制效果，只保留 context、warning 和 error。
- 配置刷新保留在途任务，新任务使用新 snapshot。
- 将 task owner 纳入 Session/进程关闭顺序和取消保护。
- 删除 discovery 对异步 command 的跳过逻辑和 runtime 的 unsupported no-op 分支。

出口证据：

- 主操作不会等待后台 handler；后台实际执行且最终产生 completed/failed。
- 异步 deny/block/updatedInput 不改变已经继续的主流程。
- 并发上限、配置刷新、SessionEnd、session close、process close 和调用方取消矩阵通过。
- Windows、macOS、Linux 都证明后台 shell 进程能被正确 join 或 terminate。

### 迭代 5：状态契约与兼容层收口

**交付用例**：Hook runtime、目录和展示共享一套真实状态；wire 输出只转换一次，内部不再维护
兼容字典和未声明扩展。

实施范围：

- 补齐 HookRunSummary 的 handler/mode/scope/source/order 字段并更新 presentation projection。
- 区分 installed、enabled、trusted、active、running；不允许目录 active 与 runtime active 不同。
- consumer 全部改读 HookOutputEffect/事件具名 outcome，删除 `_normalized_output` 双写。
- 删除 `replacementResult` 及其 PostToolUse 应用路径；保留 `updatedMCPToolOutput` fail-closed。
- 复核 HookManager、HookRegistry、HookExecutionScope 和资源回调；只保留有状态所有权或真实边界
  的类型。
- 收紧 package `__all__`，不因测试扩大生产公开面。

出口证据：

- catalog、runtime status、started/completed 和 transcript 对同一 Hook 的身份与状态一致。
- 不再存在 Hook result 的动态属性读取、snake_case 兼容副本或 replacement 扩展。
- 架构测试证明 Harness 不导入 concrete MCP/platform/config/frontend。
- HookManager 仍承担配置快照、信任更新和资源生命周期，不退化为转发 facade。

### 迭代 6：跨平台、CI 与发布验收

**交付用例**：固定 Codex 基线下的 12 事件、两类可执行 handler、同步/异步和三平台命令行为
都有自动化证据，文档不再声明未实现能力。

实施范围：

- 更新 `docs/hooks.md`、测试 Hook 配置和维护者说明。
- 将 contract checker、定向 Hook 测试、架构守卫和语法检查接入 PR CI。
- Windows、macOS、Linux 使用相同测试 fixture 验证平台 shell、command override、timeout 和终止。
- 使用进程内 fake MCP，不依赖公网或个人 MCP 配置。
- 生成最终能力矩阵，逐项记录 equivalent、intentional difference、unsupported 或 blocked。
- 复核并删除所有仅被旧测试使用的生产 API。

出口证据：

- 12 个事件每个至少有输入、输出、触发和失败路径测试。
- command/mcp、sync/async、Root/Subagent、Session/Turn scope 组合矩阵通过。
- 三平台命令 Hook 集成测试通过，MCP 契约测试在至少 Windows 和 Linux 通过。
- 全量 Python 测试、Hook contract checker、package architecture 和 `py_compile` 通过。
- `.mind/hooks.json` 的事件集合由 checker 验证，不能再次少于固定基线。
- 文档中的“已支持”只对应真实可执行能力。

## CI 验证矩阵

### 每个 PR 必跑

| 检查 | 目的 |
| --- | --- |
| Hook contract checker | 锁定 revision、12 事件、scope、handler 和 schema 摘要 |
| `tests/test_hook_protocol.py` | 输入输出结构和非法组合 |
| `tests/test_hook_trust.py` | hash、enabled 和 trust 状态 |
| `tests/test_hook_catalog.py` | discovery、目录和配置快照 |
| `tests/test_hooks.py` | runtime、生命周期、command/MCP、同步/异步和聚合 |
| `tests/test_tui_hooks.py` | 管理目录、信任和详情投影 |
| `tests/test_tui_hook_status.py` | started/completed 和输出展示 |
| `tests/test_package_architecture.py` | 依赖方向、公开面和禁用包 |
| `python -m py_compile` | 修改范围语法检查 |

### 平台矩阵

| 平台 | 必测内容 |
| --- | --- |
| Windows | `commandWindows` 优先级、带空格路径、COMSPEC、timeout、terminate、async close |
| macOS | `$SHELL -lc`、fallback shell、信号终止、async close |
| Linux | `$SHELL -lc`、`/bin/sh` fallback、信号终止、async close |
| Windows + Linux | fake MCP hook、超时、runtime restart、close |

CI 不直接依赖未提交的 `codex-main` 目录。更新基线时由维护脚本从指定 revision 生成结构化
fixture 并提交；普通 PR 只比较本地实现与该 fixture。生成脚本必须校验 revision 和输入文件
摘要，不能用易漂移的文本正则推断 Rust 类型。

## 验收矩阵

| 维度 | 值 |
| --- | --- |
| event | 12 个稳定事件 |
| handler | command、mcp_tool、prompt unsupported、agent unsupported |
| mode | sync、async、SessionEnd forced sync |
| scope | Thread、Turn、Root、Subagent |
| matcher | none、`*`、literal、alternation、regex、canonical/alias |
| trust | managed、untrusted、trusted、modified、disabled、bypass |
| result | completed、failed、blocked、stopped、cancelled in-flight |
| control | allow、deny、ask、block、continue false、updatedInput、additionalContext |
| output | empty、plain、JSON、invalid JSON、stderr、exit 2、large/spilled |
| lifecycle | startup、resume、clear、compact、tool、stop、interrupt、session end、close |
| concurrency | 多 handler、完成乱序、async limit、刷新、重复中断、关闭竞态 |
| MCP | success、missing server/tool、timeout、protocol error、restart、close、no recursion |
| platform | Windows、macOS、Linux |

## 不可接受的实现

- 只把 `Interrupt` 加到枚举和菜单，不接真实中断生命周期。
- 继续让 `mcp_tool` 出现在 active/installed 中但用 warning 代替调用。
- 为 MCP Hook 新建第二套 MCP session owner，或经普通工具路径造成 Hook/审批递归。
- 用 `create_task` 启动无所有者后台 Hook，不记录任务、不限制并发、不在关闭时 join。
- 让异步 Hook 的 deny/block/updatedInput 影响已经继续的主操作。
- 继续用一个包含大量可空字段的 handler 类型或动态属性猜测 runner 结果。
- 组合依赖缺失或 provider 类型错误时静默回退为空 Hook 系统。
- 为迁移保留 unconfigured runner、旧 output 字典、双字段、双写或一次转发 facade。
- 删除 `command_windows` 或工具别名并错误宣称是在清理兼容层。
- 引入 Codex legacy `notify`、退役 ProxyMind 包、`backend` 或 `schematic` 依赖。
- 直接正则解析 Rust 文件作为 CI 权威，或让 CI 依赖本地未提交的 `codex-main`。
- 只运行现有 190 个测试就声明对齐，不替换锁定缺失能力的现状测试。
- 三平台中任一平台尚未验证进程终止和异步关闭时声明完整对齐。

## 完成声明边界

只有迭代 1 至 6 的出口证据全部满足后，才可以声明：

1. ProxyMind Hook 架构符合本仓库 Agent Harness 的依赖方向和生命周期所有权；
2. 本计划列出的 Hook 可观察行为与固定 Codex 基线等价；
3. ProxyMind 保留的应用目录、transcript 和 presentation 差异都有明确边界；
4. 没有以兼容、warning、默认值或空实现伪装尚未实现的 Hook 能力。

managed/plugin 来源若尚无产品入口，应声明“未纳入”，不能声明全部 Codex Hook 产品能力
完全相同。未来 Codex 基线新增事件、handler 或输出字段时，先更新 manifest 和本计划，再决定
实现、明确不支持或升级基线；不得通过接受未知字段静默获得表面对齐。

## 计划维护

- 每次只把一个迭代标为进行中；完成时记录测试数量、平台和准确 Codex revision。
- 每个迭代开始前补充具体文件、删除项、失败路径和定向测试命令。
- 新路径与旧路径必须同次切换和删除，不建立无截止条件的兼容阶段。
- 更新 Codex 基线时重新生成 manifest/schema fixture，并人工复核行为语义而非只接受摘要变化。
- 发现职责归属与 `ARCHITECTURE.md` 冲突时先修订架构文档，不以局部测试通过覆盖依赖规则。
- `backend` 与 `schematic` 不属于本计划改动范围，任何迭代都不得修改。
