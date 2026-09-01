# Agent Harness 迁移计划

状态：阶段 0、阶段 1、阶段 2、阶段 3、阶段 4 已完成；阶段 5 进行中（2026-08-31）

本文件是 ProxyMind Agent Harness 迁移的唯一状态权威。它只保留当前决策、阶段准入与
出口、进行中的切片、风险和最新验证证据。已完成切片的逐项历史、旧测试数字和完整变更
记录见[迁移历史归档](AGENT_RUNTIME_MIGRATION_HISTORY.md)，归档不覆盖本文件的当前状态。

## 交接入口

接手一次改造时按以下顺序阅读：

1. 本文件的“当前状态”和“下一切片”。
2. [Agent Harness 架构基线](AGENT_RUNTIME_ARCHITECTURE.md)，确认职责边界与目标目录。
3. [导入图](AGENT_RUNTIME_IMPORT_GRAPH.md)，确认实际依赖方向。
4. [协议文档](services/llm/PROTOCOL.md)，确认线上 `mind.chat` 契约未被本地迁移改变。
5. [历史归档](AGENT_RUNTIME_MIGRATION_HISTORY.md)，只在需要审计某个已完成切片时查阅。

阶段状态只能在本文件更新。任何新切片都必须先写入“下一切片”和准入条件，完成后再
补充证据并移动到“最近完成”。不得用目录移动、单个测试通过或局部导入切换推断阶段
完成。

## 范围与不变量

- 本计划只管理 ProxyMind 客户端内部 Agent Harness 迁移，不改变 `backend/` 的边界。
- Agent Harness 是本地编排主线，正式包名为 `agent.harness`；不得重新创建
  `agent.runtime` 兼容 facade。
- `mind.chat` 是 TUI、桌面端和 Web 可共享的线上协议；本地 Harness 事件和线上 wire
  schema 分开拥有。`server/` 只是客户端内置配置服务，不拥有 Harness 状态。
- 架构判断先于需求实现：先确认状态所有权、依赖方向、生命周期和删除条件，再修改代码。
- 迁移必须按完整用例进行；同一变更中完成入站导入切换、旧路径删除、边界守卫和回归验证。
- 不新增只转发一次调用的 facade、未接入的空目录、未声明字段或长期兼容分支。
- 所有入口都通过组合根获得具体能力；控制器、前端和 application 不自行发现或构造
  基础设施实现。

## 目标架构

```text
mind.py                         # 稳定启动入口，只调用组合根
agent/                          # 本地 Agent Harness：domain/application/harness/ports
protocol/                       # 独立 mind.chat wire SDK，供多种前端复用
frontends/                      # CLI、TUI、MCP、Subscription 适配器（迁移期逐步落位）
infrastructure/                 # 配置、平台进程、Helix、持久化等具体实现
metadata/                       # 版本、编码和产品展示元数据
server/                         # 客户端内置配置服务，不拥有 Harness 状态
```

| 当前边界 | 责任 | 禁止事项 |
| --- | --- | --- |
| `agent/domain` | 纯领域值对象、规则和状态转移 | 依赖 IO、配置、UI 或历史包 |
| `agent/application` | 用例、Command、结果 projection 和生命周期无关的应用服务 | 直接导入 Harness、具体 store 或基础设施 |
| `agent/harness` | Session/Run/Agent 并发、接管、取消和关闭编排 | 回流 `agent.runtime` 或拥有线上协议状态 |
| `agent/ports` | 能力、Session、Workspace、工具和持久化的最小契约 | 为某个实现复制一套 facade |
| `agent/capabilities` | 本机进程、文件、环境、MCP、Helix 等副作用实现 | 读取前端状态或隐式发现配置 |
| `agent/stores` | Run、Transcript、Effect、Approval、Agent 等权威事实存储 | 读取基础设施路径或持有 UI 状态 |
| `agent/adapters` | Protocol Client、Canonical Item 和外部 Agent 交互适配 | 重新定义线上 wire schema |
| `protocol` | `schema`、`transport`、`client` 三层的 `mind.chat` SDK | 恢复 `protocol.requests` 等扁平兼容层 |
| `infrastructure` | 具体配置、平台、观测和外部资源实现 | 被领域层反向依赖 |

阶段 5 的最终删除目标仍是 `engine`、`mind_nova`、`mind_core`、`mind_app` 四个历史
包。删除前必须完成对应职责迁移、数据回读、启动/恢复验证、打包元数据切换和生产导入
图清零；不要求文件级一对一搬迁，但不允许保留无意义的旧包入口。

## 阶段总览

| 阶段 | 状态 | 目标 | 出口信号 |
| --- | --- | --- | --- |
| 0. 契约冻结 | 已完成 | 固定外部行为、依赖和风险基线 | 契约清单、导入图、基线矩阵齐备 |
| 1. Session 骨架 | 已完成 | 引入 Command/Event、Session 队列和单写者 | 主动 Turn 完成完整闭环 |
| 2. 持久化收束 | 已完成 | 收束事件、快照、效果、审批和 outbox | 强退后可恢复或进入对账 |
| 3. 能力解耦 | 已完成 | 模型、MCP、Helix、进程通过端口接入 | runtime 不直接拥有具体传输实现 |
| 4. 多入口与协议前端迁移 | 已完成 | 统一 Harness 语义并建立可复用 Protocol Client | CLI、TUI、MCP、Subscription 共用用例语义 |
| 5. 历史包退役 | 进行中 | 按职责迁移并删除四个历史包 | 生产导入图不再指向历史包，所有入口可启动/恢复 |

## 当前状态：阶段 5

### 最近完成

已完成 `agent/` 首轮职责化重组，并同步收敛两个跨层生命周期端口：

- `agent/application` 按 `agents/`、`turns/`、`hooks/`、`config/` 分组，
  `agent/harness` 按 `agents/`、`execution/`、`sessions/` 分组。
- `agent/stores` 按 `agents/`、`runs/`、`effects/`、`approvals/` 分组，
  `agent/adapters` 按 `protocol/`、`agents/` 分组；旧平铺模块已物理删除，未保留转发 facade。
- `agent/ports/sessions.py` 和 `agent/ports/workspace.py` 成为 Session/Workspace
  生命周期契约；application 通过 factory 使用 Harness，stores 只接收已解析路径。
- `agent.application` 包级导出已收窄为 `RuntimeServices`、`TurnApplication`、
  `SubmitTurnResult` 和 `submit_turn`；协议、端口、领域和内部值对象的消费者均改为
  从职责模块导入，并由架构守卫锁定公开面。
- Skills payload 转换器改为组合参数注入，`agent.composition` 不再导入
  `infrastructure`；`agent -> infrastructure -> agent` 跨边界循环已从导入图清除。
- Skills 文件读取在组合根创建 provider，runtime 只消费注入的 `SkillsProvider`；具体
  文件系统和 Harness 实现没有回流到 application。
- `AgentMessageEvent` 使用最小端口协议，避免 application 依赖具体 mailbox 存储。
- `ServerManageHelixCapability` 已迁移至 `infrastructure/services/helix_capability.py`；
  `mind_app.runtime.mcp.service_lifecycle` 仅保留 `ServiceRuntimeOwner`，具体 Helix
  生命周期适配器不再由历史运行时模块持有，旧导入路径已删除。
- `ExecPolicyManager` 及其本地执行审批值对象已迁移至
  `infrastructure/config/execution_policy_manager.py`；`mind_app.native_coding.exec`
  不再导出策略管理器，规则解析/发现与编码工具组件的职责边界已固定。
- `ProcessSessionManager`、`ProcessSession` 和 `ProcessSessionSpec` 已迁移至
  `infrastructure/platform/process_sessions.py`；完整权限 capability、受限 Sandbox
  sidecar、输出缓冲和回收状态不再由 `mind_app.native_coding.exec` 持有。
- `mind.py::create_native_coding` 成为 SandboxClient 和 ProcessSessionManager 的唯一生产
  装配点；`NativeCoding` 强制接收会话运行时，`coding_tools/default_registry` 强制接收
  工作区编码实例，不再通过空参数创建具体平台能力。
- `RootTurnCommandExecutor` 已迁移至 `agent/adapters/turns/root.py`，只依赖冻结命令、
  权限领域值和注入的 operation；CLI、MCP、Subscription 在各自入口绑定 controller，
  `mind_app.runtime.turns.root` 不再拥有 application 命令适配器。
- `run_foreground_turn` 已迁移至 `mind_app/presentation/terminal/turn_lifecycle.py`，
  动画、终端进度和清理由展示边界持有；runtime root 不再定义前端生命周期函数，TUI、
  CLI 和根轮次执行仍共享同一实现。
- 跨前端应用结果视图已迁移至 `agent/application/views/`，按 Run、工具、计划、补丁、
  审批、Hook 和进度语义拆分；`PresentationView`/`PresentationSink` 归
  `views/contracts.py`，纯文本原语继续归 `agent/ports/presentation.py`。旧
  `mind_app.presentation.models` 与 `contracts` 已物理删除，renderer、output、runtime
  和 TUI adapter 均改用新边界。
- 工具展示策略已拆分：`agent/domain/tool_policy.py` 持有工具过滤和审批专用判定，
  `agent/application/views/tool_display.py` 持有展示分类、阶段和状态文案；旧
  `mind_app.presentation.tool_policy` 已删除，runtime、renderer、TUI 和测试不再依赖旧路径。
- Subscription 的 Turn application 装配已改为显式 `TurnApplicationFactory`：组合根 `mind.py`
  负责绑定持久 application，`frontends/subscription/runtime.py` 不再通过宿主动态属性发现
  `runtime_services`，关闭时继续由订阅执行器回收 application。

### 最新证据

截至 2026-08-31，本切片已完成：

- 受影响行为回归：`2958 passed, 11 skipped`。
- 完整架构守卫：`75 passed, 51 warnings`。
- `agent_runtime_import_graph.py --write/--check` 通过，导入图已刷新。
- `compileall`、`git diff --check` 通过；旧平铺路径和旧导入扫描无结果。
- 组合根切片定向回归：`76 passed`；新增组合/架构守卫：`4 passed`。
- Helix 生命周期适配器定向回归：`6 passed`；完整架构守卫：`77 passed, 52 warnings`。
- Helix 适配器迁移后的导入图已重新生成并通过 `--check`；跨边界循环仍为零。
- 执行策略迁移全量行为回归：`3037 passed, 11 skipped, 52 warnings`；完整架构守卫仍为
  `77 passed, 52 warnings`。
- 策略迁移后的导入图、`compileall` 和 `git diff --check` 均通过；旧
  `mind_app.native_coding.exec.exec_policy` 文件及生产导入已清零。
- 进程会话迁移定向回归：`58 passed`；平台所有权守卫：`2 passed`；导入图、
  `compileall` 和 `git diff --check` 均通过，旧 `process_session` 文件和导入已清零。
- Sandbox 显式组合定向回归：`339 passed`；平台/组合所有权守卫：`2 passed`；导入图、
  `compileall` 和 `git diff --check` 均通过，NativeCoding 内部 Sandbox 构造已清零。
- 根轮次命令适配器三入口回归：`155 passed`；专项 adapter/application 守卫：`2 passed`；
  完整架构守卫：`78 passed, 52 warnings`；失败项修正后的边界专项：`5 passed`；导入图、
  `compileall` 和 `git diff --check` 均通过，旧 runtime 适配器定义和导入已清零。
- 终端轮次生命周期迁移回归：`108 passed`；生命周期归属架构守卫：`1 passed`；导入图、
  `compileall` 和 `git diff --check` 均通过，旧 runtime 生命周期函数定义已清零。
- Hook 命令执行器平台迁移回归：`87 passed`；平台归属与端口守卫通过；导入图、
  `compileall` 和 `git diff --check` 均通过，旧 `mind_app.runtime.hooks.command` 文件与导入已清零。
- Hook runtime/registry Harness 迁移回归：`413 passed`；完整架构守卫：`81 passed, 53 warnings`；
  Harness 边界、端口和旧路径守卫通过；未注入平台执行器时只返回明确配置错误，具体执行器
  由 `mind.py` 组合根注入。
- HookExecutionScope Harness 迁移回归：`308 passed, 1 warning`；完整架构守卫：`81 passed, 53
  warnings`；Scope 已切换到 `agent/harness/hooks`，application context 与 Harness 执行
  作用域边界由架构守卫锁定，旧 `mind_app.runtime.hooks.scope` 路径和导入已清零。
- MCP 生命周期所有者迁移回归：`36 passed`；MCP 所有权守卫 `3 passed`；`McpRuntimeOwner`
  已切换到 `agent/harness/mcp`，只依赖端口和显式工厂，旧
  `mind_app.runtime.mcp.lifecycle` 路径与导入已清零；取消关闭仍等待 stop 清理完成；
  完整架构守卫：`82 passed, 54 warnings`。
- 本地服务生命周期迁移回归：`44 passed`；`ServiceRuntimeOwner`、keepalive 和服务上下文
  已切换到 `infrastructure/services`，旧 `mind_app.runtime.mcp.service_lifecycle`、
  `keepalive` 路径与生产导入已清零；服务停止、重启、保活取消和关闭顺序守卫通过；完整
  架构守卫：`82 passed, 54 warnings`。
- 服务运行时 setup 迁移回归：`182 passed`；路径解析、PATH 注入、打包校验、Darwin 权限
  和资产缺失判断已切换到 `infrastructure/services/runtime_setup.py`，旧 runtime helper
  定义与生产导入已清零；setup 架构守卫通过；完整架构守卫：`83 passed, 55 warnings`。
- Subscription 前端迁移回归：订阅 open/resume、WS 断线恢复、收件箱转发、取消和关闭
  定向回归 `88 passed`；Subscription 前端归属守卫与 Harness owner/port 物理布局守卫通过；
  旧 `mind_app/subscription`、`mind_app/runtime/agent` 源文件和生产导入已清零。
- 本切片全仓行为回归：`3047 passed, 11 skipped, 55 warnings`；完整架构守卫随全仓回归
  通过，警告仍仅来自测试依赖 Nuitka `glob2` 的弃用转义。
- Subscription 反向依赖收口已完成：`AgentExecutor` 的根轮次执行器和环境快照提供器改为
  显式组合根注入；`frontends/subscription` 不再导入 `mind_app`，导入图中的该边和跨边界
  循环均已清零。
- MCP stdio 前端迁移已完成：`MindMcpRuntime`、`create_mind_mcp_server` 和
  `run_mind_mcp_server` 整体归入 `frontends/mcp/server.py`；CLI 不再导入具体 MCP
  实现，而由 `mind.py` 组合根注入 runner。MCP 工具发现、命令校验、Turn application
  提交、会话续接、关闭和失败回执专项回归 `14 passed`，CLI 入口回归 `1 passed`，
  MCP 归属/旧路径守卫 `2 passed`。
- MCP 前端反向依赖收口已完成：根轮次 runner 与环境快照 provider 改为组合根显式注入，
  `frontends/mcp` 不再导入 `mind_app.runtime.turns.root` 或
  `mind_app.interaction.environment`；MCP 行为回归 `14 passed`，前端边界守卫通过，
  导入图无循环。
- CLI 前端迁移已完成：`mind_app/cli` 整体归入 `frontends/cli`，命令解析、路由、TUI
  启动、doctor、MCP registry 和进程级中断生命周期保持在同一适配器边界；CLI 回归
  `136 passed`，TUI 启动回归 `11 passed`，旧路径归属守卫通过。
- CLI 前端反向依赖收口已完成：`run_root_turn` 和环境快照 provider 改为由
  `mind.py` 组合根注入，`frontends/cli` 不再导入 `mind_app.runtime.turns.root` 或
  `mind_app.interaction.environment`；CLI/TUI 定向回归 `147 passed`，边界守卫通过。
- TUI 前端整体迁移已完成：输入、会话、渲染、展示 runtime 和契约整体归入
  `frontends/tui`，所有生产消费者与测试已切换新路径，旧 `mind_app/tui` 源目录和导入
  已清零；TUI/CLI 回归 `1618 passed`，完整架构守卫 `88 passed, 58 warnings`，
  前端归属守卫和导入图共同证明不存在前端与旧应用的包级循环。
- TranscriptSink 端口迁移已完成：`agent/ports/transcript.py` 单一声明 TranscriptActor
  和写入端口，runtime、Hook、执行器与历史 writer 已切换新路径；文件存储和历史 UI
  归约仍留在实现边界，旧 `mind_app/history/contracts.py` 已删除。

警告来自测试依赖的 Nuitka `glob2` 弃用转义，不属于本次生产代码失败；下次扩大验证时
仍需记录是否发生变化。

### 下一切片：入口与数据迁移

当前只允许进入以下顺序，不以补丁式需求插队：

1. **入口与数据迁移**：`mind_core` 的配置、权限、hooks、skills 已完成生产导入清零，
   终端轮次生命周期已迁入 `mind_app/presentation/terminal`；Hook 命令执行器已归属
   `infrastructure/platform`，Hook runtime/registry/Scope 已接入 `agent/harness/hooks`；
   通用 MCP 生命周期所有者已迁入 `agent/harness/mcp`；本地服务生命周期 owner、keepalive、
   上下文类型和 setup helpers 已迁入 `infrastructure/services`；Subscription 适配器已
   整体迁入 `frontends/subscription`，仅供它使用的 HTTP/WS 客户端与 wire envelope 已
   同步迁出，旧路径已删除；下一条补齐 CLI、TUI、MCP、Subscription 的独立启动/恢复证据，
   具体外部 MCP runtime 仍由后续 capability/adapters 切片接管。
   Subscription 对旧根轮次与环境采集模块的反向依赖已收口：执行器和环境快照提供器由
   组合根注入，前端适配器不再导入 `mind_app.runtime.turns` 或
   `mind_app.interaction.environment`；下一条补齐 CLI、TUI、MCP、Subscription 的独立
   启动/恢复证据。
   stdio MCP 入站适配器及其根轮次/环境能力注入已完成，CLI 适配器及其根轮次/环境
   能力注入也已收口；下一条只补齐四类入口的独立启动/恢复证据，再进入具体外部 MCP
   capability/adapters 的职责迁移。
   TUI 输入、会话、渲染和 runtime 已作为同一可替换前端边界整体迁入
   `frontends/tui`，旧路径已删除；下一条补齐 CLI、TUI、MCP、Subscription 的独立
   启动/恢复证据，再进入具体外部 MCP capability/adapters 的职责迁移。
     TranscriptSink 端口、Transcript 共享记录值/归约器和 Session 游标存储均已完成职责迁移：
     共享值位于 `agent/stores/transcripts`，Session 状态位于 `agent/stores/sessions`，文件
     JSONL adapter 位于 `infrastructure/persistence`；旧 `mind_app/history` 包及 contract/store
     路径已删除。已通过 Transcript/TUI/Turn/Subagent/History 回归、完整架构守卫、导入图和
     `compileall`，下一条只补齐四类入口独立启动/恢复证据，再进入其他历史包删除收口。
      跨入口应用展示端口和纯文本值对象已归入 `agent/ports/presentation.py`；应用结果
      view 已按语义拆入 `agent/application/views`，前端和运行侧只依赖显式契约，交互和
      输出生命周期未混入 ports。下一切片补齐四类入口独立启动/恢复证据，并开始迁移
      `mind_app.presentation` 的 renderer/stream 实现到前端或 application adapter，
      继续保持 view 与具体终端渲染解耦。工具展示策略和 Subscription 显式装配前置切片
      已完成，下一条只补齐 CLI、TUI、MCP、Subscription 四类入口独立启动/恢复证据。
      TUI 输入控制器、session loop 和 CLI durable exec 的 Protocol Client/application
      factory 已改为组合根显式注入；下一条收口 TUI 对话 fork feature 的 Protocol Client
      发现，再补四类入口独立启动/恢复证据。
2. **历史包删除收口**：按导入图逐批删除 `mind_app`、`mind_core`、`mind_nova`、
   `engine`，并完成存量配置、历史、报告和打包元数据回读。

每一项的准入条件是：一个完整生产用例、一个关键失败路径、明确状态所有者、旧路径可
删除、架构守卫和 `compileall` 证据。任一条件不足时只更新本计划，不创建空目录。

本次 MCP 生命周期切片的删除条件已满足：Harness 所有者不得导入 `mind_app` 或具体 MCP 实现；
组合根必须显式注入 `ExternalMcpRuntime` 工厂；旧 `mind_app.runtime.mcp.lifecycle`
文件、导入和默认隐式构造全部清零，并以启动、重启、取消清理回归证明行为保持一致。

本次服务生命周期切片的删除条件已满足：`ServiceRuntimeOwner`、keepalive 和服务上下文类型不得
依赖 `mind_app`；服务停止、重启、保活取消和关闭顺序由 infrastructure 单一实现持有；
旧 `mind_app.runtime.mcp.service_lifecycle`、`keepalive` 路径及生产导入清零，并保留
Helix/TUI 启动与取消清理回归。

本次服务 setup 切片的删除条件已满足：路径解析、PATH 注入、打包文件校验、Darwin 执行权限和
服务资产缺失判断由 `infrastructure/services/runtime_setup.py` 单一持有；该模块不得
依赖 `mind_app` 或 presentation，旧 runtime 中的同名 helper 定义与生产导入清零，并以
CLI doctor、TUI Helix 和服务启动回归证明行为一致。

本次 Subscription 前端切片的删除条件已满足：`frontends/subscription` 单一持有远端
HTTP/WS 客户端、wire envelope、open/resume、收件箱转发和恢复逻辑；Harness 只通过
`agent/ports/subscription.py` 管理 owner 生命周期；控制器、TUI、CLI 和测试已切换新路径，
`mind_app/subscription`、`mind_app/runtime/agent` 旧源文件及生产导入清零。

本次 Subscription 反向依赖收口的删除条件已满足：`AgentExecutor` 在缺少根轮次/环境能力时
只返回明确配置错误，生产实现由 `mind.py` 组合根提供；Subscription 前端不再导入旧
`mind_app` runtime 或 interaction 模块，成功、失败、取消和环境快照回归保持一致。

本次 MCP stdio 前端迁移的删除条件已满足：`frontends/mcp/server.py` 单一持有 stdio
工具发现、`mind_exec` 调用、会话续接和关闭生命周期；`frontends.cli` 仅通过组合根注入
runner，不导入具体 MCP 实现；旧 `mind_app/runtime/mcp/server.py` 文件和生产导入已清零，
并由 MCP 专项回归、CLI 入口回归、架构守卫、导入图和 `compileall` 证明行为一致。

本次 MCP 前端反向依赖收口的删除条件已满足：`RootTurnRunner` 和
`EnvironmentSnapshotProvider` 只作为 `frontends/mcp` 的显式能力端口，生产实现由
`mind.py` 组合根绑定；旧根轮次与环境采集模块不再由 MCP 前端导入，MCP 续接、超时和
失败回执保持原有语义。

本次 CLI 前端迁移的删除条件已满足：`frontends/cli` 单一持有命令解析、入口中断、
非交互执行、TUI 启动和配置管理命令；`mind.py` 只从新前端导入 `run`，旧
`mind_app/cli` 目录和生产导入已清零，CLI/TUI 回归与架构守卫证明行为一致。

本次 CLI 前端反向依赖收口的删除条件已满足：`frontends/cli` 只声明根轮次和环境快照
能力协议，生产实现由 `mind.py` 组合根绑定；未装配时返回明确配置错误，不复制或隐式
导入旧 runtime，实现与 CLI 入口生命周期保持一致。

本次 TUI 展示契约切片的删除条件已满足：`frontends/tui/contracts` 只依赖标准库和
同一契约包，不能导入 `mind_app`、`agent`、`infrastructure`、`engine` 或具体前端；
所有 TUI、CLI 和测试消费者必须切换到新路径，旧 `mind_app/tui/contracts` 文件与导入
清零，并通过契约行为回归、前端边界守卫、导入图和 `compileall` 验证。会话、渲染和
runtime 目录不随本切片搬迁，后续按状态所有权分别处理。

本次 TUI 完整前端迁移的准入条件已满足：`frontends/tui` 整体持有 TUI 的输入、会话、
渲染和展示 runtime；生产消费者、测试和文档统一使用新路径，旧 `mind_app/tui` 源目录
与导入清零，且导入图不得出现 `frontends -> mind_app -> frontends` 循环。Harness、
Controller 和线上 Protocol Client 状态所有权不随目录迁移，必须通过 TUI 启动、交互、
恢复和关键失败路径回归验证。

本次 TUI 完整前端迁移的删除条件已满足：`frontends/tui` 整体持有 TUI 输入、会话、渲染
和展示 runtime，生产消费者、测试和文档均使用新路径，旧 `mind_app/tui` 源目录与导入
清零；导入图无 `frontends -> mind_app -> frontends` 循环，TUI 启动、交互、恢复和
关键失败路径回归通过。

本次 TranscriptSink 端口切片的准入与删除条件已满足：`agent/ports/transcript.py` 只依赖标准库，
runtime、Hook、执行器和 Transcript writer 统一从该端口导入；旧
`mind_app/history/contracts.py` 文件与生产导入清零，并通过 Transcript、Turn、Hook
关键路径回归和端口边界守卫验证。TranscriptEntry/TranscriptReplay 已由
`agent/stores/transcripts` 持有，文件 Reader/Writer 和历史文件路径在本轮迁入
`infrastructure/persistence`。

本次 Transcript 共享记录切片的准入条件：`agent/stores/transcripts` 只持有不依赖文件系统
的 `TranscriptEntry` 和 `TranscriptReplay`，工具开始/完成归并策略由 `agent.domain` 提供；
所有跨层消费者通过新路径读取记录值，文件 Reader/Writer 只作为 infrastructure adapter 使用，且
stores 不导入 `mind_app`、`infrastructure` 或展示模块。删除条件是旧
`mind_app/history/transcript.py` 已删除，文件 adapter 由
`infrastructure/persistence/transcripts.py` 独立持有，并通过存量读取、追加写入和路径
失败回归；后续不再在 `mind_app/history` 添加新的实现。

本次 Session history store 切片的准入与删除条件已满足：`agent/stores/sessions/history.py` 单一持有
SQLite 会话游标和待分支请求状态，构造必须接收显式数据库路径，不导入 `mind_app`、
`infrastructure` 或 UI；CLI、Controller、TUI 和测试统一切换到新路径，旧
`mind_app/history/store.py` 与 history 导出删除，并通过会话归档、恢复、过滤和缺失记录
失败路径验证；旧 `mind_app/history/store.py` 与 history 导出已删除。

本次 Transcript 文件 adapter 切片的准入条件：`infrastructure/persistence/transcripts.py`
单一持有 JSONL Reader/Writer、Session 日期路径、编码和损坏记录观测；共享记录值与归约
仍来自 `agent/stores/transcripts`，不得在基础设施复制；所有生产/测试消费者切换新路径，
旧 `mind_app/history` 包删除，并通过存量读取、追加写入、尾部读取和路径失败回归。满足后
才允许继续删除 `mind_app` 的 history 目录及相关启动依赖。

本次 Subagent fork history adapter 切片的准入与删除条件已满足：`agent/adapters/agents/fork_context.py`
只依赖 application fork context、agent Transcript records/replay 和显式读取 callable；
`SubagentRuntime` 不再导入或实例化具体 Transcript 文件 Store，Controller 负责绑定
infrastructure reader；旧 `mind_app/runtime/subagents/context.py` 已删除，并通过 fork 范围、
字符预算和无读取器失败路径回归。

本次应用展示端口切片的准入与删除条件已满足：`agent/ports/presentation.py` 只依赖标准库并
单一持有 `ApplicationView`、`ApplicationSink` 和 `Viewport`；`mind_app/presentation/application.py`
不再重新定义这些端口，前端、入口 sink 和运行侧统一从新路径导入。跨入口展示回归 `704 passed`、
完整架构守卫 `93 passed, 60 warnings`，端口边界守卫、导入图、`compileall` 和
`git diff --check` 均通过。

本次文本展示值对象切片的准入条件：`agent/ports/presentation.py` 单一持有
`TextStyle`、`TextSpan`、`StyledBlock` 及应用展示端口；`mind_app.presentation.models`
不得重新定义这些无业务语义的值对象，前端、输出端口和渲染器统一从新路径导入。删除条件是
旧定义和生产导入清零，文本/终端/输出回归、端口边界守卫、导入图和 `compileall` 均通过。

本次应用结果视图重组切片的准入与删除条件已满足：`agent/application/views` 按语义持有
跨前端不可变 view，`views/contracts.py` 单一持有展示联合类型和 sink 协议；新模块不依赖
`mind_app`、具体 renderer 或前端。旧 `mind_app.presentation.models`、`contracts` 文件及
生产/测试导入已清零；展示与 TUI 回归 `158 passed`、Run/TUI 回归 `512 passed`，架构守卫
除允许模块清单漏登记外其余 `92 passed`，修正清单后专项守卫通过；`compileall` 和
`git diff --check` 通过。完整守卫需在提交前重跑确认。

下一切片工具展示策略的准入条件：展示分类值对象和工具规格只依赖标准库，审批判定属于
`agent.domain.tool_policy`；runtime、renderer、TUI 和测试统一切换，旧
`mind_app.presentation.tool_policy` 可物理删除，且不得新增旧路径 facade。关键失败路径
是未知工具回退通用规格、审批专用工具判定和工具过滤模式保持现有语义；删除条件为旧文件与
生产导入清零，并通过工具策略、渲染、TUI 回归、架构守卫、导入图和 `compileall`。

本次工具展示策略切片已满足上述条件：工具策略/渲染回归 `123 passed`，Run/TUI/输出回归
`544 passed`，完整架构守卫 `93 passed, 60 warnings`，导入图、`compileall` 和
`git diff --check` 均通过；旧 `mind_app.presentation.tool_policy` 文件和生产导入清零。

下一切片 Subscription 独立装配的准入条件：`AgentRuntime` 只接收显式
`TurnApplicationFactory`，不读取宿主动态属性；组合根负责绑定持久 application，缺失依赖在
启动边界立即失败，关闭时由订阅执行器回收 application。关键失败路径是缺失 factory、factory
返回错误对象和 runtime shutdown；删除条件是前端中 `runtime_services` 动态发现清零，并通过
订阅启动/恢复、执行与关闭回归及架构守卫验证。

本次 Subscription 独立装配切片已满足上述条件：订阅运行时回归 `18 passed`，架构/旧路径
守卫与装配专项回归合计 `19 passed`；缺失显式 factory 时保持未配置状态，错误 factory
结果会在边界抛出，shutdown 回收持久 application；导入图、`compileall` 和
`git diff --check` 通过，`frontends/subscription/runtime.py` 已无 `runtime_services` 动态发现。

下一切片 TUI 输入端口的准入条件：`TuiTurnInputControl` 只接收显式
`ProtocolCommandClient | None`，生产 session 在创建控制器时绑定能力；无客户端时仅允许
测试 seam 的命令函数，不读取宿主 `runtime_services`。关键失败路径是客户端类型错误、缺失
客户端时的明确配置错误和关闭期间输入对账；删除条件是控制器模块动态发现清零，并通过 TUI
输入、流式命令和架构守卫验证。

本次 TUI 输入端口切片已满足上述条件：`TuiTurnInputControl` 删除 controller 上的
`runtime_services`/`model_capability` 反射发现，只接受显式 `ProtocolCommandClient`，类型错误
在构造边界立即拒绝；缺失客户端时仅使用测试 seam，关闭期间的 steer/interrupt 对账语义保持不变。
TUI 输入、流式命令、中断和前端边界回归 `57 passed`；完整架构守卫 `93 passed, 60 warnings`，
导入图、`compileall` 和 `git diff --check` 均通过。下一切片收口 `frontends/tui/session/loop.py` 的
Protocol Client/application factory 装配，使 session loop 和 CLI durable exec 不再从 `Mind`
反射读取 `runtime_services`。

本次 TUI session/CLI durable 装配切片已满足上述条件：`run_tui_loop`、TUI 输入控制器和
CLI `run_selected_command` 均接收显式 `ProtocolCommandClient`/`TurnApplicationFactory`，
缺失 durable factory 在入口边界立即抛出明确配置错误；bootstrap 是唯一把进程级
`RuntimeServices` 映射到这些入口的组合边界。TUI/CLI 回归 `145 passed, 1 deselected`，
原有极短时序测试单独复跑通过；完整架构守卫 `93 passed, 60 warnings`，导入图、
`compileall` 和 `git diff --check` 均通过。下一切片收口
`frontends/tui/features/conversation.py` 的 Protocol Client 显式注入。

本次 TUI conversation fork 切片已满足上述条件：`fork_current_conversation` 删除
`_protocol_client_for` 和 `runtime_services` 反射，`/fork` 与 transcript backtrack 均由
session 显式传递 `ProtocolCommandClient`；源缺失恢复、可重试失败和本地请求 seam 语义保持
不变。Fork/backtrack/command/stream/input 回归 `211 passed`，其中显式客户端调用有独立
断言；完整架构守卫 `93 passed, 60 warnings`，导入图、`compileall` 和
`git diff --check` 均通过。

本次流式执行依赖切片已满足上述条件：`stream_turn` 不再从 `Mind.runtime_services` 反射
模型能力或效果账本，模型 `ModelCapability`、控制 `ProtocolCommandClient` 和
`EffectJournalFactory` 由组合根沿根轮次、TUI、CLI、订阅/MCP 和 Subagent 显式注入；
Stop Hook continuation 复用同一组能力，Subagent 恢复不会重新发现宿主服务。流式结果、
CLI、TUI、Subagent、MCP 定向回归合计 `205 passed`；完整架构守卫 `93 passed, 60 warnings`，
导入图、`compileall` 和 `git diff --check` 均通过。下一切片收口 `stream.py` 对
Controller 的剩余运行上下文依赖，将 Transcript、输出会话、清理和工作区端口归入
Harness session application，继续缩小 legacy runtime 的职责面。

本次审批状态所有权切片已满足上述条件：新增 `agent.ports.ApprovalLedger`，由
`TurnContext` 携带审批消费/终态事实；根轮次、TUI 和 Subagent 在创建执行上下文时显式
注入，`stream_turn` 删除 `ApprovalCallLedger` 的宿主反射、临时创建和写回，并直接使用
上下文账本。流式结果、根轮次、TUI、CLI 和 Subagent 定向回归 `213 passed`；端口/public
API/legacy 边界架构断言 `4 passed, 1 warning`，导入图、`compileall` 和
`git diff --check` 均通过。下一切片继续收口 `stream.py` 的 Transcript、输出会话、清理
和工作区上下文，将其迁入 Harness session application。

本次输出边界切片已满足上述条件：`stream_setup` 不再从 `controller.frontend` 反射查找
输出工厂，根轮次、CLI 和 TUI 由组合入口显式传入 `SessionFactory`，Subagent 继续使用
显式的 silent factory，续跑沿用同一显式工厂。输出会话准备和流式行为回归 `211 passed`；
新增缺失工厂门禁，导入图、`compileall` 和 `git diff --check` 通过。下一切片收口
`stream.py` 的 Transcript、清理和工作区上下文依赖。

本次 Transcript 所有权切片已满足上述条件：新增 `agent.ports.TranscriptFactory`，由
`TurnContext` 携带按轮次创建 writer 的能力；根轮次、TUI、Subagent 和执行建立失败路径
均使用显式工厂，`stream.py` 不再访问 `controller.transcripts`。流式结果、根轮次、TUI、
CLI、Subagent 和 Transcript setup failure 回归 `233 passed`；端口边界专项 `4 passed`，
导入图、`compileall` 和 `git diff --check` 通过。下一切片继续收口清理和工作区上下文。

本次清理生命周期切片已满足上述条件：新增 `agent.ports.TurnCleanupPort`，由
`TurnContext` 携带异步资源清理端口；`stream.py` 和 `StreamTurnFinalizer` 不再直接调用
`mind.await_cleanup`，根轮次、TUI、Subagent 和 CLI 组合入口显式传递清理能力。受影响
回归 `233 passed`；端口边界专项 `4 passed, 1 warning`，导入图、`compileall` 和
`git diff --check` 通过。下一切片收口动画状态、工作区补丁预览和会话上下文端口。

本次工作区补丁预览切片已满足上述条件：新增 `agent.ports.PatchPreviewPort`，通过
`TurnContext` 显式注入根轮次、TUI 和 Subagent；`stream.py`、客户端工具和本地审批策略
只消费只读预览端口，不再访问完整 `WorkspaceRuntime`。工作区审批、流式、根轮次、TUI、
CLI、Subagent 回归 `272 passed`；端口边界专项 `4 passed, 1 warning`，导入图、
`compileall` 和 `git diff --check` 通过。下一切片收口动画状态与会话上下文端口。

本次重试展示状态切片已满足上述条件：新增 `agent.ports.RetryStatePort` 和稳定的
`RetryState` 类型，通过 `TurnContext` 由组合根传入根轮次及 TUI；`stream_setup.py` 和
`stream.py` 删除对 `controller.frontend.runtime.set_wait_retry_state` 的隐式发现，现有
`turn.retrying` 的 transport/provider/idle 展示合并语义保持不变。流式、TUI、CLI、输出
和重试回归 `280 passed`；端口边界专项 `4 passed, 1 warning`，导入图、`compileall` 和
`git diff --check` 通过。下一切片收口动画生命周期与会话上下文端口。

本次动画生命周期切片已满足上述条件：新增 `agent.ports.TurnAnimationPort` 和展示适配器，
通过 `TurnContext` 由组合根传入根轮次及 TUI；`stream.py` 删除对
`mind.frontend.runtime.active`、`mind.stop_anim` 的直接访问，只消费端口并保留
`settle`、首帧和终态停止顺序。流式、TUI、CLI、输出、重试和终端动画回归 `280 passed`；
端口边界专项 `4 passed, 1 warning`，导入图、`compileall` 和 `git diff --check` 通过。
下一切片收口会话上下文端口。

本次会话上下文切片已满足上述条件：新增 `agent.ports.TurnSessionContextPort` 和
`ControllerTurnSessionContext` 适配器，通过 `TurnContext` 沿根轮次、TUI、CLI 显式传递；
`stream_setup.py` 不再接收 Controller，也不再导入配置、环境捕获或 skills 实现，只消费
环境快照、skills payload 和动画开关。流式、TUI、CLI、输出、重试和终端动画回归
`280 passed`；端口边界专项 `4 passed, 1 warning`，导入图、`compileall` 和
`git diff --check` 通过。下一切片收口流式执行剩余的会话结果与工具策略依赖。

本次会话结果状态切片已满足上述条件：新增 `agent.ports.TurnSessionStatePort` 和
`ControllerTurnSessionState`，通过 `TurnContext` 沿根轮次、TUI、CLI 显式传递；
`stream.py` 不再直接写入 `ConversationState` 或调用 Controller 的最近回复属性，失败、
取消和成功结果写回语义保持不变。流式、TUI、CLI、输出、重试和终端动画回归
`280 passed`；端口边界专项 `4 passed, 1 warning`，导入图、`compileall` 和
`git diff --check` 通过。下一切片复核工具策略和剩余 Controller 结果依赖。

本次执行策略端口切片已满足上述条件：新增 `agent.ports.ExecutionPolicy`、判定结果和
规则提案契约，通过 `TurnContext` 沿根轮次、TUI、CLI、Subagent 显式传递；流式工具和
审批处理器不再读取 `controller.workspace_runtime.execution_policy`，本地命令审批、补丁
会话批准和规则提案写回语义保持不变。核心流式、TUI、Subagent、权限和执行策略回归
`181 passed`；架构专项 `5 passed, 2 warnings`，导入图、`compileall` 和
`git diff --check` 通过。下一切片复核剩余 `stream.py` Controller 结果依赖。

本次工作区与 Hook 会话上下文切片已满足上述条件：扩展
`agent.ports.TurnSessionContextPort`，由 `ControllerTurnSessionContext` 提供工作区、
启动告警和持续命令 Hook 会话；`CommandHookSessionPort` 归入 Hook 端口，`stream.py`
不再反射读取 `history_workspace`、`hook_startup_warnings` 或
`command_hook_sessions`。流式启动展示、持续命令 Hook、TUI 和 Subagent 回归
`280 passed`；架构专项 `5 passed, 2 warnings`，导入图、`compileall` 和
`git diff --check` 通过。下一切片收口审批协调器和权限授予依赖。

本次审批协调与权限授予切片已满足上述条件：新增
`agent.ports.ApprovalCoordinatorPort`、`ApprovalOutcomePort` 和
`PermissionGrantPort`，通过 `TurnContext` 沿根轮次、TUI、CLI、Subagent 显式传递；
流式工具和审批处理器不再读取 Controller 的审批协调器或权限存储属性，审批等待、
本地策略回写和权限授予语义保持不变。核心流式、TUI、Subagent 回归 `123 passed`；
架构专项 `5 passed, 2 warnings`，导入图、`compileall` 和 `git diff --check` 通过。
下一切片复核 `stream.py` 的生命周期入口并开始迁移剩余运行时服务依赖。

本次流式生命周期入口切片已满足上述条件：`stream_turn` 删除 Controller 类型注解，
仅接收不透明的生命周期 owner 参数，内部不再依赖宿主类型或属性。Subagent、根轮次和
TUI 的终端生命周期调用保持不变，流式与子 Agent 回归 `123 passed`，`compileall` 和
`git diff --check` 通过。下一切片迁移剩余运行时服务依赖并评估 `run_foreground_turn`
的展示生命周期端口。

本次终端前台生命周期切片已满足上述条件：新增
`agent.ports.TurnForegroundLifecyclePort` 和 `ControllerTurnForegroundLifecycle` 适配器，
根轮次/TUI 通过组合根显式传入，`run_foreground_turn` 不再读取 Controller 的前端、动画、
进度或清理属性。终端进度、worked footer、动画停止顺序和异常收束语义保持不变；根轮次、
TUI、流式和输出回归 `318 passed`；架构专项 `4 passed`，导入图、`compileall` 和
`git diff --check` 通过。下一切片收口 Turn 执行器的运行时服务端口。

本次 Turn 执行运行时端口切片已满足上述条件：新增
`agent.ports.TurnEventReportHandle`、`TurnEventReportingPort` 和
`TurnExecutionRuntimePort`，由 `ControllerTurnExecutionRuntime` 适配报告租约、MCP 会话、
工具过滤和异步清理；`execute_turn` 删除 `Mind` 类型依赖和宿主属性反射，根轮次、TUI、
Subagent 均通过显式端口执行。核心 Turn/TUI/Subagent 回归 `56 passed`，补充工具回归
`31 passed`；架构端口专项、`compileall` 和 `git diff --check` 通过。下一切片收口根轮次
准备阶段的会话登记、Transcript 路径和 Hook scope 端口。

本次根轮次会话端口切片已满足上述条件：新增
`agent.ports.TurnStartResultPort`、`RootTurnSessionPort` 和
`ControllerRootTurnSession`，根轮次/TUI 通过组合根显式传入；`prepare_root_turn` 不再
读取 Controller 的会话、工作区、权限、报告、Transcript 或 Hook 属性，登记结果和
作用域由端口统一提供。根轮次、TUI、Subagent 回归 `63 passed`；根准备与执行端口专项
`4 passed, 1 warning`，导入图、`compileall` 和 `git diff --check` 通过。下一切片继续
收口 `run_root_turn` 的前端生命周期 owner 和根轮次入口协议。

本次根轮次入口切片已满足上述条件：`run_root_turn` 改为直接消费
`RootTurnSessionPort` 与 `TurnExecutionRuntimePort`，偏好配置、默认权限和审批账本均由
根会话端口提供；前台生命周期只接收不透明执行运行时 owner，运行时不再需要 Controller
参数。CLI/TUI/根轮次/子 Agent 回归 `161 passed`；根准备与执行端口专项
`4 passed, 1 warning`，导入图、`compileall` 和 `git diff --check` 通过。下一切片复核
SubagentRuntime 的可选执行器端口，移除其 Controller fallback。

本次 Subagent 执行端口切片已满足上述条件：SubagentRuntime 在未显式传入时只读取
Controller 已装配的 `turn_execution_runtime` 端口，不再将 Controller 本身作为执行器
能力回退；生产组合根始终显式注入端口，测试替身同步声明相同边界。Subagent/工具回归
`39 passed`；端口类型、导入图、`compileall` 和 `git diff --check` 通过。下一切片复核
Subagent 的清理与 Hook scope 依赖，继续把宿主生命周期从 runtime 移出。

本次 Subagent 生命周期端口切片已满足上述条件：SubagentRuntime 的流式 owner 改为
`TurnExecutionRuntimePort`，Hook scope 改为显式宿主端口，权限授予直接使用注入端口，
停止 Hook 清理复用显式清理/执行端口；runtime 不再保存 Controller 或把它传给流式适配器。
Subagent/工具回归 `39 passed`；架构专项 `3 passed, 1 warning`，导入图、`compileall` 和
`git diff --check` 通过。

本次 Subagent 宿主端口切片已满足上述条件：新增 `SubagentRuntimeHostPort`，
SubagentRuntime 首参改为组合根宿主端口，删除 `execution_runtime`、`hook_scope_for` 两个
重复注入参数及 `Mind` 类型依赖；生产组合根通过 `Mind.turn_execution_runtime` 与
`Mind.turn_hook_scope` 实现端口，测试替身同步使用 `HookExecutionContext` 边界转换。
Subagent/工具回归 `39 passed`，根轮次、TUI、CLI 与 Subagent 回归 `161 passed`；架构守卫
新增端口断言通过，定向专项 `3 passed, 1 warning`，导入图、`compileall` 和
`git diff --check` 通过。下一切片收口 `mind_app/runtime/subagents/runtime.py` 对
`mind_app.runtime.turns` 与静默输出工厂的历史依赖，评估将 Subagent 运行编排下沉到
`agent/harness` 的完整删除条件。

本次 Subagent 执行适配切片已满足上述条件：新增
`mind_app/runtime/subagents/execution.py`，集中承载 `execute_turn`、`stream_turn` 和
静默输出会话的组合根适配；`SubagentRuntime` 只保留 Agent 树、mailbox、提交和关闭编排，
不再导入 `mind_app.runtime.turns`、输出实现或 `agent.adapters.agents.execution`，并通过
`SubagentRuntimeHostPort` 获取执行端口。Subagent/工具回归 `39 passed`，根轮次、TUI、
CLI、MCP 与清理回归 `167 passed`；Subagent 编排架构专项 `4 passed, 1 warning`，导入图、
`compileall` 和 `git diff --check` 通过。下一切片将纯编排模块物理迁移到
`agent/harness/agents/runtime.py`，同步切换客户端工具与组合根入口并删除旧 runtime 路径，
不保留转发 facade。

本次 Subagent 编排物理迁移已满足上述条件：`SubagentRuntime` 已迁入
`agent/harness/agents/runtime.py`，Controller、客户端工具和测试全部切换到 Harness
公开路径，`mind_app/runtime/subagents/runtime.py` 已删除且没有保留转发 facade。Harness
仍只依赖 Agent application、ports、stores 和 adapters；旧执行流依赖继续隔离在
`mind_app/runtime/turns/subagent_adapter.py` 组合适配器。Subagent、工具、MCP、清理和根入口
回归 `167 passed`；Subagent 编排架构专项 `4 passed, 1 warning`，导入图、`compileall`
和 `git diff --check` 通过。下一切片复核 `mind_app/runtime/turns/subagent_adapter.py`
的适配职责，评估将其拆分为 Harness 可消费的 Model/Output 能力端口并继续清理
`mind_app/runtime/turns` 依赖。

本次 Subagent 宿主清理端口收窄已满足上述条件：`SubagentRuntimeHostPort` 删除通用
`turn_execution_runtime` 暴露，改为专用 `subagent_cleanup` 端口；Harness 编排只消费
子执行、子轮次 runner、Hook scope 和清理能力，通用 Turn 执行运行时留在组合适配器内部。
Subagent、工具、MCP、清理和根入口回归 `167 passed`；架构专项 `4 passed`，导入图、
`compileall` 和 `git diff --check` 通过。下一切片复核 `SubagentRuntime` 的可选
`executor`/`turn_runner` 参数，评估是否应由宿主契约统一提供并进一步缩小组合根入口。

本次 Subagent 执行入口收口已满足上述条件：删除 `SubagentRuntime` 的可选
`executor`/`turn_runner` 构造参数及对应生产类型依赖，执行端口统一由
`SubagentRuntimeHostPort` 提供，消除调用方绕过宿主契约的装配分叉。Subagent、工具、MCP、
清理和根入口回归 `167 passed`；架构专项 `4 passed`，导入图、`compileall` 和
`git diff --check` 通过。下一切片复核 `mind_app/runtime/turns/subagent_adapter.py` 的
适配职责，设计可由 Harness 消费的模型流与输出会话端口，继续缩小历史包边界。

本次 Subagent 适配目录收口已满足上述条件：`execution.py` 已物理迁入
`mind_app/runtime/turns/subagent_adapter.py`，空的 `mind_app/runtime/subagents` 包已删除，
组合根入口同步切换且没有新增兼容 facade。Subagent、工具、MCP、清理和根入口回归
`167 passed`；架构专项 `4 passed`，导入图、`compileall` 和 `git diff --check` 通过。
下一切片复核 Turn 流适配器的 Model/Output 端口边界，优先删除其对旧输出实现的直接依赖。

## 过渡入口与删除条件

| 过渡入口 | 当前用途 | 删除条件 |
| --- | --- | --- |
| `mind.py -> agent.composition` | 稳定启动和唯一具体组合根 | 新入口完成启动/恢复回归并切断历史包导入 |
| `mind_app/*` | 迁移期 CLI/TUI/MCP/Subscription 入口和适配器 | 对应前端在 `frontends/` 有完整用例且数据/观测兼容 |
| `mind_nova` | 迁移期 wire/认证/事件实现 | `protocol/` SDK 跨前端 fixture、版本和打包验证完成 |
| `mind_core` | 迁移期配置、策略和资源读取 | 配置/策略/skills/hooks 所有权拆分并完成存量回读 |
| `engine` | 迁移期平台和进程实现 | capability/infrastructure 接管，反向依赖清零 |

过渡入口只允许在本表登记的用途内存在；任何新增跨层导入必须同时登记删除条件和最晚
阶段。`backend/` 不参与这些迁移。

## 阶段 5 出口条件

- `agent`、`protocol`、`frontends`、`infrastructure` 的依赖方向由导入图守卫；
  `agent.domain`、`agent.protocol` 脱离 IO、UI 和历史包可独立测试。
- 四类入口共享同一 application Command、Turn/Run、Tool/Approval/Effect 和结算语义，
  断线恢复、取消、重试、幂等和终态均有端到端证据。
- Protocol Client、Canonical Event/Item projection 和线上 `mind.chat` fixture 可被
  TUI、桌面端、Web 复用，且不需要 `Mind` 控制器才能运行。
- 历史配置、Transcript、报告、订阅数据完成版本迁移和回读；强退、接管和 Redis 清空
  演练不依赖进程内状态。
- 生产代码不再导入四个历史包，旧目录和旧 facade 已删除，`mind.py`、打包和安装元数据
  均使用新边界。

## 每次切片的最小验证集

日常切片采用快速验证路径：只运行受影响模块的定向回归、与改动边界对应的架构
专项，以及导入图、语法和 diff 检查。`tests/test_package_architecture.py` 的完整
守卫会重复扫描整个仓库，保留到阶段出口、跨多个职责边界的变更或发布前复核；它
不是每个小切片的阻塞条件。若切片删除旧路径或改变公共包边界，仍须在当前切片执行
相应的架构专项，不能用快速路径掩盖边界回归。

```text
受影响模块定向 pytest
架构边界专项 pytest（按改动选择 -k）
scripts/agent_runtime_import_graph.py --write
scripts/agent_runtime_import_graph.py --check
python -m compileall -q agent mind_app infrastructure protocol tests
git diff --check
```

阶段出口和发布前复核增加：

```text
tests/test_package_architecture.py
```

Windows 使用仓库虚拟环境：`.\venv\Scripts\python.exe -m pytest`。提交前必须复核
`git diff --name-status`，确认旧路径删除而非新增兼容副本。

## 风险与决策

- **状态竞争**：Session/Run 的权威状态只能由 Harness/store 写入；入口断开不等于取消。
- **外部副作用重复**：效果账本和 lease/fence 先于重新派发；未知效果必须进入 reconciliation。
- **过渡层永久化**：每个临时导入必须在本计划登记删除条件，阶段 5 出口前不得新增。
- **前端分叉**：事件展示只消费 Protocol Client 的 Canonical Item，不在 TUI/CLI 复制 reducer。
- **文档漂移**：协议变更先同步 `services/llm/PROTOCOL.md` 与 fixture，再更新实现和本计划证据。

## 交接检查清单

- [ ] 先阅读本文件、架构基线、导入图和正式协议。
- [ ] 确认目标改动属于当前“下一切片”，并写清状态所有权和删除条件。
- [ ] 先补失败路径和边界守卫，再切换生产调用者并删除旧路径。
- [ ] 完成最小验证集，更新“最近完成”和最新证据；失败时不标记出口。
- [ ] 提交后在本文件记录提交号、验证结果和下一切片，详细过程放入历史归档。

## 最近变更

| 日期 | 变更 | 证据 |
| --- | --- | --- |
| 2026-08-31 | `agent/` 按职责重组；新增 Session/Workspace 生命周期端口；Skills provider 移至组合根；删除旧平铺路径 | 行为 `495 passed`；架构 `74 passed, 51 warnings`；导入图、`compileall`、`git diff --check` 通过 |
| 2026-08-31 | 收窄 `agent.application` 公开 API，端口类型消除对 application 的反向导入，所有消费者改用职责模块 | 行为 `2958 passed, 11 skipped`；架构 `75 passed, 51 warnings`；导入图、`compileall`、`git diff --check` 通过 |
| 2026-08-31 | 组合根 Skills payload 注入，清除 `agent -> infrastructure -> agent` 跨边界循环 | 组合切片定向回归 `76 passed`；新增守卫 `4 passed`；导入图、`compileall`、`git diff --check` 通过 |
| 2026-08-31 | 将本计划精简为当前状态与交接入口，完整切片历史移入归档 | 主计划与归档链接可访问，状态权威仍为本文件 |
| 2026-08-31 | 将 `ServerManageHelixCapability` 迁入 `infrastructure/services`，删除 `mind_app` 旧实现和导入 | 生命周期 `6 passed`；架构 `77 passed, 52 warnings`；导入图、`compileall`、`git diff --check` 通过 |
| 2026-08-31 | 将 `ExecPolicyManager` 迁入 `infrastructure/config`，删除 `mind_app/native_coding/exec/exec_policy.py` 和旧导出 | 全量行为 `3037 passed, 11 skipped, 52 warnings`；架构 `77 passed, 52 warnings`；导入图、`compileall`、`git diff --check` 通过 |
| 2026-08-31 | 将本地多后端 `ProcessSessionManager` 迁入 `infrastructure/platform`，删除 native coding 旧实现和导入 | 会话/TUI Shell `58 passed`；架构守卫 `2 passed`；导入图、`compileall`、`git diff --check` 通过 |
| 2026-08-31 | 将 SandboxClient/ProcessSessionManager 装配提升到 `mind.py`，删除 NativeCoding 和工具注册表的隐式构造 | 受影响行为 `339 passed`；架构守卫 `2 passed`；导入图、`compileall`、`git diff --check` 通过 |
| 2026-08-31 | 将 `RootTurnCommandExecutor` 迁入 `agent/adapters/turns`，CLI/MCP/Subscription 显式绑定旧 runner | 三入口行为 `155 passed`；专项守卫 `2 passed`，完整架构 `78 passed, 52 warnings`；导入图、`compileall`、`git diff --check` 通过 |
| 2026-08-31 | 将 `run_foreground_turn` 迁入 `mind_app/presentation/terminal/turn_lifecycle.py`，runtime root 仅保留执行编排 | 生命周期回归 `108 passed`；归属守卫 `1 passed`；导入图、`compileall`、`git diff --check` 通过 |
| 2026-08-31 | 将 HookCommandExecutor、HookCommandOutput 和 HookCommandError 迁入 `infrastructure/platform/hook_command.py`，runtime 仅通过 HookCommandRunner 使用 | Hook/平台回归 `87 passed`；端口与归属守卫通过；导入图、`compileall`、`git diff --check` 通过 |
| 2026-08-31 | 将 HookRuntime/HookRegistry 迁入 `agent/harness/hooks`，移除 Harness 对平台执行器的直接导入并由组合根注入资源 | Hook/入口/Turn 回归 `413 passed`；完整架构 `81 passed, 53 warnings`；Harness 边界与旧路径守卫、导入图、`compileall`、`git diff --check` 通过 |
| 2026-08-31 | 将 HookExecutionScope 迁入 `agent/harness/hooks/scope.py`，保留 application context 为纯输入契约并清除 runtime 旧路径 | Hook/Turn/Subagent/TUI 回归 `308 passed, 1 warning`；完整架构 `81 passed, 53 warnings`；导入图、`compileall`、`git diff --check` 通过 |
| 2026-08-31 | 将通用 `McpRuntimeOwner` 迁入 `agent/harness/mcp`，以 `McpRuntime` 端口和组合根工厂管理外部 MCP 生命周期 | MCP/TUI/server 回归 `36 passed`；MCP 所有权守卫 `3 passed`；完整架构 `82 passed, 54 warnings`；导入图、`compileall`、`git diff --check` 通过 |
| 2026-08-31 | 将 `ServiceRuntimeOwner`、keepalive 和服务上下文类型迁入 `infrastructure/services`，删除 runtime 旧生命周期实现 | 服务/CLI/TUI 回归 `44 passed`；完整架构 `82 passed, 54 warnings`；导入图、`compileall`、`git diff --check` 通过 |
| 2026-08-31 | 将服务运行时路径解析、环境注入、打包校验和权限 setup helpers 迁入 `infrastructure/services/runtime_setup.py`，runtime 仅保留启动编排 | 服务/CLI/TUI 回归 `182 passed`；完整架构 `83 passed, 55 warnings`；导入图、`compileall`、`git diff --check` 通过 |
| 2026-08-31 | 将 Subscription 适配器及专用 HTTP/WS client、wire envelope 迁入 `frontends/subscription`，将生命周期 owner/端口归入 `agent/harness/subscription` 与 `agent/ports` | Subscription/TUI 回归 `88 passed`；全仓 `3047 passed, 11 skipped, 55 warnings`；旧路径和导入清零；导入图、`compileall`、`git diff --check` 通过 |
| 2026-08-31 | 收口 Subscription 对旧根轮次和环境采集模块的反向依赖，改为组合根显式注入 | Subscription/TUI 回归 `88 passed`；`frontends` 旧 `mind_app` 导入守卫、Harness 边界守卫 `3 passed`；导入图无循环、`compileall`、`git diff --check` 通过 |
| 2026-08-31 | 将 stdio MCP 入站适配器迁入 `frontends/mcp`，CLI 通过组合根注入 runner | MCP server `14 passed`、CLI `1 passed`；MCP 归属/旧路径守卫 `2 passed`；导入图无循环、`compileall`、`git diff --check` 通过 |
| 2026-08-31 | 收口 MCP 前端对旧根轮次和环境采集模块的反向依赖，改为组合根显式注入 | MCP 回归 `14 passed`；前端边界守卫通过；导入图无循环、`compileall`、`git diff --check` 通过 |
| 2026-08-31 | 将 CLI 命令适配器整体迁入 `frontends/cli`，清除 `mind_app/cli` 旧路径 | CLI `136 passed`、TUI 启动 `11 passed`；CLI 归属/旧路径守卫通过；导入图无循环、`compileall`、`git diff --check` 通过 |
| 2026-08-31 | 收口 CLI 前端对旧根轮次和环境采集模块的反向依赖，改为组合根显式注入 | CLI/TUI `147 passed`；CLI 边界守卫通过；导入图无循环、`compileall`、`git diff --check` 通过 |
| 2026-08-31 | 将 TUI 输入、会话、渲染、展示 runtime 和契约整体迁入 `frontends/tui`，删除 `mind_app/tui` 旧路径并消除包级循环 | TUI/CLI 回归 `1618 passed`；完整架构守卫 `88 passed, 58 warnings`；导入图、`compileall`、`git diff --check` 通过 |
| 2026-08-31 | 将 TranscriptSink/TranscriptActor 从 history 实现包提升到 `agent/ports/transcript.py`，删除旧 contract 路径 | Turn/Hook/Subagent/Transcript 回归 `146 passed`；端口专项 `18 passed`；导入图、`compileall`、`git diff --check` 通过 |
| 2026-09-01 | 将 TranscriptEntry/TranscriptReplay 拆入 `agent/stores/transcripts`，归并策略下沉到 `agent.domain`；history 仅保留文件 Reader/Writer 和 Session 路径 adapter | Transcript/TUI/Turn/Subagent/工具策略回归 `229 passed`；完整架构守卫 `90 passed, 59 warnings`；导入图、`compileall`、`git diff --check` 通过 |
| 2026-09-01 | 将 Session history cursor 从 `mind_app/history` 迁入 `agent/stores/sessions`，由 CLI/Controller 显式注入数据库路径并删除旧 store/export | History/CLI/TUI/Controller 回归 `181 passed`；完整架构守卫 `91 passed, 59 warnings`；导入图、`compileall`、`git diff --check` 通过 |
| 2026-09-01 | 将 Transcript JSONL 文件 adapter 迁入 `infrastructure/persistence`，删除 `mind_app/history` 包并让入口使用基础设施实现 | Transcript/TUI/Turn/Subagent 回归 `226 passed`；完整架构守卫 `91 passed, 59 warnings`；导入图、`compileall`、`git diff --check` 通过 |
| 2026-09-01 | 将 Subagent fork history adapter 迁入 `agent/adapters/agents/fork_context.py`，改为显式 Transcript reader 注入并删除 runtime 旧模块 | Fork/Subagent/Tools 回归 `34 passed`；完整架构守卫 `92 passed, 59 warnings`；导入图、`compileall`、`git diff --check` 通过 |
| 2026-09-01 | 将跨入口应用展示端口迁入 `agent/ports/presentation.py`，清除 `mind_app.presentation.application` 的旧定义和生产导入 | 展示/CLI/TUI 回归 `704 passed`；完整架构守卫 `93 passed, 60 warnings`；导入图、`compileall`、`git diff --check` 通过 |
| 2026-09-01 | 将 `TextStyle`、`TextSpan`、`StyledBlock` 三个纯展示值对象迁入 `agent/ports/presentation.py`，清除 `mind_app.presentation.models` 的旧定义和生产导入 | 文本/渲染/输出/CLI/TUI 回归 `905 passed`；完整架构守卫 `93 passed, 60 warnings`；导入图、`compileall`、`git diff --check` 通过 |
| 2026-09-01 | 将跨前端应用结果 view 按 Run、工具、计划、补丁、审批、Hook、进度拆入 `agent/application/views`，迁移 `PresentationView/PresentationSink` 并删除旧 `mind_app.presentation.models/contracts` | 展示回归 `158 passed`；Run/TUI 回归 `512 passed`；架构守卫修正后专项通过；导入图、`compileall`、`git diff --check` 通过 |
| 2026-09-01 | 将工具过滤/审批判定与展示分类拆分到 `agent.domain.tool_policy`、`agent.application.views.tool_display`，删除旧 `mind_app.presentation.tool_policy` | 工具策略/渲染回归 `123 passed`；Run/TUI/输出回归 `544 passed`；专项架构守卫、导入图、`compileall`、`git diff --check` 通过 |
| 2026-09-01 | 将 Subscription `TurnApplication` 改为组合根显式 `TurnApplicationFactory` 注入，清除前端对 `runtime_services` 的动态发现 | Subscription 回归 `18 passed`；装配/架构守卫专项 `19 passed`；导入图、`compileall`、`git diff --check` 通过 |
| 2026-09-01 | 将 TUI `TuiTurnInputControl` 改为显式注入 `ProtocolCommandClient`，删除输入控制器对 `runtime_services` 的隐式发现 | TUI 输入/流式命令/中断及前端边界回归 `57 passed`；完整架构守卫 `93 passed, 60 warnings`；导入图、`compileall`、`git diff --check` 通过 |
| 2026-09-01 | 将 TUI session loop 与 CLI durable exec 的 `TurnApplication`/`ProtocolCommandClient` 改为 bootstrap 显式注入，删除前端对 `Mind.runtime_services` 的动态发现 | TUI/CLI 回归 `145 passed, 1 deselected`，时序测试单独通过；完整架构守卫 `93 passed, 60 warnings`；导入图、`compileall`、`git diff --check` 通过 |
| 2026-09-01 | 将 TUI conversation fork/backtrack 的 Protocol Client 改为 session 显式注入，删除 feature 对 `Mind.runtime_services` 的动态发现 | Fork/backtrack/command/stream/input 回归 `211 passed`；完整架构守卫 `93 passed, 60 warnings`；导入图、`compileall`、`git diff --check` 通过 |
| 2026-09-01 | 将流式执行的 Model/Protocol/Effect Journal 依赖沿组合根、根轮次、TUI、CLI、订阅/MCP 与 Subagent 显式注入，删除 `stream.py` 对 `Mind.runtime_services` 的反射 | 流式结果、CLI、TUI、Subagent、MCP 定向回归 `205 passed`；完整架构守卫 `93 passed, 60 warnings`；导入图、`compileall`、`git diff --check` 通过 |
| 2026-09-01 | 将审批调用账本提升为 `agent.ports.ApprovalLedger`，通过 `TurnContext` 注入根轮次、TUI 和 Subagent，删除 `stream.py` 的隐式账本创建和宿主反射 | 流式结果、根轮次、TUI、CLI、Subagent 回归 `213 passed`；端口/public API/legacy 架构断言 `4 passed, 1 warning`；导入图、`compileall`、`git diff --check` 通过 |
| 2026-09-01 | 将流式输出 `SessionFactory` 从 `controller.frontend` 反射兜底改为组合根、CLI、TUI 和 Subagent 显式注入，并沿续跑传递 | 输出准备/流式/TUI/CLI 回归 `211 passed`；缺失工厂门禁通过；导入图、`compileall`、`git diff --check` 通过 |
| 2026-09-01 | 新增 `agent.ports.TranscriptFactory`，通过 `TurnContext` 显式注入根轮次、TUI、Subagent 和 session setup failure，删除 `stream.py` 与执行收束对 `controller.transcripts` 的访问 | 流式/根轮次/TUI/CLI/Subagent/Transcript 回归 `233 passed`；端口边界专项 `4 passed, 1 warning`；导入图、`compileall`、`git diff --check` 通过 |
| 2026-09-01 | 新增 `agent.ports.TurnCleanupPort` 并通过 `TurnContext` 显式注入流式 Finalizer、根轮次、TUI、Subagent 和 CLI，删除 `stream.py` 对 `mind.await_cleanup` 的直接依赖 | 流式/根轮次/TUI/CLI/Subagent 回归 `233 passed`；端口边界专项 `4 passed, 1 warning`；导入图、`compileall`、`git diff --check` 通过 |
| 2026-09-01 | 新增 `agent.ports.PatchPreviewPort` 并通过 `TurnContext` 显式注入工具审批和客户端工具，删除 `stream.py` 对完整 `WorkspaceRuntime.coding` 的直接访问 | 工作区审批/流式/根轮次/TUI/CLI/Subagent 回归 `272 passed`；端口边界专项 `4 passed, 1 warning`；导入图、`compileall`、`git diff --check` 通过 |
| 2026-09-01 | 新增 `agent.ports.RetryStatePort` 并通过 `TurnContext` 显式注入重试展示状态，删除 `stream_setup.py` 对 `controller.frontend.runtime` 的隐式读取 | 流式/TUI/CLI/输出/重试回归 `280 passed`；端口边界专项 `4 passed, 1 warning`；导入图、`compileall`、`git diff --check` 通过 |
| 2026-09-01 | 新增 `agent.ports.TurnAnimationPort` 和终端展示适配器，通过 `TurnContext` 显式注入等待动画生命周期，删除 `stream.py` 对 `frontend.runtime.active` 与 `mind.stop_anim` 的直接访问 | 流式/TUI/CLI/输出/重试/终端动画回归 `280 passed`；端口边界专项 `4 passed, 1 warning`；导入图、`compileall`、`git diff --check` 通过 |
| 2026-09-01 | 新增 `agent.ports.TurnSessionContextPort` 和 `ControllerTurnSessionContext`，删除 `stream_setup.py` 的 Controller、配置、环境和 skills 直接依赖 | 流式/TUI/CLI/输出/重试/终端动画回归 `280 passed`；端口边界专项 `4 passed, 1 warning`；导入图、`compileall`、`git diff --check` 通过 |
| 2026-09-01 | 新增 `agent.ports.TurnSessionStatePort` 和 `ControllerTurnSessionState`，通过 `TurnContext` 显式注入失败上下文/最近回复写回，删除 `stream.py` 对 `ConversationState` 与 Controller 结果属性的直接访问 | 流式/TUI/CLI/输出/重试/终端动画回归 `280 passed`；端口边界专项 `4 passed, 1 warning`；导入图、`compileall`、`git diff --check` 通过 |
| 2026-09-01 | 新增 `agent.ports.ExecutionPolicy` 及判定结果契约，通过 `TurnContext` 显式注入本地执行策略，删除流式工具/审批处理器对 `WorkspaceRuntime` 策略的直接访问 | 核心流式/TUI/Subagent/权限/执行策略回归 `181 passed`；架构专项 `5 passed, 2 warnings`；导入图、`compileall`、`git diff --check` 通过 |
| 2026-09-01 | 扩展 `TurnSessionContextPort` 提供工作区、Hook 告警和持续命令会话，新增 `CommandHookSessionPort`，删除 `stream.py` 对 Controller 会话属性的直接反射 | 流式启动展示/持续命令 Hook/TUI/Subagent 回归 `280 passed`；架构专项 `5 passed, 2 warnings`；导入图、`compileall`、`git diff --check` 通过 |
| 2026-09-01 | 新增 `ApprovalCoordinatorPort`、`ApprovalOutcomePort` 和 `PermissionGrantPort`，通过 `TurnContext` 显式注入审批等待与权限授予，删除流式工具/审批处理器对 Controller 审批和权限属性的直接访问 | 核心流式/TUI/Subagent 回归 `123 passed`；架构专项 `5 passed, 2 warnings`；导入图、`compileall`、`git diff --check` 通过 |
| 2026-09-01 | 删除 `stream_turn` 的 Controller 类型依赖，生命周期 owner 仅作为不透明操作参数传递，保持根轮次/TUI/Subagent 生命周期调用兼容 | 流式/TUI/Subagent 回归 `123 passed`；`compileall`、`git diff --check` 通过 |
| 2026-09-01 | 新增 `TurnForegroundLifecyclePort` 与 Controller 前台适配器，根轮次/TUI 显式传递终端生命周期，删除 `run_foreground_turn` 对 Controller 嵌套属性的直接读取 | 根轮次/TUI/流式/输出回归 `318 passed`；架构专项 `4 passed`；导入图、`compileall`、`git diff --check` 通过 |
| 2026-09-01 | 新增 `TurnEventReportHandle`、`TurnEventReportingPort` 和 `TurnExecutionRuntimePort`，由 `ControllerTurnExecutionRuntime` 适配报告、MCP 会话、工具过滤和清理；执行器删除 `Mind` 类型与宿主反射 | Turn/TUI/Subagent `56 passed`，工具补充回归 `31 passed`；端口专项、`compileall`、`git diff --check` 通过 |
| 2026-09-01 | 新增 `TurnStartResultPort`、`RootTurnSessionPort` 和 `ControllerRootTurnSession`，根轮次准备改为显式会话端口，删除 `prepare_root_turn` 对 Controller 会话资源和 Hook 属性的直接读取 | 根轮次/TUI/Subagent `63 passed`；根准备/执行端口专项 `4 passed, 1 warning`；导入图、`compileall`、`git diff --check` 通过 |
| 2026-09-01 | `run_root_turn` 改为直接消费 `RootTurnSessionPort` 与 `TurnExecutionRuntimePort`，偏好配置、默认权限、审批账本和生命周期 owner 脱离 Controller 入口 | CLI/TUI/根轮次/Subagent `161 passed`；根准备/执行端口专项 `4 passed, 1 warning`；导入图、`compileall`、`git diff --check` 通过 |
| 2026-09-01 | SubagentRuntime 删除 `execution_runtime or controller` 回退，改为读取 Controller 已装配的显式 `turn_execution_runtime` 端口 | Subagent/工具 `39 passed`；端口与导入边界、`compileall`、`git diff --check` 通过 |
| 2026-09-01 | SubagentRuntime 的流式 owner、Hook scope、权限授予和停止清理改为显式端口，删除 `_controller` 状态及流式调用传递 | Subagent/工具 `39 passed`；生命周期专项 `5 passed, 1 warning`；导入图、`compileall`、`git diff --check` 通过 |
