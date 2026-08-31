# Agent Harness 迁移计划

状态：阶段 0、阶段 1、阶段 2、阶段 3、阶段 4 已完成；阶段 5 进行中（2026-08-30）

这份计划配合 [Agent Harness 架构基线](AGENT_RUNTIME_ARCHITECTURE.md) 使用。
它把从历史包到 `agent` bounded context 的改造拆成可回滚阶段；每一阶段都必须
有代码、测试和导入边界证据，不能以“目录已经移动”作为完成标准。

## 范围与状态权威

- 本计划只管理 ProxyMind 客户端内部 Agent Harness 迁移。
- 阶段号只在本文档内有效，不与任何外部服务计划共用进度。
- 架构拆分、协议兼容或生命周期所有权收敛只是准备性工作；未满足
  本阶段退出条件时，不得标记为该阶段完成，也不得计入后续阶段。
- 每次状态变更必须在本文档记录日期、实现证据、测试结果和未决风险。

## 迁移原则

- 先建立运行契约，再迁移实现；先迁移一个完整用例，再扩大范围。
- 每个阶段保持 `mind.py`、CLI、MCP 和订阅入口可验证。
- `backend/` 是独立打包，整个计划不修改它、不改变它的依赖。
- 只保留必要的过渡入口，并在本文件登记删除条件和截止阶段。
- 线上 `mind.chat` Protocol Client 与本地 Agent Harness 分开验收；不能因为
  `agent.application` 已接入就宣称 TUI、桌面端和 Web 已共享前端协议实现。
- Agent Harness 是主线名称；`agent.harness` 是本地编排内核的正式包名，阶段 4B
  已完成首个包迁移切片。该迁移不改变 `mind.chat` 线上协议，也不把客户端内置
  `server/` 配置服务误认为协议服务端。
- 任何状态所有权不清的代码先停止扩散，不通过共享工具函数掩盖边界问题。
- 目录迁移不采用一对一改名作为完成标准。允许并鼓励按职责把历史模块重组为
  `schema`、`transport`、`client`、`capabilities`、`infrastructure` 和
  `frontends`；每次重组必须同时完成入站导入切换、旧路径删除、边界测试和导入图更新。

## 最终架构目标（四个历史包退役）

阶段 5 的目标已明确为移除 `engine`、`mind_nova`、`mind_core`、`mind_app`，而不是
永久保留其中任一包作为“基础层”或协议 SDK。职责按以下边界落位；允许对每个历史包
按职责重新组合目录，不要求文件级一对一搬迁：

```text
mind.py                         # 稳定启动入口，只调用组合根
agent/                          # 本地 Agent Harness：domain/application/harness/ports
protocol/                       # 独立 mind.chat wire SDK，供 TUI/桌面/Web 复用
frontends/                      # CLI、TUI、MCP、Subscription 适配器
infrastructure/                 # 配置、平台进程、Helix、持久化等具体实现
metadata/                       # 版本、编码和产品展示元数据
server/                         # 客户端内置配置服务，不拥有 Harness 状态
```

职责映射和删除顺序：

| 历史包 | 迁移目标 | 删除前置条件 |
| --- | --- | --- |
| `mind_app` | 运行用例进入 `agent.application`/`agent.harness`；CLI、TUI、MCP、Subscription 进入 `frontends/`；本地副作用进入 `agent.capabilities` | 每类入口至少有一个完整可启动、可恢复用例；生产导入切断后删除旧模块和包入口 |
| `mind_core` | 领域策略进入 `agent.domain.policies`；用例配置进入 `agent.application`；文件/环境适配进入 `infrastructure/config` | 配置读取、策略判断、skills/hooks 生命周期分离并有独立测试；不创建新的 `core`/`shared` 杂物包 |
| `mind_nova` | 请求/响应 schema 进入 `protocol/schema/`；认证、HTTP、SSE 和事件投递进入 `protocol/transport/`；命令操作进入 `protocol/client/`；版本和展示常量进入 `metadata/` | 跨前端 fixture 和版本兼容测试通过；`setup.py`、运行时和测试生产路径均不再导入旧包，且不保留 `protocol.requests` 等扁平兼容层 |
| `engine` | 进程/Helix 端口实现进入 `agent.capabilities`；纯平台复用代码进入 `infrastructure/platform` | 先消除反向业务依赖和循环；所有消费者切换后删除旧模块，不保留转发 facade |

这些目录只在对应迁移切片有完整生产用例时创建。阶段 5 未启用相应切片前只做职责
审计、导入清单和删除条件登记，不进行批量改名或预建空目录。

## 阶段总览

| 阶段 | 状态 | 目标 | 可交付物 | 完成信号 |
| --- | --- | --- | --- | --- |
| 0. 契约冻结 | 已完成 | 固定外部行为和依赖基线 | 导入图、协议清单、风险清单 | 全部阶段 0 退出条件通过 |
| 1. Session 骨架 | 已完成 | 引入 Command/Event 和单写者 | `agent.protocol`、SessionLoop、事件游标 | 一个主动 turn 走完整闭环 |
| 2. 持久化收束 | 已完成 | 迁移事件、快照、效果和 outbox | stores 实现及恢复测试 | 强制退出后可恢复或对账 |
| 3. 能力解耦 | 已完成 | 模型、MCP、Helix、进程通过端口接入 | capabilities 和 adapters | 当前 runtime 子层不导入具体传输实现 |
| 4. 多入口与协议前端迁移 | 已完成 | 将运行时子层收敛为 Agent Harness，统一入口提交命令，并建立可复用的 `mind.chat` Protocol Client | `agent.harness`、application 接入、Protocol Client、Canonical Event/Item reducer 和前端适配器 | 本地入口共享 Harness 语义；TUI/桌面/Web 可消费同一线上事件 |
| 5. 历史包退役 | 进行中 | 按职责重组并删除 `engine`、`mind_nova`、`mind_core`、`mind_app` 四个历史包 | 分层 `protocol/` SDK、独立 `observability/`、前端/基础设施边界、旧包删除清单和导入图证据 | 生产导入图不再指向四个历史包；`mind.py` 可启动、可恢复，协议 SDK 和所有前端仍可复用 |

## 阶段 0：契约冻结

状态：已完成（2026-08-28）

### 工作项

1. 记录当前 CLI、MCP、订阅、工具结果和历史记录的外部 schema。
2. 生成非 `backend/` 的 Python 导入图，标出 `engine` 反向依赖和循环依赖。
3. 给主动 turn、工具调用、审批、取消、断线恢复建立端到端基线测试。
4. 标记现有 `Mind` 控制器中真正拥有状态的字段；禁止新增字段继续堆入控制器。

### 已完成证据

- 主动 Turn 入口已收敛到 `mind_app/runtime/turns/root.py`，但仍是旧运行时
  用例，不视为 SessionLoop 或 Command Gateway。
- 订阅、外部 MCP、本地服务、事件报告和工作区编码资源已分别收敛到
  具有完整启停语义的 runtime owner；配置服务生命周期已回到 CLI 组合根。
- 控制器已删除对应的任务、连接和资源别名，
  `tests/test_package_architecture.py` 防止已移出所有权回流。
- 当前完整测试基线为 `2808 passed, 13 skipped`；`py_compile` 和
  `git diff --check` 通过。

### 完成清单

- [x] 生成并提交非 `backend/` Python 导入图，标注循环、`engine` 反向依赖和临时跨层边。
- [x] 固化 CLI、MCP、Subscription、工具结果和历史记录的外部 schema 目录。
- [x] 为四类入站入口补齐 Command/Event 映射草图，明确身份、幂等键和终态。
- [x] 将主动 Turn、工具、审批、取消和断线恢复测试整理为可重复执行的基线矩阵。
- [x] 记录 `backend/` 的目录、导入和构建基线，证明 Agent Harness 迁移没有改变其边界。

### 出口条件

- 基线测试在仓库虚拟环境通过；
- 每个外部入口都有对应的 Command/Event 映射草图；
- `backend/` 的目录、构建和测试结果与本阶段之前一致。

退出结果记录在 `AGENT_RUNTIME_PHASE0_BASELINE.md`。阶段 0 的全部待完成项和
退出条件已通过，因此阶段 1 可以创建首个接入主动 Turn 的顶层 `agent` 切片。

## 阶段 1：Session 骨架

状态：已完成（2026-08-28）

现有 `root.py`、Turn executor 和各 runtime owner 只是本阶段的输入边界，
新切片已接管主动 `exec` 和 TUI 普通 prompt 的命令、Session 队列、状态事件与
终态投影；MCP、Subscription 和 TUI 其他操作仍由旧运行时直接编排。

### 工作项

1. 实现只包含类型和序列化规则的 `agent.protocol`。
2. 实现 `SessionLoop` 和 `RunActor`，先将现有主动 turn 包装成一个命令处理器。
3. 将 `stream_turn` 的输入解析、状态转移、模型流处理、工具效果和输出投影
   分成独立的调用点；不在第一步重写模型逻辑。
4. 引入 Event Queue，让 TUI 和 CLI 通过事件投影得到结果。

### 当前切片证据

- [x] `SubmitTurnCommand` 和 `RunEvent` 只依赖标准库，载荷经过 JSON 校验、
  深复制和冻结；本地身份不复用线上 `cid/sid/turn_id/event_seq`。
- [x] `SessionLoop` 使用唯一 worker 串行执行 `RunActor`，按 command id 或
  idempotency key 去重，并为每个 Run 生成从 1 开始的连续事件序号。
- [x] CLI `exec` 已通过 `agent.application.submit_turn` 提交命令，现有
  `run_root_turn` 作为注入能力执行；成功、失败和用户取消路径均有测试。
- [x] `RunActor` 终态事件携带完整 `RunResult` 协议快照，application 校验唯一
  终态、连续序号、状态和退出码；CLI `exec` 的退出码已改从该事件投影读取。
- [x] `stream_turn` 的回调校验、请求参数固定、执行环境/skills 解析和输出会话
  装配已收敛到 `stream_setup.py`，当前请求与停止 Hook 续跑参数分开持有。
- [x] `stream_turn` 的协议终态、工具中断、对账要求和本地异常已统一写入
  `StreamTurnOutcome`；会话记录、Stop Hook、观测和 `RunResult` 使用同一终态优先级。
- [x] `ModelStreamEventHandler` 消费 Protocol Client 的 current/active/audit Item 投影，
  只拥有展示交付与 Transcript revision 水位；retry、正文、attempt、展示替换和
  sources 的业务状态由 Canonical Item reducer 统一裁决，旧 `SegmentTracker` 已删除。
- [x] `ToolResultDelivery` 独占单轮工具结果冻结快照和同键并发去重，统一执行
  权威状态查询、原请求重试以及本地/服务端 Effect 对账。
- [x] `StreamTurnFinalizer` 独占临时授权/审批清除、流结束通知、模型缓冲提交、
  终态 transcript、Stop Hook 和 idle/output 关闭顺序，并隔离 Stop Hook 失败。
- [x] `StreamTurnPresentation` 独占启动、失败、完成和未完整投影，使用具名模式区分
  本地失败上报、协议终态展示和权威 usage/meta；旧的两层失败转发模块已删除。
- [x] 根目录 `PROTOCOL.md` 已作为线上唯一规范源接入：可展示事件严格校验
  `item_id/item_kind/item_status`，控制事件拒绝 Item 字段；`tool.calls.done` 不再
  继承 start 的 `ready/timeout_sec` 语义，旧 `tool` 名称别名已移除。
- [x] `stream.gap` 已建模为非持久控制事件：`retained_prefix` 只推进到服务端给出的
  回放下界，`internal` 立即停止交付；当前 `ProtocolEventCursorStore` 按 `cid + sid`
  跨 Turn 持有线上 `event_seq`，不与本地 Run sequence 混用。
- [x] `agent.harness` 核心没有导入 `mind_app`、`mind_core`、`mind_nova`、
  `engine` 或 `server`；生成导入图只新增 `mind_app -> agent`。原
  `agent.runtime` 已在完整用例迁移后删除。
- [x] TUI 普通 prompt 已从 Event Queue 投影读取终态；`TurnApplication` 通过
  `SessionRuntimeOwner` 管理长生命周期 SessionLoop，关闭、并发提交和取消均由
  application 公开入口收束。TUI 流式展示仍使用现有 OutputSession。

### 出口条件

- 同一个 Run 的并发提交不会产生交错状态序列；
- 取消和关闭能等待 SessionLoop 收束；
- 主动 `exec` 至少有成功、工具失败、用户取消三条测试路径。

退出条件已全部满足：同 Session 并发提交的 FIFO 与逐 Run 连续序号、取消后
SessionLoop 重建、并发关闭等待屏障，以及 TUI Event 终态投影均有定向测试；
CLI 既有成功、工具失败和用户取消路径继续通过，据此启用阶段 2。

## 阶段 2：持久化收束

状态：已完成（2026-08-28）

### 工作项

1. 将 `LocalEffectJournal` 的状态机提升为正式 store，并为每个效果分配稳定
   `effect_id` 和请求指纹。
2. 写入事件、快照和 outbox 的本地事务；定义事件游标和快照版本。
3. 将 Agent Graph 检查点与 Session/Run 历史分开存储，避免恢复时互相污染。
4. 增加进程终止、网络超时、未知结果和重复 dispatch 的恢复测试。

### 已完成证据

- [x] `LocalEffectJournal` 已从 `mind_app/runtime/durable_effects.py` 迁入
  `agent/stores/effect_journal.py`，旧文件已删除且没有转发 facade；效果端口要求
  稳定 `effect_id`、SHA-256 指纹和 `safe/manual` 重放策略，不确定结果进入对账。
- [x] `SQLiteRunStore` 使用独立 `runtime.db`，schema 与 snapshot 版本均为 1；
  `run_events`、`run_snapshots`、`run_outbox` 和 `run_facts` 在同一个
  `BEGIN IMMEDIATE` 事务中推进，序号、身份和状态转移均在写入门禁校验。
- [x] `RunActor` 在进入队列和调用 executor 前分别持久提交 queued/running；
  `SessionLoop` 启动时读取未终结快照，只有 queued 原命令允许安全再派发，
  running/waiting_effect/超时进入 reconciliation，waiting_approval 和 paused
  保持显式等待或恢复动作。
- [x] 最终结果投影为 `final_result`、`assistant_message`、`tool_result`、
  `approval_decision` 和 `evidence_reference` 事实，可按 Run 回读；流式 token
  不作为恢复前提。
- [x] `runtime.db`、`effects.db`、`agents.db` 和 `history.db` 分离；CLI 与 TUI
  生产组合使用稳定哈希本地 Session 身份，线上 `cid/sid` 不写入本地身份。
- [x] `/tool-result/status` 客户端契约门禁使用正式字段
  `execution_deadline_at`，拒绝文档旧字段 `expires_at`；交互型工具要求截止点为
  `null`。
- [x] AST 边界测试覆盖 `agent/stores` 不导入历史包，并强制 `mind_app` 只能通过
  `agent.application` 公开入口调用新运行时；生成导入图保持只有
  `mind_app -> agent` 的正向边。

### 出口条件

- 重启后可以恢复 queued/running/waiting/paused 状态；
- 未知外部结果进入人工对账，不自动重复高风险动作；
- 最终消息、工具结果、审批决定和证据引用可读取；流式 token 不作为恢复前提。

退出条件已全部满足：原子事务、版本门禁、四类恢复动作、超时与未知结果对账、
重复/冲突身份、最终事实回读以及 CLI/TUI 真实组合路径均有定向测试；全量测试
`2876 passed, 11 skipped`，导入图、语法检查和 `git diff --check` 通过。
据此启用阶段 3；阶段 2 不再接受新的持久化职责扩张。

## 阶段 3：能力解耦

状态：已完成（2026-08-29）

当前切片：先将主动 Turn 的模型事件流从旧请求模块迁到具名 `ModelCapability`。请求数据必须
冻结为协议对象，重连状态和审批恢复保持独立回调端口；`mind.py` 组合根创建
进程级 `RuntimeServices`，CLI、TUI 和 MCP 入口只接收依赖。MCP、Helix、process
和 filesystem 端口均已具备真实或内存实现，并通过 capability 层的稳定错误语义
对外收口。

### 当前证据

- [x] `ModelStreamRequest` 位于 `agent.protocol`，只接收并冻结 JSON 兼容的模型
  坐标、配置、消息、工具、附件、环境快照、metadata 和请求选项；重连状态和审批
  恢复 callback 不进入协议对象。
- [x] `ModelCapability` 明确事件迭代、服务端水位和关闭生命周期；
  当前 `MindChatProtocolClient` 是唯一把冻结请求翻译到现有远端传输的实现。
- [x] `stream_turn` 在进入事件处理前校验 `ModelEventStream` 的异步迭代、关闭、
  终止原因和服务端游标端口，并在 `finally` 中等待 `aclose()` 完成，关闭失败只
  进入结构化观测，不跳过本轮终态收尾。
- [x] `mind.py` 是唯一具体组合根，创建一次 `RuntimeServices` 并显式传给 CLI、
  TUI 与 stdio MCP；主动 Turn、停止 Hook 续跑和子 Agent 从同一容器取得模型能力。
- [x] `agent.application` 不再导入 `agent.composition`、stores 或 capabilities；
  CLI/TUI 的 Turn application 与工具效果账本也通过注入工厂创建，不存在导入时
  装载具体 adapter 或工具执行器自行查找全局工厂的路径。
- [x] `mind_app` 已无旧协议请求模块导入；TUI 使用的结束原因类型也已
  迁入 `agent.protocol`。AST 边界测试禁止旧运行时重新直连模型传输。
- [x] `agent.capabilities` 只依赖 `agent` 内部边界；迁移期正式协议传输依赖已集中到
  `agent.adapters.protocol_client`，两者均禁止导入 `mind_app`、`mind_core`、`engine`、
  `server` 或 `backend`。
- [x] `ProtocolModelEventStream` 覆盖远端流异步迭代和幂等关闭，将 HTTP、超时、
  协议及未知异常转换为稳定的 `ModelCapabilityError`；`RunResult` 保留
  `error_code/error_details`，`RunActor` 直接收到能力异常时也写入具名 `run_failed`
  事件，避免错误只停留在 UI 文本。
- [x] `TurnRetryingEvent` 和 `TurnFailedEvent` 已保留服务端 `error_type`、来源、
  HTTP 状态和可重试性；新格式出现时校验 `error_type == error.type`，并将失败分类
  投影到 `RunResult.error_code/error_details`。缺少附加字段的历史事件暂时兼容，
  待服务端全量启用后再收紧缺省路径。
- [x] `agent.protocol.ModelEvent` 固化模型事件跨 capability 边界的最小坐标契约，
  `ModelEventStream` 不再以 `Any` 暴露事件；具体传输事件由 `protocol` adapter
  解析和返回，runtime 只依赖协议形状。
- [x] `ProtocolModelEventStream` 在 adapter 边界执行 `ModelEvent` 运行时门禁，
  非协议对象统一转换为 `model_protocol_error` 并完成流关闭，不再使用
  `typing.cast()` 绕过真实契约。
- [x] `agent.protocol.validate_model_event` 统一校验模型事件公共字段：普通事件
  要求 `mind.chat`、完整会话坐标、正数 `event_seq` 和 `presentation_epoch`；
  `ping` 保留无业务坐标例外，非法字段统一收敛为 `model_protocol_error`。
- [x] `agent.protocol` 与 `agent.ports` 的冻结 JSON 解冻边界使用具名的
  `ThawedJsonValue` 和顶层对象校验，移除生产代码中全部 `typing.cast()`，
  不改变请求、错误回执或事件载荷结构。
- [x] `agent.protocol` 定义冻结的 `McpToolDefinition`、`McpToolResult`；MCP
  调用、Helix 启停、进程读写和文件访问均由独立 capability port 表达，领域层
  不依赖 SDK、TUI 或操作系统对象。
- [x] `InMemoryMcpCapability`、`InMemoryHelixCapability`、
  `InMemoryProcessCapability` 和 `InMemoryFilesystemCapability` 覆盖无副作用的
  domain/runtime 测试路径；`LocalProcessCapability` 和
  `LocalFilesystemCapability` 提供本地生产实现，受限进程必须显式注入 sandbox
  launcher。
- [x] `CapabilityError` 统一错误码、消息、`retryable` 和冻结 JSON `details`；
  `ModelCapabilityError` 复用同一错误基类，能力失败可直接写入持久事件。
- [x] 全量测试 `2926 passed, 11 skipped`；新增 capability 定向测试 30 项，导入
  边界、语法和 diff 检查通过。

阶段 3 的旧入口边界已登记，不在本阶段伪装为已删除：

- `protocol` 的具体模型事件类作为 wire decoder 的私有实现；跨边界唯一语义是
  `agent.protocol.ModelEvent`。运行流只依赖该类型，不把 wire 事件类泄漏到 Harness。
- `engine.ServerManage`、`ProcessSessionManager` 和 `SandboxClient` 仍作为平台
  adapter 保留，但生产生命周期已由显式 `HelixCapability`/`ProcessCapability` 注入
  端口拥有；Sandbox sidecar 仅服务受限模式，阶段 5 再按删除条件评估物理退役。
  模型轮次不会隐式启动 Helix。

### 工作项

1. [x] 定义 model、MCP、Helix、process、filesystem 的最小 capability port。
2. [x] 将模型传输请求实现拆到 capability adapter；类型和跨边界事件形状留在
   protocol。
3. [x] 将平台差异收口在 capability 实现边界：标准库本地实现不依赖 `engine`，
   受限进程通过显式 launcher 注入；完整权限进程与 Helix 已经由显式 adapter 接入，
   Sandbox sidecar 的物理退役留给阶段 5。
4. [x] 为每个 capability 提供 fake/in-memory 实现，用于 domain/runtime 测试。

### 出口条件

- `protocol`、`domain` 和当前 `agent.runtime` 编排核心可脱离网络和 TUI 执行测试；
- capability 失败能转换为具名、可持久化的错误事件；
- Helix 的启动、连接和回收不被模型轮次代码隐式触发。

阶段 3 复核结论：上述出口均已满足；旧入口的 adapter 迁移不再阻塞能力层，
转入阶段 4 的多入口接管。

## 阶段 4：多入口与协议前端迁移

状态：已完成（2026-08-30）

阶段 4A（入口接入）当前切片：stdio MCP、subscription 和 CLI `exec` 已通过注入的
`TurnApplication` 提交 `SubmitTurnCommand`，并以 `RunResultProjection` 作为终态
观测或回执的权威来源。入口只负责请求校验、工作区、权限、WebSocket mailbox
和前端生命周期；Session 状态、取消、事件序列和 application 关闭由统一入口拥有。
TUI 已在中断收束处优先读取同一 projection，但正文展示仍由既有 EventReport
生命周期承载。旧 `run_root_turn` 仅作为显式执行器适配。

阶段 4B（Harness 与协议前端边界）已完成：SessionLoop、RunActor 和 Session 所有者
已从 `agent.runtime` 迁移到 `agent.harness`，并通过完整用例、恢复/取消/并发测试。
最新 `exec_env` 契约插入切片已收口：模型请求使用显式不可变环境快照，本机采集和
Helix provider 聚合由进程级注入的 capability 负责；CLI、TUI、MCP 和 subscription
均在 queued 命令持久化前冻结快照。Protocol Client 的首个生产切片已经建立：
模型请求显式携带 Session/Turn 坐标，wire 映射、协议事件坐标门禁和结算后 Session
游标提交已从 `Mind`/控制器迁入 `agent.adapters.protocol_client`。Canonical Item
reducer 的首个生产切片也已接入事件交付：正文/工具 Item、状态单向收敛、provider
retry 和 presentation supersede 形成统一 active/audit 投影。下一切片让 TUI 消费
该投影。审批快照已由同一 reducer 按权威水位收口；RunResult、Stop Hook 和最后回复
记忆已改读 `ModelEventStream.assistant_text`；delta、sources 和 Transcript presenter
也已改为消费 current/active/audit Item 投影，旧 `SegmentTracker` 已删除。后续继续
工具/审批交互与协议命令面已收敛到同一 Protocol Client；本地策略、工具执行和
UI 交互仍作为明确的前端/runtime adapter 保留，不复刻 Harness 状态机。

### 当前证据

- [x] `MindMcpRuntime.open()` 从 `RuntimeServices.create_turn_application()` 创建
  长驻 application；测试替身仍可显式注入 application，不再让 MCP 入口自行装配
  store 或模型能力。
- [x] `mind_exec` 为每次请求创建冻结的 `SubmitTurnCommand`，使用远端会话 `sid`
  作为本地 Session identity；根轮次执行器只接收已解析的消息、权限和附件。
- [x] MCP runtime 关闭时先关闭 application，再释放控制器资源和报告；超时或取消
  通过 application 的 Session runtime 收束，不释放调用锁前留下活动 Run。
- [x] 新增 application 边界测试，验证命令坐标、执行器适配和关闭顺序；MCP 与
  architecture 定向测试通过。
- [x] `MindMcpExecutionResult.to_dict()` 优先输出 application 的
  `RunResultProjection.result`，原始 `RunResult` 只保留为兼容的结构化对象和错误
  访问入口。
- [x] `AgentRuntime` 生产组合从 `RuntimeServices` 创建单个长驻
  `TurnApplication`；`AgentExecutor` 将远端 `message_id/call_id/sid` 冻结为稳定
  command/run/session identity，metadata、附件和 extras 随命令快照保存。
- [x] 订阅执行超时和取消均包在 application 提交边界外，保证
  `TurnApplication.submit()` 的 Session 取消收束生效；运行时关闭时回收其拥有的
  application，显式注入的测试执行器不被越权关闭。
- [x] 新增 subscription 命令冻结、组合和关闭测试；订阅旧行为定向测试共 `39
  passed`。
- [x] subscription 终态分类使用 `TurnApplication.submit()` 返回的
  `RunResultProjection`，不再以执行器返回对象决定完成、失败或中断；原始结果仅
  保留兼容错误文本。
- [x] CLI `exec` 的 `command.complete` 观测使用 projection 的终态，不再从原始
  `RunResult.status` 重新解释命令结果；退出码和结构化结果继续保持原有兼容入口。
- [x] `agent.runtime` 已重命名为 `agent.harness`，`session_runtime.py` 已重命名为
  `session_owner.py`；`agent.application`、架构边界测试和 SessionLoop 测试均已切换，
  旧包没有生产导入。
- [x] 客户端 `exec_env` 已按正式协议形成每 Turn 快照并在 continuation 中复用；
  `ModelStreamRequest.environment_snapshot` 将它作为一等不可变输入，通用 `options`
  拒绝 `exec_env`，远端 model adapter 只在 wire 边界显式映射该字段。
- [x] `EnvironmentSnapshotCapability` 由 `RuntimeServices` 进程级注入；本机 shell/
  tool 静态事实由 capability 实例缓存，每次 Turn 仍生成独立 `snapshot_id`，旧
  `mind_app/runtime/environment/exec_env.py` 已删除且没有兼容 facade。
- [x] CLI、TUI、MCP 和 subscription 在创建 `SubmitTurnCommand` 前捕获环境；命令
  深冻结快照并将其纳入序列化和 SHA-256 意图指纹，SQLite queued redispatch 测试
  证明执行器读取持久命令中的原快照，不在恢复进程重新采集。
- [x] `MindChatProtocolClient` 已作为 `ModelCapability` 的生产 adapter 接入组合根；
  `ModelStreamRequest` 显式冻结 `cid/sid/turn_id` 并拒绝 options/metadata 重复声明
  协议坐标，wire 参数翻译不再依赖控制器隐式状态。
- [x] `ProtocolEventCursorStore` 和坐标校验进入 Protocol Client；只有结束原因为
  `settled` 的流提交 Session 水位，fatal/cancelled/protocol_error 均保留原确认点。
  旧 `mind_app/runtime/turns/delivery.py` 和单独的 model capability facade 已删除。
- [x] `CanonicalItem` 是冻结、JSON 安全且不包含 UI/Transcript/工具句柄的前端快照；
  `ModelEventStream` 公开 active 与 audit 两种 Item 投影，具体 reducer 不依赖
  `mind_app`、`mind_nova` 事件类、`prompt_toolkit` 或 `OutputSession`。
- [x] reducer 已接入 `ProtocolModelEventStream` 的 yield 前门禁：正文 delta/done/meta
  聚合、工具调用/输出分离、Item 状态回退拒绝、同 round provider retry 和跨 Worker
  presentation supersede 使用一套规则。旧展示版本保留审计但不进入 canonical 正文。
- [x] capability 公共事件校验已接受无 `event_seq` 的 `stream.gap` 非持久控制信号，
  retained-prefix 可以继续交付且不创建 Item；内部缺口仍由底层传输停止连接。
- [x] Protocol Client 在 attach 前始终接收审批快照并先写入 Canonical Item reducer；
  `pending_approval_items` 只暴露权威状态仍为 pending 的审批，旧 pending 回放不能
  重开 resolved/cancelled Item。快照 `last_event_seq` 不推进 Session 确认游标，旧
  `ApprovalEventHandler` 仅在归约完成后处理交互和决定提交。
- [x] `ModelEventStream.assistant_text` 成为最终正文端口；Turn 收尾显式把它传给
  RunResult 和 Stop Hook，根会话最后回复记忆读取同一快照。旧
  `ModelStreamEventHandler`/`SegmentTracker` 的正文聚合 API 已删除。
- [x] 运行流测试替身接入真实 `CanonicalItemReducer`，并修复 provider retry 后迟到
  的旧 Item 被误归入新 attempt 的问题；旧 Item 现保留审计但不会污染 canonical 正文。
- [x] `ModelEventStream.current_item` 暴露最近事件已归约的 Item revision，忽略或控制
  事件返回空；模型 presenter 只据此生成 delta/完成展示，并按 audit Items 幂等提交
  Transcript created/updated/superseded，不再复制 Item/attempt 状态机。
- [x] canonical sources 从 active text/builtin Items 按首次出现去重，TUI 结果展示直接
  消费该投影；旧 built-in source 回填和 `mind_app/stream_state/segment.py` 已删除，
  reducer 与端到端运行流测试覆盖 metadata、替换过滤和来源去重。
- [x] `ProtocolCommandClient` 接管运行流的 `turn/interrupt`、`tool-result`、状态查询、
  `tool-approval`、`effect/reconcile`、steer、输入对账、Turn 状态、fork 和托管工具
  续期传输；`stream.py` 的生产调用不再直连这些 wire 函数，生产组合由同一个
  `MindChatProtocolClient` 同时提供模型流和命令端口。
- [x] `RootTurnCommandExecutor` 统一 CLI、stdio MCP 和 Subscription 的根轮次执行器
  适配；三类入口只负责解析请求、生命周期和结果回执，Turn/Run 状态继续由
  `TurnApplication`/`SessionLoop` 拥有。
- [x] `ProcessCapability` 已接入完整权限模式的进程会话；受限模式继续通过显式注入的
  Sandbox sidecar 后端执行，`WorkspaceRuntimeOwner` 负责共享能力的关闭，
  不把平台进程句柄带入 Protocol 或 Harness。
- [x] `ServerManageHelixCapability` 已接管生产 Helix 启动、重启、停止和关闭；
  `ServiceRuntimeOwner` 只通过 capability 编排生命周期，模型轮次不隐式启动服务。
- [x] TUI 输入控制、fork、模型正文、sources、工具结果、审批和效果核对均通过
  Protocol Client 或明确的本地 capability adapter；本地策略与 UI 交互不拥有服务端
  Turn 状态，也不绕过 Protocol Client 写入协议命令。
- [x] TUI、桌面端和 Web 共用的 Canonical Event/Item fixture 已覆盖一致投影、工具批次、
  approval pending、tool output、来源聚合、结算游标和 `turn.logical_settled`。

阶段 4 出口复核已通过：CLI、TUI、MCP 和 Subscription 均通过统一 application/
Protocol Client 边界提交或观察运行；运行结果和 Canonical Item 均可从各自权威 Event
游标重放；WebSocket 回调不启动模型或工具；环境快照在 queued、continuation、provider
retry 和 redispatch 中复用同一 `snapshot_id`。协议 wire decoder 的结构重组、legacy
错误类型兼容导入、Sandbox sidecar 和根轮次显式 executor 都已登记为阶段 5
的退役路径，不构成阶段 4 的状态所有权或协议绕过。

### 工作项

1. [x] CLI 将参数、stdin、resume 和退出处理映射到 application command。
2. [x] 建立独立 Protocol Client：模型流、坐标门禁、结算游标、工具/审批命令、steer、
  对账、状态、fork 和托管工具续期均由同一端口提供；Canonical Item reducer 的
  正文/工具、展示替换和审批快照切片已接入；实现不依赖 `Mind`、`prompt_toolkit` 或
  `OutputSession`。
3. [x] 将 `agent.runtime` 的完整生产切片迁移到 `agent.harness`，同步更新
   `RuntimeServices` 的内部依赖和测试导入；旧包已删除，没有兼容 facade。
4. [x] TUI 改为 Protocol Client 的一个渲染/交互适配器；模型正文、delta、sources 和
  Transcript presenter 已接管且 `SegmentTracker` 已删除；工具/审批命令已走
  Protocol Command Port，本地策略、工具执行和 UI 交互通过明确 adapter 边界保留。
   本地工具通过窄 capability port 注入，TUI 不拥有服务端 Run 状态。
5. [x] MCP server 将每个请求交给统一的 Command/Application adapter，不直接构造
  控制器或模型。
6. [x] Subscription handler 只处理 open/ws/resume、去重、mailbox 和确认；任务执行
  通过同一个 application command。
7. [x] 用同一组 Canonical Event/Item fixture 验证 TUI、桌面端和 Web 的去重、回放、
  `turn.logical_settled`、审批和工具结果投影。
8. [x] 根目录 `server/` 只作为客户端内置 `ConfigServiceRuntime` 保留，提供配置 UI
   和健康检查；它不拥有 Harness 状态，也不承载 `mind.chat` 线上服务端职责。
9. [x] 将本机环境采集与 Helix provider 聚合迁入注入式 environment capability；在
   `SubmitTurnCommand` 持久化前冻结快照并纳入指纹，queued redispatch 不得重采集。

### 出口条件

- 主动执行和订阅执行共享 Run、Tool、Approval、Effect 语义；
- WebSocket 回调中没有模型轮次或工具调用；
- TUI、CLI、MCP 和 subscription 的结果都可从 Event 游标重放；
- Protocol Client 可以在没有 `Mind`、TUI 和本地 Python UI 的环境中运行；
- TUI、桌面端和 Web 消费同一组正式 `mind.chat` 事件和命令语义；
- TUI 的模型、工具、审批和效果编排不再绕过 Protocol Client。
- `agent.harness` 成为本地 Session/Run 编排的唯一实现包，旧 `agent.runtime`
  已删除且不再有生产导入。
- continuation、provider retry 和 queued redispatch 都复用持久命令中的同一环境
  `snapshot_id`，恢复进程不读取当前环境替换它。

阶段 4 出口证据：`agent.harness` 是本地 Session/Run 编排的唯一实现包；四类入口
共享 application command、Run/Tool/Approval/Effect 语义；Protocol Client 在无
`Mind`、TUI 或本地 UI 的环境中可独立运行；Canonical Event/Item fixture 和运行流
测试覆盖正式事件、审批、工具结果、重试、回放游标与结算；该阶段记录时阶段 5 尚未
开始，当前状态以本计划顶部和阶段 5 小节为准。

## 阶段 5 前置：`agent/` 与外围目录职责审计

状态：规划完成，迁移进行中（2026-08-30）

阶段 4 出口已经证明当前 `agent/` 分层可以承载 Harness 主线；本节同时冻结
`protocol/`、`frontends/` 和 `infrastructure/` 的外围边界，避免阶段 5 以“目录更整齐”
为理由进行无用搬迁。表中“保持”表示当前路径就是目标边界，不要求重新创建同义目录；
“候选迁移”只有在完整用例、删除条件和回滚证据同时具备时才可执行。

| 当前目录 | 稳定职责 | 阶段 5 动作 | 进入条件 |
| --- | --- | --- | --- |
| `agent/protocol` | 本地 Harness 的冻结命令、事件、Item、模型请求和 JSON 值对象 | 保持；禁止引入网络客户端、UI 或历史包依赖 | 任何新增类型先登记协议所有权和序列化测试 |
| `agent/domain` | 纯 Run 规则和状态转移 | 保持；只在出现独立领域不变量时拆分文件 | 领域测试可脱离 IO、配置和 UI 执行 |
| `agent/harness` | SessionLoop、RunActor、Session owner 的并发和生命周期编排 | 保持；不得回流 `agent.runtime` | 恢复、取消、并发和单写者测试继续通过 |
| `agent/application` | 对外用例、Command 提交和结果 projection | 保持为唯一应用公开入口；收窄 `__init__` 导出而不复制 facade | 所有入口只依赖公开 application API |
| `agent/ports` | 能力和持久化的最小 Protocol/ABC 契约 | 保持；删除无消费者的过渡端口 | 每个端口都有生产实现或明确测试替身 |
| `agent/capabilities` | 本机进程、文件、环境、MCP、Helix 等副作用实现 | 保持；逐项移除不必要的历史包依赖 | capability 能独立测试，生命周期由 composition 拥有 |
| `agent/stores` | Run 事件、快照、效果账本等事实持久化 | 保持当前事务边界，不按文件名强行拆分 | 新 store 必须有独立生命周期、规模或一致性理由 |
| `agent/adapters/protocol_client.py`、`item_reducer.py` | `mind.chat` wire 翻译、恢复游标和 Canonical Item 投影 | 冻结公共边界；作为 `protocol/` SDK 的 Harness 适配层 | 跨前端兼容测试、版本所有权和旧请求模块删除证据齐备 |
| `agent/composition.py` | 唯一具体组合根 | 保持；不向入口或控制器扩散具体装配 | 所有能力、store 和 adapter 在此注入 |
| `mind_app/cli`、`mcp`、`subscription`、`tui` | 外部入口和本地 UI adapter | 暂不整体搬入 `agent/adapters`；只迁移无 UI/控制器依赖的完整用例 | 先切断具体 runtime 依赖，再提供等价入口测试 |
| `protocol/schema`、`protocol/transport`、`protocol/client` | 正式 `mind.chat` Python wire SDK、HTTP/认证/事件解析 | 保持三层单向依赖；禁止恢复 `protocol.requests` 或把 wire schema 复制到 `agent.protocol` | SDK 版本、PROTOCOL 对照、跨前端兼容和调用者清单完整 |
| `engine` | 平台基础设施和端口探测/进程清理 | 按职责迁入 `agent.capabilities` 或 `infrastructure/platform`；消费者全部切换后删除旧包 | capability 接管、生命周期测试和回滚证据完整 |

### 阶段 5 执行顺序

1. **元数据边界拆分（已完成首个切片）**：将版本、编码和展示常量从协议传输中
   抽出到顶层 `metadata/`，先切换打包信息和内置配置服务；端点、认证和运行时路径
   不混入该包。
2. **公共边界冻结**：核对 `agent.protocol`、`agent.application`、`agent.ports` 的
   导出面和依赖方向；禁止新增只转发一次调用的入口。
3. **Protocol SDK 迁移（已完成结构切片）**：顶层 `protocol/` 作为正式 `mind.chat`
   Python wire SDK 的最终所有者，按 `schema`、`transport`、`client` 分层负责请求/响应
   schema、HTTP、认证、事件解析和 endpoint 错误；`agent.adapters.protocol_client` 只
   负责冻结请求映射、Session 游标、Canonical Item 投影和 `ProtocolCommandError` 归一化。
   后续只补齐版本兼容和跨前端 fixture，不恢复旧扁平路径。
4. **历史副作用适配器退役**：按完整权限进程、Helix、Sandbox sidecar、根轮次
   executor 的实际消费者逐项处理；每项必须先由 capability/application 接管，再删除
   旧调用路径和 `engine` 模块。
5. **配置与入口迁移**：将 `mind_core` 的配置、权限、hooks、skills 按职责拆入
   `agent.domain`、`agent.application` 和 `infrastructure/config`；CLI、TUI、MCP、
   Subscription 的完整用例迁入 `frontends/`，不以批量目录改名代替边界迁移。
6. **历史包删除和收口**：按导入图逐批删除 `mind_app`、`mind_core`、`mind_nova`、
   `engine`；完成历史数据回读、启动/恢复、打包元数据和删除证据后再关闭阶段 5。

每个切片的最低准入是：一个完整生产用例、旧路径可删除、定向失败路径测试、导入图
和 `py_compile` 证据。未满足准入时只更新规划，不创建空目录或兼容 facade。

## 阶段 5：历史包退役

状态：进行中

### 首个切片：产品元数据边界

状态：已完成（2026-08-30）

- [x] 新增顶层 `metadata/`，只承载版本、展示、编码和构建元数据，不承载协议端点、
  认证密钥或 Harness 状态。
- [x] `setup.py` 改为从 `metadata.const` 读取打包名称、版本、描述、许可证和作者信息。
- [x] 内置 `server` 的页面渲染与 `/version` 响应改为从 `metadata.const` 读取展示元数据。
- [x] 为配置服务版本响应补充元数据边界测试；导入图已移除 `setup/server -> mind_nova`
  的对应边，未创建空的协议迁移目录。

该切片只迁移了已确认的元数据职责。`build.py` 和运行时仍使用旧包中的路径、端点或
认证常量，待对应职责具备完整用例后分别迁移，不把它们继续扩散到 `metadata`。

### 已完成切片：Protocol SDK 结构重组与可观测性边界

状态：已完成（2026-08-30）

- [x] 将正式 wire SDK 从历史扁平布局重组为 `protocol/schema`、
  `protocol/transport` 和 `protocol/client` 三层；删除 `protocol/requests`，
  所有生产与测试导入已切换到职责化路径。
- [x] 将结构化观测实现从 `engine` 提取到独立顶层 `observability/`，并把 MCP
  SDK 的标准日志过滤、进程级 sink 和报告文件 sink 封装到该边界；业务模块不再
  直接拥有标准 logging 或 loguru logger。
- [x] 增加 AST 架构守卫，禁止生产代码导入标准 `logging`/`loguru`、创建 `_LOGGER`
  或调用 `logging.getLogger`；backend 和 observability 保持显式基础设施例外。
  日志、MCP、协议传输和包边界定向测试通过（103 项）。
- [x] 完成全量测试、打包导入图和跨前端 fixture 复核：全量测试
  `2964 passed, 11 skipped`，导入图 `--check`、`compileall` 和 `git diff --check`
  通过；下一切片进入 `mind_app`、`mind_core`、`engine` 的职责化重组。

本切片的目录重组是后续历史包退役的强制模板：先建立真实职责边界，再迁移完整用例，
最后删除旧路径；不得恢复扁平兼容导入或以一次性 facade 隐藏依赖。

### 已完成切片：运行工具增强边界与循环依赖拆除

状态：已完成（2026-08-30）

- [x] 将 `engine/enhance` 的结果增强实现迁入
  `mind_app/runtime/tools/enhancement`，保留字段提取、增强过程 reporter 和远端
  自愈结果汇总的完整用例，不在旧包中保留兼容转发。
- [x] 运行工具执行器和增强测试改用新的职责路径；`engine` 不再反向导入
  `mind_core.remote_services`，历史 `engine -> mind_core -> engine` 循环的反向边已
  消除，低层平台包不再承载工具业务编排。
- [x] 导入图、增强用例、架构边界和全量测试复核通过；后续可继续按同一规则迁移
  `engine` 的平台实现与 `mind_core` 的配置/策略职责，完成消费者切换后再删除历史包。

### 已完成切片：平台基础设施边界与 legacy 错误退役

状态：已完成（2026-08-30）

- [x] 将进程输出编码、终端进程、端口探测/清理和文件辅助迁入
  `infrastructure/platform`，所有构建、服务管理、Server、MCP 和 TUI 消费者均已
  切换；旧 `engine/encoding.py`、`terminal.py`、`ports.py`、`file_assist.py` 已删除。
- [x] 将入口可展示的 `AppError` 迁入 `infrastructure/errors.py`，切换全部应用、
  Server、构建和测试消费者并删除 `engine/errors.py`。许可实现随后归入
  `infrastructure/services/licensing.py`，导入图中的历史反向边已清零。
- [x] 新增基础设施边界架构测试，验证 `infrastructure` 不得导入
  `engine`、`mind_app`、`mind_core` 或 `server`；定向测试 295 项、语法检查和导入图
  生成通过；全量测试 `2965 passed, 11 skipped`。服务/升级职责迁移见下一已完成切片。

### 已完成切片：服务生命周期与升级职责归位

状态：已完成（2026-08-30）

- [x] 将 `ServerManage` 迁入 `infrastructure/services/server_manager.py`，将升级
  流程迁入 `infrastructure/update/runtime.py`；异步动画和任务中断检测归入
  `infrastructure/platform`，所有 CLI、MCP、资源和测试消费者均已切换。
- [x] 删除 `engine` 源包，不保留旧模块 facade；服务生命周期仍由
  `ServerManageHelixCapability` 适配，升级进度仍通过 `UpgradeProgress` 端口注入，
  没有把平台进程句柄或升级状态带入 Harness/Protocol。
- [x] 导入图已移除 `engine` 运行时节点并增加残留引用检测；基础设施边界测试、
  服务/升级定向测试和全量回归均通过。阶段 5 下一切片进入 `mind_core` 配置、策略、
  hooks/skills 的职责化拆分。

### 已完成切片：许可与远程服务归入基础设施

状态：已完成（2026-08-30）

- [x] 将签名校验、设备指纹、网络授时、授权续期迁入
  `infrastructure/services/licensing.py`，将远程服务元数据查询迁入
  `infrastructure/services/remote_services.py`；增强工具切换到新的远程服务路径。
- [x] 删除 `mind_core/licensing.py` 和 `mind_core/remote_services.py` 源模块，配置/策略
  包不再持有外部网络、密码学或平台进程依赖；基础设施边界守卫继续禁止回流旧包。
- [x] 定向导入、增强和架构测试通过；导入图中的 `mind_core -> infrastructure` 许可
  边已消除。阶段 5 下一切片进入 `mind_core` 配置、策略、hooks/skills 的拆分。

### 已完成切片：MCP 状态展示边界归位

状态：已完成（2026-08-30）

- [x] 将 `McpStatusView`、`McpStatusDetail`、内置/外部 MCP 快照归约逻辑与既有
  `render_mcp_status_block` 合并到 `mind_app/presentation/mcp_status.py`，保持纯展示
  值对象和渲染职责在同一边界。
- [x] 所有 TUI、presentation 和测试消费者切换到展示模块，删除
  `mind_core/mcp_status.py`，配置核心不再持有 UI 状态语义。
- [x] MCP/TUI/架构定向测试 `208 passed`，语法检查通过；下一切片继续拆分
  `mind_core` 的配置、权限、策略和 hooks/skills 生命周期。

### 已完成切片：应用路径配置基础设施归位

状态：已完成（2026-08-30）

- [x] 将应用入口识别、source/packaged 模式、用户数据目录和支持资源目录解析迁入
  `infrastructure/config/paths.py`，切换 CLI、MCP、native coding、环境生命周期、
  Server 配置存储和测试消费者。
- [x] 删除 `mind_core/application_paths.py`，配置核心不再持有平台入口和本地路径环境
  事实；基础设施边界测试验证新模块不依赖 `mind_core` 或其他 legacy runtime。
- [x] 应用路径定向测试、架构测试、语法检查和导入图检查通过。下一切片继续拆分
  `mind_core` 的配置、权限、策略和 hooks/skills 生命周期。

### 已完成切片：应用设置与 Provider 配置边界归位

状态：已完成（2026-08-30）

- [x] 将 `mind_core/agent_config.py` 与 `mind_core/feature_config.py` 合并迁入
  `agent/application/settings.py`，由 application 公开入口提供 Agent 并发设置、能力
  开关、规范化函数和校验错误；切换 CLI、MCP、TUI、Subagent、工具注册和配置校验消费者。
- [x] 将 `mind_core/provider_config.py` 迁入 `infrastructure/config/providers.py`，切换
  配置存储、偏好服务、内置配置服务和 TUI Provider 选择消费者；Provider 默认值、路由
  约束和标识校验不再属于 `mind_core`。
- [x] 删除三个旧源模块，不保留兼容 facade；架构测试、设置/配置定向测试、语法检查和
  导入图检查通过。下一切片继续拆分权限、项目策略以及 hooks/skills 生命周期。

### 已完成切片：Skills 资源基础设施归位

状态：已完成（2026-08-30）

- [x] 将技能路径解析、frontmatter 读取、`SkillSpec`、来源优先级去重、配置过滤和
  payload 生成从 `mind_core/skills/` 重组到 `infrastructure/skills/`，切换 TUI 输入、
  技能菜单、模型流和 Subagent 消费者。
- [x] 删除 `mind_core/skills/` 旧目录，不保留配置核心到文件系统发现的反向职责；基础设施
  边界继续禁止导入 `mind_core`、`mind_app` 或 `server`。
- [x] Skills 注册、TUI 菜单/输入完成、流式设置和架构测试通过；下一切片继续拆分权限、
  项目信任策略以及 hooks 生命周期。

### 已完成切片：项目边界信任基础设施归位

状态：已完成（2026-08-30）

- [x] 将项目根、Git checkout/worktree、仓库根和项目级信任登记解析从
  `mind_core/project_trust.py` 迁入 `infrastructure/config/trust.py`，配置层和配置会话
  切换到新的基础设施边界。
- [x] 用显式的 `ProjectTrustLevel` 分支完成信任级别收窄，移除旧实现中的强制类型转换；
  基础设施模块不依赖 `mind_core`、`mind_app` 或 `server`。
- [x] 删除旧项目策略模块；项目配置/CLI 信任回归、架构守卫、语法检查和导入图检查通过。
  下一切片继续处理权限策略和 hooks 生命周期。

### 已完成切片：权限领域策略归位

状态：已完成（2026-08-30）

- [x] 将沙箱模式、审批策略、网络访问、用户预设和权限展示标签从
  `mind_core/permissions.py` 迁入 `agent/domain/policies.py`，由
  `agent.application` 作为唯一公开入口提供；执行、TUI、MCP 和 Subagent 消费者不再
  直接依赖配置核心。
- [x] domain 仅依赖无副作用的权限 schema 类型和规范化规则，不读取环境、不启动进程、
  不调用具体工具；架构守卫继续禁止 legacy runtime 和具体 adapter 依赖。
- [x] 删除旧权限模块；权限、执行上下文、TUI/MCP/Subagent 和架构测试通过，下一切片
  继续拆分 hooks 生命周期。

### 已完成切片：服务配置基础设施归位

状态：已完成（2026-08-30）

- [x] 将服务域名规范化、远程配置读取和观测事件从 `mind_core/service_config.py` 迁入
  `infrastructure/services/service_config.py`，CLI、MCP 和内置配置服务切换到新的服务边界。
- [x] `ServiceConfig` 改为接收最小 `ConfigReader` 协议，不再在基础设施内部隐式创建
  `ConfigSession`/`ConfigStore`；配置生命周期由组合调用方显式拥有。
- [x] 删除旧服务配置模块；服务域名、CLI/MCP 启动和架构测试通过，下一切片继续拆分
  偏好配置与剩余 `mind_core` 状态。

### 已完成切片：偏好配置基础设施归位

状态：已完成（2026-08-30）

- [x] 将有效配置到运行时偏好的投影、模型槽位/托管工具规范化、临时模型覆盖和远程
  偏好读取从 `mind_core/preference.py` 重组到 `infrastructure/config/preferences.py`，
  切换 CLI、TUI、MCP、配置服务测试和运行时调用方。
- [x] 删除 `mind_core/preference.py`，移除 `mind_core.config.config_to_preferences` 重复
  投影；`Preferences` 通过最小 `ConfigReader` 读取，不隐式创建或持有配置存储。
- [x] 偏好、Provider、CLI/MCP 启动和架构测试通过；下一切片处理剩余配置层/存储边界与
  前端入口拆分。

### 已完成切片：配置文件存储基础设施归位

状态：已完成（2026-08-30）

- [x] 将 TOML 文档保真读取、默认配置初始化、原子更新/删除和配置存储错误从
  `mind_core/config_store.py` 迁入 `infrastructure/config/store.py`，切换配置层、CLI、
  MCP、TUI、Server 和测试消费者。
- [x] 配置解析与会话继续拥有 schema/分层语义，底层存储只提供文件读写契约；基础设施
  存储不依赖 `mind_core`、`mind_app` 或 `server`。
- [x] 配置会话、服务路由、CLI/TUI 和架构测试通过；下一切片继续拆分配置解析/分层状态
  与前端入口。

### 已完成切片：配置 schema、分层与会话归位

状态：已完成（2026-08-30）

- [x] 将 `mind_core/config.py`、`config_layers.py` 和 `config_session.py` 分别迁入
  `infrastructure/config/schema.py`、`layers.py` 和 `session.py`，删除旧配置入口并切换
  CLI、TUI、MCP、Server 及测试消费者。
- [x] schema 负责原始配置校验、规范化、覆盖表达式和字段投影；layers 负责用户/Profile/
  项目/CLI 合并与项目信任；session 负责持久更新和 CAS 前置校验，三者均通过
  `infrastructure.config.store` 读写 TOML，不互相复制存储逻辑。
- [x] 配置/CLI/TUI/Server 定向回归 `459 passed`，架构守卫 `23 passed`，全量回归
  `2975 passed, 11 skipped`，`compileall`、导入图和差异检查通过；下一切片进入
  TUI/Presentation 设计基础设施归位。

### 已完成切片：终端展示基础设施归位

状态：已完成（2026-08-30）

- [x] 将 `mind_core/design/` 重组为 `mind_app/presentation/terminal/`：终端探测进入
  `capabilities.py`，颜色语义进入 `palette.py`，标题状态进入 `progress.py`，启动帧进入
  `intro.py`，下载动画进入 `download.py`。
- [x] 将下载渲染端口迁入 `terminal/contracts.py`，具体实现命名为
  `TerminalDownloadRenderer`；删除 `mind_app/runtime/design.py` 和旧 `Design` facade，
  CLI、TUI、资产升级和 MCP 生命周期只依赖展示端口。
- [x] 终端能力、颜色、进度、TUI、升级定向回归通过，架构守卫 `24 passed`；旧设计包/
  协议守卫、`compileall`、导入图和差异检查通过，全量回归 `2976 passed, 11 skipped`；
  下一切片继续收敛前端目录和剩余历史入口。

### 已完成切片：应用级展示契约归位

状态：已完成（2026-08-30）

- [x] 将 `mind_app/frontend/contracts.py` 和 `sinks.py` 重组为
  `mind_app/presentation/application.py` 与 `application_sinks.py`，保留应用级
  `ApplicationView`、`ApplicationSink`、`FrontendRuntime` 和 `Frontend` 组装契约。
- [x] CLI、TUI、MCP、运行时事件和测试消费者全部切换到 presentation 公开模块；旧
  `mind_app/frontend` 源码删除，顶层 `frontends/` 暂不创建，避免共享契约迁移期间形成
  `frontends -> mind_app -> frontends` 循环。
- [x] 前端契约/TUI/CLI 定向回归 `949 passed`，架构守卫 `25 passed`，全量回归
  `2977 passed, 11 skipped`；导入图脚本已覆盖当前真实边界，`compileall` 和差异检查通过。

### 已完成切片：单轮输出适配器归位

状态：已完成（2026-08-30）

- [x] 将 `mind_app/output/` 重组为 `mind_app/presentation/output/`，统一承载
  `OutputSession`、正文内容协议、文本/JSONL/静默输出以及终端内容 sink；移动后只
  通过 presentation 的结构化契约依赖输出记录器，不引入新的业务状态所有权。
- [x] 流式 Turn、工具执行、Subagent、CLI、MCP、TUI 和测试消费者全部切换到新路径，
  旧输出目录源码与导入删除；`mind_app/interaction` 保留为审批协调器使用的输入/审批
  端口，待完整入口迁移时再决定其最终归属。
- [x] 输出/流式/TUI 定向回归 `98 passed`，架构守卫 `26 passed`，
  `compileall`、导入图和差异检查通过；全量回归 `2978 passed, 11 skipped`。

### 已完成切片：流事件展示投影归位

状态：已完成（2026-08-30）

- [x] 将 `mind_app/stream_events/` 重组为 `mind_app/presentation/stream/`，统一承载
  生命周期事件投影、assistant 边界、工具 trace、审批/失败/命令预览和耗时页脚；
  运行时与渲染器改用 presentation 的公开投影入口。
- [x] 将 `stream_io/output_record.py` 和 `stream_state/boundary.py` 归入
  `mind_app/presentation/output/recording.py`、`boundary.py`，并把仅由边界状态使用的
  spacing 函数内聚；删除 `tool_trace.py` 一次性导出 facade 以及三个旧平铺包入口。
- [x] 流事件/输出/TUI 定向回归 `174 passed`，架构守卫 `27 passed`，全量回归
  `2979 passed, 11 skipped`；`compileall`、导入图和差异检查通过。

### 已完成切片：审批状态存储归位

状态：已完成（2026-08-30）

- [x] 将 `mind_app/approval/permission_grants.py` 和 `ledger.py` 重组为
  `agent/stores/permission_grants.py` 与 `approval_ledger.py`，由 stores 统一持有
  会话权限授权和工具审批消费状态；审批 coordinator、策略和展示模型仍保留在
  `mind_app.approval`，不把 UI 生命周期混入状态存储。
- [x] 控制器、流式 Turn、工具策略、native coding、内置工具、执行上下文和测试消费
  者均切换到明确的 `agent.stores` 模块；旧审批状态源码删除，应用架构守卫只允许这
  两个显式 state store 入口，其他 `agent.*` 直连仍被拒绝。
- [x] 账本 key 规范化改为显式类型收窄并移除 `typing.cast()`；审批/权限定向回归
  `179 passed`，架构与基线回归 `29 passed`，全量回归 `2980 passed, 11 skipped`；
  `compileall`、导入图和差异检查通过。

### 已完成切片：运行报告观测边界归位

状态：已完成（2026-08-30）

- [x] 将 `mind_app/reporting.py` 重组为 `observability/reporting.py`，由观测基础
  设施统一拥有运行报告目录、诊断日志 sink、输出记录路径和报告关闭生命周期；
  CLI、MCP、控制器和日志测试消费者全部切换。
- [x] 旧 `mind_app.reporting` 源码与导入删除，架构守卫确保报告实现不回流业务包；
  `observability` 仍不依赖 `mind_app`，报告对象只作为组合根注入的运行资源。
- [x] 观测/CLI/MCP 定向回归 `66 passed`，架构守卫 `29 passed`，基线回归
  `1 passed`；全量行为回归 `2980 passed, 11 skipped`，导入图修正后基线、
  `compileall` 和差异检查通过。

### 已完成切片：运行时路径基础设施归位

状态：已完成（2026-08-30）

- [x] 将 `mind_app/paths.py` 重组为 `infrastructure/config/runtime_paths.py`，
  统一承载用户 home、报告/会话/历史目录、各类本地数据库路径和子进程环境变量；
  应用入口布局解析继续由 `infrastructure.config.paths` 负责，职责不混合。
- [x] CLI、MCP、Subscription、TUI、History、Runtime、native coding、配置存储和
  测试消费者全部切换到新路径，旧应用路径模块删除；新增架构守卫禁止旧导入回流。
- [x] 路径/历史/配置/CLI/TUI 定向回归 `101 passed`，架构守卫 `30 passed`，全量
  回归 `2982 passed, 11 skipped`；`compileall`、导入图和差异检查通过。

### 已完成切片：升级资产与交互附件边界归位

状态：已完成（2026-08-30）

- [x] 将资产存在性判断和升级触发从 `mind_app/assets.py` 重组到
  `infrastructure/update/assets.py`；更新基础设施只接收 `UpgradeProgress` 端口，
  不再依赖动画管理器或终端设计。`TerminalDownloadProgress` 留在
  `mind_app/presentation/terminal/download_renderer.py`，负责把入口动画管理器和
  终端下载设计适配为更新进度端口。
- [x] 将待发送附件的路径清洗、glob/目录展开、MIME 分类、快照恢复、去重和消费从
  `mind_app/attach.py` 迁入 `mind_app/interaction/attachments.py`；控制器及 TUI
  测试切换到交互输入边界，未创建新的持久化或协议状态副本。
- [x] 删除两个旧根模块并新增架构守卫，禁止 `mind_app.assets`、`mind_app.attach`
  导入回流；附件/Helix/CLI/TUI/架构定向回归 `295 passed`，全量回归
  `2983 passed, 11 skipped`，导入图、`compileall` 与差异检查通过。

### 已完成切片：MCP 运行时边界合并

状态：已完成（2026-08-30）

- [x] 将 `mind_app/mcp/` 的配置规范化、会话契约、外部连接组、状态、工具结果、
  工具索引、组合会话和 stdio MCP 服务重组到已有的 `mind_app/runtime/mcp/`；该包
  统一承载 MCP 运行时生命周期，避免应用根目录同时存在两套 MCP 模块边界。
- [x] 所有 CLI、TUI、Turn、Subagent、native coding、外部连接和测试消费者切换到
  `mind_app.runtime.mcp`；删除旧根包和 `__init__` 入口，不保留兼容转发 facade，
  并加入旧源码/导入架构守卫。
- [x] MCP/工具/模型流/架构定向回归 `119 passed`，全量回归
  `2984 passed, 11 skipped`；`compileall`、导入图和差异检查通过。

### 已完成切片：进程输出编码平台归位

状态：已完成（2026-08-30）

- [x] 将完整的进程输出编码探测、规范化、BOM/分段解码和质量评分实现从
  `mind_app/native_coding/encoding.py` 提升到 `infrastructure/platform/encoding.py`，
  覆盖原平台模块的基础 API；终端和 native coding 共用同一个平台实现。
- [x] 工作区命令、shell/exec 会话、捕获器和输出解码器全部切换到平台编码边界，
  删除 native coding 旧模块，不增加别名或转发层；新增架构守卫禁止旧路径和导入。
- [x] 进程/shell/终端/native coding/架构定向回归 `137 passed`，全量回归
  `2985 passed, 11 skipped`；`compileall`、导入图和差异检查通过。

### 已完成切片：工作区进程与 Git 平台归位

状态：已完成（2026-08-31）

- [x] 将进程组创建、stdin 收束、树级中断/终止和跨平台进程等待从
  `mind_app/runtime/processes.py` 重组到 `infrastructure/platform/processes.py`，
  同时清除该模块残留的强制类型断言。
- [x] 将无 shell 工作区命令执行迁入 `infrastructure/platform/workspace.py`，将
  Git worktree 探测与差异采集迁入 `infrastructure/platform/git_diff.py`；native
  coding、TUI、Hook 和测试消费者只依赖平台边界，旧模块和导入已删除。
- [x] 进程/工作区/Git/TUI/native coding/架构定向回归 `123 passed`，全量回归
  `2986 passed, 11 skipped`；`compileall`、导入图和差异检查通过。

### 已完成切片：命令安全平台归位

状态：已完成（2026-08-31）

- [x] 将危险命令递归识别、PowerShell/cmd 删除与外部 URL 启动检测从
  `mind_app/native_coding/exec/command_safety/` 重组到
  `infrastructure/platform/command_safety/`；执行策略保留在 native coding，
  只接收平台返回的分类结果。
- [x] 更新 Unix/Windows 执行策略和安全测试消费者，删除旧安全包，不保留别名或
  转发 facade；平台基础设施仍不依赖应用包。
- [x] 命令安全/执行策略/平台/架构定向回归 `147 passed`，全量回归
  `2987 passed, 11 skipped`；`compileall`、导入图和差异检查通过。

### 已完成切片：本地进程执行基座归位

状态：已完成（2026-08-31）

- [x] 将 `ProcessCapture`、`CapturedOutputDecoder`、本地 Sandbox sidecar 客户端和
  `ShellRuntimeResolver` 从 `mind_app/native_coding/exec/` 重组到
  `infrastructure/platform/`；平台层统一持有进程捕获、输出编码、sidecar 协议和
  shell 运行时解析，native coding 保留工具业务和执行策略。
- [x] 切换 shell/exec/ProcessSession/native coding 及 sidecar、shell 测试消费者，
  删除四个旧模块，不增加别名或转发 facade；新增平台归属架构守卫。
- [x] 进程捕获/解码、Sandbox、shell、执行策略/平台/架构定向回归 `146 passed,
  11 skipped`，全量回归 `2988 passed, 11 skipped`；`compileall`、导入图和差异
  检查通过。

### 已完成切片：JavaScript REPL 平台归位

状态：已完成（2026-08-31）

- [x] 将 Node 内核进程、会话隔离、临时目录、超时/取消重置、工具消息桥接和图片
  附件校验从 `mind_app/native_coding/js_repl/` 重组到
  `infrastructure/platform/javascript_repl.py`；native coding 仅保留工具入口和
  调用策略。
- [x] `JavaScriptReplPool` 以显式 `application_root` 注入静态资源目录，平台模块
  不再依赖自身文件路径推断应用布局；删除旧包和导入，不保留兼容 facade，并加入
  平台归属架构守卫。
- [x] JavaScript REPL 定向回归 `28 passed`，架构守卫回归 `37 passed`，全量回归
  `2989 passed, 11 skipped`；`compileall`、导入图和差异检查通过。

### 已完成切片：平台环境助手归位

状态：已完成（2026-08-31）

- [x] 将支持工具 PATH 路由和当前工作区根探测从
  `mind_app/runtime/environment/shell_tools.py`、`workspace.py` 重组到
  `infrastructure/platform/shell_tools.py`、`workspace_context.py`；runtime、CLI、
  MCP 和 TUI 只消费平台结果。
- [x] 删除旧 runtime 环境助手及导入，不增加兼容 facade；新增旧路径/旧导入平台归属
  架构守卫，保留 shell 工具路由和工作区探测的现有行为。
- [x] 平台环境/CLI/MCP/TUI/架构定向回归 `179 passed`，全量回归
  `2990 passed, 11 skipped`；`compileall`、导入图和差异检查通过。

### 已完成切片：执行策略领域与配置解析分离

状态：已完成（2026-08-31）

- [x] 将执行策略的 `Decision`、规则值对象和匹配算法从
  `mind_app/native_coding/exec/execpolicy/` 重组到
  `agent/domain/execution_policy/`；domain 不依赖应用或基础设施。
- [x] 将规则文件 AST 解析和文件读取重组到
  `infrastructure/config/execution_policy.py`，native coding 的执行策略管理器只
  组合领域策略与配置解析，不在策略域持有 IO；删除旧包和导入，不保留 facade。
- [x] 执行策略专项回归 `19 passed`，架构守卫专项 `2 passed`，全量回归
  `2991 passed, 11 skipped`；`compileall`、导入图和差异检查通过。

### 已完成切片：工作区运行时生命周期归位

状态：已完成（2026-08-31）

- [x] 将工作区编码、Shell、执行策略和共享进程能力的替换、退役和关闭逻辑从
  `mind_app/runtime/environment/coding_lifecycle.py` 重组到
  `agent/harness/workspace_runtime.py`；Harness 只依赖最小生命周期 Protocol，不导入
  `mind_app`、`infrastructure` 或具体本机实现。
- [x] `RuntimeServices` 新增可选的工作区运行时工厂端口，`mind.py` 作为唯一具体组合根
  注入 `NativeCoding`、`ExecPolicyManager` 和 `ProcessCapability`；Controller 不再
  隐式创建具体能力，缺少工厂时在组装边界明确失败。
- [x] 删除旧生命周期模块和导入，补充旧路径/旧导入架构守卫；工作区替换、无事件循环
  延迟回收、幂等关闭和组合接线定向回归 `20 passed`，旧路径/旧导入守卫 `2 passed`；
  全量回归 `2992 passed, 11 skipped`，导入图、`compileall` 和差异检查通过。

### 已完成切片：环境快照用例归位

状态：已完成（2026-08-31）

- [x] 将环境能力调用、返回值边界校验和 `CapabilityError` 收敛从
  `mind_app/runtime/environment/snapshot.py` 提取到
  `agent/application/environment.py`；应用核心只依赖环境能力端口和协议值类型，失败
  通过显式 observer 交给外层记录。
- [x] 将 Controller 工作区和 Helix provider 聚合归入
  `mind_app/interaction/environment.py`，切换 CLI、TUI、MCP、Subscription 和 Turn
  setup 的全部消费者；删除旧 runtime 快照模块，不保留转发 facade。
- [x] 环境快照/四类入口回归 `180 passed`，环境旧路径/旧导入及 application 边界守卫
  `2 passed`；全量回归 `2995 passed, 11 skipped`，导入图、`compileall` 和差异检查通过。

### 已完成切片：Hook 运行模型归位

状态：已完成（2026-08-31）

- [x] 将纯 Hook 生命周期快照、决定、输出条目和工具结果值对象从
  `mind_app/runtime/hooks/models.py` 重组到 `agent/application/hook_models.py`；新模块只
  依赖 `agent.domain` 的 Hook 类型，不依赖 runtime、UI、基础设施或观测实现。
- [x] 切换 Hook 执行器、注册器、scope、Turn 流、工具/Subagent、TUI 适配器和测试的全部
  消费者；执行器和注册器继续留在 runtime，避免把执行副作用与 application contract 混合。
- [x] 删除旧模型模块和旧导入，不保留兼容 facade；新增 application 归属与旧路径架构守卫，
  导入图、`compileall` 和差异检查通过。Hook/工具/Turn/TUI 定向回归 `421 passed`，全量
  回归 `2996 passed, 11 skipped`。

### 已完成切片：Hook 协议边界归位

状态：已完成（2026-08-31）

- [x] 将 Hook 进程 stdin/stdout schema、事件输入构建和输出校验从
  `mind_app/runtime/hooks/protocol.py` 重组到 `agent/application/hook_protocol.py`；该模块
  只依赖 application 可用的 Hook 事件类型和标准库，不依赖 runtime、UI、基础设施或线上
  `protocol/`。
- [x] 切换 Hook scope、事件目录、效果归一化和协议测试消费者，删除旧模块与相对导入；新增
  旧路径/旧导入及 application 边界守卫，不保留兼容 facade。
- [x] Hook 协议与架构定向回归 `31 passed`，全量回归 `2997 passed, 11 skipped`，导入图、
  `compileall` 和差异检查通过。

### 已完成切片：Hook 目录与匹配规则归位

状态：已完成（2026-08-31）

- [x] 将 Hook 管理目录、不可变状态快照和内容冲突错误从
  `mind_app/runtime/hooks/catalog.py` 重组到 `agent/application/hook_catalog.py`，切换
  Controller、TUI 和注册器消费者；application contract 不持有执行器或平台副作用。
- [x] 将 matcher 解析、工具 canonical 名称和别名候选从
  `mind_app/runtime/hooks/matching.py` 重组到 `agent/domain/hook_matching.py`，改为只依赖
  domain Hook 配置类型，禁止 domain 反向依赖 application。
- [x] 删除旧模块与旧导入，不保留兼容 facade；新增 application/domain 归属守卫。Hook
  目录、TUI、执行和架构定向回归 `22 passed`，全量回归 `2999 passed, 11 skipped`，
  导入图、`compileall` 和差异检查通过。

### 已完成切片：Hook 输出与事件规格归位

状态：已完成（2026-08-31）

- [x] 将 Hook 输出 schema 后的语义校验、决定归一化和业务阻断结果从
  `mind_app/runtime/hooks/effects.py` 重组到 `agent/application/hook_output.py`；该模块
  不持有外部效果或执行器副作用。
- [x] 将生命周期事件规格目录和目录一致性校验从
  `mind_app/runtime/hooks/events.py` 重组到 `agent/application/hook_events.py`，只通过同层
  application 模块组合输出归一化；runtime 仅消费规格。
- [x] 已切换 runtime、Hook 协议测试和事件测试导入，新增旧路径/旧导入与 application
  边界守卫；定向回归 `32 passed`，导入图、`compileall` 和差异检查通过。
- [x] 全量回归 `3000 passed, 11 skipped`，确认 Hook 输出语义、事件规格目录和 runtime
  调度行为无回归；导入图、`compileall` 和差异检查通过。

### 已完成切片：Hook 输出 spill 平台归位

状态：已完成（2026-08-31）

- [x] 将 Hook 流输出读取、阈值 spill、头尾预览、临时文件登记和会话清理从
  `mind_app/runtime/hooks/output_spill.py` 重组到
  `infrastructure/platform/hook_output_spill.py`；平台模块只依赖标准库和 metadata 编码
  常量，不依赖 runtime、application、Harness 或 UI。
- [x] 切换 `HookCommandExecutor` 与 Hook 测试消费者，删除旧模块与旧导入，不保留兼容
  facade；新增平台归属与旧路径架构守卫。
- [x] Hook 执行/平台定向回归 `20 passed`，全量回归 `3001 passed, 11 skipped, 33 warnings`；
  导入图、`compileall` 和差异检查通过。

### 已完成切片：Hook 工具结果投影归位

状态：已完成（2026-08-31）

- [x] 将后置 Hook 的工具结果替换、反馈、阻断和追加上下文投影从
  `mind_app/runtime/hooks/results.py` 重组到 `agent/application/hook_result.py`；该模块
  只处理已归一化的 application 值，不执行 IO 或持有 runtime 生命周期。
- [x] 切换 `ToolHookEvents` 和工具结果测试消费者，删除旧模块与旧导入，不保留兼容
  facade；新增 application 归属与旧路径架构守卫。
- [x] Hook 工具/架构定向回归 `61 passed`，全量回归 `3002 passed, 11 skipped, 34 warnings`；
  导入图、`compileall` 和差异检查通过。

### 已完成切片：工具模式策略归位

状态：已完成（2026-08-31）

- [x] 将 app/api 工具可见性、隐藏规则、内联元数据过滤和默认策略从
  `mind_app/runtime/tools/mode_policy.py` 重组到 `agent/domain/tool_policy.py`；该模块只
  处理纯领域规则，不依赖 runtime、application、基础设施或 UI。
- [x] 切换 Controller、CLI、MCP、Turn executor、TUI 和策略测试消费者，删除旧模块与旧
  导入，不保留兼容 facade；新增 domain 归属与旧路径架构守卫。
- [x] 工具策略/架构定向回归 `4 passed`，全量回归 `3003 passed, 11 skipped, 35 warnings`；
  导入图、`compileall` 和差异检查通过。

### 已完成切片：运行结果与平台计时器归位

状态：已完成（2026-08-31）

- [x] 将单次模型运行的不可变 `RunResult` 从
  `mind_app/runtime/turns/result.py` 重组到 `agent/application/run_result.py`，将流式终态
  优先级、协议终态归并和结果构建从 `stream_outcome.py` 重组到
  `agent/application/stream_outcome.py`；application 公开入口统一提供两类结果契约，前端、
  Subagent、MCP 和 Subscription 不再直接依赖 runtime turns。
- [x] 将远端 `cid/sid` 到本地 Session 身份的确定性派生从
  `mind_app/runtime/support/session_identity.py` 重组到 `agent/application/session_identity.py`；
  身份哈希规则由 application 统一持有，不进入线上 `protocol` 或各前端私有实现。
- [x] 将 asyncio 空闲状态计时器从 `mind_app/runtime/support/idle_status.py` 下沉到
  `infrastructure/platform/idle_status.py`；删除全仓库无调用者的 `AsyncRWLock`，避免保留未接入
  Harness 的并发抽象和 runtime support 杂物入口。
- [x] 切换所有生产与测试消费者，删除旧模块和旧导入，不保留兼容 facade；新增 application、
  platform 归属及死代码删除守卫。结果/流式/TUI/CLI/架构定向回归 `263 passed, 36 warnings`，
  全量回归 `3004 passed, 11 skipped, 36 warnings`，导入图、`compileall` 和差异检查通过。

### 已完成切片：MCP 工具进度边界归位

状态：已完成（2026-08-31）

- [x] 将 MCP 工具进度通知从 `mind_app/runtime/tools/notify.py` 重组到
  `mind_app/runtime/mcp/tool_progress.py`；通知策略和观测回退与 MCP 调用生命周期同属 runtime
  MCP 适配边界，工具路由不再持有平铺通知实现。
- [x] 确认 `mind_app/runtime/tools/policy.py` 全仓库无生产或测试调用者并删除，不迁移死代码或
  增加兼容 facade；新增旧路径/旧导入和 MCP 归属架构守卫。
- [x] 切换工具路由并通过 MCP/工具/架构定向回归 `108 passed, 37 warnings`，导入图、
  `compileall` 和差异检查通过。

### 已完成切片：Hook 执行端口归位

状态：已完成（2026-08-31）

- [x] 将 `HookCommandRunner`、`HookCommandResult` 和 `HookContextSpiller` 从
  `mind_app/runtime/hooks/runtime.py` 重组到 `agent/ports/hooks.py`；端口只声明 Hook 定义、
  已校验输出和上下文 spill 生命周期，不依赖 runtime、基础设施、线上协议或展示实现。
- [x] 切换 Hook runtime/registry 的端口导入，保留 `HookStatusPort` 在 runtime 作为展示回调，
  避免把前端生命周期混入 Harness ports；删除 runtime 内重复 Protocol 定义，不增加兼容 facade。
- [x] 新增 ports 归属和 runtime 重复定义架构守卫；Hook/工具/架构定向回归
  `171 passed, 37 warnings`，导入图、`compileall` 和差异检查通过。

### 已完成切片：Hook 生命周期显式注入

状态：已完成（2026-08-31）

- [x] 将 Hook runtime/registry 对 `HookCommandExecutor` 的能力推断改为显式资源绑定：调用方可
  注入 `context_spiller`、`cleanup_session` 和 `close`，自定义 runner 不再通过类型反射获得隐式
  生命周期能力；默认执行器只在构造分支绑定一次，保持默认入口可用。
- [x] 保留 `HookStatusPort` 作为 runtime 展示回调，不把前端状态生命周期混入 `agent.ports`；
  超限上下文、会话清理和关闭路径均通过明确端口执行，支持后续由唯一组合根创建执行器。
- [x] 新增显式资源生命周期回归和 `isinstance(HookCommandExecutor)` 架构守卫；Hook/工具/架构
  定向回归 `172 passed, 37 warnings`，导入图、`compileall` 和差异检查通过。

### 已完成切片：Hook Registry 组合边界收敛

状态：已完成（2026-08-31）

- [x] 在 `agent/ports/hooks.py` 定义 `HookStatusPort`、`HookDispatcherPort`、
  `HookRegistryPort` 和 `HookRegistryFactory`，把 Hook 发现、作用域分发和资源关闭作为
  可替换端口暴露；`RuntimeServices` 要求显式提供 `create_hook_registry` 工厂。
- [x] `mind.py` 成为具体 `HookRegistry` 的唯一组合根；CLI bootstrap、MCP server 和
  Controller 只消费注入的 registry/dispatcher port，不再导入或隐式构造 runtime registry。
  `HookExecutionScope` 也只依赖 dispatcher port，runtime 不向上泄漏具体实现类型。
- [x] CLI、MCP、Hook 和架构回归 `244 passed`，新增组合根守卫通过；全量回归
  `3009 passed, 11 skipped, 38 warnings`，编译、导入图、文档契约和差异检查通过。

### 已完成切片：runtime support 职责拆分

状态：已完成（2026-08-31）

- [x] 将本地会话标识、轮次边界和一次性上下文从
  `mind_app/runtime/support/conversation.py` 重组到
  `mind_app/interaction/conversation.py`；Controller、Turn 和测试改走交互边界，保留
  会话状态的唯一所有权。
- [x] 将 TUI 剪贴板 I/O 从 `mind_app/runtime/support/clipboard.py` 重组到
  `mind_app/tui/adapters/clipboard.py`；将 MCP 关闭期传输异常判断内聚到
  `mind_app/runtime/mcp/errors.py`，将用户可见 HTTP/运行期异常摘要重组到
  `mind_app/presentation/stream/exception_text.py`。
- [x] 删除混合的 `session_policy.py` 与旧 support 源码，不保留兼容 facade；新增旧路径、旧导入、
  TUI/MCP/presentation 归属守卫。会话/Turn/流协议定向回归 `107 passed, 1 warning`，完整架构
  测试 `54 passed, 38 warnings`，导入图、`compileall` 和差异检查通过；下一切片继续审计剩余
  `mind_app` runtime 与完整入口边界。

### 已完成切片：子 Agent mailbox 存储归位

状态：已完成（2026-08-31）

- [x] 将 `AgentMailboxEvent`、`AgentMailboxSnapshot`、`AgentMailboxStore` 及其有界日志、消费
  游标和上下文格式化从 `mind_app/runtime/subagents/mailbox.py` 重组到
  `agent/stores/agent_mailbox.py`；mailbox 状态由 stores 统一持有，runtime/subagents 只负责
  控制和调度。
- [x] 切换子 Agent 控制、投递、图持久化、运行时、客户端工具和测试消费者，通过
  `agent.stores` 公开导出；删除旧 runtime mailbox 入口，不保留兼容 facade。
- [x] mailbox/子 Agent 定向回归 `53 passed, 1 warning`，新增 stores 边界与旧导入守卫、导入图、`compileall`
  和差异检查通过；下一切片继续拆分 Agent graph 快照与 SQLite 持久化实现。

### 已完成切片：协议身份校验归位

状态：已完成（2026-08-31）

- [x] 将 `CID_RE`、`SID_RE` 和 `valid_session_ids` 从 `mind_app/history/ids.py` 提升到
  `protocol/schema/identifiers.py`；历史存储、Controller、交互和 TUI 统一复用协议 schema，删除
  旧 history 身份入口。
- [x] 新增协议身份归属守卫，确认旧模块和旧导入不存在；该切片与子 Agent 上下文迁移一起通过
  `compileall`、导入图和 `git diff --check`，协议身份和历史路径回归 `350 passed`。

### 已完成切片：子 Agent 线程与继承上下文归位

状态：已完成（2026-08-31）

- [x] 将 `AgentThreadContext`、`AgentTurnContext` 从 `mind_app/runtime/subagents/thread.py` 重组到
  `agent/application/agent_thread.py`；application 契约只依赖 domain、协议 schema 和 application
  执行/设置类型，不依赖 runtime、历史或具体基础设施。
- [x] 将 `ForkContextSnapshot`、`ForkTurns` 和 `normalize_fork_turns` 重组到
  `agent/application/fork_context.py`；`mind_app/runtime/subagents/context.py` 仅保留 transcript
  上下文构建职责，删除旧 thread 入口和 `typing.cast()` 强制断言。
- [x] 新增 `agent/ports/PermissionGrantReader`，使执行上下文只依赖读取端口，不再通过
  `TYPE_CHECKING` 引入具体 `PermissionGrantStore`。
- [x] 子 Agent context/control/graph/runtime/TUI 与身份边界回归 `65 passed, 2 warnings`，扩展交互、
  历史、Controller、TUI 和 CLI 回归 `350 passed`；完整架构守卫 `60 passed, 44 warnings`，导入图、
  全量行为回归 `3015 passed, 11 skipped, 44 warnings`，导入图、`compileall` 和 `git diff --check`
  已通过。下一切片继续拆分 Agent graph 快照与 SQLite 持久化实现。

### 已完成切片：Agent graph 快照与 SQLite 存储归位

状态：已完成（2026-08-31）

- [x] 将 `AgentStatus`、`AgentResumeStatus`、`AgentSubmission` 和重启中断事实从
  `mind_app/runtime/subagents/control.py` 重组到 `agent/domain/agents.py`；领域值对象只依赖协议
  标识和标准库，不拥有运行时或存储状态。
- [x] 将 `AgentGraphRecord`、`AgentGraphCheckpoint`、`AgentGraphStore` 和
  `AgentGraphPersistence` 从 `mind_app/runtime/subagents/graph.py` 重组到
  `agent/stores/agent_graph.py`；control 仅保留可变调度协调器，SQLite schema、载荷校验和单写者
  持久化全部由 stores 负责，删除旧 graph 入口且不保留 facade。
- [x] 子 Agent graph/control/runtime 与 stores/domain/旧导入守卫回归 `51 passed, 1 warning`；导入图、
  `compileall` 和 `git diff --check` 通过。下一切片继续收敛 Subagent runtime 的执行器、投递和
  控制端口，减少 runtime 平铺依赖。

### 已完成切片：子 Agent 消息投递端口拆分

状态：已完成（2026-08-31）

- [x] 将 `AgentMessageReceipt`、`AgentMessageReceiptStatus` 和 `AgentMessageDeliveryPort` 提升到
  `agent/ports/agent_messages.py`；runtime 不再定义跨层投递端口或协议回执值对象。
- [x] 将 `SteeringMessageDelivery` 提升到 `agent/adapters/agent_messages.py`，集中承担
  `/turn/steer` 请求、稳定 request_id、有限重试和状态映射；`AgentActiveTurn` 与
  `AgentMessageDispatch` 仍由 runtime 保留为活动轮次状态机。
- [x] 新增消息端口/适配器架构守卫；消息投递与 runtime 相关回归 `45 passed, 1 warning`，
  导入图、`compileall` 和 `git diff --check` 通过。完整架构扫描的既有唯一失败为允许清单遗漏本次
  两个正式模块，修正后对应守卫已通过；下一切片继续拆分 Subagent 执行器和 runner 的 runtime 依赖。

### 已完成切片：Hook 输入上下文归位

状态：已完成（2026-08-31）

- [x] 将 `HookExecutionContext` 与权限模式映射从 `mind_app/runtime/hooks/scope.py` 重组到
  `agent/application/hook_context.py`；上下文只依赖 Turn、domain Hook 事件名和已定义的 Hook schema，
  不依赖 runtime dispatcher 或基础设施。
- [x] `HookExecutionScope` 保留在 runtime，继续拥有 dispatcher、HookRuntime.empty、状态端口绑定和
  生命周期校验；所有生产/测试消费者切换到 `agent.application.HookExecutionContext`，删除旧导入。
- [x] Hook/Turn/压缩/Subagent/TUI 定向回归 `247 passed`，新增 application/runtime 分离守卫；
  `compileall`、导入图和 `git diff --check` 通过。下一切片继续拆分 Subagent 执行器和 runner 的 runtime 依赖。

### 已完成切片：Turn 执行契约归位

状态：已完成（2026-08-31）

- [x] 将不可变 `TurnExecution` 从 `mind_app/runtime/turns/executor.py` 重组到
  `agent/application/turn_execution.py`；模型身份、输入载荷、metadata 和追加上下文的冻结逻辑由
  application 统一持有。
- [x] 在 `agent/ports/hooks.py` 增加 `HookExecutionScopePort`，TurnExecution 只依赖
  `require_turn`、context 和 dispatcher 的最小结构端口；runtime `HookExecutionScope` 继续负责
  具体 dispatcher、状态端口绑定和生命周期。
- [x] Turn/RunResult/Stream/Subagent/TUI 定向回归 `108 passed`，新增 TurnExecution/application
  守卫；导入图、`compileall` 和 `git diff --check` 通过。下一切片继续拆分 Subagent 执行器和 runner
  的 runtime 依赖。

### 已完成切片：MCP 会话端口归位

状态：已完成（2026-08-31）

- [x] 将 `McpSessionLike` 从 `mind_app/runtime/mcp/contracts.py` 重组为
  `agent/ports/mcp_session.py` 的 `McpSessionPort`；会话端口只描述工具发现与调用所需能力，
  不拥有 Composite session 或 MCP provider 生命周期。
- [x] MCP runtime、Turn、工具、Subagent、Controller 和 TUI 全部切换到 `agent.ports` 公开入口，
  删除旧 contracts 模块和旧导入，不保留兼容 facade。
- [x] 新增旧路径/旧导入及端口边界守卫；MCP/工具/Subagent/架构回归 `64 passed, 48 warnings`，
  `compileall`、导入图和 `git diff --check` 通过。下一切片继续拆分 Subagent 执行器和 runner 的
  runtime 依赖。

### 已完成切片：Turn 与 Subagent 执行端口归位

状态：已完成（2026-08-31）

- [x] 将 `TurnResult`/`TurnOperation` 与输入事件回调重组到 `agent/ports/turns.py`，
  `mind_app/runtime/turns/executor.py` 只保留模型会话生命周期、工具过滤和结果收束实现。
- [x] 将 `SubagentExecutionPort`/`SubagentOperation` 重组到 `agent/ports/subagents.py`，
  `StreamSubagentExecutor` 和 `SubagentRunner` 不再定义跨层协议；Subagent runtime 通过 ports 注入
  执行端口，保留 Hook 停止决定与续跑状态机。
- [x] Turn/Subagent/工具回归 `54 passed`，新增端口与旧 runtime contract 守卫；导入图、
  `compileall` 和 `git diff --check` 通过。下一切片继续拆分 Subagent 流式适配器与 runtime 控制器。

### 已完成切片：Subagent Hook 生命周期归位

状态：已完成（2026-08-31）

- [x] 将 `SubagentHookEvents` 从 `mind_app/runtime/hooks/subagent.py` 重组到
  `agent/application/subagent_hooks.py`；生命周期事件聚合、续跑决定和上下文上限只依赖
  `HookExecutionScopePort` 与 application Hook 结果模型。
- [x] 扩展 `HookExecutionScopePort` 的匹配和分发能力，runtime `HookExecutionScope` 继续拥有
  公共上下文注入、输入校验和 dispatcher 生命周期；删除旧 runtime Hook 模块及旧导入。
- [x] Hook/Subagent/Turn 与架构守卫回归 `137 passed, 1 warning`，`compileall`、导入图和
  `git diff --check` 通过。下一切片继续拆分 Subagent 流式适配器与 Harness 控制器。

### 已完成切片：SubagentRunner Harness 化

状态：已完成（2026-08-31）

- [x] 将 `SubagentRunner` 从 `mind_app/runtime/subagents/runner.py` 重组到
  `agent/harness/subagent_runner.py`；续跑构造 `create_continuation_execution` 同步归入
  application，并通过 `HookExecutionScopePort.for_turn` 创建新 Turn 作用域。
- [x] Harness runner 只依赖 `SubagentTurnRunner`、`SubagentCleanupPort`、Hook scope 和
  application 结果，不再持有 `Mind` 或调用 runtime `execute_turn`；`SubagentRuntime` 通过
  `_run_turn` 适配器注入具体执行器。
- [x] 修正 `agent.harness` 包初始化循环，移除聚合导出，组合根和测试改为职责模块显式导入；
  Subagent/Turn/SessionLoop 回归 `65 passed`，新增 Harness 归属守卫，`compileall`、导入图和
  `git diff --check` 通过。下一切片继续拆分 Subagent 流式适配器。

### 已完成切片：Subagent 流式执行适配器归位

状态：已完成（2026-08-31）

- [x] 将 `StreamSubagentExecutor` 从 `mind_app/runtime/subagents/executor.py` 重组为
  `agent/adapters/subagent_execution.py` 的 `StreamSubagentExecution`；adapter 只依赖
  application Turn/Result 与 `SubagentStreamPort`，不加载 Controller、stream runtime 或输出实现。
- [x] 在 `agent/ports/subagents.py` 增加 `SubagentStreamPort` 和输入事件端口；SubagentRuntime
  通过 `_run_stream` 注入具体 stream 与静默输出工厂，删除旧 executor 模块，不保留 facade。
- [x] Subagent/Turn/工具回归 `61 passed`，新增 adapter/旧路径守卫；`compileall`、导入图和
  `git diff --check` 通过。下一切片继续收敛 SubagentRuntime 的 mailbox/graph 控制职责。

### 已完成切片：AgentControl Harness 归位

状态：已完成（2026-08-31）

- [x] 将完整 `AgentControl` 可变树状态机从 `mind_app/runtime/subagents/control.py` 重组到
  `agent/harness/agent_control.py`；Harness 现在拥有 Agent 生命周期、队列、mailbox 协调、
  caller 权限边界和恢复收束，domain 状态值与 stores 持久化继续保持独立。
- [x] SubagentRuntime、client tools、TUI 和测试全部切换到 Harness 职责模块，删除旧 control
  模块，不保留兼容 facade；现有 graph store/mailbox store 契约不变。
- [x] AgentControl/graph/Subagent/TUI 回归 `78 passed`，更新 graph/control 归属守卫；
  `compileall`、导入图和 `git diff --check` 通过。下一切片继续拆分 SubagentRuntime 的外部
  资源协调和 mailbox 投递状态。

### 已完成切片：Agent 活动投递状态归位

状态：已完成（2026-08-31）

- [x] 将 `AgentActiveTurn`、`AgentMessageDispatch` 和活动投递等待状态从
  `mind_app/runtime/subagents/delivery.py` 重组到 `agent/harness/agent_delivery.py`；状态机只
  依赖协议事件、Agent mailbox store 和消息投递端口。
- [x] SubagentRuntime 使用 Harness delivery 模块，TUI/测试切换到新职责路径，删除旧 runtime
  delivery 模块；端口、协议适配、活动状态三者不再混合。
- [x] Agent delivery、Subagent、TUI 与架构守卫回归通过，`compileall`、导入图和
  `git diff --check` 通过。下一切片继续拆分 SubagentRuntime 的外部资源协调和 mailbox 投递状态。

### 已完成切片：活动投递注册表归位

状态：已完成（2026-08-31）

- [x] 将 SubagentRuntime 内部的活动 Turn 注册、同 Agent 旧轮次关闭、并发注销和根会话清理
  逻辑收敛到 `agent/harness/agent_delivery.py` 的 `AgentDeliveryRegistry`；runtime 不再持有
  活动投递字典或锁。
- [x] 注册表与 `AgentActiveTurn` 共用 Harness 生命周期，保留 active-turn/mailbox 投递语义，
  不增加新的消息协议或状态源。
- [x] Agent delivery/Subagent/graph/TUI 回归 `83 passed, 1 warning`，扩展 delivery 归属守卫；
  `compileall`、导入图和 `git diff --check` 通过。下一切片继续拆分 SubagentRuntime 的外部
  资源协调和 mailbox 投递状态。

### 已完成切片：Agent 只读视图归位

状态：已完成（2026-08-31）

- [x] 将 `AgentSnapshot`、`AgentWaitResult`、`AgentMailboxWaitResult` 从
  `agent/harness/agent_control.py` 重组到 `agent/application/agent_views.py`；只读状态、等待结果
  和 mailbox 事件视图由 application 统一公开。
- [x] AgentControl 保留可变树记录与状态转移，SubagentRuntime、client tools、TUI 和测试切换到
  application 视图入口，避免 Harness 文件同时承担 mutable state 与 UI contract。
- [x] AgentControl/graph/Subagent/TUI 回归通过，新增视图边界守卫；`compileall`、导入图和
  `git diff --check` 通过。下一切片继续拆分 SubagentRuntime 的外部资源协调和 mailbox 投递状态。

### 已完成切片：Agent 根会话注册表归位

状态：已完成（2026-08-31）

- [x] 将 SubagentRuntime 持有的根会话 control 字典、并发锁和 shutdown 生命周期重组到
  `agent/harness/agent_registry.py` 的 `AgentControlRegistry`；恢复/创建通过显式 control factory 注入。
- [x] runtime 仅保留 graph store/persistence 和业务执行绑定，注册表统一负责 control 的串行复用、移除和批量关闭；保持禁用、缺失根会话和重复关闭语义不变。
- [x] 新增注册表行为与架构边界守卫；Subagent/runtime/graph 回归通过，`compileall`、导入图和
  `git diff --check` 通过。下一切片继续审计 SubagentRuntime 的上下文构造与外部资源适配边界。

### 已完成切片：父会话继承上下文算法归位

状态：已完成（2026-08-31）

- [x] 将继承上下文条目、轮次分组、范围选择、JSON 渲染和字符预算算法重组到
  `agent/application/fork_context.py`；新增 `ForkContextEntry`，application 不再依赖历史存储类型。
- [x] `mind_app/runtime/subagents/context.py` 收敛为 Transcript history adapter，负责读取并校验具体历史记录后调用 application builder；SubagentRuntime 只使用 `load_fork_context`。
- [x] 补充纯 application 与 history adapter 回归，旧 runtime 合约未重复定义；Subagent/context/架构守卫通过，`compileall`、导入图和
  `git diff --check` 通过。下一切片继续审计 SubagentRuntime 的上下文构造与外部资源适配边界。

### 已完成切片：上下文压缩结果与编排分离

状态：已完成（2026-08-31）

- [x] 将不可变 `CompactResult` 从 `mind_app/runtime/conversation.py` 提升到
  `agent/application/compact_result.py`，通过 application 公开入口提供稳定的压缩结果契约；
  runtime 不再定义结果值对象。
- [x] 将压缩 Hook、远端 compact stream、Transcript 记录和会话启动收束模块改名为
  `mind_app/runtime/compaction.py`，TUI 与压缩测试分别依赖运行时编排和 application 结果，删除
  旧 `runtime/conversation.py`，不保留兼容 facade。
- [x] 新增结果/编排分离架构守卫；压缩、TUI 和架构定向回归、导入图、`compileall` 与差异检查
  通过，下一切片继续审计 runtime/subagents 和剩余平铺入口。

### 已完成切片：执行上下文契约归位

状态：已完成（2026-08-31）

- [x] 将 `AgentContext`、`TurnContext` 和 `ToolInvocation` 从
  `mind_app/runtime/execution.py` 重组到 `agent/application/execution.py`，并通过
  `agent.application` 公开；契约模块只依赖 domain 权限类型、协议 schema 和 stores 类型，
  不依赖 runtime、基础设施或具体前端。
- [x] 切换 Turn、工具、Hook、MCP、子 Agent、内置工具、TUI 与测试消费者，删除旧 runtime
  execution 入口，不保留兼容 facade；新增 application 归属、旧导入和跨边界守卫。
- [x] 执行上下文及能力定向回归 `230 passed, 1 warning`，导入图、`compileall` 和差异检查通过；下一切片
  继续收敛子 Agent 的 mailbox/graph 值对象与持久化边界。

只有全部条件满足后才能删除四个历史包中的对应职责。根据阶段 5 前置审计，正式
`mind.chat` Python wire SDK 必须先迁入顶层 `protocol/`，再删除 `mind_nova`；不能
为了目录整洁把协议实现塞回 `agent.protocol`，也不能在旧包中长期保留兼容 facade：

- 非测试生产代码不再导入待删除的历史实现模块；
- `mind.py` 和所有外部入口已经切换到 `agent.composition`；
- 旧配置、历史、报告和订阅数据完成版本迁移；
- 主流程、失败路径、恢复、协议兼容和构建测试通过；
- 每个过渡入口都有删除记录，没有长期转发 facade；`protocol/` 必须有独立版本、
  兼容测试和明确的 wire contract 所有权。

历史包的物理目录可以分批删除，但每一批都必须保持可构建、可启动、可恢复。
不得用一次性 `Move-Item` 或批量改名替代上述出口条件。

## 过渡入口登记

| 入口 | 保留原因 | 删除条件 | 所属阶段 |
| --- | --- | --- | --- |
| `mind.py -> agent.composition` | 稳定启动方式和当前唯一具体组合根 | 保留稳定入口；下游 adapter 已由 capability/application 注入，不把具体装配退回旧包 | 3/4 |
| 旧 CLI 导入路径 | 外部脚本兼容 | 所有内部调用改走 application，完成兼容窗口 | 4/5 |
| 旧协议类型别名 | 数据和客户端迁移 | 新旧 schema 均有版本识别且无旧生产消费者 | 4/5 |
| 旧历史读取器 | 读取存量会话 | 历史数据迁移并完成回读校验 | 2/5 |
| `mind_app/cli/dispatch.py -> agent.application/protocol` | 首个主动 `exec` 入站切片 | CLI adapter 迁入 `agent.adapters.cli` 且入口只依赖公开组合根 | 4 |
| `agent.runtime` -> `agent.harness` | SessionLoop、RunActor 和 Session 所有者的 Harness 编排内核 | 已完成完整用例迁移、恢复/取消/并发测试和导入图收敛；旧包已删除且不保留兼容 facade | 4B |
| `agent/adapters/protocol_client.py -> protocol.client.chat` | 统一模型流、attach/replay 和审批恢复传输原语 | `protocol.client` 接管所有调用；adapter 只保留本地坐标、游标和 Item 投影 | 4B/5 |
| `agent/ports.ProtocolCommandClient -> protocol.client.*` | Protocol Client 统一提供中断、输入控制/对账、状态、fork、工具结果/状态/续期、审批和效果核对命令，并将 wire 错误归一化为 `ProtocolCommandError` | `protocol.client` 接管全部 endpoint；旧请求路径删除 | 4B/5 |
| 旧 wire 模块 -> `protocol/schema`、`protocol/transport`、`protocol/client` | 按职责重组 schema、HTTP/SSE/认证/事件投递和命令操作 | 三层边界测试、导入图和全量兼容测试通过；不保留 `protocol.requests` facade | 5 |
| `engine/observability.py` 及业务 logger -> `observability/` | 结构化观测和第三方 SDK 日志适配统一归属独立基础设施 | 业务包不再导入标准 `logging`、创建 `_LOGGER` 或调用 `getLogger`；AST 守卫持续通过 | 5 |
| `engine/enhance/` -> `mind_app/runtime/tools/enhancement/` | 运行工具结果增强和远端自愈结果汇总归属工具执行 adapter | 所有生产/测试消费者切换，导入图不再出现 `engine -> mind_core` 反向业务边；旧目录删除 | 5 |
| `engine/encoding.py`、`terminal.py`、`ports.py`、`file_assist.py` -> `infrastructure/platform/` | 进程、终端、端口和文件系统平台能力集中到独立基础设施边界 | 所有消费者切换且平台边界测试通过；旧模块删除 | 5 |
| `engine/errors.py` -> `infrastructure/errors.py` | 入口展示异常脱离历史 engine 包 | 全部消费者切换、异常行为回归通过、旧模块删除 | 5 |
| `engine/manage.py` -> `infrastructure/services/server_manager.py` | 本地后台服务生命周期进入服务基础设施 | CLI/MCP/Helix adapter 和服务测试切换；旧模块删除 | 5 |
| `engine/upgrade.py` -> `infrastructure/update/runtime.py` | 运行时下载、安装和升级进度进入更新基础设施 | 资源/入口升级测试切换；旧模块删除 | 5 |
| `engine/animation.py`、`signals.py` -> `infrastructure/platform/` | 通用异步动画和任务中断检测进入平台基础设施 | 全部消费者切换、语法和回归测试通过；旧模块删除 | 5 |
| `mind_core/licensing.py`、`remote_services.py` -> `infrastructure/services/` | 授权验证和远程服务元数据归入服务基础设施 | 唯一消费者切换、旧源模块删除、远程服务/增强回归通过 | 5 |
| `mind_core/mcp_status.py` -> `mind_app/presentation/mcp_status.py` | MCP 状态值对象与快照归约归入展示边界 | TUI/presentation 消费者切换、展示回归通过、旧源模块删除 | 5 |
| `mind_core/design/` -> `mind_app/presentation/terminal/` | 终端能力、颜色、进度、启动帧和下载渲染归入展示边界 | 终端/TUI/升级回归通过，旧设计包删除 | 5 |
| `mind_app/runtime/design.py` -> `mind_app/presentation/terminal/contracts.py` | 下载渲染协议归入终端展示端口 | 资产/升级/CLI/MCP 消费者切换，旧协议文件删除 | 5 |
| `mind_app/frontend/` -> `mind_app/presentation/application.py`、`application_sinks.py` | 应用级展示契约和 sink 归入 presentation | CLI/TUI/MCP/输出回归通过，旧目录源码删除；顶层 frontends 延后至完整入口迁移 | 5 |
| `mind_app/output/` -> `mind_app/presentation/output/` | 单轮输出端口、正文内容与文本/JSONL/静默适配器归入 presentation | 流式/TUI/CLI/MCP/Subagent 消费者切换，旧目录源码删除，架构守卫通过 | 5 |
| `mind_app/stream_events/` -> `mind_app/presentation/stream/` | 流事件展示投影、trace 和生命周期渲染归入 presentation | 流事件/输出/TUI 消费者切换，旧目录源码和 `tool_trace` facade 删除，架构守卫通过 | 5 |
| `mind_app/stream_io/`、`stream_state/` -> `mind_app/presentation/output/` | 输出记录、边界和 spacing 状态归入输出适配器 | `StreamRecordWriter` 消费者切换，旧平铺包删除，输出回归通过 | 5 |
| `mind_app/approval/permission_grants.py`、`ledger.py` -> `agent/stores/` | 会话权限授权与审批消费状态由 stores 持有 | 控制器/流式/工具消费者切换，旧状态模块删除，state store 守卫通过 | 5 |
| `mind_app/reporting.py` -> `observability/reporting.py` | 运行报告目录和诊断日志 sink 归入可观测性基础设施 | CLI/MCP/控制器消费者切换，旧模块删除，observability 反向依赖守卫通过 | 5 |
| `mind_app/paths.py` -> `infrastructure/config/runtime_paths.py` | 用户数据目录和运行时数据库路径归入配置基础设施 | CLI/MCP/Subscription/TUI/History/Runtime 消费者切换，旧模块删除，路径守卫通过 | 5 |
| `mind_app/assets.py` -> `infrastructure/update/assets.py`；终端进度适配 -> `mind_app/presentation/terminal/download_renderer.py` | 升级资产判断下沉到更新基础设施，UI 适配停留在展示边界 | 更新/MCP/CLI/TUI 升级回归通过，旧模块删除，基础设施无 UI 反向依赖 | 5 |
| `mind_app/attach.py` -> `mind_app/interaction/attachments.py` | 待发送附件状态归入交互输入边界 | 控制器/TUI/附件回归通过，旧模块和导入删除，架构守卫通过 | 5 |
| `mind_app/mcp/` -> `mind_app/runtime/mcp/` | MCP 配置、连接、会话组合、工具结果和 stdio 服务归入单一运行时边界 | MCP/工具/Turn/TUI 回归通过，旧包删除，架构守卫和导入图通过 | 5 |
| `mind_app/native_coding/encoding.py` -> `infrastructure/platform/encoding.py` | 进程输出解码统一由平台基础设施持有 | 编码/进程/shell/终端回归通过，旧模块删除，平台边界守卫通过 | 5 |
| `mind_app/runtime/processes.py`、`native_coding/workspace_command.py`、`git_diff.py` -> `infrastructure/platform/` | 进程树、工作区命令和 Git 差异统一归入平台基础设施 | 进程/工作区/Git/TUI 回归通过，旧模块删除，基础设施边界守卫通过 | 5 |
| `mind_app/native_coding/exec/command_safety/` -> `infrastructure/platform/command_safety/` | 危险命令与外部启动识别归入跨平台安全边界 | 安全/执行策略回归通过，旧安全包删除，平台边界守卫通过 | 5 |
| `mind_app/native_coding/exec/process_capture.py`、`output_decoder.py`、`sandbox_client.py`、`shell_runtime.py` -> `infrastructure/platform/` | 本地进程捕获、输出解码、Sandbox sidecar 和 shell 解析归入平台执行基座 | 进程/shell/Sandbox/执行策略回归通过，旧模块删除，平台边界守卫通过 | 5 |
| `mind_app/native_coding/exec/execpolicy/` -> `agent/domain/execution_policy/`、`infrastructure/config/execution_policy.py` | 执行策略领域值对象与规则文件解析分离 | 执行策略/审批/工具/配置回归通过，旧策略包删除，domain/config 边界守卫通过 | 5 |
| `mind_app/runtime/environment/coding_lifecycle.py` -> `agent/harness/workspace_runtime.py` | 工作区编码/Shell/策略/进程能力生命周期由 Harness 持有，具体工厂由唯一组合根注入 | 生命周期/组合接线回归通过，旧模块删除，Harness legacy-import 守卫通过 | 5 |
| `mind_app/runtime/environment/snapshot.py` -> `agent/application/environment.py`、`mind_app/interaction/environment.py` | 环境能力采集用例与 Controller/Helix 上下文适配分离 | 环境/入口回归通过，旧模块删除，application 边界守卫通过 | 5 |
| `mind_app/runtime/hooks/models.py` -> `agent/application/hook_models.py` | Hook 生命周期快照、决定、输出和工具结果值对象归入 application contract；执行器/注册器仍由 runtime 持有 | Hook/工具/Turn/TUI 定向回归 `421 passed`，旧模块/旧导入守卫和 application 边界守卫通过 | 5 |
| `mind_app/runtime/hooks/protocol.py` -> `agent/application/hook_protocol.py` | Hook stdin/stdout schema、构建和校验归入 application boundary；不与线上 `protocol/` 混淆 | Hook 协议/架构定向回归 `31 passed`，旧模块/旧导入及 application 边界守卫通过 | 5 |
| `mind_app/runtime/hooks/catalog.py` -> `agent/application/hook_catalog.py` | Hook 管理目录、状态快照和内容冲突归入 application contract | Hook 目录/TUI/架构定向回归 `22 passed`，旧模块/旧导入及 application 边界守卫通过 | 5 |
| `mind_app/runtime/hooks/matching.py` -> `agent/domain/hook_matching.py` | Hook matcher 和工具别名归入纯 domain 规则，不反向依赖 application | Hook 目录/执行/架构定向回归 `22 passed`，domain 边界守卫通过 | 5 |
| `mind_app/runtime/hooks/events.py` -> `agent/application/hook_events.py` | Hook 生命周期事件规格和目录一致性归入 application contract | Hook 事件/架构定向回归 `32 passed`，旧模块/旧导入及 application 边界守卫通过 | 5 |
| `mind_app/runtime/hooks/effects.py` -> `agent/application/hook_output.py` | Hook 输出语义校验、决定归一化和业务阻断归入 application contract | Hook 输出/架构定向回归 `32 passed`，旧模块/旧导入及 application 边界守卫通过 | 5 |
| `mind_app/runtime/hooks/output_spill.py` -> `infrastructure/platform/hook_output_spill.py` | Hook 大输出临时文件和流读取归入平台基础设施 | Hook 执行/平台/架构定向回归通过，旧模块/旧导入及平台边界守卫通过 | 5 |
| `mind_app/runtime/hooks/results.py` -> `agent/application/hook_result.py` | 后置 Hook 工具结果替换、反馈和上下文投影归入 application contract | Hook 工具/架构定向回归通过，旧模块/旧导入及 application 边界守卫通过 | 5 |
| `mind_app/runtime/tools/mode_policy.py` -> `agent/domain/tool_policy.py` | app/api 工具可见性与元数据过滤归入纯 domain 策略 | 工具策略/CLI/MCP/Turn/TUI 定向回归通过，旧模块/旧导入及 domain 边界守卫通过 | 5 |
| `mind_core/application_paths.py` -> `infrastructure/config/paths.py` | 应用入口和本地资源路径解析归入配置基础设施 | 所有路径消费者切换、路径回归通过、旧源模块删除 | 5 |
| `mind_core/agent_config.py`、`feature_config.py` -> `agent/application/settings.py` | Agent 运行设置与能力开关归入 application | application 公开入口、配置/设置/Subagent 回归通过，旧源模块删除 | 5 |
| `mind_core/provider_config.py` -> `infrastructure/config/providers.py` | Provider 默认值、路由和 Profile 标识约束归入配置基础设施 | 配置服务/偏好/Provider 选择回归通过，旧源模块删除 | 5 |
| `mind_core/skills/` -> `infrastructure/skills/` | 技能发现、解析、过滤和 payload 归入本地资源基础设施 | Skills/TUI/流式/Subagent 回归通过，旧目录删除 | 5 |
| `mind_core/project_trust.py` -> `infrastructure/config/trust.py` | 项目根、Git 和信任登记解析归入配置基础设施 | 配置层/CLI 信任回归通过，旧源模块删除 | 5 |
| `mind_core/permissions.py` -> `agent/domain/policies.py` | 沙箱、审批、网络访问和预设策略归入 Harness domain | 权限/执行上下文/TUI/MCP/Subagent 回归通过，旧源模块删除 | 5 |
| `mind_core/service_config.py` -> `infrastructure/services/service_config.py` | 服务域名读取和规范化归入服务基础设施 | CLI/MCP/配置服务回归通过，旧源模块删除 | 5 |
| `mind_core/preference.py`、`config_to_preferences` -> `infrastructure/config/preferences.py` | 偏好投影、覆盖和远程读取归入配置基础设施 | 偏好/Provider/CLI/MCP 回归通过，旧源模块删除 | 5 |
| `mind_core/config_store.py` -> `infrastructure/config/store.py` | TOML 文件存储和原子更新归入配置基础设施 | 配置会话/服务/CLI/TUI 回归通过，旧源模块删除 | 5 |
| `mind_core/config.py` -> `infrastructure/config/schema.py` | 配置 schema、规范化和覆盖校验归入配置基础设施 | 配置/CLI/TUI 回归通过，旧源模块删除 | 5 |
| `mind_core/config_layers.py` -> `infrastructure/config/layers.py` | 配置优先级合并和项目层解析归入配置基础设施 | 配置层/项目信任/CLI 回归通过，旧源模块删除 | 5 |
| `mind_core/config_session.py` -> `infrastructure/config/session.py` | 配置更新、覆盖校验和信任提交归入配置基础设施 | 配置服务/CLI/TUI/Server 回归通过，旧源模块删除 | 5 |

## 风险与处理

### 并发状态竞争

风险：原有 controller、stream 和 subagent 控制器可能同时写状态。

处理：先让所有写入口经过 RunActor，再拆分实现；测试检查同一 Run 的 sequence
单调递增和唯一写者。

### 外部副作用重复

风险：进程在请求已发出但结果未返回时退出。

处理：Effect Journal 标记 `unknown`，用 fingerprint 对账；高风险效果没有明确
结果时不自动重放。

### 过渡层永久化

风险：旧包 facade 逐渐成为新的共享杂物层。

处理：每个过渡入口必须有删除条件、负责人和阶段；新代码禁止反向依赖旧包。

### 前端协议分叉

风险：TUI、桌面端和 Web 各自解释 `event_seq`、Canonical Item、审批或工具结果，
导致同一 Turn 在不同前端出现不同状态。

处理：先建立唯一 Protocol Client 和 Canonical Event/Item reducer；前端只实现
渲染、输入和平台工具能力，并使用同一组协议 fixture 验证恢复与终态。

### 文档与实现漂移

风险：协议、CLI、MCP 和订阅行为只更新代码。

处理：架构文档、专题文档、manifest 和生成页随契约变更一起校验。

## 每个变更的最小验证集

```text
.\venv\Scripts\python.exe -m pytest <受影响测试>
.\venv\Scripts\python.exe -m py_compile <受影响源码>
python website/mind/scripts/check_docs.py
```

修改共享协议、控制器、恢复、配置或公开入口时，扩大到完整测试套件并检查：

- import graph 是否出现 domain/protocol -> adapter 反向依赖；
- `backend/` 是否保持无外部导入；
- 事件游标、快照版本和 outbox 是否向后兼容；
- Windows、Linux、macOS 的路径和子进程行为是否仍通过显式平台端口。

## 变更记录

| 日期 | 阶段 | 结果 | 未决项 |
| --- | --- | --- | --- |
| 2026-08-28 | 阶段 0 | 已完成主动 Turn 入口和五类 runtime 所有权收敛；全量测试 `2808 passed, 13 skipped` | 导入图、外部契约目录、Command/Event 映射、基线矩阵和 `backend/` 证据待收口 |
| 2026-08-28 | 阶段 0 | 提交导入图、外部契约、Command/Event 映射、测试矩阵和 backend tree 基线；定向验收 `222 passed`，其余静态检查通过 | `engine -> mind_core -> engine` 循环登记到阶段 3；阶段 0 无未决项 |
| 2026-08-28 | 阶段 1 | 主动 `exec` 已接入类型化 Command、SessionLoop、RunActor 和 Event Queue；全量测试 `2816 passed, 13 skipped`，导入图和静态检查通过 | `stream_turn` 内部职责拆分、CLI/TUI Event 投影和长生命周期 Session 接管待完成 |
| 2026-08-28 | 阶段 1 | CLI 改从终态 Event 投影退出码；`stream_turn` 已拆出请求准备、唯一终态写者和模型正文投影；全量测试 `2828 passed, 13 skipped` | 工具效果、回合级展示/清理、TUI 和长生命周期 Session 接管待完成 |
| 2026-08-28 | 阶段 1 | 工具结果冻结、同键并发去重、状态重试和 Effect 对账已收敛到 `ToolResultDelivery`；全量测试 `2832 passed, 13 skipped` | 回合级展示/清理、TUI Event 投影和长生命周期 Session 接管待完成 |
| 2026-08-28 | 阶段 1 | 临时状态、终态记录、Stop Hook 和输出资源关闭已收敛到 `StreamTurnFinalizer`；全量测试 `2835 passed, 13 skipped` | 回合级展示、TUI Event 投影和长生命周期 Session 接管待完成 |
| 2026-08-28 | 阶段 1 | 启动、失败和最终运行展示已收敛到 `StreamTurnPresentation`，两层旧失败转发已删除；全量测试 `2841 passed, 13 skipped` | TUI Event 投影和长生命周期 Session 接管待完成 |
| 2026-08-28 | 阶段 1 | 正式 `PROTOCOL.md` 已落地 Canonical Item 门禁、工具批次边界、`stream.gap` 和 Session 跨 Turn 事件水位；全量测试 `2853 passed, 13 skipped` | TUI Event Queue 投影和长生命周期 Session application 接管待完成 |
| 2026-08-28 | 阶段 1 | TUI 普通 prompt 已接入 `TurnApplication`、长生命周期 SessionLoop 和 Event 终态投影；并发提交、取消重建及可取消关闭等待均有测试；全量测试 `2859 passed, 11 skipped`，导入图、语法和边界检查通过 | 阶段 1 无未决项；阶段 2 未开始 |
| 2026-08-28 | 阶段 2 | `SQLiteRunStore` 落地事件、快照、outbox、最终事实原子提交与恢复门禁；效果账本迁入 `agent.stores`，CLI/TUI 生产组合接入独立 `runtime.db`；全量测试 `2876 passed, 11 skipped`，导入图、语法和边界检查通过 | 阶段 2 无未决项；阶段 3 未开始 |
| 2026-08-29 | 阶段 3 | 模型请求、事件流生命周期与结束原因已进入 `agent.protocol/ports`，远端翻译收口到 capability；`mind.py` 通过 `RuntimeServices` 统一注入模型、Turn store 和效果账本工厂，application 不再反向加载具体组合；全量测试 `2884 passed, 11 skipped` | 模型事件类和具名持久错误仍待迁移；MCP、Helix、process、filesystem 端口尚未接管 |
| 2026-08-29 | 阶段 3 | runtime 增加 `ModelEventStream` 运行时契约门禁，并在流式回合 `finally` 等待 capability 关闭，覆盖正常、失败和取消路径；全量测试 `2884 passed, 11 skipped` | 模型事件类和具名持久错误仍待迁移；MCP、Helix、process、filesystem 端口尚未接管 |
| 2026-08-29 | 阶段 3 | `RemoteModelEventStream` 统一模型能力异常，`RunResult` 与 `run_failed` 事件保留稳定错误码、重试性和 JSON 细节；全量测试 `2890 passed, 11 skipped`，定向异常/持久化测试通过 | 模型事件类仍位于 `mind_nova`；MCP、Helix、process、filesystem 端口尚未接管 |
| 2026-08-29 | 阶段 3 | 客户端事件模型接收新的 provider retry/failure 元数据并校验错误类型一致性；失败分类进入 `RunResult`，`Retrying` 展示状态机保持不变；全量测试 `2892 passed, 11 skipped` | 模型事件类整体仍位于 `mind_nova`；MCP、Helix、process、filesystem 端口尚未接管 |
| 2026-08-29 | 阶段 3 | `agent.protocol.ModelEvent` 固化 capability 事件坐标，`ModelEventStream` 从 `Any` 收窄为协议事件迭代器；全量测试 `2912 passed, 11 skipped`，导入边界、语法和文档检查通过 | 具体模型事件类仍位于 `mind_nova`；MCP、Helix、process、filesystem 尚未接管 |
| 2026-08-29 | 阶段 3 | 模型 adapter 将 `ModelEvent` 声明升级为运行时门禁，非法传输对象收敛为 `model_protocol_error` 并关闭流；全量测试 `2913 passed, 11 skipped`，定向、语法、文档和 diff 检查通过 | 具体模型事件类仍位于 `mind_nova`；MCP、Helix、process、filesystem 尚未接管 |
| 2026-08-29 | 阶段 3 | 协议层统一模型事件坐标与序号校验，adapter 仅负责传输错误转换；新增非法字段关闭路径测试；全量测试 `2914 passed, 11 skipped`，定向、语法和边界检查通过 | 具体模型事件类仍位于 `mind_nova`；MCP、Helix、process、filesystem 尚未接管 |
| 2026-08-29 | 阶段 3 | 冻结 JSON 的解冻边界改为具名类型和运行时对象校验，清除 `agent` 生产代码中的强制类型断言；全量测试 `2914 passed, 11 skipped`，定向测试 `96 passed`，语法检查通过 | 具体模型事件类仍位于 `mind_nova`；MCP、Helix、process、filesystem 尚未接管 |
| 2026-08-29 | 阶段 3 | 完成 model/MCP/Helix/process/filesystem capability ports；新增协议值对象、统一 `CapabilityError`、本地进程/文件实现和四类内存替身；定向 capability 测试 30 项、全量测试 `2926 passed, 11 skipped` | 旧 `mind_nova` wire decoder、`ServerManage`、`ProcessSessionManager` 和 `SandboxClient` 作为 legacy adapters 转入阶段 4/5；模型轮次不隐式触发 Helix |
| 2026-08-29 | 阶段 4 | stdio MCP `mind_exec` 通过注入的 `TurnApplication` 提交冻结 `SubmitTurnCommand`，structured content 优先来自 `RunResultProjection`；MCP 不再直接拥有 Turn application 生命周期；定向 MCP/架构测试 `24 passed` | CLI、TUI、Subscription 仍待统一 Command Gateway；旧根轮次仅保留为显式执行器 adapter |
| 2026-08-29 | 阶段 4 | Subscription `AgentExecutor` 接入长驻 `TurnApplication`，稳定冻结远端身份、metadata、附件和 extras，并在取消/关闭时收束 Session；终态分类改用 Event projection；定向 Subscription 测试 `49 passed`，全量测试 `2929 passed, 11 skipped` | 四类入口的完整结果/展示投影仍待统一；旧根轮次和 legacy Helix/process adapter 待迁移 |
| 2026-08-29 | 阶段 4 | CLI `exec` 的完成观测改用 `RunResultProjection.status`，补齐结果投影权威性测试；CLI/架构定向测试 `107 passed` | 四类入口仍保留兼容性原始结果对象；旧根轮次和 legacy Helix/process adapter 待迁移 |
| 2026-08-29 | 阶段 4 规划修订 | 根据多前端目标补充 Protocol Client 与 Canonical Event/Item reducer 边界；明确 `mind.chat` 是 TUI、桌面端和 Web 的公共 wire contract，`agent.protocol` 仅为本地 Harness 事件 | 阶段 4B 尚未开始；TUI 仍依赖 `Mind`、`stream_turn` 和 EventReport，协议 SDK 的最终归属待确定 |
| 2026-08-29 | 阶段 4 规划修订 | 将主线名称从 Agent Runtime 提升为 Agent Harness；目标编排包确定为 `agent.harness`，根目录 `server/` 明确为客户端内置 ConfigServiceRuntime，仅提供配置 UI/健康检查 | 已完成 `agent.runtime` -> `agent.harness` 的首个完整切片；Protocol Client/TUI 迁移仍待开始，server 仍出现在当前导入图中但不属于 Harness 状态边界 |
| 2026-08-29 | 阶段 4 插入切片 | 复核新的客户端 `exec_env` 快照契约；将环境快照提升为 `ModelStreamRequest.environment_snapshot` 一等冻结字段，通用 options 拒绝夹带，model adapter 在 wire 边界映射回 `exec_env`；定向测试 `93 passed`、全量测试 `2951 passed, 11 skipped` | 本机采集和 Helix provider 聚合仍位于 legacy runtime；持久 queued 命令尚未冻结环境快照，跨进程 redispatch 仍需收口 |
| 2026-08-29 | 阶段 4 插入切片 | 完成环境输入所有权收口：新增进程级 `EnvironmentSnapshotCapability`，删除 legacy `exec_env.py`；四类主动入口在提交前捕获，`SubmitTurnCommand` 深冻结、序列化并纳入指纹，stream/model adapter 只消费命令快照；补齐 SQLite 重启恢复证据，全量测试 `2951 passed, 11 skipped` | 环境切片完成；阶段 4 下一项恢复为 Protocol Client 与 TUI Canonical Event/Item 投影迁移 |
| 2026-08-30 | 阶段 4B | 建立首个独立 `MindChatProtocolClient` 生产切片：请求坐标、wire 映射、事件坐标门禁和 Session 结算游标迁出控制器；删除旧 model capability facade 与 `turns/delivery.py`，更新临时依赖登记和导入图；定向测试 `197 passed` | Canonical Event/Item reducer、命令面和 TUI adapter 待迁移；全量两轮均为 `2953 passed, 11 skipped, 1 failed`，失败分别是既有 TUI 120ms scrollback 和 Windows Hook 2s 时序上限，两个失败用例单独复跑均通过 |
| 2026-08-30 | 阶段 4B | `CanonicalItemReducer` 接入 Protocol Client 的事件交付门禁，统一正文/工具 Item、状态收敛、provider retry 和 presentation supersede，提供 active/audit 快照；同时修复非持久 `stream.gap` 被 capability 误拒绝的问题；定向测试 `197 passed`，全量测试 `2962 passed, 11 skipped` | reducer 尚未归约审批快照，TUI 仍使用旧 `SegmentTracker` 解释正文和展示边界；协议命令面尚待迁移 |
| 2026-08-30 | 阶段 4B | 审批恢复快照接入 `CanonicalItemReducer`：Protocol Client 在旧前端 callback 前按快照水位提交 pending/resolved/cancelled 状态，旧 pending 回放不得重开终态审批，并独立公开 `pending_approval_items`；定向测试 `199 passed`、全量测试 `2964 passed, 11 skipped` | TUI 尚未消费 Canonical Item 投影；协议命令面和旧审批交互 adapter 待迁移 |
| 2026-08-30 | 阶段 4B | 最终正文所有权迁入 Protocol Client：`ModelEventStream.assistant_text` 统一驱动 RunResult、Stop Hook 和最后回复记忆，删除旧 handler/tracker 聚合 API；运行流测试接入真实 reducer，并修复 retry 后迟到旧 Item 污染新 attempt 的问题；定向测试 `231 passed`、全量测试 `2965 passed, 11 skipped` | TUI delta、sources 和 Transcript presenter 仍待消费 Canonical Items；协议命令面尚待迁移 |
| 2026-08-30 | 阶段 4B | 模型输出 presenter 完成 Canonical Item 迁移：`current_item` 驱动 delta/完成展示，active text/builtin Items 聚合 sources，audit Items 幂等提交 Transcript revision；删除 `SegmentTracker` 及重复状态机；定向测试 `246 passed`、全量测试 `2955 passed, 11 skipped` | TUI 工具/审批交互仍直接消费具体事件并调用 legacy 请求函数；Protocol Client 命令面尚待迁移 |
| 2026-08-30 | 阶段 4B | 建立 `ProtocolCommandClient` 端口并由 `MindChatProtocolClient` 实现；运行流的中断、工具结果/状态、审批和效果核对统一经 Protocol Client 交付，wire 错误归一化为 `ProtocolCommandError`；定向测试 `100 passed`、全量测试 `2958 passed, 11 skipped` | TUI 本地工具/审批策略与 UI 交互仍在 `mind_app/runtime/turns`，TUI 输入控制、attach/replay 和其余命令尚待纳入同一公共端口 |
| 2026-08-30 | 阶段 4 出口 | 完成多入口与 Protocol Client 收口：CLI/MCP/Subscription 使用 `RootTurnCommandExecutor`，TUI 输入、fork、模型/工具/审批/效果命令经统一 Protocol Client；Process/Helix 生命周期通过显式 capability 注入；跨前端 Canonical fixture 覆盖文本、工具批次、审批、工具输出、来源和结算；全量测试 `2961 passed, 11 skipped`，语法、架构边界和文档检查通过 | 阶段 5 再处理 legacy wire 错误兼容导入、attach/replay 独立 SDK 归属、根轮次 executor 和 Sandbox sidecar 的物理退役；阶段 5 未开始 |
| 2026-08-30 | 阶段 5 前置规划 | 完成 `agent/` 与外围目录职责审计并将最终目标修订为退役 `engine`、`mind_nova`、`mind_core`、`mind_app`；正式 wire SDK 迁入职责化的顶层 `protocol/`，前端和基础设施边界单独规划，不创建空目录或进行机械搬迁 | 阶段 5 从元数据切片启动；下一切片为协议 SDK 分层与可观测性边界 |
| 2026-08-30 | 阶段 5 首个切片 | 将产品版本、展示和编码常量抽出到顶层 `metadata/`；`setup.py`、配置服务页面和 `/version` 响应已切换，补充元数据边界测试；导入图、文档检查、语法检查和定向测试 `12 passed` | 端点、认证和运行时路径已在后续 Protocol/Observability 切片按职责归位；`mind_app`、`mind_core`、`engine` 仍待重组 |
| 2026-08-30 | 阶段 5 Protocol/Observability 切片 | 将 wire SDK 按 `schema/transport/client` 重组，删除 `protocol/requests`；结构化观测从 `engine` 提取到顶层 `observability/`，第三方日志过滤集中管理；新增日志 AST 守卫，协议/日志定向测试 `103 passed` | 全量测试 `2964 passed, 11 skipped`，导入图 `--check`、语法和 diff 检查通过；下一切片处理 `mind_app`、`mind_core`、`engine` 职责重组 |
| 2026-08-30 | 阶段 5 运行工具增强切片 | 将 `engine/enhance` 迁入 `mind_app/runtime/tools/enhancement`，并把远程自愈流拆到 `protocol/client/heal.py`；旧增强路径删除，`engine -> mind_core` 反向业务依赖消除；定向增强/架构测试 `13 passed` | 全量测试 `2965 passed, 11 skipped`，导入图显示无跨边界循环；平台和许可职责仍待迁移 |
| 2026-08-30 | 阶段 5 平台基础设施切片 | 将编码、终端、端口、文件辅助和 `AppError` 迁入 `infrastructure`，全部消费者切换并删除旧 `engine` 模块；新增平台边界守卫 | 全量测试 `2965 passed, 11 skipped`，`compileall`、`git diff --check` 和导入图 `--check` 通过；`engine/manage`、`engine/upgrade` 仍待退役 |
| 2026-08-30 | 阶段 5 服务/升级基础设施切片 | 将 `engine/manage`、`engine/upgrade`、`animation`、`signals` 按职责迁入 `infrastructure/services`、`infrastructure/update` 和 `infrastructure/platform`；删除整个 `engine` 源包并加入生产残留引用守卫 | 定向架构/服务/升级测试 `17 passed`，全量测试 `2965 passed, 11 skipped`，`compileall`、`git diff --check` 和导入图 `--check` 通过；下一切片处理 `mind_core` 职责拆分 |
| 2026-08-30 | 阶段 5 许可/远程服务切片 | 将 `mind_core/licensing.py`、`remote_services.py` 迁入 `infrastructure/services`，增强工具改用新服务边界，删除旧源模块并清除 `mind_core -> infrastructure` 许可反向边 | 定向架构/增强测试通过，导入图和语法检查通过；下一切片处理 `mind_core` 配置、策略、hooks/skills |
| 2026-08-30 | 阶段 5 MCP 展示切片 | 将 `mind_core/mcp_status.py` 合并到 `mind_app/presentation/mcp_status.py`，切换 TUI/presentation/test 消费者并删除旧 UI 状态模块 | 定向 MCP/TUI/架构测试 `208 passed`，语法检查通过；下一切片处理 `mind_core` 配置、策略、hooks/skills |
| 2026-08-30 | 阶段 5 应用路径切片 | 将 `mind_core/application_paths.py` 迁入 `infrastructure/config/paths.py`，切换应用、MCP、native coding、配置存储和测试消费者并删除旧路径模块 | 路径/架构定向测试、`compileall` 和导入图检查通过；下一切片处理 `mind_core` 配置、策略、hooks/skills |
| 2026-08-30 | 阶段 5 应用设置/Provider 配置切片 | 将 Agent/Feature 设置合并到 `agent/application/settings.py`，将 Provider 规则迁入 `infrastructure/config/providers.py`，切换所有消费者并删除三个旧模块 | 设置/配置服务/Subagent 定向测试 `244 passed`，架构测试 `15 passed`，全量回归 `2967 passed, 11 skipped`，`compileall` 和导入图检查通过；下一切片处理权限、项目策略、hooks/skills |
| 2026-08-30 | 阶段 5 Skills 资源切片 | 将 `mind_core/skills/` 重组到 `infrastructure/skills/`，切换 TUI、模型流和 Subagent 消费者并删除旧目录 | Skills/TUI/流式定向回归 `326 passed`，架构测试 `16 passed`，全量回归 `2968 passed, 11 skipped`，`compileall` 和导入图检查通过；下一切片处理权限、项目信任和 hooks |
| 2026-08-30 | 阶段 5 项目边界信任切片 | 将 `mind_core/project_trust.py` 迁入 `infrastructure/config/trust.py`，切换配置层/会话消费者，收窄信任级别类型并删除旧模块 | 项目配置/CLI 信任定向回归 `138 passed`，架构测试 `17 passed`，全量回归 `2969 passed, 11 skipped`，`compileall` 和导入图检查通过；下一切片处理权限策略和 hooks |
| 2026-08-30 | 阶段 5 权限领域策略切片 | 将 `mind_core/permissions.py` 迁入 `agent/domain/policies.py`，通过 application 公开入口切换执行、TUI、MCP 和 Subagent 消费者并删除旧模块 | 权限/执行上下文/TUI/MCP/Subagent 定向回归 `134 passed`，架构测试 `18 passed`，全量回归 `2970 passed, 11 skipped`，`compileall` 和导入图检查通过；下一切片处理 hooks 生命周期 |
| 2026-08-30 | 阶段 5 Hooks 领域/发现切片 | 将 Hook 类型和信任状态迁入 `agent/domain`，将配置与 `hooks.json` 发现迁入 `infrastructure/hooks/discovery.py`，切换所有配置/执行/TUI 消费者并删除旧模块 | Hook/配置/执行定向回归 `301 passed`，架构测试 `19 passed`，全量回归 `2971 passed, 11 skipped`，`compileall` 和导入图检查通过；下一切片处理前端入口与剩余 `mind_core` 配置职责 |
| 2026-08-30 | 阶段 5 服务配置切片 | 将 `mind_core/service_config.py` 迁入 `infrastructure/services/service_config.py`，引入 `ConfigReader` 端口并切换 CLI/MCP/配置服务消费者 | 服务域名和启动定向回归 `159 passed`，架构测试 `20 passed`，全量回归 `2972 passed, 11 skipped`，`compileall` 和导入图检查通过；下一切片处理偏好配置和剩余 `mind_core` 状态 |
| 2026-08-30 | 阶段 5 偏好配置切片 | 将 `mind_core/preference.py` 与 `config_to_preferences` 重组到 `infrastructure/config/preferences.py`，引入 `ConfigReader` 并切换 CLI/TUI/MCP/测试消费者 | 偏好/Provider/CLI/MCP 定向回归 `145 passed`，架构测试 `21 passed`，全量回归 `2973 passed, 11 skipped`，`compileall` 和导入图检查通过；下一切片处理配置层/存储边界和前端入口 |
| 2026-08-30 | 阶段 5 配置存储切片 | 将 `mind_core/config_store.py` 迁入 `infrastructure/config/store.py`，切换配置层、CLI、MCP、TUI、Server 和测试消费者 | 配置会话/服务/CLI/TUI/架构定向回归 `314 passed`，全量回归 `2974 passed, 11 skipped`，`compileall`、导入图和差异检查通过；下一切片处理配置解析/分层状态和前端入口 |
| 2026-08-30 | 阶段 5 配置 schema/分层/会话切片 | 将 `mind_core/config.py`、`config_layers.py`、`config_session.py` 迁入 `infrastructure/config/schema.py`、`layers.py`、`session.py`，删除旧配置入口并切换全部消费者 | 配置/CLI/TUI/Server 定向回归 `459 passed`，架构守卫 `23 passed`，全量回归 `2975 passed, 11 skipped`，`compileall`、导入图和差异检查通过；下一切片处理 TUI/Presentation 设计基础设施 |
| 2026-08-30 | 阶段 5 终端展示切片 | 将 `mind_core/design/` 重组到 `mind_app/presentation/terminal/`，迁移终端能力/颜色/进度/启动帧/下载渲染，删除 runtime 设计协议和 `Design` facade | 终端/TUI/升级定向回归通过，架构守卫 `24 passed`，全量回归 `2976 passed, 11 skipped`，`compileall`、导入图和差异检查通过；下一切片继续收敛前端目录和剩余历史入口 |
| 2026-08-30 | 阶段 5 应用级展示契约切片 | 将 `mind_app/frontend` 重组为 `mind_app/presentation/application.py` 与 `application_sinks.py`，切换 CLI/TUI/MCP/运行时事件消费者并修正导入图边界；顶层 `frontends/` 延后至完整入口迁移 | 前端契约/TUI/CLI 定向回归 `949 passed`，架构守卫 `25 passed`，全量回归 `2977 passed, 11 skipped`，`compileall`、导入图和差异检查通过；下一切片继续迁移完整 CLI/TUI 入口 |
| 2026-08-30 | 阶段 5 单轮输出适配器切片 | 将 `mind_app/output` 重组到 `mind_app/presentation/output`，切换流式 Turn、工具、Subagent、CLI、MCP、TUI 和测试消费者，删除旧输出目录源码并补充遗留导入守卫 | 输出/流式/TUI 定向回归 `98 passed`，架构守卫 `26 passed`，全量回归 `2978 passed, 11 skipped`，`compileall`、导入图和差异检查通过；下一切片进入流事件展示投影迁移 |
| 2026-08-30 | 阶段 5 流事件展示投影切片 | 将 `mind_app/stream_events` 重组到 `mind_app/presentation/stream`，将 `stream_io`、`stream_state` 内聚到 `presentation/output`，删除 `tool_trace` 导出 facade 并更新运行时/TUI/渲染器消费者 | 流事件/输出/TUI 定向回归 `174 passed`，架构守卫 `27 passed`，全量回归 `2979 passed, 11 skipped`，`compileall`、导入图和差异检查通过；下一切片继续完整入口边界迁移 |
| 2026-08-30 | 阶段 5 审批状态存储切片 | 将权限授权与审批调用账本从 `mind_app/approval` 重组到 `agent/stores`，切换控制器/流式/工具执行消费者，收紧旧应用对 agent 边界的允许集合并移除账本 `typing.cast()` | 审批/权限定向回归 `179 passed`，架构与基线回归 `29 passed`，全量回归 `2980 passed, 11 skipped`，`compileall`、导入图和差异检查通过；下一切片继续完整入口边界迁移 |
| 2026-08-30 | 阶段 5 运行报告观测切片 | 将 `mind_app/reporting` 重组到 `observability/reporting`，切换 CLI/MCP/控制器/日志测试消费者并补充旧路径守卫 | 观测/CLI/MCP 定向回归 `66 passed`，架构守卫 `29 passed`，基线 `1 passed`，全量行为回归 `2980 passed, 11 skipped`，导入图、`compileall` 和差异检查通过；下一切片继续完整入口边界迁移 |
| 2026-08-30 | 阶段 5 运行时路径基础设施切片 | 将 `mind_app/paths` 重组到 `infrastructure/config/runtime_paths`，切换 CLI/MCP/Subscription/TUI/History/Runtime/native coding 和配置存储消费者并删除旧模块 | 路径/历史/配置/CLI/TUI 定向回归 `101 passed`，架构守卫 `30 passed`，全量回归 `2982 passed, 11 skipped`，`compileall`、导入图和差异检查通过；下一切片继续完整入口边界迁移 |
| 2026-08-30 | 阶段 5 升级资产与交互附件切片 | 将 `mind_app/assets.py` 重组到 `infrastructure/update/assets.py`，终端进度适配留在 `presentation/terminal`；将 `mind_app/attach.py` 重组到 `mind_app/interaction/attachments.py`，删除旧根模块并加入双路径架构守卫 | 附件/Helix/CLI/TUI/架构定向回归 `295 passed`，全量回归 `2983 passed, 11 skipped`，`compileall`、导入图和差异检查通过；下一切片继续审计交互/历史/客户端工具和完整入口边界 |
| 2026-08-30 | 阶段 5 MCP 运行时边界切片 | 将平铺的 `mind_app/mcp/` 合并到 `mind_app/runtime/mcp/`，统一 MCP 配置、连接、状态、工具结果、会话组合和 stdio 服务；删除旧包及所有旧导入，不保留 facade | MCP/工具/模型流/架构定向回归 `119 passed`，全量回归 `2984 passed, 11 skipped`，`compileall`、导入图和差异检查通过；下一切片继续审计交互/历史/客户端工具和完整入口边界 |
| 2026-08-31 | 阶段 5 进程输出编码切片 | 将完整进程输出解码器从 `mind_app/native_coding/encoding.py` 提升到 `infrastructure/platform/encoding.py`，终端与 native coding 共用唯一平台实现并删除旧模块 | 进程/shell/终端/native coding/架构定向回归 `137 passed`，全量回归 `2985 passed, 11 skipped`，`compileall`、导入图和差异检查通过；下一切片继续审计交互/历史/客户端工具和完整入口边界 |
| 2026-08-31 | 阶段 5 工作区进程与 Git 平台切片 | 将进程树管理、无 shell 工作区命令和 Git 差异探测从 `mind_app` 下沉到 `infrastructure/platform`，清除进程工具 `typing.cast()` 并切换 Hook/native coding/TUI 消费者 | 进程/工作区/Git/TUI/native coding/架构定向回归 `123 passed`，全量回归 `2986 passed, 11 skipped`，`compileall`、导入图和差异检查通过；下一切片继续审计交互/历史/客户端工具和完整入口边界 |
| 2026-08-31 | 阶段 5 命令安全平台切片 | 将危险命令递归识别、PowerShell/cmd 删除与外部 URL 启动检测从 `mind_app/native_coding/exec/command_safety/` 重组到 `infrastructure/platform/command_safety/`；执行策略仅消费平台分类结果，删除旧安全包并加入架构守卫 | 命令安全/执行策略/平台/架构定向回归 `147 passed`，全量回归 `2987 passed, 11 skipped`，`compileall`、导入图和差异检查通过；下一切片继续审计交互、历史、客户端工具和完整入口边界 |
| 2026-08-31 | 阶段 5 本地进程执行基座切片 | 将进程捕获、输出解码、本地 Sandbox sidecar 客户端和 shell 运行时解析从 `mind_app/native_coding/exec/` 重组到 `infrastructure/platform/`，native coding 仅保留工具业务与执行策略；删除旧模块并加入平台归属守卫 | 进程捕获/解码、Sandbox、shell、执行策略/平台/架构定向回归 `146 passed, 11 skipped`，全量回归 `2988 passed, 11 skipped`，`compileall`、导入图和差异检查通过；下一切片继续审计交互、历史、客户端工具和完整入口边界 |
| 2026-08-31 | 阶段 5 JavaScript REPL 平台切片 | 将 Node 内核进程、会话隔离、临时目录、消息桥接和内核重置从 `mind_app/native_coding/js_repl/` 重组到 `infrastructure/platform/javascript_repl.py`，资源根改为显式应用布局注入并删除旧包 | JavaScript REPL 定向回归 `28 passed`，架构守卫 `37 passed`，全量回归 `2989 passed, 11 skipped`，`compileall`、导入图和差异检查通过；下一切片继续审计交互、历史、客户端工具和完整入口边界 |
| 2026-08-31 | 阶段 5 平台环境助手切片 | 将 shell 工具 PATH 路由和工作区根探测从 `mind_app/runtime/environment/` 重组到 `infrastructure/platform/`，runtime/CLI/MCP/TUI 只消费平台结果并删除旧模块 | 平台环境/CLI/MCP/TUI/架构定向回归 `179 passed`，全量回归 `2990 passed, 11 skipped`，`compileall`、导入图和差异检查通过；下一切片继续审计交互、历史、客户端工具和完整入口边界 |
| 2026-08-31 | 阶段 5 执行策略领域切片 | 将执行策略决定、规则和值对象从 `mind_app/native_coding/exec/execpolicy/` 重组到 `agent/domain/execution_policy/`，将 AST/文件解析重组到 `infrastructure/config/execution_policy.py`，删除旧策略包 | 执行策略专项回归 `19 passed`，架构守卫专项 `2 passed`，全量回归 `2991 passed, 11 skipped`，`compileall`、导入图和差异检查通过；下一切片继续审计交互、历史、客户端工具和完整入口边界 |
| 2026-08-31 | 阶段 5 工作区运行时生命周期切片 | 将工作区编码/Shell/执行策略/进程能力的生命周期所有者从 `mind_app/runtime/environment/coding_lifecycle.py` 重组到 `agent/harness/workspace_runtime.py`；`RuntimeServices` 提供工厂端口，`mind.py` 注入具体实现，Controller 不再隐式装配 | 生命周期与组合接线定向回归 `20 passed`，旧路径/旧导入守卫 `2 passed`，全量回归 `2992 passed, 11 skipped`，旧模块删除，导入图、`compileall` 和差异检查通过；下一切片继续审计交互、历史、客户端工具和完整入口边界 |
| 2026-08-31 | 阶段 5 环境快照用例切片 | 将环境能力调用与失败收敛从 `mind_app/runtime/environment/snapshot.py` 提取到 `agent/application/environment.py`，将工作区/Helix 上下文聚合放入 `mind_app/interaction/environment.py`，切换 CLI/TUI/MCP/Subscription/Turn setup 并删除旧模块 | 环境与入口定向回归 `180 passed`，旧路径/旧导入及 application 边界守卫 `2 passed`，全量回归 `2995 passed, 11 skipped`，旧模块删除，导入图、`compileall` 和差异检查通过；下一切片继续审计交互、历史、客户端工具和完整入口边界 |
| 2026-08-31 | 阶段 5 Hook 运行模型切片 | 将纯 Hook 生命周期快照、决定、输出和工具结果值对象从 `mind_app/runtime/hooks/models.py` 重组到 `agent/application/hook_models.py`，切换 runtime/TUI/工具/Subagent 消费者并删除旧模块 | Hook/工具/Turn/TUI 定向回归 `421 passed`，旧路径/旧导入及 application 边界守卫通过；全量回归 `2996 passed, 11 skipped`，导入图、`compileall` 和差异检查通过；下一切片继续审计交互、历史、客户端工具和完整入口边界 |
| 2026-08-31 | 阶段 5 Hook 协议边界切片 | 将 Hook stdin/stdout schema、事件输入构建和输出校验从 `mind_app/runtime/hooks/protocol.py` 重组到 `agent/application/hook_protocol.py`，切换 scope/事件/效果消费者并删除旧模块 | Hook 协议/架构定向回归 `31 passed`，旧路径/旧导入及 application 边界守卫通过；全量回归 `2997 passed, 11 skipped`，导入图、`compileall` 和差异检查通过 |
| 2026-08-31 | 阶段 5 Hook 目录与匹配规则切片 | 将 Hook 管理目录从 `mind_app/runtime/hooks/catalog.py` 重组到 `agent/application/hook_catalog.py`，将 matcher 与工具别名规则从 `matching.py` 重组到 `agent/domain/hook_matching.py`，切换 Controller/TUI/runtime 消费者并删除旧模块 | Hook 目录/TUI/执行/架构定向回归 `22 passed`，旧路径/旧导入及 application/domain 边界守卫通过；全量回归 `2999 passed, 11 skipped`，导入图、`compileall` 和差异检查通过 |
| 2026-08-31 | 阶段 5 Hook 输出与事件规格切片 | 将 Hook 输出归一化从 `mind_app/runtime/hooks/effects.py` 重组到 `agent/application/hook_output.py`，将事件规格从 `events.py` 重组到 `agent/application/hook_events.py`，切换 runtime/协议测试消费者并删除旧模块 | Hook 输出/事件/架构定向回归 `32 passed`，旧路径/旧导入及 application 边界守卫通过；全量回归 `3000 passed, 11 skipped`，导入图、`compileall` 和差异检查通过 |
| 2026-08-31 | 阶段 5 Hook 输出 spill 平台切片 | 将 Hook 流输出读取、临时文件 spill、预览和会话清理从 `mind_app/runtime/hooks/output_spill.py` 重组到 `infrastructure/platform/hook_output_spill.py`，切换 Hook command/测试消费者并删除旧模块 | Hook 执行/平台/架构定向回归 `20 passed`，旧模块/旧导入及平台边界守卫通过；全量回归 `3001 passed, 11 skipped, 33 warnings`，导入图、`compileall` 和差异检查通过 |
| 2026-08-31 | 阶段 5 Hook 工具结果投影切片 | 将后置 Hook 工具结果替换、反馈、阻断和上下文投影从 `mind_app/runtime/hooks/results.py` 重组到 `agent/application/hook_result.py`，切换 ToolHookEvents/工具测试消费者并删除旧模块 | Hook 工具/架构定向回归 `61 passed`，旧路径/旧导入及 application 边界守卫通过；全量回归 `3002 passed, 11 skipped, 34 warnings`，导入图、`compileall` 和差异检查通过 |
| 2026-08-31 | 阶段 5 工具模式策略切片 | 将 app/api 工具可见性、隐藏规则和元数据过滤从 `mind_app/runtime/tools/mode_policy.py` 重组到 `agent/domain/tool_policy.py`，切换 Controller/CLI/MCP/Turn/TUI 消费者并删除旧模块 | 工具策略/架构定向回归 `4 passed`，旧路径/旧导入及 domain 边界守卫通过；全量回归 `3003 passed, 11 skipped, 35 warnings`，导入图、`compileall` 和差异检查通过 |
| 2026-08-31 | 阶段 5 运行结果与平台计时器切片 | 将 `RunResult`、流式终态聚合和本地 Session 身份派生重组到 `agent/application`，将空闲状态计时器下沉到 `infrastructure/platform`，删除无调用者的 `AsyncRWLock` 并清除旧 runtime/support 导入 | 定向结果/流式/TUI/CLI/架构回归 `263 passed, 36 warnings`；全量回归 `3004 passed, 11 skipped, 36 warnings`，旧路径守卫、导入图、`compileall` 和 `git diff --check` 通过 |
| 2026-08-31 | 阶段 5 MCP 工具进度边界切片 | 将 MCP 进度通知从 `mind_app/runtime/tools/notify.py` 重组到 `mind_app/runtime/mcp/tool_progress.py`，删除无调用者的 `runtime/tools/policy.py` 并清除旧导入 | MCP/工具/架构定向回归 `108 passed, 37 warnings`，旧路径守卫、导入图、`compileall` 和 `git diff --check` 通过 |
| 2026-08-31 | 阶段 5 Hook 执行端口切片 | 将 `HookCommandRunner`、`HookCommandResult` 和 `HookContextSpiller` 从 runtime 重组到 `agent/ports/hooks.py`，保留展示状态端口在 runtime 并删除重复 Protocol 定义 | Hook/工具/架构定向回归 `171 passed, 37 warnings`，旧 runtime 定义守卫、导入图、`compileall` 和 `git diff --check` 通过 |
| 2026-08-31 | 阶段 5 Hook 生命周期显式注入切片 | 将 Hook spill、会话清理和关闭从执行器类型推断改为显式端口参数，默认执行器仅在构造分支绑定并删除 runtime/registry 的 `isinstance` 能力判断 | Hook/工具/架构定向回归 `172 passed, 37 warnings`，显式资源回归、旧类型反射守卫、导入图、`compileall` 和 `git diff --check` 通过 |
| 2026-08-31 | 阶段 5 事件报告客户端边界切片 | 将 `EventReportRuntimeOwner`、`EventReportLifetime` 和 `TurnEventReportHandle` 从 `mind_app/runtime/turns/event_reporting.py` 重组到 `protocol/client/reports.py`；Session/Turn 报告生命周期与 `protocol.transport.events` 归属同一 Protocol Client，runtime turns 与测试消费者切换并删除旧模块 | 事件报告、Turn/Subagent/TUI 定向回归 `61 passed`；新增旧路径/旧导入守卫 `1 passed`，全量架构扫描 `52 passed`，导入图、`compileall`、文档契约和 `git diff --check` 通过 |
| 2026-08-31 | 阶段 5 Hook Registry 组合边界切片 | 将 Hook status/dispatcher/registry 抽象为 `agent.ports` 端口，`RuntimeServices` 注入 `create_hook_registry`，具体 `HookRegistry` 仅由 `mind.py` 组合；CLI/MCP/Controller 删除直接构造和 fallback | CLI/MCP/Hook 定向回归 `244 passed`，新增组合根守卫通过；全量回归 `3009 passed, 11 skipped, 38 warnings`，导入图、`compileall`、文档契约和 `git diff --check` 通过 |
| 2026-08-31 | 阶段 5 runtime support 职责拆分切片 | 将会话状态迁入 `mind_app/interaction/conversation.py`、TUI 剪贴板迁入 `mind_app/tui/adapters/clipboard.py`，并把 MCP 传输错误与 stream 异常摘要拆到各自边界；删除 `conversation.py`、`clipboard.py`、`session_policy.py` 旧 support 入口并加入架构守卫 | 会话/Turn/流协议定向回归 `107 passed, 1 warning`，完整架构测试 `54 passed, 38 warnings`，导入图、`compileall` 和 `git diff --check` 通过；下一切片继续审计剩余 runtime/interaction 历史职责 |
| 2026-08-31 | 阶段 5 上下文压缩结果与编排分离切片 | 将 `CompactResult` 提升到 `agent/application/compact_result.py`，将 runtime 编排改名为 `mind_app/runtime/compaction.py`，删除旧 `runtime/conversation.py` 并补充结果/编排分离守卫 | 压缩/TUI/架构定向回归通过，导入图、`compileall` 和 `git diff --check` 通过；下一切片继续审计 runtime/subagents 和剩余平铺入口 |
| 2026-08-31 | 阶段 5 执行上下文契约归位切片 | 将 `AgentContext`、`TurnContext`、`ToolInvocation` 提升到 `agent/application/execution.py`，切换 Turn/工具/Hook/MCP/子 Agent/TUI 全部消费者并删除旧 `runtime/execution.py` | 执行上下文及能力定向回归 `230 passed, 1 warning`，application 归属与旧导入守卫、导入图、`compileall` 和 `git diff --check` 通过；下一切片继续收敛子 Agent mailbox/graph 边界 |
| 2026-08-31 | 阶段 5 子 Agent mailbox 存储归位切片 | 将 mailbox 事件/快照/消费游标和有界日志从 `mind_app/runtime/subagents/mailbox.py` 重组到 `agent/stores/agent_mailbox.py`，切换控制/投递/图/运行时/客户端工具消费者并删除旧入口 | mailbox/子 Agent 定向回归 `53 passed, 1 warning`，stores 边界与旧导入守卫、导入图、`compileall` 和 `git diff --check` 通过；下一切片继续拆分 Agent graph 快照与 SQLite 持久化实现 |
| 2026-08-31 | 阶段 5 协议身份校验归位切片 | 将 `CID_RE`、`SID_RE` 和 `valid_session_ids` 从 `mind_app/history/ids.py` 重组到 `protocol/schema/identifiers.py`，历史/交互/Controller/TUI 复用统一 schema 并删除旧身份模块 | 身份、历史、交互、Controller、TUI 和 CLI 回归 `350 passed`；协议身份守卫、导入图、`compileall` 和 `git diff --check` 通过 |
| 2026-08-31 | 阶段 5 子 Agent 线程与继承上下文归位切片 | 将 `AgentThreadContext`、`AgentTurnContext`、`ForkContextSnapshot` 和 `normalize_fork_turns` 重组到 `agent/application`，runtime context 仅保留 transcript builder，并以 `PermissionGrantReader` 解耦执行上下文与 stores | 子 Agent/上下文/架构定向回归 `65 passed, 2 warnings`；交互/历史/Controller/TUI/CLI 扩展回归 `350 passed`；完整架构守卫 `60 passed, 44 warnings`；全量行为回归 `3015 passed, 11 skipped, 44 warnings`，导入图、`compileall` 和 `git diff --check` 通过 |
| 2026-08-31 | 阶段 5 Agent graph 快照与 SQLite 存储归位切片 | 将 Agent 状态/任务值对象重组到 `agent/domain/agents.py`，将图快照、SQLite 存储和单写者持久化重组到 `agent/stores/agent_graph.py`，删除旧 runtime graph 入口并清理 control 重复定义 | Agent graph/control/runtime 与 stores/domain/旧导入守卫回归 `51 passed, 1 warning`，导入图、`compileall` 和 `git diff --check` 通过 |
| 2026-08-31 | 阶段 5 子 Agent 消息投递端口拆分 | 将消息回执/投递端口重组到 `agent/ports`，将 steer 协议适配重组到 `agent/adapters`，runtime 仅保留活动轮次状态机 | 消息投递/运行时与架构守卫回归 `45 passed, 1 warning`，导入图、`compileall` 和 `git diff --check` 通过；下一切片拆分 Subagent 执行器和 runner 的 runtime 依赖 |
| 2026-08-31 | 阶段 5 Hook 输入上下文归位切片 | 将 Hook 输入上下文值对象和权限模式映射重组到 `agent/application/hook_context.py`，runtime scope 仅保留 dispatcher 与生命周期实现 | Hook/Turn/压缩/Subagent/TUI 定向回归 `247 passed`，application/runtime 守卫、导入图、`compileall` 和 `git diff --check` 通过 |
| 2026-08-31 | 阶段 5 Turn 执行契约归位切片 | 将 `TurnExecution` 提升到 `agent/application/turn_execution.py`，以 `HookExecutionScopePort` 解耦 application 与 runtime HookScope | Turn/RunResult/Stream/Subagent/TUI 与架构守卫回归 `108 passed`，导入图、`compileall` 和 `git diff --check` 通过 |
| 2026-08-31 | 阶段 5 MCP 会话端口归位切片 | 将 `McpSessionLike` 重组为 `agent/ports/mcp_session.py` 的 `McpSessionPort`，切换所有 MCP/Turn/工具/Subagent/TUI 消费者并删除旧 runtime contract | MCP/工具/Subagent/完整架构守卫回归 `64 passed, 48 warnings`，导入图、`compileall` 和 `git diff --check` 通过 |
| 2026-08-31 | 阶段 5 Turn 与 Subagent 执行端口归位切片 | 将 Turn/Subagent 调用协议重组到 `agent/ports/turns.py`、`agent/ports/subagents.py`，runtime executor/runner 只保留具体执行与状态协调 | Turn/Subagent/工具回归 `54 passed`，端口归属和旧 contract 守卫通过，导入图、`compileall` 和 `git diff --check` 通过 |
| 2026-08-31 | 阶段 5 Subagent Hook 生命周期归位切片 | 将 `SubagentHookEvents` 重组到 `agent/application/subagent_hooks.py`，扩展 Hook scope 端口并删除旧 runtime Hook 模块 | Hook/Subagent/Turn/架构守卫回归 `137 passed, 1 warning`，导入图、`compileall` 和 `git diff --check` 通过 |
| 2026-08-31 | 阶段 5 SubagentRunner Harness 化 | 将 SubagentRunner 重组到 `agent/harness/subagent_runner.py`，将 continuation 构造归入 application，改用 Turn runner/cleanup 注入并消除 harness 包初始化循环 | Subagent/Turn/SessionLoop 回归 `65 passed`，Harness 归属守卫、导入图、`compileall` 和 `git diff --check` 通过 |
| 2026-08-31 | 阶段 5 Subagent 流式执行适配器归位 | 将 StreamSubagentExecutor 重组为 `agent/adapters/subagent_execution.py`，新增 SubagentStreamPort 并删除旧 runtime executor | Subagent/Turn/工具/adapter 守卫回归 `61 passed`，导入图、`compileall` 和 `git diff --check` 通过 |
| 2026-08-31 | 阶段 5 AgentControl Harness 归位 | 将完整 AgentControl 状态机重组到 `agent/harness/agent_control.py`，切换 SubagentRuntime/client tools/TUI 并删除旧 runtime control | AgentControl/graph/Subagent/TUI 回归 `78 passed`，graph/control 归属守卫、导入图、`compileall` 和 `git diff --check` 通过 |
| 2026-08-31 | 阶段 5 Agent 活动投递状态归位 | 将 AgentActiveTurn/AgentMessageDispatch 重组到 `agent/harness/agent_delivery.py`，切换 SubagentRuntime/TUI/测试并删除旧 runtime delivery | Agent delivery/Subagent/TUI/架构守卫回归通过，导入图、`compileall` 和 `git diff --check` 通过 |
| 2026-08-31 | 阶段 5 活动投递注册表归位 | 将 SubagentRuntime 活动 Turn 字典/锁抽取为 Harness `AgentDeliveryRegistry`，runtime 仅使用注册表端口 | Agent delivery/Subagent/graph/TUI 回归 `83 passed, 1 warning`，delivery 守卫、导入图、`compileall` 和 `git diff --check` 通过 |
| 2026-08-31 | 阶段 5 Agent 只读视图归位 | 将 AgentSnapshot/AgentWaitResult/AgentMailboxWaitResult 重组到 `agent/application/agent_views.py`，Harness control 只保留可变状态机 | AgentControl/graph/Subagent/TUI/视图守卫回归通过，导入图、`compileall` 和 `git diff --check` 通过 |
| 2026-08-31 | 阶段 5 Agent 根会话注册表归位 | 将 SubagentRuntime 根会话 control 字典、生命周期锁和 shutdown 状态重组到 `agent/harness/agent_registry.py`，以显式 factory 负责恢复/创建 | 注册表/AgentControl/Subagent/runtime/架构守卫回归通过，导入图、`compileall` 和 `git diff --check` 通过 |
| 2026-08-31 | 阶段 5 父会话继承上下文算法归位 | 将 ForkContextEntry、继承范围选择、渲染和字符预算算法重组到 `agent/application/fork_context.py`，runtime context 收敛为 history adapter | Subagent/context/application/架构守卫回归通过，导入图、`compileall` 和 `git diff --check` 通过 |
