# JavaScript Sidecar 实施与验收计划

状态：阶段 0–3 已验证（2026-09-02）

本文把 `ARCHITECTURE.md` 中的 JavaScript Sidecar 边界转换为分阶段实施和验收门禁。稳定
架构以 `ARCHITECTURE.md` 为准；本文只记录实施状态和证据，完成后不成为第二份架构权威。

本计划只涉及客户端本地 JavaScript 执行能力，不修改根目录 `PROTOCOL.md`、服务端协议或
`NETWORK_APPROVAL_ALIGNMENT_PLAN.md`。

## 架构结论

Codex 对齐目标是职责和生命周期，而不是改写 Codex 已经稳定的 JavaScript 资产：

```text
application tool
    -> execution port
        <- provider
            -> session
                -> process
                    -> immutable js_repl/kernel.js

Harness/session lifecycle
    -> lifecycle port
        <- the same provider
```

ProxyMind 对齐 Codex 的薄应用服务、Provider、Session、独立 Host 进程和 delegate 回调边界。
以下差异是既有资产和本地安全边界决定的正式设计，不建立兼容层消除差异：

| Codex Code Mode | ProxyMind JavaScript Sidecar |
| --- | --- |
| 自有版本化 Host protocol | 保留不可变 Kernel 的现有 JSONL protocol |
| Host 可以复用并承载多个 Session | 每个安全信封独占 Node 进程 |
| execute/wait/terminate Cell 生命周期 | execute 完成返回；取消和重置终止进程 |
| 运行时能力握手 | 客户端与 bundle 原子发布并校验固定散列 |

不增加 wrapper Host。仅为握手或消息改名增加第二层 Node 进程会产生双重生命周期、双重故障面
和无业务价值的协议翻译。

## 不可变资产门禁

原 `js_repl` 目录是一个完整 Codex 运行时 bundle，最终只允许整体机械移动为
`sidecars/js_repl`。以下文件从实施开始到最终产物必须保持字节不变：

| 相对路径 | SHA-256 | Git blob |
| --- | --- | --- |
| `kernel.js` | `70dc77d3172ce04fc34b56b0885936a0b7e522bad94396be92ba319d9df96ae8` | `5bce51a0788fdc9a7cbdbab9edfae64a3b36775b` |
| `vendor/meriyah.umd.min.js` | `446def5e8718bb25a18d9bb0bdc1a8b40f4ddde8c071a08c970638b6bdfc4562` | `f9e5e0407dcb26af625173005ef2219bcdd04284` |

明确禁止：

- 拆分、合并、重写、格式化或重命名 bundle 内文件；
- 修改注释、空白、编码、行尾或压缩结果；
- 注入 handshake、cancel、reset、shutdown 或观测代码；
- 创建 `host.js`、`runtime.js`、`transform.js` 或其他包装入口；
- 在新旧目录保留两份资产或运行时路径 fallback。

资产内容升级必须作为独立的上游同步变更处理，更新来源、散列和完整行为基线，不得夹在架构
重组中完成。

## 稳定行为

本次架构改造保持以下外部语义：

- 工具名为 `js_repl` 和 `js_repl_reset`，配置项为 `features.js_repl`；
- `js_repl` 接受必需字符串 `code` 和可选非负整数 `timeout_ms`，默认 `30000`；
- `timeout_ms=0` 表示立即超时，Node 最低版本为 `22.22.0`；
- Sandbox 模式为 `read-only`、`workspace-write`、`danger-full-access`；
- `host.tool(name, args)` 继续进入本地工具授权、审批、执行和展示链路；
- `host.emitImage(value)` 继续只接受显式有效图片；
- 普通 Cell 错误保留已提交绑定；超时、取消、Kernel 崩溃和 reset 清空上下文；
- 同一 Session 串行执行，不同 Session 可以并行；
- reset 未创建的 Session 无副作用；Session 的 cwd 或 Sandbox 变化时关闭旧进程再创建；
- `/tool-result` 不出现任何 Sidecar 私有状态。

## 最终目录与职责

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
        ├── bundle.py
        ├── protocol.py
        ├── process.py
        ├── session.py
        └── provider.py

sidecars/
└── js_repl/
    ├── kernel.js
    └── vendor/
        └── meriyah.umd.min.js
```

- `agent.ports.javascript`：具名执行请求、结果、失败和最小执行/生命周期端口；
- `application.tools.javascript`：模型参数、nested authorization、approval 和结果投影；
- `bundle.py`：固定文件清单、散列、相对路径和完整性验证；
- `protocol.py`：现有 JSONL 帧的构造、解析、类型和大小校验；
- `process.py`：Node 发现与版本、权限参数、环境、stdio、stderr 尾部和终止；
- `session.py`：单 Session FIFO、当前 request、delegate task、超时、reset 和 close；
- `provider.py`：安全信封、Session 索引、延迟创建、替换和总关闭；
- `sidecars/js_repl`：不导入 Python，不承载审批、线上协议或产品状态。

包根 `__init__.py` 不重导出具体实现。生产调用方必须从职责模块导入。

## 现有私有协议

Kernel 协议是一行一个 UTF-8 JSON 对象。Python 不改变 wire 结构。

### Python 到 Kernel

| `type` | 必需字段 | 作用 |
| --- | --- | --- |
| `exec` | `id`, `code`, `timeout_ms` | 执行一个 Cell |
| `run_tool_result` | `id`, `ok`, `response`, `error` | 返回 nested tool 结果 |
| `emit_image_result` | `id`, `ok`, `error` | 返回图片接收结果 |

### Kernel 到 Python

| `type` | 必需字段 | 作用 |
| --- | --- | --- |
| `exec_result` | `id`, `ok`, `output`, `error` | 返回 Cell 结果 |
| `run_tool` | `id`, `exec_id`, `tool_name`, `arguments` | 请求 nested tool |
| `emit_image` | `id`, `exec_id`, `image_url`, `detail` | 请求附加图片 |

Python 只发送由具名构造器产生的消息。输入帧必须在改变 Session 状态前完成 JSON 对象、判别
类型、精确字段、字段类型、身份关联和 `32 MiB` 帧预算校验。未知、畸形、残缺、超限或无法
关联的执行结果是 protocol failure，当前进程必须关闭；合法的迟到 delegate 返回确定的
`js_repl exec context not found`，不恢复旧 execution。

Kernel 没有控制消息，因此 timeout、caller cancellation、reset 和 close 都通过终止进程实现。
stderr 只保留最多 20 行、每行 512 bytes、合计 4096 bytes 的诊断尾部，不拥有协议语义。

## 端口与所有权

`agent.ports.javascript` 使用不可变 dataclass 和枚举定义：

- `JavaScriptExecutionRequest`：`session_id`、`code`、`cwd`、`sandbox_mode`、`timeout_ms`；
- `JavaScriptExecutionResult`：`output` 和不可变 `attachments`；
- `JavaScriptExecutionPort`：执行和重置；
- `JavaScriptSessionLifecyclePort`：关闭指定 Session 和关闭全部 Session；
- `JavaScriptResetDisposition`：`not_started` 或 `reset`；
- `JavaScriptFailure`：稳定 kind 与有界 detail。

Provider 由唯一组合根创建。application 只接收执行端口，根 Session/Subagent/ApplicationHost
只接收生命周期端口。Workspace 不实现、不继承、不保存 JavaScript 端口，也不转发清理回调。

Provider 以 `session_id` 索引 Session。Session 的不可变安全信封由规范化 cwd 和
`sandbox_mode` 构成；信封变化先从索引移除并关闭旧 Session，再创建新 Session。每个 Session
拥有一个 Node 进程，不跨安全信封共享。

## 分阶段实施

阶段状态只使用“待开始、进行中、已验证、阻塞”。每一阶段通过全部出口条件后才能提交并
进入下一阶段。

### 阶段 0：文档与行为基线

状态：已验证

实施：

1. 修正 `ARCHITECTURE.md` 和本文，删除拆分 JS、V1 握手和 wrapper Host 设计。
2. 记录两个资产的 SHA-256 和 Git blob。
3. 运行真实 Node REPL、application 工具和配置基线。
4. 记录超时、取消、异常、崩溃、reset、Session 隔离和 nested callback 的现状。

验证：

```powershell
.\venv\Scripts\python.exe -m pytest tests\test_js_repl.py tests\test_feature_config.py tests\test_client_tool_call.py
node --check js_repl\kernel.js
git diff --check
```

出口证据：Windows、Python 3.11.8、Node 24.12.0；`tests/test_js_repl.py`、
`tests/test_feature_config.py`、`tests/test_client_tool_call.py` 共 72 项通过；`node --check` 和
`git diff --check` 通过；两个资产散列与不可变清单一致。文档没有拆分资产、新增握手或改写
Kernel 的实施步骤，用户无关改动保持原样。

### 阶段 1：不可变 Bundle 与 Python Sidecar 垂直切片

状态：已验证

实施：

1. 建立 bundle、protocol、process、session 和 provider 职责模块。
2. 将 `js_repl` 整个目录机械移动到 `sidecars/js_repl`，移动前后校验散列。
3. Provider 通过真实 Kernel 完成执行、跨 Cell 状态、nested tool、图片、reset 和关闭。
4. 用 Provider 替换生产 `JavaScriptReplPool`，删除
   `infrastructure/platform/javascript_repl.py`，不保留 alias 或 facade。
5. 同步切换 `setup.py`、`build.py` 和资产路径，不保留旧路径 fallback。

必须测试：

- bundle 缺失、散列不匹配、路径含空格和从非项目 cwd 启动；
- 已有六类消息的合法帧，以及未知类型/字段、类型错误、非法 JSON、残帧和超限帧；
- Node 显式路径、PATH、版本不足、启动失败和三种 Sandbox 参数；
- 同 Session FIFO、不同 Session 并行、安全信封变化、reset、重复 close；
- timeout、caller cancellation、EOF、Kernel exit、callback 完成和 callback 取消；
- 两个资产移动前后和测试结束后的散列完全相同。

验证：

```powershell
.\venv\Scripts\python.exe -m pytest tests\test_javascript_sidecar_bundle.py tests\test_javascript_sidecar_protocol.py
.\venv\Scripts\python.exe -m pytest tests\test_javascript_sidecar_process.py tests\test_javascript_sidecar_session.py tests\test_javascript_sidecar_provider.py
.\venv\Scripts\python.exe -m pytest tests\test_js_repl.py
node --check sidecars\js_repl\kernel.js
```

出口证据：新增 bundle/protocol/process/session/provider 并接入生产主链路；91 项 Sidecar、
真实 Kernel、配置和客户端工具测试通过；163 项扩大回归通过；compileall、Node syntax、asset
hash 和 diff check 通过。旧 Python 单体、旧 import 和旧资产目录已删除，两个 Git blob 与迁移
前完全相同。

### 阶段 2：组合根和 Harness 生命周期

状态：已验证

实施：

1. 重写 `agent.ports.javascript` 为具名执行端口和生命周期端口。
2. 唯一组合根创建 Provider，并分别注入 application 与 Session 资源 owner。
3. 从 `WorkspaceCodingPort`、`WorkspaceCoding` 和 Workspace close 移除 JavaScript。
4. 根 Session、Subagent 和应用关闭直接调用生命周期端口。
5. 更新 registry builder 和测试 fixture，不增加具体 Provider 的包根重导出。

验证：

```powershell
.\venv\Scripts\python.exe -m pytest tests\test_js_repl.py tests\test_feature_config.py tests\test_client_tool_call.py
.\venv\Scripts\python.exe -m pytest tests\test_agent_composition.py tests\test_workspace_coding_lifecycle.py tests\test_subagent_tools.py
.\venv\Scripts\python.exe -m pytest tests\test_package_architecture.py -k "javascript or workspace"
.\venv\Scripts\python.exe -m pytest tests\test_package_architecture.py -q
.\venv\Scripts\python.exe -m compileall -q agent infrastructure composition.py mind.py
node --check sidecars\js_repl\kernel.js
```

出口：Workspace 不再包含 JavaScript 成员或清理；组合根是具体 Provider 和 bundle 路径的唯一
创建者；模型工具、审批、Subagent 和展示行为不变。定向回归 190 passed，完整架构守卫
116 passed；compileall、Node syntax、bundle hash、diff check 和打包资产配置检查通过。

### 阶段 3：可靠性、Nuitka 打包与最终验收

状态：已验证

实施：

1. 固化 unavailable、protocol、timeout、cancelled、runtime 五类失败，不解析异常文本做决策。
2. 通过故障注入证明 EOF、畸形帧、超限帧、进程退出和关闭竞争确定收敛。
3. 固化 Nuitka `--include-data-dir=sidecars=sidecars`，确保独立产物根目录保留原始 bundle。
4. 增加架构守卫，禁止 Workspace 所有权、旧目录、复制资产、直接 infrastructure 导入和标准
   logger 回流。
5. 执行全量回归和语法检查，更新本文证据。

最终验证：

```powershell
.\venv\Scripts\python.exe -m pytest
.\venv\Scripts\python.exe -m compileall agent protocol frontends infrastructure observability metadata
node --check sidecars\js_repl\kernel.js
git diff --check
```

平台产物验收覆盖 Windows、macOS 和 Linux 的 Node 查找、权限参数、信号/进程树关闭、空格及
非 ASCII 路径、只读安装目录和非安装 cwd。当前机器不能替代其他平台的真实产物证据；缺少
的平台必须明确记录为待平台验收，不能宣称三平台全部完成。

出口：Sidecar 全量回归、项目全量（排除 `codex-main` 测试树及一份与当前用户协议改动不一致的
`test_run_result.py`）、compileall、Node syntax、资产散列、Nuitka 根目录资产配置 smoke 和
架构守卫通过；没有遗留 Node 进程或 pending task；文档与实现一致。完整项目命令首个失败为
`test_stream_passes_environment_as_explicit_model_request_field`，原因是测试仍引用已移除的
`turn_stream.interrupt_turn`，不属于本阶段改动。

## 架构守卫

最终守卫至少验证：

- application 只导入 `agent` 契约，不导入具体 Sidecar；
- Workspace 端口和实现没有 JavaScript 执行、Session 或 lifecycle 成员；
- `infrastructure/sidecars/javascript` 是唯一 Python 实现位置；
- `sidecars/js_repl` 是唯一资产位置，文件集合和散列精确匹配清单；
- 不存在 `host.js`、`runtime.js`、`transform.js`、wrapper Host 或旧路径 fallback；
- frontends、protocol、server 和 backend 不导入具体 Sidecar；
- Sidecar 不包含线上协议字段，不直接创建标准库 logger；
- 不存在旧类名 alias、具体 Provider 包根重导出或测试专用生产接口。

完整架构守卫只在阶段 2 出口和阶段 3 最终验收运行，协议或进程的小步修改先运行对应定向
测试，避免用重复全仓扫描拖慢实施。

## 提交与证据

- 每个阶段仅在出口条件满足后提交；阶段内不提交红测试、空模块或临时 facade。
- 回退以阶段 commit 为单位，不通过恢复旧路径、双实现或运行时 fallback 回退。
- 用户工作树中的无关修改不纳入 Sidecar 阶段提交，也不被回退。

| 阶段 | 状态 | Commit | 验证结果 | 平台/产物 | 未决风险 |
| --- | --- | --- | --- | --- | --- |
| 0 文档与基线 | 已验证 | `136fcd24` | 72 passed；Node syntax、asset hash、diff check 通过 | Windows 11/source；Python 3.11.8；Node 24.12.0 | - |
| 1 Bundle/Adapter | 已验证 | `e38614ae` | 91 passed；扩大回归 163 passed；compileall、Node syntax、asset hash、diff check 通过 | Windows 11/source | - |
| 2 Composition/Harness | 已验证 | `ac35a4fb` | 190 passed；架构守卫 116 passed；compileall、Node syntax、asset hash、diff check、Nuitka 资产配置检查通过 | Windows 11/source；Python 3.11.8；Node 24.12.0 | Nuitka 独立产物 smoke 留待阶段 3 |
| 3 最终验收 | 已验证 | 待提交 | 2951 passed、11 skipped（项目测试排除 `codex-main` 和已知 `test_run_result.py`）；Sidecar/架构守卫、compileall、Node syntax、asset hash、Nuitka 根目录配置 smoke 通过 | Windows 11/source；Python 3.11.8；Node 24.12.0 | 完整桌面包需在具备 MSVC 的环境执行 Nuitka |
