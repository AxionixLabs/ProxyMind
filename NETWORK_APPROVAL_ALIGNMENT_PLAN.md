# 审批架构与网络审批实施计划

- 计划版本：V1
- 状态：迭代 6 进行中
- 基线日期：2026-09-02
- Codex 源码基线：revision `608f4a8a98feff0889cbfc9ed691efbf42d34cc6`
- 目标平台：Windows、macOS、Linux
- 架构权威：`ARCHITECTURE.md`

参考实现：

- `codex-main/codex-rs/core/src/tools/approvals.rs`
- `codex-main/codex-rs/core/src/tools/sandboxing.rs`
- `codex-main/codex-rs/core/src/tools/network_approval.rs`
- `codex-main/codex-rs/core/src/session/mod.rs`
- `codex-main/codex-rs/network-proxy`
- `codex-main/codex-rs/sandboxing`
- `codex-main/codex-rs/linux-sandbox`
- `codex-main/codex-rs/windows-sandbox-rs`
- `codex-main/codex-rs/tui/src/bottom_pane/approval_overlay.rs`

## 目标与结论

本计划不是先复制 Codex 网络代理，也不是先扩展 ProxyMind 的 sandbox 子进程协议。实施顺序
固定为：

```text
评估 Codex 审批架构
  -> 建立更严格的 ProxyMind 通用审批核心
  -> 迁移现有命令、补丁、权限和 MCP 审批
  -> 接入三平台受管网络执行
  -> 把网络审批作为一种 ApprovalAction/Effect 接入
  -> 完成恢复、安全与平台验收
```

审批架构可以成为 ProxyMind 超越 Codex 的产品能力，但不能成为一套以 `Mind` 命名、与
Agent Harness 平行的系统。`mind.py` 和 `composition.py` 只负责选择实现并组合端口；审批
领域、用例、状态和恢复分别归 `agent.domain`、`agent.application`、`agent.harness` 和
`agent.stores` 所有。

建议分 **6 个迭代**：

| 迭代 | 主要交付 | 相对规模 | 状态 | 完成后的声明范围 |
| --- | --- | --- | --- | --- |
| 1 | 审批领域契约、状态所有权和迁移准入 | 大 | 已实现 | 目标审批架构冻结 |
| 2 | 通用审批核心落地并迁移现有审批 | 特大 | 已实现 | 非网络审批使用新核心 |
| 3 | 三平台静态受管网络 | 特大 | 已实现 | 静态网络规则可执行 |
| 4 | 网络决定、规则提交和审批卡 | 特大 | 已实现 | 前台网络审批闭环 |
| 5 | 后台生命周期、执行面收口和平台加固 | 特大 | 已实现 | 三平台功能闭环 |
| 6 | 恢复、安全一致性和发布验收 | 大 | 进行中 | 可声明选定 Codex 基线行为等价 |

计划版本 V1 只表示本文的产品实施版本，与任何 Python/Rust 私有协议版本无关。执行适配器
需要升级协议时，应根据兼容性和安全字段变化独立决策，不得为了维持“V1”而静默接受未知
字段或保留永久双版本分支。

## Codex 审批架构评估

### 值得复用的设计

| Codex 设计 | 证据 | ProxyMind 取向 |
| --- | --- | --- |
| 统一动作模型 | `ApprovalAction` 覆盖 exec、patch、MCP、network 和 permission request | 建立通用动作族，不为网络另建审批框架 |
| 策略判定先于交互 | `ExecApprovalRequirement` 区分 skip、needs approval、forbidden | 保留纯策略判定，不让前端决定是否需要审批 |
| Reviewer 路由集中 | `request_approval` 统一执行 Hook，再选择 Guardian/user | 建立统一 reviewer chain，记录决定来源 |
| 会话批准复用 | `with_cached_approval` 支持 approved-for-session | 保留会话 grant，但改用具名 key 和独立 Store |
| 网络请求并发归并 | `PendingHostApprovalKey` 共享同一目标的等待结果 | 使用严格作用域 key 归并，不重复弹卡 |
| 失败关闭 | 请求断线、owner drop、代理失败时默认 deny | 所有未知、断线、超时和恢复歧义均 fail closed |
| 动态规则提交 | 网络 allow/deny amendment 更新运行时和配置 | 由网络 Effect owner 执行并报告部分成功 |
| 展示独立投影 | TUI 根据结构化事件渲染审批 overlay | 前端只负责布局、颜色、输入和焦点 |

### 不直接复制的局限

| Codex 当前局限 | 风险 | ProxyMind 改进 |
| --- | --- | --- |
| `ApprovalStore` 用序列化 key 的进程内 `HashMap` | 类型边界弱，不能承担恢复和审计 | 类型化 Grant Key；会话 grant 与审批事实分离 |
| `ReviewDecision` 同时承载多种动作的决定 | 非法动作/决定组合只能在运行时兜底 | 每种 action 配套精确 decision union |
| `NetworkApprovalService` 同时持有归属、等待者、grant、持久提交和遥测 | 状态所有权集中，难以恢复和替换 | 拆为执行注册、审批用例、Grant Store 和网络 Effect owner |
| 缺少 execution ID 时可回退到“唯一活动调用” | 后台和并发场景可能把网络请求归错 Run | 缺失或歧义一律拒绝，不猜测最近或唯一调用 |
| 网络审批依赖当前活动 Turn | 长驻进程跨 Turn 时生命周期不自然 | 使用稳定 Session/Run/Action/Execution 身份和后台审批上下文 |
| 用户展示调用仍由 `Session` 按动作分支 | application、协议和展示耦合较深 | 中立 presentation 端口与 action handler 分离 |
| 会话决定、审批事实和持久规则不是统一恢复模型 | 重启后只能丢弃或靠专项逻辑推断 | 单调 Approval Fact Store 加独立 Effect Journal |

“更好”必须由上述结构和测试证明，不以模块数量、抽象名称或额外审批类型衡量。

## ProxyMind 当前基线

已经具备：

- `ApprovalCoordinator` 提供 FIFO 展示、重复请求共享、取消、关闭和只读队列快照；
- `ApprovalOutcome` 已记录决定、来源、收束原因和时间；
- command、patch、permission、MCP 和 JS nested call 已有审批调用链；
- Hook、user、`auto_review` 和非交互策略已有部分路由能力；
- `protocol` 已能校验和提交线上 `mind.chat` 网络审批事件；
- `agent.domain.execution_policy` 已能解析和匹配 `network_rule(...)`；
- 本地源码树包含 Codex proxy/sandbox Rust crate，可作为固定基线复用。

必须先偿还的结构债务：

- coordinator 同时持有展示队列和决定等待状态，并按进程级 `request_id` 建索引；
- cancel 可以批量收束与来源 Run 无关的排队请求；
- 核心端口仍接受 `Mapping[str, Any]`，payload 内部继续使用可变字典；
- 通用 decision union 允许动作不支持的决定进入运行时；
- `ApprovalCallLedger` 和 `PermissionGrantStore` 是进程内集合，不能承担恢复权威；
- 本地执行 identity 尚未稳定贯穿 Session、Run、Action、Environment 和 Execution；
- 线上网络审批 schema 已存在，但本地受管代理阻断、回调和规则提交尚未形成闭环；
- 当前 sandbox 子进程是 ProxyMind 自有适配方式，不能被误认为 Codex 审批架构。

## 能力取舍

| 上游能力 | Codex 基线状态 | 本计划决定 |
| --- | --- | --- |
| 独立 Skill 审批 | 运行路径已移除 | 不新增 Skill 审批 kind、卡片或授权缓存 |
| Skill `scripts/*` | 作为普通 `exec_command` 执行，受同一 exec policy/sandbox；可信插件脚本可附带归因 | 按 command 审批、沙箱和网络策略执行；`plugin_id/script_path` 仅用于审计，不改变权限 |
| granular `skill_approval` | schema/测试夹具仍有字段，但当前生产路径无消费者；Skill 脚本测试明确跳过旧 gate | 新本地契约不消费；线上字段只在协议整体迁移时删除 |
| `approval_policy=untrusted` | 公共配置已退役 | 新路径不接受；project trust 保持独立概念 |
| `guardian_subagent` | `auto_review` 的 legacy alias | 不接受 alias，不写兼容分支 |
| `on-failure` | `on-request` 的 legacy alias | 不接受 alias |
| granular approval | 实验 API | 只吸收按动作控制的思想，不冻结实验 wire 字段 |
| Guardian/auto review | 活跃稳定路径 | 作为可替换 reviewer，超时和不可用显式 fail closed |
| MCP 会话/持久审批 | 活跃生产路径 | 迁移时复用通用核心，产品策略单独验收 |
| managed network proxy | 活跃实验能力 | 冻结本计划需要的行为，不复制无关实验开关 |

Skill 脚本与普通命令产生相同外部 Effect 时，必须走相同执行策略和审批链路。Skill 来源只
用于审计，不能授予权限。

### Skill 脚本行为复核

对固定 Codex 基线复核后，Skill 的两条路径必须保持分离：

1. `$skill`/Skill 列表只负责加载正文、脚本元数据和遥测，不产生独立审批请求。
2. 模型随后调用 `exec_command` 执行 `scripts/*` 时，动作类别仍是 command；普通 exec policy、
   sandbox、网络阻断和统一 reviewer chain 共同决定是否放行。
3. 可信插件脚本可在审批事件中携带 `plugin_id`、`script_path` 作为来源证据；来源字段不得
   变成额外 grant，也不得使用 Skill 声明的权限扩大本轮沙箱。
4. 复杂 shell、缺失脚本、符号链接逃逸或多个插件根同时命中时不做可信归因，但仍按普通
   command 处理，不能因为无法归因而放行。

基线证据：`codex-main/codex-rs/core/src/skills.rs`、
`codex-main/codex-rs/core-plugins/src/script_attribution.rs`、
`codex-main/codex-rs/core/src/tools/handlers/unified_exec/exec_command.rs` 和
`codex-main/codex-rs/core/tests/suite/skill_approval.rs`。

## 目标审批架构

### 职责链

```text
tool intent / blocked network request
  -> action owner 构造类型化 ApprovalAction
  -> 纯 Policy Evaluator 返回 allow / deny / review
  -> 精确作用域 Grant Resolver
  -> Approval Fact Store 记录 requested
  -> Hook reviewer
  -> 配置选择的 user 或 auto_review reviewer
  -> Approval Fact Store 以 CAS 确立首个终态
  -> action owner 校验决定并准备 Effect
  -> capability/infrastructure 执行
  -> Effect Journal 提交或进入 reconciliation
  -> typed result / protocol projection
```

用户 reviewer 需要交互时，每个交互展示通道的 `PresentationArbiter` 只串行化该通道的可见
表面：

```text
review request -> neutral presentation -> frontend -> typed decision
```

它不拥有策略、grant、请求终态或外部 Effect。

### 类型契约

- `ApprovalIdentity` 至少包含 `session_id`、`run_id`、`approval_id` 和 `action_id`。
- 执行动作另带 `environment_id`、`execution_id`；工具动作另带稳定 `tool_call_id`。
- 线上 `cid/sid/turn_id/approval_id/call_id` 只在 protocol adapter 映射，不与本地 ID 比较或
  依赖字符串相同。
- `ApprovalAction` 使用判别联合表达 Exec、Patch、Permission、MCP 和 Network；不使用
  `Mapping[str, Any]` 或动态属性猜测。
- 每种 action 定义自己的可用决定。Network 决定显式区分 allow-once、allow-for-session、
  持久 allow、持久 deny、decline 和 cancel。
- 持久 amendment 直接包含在不可变 decision 中，并绑定原 action fingerprint；提交阶段不从
  UI payload 和决定字符串重新拼装。
- `ApprovalOutcome` 保留 decision、source、reason、resolved_at 和事实版本；decline、cancel、
  timeout、unavailable、abandoned 不互相伪装。

### 状态所有权

| 状态 | 所有者 | 生命周期 |
| --- | --- | --- |
| 动作是否需要审批 | domain Policy Evaluator | 纯计算 |
| 待决请求、等待者和 cancel | Session/Run approval registry | Run 或后台执行上下文 |
| 单一交互表面队列 | per-channel `PresentationArbiter` | frontend/交互会话 |
| requested/resolved/abandoned 事实 | `agent.stores.approvals` | 持久、首终态权威 |
| 会话 grant | `SessionGrantStore` | Session 关闭时丢弃 |
| 持久命令/网络规则 | 配置策略 Store | 跨进程持久 |
| prepared/committed/unknown Effect | `agent.stores.effects` | 持久、可对账 |
| 代理连接与运行时规则 | network capability owner | 执行资源生命周期 |
| 布局、颜色、快捷键和焦点 | frontend | 展示会话 |

### 并发与终态

- 审批事实只允许 `requested -> resolved` 或 `requested -> abandoned`；首个终态权威。
- 同一严格 action fingerprint 可以共享等待结果；不同 Session、Run、Action、Environment、
  Execution、协议或端口不得共享。
- `request_id` 只用于传输幂等，不能作为进程级授权身份。
- 一个 Run 的 cancel 只收束该 Run；应用关闭才收束全部 Session。
- 前端任务失败只影响对应展示请求，不把队列中其他请求变成 decline。
- reviewer 迟到结果、重复 resolve、断线后 allow 和旧 pending 重放都不能重新打开终态。

### 裁决优先级

顺序固定为：

1. 显式 deny 或不允许交互的策略；
2. 显式 allow 或已存在且作用域完全匹配的 grant；
3. PermissionRequest Hook；
4. 配置选择的 user 或 auto_review reviewer。

Hook deny 不再交给其他 reviewer；auto_review timeout/unavailable 不静默回退用户批准。未来
增加 reviewer 时只能实现同一端口，不能复制 action handler。

## 网络审批接入边界

### Codex 的真实执行结构

Codex 没有把三平台沙箱统一做成长驻 JSONL sidecar：

- macOS 为每次命令生成 Seatbelt profile，并通过 `/usr/bin/sandbox-exec` 包装执行；
- Linux 使用 `codex-linux-sandbox`/bubblewrap/Landlock/seccomp 执行 helper；
- Windows 使用 Restricted Token 或 Elevated sandbox backend；
- `NetworkProxy` 由 Session 启动和持有，以 Rust library、listener task 或 Windows shared
  ingress 运行；
- 远端 executor 可以接收 sandbox/network context，但这不是通用本地 sidecar 协议。

ProxyMind 可以保留现有 sandbox 子进程作为本地部署适配器，但这只是 infrastructure 选择：

- 它不得持有 Approval Store、reviewer、session grant、前端字段或线上协议语义；
- 它只接收已经解析的执行权限，报告精确 execution identity 和被阻断网络请求，并执行结构化
  resolve；
- 其协议版本按兼容性单独管理，不与本计划 V1 绑定；
- 若直接复用 Codex Rust library/平台 backend 更简单，应删除多余进程边界，不为既有 sidecar
  保留无意义 facade。

### 受管范围

纳入：

- 模型发起的 `shell_command`、`exec_command` 及后续 `write_stdin`；
- 后台化后仍存活的上述进程；
- Root Run 和 Subagent Run 发起的上述进程；
- 受限模式下的 JS REPL 内核及其嵌套进程。

不纳入：

- 应用自身模型、授权、更新、配置服务和 MCP HTTP 传输；
- 用户显式启动的交互 Shell；
- 用户配置并信任的外部 MCP/Hook 进程，除非以后建立独立权限模型；
- `danger-full-access` 进程。

### 网络行为契约

| 条件 | 行为 |
| --- | --- |
| network disabled | OS 层阻断，不展示目标审批 |
| network enabled | 文件系统沙箱保持，网络不触发逐目标审批 |
| restricted、deny 命中 | 立即拒绝，deny 优先 |
| restricted、allow 命中 | 立即允许 |
| restricted、未匹配、policy never | 拒绝且不展示 |
| restricted、未匹配、reviewer user | 阻塞该连接并展示一次审批 |
| restricted、未匹配、reviewer auto_review | 自动审查，不同时展示用户卡 |
| allow once | 只恢复原始阻断请求 |
| allow for session | 只覆盖精确 Session+Environment+host+protocol+port |
| persistent allow/deny | 校验并提交 network rule，返回真实提交结果 |
| decline | 拒绝当前请求 |
| cancel | 取消来源 Run，不影响其他 Run |
| 请求断线或归属歧义 | abandoned/deny，不允许迟到放行 |

目标规范化复用 Codex host、协议和私网保护语义。持久规则继续使用现有 execpolicy 的
`host + protocol + decision` 契约；端口只参与会话 grant 和待决归并，不暗中扩大持久规则
格式。

### 网络 Effect 提交

网络 approval core 只返回类型化决定。网络 action owner 必须：

1. 校验 action identity、目标 fingerprint 和 amendment 完全一致；
2. 在同一提交锁内复核 deny 和并发规则变化；
3. 更新当前运行时代理策略；
4. 确立当前请求可观察终态；
5. 尝试持久化规则并返回成功或失败事实；
6. 磁盘失败时不得谎称持久成功，也不得撤销已经发生的当前运行时决定。

决定、运行时更新和磁盘提交的部分成功必须进入 Effect Journal，不能靠前端提示充当事实。

### 网络审批卡

网络卡视觉也属于对齐范围：

- 默认终端表面，不填充整张卡片背景；
- 标题加粗；目标 host/协议/端口可扫描且不截断关键内容；
- 原因斜体；
- 未选快捷键和页脚弱化；
- 选中整行使用语义青色并加粗；
- allow、deny 不额外使用绿红色编码；
- truecolor、ANSI 256、ANSI 16、深色、浅色和颜色能力未知时保持可读；
- 窄终端换行不覆盖前后内容，关闭后焦点回到输入区。

若通用审批样式不能同时满足网络卡和既有命令/补丁卡，前端按 action kind 选择专用语义
token；application/domain 不保存 RGB、边框或布局指令。

## 架构归属

| 位置 | 职责 |
| --- | --- |
| `agent/domain/approvals` | identity、action/decision、grant key、终态和纯优先级规则 |
| `agent/domain/execution_policy` | 命令/host/协议规则和纯匹配语义 |
| `agent/application/approvals` | 审批用例、reviewer chain、决定校验和中立 presentation |
| `agent/ports` | reviewer、presentation、fact store、grant、effect 和网络执行的最小协议 |
| `agent/harness/approvals` | Session/Run registry、等待者、归并、作用域 cancel 和每通道展示仲裁 |
| `agent/stores/approvals` | requested/resolved/abandoned 首终态事实 |
| `agent/stores/effects` | 外部效果提交与 unknown 对账事实 |
| `infrastructure/config` | 持久命令/网络规则的原子读写 |
| `infrastructure/platform` | Codex proxy/sandbox 的本地或远端执行适配，不判断审批策略 |
| `frontends` | 卡片、颜色、键盘、焦点和无交互错误投影 |
| `mind.py` / `composition.py` | 唯一具体组合根和资源关闭顺序 |

不新增 `mind_approval`、`MindApprovalService` 或第二个全局 coordinator。新类和模块按职责
命名。

## 迭代计划

### 迭代 1：审批领域契约与迁移准入

**交付用例**：在不改变现有用户行为的前提下，冻结审批 identity、action、decision、事实、
grant、reviewer 和 Effect 边界，并用契约测试证明非法组合无法进入核心。

实施范围：

- 建立 `agent/domain/approvals` 的不可变判别联合和值对象。
- 定义本地 identity 与线上 identity 的 adapter 映射。
- 定义 Approval Fact Store、Session Grant Store、Reviewer、Presentation 和 Effect 端口。
- 确定 Session/Run registry 与 per-channel PresentationArbiter 的所有权和关闭顺序。
- 为当前 command、patch、permission、MCP、JS nested approval 建立迁移清单。
- 固定旧 `Mapping[str, Any]`、字符串 decision、进程级 request ID 索引和跨 Run cancel 的同次
  删除条件。

出口证据：

- action/decision 非法组合、identity 冲突、重复终态和跨 Run cancel 契约测试通过。
- 核心类型不依赖 `protocol`、frontend、配置路径或具体网络客户端。
- 静态检查确认没有新 `Mind` 领域命名、`typing.cast()`、`Any` 扩大或兼容 fallback；架构守卫由
  开发者按本轮指示不再重复运行。
- 现有审批测试保持不变通过。

### 迭代 2：通用审批核心与现有审批迁移

**交付用例**：命令、补丁、权限、MCP 和 JS 嵌套调用全部经过新核心；Hook、user、auto_review、
会话批准、取消和非交互入口行为稳定。

实施范围：

- 实现纯 Policy Evaluator、ReviewerChain、RunApprovalRegistry 和 PresentationArbiter。
- 实现持久 Approval Fact Store 与进程内 Session Grant Store，首终态使用 CAS/事务。
- 把 coordinator 的展示队列与决定等待状态拆开。
- 逐类迁移现有审批 action handler，决定应用仍归各 action owner。
- 接入 Effect Journal：审批成功不等于外部 Effect 已成功。
- 迁移完成后删除旧 Mapping 入口、动态 payload、旧 ledger authority 和跨 Run batch cancel。

出口证据：

- Hook allow/deny、user、auto_review allow/deny/timeout/unavailable 全矩阵通过。
- 相同 request ID 的不同 Session/Run 不冲突；同 action 重复请求幂等。
- presenter 失败、调用方取消、应用关闭和迟到决定无悬挂 future。
- command、patch、permission、MCP、Root/Subagent、CLI/TUI/非交互回归通过。
- Skill 脚本没有专用审批路径，权限不因来源放大。

### 迭代 3：三平台静态受管网络

**交付用例**：Windows、macOS、Linux 的 restricted 进程只能通过 Codex managed proxy 访问
静态允许目标；直接 socket 和忽略代理环境的程序不能绕过。

实施范围：

- 复用 `codex-network-proxy` 的 HTTP、HTTPS、SOCKS5、MITM 和私网保护实现。
- 由 composition 决定直接嵌入、执行 helper 或现有 sandbox adapter；记录选择和删除条件。
- Windows 接入 Restricted Token/Elevated backend、proxy SID/WFP 和进程树清理。
- macOS 接入 Seatbelt，只允许受管代理端口及声明的 Unix socket。
- Linux 接入 bubblewrap/network namespace/seccomp 及代理 bridge。
- 将 network mode、规则和 execution identity 从 action owner 传到执行边界。
- 固定三平台与 CPU 架构的构建、签名、校验和及资产定位。

出口证据：

- 三平台使用相同本机 HTTP/HTTPS/SOCKS5 夹具验证静态 allow/deny。
- 原始 TCP、loopback、私网、IPv4、IPv6 和忽略 proxy env 的绕行测试通过。
- network enabled、danger-full-access、用户 Shell 和应用 HTTP 不错误继承代理。
- 代理启动、规则加载、平台强制机制或 capability 缺失时 fail closed。
- 实现方交付三平台单命令本机验收脚本；用户在对应机器回填结果。

### 迭代 4：网络审批、规则提交与审批卡

**交付用例**：未匹配目标产生类型化 Network action，并通过统一 reviewer chain 得到一次、
会话或持久决定；审批卡行为和颜色与 Codex 基线对齐。

实施范围：

- 网络执行 adapter 报告带精确 Session/Run/Action/Execution identity 的 blocked request。
- Network action owner 规范化 host、protocol、port 和原始目标 fingerprint。
- 同严格作用域和目标的并发请求共享一个待决审批；其他请求隔离。
- 实现 allow-once、allow-for-session、persistent allow/deny、decline、cancel。
- 按网络 Effect 提交顺序更新运行时、审批事实和持久规则。
- 接入 Hook、user、auto_review，保留来源、超时和不可用终态。
- 实现网络审批卡及终端颜色降级快照。

出口证据：

- allow-once 不污染下一请求；session grant 不跨 Session/Environment/protocol/port。
- deny-wins；amendment 与原目标任何错配均在代理更新前拒绝。
- 运行时成功但磁盘失败、运行时失败、并发保存和重复提交具有确定结果。
- 请求先断开、进程先退出、决定先返回三类竞态无迟到放行。
- 网络卡在基准宽度和窄终端通过解析后颜色/属性断言；既有审批卡无非预期变化。
- 三平台真实执行 adapter 通过同一前台审批矩阵。

### 迭代 5：后台生命周期、执行面收口与平台加固

**交付用例**：长驻进程跨 Run、后台化、Subagent 和 JS REPL 的网络请求仍精确归属；所有
模型可达执行面都不能绕过受管网络。

实施范围：

- `ProcessSessionSpec` 和工作区进程端口携带 `cid/sid/run_id/environment_id`；受管网络回调由
  组合根绑定同一个 `NetworkApprovalService`，缺失身份时 fail closed。
- 每个受限进程创建独立回环代理实例，共享静态策略但不共享代理 Session 标识；一次授权、
  Session 授权和并发阻断不会串线。
- 代理启动失败、进程异常启动、正常退出、超时、终止、工作区替换和应用关闭都回收代理及
  连接任务；`network_access=enabled` 不注入代理。
- 原 Run 结束后保留明确后台审批上下文；不可用时拒绝，不附着当前或最近 Run。
- `write_stdin`、轮询、超时、terminate、工作区切换和应用关闭共享一个状态机。
- JS REPL 内核及嵌套 shell 纳入平台网络约束，不能只注入 proxy env。
- Skill `scripts/*`、插件脚本和嵌套 shell 复用 command approval；保留可信来源归因但不提供
  Skill 专属权限或缓存。
- 审计 Hook、外部 MCP、用户 Shell 和应用 HTTP，防止误代理或模型绕行。
- 加固 Windows 进程树/Job Object、macOS Seatbelt/Unix socket、Linux namespace/WSL 差异。
- 完成三平台发布资产和真实产品目录烟测。

出口证据：

- 进程级代理隔离、阻断回调重新评估和终态回收已由定向测试覆盖；后台批准、拒绝、Run
  cancel、请求断线和执行 adapter 重启的三平台真实证据仍待验收。
- 同一持久进程跨多个 Run 不复用过期审批上下文。
- JS `fetch`、Node `net.connect`、子进程和嵌套 shell 具有绕行测试。
- 应用关闭后无残留代理、审批任务、Node 内核或 sandbox 进程。
- 三个平台完整网络审批矩阵通过；未取得本机结果的平台标记“实现完成待验收”。

### 迭代 6：恢复、安全一致性与发布验收

**交付用例**：崩溃、重启、配置刷新和高并发不会产生静默放行、重复 Effect 或策略/运行时
不一致，并能证明与固定 Codex 基线的等价项和明确改进项。

实施范围：

- 受管代理覆盖 HTTP、HTTPS/CONNECT 和 SOCKS5 CONNECT；不支持的 SOCKS5 UDP 命令明确
  返回拒绝，不把未实现能力误报为已放行。
- 每个实际受管进程生成独立 `execution_id`，网络阻断回调、审批载荷和去重 ID 均绑定该
  执行身份；同一 Session 的会话授权仍可复用，不同执行不会共享一次性决定。
- 允许域名在上游连接前执行有界 DNS 检查；解析到回环、私网、链路本地、保留或未指定
  地址时拒绝转发，显式 IP 与 `localhost` 仍遵循静态规则。
- 持久网络 allow amendment 先写入执行策略规则文件，再安装运行时规则；工作区运行时
  创建时从已有 `network_rule` 水合受管策略，重启后不丢失批准。
- 对账 requested/resolved/abandoned Approval Facts 和 prepared/committed/unknown Effects。
- 覆盖代理端口占用、CA 失败、DNS 变化、上游代理、规则热更新和 Rust 执行组件崩溃。
- 对比 Codex 测试，为每项记录“等价、严格改进、明确不支持或阻塞”。
- 复核上游能力状态，禁止废弃字段重新进入生产路径。
- 删除所有被替代 authority、动态审批入口和无删除条件的兼容层。
- 固定 Rust revision、Cargo.lock、toolchain、资产校验和和 attribution。

出口证据：

- 网络一致性、全量 Python、Rust 定向和编译检查通过；架构守卫按本轮开发指示不运行，
  包边界仍以既有审计文件和代码复核为准。
- `tests/test_managed_network.py`、`tests/test_network_approval_service.py`、
  `tests/test_sandbox_client.py` 和 `tests/test_codex_execpolicy.py` 的网络/策略回归通过；
  `tests/test_permission_grants.py` 覆盖 Skill `scripts/*` 复用普通 command policy。
- Approval/Effect 恢复、重复 resolve、写入失败、迟到事件和未知结果对账通过。
- 安全审查确认模型可达执行面不存在已知非代理网络路径。
- Windows、macOS、Linux 都有用户在对应机器执行的真实端到端证据。
- 产品文档只声明已被证据支持的行为，不把网络对齐扩大为全部审批产品等价。

## 验证矩阵

| 维度 | 值 |
| --- | --- |
| action | command、patch、permission、MCP、network |
| reviewer | Hook、user、auto_review、timeout、unavailable |
| identity | Session、Run、Action、Execution、相同 wire request ID、缺失/歧义 |
| decision | allow once、session、persistent allow/deny、decline、cancel |
| policy | on-request、never、显式 allow、显式 deny、deny-wins |
| lifecycle | 前台、后台、超时、断线、进程退出、Run cancel、应用关闭 |
| concurrency | 同 fingerprint 归并、不同作用域隔离、迟到决定、重复 resolve |
| recovery | pending 重启、首终态、事实失败、Effect unknown、规则部分成功 |
| protocol | HTTP、HTTPS、SOCKS5 TCP、SOCKS5 UDP、原始 TCP |
| target | 域名、IPv4、IPv6、通配符、本机、私网、端口差异 |
| source | 普通命令、Skill、JS REPL、嵌套命令、Root/Subagent |
| platform | Windows、macOS、Linux |
| card | 初始、切换、允许、拒绝、取消、焦点恢复、长文本、窄宽度 |
| terminal | truecolor、ANSI 256、ANSI 16、深色、浅色、能力未知 |

网络测试默认使用本机临时服务、测试 CA 和受控 DNS/目标，不依赖公网稳定性。实现方为三个
平台交付等价单命令入口、期望结果和脱敏日志格式；用户回填 OS、架构、源码 revision、
Rust 资产 SHA-256、命令和用例结果。三平台证据未齐时状态保持“实现完成待验收”。

## 不可接受的实现

- 先做网络专用 coordinator，再计划以后抽通用审批。
- 把审批架构放进 `mind_*` 领域模块、Controller、frontend 或 Rust sandbox adapter。
- 把 Codex 的 sandbox helper 描述为统一 sidecar 架构。
- 让产品计划 V1 约束私有执行协议版本。
- 让审批核心接收 `Mapping[str, Any]`、动态 payload 或无载荷决定字符串。
- 使用 wire request ID、当前 Turn 或“唯一活动调用”猜测本地授权身份。
- 让一个 Run 的 cancel 收束其他 Run 的请求。
- 把进程内 dict/set 命名为可恢复 Approval Store。
- 让审批决定直接等同于外部 Effect 成功。
- 为 Skill 建立独立审批 kind、卡片、权限缓存或 grant。
- 在新路径消费 `skill_approval`、`guardian_subagent`、`on-failure` 或 `untrusted`。
- 只注入 `HTTP_PROXY`，没有 OS 层禁止直接 socket。
- 在 Python 中重写 HTTP CONNECT、SOCKS5 或 HTTPS MITM。
- 把 reviewer、Approval Store、前端字段或线上协议塞进执行 adapter。
- 在后台、JS REPL、Subagent 或任一目标平台仍可绕行时宣称完成。
- 只对齐审批文案，忽略卡片背景、选中颜色、文本属性和焦点状态。

## 完成声明边界

第 6 个迭代完成且三平台本机证据齐全后，可以声明：

1. ProxyMind 通用审批核心覆盖本计划列出的 action、reviewer、作用域和恢复契约；
2. 三平台受管网络审批与固定 Codex 基线在可观察行为和安全边界上等价；
3. 类型安全、状态所有权、持久首终态和执行归属严格性是相对 Codex 基线的明确改进。

不能据此声明所有实验审批产品完全相同。granular approval、`request_permissions` 产品开关、
MCP 持久审批策略和线上废弃字段删除仍需各自契约。独立 Skill 审批不在待补清单中，因为它
是上游已移除的错误授权边界。

## 计划维护

- 每次只把一个迭代的实现标为“进行中”；平台验收状态单独记录。
- 进入迭代前补充具体文件、删除清单、生产用例、失败路径和定向测试命令。
- 完成时记录测试数量、平台、源码/Rust revision、资产校验值和剩余限制。
- 更新 `codex-main` 后重新核对 Stable/Experimental/Under Development/Removed 状态。
- 实现发现职责或依赖方向与本计划冲突时，先更新本计划和 `ARCHITECTURE.md`。
- 不以审批卡能显示、代理能启动或当前开发机测试通过单独标记网络审批完成。
