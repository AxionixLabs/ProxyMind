# Agent Harness 迁移计划

状态：阶段 0、阶段 1、阶段 2、阶段 3、阶段 4 已完成；阶段 5 进行中（2026-09-02）

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
- `mind.py::create_workspace_coding` 成为 SandboxClient 和 ProcessSessionManager 的唯一生产
  装配点；`NativeCoding` 强制接收会话运行时，`coding_tools/default_registry` 强制接收
  工作区编码实例，不再通过空参数创建具体平台能力。
- `RootTurnCommandExecutor` 已迁移至 `agent/adapters/turns/root.py`，只依赖冻结命令、
  权限领域值和注入的 operation；CLI、MCP、Subscription 在各自入口绑定 controller，
  `mind_app.runtime.turns.root` 不再拥有 application 命令适配器。
- `run_foreground_turn`、活动状态适配和前台清理顺序已统一归入
  `agent/application/turns/foreground.py`；`agent/ports/frontend.py` 定义可替换前端的
  Activity、Frontend、附件和完成投影端口，worked footer 的具体 renderer 由组合根注入。
- 跨前端应用结果视图已迁移至 `agent/application/views/`，按 Run、工具、计划、补丁、
  审批、Hook 和进度语义拆分；`PresentationView`/`PresentationSink` 归
  `views/contracts.py`，纯文本原语继续归 `agent/ports/presentation.py`。旧
  `mind_app.presentation.models` 与 `contracts` 已物理删除，renderer、output、runtime
  和 TUI adapter 均改用新边界。
- 工具展示策略已拆分：`agent/domain/tool_policy.py` 持有工具过滤和审批专用判定，
  `agent/application/views/tool_display.py` 持有展示分类、阶段和状态文案；旧
  `mind_app.presentation.tool_policy` 已删除，runtime、renderer、TUI 和测试不再依赖旧路径。
- 旧 `mind_app/presentation` 源包已完全删除：运行期错误摘要、工具 view builder 和审批
  修订提案解析归 `agent/application`，终端 renderer、trace、高亮、样式、MCP 状态与 worked
  footer 归 `frontends/terminal`。工具 application view 不再保存终端 title、preview 或
  trace entries，runtime 不再导入终端实现判断工具展示类型。
- 旧 `mind_app/approval` 源包已完全删除：请求模型、策略、协调器、presenter 契约和
  展示摘要归 `agent/application/approvals`；协调器不依赖 interaction 或 observability，
  生产观测回调由组合根显式注入。
- Subscription 的 Turn application 装配已改为显式 `TurnApplicationFactory`：组合根 `mind.py`
  负责绑定持久 application，`frontends/subscription/runtime.py` 不再通过宿主动态属性发现
  `runtime_services`，关闭时继续由订阅执行器回收 application。
- 旧 `mind_app/interaction` 源包已完全删除：会话身份、轮次边界和一次性上下文归
  `agent/harness/sessions`，提示/附件/非交互输入归 `frontends/interaction`，工作区与
  Helix 环境聚合归 `infrastructure/services`。TUI 的 execution runtime 和 root session
  改由 CLI 组合边界显式注入，不再从 `Mind` 动态读取。
- `protocol/schema/identifiers.py` 已恢复独立 wire 所有权，不再为稳定请求 ID 反向导入
  `agent.domain`；协议到 Harness 的反向依赖和初始化环已清零。
- 外部 MCP 的配置规范化、SDK 参数构造、网络预检、工具名和值截断、注册表持久化、
  连接组、启动状态、错误分类和本地/外部 SDK 会话已按职责拆入 `infrastructure/mcp`；
  对应旧模块全部删除，Harness 生命周期 owner 只消费组合根注入的 runtime。
- Helix 环境聚合已迁入 `infrastructure/services/helix_environment.py`，启动展示和前端
  宿主协调已迁入 `frontends/helix/runtime.py`；资源下载显式消费 `UpgradeProgress`，
  Controller 不再保存 `TerminalDesign` 或替前端选择下载展示实现。
- 流式输出净化已迁入 `frontends/output/sanitize.py`，旧 `mind_app/stream_sanitize.py`
  已删除；净化行为与输出适配器同属可替换前端边界。
- TUI 根轮次执行已收敛为组合根绑定的 `TuiRootTurnRunner` 用例，前端不再逐层传递
  execution/session/model/tool/report 等具体端口；会话压缩改由注入的
  `ConversationCompactor` 持有 runtime 事务、Hook 和 Transcript 生命周期，TUI 只保留
  动画、命令与结果展示。

### 最新证据

截至 2026-09-02，本切片已完成：

- Interaction 职责拆分与 TUI 显式端口注入扩展回归：`1651 passed`；先行定向回归
  `262 passed`，端口注入专项回归 `147 passed`。
- Interaction/Protocol/TUI 边界架构专项通过；旧 `mind_app/interaction` 源文件和生产
  导入均清零，`protocol -> agent` 反向边清零。
- 导入图中 `frontends -> mind_app` 从 `17 files / 21 edges` 降至
  `5 files / 7 edges`；本批从 `11 files / 14 edges` 再减少 `6 files / 7 edges`。
- MCP/Helix/输出拆分行为回归分别为 `287 passed`、`193 passed`、`146 passed`；
  新增 MCP 基础设施契约回归 `3 passed`，终端轮次端口扩展回归 `107 passed`。
- MCP、Helix、输出职责架构专项 `6 passed, 4 warnings`；旧源路径与生产导入清零，
  `infrastructure/mcp` 和 `frontends/helix` 均无 legacy 反向依赖。
- TUI 根轮次/压缩边界回归 `65 passed`，CLI 入口回归 `121 passed`，application 扩大
  行为回归 `505 passed`，新增用例端口与 legacy runtime 禁入守卫通过。
- domain/port/capability 补充回归 `179 passed`；完整架构与 baseline 扫描中其余
  `104 passed`，两项过期 owner/导入图断言修正后专项 `14 passed, 1 warning`。
- 导入图中 `frontends -> mind_app` 从 `5 files / 7 edges` 降至 `3 files / 3 edges`；
  `frontends/tui` 已不再导入 `mind_app`，剩余范围仅为 CLI bootstrap/dispatch 与 MCP
  server 对 Controller 的组合和类型依赖。
- CLI 与 stdio MCP 已分别建立最小 `CliApplicationHost`/`CliCommandHost` 和
  `McpApplicationHost` 契约，具体宿主构造器由 `mind.py` 注入；前端树对 `mind_app` 的生产
  导入已从 `3 files / 3 edges` 清零，CLI/MCP/TUI/组合根/baseline 回归
  `167 passed, 1 warning`。
- 前台轮次执行顺序已从 terminal adapter 提升到 `agent/application/turns/foreground.py`，
  worked footer 通过 `TurnForegroundLifecyclePort` 交给 terminal 实现；展示、失败清理、TUI
  活动和根轮次回归 `122 passed`，`mind_app/runtime/turns/root.py` 不再导入前端。
- Controller 的附件状态、Frontend runtime、静默 Subagent 输出、审批 presenter 和轮次完成
  投影均改为显式端口/组合根注入，旧 terminal lifecycle/animation adapter 已删除；Helix
  下载改为显式 `UpgradeProgress`。导入图中 `mind_app -> frontends` 的 `1 file / 6 edges`
  已清零，前后端历史包双向边均为零。
- 四类入口已在同一基线下分别完成启动/恢复证据：CLI Resume、失败恢复和最终清理
  `14 passed`，TUI 启动门禁与 backtrack 事务恢复 `29 passed`，stdio MCP 会话续接和
  清理失败 `14 passed`，Subscription 暂停续接、ready 超时与重启 `18 passed`。
- 外部 MCP runtime/adapters 第一批已完成：五个 SDK/连接生命周期模块迁入
  `infrastructure/mcp`，旧文件和生产导入清零；MCP group `11 passed`，TUI/运行入口
  `95 passed`，工具上下文与结果链路 `63 passed`，架构专项 `3 passed, 2 warnings`。
- 外部 MCP runtime/adapters 第二批已完成：新增工具注册表、外部工具组、动态来源和
  runtime 组合端口，Composite session、tool catalog、tool runtime 迁入
  `infrastructure/mcp`；当前由 `ExecutionResources` 创建并持有 `ToolRuntimeSources`，具体
  registry/runtime builders 由 `mind.py` 注入。
  工具会话回归 `26 + 81 + 35 passed`，Turn/Subagent `51 passed`，工具结果/权限
  `70 passed`，CLI/TUI/清理 `39 + 123 passed`，架构专项 `5 passed, 2 warnings`。
- MCP 结果与展示职责已收口：SDK `CallToolResult` 归一化迁入
  `infrastructure/mcp/tool_results.py`，纯目录查询迁入 `agent/application/tools/catalog.py`，
  进度支持规则迁入 domain，通知/观测内聚到唯一执行路由；旧 `mind_app/runtime/mcp`
  不再包含源码。工具结果/进度/计划 `24 passed`，客户端工具链 `48 passed`，流式结果
  `70 passed`，架构专项 `4 passed, 2 warnings`。
- 本地工具契约与注册状态已完成第一步收口：`ToolHandlerContext`、`ClientTool` 和
  `BuiltinTool` 归 `agent/application/tools`，唯一 `ToolRegistry` 归
  `infrastructure/mcp/local_tool_registry.py`；当前只有 `ExecutionResources` 依赖
  `ToolRegistryPort`，四个旧
  types/registry 模块和包级兼容导出已删除。工具/权限/Turn 回归 `270 passed`，前端并行
  改动回归 `1813 passed`；完整架构守卫 `106 passed / 2 stale assertions`，修正后职责专项
  `5 passed, 3 warnings`，导入图、`compileall` 和差异检查通过。
- 本地工具结果与 planning 能力族已收口：所有本地 handler 返回不可变、JSON 校验的
  `LocalToolResult`，MCP registry 统一适配 `CallToolResult` 并校验工具名和来源；旧
  `client_tools/result.py` 已删除。`plan_steps` 与 `update_plan` 的 schema、校验、描述和结果
  已迁入 `agent/application/tools`，旧能力模块和导入清零。规划/工具/Turn 回归 `230 passed`，
  终端展示回归 `145 passed`，职责专项 `5 passed, 2 warnings`，导入图和编译通过。
- `view_image` 能力族已完成 IO 边界拆分：schema、稳定错误映射与结果投影归
  `agent/application/tools/media.py`，异步读取契约和不可变图片快照归 `agent/ports/media.py`，
  文件解析、大小限制、格式识别与 data URL 编码归 `infrastructure/platform/images.py`。
  Workspace Runtime 在工作区切换时同步替换读取器，旧 `mind_app/client_tools/view_image.py`
  已物理删除。媒体/工作区回归 `51 passed`，扩展工具回归 `242 passed`，职责专项
  `8 passed, 3 warnings`。
- permissions 能力族已完成状态、规则和用例拆分：权限 profile 的规范化、交并、覆盖和
  稳定键归 `agent/domain/permission_profiles.py`，`request_permissions` 的 schema、审批和
  授权写入归 `agent/application/tools/permissions.py`，工具参数授权与 Turn 中断使用独立
  application 契约。grant store 只持有 Turn/Session 状态，工具通过
  `ApprovalCoordinatorPort` 和 `PermissionGrantPort` 注入，不再导入具体 store 或 wire client
  异常；旧 `mind_app/builtin_tools` 和 `native_coding/execution_authorization.py` 已删除。
  权限/策略回归 `116 passed`，工具/审批扩展回归 `114 + 200 passed`，职责专项
  `4 passed, 1 warning`。
- subagent 工具能力族已迁入 `agent/application/tools/subagents.py`：八个控制工具只消费
  新增的 `SubagentControlPort`，不再直接依赖 `SubagentRuntime` 或 mailbox store；Harness
  通过结构化实现端口继续唯一持有 Agent 树、执行、并发和等待状态。消息长度约束提升到
  `agent/domain/agents.py`，application schema 与 mailbox 校验共用同一值；同时收窄
  `AgentMessageEvent` 和 `AgentSnapshot.result` 契约，删除动态结果属性猜测。Subagent/TUI/
  store 回归 `102 passed`，职责专项 `4 passed, 1 warning`。
- workspace coding 已完成 schema 与补丁用例切片：全部 coding schema 迁入
  `agent/application/tools/coding_schemas.py`，`apply_patch` 的权限门禁、参数规范化、结果投影
  和工具定义迁入 `agent/application/tools/patching.py`，执行只通过新增的
  `WorkspacePatchPort` 访问工作区实现。通用本地执行结果信封在 application 边界校验并冻结，
  旧 schema 文件和旧补丁 handler 已删除；补丁/权限快速回归 `74 passed`，schema/职责专项
  `4 passed, 1 warning`，重型 workspace/JS/架构扩展回归 `188 passed / 3 stale assertions`，
  三项过期断言修正后 `5 passed, 1 warning`；同提交纳入的前端整理通过 TUI/terminal
  定向回归 `1551 + 415 passed`。
- workspace coding 已完成进程工具切片：`shell_command`、`exec_command` 和
  `write_stdin` 的工具定义、参数门禁与结果投影迁入 `agent/application/tools/processes.py`，
  只通过 `WorkspaceProcessPort` 调用工作区执行器；sandbox 覆盖规范化、参数组合校验与
  有效模式迁入 `agent/domain/execution_policy/sandbox.py`，基础设施不再拥有纯规则副本。
  旧 `native.py` 三个 handler 同步删除；权限/执行策略/工具与完整架构回归
  `188 passed, 65 warnings`，同提交 Session 类型收窄回归 `29 passed`。
- workspace coding 已完成 JS REPL 与注册表装配切片：REPL 用例、嵌套执行策略和审批编排
  迁入 `agent/application/tools/javascript.py`，工作区执行通过 `WorkspaceJavaScriptPort`，
  MCP SDK 结果在 `infrastructure/mcp/nested_tool_results.py` 校验并转换为稳定 JSON；客户端
  与内置工具工厂统一归 `infrastructure/mcp/local_tool_factory.py`，旧
  `mind_app/client_tools` 源包已整体删除，Controller 不再直接构造具体注册表。
- JS/权限/补丁/工具工厂与 Controller 组合回归 `172 passed`；完整架构守卫除一项新增端口
  白名单过期外其余 `107 passed, 65 warnings`，修正后失败节点及两项职责守卫
  `3 passed, 2 warnings`。导入图、`compileall`、旧导入扫描和差异检查通过。
- 旧 `mind_app/native_coding` 源包已整体退役：纯补丁模型、解析和 delta 归
  `agent/domain/patches`，工作区 context、补丁执行、命令/Shell 与聚合运行时归
  `infrastructure/workspace`；`NativeCoding` 改为 `WorkspaceCoding`，组合根直接注入应用
  资源根和进程会话。两个无调用者 helper 未迁移；工作区行为回归 `169 passed`，职责与
  legacy 清零守卫 `5 passed, 3 warnings`。
- 工具执行编排已完成整体归位：工具开始/结果/进度展示归
  `agent/application/views/tool_execution.py`，MCP 调用和结果执行适配归
  `infrastructure/mcp`，远端 heal 增强归 `infrastructure/services`；稳定执行结果和 adapter
  契约归 `agent/application/tools/execution.py`，客户端工具、效果账本、Hook 与计划执行归
  `agent/harness/tools`，工具 Hook 生命周期归 `agent/harness/hooks/tool_lifecycle.py`。具体
  `McpToolExecutionAdapter` 由 `mind.py` 注入 `RuntimeServices` 并同时贯穿根 Turn 与子 Turn；
  Harness 不再导入 MCP SDK、线上协议请求函数或基础设施实现。旧
  `mind_app/runtime/tools` 源包和 `mind_app/runtime/hooks/tool.py` 已删除。工具/计划/嵌套回归
  `82 passed`，Turn/Subagent 回归 `117 passed`，入口回归 `151 passed`，职责守卫
  `5 passed, 1 warning`。
- Hook 领域生命周期已完成整体归位：压缩前后、SessionEnd、SessionStart、用户输入、Stop
  continuation 与展示通道适配归 `agent/harness/hooks`，运行快照到中立 Hook view 的纯映射归
  `agent/application/views/builders/hooks.py`。旧 `mind_app/runtime/hooks` 源包已删除；Hook、
  压缩和流式回归 `167 passed`，职责守卫 `4 passed`。
- Turn 协议边界第一组已完成归位：模型 Canonical Item 交付和工具结果投递/对账迁入
  `agent/adapters/protocol`，Turn 启动、失败、来源与终态展示迁入
  `agent/application/turns/presentation.py`。新增 `EventReportPort` 后 Harness、Subagent 与 Turn
  契约不再导入具体 `protocol.transport.events.EventReport`；三个旧 stream 模块已删除，定向
  回归 `88 passed`、扩展主链 `326 passed`、职责守卫 `3 passed`。
- Turn 协议边界第二组已完成归位：本地执行/补丁审批规则迁入 application，审批恢复、决定
  回灌和工具批次事件迁入 protocol adapters；不可变 `ExecutionPolicyRequirement` 与 amendment
  由 domain 单一持有，配置 manager 只负责规则 IO 与实现端口。hosted tool output 通过
  `ToolExecutionAdapter` 投影，adapter 不导入 infrastructure；旧 policy/approval/tools 三模块和
  旧 `Exec*` 结果类型已删除。策略/权限/协议回归 `144 passed`，扩展主链 `186 passed`，职责
  守卫 `5 passed, 1 warning`；finalizer 同时改用具名 `TranscriptLifecyclePort`，相关回归
  `20 passed`。
- Turn 协议边界第三组已完成归位：请求环境与输出会话准备迁入
  `agent/adapters/protocol/turn_setup.py`，唯一终态资源收敛迁入
  `agent/harness/execution/turn_finalizer.py`，Turn transcript payload 和开始/终态事实迁入
  `agent/application/turns/transcript.py`。新增 `IdleStatusPort` 后 Harness 不再依赖具体 timer，
  旧 setup/finalize 文件已删除；setup/finalize/Transcript/主链回归 `109 passed`，职责守卫
  `4 passed, 1 warning`。
- Turn 执行编排已完成 Harness 归位：报告租约、MCP 会话、工具过滤、setup 失败事实和清理
  统一迁入 `agent/harness/execution/turn_runner.py`。事件报告生命周期改由 ports 的
  `EventReportLifetime` 声明，Hook scope 通过 `HookScopeProviderPort` 解析；Harness 不再导入
  Protocol Client 具体实现或通过 `object` 猜测 Controller。旧 `turns/executor.py` 已删除，
  Turn/stream/Protocol/Subagent 扩展回归 `204 passed`，职责守卫 `4 passed, 2 warnings`。
- Turn 协议流已完成整体归位：旧 `stream.py` 拆为 Protocol 主适配器、模型请求和稳定中断
  命令，provider/transport 重试状态归 application，idle 调度归 Harness。效果账本路径在组合根
  绑定，流适配器只接收无参 factory；未使用的 lifecycle owner 参数和旧
  `subagent_adapter.py` 同批删除，子轮次复用 `TurnRunner`、`ProtocolSubagentStream` 与通用
  `StreamSubagentExecution`。主流回归 `215 passed`，Controller/根 Turn/Subagent 联合
  `144 passed`，职责守卫 `7 passed, 2 warnings`。
- 根 Turn 与宿主端口已完成收口：根准备/执行迁入 `agent/harness/execution/root_runner.py`，
  Controller 直接实现它当前拥有的 Root Session、Turn Session State/Context 和 Execution
  Runtime 端口；四个只转发调用的 facade 与空 `mind_app/runtime/turns` 源目录已删除。根、流、
  Controller 与 Subagent 回归 `144 passed`，CLI/TUI/MCP/Subscription 入口回归 `140 passed`，
  根 runner 与目录清零守卫通过。
- Conversation Compaction 已完成职责化迁移：远端 payload/SSE 字典归
  `agent/adapters/protocol/compaction.py` 并投影为 `CompactEvent`，Hook、Transcript、取消与
  SessionStart 编排归 `agent/harness/execution/compaction.py`，依赖由 compaction ports 固定。
  旧 `mind_app/runtime` 源目录及测试 seam 已删除；压缩/Hook/根 Turn 回归
  `135 passed, 1 warning`，Harness 无 Protocol Client、基础设施或旧应用导入。
- Hook 配置管理已从 Controller 拆出：`infrastructure/config/hooks.py::HookManager`
  单一拥有配置快照、清单检查、信任/启用状态、执行作用域和 registry 资源关闭；TUI 只消费
  `HookManagementPort`，根 Turn、压缩和 Subagent 只读取 `HookScopeProviderPort`。Controller
  已删除六个 Hook 实现/facade，Hook/TUI/根 Turn/Subagent/Controller 回归 `149 passed`；
  完整架构 `109 passed / 4 stale assertions`，修正既有陈旧路径断言后相关 `4 passed`。
- 根会话、历史和 Transcript 所有权已完成拆分：`RootConversationSession` 单一持有
  `ConversationState`、轮次上下文、最近回复、根会话结束与归档回滚事务；
  `LocalConversationHistory` 在基础设施边界组合 SQLite 游标、分支幂等请求和 Transcript
  reader。CLI、TUI、MCP、Compaction 与根 Turn 直接消费 `RootConversationPort`，Controller
  删除会话、历史和 Transcript 的全部同义方法及持久化属性。`TranscriptEntry`、
  `TranscriptReplay` 归入 `agent/domain/transcripts.py`，旧 `agent/stores/transcripts` 源包删除。

- 受影响行为回归：`2958 passed, 11 skipped`。
- 完整架构守卫：当前全量扫描 `114 passed, 66 warnings`；警告仍来自 Nuitka `glob2`
  的弃用转义。
- `agent_runtime_import_graph.py --write/--check` 通过，导入图已刷新。
- `compileall`、`git diff --check` 通过；旧平铺路径和旧导入扫描无结果。
- 本次根会话/历史/Transcript 最终回归分组 `378 passed`；本地历史真实组合及故障路径
  `12 passed`；CLI/MCP `112 passed`、TUI 启动/监听 `40 passed`、Subscription/Agent
  `42 passed`。
- Turn 执行资源所有权已从 Controller 拆出：`agent/harness/execution/resources.py` 单一持有
  动态 client/builtin registry、外部 MCP owner、Helix 工具链接档位、不可变执行环境快照、
  Composite tool runtime 和事件报告关闭生命周期。Controller 删除八个同义方法及对应状态，
  CLI、TUI、stdio MCP、环境采集与根/子 Turn 全部显式消费 `execution`；具体 registry 和
  runtime builders 仍只由 `mind.py` 注入。定向联合回归 `376 passed`，资源专项补充后
  `33 passed`；完整架构守卫 `122 passed / 1 stale allowlist`，修正后失败节点、资源归属和
  baseline `3 passed`。导入图、`compileall` 和差异检查通过。
- 进程设置所有权已从 Controller 拆出：`infrastructure/config/settings_session.py` 单一持有
  `ConfigSession`、偏好快照、有效权限、刷新 TTL 和并发刷新 generation；权限写入后重新解析
  有效值，偏好失败保留最后有效快照，并发强制刷新只执行一次实际加载。TUI 通过 `settings`
  访问该所有者，CLI 与 Subscription 复用 `RootConversationPort`，Controller 已删除旧
  `pref/config_session/permissions` 属性及三项设置 facade。外部 MCP 同时改为只消费冻结的
  `McpRuntimeContext`，不再持有完整 Controller 宿主。
- 进程与前端活动生命周期已从 Controller 拆出：`ProcessLifecycle` 单一持有停止信号、
  退出码和取消态清理，`FrontendActivity` 单一协调可替换前端活动区与非交互后备动画；
  两者均由 `mind.py` 构造并通过端口注入。CLI、TUI、Subscription、Helix、MCP 和前台 Turn
  已删除对 `task_event/exit_code/animate/anim_manager` 以及八个动画/清理 facade 的访问，
  stdio MCP 同时改为直接读取 `conversation.permissions`，不再静默回退默认权限。
- 本切片入口、设置、MCP 和执行资源联合回归 `308 passed`；设置并发与失败路径专项
  `4 passed`；完整包架构守卫 `114 passed, 66 warnings`，新增 MCP/Controller 所有权、TUI
  架构和 baseline 门禁 `12 passed, 1 warning`。导入图无跨边界循环，`compileall`、导入图
  `--check` 和 `git diff --check` 通过；警告仍只来自 Nuitka `glob2` 的既有弃用转义。
- 进程生命周期、前端活动和四入口联合回归 `605 passed`；过期测试替身已全部迁到具名
  lifecycle/activity/settings 端口。Controller facade、组合根所有权和物理目录专项守卫
  `4 passed`；完整架构守卫 `113 passed / 2 stale assertions`，修正清单后相关职责专项
  `6 passed, 1 warning`。`compileall` 与差异检查通过，警告仍来自 Nuitka `glob2`。
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

0. **旧应用反向前端依赖清零（已完成）**：`frontends -> mind_app` 和
   `mind_app -> frontends` 均已清零并由双向架构守卫锁定。附件输入、Frontend runtime、
   静默输出 Session、审批 presenter 和终端完成投影分别使用具名端口或组合根注入；旧
   terminal lifecycle/animation 文件已物理删除，没有新增聚合兼容 facade。

1. **四入口独立启动/恢复证据（已完成）**：CLI、TUI、stdio MCP 和 Subscription 已分别
   覆盖启动、恢复/续接及关键清理失败；入口均消费组合根绑定的 application/Protocol
   能力，前端不通过 Controller 动态发现运行时服务。

2. **外部 MCP runtime/adapters 拆分（已完成）**：SDK session、外部连接组、启动状态、
   错误分类、Composite session、tool catalog 和 tool runtime 已迁入 `infrastructure/mcp`；
   通用 owner 保持在 `agent/harness/mcp`，具体 runtime 由 `mind.py` 注入。旧文件、旧导入和
   四个 Controller provider facade 全部删除，基础设施不反向依赖 `mind_app`。

3. **MCP 结果与展示职责收口（已完成）**：SDK 结果归一化、纯目录查询、进度策略与
   运行期投递已按 adapter/application/domain/runtime 分开；三个旧模块、旧导入和单调用者
   facade 已删除，`mind_app/runtime/mcp` 源码清零。

4. **本地工具能力族重组（已完成）**：registry、调用上下文、类型契约、稳定结果、
   planning、media、permissions 与 subagent 能力已迁入 `agent`、`infrastructure` 和具名
   ports；workspace coding 的 schema、补丁、进程和 JS REPL 用例已全部迁出 legacy，sandbox
   参数规则归 domain，嵌套 MCP 结果归 infrastructure adapter。旧 `mind_app/client_tools`
   源包整体删除，application 不导入 MCP SDK、具体 `ExecPolicyManager` 或 `NativeCoding`。

5. **Workspace 编码实现归位（已完成）**：补丁 domain 与工作区 infrastructure 已按
   状态/副作用拆分，`mind.py` 保持唯一具体组合根；旧 `mind_app/native_coding` 源包和
   生产导入清零，没有 `native_coding` 同名 facade。

6. **工具执行编排归位（已完成）**：展示 projection、稳定执行契约、MCP SDK adapter、
   远端增强、effect/Hook 生命周期和计划执行已按 application/Harness/infrastructure 边界
   拆分。具体执行器只由组合根注入，嵌套工具回调只传递稳定 JSON；旧
   `mind_app/runtime/tools` 源包和工具 Hook 旧路径已经清零且由架构守卫锁定。

7. **历史包删除收口**：按导入图逐批删除 `mind_app`、`mind_core`、`mind_nova`、
   `engine`，并完成存量配置、历史、报告和打包元数据回读。`mind_app/runtime` 已源码清零，
   Hook 管理、Session/history/Transcript/SessionEnd 以及 Turn 工具执行资源所有权已迁出；
   偏好、权限刷新、外部 MCP 宿主依赖、前端活动和进程生命周期已经拆出。下一步把
   `mind_app/controller.py` 剩余的资源关闭顺序与协作者组合提升到 `mind.py`/Harness owner，
   入口改为消费职责化应用宿主后物理删除历史 Controller。每次迁移都要完成入口切换和旧实现
   删除，禁止整体改名搬运。

每一项的准入条件是：一个完整生产用例、一个关键失败路径、明确状态所有者、旧路径可
删除、架构守卫和 `compileall` 证据。任一条件不足时只更新本计划，不创建空目录。

本次工具执行编排的删除条件已满足：`ToolExecutionAdapter` 返回 SDK-free 稳定结果，
`McpToolExecutionAdapter` 独占 SDK 调用、归一化与嵌套输出投影，Harness 独占 Hook、效果
日志和计划生命周期；根 Turn、续跑与 Subagent 使用同一组合根实例。旧工具包无源码、旧
导入扫描为空，效果核对失败、Hook 拒绝、计划失败和嵌套审批均有回归覆盖。

本次 Hook 生命周期的删除条件已满足：全部事件编排只依赖固定 `HookExecutionScope`、
application 契约和统一 observability，展示 adapter 不拥有 view 构建规则；旧 Hook 源目录无
源码且生产导入清零。SessionEnd 重复关闭、清理失败、Prompt 阻断、Stop continuation 与压缩
后恢复均由现有回归覆盖。

本次 Turn 协议边界第一组的删除条件已满足：模型事件和工具结果只在 protocol adapter 解释
wire schema，application 终态展示只消费中立输出与报告端口；Harness 和 Subagent 的具体
`EventReport` 导入清零，旧 `stream_model.py`、`stream_effects.py`、`stream_presentation.py`
物理删除。provider retry 替换、并发结果去重、effect reconciliation、失败上报顺序和真实入口
组合均有回归覆盖。

本次 Turn 协议边界第二组的删除条件已满足：策略判断只消费 domain requirement 与
`ExecutionPolicy` 端口，协议 event handlers 只依赖 agent 内部职责和 wire schema；具体 MCP
结果归一化留在注入 adapter。旧 stream 三文件、旧 infrastructure 结果类、相关旧导入及迁移
范围内的 `typing.cast`/`type: ignore` 已清零；批次乱序、审批恢复、权限授权、策略拒绝、hosted
输出和根/Subagent Turn 均有回归覆盖。

本次 Turn 协议边界第三组的删除条件已满足：setup 是唯一 wire request 准备边界，finalizer
只持有具名生命周期端口，transcript helper 不解释协议或依赖基础设施；旧 setup/finalize 路径
与生产导入清零。环境注入、回调继承、缺失输出工厂、终态清理顺序、中断 Stop Hook 和
Transcript 回读均有回归覆盖。

本次 Turn 执行编排切片的删除条件已满足：Harness runner 只依赖 application、domain、ports
和 observability，报告生命周期不再从 Protocol Client 反向导入，Hook scope provider 经过
运行时结构校验并统一降级。根 Turn、Subagent、provider retry、协议事件、终态清理和报告池
复用回归均通过；旧 executor 文件、生产导入和相对导入清零，并由职责守卫锁定。

本次 Turn 协议流切片的删除条件已满足：Protocol adapter 目录不依赖 infrastructure、旧应用
或前端，模型请求在边界校验坐标、metadata 和环境快照；效果账本 factory 不暴露数据库路径，
idle timer 不再伪装为平台能力。根与子 Turn 共用同一 stream，Stop continuation 复用全部绑定
能力；旧 stream、旧 Subagent 适配器、旧 idle 路径和生产导入清零，主适配器行数由守卫限制。

本次根 Turn 与宿主端口切片的删除条件已满足：Harness root runner 只依赖 `agent` 内部职责，
Controller 作为现有状态所有者直接满足四个结构化端口，不再保存指回自身的 runtime/session
包装对象。CLI、TUI、stdio MCP、Subscription、根 Turn 与 Subagent 入口均通过；旧
`mind_app/runtime/turns` Python 源文件及生产导入清零，并由目录守卫锁定。

本次 Conversation Compaction 切片的删除条件已满足：Harness 只读取
`CompactionSessionPort` 并消费具名 `CompactEvent`，wire 解析只存在于 Protocol adapter；
Hook scope 通过结构化 provider 统一降级，Transcript 和清理生命周期由 Session port 提供。
成功、失败、空流、取消、前后 Hook 和会话启动均有回归；旧 compaction、旧 runtime 空包与
生产导入清零，没有为测试保留转发函数或 wire seam。

本次 media 切片的删除条件已满足：应用工具只消费 `ImageReaderPort`，具体文件读取器由
`mind.py` 注入并由 `WorkspaceRuntimeOwner` 随工作区统一替换；旧 `view_image.py`、旧导入和
工厂内部路径解析已清零，成功读取、稳定失败和工作区切换均有回归与职责守卫覆盖。

本次 permissions 切片的删除条件已满足：权限算法不再由 store 拥有，申请工具只依赖审批与
授权 ports，本地工具中断不再借用 wire transport 异常；旧 builtin 源包、旧执行授权模块、
旧 schema 和旧导入均清零，授权成功、拒绝、取消、交集、覆盖与 inline 权限均有回归覆盖。

本次 subagent 工具切片的删除条件已满足：application 工具只依赖 `SubagentControlPort`，
具体 Harness runtime 和 mailbox store 均不进入工具包；旧 `client_tools/subagents.py` 和生产
导入清零，八类控制命令、消息交付、等待、关闭与 TUI 视图均有联合回归覆盖。

本次 workspace patch 切片的删除条件已满足：application 只通过 `WorkspacePatchPort` 执行
补丁，结果信封在 application 边界转换为不可变 `LocalToolResult`；旧 coding schema 和
`native.py` 内的补丁 handler 已删除，read-only 拒绝、补丁格式、SHA256 基线与差异跟踪均
沿用现有行为。进程与 REPL handler 仍留在 legacy，未以本切片完成推断整个 coding 迁移完成。

本次 workspace process 切片的删除条件已满足：三个进程工具只消费
`WorkspaceProcessPort`，application 不导入配置实现、平台进程或 legacy coding；sandbox
参数规则由 domain 单一声明，执行策略、Turn 审批与嵌套 JS 调用复用同一校验。旧三个 handler
和 infrastructure 规则定义已删除；JS REPL、MCP 结果适配及嵌套审批的后续切片也已按下述
删除条件完成，因此 workspace coding application 用例现已整体收口。

本次 workspace JavaScript 切片的删除条件已满足：REPL application 用例只消费
`WorkspaceJavaScriptPort`、`ApprovalCoordinatorPort` 和 `ExecutionPolicy`，MCP SDK 对象在
infrastructure 注册表边界转换为经过 JSON 校验的嵌套输出；客户端与内置注册表装配不再由
Controller 或 legacy 能力包持有。旧 `mind_app/client_tools` 源文件和生产导入已清零，JS
持久上下文、嵌套审批取消、本地工具展示及 MCP 图片桥接均保留回归覆盖。

本次 workspace 编码实现切片的删除条件已满足：纯补丁 domain 不依赖 metadata、IO、协议或
legacy 包；`infrastructure/workspace` 不依赖前端、Controller 或历史包，并通过
`WorkspaceCoding` 结构化实现既有工作区 ports。`mind.py` 是 Sandbox、进程会话和应用资源根
的唯一组合点，旧 `mind_app/native_coding` 源文件及导入清零；补丁、冲突保护、命令会话、
Sandbox、JS REPL、用户 Shell 完成/中断与差异跟踪均通过联合回归。

本次工具展示与 MCP 执行适配切片的删除条件已满足：展示模块只依赖 application view、domain
策略和输出端口；MCP SDK 调用、结果归一化及 Hook 响应转换已归 infrastructure；远端 heal
license 和协议流由 services adapter 持有。旧 display/progress/enhancement/router/run 源码与
生产导入清零，工具开始、结果、进度、增强旁路、失败和服务端输出均保留回归覆盖。剩余
`client_call.py` 的 SDK 响应载体将在下一切片随稳定执行结果端口一并消除。

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

本次 TranscriptSink 端口切片的准入与删除条件已满足：`agent/ports/transcript.py` 只依赖 domain，
runtime、Hook、执行器和 Transcript writer 统一从该端口导入；旧
`mind_app/history/contracts.py` 文件与生产导入清零，并通过 Transcript、Turn、Hook
关键路径回归和端口边界守卫验证。TranscriptEntry/TranscriptReplay 已由
`agent/domain/transcripts.py` 持有，文件 Reader/Writer 和历史文件路径在本轮迁入
`infrastructure/persistence`。

本次 Transcript 共享记录切片的准入条件已满足：`agent/domain/transcripts.py` 单一持有不依赖
文件系统的 `TranscriptEntry` 和 `TranscriptReplay`，工具开始/完成归并策略由 domain 提供；
所有跨层消费者通过新路径读取记录值，文件 Reader/Writer 只作为 infrastructure adapter 使用，且
domain 不导入 `mind_app`、`infrastructure` 或展示模块。删除条件是旧
`mind_app/history/transcript.py` 已删除，文件 adapter 由
`infrastructure/persistence/transcripts.py` 独立持有，并通过存量读取、追加写入和路径
失败回归；旧 `agent/stores/transcripts` 源包同步删除，不保留转发导出。

本次 Session history store 切片的准入与删除条件已满足：`agent/stores/sessions/history.py` 单一持有
SQLite 会话游标和待分支请求状态，构造必须接收显式数据库路径，不导入 `mind_app`、
`infrastructure` 或 UI；CLI、Controller、TUI 和测试统一切换到新路径，旧
`mind_app/history/store.py` 与 history 导出删除，并通过会话归档、恢复、过滤和缺失记录
失败路径验证；旧 `mind_app/history/store.py` 与 history 导出已删除。

本次 Transcript 文件 adapter 切片的准入条件：`infrastructure/persistence/transcripts.py`
单一持有 JSONL Reader/Writer、Session 日期路径、编码和损坏记录观测；共享记录值与归约
来自 `agent/domain/transcripts.py`，不得在基础设施复制；所有生产/测试消费者切换新路径，
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

本次 Subagent 输出工厂注入已满足上述条件：`ControllerSubagentExecution` 通过显式
`SessionFactory` 接收输出会话，删除对 `create_silent_output_session` 的直接选择；Mind
只在组合根绑定静默输出，未来前端可以注入不同输出实现。Subagent、工具、MCP、清理和
根入口回归 `167 passed`；Subagent 架构专项 `52 passed, 4 warnings`，导入图、
`compileall` 和 `git diff --check` 通过。下一切片复核 Turn 流的输出端口定义，评估将
`OutputControlPort`、`OutputStatusPort` 和 `SessionFactory` 提升到 Agent ports 而不把
具体 UI 实现带入 Harness。

本次输出控制端口归位已满足上述条件：`OutputControlPort`、`OutputStatusPort`、
`OutputPort` 及 stream/block 模式常量已迁入 `agent/ports/output.py`，所有 Turn、工具、
TUI 和具体输出 sink 消费者已切换，旧 `mind_app/presentation/output/contracts.py` 已删除。
`OutputSession` 与 `SessionFactory` 暂留输出适配器，因为其值对象组合了具体 Content/Presentation
sink；输出/流式/TUI 定向回归 `114 passed`，架构专项 `2 passed, 1 warning`，导入图、
`compileall` 和 `git diff --check` 通过。下一切片拆分 `OutputSession` 的跨层值对象，定义
不携带具体 UI 实现的 session/content 端口后再迁移 `SessionFactory`。

本次输出会话契约迁移已满足上述条件：`ContentOutput`、`ContentSink` 和内容值对象已迁入
`agent/ports/content.py`，`OutputSession`、泛型 `OutputPresentationPort` 和
`OutputSessionFactory` 已归入 `agent/ports/output.py`；所有流式、根轮次、TUI、CLI 和
Subagent 调用点改用 `OutputSessionFactory`，旧 `mind_app/presentation/output/content.py`
与 `session.py` 已删除，输出实现包不再导出契约 facade。输出/流式/TUI/根轮次回归
`122 passed`，架构专项 `4 passed, 1 warning`，导入图、`compileall` 和 `git diff --check`
通过。下一切片复核 `mind_app/presentation/output` 的具体 sink 与 `mind_app/presentation`
渲染器边界，继续把无 UI 状态的适配器归入 `frontends` 或 `infrastructure`。

本次具体输出适配器迁移已满足上述条件：文本、JSONL、静默、终端内容、来源文本、记录器
和输出边界状态已整体迁入 `frontends/output`，CLI、TUI、MCP 与 Subagent 组合入口均切换
到新路径，旧 `mind_app/presentation/output` 源包不再存在。输出/流式/TUI/日志和 Subagent
定向回归、架构归属守卫、导入图、`compileall` 与 `git diff --check` 均通过。下一切片复核
`mind_app/presentation` 剩余 renderer/stream 模块，按纯 view 投影、前端渲染和协议 adapter
重新归类并继续删除 legacy presentation 平铺入口。

本次 application view builder 迁移已满足上述条件：Run、Lifecycle、Progress、Plan、Batch、
Approval、Patch 七类纯 builder 已迁入 `agent/application/views/builders`，runtime、审批、
工具和各前端调用点全部切换，旧 `mind_app/presentation/*_views.py` 已删除；同时以显式
分支替代迁移模块中的 `typing.cast`。展示、流式、工具、Hook、TUI 回归 `706 passed`，架构
专项 `3 passed, 1 warning`，导入图、`compileall` 和 `git diff --check` 通过。下一切片复核
`mind_app/presentation/renderers` 与 `presentation/stream` 的纯投影和终端渲染边界，继续将
可复用投影下沉到 application、将终端实现留在 frontends。

本次 Turn stream 纯投影迁移已满足上述条件：assistant 输出边界判定和 lifecycle display
事件投影已迁入 `agent/application/turns`，模型流和 Turn 编排调用点全部切换，旧
`mind_app/presentation/stream/assistant_boundary.py` 与 `lifecycle.py` 已删除；新的 application
模块不依赖旧包或基础设施。流式与 Run 结果回归 `87 passed`，架构专项 `3 passed`，
`compileall` 和 `git diff --check` 通过。下一切片继续盘点 renderer/stream 终端模块，优先
迁移可完整归入 `frontends/output` 的渲染链，不拆断工具轨迹的内部一致性。

本次前端 runtime 与终端实现迁移已满足上述条件：`Frontend`/`FrontendRuntime` 迁入
`frontends/runtime.py`，Console/JSON/Null application sink 迁入
`frontends/output/application.py`，终端能力、进度、动画、下载、文本净化和布局整体迁入
`frontends/terminal`；CLI、TUI、MCP、Controller、升级和测试调用点全部切换，旧
`mind_app/presentation/application.py`、`application_sinks.py`、`terminal/`、
`terminal_text.py` 和 `text_layout.py` 已删除。广覆盖行为回归 `807 passed`；后续联合复核
`352 passed` 并发现 3 个仍引用旧目录或旧准入清单的过期守卫，修正后定向复核
`3 passed, 1 warning`；`compileall`、依赖图和 `git diff --check` 通过。下一切片迁移剩余
`mind_app/presentation/renderers` 与终端 stream/trace 链到 `frontends`，并拆出仍混在
`tool_views.py` 中的 application builder。

本次 presentation 收口已完成结构改造：`mind_app/presentation` 的 renderer、stream、trace、
高亮、样式、MCP 状态和 worked footer 全部迁入 `frontends/terminal`；HTTP/运行期错误摘要、
工具 view builder、执行策略修订提案和值对象分别迁入 `agent/application/turns`、
`agent/application/views/builders` 和 `agent/application/approvals`，确定性标识派生下沉到
`agent/domain/identifiers.py`。工具 view 删除预渲染的终端 title、preview 和 entries，终端
renderer 在消费纯语义 view 时生成轨迹；runtime 使用 application 展示策略，不再导入
frontend trace。审批 models/factory/policy/presentation 及摘要同步迁入
`agent/application/approvals`，终端/TUI 只保留渲染和交互，消除审批 application 对前端
trace 的反向依赖；随后协调器与 presenter 契约一并迁入，快照通知失败由组合根注入统一
observability 回调，旧 `mind_app/approval` 源包删除。旧 `mind_app/presentation` 没有保留
facade 或源码。第一轮阶段出口行为回归 `926 passed`，职责守卫 `8 passed, 3 warnings`；
审批协调器收口证据见下一条迁移记录。

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
| 2026-09-02 | 将进程停止/退出/取消态清理与前端活动展示迁出 Controller，由组合根注入 `ProcessLifecycle` 和 `FrontendActivity`，删除旧状态字段与动画/清理 facade，并修正 stdio MCP 权限来源 | 四入口、生命周期、活动与流式联合回归 `605 passed`；完整架构 `113 passed / 2 stale assertions`，修正后职责专项 `6 passed, 1 warning`；`compileall`、导入图和差异检查通过 |
| 2026-09-02 | 将根 Session、历史游标、Transcript 和 SessionEnd/归档事务迁出 Controller；历史组合归 infrastructure，Transcript 值与归约归 domain，删除旧 transcripts store 源包 | 根会话/历史/Transcript 最终分组 `378 passed`；历史成功/故障 `12 passed`；四入口 `194 passed`；完整架构 `114 passed, 66 warnings`；导入图和差异检查通过 |
| 2026-09-02 | 将 Hook 配置管理、执行作用域和 registry 资源生命周期迁出 Controller，三类执行入口改用显式 scope provider，TUI 改用独立管理端口 | Hook/TUI/根 Turn/Subagent/Controller `149 passed`；完整架构 `109 passed / 4 stale assertions`，修正后相关 `4 passed`；导入图和差异检查通过 |
| 2026-09-02 | 将 Conversation Compaction 拆为 Protocol adapter、Harness 用例、Session/Client ports 和 application 事件/结果，删除旧 runtime 源目录 | 压缩/Hook/根 Turn `135 passed, 1 warning`；职责守卫、`compileall`、旧导入扫描通过 |
| 2026-09-02 | 将根 Turn runner 迁入 Harness，Controller 直接实现其状态端口，删除四个单调用 facade 并清空旧 runtime/turns 源目录 | 根/流/Controller/Subagent `144 passed`；四入口 `140 passed`；目录与职责守卫通过；`compileall`、旧导入扫描通过 |
| 2026-09-02 | 将 Turn 协议流拆入 Protocol/Application/Harness，组合根绑定效果账本路径，删除无意义 lifecycle owner、旧 stream 和旧 Subagent 适配器 | 主流 `215 passed`；Controller/根 Turn/Subagent `144 passed`；职责守卫 `7 passed, 2 warnings`；`compileall`、旧导入扫描通过 |
| 2026-09-02 | 将 Turn 执行器迁入 Harness，以事件报告生命周期和 Hook scope provider 端口消除 Protocol Client/Controller 反向依赖，并删除旧 executor | Turn/stream/Protocol/Subagent `204 passed`；职责守卫 `4 passed, 2 warnings`；`compileall`、旧导入扫描通过 |
| 2026-09-02 | 拆分 Turn 协议边界第三组：setup 归 protocol adapter、finalizer 归 Harness、Turn transcript 事实归 application，并新增空闲计时器端口 | setup/finalize/Transcript/主链 `109 passed`；职责守卫 `4 passed, 1 warning`；`compileall`、旧路径扫描通过 |
| 2026-09-02 | 拆分 Turn 协议边界第二组：执行策略结果归 domain、本地审批规则归 application、审批/工具事件归 protocol adapters，hosted 输出改由工具执行端口投影；finalizer 改用 Transcript 生命周期端口 | 策略/权限/协议 `144 passed`；扩展主链 `186 passed`；Transcript `20 passed`；职责守卫 `5 passed, 1 warning`；`compileall`、旧路径与迁移范围强制类型声明扫描通过 |
| 2026-09-02 | 拆分 Turn 协议边界第一组：模型事件与工具结果归 protocol adapters，终态展示归 application，并以 `EventReportPort` 替代 Harness 对具体 transport 报告器的依赖 | 定向 `88 passed`；扩展主链 `326 passed`；全量架构 `109 passed / 4 stale assertions`，修正后相关 `5 passed, 2 warnings`；导入图、`compileall` 与旧导入扫描通过 |
| 2026-09-02 | 将 Compact/Session/Turn Hook 生命周期迁入 Harness，拆出纯 Hook view builder 并删除旧 `mind_app/runtime/hooks` 源包 | Hook/压缩/流式 `167 passed`；职责守卫 `4 passed`；导入图、`compileall`、旧导入扫描和差异检查通过 |
| 2026-09-02 | 完成工具执行编排归位：新增 SDK-free 执行契约与组合根 adapter，迁移客户端工具、计划和 Hook 生命周期，删除 `mind_app/runtime/tools` 与旧工具 Hook 路径 | 工具/计划/嵌套 `82 passed`；Turn/Subagent `117 passed`；入口 `151 passed`；职责守卫 `5 passed, 1 warning`；导入图、`compileall`、旧导入扫描和差异检查通过 |
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
| 2026-09-01 | 删除 `mind_app/presentation` 源包，将纯工具 view/错误摘要/审批 application 迁入 `agent`，将终端 renderer/trace/样式迁入 `frontends/terminal`，并移除 application view 的终端预渲染字段 | 展示、TUI、审批、协议效果回归 `926 passed`；职责守卫 `8 passed, 3 warnings`；依赖图、`compileall`、`git diff --check` 通过 |
| 2026-09-01 | 将审批 Coordinator 与 presenter 契约迁入 `agent/application/approvals`，由组合根注入快照失败观测回调并删除 `mind_app/approval` 源包 | 审批/TUI/终端交互回归 `283 passed`，Controller/启动回归 `42 passed`；职责守卫 `4 passed`；依赖图、`compileall`、`git diff --check` 通过 |
| 2026-09-01 | Controller 前端边界收口：新增 Frontend/Activity/Attachment ports，组合根注入具体附件、静默输出、审批与完成投影；删除 terminal lifecycle/animation，并显式注入 Helix UpgradeProgress | 生命周期 `121 passed`；CLI/TUI `236 passed`；MCP `14 passed`；Controller `49 passed`；完整架构扫描 `104 passed`，修正两项过期路径断言后专项 `5 passed`；导入图双向边清零，`compileall`、差异检查通过 |
| 2026-09-01 | 四入口独立启动/恢复证据收口，并将下一迁移切片推进到外部 MCP runtime/adapters | CLI `14 passed`、TUI `29 passed`、stdio MCP `14 passed`、Subscription `18 passed`；覆盖续接、事务回退、ready 超时、重启和清理失败 |
| 2026-09-01 | 外部 MCP SDK 与连接生命周期基础设施化，删除旧 runtime 下的 errors/external/group/local/status 模块 | MCP group `11 passed`、TUI/运行入口 `95 passed`、工具链路 `63 passed`、架构专项 `3 passed, 2 warnings`；导入图、`compileall`、`git diff --check` 通过 |
| 2026-09-01 | 工具会话组合基础设施化：新增工具 runtime/source/registry ports，将 Composite session、tool catalog 和 runtime 迁出旧应用并由组合根构造 | 工具会话 `26 + 81 + 35 passed`、Turn/Subagent `51 passed`、工具结果/权限 `70 passed`、CLI/TUI/清理 `39 + 123 passed`、架构专项 `5 passed, 2 warnings`；导入图和语法检查通过 |
| 2026-09-01 | 退役旧 MCP runtime 源目录：SDK 结果归一化、目录查询和进度语义分别归入 infrastructure、application、domain/执行路由 | 工具结果/进度/计划 `24 passed`、客户端工具链 `48 passed`、流式结果 `70 passed`、架构专项 `4 passed, 2 warnings`；旧路径扫描、`compileall`、差异检查通过 |
| 2026-09-01 | 迁移全部 coding schema 与 `apply_patch` 用例，新增 `WorkspacePatchPort` 和本地执行结果投影，删除旧 schema 与补丁 handler | 补丁/权限/工具上下文 `74 passed`；schema/职责专项 `4 passed, 1 warning`；重型扩展 `188 passed / 3 stale assertions`，修正后失败节点 `5 passed, 1 warning`；同提交前端回归 `1551 + 415 passed`；导入图、`compileall`、差异检查通过 |
| 2026-09-01 | 将三个 workspace process 工具迁入 application，新增 `WorkspaceProcessPort`，sandbox 参数规则归 domain，删除 legacy handler 与 infrastructure 规则副本 | 权限/策略/工具/完整架构 `188 passed, 65 warnings`；同提交 Session 回归 `29 passed`；导入图、`compileall`、差异检查通过；下一切片拆分 JS REPL 嵌套调用边界 |
