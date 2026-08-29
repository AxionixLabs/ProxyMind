# Mind Runtime Protocol

本文档是 Mind Runtime 的唯一规范源。客户端与服务端按本文档同步使用字段、端点、事件和错误码；本文件中的规范性条款优先于示例和实现说明。

机器可读的接口契约以仓库生成的 `openapi.json` 为准，并必须与本文档和测试在同一次变更中保持一致。

## Domain Model

| Entity | Stable identity | Authority | Meaning |
| --- | --- | --- | --- |
| Session | `cid + sid` | `runtime_sessions` | 一个可连续产生多个 Turn 的逻辑会话；事件水位在此范围内递增 `event_seq`。 |
| Turn | `cid + sid + turn_id` | `runtime_turns` | 一次逻辑用户轮次；输入、控制命令、终态和结算事实都绑定到该坐标。 |
| Attempt | `cid + sid + turn_id + attempt` | `runtime_turns.attempt` | Worker 对同一 Turn 的一次执行尝试；当前从 `1` 开始，后续接管必须递增。 |
| Item | `item_id`；领域来源仍为 `client_message_id/call_id/segment_id/builtin_call_id` | `runtime_transcripts` / tool tables + 领域事件投影 | 消息、文本段、工具调用、工具输出、审批和内置工具等可持久化上下文单元。 |
| Event | `cid + sid + event_seq` | `runtime_events` | 已持久化后才可广播的领域事实；同一 Session 内严格单调。 |

`run_id` 只标识一次运行过程，不替代 `turn_id` 或 Attempt identity。SSE/WS 是观察和命令传输通道，不拥有以上实体的生命周期。

### Item Projection

可展示事件统一携带以下三个字段：

```json
{
  "item_id": "turn_01:p1:a1:r1:s1",
  "item_kind": "text",
  "item_status": "in_progress"
}
```

`item_id` 必须跨重连、事件回放和 Worker Attempt 接管保持稳定；`event_seq` 只负责传输游标。文本事件使用 `segment_id`，客户端工具事件使用 `call_id`（输出使用 `<call_id>:output`），审批使用 `approval_id`，内置工具使用 `builtin_call_id`，没有上游 ID 时使用服务端确定性哈希回退。`turn.*`、`lifecycle.*`、`presentation.*` 和 `stream.*` 控制事件不携带 Item 字段。

`item_kind` 取值为 `message`、`text`、`reasoning`、`tool_call`、`tool_output`、`builtin_tool`、`approval` 或 `custom`；`item_status` 取值为 `registered`、`in_progress`、`waiting_result`、`waiting_approval`、`result_received`、`completed`、`failed`、`cancelled` 或 `reconciliation_required`。canonical Transcript 使用同一规则保存内部 `_mind_item_id`、`_mind_item_kind` 和 `_mind_item_status` 元数据，发送给供应商前必须剥离这些服务端字段。

阶段 0 已冻结 `review` Item 扩展，但在阶段 1 的模型、路由、持久化和 OpenAPI 同步完成前，客户端不得发送 `item_kind=review` 或 `review.*` 事件。启用后，`review` 将成为独立 Item 类型，不得退化为 `custom` 或通过普通消息文本推断。

可展示事件的身份映射如下；控制事件不创建 Item：

| Event | `item_id` | `item_kind` | Typical lifecycle |
| --- | --- | --- | --- |
| `text.delta/done/meta` | `segment_id` | `text` | `in_progress` -> `completed` |
| `tool.call` | `call_id` | `tool_call` | `waiting_result` |
| `tool.output` | `<call_id>:output` | `tool_output` | `result_received` -> `completed/failed/cancelled` |
| `tool.approval_required` | `approval_id` | `approval` | `waiting_approval` |
| `tool.builtin.call/done` | `builtin_call_id` | `builtin_tool` | `in_progress` -> `completed/failed` |
| `review.started/completed/failed/cancelled/reconciliation_required` | `review_item_id` | `review` | `in_progress` -> `completed/failed/cancelled/reconciliation_required` |

## Review Domain Extension

本节定义已启用的 Review 领域契约。它只定义 Mind Runtime 语义，不定义 Codex JSON-RPC 兼容层。

### Review command

`POST /mind-review` 是唯一的 Review 启动命令，成功登记返回 HTTP `202`。请求必须携带 `request_id`、`cid`、`sid`、`turn_id`、结构化 `target`、`delivery`、`workspace` 和 `execution`。`execution` 复用通用 Agent 运行参数，但强制 `sandbox_mode=read-only`，禁止 hosted tools、skills、attachments 和流式 HTTP 响应；客户端工具必须声明 `annotations.readOnlyHint=true`。请求模型使用 `extra="forbid"`，不得从普通 `message` 内容猜测审查类型。

`target` 只允许以下判别联合：

- `{"type":"uncommitted_changes"}`
- `{"type":"base_branch","branch":"..."}`
- `{"type":"commit","sha":"...","title":"..."}`
- `{"type":"custom","instructions":"..."}`

`delivery` 只允许 `inline` 和 `detached`。inline 在当前 `cid/sid` 创建 Review Turn；detached 必须原子创建新的 Review Session、父子关联和 Review Turn，不能让客户端串行调用 `/fork` 和 `/mind-chat` 模拟。

`workspace.source` 只允许 `server`、`client` 和 `reference`。三类来源在当前接口都必须提交完整的不可变 `patch` 或 `files` 快照；`server.workspace_id` 与 `reference.reference_id + signature` 是来源审计字段，不改变内容校验或授权边界。服务端在登记前重新计算并拒绝 hash 不一致，且总快照正文不超过 4,000,000 字节。`files[].path` 只允许规范化相对路径，文件正文还必须匹配各自 `sha256`。服务端不得接受任意客户端本地路径并直接读取。

Review 命令的幂等指纹覆盖规范化 target、delivery、workspace source、revision/hash 以及完整输入。相同 `request_id` 和指纹返回同一登记结果；相同 `request_id` 的不同指纹返回 `409 request_id_conflict`；同一源 `cid + sid + turn_id` 的相同语义即使更换 `request_id` 也只返回既有 Review Turn，不同语义返回 `409 turn_id_reused`；Session 已有其他活动 Turn 返回 `409 turn_already_active`。`execution.system_message` 固定为空并由服务端注入版本化只读审查提示词，客户端不能覆盖。

### Review lifecycle

Review 复用现有 Turn 状态机和结算事实，不创建第二套状态机。事件顺序为：

```text
turn.start
review.started
tool.* (可选)
review.completed | review.failed | review.cancelled
turn.done | turn.failed
turn.logical_settled
```

`item_kind=review` 的 Item 必须携带稳定 `item_id`、`item_status` 和目标摘要。Review 结果必须在 `review.completed.output` 中按 `ReviewOutput` 提交；结果、Review 终态事件、Turn 终态和 `turn.logical_settled` 由同一个 Worker lease-fenced 数据库事务提交，并在数据库内校验事件顺序与 `output` 一致性。结构化结果解析失败产生 `review.failed`；外部效果未知时产生 `review.reconciliation_required` 并暂停 Turn，不得伪装成普通 `turn.failed`。Worker 从 `terminal_ready` checkpoint 接管时只执行幂等终结，不再次调用模型。

Review 状态、断线恢复、中断、工具结果和外部效果核对分别复用 `/turn/status`、`/mind-attach`、`/mind-replay`、`/turn/interrupt`、`/tool-result/status` 和 `/effect/reconcile`，不新增同义接口。

## Turn State Machine

```text
queued -> running -> waiting_tool     -> running
                  -> waiting_approval -> running
                  -> waiting_user     -> running
                  -> reconciliation_required
                  -> finalizing       -> completed

queued/running/waiting_*/finalizing -> failed | interrupted | cancelled
reconciliation_required -> queued | interrupted | cancelled
```

`queued` Turn 可以接收 interrupt；Worker 领取后必须观察该中断事实并收敛为 `interrupted`。`reconciliation_required` 是不可领取、不可结算、不可继续准备新效果的持久暂停态；效果核对成功后重新排队并由接管 Attempt 继续，也允许用户将其收敛为 `interrupted` 或 `cancelled`。终态为 `completed`、`failed`、`interrupted`、`cancelled`，不可离开。重复写入同一状态是幂等更新；其他未列出的跳转必须拒绝。每个 `cid/sid` 同时最多存在一个非终态 Turn。

## Commands And Idempotency

所有客户端命令都必须携带独立 `request_id`，成功响应会回显该值。同一个 `request_id` 与相同 payload 重试时返回第一次业务结果；同一个 `request_id` 携带不同 payload 时返回 `409`，稳定错误码为 `request_id_conflict`。

命令幂等指纹覆盖规范化后的完整请求语义。`/tool-result` 与 `/tool-approval` 的 `additional_context` 也属于指纹；使用同一 `request_id` 改变补充上下文必须返回 `request_id_conflict`。

控制命令在数据库中按 `cid/sid/turn_id/request_id` 串行化，并在锁内读取既有结果。并发提交相同命令必须得到同一个业务结果；并发复用 `request_id` 提交不同语义必须得到 `request_id_conflict`，不得暴露唯一键异常。

| Command | Transport idempotency | Business uniqueness |
| --- | --- | --- |
| `POST /turn/steer` | `request_id` | `cid + sid + turn_id + client_message_id` |
| `POST /turn/interrupt` | `request_id` | Turn 上唯一的 interrupt fact |
| `POST /tool-result` | `request_id` | `cid + sid + call_id` 的首个结果 |
| `POST /tool-result/renew` | `request_id` | 仅用于仍在执行中的托管工具预算续期；交互型调用不需要续期 |
| `POST /tool-approval` | `request_id` | `cid + sid + call_id + approval_id` 的首个 decision |
| `POST /fork` | `request_id` | 认证主体、源 Session、分叉边界和 prompt source |
| `POST /effect/reconcile` | `request_id` | 不确定持久效果的人工或供应商核对结论 |

`client_message_id`、`call_id` 和 `approval_id` 是领域对象 ID，不得替代传输命令的 `request_id`。

Hosted 工具通过 `hosted_tools.enabled_groups` 按组启用。启用 `sandbox_cloud` 后，模型可以调用 `sandbox_cloud_start`、`sandbox_cloud_status` 和 `sandbox_cloud_terminate`；`shell_command`、`exec_command` 与 `/tool-result` 属于客户端本地工具链。

## Event Delivery

- `event_seq` 是同一 `cid/sid` 下跨 Turn 的唯一单调事件序列。
- 除 `ping` 外，聊天事件统一携带 `proto=mind.chat`、`cid`、`sid`、`turn_id`、正整数 `event_seq` 和正整数 `presentation_epoch`；缺失或坐标不匹配必须作为协议错误拒绝。
- 可展示的 `text.*`、`tool.*` 事件还必须携带完整的 `item_id`、`item_kind` 和 `item_status`；控制事件明确不携带 Item 投影。
- 事件 append 在同一数据库事务内锁定并更新 `runtime_sessions.last_event_seq`、写入 `runtime_events` 与唯一对应的 `runtime_event_outbox`；事务提交失败时不得广播。
- Worker 产生的事件必须携带当前 `worker_id + lease_token`，数据库在持有 Turn 行锁时校验 lease 后才允许追加，失去租约的 Worker 不得继续写入权威事件流。
- `runtime_events` 是回放和跨进程 relay 的唯一权威源。事务提交后允许先通知当前进程 EventHub；outbox dispatcher 使用 lease 领取通知并向 Redis 仅发布 `cid/sid/event_seq` 唤醒坐标，发布失败时持久退避重试，进程退出后由其他实例接管。
- `runtime_sessions.first_event_seq` 是当前持久事件保留窗口的第一序号；没有保留事件时等于 `last_event_seq + 1`。清理只能删除 Session 的连续历史前缀并原子推进该水位，不得在保留窗口内部制造缺口，也不得跨越尚未投递的 outbox。
- Redis relay 使用实例唯一的 `consumer_id` 在 `runtime_delivery_cursors` 保存每个 Session 已从数据库交给本地 EventHub 的水位；需要跨重启追赶时，同一实例应复用原值，但并发实例不得共享该值。重复通知直接忽略，序号缺口和 Redis 重连都从 `runtime_events` 按稳定分页完整补齐后再推进水位，单次批次预算只允许协作式让步，不得截断恢复。
- relay 只允许依据 `first_event_seq` 跳过已裁剪前缀；窗口内部缺口必须停止推进并报告权威事件损坏。客户端使用早于保留窗口的 `after_seq` 回放时，首条结果可以从 `first_event_seq` 开始，仍按收到的 `event_seq` 更新游标。
- outbox 的 `delivered` 只表示实时总线已经接受通知，不表示客户端已经处理；Redis、delivery cursor、EventHub 和 SSE 都不是事件权威源。
- SSE 连接允许重复投递；客户端必须按 `event_seq` 幂等去重。
- 实时通道出现序号缺口、本地订阅队列溢出或通知丢失时，SSE 会立即从 `runtime_events` 补拉；空闲连接还会按低频周期核对权威水位。
- Redis 故障只能增加跨实例事件延迟：outbox 保留 pending，当前实例仍走提交后 EventHub，其他实例由 SSE 数据库兜底补拉，Redis 恢复后自动排空积压。
- 传输语义为 at-least-once；客户端按 `event_seq` 去重后实现展示层 exactly-once。
- 首次 `POST /mind-chat` 必须携带 `turn_id` 以及 `metadata.cid/metadata.sid`。
- `POST /mind-chat` 以 `cid/sid/turn_id` 和完整请求语义幂等；相同请求重试时观察既有 Turn，不重复执行。相同 `turn_id` 携带不同请求语义必须返回 `409 turn_id_reused`。
- `turn_already_active` 的错误详情会尽量返回当前活动 Turn 的 `active_turn_id` 和 `active_status`；客户端应使用该 ID 进行 attach 或 interrupt，避免盲目重复提交。
- 若历史数据存在终态但尚未写入 `settled` 标记，新的 `/mind-chat` 会在预留事务中补写唯一 `turn.logical_settled` 并释放会话占用，再创建新轮次。
- `AgentRequest.approval_policy` 接受 `untrusted`、`on-request`、`never`，或 `{"granular": {...}}`；细粒度对象必须声明 `sandbox_approval`、`rules`、`mcp_elicitations`，`skill_approval` 和 `request_permissions` 缺省时均为 `false`。`approval_policy` 和 `approvals_reviewer` 只描述调用侧工具的权限选择，服务端不据此执行工具或作出安全裁决。
- 断线后使用 `POST /mind-attach`，请求只包含 `cid`、`sid`、`turn_id`、`after_seq`。
- 审批恢复时先调用 `POST /turn/approval-snapshot`，请求体携带 `cid/sid/turn_id`；服务端完成审批状态对账后返回权威 `last_event_seq`、Turn 状态和与实时事件同构的审批信封数组。随后仍使用原客户端确认游标调用 `POST /mind-attach`，不得把快照水位直接当作已确认游标。
- 快照中的审批信封按持久创建顺序返回，使用 `approval_id + call_id` 合并。快照状态对所有 `event_seq <= last_event_seq` 的重放审批事件具有优先级；客户端只展示 `status=pending`，不得用较旧的 pending 事件重新打开 `resolved` 或 `cancelled` 审批。
- `GET /mind-replay` 和 `GET /stream-events` 必须携带与 `cid/sid` 匹配的短期 `vt`，只使用 `after_seq`；回放响应返回 `events`、`next_seq`、保留窗口 `first_seq`、页末 `last_seq`、`has_more` 以及 `gap` 元数据。`gap=retained_prefix` 表示请求游标早于已裁剪历史，服务端从 `first_seq` 继续；`gap=internal` 表示权威序列内部损坏，连接不得静默跳过。
- SSE 在检测到历史裁剪或权威内部缺口时发送非持久 `stream.gap` 控制事件；前者可继续回放，后者必须进入可重试的对账路径。实时通知仅提供唤醒，最终顺序始终由 `event_seq` 回放裁决。
- 客户端收到 `turn.logical_settled` 后才结束该逻辑轮次；连接关闭、`turn.done` 或 `turn.failed` 均不能替代结算事件。
- `turn.logical_settled`、Turn 终态和 Session idle 状态由 Worker 在同一数据库事务内提交；`settled=true` 的 Turn 不得再次被领取。

## Event Ingress

`POST /events-ingest` 只接收标准领域事件：`turn.*`、`text.*`、`tool.*`、`presentation.*`、`lifecycle.*`、`stream.*` 和 `heal.*`。请求必须携带 `proto=mind.chat`、匹配请求的 `cid/sid`、非空 `turn_id` 和正整数 `presentation_epoch`；坐标缺失或不匹配返回 `422`。客户端不得提供 `event_seq`，该字段只能由服务端数据库 append 分配。服务端内部 provider 事件在写入权威存储前也必须先投影为本目录定义的标准事件。

## Command And Recovery Semantics

以下规则补充命令表和事件投影，所有状态事实仍由数据库和事件流拥有：

- `/mind-chat` 顶层 `turn_id` 是 steer/interrupt 的稳定匹配键；每个逻辑轮次使用不同 ID，`metadata` 必须携带稳定的 `cid`、`sid`。同一 Session 只允许一个活动 Turn；重复请求必须观察既有 Turn，不得重复执行。
- 客户端收到 `turn.start` 后才可发送 steer。`queued` Turn 可以 interrupt；未绑定执行者或已进入收敛阶段的 Turn 返回 `turn_not_steerable`，不会把输入写入 mailbox。
- `/turn/steer` 的 `status=accepted` 仅表示输入进入缓冲区；只有 `turn.input.accepted` 表示输入已在模型采样边界写入 canonical Transcript。未采样输入在结算时交还 `next_input` 或通过 `/turn/reconcile` 分组恢复。
- 客户端断开观察连接不会取消模型任务。收到 `turn.logical_settled` 后才结束逻辑轮次并启动本地 FIFO；连接关闭、`turn.done` 或 `turn.failed` 不能替代结算事件。
- `/fork` 在数据库中原子复制 canonical Transcript。响应丢失时使用相同 `request_id` 和分叉边界重试；活动源 Session、目标冲突、身份不匹配和缺少可分支输入分别返回稳定错误，不覆盖已有会话。
- 自动压缩发生在当前用户消息写入前，摘要和 Transcript 使用 CAS 提交；快照冲突、压缩失败或无收益时保留现有历史，不中断当前 Turn。压缩状态使用 `lifecycle.display`，不改变正文、工具或结算语义。
- Worker 接管先从持久 execution checkpoint 恢复，再创建模型客户端和会话 store。需要重新执行模型或工具时递增 `presentation_epoch` 并先发送 `presentation.superseded`；仅执行 `terminal_ready` 的幂等终结不创建新展示 Attempt。未形成安全点的 Transcript 尾部必须在接管事务中丢弃。

### Session Fork

`POST /fork` 请求使用源会话的 `cid`、`sid`，必须携带稳定的 `request_id` 和 `prompt_source`，可选携带 `before_turn_id`、`target_cid` 和 `target_sid`。服务端原子复制指定轮次之前的 canonical messages，返回目标 `data.cid`、`data.sid`；历史展示事件不复制，目标流从 `conversation.forked` 开始。

- `target_cid` 和 `target_sid` 必须成对出现，且目标标识不能单独复用源标识；源/目标关联段必须一致并符合各自 ID 格式。
- `prompt_source=none` 只用于完整分支；指定 `before_turn_id` 时必须明确使用 `server` 或 `client`。`server` 返回可重新提交的 `message`、`attachments` 和 `extras`，不返回模型配置、工具定义、执行环境或源 metadata；`client` 由调用方持有原始输入，服务端只验证边界，不返回 prompt。
- 可以在第一条用户消息之前分叉，此时 `copied_turns` 和 `copied_items` 可以为 `0`。源忙、目标已存在、源上下文不存在或分叉点不属于源会话时分别返回确定性错误，绝不覆盖目标会话。

### Conversation Compaction

- 每次 `/mind-chat` 采样前检查 canonical history；达到字符压力或最近 handoff 后用户轮次阈值时生成 Memento handoff summary。活动 Turn 不设置固定模型或工具轮数上限，但连续工具轮次无执行事实时进入无工具收敛阶段。
- 自动压缩在当前用户消息写入前完成，因此当前输入不会同时进入 summary 和本轮 user message。摘要生成、replacement 校验和消息快照 CAS 必须全部成功才采用压缩结果。
- 压缩期间历史发生变化时拒绝过期摘要；压缩失败、无收益或存在未闭环工具调用时使用确定性上下文裁剪，不中断对话。手动 `/compact` 与活动 `/mind-chat` 互斥，状态通过 `lifecycle.display` 或独立 `conversation.compact.*` 事件表达。

### Worker Recovery

- execution checkpoint 是服务端内部安全点，不新增客户端事件类型。安全点覆盖压缩提交、模型采样前、模型输出提交、工具等待、审批等待、工具结果提交和终结前，并与 canonical Transcript、`event_seq` 和 continuation 一起以 CAS 持久化。
- 接管 Attempt 必须先在 PostgreSQL 恢复最后安全点，再创建模型客户端和会话 store；Redis 清空不改变恢复结果。安全点后只有部分 canonical Item 落库时，接管事务丢弃未提交尾部，已形成安全点的上下文不得回退。
- `terminal_ready=true` 表示模型与工具阶段已完成。接管 Worker 只执行幂等终结事务，不再次采样模型；Turn 终态后删除内部 checkpoint，但终态事件和 canonical Transcript 继续保留。

## Event Catalog

### Event Families

核心事件族：

- `turn.*`：轮次生命周期
- `text.*`：可见正文与正文元信息
- `tool.*`：工具调用、审批与结果
- `lifecycle.*`：服务端阶段性状态提示
- `presentation.*`：展示 Attempt 的替代边界
- `stream.*`：传输恢复和事件缺口控制信号

常见事件：

`turn.start`、`turn.thinking`、`turn.retrying`、`turn.input.accepted`、`turn.done`、`turn.failed`、`turn.logical_settled`、`turn.reconciliation_required`、`presentation.superseded`、`lifecycle.display`、`text.delta`、`text.done`、`text.meta`、`tool.calls.start`、`tool.call`、`tool.calls.done`、`tool.approval_required`、`tool.output`、`tool.builtin.call`、`tool.builtin.done`、`stream.gap`。

### Event Semantics

#### `turn.start` and `presentation.superseded`

- `turn.start` 表示当前逻辑轮次开始，必须原样回显请求的 `turn_id`。
- 每次真正重新进入模型或工具执行的 Worker Attempt 使用递增的 `presentation_epoch`。当 epoch 大于 1 时，先发 `presentation.superseded`，再发新的 `turn.start`；客户端保留被取代 Attempt 的审计，但不得把它并入最终 canonical 回复或后续模型上下文。
- `presentation.superseded.superseded_epoch` 必须小于当前事件的 `presentation_epoch`。该事件不要求客户端清屏或删除 scrollback，替代正文必须进入独立 assistant block。

#### `turn.retrying`

- 表示 provider 请求失败后，服务端将以同一模型 round 的后续 attempt 重新发起请求。重试不按 HTTP 状态码、异常类名或 provider 错误类型白名单筛选；取消和本地请求前置校验除外。
- 必须携带 `round`、大于 1 且不超过 `max_attempts` 的 `attempt`、非负 `retry_in_ms`、稳定 `reason` 和同值 `error_type`。`error_type` 是客户端可直接展示的稳定错误类别，失败事件中的 `error.type` 与它一致。
- 客户端只替换同一 `presentation_epoch + round` 中更早 attempt 的正文；前序已完成 round 保留。首个正文前重试不得创建空 assistant block。
- 该事件是瞬时事件，不属于 `/turn/status` 的持久状态；退避期间 Turn 状态仍为 `running`。

#### `lifecycle.display`

用于传递服务端阶段性提示。常见字段为 `display.text`、`code`、`phase` 和 `trigger`。自动压缩使用 `code=context_compaction`，`phase` 为 `started`、`completed` 或 `skipped`，`trigger` 为 `automatic` 或 `manual`；完成时只可携带条目数和字符数等统计，不得携带摘要正文。该事件可以出现在 `turn.start` 前，但必须带当前 `turn_id`，不改变正文、工具或结算语义。

#### Text events

- `text.delta` 承载正文增量，客户端按 `segment_id` 追加。
- `text.done` 只表示当前文本段结束，不承载 citations、sources 或其他补充信息。
- `text.meta` 承载当前 `segment_id` 的 `annotations`、`citations`、`sources`、`source_count` 或 `builtin_call_ids`。

#### Tool batch events

- `tool.calls.start` 表示服务端已经在一个事务中完成本批所有 `call_id` 的 pending、effect、事件和 outbox 登记。字段包括 `batch_id`、`call_ids`、`count`、`ready=true`，可选的 `timeout_sec` 只表示工具自身执行预算，不是结果投递期限。
- `tool.call` 字段固定包含 `cid`、`sid`、`turn_id`、`call_id`、`name` 和 `arguments`。普通客户端工具必须位于同一 `batch_id` 的 start/done 边界内。
- `tool.calls.done` 表示批次事件已经全部提交。客户端应校验收到的 `call_ids` 和 `count` 后再执行工具；不得在只收到单个 `tool.call` 时提前执行。
- 同一 `turn_id + call_ids` 派生稳定 `batch_id` 和事件幂等键。Worker 恢复可以重新进入准备流程，但不得创建第二组权威 `tool.call` 事实。

#### `tool.output` and `/tool-result`

- `tool.output.status` 只允许 `completed`、`failed`、`declined` 或 `cancelled`。`declined` 表示工具未执行且仅拒绝当前调用；`cancelled` 表示 Ctrl-C 或全局中断取消。
- `/tool-result` 请求包含顶层 `request_id`、`cid`、`sid`、`call_id`、`name`、`ok`、`result` 和同级 `additional_context`。`result` 使用统一结果信封，包含 `ok`、`tool`、`source`、`args`、`text`、`attachments` 和 `data`；进程、补丁、图片和子代理等工具的业务字段放在 `result.data`。
- `result.ok` 必须等于外层 `ok`，`result.tool` 必须等于外层 `name`。进程、补丁、图片和子代理等工具的业务字段只能放在 `result.data`。
- PostToolUse 上下文必须与工具结果在同一次 `/tool-result` 请求中提交到同级 `additional_context`；禁止第二次请求单独补交 Hook 内容，也不接受工具级 `system_message`。
- `request_id` 只承担传输幂等；`cid + sid + call_id` 是业务唯一身份。同一 request_id 和内容重试返回 `already_received`；不同内容返回 `409 request_id_conflict`；不同 request_id 不得覆盖首个结果。
- 成功状态仅为 `matched` 和 `already_received`。`missing`、`not_ready` 为瞬态且不得写负缓存；`cancelled`、`turn_closed`、`tool_call_execution_timed_out` 和 `already_completed` 必须保持确定性分类。交互型 `waiting_result` 没有墙钟 TTL，托管工具才受执行预算限制。
- `/tool-result/status` 按 `cid + sid + call_id` 返回调用、Turn、首次 `request_id`、`completed_at`、`execution_deadline_at`、失败原因和 effect 权威状态。该字段仅表示托管工具的执行预算截止点；交互型客户端工具为 `null`，不因墙钟时间自动过期。结果投递无法确认时进入 reconciliation，不得直接转换为普通 `turn.failed`；未知 effect 复用 `/effect/reconcile` 收束。

#### `tool.approval_required`

- 它只转发本地核心产生的审批请求，服务端负责持久化、广播和等待决定，不作安全裁决。信封固定带 `approval_id`、`call_id`、`turn_id`、`kind`、`started_at_ms`、`available_decisions`、`status` 和 `ack`。
- `kind` 只允许 `command`、`write_stdin`、`apply_patch`、`network_access`、`request_permissions` 和 `mcp_tool_call`；每种动作使用自己的结构化载荷，缺少 `kind` 或动作字段时拒绝，不推断为命令审批。
- 实时请求为 `status=pending, ack=null`。决定只允许动作定义的合法值；服务端不按墙钟时间生成 `expired`。
- `/tool-approval` 的 `additional_context` 与决定一起保存并在结果回灌时透传。审批事件发送失败、显式取消或 Worker 中断时，pending 记录由 lease 保护地收束为 `cancelled`。
- `decline` 只关闭当前工具 Item；`cancel` 关闭工具 Item 后中断同一 Turn。响应 ACK 表示同步完成，不表示服务端作出安全裁决。

#### Built-in tools and terminal events

- `tool.builtin.call` / `tool.builtin.done` 描述 provider built-in tool（如 `web_search_call`、`file_search_call`、`code_interpreter_call`）的过程和完成状态，常见字段包括 `builtin_call_id`、`builtin_type`、`status`、`action`、`queries`、`domains`、`sources`、`code`、`container_id`、`output_count` 和 `error`。
- `turn.done` 表示当前轮次完成，通常为 `status=completed`，控制接口取消时为 `interrupted`。
- `turn.failed` 表示确认的轮次失败；事件必须携带 `error_type`，并在可用时携带 `error_source`、`status_code`、`request_id` 和 `retryable`。工具投递未知、外部 effect 未核对或 reconciliation 等状态不得伪装为该事件。
- `turn.input.accepted` 表示 mailbox 输入已在一次模型采样前写入 canonical Transcript；每个被消费的 `client_message_id` 都必须在 `turn.logical_settled` 前收到该事件。
- `turn.reconciliation_required` 表示外部 effect 可能已发生但结果不可判定。它不是普通工具失败，服务端暂停 finalization/settlement，待 `/effect/reconcile` 得到确定结论。
- `turn.logical_settled` 是唯一逻辑结算事实；它、Turn 终态和 Session idle 在同一事务内提交，每个逻辑轮次恰好一次。`next_input` 返回未赶上采样的第一项，其余输入通过 `/turn/reconcile` 恢复。

### Ordering And Client Requirements

推荐按以下顺序理解一个正常事件流：

1. `turn.thinking`
2. `turn.start`
3. 可选 `lifecycle.display`
4. 可选 `tool.builtin.call` / `tool.builtin.done`
5. `text.delta`（零个或多个）
6. `text.done`
7. 可选 `text.meta`
8. `tool.calls.start`
9. `tool.call`（一个或多个）
10. `tool.calls.done`
11. 可循环出现的 `tool.approval_required` / `tool.output`
12. 可选 `turn.input.accepted`
13. `turn.done` 或 `turn.failed`
14. `turn.logical_settled`

`event_seq` 不定义领域事件的先后类型，它是跨 Turn 的持久传输序号和恢复游标。客户端必须按 `event_seq` 去重、按 `after_seq` 恢复，并严格校验 `cid/sid/turn_id`。`text.meta` 必须附着 `segment_id`；`tool.call/tool.output` 只表示本地 function tool；provider 原生事件只能在服务端内部映射。

provider 无法稳定下发 built-in tool 过程时，服务端可以根据完成响应合成 `tool.builtin.call/done`；原生 SDK 事件不得产生新的客户端事件族。`turn.done` 和带 provider 响应的 `turn.failed` 可保留响应元数据，但不得下发 provider 原始响应正文。

### Example Stream

```jsonl
{"type":"turn.start","proto":"mind.chat","cid":"c","sid":"s","turn_id":"turn_1","event_seq":1,"presentation_epoch":1,"round":1}
{"type":"text.delta","proto":"mind.chat","cid":"c","sid":"s","turn_id":"turn_1","event_seq":2,"presentation_epoch":1,"item_id":"turn_1:s1","item_kind":"text","item_status":"in_progress","segment_id":"turn_1:s1","text":"答案正文"}
{"type":"text.done","proto":"mind.chat","cid":"c","sid":"s","turn_id":"turn_1","event_seq":3,"presentation_epoch":1,"item_id":"turn_1:s1","item_kind":"text","item_status":"completed","segment_id":"turn_1:s1"}
{"type":"tool.calls.start","proto":"mind.chat","cid":"c","sid":"s","turn_id":"turn_1","event_seq":4,"presentation_epoch":1,"batch_id":"turn_1:tool-round-1","call_ids":["call_1","call_2"],"count":2,"ready":true}
{"type":"tool.call","proto":"mind.chat","cid":"c","sid":"s","turn_id":"turn_1","event_seq":5,"presentation_epoch":1,"item_id":"call_1","item_kind":"tool_call","item_status":"waiting_result","call_id":"call_1","name":"go_home","arguments":{}}
{"type":"tool.call","proto":"mind.chat","cid":"c","sid":"s","turn_id":"turn_1","event_seq":6,"presentation_epoch":1,"item_id":"call_2","item_kind":"tool_call","item_status":"waiting_result","call_id":"call_2","name":"inspect_page","arguments":{}}
{"type":"tool.calls.done","proto":"mind.chat","cid":"c","sid":"s","turn_id":"turn_1","event_seq":7,"presentation_epoch":1,"batch_id":"turn_1:tool-round-1","call_ids":["call_1","call_2"],"count":2}
{"type":"turn.done","proto":"mind.chat","cid":"c","sid":"s","turn_id":"turn_1","event_seq":8,"presentation_epoch":1,"status":"completed"}
{"type":"turn.logical_settled","proto":"mind.chat","cid":"c","sid":"s","turn_id":"turn_1","event_seq":9,"presentation_epoch":1,"status":"completed","next_input":null}
```

## Provider Stream Retry

- provider 流对所有 provider 失败事件执行有界重试，不按错误类型筛选；本地 context budget、取消和已经进入不可重放的 provider 内置工具屏障除外。每个失败事件都必须带 `error_type`、`error.type`、`error.source` 和 `error.retryable`。
- Responses provider 内置工具一旦开始执行即形成自动重放屏障；后续断流直接失败收口，不重新触发搜索、代码解释器或图片生成。客户端本地工具只有在完整模型响应提交后才会下发，不属于该屏障。
- `max_retries` 默认允许首次请求之外再重试 5 次；`stream_max_attempts` 默认允许一次模型流最多 5 次 provider attempt。`stream_retry_max_elapsed_sec` 只限制首次失败后的退避预算；这些设置均不限制 Turn 总时长、健康流持续时间或工具轮数。
- 每次重试前先持久化 `turn.retrying`，再启动后续 provider attempt。事件携带模型 `round`、该 round 内的 `attempt`、`max_attempts`、`retry_in_ms`、`reason` 和 `error_type`；收到该事件即表示原子替换当前 round 的既有 attempt，后续模型 round 必须从 attempt 1 重新计数。
- provider 已产生部分正文时允许替换当前回答；客户端必须在同一 Turn 内隔离先前 provider attempt，不能把先前正文并入最终回答。首个正文前重试不得制造空展示块。
- Worker 进程退出不属于 provider 内部重试。接管进程通过 lease 接管并递增持久 Worker Attempt；只有重新进入模型或工具执行时才创建展示 epoch，并先发送 `presentation.superseded`。`terminal_ready` 的纯终结接管不创建展示 epoch。
- `/turn/status` 不增加 `retrying` 状态；provider 退避期间持久 Turn 状态保持 `running`，从而避免事件类型与控制面状态混用。

## Process Roles And Scheduling

- `RUNTIME_PROCESS_ROLE` 只允许 `http`、`worker` 或 `all`，未配置时默认为 `all`，保持 `uvicorn main:app` 的单进程运行方式。
- `http` 角色提供路由、SSE、attach/replay、命令写入和事件 relay，但不创建本地 Worker Scheduler，也不执行事件投递状态维护。`POST /mind-chat` 先把 Turn 持久化为 `queued`；本地 wake 只是可选延迟优化，不是任务交付条件。
- `worker` 角色通过 `python worker.py` 启动无 HTTP 路由的独立执行进程，执行 outbox 派发和事件投递状态维护，但不订阅无本地客户端消费者的 Redis relay。数据库轮询是领取 Turn 的权威机制，因此 HTTP 与 Worker 可以独立启动、滚动重启和扩缩容。
- `all` 同时承担 HTTP 与 Worker 职责，仅用于单进程运行或本地开发。生产环境可以分别使用 `RUNTIME_PROCESS_ROLE=http` 启动 `uvicorn main:app`，并使用 `python worker.py` 启动一个或多个 Worker。
- Worker 正常退出时取消本地 claim 生命周期并主动 relinquish 尚未终结的租约；进程被强杀时不依赖清理回调，其他 Worker 在 lease 过期后接管。所有接管继续受 `worker_id + lease_token` fencing 约束。

## Ownership And Tool Durability

- `POST /events-ingest`、`POST /tool-result`、`GET /tool-result/status`、`POST /tool-result/renew` 和 `POST /tool-approval` 必须使用全局认证得到的应用标识校验目标 Session/Turn owner；所有权不匹配返回 `403`。
- `POST /turn/approval-snapshot` 同样按认证应用校验 Turn owner；不存在的 Turn 返回 `404`，不属于当前应用返回 `403`。
- `/mind-replay` 保持可供报告页访问，但只接受服务端签发、与目标 Session 坐标绑定且未过期的 `vt`；仅凭 `cid/sid` 不得读取事件。
- 客户端工具调用和服务端托管工具都必须在执行或下发前写入 `runtime_tool_calls`。`request_id` 只负责传输幂等，`cid + sid + call_id` 是结果业务唯一身份；后续 Turn 复用该身份时只返回冲突，不得改写原调用或首个结果。Worker 写入同时受 lease fencing 保护，幂等内容指纹统一使用 SHA-256。
- 客户端工具的单次与批次派发共用 `runtime_worker_prepare_tool_dispatch`：工具调用登记、效果登记和派发权获取是同一个数据库事务，任一步失败均不留下孤立 pending 或 effect。普通客户端工具批次再由 `runtime_worker_prepare_tool_batch` 在同一事务内写入全部 `tool.calls.start`、`tool.call`、`tool.calls.done` 和 outbox；任一调用、效果或事件冲突时整批回滚，只有事务提交后才允许发布本地实时事件。
- `reconciliation_required` 停止客户端、托管工具和通用 effect 的全部新准备；`finalizing` 停止全部新工具调用。状态门禁发生在任何 pending/effect 写入前，暂停或最终化不得留下孤立调用。
- 交互型 `waiting_result` 调用不按墙钟时间过期，也不需要续期；调用只在收到结果、明确取消、所属 Turn 关闭或进入 reconciliation 时收束。`POST /tool-result/renew` 仅适用于带执行预算的托管工具，并同时校验 Session owner、Turn、调用身份、登记 Worker 和当前 lease。
- 续期命令按独立 `request_id` 保存成功结果以及身份冲突、终态、Turn 关闭和预算耗尽等确定性回执；`missing`、`not_ready` 与 `lease_lost` 不写负缓存。交互调用不存在“结果期限耗尽”状态。
- 恢复时，已有 `result_received` 的托管工具直接复用持久结果；客户端工具批次使用由 `turn_id + call_ids` 派生的稳定事件幂等键，重复恢复不得重复创建 `tool.call` 事实。断线方使用 `GET /tool-result/status?cid=...&sid=...&call_id=...` 查询调用、Turn 和 effect 权威状态；该查询以单个数据库快照计算 Turn 关闭和执行预算状态，不加写锁、不修改调用记录，`not_ready` 同时返回仍可继续准备调用的活动 `turn_id/turn_status`。Turn 进入终态或结算完成时，数据库在同一事务内把 `waiting_result` 收束为 `turn_closed`，并把仍为 `dispatching` 的客户端 effect 标为 `unknown`；查询也会保守识别终态快照中的未确认派发。返回 `effect_id` 且 `reconciliation_required=true` 时复用 `POST /effect/reconcile` 收束，不新增第二套核对状态机，且核对不得重开已确定的 Turn 终态。

## Transcript And Execution Checkpoint

- `runtime_transcripts` 是会话 canonical Transcript 的唯一权威源；`ChatStore` 的 Redis 列表只作为可丢弃镜像。读取时即使 Redis 已清空也必须从 PostgreSQL 恢复。
- Transcript 的 `version` 是 CAS 版本；compact 和 fork 不得使用 Redis etag 或连接生命周期作为一致性依据。
- `runtime_execution_checkpoints` 每个 Turn 只保存一行 Worker 内部执行安全点，以 `checkpoint_version` CAS 原地推进，不通过 HTTP 暴露。
- 执行安全点边界为 `turn_started`、`compaction_committed`、`model_ready`、`model_output_committed`、`tool_waiting`、`approval_waiting`、`user_waiting`、`tool_results_committed` 和 `finalizing`。该行同时快照 canonical Transcript、`event_seq` 和 continuation；`approval_waiting` 只表示等待客户端决定，不表示服务端策略判断。
- Worker 每次写入执行安全点都必须校验当前 `worker_id + lease_token + lease_expires_at`，并对前一 `checkpoint_version` 执行 CAS。失去租约的 Worker 不得覆盖接管 Worker 的安全点。
- Worker 开始 Attempt 前必须读取最后执行安全点；若 Transcript 存在未形成安全点的尾部，数据库将其恢复为安全点快照并递增 Transcript CAS 版本。Redis 内容不参与恢复判断。
- `model_output_committed` 或 `finalizing` 携带 `terminal_ready=true` 时，continuation 同时保存产生该结果的 `presentation_epoch`。接管 Worker 只重做幂等终结事务，不得再次调用模型，并用该 epoch 提交 `turn.logical_settled`；再次接管和重写 `finalizing` 安全点也不得把 Worker Attempt 覆盖为其他展示 epoch。等待中的工具和审批从 canonical pending call 与 durable tool/approval record 恢复。
- Turn 进入终态后立即删除其 execution checkpoint；权威事件、Turn 状态和 canonical Transcript 不受影响，仍可完成终态查询与事件回放。
- `POST /fork` 在 PostgreSQL 中原子复制权威 Transcript 并创建目标 Session；Redis 只在事务提交后刷新为镜像。
- 本地工具派发使用服务端内部效果账本；效果标识、重放策略和派发状态不进入客户端 `tool.call`。审批请求由本地核心产生后通过 `tool.approval_required` 转发，服务端不参与安全判断。
- 审批信封固定携带 `approval_id`、`call_id`、`turn_id`、`kind`、`started_at_ms`、`available_decisions`、`status` 和 `ack`。动作联合类型只允许 `command`、`write_stdin`、`apply_patch`、`network_access`、`request_permissions`、`mcp_tool_call`，缺少 `kind` 或动作必填字段时拒绝请求，不推断为命令审批。
- 六类动作分别保存自己的载荷。补丁使用 `patch + files`；网络使用 `target + host + protocol + port`，并在允许策略决定时携带 `proposed_network_policy_amendment`；权限申请使用可空的 `environment_id/cwd` 和原生结构化 `permissions`，文件系统目标按 `path/glob_pattern/special` 判别；MCP 调用使用 `server + tool_name + arguments + mcp_request_id`，其中 `arguments` 必须存在但可为任意 JSON，并可携带 connector、账号、工具标题、工具描述和行为提示字段。网络、权限和 MCP 不借用 `command` 表达。
- 审批请求先持久化为 pending 再发送事件；事件发送失败、显式设置的等待超时或 Worker 中断时，通过 lease 保护的取消过程收束为 `cancelled`，重复取消保持幂等。
- `runtime_reconcile_tool_approval_snapshot` 在单个数据库事务中读取 Turn 水位和该 Turn 的审批记录；已收束 Turn 中残留的 pending 记录收束为 `cancelled`，活动 Turn 的 pending 审批保持等待。返回的每个数组项就是完整审批信封，不附加数据库记录字段。
- 决定按动作严格区分：命令保留执行策略修订；网络只能提交 pending 信封已声明的 `{host, action: allow|deny}` 提案；权限授予必须提交 scope、permissions 和 strict_auto_review，且 permissions 不得超出 pending 申请；MCP 仅允许 `accept`、`decline`、`cancel`，并必须回传服务器、工具、参数与请求关联标识。`runtime_tool_approvals` 保存完整动作信封、kind 和结构化决定，幂等指纹覆盖完整决定 JSON；语义重复请求的响应 ACK 使用当前请求幂等键，权威快照不改写首次决定。
- 普通客户端工具不经过服务端审批判定；正常下发仍使用 `tool.call`，审批请求事件只承担跨服务端事件面的转发职责。
- `replay` 只允许 `safe` 或 `manual`。只读本地操作可以使用 `safe`；工作区、进程和外部操作必须使用 `manual`，该分类由工具效果提示决定。
- 供应商已经成功但效果账本无法提交时，Turn 进入 `reconciliation_required`，服务端发出 `turn.reconciliation_required` 并停止 finalization/settlement。客户端普通工具和服务端合成的客户端工具共用同一不确定结果收束逻辑；超时、读取异常或无效持久结果不得降级为普通工具失败。核对方使用 `POST /effect/reconcile` 提交 `committed`、`failed` 或 `retry`；该端点按认证主体校验效果所有权且不依赖原 Worker lease。client effect 的 `committed/failed` 必须携带完整 `/tool-result` payload，数据库使用与 `/tool-result` 相同的信封校验器和 SHA-256 业务指纹，并在同一事务内闭合 `runtime_tool_calls`；client effect 不接受 `retry`。

## Durable Effects

- `runtime_effects` 是外部效果的权威账本；`runtime_tool_calls` 继续负责工具请求与结果，不承担效果状态机。效果状态只允许 `prepared -> dispatching -> committed/failed/unknown -> reconciled`。活动 Turn 中已派发调用因执行预算耗尽或取消时，数据库把客户端 `dispatching` 转为 `unknown` 并将 Turn 暂停为 `reconciliation_required`；存在多个 `unknown` 时必须全部核对后才允许恢复，任何未决 effect 或 `waiting_result` 调用都禁止进入成功 finalization。Turn 已终止时保留其终态，只开放核对收束。`prepared` 表示尚未派发的确定事实，不能伪装为普通工具失败。
- 效果按 `read_only`、`idempotent`、`non_replayable` 分类，并分别使用 `safe`、`provider_idempotent`、`manual` 重放策略。Worker 接管只能自动重放前两类；`manual` 在既有派发结果不明时必须进入 `unknown`。
- `provider_idempotency_key` 只为明确支持供应商幂等键的 `provider_idempotent` 效果稳定派生；`manual` client effect 必须为空。接管或网络重试不得把非幂等操作重新分类为供应商幂等。
- 服务端内部 effect class、scope、派发状态和供应商幂等键不得暴露给客户端；客户端提案标识只作为不透明同步数据保存。
- client effect 在 `dispatching` 后强杀时，若对应 `tool.call` 尚未写入权威事件流，接管 Worker 可以继续首次下发；事件已经持久化时只恢复等待，由 attach/replay 交付既有事件，不重新创建派发语义。
- 客户端结果写入 `runtime_tool_calls` 时，数据库在同一事务内把对应 client effect 提交为 `committed`。hosted/cloud effect 必须先提交账本结果，再闭环工具结果；进程在两步之间退出时，接管 Worker复用账本结果。
- 外部调用返回异常或成功后本地提交失败时，账本保守保留 `unknown`。不可重放效果不得自动重试；核对使用独立 `request_id`，只允许显式解析为 `committed`、`failed` 或 `retry`，相同请求幂等、不同内容冲突。client effect 只允许 `committed/failed`；核对请求的 Session、Call、工具名和 `effect_id` 由服务端持久账本匹配，`result_payload` 使用与 `/tool-result` 相同的结果结构，不携带 `execution`，否则返回 `409`。

## Stable Errors

| HTTP/status | Code | Meaning |
| --- | --- | --- |
| `401` | `authentication_required` | 缺少认证主体。 |
| `401` | `view_token_invalid` | 查看令牌无效、过期或与 Session 坐标不匹配。 |
| `403` | `authentication_invalid` | 应用凭据无效。 |
| `403` | `owner_mismatch` | Session/Turn 不属于当前认证主体。 |
| `403` | `lease_lost` | Tool Call 登记时的 Worker lease 已不是 Turn 当前 lease。 |
| `404` | `conversation_not_found` | 权威存储中不存在目标 Session 或其历史。 |
| `404` | `turn_not_found` | 权威存储中不存在目标 Turn。 |
| `404` | `prompt_not_found` | 指定源 Turn 不存在可分支的用户输入。 |
| `404` | `effect_not_found` | 权威账本中不存在目标 Effect。 |
| `404` | `tool_call_missing` | 当前 Session 中不存在目标 Tool Call；该投递错误可重试并查询权威状态。 |
| `404` | `tool_call_not_ready` | 活动 Turn 尚未完成目标 Tool Call 登记；不写负缓存，可稍后用同一命令重试。 |
| `404` | `approval_not_pending` | 目标 Approval 不存在或不再等待决定。 |
| `404` | `report_session_not_found` | 实时报告会话不存在或已过期。 |
| `404` | `manifest_not_found` | 运行时版本清单不存在。 |
| `404` | `manifest_host_not_found` | 清单中不存在目标宿主。 |
| `404` | `manifest_service_not_found` | 清单中不存在目标服务。 |
| `404` | `manifest_channel_not_found` | 清单中不存在目标发布通道。 |
| `404` | `manifest_package_not_found` | 清单中不存在目标平台安装包。 |
| `409` | `turn_already_active` | 同一 Session 已有非终态 Turn。 |
| `409` | `turn_id_reused` | `turn_id` 已在该 Session 使用。 |
| `409` | `request_id_conflict` | 幂等键被不同命令复用。 |
| `409` | `source_busy` | 分支源 Session 仍有活动 Turn。 |
| `409` | `target_session_matches_source` | 分支目标与源 Session 相同。 |
| `409` | `target_session_conflict` | 分支目标 Session 已存在。 |
| `409` | `fork_allocation_failed` | 无法分配分支 Session。 |
| `409` | `compact_conflict` | 压缩期间 Session 状态发生冲突。 |
| `409` | `conversation_not_compactable` | 当前 Session 尚不满足压缩条件。 |
| `409` | `effect_reconciliation_conflict` | 相同核对幂等键对应不同 Effect 结果。 |
| `409` | `effect_reconciliation_closed` | Effect 已闭环，不能再次核对。 |
| `409` | `effect_reconciliation_unavailable` | Effect 当前状态不允许核对。 |
| `409` | `tool_call_already_completed` | Tool Call 已有首个结果，后续请求不得覆盖。 |
| `409` | `tool_call_mismatch` | 工具结果身份与待处理 Tool Call 不匹配。 |
| `409` | `renewal_not_required` | 交互型 Tool Call 没有结果投递期限，无需续期。 |
| `410` | `tool_call_execution_timed_out` | 托管工具的执行预算已耗尽。 |
| `410` | `tool_call_cancelled` | Tool Call 已被显式取消。 |
| `410` | `tool_call_turn_closed` | Tool Call 所属 Turn 已关闭。 |
| `409` | `approval_decision_conflict` | Approval 已由不同决定闭环。 |
| `409` | `client_effect_retry_unsupported` | client effect 不能通过服务端重置本地 manual journal 后重放。 |
| `409` | `client_tool_result_invalid` | client effect 核对没有提供身份匹配的完整工具结果。 |
| `409` | `client_tool_call_missing` | client effect 对应的持久工具调用不存在。 |
| `409` | `client_tool_result_conflict` | client effect 核对结果与已有工具结果冲突。 |
| `422` | `request_validation` | 缺字段、未定义字段或字段格式不符合约束。 |
| `422` | `conversation_identity_required` | `/mind-chat` 缺少 `metadata.cid` 或 `metadata.sid`。 |
| `422` | `approval_decision_invalid` | Approval 决定不属于定义枚举。 |
| `500` | `manifest_invalid` | 运行时版本清单损坏或缺少必要字段。 |
| `500` | `tool_result_delivery_failed` | 持久工具结果写入返回未知状态。 |
| `502` | `compact_history_read_failed` | 无法读取待压缩的权威历史。 |
| `502` | `compact_history_write_failed` | 无法提交压缩后的权威历史。 |
| `502` | `compact_summary_failed` | 摘要生成失败。 |
| `503` | `runtime_unavailable` | 持久运行时依赖暂时不可用。 |
| `503` | `approval_snapshot_malformed` | 持久审批对账结果不符合当前协议。 |
| `503` | `approval_receipt_malformed` | 持久审批决定回执不符合当前协议。 |
| response status | `turn_not_active` | 当前不存在匹配的活动 Turn。 |
| response status | `turn_not_steerable` | Turn 尚未绑定执行者或已进入收敛阶段。 |
| response status | `turn_mismatch` | Session 活动 Turn 与请求 `turn_id` 不同。 |
| response status | `duplicate` | `client_message_id` 已被该 Turn 接收。 |

业务错误统一放在全局错误信封的 `details.code` 与 `details.message` 中。错误响应不得改变命令目标或重新解释未定义字段。

## Storage Boundaries

- PostgreSQL/Supabase：Session、Turn/Attempt 计数、Worker lease/fence、mailbox、控制命令、Tool/Approval、Transcript、Execution Checkpoint、终态、Event、Outbox 和 relay delivery cursor 的权威数据。
- Redis：跨实例事件实时通知、Transcript 镜像、缓存、限流和非权威协调锁；不是 Turn、Worker lease、Transcript、Event 或客户端交付水位的 authority。
- 运行时 8A 使用持久 Worker heartbeat 和 `runtime_operational_health` 聚合 0–4 的生产健康状态；内部健康、Prometheus 指标、告警阈值、灰度步骤与故障演练见 `RUNTIME_8A.md`。这些观测接口不改变客户端事件协议。
- Object Storage：大工具输出、截图与文件。
- SSE/WS：观察和命令传输，不承担任务生命周期。
