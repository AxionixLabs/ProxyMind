# Agent Harness 架构基线

状态：已采纳（Architecture Decision Record）；阶段 4 已完成，阶段 5 进行中

这份文档是 ProxyMind 下一代 Agent Harness 的目标架构。它解决的是
`mind_app`、`mind_core`、`mind_nova` 和 `engine` 四个历史包职责交叉、状态所有权不清和
副作用难以恢复的问题；它不改变 Mind/Helix 的产品边界，也不改动独立打包的
`backend/`。

## 文档权威与当前边界

- 本文档是 ProxyMind Agent Harness 的目标 ADR，不表示目标目录已经存在。
- `PROTOCOL.md` 是 Mind Runtime 线上字段、状态、事件、恢复和错误语义的唯一
  规范源；本地 Harness 只实现客户端所有权，不复制服务端状态机。
- `AGENT_RUNTIME_MIGRATION.md` 是阶段状态的唯一权威来源；准备性拆分
  不能自动计入后续阶段。
- `AGENTS.md` 定义当前可执行的生产依赖规则；阶段 5 允许按职责重组历史包，
  但每批重组都必须同步导入图、测试和删除条件。
- 线上协议客户端与本地 Agent Harness 是两个不同的边界：`PROTOCOL.md` 定义
  TUI、桌面端和 Web 可以共用的 `mind.chat` wire contract；`agent.protocol`
  只定义进程内 Harness 的 Command/Event，不作为跨进程或浏览器 SDK。
- 正式 `mind.chat` Python wire SDK 已进入顶层 `protocol`，不再按历史包名组织。
  `agent.adapters.protocol_client` 只把冻结的 Harness 请求映射到该 SDK，并拥有本地
  Session 游标、Canonical Item 投影和能力错误归一化，不复制线上 schema。
- Agent Harness 是产品架构主线，`agent.harness` 是本地编排内核的正式包名；
  `agent.runtime` 已在阶段 4B 的首个切片中删除，不得重新创建兼容 facade。
  `RuntimeServices` 等既有技术名称暂不随包名机械重命名，必须在对应职责完成
  迁移时单独评审。
- 阶段 0、阶段 1、阶段 2 已于 2026-08-28 通过；阶段 3 已于 2026-08-29
  通过，阶段 4 已于 2026-08-30 通过。主动 `exec` 和 TUI 普通 prompt 已使用顶层 `agent` 包、持久化
  `SessionLoop`、`RunActor` 和注入式模型能力；`mind.py` 已成为唯一具体组合根，
  CLI、TUI、stdio MCP 与 Subscription 只接收 application 层公开的 `RuntimeServices`。

### 本地运行时与线上协议身份

本 ADR 中的 `Run` 是 ProxyMind 本地可恢复执行单元，不替换已有线上
`cid` / `sid` / `turn_id` / `attempt` / `item_id` / `event_seq` 身份。迁移不得：

- 为本地 `Run` 另建一套对外会话或 Item 协议；
- 将本地事件序号冒充为服务端 `event_seq`；
- 重命名或重新解释已稳定的线上协议字段。

本地 Command/Event 必须显式携带所属的线上坐标，并在 adapter 边界完成
本地 `run_id` 与线上 `turn_id` / `attempt` 的映射。

客户端执行环境也是 Turn 输入事实，而不是进程级可变配置。`PROTOCOL.md` 和当前
顶层 `protocol.schema.environment` 拥有线上 `exec_env` schema 与规范化；Harness
只保存已经校验的不可变快照，不复制字段模型。每个新 Turn 采集新 `snapshot_id`，
同一 Turn 的 continuation、重试和安全 redispatch 必须复用原快照，且快照必须在
持久 queued 命令的幂等指纹内。当前实现由注入的 environment capability 捕获，
并在四类主动入口创建 `SubmitTurnCommand` 前完成冻结；执行和 queued redispatch
只读取命令快照，不再按当前进程环境重新采集。

线上 `event_seq` 的客户端水位按 `cid + sid` 归属 Session，而不是归属单次
Turn 或连接。事件解析、去重和 attach 传输原语统一归属 `protocol`；跨 Turn
水位由进程级 `MindChatProtocolClient` 的 `ProtocolEventCursorStore` 持有，并且
只在逻辑结算后提交。控制器和具体前端不再拥有该状态。本地 `RunEvent.sequence`
仍只在单个本地 Run 内递增，两者不得互相赋值或比较。

### 协议客户端与可替换前端

目标支持同一套线上协议驱动 TUI、桌面端和 Web，但不要求这些前端共享 Python
UI 实现。前端必须通过一个与渲染工具包无关的 Protocol Client 观察和控制服务端：

```text
TUI / Desktop / Web
        |
        v
Protocol Client
  - mind-chat / mind-attach / mind-replay
  - turn control / tool result / approval
  - event cursor / Canonical Item reducer
        |
        v
Mind Runtime Service
```

Protocol Client 的职责是构造冻结命令、维护 `cid/sid/turn_id` 和 `event_seq`、
执行回放/断线接管，并把正式事件投影为可渲染的 Canonical Items。它不得依赖
`Mind`、`prompt_toolkit`、`OutputSession` 或具体平台 UI。TUI、桌面端和 Web 只
替换输入、渲染和本地工具能力适配器；Turn、Attempt、Tool、Approval 和 Effect
的权威生命周期仍由服务端拥有。

当前 Python 实现中的 `protocol.client` / `protocol.schema.stream_events` 是该 Protocol Client
边界的独立传输实现。`agent.adapters.item_reducer` 已在传输交付前把 Item 事件归约
为不可变 `CanonicalItem`，并同时保留 active 视图和被 retry/presentation 替换的
审计视图。重连审批快照先在同一 reducer 中按服务端 `last_event_seq` 归约，再通知
前端审批处理器；快照水位只裁决旧审批事件，不能推进客户端已确认事件游标。
`ModelEventStream.assistant_text` 由 active Canonical Items 派生，是 Turn 结果、Stop
Hook 和最后回复记忆的唯一正文来源；`current_item` 为当前事件提供已裁决的 Item
revision，`sources` 从 active text/builtin Items 聚合。展示 adapter 不得再从收到的
delta 自建最终正文、attempt 或来源归属。
`ProtocolCommandClient` 与模型流共用同一 Protocol Client 身份，但职责单独收敛为
`turn/interrupt`、`tool-result`、`tool-result/status`、`tool-approval` 和
`effect/reconcile` 命令；它返回已校验的控制回执或状态快照，TUI 不得直接调用
`protocol.client` 的 wire 函数。这样桌面端和 Web 可以复用同一命令语义而不引入
`Mind` 或本地 UI 生命周期。
迁移期间可以复用既有传输，但不能把 `agent.application` 的本地
`SubmitTurnCommand` 或 `agent.protocol.RunEvent` 暴露为桌面/Web 公共协议。

## 决策结论

采用 **Codex-inspired Agent Harness + ProxyMind Durable Effects**：

- 借鉴 Codex-main 的类型化 `Op/Event` 协议、Submission Queue / Event Queue、
  Session 单一状态所有者、独立存储包和小型公开 API。
- 保留 ProxyMind 的端侧确定性执行、Helix MCP、外部 MCP、审批、订阅和
  `LocalEffectJournal`，并将副作用统一纳入效果日志与事务性 Outbox。
- 用一个 `agent` bounded context 取代 `mind_app / mind_core / mind_nova / engine` 的
  本地运行语义分层。正式 `mind.chat` wire SDK 独立归属 `protocol`，不把它
  重新当作 Harness 的共享业务层；迁移完成后四个历史包均从生产源码和发行包中移除。
- `mind.py` 仍是稳定入口；`backend/` 仍是独立打包边界。二者不是 Agent Harness
  的内部模块。

Agent Harness 的范围是：命令入口、Session/Run 编排、模型/工具/审批/效果端口、
持久化恢复、幂等与观测，以及供 TUI、桌面端和 Web 使用的 Protocol Client 边界。
根目录 `server/` 不属于 Harness：它是客户端内置的可选 `ConfigServiceRuntime`，
只提供配置 UI 和健康检查，不拥有 Turn、Run、Tool、Effect 或线上事件的权威状态。
`agent.harness` 承载 Harness 的内部运行机制，不应再以 Runtime 作为产品主线名称。

### Harness 质量目标

- 可维护：状态规则集中在 `domain`，生命周期集中在 `harness`，跨边界协作只通过
  具名 `ports`，组合和配置只在 `composition.py` 发生。
- 可扩展：模型、工具、审批、存储、传输和前端都以能力端口或适配器扩展，新增
  能力不修改 Session 单写者和既有终态规则。
- 可恢复：每个可重试命令、外部效果和生命周期阶段都有稳定身份、快照/事件证据
  和明确的未知结果收束路径。
- 可观测：Command、Event、Attempt、Effect 和错误分类都能产生结构化投影，前端
  只消费投影，不从异常文本重新推断业务状态。

这不是把 Codex-main 的 Rust crate 数量或 `codex-core` 名称原样复制到 Python。
目标是复制已经验证的运行时约束，而不是复制实现细节。

## 为什么选择这个形态

Codex-main 对当前项目最有价值的不是“有一个 core”，而是以下约束：

1. 调用方只能提交带类型的操作，不能直接修改 Session 内部状态。
2. Session 负责一个运行上下文的生命周期，并通过事件向外广播结果。
3. 协议、持久化、模型、MCP、执行器和前端属于不同的可替换边界。
4. 公共 API 由少量显式入口组成，内部模块默认私有。
5. 线程/会话恢复、取消和等待是生命周期的一部分，而不是 UI 补丁。

ProxyMind 需要在此基础上额外保证：

- 外部工具调用具有可重试、可对账的 effect identity；
- 审批、等待、断线、重连和远端订阅任务可以在进程重启后收敛；
- Helix 是执行面，不被模型编排逻辑反向吸收；
- 流式 token 可以丢失，最终事件、计划和证据不能无故丢失。

## 最终终态与退役原则

最终目标不是批量改名，而是让每个职责只有一个真实所有者，并在删除旧包后仍能独立
验证启动、恢复和协议兼容。`mind.py` 保留为稳定可执行入口，但不再意味着保留
`mind_app` 包。`server/` 仍是客户端内置的可选配置服务，不属于 Harness 状态边界。

```text
mind.py                         # 稳定启动入口，只负责调用组合根
agent/                          # 本地 Agent Harness bounded context
protocol/                       # 独立 mind.chat wire SDK，供所有前端复用
frontends/                      # CLI、TUI、MCP、Subscription 及未来桌面/Web adapter
infrastructure/                 # 配置、平台进程、Helix 和持久化的具体外部实现
metadata/                       # 版本、编码和产品展示元数据，不承载运行时状态
server/                         # 可选 ConfigServiceRuntime，不拥有 Harness 状态
```

四个历史包的最终去向固定如下：

| 历史包 | 最终职责去向 | 退役约束 |
| --- | --- | --- |
| `mind_app` | `agent.application`/`agent.harness` 的运行用例；`frontends/` 的入口和 UI；`agent.capabilities` 的本地能力 | 每个入口先完成一个可启动、可恢复的完整用例，再删除对应旧模块；最后移除包目录和启动依赖 |
| `mind_core` | `agent.domain.policies` 的领域规则、`agent.application` 的用例配置、`infrastructure/config` 的文件与环境适配 | 配置读取、策略判断、skills/hooks 分开迁移；禁止以新的 `core` 或 `shared` 包承接杂项 |
| `mind_nova` | `protocol/schema` 的请求/响应 schema、`protocol/transport` 的传输/认证/事件投递、`protocol/client` 的恢复和命令 API；`metadata/` 的版本与展示常量 | 先按职责拆分 wire SDK 与产品元数据，再迁移跨前端 fixture，最后清除历史包记录和兼容别名 |
| `engine` | `agent.capabilities` 的进程/Helix 端口实现；可复用的纯平台代码进入 `infrastructure/platform` | 先切断反向业务依赖和循环，再逐项删除 `engine` 模块；不得保留只转发一次调用的 facade |

目标目录中的新模块只在迁移计划启用对应切片且能承载完整用例时创建；不为“未来可能
使用”预建空目录。

## 目标目录

`agent` 是本地 Harness bounded context；顶层 `protocol` 是独立线上 SDK，
`frontends` 和 `infrastructure` 是其外部适配边界。下面的文件名是职责边界，
不要求一次性全部创建；迁移时应按阶段启用，避免建立未接入主链路的空壳模块。

```text
agent/
├── protocol/
│   ├── commands.py          # Command、Op、幂等键、因果链
│   ├── events.py            # Event、状态事件、错误事件
│   ├── turn_input.py        # TurnInput、恢复和追加输入
│   └── errors.py            # 可序列化协议错误
├── domain/
│   ├── runs.py              # Run 身份、生命周期和状态转移
│   ├── turns.py             # Turn、消息、工具轮次
│   ├── plans.py             # 计划、步骤和证据引用
│   ├── tools.py             # 工具调用意图、结果和失败分类
│   ├── approvals.py         # 审批请求、决定和策略
│   ├── agents.py            # 子 Agent 身份、关系和状态
│   └── policies.py          # 权限、预算、取消和重试规则
├── harness/
│   ├── session_loop.py      # 一个 Session 的单写者事件循环
│   ├── run_actor.py         # 一个 Run 的串行状态执行器
│   ├── scheduler.py         # Run 调度、并发上限和公平性
│   ├── supervisor.py        # 子任务、断线和关闭收束
│   └── recovery.py          # 快照恢复、未完成命令和效果对账
├── application/
│   ├── commands.py          # submit、resume、approve、cancel、retry
│   ├── queries.py           # 历史、状态、计划和证据读取
│   ├── projections.py       # Event Queue 到入口稳定结果的投影
│   ├── settings.py          # Harness 并发和可选能力的启动时设置
│   └── services.py          # 用例编排，不持有长期运行状态
├── ports/
│   ├── capabilities.py      # 模型、MCP、Helix、进程和文件端口
│   ├── persistence.py       # 事件、快照、历史和 outbox 端口
│   └── observability.py     # 日志、指标和 tracing 端口
├── stores/
│   ├── session_store.py     # Session/Run 元数据和最终记录
│   ├── event_store.py       # 追加事件、读取游标和快照
│   ├── agent_graph.py       # 子 Agent 图和检查点
│   ├── effect_journal.py   # 外部副作用状态机
│   └── outbox.py            # 事务性待发送消息
├── capabilities/
│   ├── model.py             # 模型流式响应能力
│   ├── mcp.py               # MCP 工具发现与调用能力
│   ├── helix.py             # Helix 执行面连接
│   ├── process.py           # 进程、端口和沙箱能力
│   ├── environment.py       # 客户端执行环境快照采集与 provider 聚合
│   └── filesystem.py        # 受控文件能力
├── adapters/
│   ├── protocol_client.py # mind.chat 命令、传输恢复和 Canonical Item 投影
│   ├── cli.py               # CLI 输入/退出码到 Command
│   ├── tui.py               # TUI 输入、渲染和 Event 投影
│   ├── mcp_server.py        # stdio MCP 入站协议
│   ├── subscription.py      # 远端订阅、resume 和 mailbox
│   └── observability.py     # 日志、指标和 tracing 出站适配
└── composition.py           # 唯一组合根
```

顶层 `protocol/` 只承载正式 `mind.chat` wire contract，不依赖 `agent` 或 UI；
目录按 schema、transport、client 分层，禁止把网络细节和数据契约混在同一模块：

```text
protocol/
├── schema/                   # 请求/响应、事件、Item 和身份值对象
│   ├── environment.py
│   ├── attachments.py
│   ├── identifiers.py
│   ├── item_projection.py
│   ├── stream_events.py
│   ├── tool_approval.py
│   └── turn_inputs.py
├── transport/                # HTTP、SSE、认证、端点和事件上报
│   ├── auth.py
│   ├── config.py
│   ├── endpoints.py
│   ├── events.py
│   ├── reports.py
│   ├── reliable.py
│   └── streaming.py
└── client/                   # 面向前端的命令和资源操作
    ├── chat.py
    ├── compact.py
    ├── effects.py
    ├── fork.py
    ├── manifest.py
    ├── payload.py
    ├── reports.py
    ├── tools.py
    ├── turn_control.py
    └── upload.py
```

`observability/` 是独立的横切基础设施，拥有结构化观测、日志 sink 和第三方日志适配；
业务包不得直接导入标准库 `logging` 或 `loguru`、创建 `_LOGGER` 或调用
`logging.getLogger`。`backend/` 是独立打包边界，继续维护自己的日志实现。

### 依赖方向

```text
adapters ───────────────> application ──> harness ──> domain
                              │              │          │
                              └──────────────┴──────> protocol

capabilities ──> ports <── harness / application
stores ────────> ports <── harness / application
composition.py ──────────> all concrete implementations

frontends ──────────────> application / protocol
agent.adapters.protocol_client ─────────────> protocol
infrastructure ────────> ports
```

具体规则：

- `agent.protocol` 只使用标准库类型和可序列化值，不导入 `engine`、配置、网络客户端
  或任意适配器；顶层 `protocol` 可以实现线上传输，只允许依赖协议所需的端点/认证
  配置和 `metadata`，不得导入本地业务配置、`agent` 或 UI。
- `domain` 是纯状态和规则，不执行 IO，不启动任务，不读取环境变量。
- `harness` 只负责并发、生命周期和状态提交；模型、MCP、Helix 等均通过
  `ports` 的具名端口进入，`capabilities` 只提供这些端口的实现。
- `application` 组合用例，但不直接持有 Session 的可变状态，也不依赖具体能力实现。
- `ports` 只定义跨边界的最小 Protocol/ABC、生命周期和实现约束，不包含业务规则。
- `stores` 负责持久化事实与游标；具体 SQLite、文件或远端实现放在其内部适配器，
  通过 `ports.persistence` 接入，不能把数据库细节泄漏到 domain。
- `adapters` 只翻译输入和输出，不判断模型策略，不直接调用工具。
- `adapters.protocol_client` 是线上 `mind.chat` 的客户端边界，只依赖正式协议、
  传输和本地客户端能力端口；它不依赖 `Mind`、TUI 渲染或 `agent.application`。
- `adapters.tui`、桌面端和 Web 适配器只消费 Protocol Client 的事件投影，并把用户
  操作翻译为协议命令；它们不得各自复制 Turn、Tool、Approval 或 Effect 状态机。
- `composition.py` 是唯一允许组装配置、端口、存储和运行时的模块。
- `observability` 是全局结构化观测入口；第三方 SDK 的日志兼容只能放在其显式
  adapter 中，业务模块不得自行创建日志器或绕过字段规范化。报告文件 sink 和
  进程级 sink 生命周期也必须通过该入口管理。
- 迁移期间 `engine` 只能作为低层平台能力被 capability adapter 使用，不能成为新的
  业务层；阶段 5 完成后该包必须删除。
- 根目录 `server/` 只是客户端内置配置服务，只能通过显式配置服务入口被组合，
  不得拥有 Harness 状态或实现 `mind.chat` 线上服务端语义。
- `backend/` 只能依赖自身、标准库和第三方库；Agent Harness 不得导入它。

## 运行时契约

### Command / Event

所有外部输入先变成命令，所有状态变化通过事件对外可见。字段名可以在实现时
采用 Python dataclass 或 Pydantic，但语义必须保持稳定。

```text
Command
  command_id       # 全局唯一，重试时保持不变
  session_id
  run_id
  kind             # submit_turn / append_input / approve / cancel / resume ...
  payload          # 具名、可序列化字段
  idempotency_key
  causation_id
  trace_context

Event
  event_id
  sequence         # 同一个 run 内单调递增
  session_id
  run_id
  kind             # run_started / turn_started / tool_requested / ...
  payload
  causation_id
  occurred_at
```

约束：

- 命令只描述意图，不携带可执行的 Python callable。
- 事件是不可变事实，不复用为命令，也不让 UI 修改事件内容。
- 重复命令按 `command_id` 或 `idempotency_key` 去重；同一个命令只能产生一条
  已提交结果。
- 事件 payload 不放不可序列化的异常对象、协程、文件句柄或 UI 对象。
- 取消、暂停、审批、等待和恢复都必须有明确事件，而不是只设置内存布尔值。

### Session 和 Run

`Session` 是双向通信边界，`Run` 是可恢复的业务执行单元：

```text
Inbound Adapter
   -> Submission Queue
   -> SessionLoop
      -> RunActor (one writer per run)
         -> domain transition
         -> Event Store + Snapshot
         -> Outbox / Effect Journal
   -> Event Queue
   -> TUI / CLI / MCP / Subscription projection
```

上图仅描述同一进程内的本地 Agent Harness 路径。跨进程的桌面端、Web 或远程 TUI
不进入本地 `SessionLoop`，而是通过上一节的 Protocol Client 连接服务端，并在
客户端侧完成事件游标和 Canonical Item 投影。

一个 `Session` 同时最多执行一个需要占用当前会话输入的活动任务；其他 Run 可以
处于排队、等待审批、等待外部效果或暂停状态。调度器可以有并发上限，但同一个
Run 内的状态转移和事件序列不能并行写入。

建议的 Run 状态：

```text
created -> queued -> running
running -> waiting_approval -> running
running -> waiting_effect -> running
running -> paused -> queued
running -> completed
running -> failed
running -> cancelled
```

终态不可重新写入；重试必须创建新的命令和新的尝试编号，并通过
`causation_id` 关联原失败。

### 持久化与副作用

采用“事件日志 + 快照 + Outbox + Effect Journal”的混合模型：

1. 在一个本地事务中写入事件、更新快照和写入待发送 outbox 记录。
2. outbox worker 读取记录，使用 `effect_id` 和请求指纹调用外部能力。
3. 成功、明确失败或需要人工对账都写回 Effect Journal。
4. 进程重启时先恢复快照，再重放快照后的事件，最后对账未收束效果。

外部系统通常无法提供真正的 exactly-once，因此目标是：

- 本地状态提交 exactly-once；
- 外部 dispatch 至少一次；
- 通过 `effect_id + fingerprint` 实现幂等或可检测重复；
- 对未知结果使用 `unknown / reconciliation_required`，禁止盲目再次执行高风险动作。

流式 token 属于易失投影，可以不落事件日志；最终 assistant message、工具请求、
工具结果、审批决定、计划步骤和证据引用必须持久化。

阶段 2 的首个生产切片已经落实以下边界：

- `agent/stores/run_store.py` 编排 SQLite 事务，私有 schema 与记录投影分别位于
  `_run_schema.py`、`_run_records.py`；公开存储实现不泄漏数据库行或 SQL。
- `runtime.db` 的 schema/snapshot 版本为 1；事件、快照、outbox 和最终事实由
  同一事务提交。`effects.db`、`agents.db`、`history.db` 保持独立文件和所有权。
- queued 快照可以使用持久化原命令安全再派发；running、waiting_effect 和执行
  超时必须对账，waiting_approval 保持等待，paused 只能显式恢复。
- `agent/stores/effect_journal.py` 是本地外部效果状态机的正式实现；旧
  `mind_app/runtime/durable_effects.py` 已删除，没有兼容 facade。
- CLI 和 TUI 的生产组合使用哈希派生的本地 Session 身份接入 `runtime.db`；
  测试替身可以显式使用内存运行时。本切片不改变线上 Session/Turn 身份。

## 能力与适配器

### 入站适配器

- CLI 把参数和 stdin 转为 `submit_turn` 等命令，并把 Event 映射为退出码和文本。
- TUI、桌面端和 Web 都是 Protocol Client 的前端适配器，只订阅 Canonical Event
  投影并发送协议命令，不拥有服务端 Run 状态；本地工具执行通过平台能力端口完成。
- TUI 当前的 `Mind`、`stream_turn` 和 `EventReport` 组合是迁移期前端 adapter，
  已由 Protocol Client 与 Canonical Item reducer 接管模型正文、工具、审批、效果
  命令和展示投影；本地策略、工具执行和 UI 交互不拥有服务端 Run 状态。
  模型输出 presenter 消费 `current_item`、active/audit Items 和 canonical sources，
  只保留 Transcript 已交付 revision 水位；旧 `SegmentTracker` 已删除。
- MCP Server 只做协议解析和响应流控制；每个请求进入同一 Command Gateway。
- Subscription 只负责 open、WebSocket、resume、去重和 mailbox；收到远端任务后
  入队，不在 WebSocket 回调里启动模型轮次。

### 出站能力

- Model capability 接收冻结、可序列化的 `ModelStreamRequest`，返回具有明确关闭
  和事件水位生命周期的流，不返回 UI 对象；重连展示和审批恢复通过独立 callback
  端口接入，不写入请求协议对象。已规范化的执行环境通过显式
  `environment_snapshot` 字段进入请求，禁止隐藏在通用 `options`；model adapter
  只在 wire 边界将其映射回 `exec_env`。审批 callback 调用前，Protocol Client 必须
  已完成快照优先级归约，并只向前端公开仍为 `waiting_approval` 的 Item；不得把
  快照 `last_event_seq` 当作客户端确认游标。`assistant_text` 必须由 active canonical
  text Items 派生，provider retry 后迟到的旧 Item 不得重新进入正文；sources 只从
  active text/builtin Items 聚合。Harness 必须在回合收尾中校验并等待流关闭。
  传输、协议和适配器失败统一为 `ModelCapabilityError`，以稳定 `code`、
  `retryable` 和 JSON `details` 进入 Run 终态结果与持久事件，不把 HTTP 客户端
  异常类型泄漏到 runtime。
- MCP/Helix capability 只暴露工具发现、调用和生命周期结果；协议值对象不携带
  MCP SDK 会话或 Helix 进程句柄。
- Process/filesystem capability 通过受控 `ProcessSpec` 和根目录相对路径暴露；
  本地实现可使用标准库，受限模式必须显式注入 sandbox launcher。端口探测和
  进程清理不下沉到协议包；完整权限进程和 Helix 生命周期已通过显式 capability
  adapter 接入，Sandbox sidecar 作为受限平台后端保留到后续退役评审。
- Observability capability 接收结构化事件，不要求 domain 直接写 stdout/stderr。

## 当前实现映射

| 当前位置 | 目标归属 | 迁移要求 |
| --- | --- | --- |
| `mind.py`、`agent/composition.py` | `composition.py` | `mind.py` 已创建单个 `RuntimeServices` 并注入全部进程入口；具体 store 和 capability 只能在 `agent/composition.py` 装配 |
| `mind_app/controller.py` | application 公开门面 | 只借用入口注入的 `RuntimeServices`，不复制能力引用、不选择具体实现；已有可变状态按完整生命周期迁出 |
| `mind_app/runtime/turns/root.py` | `application/commands.py` | CLI、TUI、MCP 和 Subscription 已由类型化 Command 驱动；`RootTurnCommandExecutor` 作为显式 composition adapter 保留，统一根轮次调用和结果投影，不拥有 Session/Run 状态 |
| `mind_app/runtime/turns/stream.py`、`stream_model.py` | `harness/session_loop.py`、`application/turn_pipeline.py`、TUI adapter | 输入准备、终态、工具交付、资源收尾和回合展示已拆到具名所有者；模型 presenter 只消费 Protocol Client current/active/audit Item 投影并持有 Transcript 交付水位，RunResult、Stop Hook、最后回复和 sources 均读取 canonical 投影；`stream.py` 暂留迁移期事件路由，所有模型/工具/审批/效果命令均走 Protocol Client |
| `mind_app/runtime/mcp/*`、`subscription/lifecycle.py`、`runtime/environment/coding_lifecycle.py` | capabilities、adapters、harness supervisor | 保留已收敛的资源所有权，迁移时按端口而非按文件直接搬运 |
| `mind_app/runtime/subagents/control.py` | `harness/scheduler.py`、`domain/agents.py` | 将 mailbox、生命周期和图持久化分开 |
| `mind_app/runtime/subagents/graph.py` | `stores/agent_graph.py` | 保留检查点语义，存储实现不得进入 domain |
| `agent/stores/effect_journal.py`（旧 `mind_app/runtime/durable_effects.py` 已删除） | `stores/effect_journal.py` | 已成为现有效果状态机的正式落点；效果身份、指纹、重放和对账由端口约束 |
| `agent/stores/run_store.py`、`_run_schema.py`、`_run_records.py` | `stores/session_store.py`、`event_store.py`、`outbox.py` 的首个事务切片 | 已原子提交事件、快照、outbox 和最终事实；只有出现独立生命周期或规模压力时再物理拆 store，避免单次转发 facade |
| 旧 wire 模块 | `protocol/schema`、`protocol/transport`、`protocol/client` | 已按正式协议校验 Canonical Item、批次边界、`stream.gap` 和 Turn 坐标；schema、传输和客户端操作分层，协议不得导入 `engine` |
| 旧 chat/tool/effect 请求模块 | `protocol/client`；`agent/adapters/protocol_client.py`、`item_reducer.py` | `protocol` 是最终 wire schema、HTTP、认证、事件解析、SSE、attach 和恢复原语的唯一所有者；Protocol Client 独立拥有 Harness 请求坐标、结算游标、Canonical Item 状态、审批快照优先级以及 steer/status/fork/renew/tool/approval/effect 命令端口，由 TUI、桌面端和 Web 共用 |
| 旧模型请求模块 | `agent/protocol/model.py`、`protocol/client/chat.py`、`agent/adapters/protocol_client.py` | `ModelStreamRequest` 显式冻结 Turn 坐标、metadata 和环境快照；`MindChatProtocolClient` 通过 `protocol` 完成 wire 映射，不在 `agent.protocol` 复制 endpoint schema 或传输实现 |
| `engine/observability.py` 及业务 logger | `observability/` | 结构化观测和第三方日志适配拥有唯一实现；迁移所有消费者后删除旧模块，并由 AST 守卫禁止标准 logging 回流 |
| `engine/enhance/` | `mind_app/runtime/tools/enhancement/`（迁移期运行工具 adapter） | 结果增强依赖工具执行与远端自愈协议，不能留在低层 engine；完整调用者切换后删除旧目录，后续随 `mind_app` 工具 adapter 一并迁入目标前端/能力边界 |
| `engine/encoding.py`、`engine/terminal.py`、`engine/ports.py`、`engine/file_assist.py` | `infrastructure/platform/` | 进程输出编码、终端进程、端口探测/清理和文件打开属于平台实现；消费者切换后删除旧模块，平台实现不得反向导入 legacy runtime |
| `engine/errors.py` | `infrastructure/errors.py` | 入口可展示异常由独立基础设施边界持有；所有消费者切换后删除旧错误模块，不在 `engine` 保留别名 |
| `engine/manage.py` | `infrastructure/services/server_manager.py` | 本地后台服务探测、启动、重启和关闭由服务基础设施持有；Helix capability 只适配生命周期，不复制服务状态机 |
| `engine/upgrade.py` | `infrastructure/update/runtime.py` | 运行时下载、归档校验、安装替换和升级进度由更新基础设施持有；入口只注入进度端口 |
| `engine/animation.py`、`engine/signals.py` | `infrastructure/platform/` | 通用异步动画与任务中断检测属于平台运行时；不向 Harness 或协议层暴露平台句柄 |
| `mind_core/licensing.py` | `infrastructure/services/licensing.py` | 签名验证、设备指纹、授权续期和网络授时属于基础设施服务；配置/策略包不直接拥有外部网络或平台进程依赖 |
| `mind_core/remote_services.py` | `infrastructure/services/remote_services.py` | 远程服务元数据和授权状态查询归服务基础设施；工具增强只依赖该服务边界，不读取 `mind_core` 内部实现 |
| `mind_core/mcp_status.py` | `mind_app/presentation/mcp_status.py` | MCP 状态值对象、快照归约和渲染器共同归入展示边界；配置核心不持有 UI 状态语义 |
| `mind_core/design/` | `mind_app/presentation/terminal/` | 终端能力探测、颜色调色板、标题进度、启动动画和下载渲染属于可替换前端展示基础设施；不让配置或 Harness 持有终端句柄 |
| `mind_app/runtime/design.py` | `mind_app/presentation/terminal/contracts.py` | 下载进度渲染端口与终端实现放在同一展示边界；删除 runtime 级一次转发协议文件 |
| `mind_app/frontend/` | `mind_app/presentation/application.py`、`application_sinks.py` | 应用级展示值、前端运行期端口和 CLI/JSON sink 属于 presentation 边界；顶层 `frontends/` 仅在完整入口用例迁移时启用 |
| `mind_app/output/` | `mind_app/presentation/output/` | 单轮输出端口、结构化正文、文本/JSONL/静默 sink 属于 presentation 适配器；不把输出生命周期放入 Harness |
| `mind_app/stream_events/` | `mind_app/presentation/stream/` | 流事件到展示视图的投影、工具 trace 和生命周期渲染归入 presentation；运行时只消费公开投影函数 |
| `mind_app/stream_io/`、`stream_state/` | `mind_app/presentation/output/recording.py`、`boundary.py` | 输出记录和段间边界状态归入输出适配器；单调用者 spacing 逻辑内聚到 boundary，不保留平铺状态包 |
| `mind_app/approval/permission_grants.py`、`ledger.py` | `agent/stores/permission_grants.py`、`approval_ledger.py` | 会话权限授权和审批消费状态由 stores 持有；协调器、策略和展示模型不随状态存储迁移 |
| `mind_app/reporting.py` | `observability/reporting.py` | 单次运行报告目录、诊断日志 sink 和输出记录路径由可观测性基础设施统一管理；控制器只持有注入的报告对象 |
| `mind_app/paths.py` | `infrastructure/config/runtime_paths.py` | 用户数据目录、报告/会话/历史/效果/运行时数据库路径和子进程环境属于配置基础设施；入口布局解析保持在 `config/paths.py` |
| `mind_app/assets.py` | `infrastructure/update/assets.py` 与 `mind_app/presentation/terminal/download_renderer.py` | 资产存在性和升级触发属于更新基础设施；动画管理器到终端进度端口的适配属于 presentation，不让更新层依赖 UI |
| `mind_app/attach.py` | `mind_app/interaction/attachments.py` | 待发送附件的路径解析、分类、快照和消费属于交互输入状态；不把一次输入状态伪装成持久化 Store 或协议模型 |
| `mind_app/mcp/` | `mind_app/runtime/mcp/` | MCP 配置、外部连接、会话组合、工具结果和 stdio 服务同属运行时适配边界；不在应用根保留平铺包或转发 facade |
| `mind_core/application_paths.py` | `infrastructure/config/paths.py` | 应用入口、打包模式、本地资源目录和用户数据目录解析属于配置基础设施；不把路径环境事实放入策略模块 |
| `mind_core/agent_config.py`、`mind_core/feature_config.py` | `agent/application/settings.py` | Agent 并发限制和可选能力开关是应用启动设置；通过 application 公开入口提供，不让配置包持有运行设置模型 |
| `mind_core/provider_config.py` | `infrastructure/config/providers.py` | Provider Profile 默认值、路由和标识校验属于配置基础设施；不把供应商连接规则放入 Harness domain |
| `mind_core/skills/` | `infrastructure/skills/` | 技能目录发现、frontmatter 解析、来源去重、配置过滤和请求 payload 属于本地资源基础设施；配置核心不读取技能文件系统 |
| `mind_core/project_trust.py` | `infrastructure/config/trust.py` | 项目根、Git checkout、信任登记和配置目录边界依赖本地文件系统；配置解析只消费已解析的信任上下文 |
| `mind_core/permissions.py` | `agent/domain/policies.py` | 沙箱、审批、网络访问和用户预设是 Harness 领域策略值对象；通过 application 公开入口提供，不让前端或配置核心拥有策略实现 |
| `mind_core/service_config.py` | `infrastructure/services/service_config.py` | 服务域名规范化和远程配置读取属于服务基础设施；通过最小 `ConfigReader` 端口注入配置，不在基础设施内部创建配置存储 |
| `mind_core/preference.py`、`config_to_preferences` | `infrastructure/config/preferences.py` | 本地配置到运行时偏好的投影、远程偏好读取和偏好覆盖属于配置基础设施；不把配置存储或 HTTP 细节带入 Harness domain |
| `mind_core/config_store.py` | `infrastructure/config/store.py` | TOML 文件读写、原子更新和配置文档保真保存属于配置存储基础设施；配置解析/会话只依赖其公开存储契约 |
| `mind_core/config.py` | `infrastructure/config/schema.py` | 配置 schema、规范化、点路径覆盖和 Provider/MCP/TUI 校验属于配置基础设施；不让前端直接解释原始 TOML |
| `mind_core/config_layers.py` | `infrastructure/config/layers.py` | 用户、Profile、项目和 CLI 的优先级合并及信任边界解析属于配置基础设施；只消费存储、信任和 schema 契约 |
| `mind_core/config_session.py` | `infrastructure/config/session.py` | 配置读取、原子更新、覆盖校验和项目信任提交属于配置会话基础设施；应用入口只依赖会话公开接口 |
| `mind_nova/const.py` | `metadata/const.py` | 产品版本、展示、编码和构建元数据已抽出；`setup.py` 与内置配置服务已切换，服务端点、认证和运行时路径仍按职责在后续切片迁移 |
| `agent/ports/capabilities.py`、`agent/adapters/protocol_client.py` | `ports`、`adapters/protocol_client.py` | `ModelCapabilityError` 统一传输/协议失败，`ProtocolModelEventStream` 负责坐标门禁、current/active/audit Items、canonical 正文/sources、异步迭代、幂等关闭及结算后游标提交；错误码、重试性和 JSON 细节由 Run 终态及 `run_failed` 事件保留 |
| 已删除的 `mind_app/runtime/environment/exec_env.py`、旧 environment 请求模块 | `capabilities/environment.py`、`protocol/schema/environment.py` | 本机事实采集和 Helix provider 聚合已迁入进程级注入的 `EnvironmentSnapshotCapability`；线上 schema 与规范化归属 `protocol.schema`。四类入口在命令持久化前冻结快照，model adapter 只在 wire 边界映射 `exec_env` |
| `agent/protocol/capabilities.py`、`agent/ports/capabilities.py` | `protocol`、`ports` | MCP 工具值对象、Helix 生命周期、受控进程/文件和本地 sandbox 权限只通过具名 port 表达；不把 SDK 会话、进程句柄或操作系统路径带入 domain |
| `agent/capabilities/mcp.py`、`helix.py`、`process.py`、`filesystem.py` | `capabilities` | MCP/Helix 内存替身和本地进程/文件实现均可脱离网络、TUI 和 legacy runtime 测试；完整权限进程与 Helix 已由显式 adapter 接入，受限进程继续接受显式 sandbox launcher |
| 已删除的 `mind_app/runtime/turns/delivery.py` | `adapters/protocol_client.py` | Session 跨 Turn 的 `event_seq` 水位已迁入独立 Protocol Client；控制器和前端不再持有，也不与本地 Run sequence 合并 |
| `mind_core` 配置、权限、hooks、skills | `domain/policies.py`、`application/`、`infrastructure/config`、capability adapters | 配置读取、策略判断和技能/Hook 生命周期拆开，禁止形成新的共享杂物包；完成后删除 `mind_core` |
| `mind_app/cli`、`tui`、`mcp`、`subscription` | `frontends/`、`application/`、Protocol Client | 四类入口均通过 `RuntimeServices` 接收 application；CLI/TUI/MCP/Subscription 的执行命令已冻结并提交统一入口，终态观测和回执优先使用 Run/Canonical Event projection；TUI 作为 Protocol Client 前端 adapter，桌面/Web 通过同一 fixture 校验协议投影；完整用例迁出后删除 `mind_app` |
| `mind_app/mcp/server.py` | `adapters/mcp_server.py` | `mind_exec` 已通过注入的 `TurnApplication` 和 `RootTurnCommandExecutor` 提交 `SubmitTurnCommand`，structured content 优先使用 `RunResultProjection`；MCP runtime 不拥有控制器或模型生命周期 |
| `mind_app/subscription/forwarding.py`、`subscription/runtime.py` | `adapters/subscription.py`、`application` | `AgentExecutor` 将远端 forward 冻结为稳定 `SubmitTurnCommand`，长驻 application 由 `AgentRuntime` 拥有并在 shutdown 关闭；完成/失败/中断分类使用 application 的 Event projection |
| `engine` | `agent.capabilities` 与 `infrastructure/` 的具体实现 | 已完成全部生产消费者切换并删除 `engine` 源包；后续只允许从导入图和退役守卫确认无残留引用，不恢复兼容 facade |
| `server` | 客户端内置配置服务 | `ConfigServiceRuntime` 只提供配置 UI/健康检查；不承载 Harness 状态、不拥有线上事件，也不是 `mind.chat` 服务端 |
| `backend` | 独立打包 | 本次和后续迁移均不修改其目录和依赖边界 |

## 不采用的方案

- 不把 `mind_app`、`mind_core`、`mind_nova` 简单改名为 `control_plane`、
  `shared` 或 `protocol`。顶层 `protocol` 只有在完成 wire SDK 职责迁移并具备独立
  测试、版本和所有权后才能建立；改名本身不解决状态所有权和副作用恢复。
- 不建立一个新的万能 `core` 或 `services` 包。跨域代码必须归属到明确的 domain、
  runtime、store 或 capability。
- 不用事件总线替代 Session 单写者。事件总线用于传播事实，不能让多个消费者
  同时写同一个 Run。
- 不将每一个流式 token 作为必须恢复的持久化事件。
- 不让 WebSocket、TUI 或 MCP handler 直接启动模型轮次。
- 不为旧包保留只转发一次调用的长期 facade。迁移期间只保留必要的稳定入口，
  且每个入口都要有删除条件。

## 架构验收标准

迁移完成前必须满足：

1. 同一 Run 的所有状态写入都经过一个 `RunActor`。
2. 任意入站通道都只能提交 Command，不能直接访问模型、工具或持久化实现。
3. `protocol` 和 `domain` 可在无网络、无配置、无 TUI 的环境中独立测试。
4. 进程在任意一步退出后，可以从快照、事件和效果日志恢复或明确进入人工对账。
5. 审批、暂停、取消、超时、重试和断线恢复都有主流程和失败路径测试。
6. `backend/` 无来自 Agent Harness 的导入，Agent Harness 也不导入 `backend`。
7. 公共 API 只从 `agent.protocol`、`agent.application` 和必要的 capability port
   导出；内部模块默认私有。
8. 新增模块原则上不超过 500 行；超过 800 行必须拆分职责并补充边界说明。
9. 文档、协议和 CLI 变更经过对应的兼容性评审，不以批量目录移动代替迁移。
10. Protocol Client 只依赖 `PROTOCOL.md` 定义的命令、事件、游标和恢复语义，
    不依赖 `Mind`、TUI、`prompt_toolkit` 或本地 `OutputSession`。
11. TUI、桌面端和 Web 使用同一组 Canonical Event/Item fixture 验证去重、回放、
    `turn.logical_settled`、审批和工具结果命令，不各自复制协议状态机。
12. 新 Turn 的环境快照在进入持久队列前冻结并纳入命令指纹；continuation 和 queued
    redispatch 复用同一 `snapshot_id`，不得按恢复进程的当前环境重新采集。

## 版本与兼容

本架构允许一次有计划的 breaking change，但必须先冻结以下外部契约：

- `mind.py` 启动方式和主要 CLI 命令；
- MCP 工具名称、输入 schema 和结果结构；
- 订阅 open/ws/resume 消息和去重语义；
- 会话、任务、报告和证据的标识规则；
- 配置文件和历史记录的迁移策略。

旧包中已迁移职责的删除条件是：所有入站适配器已经使用 `agent.application`，
所有出站能力已经从 legacy 实现解耦，Protocol Client 的公共 wire contract 已
明确归属，定向测试和恢复测试通过，并且没有生产导入继续指向待删除的历史实现。
协议 SDK 若仍被桌面端或 Web 客户端需要，必须作为独立公共边界保留，不能为了
追求单一 `agent` 导入图而物理删除。此前不进行物理删除，只允许有期限、有追踪项
的迁移入口。
