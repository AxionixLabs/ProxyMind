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
   stdio MCP 入站适配器及其根轮次/环境能力注入已完成；下一条只补齐 CLI、TUI、MCP、
   Subscription 的独立启动/恢复证据，再进入具体外部 MCP capability/adapters 的职责迁移。
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
工具发现、`mind_exec` 调用、会话续接和关闭生命周期；`mind_app.cli` 仅通过组合根注入
runner，不导入具体 MCP 实现；旧 `mind_app/runtime/mcp/server.py` 文件和生产导入已清零，
并由 MCP 专项回归、CLI 入口回归、架构守卫、导入图和 `compileall` 证明行为一致。

本次 MCP 前端反向依赖收口的删除条件已满足：`RootTurnRunner` 和
`EnvironmentSnapshotProvider` 只作为 `frontends/mcp` 的显式能力端口，生产实现由
`mind.py` 组合根绑定；旧根轮次与环境采集模块不再由 MCP 前端导入，MCP 续接、超时和
失败回执保持原有语义。

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

```text
受影响模块定向 pytest
tests/test_package_architecture.py
scripts/agent_runtime_import_graph.py --write
scripts/agent_runtime_import_graph.py --check
python -m compileall -q agent mind_app infrastructure protocol tests
git diff --check
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
