# 审批架构分阶段优化计划

- 计划版本：V1
- 基线日期：2026-09-02
- 目标平台：Windows、macOS、Linux
- 架构权威：`ARCHITECTURE.md`
- Codex 参考基线：`codex-main/codex-rs` revision `608f4a8a98feff0889cbfc9ed691efbf42d34cc6`
- 当前状态：网络审批作为既有基线；MCP 调用审批门与事实授权闭环已完成，审批卡实施中

## Codex 参考文件

以下文件均以元数据中的固定 revision 为准。实现阶段只吸收可观察行为和边界约束，
不复制 Codex 的私有模块命名或内部状态所有权。

### 审批核心与生命周期

- `codex-main/codex-rs/core/src/tools/approvals.rs`
- `codex-main/codex-rs/core/src/tools/sandboxing.rs`
- `codex-main/codex-rs/core/src/tools/approvals_tests.rs`
- `codex-main/codex-rs/core/src/session/mod.rs`
- `codex-main/codex-rs/core/src/session/turn.rs`
- `codex-main/codex-rs/protocol/src/approvals.rs`

### MCP 工具审批

- `codex-main/codex-rs/core/src/mcp_tool_call.rs`
- `codex-main/codex-rs/core/src/mcp_tool_approval_templates.rs`
- `codex-main/codex-rs/core/src/mcp_tool_call_tests.rs`
- `codex-main/codex-rs/core/src/tools/handlers/mcp.rs`
- `codex-main/codex-rs/core/src/session/mcp.rs`
- `codex-main/codex-rs/protocol/src/mcp_approval_meta.rs`

重点对齐 MCP 工具执行前审批、`auto/prompt/writes/approve` 模式、工具 annotations、
Session/Persistent grant、connector 作用域、参数展示和 MCP request metadata。

### 网络审批与执行边界

- `codex-main/codex-rs/core/src/tools/network_approval.rs`
- `codex-main/codex-rs/network-proxy/`
- `codex-main/codex-rs/sandboxing/`
- `codex-main/codex-rs/linux-sandbox/`
- `codex-main/codex-rs/windows-sandbox-rs/`

网络审批作为既有基线复核 HTTP、HTTPS CONNECT、SOCKS5、DNS、执行身份、规则 amendment、
失败关闭和平台 adapter；MCP 工具 grant 不得冒充网络 grant。

### TUI、Guardian 与交互表现

- `codex-main/codex-rs/tui/src/bottom_pane/approval_overlay.rs`
- `codex-main/codex-rs/tui/src/bottom_pane/mcp_server_elicitation.rs`
- `codex-main/codex-rs/tui/src/chatwidget/tool_requests.rs`
- `codex-main/codex-rs/core/src/guardian/approval_request.rs`
- `codex-main/codex-rs/core/src/guardian/review.rs`
- `codex-main/codex-rs/core/src/guardian/review_session.rs`

重点对齐审批卡的结构化字段、选中态、取消、焦点恢复、颜色和 reviewer 路由，不把前端当作
策略或事实状态 owner。

### Skill 脚本边界

- `codex-main/codex-rs/core/src/skills.rs`
- `codex-main/codex-rs/core-plugins/src/script_attribution.rs`
- `codex-main/codex-rs/core/src/tools/handlers/unified_exec/exec_command.rs`
- `codex-main/codex-rs/core/tests/suite/skill_approval.rs`

这些文件只用于确认 Skill `scripts/*` 作为普通 command 执行的行为。ProxyMind 不新增独立
Skill 审批、Skill grant 或 `skill_approval` 生产路径。

## 目标

本计划把既有网络审批实现和后续 MCP 审批优化放入同一条审批架构演进路径。目标不是复制
Codex 的模块名称，而是让所有模型可达的外部效果都经过同一套可验证的动作、策略、审批、
事实、效果和展示边界。

完成后应能证明：

1. MCP 工具调用在真实执行前经过策略判定和必要的审批，不存在本地路径绕过。
2. Approval、Tool Call 和 Effect 使用互相绑定但职责独立的稳定身份。
3. Session grant、持久策略、审批事实和 Effect 恢复分别由唯一 owner 管理。
4. CLI、TUI、stdio MCP、Subscription 和 Subagent 使用同一 application 语义。
5. 未知、超时、断线、身份冲突和恢复歧义全部 fail closed。
6. 审批卡的语义、颜色、选中态、焦点和窄终端布局都由结构化 PresentationView 驱动。

本计划不把 `Mind` 扩展成审批领域，不把 sandbox 或 sidecar 当成审批架构，也不为 Skill
建立独立审批类型。

## 当前评估

### 已具备的基础

- `agent.domain.approvals` 已有 `ApprovalAction`、MCP 动作、决定校验、事实状态和 grant key。
- `agent.application.approvals` 已有 `ApprovalCore`、reviewer、展示仲裁和旧入口桥接。
- `agent.stores.approvals` 已有 SQLite 首终态事实；`agent.stores.effects` 已有效果日志和对账。
- `protocol/schema` 和 `protocol/client` 已能严格解析并提交 `mcp_tool_call` 审批载荷。
- 网络审批的受管代理、DNS 安全检查、执行身份绑定、规则提交和网络卡已经形成既有基线。

### 必须先修复的缺口

| 优先级 | 缺口 | 当前证据 | 结果 |
| --- | --- | --- | --- |
| P0 | 本地 MCP 工具调用绕过审批 | `ToolEventHandler` 只在 command/patch 路径处理本地执行策略，之后直接进入 `ClientToolCallRunner`；外部 MCP 在 `CompositeToolSession` 中直接转给 `ExternalMcpGroup` | `on-request` 不保证弹卡，`never` 也不能阻止外部 MCP 调用 |
| P0 | MCP 执行身份丢失 | 外部调用没有完整传递 `call_id`、`turn_context`、`pref_config` | 无法可靠绑定 Run/Execution、去重、恢复和审计 |
| P0 | MCP 策略模型缺失 | MCP 配置只有工具暴露 `allow/deny`，没有 `auto/prompt/writes/approve` 和工具注解判定 | 无法复刻 Codex 的 MCP 审批行为 |
| P1 | 类型化核心不是直接生产入口 | MCP 动作通过 `DomainApprovalCoordinator` 包装旧 `Mapping` 队列 | 新旧状态和决定语义可能分叉 |
| P1 | 审批事实存在双重权威 | SQLite facts 与进程内 `ApprovalCallLedger` 并存 | 重启、重放和恢复可能出现不一致 |
| P1 | Session/Persistent grant 未完成 | MCP 默认决定仍是 `accept/decline`；没有独立的 server/connector/tool grant 策略 | 只能做一次性决定，或错误复用命令 amendment 语义 |
| P1 | MCP Effect 未统一接入 | `ApprovalCore.execute_effect` 存在，但本地 MCP 调用没有通过它取得效果执行权 | 审批成功、调用成功和恢复状态没有统一闭环 |
| P1 | MCP 审批卡信息不足 | 当前主要显示 `server: tool` 摘要，参数、风险注解和 connector 信息没有专用投影 | 用户无法充分判断具体外部效果 |
| P2 | 传输边界和验收仍需明确 | 用户配置的外部 MCP/Hook 进程与应用自有 MCP HTTP 传输属于不同信任边界 | 不能把 MCP 工具审批误称为 MCP 传输网络审批 |
| P2 | 回归门槛仍有债务 | 既有全量 Python 回归受旧 `turn_stream.interrupt_turn` 测试引用影响 | 发布前需单独清理测试基线 |

## 对齐原则

### 统一执行链

```text
model tool call
  -> tool descriptor/schema validation
  -> MCP policy evaluator
  -> exact Session grant lookup
  -> Approval Fact: requested
  -> Hook reviewer
  -> user/auto-review reviewer
  -> Approval Fact: resolved/abandoned
  -> Effect Journal: prepared
  -> MCP capability execution
  -> Effect Journal: committed/unknown
  -> typed result and protocol projection
```

审批核心只处理类型化动作和决定；MCP adapter 只负责第三方 SDK、传输和结果转换；Harness
只负责 Run 生命周期、并发和取消；前端只负责 PresentationView、颜色和焦点。

### MCP 策略

沿用 Codex 的可观察语义，同时对未知注解采取更严格的默认值：

| 模式 | 规则 |
| --- | --- |
| `approve` | 明确配置为无需逐次询问 |
| `prompt` | 每次调用询问 |
| `writes` | 只读工具可免询问，其他工具询问 |
| `auto` | 根据 `readOnlyHint`、`destructiveHint`、`openWorldHint` 判定；注解缺失或不可信时询问 |

MCP 的一次性 action fingerprint 必须包含规范化参数；Session/Persistent grant 使用独立的
`server + connector + tool` 作用域，不把一次调用指纹错误地当成长期授权键。选定插件或
无法确认来源的工具不得自动产生持久 grant。

### 明确边界

- `mcp_tool_call` 是工具效果审批，不等同于 MCP HTTP/SSE/STDIO 传输网络审批。
- 当前 V1 保持用户配置外部 MCP/Hook 进程为独立信任边界；若以后要管控其网络，另立网络
  Effect/执行 adapter，不把网络逻辑塞进 MCP 审批卡。
- Skill `scripts/*` 始终作为普通 command 执行，复用 command policy、sandbox 和网络策略；
  不新增 `skill_approval`、Skill grant 或 Skill 专用卡片。
- 不新增第二个全局 coordinator、`mind_approval` 或 sidecar 审批 authority。

## 分阶段实施

### 阶段 0：契约冻结与调用面盘点

**规模：中；优先级：P0；状态：已完成**

交付内容：

- 冻结本地 MCP、应用自有 MCP、外部 STDIO、SSE、Streamable HTTP 的边界和信任模型。
- 定义 `McpToolDescriptor`、`McpApprovalPolicy`、`McpApprovalGrantKey` 和执行身份映射。
- 明确本地调用使用 `tool_call_id`，线上 `tool.approval_required` 必须绑定 `mcp_request_id`；
  不混用协议 ID 和本地授权 ID。
- 列出旧 `Mapping` 入口、ledger 查询和所有 MCP 调用入口的删除条件。

出口条件：文档和类型契约能回答“谁构造动作、谁决定策略、谁保存事实、谁执行效果、谁渲染卡片”。

冻结结果：

- `CompositeToolSession.call_tool` 是模型可达本地工具的统一分发入口；只有其外部工具分支构造
  MCP 审批描述符。`ExternalMcpGroup.call_hook_tool` 是 Hook reviewer 的独立受信任调用入口，
  不递归触发用户工具审批。
- `McpToolDescriptor` 保存 SDK 边界已经校验的 server、原始/暴露工具名、schema 指纹、annotations、
  connector/account、transport 和有效策略；domain 只消费该值对象，不导入 MCP SDK。
- `McpApprovalPolicy` 和 `mcp_requires_approval` 是策略唯一语义；配置 adapter 只负责把 TOML
  转成 `auto/prompt/writes/approve`，前端不得重新判断风险。
- 本地身份映射固定为 `session_id = root_session_id`、`run_id = turn_id`、`action_id = tool_call_id`；
  本地审批 ID 使用动作指纹派生。线上审批继续使用正式 `approval_id + mcp_request_id`，两者不互换。
- `ApprovalFactStore` 保存审批首终态，`SessionGrantStore` 保存 Session grant，Effect Journal 保存
  外部调用效果；`McpApprovalPresentation` 只渲染结构化字段。
- 旧 MCP `Mapping` 入口在所有本地外部调用改用类型化动作、快照恢复不再依赖动态载荷且定向
  回归通过后删除；`ApprovalCallLedger` 只保留线上协议调用的兼容消费投影。

### 阶段 1：MCP 实际调用审批门

**规模：大；优先级：P0；状态：已完成**

交付内容：

- 在客户端工具调用执行前构造类型化 `McpApprovalAction`，所有外部 MCP 调用必须经过同一审批端口。
- 从真实 `mcp.types.Tool` 提取 server、原始 tool name、title、description、annotations、
  connector 和参数 schema，禁止只根据 `mcp__` 名称猜测权限。
- 把 `call_id`、`turn_context`、`pref_config` 和 execution identity 传入外部 MCP adapter。
- 接入 `approve/prompt/writes/auto`，并实现 `never`、Hook deny、身份缺失和未知注解的拒绝路径。
- 同一严格作用域和动作指纹的并发调用共享一个 pending 审批，不同作用域隔离。

出口测试：

- 外部 MCP 写工具在 `on-request` 下先弹卡，拒绝时不会调用 `ExternalMcpGroup.call_tool`。
- 只读、破坏性、open-world 和未知注解分别符合策略。
- `approval_policy=never`、工具不存在、server 冲突、参数非法全部 fail closed。
- Windows、macOS、Linux 使用同一调用行为和结果契约。

实施结果：

- 外部 MCP 工具目录保存真实 server、transport、SDK annotations、参数 schema 及逐服务/逐工具审批模式；
  `CompositeToolSession` 在审批边界校验参数并生成不依赖 SDK 的类型化描述符。
- `ClientToolCallRunner` 在 Effect 和外部 SDK 调用前执行统一 MCP 审批门，常规调用与 JS 嵌套调用
  使用同一工具 metadata；应用内建 MCP 和 Hook reviewer 的受信任入口不进入该用户审批门。
- `prompt/writes/auto/approve`、全局 `never`、缺失 coordinator、未知工具、server 冲突、非法参数和
  同 Run 相同动作并发共享均有定向回归；拒绝路径不会调用外部 MCP SDK。
- 三平台共享相同 Python 调用链，不包含平台分支；真实 macOS/Linux 传输验收保留到阶段 4 的平台矩阵。

### 阶段 2：统一事实、grant、Effect 和恢复

**规模：大；优先级：P0/P1；状态：已完成**

交付内容：

- MCP 本地调用直接进入 `ApprovalCore`；旧桥只保留到迁移出口，随后删除 MCP 的动态 payload 路径。
- 以 `server + connector + tool + Session/Environment` 建立 Session grant；持久 grant 只通过配置
  Store 原子写入，并能在重启后明确恢复。
- 将 `ApprovalCallLedger` 降为兼容投影，事实终态只由 `ApprovalFactStore` 裁决。
- 调用前写入 Effect `prepared`，成功写 `committed`，取消、异常或提交不确定写 `unknown` 并进入对账。
- 完成 pending 恢复、重复 resolve、迟到决定、Run cancel、应用关闭和同一工具不同参数测试。

出口条件：重启不重复执行已提交效果；Session grant 不跨 Session；不同 Run/Execution 不共享一次性决定；
事实、grant、效果和配置策略没有双重 authority。

实施结果：

- 本地 MCP 请求以类型化 `McpApprovalAction` 进入 `ApprovalCore`，`ApprovalFactStore` 是请求与
  首终态的唯一 authority；旧字典只保留为阶段 3 替换前的展示投影，不参与策略或事实裁决。
- Session grant 按 Session、Environment、server、connector 和原始 tool 精确隔离，同一范围内
  可跨不同参数复用；一次性决定仍绑定完整动作指纹。
- “永久允许”先经配置端口原子写入原始 MCP 服务键下的工具级 `approval_mode = approve`，写入
  失败时审批事实保持 requested，重启后由正常配置加载恢复策略。
- 本地 `acceptForSession` 和 `acceptAndRemember` 不进入正式 wire schema；线上 MCP 审批继续只
  接受服务端契约声明的决定集合。
- 正式 `ToolInvocation.effect` 存在时，MCP 审批先于 Effect inspect/begin 和 SDK 调用，随后复用
  既有 committed/unknown 与对账语义；服务端未提供正式 Effect identity 时不合成本地替代身份。
- pending 事实重放、持久写入失败、不同 grant 范围、正式 Effect 提交与协议决定隔离均有定向回归。

### 阶段 3：MCP 审批卡与协议表现

**规模：中；优先级：P1；状态：待开始**

交付内容：

- 增加专用 `McpApprovalPresentation`，展示 server、tool、标题、描述、参数摘要、风险注解和
  connector/account。
- 参数实行上限、结构化折叠和敏感字段脱敏；展示层不从原始异常或第三方对象重建语义。
- 保持网络卡的颜色对齐，同时为 MCP 卡提供独立 action 语义、选中态、取消态和降级色彩快照。
- 协议 adapter 严格保留 `mcp_request_id`、ack、snapshot 和 terminal 状态；不引入未获服务端契约
  支持的决定字段。

#### MCP 审批卡样式实施规范

本节是阶段 3 的 UI 实施契约。卡片只消费结构化 `McpApprovalPresentation`，不从第三方 MCP
对象、原始异常或未经校验的字典重新推断语义。TUI、CLI、Subscription 和 Subagent 的展示
可以有不同排版，但字段语义、顺序、颜色角色和脱敏结果必须一致。

##### 当前基线

当前 MCP 请求复用通用工具审批卡，典型内容为：

```text
Would you like to approve the following tool action?

$ github: create_issue

› 1. Yes, proceed (y)
  2. No, and tell ProxyMind what to do differently (n/esc)

Press enter to confirm or ctrl + c to cancel
```

当前基线不展示参数、工具描述、风险注解、connector 或 account，也没有 MCP 专用颜色。该
基线只用于兼容回放和迁移期对照，不能作为阶段 3 的完成标准。

##### 目标字段与顺序

MCP 卡片按以下顺序生成；缺失的可选字段整行省略，缺失的安全关键信息不能触发自动允许：

1. **Question**：`Would you like to approve the following MCP tool call?`
2. **Source**：子执行线程存在时显示 `Agent <type> · <id>`。
3. **Target**：`Server`、`Tool`，有值时追加 `Title`。
4. **Description**：工具描述，按终端宽度折行，禁止使用原始 Markdown 控制布局。
5. **Arguments**：规范化参数摘要；默认折叠结构化值，显示字段数和可展开提示。
6. **Risk**：根据 annotations 和策略评估显示 `read-only`、`external write`、`destructive`、
   `open-world` 或 `unknown`。`unknown` 必须按需审批。
7. **Connector / Account**：存在且已通过边界校验时显示；不可用时显示 `unverified`，不猜测来源。
8. **Reason / Environment**：服务端或策略提供时显示，不能覆盖 Target 和 Arguments。
9. **Decisions**：只渲染协议实际提供并通过 `approval_decisions` 校验的选项。
10. **Footer**：保留 pending 数量、确认和取消提示。

标准宽度下的目标示例：

```text
Would you like to approve the following MCP tool call?
Agent worker · agent-7

Server: github
Tool: create_issue
Title: Create issue
Description: Create an issue in the selected repository.
Arguments (2):
  title: Bug report
  body: Request details
Risk: external write
Connector: github
Account: configured account

› 1. Yes, proceed (y)
  2. Yes, for this session (s)
  3. No, and tell ProxyMind what to do differently (n/esc)

Press enter to confirm or ctrl + c to cancel
```

示例中的 `acceptForSession` 只在协议和策略实际提供时出现；MCP 卡不得自行增加服务端未声明
的决定值。一次性允许、Session grant 和持久 grant 的作用域文案必须与决定值一一对应，不能
复用命令前缀 amendment 文案。

##### 参数摘要与安全规则

- 参数先按协议 schema 校验，再转换为展示值；展示层不执行 schema 推断或字符串拼接。
- 默认最多显示 12 个顶层字段、每个字段最多 120 个显示列、嵌套深度最多 2 层，总展示预算
  2,048 个 UTF-8 字节。超出部分显示 `… +N more`，并保留原始字段计数。
- `token`、`secret`、`password`、`authorization`、`api_key`、`access_token`、`private_key`
  和 `credential` 等键名大小写不敏感匹配，值统一显示 `[redacted]`；嵌套对象和数组同样递归脱敏。
- 字符串、数字、布尔值和 null 使用稳定的 JSON 表示；换行、控制字符和 ANSI 序列必须转义。
- 结构化值默认折叠；展开只改变 PresentationView，不改变审批事实、动作指纹或实际参数。
- 参数无法解析、schema 不匹配或身份不完整时显示降级卡并保持审批阻断，不回退为“看起来像
  只读”的摘要。

##### 颜色 Token 与状态

颜色是语义角色，不直接散落在各前端。truecolor 的基准值如下；ANSI 256 和 ANSI 16 必须映射
到同一语义，未知终端能力使用无颜色但保持粗体、前缀和顺序：

| Token | Truecolor | 用途 |
| --- | --- | --- |
| `approval-mcp-label` | `#2563EB` | MCP、Server、Tool、Risk 标签；与网络审批主色对齐 |
| `approval-mcp-value` | `#AAB7C4` | 已校验的字段值 |
| `approval-mcp-connector` | `#0EA5E9` | connector、account 和来源值；与网络目标值对齐 |
| `approval-mcp-readonly` | `#16A34A` | 明确只读风险 |
| `approval-mcp-write` | `#D97706` | 外部写入或 open-world 风险 |
| `approval-mcp-destructive` | `#DC2626` | destructive 注解或策略判定 |
| `approval-mcp-unknown` | `#7D8A98` | 注解缺失、来源未验证或降级状态 |
| `approval-option-selected` | `#5B8DEF` | 当前焦点选项，保持现有审批选中态 |
| `approval-shortcut-selected` | `#C7F7FF` | 当前焦点快捷键，保持现有审批快捷键态 |

卡片默认不设置背景色，不使用渐变、图标代替风险文字或嵌套卡片。网络审批继续使用
`approval-network` / `approval-network-host`；MCP 只能复用颜色角色，不能把 MCP tool grant
显示成网络授权。

状态显示规则：

| 状态 | 展示要求 | 是否可操作 |
| --- | --- | --- |
| `requested` | 完整字段、风险颜色、焦点选项 | 是 |
| `selected` | 仅焦点和快捷键颜色变化，内容不抖动 | 是 |
| `submitting` | 保留请求摘要，禁用重复提交 | 否 |
| `accepted` | 显示允许作用域和 terminal 结果 | 否 |
| `declined` | 显示拒绝原因或下一步提示 | 否 |
| `cancelled` / `expired` | 显示终止原因，不重新打开卡片 | 否 |
| `degraded` | 缺失字段以 `unknown` 标识，继续要求明确决定 | 是 |

##### 排版与跨平台要求

- 卡片内容宽度由前端可用区域决定；任何字段都必须按显示列折行，不能横向溢出或覆盖选项。
- 窄终端优先保留 Question、Server、Tool、Risk 和 Decisions；Description、Arguments 细节可
  折叠，但不能静默删除风险或身份字段。
- Windows、macOS、Linux 使用相同的 PresentationView、字段顺序、脱敏和颜色语义；平台差异
  只能出现在终端能力适配器，不得产生不同的审批决定或不同的默认风险。
- CLI 和非交互入口不能伪造用户确认；无审批表面时按策略拒绝或返回明确的 pending 状态。
- 多个 pending 请求必须保持卡片高度稳定，焦点恢复到原请求，迟到 ack 或旧 snapshot 不得
  重开已收束的卡片。

##### Codex 对齐依据

- `codex-main/codex-rs/core/src/mcp_tool_call.rs`：工具执行前审批、决定和 grant 作用域。
- `codex-main/codex-rs/core/src/mcp_tool_approval_templates.rs`：MCP 审批提示和结构化元数据。
- `codex-main/codex-rs/protocol/src/mcp_approval_meta.rs`：MCP request metadata 与 annotations。
- `codex-main/codex-rs/tui/src/approval_events.rs`：审批事件在 TUI 中的结构化投影。
- `codex-main/codex-rs/tui/src/styles.md`：Codex TUI 的颜色和终端能力约束。
- `codex-main/codex-rs/tui/src/auto_review_denials.rs`：MCP 来源和 connector 的失败展示语义。

##### 阶段 3 样式验收

- 为每个字段和每个状态增加结构化快照测试；快照只比较语义片段和样式 Token，不比较终端控制
  序列的偶然差异。
- 覆盖 truecolor、ANSI 256、ANSI 16、深色、浅色和无颜色终端；颜色降级后仍能仅凭文本和前缀
  区分 MCP、网络、写入、破坏性和未知风险。
- 覆盖参数脱敏、长字符串、嵌套对象、Unicode、控制字符、窄宽度、多 pending、焦点恢复、取消、
  超时和重复 ack。
- 样式完成不能替代执行面验收；只有阶段 1 的真实 MCP 审批门和阶段 2 的事实/Effect 闭环
  通过后，才能将卡片从兼容基线切换为默认表现。

出口测试：

- truecolor、ANSI 256、ANSI 16、深色、浅色和未知能力终端均有稳定快照。
- 长参数、Unicode、窄宽度、多 pending、取消和焦点恢复无重叠或越界。
- 协议回放、重复 ack、旧 snapshot 和迟到决定均保持首终态。

### 阶段 4：传输边界、平台加固与发布收口

**规模：中到大；优先级：P1/P2；状态：待开始**

交付内容：

- 明确外部 MCP 传输是否继续属于受信任配置边界；若扩大网络管控范围，建立独立网络执行
  adapter 和 Effect，不复用 MCP tool grant 冒充网络授权。
- 覆盖 STDIO 进程启动、退出、超时、断线、重复关闭和凭据清理。
- 完成三平台真实 MCP 工具调用矩阵和现有网络审批回归。
- 修复已知全量 Python 测试基线问题，完成 compileall、git diff 检查和受影响测试回归。

出口条件：产品文档只声明已有证据支持的 MCP 工具审批和网络审批行为；未验收平台明确标记，
不以“审批卡能显示”代替执行面验收。

## 验收矩阵

| 维度 | 必测值 |
| --- | --- |
| 来源 | 外部 STDIO、SSE、Streamable HTTP、应用自有 MCP、Subagent、JS 嵌套调用 |
| 策略 | approve、prompt、writes、auto、never、显式 deny、未知注解 |
| 注解 | read-only、destructive、open-world、缺失、冲突 |
| grant | 一次性、Session、持久、不同参数、不同 connector、不同 server |
| 身份 | Session、Run、Action、Execution、tool_call_id、mcp_request_id、缺失和冲突 |
| 生命周期 | 前台、后台、超时、断线、取消、重启、应用关闭 |
| 并发 | 同指纹归并、不同作用域隔离、重复请求、迟到决定 |
| 效果 | prepared、committed、unknown、对账、不可重放效果 |
| 展示 | 初始、切换、允许、拒绝、取消、长参数、窄终端、降级颜色 |
| 平台 | Windows、macOS、Linux |

## 优先级结论

先做阶段 1，再做阶段 2；在本地 MCP 调用真正经过审批核心之前，不应继续投入 MCP 卡片细节或
持久策略。阶段 3 解决可判断性和视觉对齐，阶段 4 负责边界和发布验收。既有网络审批不回退、
不重做，只作为阶段 4 的回归基线。

本计划完成前，不能声称“整个审批架构完全对齐”；可以声称“网络审批基线已建立，MCP 审批
正在按统一核心补齐”。

## 维护规则

- 每次只推进一个阶段，完成出口条件后再进入下一阶段。
- 代码、协议、稳定配置字段和受影响测试必须在同一阶段同步更新。
- 不运行架构守卫作为日常迭代门槛；只有实际改变包边界时才按 `AGENTS.md` 规则单独执行。
- 发现职责归属、生命周期或正式协议冲突时，先更新本计划和 `ARCHITECTURE.md`，再修改实现。
