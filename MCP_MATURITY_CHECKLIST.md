# 外接 MCP 工程成熟度改造清单

本清单接续 `MCP_OAUTH_CHECKLIST.md`，依据本地 `codex/` 源码核对差距。
客户端边界以 `ARCHITECTURE.md` 为准，跨系统审批与线上字段以
`ARCHITECTURE_SYSTEM.md` 和正式协议为准。仅从源码运行，不编译打包。

OAuth 登录、系统凭据库、刷新事务、跨进程退出失效、工具审批和连接 owner 已存在；
各阶段复用这些能力，不另建凭据库、工具目录所有者或审批事实。

## 阶段一：HTTP 重定向与工具目录完整性

职责归属：`infrastructure/mcp` 的传输与 SDK 目录边界。目录仍由单连接 owner
完成初始化后原子发布，失败时关闭本次连接，不保留半份工具目录。

- [x] HTTP/SSE 在发送重定向请求前校验同源，拒绝跨主机、跨端口和协议降级；
  错误不包含 Location、认证头、令牌或工具正文。
- [x] 保留正常同源重定向的 HTTP 方法与正文语义，限制跳数，关闭中间响应。
  OAuth 连接继续禁止自动重定向；不弱化登录和 token endpoint 的现有策略。
- [x] 完整读取 `tools/list` 的 `nextCursor`，统一应用 allow/deny 和审批元数据。
- [x] 限制页数、条目数和游标字节数；拒绝循环游标及原始/规范化后的重复工具名；分页共用启动期限。
- [x] 覆盖第二跳跨源、自定义认证头、POST 正文、同源成功、分页成功、恶意游标、
  超限、后续页失败与取消；失败时不发布部分目录。
- [x] 更新外接 MCP 操作说明，完成受影响传输、生命周期和 OAuth 回归。

参考：`codex/codex-rs/rmcp-client/src/http_client_redirect.rs`、
`codex/codex-rs/codex-mcp/src/pagination.rs`。

阶段一复核通过：定向回归覆盖 HTTP/SSE/stdio 本地 fixture、OAuth 和连接生命周期；
分页后续页失败、超时及取消均确认不发布部分目录并关闭资源。139 项架构审计、
文档生成前后检查及差异检查通过。新增边界故障由自动化注入验证，未将其记作 Sentry 真机结果。

## 阶段二：协议输入上限与有限建连重试

职责归属：传输 adapter 负责有界字节读取；既有逐连接 owner 负责尝试、清理和总期限。

- [x] 在 JSON 解码前限制 stdio 单帧字节数，覆盖分块输入和长期无换行输出。
  超限关闭所属连接；stderr 继续复用既有脱敏、有界诊断。
- [x] 保留 Windows/Linux/macOS 的进程启动、取消、信号和关闭语义；复用公开生命周期，
  替换旧无界读取路径，不维护两套可切换实现。
- [x] HTTP 初始化对明确的临时网络错误、408/429/500/502/503/504 有限重试，
  各次尝试共用总期限，并在重新建连前释放前一次资源。
- [x] 认证失败、协议错误及已开始执行的工具调用不纳入握手重试；取消不继续尝试。
- [x] 覆盖超长帧、跨块 UTF-8、正常大消息、握手恢复、重试耗尽、总超时、
  取消及资源关闭；用真实本地子进程/HTTP fixture 验证 adapter 集成。

参考：`codex/codex-rs/rmcp-client/src/bounded_stdio_transport.rs`、
`codex/codex-rs/rmcp-client/src/streamable_http_retry.rs`。

阶段二 Windows 真机通过：源码 adapter 在真实 TTY 完成 11 项本地子进程/HTTP 故障验收；
Selector 事件循环另完成 4 项 stdio 验收，覆盖 SDK 进程回退及卡住进程的取消。
源码 TUI 完成 `/mcp status`、`/mcp restart`、`/mcp stop` 和 Ctrl+D 退出，超限服务不发布工具。
操作系统核对本轮 44 个 fixture 进程身份均已退出；验收入口为
`python -m tests.manual.mcp_transport_acceptance`，报告位于
`build/mcp-maturity-phase2-20260917/ACCEPTANCE.md`。这批结果属于 Windows 本地故障服务，
不代替 Sentry、Linux 或 macOS 的真实验收；跨平台进程代码复用 SDK 公开实现，实机证据留在阶段七。
366 项 MCP 定向回归、139 项架构审计、文档生成校验及差异检查通过。

## 阶段三：认证状态与恢复提示贯通

- [x] 由凭据与连接边界生成最小类型化状态，区分未知、显式 Header/Bearer、OAuth、
  未登录、需要重新授权；明确本地凭据存在与远端认证成功的区别。
- [x] CLI、`/mcp`、`/tools` 消费同一事实来源；TUI 不直接访问凭据库或发起探测。
- [x] 在失败服务旁给出针对该注册的登录/重启提示；普通启动不自动打开浏览器。
- [x] 覆盖登录替换、logout、刷新失败、匿名服务、显式认证及零工具服务。
- [x] 真机核对 Sentry 首次登录、认证失效和恢复后的展示一致性。

参考：`codex/codex-rs/rmcp-client/src/auth_status.rs`。

阶段三 Windows 源码 TTY 通过：独立命名空间完成首次登录、真实 `find_releases`、
跨进程退出后的请求阻断与目录撤下、重新登录和重启恢复；`/mcp status`、`/tools`
都显示相同认证事实及 5 个允许工具。CLI 只查本地，始终不宣称远端认证刚刚成功。
本轮失效证据来自本地 logout，不代表服务端撤销；结束时已停止连接并删除验收凭据。
787 项定向回归、31 项最终状态边界复核、139 项架构审计和文档检查通过；
报告位于 `build/mcp-maturity-phase3-20260917/ACCEPTANCE.md`，可复用入口为
`python -m tests.manual.mcp_authorization_acceptance`。

## 阶段四：远程终端的手动 OAuth 回调

- [x] 为显式 CLI login 增加独立输入模式，在终端隐藏粘贴的完整回调 URL。
- [x] 粘贴输入与 loopback 回调共用 state、issuer、回调地址校验和一次性消费规则；
  不导航到粘贴地址，不把地址或授权码写进诊断。
- [x] 对输入长度、等待期限及取消设置边界，任何终态恢复终端模式并释放监听资源。
- [x] 覆盖非法来源、重复参数、错误 state/issuer、超长输入、两路同时完成、
  token 交换时取消和退出登录竞争；从真实终端验收恢复路径。

参考：`codex/codex-rs/cli/src/mcp_login.rs`、
`codex/codex-rs/rmcp-client/src/oauth_callback_input.rs`。

阶段四 Windows 源码真机通过：`mcp login --manual` 隐藏输入完整回调，输入上限为
64 KiB，未闭合粘贴和控制序列也有界；取消、EOF、超时及失败均恢复原终端模式。
15 项真实 PTY、本机 HTTP OAuth 和系统凭据库验收全部通过，包含交换时取消/超时、
跨进程 logout 阻止迟到提交；确认 15 个验收进程均已退出，并对最终代码复验两种回调路径。
独立目录完成 Sentry 手动模式登录、真实 `find_releases` 和凭据清理；本次 Sentry 由
浏览器 loopback 送达，隐藏粘贴路径的真机证据来自本机 OAuth 服务。
392 项 MCP 回归、54 项最终边界复验及 139 项架构审计通过，文档生成与差异检查通过。
验收入口为 `python -m tests.manual.mcp_callback_acceptance`，报告位于
`build/mcp-maturity-phase4-20260917/ACCEPTANCE.md`。跨机器 SSH、Linux 和 macOS
实机仍留待阶段七，不以 Windows 本机结果代替。

## 阶段五：可选服务启动与目录复用

- [ ] 在既有 required 语义上增加可选服务等待预算，允许慢服务在后续使用边界加入。
- [ ] 用配置、工作区、服务及认证身份约束目录缓存；容量、TTL、失效和清理有界。
  缓存只作为派生数据，不能授予调用权限或表示连接已经就绪。
- [ ] 若复用目录，调用绑定明确版本；不得改变已冻结 Turn 的工具和审批语义。
- [ ] 在现有 owner 下合并预热请求，覆盖配置变化、账号切换、工作区退休、取消、
  required 失败及旧结果迟到，不额外创建后台生命周期所有者。
- [ ] 用多个真实本地慢/失败服务记录启动耗时和目录可用性，证明改善及关闭正确性。

参考：`codex/codex-rs/codex-mcp/src/tool_catalog_cache.rs`、
`codex/codex-rs/codex-mcp/src/client_tool_catalog.rs`、
`codex/codex-rs/core/src/session/mcp_prewarm.rs`。

## 阶段六：调用中的表单与 URL 交互

- [ ] 定义完整的本地 elicitation 用例：SDK 边界校验请求后转换为具名类型，
  通过现有交互队列呈现表单/URL 并返回 accept、decline 或 cancel。
- [ ] 只有实际支持的交互类型才向服务声明能力；非交互入口返回明确结果。
- [ ] 绑定调用身份与请求身份，处理并行请求、用户等待期限、服务端取消、
  连接关闭和调用方取消，不重放已产生效果的工具调用。
- [ ] 明确本地交互与 AppServer 审批的职责；涉及线上消息时先遵守正式契约，
  不通过自造字段绕过远端审批。
- [ ] 覆盖 schema 校验、敏感字段展示、URL 处理、所有终态及资源清理，
  使用确实提供对应能力的服务执行终端验收。

参考：`codex/codex-rs/codex-mcp/src/elicitation.rs`、
`codex/codex-rs/rmcp-client/src/elicitation_client_service.rs`。

## 阶段七：回归、真机与交付

- [ ] 每阶段先跑受影响的定向测试，复核错误脱敏、类型契约、取消和 owner 关闭。
- [ ] 包边界变更或最终收口执行架构审计；同步受影响稳定文档与文档生成校验。
- [ ] 源码真实终端验收 HTTP、SSE、stdio 与 Sentry OAuth；危险和超限场景使用
  隔离本地服务，报告与真实第三方服务结果分开记录。
- [ ] 继续完成 `MCP_OAUTH_CHECKLIST.md` 中未完成的真实故障与跨平台验收。
  没有实际执行的平台和场景保留待验收。
- [ ] 复核后按阶段提交和推送；交付列出通过证据、未完成事项和实际运行入口。

原生 MCP Resources/资源模板、工具按需搜索和新协议模式另立完整用例后再实施，
不混入本轮传输、认证与生命周期加固。
