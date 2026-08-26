# Codex 审批对齐分阶段方案

本文是客户端改造路线图，不要求一次性完成，也不表示本文列出的内容已经全部实现。
对照基线为根目录 `codex-main`，服务端契约以 `AppServer/services/llm/EVENT.md`
和 `PROTOCOL.md` 为准。本方案只规划客户端改动；需要服务端扩展的能力单独列出，
不得在客户端伪造不存在的字段。

## 一、对齐原则

### 1. 三类状态分别归属

```text
服务端：审批请求的转发、pending/decision 状态和结果确认
客户端：本地 .mind 规则、危险命令判断、沙箱边界、UI 审批队列
工具结果：实际执行、结果提交、断线恢复和幂等重试
```

`tool.approval_required` 只是执行核心发出的审批请求通知，不是服务端策略结论。
客户端决定后，服务端只负责转发决定、保存状态和确认结果。已确认的审批调用不能覆盖
客户端本地 `forbidden`、危险命令和沙箱拒绝，但可以抑制同一调用的第二次交互。

### 2. 决策优先级

本地策略保持 Codex 的三态语义：

```text
Forbidden > Prompt > Allow
```

其中 `Forbidden` 不受用户批准、已确认审批调用、session grant 或 execpolicy amendment
覆盖。`Prompt` 在已有有效审批确认时可以跳过第二次 UI 询问，但仍然要经过本地判断。

### 3. 当前服务端契约边界

当前服务端已经提供：

- `tool.approval_required` 审批请求转发事件；
- 必填 `reason` 和 `available_decisions`；
- `accept`、`acceptForSession`、`acceptWithExecpolicyAmendment`、`decline`、`cancel`；
- 同一 `call_id` 的批准后重放；
- `/tool-approval` 和 `/tool-result` 的 `request_id` 幂等 ACK；
- `event_seq` 的持久回放和重复投递。

客户端不新增 `execution`、`grant`、审批过期时间或服务端未下发的授权字段。
`auto_review` 已由服务端实现，客户端只消费服务端给出的决定和来源。

## 二、现状基线

当前已具备或部分具备：

- 多命令片段的本地 fallback 判断；
- 服务端 `available_decisions` 的解析和 UI 传递；
- 同一进程内按 `cid/sid/turn_id/call_id` 关联审批确认和重放的基础账本；
- 本地审批 `cancel` 后提交取消结果并中断 Turn；
- `event_seq` 去重、缺口检测和 `after_seq` 恢复。

仍存在的主要问题：

1. 已确认审批调用会跳过整段本地执行策略，可能绕过本地 `forbidden`。
2. 审批账本在服务端 ACK 前写入 approved。
3. 工具调用在执行前标记 consumed，结果提交失败后无法安全恢复。
4. 嵌套进程审批的 `cancel` 仍未统一为 Turn abort。
5. `acceptForSession` 没有客户端本地会话授权镜像，容易产生重复询问。
6. 危险命令审批理由仍然过于笼统。
7. 审批 ACK 已校验但没有参与客户端状态收敛。
8. 本地缓存键和生命周期还没有达到 Codex 的环境隔离和恢复语义。

## 三、阶段 0：冻结契约和状态模型

目标：先把职责和状态写清楚，避免继续增加兼容层。

### 改造内容

- 以 `ToolApprovalRequiredEvent`、`ToolApprovalAck`、`ToolCallEvent` 为现有协议边界。
- 明确审批账本只负责“审批请求确认与后续 `tool.call` 的关联”，不负责工具结果账本。
- 将审批状态与工具执行状态拆开，至少分别描述：

```text
审批：pending -> accepted_once / accepted_session / declined / cancelled
执行：not_started -> running -> result_pending -> result_committed
```

- `consumed` 不再同时表示“已授权”和“结果已提交”。
- 记录每个状态的所有者、清理条件和可恢复动作。

### 验收标准

- 文档和类型定义中不再把审批请求、执行结果、session rule 混为一个状态。
- 不增加服务端不存在的字段。
- 所有后续阶段都能引用同一套状态转换表。

## 四、阶段 1：修复本地安全边界和审批收敛

这是第一优先级，完成后才允许扩大 Codex 能力范围。

### 1. 审批确认不能绕过本地策略

`stream_turn` 收到审批请求确认后的 `tool.call` 时仍必须执行本地策略判断：

```text
本地 Forbidden -> 直接拒绝并回传 blocked_before_execution
本地 Prompt + 已确认审批调用 -> 不再弹第二张卡，继续执行
本地 Allow -> 直接执行
```

审批确认只消费一次性审批关联，不改变本地 `.mind` 规则，也不改变沙箱模式。
应覆盖 `shell_command`、`exec_command`、`write_stdin` 以及嵌套进程调用。

### 2. 审批 ACK 后再提交本地批准状态

审批请求转发流程调整为：

```text
展示决定
  -> POST /tool-approval
  -> 校验 ToolApprovalAck（仅确认服务端已记录决定）
  -> ACK 成功后写入 accepted_once/session
  -> 等待并消费对应 tool.call
```

请求失败、ACK 不匹配、`approval_not_pending` 或决定冲突时，不得留下 approved 状态。
相同 `request_id` 重试必须复用原始 payload。

### 3. 工具结果可恢复且不重复执行

工具执行和结果投递必须分开记录：

- 执行开始前记录调用身份和参数指纹；
- 执行完成后保留完整标准结果信封；
- `/tool-result` 失败时使用相同 `request_id` 重试；
- 重放相同 `tool.call` 时，若本地已有结果，重新提交结果而不是再次执行；
- 只有服务端确认结果已接收后，才进入 `result_committed`。

该阶段只使用现有 `/tool-result` 幂等契约，不新增客户端到服务端的执行状态字段。

### 4. 所有 cancel 统一为 Turn abort

- 审批请求 `cancel` 继续只调用 `/tool-approval`，遵守服务端的原子取消语义；
- 本地审批和嵌套进程审批的 `cancel` 统一调用同一个 Turn interrupt 边界；
- `decline` 仍然只拒绝当前工具并允许 Turn 继续；
- `turn_not_active` 视为中断已完成；
- 中断后不得继续执行同一 Turn 的待处理 `tool.call`。

### 阶段验收

- 已确认审批的 `rm -rf` 仍会被本地 `forbidden` 拒绝；
- 已确认审批的普通 `Prompt` 命令不会二次弹卡；
- 工具执行成功但结果请求首次失败时，恢复只重发结果、不重跑命令；
- 嵌套进程 `cancel` 最终得到 `interrupted`，不再伪装成普通拒绝。

## 五、阶段 2：对齐现有契约内的审批体验

### 1. 实现远端 session approval 的客户端镜像

收到 `acceptForSession` 后，客户端按审批请求事件中的完整调用坐标和 canonical
arguments 建立当前 `sid` 内的本地会话批准记录。该记录只用于跳过重复 UI，不能覆盖
本地 `forbidden`，并在 session 结束、策略变更或坐标失效时清理。

收到 `acceptWithExecpolicyAmendment` 时：

- 不在客户端改写服务端规则内容；
- 使用 amendment ID 作为本次远端决定的关联；
- 后续调用仍执行本地策略，必要时只跳过重复询问；
- 服务端安装的规则是否生效仍以服务端后续行为为准。

### 2. 统一 `available_decisions` 消费链路

规范化后的集合必须被以下所有层共享：

```text
事件解析 -> ApprovalRequest.decisions -> coordinator -> TUI/非交互 reviewer -> POST 校验
```

不得在 UI、快捷键或 coordinator 中补回固定选项。`cancel` 是隐藏控制结果，不能被
当成可见菜单项，也不能因本地默认值强行加入服务端集合。

### 3. 消费 ACK 状态

审批提交函数返回的 `tool_status`、`turn_status` 要参与客户端状态机：

- `approved` 才推进 accepted 状态；
- `declined/cancelled` 立即收束对应 UI 和账本；
- `interrupting` 进入等待中断状态，不再接受新的本地执行；
- `duplicate`、`turn_not_active` 等稳定结果记录为幂等收束，而不是普通异常。

### 4. 提升审批理由和缓存键

- `Evaluation` 保存危险启发式命中信息，审批卡区分强制删除、包装器传播、规则命中、
  approval policy 和 sandbox 原因；
- 本地会话缓存至少绑定 `cid/sid`、工具、规范化参数、解析后的 cwd 和当前策略版本；
- 策略或权限上下文改变后，旧缓存不可继续复用；
- 进程重启后的恢复只在已有权威事件和结果记录可证明时继续，不凭内存中的最近审批猜测。

### 阶段验收

- `acceptForSession` 后相同 canonical call 不再重复弹卡，但本地 forbidden 仍有效；
- UI 和提交层对服务端禁止的 decision 保持一致；
- 审批卡能显示具体危险原因；
- 重连和进程恢复不会把旧策略批准误用于新环境。

## 六、阶段 3：补齐 Codex 审批能力（需服务端先扩展契约）

以下能力不能仅靠当前客户端实现。服务端需要先提供事件、决定和持久化语义，之后再
按同一审批状态机接入客户端。

### 1. Granular approval 和 sandbox escalation

对齐 Codex `GranularApprovalConfig`，区分：

- sandbox 权限提升；
- execpolicy 规则审批；
- skill/plugin 脚本审批；
- request permissions；
- MCP elicitation。

本地当前只有三种 `SandboxMode`，不能自行把它扩展成服务端不存在的请求格式。

### 2. 扩展审批动作类型

对齐 Codex `ApprovalAction` / `ReviewDecision`：

- `Execve`；
- `ApplyPatch` 文件变更审批；
- `NetworkAccess` 和 network policy amendment；
- `RequestPermissions` 及 turn/session 范围；
- `McpToolCall` 和 MCP elicitation。

需要服务端同时定义事件字段、可用决定、ACK、重放和权限撤销语义。

### 3. 丰富审批上下文和缓存身份

服务端契约准备好后，再引入 Codex 中的 environment、network context、additional
permissions、plugin/script attribution、TTY、sandbox profile、policy fingerprint 和
patch file scope。缓存键必须覆盖真正影响安全结论的上下文，不能只用原始 argv。

### 阶段验收

- 每类 `ApprovalAction` 都有独立事件、决定、结果和恢复测试；
- 服务端重放、客户端重连、进程接管不会重复执行不可重放效果；
- `Forbidden`、`Abort`、session grant、network amendment 的优先级与 Codex 一致。

## 七、阶段 4：对照验证和发布门槛

每个阶段单独合并和验证，不等所有 Codex 能力完成后再一次性上线。

### 必测场景

1. 多命令中前段 `allow` 不得跳过后段危险命令。
2. 审批确认后仍执行本地 `forbidden`，普通 `Prompt` 不二次询问。
3. `acceptForSession`、amendment、decline、cancel 的生命周期互不串线。
4. 重复 `event_seq`、SSE 断线、ACK 丢失、工具结果丢失都能幂等恢复。
5. 审批请求、本地策略、嵌套进程审批的 cancel 都最终收敛到 `interrupted`。
6. `apply_patch` 仍保持现有路径边界和审计；在服务端没有 patch approval 契约前不伪造
   Codex patch 审批事件。

### 每阶段发布门槛

- 先运行受影响模块的定向 pytest；
- 修改协议、状态机或恢复逻辑时再运行完整测试；
- 执行 `python -m py_compile` 和 `git diff --check`；
- 对 Windows、macOS 的沙箱执行路径分别验证，审批策略不得依赖某一平台的默认行为。

## 八、明确不做的事情

- 不把服务端审批规则重新搬回一套客户端“隐藏 grant 字段”；
- 不新增当前服务端不会下发的过期时间、execution、grant 或网络上下文兼容字段；
- 不让审批确认覆盖本地 `.mind` 的 `forbidden` 或沙箱拒绝；
- 不把 `decline` 和 `cancel` 合并为同一种失败结果；
- 不在没有服务端契约和恢复语义的情况下假装支持 Codex 的 MCP、network、request_permissions
  或 granular approval。
