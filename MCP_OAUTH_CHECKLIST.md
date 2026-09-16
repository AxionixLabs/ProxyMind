# 外接 MCP OAuth 分阶段实现与真机验收清单

目标：支持 `mind mcp login <name>` 完成浏览器授权，保存凭据，并在后续外接 MCP
连接中恢复认证、刷新令牌；支持 `mind mcp logout <name>` 清除本地登录凭据。
最终以真实终端、系统浏览器、Sentry 服务和实际工具调用完成验收。

本文件是实施任务清单。顺序执行各阶段，满足完成条件后再勾选；已勾选项只表示对应阶段
的工作完成，不表示后续登录功能或真机验收已经完成。
客户端职责以 [ARCHITECTURE.md](ARCHITECTURE.md) 为准，跨系统职责以
[ARCHITECTURE_SYSTEM.md](ARCHITECTURE_SYSTEM.md) 为准；本文件不新增线上协议契约。

## 范围与现有基础

- 首版实现远程 Streamable HTTP MCP 的 OAuth 登录闭环，保留现有 stdio、SSE、Bearer
  Token 和 HTTP Header 认证行为。OAuth 登录与工具执行审批分别生效。
- 首版包含 CLI 登录、退出登录、凭据状态展示、运行时恢复与刷新、必要的并发保护和测试。
  企业 IdP、AppServer 登录 RPC、TUI 内嵌登录流程、远程机器粘贴回调交互另行立项。
- 当前依赖为 `mcp==1.24.0`，其 `OAuthClientProvider` 已提供 PKCE、认证发现、动态客户端
  注册、CIMD 和刷新基础能力。接入前必须验证冷启动恢复及真实服务兼容性。
- 实施前粗估约 1,000～1,800 行实现、600～1,200 行测试；阶段一完成后按 SDK 缺口修正。
  完成条件优先于代码量预算。

参考实现：

| 本地 Codex 源码 | 参考内容 |
| --- | --- |
| [mcp_cmd.rs](codex/codex-rs/cli/src/mcp_cmd.rs) | CLI 登录、退出、配置和 scopes 选择 |
| [perform_oauth_login.rs](codex/codex-rs/rmcp-client/src/perform_oauth_login.rs) | 浏览器打开、回调监听、取消与超时 |
| [oauth_client_registration.rs](codex/codex-rs/rmcp-client/src/oauth_client_registration.rs) | 客户端注册和 CIMD 选择 |
| [oauth.rs](codex/codex-rs/rmcp-client/src/oauth.rs) | 凭据身份、存储和绝对过期时间 |
| [refresh_transaction.rs](codex/codex-rs/rmcp-client/src/oauth/refresh_transaction.rs) | 刷新令牌轮换、跨进程协调和落盘 |
| [auth_status.rs](codex/codex-rs/rmcp-client/src/auth_status.rs) | 认证能力发现和状态展示 |

## 阶段一：确认 SDK 适配范围与职责契约

- [x] 阅读目标目录实现与测试，确认最新工作区改动和局部指令。
- [x] 用本地可控 OAuth/MCP 服务验证当前 SDK 的完整授权码流程，包括 PKCE、state、
  protected resource metadata、authorization server metadata、scopes 和 resource 参数。
- [x] 验证动态注册、预注册客户端和 CIMD 的公开接入方式。使用 Mind 自身的客户端身份，
  不硬编码 `https://chatgpt.com/oauth/codex/client.json`。
- [x] 验证已有凭据冷启动：恢复绝对过期时间、授权服务器身份和 token endpoint；覆盖授权
  服务器与 MCP 服务不同源的情况，防止将刷新请求错误发向 MCP 地址的固定 `/token`。
- [x] 验证过期令牌、401、403 scopes 不足和 refresh token 轮换时 SDK 的实际行为，确认
  哪些由 SDK 处理、哪些需要 adapter 补足。SDK 内存锁不能代替跨进程协调。
- [x] 若现有 SDK 的公开接口无法满足契约，明确采用固定版本升级或具名 adapter；同步
  依赖与契约测试，不修改 `venv`、不 monkey patch SDK 私有方法、不复制整套 OAuth 栈。
- [x] 确定类型化的认证请求、结果、错误和凭据状态，明确登录成功、用户拒绝、超时、
  取消、需重新登录、存储失败的行为与退出码。
- [x] 确定显式 Bearer/Header 与 OAuth 的优先级；存在显式认证时不得暗中改用其他身份。
- [x] 确定 scopes、客户端身份和存储选项所需的最小配置 schema；未声明字段按不存在处理。

职责落点：

| 职责 | 实现位置与约束 |
| --- | --- |
| CLI 参数、提示和退出码 | `frontends/cli/arguments.py`、`commands.py`、`mcp_parser.py`、`entry.py` 及 MCP 命令适配 |
| 登录用例与跨层契约 | 复用 `agent.application`、`agent.ports`；仅在完整用例需要时增加具名端口 |
| OAuth/SDK、凭据存储适配 | `infrastructure/mcp/`，SDK 对象在此边界内转换 |
| 浏览器、系统凭据库及平台差异 | `infrastructure/platform/` 中职责明确的 adapter |
| 配置与路径 | `infrastructure/config/`，机密不得写入普通配置展示或导出 |
| 活动连接与关闭 | 复用 `agent/harness/mcp/owner.py` 及 infrastructure 的逐服务 owner |
| TUI 状态 | 消费 owner 的类型化快照，不直接读凭据库或 SDK |
| 实现组合 | 根组合边界显式注入，独立 CLI 登录拥有并关闭自己的短期资源 |

完成条件：SDK 成功及失败路径已有验证，所有状态和资源都有唯一 owner，后续阶段无需猜测
凭据格式、客户端身份或公开协议。不为本地 OAuth 向 `protocol/schema/` 增加服务端未声明字段。

### 阶段一结论：SDK 可复用范围

证据入口为 `tests/infrastructure/mcp/test_oauth_sdk_login.py`、
`test_oauth_sdk_refresh.py` 和 `test_oauth_sdk_boundaries.py`，共 24 项测试。
测试通过 HTTPX MockTransport 提供进程内 HTTP 服务，分别路由 MCP 与授权服务器来源，
实际运行 SDK 的 HTTP 认证、MCP initialize 和 tools/list。浏览器只在测试中模拟重定向，
不访问账户、不打开监听端口；真实网络、系统浏览器与凭据库留到阶段六验收。

| 已验证行为 | 后续实现要求 |
| --- | --- |
| 动态注册、预注册客户端和 CIMD 均可完成授权码交换及 MCP 工具发现 | 保留三种明确的客户端注册策略；客户端名称使用 `metadata.const` |
| 服务端校验 S256 challenge/verifier、client_id、redirect_uri 和 resource，SDK 拒绝错误/缺失 state | 复用公开 PKCE 原语；回调 owner 继续承担路由、issuer、超时和资源关闭 |
| 活动 provider 能向已发现的不同源 token endpoint 刷新，并保存轮换结果 | 保留同等能力，并增加持久化事务与凭据版本检查 |
| 新 provider 只恢复令牌与客户端信息；过期令牌先被发送，收到 401 后进入交互授权 | 冷启动必须恢复绝对过期时间、issuer、resource 与 token endpoint；不能直接接入默认 provider |
| 401 不先尝试现有 refresh token，而是重新授权；刷新失败也进入重新授权 | 运行时只报告需登录；浏览器授权必须由显式登录用例发起 |
| 403 insufficient_scope 会启动交互授权；普通 403 也会将同一 POST 重放一次 | 运行时认证层不隐式重放工具请求或自动提升 scopes |
| 构造时设置的 scope 被发现结果覆盖 | 显式 CLI/config scopes 由本地用例裁决，不能依赖 provider 默认逻辑 |
| 发现的 metadata issuer 与声明授权服务器不一致时，SDK 仍继续登录 | adapter 在注册或交换前验证 issuer 绑定及端点策略 |
| 活动 provider 刷新前不重读已被外部更新的存储 | 刷新、登录和退出共用跨进程事务及版本检查，SDK 内存锁不能充当权威 |
| 注册失败、令牌交换失败、保存失败、回调拒绝/超时、调用方取消均能传播 | 业务边界转换成具名安全错误；取消沿现有生命周期传播；成功提示以保存完成为准 |

固定保留 `mcp==1.24.0`，本阶段不升级依赖。后续采用具名 `McpOAuthAdapter`，复用
SDK 公开的 `PKCEParameters`、`OAuthClientMetadata`、`OAuthClientInformationFull`、
`OAuthMetadata`、`ProtectedResourceMetadata`、`OAuthToken` 和现有 HTTPX 传输。
该 adapter 拥有本项目所需的授权码与刷新请求编排，不将 `OAuthClientProvider` 直接接入
生产连接，不修改 SDK 私有方法或上下文，也不复制 SDK 的 MCP 协议/传输实现。
现有 provider 的契约测试继续作为固定依赖的边界证据；升级依赖时重新评估这些差异。

上述缺口使“只接几个回调”的初始估算不再适用。后续实现按约 1,500～2,500 行实现、
1,000～1,500 行测试预留，包含已有阶段一测试；最终仍以行为验证和职责边界为准。

### 阶段一结论：待落地的类型与状态契约

下表确定后续代码的字段和责任；在阶段二至四形成完整用例时落地，不提前加入无人消费的
生产类、端口或配置字段。跨层不传 SDK 对象、原始响应字典或带机密的异常字符串。

| 具名类型 | 字段与约束 |
| --- | --- |
| `McpOAuthTarget` | `config_key: str` 保留原始键；`server_url: str` 使用冻结的规范化完整 URL；保留端口、路径和查询语义，拒绝 userinfo/fragment |
| `McpOAuthLoginRequest` | target、客户端注册策略、`scopes: tuple[str, ...] \| None`、`callback_port: int \| None`、`timeout_sec: float`；None 表示发现 scopes，空元组表示显式不发送 scope |
| `McpOAuthClientRegistration` | 判别联合：dynamic；metadata（HTTPS metadata_url）；registered（client_id 与已注册的固定 loopback 端口）。首版只接入原生公共客户端的 token auth method `none` |
| `McpOAuthCredentialSnapshot` | target、issuer、resource、验证过的 token_endpoint、客户端注册信息、generation 及可空 token；无 token 且无 recovery 为 registered；recovery 为 refresh_uncertain / reauthorization_required 时禁止消费旧令牌 |
| token 记录 | `access_token: str`、`refresh_token: str \| None`、`expires_at: float \| None`（UTC Unix 秒）、`granted_scopes: tuple[str, ...] \| None`；无 scopes 返回值时按请求范围确定有效授权范围；缺少 expires_in 时不虚构有效期 |
| `McpOAuthCredentialView` | target、状态及可空的 expires_at；状态为 not_applicable / missing / registered / stored / expired / unavailable / refresh_uncertain / reauthorization_required；不包含机密，不表示刚刚验证了远端账户 |
| `McpOAuthLoginResult` | 保存完成后的 target 和 expires_at；保存失败不构造成功结果 |
| `McpOAuthLogoutResult` | target、`removed: bool` 和最新 generation；清除令牌后保留必要的无机密版本标记，防止迟到事务复活旧登录 |
| `McpOAuthError` | 具名错误码与安全提示；错误码区分 unsupported_transport、configuration_conflict、invalid_response、registration_failed、authorization_denied、timeout、network_error、reauthorization_required、storage_unavailable；不携带原始响应体或完整回调 URL |

CLI 成功（包括重复 logout）返回 0；上述用例失败经现有 `AppError` 边界返回 1；参数
语法错误沿现有 parser 返回 2；Ctrl+C / 调用方取消继续传播，由现有入口返回 130。
回调 owner 捕获授权拒绝并形成 authorization_denied；总期限超时形成 timeout；二者均
关闭短期资源。运行时的凭据状态与服务连接状态分别投影，不改写既有连接状态机。

凭据身份由配置/状态命名空间、原始配置键、完整服务 URL、授权服务器及客户端身份共同
约束。存储 owner 持有版本与跨进程锁；SDK adapter 只在事务内消费快照；前端只读 view。
浏览器监听和令牌交换由独立登录用例的资源 owner 关闭；活动认证与连接仍由 MCP owner
持有。不存在将账号凭据借用为工具审批 grant 的路径。

### 阶段一结论：认证选择与最小配置

以下配置契约由阶段三实现；运行时使用已批准凭据，配置与 CLI 的 scopes 选择只发生在显式登录时。

| `mcp_servers.<name>.oauth` 字段 | 约束与默认值 |
| --- | --- |
| `scopes` | 可选字符串数组；拒绝空白项及项内空白，去重保序；省略代表发现，空数组代表显式不请求 scopes |
| `client_id` | 可选非空字符串；预注册公共客户端身份；与 client_metadata_url 互斥，使用时要求 callback_port |
| `client_metadata_url` | 可选 HTTPS URL，非根路径，不含 userinfo/fragment；服务器必须声明支持 CIMD，否则明确失败 |
| `callback_port` | 可选整数 1～65535，不接受 bool；省略时由 OS 分配 loopback 端口；不支持自定义远程 callback URL |
| `login_timeout_sec` | 可选有限正数，默认 300；整个登录用例的总期限，不复用工具调用超时 |

客户端选择按预注册 client_id、显式 CIMD、动态注册三种互斥策略执行。动态注册要求已
验证 metadata 中声明 registration_endpoint；缺失时报告不支持，不猜测 `/register`。
首版存储后端固定为系统凭据库，无文件后端配置、无明文回退；非敏感版本及协调资源位于
注入的状态目录，平台后端不可用时报告 storage_unavailable。

scopes 优先级为 CLI 显式值、配置显式值、WWW-Authenticate 挑战、资源 metadata、授权
服务器 metadata，均缺失时省略。显式范围与服务要求冲突时明确失败，不能静默扩大范围，
也不自动用空 scopes 重试。授权结果与请求范围在边界验证，扩大或不足的授权均不得冒充
请求已经满足。

已配置 `bearer_token_env_var` 或大小写不敏感的 Authorization header 时，使用显式
认证；环境变量缺失也不能回退到 OAuth。OAuth login 与这类配置冲突时返回明确错误。
其他自定义 header 只发送到配置的 MCP 来源，不转发给发现或 token 端点。没有显式认证
时，已保存的匹配 OAuth 凭据才可参与认证；匿名连接收到认证要求只报告需登录。
logout 只清除本地 OAuth 凭据，不修改用户的 Bearer/Header 配置，也不声称撤销远端授权。

单个刷新事务必须加锁后重读最新 generation 与绝对有效期，再决定是否刷新；保存前再次
验证身份与版本。refresh token 轮换结果即使遭遇调用方取消也必须完成有界持久化；网络
超时造成未知结果时保留明确的恢复状态，不盲目重复消费令牌或重放工具调用。持久化模型
及事务在阶段二实现，运行时采用和失效处理在阶段四实现。

## 阶段二：实现凭据存储与恢复

- [x] 建立具名凭据模型，覆盖原始配置键、规范化服务 URL、授权服务器身份、客户端信息、
  scopes、令牌、绝对过期时间和更新版本。不同端口、路径、服务或客户端身份不得混用凭据。
- [x] 使用系统凭据库保存机密，明确后端不可用时的失败行为；首版不提供文件后端，
  不在运行中静默切换到另一份凭据。
- [x] 非敏感状态、索引和协调资源遵循 `MIND_HOME` / `MIND_STATE_HOME` 的既有职责，
  路径由上层解析后注入，不硬编码用户目录或复用 Codex 凭据文件。
- [x] 实现读取、保存和删除；用原子更新或等价事务保证凭据一致，并发写入不得损坏其他
  服务记录。令牌与注册信息若分步保存，显式区分“已注册”和“已登录”。
- [x] 从绝对过期时间恢复剩余有效期，不因进程重启延长令牌有效期。
- [x] 建立同一凭据的跨进程协调与版本检查，为刷新、重新登录和退出登录使用统一规则。
- [x] 覆盖无记录、存储不可用、记录损坏、写入失败、身份隔离、过期恢复、并发更新及删除。
- [x] 验证 Token、client secret、授权码不进入日志、异常展示、模型上下文或 `mcp get/list`。

完成条件：独立进程能够恢复同一凭据，损坏或不可用状态可诊断，失败写入不会产生虚假的
登录成功结果；后续阶段共用这一个凭据权威。

### 阶段二存储契约

- `agent/domain/mcp_oauth.py` 落地 target、公共客户端信息、token、snapshot、record、view
  和 logout result；注册策略请求仍在阶段三落地。`record` 在无凭据时保留 generation。
  `McpOAuthStorageError` 固定区分 storage_unavailable、storage_corrupt、storage_busy 和
  credential_conflict，不附带底层异常文本；后续用例将其纳入既有退出码 1 的错误边界。
- `SystemMcpCredentialStore` 实现 `agent.ports.mcp_credentials`，接收上层解析的 config_root
  和 state_root。原始键与完整 URL 隔离逐目标记录，当前记录明确绑定 issuer、resource
  和 client_id；消费令牌前使用 `require_binding` 验证身份。重新登录替换当前身份也必须
  使用当前 generation；锁覆盖整个目标，退出不会遗漏旧客户端身份的待清理记录。
- 固定 `keyring==25.7.0`，只显式选择 Windows Credential Manager、macOS Keychain 或
  Linux Secret Service。无文件后端和 keyring 插件发现；系统后端缺失、锁定、拒绝访问或
  保存失败均报告不可用。状态索引不含 URL、客户端信息、Token 或授权码。
- 系统记录按独立新标识写入，载荷编码为每片最多 1,000 个 ASCII 字符，最多 256 片；
  读取校验完整摘要、格式、目标和版本。这避免 Windows 单条记录容量限制及后端覆盖时
  的副本行为，也不依赖各平台覆盖操作具有相同原子性。超过容量明确失败。
- `MIND_STATE_HOME/mcp/oauth/` 保存版本/清理索引及逐目标 SQLite 锁。先登记待清理项、
  写入系统机密，再原子切换活动指针；切换前失败恢复旧记录，切换后清理失败恢复新记录。
  未完成清理时不报告保存或退出成功；下次访问重试清理。墓碑阻止迟到结果复活旧登录。
- 绝对有效期跨进程保持不变，缺少 expires_at 时仍为未知；registered 不等于 stored。
  快照和 Token 的 repr 隐藏机密，展示只接收 view；客户端 secret、授权码等未声明字段
  在读取边界被拒绝。CLI 展示与运行时认证接入分别由阶段三、四消费此契约。

验证入口为 `tests/agent/domain/test_mcp_oauth.py`、
`tests/infrastructure/mcp/test_oauth_credentials.py`、
`test_oauth_credential_processes.py`、`tests/infrastructure/platform/test_credential_vault.py`。
进程测试注入测试专用共享后端，不触碰用户凭据；
`python -m tests.manual.mcp_credential_storage` 使用临时命名空间和合成大令牌，验证真实系统
凭据库的独立进程写入、恢复、绝对过期时间和删除，并清理本次记录。当前 Windows 已通过
该项实测；macOS/Linux、真实 Sentry 浏览器登录与工具调用仍按阶段六验收，不提前勾选。

## 阶段三：实现 CLI 浏览器登录与退出登录

- [x] 增加 `mind mcp login <name>` 和 `mind mcp logout <name>`，接入既有参数解析、
  命令类型、分发和帮助。scopes、超时等选项只按阶段一确认的契约添加。
- [x] 按原始配置键读取服务。不存在的服务、不支持 OAuth 登录的传输及认证配置冲突
  给出明确错误，不创建或覆盖其他服务注册。
- [x] 登录前创建本机 loopback 回调监听，再使用实际绑定端口构造 redirect URI。
  登录、发现、浏览器等待和令牌交换分别有明确的取消与超时边界。
- [x] 输出授权地址，并通过平台 adapter 打开浏览器；打开失败时保留可手动访问的地址。
- [x] 完成发现、客户端注册和 PKCE 授权；校验 state、回调路由及授权服务器身份绑定，
  正确处理用户拒绝、错误回调、重复回调、端口冲突和超时。
- [x] 凭据成功保存后才报告登录成功；已有凭据的重新登录失败，不破坏仍有效的旧记录。
- [x] 在成功、失败、Ctrl+C 和关闭路径释放监听 socket、任务及 HTTP 客户端。
- [x] 退出登录幂等地清除当前目标的本地凭据，并与正在进行的刷新协调，防止迟到写入
  恢复已删除的登录。明确本地退出与服务端撤销授权的区别。
- [x] 在 `mcp list/get` 展示适用的本地凭据状态；显示已保存凭据时，不声称已实时验证
  远端认证有效性，保留原有配置、连接和工具数量的独立语义。
- [x] 使用可控服务覆盖完整 CLI 成功路径及拒绝、取消、超时、无 OAuth 能力、注册失败、
  换取令牌失败和保存失败；确认 CLI 帮助、返回码、输出和资源清理。

完成条件：通过生产 CLI 入口可完成浏览器登录及退出，本地测试服务上的授权结果与持久
凭据一致；此阶段尚不等于真实 Sentry 验收通过。

实现入口为 `agent/application/mcp/oauth.py`、`infrastructure/mcp/oauth_adapter.py`、
`oauth_callback.py`、`infrastructure/config/mcp_oauth.py` 和 CLI MCP 命令适配。
浏览器启动由平台 adapter 注入，状态查询只读取本地存储。授权期间不持有凭据锁；保存
复用阶段二的版本事务，退出登录与迟到登录提交的竞争由集成测试覆盖。
证据入口为 `tests/integration/test_mcp_oauth_cli.py`、`test_mcp_oauth_concurrency.py`、
`tests/infrastructure/mcp/test_oauth_adapter.py` 和 `test_oauth_callback.py`。
HTTPX 可控服务验证远端协议，回调使用真实 loopback TCP；平台浏览器启动以 mock 验证。
运行时认证和自动刷新仍在阶段四接入，真实浏览器、Sentry 与平台验收仍在阶段六执行。

## 阶段四：接入运行时认证与自动刷新

- [x] 在 `infrastructure/mcp/external_group.py`、`transport.py` 的既有连接创建路径注入
  OAuth 认证，复用现有资源栈与逐服务 owner，避免重复连接或第二套工具目录。
- [x] 普通启动从凭据库恢复认证；缺少凭据或需重新授权时返回类型化结果并提示登录。
  非交互入口不得等待浏览器，也不得在后台自动启动交互登录。
- [x] 实现运行中及进程重启后的过期刷新，刷新后的凭据及时持久化；认证服务器发现及
  token endpoint 使用经过验证的身份，不将机密随重定向发送到其他来源。
- [x] 同一凭据执行“加锁、重读、判断有效期、刷新、保存”的完整事务；已有其他进程
  更新时采用最新结果，避免重复消费轮换中的 refresh token。
- [x] 刷新已经可能产生远端效果后，调用方取消不得丢弃已取得的新凭据。锁等待、网络
  等待和关闭均有边界；无法确定刷新结果时明确报告恢复状态，不无限重试。
- [x] 活动连接在请求或重连边界检查凭据版本，识别外部登录替换和退出登录；旧连接
  不得把旧凭据写回。退出后已有远端请求按既有生命周期收束。
- [x] 401、刷新失败和 scopes 不足收敛为重新登录或明确失败；认证重试不得擅自重放
  可能已经产生副作用的工具调用。
- [x] TUI 通过现有快照显示需登录/认证失败等事实；认证成功后仍执行既有 MCP 工具审批。
- [x] 验证根 Turn、子代理、Hook、Subscription 等消费者共用认证后的工具来源；保持
  使用引用、busy、stop/restart、工作区切换和最终关闭的既有约束。
- [x] 覆盖首次连接、重启恢复、刷新轮换、并发刷新、退出与刷新竞争、网络失败、断线
  重连和取消；回归匿名 HTTP、Bearer/Header、stdio、SSE 及工具审批。

完成条件：登录后的生产运行时可发现并调用工具，冷启动与刷新可恢复，认证错误和取消
能收束到确定状态；多进程不会互相覆盖新凭据或复活已退出的登录。

`infrastructure/mcp/oauth_runtime.py` 在请求边界使用统一存储并注入原 HTTP 客户端。
`McpOAuthRefreshAdapter` 只消费登录时已验证的 token endpoint、resource 和客户端身份；
不重新猜测端点，不跟随重定向，不打开浏览器。刷新前提交无令牌的 recovery 标记，成功后
提交轮换结果并清除标记；交换总期限为 5 秒，存储锁沿用阶段二的有界等待。
不可确认的消费持久投影为 `refresh_uncertain`，明确的拒绝投影为
`reauthorization_required`，直到显式登录替换记录。旧响应失效凭据时必须匹配 generation。
连接 owner 在认证失败时撤下目录，`McpServiceSnapshot.authorization_error` 只携带安全代码；
SDK 退出时再次报告认证错误不等于资源清理失败，等待中的工具调用随 owner 终结而结束。

证据入口为 `tests/infrastructure/mcp/test_oauth_runtime.py`、
`test_oauth_refresh_adapter.py`、`test_oauth_credential_processes.py` 及
`tests/integration/test_mcp_oauth_runtime.py`；覆盖 CLI 登录到生产 SDK 工具调用、冷恢复、
并发轮换、消费后进程崩溃、取消提交、退出竞争、旧 401 与无重放失败。
真实服务与系统浏览器验收仍保留在阶段六。

## 阶段五：回归、文档与发布收口

- [ ] 更新 `README.md`、`docs/cli-usage.md` 和受影响的配置/MCP 文档，说明注册、登录、
  退出、凭据状态、失效恢复和首版支持范围；同步文档生成或校验所需的命令登记。
- [ ] 如增加公开端口、状态所有者或依赖边界，同步架构文档及契约测试。长期文档只描述
  最终行为，不写调试流水账；删除实施中被替代的代码和未采用方案。
- [ ] 先激活仓库虚拟环境，运行各阶段新增测试及受影响测试。不得通过修改进程环境
  构造单元测试，使用注入的路径、时钟、依赖和配置派生值。
- [ ] 对 CLI、配置、外接 MCP 生命周期和审批执行一次集成回归；共享契约受影响时扩大
  对应测试范围。新增测试归入真实责任目录，不在 `tests/` 根目录增加普通测试模块。
- [ ] 发布收口时完成架构审计、编译及差异检查。以下现有测试作为基线，执行时补上新增
  OAuth 测试的具体路径：

```shell
python -m pytest tests/frontends/cli tests/infrastructure/mcp tests/infrastructure/config tests/external_mcp tests/frontends/tui/features/test_tui_mcp.py tests/integration/test_mcp_approval_gate.py tests/integration/test_mcp_approval_semantics_matrix.py -q
python -m pytest tests/test_package_architecture.py tests/architecture -q
python -m compileall agent protocol frontends infrastructure observability metadata
git diff --check
```

- [ ] 检查安装/打包后的 `mind` 入口、凭据库依赖和平台 adapter，避免仅源码入口可用。
- [ ] 准备真机验收记录模板和可重复步骤；诊断证据只记录时间、平台、版本、脱敏服务
  身份、状态与结果，保存在现有 reports 目录，不保存 Token 或完整 OAuth 回调 URL。

完成条件：自动化与静态验证完成，用户文档匹配实现，安装产物可进入最终真机验收。

## 阶段六：最终真机验收

必须在真实操作系统终端、浏览器和外接服务上执行。模拟服务测试用于故障路径验证，
真机结果单独记录；没有实际执行的平台或场景保持“待验收”。

### 6.1 环境准备

- [ ] 使用独立验收配置/状态目录，或确认专用注册名 `sentry-oauth-acceptance` 未被占用；
  不覆盖既有 Sentry 注册和账号凭据。记录实际使用的 Mind 版本或提交及 SDK 版本。
- [ ] 准备 Sentry 实际 MCP URL、可用测试账户、组织/项目访问范围和客户端注册方式。
  使用真实授权页面完成账户选择及同意步骤。
- [ ] 从真实工具目录选择一个可验证结果的只读调用；仅当服务确实暴露对应工具时使用
  `find_releases`。测试目标和参数使用该账户可访问的数据。

### 6.2 首次登录与实际工具调用

以下为功能完成后的验收命令；先替换 URL 占位符。安装环境使用 `mind`，源码排障可用
`python mind.py` 复核，但不替代安装入口验收。

```shell
mind mcp add sentry-oauth-acceptance --url "<SENTRY_MCP_URL>"
mind mcp login sentry-oauth-acceptance
mind mcp get sentry-oauth-acceptance
mind mcp list
```

- [ ] 真实浏览器打开正确授权页面，授权结束后本地回调成功，CLI 返回成功。
- [ ] `get/list` 状态符合本地凭据事实，输出不包含 Token、secret 或授权码。
- [ ] 启动 Mind，发现该服务工具，经现有审批路径执行选定的只读调用并获得真实结果；
  确认未使用环境中的旧 Bearer Token 或 Codex 保存的凭据绕过本次 OAuth。
- [ ] 退出 Mind 并启动新进程，再次调用成功，无需重复登录；重启后的到期判断准确。

### 6.3 令牌刷新与多进程

- [ ] 使用真实服务发放的令牌等待实际过期，或使用该服务正式支持的短有效期测试设置；
  确认下一次调用触发刷新并成功，刷新后再启动新进程仍可调用。
- [ ] 记录脱敏的刷新发生及持久化证据。仅有“调用成功”不足以证明刷新路径已执行。
- [ ] 两个真实 Mind 进程共用同一验收凭据，在到期边界进行只读调用，确认没有刷新
  轮换冲突、凭据覆盖或重复浏览器登录。
- [ ] 如果 Sentry 当前授权不发放 refresh token 或无法在验收窗口内验证过期，保留该项
  待验收，并用确实支持刷新流程的真实 MCP/OAuth 服务补验，不能以模拟测试替代。

### 6.4 失败恢复、退出与清理

- [ ] 在真实授权页拒绝一次登录；另一次登录按 Ctrl+C；两次均正确退出并释放监听端口，
  随后重新登录成功。验证浏览器打开失败时手动访问授权地址仍可完成流程。
- [ ] 网络暂时不可用时给出可理解的错误，恢复网络后可重试；使用测试账户在服务端撤销
  授权后，客户端识别失效并提示重新登录，不进入无限刷新或自动弹窗循环。
- [ ] 登录恢复后，执行 `mind mcp logout sentry-oauth-acceptance`，确认本地凭据清除；
  再次退出登录无害。新进程和仍活动的进程在下一认证边界都不得恢复旧凭据。
- [ ] 验证未登录调用提示明确，重新登录后可恢复。清理仅限本次创建的测试注册、凭据
  和验收资源；保留原有用户配置。
- [ ] 在 Windows 完成整条链路；Linux、macOS 分别验证真实终端、浏览器回调、系统
  凭据库、取消和关闭。未执行的平台明确记录为待验收，不宣称全部平台通过。

### 6.5 最终记录与完成条件

| 验收项目 | 初始状态 | 完成时填写的证据 |
| --- | --- | --- |
| Windows + Sentry 首次登录及工具调用 | 待验收 | 系统/版本、命令返回码、脱敏工具结果 |
| 新进程恢复认证 | 待验收 | 重新启动后的实际调用结果 |
| 实际过期、刷新及刷新后重启 | 待验收 | 真实服务、过期时间、刷新与持久化结果 |
| 多进程及退出登录竞争 | 待验收 | 并发场景、凭据版本变化与最终结果 |
| 拒绝、取消、网络故障和撤销恢复 | 待验收 | 对应错误结果、资源释放及恢复结果 |
| 安装/打包入口 | 待验收 | 安装方式、版本和入口验证结果 |
| Linux 平台行为 | 待验收 | 系统、浏览器、凭据库及验证结果 |
| macOS 平台行为 | 待验收 | 系统、浏览器、凭据库及验证结果 |

- [ ] 将实际结果填入验收记录，未通过项目修复后定向复验。测试账户、真实服务或平台
  不可用时明确记录阻塞，不把待验收项目勾选为完成。
- [ ] 最终交付说明列出实现范围、自动化结果、真机结果和剩余平台限制。
- [ ] 全部目标完成后，将最终操作说明归入稳定文档，按仓库维护约定归档或移除此执行
  清单；不把实施过程日志写入架构文档。
