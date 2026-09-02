# Agent Harness 最终架构

状态：现行架构决策（2026-09-02）

本文是 ProxyMind 客户端 Agent Harness 的唯一架构权威，定义职责归属、依赖方向、
状态所有权、生命周期和验收边界。需求、修复与重构必须先符合本文，再进入实现。

线上 `mind.chat` 的字段、端点、事件、错误和恢复语义只以根目录 `PROTOCOL.md` 为准。
本文不复制服务端状态机，也不改变 `backend/` 的独立打包边界。

## 架构目标

Agent Harness 必须同时满足以下约束：

- 可维护：领域规则、应用用例、运行编排、外部实现和展示各有唯一所有者。
- 可扩展：模型、工具、审批、持久化、传输和前端通过具名端口替换，不修改 Session 单写者或终态规则。
- 可恢复：Command、Run、外部 Effect 和线上游标具有稳定身份，未知结果进入显式对账。
- 可观测：结构化事件在职责边界产生，业务模块不直接创建标准库 logger，也不从日志文本推断状态。
- 可移植：路径、Shell、子进程和信号行为默认兼容 Windows、Linux、macOS，平台差异只在 infrastructure adapter 内出现。
- 可验证：依赖方向、公开 API、生命周期和协议边界由自动化守卫锁定。

## 系统边界

```text
mind.py
  -> composition.py
      -> agent
      -> protocol
      -> infrastructure
          -> sidecars
      -> observability
  -> frontends

frontends
  -> agent application/ports
  -> protocol client
  -> infrastructure adapters

infrastructure
  -> agent application/domain/ports/stores
  -> protocol schema
  -> observability
```

顶层职责固定如下：

| 边界 | 唯一职责 | 不得拥有 |
| --- | --- | --- |
| `mind.py` | 稳定进程入口、具体工厂选择和最终启动 | 领域规则、前端状态、隐式服务定位 |
| `composition.py` | 组装 `ApplicationHost`、进程资源和公开能力 | 业务分支、协议解析、UI 逻辑 |
| `agent/` | 本地 Agent Harness 的领域、用例、编排、端口和本地事实 | 具体 UI、配置文件路径、HTTP 实现 |
| `protocol/` | 可供多前端复用的 `mind.chat` wire SDK | Harness 生命周期、本地 UI 或配置策略 |
| `frontends/` | CLI、TUI、stdio MCP、Subscription 和终端展示适配 | Run/Effect 权威状态、具体能力组装 |
| `infrastructure/` | 配置、平台、MCP、持久化、服务、技能和工作区实现 | 前端状态、线上 Turn 权威状态 |
| `sidecars/` | 随客户端发布的隔离子进程入口和私有运行时资产 | Harness 编排、审批决策、线上协议语义 |
| `observability/` | 结构化日志、报告和异常观测入口 | 业务状态机、用户交互策略 |
| `metadata/` | 产品名称、版本、编码和展示元数据 | 运行状态、配置读取 |
| `server/` | 客户端内置的可选配置 UI 与健康检查 | Harness 状态、`mind.chat` 服务端语义 |
| `backend/` | 独立打包服务 | 对客户端包的反向依赖 |

## Agent 包结构

```text
agent/
├── protocol/       # 进程内 Command/Event/Item/能力值，不是 wire SDK
├── domain/         # 纯领域规则、身份、状态转换、权限和补丁模型
├── ports/          # 跨职责的最小能力契约
├── application/    # 用例、执行上下文、稳定结果和展示 projection
├── harness/        # Session/Run/Agent/Hook/Tool 的并发与生命周期编排
├── stores/         # Run、Agent、Session、Transcript、Approval、Effect 事实
├── capabilities/   # 通过端口提供的本机能力
├── adapters/       # 线上协议和外部 Agent 的映射
└── composition.py  # 仅组合 agent 内部能力，不导入 infrastructure
```

### `agent.protocol`

负责进程内、与传输无关的类型化契约：

- `SubmitTurnCommand` 等 Command；
- `RunEvent`、事件类别和稳定顺序；
- `CanonicalItem` 等 Harness 内部投影；
- 模型能力及不可变 JSON 值。

它不是 HTTP、SSE 或 WebSocket schema。不得导入 `protocol.client`、具体网络客户端、
配置、持久化、前端或平台实现。

### `agent.domain`

负责无 IO 的业务规则和值对象，包括 Run 状态、身份、权限组合、执行策略、工具可见性、
Hook 匹配、Transcript 合并和补丁语义。领域函数只依据显式输入返回决定，不读取环境、
文件、数据库或当前前端。

领域层可以复用稳定的 wire 值枚举，但不得发起协议请求或依赖具体 adapter。

### `agent.ports`

负责跨层契约。端口应按一个可替换能力或生命周期命名，参数与返回值必须准确描述真实
契约。端口不得成为聚合所有能力的 Service Locator，也不得为单个函数增加等价 facade。

跨层对象满足以下要求：

- 使用 `Protocol`、ABC、dataclass 或具名结果；
- 生命周期由创建方关闭，消费者不得猜测实现类型；
- 可空依赖只表示真实可选能力，不能作为未注入时的兼容回退；
- 端口实现失败返回稳定结果或抛出职责明确的异常，不通过动态属性传递状态。

### `agent.application`

负责调用方可见的用例和投影：Turn 提交、环境快照、工具执行、审批协调、Hook 输入输出、
Agent 消息、RunResult 和 PresentationView。它协调领域规则与端口，但不拥有事件循环、
数据库连接、网络会话或 UI 控件。

`agent.application` 的包级公开面保持小型；消费者从职责模块导入内部值对象，不通过根包
暴露所有实现。

### `agent.harness`

负责长生命周期和并发所有权：

- `sessions/`：Session 单写者、Command 队列、恢复和关闭；
- `execution/`：Run actor、根 Turn、Subagent、压缩、终结和 Sidecar 等执行资源；
- `agents/`：Agent 树、活动 Turn、mailbox 投递和根会话注册；
- `hooks/`：Hook 作用域、注册、调用顺序和收束；
- `tools/`：客户端工具、计划工具和调用闭环；
- `mcp/`、`subscription/`：对应长驻运行资源的 owner；
- `process_lifecycle.py`、`process_resources.py`：进程关闭顺序和失败收敛。

Harness 可以协调 application 用例和 ports，不得读取具体配置文件、构造 HTTP client、
创建 UI 对象或把本地 Run 状态冒充为线上 Turn 状态。

### `agent.stores`

负责本地权威事实和 CAS/幂等边界。按 `agents`、`approvals`、`effects`、`runs`、
`sessions`、`transcripts` 分组。Store 接收已解析的路径或连接，不自行发现应用目录；
文件和数据库 IO 的具体媒介可由 infrastructure 提供。

Redis、缓存和内存视图只能作为可重建镜像，不得覆盖持久事实。

### `agent.capabilities` 与 `agent.adapters`

Capabilities 实现本机文件、进程、环境、MCP、Helix 等端口，不读取前端状态。
Adapters 负责边界映射：

- `adapters/protocol` 将 Harness 请求映射到顶层 wire SDK，并维护线上游标和
  Canonical Item reducer；
- `adapters/agents` 连接子 Agent 执行、消息和 fork context；
- `adapters/turns` 连接稳定入口与根 Turn 用例。

Adapter 可以隔离第三方无类型数据，但必须在边界完成校验，内部只传递具名类型。

## 依赖规则

依赖只能朝向职责更稳定的一侧：

```text
domain / agent.protocol
        ^
        |
      ports
        ^
        |
   application
        ^
        |
     harness

stores / capabilities / adapters / infrastructure / frontends
        -> 通过上述公开契约接入
```

规则解释：

1. `domain` 与 `agent.protocol` 不依赖 application、harness、stores、capabilities、
   adapters、infrastructure 或 frontends。
2. `application` 不依赖 harness、stores、capabilities、infrastructure 或 frontends。
3. `harness` 通过 application、domain、protocol 和 ports 组织生命周期，不导入前端。
4. `agent.composition` 只组装 agent 内部对象，不导入 infrastructure。
5. `infrastructure` 可以实现 agent ports，也可以消费 application 的具名工具契约；它不
   反向控制 Harness 生命周期。
6. `frontends` 只调用 application/ports/protocol 或显式注入的 infrastructure adapter，
   不构造模型、Store、MCP runtime 或进程 owner。
7. `protocol` 不依赖 `agent`、frontends、infrastructure、server 或 backend。
8. `backend` 只依赖自身、标准库和第三方库；客户端代码不导入 backend。
9. `sidecars` 不导入 Python 业务包；只有 `infrastructure.sidecars` 可以解析、启动并通过
   私有 IPC 驱动对应宿主，其他包只依赖 agent ports。

禁止通过 `typing.cast()`、`Any` 扩大、动态 `getattr`、模块级可变替身或可空 fallback
绕过这些方向。测试替身从组合边界显式注入。

## 组合与生命周期

`mind.py` 和 `composition.py` 共同构成唯一具体组合边界：

1. `mind.py` 选择应用目录、平台实现、模型/MCP/工具工厂和前端入口。
2. `agent.composition.create_runtime_services` 创建与 UI 无关的 Harness 服务集合。
3. `composition.py` 组装 `ApplicationHost`、Session、执行资源、持久化和服务 owner。
4. CLI/TUI/MCP/Subscription 接收已组装的 host、factory 或 port。
5. `ProcessResourceOwner` 按确定顺序关闭前台 Turn、后台任务、MCP、Sidecar、服务、
   Sandbox、Store 和观测资源；清理失败必须可观测且不得跳过后续资源。

业务模块不得从 `Mind`、Controller、frontend 或全局变量反射发现能力。需要的新能力应先
形成最小端口，再由组合边界注入完整调用链。

## Command、Session 与 Run

### 身份

本地身份和线上身份必须分开：

| 范围 | 稳定身份 | 序号/幂等键 |
| --- | --- | --- |
| 本地 Harness | `session_id`、`run_id` | `command_id`、`idempotency_key`、Run 内 `sequence` |
| 线上协议 | `cid`、`sid`、`turn_id`、`attempt`、`item_id` | `request_id`、`client_message_id`、`event_seq` |
| Subscription | 订阅 `session_id`、任务 `call_id` | `message_id`、订阅 `seq`、`last_acked_seq` |

三组序号不得互相赋值或比较。Adapter 显式保存映射，不依赖字符串碰巧相等。

### 命令入口

CLI、TUI、stdio MCP 与 Subscription 最终都把主动执行映射为同一 application Command：

```text
Inbound adapter
  -> immutable Command
  -> Session command queue
  -> RunActor
  -> model / tool / approval ports
  -> durable event + projection
  -> frontend adapter
```

Command 必须在入队前冻结完整语义与 `exec_env` 快照；重试和安全 redispatch 复用原快照，
不能重新读取当前进程环境。相同幂等键和相同语义返回既有结果；不同语义必须冲突。

### 单写者

每个 Session 只有一个状态写入者。调用方提交 Command，不直接修改 Session、Run、计划、
工具或审批状态。并发输入、取消、审批和恢复都经由队列或具名 mailbox 协调。

Run 的状态转移由领域规则和 actor 共同约束。终态不可离开；连接关闭、局部展示完成或
异常文本不能代替逻辑结算事实。

## 线上协议边界

`protocol/` 是独立 wire SDK：

```text
protocol/schema     # 严格字段、判别联合、身份和值约束
protocol/transport  # 认证、端点、可靠请求、SSE 与报告传输
protocol/client     # chat、turn control、tool、effect、fork、compact 等用例
```

TUI、桌面端和 Web 可以复用同一线上协议，但不要求共享 Python UI。可替换前端只需要：

- Protocol Client：命令、attach/replay、游标和可靠交付；
- Canonical Item reducer：按 `event_seq` 去重并处理 retry/presentation 替代；
- 本地能力 adapter：工具、审批、附件和展示。

`agent.protocol` 的本地 Event 不得暴露为浏览器或远程 SDK；`protocol` 也不得拥有本地
SessionLoop、RunActor、工具执行器或前端生命周期。

线上 `event_seq` 在 `cid + sid` 范围内跨 Turn 单调。Protocol Client 只在完整处理后推进
确认游标，并以 `turn.logical_settled` 结束逻辑轮次。`turn.done`、`turn.failed`、SSE 关闭
或前端退出不能替代结算。

## Canonical Item 与展示

正式可展示事件先由 `CanonicalItemReducer` 归约，再交给前端：

- active 视图只包含未被 provider retry 或 `presentation.superseded` 替换的 revision；
- audit 视图保留所有展示 attempt；
- 最终 `assistant_text` 只从 active text Items 派生；
- approval snapshot 只裁决旧审批状态，不推进客户端确认游标；
- `stream.gap` 等控制信号不创建 Item；
- sources、工具、审批和正文归属不能由各前端重复实现。

Application 产出与 UI 工具包无关的 PresentationView。Terminal/TUI/未来桌面端只负责布局、
交互和渲染，不从异常文本或原始 provider payload 重建业务语义。

## 工具、审批与 Effect

工具调用的职责链固定为：

```text
model intent
  -> application tool contract
  -> execution policy
  -> approval coordinator when required
  -> durable effect preparation
  -> capability/infrastructure execution
  -> typed result
  -> protocol delivery and reconciliation
```

约束如下：

- 本地工具、provider built-in tool 和 hosted tool 是不同边界，不通过字段猜测互换。
- 工具参数在执行前完成 schema 校验，结果在 adapter 边界归一化。
- 审批只决定当前动作，不成为底层平台或服务端的全局安全策略。
- Approval、Tool Call 与 Effect 各有稳定 identity，`request_id` 只承担传输幂等。
- 外部效果成功与本地提交之间存在未知窗口时进入 reconciliation，不伪装为普通失败。
- 不可重放效果不得由接管 actor 自动重试；只读或有供应商幂等保证的效果按明确策略恢复。
- `/tool-result` 只发送正式协议字段，本地工作区、sidecar 或 UI 状态不能混入 wire payload。

## 持久化与恢复

持久状态按事实类型分开：

| 事实 | 所有者 | 恢复原则 |
| --- | --- | --- |
| Run/Command/Event | `agent.stores.runs` | 幂等写入、单调事件、终态不可离开 |
| Agent graph/mailbox | `agent.stores.agents` | 活动投递可恢复，消息身份稳定 |
| Session cursor | `agent.stores.sessions` | 只保存本地会话索引和分支事实 |
| Transcript | `agent.stores.transcripts` + persistence adapter | 规范记录与 IO 分离，损坏可观测 |
| Approval | `agent.stores.approvals` | 首个决定权威，重复请求幂等 |
| Effect | `agent.stores.effects` | prepared/committed/unknown 等事实可对账 |

恢复必须从持久事实和安全点开始，不能以 Redis、当前连接、前端缓存或日志作为 authority。
进程退出后，已提交事件不丢失，未确认外部效果不重复执行，无法确定的结果进入显式对账。

## 基础设施边界

`infrastructure/` 按外部变化来源分组：

- `config/`：应用目录、配置 schema、分层、偏好、信任和执行策略文件；
- `platform/`：进程、Sandbox、Shell、信号、编码、图片和平台差异；
- `workspace/`：工作区命令、补丁应用、diff 和运行资源；
- `mcp/`：外部/本地 MCP session、工具目录、调用和结果适配；
- `persistence/`：Transcript 与会话索引的具体存储；
- `services/`：可选服务 owner、健康、许可、Helix 与 Turn 环境；
- `skills/`：技能发现、解析和不可变 payload；
- `hooks/`：Hook 文件发现；
- `sidecars/`：私有子进程协议、进程托管、会话连接和资源关闭；
- `update/`：升级资产和运行流程。

Infrastructure 不得读取 TUI 控件、构造前端文案或修改 Harness 内部状态；它通过 ports、
具名 application 契约和返回值交互。

## JavaScript Sidecar

JavaScript REPL 是受 Harness 管理的本地执行能力，模型侧稳定工具名为 `js_repl` 和
`js_repl_reset`。它不是线上协议、Workspace 聚合能力或前端特性；工具授权、审批、
嵌套工具调用和结果投影仍由 application/Harness 裁决，Sidecar 只执行已经授权的代码并
返回具名结果。`kernel.js` 及其 `vendor` 目录是来自 Codex 的稳定、不可变运行时资产；架构
重组只调整资产位置和 Python 托管边界，不重写或拆解该运行时。

### 最终结构

```text
agent/
├── ports/
│   └── javascript.py             # 执行与会话生命周期端口
└── application/
    └── tools/
        └── javascript.py         # 模型工具、参数校验、授权和结果投影

infrastructure/
└── sidecars/
    └── javascript/
        ├── bundle.py             # 不可变资产清单、散列和路径校验
        ├── protocol.py           # 既有 JSONL 消息的具名模型和边界校验
        ├── process.py            # Node 发现、权限参数、启动和终止
        ├── session.py            # 执行串行化、请求关联和回调任务
        └── provider.py           # 会话索引、创建、重置和关闭

sidecars/
└── js_repl/                      # Codex 原始资产目录，内容不可变
    ├── kernel.js                 # 完整 Host、Runtime、转换和 JSONL 入口
    └── vendor/
        └── meriyah.umd.min.js
```

`agent.ports.javascript` 分离消费者所需的执行端口与 Harness 所需的会话生命周期端口，
不得重新形成包含 Shell、补丁、Workspace 和 JavaScript 的聚合接口。具体 Provider 由
`composition.py` 创建：application 工具只接收执行端口，Harness 资源 owner 只接收生命周期
端口，二者不发现或向下转换具体实现。

### 状态与进程所有权

- Provider 以 `session_id` 索引唯一活动 JavaScript 会话，并保存已经规范化的工作目录和
  `sandbox_mode` 安全信封。
- 每个活动安全信封拥有独立 Node 进程。不同 Session 不共享宿主，因为 Node 文件权限、
  工作目录和进程环境是进程级安全边界。
- 同一 Session 的工作目录或 `sandbox_mode` 发生变化时，必须先关闭原进程，再用新的不可变
  安全信封创建会话；不得在活动进程上扩大权限。
- Process 对象拥有子进程、stdin/stdout/stderr 和进程终止；Session 对象拥有执行锁、当前
  request 和 delegate callback；不可变 JavaScript Kernel 只拥有进程内的 REPL 变量与当前
  execution。
- 每个 Session 只有一个 execution 写入者。外部执行按 FIFO 串行进入 Host，delegate callback
  只归属于触发它的 execution；`reset` 和 `close` 是阻止后续执行越过的生命周期屏障。
- Workspace 能力只负责工作区命令、补丁和文件操作，不拥有 JavaScript 会话或清理回调。
- 根 Session、Subagent 和进程退出均通过同一生命周期端口关闭对应会话；关闭操作幂等，
  单个资源清理失败不得阻止其余资源收束。

### 私有 IPC

Python Client 与不可变 JavaScript Kernel 沿用现有 UTF-8 JSONL 协议，不增加握手、版本协商、
cancel、reset 或 shutdown 消息，也不增加包装进程。Client 只发送：

- `exec`：携带 `id`、`code` 和 `timeout_ms`；
- `run_tool_result`：按 `id` 返回嵌套工具结果；
- `emit_image_result`：按 `id` 返回图片接收结果。

Kernel 只返回：

- `exec_result`：按 `id` 返回执行结果；
- `run_tool`：按 `exec_id` 和 `id` 请求嵌套工具；
- `emit_image`：按 `exec_id` 和 `id` 请求附加图片。

`protocol.py` 对 Python 发出的消息执行具名构造，对收到的完整帧执行判别、字段、标识和大小
校验；未知类型、未知字段、非法 JSON、错误字段类型、越界帧和无法关联的执行结果均使当前
Session 失败并关闭进程，不能静默忽略或猜测。合法但已经离开活动 Cell 的迟到 delegate 按
既有契约返回 `js_repl exec context not found`，不恢复旧 execution。stdout 只承载协议帧；
stderr 只作为受限诊断输入，不拥有状态语义。

Kernel 没有控制面消息，因此执行超时、调用方取消、reset 和 close 均由 Python 终止整个
子进程，并使当前 request 与 delegate task 确定收敛；下一次执行按需创建新进程。Host EOF、
崩溃和残缺帧采用同一失效路径。

基础设施以具名失败类型向上层报告 `unavailable`、`protocol_error`、`execution_timeout`、
`cancelled` 和 `runtime_error`；application 依据类型构造工具结果，不解析异常文本、stderr
或进程退出文案。失败类型不直接成为线上协议错误码。

### 安全与边界

- `sidecars/js_repl` 只允许从原 `js_repl` 目录机械迁移。`kernel.js` 和
  `vendor/meriyah.umd.min.js` 的文件名、目录关系和字节必须保持不变；禁止拆分、重写、格式化、
  添加注释、转换行尾或注入握手。构建和测试以固定 SHA-256 校验该约束。
- Node 可执行文件、最低版本和 Sidecar bundle 路径由 composition 解析后以不可变值传入
  Provider；业务模块和 Kernel 不读取客户端配置目录。
- `sandbox_mode` 在启动参数中落实，平台差异只存在于 `process.py`。Sidecar 不自行放宽文件、
  网络或子进程权限。
- `delegate.call` 只是调用提案；Python application 必须重新执行工具可见性、schema、审批和
  Effect 规则，Host 无权绕过这些规则直接调用本地工具。
- Sidecar 内部状态不得进入 `/tool-result`、线上事件、Transcript 元数据或前端状态。
- Sidecar 模块只使用 `observability` 的结构化入口；代码正文、完整输出、凭据和未筛选回调
  载荷不得写入日志。

### 发布与验收

`sidecars/js_repl` 作为完整目录随 wheel、源码分发和独立可执行包发布，运行时路径解析不
依赖当前工作目录。发布验证必须先校验资产散列，再从安装产物启动真实 Kernel，完成跨 Cell
状态保持、reset 和关闭，不能只检查文件存在。

JavaScript Sidecar 边界只有在以下事实持续成立时才视为健康：

- JavaScript 运行时资产只位于 `sidecars/js_repl`，Python 进程实现只位于
  `infrastructure/sidecars/javascript`；
- 两个 JavaScript 资产与迁移前的固定 SHA-256 完全相同，且不存在复制品、包装 Host 或拆分
  后的运行时文件；
- application 工具、Harness 生命周期和 Sidecar 实现通过两个最小端口协作；
- Session 隔离、权限冻结、取消、超时、崩溃、EOF、重置和关闭具有确定行为；
- 嵌套工具调用完整经过现有授权、审批和 Effect 链路；
- Windows、Linux、macOS 的 Node 发现、启动、终止和打包安装路径均有验证；
- 架构守卫禁止 Workspace 所有权、跨 Session 共享宿主、直接 Sidecar 导入和私有状态进入
  线上协议。

## 可观测性

业务代码只调用 `observability` 的结构化入口。标准库 `logging.getLogger(__name__)`、直接
logger 方法、吞异常和日志文本协议都被架构守卫禁止。

观测事件至少携带可用的 `session_id`、`run_id`、线上坐标、command/effect identity、环节、
结果类别和异常来源。敏感正文、凭据、完整工具输出和未筛选 provider payload 不进入日志。

可观测性是事实的投影，不拥有重试、取消、审批或终态决定。

## 公共 API 与扩展规则

新增能力时按以下判断顺序：

1. 确认状态所有者和生命周期 owner。
2. 确认是领域规则、application 用例、Harness 编排还是外部 adapter。
3. 优先复用现有端口；只有出现真实可替换边界时新增端口。
4. 由组合边界注入实现，删除动态发现和备用构造路径。
5. 同次变更删除被替代的字段、路径和回退，不保留无期限兼容层。
6. 用核心成功路径、关键失败路径、架构守卫和语法检查证明边界成立。

不允许：

- 创建 `core`、`common`、`shared`、`utils` 等无明确所有者的杂项层；
- 为测试扩大生产公开 API 或保留模块级替身；
- 在前端复制协议 reducer、Run 状态机或效果对账；
- 让 adapter 的第三方类型穿透 application/domain；
- 以目录移动、命名变化或单个测试通过代替完整用例证据；
- 为已删除字段增加 `pop`、别名、存在性检查或静默忽略。

## 架构验收

一次跨边界变更至少满足：

- 职责归属、依赖方向、状态所有权和关闭顺序可由代码直接看出；
- 新路径接入完整用例，旧路径与回退已经删除；
- Command、Event、Effect、Approval 和线上 identity 保持稳定；
- CLI、TUI、stdio MCP、Subscription 中受影响入口使用同一 application 语义；
- `tests/test_package_architecture.py` 的依赖方向和公开面守卫通过；
- 受影响行为测试、`compileall` 或等价语法检查、`git diff --check` 通过；
- 协议变更同时更新 `PROTOCOL.md`、schema、client 和契约测试；
- 文档只记录稳定决策和当前验证方式。

仓库级推荐复核命令：

```powershell
.\venv\Scripts\python.exe -m pytest tests\test_package_architecture.py
.\venv\Scripts\python.exe -m compileall agent protocol frontends infrastructure observability metadata
git diff --check
```

## 现行完成定义

当前架构只有在以下事实持续成立时才视为健康：

- `mind.py`/`composition.py` 是唯一具体组合边界；
- `agent.application`、`agent.harness`、stores、capabilities 和 adapters 职责化分组；
- 顶层 `protocol` 独立于 Harness，可供替换前端复用；
- 所有具体前端只消费显式 host、factory 或 port；
- 本地执行状态、线上协议状态和 Subscription 状态相互隔离；
- 持久事实能够驱动恢复，未知 Effect 有确定的对账路径；
- 架构守卫能够阻止反向依赖、动态宿主发现、直接 logging 和旧路径回归。

任一事实不成立都属于架构缺陷，应先恢复边界，再继续叠加业务能力。
