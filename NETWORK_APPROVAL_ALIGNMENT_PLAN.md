# Codex 网络审批对齐计划

- 状态：待开始
- 基线日期：2026-09-02
- 参考实现：`codex-main/codex-rs/network-proxy`、
  `codex-main/codex-rs/core/src/tools/network_approval.rs` 及其进程执行链路

## 结论

建议分 **6 个迭代** 完成。

前 4 个迭代先在 Windows 受管沙箱上形成完整生产闭环，第 5 个迭代完成 macOS/Linux
平台实现，第 6 个迭代完成恢复、安全一致性和发布验收。不得把“审批卡可以显示”或
“代理可以启动”单独视为网络审批完成。

最终目标是：在声明支持的受管执行路径上达到 Codex 的可观察行为和安全边界等价，
包括静态规则、逐次审批、会话批准、持久规则、并发归并、后台进程、取消、代理强制
路由和本地网络保护。这里的“等价”不表示代理能够单独解决 DNS rebinding 等需要
防火墙、VPC 或企业出口策略解决的问题；Codex 自身也不提供这种绝对保证。

| 迭代 | 主要交付 | 相对规模 | 状态 | 完成后的声明范围 |
| --- | --- | --- | --- | --- |
| 1 | Windows 静态代理与强制出口 | 大 | 待开始 | 仅静态规则可用 |
| 2 | 前台 allow-once 审批闭环 | 大 | 待开始 | 前台逐次审批可用 |
| 3 | 会话决定、持久规则和 UX | 大 | 待开始 | 决定语义基本对齐 |
| 4 | 后台、持续进程、Subagent、JS REPL | 特大 | 待开始 | Windows 可声明完成 |
| 5 | macOS/Linux sidecar 与平台约束 | 特大 | 待开始 | 通过矩阵的平台可声明完成 |
| 6 | 恢复、安全一致性和发布验收 | 大 | 待开始 | 可声明选定 Codex 基线行为等价 |

相对规模只用于控制单次改动范围，不是工期承诺。第 1 至第 3 迭代不能绕过第 4 迭代的
安全出口直接发布“完整网络审批”；第 5 迭代可按平台并行实施，但必须分别提供真实平台
证据。

## 当前基线

已经具备：

- `protocol/schema/tool_approval.py` 已声明 `network_access`、网络协议、审批决定和 ACK。
- `protocol/schema/stream_events.py`、`protocol/client/tools.py` 已能校验和提交线上
  `mind.chat` 网络审批。
- `agent/application/approvals` 已能把网络事件投影为通用审批卡。
- `agent/domain/execution_policy` 已能解析和匹配 `network_rule(...)`。
- `codex-main` 已包含完整 `codex-network-proxy` Rust crate，当前自定义
  `mind_sandbox_server` 与其处于同一 Cargo workspace。

尚未具备：

- `SandboxClient` JSONL V1 当前只支持 `spawn`、`write`、`terminate`、`close`，只接收
  stdout、stderr 和 exit 事件。
- Rust sidecar 启动进程时仍使用 `proxy_enforced: false`，没有代理限制 SID、代理环境、
  CA 或网络审批回调。
- 本地执行没有稳定传递 `turn_id`、`call_id`、`environment_id` 和 `execution_id`，
  不能把被阻断连接可靠归属到发起它的工具调用。
- `ExecPolicyManager` 只有命令和补丁的会话缓存，并只持久化命令前缀规则；网络规则
  只能读取，不能在运行时原子更新并保存。
- 当前通用审批协调器按 `request_id` 去重，不具备按网络目标归并并发请求、会话级
  host allow/deny 和请求断线收束。
- JS REPL 内核由普通 Node 子进程启动；在受限模式下仍是潜在的直接网络绕行面。
- 发布资产目前只有 Windows sidecar；macOS 只有目录和构建约定，Linux 尚未进入产品
  sidecar 支持清单。

## 对齐范围

### 纳入受管网络

- 模型发起的 `shell_command`。
- 模型发起的 `exec_command` 及其后续 `write_stdin`。
- 后台化后仍存活的上述进程。
- Root Turn 和 Subagent Turn 发起的上述进程。
- 受限模式下的 JS REPL 内核；若不能安全路由，必须明确禁用其原生网络，而不能只依赖
  `HTTP_PROXY` 环境变量。

### 不纳入受管网络

- 应用自身的模型、授权、更新、配置服务和 MCP HTTP 传输。
- 用户显式启动的交互 Shell。
- 用户配置并信任的外部 MCP/Hook 进程，除非以后为其建立独立权限模型。
- `danger-full-access` 进程；它按既有语义直接使用主机网络。

这些排除项必须有回归测试证明不会错误继承受管代理环境。它们不是允许模型绕过受管
执行面的后门。

## 最终行为契约

| 条件 | 行为 |
| --- | --- |
| `network_access=enabled` | 文件系统沙箱保持有效，网络不触发逐目标审批 |
| `network_access=restricted` 且规则 allow | 仅通过受管代理放行匹配目标 |
| `network_access=restricted` 且规则 deny | 直接拒绝，deny 始终优先 |
| restricted、未匹配、`approval_policy=never` | 拒绝且不展示审批 |
| restricted、未匹配、允许询问 | 展示一次网络审批并阻塞该连接 |
| `accept` | 只放行当前被阻断请求 |
| `acceptForSession` | 当前应用会话内放行相同环境、host、协议和端口 |
| 持久 allow/deny | 先更新运行中代理，再保存 `network_rule`；重启后仍有效 |
| decline | 拒绝当前请求，工具收到稳定的策略拒绝结果 |
| cancel/Turn 取消/进程退出 | 断开请求并收束等待者，不把取消降格为普通拒绝 |

目标规范化必须复用 Codex 语义：host 大小写归一、协议显式区分
HTTP/HTTPS/SOCKS5 TCP/SOCKS5 UDP，会话键包含 `environment_id + host + protocol + port`。
持久规则继续使用现有 execpolicy 的 `host + protocol + decision` 契约，不暗中扩大为端口
规则。

## 架构归属

| 位置 | 职责 |
| --- | --- |
| `agent/domain/execution_policy` | host、协议、规则、决定及纯匹配语义 |
| `agent/application/approvals` | 构造网络审批请求、校验决定和 amendment、生成中立展示模型 |
| `agent/ports` | 执行身份、代理控制、网络审批和策略提交的最小具名协议 |
| `agent/harness` | 活动执行注册、Turn/后台生命周期、会话 allow/deny、并发归并和取消 |
| `agent/stores/approvals` | 需要恢复的审批事实；不持有代理或配置路径 |
| `infrastructure/config` | 网络规则文件读取、追加、并发写入和当前策略替换 |
| `infrastructure/platform/sandbox.py` | sidecar V1 JSONL 适配、请求关联和进程事件，不判断用户策略 |
| Rust `mind_sandbox_server` | 代理监听、CA、环境注入、OS 强制路由、阻断连接等待和运行时规则更新 |
| `frontends` | 只展示 application 提供的决定，不持有会话网络状态 |
| `mind.py` / `composition.py` | 唯一具体组合根和资源关闭顺序 |

线上 `mind.chat` schema 与本地 sidecar schema 必须分开。本地需要 Codex 的复数 allow/deny
提案时，应先在 application 使用具名值表达；不得为了复用本地实现直接改变已经冻结的
线上单数 `proposed_network_policy_amendment`。如服务端也要升级，另立协议版本和端到端
迁移，不保留永久单双数兼容分支。

## Sidecar V1 扩展原则

- `PROTOCOL_VERSION` 保持为 `1`，本次不引入 V2 或双版本分支。
- Python 客户端与 Rust 二进制同批扩展 V1；`ready` 在既有版本号外声明结构化
  capabilities，新客户端在受限网络模式下未发现 managed-network capability 时必须
  fail closed，不能把旧 V1 二进制误判为支持网络审批。
- `spawn` 显式携带执行身份和有效网络模式，不从环境变量反射 Turn 状态。
- sidecar 可主动发送带独立请求 ID 的 `network.approval_required` 事件；Python 必须回送
  与原请求 ID、执行 ID 和目标一致的结构化决定。
- 运行时策略更新使用独立、幂等的方法，不能通过重新启动全部用户进程刷新。
- sidecar 报告 request disconnect、代理失败和策略更新失败；未知事件或字段必须失败，
  不静默忽略安全相关消息。
- sidecar 关闭时先拒绝/断开全部待审批连接，再终止子进程和代理监听器。
- Rust 侧保持为薄适配器，直接复用 `codex-network-proxy`；不复制其 HTTP、SOCKS5、MITM、
  私网保护或 Windows attribution 实现。
- 开始前记录 `codex-main` 来源 revision 或源码归档校验值；更新上游时必须重新运行完整
  网络一致性测试，并保留 Apache-2.0 attribution。

## 迭代计划

### 迭代 1：Windows 静态受管出口

**交付用例**：Windows 受限命令只能通过受管代理访问 execpolicy 已允许的目标；未匹配或
明确 deny 的目标不能联网，直接 socket 也不能绕过。

实施范围：

- 冻结 sidecar V1 新增的 ready capability、spawn 网络字段、网络配置和策略更新 schema。
- 在 `mind_sandbox_server` 内启动并持有 `codex-network-proxy`。
- 从现有 `Policy.network_rules` 生成代理 allow/deny 初始状态。
- 为受限进程注入 HTTP/HTTPS/SOCKS5、CA 和代理标记环境。
- Windows 启用 `proxy_enforced`、代理 restricting SID 和正确的 proxy settings mode。
- 将 `network_access` 从 Turn 上下文显式传到进程端口和 sidecar。
- 更新 `schematic/sandbox/README.md`、构建命令和发布资产校验。

出口证据：

- 本地 HTTP、HTTPS、SOCKS5 测试服务验证 allow/deny，测试不访问公网。
- 原始 TCP 直连、忽略代理环境的程序和私网/loopback 目标均不能绕过规则。
- `network_access=enabled`、`danger-full-access` 和用户 Shell 不错误继承受管代理。
- sidecar 协议错误、代理启动失败和规则加载失败均 fail closed。
- Rust 定向测试、Python sandbox/process 定向测试和语法检查通过。

### 迭代 2：前台逐次网络审批

**交付用例**：Root 或 Subagent 的前台受限命令访问未匹配目标时只弹出一次审批；接受后
仅恢复该请求，拒绝或取消后工具得到正确终态。

实施范围：

- 定义不可变的执行身份，贯穿 `ToolHandlerContext -> WorkspaceProcessPort ->
  ProcessSessionSpec -> SandboxClient -> sidecar`。
- 在 Harness 建立活动执行注册和网络请求归属，不让 infrastructure 读取 TurnContext。
- sidecar 将 `BlockedRequest` 转换为 V1 新增的网络审批事件，并等待结构化 resolve。
- application 构造本地网络审批，复用 `ApprovalCoordinator` 和现有前端。
- 按 `environment + host + protocol + port + turn + execution` 归并同一待审批目标。
- `approval_policy=never`、非受管权限和找不到唯一执行归属时直接拒绝。
- 实现请求断线、调用取消、sidecar 退出和用户 cancel 的幂等收束。

出口证据：

- allow once 只放行原始请求，下一次同目标重新询问。
- 同执行内并发请求只展示一张审批卡，所有等待者得到同一决定。
- 不同 Turn、环境、协议或端口不会错误共享待审批结果。
- 进程先退出、请求先断开、用户先决定三种竞态均无悬挂 future 或迟到放行。
- Root、Subagent、CLI、TUI 和非交互入口的策略行为有定向回归。

### 迭代 3：会话决定、持久规则和审批体验

**交付用例**：用户可选择仅本次、当前会话或未来规则；运行中行为与写入规则一致，重启后
持久规则仍生效。

实施范围：

- Harness 网络审批服务持有 session approved/denied host 集合和提交锁。
- 新增具名网络 policy amendment，支持 allow 与 deny，不复用宽泛字典穿层。
- 为 `ExecPolicyManager` 增加网络规则持久化和内存 Policy 更新，复用现有 parser/AST 语义。
- 提交顺序对齐 Codex：校验 host，更新运行中代理，确立当前决定，再执行可能失败的磁盘
  写入；磁盘失败必须告警且不能撤销已经生效的运行时决定。
- deny 删除同键 session allow，deny 优先于正在等待或稍后到达的 allow。
- 对齐网络专用标题、目标、原因、决定文案和快捷键；cancel 保持控制决定，不伪装 decline。
- 展示规则保存成功或失败的结构化结果，不在 TUI 中实现策略逻辑。

出口证据：

- `acceptForSession` 只覆盖相同会话键，换环境、协议或端口后重新询问。
- 持久 allow/deny 写入合法 `network_rule(...)`，无重复规则，重载后结果相同。
- 运行时刷新成功但磁盘失败、运行时刷新失败、并发保存三条失败路径均有测试。
- allow/deny 提案与批准目标不一致时拒绝提交。
- 审批模型、终端 renderer、TUI 交互、规则 Manager 和真实 sidecar 端到端测试通过。

### 迭代 4：持续执行、后台生命周期和绕行面收口

**交付用例**：持续命令转入后台或原 Turn 完成后，新的网络请求仍能正确归属、审批和回写
最终工具结果；所有模型可达的本地执行面都不能绕过受管网络。

实施范围：

- 将 active network call 转为可延迟完成的执行句柄，绑定 process session 和 cancellation。
- `write_stdin`、轮询、后台化、超时、显式 terminate、应用关闭和工作区切换共享同一状态机。
- 活动 Turn 已结束但进程仍有效时，按明确规则附着当前可交互 Turn；无法唯一归属时拒绝，
  不猜测最近 Turn。
- Root 与 Subagent 并发时按 execution ID 精确归属，不使用“唯一活动调用”以外的模糊回退。
- 将受限 JS REPL 内核纳入 sidecar/OS 网络约束，或在受限模式明确关闭其原生网络；仅注入
  proxy env 不算完成。
- 审计 Hook、外部 MCP、用户 Shell 和应用自身 HTTP，确保它们既不会误用受管代理，也
  不能被模型当作未声明的网络跳板。
- 增加结构化审计：环境、协议、host、port、决定来源和耗时；不记录 URL path/query 或
  凭证。

出口证据：

- 后台请求批准、拒绝、Turn 取消、进程结束和 sidecar 重启全部收敛。
- JS `fetch`、Node `net.connect`、子进程和嵌套 shell 工具分别有绕行测试。
- 同一持久进程跨多个 Turn 时不复用过期审批上下文。
- 应用关闭后无残留代理监听器、审批任务、Node 内核或 sandbox 子进程。
- Windows 产品范围的完整网络审批行为矩阵通过，才可宣称 Windows 对齐完成。

### 迭代 5：macOS 与 Linux 平台对齐

**交付用例**：同一受管网络契约在 macOS 和 Linux 上成立，平台差异只存在于 sidecar
内部，Python、application 和前端不增加平台分支。

实施范围：

- sidecar 按平台选择 Windows Restricted Token、macOS Seatbelt、Linux seccomp/
  Landlock/bubblewrap 执行后端，不再硬编码 Windows sandbox 类型。
- macOS 只允许访问代理监听端口，并覆盖 Unix socket allowlist 和绝对路径限制。
- Linux 接入并打包 `codex-linux-sandbox`，验证禁止绕过代理的网络 namespace/出口约束。
- 为 x86_64/arm64 的实际产品目标生成、签名并校验 sidecar 资产。
- 建立平台 CI；缺少真实平台强制出口测试时，不得仅凭 Rust 单元测试标记完成。

出口证据：

- 三个平台运行相同的 HTTP、HTTPS、SOCKS5、直接 TCP、私网目标和取消矩阵。
- macOS Unix socket 和 Linux namespace/WSL 差异具有独立失败路径测试。
- 打包后从真实应用根定位资产并启动，不依赖源码树或 Cargo target。
- 如果 Linux 尚未进入产品支持范围，应把 Linux 明确标为后续迭代，不得把 Windows/macOS
  完成描述成全平台完成。

### 迭代 6：恢复、安全一致性与发布验收

**交付用例**：崩溃、重启、配置刷新和高并发下不会产生静默放行、重复审批或策略与运行时
不一致，并能用固定矩阵证明与选定 Codex 基线等价。

实施范围：

- 对账未收束审批：重启后恢复可恢复事实，无法恢复的连接明确取消，不合成用户决定。
- 覆盖代理端口占用、CA 生成失败、上游代理、DNS 解析变化、规则热更新和 sidecar 崩溃。
- 验证 allowlist-first、deny-wins、私网保护、通配符、limited mode 和审计脱敏。
- 对比 Codex 参考测试，为每一项差异记录“等价、产品明确不支持或阻塞”，不接受未解释差异。
- 固定 Rust 来源 revision、Cargo.lock、构建 toolchain、二进制校验和及第三方 attribution。
- 更新本计划状态、架构映射、sandbox 文档、发布清单和用户权限说明。

出口证据：

- 网络一致性测试、全量 Python 测试、Rust workspace 定向测试、架构守卫和编译检查通过。
- 每个支持平台都有真实 sidecar 端到端证据，而不是 mock-only 证据。
- 安全审查确认模型可达执行面不存在已知的非代理网络路径。
- 资源泄漏、审批悬挂、规则竞态、迟到决定和 fail-open 测试全部通过。
- 产品文档只对已通过矩阵的平台和协议声明支持。

## 验证矩阵

每个受管执行面至少覆盖以下组合：

| 维度 | 值 |
| --- | --- |
| 网络模式 | restricted、enabled |
| 审批策略 | on-request、never |
| 规则 | 无匹配、allow、deny、deny 覆盖 allow |
| 决定 | accept、acceptForSession、持久 allow、持久 deny、decline、cancel |
| 协议 | HTTP、HTTPS、SOCKS5 TCP、SOCKS5 UDP |
| 目标 | 公网形态、本机、私网、域名、IPv4、IPv6、通配符子域 |
| 生命周期 | 前台、后台、超时、进程先退出、请求先断开、Turn 取消、应用关闭 |
| 并发 | 同键归并、不同环境、不同 Turn、不同端口、Root/Subagent 并行 |
| 平台 | Windows、macOS、Linux；仅对正式支持平台做完成声明 |

所有网络测试默认使用本机临时服务和受控 DNS/证书夹具，不依赖公网稳定性。需要验证公网
出口时单独标记为可选集成测试，不能作为唯一完成证据。

## 不可接受的实现

- 只新增 TUI 按钮或 wire 字段，没有实际阻断连接。
- 只设置 `HTTP_PROXY`，没有 OS 层禁止直接 socket。
- 在 Python 中重新实现 HTTP CONNECT、SOCKS5 或 HTTPS MITM。
- 把 TurnContext、ApprovalCoordinator 或配置路径传入 Rust/平台层。
- 在 `protocol` 中混入本地 sidecar 消息。
- 用 `typing.cast()`、`Any` 扩大、动态属性或 `# type: ignore` 掩盖执行身份缺失。
- 为本次改造引入 sidecar V2、双版本兼容分支，或静默接受未知安全字段。
- 将用户 Shell、应用自身 HTTP 或外部 MCP 全局注入代理，造成职责外行为变化。
- 在 JS REPL、后台进程或 Subagent 仍可绕行时宣称网络审批完成。

## 计划维护

- 本文件管理网络审批对齐状态，不改变 `AGENT_RUNTIME_MIGRATION.md` 已完成的阶段 5 状态。
- 每次只把一个迭代标为“进行中”；进入前必须补充具体改动文件、生产用例和失败路径。
- 完成迭代时记录测试命令、通过数量、平台、Rust revision 和仍未覆盖的限制。
- 如果实现发现状态所有权或依赖方向与本计划不符，应先更新本计划和架构文档，再改代码。
- 第 4 迭代完成前只能描述为“网络审批建设中”；第 6 迭代完成且平台矩阵通过后，才能对
  对应平台声明 Codex 行为等价。
