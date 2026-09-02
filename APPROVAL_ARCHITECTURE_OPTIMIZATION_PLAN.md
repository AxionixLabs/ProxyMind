# 审批架构分阶段优化计划

- 计划版本：V1
- 基线日期：2026-09-02
- 目标平台：Windows、macOS、Linux
- 架构权威：`ARCHITECTURE.md`
- Codex 参考基线：`codex-main/codex-rs` revision `608f4a8a98feff0889cbfc9ed691efbf42d34cc6`
- 当前状态：网络审批作为既有基线；MCP 审批优化尚未开始

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

**规模：中；优先级：P0；状态：待开始**

交付内容：

- 冻结本地 MCP、应用自有 MCP、外部 STDIO、SSE、Streamable HTTP 的边界和信任模型。
- 定义 `McpToolDescriptor`、`McpApprovalPolicy`、`McpApprovalGrantKey` 和执行身份映射。
- 明确本地调用使用 `tool_call_id`，线上 `tool.approval_required` 必须绑定 `mcp_request_id`；
  不混用协议 ID 和本地授权 ID。
- 列出旧 `Mapping` 入口、ledger 查询和所有 MCP 调用入口的删除条件。

出口条件：文档和类型契约能回答“谁构造动作、谁决定策略、谁保存事实、谁执行效果、谁渲染卡片”。

### 阶段 1：MCP 实际调用审批门

**规模：大；优先级：P0；状态：待开始**

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

### 阶段 2：统一事实、grant、Effect 和恢复

**规模：大；优先级：P0/P1；状态：待开始**

交付内容：

- MCP 本地调用直接进入 `ApprovalCore`；旧桥只保留到迁移出口，随后删除 MCP 的动态 payload 路径。
- 以 `server + connector + tool + Session/Environment` 建立 Session grant；持久 grant 只通过配置
  Store 原子写入，并能在重启后明确恢复。
- 将 `ApprovalCallLedger` 降为兼容投影，事实终态只由 `ApprovalFactStore` 裁决。
- 调用前写入 Effect `prepared`，成功写 `committed`，取消、异常或提交不确定写 `unknown` 并进入对账。
- 完成 pending 恢复、重复 resolve、迟到决定、Run cancel、应用关闭和同一工具不同参数测试。

出口条件：重启不重复执行已提交效果；Session grant 不跨 Session；不同 Run/Execution 不共享一次性决定；
事实、grant、效果和配置策略没有双重 authority。

### 阶段 3：MCP 审批卡与协议表现

**规模：中；优先级：P1；状态：待开始**

交付内容：

- 增加专用 `McpApprovalPresentation`，展示 server、tool、标题、描述、参数摘要、风险注解和
  connector/account。
- 参数实行上限、结构化折叠和敏感字段脱敏；展示层不从原始异常或第三方对象重建语义。
- 保持网络卡的颜色对齐，同时为 MCP 卡提供独立 action 语义、选中态、取消态和降级色彩快照。
- 协议 adapter 严格保留 `mcp_request_id`、ack、snapshot 和 terminal 状态；不引入未获服务端契约
  支持的决定字段。

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
