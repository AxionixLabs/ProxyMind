# Agent Runtime 架构基线

状态：已采纳（Architecture Decision Record）；实施处于阶段 1

这份文档是 ProxyMind 下一代 Agent Runtime 的目标架构。它解决的是
`mind_app`、`mind_core`、`mind_nova` 三个历史包职责交叉、状态所有权不清和
副作用难以恢复的问题；它不改变 Mind/Helix 的产品边界，也不改动独立打包的
`backend/`。

## 文档权威与当前边界

- 本文档是 ProxyMind Agent Runtime 的目标 ADR，不表示目标目录已经存在。
- `AGENT_RUNTIME_MIGRATION.md` 是阶段状态的唯一权威来源；准备性拆分
  不能自动计入后续阶段。
- `AGENTS.md` 定义当前可执行的生产依赖规则。迁移计划没有启用
  相应阶段时，继续遵守 `mind_app -> mind_core -> mind_nova` 边界。
- 阶段 0 基线已于 2026-08-28 通过，阶段 1 从主动 `exec` 用例开始引入
  顶层 `agent` 包、`SessionLoop` 和 `RunActor`；其他入口仍遵守当前包边界。

### 本地运行时与线上协议身份

本 ADR 中的 `Run` 是 ProxyMind 本地可恢复执行单元，不替换已有线上
`cid` / `sid` / `turn_id` / `attempt` / `item_id` / `event_seq` 身份。迁移不得：

- 为本地 `Run` 另建一套对外会话或 Item 协议；
- 将本地事件序号冒充为服务端 `event_seq`；
- 重命名或重新解释已稳定的线上协议字段。

本地 Command/Event 必须显式携带所属的线上坐标，并在 adapter 边界完成
本地 `run_id` 与线上 `turn_id` / `attempt` 的映射。

## 决策结论

采用 **Codex-inspired Session Runtime + ProxyMind Durable Effects**：

- 借鉴 Codex-main 的类型化 `Op/Event` 协议、Submission Queue / Event Queue、
  Session 单一状态所有者、独立存储包和小型公开 API。
- 保留 ProxyMind 的端侧确定性执行、Helix MCP、外部 MCP、审批、订阅和
  `LocalEffectJournal`，并将副作用统一纳入效果日志与事务性 Outbox。
- 用一个 `agent` bounded context 取代 `mind_app / mind_core / mind_nova` 的
  语义分层。目录按职责和依赖方向拆分，不再按历史包名拆分。
- `mind.py` 仍是稳定入口；`backend/` 仍是独立打包边界。两者不是 Agent Runtime
  的内部模块。

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

## 目标目录

目标是一个顶层 `agent` 包。下面的文件名是职责边界，不要求一次性全部创建；
迁移时应按阶段启用，避免建立未接入主链路的空壳模块。

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
├── runtime/
│   ├── session_loop.py      # 一个 Session 的单写者事件循环
│   ├── run_actor.py         # 一个 Run 的串行状态执行器
│   ├── scheduler.py         # Run 调度、并发上限和公平性
│   ├── supervisor.py        # 子任务、断线和关闭收束
│   └── recovery.py          # 快照恢复、未完成命令和效果对账
├── application/
│   ├── commands.py          # submit、resume、approve、cancel、retry
│   ├── queries.py           # 历史、状态、计划和证据读取
│   ├── projections.py       # Event Queue 到入口稳定结果的投影
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
│   └── filesystem.py        # 受控文件能力
├── adapters/
│   ├── cli.py               # CLI 输入/退出码到 Command
│   ├── tui.py               # TUI 输入和 Event 投影
│   ├── mcp_server.py        # stdio MCP 入站协议
│   ├── subscription.py      # 远端订阅、resume 和 mailbox
│   └── observability.py     # 日志、指标和 tracing 出站适配
└── composition.py           # 唯一组合根
```

### 依赖方向

```text
adapters ───────────────> application ──> runtime ──> domain
                              │              │          │
                              └──────────────┴──────> protocol

capabilities ──> ports <── runtime / application
stores ────────> ports <── runtime / application
composition.py ──────────> all concrete implementations
```

具体规则：

- `protocol` 只使用标准库类型和可序列化值，不导入 `engine`、配置、网络客户端或
  任意适配器。
- `domain` 是纯状态和规则，不执行 IO，不启动任务，不读取环境变量。
- `runtime` 只负责并发、生命周期和状态提交；模型、MCP、Helix 等均通过
  `ports` 的具名端口进入，`capabilities` 只提供这些端口的实现。
- `application` 组合用例，但不直接持有 Session 的可变状态，也不依赖具体能力实现。
- `ports` 只定义跨边界的最小 Protocol/ABC、生命周期和实现约束，不包含业务规则。
- `stores` 负责持久化事实与游标；具体 SQLite、文件或远端实现放在其内部适配器，
  通过 `ports.persistence` 接入，不能把数据库细节泄漏到 domain。
- `adapters` 只翻译输入和输出，不判断模型策略，不直接调用工具。
- `composition.py` 是唯一允许组装配置、端口、存储和运行时的模块。
- `engine` 只能作为低层平台能力被 capability adapter 使用，不能成为新的业务层。
- `server` 若继续独立部署，只能依赖 `agent.protocol` 和明确的 application API。
- `backend/` 只能依赖自身、标准库和第三方库；Agent Runtime 不得导入它。

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

## 能力与适配器

### 入站适配器

- CLI 把参数和 stdin 转为 `submit_turn` 等命令，并把 Event 映射为退出码和文本。
- TUI 只订阅 Event 投影，不拥有 Run 状态，不直接调用模型或工具。
- MCP Server 只做协议解析和响应流控制；每个请求进入同一 Command Gateway。
- Subscription 只负责 open、WebSocket、resume、去重和 mailbox；收到远端任务后
  入队，不在 WebSocket 回调里启动模型轮次。

### 出站能力

- Model capability 返回类型化的流事件，不返回 UI 对象。
- MCP/Helix capability 只暴露工具发现、调用和生命周期结果。
- Process/filesystem capability 复用现有 `engine.ports` 等低层能力；端口探测和
  进程清理不下沉到协议包。
- Observability capability 接收结构化事件，不要求 domain 直接写 stdout/stderr。

## 当前实现映射

| 当前位置 | 目标归属 | 迁移要求 |
| --- | --- | --- |
| `mind_app/controller.py` | `composition.py`、application 公开门面 | 不再新增长期运行状态，已有所有权按完整生命周期迁出 |
| `mind_app/runtime/turns/root.py` | `application/commands.py` | CLI `exec` 已由类型化 Command 驱动；TUI、MCP、Subscription 仍把它作为过渡能力入口 |
| `mind_app/runtime/turns/stream.py` | `runtime/session_loop.py`、`application/turn_pipeline.py` | 输入/输出准备、终态结果、模型正文投影和工具结果交付已拆到 `stream_setup.py`、`stream_outcome.py`、`stream_model.py`、`stream_effects.py`；继续拆回合级展示和清理 |
| `mind_app/runtime/mcp/*`、`subscription/lifecycle.py`、`runtime/environment/coding_lifecycle.py` | capabilities、adapters、runtime supervisor | 保留已收敛的资源所有权，迁移时按端口而非按文件直接搬运 |
| `mind_app/runtime/subagents/control.py` | `runtime/scheduler.py`、`domain/agents.py` | 将 mailbox、生命周期和图持久化分开 |
| `mind_app/runtime/subagents/graph.py` | `stores/agent_graph.py` | 保留检查点语义，存储实现不得进入 domain |
| `mind_app/runtime/durable_effects.py` | `stores/effect_journal.py`、`stores/outbox.py` | 作为现有效果状态机的正式落点，不降级为日志工具 |
| `mind_nova/requests`、`stream_events.py` | `protocol/`、`capabilities/model.py` | 请求/事件类型与传输实现分开，协议不得导入 `engine` |
| `mind_core` 配置、权限、hooks、skills | `domain/policies.py`、`stores/`、capability adapters | 配置读取和策略判断拆开，禁止形成新的共享杂物包 |
| `mind_app/cli`、`tui`、`mcp`、`subscription` | `adapters/` | 只做边界翻译和生命周期接入 |
| `engine` | capability 的基础实现 | 保持平台基础设施定位，禁止反向依赖 Agent 业务 |
| `server` | 独立入站/服务适配器 | 只依赖协议和显式 application API |
| `backend` | 独立打包 | 本次和后续迁移均不修改其目录和依赖边界 |

## 不采用的方案

- 不把 `mind_app`、`mind_core`、`mind_nova` 简单改名为 `control_plane`、
  `shared`、`protocol`。这只改变目录，不解决状态所有权和副作用恢复。
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
6. `backend/` 无来自 Agent Runtime 的导入，Agent Runtime 也不导入 `backend`。
7. 公共 API 只从 `agent.protocol`、`agent.application` 和必要的 capability port
   导出；内部模块默认私有。
8. 新增模块原则上不超过 500 行；超过 800 行必须拆分职责并补充边界说明。
9. 文档、协议和 CLI 变更经过对应的兼容性评审，不以批量目录移动代替迁移。

## 版本与兼容

本架构允许一次有计划的 breaking change，但必须先冻结以下外部契约：

- `mind.py` 启动方式和主要 CLI 命令；
- MCP 工具名称、输入 schema 和结果结构；
- 订阅 open/ws/resume 消息和去重语义；
- 会话、任务、报告和证据的标识规则；
- 配置文件和历史记录的迁移策略。

旧包删除的条件是：所有入站适配器已经使用 `agent.application`，所有出站能力
已经从旧包解耦，定向测试和恢复测试通过，并且没有生产导入继续指向旧包。此前
不进行物理删除，只允许有期限、有追踪项的迁移入口。
