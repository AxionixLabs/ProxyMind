# Windows 沙箱接入实施方案

## 1. 文档定位

本文记录 ProxyMind 的 Windows 本地受限执行接入方案和当前落地状态。

范围限定为：

- 不启动或打包 `codex.exe`；
- 不引入 CLI、TUI、模型、认证和会话逻辑；
- 仅复用 Windows restricted token、ACL、helper 和受控子进程能力；
- 由独立 Rust sidecar 提供本地执行服务；
- 运行时目录不依赖 ProxyMind 父目录或其他本地仓库。

当前 sidecar 已接入现有 Python 执行链：`read-only`、`workspace-read` 和
`workspace-write` 使用本地 sidecar，`danger-full-access` 才使用显式选择的本地进程路径；
sidecar 不可用时受限模式直接失败，不自动降级到裸进程。

## 2. 目标架构

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

sidecar 不是 CLI 替代品，只负责启动、读写和终止受限子进程。ProxyMind 继续负责策略判断、审批、结果格式化、文件审计和补丁应用。

## 3. ProxyMind 接入边界

现有执行链的主要入口：

- `mind_app/native_coding/exec/shell_exec.py`：一次性 shell 执行；
- `mind_app/native_coding/exec/exec_command.py`：交互式命令和会话控制；
- `ProcessSessionManager`：进程会话生命周期；
- `CommandPolicy`：命令目标和权限策略判断。

建议职责保持如下：

| 层 | 职责 |
| --- | --- |
| `CommandPolicy` | 判断是否允许执行、选择本地权限模式 |
| `SandboxClient` | 启动 sidecar、编解码 JSONL、维护进程 ID 和流事件 |
| Rust sidecar | 创建受限进程、应用 Windows 策略、转发输入输出 |
| `NativeCoding` | 超时、取消、结果标准化、文件审计 |
| `apply_patch` | 继续由 ProxyMind 自己执行 |

`cloud_sandbox` 仍表示远程执行后端，不与本地 Windows 沙箱混用。`danger-full-access` 只能由上层显式选择，不能作为 sidecar 缺失时的自动降级路径。

## 4. 临时构建目录

构建使用源码仓库中的 Rust crate 和临时 target 目录，不把 `target/`、Cargo 缓存或源码复制进运行时目录。

运行时发布目录与构建工程分开：

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

`schematic/sandbox` 不保存源码、`target/`、调试符号或 Cargo 缓存；运行时目录按平台只保存可执行文件。
源码只来自 `codex-main/codex-rs`，构建中间产物放在系统临时目录。

## 5. Sidecar 协议

sidecar 使用标准输入和标准输出交换 JSONL，不监听 TCP 端口。

### 启动请求

```json
{
  "id": "p1",
  "method": "spawn",
  "params": {
    "argv": ["powershell.exe", "-NoProfile", "-Command", "Get-ChildItem"],
    "cwd": "D:\\workspace",
    "workspace_roots": ["D:\\workspace"],
    "mode": "workspace-write",
    "level": "restricted-token",
    "env": {},
    "stdin_open": false,
    "tty": false
  }
}
```

`cwd` 和 `workspace_roots` 必须是绝对路径。sidecar 当前支持：

- `spawn`：创建受限进程并返回 sidecar 进程 ID；
- `write`：向指定进程写入 Base64 编码的 stdin 数据；
- `terminate`：终止指定进程；
- `close`：终止所有会话并退出 sidecar。

输出事件包含 `stdout`、`stderr` 和 `exit`。输出数据使用 Base64 保留字节流语义，编码转换由 Python 层完成。

## 6. 权限映射

| ProxyMind 模式 | Windows 行为 |
| --- | --- |
| `workspace-read` | workspace 可读，禁止写入 |
| `workspace-write` | workspace 根目录可写，其余位置受限 |
| `danger-full-access` | 不调用本地沙箱，必须由上层显式确认 |
| `cloud-sandbox` | 继续走现有远程后端 |

sidecar 只接受有限的模式和绝对根目录，不接受任意系统路径白名单。权限 profile 在进入 Windows API 前会根据 workspace roots 解析。

## 7. Windows helper 和初始化

三个运行时文件必须来自同一版本：

- `mind_sandbox_server.exe`：ProxyMind sidecar；
- `codex-command-runner.exe`：Elevated 路径的 command runner；
- `codex-windows-sandbox-setup.exe`：受限用户、ACL 和系统设置初始化 helper。

helper 使用固定文件名是为了匹配现有沙箱实现的资源查找逻辑。它们不等于 `codex.exe`。首次 setup 可能需要管理员权限，必须由单独的 readiness 流程触发，不能在普通命令中静默提权。

## 8. 构建和发布

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

只复制生成的 `.exe`，完成后删除临时目录。构建和安装包应记录 `Cargo.lock`、工具链、target 以及 helper 的版本信息。

## 9. 当前状态

已完成：

1. 已将 sidecar 和两个 helper 放入 `schematic/sandbox/windows/bin/`；
2. 已通过 sidecar 的 JSONL 参数校验、未知方法和关闭流程 smoke test；
3. 已移除构建缓存和临时 `target/`；
4. 已将 shell、exec 会话和 stdin 生命周期接入 sidecar；
5. 已将 shell/exec 审批收敛到客户端本地策略和 `.mind/rules`。

尚未完成：

1. setup readiness 和管理员权限恢复流程；
2. Windows workspace 外读写、junction/symlink 越界测试；
3. 长输出、背压、TTY、超时和进程树终止测试；
4. 完成安装包收集和干净机器验证。

## 10. 下一步

按以下顺序推进：

1. 在 Windows 上完成 setup readiness 和边界安全测试；
2. 保持 `cloud_sandbox` 仅作为服务端其他工具的远程目标，不参与本地 shell 执行；
3. 完成安装包收集和干净机器验证。

## 11. 必测风险

- workspace 外读取和写入；
- 符号链接、junction 和重解析点越界；
- 子进程创建和终止树；
- 环境变量过滤，避免继承凭据；
- 网络关闭时的连接尝试；
- TTY、长输出和背压；
- 超时、取消、sidecar 崩溃和重启；
- setup 未完成、权限不足、helper 缺失；
- 安装路径含空格、非 ASCII 字符或受 Defender 保护。

边界测试未通过前，不应把受限模式标记为可用；sidecar 不可用时必须保持失败闭环。
