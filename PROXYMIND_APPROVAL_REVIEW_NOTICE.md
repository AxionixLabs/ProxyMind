# ProxyMind Approval Auto-Review 客户端改造通知

生效范围：AppServer 最新协议；不保留旧客户端兼容分支。

## 服务端契约

AppServer 只新增并持久发送以下自动评审通知：

- `tool.approval_review.started`
- `tool.approval_review.completed`

两类通知共同携带 `cid`、`sid`、`turn_id`、`event_seq`、
`presentation_epoch`、`review_id`、`approval_id`、`call_id`、
`target_item_id`、`kind`、`action` 和 `started_at_ms`。

`started` 只允许 `review.status=in_progress`，不得携带
`decision_source` 或 `completed_at_ms`。`completed` 增加
`completed_at_ms` 和固定值 `decision_source=agent`；终态只允许
`approved`、`denied`、`timed_out`、`aborted`。

`approved/denied` 必须携带 `risk_level`、`user_authorization` 和非空
`rationale`；`timed_out` 只携带非空 `rationale`；`aborted` 不携带风险结论。
`review_id` 是独立评审生命周期身份，不得复用 `approval_id`、`call_id` 或
`target_item_id`。

通知不是 Approval 决定，不改变本地审批事实、不授予权限、不触发工具执行。
服务端会在 `completed` 之后发送匹配的 `tool.approval_required`，并且只有收到
ProxyMind 本地 ApprovalCore 提交的 allow 决定后，才会持久化 Effect 并发送
`tool.call`。

## 与 Codex 的对应关系

`tool.approval_required` 是 AppServer 的统一审批信封，不对应 Codex 的某一条单独
事件。客户端应按 `kind` 映射到本地 ApprovalCore，不按上游事件名反向猜测动作：

| AppServer `kind` | Codex core / app-server 对应入口 |
| --- | --- |
| `command` | `ExecApprovalRequest` / `item/commandExecution/requestApproval` |
| `write_stdin` | `ExecApprovalRequest(kind=write_stdin)` / `item/commandExecution/requestApproval` |
| `network_access` | 带 `network_approval_context` 的 `ExecApprovalRequest` / `item/commandExecution/requestApproval` |
| `apply_patch` | `ApplyPatchApprovalRequest` / `item/fileChange/requestApproval` |
| `request_permissions` | `RequestPermissions` / `item/permissions/requestApproval` |
| `mcp_tool_call` | Codex 按功能开关使用 `ElicitationRequest` 或 `RequestUserInput`，AppServer 将其收敛为结构化 MCP 动作 |

自动评审通知对应 Codex 的 `GuardianAssessment` 生命周期及
`item/autoApprovalReview/started|completed` 行为，但使用 AppServer 自己的
`mind.chat` 事件坐标。这里对齐的是状态机、展示和 fail-closed 语义，不是复制
Codex JSON-RPC 线协议。

## 客户端必须收口

1. `target_item_id` 改为必填非空字段。started/completed 必须校验
   `review_id + approval_id + call_id + target_item_id + kind + action` 完整一致；
   缺失、漂移或冲突一律 fail-closed，不再按可选字段兼容。
2. 所有可见输出统一状态策略。`approved` 和 `aborted` 只清除活动评审状态，
   不写终端、纯文本或历史成功记录；`denied` 显示拒绝并阻止当前动作；
   `timed_out` 使用独立超时文案并 fail-closed。纯文本输出不得在 renderer 返回
   空块时生成兜底的 `approval review approved/aborted` 文本。

客户端继续以本地 ApprovalCore 和 Effect gate 为唯一执行授权边界：服务端
reviewer 给出 `approved` 时仍须校验当前本地策略、动作指纹与授权范围；只有本地
账本形成 allow 事实后才能消费后续 `tool.call`。`denied`、`timed_out` 或身份不匹配
不得执行 Effect。

## 验收用例

- started 缺少 `target_item_id` 时协议解析失败。
- completed 与 started 的任一身份字段或 `action` 不一致时 fail-closed。
- `approved -> tool.approval_required -> tool.call` 仅在本地 allow 后执行一次。
- reviewer approved 但本地策略拒绝时，提交 decline 且 Effect 执行次数为零。
- `denied` 和 `timed_out` 分别产生拒绝、超时展示，Effect 执行次数为零。
- `approved`、`aborted` 在 TUI、纯文本及可见历史中均不生成成功/取消记录。
- 断线重放相同 `review_id` 不重复提交决定、不重复执行 Effect。
