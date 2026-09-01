# Agent Harness 架构基线

状态：已采纳（Architecture Decision Record）；阶段 4 已完成，阶段 5 进行中（2026-08-31）

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
  `agent.adapters.protocol.client` 只把冻结的 Harness 请求映射到该 SDK，并拥有本地
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
边界的独立传输实现。`agent.adapters.protocol.items` 已在传输交付前把 Item 事件归约
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
├── persistence/                # 本地文件与外部持久化 adapter
│   └── transcripts.py          # Transcript JSONL 读写和 Session 路径
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
使用”预建空目录。阶段 5 的职责化重组已完成首轮：application、harness、stores 和
adapters 的子职责目录均已有真实调用者，旧平铺路径已删除；后续只在独立生命周期、
一致性或扩展边界成立时继续拆分。

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
│   ├── agents.py            # 子 Agent 状态、关系和任务提交值对象
│   ├── approvals.py         # 审批请求、决定和策略
│   ├── hook_matching.py     # Hook matcher 解析、工具别名和候选值规则
│   ├── tool_policy.py       # app/api 工具可见性和元数据过滤规则
│   └── policies.py          # 权限、预算、取消和重试规则
├── harness/
│   ├── agents/              # Agent 树状态、活动 Turn 和根会话注册
│   │   ├── control.py       # Agent 树状态机、队列和 mailbox 协调
│   │   ├── delivery.py      # 活动 Turn 投递状态
│   │   └── registry.py      # 根会话 control 注册、恢复和关闭生命周期
│   ├── execution/           # Run/Subagent 执行与接管协调
│   │   ├── actor.py         # 一个 Run 的串行状态执行器
│   │   ├── subagent_runner.py # 子 Agent Hook 生命周期和续跑协调
│   │   └── subagent_submission.py # 已分配提交的上下文、执行和 mailbox 确认协调
│   └── sessions/            # Session 单写者和生命周期所有者
│       ├── loop.py          # 一个 Session 的单写者事件循环
│       └── owner.py         # SessionLoop 的创建、关闭和恢复入口
├── application/
│   ├── agents/              # 子 Agent 上下文、消息结果和只读视图
│   │   ├── thread.py        # 子 Agent 线程和轮次上下文
│   │   ├── messages.py      # Agent 消息派发结果值对象
│   │   ├── views.py         # Agent 状态、等待和 mailbox 只读视图
│   │   └── fork_context.py  # 父会话继承范围和上下文快照
│   ├── turns/               # Turn 输入、执行上下文和结果投影
│   │   ├── commands.py      # submit、resume、approve、cancel、retry
│   │   ├── context.py       # Agent、Turn 和工具调用执行上下文
│   │   ├── execution.py     # 固定 Hook 作用域的 Turn 执行契约
│   │   ├── environment.py   # 环境快照采集用例与能力失败收敛
│   │   ├── compact_result.py # 上下文压缩稳定结果值对象
│   │   ├── run_result.py    # 单次 Run 的不可变结果值对象
│   │   ├── stream_outcome.py # 流式 Turn 终态聚合与结果构建
│   │   └── projections.py   # Event Queue 到入口稳定结果的投影
│   ├── hooks/               # Hook 输入、生命周期和输出契约
│   │   ├── catalog.py       # Hook 管理目录、状态快照和变更冲突
│   │   ├── context.py       # Hook 输入上下文值对象
│   │   ├── events.py        # Hook 生命周期事件规格目录
│   │   ├── models.py        # Hook 生命周期快照、决定和工具结果值对象
│   │   ├── output.py        # Hook 输出语义校验与归一化
│   │   ├── protocol.py      # Hook stdin/stdout schema、构建和边界校验
│   │   ├── result.py        # 后置 Hook 对模型可见工具结果的投影
│   │   └── subagent.py      # 子 Agent Hook 生命周期聚合
│   ├── views/               # 跨前端共享的应用结果 projection/view 契约
│   │   ├── contracts.py      # PresentationView 与 PresentationSink
│   │   ├── run.py            # Run 终态、失败和生命周期视图
│   │   ├── tools.py          # 工具轨迹、调用、结果和批次视图
│   │   ├── plan.py           # 计划步骤视图
│   │   ├── patch.py          # 补丁差异和诊断视图
│   │   ├── approval.py       # 审批结果视图
│   │   ├── hooks.py          # Hook 生命周期和输出视图
│   │   ├── progress.py       # 工具进度视图
│   │   └── tool_display.py   # 工具展示分类和阶段策略
│   ├── config/              # 应用启动设置和本地身份
│   │   ├── settings.py      # Harness 并发和可选能力的启动时设置
│   │   └── session_identity.py # 远端坐标到本地 Session 身份的确定性派生
│   └── services.py          # 用例编排，不持有长期运行状态
├── ports/
│   ├── capabilities.py      # 模型、MCP、Helix、进程和文件端口
│   ├── presentation.py      # 应用级展示值和 sink 端口
│   ├── hooks.py              # Hook 执行器和超限上下文 spill 端口
│   ├── agent_messages.py    # 子 Agent 消息回执和投递端口
│   ├── mcp_session.py       # 工具执行所需的 MCP 会话端口
│   ├── turns.py              # 模型轮次操作和输入事件端口
│   ├── subagents.py          # 子 Agent 执行和操作端口
│   ├── sessions.py           # Turn application 使用的 Session 生命周期端口
│   ├── workspace.py          # 工作区资源生命周期和组合工厂端口
│   ├── persistence.py       # 事件、快照、历史和 outbox 端口
│   ├── permissions.py       # 执行上下文读取权限授权端口
│   └── observability.py     # 日志、指标和 tracing 端口
├── stores/
│   ├── agents/              # 子 Agent 图与 mailbox 持久化
│   │   ├── graph.py         # 子 Agent 图快照、SQLite 存储和单写者持久化
│   │   └── mailbox.py       # 子 Agent mailbox 事件、快照和消费游标
│   ├── runs/                # Run 事件、快照和最终事实
│   │   ├── store.py         # Session/Run 元数据和最终记录
│   │   ├── records.py       # Run 快照记录投影
│   │   └── schema.py        # SQLite schema 版本
│   ├── effects/             # 外部副作用账本
│   │   └── journal.py       # 外部副作用状态机
│   ├── transcripts/          # 跨入口 Transcript 记录值和归约
│   │   ├── records.py        # 结构化记录校验与稳定 JSON 投影
│   │   └── replay.py         # 消息更新、替代和工具结果归约
│   ├── sessions/             # Session 游标和分支请求持久化
│   │   └── history.py        # SQLite history cursor 存储
│   └── approvals/           # 审批和权限状态
│       ├── ledger.py        # 工具审批消费记录
│       └── permissions.py   # 会话权限授权记录
├── capabilities/
│   ├── model.py             # 模型流式响应能力
│   ├── mcp.py               # MCP 工具发现与调用能力
│   ├── helix.py             # Helix 执行面连接
│   ├── process.py           # 进程、端口和沙箱能力
│   ├── environment.py       # 客户端执行环境快照采集与 provider 聚合
│   └── filesystem.py        # 受控文件能力
├── adapters/
│   ├── protocol/             # mind.chat 命令、传输恢复和 Canonical Item 投影
│   │   ├── client.py        # Protocol Client 请求和恢复
│   │   └── items.py         # Canonical Item reducer
│   ├── agents/               # 子 Agent 外部交互适配
│   │   ├── messages.py      # 子 Agent steer 协议适配
│   │   └── execution.py     # 流式 Subagent 执行适配
│   ├── cli.py               # CLI 输入/退出码到 Command
│   ├── tui.py               # TUI 输入、渲染和 Event 投影
│   ├── mcp_server.py        # MCP 入站协议端口（实现落在 frontends/mcp）
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
agent.adapters.protocol.client ─────────────> protocol
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

- `agent/stores/runs/store.py` 编排 SQLite 事务，私有 schema 与记录投影分别位于
  `schema.py`、`records.py`；公开存储实现不泄漏数据库行或 SQL。
- `runtime.db` 的 schema/snapshot 版本为 1；事件、快照、outbox 和最终事实由
  同一事务提交。`effects.db`、`agents.db`、`history.db` 保持独立文件和所有权。
- queued 快照可以使用持久化原命令安全再派发；running、waiting_effect 和执行
  超时必须对账，waiting_approval 保持等待，paused 只能显式恢复。
- `agent/stores/effects/journal.py` 是本地外部效果状态机的正式实现；旧
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
| `mind.py`、`agent/composition.py` | `composition.py` | `mind.py` 已创建单个 `RuntimeServices` 并注入全部进程入口；根轮次 runner 在组合根绑定 Model/Protocol/Effect 能力，具体 store 和 capability 只能在 `agent/composition.py` 装配 |
| `mind_app/controller.py` | application 公开门面 | 只借用入口注入的 `RuntimeServices`，不复制能力引用、不选择具体实现；已有可变状态按完整生命周期迁出 |
| `mind_app/runtime/turns/root.py` | `agent/application/turns/commands.py`、`agent/adapters/turns/root.py` | CLI、TUI、MCP 和 Subscription 已由类型化 Command 驱动；命令映射已迁入 controller 无关的入站 adapter，旧 runtime 只保留尚待 Harness 接管的根轮次准备与执行编排 |
| `mind_app/runtime/turns/root.py::run_foreground_turn` | `mind_app/presentation/terminal/turn_lifecycle.py` | 动画、终端进度和清理属于展示生命周期；runtime root 只保留根轮次准备与执行编排，生命周期模块不拥有 Turn/Session 状态 |
| `mind_app/runtime/turns/stream.py`、`stream_model.py` | `agent/harness/sessions/loop.py`、`agent/application/turns/`、TUI adapter | 输入准备、终态、工具交付、资源收尾和回合展示已拆到具名所有者；模型 presenter 只消费 Protocol Client current/active/audit Item 投影并持有 Transcript 交付水位，RunResult、Stop Hook、最后回复和 sources 均读取 canonical 投影；模型 `ModelCapability`、控制 `ProtocolCommandClient`、`EffectJournalFactory` 和输出 `SessionFactory` 由组合根显式绑定并贯穿 continuation，审批状态由 `TurnContext` 的 `ApprovalLedger` 端口承载，`stream.py` 暂留迁移期事件路由，下一步收口其剩余 Controller 运行上下文依赖 |
| `agent/application/turns/context.py::TurnContext.approval_ledger` | `agent/ports/approvals.py` + Harness session application | 审批消费和终态事实属于单次 Turn 执行上下文；具体 `ApprovalCallLedger` 只在组合根/Store 实现，application 与流式路由依赖端口，不创建临时账本或回写宿主 |
| `agent/application/turns/context.py::TurnContext.transcript_factory` | `agent/ports/transcript.py` + Harness session application | Transcript writer 的创建属于组合根提供的会话端口；续跑按新的 Turn 坐标重新创建 writer，流式路由和 session setup failure 不得访问 `controller.transcripts` |
| `agent/application/turns/context.py::TurnContext.cleanup` | `agent/ports/turns.py::TurnCleanupPort` + Harness session application | 单轮异步资源清理由执行上下文携带；流式路由和 Finalizer 只等待该端口，不直接依赖 Controller 的清理实现；动画、输出和工作区资源仍由各自所有者关闭 |
| `agent/application/turns/context.py::TurnContext.patch_preview` | `agent/ports/workspace.py::PatchPreviewPort` + workspace adapter | 补丁审批和客户端工具只接收只读预览端口，不读取完整 `WorkspaceRuntime`；补丁规划失败只能省略预览，不得修改工作区 |
| `agent/application/turns/context.py::TurnContext.retry_state` | `agent/ports/turns.py::RetryStatePort` + 前端状态 adapter | provider/transport 重试展示状态由单轮上下文携带；流式路由不得从 `controller.frontend.runtime` 反射读取，Subagent 不创建根前端状态 |
| `agent/application/turns/context.py::TurnContext.animation` | `agent/ports/turns.py::TurnAnimationPort` + `mind_app/presentation/terminal/animation.py` | 流式执行只消费前台活动状态和等待动画停止端口；Controller/前端负责具体动画实现，执行层不得读取 `frontend.runtime` 或调用 `stop_anim` |
| `agent/application/turns/context.py::TurnContext.session_context` | `agent/ports/turns.py::TurnSessionContextPort` + `mind_app/runtime/turns/session_context.py` | 流式准备和启动展示只消费已解析的环境、skills、工作区、Hook 告警与持续命令会话；Controller 配置、环境能力、skills loader 和 Hook 存储由组合边界适配，执行层不得直接读取宿主属性 |
| `agent/application/turns/context.py::TurnContext.session_state` | `agent/ports/turns.py::TurnSessionStatePort` + `mind_app/runtime/turns/session_context.py` | 流式失败、取消上下文和最近 assistant 回复通过会话状态端口写回；执行层不得直接访问 `ConversationState` 或 Controller 结果属性 |
| `agent/application/turns/context.py::TurnContext.execution_policy` | `agent/ports/workspace.py::ExecutionPolicy` + 组合根工作区策略适配器 | 本地工具审批判定、会话批准和规则提案写回通过单轮执行策略端口注入；流式工具/审批处理器不得反射读取 `WorkspaceRuntime` |
| `mind_app/runtime/mcp/*`、`subscription/lifecycle.py` | capabilities、adapters、harness supervisor；MCP 生命周期所有者归 `agent/harness/mcp/owner.py`，Subscription 生命周期所有者归 `agent/harness/subscription/owner.py` | 保留已收敛的资源所有权，迁移时按端口而非按文件直接搬运；具体 MCP/Subscription runtime 均由组合根工厂注入 |
| `frontends/subscription/runtime.py::_build_default_executor` | `frontends/subscription/runtime.py` + 组合根 `mind.py` | `AgentRuntime` 只接受显式 `TurnApplicationFactory`；持久 application 由 `mind.py` 绑定，前端不通过宿主动态属性发现 `RuntimeServices`，关闭时由执行器回收 application |
| `mind_app/runtime/mcp/service_lifecycle.py`、`keepalive.py`、`service_runtime.py` 中的服务上下文值对象与 setup helpers | `infrastructure/services/runtime_owner.py`、`keepalive.py`、`runtime_context.py`、`runtime_setup.py` | 本地 Helix 服务的启动任务、保活、端口终止、路径解析和上下文规格属于基础设施；TUI/CLI 只消费服务生命周期入口，不让 runtime 持有平台资源所有权 |
| `mind_app/runtime/subagents/control.py` | `agent/harness/agents/control.py`；状态值对象归 `agent/domain/agents.py`、图归 `agent/stores/agents/graph.py` | AgentControl 只保留可变树调度、mailbox 协调和观察快照；Harness 持有状态机，domain/stores 不反向依赖它 |
| `SubagentRuntime._execute_submission` | `agent/harness/execution/subagent_submission.py` | 已分配提交的 mailbox claim、Turn 上下文构造、活动轮次投递、结果确认和失败收束归 Harness；runtime 只注入 Controller、Hook scope 与执行适配器 |
| `SubagentRuntime._controls`、根会话生命周期锁 | `agent/harness/agents/registry.py` | AgentControlRegistry 串行管理根会话 control 的创建、恢复、移除和关闭；runtime 不再持有执行树注册表或 shutdown 状态 |
| `AgentSnapshot`、`AgentWaitResult`、`AgentMailboxWaitResult` | `agent/application/agents/views.py` | 跨 TUI、工具和 runtime 的只读 Agent 视图归 application；Harness control 只创建视图，不拥有公共值对象 |
| `AgentMessageDispatch` | `agent/application/agents/messages.py` | 消息派发结果是跨入口复用的 application 值对象；活动轮次和注册表只保留 Harness 内部状态 |
| `mind_app/runtime/subagents/graph.py` | `agent/stores/agents/graph.py` | 图快照、SQLite 存储和持久化单写者归入 stores，存储实现不得进入 domain |
| `AgentSubmission`、Agent 状态字面量 | `agent/domain/agents.py` | 任务提交和状态分类只依赖协议标识与标准库，供 control、stores 和后续 Harness 调度复用 |
| `mind_app/runtime/subagents/mailbox.py` | `agent/stores/agents/mailbox.py` | 子 Agent mailbox 事件、快照、消费游标和有界日志是持久状态；runtime/subagents 只依赖存储契约，不拥有 mailbox 数据结构 |
| `mind_app/runtime/subagents/thread.py` | `agent/application/agents/thread.py`、`agent/application/agents/fork_context.py` | 子 Agent 线程/轮次上下文和父会话继承快照是 application 执行契约；运行时控制器只消费已冻结值，不持有跨边界身份结构 |
| `mind_app/runtime/subagents/context.py`（已删除） | `agent/adapters/agents/fork_context.py` + `agent/application/agents/fork_context.py` | fork history adapter 只接收组合根注入的 Transcript 条目读取 callable；继承范围、渲染和字符预算算法由 application 持有，SubagentRuntime 不实例化文件 Store |
| `mind_app/presentation/application.py`（展示端口定义） | `agent/ports/presentation.py` | `ApplicationView`、`ApplicationSink`、`Viewport` 是跨入口的纯展示端口；`FrontendRuntime` 和 `Frontend` 仍属于现有装配边界，避免把交互生命周期下沉到 ports |
| `mind_app/presentation/models.py`（已删除的混合视图定义） | `agent/application/views/` | Run、工具、计划、补丁、审批、Hook 和进度 view 按语义拆分；`PresentationView/PresentationSink` 归 `views/contracts.py`，纯文本原语仍归 `agent/ports/presentation.py`；旧总模型不得保留兼容入口 |
| `mind_app/runtime/subagents/delivery.py` | `agent/ports/agent_messages.py`、`agent/adapters/agents/messages.py`、`agent/harness/agents/delivery.py` | 消息回执和投递端口归 ports，`/turn/steer` 归 Protocol Client adapter，Harness 维护活动轮次就绪和 pending 输入状态 |
| `mind_app/history/ids.py` | `protocol/schema/identifiers.py` | `cid/sid` 正则和关联校验属于 wire identity schema；历史、交互、Controller 和 Harness 复用协议边界，不在 history 保留身份实现 |
| `mind_app/history/contracts.py`（已删除） | `agent/ports/transcript.py` | TranscriptSink 是 runtime、Hook、执行器和历史 writer 共享的最小写入端口；端口不依赖旧包或基础设施 |
| `mind_app/history/transcript.py` 中的 `TranscriptEntry`、`TranscriptReplay` | `agent/stores/transcripts/records.py`、`replay.py` | 共享记录值和事件归约器不依赖本地文件、观测或展示；工具归并策略由 `agent.domain.tool_policy` 提供 |
| `mind_app/history/transcript.py`（已删除） | `infrastructure/persistence/transcripts.py` | JSONL Reader/Writer、Session 日期路径、编码和损坏记录观测属于基础设施；实现依赖 `agent` 的记录值与 Sink 端口，不反向依赖旧应用 |
| `mind_app/presentation/tool_policy.py` | `agent/domain/tool_policy.py`、`agent/application/views/tool_display.py` | 工具过滤和 `is_approval_only_tool` 属于领域规则；`ToolDisplayKind`、`ToolDisplaySpec` 和 renderer 分类归应用 view policy；旧 presentation policy 文件删除，不保留兼容 facade |
| `mind_app/history/store.py`（已删除） | `agent/stores/sessions/history.py` | Session 游标和待分支请求是独立持久状态；存储只接收显式 `db_path`，CLI/Controller 在组合边界注入运行时路径，stores 不读取基础设施配置 |
| `agent/stores/effects/journal.py`（旧 `mind_app/runtime/durable_effects.py` 已删除） | `agent/stores/effects/journal.py` | 已成为现有效果状态机的正式落点；效果身份、指纹、重放和对账由端口约束 |
| `agent/stores/runs/store.py`、`schema.py`、`records.py` | `agent/stores/runs/` 的事务切片 | 已原子提交事件、快照、outbox 和最终事实；只有出现独立生命周期或规模压力时再物理拆 store，避免单次转发 facade |
| 旧 wire 模块 | `protocol/schema`、`protocol/transport`、`protocol/client` | 已按正式协议校验 Canonical Item、批次边界、`stream.gap` 和 Turn 坐标；schema、传输和客户端操作分层，协议不得导入 `engine` |
| `mind_app/runtime/turns/event_reporting.py` | `protocol/client/reports.py` | EventReport 的 Session/Turn 生命周期包装器与 wire transport 同属 Protocol Client；迁移后 runtime turns 只消费公开报告句柄，不保留旧路径或兼容 facade |
| 旧 chat/tool/effect 请求模块 | `protocol/client`；`agent/adapters/protocol/client.py`、`items.py` | `protocol` 是最终 wire schema、HTTP、认证、事件解析、SSE、attach 和恢复原语的唯一所有者；Protocol Client 独立拥有 Harness 请求坐标、结算游标、Canonical Item 状态、审批快照优先级以及 steer/status/fork/renew/tool/approval/effect 命令端口，由 TUI、桌面端和 Web 共用 |
| 旧模型请求模块 | `agent/protocol/model.py`、`protocol/client/chat.py`、`agent/adapters/protocol/client.py` | `ModelStreamRequest` 显式冻结 Turn 坐标、metadata 和环境快照；`MindChatProtocolClient` 通过 `protocol` 完成 wire 映射，不在 `agent.protocol` 复制 endpoint schema 或传输实现 |
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
| `mind_app/approval/permission_grants.py`、`ledger.py` | `agent/stores/approvals/permissions.py`、`ledger.py` | 会话权限授权和审批消费状态由 stores 持有；协调器、策略和展示模型不随状态存储迁移 |
| `mind_app/reporting.py` | `observability/reporting.py` | 单次运行报告目录、诊断日志 sink 和输出记录路径由可观测性基础设施统一管理；控制器只持有注入的报告对象 |
| `mind_app/paths.py` | `infrastructure/config/runtime_paths.py` | 用户数据目录、报告/会话/历史/效果/运行时数据库路径和子进程环境属于配置基础设施；入口布局解析保持在 `config/paths.py` |
| `mind_app/assets.py` | `infrastructure/update/assets.py` 与 `mind_app/presentation/terminal/download_renderer.py` | 资产存在性和升级触发属于更新基础设施；动画管理器到终端进度端口的适配属于 presentation，不让更新层依赖 UI |
| `mind_app/attach.py` | `mind_app/interaction/attachments.py` | 待发送附件的路径解析、分类、快照和消费属于交互输入状态；不把一次输入状态伪装成持久化 Store 或协议模型 |
| `mind_app/mcp/` | `mind_app/runtime/mcp/` | MCP 配置、外部连接、会话组合、工具结果和 stdio 服务同属运行时适配边界；不在应用根保留平铺包或转发 facade |
| `mind_app/native_coding/encoding.py` | `infrastructure/platform/encoding.py` | 进程输出编码探测、规范化和解码是跨能力的平台事实；native coding 只消费平台端口，不拥有第二套解码器 |
| `mind_app/runtime/processes.py` | `infrastructure/platform/processes.py` | 进程组创建、stdin 收束、树级中断/终止和 Windows/POSIX 差异属于平台生命周期能力 |
| `mind_app/native_coding/workspace_command.py` | `infrastructure/platform/workspace.py` | 无 shell 工作区命令、超时和输出上限属于平台命令执行能力；native coding 不拥有进程树实现 |
| `mind_app/native_coding/git_diff.py` | `infrastructure/platform/git_diff.py` | Git worktree 探测、安全配置和差异采集属于平台工作区能力；TUI 只消费差异结果 |
| `mind_app/native_coding/exec/command_safety/` | `infrastructure/platform/command_safety/` | 危险命令和 Windows 外部启动识别是跨平台安全边界；执行策略只消费分类结果，不拥有平台检测实现 |
| `mind_app/native_coding/exec/process_capture.py`、`output_decoder.py`、`sandbox_client.py`、`shell_runtime.py` | `infrastructure/platform/` | 进程捕获/输出解码、Sandbox sidecar 协议和 shell 运行时解析属于本机平台执行基座；native coding 只消费其能力，不拥有跨平台生命周期实现 |
| `mind_app/native_coding/exec/process_session.py` | `infrastructure/platform/process_sessions.py` | 多后端进程会话、输出缓冲、超时和回收属于平台执行生命周期；native coding 工具只提交启动规格并消费会话结果，不拥有进程状态表 |
| `mind_app/native_coding/native_coding.py` 中的 Sandbox 构造 | `mind.py::create_native_coding` | 唯一进程组合根创建 SandboxClient 和 ProcessSessionManager；NativeCoding 强制接收已装配会话运行时，工具注册表不得通过空参数隐式创建具体能力 |
| `mind_app/native_coding/js_repl/` | `infrastructure/platform/javascript_repl.py` | Node 内核进程、临时目录、消息桥接和内核重置属于平台执行基座；native coding 只负责工具入口和权限/调用策略，并显式注入应用资源根 |
| `mind_app/runtime/environment/shell_tools.py`、`workspace.py` | `infrastructure/platform/shell_tools.py`、`workspace_context.py` | 本机支持工具 PATH 路由和当前工作区探测属于平台环境助手；runtime/MCP/TUI 只消费结果，不拥有进程环境事实 |
| `mind_app/runtime/hooks/output_spill.py` | `infrastructure/platform/hook_output_spill.py` | Hook 流输出读取、临时文件 spill、预览和会话清理属于本机平台文件能力；Hook command 只依赖平台适配器 |
| `mind_app/runtime/hooks/command.py` | `infrastructure/platform/hook_command.py` | Hook 子进程启动、跨平台 shell、输出解析和终止属于平台执行能力；Hook runtime 只消费 `HookCommandRunner`，不拥有操作系统进程句柄 |
| `mind_app/runtime/hooks/runtime.py`、`registry.py` | `agent/harness/hooks/runtime.py`、`registry.py` | Hook 并发执行、信任解析、scope 构建和资源生命周期属于 Harness；Harness 只依赖 agent ports，具体平台执行器由组合根注入 |
| `mind_app/runtime/environment/coding_lifecycle.py` | `agent/harness/workspace_runtime.py` | 工作区编码、Shell、执行策略和进程能力的替换/关闭属于 Harness 生命周期；具体 NativeCoding/策略工厂只由根组合注入，Harness 不导入 legacy 或平台实现 |
| `mind_app/runtime/environment/snapshot.py` | `agent/application/turns/environment.py`、`mind_app/interaction/environment.py` | 环境能力调用与失败收敛属于 application 用例；Controller/Helix 上下文聚合属于 interaction adapter，不让 runtime 持有环境采集逻辑 |
| `mind_app/runtime/support/session_identity.py` | `agent/application/config/session_identity.py` | 远端 `cid/sid` 到本地持久化 Session 身份的确定性派生属于 application 身份用例；不让 CLI/TUI 各自复制哈希规则，也不把本地语义塞入线上 `protocol` |
| `mind_app/runtime/support/conversation.py` | `mind_app/interaction/conversation.py` | 本地会话标识、轮次边界和一次性上下文属于交互输入状态；Controller 只持有交互状态，不让 runtime support 继续承接会话生命周期 |
| `mind_app/runtime/support/clipboard.py` | `mind_app/tui/adapters/clipboard.py` | 系统剪贴板是 TUI 的平台 adapter；展示功能显式依赖该 adapter，不让通用 runtime support 持有 UI 专属 I/O |
| `mind_app/runtime/support/session_policy.py` | `mind_app/runtime/mcp/errors.py`、`mind_app/presentation/stream/exception_text.py` | MCP 传输关闭判断归 MCP 错误边界；HTTP/运行期异常的一行用户摘要归 stream presentation，按职责拆分，不保留混合 session policy |
| `mind_app/runtime/conversation.py` | `mind_app/runtime/compaction.py`、`agent/application/turns/compact_result.py` | 上下文压缩的运行时 Hook/Transcript 编排与不可变结果契约分离；runtime 只负责执行生命周期，application 只暴露稳定结果 |
| `mind_app/runtime/execution.py` | `agent/application/turns/context.py` | Agent、Turn 和工具调用上下文是跨能力共享的 application 执行契约；不让 MCP、Hook、工具和子 Agent 继续依赖 runtime 平铺实现模块 |
| `agent/stores/approvals/permissions.py` | `agent/ports/permissions.py` | application 只依赖 `PermissionGrantReader` 读取端口；具体授权存储留在 stores，由组合根注入，避免执行上下文反向依赖持久化实现 |
| `mind_app/runtime/turns/result.py` | `agent/application/turns/run_result.py` | 单次模型 Run 的稳定结果值对象属于 application 出站契约；前端和 Subagent 只消费公开结果，不从 runtime turns 导入 |
| `mind_app/runtime/turns/stream_outcome.py` | `agent/application/turns/stream_outcome.py` | 流式终态优先级、协议终态归并和 `RunResult` 构建属于 application 结果聚合；协议事件只在边界输入，不持有 UI 或执行副作用 |
| `agent/harness/sessions/owner.py`、`loop.py` | `agent/ports/sessions.py` | Session runtime 的执行、取消、恢复和关闭契约归入 ports；Harness 只提供实现，application 通过显式 factory 使用，不直接装配 owner |
| `agent/harness/workspace_runtime.py` | `agent/ports/workspace.py` | 工作区资源生命周期和组合工厂契约归入 ports；Harness 只持有具体资源替换/关闭实现，路径由组合边界解析 |
| `mind_app/runtime/support/idle_status.py` | `infrastructure/platform/idle_status.py` | asyncio 延迟状态计时器只管理平台任务生命周期；stream runtime 通过显式平台实现使用，不让 support 目录继续承接无归属基础设施 |
| `mind_app/runtime/support/rwlock.py` | 已删除 | 全仓库无生产或测试调用者；删除死代码，避免保留未接入 Harness 的并发抽象和伪迁移入口 |
| `mind_app/runtime/tools/notify.py` | `mind_app/runtime/mcp/tool_progress.py` | MCP 工具进度通知依赖 MCP 调用生命周期，归入 MCP runtime 适配边界；工具路由只调用该边界，不在平铺 tools 包维护通知实现 |
| `mind_app/runtime/tools/policy.py` | 已删除 | 全仓库无生产或测试调用者；不迁移无归属的并行策略死代码，避免形成新的兼容入口 |
| `mind_app/runtime/hooks/runtime.py` 中的 `HookCommandRunner`、`HookContextSpiller` | `agent/ports/hooks.py` | Hook 命令执行和上下文 spill 是 runtime 调用具体实现的端口；协议值对象只声明已校验结果字段，状态展示端口仍由 runtime 持有 |
| `HookRegistry`/`HookRuntime` 的执行器资源推断 | 显式 `context_spiller`、`cleanup_session`、`close` 注入 | Hook 执行、超限 spill 和资源清理按端口绑定；runtime 不通过 `isinstance` 猜测具体执行器能力，默认执行器仅在构造分支集中绑定 |
| `HookRegistry` 在 CLI/MCP/Controller 内的隐式构造 | `agent.ports.HookRegistryFactory`，由 `mind.py` 注入 `RuntimeServices` | 具体 registry 只在进程组合根创建；入口、Controller 和 Hook scope 仅依赖 registry/dispatcher/status port，不反向导入 runtime 实现 |
| `mind_app/runtime/hooks/models.py` | `agent/application/hooks/models.py` | Hook 生命周期快照、决定、输出和工具结果是跨 runtime/TUI 的 application contract；Hook 执行器、注册器和 scope 仍由 runtime 持有，不把执行副作用放入值对象 |
| `mind_app/runtime/hooks/scope.py` 中的 `HookExecutionContext` | `agent/application/hooks/context.py`；`HookExecutionScope` 归 `agent/harness/hooks/scope.py` | Hook 输入上下文只依赖 Turn、domain 事件名和 schema 构建；执行作用域持有 Harness dispatcher 和生命周期，不把具体执行器带入 application |
| `mind_app/runtime/turns/executor.py` 中的 `TurnExecution` | `agent/application/turns/execution.py`；`HookExecutionScopePort` 归 `agent/ports/hooks.py` | Turn 执行值对象只依赖固定 scope 端口；runtime executor 保留模型执行函数和具体 scope 构造，不让 application 加载 HookRuntime |
| `mind_app/runtime/mcp/contracts.py` 中的 `McpSessionLike` | `agent/ports/mcp_session.py` 的 `McpSessionPort` | MCP 会话能力是工具执行跨层端口；runtime/mcp 只实现 Composite session，工具、Turn、Subagent 和 TUI 通过 ports 依赖，不把 runtime contract 当作公共接口 |
| `mind_app/runtime/turns/executor.py` 中的 `TurnResult`、`TurnOperation` | `agent/ports/turns.py` | 模型轮次操作只依赖 MCP 会话、事件报告和 TurnExecution；runtime executor 只负责会话生命周期、工具过滤和结果收束 |
| `mind_app/runtime/turns/root.py` 中的 `RootTurnCommandExecutor` | `agent/adapters/turns/root.py` | 冻结命令到根轮次参数的映射属于入站 adapter；CLI、MCP 和 Subscription 在入口绑定 controller，adapter 不依赖旧控制器或前端生命周期 |
| `mind_app/runtime/subagents/executor.py`、`runner.py` 中的执行协议 | `agent/ports/subagents.py` | 子 Agent 执行与操作端口和具体流式适配分离；runtime runner 只负责 Hook 生命周期、续跑和停止决定 |
| `mind_app/runtime/hooks/subagent.py` | `agent/application/hooks/subagent.py` | 子 Agent Hook 事件聚合只依赖 scope 端口和 application 结果模型；runtime 不再拥有生命周期业务规则 |
| `mind_app/runtime/subagents/runner.py` | `agent/harness/execution/subagent_runner.py` | SubagentRunner 只接收 Turn runner 与 cleanup 端口，Harness 负责 Hook 停止决定和续跑；不持有 Mind Controller，避免 runtime/application 反向耦合 |
| `mind_app/runtime/subagents/executor.py` | `agent/adapters/agents/execution.py` | 流式 Subagent 执行器只实现 `SubagentExecutionPort`，通过 `SubagentStreamPort` 注入 stream；Controller、静默输出和具体 runtime stream 绑定留在 runtime 组合处 |
| `mind_app/runtime/hooks/protocol.py` | `agent/application/hooks/protocol.py` | Hook 进程 stdin/stdout schema、构建和校验属于 application boundary；runtime 只调用已校验的契约，不把内部 Hook 协议误并入线上 `protocol/` |
| `mind_app/runtime/hooks/catalog.py` | `agent/application/hooks/catalog.py` | Hook 管理目录、不可变状态快照和内容冲突错误属于 application contract；Controller/TUI 只消费该契约，注册器仍负责运行时装配 |
| `mind_app/runtime/hooks/matching.py` | `agent/domain/hook_matching.py` | Hook matcher、工具 canonical 名称和别名候选属于纯领域规则；不依赖 application、runtime 或平台实现 |
| `mind_app/runtime/tools/mode_policy.py` | `agent/domain/tool_policy.py` | app/api 工具可见性、隐藏规则和内联元数据过滤属于纯领域策略；入口与 runtime 只消费策略函数 |
| `mind_app/runtime/hooks/events.py` | `agent/application/hooks/events.py` | Hook 生命周期事件规格和目录一致性校验属于 application contract；规范化实现通过同层 `hook_output` 注入，不反向依赖 runtime |
| `mind_app/runtime/hooks/effects.py` | `agent/application/hooks/output.py` | Hook 输出 schema 后的语义校验、决定归一化和业务阻断结果属于 application contract；不持有外部效果或执行器副作用 |
| `mind_app/runtime/hooks/results.py` | `agent/application/hooks/result.py` | 后置 Hook 的工具结果替换、反馈和上下文投影属于 application contract；runtime tool 只负责协调调用 |
| `mind_app/native_coding/exec/execpolicy/` | `agent/domain/execution_policy/` 与 `infrastructure/config/execution_policy.py` | 执行策略决定、规则和值对象属于纯 domain；规则文件 AST/文件读取属于配置基础设施，native coding 只组合二者，不让策略域持有 IO |
| `mind_app/native_coding/exec/exec_policy.py` | `infrastructure/config/execution_policy_manager.py` | 本地规则发现、审批缓存和策略评估属于配置基础设施；native coding 只消费已构建的策略管理器，不再拥有策略生命周期 |
| `mind_core/application_paths.py` | `infrastructure/config/paths.py` | 应用入口、打包模式、本地资源目录和用户数据目录解析属于配置基础设施；不把路径环境事实放入策略模块 |
| `mind_core/agent_config.py`、`mind_core/feature_config.py` | `agent/application/config/settings.py` | Agent 并发限制和可选能力开关是应用启动设置；通过 application 公开入口提供，不让配置包持有运行设置模型 |
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
| `agent/ports/capabilities.py`、`agent/adapters/protocol/client.py` | `ports`、`adapters/protocol/client.py` | `ModelCapabilityError` 统一传输/协议失败，`ProtocolModelEventStream` 负责坐标门禁、current/active/audit Items、canonical 正文/sources、异步迭代、幂等关闭及结算后游标提交；错误码、重试性和 JSON 细节由 Run 终态及 `run_failed` 事件保留 |
| 已删除的 `mind_app/runtime/environment/exec_env.py`、旧 environment 请求模块 | `capabilities/environment.py`、`protocol/schema/environment.py` | 本机事实采集和 Helix provider 聚合已迁入进程级注入的 `EnvironmentSnapshotCapability`；线上 schema 与规范化归属 `protocol.schema`。四类入口在命令持久化前冻结快照，model adapter 只在 wire 边界映射 `exec_env` |
| `agent/protocol/capabilities.py`、`agent/ports/capabilities.py` | `protocol`、`ports` | MCP 工具值对象、Helix 生命周期、受控进程/文件和本地 sandbox 权限只通过具名 port 表达；不把 SDK 会话、进程句柄或操作系统路径带入 domain |
| `agent/capabilities/mcp.py`、`helix.py`、`process.py`、`filesystem.py` | `capabilities` | MCP/Helix 内存替身和本地进程/文件实现均可脱离网络、TUI 和 legacy runtime 测试；完整权限进程与 Helix 已由显式 adapter 接入，受限进程继续接受显式 sandbox launcher |
| 已删除的 `mind_app/runtime/turns/delivery.py` | `agent/adapters/protocol/client.py` | Session 跨 Turn 的 `event_seq` 水位已迁入独立 Protocol Client；控制器和前端不再持有，也不与本地 Run sequence 合并 |
| `mind_core` 配置、权限、hooks、skills | `domain/policies.py`、`application/`、`infrastructure/config`、capability adapters | 配置读取、策略判断和技能/Hook 生命周期拆开，禁止形成新的共享杂物包；完成后删除 `mind_core` |
| `frontends/cli`、`frontends/tui`、`frontends/mcp`、`frontends/subscription` | `frontends/`、`application/`、Protocol Client | CLI、TUI、MCP、Subscription 四类入口均通过 `RuntimeServices` 接收 application；执行命令已冻结并提交统一入口，CLI 的根轮次 runner/环境快照和 MCP 的对应能力均由 `mind.py` 显式注入；终态观测和回执优先使用 Run/Canonical Event projection；TUI 作为 Protocol Client adapter，桌面/Web 通过同一 fixture 校验协议投影；剩余 UI/控制器依赖迁出后删除 `mind_app` |
| `mind_app/tui`（已删除） | `frontends/tui` | TUI 输入、会话、渲染和展示 runtime 作为一个可替换前端整体迁移；内部状态仍由 TUI adapter 管理，Harness、Controller 和 Protocol Client 状态不随目录迁移，避免 `frontends -> mind_app -> frontends` 包级循环 |
| `frontends/tui/session/turn_input.py`、`frontends/tui/session/loop.py`、`frontends/cli/dispatch.py` | `frontends/` + CLI bootstrap 组合根装配 | TUI 输入控制器、TUI durable `TurnApplication` 和 CLI durable exec 均只消费显式注入的 Protocol Client/factory；前端 session/dispatch 不反射发现 `Mind.runtime_services`，输入对账、关闭收敛和短生命周期 application 仍由各自入口持有 |
| `frontends/tui/features/conversation.py`、TUI `/fork`/backtrack 调用链 | `frontends/tui` + session 组合边界 | 会话分支 feature 只消费显式 `ProtocolCommandClient`；请求幂等、源缺失恢复和本地测试 seam 保留在 feature 边界，不从 `Mind` 反射读取运行时服务 |
| `mind_app/tui/contracts`（已删除） | `frontends/tui/contracts` | TUI 的菜单、分页、Resume、文本片段、Transcript 和视图契约是无副作用的前端展示输入；只依赖标准库和同包类型，TUI/CLI/测试统一从新路径导入 |
| `mind_app/runtime/mcp/server.py` | `frontends/mcp/server.py` | stdio MCP 入站适配器整体迁移；`mind_exec` 通过注入的 `TurnApplication`、`RootTurnCommandExecutor`、根轮次 runner 和环境快照 provider 提交 `SubmitTurnCommand`，structured content 优先使用 `RunResultProjection`；MCP runtime 不拥有控制器或模型生命周期，CLI 由组合根注入 runner |
| `mind_app/subscription/forwarding.py`、`subscription/runtime.py` | `frontends/subscription/`、`application` | Subscription HTTP/WS client、wire envelope、open/resume、收件箱转发和恢复由 `frontends/subscription` 持有；`AgentExecutor` 将远端 forward 冻结为稳定 `SubmitTurnCommand`，根轮次执行器与环境快照提供器由组合根注入，长驻 application 由 `AgentRuntime` 拥有并在 shutdown 关闭；Harness 通过 `agent/ports/subscription.py` 管理生命周期，完成/失败/中断分类使用 application 的 Event projection |
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
