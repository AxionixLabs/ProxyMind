# JavaScript Sidecar 实施与验收计划

状态：待确认，尚未开始实施（2026-09-02）

本文把 `ARCHITECTURE.md` 已确定的 JavaScript Sidecar 边界转换为可执行阶段、测试矩阵和
验收门禁。确认前只允许修订本文，不迁移源码、不改变打包路径、不删除现有实现。

本文只约束客户端本地 JavaScript 执行能力，不修改根目录 `PROTOCOL.md`、服务端
`mind.chat` 语义或 `NETWORK_APPROVAL_ALIGNMENT_PLAN.md`。稳定架构仍以
`ARCHITECTURE.md` 为准；本文完成后不承担长期架构权威。

## 目标

本次实施必须同时完成以下结果：

1. 把 JavaScript REPL 建立为具有独立宿主、私有协议和进程所有权的 Sidecar 能力。
2. 将 Python 协议、编解码、进程、会话和 Provider 拆入
   `infrastructure/sidecars/javascript`。
3. 将 JavaScript 宿主资产职责化拆入 `sidecars/javascript`。
4. 让 application 只依赖执行端口，让 Harness 只依赖会话生命周期端口。
5. 从 Workspace 聚合能力中移除 JavaScript 状态与关闭职责。
6. 保持模型工具、配置、审批、展示和现有 REPL 行为稳定。
7. 用协议、进程、并发、安全、应用链路、打包和跨平台测试证明最终边界。

目录移动本身不算完成。只有旧所有权消失、完整用例接入新边界并通过最终验收，才允许结束
本计划。

## 参考边界

Codex 参考实现只用于借鉴职责分层，不直接复制产品语义：

- `codex-main/codex-rs/code-mode-protocol`：会话端口、严格消息联合、版本和能力协商；
- `codex-main/codex-rs/code-mode-runtime`：Session/Cell 状态、取消和 callback 生命周期；
- `codex-main/codex-rs/code-mode-host`：独立宿主和传输边界；
- `codex-main/codex-rs/code-mode`：进程托管 Provider；
- `codex-main/codex-rs/core/src/tools/code_mode`：薄应用接入层。

ProxyMind 不引入 Codex 的公开 `exec`、`wait` 或 Code Mode 产品命名。模型侧仍只看到
`js_repl` 和 `js_repl_reset`。

## 不变量

### 外部契约

以下契约在本次实施中保持不变：

- 工具名：`js_repl`、`js_repl_reset`；
- 配置项：`features.js_repl`；
- `js_repl` 参数：必需字符串 `code`、可选非负整数 `timeout_ms`，默认 `30000`；
- `timeout_ms=0` 表示立即超时，不增加未声明的最大值；
- Node 最低版本为 `22.22.0`；
- Sandbox 模式为 `read-only`、`workspace-write`、`danger-full-access`；
- `host.tool(name, args)` 继续进入本地工具授权、审批、执行和展示链路；
- `host.emitImage(value)` 继续只接受显式有效图片并返回具名 attachment；
- 普通 Cell 错误保留已经形成的 REPL 绑定；超时、取消、宿主崩溃和 reset 清空上下文；
- 同一 Session 串行执行，不同 Session 可以并行；
- `js_repl_reset` 对尚未创建的 Session 保持无副作用。

任何需要改变上述语义的发现都必须暂停对应阶段，先修订架构决策并取得确认，不能以迁移便利
为理由改变行为。

### 安全与协议边界

- 每个 `(session_id, normalized_cwd, sandbox_mode)` 安全信封独占 Node 进程；
- 同一 `session_id` 的安全信封变化必须先关闭原进程，不能原地扩大权限；
- Sidecar 不拥有审批、Effect、Transcript、线上事件或前端状态；
- Sidecar 私有字段不得进入 `/tool-result` 或其他线上载荷；
- stdout 只承载协议帧，stderr 只用于有界诊断；
- 未知字段、未知消息、无法关联的响应和版本不兼容必须失败收敛；
- 不保留双协议、双资产路径或只转发一次调用的兼容 facade。

## 最终依赖链

```text
agent.application.tools.javascript
  -> agent.ports.javascript.JavaScriptExecutionPort
      <- infrastructure.sidecars.javascript.JavaScriptSidecarProvider
          -> JavaScriptSidecarSession
              -> JavaScriptSidecarProcess
                  -> sidecars/javascript/host.js

agent.harness process/session lifecycle
  -> agent.ports.javascript.JavaScriptSessionLifecyclePort
      <- 同一个 JavaScriptSidecarProvider

host delegate request
  -> application nested-tool delegate
      -> authorization / approval / effect / tool dispatch
      -> typed delegate response
```

Workspace runtime 不在这条依赖链中。

## 最终文件结构

```text
agent/
├── ports/
│   └── javascript.py
└── application/
    └── tools/
        └── javascript.py

infrastructure/
└── sidecars/
    └── javascript/
        ├── __init__.py
        ├── messages.py
        ├── codec.py
        ├── process.py
        ├── session.py
        └── provider.py

sidecars/
└── javascript/
    ├── host.js
    ├── runtime.js
    ├── transform.js
    └── vendor/
        └── meriyah.umd.min.js
```

`__init__.py` 不重导出具体实现。调用方从职责模块导入端口或具体组合类型，不通过包根扩大公开
API。

## 进程内端口

`agent.ports.javascript` 最终只定义具名跨层契约：

- `JavaScriptExecutionRequest`：`session_id`、`code`、规范化前的 `cwd`、
  `sandbox_mode`、`timeout_ms`；
- `JavaScriptExecutionResult`：显式 output 与不可变 attachments；
- `JavaScriptNestedToolRequest/Result`：嵌套调用标识、工具名和已校验 JSON 值；
- `JavaScriptNestedToolDelegate`：执行当前 Cell 所属的嵌套工具调用；
- `JavaScriptExecutionPort`：执行 Cell、重置 Session；
- `JavaScriptSessionLifecyclePort`：关闭指定 Session、关闭 Provider；
- `JavaScriptFailureKind`：`unavailable`、`protocol_error`、`execution_timeout`、
  `cancelled`、`runtime_error`。

重置和关闭返回具名 disposition，不使用含义不清的布尔值。端口不暴露 subprocess、Task、
锁、JSONL 字典或具体 Provider 类型。

## 私有协议 V1

Sidecar 使用一行一个 UTF-8 JSON 对象的严格协议。字段使用判别联合，不使用字段存在性猜测
消息类型。

### Client 到 Host

| Type | 作用 | 关键身份 |
| --- | --- | --- |
| `connection/hello` | 提交支持版本及必需/可选能力 | connection |
| `operation/request` | 包装 `session/execute`、`session/reset`、`session/shutdown` | `request_id` |
| `operation/cancel` | 取消一个活动执行 | `request_id`, `cell_id` |
| `delegate/response` | 返回嵌套工具或图片处理结果 | `delegate_id`, `cell_id` |

### Host 到 Client

| Type | 作用 | 关键身份 |
| --- | --- | --- |
| `connection/ready` | 返回选定版本和能力 | connection |
| `connection/rejected` | 明确拒绝握手 | reason |
| `operation/response` | 返回启动、完成、重置或关闭结果 | `request_id`, `cell_id` |
| `delegate/request` | 请求嵌套工具调用或图片附加 | `delegate_id`, `cell_id` |
| `delegate/cancel` | 取消尚未完成的 delegate | `delegate_id`, `cell_id` |
| `cell/closed` | 宣布 Cell 已完成且不再接受 callback | `cell_id` |

### V1 规则

- 首帧必须是 `connection/hello`，Host 在 ready 前不得处理 operation；
- V1 必需能力为 execute、cancel、reset、nested tool 和 image content；
- `request_id` 在单连接内唯一，`cell_id` 在单宿主进程生命周期内唯一；
- delegate 必须同时匹配活动 `cell_id` 和唯一 `delegate_id`；
- 同一 Session 只有一个活动 Cell，后续 execute 在 Python Session FIFO 等待；
- reset、shutdown 和 close 是队列屏障，后续 execute 不得越过；
- 单帧最大值保持 `32 MiB`，stderr 尾部保持最多 20 行且总计 `4 KiB`；
- pending delegate 设置固定上限，初始值为 64，超过上限按 protocol error 收敛；
- handshake、graceful cancel 和 graceful shutdown 都有独立有界超时；
- V1 不增加对模型公开的 `wait` 工具；内部异步等待由一次 execute 生命周期拥有。

消息验证必须在改变会话状态前完成。Python 和 JavaScript 两端共享同一组协议样例作为契约
测试输入，避免分别手写后发生漂移。

## 分阶段实施

阶段状态只能使用“待开始、进行中、已验证、阻塞”。只有本阶段全部退出条件满足并记录实际
命令结果后，才能标记为“已验证”。

### 阶段 0：基线冻结

状态：待开始

目标：在移动任何生产代码前，把现有行为和当前缺口固定为可重复基线。

实施内容：

1. 运行当前 `tests/test_js_repl.py`，记录 Node、Python、操作系统和测试结果。
2. `tests/test_js_repl.py` 保留模型工具到真实 Node 的端到端基线；只补充当前行为缺口，不在
   生产 owner 尚未存在时提前拆出对应模块测试。
3. 定义后续测试文件的职责映射，由各生产 owner 所在阶段同步创建，不建立 skip 占位测试。
4. 将需要 Node 的测试统一标记；缺少 Node 或版本不足在开发机给出明确原因，在正式验收环境
   必须作为失败而非 skip。
5. 记录现有超时、取消、异常 Cell、崩溃和 reset 后状态保留/清空矩阵。

定向验证：

```powershell
.\venv\Scripts\python.exe -m pytest tests\test_js_repl.py
.\venv\Scripts\python.exe -m pytest tests\test_feature_config.py tests\test_client_tool_call.py
```

退出条件：

- 当前成功路径和关键失败路径全部有测试归属；
- 测试不依赖生产私有属性判断最终行为，进程故障 fixture 除外且必须封装在测试代码；
- 尚未创建未接入的生产模块；
- 基线失败必须先归因，不能带入下一阶段。

### 阶段 1：协议与 Host 垂直切片

状态：待开始

目标：形成可以由测试进程独立启动、握手、执行、回调和关闭的 Sidecar Host。

实施内容：

1. 建立 `messages.py` 的 V1 判别联合、具名标识、能力集和稳定失败类型。
2. 建立 `codec.py` 的增量读取、完整帧提取、UTF-8 校验和帧预算。
3. 将 JavaScript 职责拆为：
   - `host.js`：stdio、握手、消息路由、request/delegate 表和关闭；
   - `runtime.js`：持久上下文、Cell 执行、console、host API 和结果内容；
   - `transform.js`：Meriyah 解析、顶层 await/import 和绑定转换；
   - `vendor/`：固定第三方解析器资产。
4. Host 在握手失败、协议错误和 stdout 写入失败时停止接收新操作并确定性退出。
5. 普通 runtime error 保持现有绑定语义；超时、取消和 fatal async error 关闭 Host，由上层按需
   创建新进程。
6. 用相同 golden frames 同时测试 Python parser 和真实 Host。
7. 当前生产 JavaScript manager 在本阶段同步切换到 V1 codec 和新 Host，不保留旧消息解析；
   manager 暂时只保留尚未在阶段 2 拆出的进程与会话职责。
8. 同步把 `setup.py`、`build.py` 和源码资产解析切换到 `sidecars/javascript`，移除其他资产位置。

必须新增的测试：

- 正常、最低/最高支持版本和 capability 协商；
- 必需/可选 capability 重叠、缺失必需能力和版本无交集；
- 未知字段、未知 type/method、缺失身份、错误字段类型和重复 request；
- 多帧同 chunk、单帧跨 chunk、CRLF、非法 UTF-8、EOF 残帧和超限帧；
- execute、普通异常、reset、shutdown、nested tool、image 和 cell closed；
- Host stdout 不混入 console 或诊断文本。

定向验证：

```powershell
node --check sidecars\javascript\host.js
node --check sidecars\javascript\runtime.js
node --check sidecars\javascript\transform.js
.\venv\Scripts\python.exe -m pytest tests\test_javascript_sidecar_messages.py tests\test_javascript_sidecar_codec.py
.\venv\Scripts\python.exe -m pytest tests\test_javascript_sidecar_host.py
.\venv\Scripts\python.exe -m pytest tests\test_js_repl.py tests\test_build_packaging.py
```

退出条件：

- Host 黑盒测试不导入旧 Python runtime；
- Python 与 Host 对所有 golden frames 得到一致判定；
- 协议错误没有静默忽略、兼容回退或未收束 pending；
- 真实 `js_repl` 主链路已经使用 V1 Host 和最终资产目录；
- JavaScript 资产只有一个位置，生产代码只有一个 codec；
- 阶段 1–3 全部完成前不得发布包含新 Sidecar 的客户端产物。

### 阶段 2：进程、Session 与 Provider

状态：待开始

目标：完成 Python Sidecar adapter，并建立清晰的进程和会话所有权。

实施内容：

1. `process.py` 负责：
   - Node 路径与 `22.22.0` 最低版本校验；
   - Windows、Linux、macOS 参数和信号差异；
   - 基于不可变安全信封生成 `--permission` 参数；
   - stdin/stdout/stderr 所有权、握手超时、graceful close 和强制终止；
   - 结构化观测，不创建标准库 logger。
2. `session.py` 负责：
   - 单 Session FIFO 和一个活动 Cell；
   - request/cell/delegate 表；
   - nested delegate Task 的创建、取消和收束；
   - reset/close 屏障；
   - timeout、caller cancellation、EOF 和 Host exit 的稳定失败映射。
3. `provider.py` 负责：
   - 以 `session_id` 索引活动会话；
   - 规范化 cwd 并冻结安全信封；
   - 相同信封复用、信封变化先关闭再创建；
   - `reset_session`、`close_session` 和 `close` 的幂等生命周期；
   - 不同 Session 并行且永不共享进程。
4. Node executable 和 Sidecar 资产路径从 composition 传入，adapter 不自行读取应用配置。
5. 本阶段用 `JavaScriptSidecarProvider` 替换生产 manager/pool；在阶段 3 完成组合切换前，
   Workspace 只作为既有调用方持有注入的 Provider，不再实现任何 Sidecar 进程细节。
6. 删除被替代的 platform runtime；不保留类名 alias 或转发 facade。

必须新增的测试：

- Node 配置路径、PATH 探测、版本解析、版本不足、启动失败和握手超时；
- 三种 sandbox 模式的参数，路径含空格/Unicode，Windows 与 POSIX 终止路径；
- stderr 20 行/4 KiB 上限和敏感正文不进入观测；
- 同 Session FIFO、不同 Session 并行、queued execute 取消；
- reset/close 与 execute 竞争、重复关闭、close all 中单个失败不阻断；
- callback 完成、异常、取消、late response、Host exit 时 callback 收束；
- cwd 或 sandbox 变化更换进程且不扩大原进程权限；
- 一个 Session 崩溃不影响其他 Session。

定向验证：

```powershell
.\venv\Scripts\python.exe -m pytest tests\test_javascript_sidecar_process.py
.\venv\Scripts\python.exe -m pytest tests\test_javascript_sidecar_session.py tests\test_javascript_sidecar_provider.py
```

退出条件：

- subprocess、流任务、锁和 pending 表只有一个明确 owner；
- 任意失败路径最终不存在活跃 reader、callback 或僵尸 Node 进程；
- 不使用 `global`、`nonlocal`、`typing.cast()`、mixin 或动态属性猜测；
- 并发测试使用事件/barrier，不以不稳定 sleep 证明顺序。
- 真实 `js_repl` 主链路已经使用 Provider，platform runtime 不再存在。

### 阶段 3：主链路与生命周期切换

状态：待开始

目标：把完整产品链路切到新 Provider，并一次删除原所有权。

实施内容：

1. 重写 `agent.ports.javascript` 为执行端口、生命周期端口和具名请求/结果。
2. `agent.application.tools.javascript` 继续拥有参数校验、nested authorization、approval、
   Effect 接入和工具结果投影，只调用执行端口。
3. `composition.py` 创建唯一 `JavaScriptSidecarProvider`，分别把最小端口注入 application 与
   Harness 资源 owner。
4. 根 Session、Subagent、reset 和进程退出直接使用生命周期端口，不再通过 Workspace
   callback 转发。
5. `WorkspaceCoding` 只保留 Shell、进程、补丁和文件能力，不持有 JavaScript Provider。
6. 删除聚合端口继承，同一变更中清理 imports、exports、Workspace callback 和测试私有依赖。
7. 不保留旧类名 alias、兼容构造参数、路径 fallback 或双写。

必须覆盖的产品测试：

- 工具开关同时控制 `js_repl` 与 `js_repl_reset`；
- 参数拒绝、成功执行、reset 和稳定 result envelope；
- nested shell/exec/write_stdin 的本地审批、拒绝、cancel interrupt 和 session amendment；
- nested MCP/function 结果和混合图片内容；
- 根 Session、Subagent 和 ApplicationHost 关闭只关闭其拥有的会话；
- TUI/文本 presentation 仍保留两阶段展示、缩进和结果预览；
- `/tool-result` payload 不包含任何 Sidecar 私有状态。

定向验证：

```powershell
.\venv\Scripts\python.exe -m pytest tests\test_js_repl.py
.\venv\Scripts\python.exe -m pytest tests\test_client_tool_call.py tests\test_feature_config.py
.\venv\Scripts\python.exe -m pytest tests\test_tool_policy.py tests\test_tool_presentation.py tests\test_text_output.py
.\venv\Scripts\python.exe -m pytest tests\test_package_architecture.py
```

退出条件：

- `js_repl` 从模型工具到真实 Host 的完整用例只经过新端口；
- Workspace 不再实现或继承 JavaScript 端口；
- 组合根是具体 Provider 和资产路径的唯一创建位置；
- 原实现、原资产位置、原 imports 和测试私有依赖全部消失；
- 新旧路径不共存，不存在兼容 facade。

### 阶段 4：可靠性、安全与可观测性加固

状态：待开始

目标：验证在取消、崩溃、畸形输入和关闭竞争下仍能确定收敛。

实施内容：

1. 固化 `unavailable`、`protocol_error`、`execution_timeout`、`cancelled`、`runtime_error`
   映射，application 不解析异常文本。
2. caller cancellation 先取消 operation；宽限期后终止 Host，并取消全部 delegate。
3. Host fatal error、EOF、无效 frame 和协议身份冲突使当前会话失效，下次 execute 延迟创建
   新进程。
4. 记录结构化 spawn/ready/execute/cancel/reset/exit/close 事件；正文、工具完整结果和 data URL
   不进入日志。
5. 对代码、frame、stderr、attachment 和 pending delegate 执行预算门禁。
6. 添加故障注入 Host fixture，覆盖指定时点退出、卡住、写残帧、重复响应和 late callback。

故障测试矩阵：

| 故障点 | 预期结果 |
| --- | --- |
| ready 前退出 | `unavailable`，无缓存 Session |
| 握手版本不兼容 | `protocol_error`，进程关闭 |
| execute 中 EOF | 当前 Cell 失败，全部 delegate 收束 |
| 超时后 Host 不确认 cancel | 宽限期后强杀，上下文清空 |
| caller 取消 | 向上传播取消，Host/Cell 不继续产出结果 |
| delegate 执行中 Host 退出 | delegate 被取消，late result 被拒绝 |
| 超限或畸形 frame | `protocol_error`，不处理部分 payload |
| reset 与 execute 竞争 | reset 屏障前任务收束，屏障后使用新 Host |
| close 中一个 Session 失败 | 继续关闭其他 Session，失败结构化上报 |

定向验证：

```powershell
.\venv\Scripts\python.exe -m pytest tests\test_javascript_sidecar_session.py -k "cancel or timeout or exit or protocol or close"
.\venv\Scripts\python.exe -m pytest tests\test_js_repl.py -k "timeout or cancellation or exit or async_error"
.\venv\Scripts\python.exe -m pytest tests\test_package_architecture.py -k "javascript or logging or lifecycle"
```

退出条件：

- 故障矩阵逐项存在自动化测试；
- 测试结束后检查不到遗留 Node 子进程或 pending asyncio Task；
- 观测事件完整但不包含敏感正文；
- 所有异常分支具有稳定类型，stderr 只作为有界 detail。

### 阶段 5：打包与跨平台验收

状态：待开始

目标：保证源码、Python 分发和独立应用产物都能定位并启动真实 Host。

实施内容：

1. 复核 `ApplicationLayout` 对 `sidecars/javascript` 资产根的解析，不依赖当前工作目录。
2. 复核 `setup.py` 已按最终目录包含全部 Host、runtime、transform 和 vendor 资产。
3. 复核 `build.py` 已将完整 `sidecars` 目录放入 Nuitka 产物，不逐文件维护脆弱列表。
4. 构建校验在缺文件、文件散列/版本不一致或 Host 无法握手时失败。
5. Windows 和 macOS 独立应用执行真实 smoke；Linux 执行源码/安装分发 smoke。
6. 路径测试覆盖空格、非 ASCII、只读安装目录和不同启动 cwd。

产物 smoke 必须执行：

1. 从产物解析 Sidecar 资产；
2. 启动 Node 并完成 V1 握手；
3. 执行 `const value = 41`；
4. 第二个 Cell 输出 `value + 1`；
5. reset 后确认绑定不可见；
6. graceful shutdown 后确认无子进程。

验证命令：

```powershell
.\venv\Scripts\python.exe -m pytest tests\test_build_packaging.py tests\test_javascript_sidecar_packaging.py
.\venv\Scripts\python.exe setup.py sdist bdist_wheel
.\venv\Scripts\python.exe build.py
```

平台门禁：

| 平台 | 必须验证 |
| --- | --- |
| Windows | PATH/显式 Node、permission 参数、空格路径、进程树关闭、Nuitka 产物 smoke |
| macOS | PATH/显式 Node、permission 参数、SIGTERM/SIGKILL、`.app` 产物 smoke |
| Linux | PATH/显式 Node、permission 参数、SIGTERM/SIGKILL、源码和安装分发 smoke |

退出条件：

- 三个平台均有实际 CI/机器证据，不能以 mock 代替最终 smoke；
- wheel/sdist 清单和独立应用产物都只包含最终资产目录；
- 从非项目 cwd 启动仍能执行、reset 和关闭；
- 缺失或损坏资产在启动前给出稳定 unavailable 结果。

### 阶段 6：全量回归与最终收口

状态：待开始

目标：证明 Sidecar 改造没有破坏 Agent Harness、前端、协议和发布边界。

最终验证：

```powershell
.\venv\Scripts\python.exe -m pytest
.\venv\Scripts\python.exe -m compileall agent protocol frontends infrastructure observability metadata
node --check sidecars\javascript\host.js
node --check sidecars\javascript\runtime.js
node --check sidecars\javascript\transform.js
git diff --check
```

还必须完成：

1. 运行阶段 5 的三平台真实产物 smoke。
2. 搜索生产源码，确认没有旧资产字符串、旧 runtime import、Workspace JavaScript 所有权、
   标准库 logger 或 Sidecar 私有字段进入线上 payload。
3. 复核 `ARCHITECTURE.md` 与实际目录、依赖和生命周期一致。
4. 复核工具描述、用户配置和展示没有无意变化。
5. 检查 `git status`，区分并保留用户无关改动。
6. 在本文阶段记录中填入实际 commit、测试命令、通过数量、平台和产物路径。

最终退出条件：

- 阶段 0–5 全部为“已验证”，不存在跳过项或待解释失败；
- 全量 pytest、compileall、Node syntax、diff check 全部通过；
- 三平台产物 smoke 全部通过；
- 架构守卫能够阻止依赖和所有权回退；
- 代码库只有一套 Sidecar 协议、一个 Provider 所有者和一个资产位置；
- 用户确认验收结果后才结束本计划。

## 测试矩阵总览

| 层次 | 核心测试 | 是否需要真实 Node | 最终必跑 |
| --- | --- | --- | --- |
| Message | 判别联合、字段、身份、版本、capability | 否 | 是 |
| Codec | chunk、UTF-8、CRLF、EOF、frame budget | 否 | 是 |
| Host | 握手、execute、binding、callback、reset、shutdown | 是 | 是 |
| Process | Node/version、argv、spawn、stderr、terminate | 部分 | 是 |
| Session | FIFO、cancel、timeout、delegate、barrier、EOF | fake + real | 是 |
| Provider | lazy create、安全信封、隔离、close | fake + real | 是 |
| Application | 参数、审批、Effect、result projection | 部分 | 是 |
| Harness | 根/Subagent/进程资源关闭 | 部分 | 是 |
| Presentation | CLI/TUI 文本和两阶段展示 | 否 | 是 |
| Packaging | source、wheel/sdist、Nuitka 资产和 smoke | 是 | 是 |
| Architecture | 导入方向、唯一所有权、无直接 logging | 否 | 是 |
| Regression | 全仓 pytest 与语法检查 | 依测试而定 | 是 |

## 架构守卫要求

`tests/test_package_architecture.py` 最终至少锁定：

- `agent.application.tools.javascript` 只导入 agent contract，不导入 infrastructure；
- Workspace 端口和实现不包含 JavaScript 执行或生命周期成员；
- `infrastructure.sidecars.javascript` 是唯一 Python Sidecar 进程实现；
- JS 资产只存在于 `sidecars/javascript`；
- 除 composition/打包代码外，生产模块不直接解析 Sidecar 资产路径；
- frontends、protocol、server 和 backend 不导入具体 Sidecar 实现；
- Host 资产不包含线上协议字段或服务端状态语义；
- Sidecar Python 模块不直接调用 `logging.getLogger`；
- 不存在具体 Provider 的 package-root 重导出或兼容 alias。

架构守卫在阶段 3 完成后定向执行，在阶段 6 才执行全仓最终门禁。不要在每个协议或 Host 小改动
后重复运行完整守卫。

## 提交与回退策略

- 每个阶段只在退出条件满足后提交，提交信息标明阶段和已验证范围。
- 阶段内不提交红测试、未接入生产模块或临时 facade。
- 阶段 1–3 未全部完成前不发布包含新 Sidecar 的客户端产物。
- 回退以完整阶段 commit 为单位，不通过恢复旧路径、双协议或运行时 fallback 回退。
- 若阶段 3 主链路切换失败，回退整个阶段 3；阶段 1–2 的未发布实现不能被配置开关偷偷启用。
- 用户工作树中的无关改动不因回退或验收被覆盖。

## 阶段证据记录

实施后按下表逐项更新，不以口头结论替代命令证据：

| 阶段 | 状态 | Commit | 验证命令与结果 | 平台/产物 | 未决风险 |
| --- | --- | --- | --- | --- | --- |
| 0 基线 | 待开始 | - | - | - | - |
| 1 协议与 Host | 待开始 | - | - | - | - |
| 2 Process/Session/Provider | 待开始 | - | - | - | - |
| 3 主链路切换 | 待开始 | - | - | - | - |
| 4 可靠性与安全 | 待开始 | - | - | - | - |
| 5 打包与跨平台 | 待开始 | - | - | - | - |
| 6 最终收口 | 待开始 | - | - | - | - |

## 确认门禁

在用户明确确认本计划前，不执行阶段 0，不新增 Sidecar 生产模块，不移动 JavaScript 资产，
不修改 application、Workspace、composition、setup 或 build 链路。
