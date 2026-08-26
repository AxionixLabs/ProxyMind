# ProxyMind 沙箱侧车接入实施方案

## 1. 文档定位和边界

本文统一记录 ProxyMind 在 Windows 和 macOS 上接入本地受限执行侧车的架构、协议、发布和验证要求。

范围限定为：

- 不启动或打包 `codex.exe`，不引入 Codex CLI、TUI、模型、认证和会话逻辑；
- 由独立 Rust sidecar 在本地创建受限子进程、转发标准输入输出并回收进程；
- Windows 复用 restricted token、ACL、helper 和受控子进程能力；
- macOS 使用系统 Seatbelt 沙箱能力，由客户端生成权限 profile；
- 命令危险性识别、`.mind/rules` 规则、审批、结果格式化、文件审计和补丁应用继续由 ProxyMind 客户端完成；
- 运行时目录不依赖 ProxyMind 父目录或其他本地仓库。

当前 sidecar 已接入现有 Python 执行链：`read-only`、`workspace-read` 和
`workspace-write` 使用本地 sidecar，`danger-full-access` 才使用显式选择的本地进程路径；
sidecar 缺失或启动失败时，受限模式直接失败，不自动降级到裸进程。

## 2. 目录和运行时路径

发布目录只保存最终 sidecar 和平台所需的运行时 helper：

```text
schematic/sandbox/
  README.md
  windows/bin/
    mind_sandbox_server.exe
    codex-command-runner.exe
    codex-windows-sandbox-setup.exe
  macos/bin/
    mind_sandbox_server
```

源码和构建中间产物与发布目录分离：

```text
项目源码侧车入口
  <project>/codex-main/codex-rs

构建临时目录
  Windows: <system-temp>/mind-sandbox-build
  macOS:   /tmp/mind-sandbox-build

独立 macOS 应用运行时
  <app>/Contents/MacOS/mind
  <app>/Contents/MacOS/schematic/sandbox/macos/bin/mind_sandbox_server
```

`schematic/sandbox` 不保存 Rust 源码、Cargo 缓存、`target/` 或调试符号。macOS
发布二进制由 `.gitignore` 忽略；客户端必须分别解析源码入口和独立应用入口，不能通过当前工作区路径推断应用内资源路径。

## 3. 客户端链路和职责

```text
CommandPolicy
    |
    v
ProcessSessionManager
    |
    v
SandboxClient -- stdio JSONL --> 平台 sandbox sidecar
                                      |
                                      +-- Windows restricted token / ACL
                                      +-- macOS sandbox-exec / Seatbelt
                                      v
                               受限 shell、脚本和子进程
```

现有执行入口包括：

- `mind_app/native_coding/exec/shell_exec.py`：一次性 shell 执行；
- `mind_app/native_coding/exec/exec_command.py`：交互式命令和会话控制；
- `ProcessSessionManager`：进程会话生命周期；
- `CommandPolicy`：命令目标和权限策略判断。

| 层 | 职责 |
| --- | --- |
| `CommandPolicy` | 判断是否允许执行并选择本地权限模式；不把危险性判断下沉到 sidecar |
| `SandboxClient` | 解析平台 sidecar 路径、启动和关闭 sidecar、编解码 JSONL、维护进程 ID 和流事件 |
| Rust sidecar | 创建受限进程、应用平台权限策略、转发输入输出和回收进程 |
| `NativeCoding` | 处理超时、取消、结果标准化和文件审计 |
| `apply_patch` | 继续由 ProxyMind 自己执行，不交给本地 shell sidecar |

sidecar 不是 CLI 替代品，也不负责审批。`cloud_sandbox` 仍表示其他工具使用的远程执行后端，
不与本地 Windows 或 macOS shell 执行混用。`danger-full-access` 只能由上层显式选择，不能作为 sidecar 缺失时的自动降级路径。

## 4. Sidecar 协议

sidecar 使用标准输入和标准输出交换 JSONL，不监听 TCP 端口。平台客户端传递统一的工作区、模式、环境和超时字段；平台专用字段只能由对应 sidecar 解释。

### 4.1 启动请求

```json
{
  "id": "p1",
  "method": "spawn",
  "params": {
    "argv": ["powershell.exe", "-NoProfile", "-Command", "Get-ChildItem"],
    "cwd": "D:\\workspace",
    "workspace_roots": ["D:\\workspace"],
    "mode": "workspace-write",
    "env": {},
    "stdin_open": false,
    "tty": false,
    "timeout_ms": 60000
  }
}
```

`cwd` 和 `workspace_roots` 必须是解析后的绝对路径。Windows 受限 token 场景可以附带
`level=restricted-token`；macOS 不得增加 Windows 专用的 `level`、ACL helper 或 `.exe` 资源字段。

### 4.2 方法和事件

sidecar 支持以下方法：

- `spawn`：创建受限进程并返回 sidecar 进程 ID；
- `write`：向指定进程写入 Base64 编码的 stdin 数据，可用 `eof` 结束输入；
- `terminate`：终止指定进程；
- `close`：终止所有会话并退出 sidecar。

输出事件包含 `ready`、`stdout`、`stderr` 和 `exit`。输出数据使用 Base64 保留字节流语义，编码转换由 Python 层完成，`process_id` 由 sidecar 分配。

## 5. 权限模式映射

| ProxyMind 模式 | Windows 行为 | macOS 行为 |
| --- | --- | --- |
| `read-only` | 通过 sidecar 运行，工作区可读，禁止写入 | 只读访问工作区，禁止写入和网络连接 |
| `workspace-read` | 通过 sidecar 运行，允许读取工作区，工作区写入受限 | 只读访问工作区，允许读取必要的系统运行时资源 |
| `workspace-write` | 通过 sidecar 运行，工作区根目录可写，其余位置受限 | 工作区根目录可写，工作区外默认拒绝 |
| `danger-full-access` | 不调用本地沙箱，必须由上层显式确认 | 不启动侧车，必须由上层显式确认 |
| `cloud-sandbox` | 继续走现有远程后端 | 继续走现有远程后端 |

sidecar 只接受有限的模式和绝对根目录，不接受任意系统路径白名单。Windows 权限 profile
在进入 Windows API 前根据 workspace roots 解析；macOS profile 根据规范化后的工作区绝对路径生成，
并拒绝符号链接解析后越过工作区根目录的路径。macOS 网络能力默认关闭；若未来需要网络访问，
必须作为独立能力和审批项加入协议，不能通过环境变量隐式开启。

## 6. Windows 实现、构建和发布

### 6.1 运行时组件

```text
mind_app/native_coding/exec
        |
        | stdio JSONL
        v
schematic/sandbox/windows/bin/mind_sandbox_server.exe
        |
        +-- Windows sandbox library
        +-- codex-command-runner.exe
        +-- codex-windows-sandbox-setup.exe
        v
受限 powershell/cmd/python/node/其他子进程
```

三个 Windows 运行时文件必须来自同一版本：

- `mind_sandbox_server.exe`：ProxyMind sidecar；
- `codex-command-runner.exe`：Elevated 路径的 command runner；
- `codex-windows-sandbox-setup.exe`：受限用户、ACL 和系统设置初始化 helper。

helper 使用固定文件名以匹配现有沙箱实现的资源查找逻辑，它们不等于 `codex.exe`。首次 setup
可能需要管理员权限，必须由单独的 readiness 流程触发，不能在普通命令中静默提权。

### 6.2 构建和发布

目标工具链：

```text
Rust：1.95.0
Target：x86_64-pc-windows-msvc
Profile：release
```

在 `codex-main/codex-rs` 使用临时 target 目录构建：

```powershell
cargo build --release --locked --package codex-windows-sandbox --bins --target-dir "$env:TEMP\\mind-sandbox-build"
cargo build --release --locked --package codex-sandboxing --bin mind-sandbox-server --target-dir "$env:TEMP\\mind-sandbox-build"
```

发布时只复制：

```text
target/x86_64-pc-windows-msvc/release/mind-sandbox-server.exe
  -> schematic/sandbox/windows/bin/mind_sandbox_server.exe
```

只复制生成的 `.exe`，完成后删除临时目录。构建和安装包应记录 `Cargo.lock`、工具链、target
以及 helper 的版本信息。

## 7. macOS 实现、构建和签名

### 7.1 Seatbelt 权限

macOS 侧车通过 `sandbox-exec` / Seatbelt profile 创建受限进程。侧车不得复制 Windows helper，
也不得在 macOS 包中包含 Windows `.exe`。平台差异应由 sidecar 内部实现和平台目录体现，Python
客户端只传递统一的工作区、模式、环境和超时字段。

### 7.2 构建和发布流程

1. 在 `codex-main/codex-rs` 选择 macOS 对应 crate 和目标架构构建；
2. 将 Cargo `target/` 指向系统临时目录，例如 `/tmp/mind-sandbox-build`；
3. 只复制最终 sidecar 到 `schematic/sandbox/macos/bin/mind_sandbox_server`；
4. 对 sidecar 设置执行权限，和主应用使用同一签名身份完成签名与公证；
5. 打包时把 `schematic/sandbox/macos` 放入应用资源树，Windows 目录不进入 macOS 包；
6. 在干净 macOS 环境验证 App Translocation、非 ASCII 路径、Intel/Apple Silicon 架构和 hardened runtime。

构建失败、签名缺失或 sidecar 不可执行时，打包流程必须失败，不能生成一个看似完整但运行时才降级的安装包。

## 8. 当前状态

### 8.1 已完成

1. Windows sidecar 和两个 helper 已放入 `schematic/sandbox/windows/bin/`；
2. 已通过 Windows sidecar 的 JSONL 参数校验、未知方法和关闭流程 smoke test；
3. 已移除构建缓存和临时 `target/`；
4. Windows shell、exec 会话和 stdin 生命周期已接入 sidecar；
5. Windows shell/exec 审批已收敛到客户端本地策略和 `.mind/rules`；
6. Windows 和 macOS 共用 stdio JSONL 的 `spawn`、`write`、`terminate`、`close` 契约及输出事件模型；
7. macOS 客户端链路、发布路径和 Seatbelt 设计已完成文档定义。

### 8.2 尚未完成

1. Windows setup readiness 和管理员权限恢复流程；
2. Windows workspace 外读写、junction/symlink 越界测试；
3. macOS 侧车实现、签名、公证和应用内资源验证；
4. 两个平台的长输出、背压、TTY、超时和进程树终止测试；
5. 安装包收集、干净机器验证以及 macOS App Translocation/Intel/Apple Silicon 验证。

## 9. 验证清单和必测风险

两个平台都必须验证：

- 工作区内读、写和删除行为符合受限模式；
- 工作区外路径、符号链接、junction 或挂载点不能越界；
- 子进程树可以终止，超时和取消后不会遗留进程；
- 长输出、阻塞 stdin、背压和 sidecar 崩溃能够收敛到失败结果；
- 环境变量过滤，避免继承凭据；
- 默认网络访问被拒绝，审批状态不会绕过平台沙箱；
- 安装路径含空格、非 ASCII 字符或受系统安全策略保护时仍能启动；
- sidecar 缺失、权限不足或 helper 缺失时保持失败闭环。

平台专项验证：

- Windows：restricted token、ACL、setup readiness、Defender 保护路径以及 `codex-command-runner.exe` / `codex-windows-sandbox-setup.exe` 版本一致性；
- macOS：Seatbelt profile、源码入口和打包入口、App Translocation、hardened runtime、签名公证、Intel/Apple Silicon 架构以及 Windows 资源排除。

边界测试未通过前，不应把对应平台的受限模式标记为可用；sidecar 不可用时必须保持失败闭环。

## 10. 下一步

按以下顺序推进：

1. 在 Windows 上完成 setup readiness 和边界安全测试；
2. 完成 macOS sidecar 构建、签名、公证和 Seatbelt 边界测试；
3. 补齐两个平台的长输出、背压、TTY、超时、取消、崩溃恢复和进程树测试；
4. 保持 `cloud_sandbox` 仅作为服务端其他工具的远程目标，不参与本地 shell 执行；
5. 完成安装包收集、干净机器验证和对应的定向 pytest/端到端 smoke test。
