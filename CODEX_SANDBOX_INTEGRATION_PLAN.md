# Windows 沙箱接入实施方案

## 1. 文档定位

本文记录 ProxyMind 的 Windows 本地受限执行接入方案和当前落地状态。

范围限定为：

- 不启动或打包 `codex.exe`；
- 不引入 CLI、TUI、模型、认证和会话逻辑；
- 仅复用 Windows restricted token、ACL、helper 和受控子进程能力；
- 由独立 Rust sidecar 提供本地执行服务；
- 运行时目录不依赖 ProxyMind 父目录或其他本地仓库。

当前 sidecar 尚未替换现有 Python 执行链，现有链路继续保持原行为。

## 2. 目标架构

```text
mind_app/native_coding/exec
        |
        | stdio JSONL
        v
mind_sandbox/bin/mind_sandbox_server.exe
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

## 4. 独立构建工程

正式工程不能通过 `../../codex-main/...` 之类的路径依赖解析 crate。当前已创建独立工程：

```text
mind_sandbox_build/
  Cargo.toml
  Cargo.lock
  rust-toolchain.toml
  .cargo/config.toml
  mind_sandbox_server/
  windows-sandbox-rs/
  protocol/
  async-utils/
  execpolicy/
  ext/items/
  http-client/
  network-proxy/
  otel/
  codex-api/
  codex-client/
  websocket-client/
  utils/...
```

构建工程只包含 Windows 沙箱入口所需的 20 个内部 crate 和第三方依赖声明，不包含 CLI、TUI、模型、认证或完整 workspace。内部 path 依赖全部指向 `mind_sandbox_build` 自身目录。

运行时发布目录与构建工程分开：

```text
mind_sandbox/
  README.md
  bin/
    mind_sandbox_server.exe
    codex-command-runner.exe
    codex-windows-sandbox-setup.exe
```

`mind_sandbox` 不保存源码、`target/`、调试符号或 Cargo 缓存。删除其他仓库后，运行时仍可直接启动；需要重建时只需复制整个 `mind_sandbox_build`。

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

在 `mind_sandbox_build` 目录构建：

```powershell
rustup run 1.95.0 cargo build --release --target x86_64-pc-windows-msvc
```

发布时只复制：

```text
target/x86_64-pc-windows-msvc/release/mind-sandbox-server.exe
  -> mind_sandbox/bin/mind_sandbox_server.exe
```

不要复制 `target/`。构建和安装包应记录 `Cargo.lock`、工具链、target 以及 helper 的版本信息。

## 9. 当前状态

已完成：

1. 已创建独立 `mind_sandbox_build`，不依赖父目录仓库；
2. 已构建 `mind_sandbox_server.exe` Release 产物；
3. 已将 sidecar 和两个 helper 放入 `mind_sandbox/bin/`；
4. 已通过 sidecar 的 JSONL 参数校验、未知方法和关闭流程 smoke test；
5. 已移除构建缓存和临时 `target/`。

尚未完成：

1. setup readiness 和管理员权限恢复流程；
2. Windows workspace 外读写、junction/symlink 越界测试；
3. 长输出、背压、TTY、超时和进程树终止测试；
4. `mind_app/native_coding/exec` 的 `SandboxClient` 和 Python 执行链切换。

## 10. 下一步

按以下顺序推进：

1. 先在 Windows 上完成 setup readiness 和边界安全测试；
2. 为 `shell_exec.py` 增加本地 sidecar 客户端路径，缺少 sidecar 时 fail closed；
3. 为 `exec_command.py` 和 `ProcessSessionManager` 接入交互式 stdin、输出和终止；
4. 保持 `cloud_sandbox` 远程语义不变；
5. 完成安装包收集和干净机器验证。

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

在上述边界测试通过前，不应把本地沙箱设为默认执行路径，也不应在 sidecar 不可用时自动回退到裸 `subprocess`。
