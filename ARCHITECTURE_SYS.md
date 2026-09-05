# Mind Agent Runtime — System Architecture

> **Mind is the Agent Runtime. AppServer provides durable lifecycle control. Fabric provides cloud compute.**

---

## 1. 文档定位

本文定义 **Mind Agent Runtime 的系统级架构**。

它从 Mind 的视角描述三个独立运行系统之间的职责、状态权威、生命周期和依赖关系：

* **Mind**：Agent Intelligence、Local Runtime、Execution Intent、Local Evidence 与 User Experience；
* **AppServer**：Durable Lifecycle、Global Runtime Truth 与 Server Control Plane；
* **Fabric**：Cloud Compute、Sandbox 与 Specialized Compute Runtime。

本文不复制其他仓库内部实现。

各层架构权威保持独立：

```text
Mind/ARCHITECTURE.md
        │
        └── Mind Client / Agent Runtime 内部架构权威

AppServer/docs/ARCHITECTURE.md
        │
        └── Durable Runtime / Server 状态机权威

Fabric
        │
        └── Cloud Compute Runtime 实现与资源模型权威

Mind/docs/SYSTEM_ARCHITECTURE.md
        │
        └── 三者之间的系统边界与协作契约
```

Mind 当前已经明确采用 `agent / protocol / infrastructure / frontends / observability` 分层，并规定线上 Turn 状态不得由本地 Runtime 冒充；AppServer 则明确拥有 Session execution gate、Turn、Worker、Durable Queue 和 Event Plane。

---

# 2. 产品定位

Mind 不是一个简单的模型客户端，也不只是一个聊天界面。

Mind 是：

> **Agent Intelligence & Execution Runtime**

它负责理解用户意图、组织 Agent 执行、调用工具、管理本地资源，并将服务端 Durable Runtime 投影成用户可交互的运行体验。

整个系统可以抽象为：

```text
                        User
                         │
                         ▼
                ┌─────────────────┐
                │      Mind       │
                │  Agent Runtime  │
                └────────┬────────┘
                         │
                Intent / Command
                         │
                         ▼
               ┌───────────────────┐
               │     AppServer     │
               │ Durable Control   │
               │      Plane        │
               └─────────┬─────────┘
                         │
             Durable Lifecycle Truth
                         │
                         │
          ┌──────────────┴──────────────┐
          │                             │
          ▼                             ▼
   Provider / Tool                 ┌─────────┐
      Runtime                      │ Fabric  │
                                   │ Compute │
                                   │ Runtime │
                                   └────┬────┘
                                        │
                                GPU / Sandbox /
                                Vision / Compute
```

这里有一个很重要的原则：

> **Fabric 不是每个 Turn 的强制中间层。**

AppServer 当前自身已经具备 Worker、LLM、Tool 与 Runtime Execution Plane；Fabric 是独立的计算平面。当某项能力需要云端 Sandbox、GPU、Embedding、Vision 或其他隔离计算时，才通过明确的 Capability Boundary 调用 Fabric。

Fabric 当前也确实以独立 Modal API 应用运行，并组合独立 Cognitive、Vision、Sequential 和 Sandbox Compute Units。

---

# 3. 三个 Runtime 的职责

| 系统          | 核心定位              | 拥有什么                                                                | 不拥有什么                           |
|---------------|-----------------------|-------------------------------------------------------------------------|--------------------------------------|
| **Mind**      | Agent Runtime         | Intent、Local Run、Local Evidence、Tool Execution、TUI、Protocol Client | Remote Turn Truth                    |
| **AppServer** | Durable Control Plane | Session、Turn、Queue、Event、Worker Lease、Terminal                     | UI、Local Runtime                    |
| **Fabric**    | Cloud Compute Runtime | Sandbox、Compute Run、模型/视觉计算资源                                 | Agent Session、Turn、Queue、Terminal |

可以进一步压缩为：

```text
Mind
knows what the Agent wants to do.

AppServer
knows what the durable Agent lifecycle is.

Fabric
knows how cloud computation is executed.
```

因此整个体系不存在一个“万能 Runtime”。

而是三个不同 Authority：

```text
               INTENT AUTHORITY
                     Mind
                      │
                      ▼
             LIFECYCLE AUTHORITY
                  AppServer
                      │
                      ▼
              COMPUTE AUTHORITY
                   Fabric
```

---

# 4. 核心状态权威

系统最重要的架构约束是：

> **One fact, one authoritative owner.**

同一个事实不能在三个系统里分别维护一套状态机。

### Authority Matrix

| 事实                                      | Authority |
|-------------------------------------------|-----------|
| User Input / Intent                       | Mind      |
| Local Session / Run                       | Mind      |
| Local Command                             | Mind      |
| Local Tool Execution Evidence             | Mind      |
| TUI / Presentation                        | Mind      |
| `cid / sid / turn_id`                     | AppServer |
| Remote Turn Lifecycle                     | AppServer |
| Session Execution Gate                    | AppServer |
| Durable Queue                             | AppServer |
| `event_seq`                               | AppServer |
| Worker Lease / Fencing                    | AppServer |
| Terminal Fact                             | AppServer |
| Global Tool / Approval / Effect Lifecycle | AppServer |
| Fabric Sandbox                            | Fabric    |
| Fabric Compute Run                        | Fabric    |
| GPU / Model Compute Resource              | Fabric    |

AppServer 当前将 `runtime_sessions.active_turn_id` 定义为 Session execution gate 和 Active Turn 的权威事实，并使用数据库锁、Worker lease 和 fencing 控制多实例执行所有权。

Mind 则明确规定本地 Run 与线上 Turn 身份必须分离，Adapter 负责映射而不能依赖字符串相等。

---

# 5. Mind 内部架构

Mind 本体采用以下分层：

```text
mind.py
   │
   ▼
composition.py
   │
   ├── agent/
   │
   ├── protocol/
   │
   ├── infrastructure/
   │
   └── observability/
   │
   ▼
frontends/
```

其中 `agent/` 是 Agent Runtime 的核心：

```text
agent/
├── protocol/
├── domain/
├── ports/
├── application/
├── harness/
├── stores/
├── capabilities/
├── adapters/
└── composition.py
```

依赖方向：

```text
domain / agent.protocol
        ▲
        │
      ports
        ▲
        │
   application
        ▲
        │
     harness
```

外围实现：

```text
stores
capabilities
adapters
infrastructure
frontends
```

只能通过稳定契约接入，而不能反向成为 Runtime Owner。该依赖规则目前已经作为 Mind 的正式架构约束，并由包架构测试持续检查。

---

# 6. Mind Runtime 的核心职责

Mind 拥有五个核心能力域：

```text
                    Mind
                     │
     ┌───────────────┼───────────────┐
     │               │               │
     ▼               ▼               ▼
 Intelligence     Execution       Interaction
     │               │               │
     ▼               ▼               ▼
 Agent/Prompt     Tool/Effect      CLI / TUI
 Planning        Local Runtime     MCP / UI
     │
     └───────────────┬───────────────┘
                     ▼
                  Protocol
                     │
                     ▼
                 AppServer
```

具体包括：

### Agent Runtime

负责：

* Session；
* Run；
* Agent Tree；
* Subagent；
* Hook；
* Command Queue；
* Tool orchestration；
* Local execution lifecycle。

### Protocol Runtime

负责：

* Turn Submit；
* Turn Observe；
* Interrupt；
* Steer；
* Attach；
* Replay；
* Tool Result；
* Approval；
* Effect；
* Queue。

### Local Capability Runtime

负责：

* Shell；
* Filesystem；
* MCP；
* JavaScript Sidecar；
* Workspace；
* Local Sandbox；
* Helix 等本机能力。

### Presentation Runtime

负责：

* Streaming；
* Thinking；
* Tool activity；
* Approval；
* Retry；
* Recovery；
* TUI rendering。

### Local Evidence

负责记录：

* Command；
* Run；
* Transcript；
* Approval；
* Effect；
* Frozen Request；
* Recovery Evidence。

---

# 7. 身份模型

三个 Runtime 的身份不得混用。

```text
Mind Local Runtime
────────────────────────
session_id
run_id
command_id
sequence
idempotency_key


AppServer Durable Runtime
────────────────────────
cid
sid
turn_id
attempt
item_id
request_id
client_message_id
event_seq


Fabric Compute Runtime
────────────────────────
sandbox_id
run_id
compute/task identity
```

尤其：

```text
Mind run_id
    ≠
AppServer turn_id
    ≠
Fabric run_id
```

即使底层都是字符串，也不得互相赋值、比较或作为隐式关联。

跨系统关联必须通过明确 Adapter 保存。

建议在 Mind 的 Fabric Adapter 中把 Fabric 的 `run_id` 显式命名为：

```text
fabric_run_id
```

避免和 Agent Local Run 产生语义碰撞。

---

# 8. 一个 Turn 的完整生命周期

正常在线执行：

```text
User
 │
 ▼
Mind Input
 │
 ▼
Immutable Local Command
 │
 ▼
Freeze Request / Environment
 │
 ▼
Submit Turn
 │
 ▼
AppServer
 │
 ├── Create Durable Turn
 ├── Acquire Session Gate
 └── Persist Event
 │
 ▼
Worker
 │
 ▼
Provider / Tool
 │
 ├──── Optional Compute ────► Fabric
 │                               │
 │                               ▼
 │                         Compute Result
 │                               │
 ◄───────────────────────────────┘
 │
 ▼
Runtime Events
 │
 ▼
AppServer Event Plane
 │
 ▼
Mind Observe
 │
 ▼
Canonical Item Reducer
 │
 ▼
Presentation
 │
 ▼
User
```

最关键的是：

> **Mind 发起 Intent，但 AppServer 决定 Durable Turn 的事实。**

---

# 9. Submit 与 Observe

Mind 当前明确将：

```text
Submit
```

和：

```text
Observe
```

分成两个不同操作。

### 新 Turn

```text
Mind
 │
 ▼
Submit
 │
 ▼
AppServer creates Turn
 │
 ▼
turn_id confirmed
 │
 ▼
Observe
```

### 已存在 Turn

```text
Mind
 │
 ▼
Attach / Replay
 │
 ▼
Observe
```

必须遵守：

> **Existing Turn must never be resubmitted.**

因此冷恢复、Queue Start、HTTP 回执未知等情况不能为了“保险”重新调用 `/mind-chat`。

Mind 当前 Durable Queue 文档也已经明确：`queue.start` 成功后 Turn 已经由服务端创建，客户端只能 attach/replay，不得再次提交。

---

# 10. Command 与 Fact

系统必须严格区分：

```text
Command
   ≠
Fact
```

例如：

```text
interrupt request
        ≠
Turn interrupted
```

以及：

```text
queue.start accepted
        ≠
client created another Turn
```

再例如：

```text
HTTP 204
        ≠
terminal
```

AppServer 当前 `/turn/interrupt` 的 `204` 只表示中断命令已经接受，真正终态只能由 `turn.completed` 或同构 `/turn/status` terminal snapshot 确认。

---

# 11. Interrupt

Interrupt 生命周期：

```text
User Ctrl+C
      │
      ▼
Mind Interrupt Command
      │
      ▼
AppServer accepts command
      │
      ▼
Execution Capability fenced
      │
      ▼
Execution Child cancelled
      │
      ▼
Durable Finalizer
      │
      ▼
turn.completed(interrupted)
      │
      ▼
Mind releases local gate
```

Mind 不允许：

```text
Ctrl+C
  ↓
cancel HTTP
  ↓
assume Turn dead
  ↓
start next Turn
```

第一次 Ctrl+C 必须等待权威 terminal。

本地再次 Ctrl+C 可以退出进程，但不能把“退出客户端”解释成“服务端 Turn 已终结”。Mind 当前架构已经明确这一语义。

---

# 12. Steer 与 Durable Queue

Mind 有两种完全不同的“下一输入”。

### Pending Input

属于当前 Mind TUI Runtime：

```text
Active Turn
    │
    └── pending input
```

它是用户交互状态。

### Durable Queue

属于 AppServer：

```text
Session
 │
 └── Durable Queue
      ├── Item A
      ├── Item B
      └── Item C
```

它是服务端持久事实。

因此：

```text
Pending Input
      ≠
Durable Queue
```

Mind 不得自动把普通 Pending Input 转为 Durable Queue。

AppServer 当前 Queue 也是显式队列，只有 `/queue` 操作才能修改，且 `queue.start` 只允许严格 FIFO 首项启动。

---

# 13. Tool / Approval / Effect

工具执行应理解为三个不同层次：

```text
Agent Intent
      │
      ▼
Tool Semantic Identity
      │
      ▼
Approval / Policy
      │
      ▼
Effect Preparation
      │
      ▼
Execution
      │
 ┌────┴─────┐
 ▼          ▼
Mind       Fabric
Local      Cloud
      │
      ▼
Execution Evidence
      │
      ▼
AppServer Durable Fact
```

这里不能简单说“Tool 属于 Mind”或“Tool 属于 Server”。

正确拆分是：

### AppServer

拥有在线 Turn 中的：

* Tool identity；
* Approval lifecycle；
* Effect lifecycle；
* durable result；
* reconciliation state。

### Mind

可以拥有：

* Local tool implementation；
* Local capability；
* Local execution evidence；
* Approval interaction；
* Effect journal。

### Fabric

可以拥有：

* Sandbox process；
* Compute resource；
* Compute Run；
* GPU / model execution。

但是：

> **Fabric 执行 Tool，不代表 Fabric 拥有 Agent Tool 生命周期。**

Fabric 只是 Compute Executor。

---

# 14. Effect 与副作用

对外部副作用必须使用：

```text
Prepare
   ↓
Execute
   ↓
Commit
```

如果执行结果无法确定：

```text
UNKNOWN
```

不能转化成：

```text
FAILED
```

然后自动重试。

必须进入：

```text
Reconciliation
```

Mind 当前架构已经明确：外部效果成功但本地提交结果未知时进入 reconciliation，不自动重放不可重放副作用。

AppServer 同样规定 Effect 的不确定结果只能通过 owner-authenticated control plane 对账。

因此全局原则是：

> **Unknown Effect ≠ Failed Effect.**

---

# 15. Fabric Compute Plane

Fabric 是三个系统中最纯粹的计算层。

当前 Fabric API 由 Modal ASGI Application 承载，在生命周期启动时初始化 Redis、SandboxFacade，并注册 Middleware 和 Router。

其 Compute Units 当前包括：

```text
fabric-runtime
├── Cognitive
│   ├── Embedding
│   └── Cross Encoder
│
├── Vision
│   └── YOLO
│
├── Sequential
│
└── Sandbox Worker
```

这些能力由一个统一 Modal App 组合。

Mind 对 Fabric 的正确认识不是：

```text
Fabric = Remote Mind
```

而是：

```text
Fabric = Compute Substrate
```

它不得拥有：

```text
Agent Session
Turn
Steer
Interrupt semantics
Durable Queue
Agent Terminal
TUI
```

---

# 16. Fabric 调用边界

调用 Fabric 时必须通过显式 Capability：

```text
Agent Application
       │
       ▼
ComputePort
       │
       ▼
Fabric Adapter
       │
       ▼
Fabric API
```

而不能：

```text
Agent Domain
       │
       ▼
httpx.post(Fabric)   X
```

这样未来：

```text
Fabric
Local Sandbox
Other Cloud Runtime
```

可以在不修改 Agent Domain 的情况下替换。

---

# 17. Event Plane

AppServer 是线上 Agent Event 的 Authority。

其结构：

```text
PostgreSQL
Authoritative Event Log
      │
      ▼
Outbox
      │
      ▼
Relay
      │
 ┌────┴─────┐
 ▼          ▼
Redis    EventHub
```

Redis 和进程内 EventHub 只承担通知与加速，不能覆盖 PostgreSQL 的持久事实。

Mind：

```text
Observe
  ↓
CanonicalItemReducer
  ↓
Application Projection
  ↓
TUI
```

因此：

> **Mind consumes Truth. Mind does not recreate Truth.**

---

# 18. Cursor 与 Replay

`event_seq` 属于：

```text
cid + sid
```

范围。

它不是 Turn 内局部序号。

例如：

```text
seq 1  turn.started(A)
seq 2  queue.changed
seq 3  text.delta(A)
```

Mind attach Turn A 时可能只看到：

```text
1
3
```

这不是 Gap。

因此 Mind 不得假设：

```text
next_seq == previous_seq + 1
```

对于经过 Turn Projection 后的可见事件，只要求严格单调。

真正的 Gap 判断由 AppServer 在完整 Session Event Stream 上完成。

---

# 19. Provider Output

Provider 输出必须遵守：

> **Preserve upstream semantics.**

例如 assistant text 的：

```text
phase
```

只有 Provider 明确提供：

```text
commentary
final_answer
```

时才传播。

缺失就是：

```text
unknown
```

Server、Replay、Mind TUI 都不能通过文本内容自行推断。

AppServer 当前协议已经正式固化这一原则。

---

# 20. Presentation Architecture

Mind 是 Presentation Authority。

但：

```text
Presentation
     ≠
Lifecycle
```

正式链路：

```text
AppServer Event
      │
      ▼
Protocol Client
      │
      ▼
CanonicalItemReducer
      │
      ▼
TurnActivityProjector
      │
      ▼
reduce_turn_surface()
      │
      ▼
TuiTurnSurfaceCoordinator
      │
      ▼
Terminal
```

Mind 当前设计已经把：

* Content；
* Presentation；
* Activity；
* Output Control

拆成独立出口，并要求只有唯一 Turn Terminal 才能真正释放活动资源。

因此：

```text
Thinking disappeared
       ≠
Turn completed

Assistant rendered
       ≠
Turn completed

SSE EOF
       ≠
Turn completed
```

---

# 21. Recovery Architecture

恢复分成三种完全不同的能力。

## Observation Recovery

```text
Client disconnect
      ↓
Reconnect
      ↓
Attach
      ↓
Replay
      ↓
Continue Observe
```

属于 Mind + AppServer。

---

## Execution Recovery

```text
Worker crash
     ↓
Lease expire
     ↓
New Worker claim
     ↓
Checkpoint
     ↓
Continue / Finalize
```

属于 AppServer。

---

## Compute Recovery

```text
Fabric Compute Worker
       ↓
Compute Run State
       ↓
Query / Retry / Reconcile
```

属于 Fabric。

三者绝对不能混成一个：

```text
RecoveryManager
```

否则很快就会产生第二套全局状态机。

---

# 22. Persistence Boundary

Mind 保存的是：

```text
Local execution facts
Local evidence
Frozen requests
Transcript
Approval evidence
Effect evidence
Local recovery state
```

AppServer 保存的是：

```text
Session
Turn
Queue
Command receipt
Event
Checkpoint
Terminal
Worker fencing
Global Effect lifecycle
```

Fabric 保存的是：

```text
Sandbox
Compute Run
Compute result
Temporary compute state
```

因此：

> **Persistence location follows authority ownership.**

不是因为“这个数据库访问方便”就把事实放进去。

---

# 23. Observability

三个系统应共享 Trace Context，但不能共享状态 Authority。

推荐全链路字段：

```text
trace_id

Mind
session_id
run_id
command_id

AppServer
cid
sid
turn_id
attempt
request_id
client_message_id
event_seq
tool_call_id
effect_id

Fabric
sandbox_id
fabric_run_id
compute_unit
```

链路：

```text
Mind
 │ trace_id
 ▼
AppServer
 │ trace_id + turn_id
 ▼
Fabric
 │ trace_id + fabric_run_id
 ▼
Result
```

Sentry、Structured Log 和 Metrics 都只能**观察事实**，不能成为 Runtime 恢复或状态判断来源。

Mind 当前架构同样明确禁止从日志文本或异常文案反推业务事实。

---

# 24. Failure Isolation

三个 Runtime 必须能够分别失败。

### Mind Crash

```text
Mind dies
   │
   └── AppServer Turn may continue
```

### AppServer HTTP Process Crash

```text
HTTP process dies
       │
       └── Durable Turn truth remains
```

### Worker Crash

```text
Worker dies
    │
    └── AppServer lease/fencing/recovery handles it
```

### Fabric Compute Failure

```text
Fabric compute fails
       │
       └── Compute result fails
           but Agent lifecycle remains AppServer-owned
```

因此：

> **Failure boundary must follow ownership boundary.**

---

# 25. 系统依赖方向

完整系统推荐理解为：

```text
                 User / Frontend
                        │
                        ▼
                       Mind
          ┌─────────────┼─────────────┐
          │             │             │
          ▼             ▼             ▼
       Agent         Protocol     Infrastructure
          │             │
          │             ▼
          │         AppServer
          │             │
          │             │
          │      Durable Lifecycle
          │             │
          └─────────────┼──────────────┐
                        │              │
                        ▼              ▼
                    Provider         Fabric
                                       │
                                 Cloud Compute
```

允许：

```text
Mind Adapter → Fabric
```

或：

```text
AppServer Capability → Fabric
```

但无论谁调用：

```text
Fabric
```

都不能因此取得 Agent Lifecycle Authority。

---

# 26. 架构扩展规则

新增一个能力前必须先回答四个问题。

### 1. 谁拥有这个事实？

例如：

```text
Turn State?
→ AppServer
```

### 2. 谁执行？

例如：

```text
GPU inference?
→ Fabric
```

### 3. 谁展示？

```text
Tool progress?
→ Mind
```

### 4. 谁负责恢复？

```text
Worker crash?
→ AppServer

TUI restart?
→ Mind

Sandbox crash?
→ Fabric
```

只有这四个问题都有唯一答案，才允许新增能力。

---

# 27. 禁止的架构演进

禁止为了修一个问题新增：

```text
GlobalRuntimeManager
RecoveryCoordinator
UniversalState
SharedTurnState
FabricTurn
ClientServerTurnMirror
```

也禁止：

```text
Mind 保存 Remote Turn 第二套状态机

Fabric 保存 Agent Session

AppServer 根据 UI 状态决定 Terminal

TUI 根据 HTTP EOF 判断完成

Unknown network result 自动 Resubmit

Unknown Effect 自动 Retry
```

这些都会破坏 Authority Boundary。

---

# 28. E2E Architecture Gate

三仓集成至少必须保证：

```text
Normal Turn

Multi Turn

Streaming

Interrupt during Thinking
Interrupt during Assistant
Interrupt during Tool
Interrupt during Approval

Multiple Steers
Steer + Interrupt

Durable Queue Add
Durable Queue Start

Disconnect
Attach
Replay

Worker crash
Finalizer takeover

Fabric compute success
Fabric compute timeout
Fabric compute failure

Terminal → Next Turn
```

系统级不变量：

```text
Duplicate Turn       = 0
Duplicate Tool       = 0
Duplicate Terminal   = 0

Input Loss           = 0
FIFO Violation       = 0

False Cursor Gap     = 0
Session Gate Stuck   = 0

Provider Replay      = 0
Unknown Effect Retry = 0
```

---

# 29. Architecture Laws

Mind Agent Runtime 整个体系最终可以归结成以下规则：

```text
Command ≠ Fact

Presentation ≠ Lifecycle

HTTP Ack ≠ Terminal

Connection ≠ Execution

Submit ≠ Observe

Unknown ≠ Missing

Unknown Effect ≠ Failed Effect

Local Run ≠ Remote Turn

Compute ≠ Agent Lifecycle

Worker ≠ Authority

Cache ≠ Truth

Projection ≠ Source of Truth
```

以及最核心的一条：

> **One Fact, One Authoritative Owner.**

---

# 30. 最终系统定义

三个产品最终形成：

```text
Mind
Agent Intelligence & Execution Runtime

        ↓

AppServer
Durable Lifecycle & Control Plane

        ↓

Fabric
Cloud Compute Runtime
```

但这不是简单的三级调用链。

更准确的是：

```text
Mind
owns Intent, Local Execution and Experience

AppServer
owns Durable Lifecycle Truth

Fabric
owns Cloud Compute Execution
```

最终一句话：

> **Mind thinks and orchestrates. AppServer makes execution durable. Fabric provides the compute substrate.**

---
