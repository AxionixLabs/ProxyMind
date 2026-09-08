# Sandbox v1 工程成熟度改造清单

> 状态：阶段 0-3 已完成并通过复核；阶段 4 待实施。
> 计划版本：v1。
> Sandbox 私有协议版本：`1`，本次改造保持不变。
> 基准日期：2026-09-08。

本文是阶段性实施与验收清单，不定义新的架构事实。客户端职责、依赖方向、状态所有权和
生命周期以 `ARCHITECTURE.md` 为准；跨系统边界以 `ARCHITECTURE_SYSTEM.md` 为准；测试归属和
执行方式以 `tests/README.md` 为准。

## 1. 目标与硬约束

目标是把本地 Sandbox 从“基本可运行”提升到“错误可区分、生命周期可收敛、平台行为可证明、
发布产物可追溯”的生产就绪状态，同时保持现有 JSONL 私有协议 v1。

- [x] `ready.protocol_version` 继续固定为 `1`，不增加 v2 帧或双版本兼容分支。
- [x] Sidecar 错误仍使用 v1 的 `{code, detail}` 信封，客户端必须分别保存两个字段，不再拼接后反推。
- [x] 只有 helper 缺失、Sidecar 启动失败、握手失败或异常退出可以归类为 `sandbox_unavailable`。
- [x] Sidecar 已就绪但子进程无法创建时归类为 `sandbox_process_start_failed`。
- [x] 调用参数或工作区边界不合法时归类为 `sandbox_request_invalid`。
- [x] 子进程成功创建后的非零退出默认归类为 `command_failed`，Shell 解析错误不得归类为 Sandbox 故障。
- [x] 有可靠证据证明文件系统或网络策略拒绝时才归类为 `sandbox_denied`。
- [x] 未知客户端异常归类为 `tool_internal_error` 并进入结构化观测，不伪装成 Sandbox 不可用。
- [x] 任一受限执行失败均保持 fail-closed，不自动切换到宿主 Shell 或 `danger-full-access`。
- [x] 不在前端依据 WinError 文案、日志文本或展示文本重建执行事实。

## 2. 已确认基线

- [x] Windows Sidecar 可完成 v1 握手和 `ping`。
- [x] Windows restricted-token 可在 `workspace-read` 下读取当前工作区文件。
- [x] `exec_command` 执行非法 PowerShell heredoc 时返回 `command_failed`、`exit_code=1` 和 `ParserError`。
- [x] `shell_command` 的同类受限执行在命令退出后触发旧输出缓冲 API 的 `TypeError`。
- [x] `SandboxProtocolError`、`OSError`、`RuntimeError` 和 `ValueError` 当前会被过度收敛为
      `sandbox_unavailable`。
- [x] `SandboxClient` 未显式接收应用布局时会把源码应用根错误解析到仓库上一级。
- [x] 阶段 0 后 Sandbox 客户端单元测试通过：`21 passed`。
- [x] 当前 Windows Sidecar PTY 定向验收通过：`1 passed`。
- [x] 阶段 0 前没有测试覆盖 Windows `WorkspaceCoding.shell_command(..., sandbox_mode=...)` 真实链路。

## 3. 阶段 0：回归事实冻结

- [x] 为 `shell_command` 增加 Windows 非 PTY Sidecar 真实链路回归测试。
- [x] 固定成功读取、命令非零退出、PowerShell 解析失败、命令超时和输出截断五类基线。
- [x] 固定 `sandbox_spawn_failed`、`cwd_outside_workspace_roots`、`sandbox_mode_disabled` 和
      `argv_empty` 的 v1 映射结果。
- [x] 用 Fake Sidecar 固定 helper 缺失、启动拒绝、握手超时、协议版本不匹配、请求超时和异常 EOF。
- [x] 测试必须断言稳定 `reason`、backend code、执行阶段和 retryable 语义，不只匹配异常文案。
- [x] 将关键回归纳入 `runtime_p0`；真实 Sidecar 场景按平台验收规则执行。

### 阶段 0 准出

- [x] 当前已确认缺陷均先有失败测试，且失败位置与责任模块一致。
- [x] 测试不通过修改进程环境注入依赖，Sidecar 路径由应用布局或 fixture 显式提供。
- [x] 不依赖 `D:\PycharmProjects\ai-workflow` 等当前工作区之外的固定本机路径。

### 阶段 0 复核证据

- [x] `tests/infrastructure/platform/test_sandbox_client.py`：`21 passed`。
- [x] `tests/infrastructure/platform/test_windows_sidecar_integration.py`：
      `2 passed, 9 xfailed`。
- [x] Runtime P0：`333 passed, 3996 deselected, 9 xfailed`。
- [x] 九个预期失败均使用 `strict=True`：四个冻结 v1 结构化错误目标，五个冻结非 PTY
      `shell_command` 目标；阶段 1 修复对应契约时必须同步移除，不作为长期 quarantine。
- [x] 真实 v1 Sidecar 已证明读取、普通非零退出、PowerShell ParserError、大输出和四类 backend code。
- [x] Fake Sidecar 已证明 helper 缺失、启动拒绝、握手超时、版本不匹配、请求超时和 ready 前 EOF。
- [x] 额外发现：握手超时会留下待消费的 `_ready` 异常，纳入阶段 2 生命周期收口。
- [x] v1 `timeout_ms` 不作为 Sidecar 独立终止契约；命令超时继续由 `ProcessSessionManager` 所有。

## 4. 阶段 1：P0 正确性修复

### 4.1 输出所有权

- [x] `ProcessSessionManager` 提供具名的字节输出快照，统一返回 stdout、stderr、顺序事件和 dropped bytes。
- [x] `ShellCommandExecutor` 不再直接读取 `ProcessSession.lock`、`stdout`、`stderr` 或 `output_events`。
- [x] 删除 `bytes(session.stdout)` 和不存在的 `session.stdout_dropped` / `session.stderr_dropped` 旧路径。
- [x] `shell_command` 与 `exec_command` 复用同一进程终结和输出收束语义，不复制缓冲实现。
- [x] 多字节字符、stdout/stderr 交错、首尾截断和 EOF flush 继续遵守统一解码生命周期。

### 4.2 v1 错误保真

- [x] `SandboxProtocolError` 以具名属性保存 v1 `code` 和 `detail`，不把二者拼接成唯一字符串。
- [x] Sidecar v1 响应在边界完成对象、字段、类型和空值校验；畸形响应按协议故障收敛。
- [x] 建立唯一的 Sandbox-to-tool 错误映射函数，由 `shell_command`、`exec_command` 和控制操作复用。
- [x] 删除两个命令执行器对宽泛 `OSError`、`RuntimeError`、`ValueError` 的同义捕获。
- [x] 边界层将真实 OS 启动异常转换为具名 Sandbox 失败；业务编程错误保持可观测并独立归类。
- [x] `sandbox_spawn_failed` 映射为 `sandbox_process_start_failed`，并在 `failure_context` 保留 backend code。
- [x] `cwd_outside_workspace_roots`、`sandbox_mode_disabled` 和 `argv_empty` 映射为
      `sandbox_request_invalid`。
- [x] v1 `detail` 中存在 OS error code 时只作为结构化诊断事实保存，不用字符串决定是否自动提权。

### 4.3 应用布局

- [x] Sandbox 可执行文件路径由组合根根据 `ApplicationLayout` 显式注入。
- [x] 删除 `SandboxClient` 的源码目录层级猜测，不保留第二条隐式发现路径。
- [x] 源码运行、独立打包和测试 fixture 使用同一可执行文件定位契约。
- [x] 缺失产物在启动前返回包含期望路径和平台的稳定失败，不进入模糊的握手超时。

### 阶段 1 准出

- [x] 非 PTY `shell_command` 在 Windows Sidecar 下完成成功、失败、超时和截断回归。
- [x] 非法 `python - <<'PY'` 明确返回 `command_failed`，不会触发宿主 Shell 请求。
- [x] 只有真实 Sidecar 可用性问题返回 `sandbox_unavailable`。
- [x] 定向测试、Runtime P0、`compileall` 和 `git diff --check` 全部通过。

### 阶段 1 复核证据

- [x] 阶段 1 实现提交：`d5731b0c`。
- [x] Sandbox 客户端与 Windows Sidecar 定向组：`45 passed`。
- [x] 进程输出解码、PTY 与 TUI Shell 相邻回归：`82 passed`。
- [x] 干净提交快照 Runtime P0：`342 passed, 4025 deselected`，阶段 0 的 9 个 strict xfail
      已全部移除。
- [x] 架构审计首轮发现新模块缺少规范 main guard；修复后完整审计
      `138 passed, 1 warning`。
- [x] macOS Sidecar 真机验收在 Windows 上保留 `11 skipped`，不计为通过。
- [x] 全量 `compileall` 和阶段差异检查通过。

## 5. 阶段 2：生命周期与协议健壮性

- [x] Sidecar stderr 由有界后台任务持续消费，启动失败时保留有限诊断尾部。
- [x] 握手超时或读取任务取消时必须消费并收束 `_ready` future，不能向事件循环泄漏后台异常。
- [x] Sidecar 子进程退出后立即清理 `_processes`，不得随命令次数增长。
- [x] `_early_events` 设置进程数、事件数和总字节上限；未知 process id 不得形成无界缓存。
- [x] JSONL 单帧设置字节上限；非法 UTF-8、非法 JSON、未知事件和缺失字段进入明确协议故障。
- [x] 请求超时后移除 pending future；迟到响应不得错误完成新的请求。
- [x] Sidecar 重启使用本地 generation 隔离旧进程身份，防止复用 `sandbox_1` 等标识时串线。
- [x] Sidecar 异常退出时，在途命令标记为 `execution_outcome_unknown`；可能产生副作用的命令不得自动重试。
- [x] `close()` 在请求关闭、读取任务取消和进程终止任一步失败时仍继续其余清理。
- [x] 连续执行至少 1,000 个短命令，验证进程表、pending 表、事件缓存、句柄和内存不持续增长。

### 阶段 2 准出

- [x] Fake Sidecar 故障矩阵覆盖乱序、重复、迟到、畸形、超大帧、异常 EOF 和关闭竞态。
- [x] Soak 结果记录执行数、峰值句柄、峰值内存、残留进程和未清理请求。
- [x] 所有失败路径均证明不会隐式落到非 Sandbox 执行。

### 阶段 2 复核证据

- [x] 阶段 2 实现提交：`cc340cf4`。
- [x] Sandbox 客户端与 Fake Sidecar 故障矩阵：`58 passed`；覆盖严格帧校验、三重早到缓存预算、
      generation 隔离、effectful timeout、异常 EOF、重复/迟到事件和多故障关闭。
- [x] 真实 Windows Sidecar v1 回归：`11 passed`；相邻输出、PTY 与 TUI Shell 组：
      `151 passed, 11 skipped`，其中 macOS 真机验收在 Windows 上如实 skip。
- [x] 干净提交快照 Runtime P0：`345 passed, 4058 deselected`；完整架构审计：`138 passed`。
- [x] 非 PTY 全量两次均完成 `4255 passed, 12 skipped, 135 deselected`，每次各有 1 个不同的
      无关时序用例失败；JavaScript Sidecar 用例定向复测 `1 passed in 0.35s`，TUI scrollback
      用例定向复测 `1 passed in 0.73s`，按复测门禁不扩展本阶段范围。
- [x] 真实 Windows Sidecar 冷启动后连续执行 1,000 个短命令用时 `47.642s`；客户端活动进程、
      pending、早到进程、早到事件和早到字节最终均为 `0`，关闭后残留 Sidecar 为 `false`。
- [x] 1,100 次趋势复核在前 100 次热身后按 200 次采样；Sidecar 句柄为
      `174, 174, 174, 174, 174, 176`，工作集保持约 `13.2-13.9 MB`，未随命令数持续增长。
- [x] 1,000 次 soak 中 Python 句柄始终为 `159`，`tracemalloc` 峰值 `144208 bytes`、最终
      `103339 bytes`；客户端已退出进程 tombstone 固定不超过 `256`。
- [x] 全量 `compileall`、约束扫描和 `git diff --check` 通过；当前成熟度达到 L3.5，达到 L4 前
      仍保持 Production Readiness Blocked。

## 6. 阶段 3：拒绝识别与审批边界

- [x] 优先依据 v1 backend code 区分请求拒绝和进程启动失败。
- [x] 对“子进程已启动后发生的策略拒绝”建立平台 adapter 内的有限分类器。
- [x] 分类器同时考虑 backend、退出码和受控错误模式，不能仅搜索 `sandbox` 或 `access denied` 单词。
- [x] PowerShell ParserError、命令不存在和普通应用错误必须作为反例加入测试。
- [x] 推断型拒绝在本地结果中标记 `evidence_source=inferred_output`，不得冒充 Sidecar 协议事实。
- [x] 只有 `sandbox_denied` 可以进入现有审批重试流程；`sandbox_unavailable`、请求无效和命令语法错误
      不得建议提权。
- [x] 经用户批准的重试冻结原命令、工作目录和权限差异，不在 UI 中临时改写命令。

### 阶段 3 准出

- [x] WinError 5 的启动拒绝和文件读取拒绝能依据发生阶段得到不同稳定结果。
- [x] 所有自动审批和人工审批测试证明一次裁决只影响当前动作，不形成全局安全回退。
- [x] 误判反例矩阵通过，普通命令失败不会触发 Sandbox 审批。

### 阶段 3 复核证据

- [x] 阶段 3 实现提交：`04e46050`。
- [x] v1 结构化失败继续优先映射 request/spawn；仅对子进程已启动后的非零退出执行本地有限分类，
      结果使用 `reason=sandbox_denied`、`stage=execution` 和 `evidence_source=inferred_output`。
- [x] Windows PowerShell 与 macOS Shell 分类器同时校验 backend、runtime、退出码、原命令词和受控 stderr
      行；ParserError、命令不存在、普通应用文案、错误 backend、成功退出、超时和结果未知均为反例。
- [x] `shell_command` 使用一次性捕获的完整 stderr；`exec_command` / `write_stdin` 使用 Session Manager
      持有的非消费有界输出，即使先读取过增量输出也不会丢失终态拒绝证据。
- [x] 真实 Windows Sidecar 证明受保护文件读取返回 `sandbox_denied` / `stage=execution`，而 Sidecar
      启动 WinError 5 保持 `sandbox_unavailable` / `stage=startup`；Windows 真链路组 `12 passed`。
- [x] 审批账本在 `cid/sid/turn/call_id` 之外绑定执行动作指纹；命令、cwd、Shell、tty、权限模式或
      additional permissions 变化时 fail-closed 为 `approval_action_mismatch`，不会消费旧批准。
- [x] 人工审批篡改矩阵、自动 review 正向动作绑定、Hook 受信任改写和会话授权隔离均通过；审批、
      策略与执行定向组 `70 passed`，协议与审批快照兼容组 `167 passed`。
- [x] Sandbox 分类、客户端投影和 Windows 真链路定向组 `84 passed`；平台扩大组
      `151 passed, 12 skipped`，其中 macOS 真机验收在 Windows 上如实 skip。
- [x] Runtime P0：`364 passed, 4060 deselected`；快速全量：
      `4277 passed, 12 skipped, 135 deselected`；收集总数 `4424`。
- [x] 完整架构审计：`138 passed, 1 warning`；warning 为 Nuitka 内置 `glob2` 的既有
      `DeprecationWarning`。全量 `compileall` 和 `git diff --check` 通过。
- [x] 阶段 4 尚未完成，因此成熟度仍为 L3.5，Production Readiness 继续保持 Blocked。

## 7. 阶段 4：Doctor 与可观测性

- [ ] `mind doctor --json` 增加 Sandbox 产物路径、平台、v1 握手、`ping` 和受限读取检查。
- [ ] Doctor 使用临时工作区验证读写策略，不修改用户仓库或进程环境。
- [ ] Doctor 区分 helper 缺失、不可执行、握手失败、协议不兼容、spawn 失败和策略未生效。
- [ ] 每项失败给出稳定 code、measured、expected 和 remediation，不从日志文案反推状态。
- [ ] 增加 `sandbox.sidecar.started/ready/failed/exited`、`sandbox.request.failed` 和
      `sandbox.violation` 结构化事件。
- [ ] 事件携带 backend、stage、protocol version、backend code、retryable 和关联身份。
- [ ] 日志不记录完整命令、完整环境、凭据、未筛选 stderr 或不必要的绝对路径。
- [ ] `mind doctor` 的全绿结果必须真实包含 Sandbox 检查，不能只证明其他 14 项通过。

### 阶段 4 准出

- [ ] 正常安装、helper 缺失、损坏 helper、WinError 5、握手超时和策略失效均有 Doctor 快照测试。
- [ ] 线上问题可以仅凭结构化报告区分 Sidecar 不可用、进程启动失败、策略拒绝和命令失败。

## 8. 阶段 5：平台与发布证明

- [ ] Windows 验证 read-only、workspace-read、workspace-write、受保护元数据目录和越界路径。
- [ ] Windows 验证空格、中文、长路径、PowerShell、cmd、PTY、stdin、interrupt、EOF 和 terminate。
- [ ] macOS 在真实 Seatbelt Sidecar 上执行同构验收；缺少产物只能 skip，不能计为通过。
- [ ] Linux 未提供 Sandbox 产物时明确报告 unsupported；交付前必须补真实后端和同构验收。
- [ ] 为每个平台的 Sandbox 二进制维护 v1 manifest：文件名、SHA-256、目标架构、来源提交和构建标识。
- [ ] 构建阶段校验 manifest、文件格式、架构和执行权限，不只校验文件存在。
- [ ] 发布机保存平台验收、Runtime P0、快速全量、soak、`compileall` 和差异检查结果。
- [ ] 平台验收失败时禁止把其他平台的结果或 mock 测试作为替代证据。

## 9. 成熟度门禁

| 等级 | 定义 | 准入条件 |
|------|------|----------|
| L2.6 基线 | 基本 Sidecar 与 PTY 可运行，但非 PTY、错误语义和诊断未收口 | 阶段 0 前基线 |
| L3 当前 | 两个命令入口共享生命周期，错误分类稳定，已知 P0 缺陷清零 | 阶段 0-1 全部通过 |
| L3.5 健壮 | 协议严格、资源有界、异常退出结果明确、soak 无泄漏 | 阶段 2 全部通过 |
| L4 可运营 | 拒绝与审批正确，Doctor 和结构化观测可以独立定位故障 | 阶段 3-4 全部通过 |
| L4.5 可发布 | Windows/macOS 平台证明完整，产物可追溯，发布门禁可重复 | 阶段 5 全部通过 |
| L5 长期成熟 | 多版本发布、长期运行和真实故障数据持续证明架构不变量 | 至少两个稳定发布周期持续通过 |

在达到 L4 前，`ARCHITECTURE_SCORECARD.md` 应把本地 Sandbox 标记为“Production Readiness
Blocked”，但不因此改写已经成立的系统 Authority 和依赖方向结论。

## 10. 每阶段复核与提交纪律

- [x] 每个阶段先运行定向测试，风险跨越 Session Manager、公共工具结果或平台边界时扩大验证。
- [x] 每个阶段单独复核、单独提交，不与 `/review`、TUI 布局或其他用户改动混合。
- [x] 提交前确认暂存区只包含本阶段文件，保留用户现有改动。
- [x] 每个阶段记录实现提交哈希、验证命令和结果；平台缺失或 skip 必须如实保留。
- [x] 阶段完成后更新本清单；全部完成后把长期不变量收敛到正式架构和测试文档。

## 11. Codex-main 本地参考

- [UnifiedExecError 分层](/D:/PycharmProjects/ProxyMind/codex-main/codex-rs/core/src/unified_exec/errors.rs)
- [Sandbox 拒绝识别](/D:/PycharmProjects/ProxyMind/codex-main/codex-rs/core/src/unified_exec/process.rs)
- [Sandbox violation 结构化观测](/D:/PycharmProjects/ProxyMind/codex-main/codex-rs/sandboxing/src/violation.rs)
- [Windows Sandbox Doctor](/D:/PycharmProjects/ProxyMind/codex-main/codex-rs/cli/src/doctor/sandbox.rs)
- [Windows Sandbox 统一执行](/D:/PycharmProjects/ProxyMind/codex-main/codex-rs/windows-sandbox-rs/src/unified_exec/mod.rs)
- [Windows restricted-token 实现](/D:/PycharmProjects/ProxyMind/codex-main/codex-rs/windows-sandbox-rs/src/unified_exec/backends/legacy.rs)

参考原则：复用 Codex 的 `CreateProcess / ProcessFailed / SandboxDenied` 分层、Doctor 检查和违规
观测思想；ProxyMind 继续使用自己的 v1 JSONL 边界，不引入 Codex 内部 Rust 类型或第二套执行
Authority，也不为了识别拒绝而放宽严格协议。
