# Agent Runtime 外部契约基线

状态：阶段 0 冻结基线（2026-08-28）

本文记录 ProxyMind 在引入顶层 `agent` 包之前已经存在的入站、出站和持久化
契约。它用于约束迁移兼容性，不取代代码中的解析器、类型和校验逻辑；发生差异时，
先判断是代码契约变更还是本文漂移，再同步修改代码、测试和本文。

线上 Mind Runtime 的规范权威为根目录 `PROTOCOL.md`。本基线只冻结 ProxyMind
已有入口及迁移映射；与线上字段、事件或恢复语义冲突时必须按正式协议修正客户端，
不得保留隐式别名或用本地 Runtime 状态覆盖服务端事实。

## 身份与序号

迁移期间必须区分以下三组坐标：

| 边界 | 身份 | 序号或幂等键 | 约束 |
| --- | --- | --- | --- |
| 本地 Agent Runtime | `session_id`、`run_id` | `command_id`、`idempotency_key`、Run 内 `sequence` | 阶段 1 才引入；不得作为线上协议字段发送 |
| 线上 Turn/Item | `cid`、`sid`、`turn_id`、`attempt`、`item_id` | `request_id`、`client_message_id`、`event_seq` | 字段语义由服务端协议决定，本地运行时只能显式映射 |
| Subscription | `session_id`、可选 `cid`/`sid`、任务 `call_id` | `message_id`、信封 `seq`、`last_acked_seq` | 订阅 `seq` 只用于该订阅会话的恢复和确认 |

本地 `run_id` 与服务端状态快照中的 `run_id` 当前不是同一份已冻结的公开身份。
阶段 1 必须在 adapter 中保存映射，不能依赖字符串恰好相等。服务端
`event_seq`、订阅 `seq` 和未来本地 Event `sequence` 也不得混用。

线上 `event_seq` 是 `cid + sid` 范围内跨 Turn 的持久水位。客户端通过
`ProtocolEventCursorStore` 保存已完整确认的水位，单 Turn 传输从该值开始去重并用
`after_seq` attach；`turn.done`、`turn.failed` 和连接关闭都不推进本地 FIFO，
只有 `turn.logical_settled` 是逻辑轮次结算事实。

正式 Item 事件在交给前端前由 `CanonicalItemReducer` 归约。active 视图只包含未被
provider retry 或 `presentation.superseded` 替换的版本，审计视图保留全部展示版本；
正文、工具、审批等前端不得各自重新定义 Item 状态转换。`stream.gap` 是无
`event_seq` 的非持久控制信号，不创建 Item，也不能被 capability 公共门禁误判为
普通持久事件。重连审批快照的 `last_event_seq` 只定义审批状态对旧回放事件的优先级；
resolved/cancelled Item 不得被该水位内的旧 pending 事件重新打开，也不得用快照
水位替换客户端实际确认游标。最终 `assistant_text` 只从 active canonical text Items
按首次事件顺序派生；provider retry 或 presentation 替换后的旧正文只保留审计，
不得进入 RunResult、Stop Hook 或下一轮最后回复记忆。事件交付期间
`current_item` 表示当前事件归约后的 revision，忽略或控制事件为 `null`；最终来源
从 active text/builtin Items 按首次出现去重，前端不得另建来源归属状态。

## CLI 契约

权威来源：`frontends/cli/arguments.py`、`frontends/cli/invocation.py`、
`frontends/cli/dispatch.py`、`mind_app/runtime/turns/result.py`。

| 入口 | 当前输入 | 当前输出或效果 | 迁移约束 |
| --- | --- | --- | --- |
| 无子命令 | 可选 prompt、image、model、Helix 和通用 invocation 选项 | 进入交互 TUI，可开始模型 Turn | 参数兼容；TUI 入口改为提交 Command |
| `exec` / `e` | 可选 prompt 或 stdin、`--json`、image、model、Helix、hook trust | 文本或 NDJSON；退出码来自 `RunResult.exit_code` | 非交互结果字段和退出码保持稳定 |
| `resume` | 可选 session id、prompt、`--last`、`--all`、非交互来源开关及模型上下文 | 恢复线上 `cid`/`sid` 后进入交互执行 | 恢复不是创建新的线上会话身份 |
| `archive` / `unarchive` | session id 或精确标题 | 更新本地 history cursor 状态 | 属于 Session application 操作，不是 Run 状态转移 |
| `agent listen` | 可选 Helix profile | 启动远端任务订阅直到关闭 | 订阅连接生命周期不进入 RunActor |
| `mcp` 子命令 | `list`、`get`、`add`、`remove`、`enable`、`disable`、`help` | 管理外部 MCP 注册 | 配置命令不包装成模型 Run |
| `mcp-server` | stdio MCP server 运行参数 | 长驻 stdio 服务 | 每个 `mind_exec` 请求才映射为 Run Command |
| `upgrade helix`、`doctor`、`completion`、`help` | 各自显式参数 | 管理、诊断或文本输出 | 不属于 Agent Runtime Command Gateway |

`RunResult.to_dict()` 的固定字段是 `status`、`assistant_text`、`usage`、`error`、
`exit_code`。可选字段是 `response_id`、`model`、`route`、`request_id`、
`service_tier`、`stop_reason`、`stop_sequence`、`reason`、
`additional_context`；`incomplete` 额外输出 `can_continue`。`status` 取值为
`completed`、`failed`、`incomplete`、`interrupted` 或
`reconciliation_required`，只有 `completed` 的退出码为 0。

## TUI 契约

展示契约权威来源：`frontends/tui/contracts/`；输入和生命周期适配权威来源：
`frontends/tui/session/dispatch.py`、`frontends/tui/session/turn_input.py`、
`frontends/tui/session/turn.py` 和 `frontends/tui/prompting/commands.py`。

- 普通输入创建主动模型 Turn；shell escape 和 slash command 继续由 TUI adapter
  分类，只有改变 Agent Runtime 状态的输入才提交 application Command。
- 活跃 Turn 中的即时输入使用 `TurnInput.client_message_id` 调用
  `/turn/steer`；排队输入保持 FIFO，并在当前 Turn 结算后成为下一次提交。
- 本地中断立即关闭本地交互状态，远端 `/turn/interrupt` 在后台使用稳定
  `request_id` 发送；关闭时对未确认 steer 调用 `/turn/reconcile`。
- TUI 继续消费 `turn.start`、输入接受和逻辑结算等线上 StreamEvent；阶段 1 的
  本地 Event 投影不得伪造或重编号这些线上事件。

## stdio MCP 契约

权威来源：`mind_app/mcp/server.py` 和 `mind_app/runtime/turns/result.py`。

服务仅公开 structured-output 工具 `mind_exec`，调用被进程内锁串行化：

| 方向 | 字段 |
| --- | --- |
| 输入 | 必填 `prompt`；可选 `sandbox_mode`、`approval_policy`、`working_directory`、`timeout_sec`、`session_id` |
| 输出 | 完整 `RunResult.to_dict()`，并始终增加可空 `session_id` |

未传 `session_id` 时创建新会话；传入时只允许续接同一工作目录可见的历史会话。
超时、空 prompt、无效目录和无效会话都返回结构化 `failed` 结果。迁移后
MCP adapter 必须通过同一个 Command Gateway 执行，但不能改变工具名、字段、
串行语义或返回会话身份。

## Subscription 契约

权威来源：`mind_app/runtime/agent/protocol.py`、
`mind_app/runtime/agent/client.py`、`mind_app/subscription/ws.py` 和
`mind_app/subscription/forwarding.py`。

通用 JSON 信封字段为 `type`、`session_id`、`message_id`、`ts`、`payload`，
可选顶层字段为 `seq`、`cid`、`sid`。

| 方向 | 消息类型 | 关键载荷或语义 |
| --- | --- | --- |
| Client -> Server | `hello` | `client_version`、`device_id` |
| Client -> Server | `runtime.bind` | `llm_conf` |
| Client -> Server | `resume` | `last_acked_seq` |
| Client -> Server | `pong` | 心跳响应 |
| Client -> Server | `mind.received` | `call_id`、接收处置 |
| Client -> Server | `mind.started` | `call_id` |
| Client -> Server | `mind.completed` | `call_id` |
| Client -> Server | `mind.failed` | `call_id`、结构化 `error.type/message` |
| Client -> Server | `mind.cancelled` | `call_id`、固定取消原因 |
| Server -> Client | `ready`、`ping`、`pong` | 就绪和心跳 |
| Server -> Client | `mind.forward` | 远端任务；校验 `call_id`、message 和可选 intent |
| Server -> Client | `replay.batch` | 按订阅序号重放 |
| Server -> Client | `resume.result`、`resume.rejected` | 恢复结果或拒绝 |
| Server -> Client | `ack`、`error` | 客户端状态消息确认或协议错误 |

`mind.forward` 必须先进入有界 inbox、完成接收确认，再异步执行；稳定 `call_id`
是任务状态上报的关联身份。WebSocket handler 只保留连接、恢复、去重、mailbox
和确认职责，模型执行最终映射到统一的 `submit_turn` Command。

## Turn 控制、工具结果与审批

权威来源：`mind_nova/requests/turn_control.py`、
`mind_nova/requests/tools.py`、`mind_nova/turn_inputs.py`。

| 操作 | 当前请求身份和幂等键 | 当前确认语义 |
| --- | --- | --- |
| `GET /turn/status` | query `cid`、`sid`、`turn_id` | 返回匹配坐标、`run_id`、status、terminal、attempt、version、`last_event_seq` 和时间 |
| `POST /turn/steer` | query `cid/sid`；body `request_id`、`turn_id`、`client_message_id`、input | status 为 accepted/not-active/not-steerable/mismatch/duplicate 之一，并回显身份 |
| `POST /turn/interrupt` | query `cid/sid`；body 稳定 `request_id`、`turn_id` | 与 steer 相同的具名状态集合并回显身份 |
| `POST /turn/reconcile` | `cid/sid/turn_id`、`client_message_ids` | 每个 id 恰好归入 committed/pending/retry/unknown |
| `POST /tool-result` | `request_id`、`cid`、`sid`、`call_id`、name、ok、result、additional_context | 可靠 POST；matched/already_received、`delivered=true` 且回显 request_id 才成功 |
| `GET /tool-result/status` | `cid`、`sid`、`call_id` | 返回该调用是否已收到结果，用于未知确认对账 |
| `POST /tool-result/renew` | 工具调用坐标和预算续期请求 | 仅扩展受管工具等待预算，不代表结果完成 |
| 工具审批 | `request_id`、`cid`、`sid`、`turn_id`、`call_id`、`approval_id`、kind、decision | ack 必须匹配全部坐标、审批种类和生命周期状态 |
| `POST /turn/approval-snapshot` | `cid`、`sid`、`turn_id` | 返回 turn 状态、结算标记、`last_event_seq` 和 pending/resolved 审批信封 |

Protocol Client 的 `ProtocolCommandClient` 端口负责上述控制面命令的 wire 交付：
`interrupt_turn` 返回已校验的 `TurnControlReceipt`，工具结果、审批和效果核对
命令只在服务端确认后返回；`get_tool_result_status` 返回与请求坐标匹配的状态
快照。前端和运行流不得直接依赖 `mind_nova.requests` 的请求函数。

阶段 1 的 `approve`、`append_input` 和 `cancel_run` 只封装上述意图；外部请求的
`request_id`、`client_message_id`、`call_id` 和审批身份必须原样保留以支持重试与对账。
审批快照必须先进入 Protocol Client 的 Canonical Item reducer，再调用具体前端的
审批恢复处理器；前端只从归约结果展示 `waiting_approval` Item。

## 历史与 Transcript 契约

权威来源：`agent/stores/sessions/history.py`、`infrastructure/persistence/transcripts.py`（文件 adapter）和
`agent/stores/transcripts/records.py`、`agent/stores/transcripts/replay.py`（共享记录值与归约）。

本地 SQLite 只保存会话游标和待完成分支请求，不保存完整消息：

- `conversation_session_cursors` 以 `(cid, sid)` 为主键，保存 workspace、source、
  branch、active/archived status、title 和创建/更新/过期时间。
- `conversation_pending_forks` 以 `(cid, sid)` 为主键，保存
  `before_turn_id`、稳定 `request_id` 和创建/过期时间。

Transcript 是逐行 JSON。每条记录固定包含 `timestamp`、`event`、`session_id`、
`turn_id`、`actor`、`payload`；actor 为 user、assistant、system、tool 或空值。
恢复投影识别 `message.created/updated/superseded`、`tool.started/completed/failed`、
`context.compacted`、`context.compaction.failed`、`turn.failed`、
`turn.incomplete` 和 `turn.interrupted`。迁移必须保留对存量文件的读取，写入新版本时
需要显式版本识别，不能通过字段重命名使旧记录静默丢失。

记录值校验和归约由 `agent/stores/transcripts` 统一持有；工具开始/完成事件合并规则由
`agent.domain.tool_policy` 提供。`infrastructure/persistence` 只负责本地文件 I/O、会话日期路径
和损坏记录观测，不能再定义共享记录值或归约算法。

## 当前入口到目标 Command/Event 的映射

下表是阶段 1-4 的目标映射，不表示对应 Command/Event 已经实现。

| 当前入口 | 目标 Command 或 application 操作 | 身份和幂等 | 最小可见 Event / 结果 |
| --- | --- | --- | --- |
| 交互普通 prompt | `submit_turn` | 新本地 command/run；携带当前 `cid/sid`，线上创建后绑定 `turn_id/attempt` | `run_queued`、`run_started`、`turn_started`、消息/工具事件、唯一终态 |
| `exec` / `e` | `submit_turn` | 每次 invocation 一个 run；线上坐标显式映射 | 与交互相同，adapter 投影为文本/NDJSON 和 `RunResult` |
| `resume` | `resume_session`，可选后续 `submit_turn` | 复用所选 `cid/sid`；恢复操作自身具有 command id | `session_resumed` 或具名失败；prompt 另建 Run |
| TUI 活跃输入 | `append_input` | command id + 原 `client_message_id`，绑定活动 run/turn | accepted/deferred/retry/unknown 投影及后续线上输入事件 |
| TUI/CLI 用户中断 | `cancel_run` | 同一取消重试保留 command id；线上中断保留 `request_id` | `run_cancelling` 后恰好一个 interrupted/cancelled/failed 终态 |
| 工具审批 UI | `resolve_approval` | approval id + decision command id；线上 request id 不变 | `approval_resolved` 或明确冲突/过期事件 |
| stdio `mind_exec` | `submit_turn` | MCP request 关联一个 run；可选 session 映射 `cid/sid` | Event 投影为原 `RunResult` + `session_id` |
| Subscription `mind.forward` | `submit_turn` | `call_id` 关联一个 run；信封 message id/seq 用于收件去重 | received、started 及 completed/failed/cancelled 上报 |
| Subscription resume/replay | adapter 恢复操作，不是 Run Command | `session_id` + `last_acked_seq` | 连接/重放状态，不写 Run 业务终态 |
| archive/unarchive | Session application 操作 | `(cid,sid)`；重复设置同状态应幂等 | history cursor 更新结果，不产生 Run Event |

所有 Run 必须只有一个终态。adapter 可以丢弃流式展示 token，但不得丢弃最终消息、
工具结果、审批决定、错误分类或身份映射。
