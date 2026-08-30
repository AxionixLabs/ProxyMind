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
  Sandbox sidecar 后端执行，`WorkspaceCodingRuntimeOwner` 负责共享能力的关闭，
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
| `mind_core/application_paths.py` -> `infrastructure/config/paths.py` | 应用入口和本地资源路径解析归入配置基础设施 | 所有路径消费者切换、路径回归通过、旧源模块删除 | 5 |
| `mind_core/agent_config.py`、`feature_config.py` -> `agent/application/settings.py` | Agent 运行设置与能力开关归入 application | application 公开入口、配置/设置/Subagent 回归通过，旧源模块删除 | 5 |
| `mind_core/provider_config.py` -> `infrastructure/config/providers.py` | Provider 默认值、路由和 Profile 标识约束归入配置基础设施 | 配置服务/偏好/Provider 选择回归通过，旧源模块删除 | 5 |
| `mind_core/skills/` -> `infrastructure/skills/` | 技能发现、解析、过滤和 payload 归入本地资源基础设施 | Skills/TUI/流式/Subagent 回归通过，旧目录删除 | 5 |
| `mind_core/project_trust.py` -> `infrastructure/config/trust.py` | 项目根、Git 和信任登记解析归入配置基础设施 | 配置层/CLI 信任回归通过，旧源模块删除 | 5 |

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
