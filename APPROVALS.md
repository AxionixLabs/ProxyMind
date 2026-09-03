# 自动审批对齐清单

状态：实施中（阶段 1 已完成）

本文定义 ProxyMind 自动审批评审与 `codex-main` 的语义和展示对齐范围，并作为本次实现、
测试和验收清单。线上字段最终以服务端正式协议为准；本文不赋予服务端安全裁决权，也不
改变本地 Sandbox、Approval、Effect 的所有权。

## 对齐基准

- [x] 已复核 `codex-main` 的 Guardian 审批顺序：Hook 优先，其次按策略选择自动评审或
  用户审批。
- [x] 已复核自动评审由本地 Core 编排；app-server 只转发 started/completed 通知，不
  执行工具、不修改审批事实。
- [x] 已复核 `in_progress`、`approved`、`denied`、`timed_out`、`aborted` 五种状态。
- [x] 已复核进行中 footer、并行评审聚合、通过后静默、拒绝和超时历史记录。
- [x] 已复核自动评审失败采用 fail-closed，只有本地确定的 allow 事实才能继续执行。

参考实现：

- `codex-main/codex-rs/core/src/tools/approvals.rs`
- `codex-main/codex-rs/core/src/guardian/review.rs`
- `codex-main/codex-rs/tui/src/chatwidget/tool_requests.rs`
- `codex-main/codex-rs/tui/src/chatwidget/status_state.rs`
- `codex-main/codex-rs/tui/src/history_cell/approvals.rs`
- `codex-main/codex-rs/app-server-protocol/src/protocol/v2/item.rs`

## 稳定边界

- [ ] `ApprovalCore` 继续拥有审批事实、决定校验、Session grant 和 Effect 执行门禁。
- [ ] 自动评审作为 `ApprovalReviewerPort` 的一个实现接入，不创建第二套审批状态机。
- [ ] protocol adapter 只校验并转换 wire 事件，不直接批准动作或执行工具。
- [ ] TUI 只消费 application presentation/activity，不读取原始服务端载荷推断许可。
- [ ] 服务端 `approved` 只是 reviewer 输入；本地决定与动作身份、类别不一致时必须拒绝。
- [ ] 没有匹配的自动评审结果时，按既有策略进入用户审批，不把缺失通知解释为允许。
- [ ] `denied`、`timed_out` 和评审异常均阻止当前动作；`aborted` 进入取消路径。
- [ ] 自动评审不得产生永久策略修改或 Session grant；此类授权仍只来自显式用户决定。

## 事件清单

- [ ] 新增 `tool.approval_review.started`。
- [ ] 新增 `tool.approval_review.completed`。
- [ ] 两类事件共同携带 `cid`、`sid`、`turn_id`、`event_seq`、
  `presentation_epoch`、`review_id`、`approval_id`、`call_id`、`target_item_id`、
  `kind`、`action` 和 `started_at_ms`。
- [ ] started 只允许 `review.status=in_progress`，不得携带决定来源或完成时间。
- [ ] completed 必须携带 `completed_at_ms` 和 `decision_source=agent`；状态只允许
  `approved`、`denied`、`timed_out`、`aborted`。
- [ ] `approved`、`denied` 必须携带 `risk_level`、`user_authorization` 和非空
  `rationale`；`timed_out` 必须携带非空 `rationale`；`aborted` 不伪造风险结论。
- [ ] `review_id` 是独立评审生命周期身份，不复用 `approval_id`、`call_id` 或
  `target_item_id`。
- [ ] 事件使用 `item_id=review_id`、`item_kind=approval`；started 为 `in_progress`，
  approved/denied 为 `completed`，timed_out 为 `failed`，aborted 为 `cancelled`。
- [ ] 同一 `review_id` 的重复事件幂等；字段或终态冲突作为协议错误处理。
- [ ] 服务端权威事件顺序中 completed 不得先于 matching started，完成时间不得早于开始时间。
  客户端允许因保留窗口裁剪而只收到自包含的 completed，并直接归约该终态。
- [ ] 对应 `tool.approval_required` 必须在 completed 之后发布，使客户端可在不阻塞
  事件读取的情况下消费 reviewer 结果。
- [ ] attach/replay 保持原 `event_seq` 顺序；重放不得重新打开已完成评审或重复展示历史。

## 活动状态样式

| 场景 | 标题 | 明细 | 历史区 |
| --- | --- | --- | --- |
| 单项评审中 | `Reviewing approval request` | 一行安全动作摘要 | 不写入 |
| 多项评审中 | `Reviewing N approval requests` | 最多三项，剩余显示 `+N more` | 不写入 |
| 部分评审完成 | 继续显示剩余评审数量 | 移除对应 `review_id` | 不写入 |
| 全部评审完成 | 恢复当前 reducer 推导状态 | 无 | 见终态规则 |

- [ ] 评审活动使用独立 `review_id` lease，不能借用交互审批卡的 `approval_id` lease。
- [ ] 并行评审按开始顺序稳定显示；更新同一 `review_id` 不改变位置。
- [ ] 活动明细不显示风险、解释或内部模型信息，只显示待评审动作摘要。
- [ ] 独占审批卡可以暂时遮挡活动区，但不能修改评审 reducer 状态。
- [ ] `presentation.superseded`、Turn 终态、输出会话关闭必须清除陈旧评审 lease。
- [ ] attach/replay 期间只归约状态，追平后一次性投影，不重启动画计时。
- [ ] `codex-main` 评审结束回落到 `Working`；ProxyMind 已统一基础活动语义，故回落到
  当前状态机推导的 `Thinking`、`Retrying` 或隐藏状态，不重新引入 `Working`。
- [ ] `Reviewing` 属于等待动画标题族，保持常规字重和现有扫光规则，不显示工具名称。

## 终态文案

| 终态 | 警告 | 动作记录 | 行为 |
| --- | --- | --- | --- |
| `approved` | 无 | 无 | 本地核心记录一次性 allow 后才可执行 |
| `denied` | `Automatic approval review denied (risk: {risk}): {rationale}` | `Request denied for {APP_NAME} to {action}` | 阻止当前动作 |
| `timed_out` | `Automatic approval review timed out while evaluating the requested approval.` | `Review timed out before {APP_NAME} could {action}` | fail-closed |
| `aborted` | 无 | 无独立评审记录 | 走取消/中断收束 |

- [ ] `{APP_NAME}` 必须来自 `metadata.const`，不得硬编码产品名。
- [ ] `denied` 与 `timed_out` 必须是不同展示状态，不能都退化为普通 decline 文案。
- [ ] 命令、写入 stdin、补丁、网络、权限和 MCP 分别生成结构化动作摘要。
- [ ] 长命令、路径、理由按终端宽度换行，不截断决定语义。
- [ ] 自动通过不显示现有 `Auto review approved ...` 记录。
- [ ] 用户、Hook、静态 policy 的既有批准/拒绝文案不受自动评审样式覆盖。
- [ ] JSONL/text 前端输出稳定的结构化终态；TUI 的静默通过只影响可见历史，不丢审计事实。

## 实施阶段

### 阶段 1：协议模型

- [x] 在 `protocol/schema/` 增加 started/completed 强类型事件和严格字段校验。
- [x] 增加合法状态、缺字段、未知字段、身份冲突、时间倒序和非法状态组合测试。
- [x] 验收：wire SDK 可以无 UI 依赖地解析、回放和拒绝错误通知。

阶段 1 证据：`tests/test_stream_event_protocol.py` 与
`tests/test_protocol_item_reducer.py` 共 81 项通过；协议文件语法检查和
`git diff --check` 通过。

### 阶段 2：审批核心接入

- [ ] 在 application approval 边界增加无 IO 的评审收件箱，实现去重、匹配和终态消费。
- [ ] 通过 `ReviewerChain` 注入自动 reviewer；顺序保持 policy、reviewer、user presentation。
- [ ] 将状态映射为 `ALLOW_ONCE`、`DECLINE`、`TIMEOUT`、`CANCEL`。
- [ ] 校验 `turn_id + approval_id + call_id + kind`，并把决定绑定到本地动作指纹。
- [ ] 验收：只有 `approved` 能通过 `_allows_effect`，其余状态无法取得 Effect 执行权。

### 阶段 3：协议编排与恢复

- [ ] stream adapter 先登记评审事件，再处理后续 `tool.approval_required`。
- [ ] duplicate/replay 不重复决定、不重复发 `/tool-approval`、不重复创建历史记录。
- [ ] completed 缺失或与审批动作不匹配时不自动批准，并产生可观测协议错误。
- [ ] Turn 终态、替代 epoch、取消和关闭清理未完成评审。
- [ ] 验收：断线重连、重复事件和并行评审均保持相同本地审批结果。

### 阶段 4：前端展示

- [ ] 给 activity reducer 增加评审 lease 和单项/并行投影。
- [ ] TUI 实现 `Reviewing approval request(s)` 标题和动作明细。
- [ ] 自动通过保持静默；拒绝、超时使用独立的 warning 与动作记录。
- [ ] text/JSONL 输出保留可机器消费的评审终态，不依赖 TUI 文案。
- [ ] 验收：所有前端从同一 application view/activity 语义投影，不重复实现状态判断。

### 阶段 5：完整验收

- [ ] approved：无审批卡、无成功历史，动作仅在本地 allow 事实后执行。
- [ ] denied：展示风险与理由，当前动作不执行，Turn 可以按服务端后续语义继续。
- [ ] timed_out：独立超时文案，当前动作不执行，不回退到用户自动允许。
- [ ] aborted：清理活动状态，取消路径不残留审批或评审 lease。
- [ ] 并行：数量、明细、单项完成与最终恢复正确。
- [ ] 恢复：started/completed 重放幂等，陈旧 epoch 不重新显示。
- [ ] 运行审批、协议、TUI、text、JSONL 定向测试。
- [ ] 运行 `python -m compileall agent protocol frontends`。
- [ ] 运行 `python -m pytest tests/test_package_architecture.py -q`。
- [ ] 运行 `git diff --check` 并完成最终 code review。

## 完成条件

- [ ] 服务端正式协议已包含并测试上述事件，字段与本清单无分歧。
- [ ] 本清单所有实施项和验收项已标记完成，并记录对应测试证据。
- [ ] 不存在旧的自动通过可见文案、双重审批状态机或绕过本地 Effect 门禁的路径。
- [ ] 复核通过后按阶段提交；全部阶段验收完成后再结束本次对齐。
