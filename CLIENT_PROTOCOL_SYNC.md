# AppServer 客户端协议同步说明

本文档是给客户端维护者的交接清单。它描述 AppServer 当前自有可靠流式协议的客户端行为要求，不复制 Codex wire protocol，也不保留旧协议兼容分支。

## 1. 权威来源和绝对路径

客户端同步必须以以下文件为准：

1. `D:\PycharmProjects\AppServer\services\llm\PROTOCOL.md`：唯一规范源，定义实体、状态、事件、工具结果、恢复和错误码。
2. `D:\PycharmProjects\AppServer\openapi.json`：机器可读的 HTTP 请求和响应契约。
3. `D:\PycharmProjects\AppServer\schemas\mind_events.py`：请求、响应、事件和工具结果模型。
4. `D:\PycharmProjects\AppServer\routers\rt_mind.py`：Mind 路由及 HTTP 状态映射。

接口、字段或错误码发生变化时，服务端必须在同一次变更中同步上述规范、OpenAPI 和测试。

客户端当前对应的实现位置：

| 客户端职责 | 绝对路径 |
| --- | --- |
| SSE 事件解析、公共字段、序号和 Item | `D:\PycharmProjects\ProxyMind\mind_nova\stream_events.py` |
| `/mind-chat`、断线恢复和 `/mind-attach` | `D:\PycharmProjects\ProxyMind\mind_nova\requests\chat.py` |
| `/mind-review` 审查命令和工作区快照 | `D:\PycharmProjects\ProxyMind\mind_nova\requests\review.py` |
| Turn status、steer、interrupt | `D:\PycharmProjects\ProxyMind\mind_nova\requests\turn_control.py` |
| `/tool-result`、`/tool-approval`、状态查询和错误解析 | `D:\PycharmProjects\ProxyMind\mind_nova\requests\tools.py` |
| Effect reconcile | `D:\PycharmProjects\ProxyMind\mind_nova\requests\effects.py` |
| 服务端工具端点常量 | `D:\PycharmProjects\ProxyMind\mind_nova\const.py` |
| 客户端工具结果构造 | `D:\PycharmProjects\ProxyMind\mind_app\client_tools\result.py` |
| 工具调用方 | `D:\PycharmProjects\ProxyMind\mind_app\client_tools\coding\native.py`、`D:\PycharmProjects\ProxyMind\mind_app\client_tools\subagents.py`、`D:\PycharmProjects\ProxyMind\mind_app\client_tools\planning.py`、`D:\PycharmProjects\ProxyMind\mind_app\client_tools\update_plan.py`、`D:\PycharmProjects\ProxyMind\mind_app\client_tools\view_image.py` |
| Item 展示和 Attempt 替代 | `D:\PycharmProjects\ProxyMind\mind_app\output\jsonl.py`、`D:\PycharmProjects\ProxyMind\mind_app\output\content.py` |

## 2. 身份和生命周期

| 对象 | 唯一身份 | 客户端规则 |
| --- | --- | --- |
| Session | `cid + sid` | 跨多个 Turn 共享 `event_seq` 水位。 |
| Turn | `cid + sid + turn_id` | 每个逻辑轮次使用不同 `turn_id`；不得用 `run_id` 替代。 |
| Event | `cid + sid + event_seq` | `event_seq` 是传输和恢复游标，不是 Item ID。 |
| Item | `item_id` | 跨重连、回放和 Worker 接管保持稳定。 |
| Tool Call | `cid + sid + call_id` | 结果业务唯一身份，首个结果不可覆盖。 |
| Command | `request_id` | 只作为传输幂等键，不作为业务对象身份。 |

可展示事件必须携带 `item_id`、`item_kind`、`item_status`：

| 事件 | Item ID | 类型 | 常见状态 |
| --- | --- | --- | --- |
| `text.delta/done/meta` | `segment_id` | `text` | `in_progress` -> `completed` |
| `tool.call` | `call_id` | `tool_call` | `waiting_result` |
| `tool.output` | `<call_id>:output` | `tool_output` | `result_received` -> `completed/failed/cancelled` |
| `tool.approval_required` | `approval_id` | `approval` | `waiting_approval` |
| `tool.builtin.call/done` | `builtin_call_id` | `builtin_tool` | `in_progress` -> `completed/failed` |

Turn 的终态为 `completed`、`failed`、`interrupted` 或 `cancelled`。客户端只能在收到 `turn.logical_settled` 后结束逻辑轮次；连接关闭、`turn.done` 和 `turn.failed` 都不能单独启动下一轮。

## 3. SSE 信封、去重和恢复

除传输层 `ping` 外，业务事件必须包含 `proto=mind.chat`、匹配当前请求的 `cid`、`sid`、`turn_id`、正整数 `event_seq` 和正整数 `presentation_epoch`。

客户端必须：

- 严格校验事件坐标，不属于当前 `cid/sid/turn_id` 的事件直接作为协议错误处理。
- 按 `event_seq` 去重并保存最后一个已处理序号；不得用 Item ID 代替序号。
- 发现序号缺口、通知丢失、乱序、队列溢出或 Redis 重连时，从权威回放恢复，不依赖进程内事件队列补序。
- 断线后使用 `POST /mind-attach`，请求只包含 `cid`、`sid`、`turn_id`、`after_seq`；`after_seq` 使用最后确认的 `event_seq`。
- `gap=retained_prefix` 表示历史前缀已被裁剪，可以从返回的保留窗口继续；`gap=internal` 表示权威事件序列损坏，不能静默跳过。
- 收到 `presentation.superseded` 后保留旧 Attempt 的审计内容，但把后续正文放入新的 assistant 展示块。

正常事件理解顺序：

```text
turn.thinking -> turn.start -> lifecycle.display?
  -> tool.builtin.*? -> text.delta* -> text.done -> text.meta?
  -> tool.calls.start -> tool.call* -> tool.calls.done
  -> tool.approval_required / tool.output (可循环)
  -> turn.input.accepted? -> turn.done 或 turn.failed
  -> turn.logical_settled
```

## 4. HTTP 端点

| 端点 | 客户端要求 |
| --- | --- |
| `POST /mind-chat` | 顶层必须携带 `turn_id`，`metadata` 必须携带 `cid/sid`。完整语义相同的请求使用相同 ID 重试，不重复执行。 |
| `POST /mind-review` | 阶段 0 已冻结契约，阶段 1 完成服务端模型、路由、持久化和 OpenAPI 后启用；请求必须携带结构化 target、delivery 和 workspace snapshot。 |
| `POST /mind-attach` | 断线接入既有 Turn，只使用最后确认的 `after_seq`。 |
| `GET /mind-replay` | 使用与 `cid/sid` 绑定的短期 `vt` 和 `after_seq`。 |
| `GET /stream-events` | 使用 `vt`、`after_seq` 和 `event_seq` 去重。 |
| `GET /turn/status` | 创建响应丢失时确认 Turn 是否存在；存在则 attach，不存在才用原请求重试。 |
| `POST /tool-result` | 一次提交完整工具结果和 `additional_context`。 |
| `GET /tool-result/status` | 按 `cid/sid/call_id` 查询工具、Turn 和 Effect 权威状态。 |
| `POST /tool-result/renew` | 仅续期带执行预算的托管工具，不用于交互型客户端工具。 |
| `POST /tool-approval` | 提交审批决定，使用独立 `request_id` 幂等。 |
| `POST /turn/approval-snapshot` | 审批断线恢复前读取权威快照。 |
| `POST /effect/reconcile` | 外部效果不确定时完成权威核对，不创建第二套状态机。 |

## 5. 工具批次边界

客户端收到普通客户端工具时必须按以下顺序处理：

1. 收到同一 `batch_id` 的 `tool.calls.start`。
2. 收齐该批次全部 `tool.call`，校验 `call_ids` 和 `count`。
3. 收到 `tool.calls.done` 后再执行工具；不得只收到一个 `tool.call` 就执行。
4. 每个 `call_id` 只提交一次逻辑结果；网络重试复用同一 `request_id`。

`tool.calls.start` 表示服务端已在单一事务中登记全部 pending Tool Call、Effect、批次事件和 outbox。事务提交前不会向客户端发送批次事件，因此客户端不需要自行补建 pending。断线恢复或 Worker 接管不得创建第二组 `tool.call` 事实。

## 6. `/tool-result` 严格契约

服务端模型位置：`D:\PycharmProjects\AppServer\schemas\mind_events.py` 的 `ToolResultRequest` 和 `ToolResultEnvelope`。

请求结构：

```json
{
  "request_id": "tool_result_01",
  "cid": "c_01",
  "sid": "s_01",
  "call_id": "call_01",
  "name": "shell_command",
  "ok": true,
  "result": {
    "ok": true,
    "tool": "shell_command",
    "source": "client",
    "args": {"command": "echo ready"},
    "text": "ready",
    "attachments": [],
    "data": {"executed": true, "stdout": "ready\n", "stderr": "", "exit_code": 0}
  },
  "additional_context": ["PostToolUse context"]
}
```

规则：

- 外层只接受 `request_id`、`cid`、`sid`、`call_id`、`name`、`ok`、`result`、`additional_context`；未定义字段返回 `422`。
- `request_id` 长度为 8 到 160，字符只能是字母、数字、下划线和短横线。
- `result` 严格只接受 `ok`、`tool`、`source`、`args`、`text`、`attachments`、`data` 七个字段。
- `result.ok` 必须等于外层 `ok`；`result.tool` 必须等于外层 `name`。
- `stdout`、`stderr`、`exit_code`、`status`、`session_id`、`path` 等业务字段只能位于 `result.data`。
- `additional_context` 是与 `result` 同级的字符串数组。每次最多 32 项，单项最多 32000 字符，总长度最多 128000 字符。
- PostToolUse 必须和工具结果在同一次 `/tool-result` 提交；禁止第二次请求单独提交 Hook 内容。
- 不接受工具级 `system_message`，也不接受旧的扁平结果字段。

成功响应只有 `matched` 和 `already_received`：

```json
{"ok":true,"data":{"status":"matched","delivered":true,"already_received":false,"request_id":"tool_result_01"}}
```

相同内容重试时仅将 `status` 和 `already_received` 改为 `already_received` 和 `true`，不重复执行工具。

## 7. 幂等和错误分类

| 情形 | HTTP | `details.code` 或状态 | 客户端处理 |
| --- | ---: | --- | --- |
| 首次接收 | 200 | `matched` | 记录结果已被权威接收。 |
| 同 request_id、同内容 | 200 | `already_received` | 视为成功，不重复执行。 |
| 同 request_id、不同内容 | 409 | `request_id_conflict` | 停止该 ID 的重试，不覆盖原结果。 |
| 同 call_id、不同 request_id，首结果已存在 | 409 | `tool_call_already_completed` | 查询权威状态，不覆盖首结果。 |
| 工具名称不一致 | 409 | `tool_call_mismatch` | 检查 `name/call_id` 配对。 |
| 调用不存在 | 404 | `tool_call_missing`，`tool_status=missing` | 瞬态重试，不写本地永久负缓存。 |
| 调用尚未登记 | 404 | `tool_call_not_ready`，`tool_status=not_ready` | 稍后用原请求重试，不结束 Turn。 |
| 调用已取消 | 410 | `tool_call_cancelled` | 收束为工具取消。 |
| Turn 已关闭 | 410 | `tool_call_turn_closed` | 收束为 Turn 关闭。 |
| 托管执行预算耗尽 | 410 | `tool_call_execution_timed_out` | 收束为执行预算终态。 |
| 外部效果不确定 | 409 | `tool_result_reconciliation_required` | 查询状态并进入 reconcile，不生成普通 `turn.failed`。 |
| 所有权不匹配 | 403 | `owner_mismatch` | 检查应用身份和会话坐标。 |
| 请求结构非法 | 422 | `request_validation` | 修正 payload，不重试非法请求。 |

`missing` 和 `not_ready` 是瞬态状态，服务端不会写 `request_id` 负缓存。网络超时、连接断开或 404 结果无法确认时，客户端先调用 `/tool-result/status`，不得直接把 Turn 当作模型调用失败。

## 8. 状态查询、续期和审批恢复

`GET /tool-result/status?cid=<cid>&sid=<sid>&call_id=<call_id>` 返回 `data`：

```text
cid, sid, call_id, turn_id, name, tool_status, completion_mode,
turn_status, result_received, request_id, completed_at,
execution_deadline_at, failure_reason, effect_id, effect_status,
reconciliation_required
```

`tool_status`、`turn_status` 和 `reconciliation_required` 必须作为同一权威快照使用；查询不写入工具记录。`execution_deadline_at` 只适用于带执行预算的托管工具，交互型客户端工具没有墙钟投递 TTL。

`POST /tool-result/renew` 必须携带 `request_id`、`cid`、`sid`、`turn_id`、`call_id`、`name`、`extension_seconds`。服务端会校验 owner、工具身份、登记 Worker 和当前 lease；已完成、取消、Turn 关闭、预算耗尽、lease 丢失或生命周期耗尽时续期失败。

审批断线恢复顺序：

1. 调用 `POST /turn/approval-snapshot` 获取权威审批状态。
2. 使用客户端最后确认的 `after_seq` 调用 `POST /mind-attach`。
3. 只展示 `status=pending`；快照水位内的 `resolved/cancelled` 优先于旧 pending 重放。

## 9. 客户端同步验收清单

- [ ] 校验所有业务事件的 `proto/cid/sid/turn_id/event_seq/presentation_epoch`。
- [ ] 以 `event_seq` 去重和恢复，以 `item_id` 维护展示对象。
- [ ] 等待 `tool.calls.done` 后执行普通工具。
- [ ] 每个 `call_id` 只发送一次逻辑结果，重试复用同一 `request_id`。
- [ ] PostToolUse 与结果一次提交到同级 `additional_context`。
- [ ] 业务字段全部位于 `result.data`，不再发送旧扁平字段。
- [ ] 区分 `matched`、`already_received`、`missing`、`not_ready`、`cancelled`、`turn_closed`、`execution_timed_out`、`already_completed` 和 `reconciliation_required`。
- [ ] `missing/not_ready` 不写永久负缓存。
- [ ] 工具结果 404、超时或断线先查询 `/tool-result/status`，不直接结束 Turn。
- [ ] 未收到 `turn.logical_settled` 前不启动下一逻辑轮次。
- [ ] 断线恢复使用 `/mind-attach` 和最后确认游标。
- [ ] 交互型工具不调用 `/tool-result/renew`。

## 10. 服务端契约复核

```powershell
Set-Location 'D:\PycharmProjects\AppServer'
python -c "import json,main; current=main.app.openapi(); snapshot=json.load(open('openapi.json',encoding='utf-8')); print(current == snapshot)"
python -m pytest -q tests/test_tool_result_route.py tests/test_durable_tool_broker.py tests/test_runtime_effect_ledger.py tests/test_mind_runtime_boundaries.py
python -m pytest -q tests/test_runtime_event_plane.py tests/test_runtime_persistence.py
git diff --check
```

本文档是客户端交接清单，不替代 `D:\PycharmProjects\AppServer\services\llm\PROTOCOL.md`；出现差异时，以该规范和运行时 `D:\PycharmProjects\AppServer\openapi.json` 为准。
