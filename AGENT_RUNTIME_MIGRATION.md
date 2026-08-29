# Agent Runtime 迁移计划

状态：阶段 0、阶段 1、阶段 2、阶段 3 已完成；阶段 4 未开始（2026-08-29）

这份计划配合 [Agent Runtime 架构基线](AGENT_RUNTIME_ARCHITECTURE.md) 使用。
它把从历史包到 `agent` bounded context 的改造拆成可回滚阶段；每一阶段都必须
有代码、测试和导入边界证据，不能以“目录已经移动”作为完成标准。

## 范围与状态权威

- 本计划只管理 ProxyMind 客户端内部 Agent Runtime 迁移。
- 阶段号只在本文档内有效，不与任何外部服务计划共用进度。
- 架构拆分、协议兼容或生命周期所有权收敛只是准备性工作；未满足
  本阶段退出条件时，不得标记为该阶段完成，也不得计入后续阶段。
- 每次状态变更必须在本文档记录日期、实现证据、测试结果和未决风险。

## 迁移原则

- 先建立运行契约，再迁移实现；先迁移一个完整用例，再扩大范围。
- 每个阶段保持 `mind.py`、CLI、MCP 和订阅入口可验证。
- `backend/` 是独立打包，整个计划不修改它、不改变它的依赖。
- 只保留必要的过渡入口，并在本文件登记删除条件和截止阶段。
- 任何状态所有权不清的代码先停止扩散，不通过共享工具函数掩盖边界问题。

## 阶段总览

| 阶段 | 状态 | 目标 | 可交付物 | 完成信号 |
| --- | --- | --- | --- | --- |
| 0. 契约冻结 | 已完成 | 固定外部行为和依赖基线 | 导入图、协议清单、风险清单 | 全部阶段 0 退出条件通过 |
| 1. Session 骨架 | 已完成 | 引入 Command/Event 和单写者 | `agent.protocol`、SessionLoop、事件游标 | 一个主动 turn 走完整闭环 |
| 2. 持久化收束 | 已完成 | 迁移事件、快照、效果和 outbox | stores 实现及恢复测试 | 强制退出后可恢复或对账 |
| 3. 能力解耦 | 已完成 | 模型、MCP、Helix、进程通过端口接入 | capabilities 和 adapters | runtime 不导入具体传输实现 |
| 4. 多入口迁移 | 未开始 | CLI/TUI/MCP/订阅统一提交命令 | adapters 全量切换 | 四类入口共享同一 Run 语义 |
| 5. 历史包退役 | 未开始 | 删除历史职责和过渡入口 | 旧包删除清单 | 生产导入图只剩 `agent` |

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
- [x] 记录 `backend/` 的目录、导入和构建基线，证明 Agent Runtime 迁移没有改变其边界。

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
- [x] `ModelStreamEventHandler` 独占 `SegmentTracker`，统一消费 retry、正文增量/完成、
  展示代次替换和 assistant 输出边界，并负责正文 transcript 投影。
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
  回放下界，`internal` 立即停止交付；`SessionEventCursorStore` 按 `cid + sid`
  跨 Turn 持有线上 `event_seq`，不与本地 Run sequence 混用。
- [x] 新 runtime 核心没有导入 `mind_app`、`mind_core`、`mind_nova`、`engine`
  或 `server`；生成导入图只新增 `mind_app -> agent`。
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

当前切片：先将主动 Turn 的模型事件流从
`mind_nova.requests.chat.stream_chat` 迁到具名 `ModelCapability`。请求数据必须
冻结为协议对象，重连状态和审批恢复保持独立回调端口；`mind.py` 组合根创建
进程级 `RuntimeServices`，CLI、TUI 和 MCP 入口只接收依赖。MCP、Helix、process
和 filesystem 端口均已具备真实或内存实现，并通过 capability 层的稳定错误语义
对外收口。

### 当前证据

- [x] `ModelStreamRequest` 位于 `agent.protocol`，只接收并冻结 JSON 兼容的模型
  配置、消息、工具、附件和请求选项；重连状态和审批恢复 callback 不进入协议对象。
- [x] `ModelCapability` 明确事件迭代、服务端水位和关闭生命周期；
  `RemoteModelCapability` 是唯一把冻结请求翻译到现有远端传输的实现。
- [x] `stream_turn` 在进入事件处理前校验 `ModelEventStream` 的异步迭代、关闭、
  终止原因和服务端游标端口，并在 `finally` 中等待 `aclose()` 完成，关闭失败只
  进入结构化观测，不跳过本轮终态收尾。
- [x] `mind.py` 是唯一具体组合根，创建一次 `RuntimeServices` 并显式传给 CLI、
  TUI 与 stdio MCP；主动 Turn、停止 Hook 续跑和子 Agent 从同一容器取得模型能力。
- [x] `agent.application` 不再导入 `agent.composition`、stores 或 capabilities；
  CLI/TUI 的 Turn application 与工具效果账本也通过注入工厂创建，不存在导入时
  装载具体 adapter 或工具执行器自行查找全局工厂的路径。
- [x] `mind_app` 已无 `mind_nova.requests.chat` 导入；TUI 使用的结束原因类型也已
  迁入 `agent.protocol`。AST 边界测试禁止旧运行时重新直连模型传输。
- [x] `agent.capabilities` 只允许依赖 `agent` 内部边界与迁移期协议传输包，禁止
  导入 `mind_app`、`mind_core`、`engine`、`server` 或 `backend`。
- [x] `RemoteModelEventStream` 覆盖远端流异步迭代和幂等关闭，将 HTTP、超时、
  协议及未知异常转换为稳定的 `ModelCapabilityError`；`RunResult` 保留
  `error_code/error_details`，`RunActor` 直接收到能力异常时也写入具名 `run_failed`
  事件，避免错误只停留在 UI 文本。
- [x] `TurnRetryingEvent` 和 `TurnFailedEvent` 已保留服务端 `error_type`、来源、
  HTTP 状态和可重试性；新格式出现时校验 `error_type == error.type`，并将失败分类
  投影到 `RunResult.error_code/error_details`。缺少附加字段的历史事件暂时兼容，
  待服务端全量启用后再收紧缺省路径。
- [x] `agent.protocol.ModelEvent` 固化模型事件跨 capability 边界的最小坐标契约，
  `ModelEventStream` 不再以 `Any` 暴露事件；具体传输事件仍由 `mind_nova` adapter
  解析和返回，runtime 只依赖协议形状。
- [x] `RemoteModelEventStream` 在 adapter 边界执行 `ModelEvent` 运行时门禁，
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

- `mind_nova` 的具体模型事件类继续作为 wire decoder 的私有实现；跨边界唯一
  语义是 `agent.protocol.ModelEvent`。阶段 4/5 迁移完旧 stream handler 后才
  删除该兼容导入。
- `mind_app/runtime/mcp/service_runtime.py` 使用的 `engine.ServerManage`、旧
  `ProcessSessionManager` 和 `SandboxClient` 仍是 legacy adapters；新端口已经
  可独立测试，阶段 4 将把 CLI/TUI/MCP 入口改为注入 capability，再按删除条件
  移除旧路径。模型轮次不会隐式启动 Helix。

### 工作项

1. [x] 定义 model、MCP、Helix、process、filesystem 的最小 capability port。
2. [x] 将模型传输请求实现拆到 capability adapter；类型和跨边界事件形状留在
   protocol。
3. [x] 将平台差异收口在 capability 实现边界：标准库本地实现不依赖 `engine`，
   受限进程通过显式 launcher 注入；旧 `engine` adapter 迁移留给阶段 4。
4. [x] 为每个 capability 提供 fake/in-memory 实现，用于 domain/runtime 测试。

### 出口条件

- `protocol`、`domain`、`runtime` 可脱离网络和 TUI 执行测试；
- capability 失败能转换为具名、可持久化的错误事件；
- Helix 的启动、连接和回收不被模型轮次代码隐式触发。

阶段 3 复核结论：上述出口均已满足；旧入口的 adapter 迁移不再阻塞能力层，
转入阶段 4 的多入口接管。

## 阶段 4：多入口迁移

状态：未开始

### 工作项

1. CLI 将参数、stdin、resume 和退出处理映射到 application command。
2. TUI 只订阅事件投影，保留现有窄 capability port 约束。
3. MCP server 将每个请求交给 Command Gateway，不直接构造控制器或模型。
4. Subscription handler 只处理 open/ws/resume、去重、mailbox 和确认；任务执行
   通过同一个 application command。
5. server 若继续独立运行，只依赖协议与 application 的公开入口。

### 出口条件

- 主动执行和订阅执行共享 Run、Tool、Approval、Effect 语义；
- WebSocket 回调中没有模型轮次或工具调用；
- TUI、CLI、MCP 和 subscription 的结果都可从 Event 游标重放。

## 阶段 5：历史包退役

状态：未开始

只有全部条件满足后才能删除 `mind_app`、`mind_core`、`mind_nova` 中已经迁移的
职责：

- 非测试生产代码不再导入待删除模块；
- `mind.py` 和所有外部入口已经切换到 `agent.composition`；
- 旧配置、历史、报告和订阅数据完成版本迁移；
- 主流程、失败路径、恢复、协议兼容和构建测试通过；
- 每个过渡入口都有删除记录，没有长期转发 facade。

历史包的物理目录可以分批删除，但每一批都必须保持可构建、可启动、可恢复。
不得用一次性 `Move-Item` 或批量改名替代上述出口条件。

## 过渡入口登记

| 入口 | 保留原因 | 删除条件 | 所属阶段 |
| --- | --- | --- | --- |
| `mind.py -> agent.composition` | 稳定启动方式和当前唯一具体组合根 | 保留稳定入口；阶段 4 只替换下游 adapter，不把具体装配退回旧包 | 3/4 |
| 旧 CLI 导入路径 | 外部脚本兼容 | 所有内部调用改走 application，完成兼容窗口 | 4/5 |
| 旧协议类型别名 | 数据和客户端迁移 | 新旧 schema 均有版本识别且无旧生产消费者 | 4/5 |
| 旧历史读取器 | 读取存量会话 | 历史数据迁移并完成回读校验 | 2/5 |
| `mind_app/cli/dispatch.py -> agent.application/protocol` | 首个主动 `exec` 入站切片 | CLI adapter 迁入 `agent.adapters.cli` 且入口只依赖公开组合根 | 4 |
| `agent/capabilities/model.py -> mind_nova.requests.chat` | 复用已稳定的正式协议事件流、attach 和审批恢复实现 | 模型事件类型迁入 `agent.protocol`，传输细节迁入 capability adapter 且 `mind_app` 无旧类型消费者 | 最晚 5 |

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
