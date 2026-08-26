# macOS 沙箱侧车接入方案

## 1. 目标和边界

macOS 侧车只负责在本地创建受限子进程、转发标准输入输出和回收进程。命令危险性识别、`.mind/rules` 规则、审批和结果格式化仍由客户端完成；服务端不参与本地命令的最终放行判断。

本方案不启动 `codex` CLI，也不把 Windows helper 复制到 macOS 包中。macOS 使用系统提供的 Seatbelt 沙箱能力，权限配置由客户端根据当前工作区生成。

## 2. 目录与路径

```text
项目源码运行时
  <project>/schematic/sandbox/macos/bin/mind_sandbox_server

独立应用运行时
  <app>/Contents/MacOS/mind
  <app>/Contents/MacOS/schematic/sandbox/macos/bin/mind_sandbox_server

侧车源码和构建工程
  <project>/codex-main/codex-rs
  <system-temp>/mind-sandbox-build
```

`schematic/sandbox/macos/bin` 是发布目录，二进制由 `.gitignore` 忽略；源码、Cargo 缓存和 `target/` 只能存在于 `codex-main/codex-rs` 或系统临时构建目录。客户端必须分别解析源码入口和独立应用入口，不能通过当前工作区路径推断发布目录。

## 3. 客户端链路

```text
CommandPolicy
    |
    | 受限模式
    v
ProcessSessionManager
    |
    | stdio JSONL
    v
macOS sandbox sidecar
    |
    | sandbox-exec / Seatbelt profile
    v
受限 shell、脚本和子进程
```

`SandboxClient` 负责 sidecar 生命周期和 JSONL 协议，不负责判断命令是否危险。`read-only`、`workspace-read` 和 `workspace-write` 进入 sidecar；`danger-full-access` 继续走明确的裸本地进程路径。sidecar 缺失或启动失败时，受限模式直接返回失败，不自动降级。

## 4. Seatbelt 权限映射

| 客户端模式 | Seatbelt 行为 |
| --- | --- |
| `read-only` | 只读访问工作区，禁止写入和网络连接 |
| `workspace-read` | 只读访问工作区，允许读取必要的系统运行时资源 |
| `workspace-write` | 工作区根目录可写，工作区外默认拒绝 |
| `danger-full-access` | 不启动侧车，由上层显式确认后执行 |

profile 由客户端根据规范化后的工作区绝对路径生成，并拒绝符号链接解析后越过工作区根目录的路径。网络能力默认关闭；若未来需要网络访问，必须作为独立能力和审批项加入协议，不能通过环境变量隐式开启。

## 5. 侧车协议

沿用 Windows 侧车的 stdio JSONL 契约：`spawn`、`write`、`terminate` 和 `close`，输出事件为 `ready`、`stdout`、`stderr` 和 `exit`。路径使用绝对路径，输入输出字节使用 Base64 编码，`process_id` 由侧车分配。

macOS 侧车不得增加 Windows 专用的 `level=restricted-token`、ACL helper 或 `.exe` 资源字段。平台差异应由侧车内部实现和平台目录体现，Python 客户端只传递统一的工作区、模式、环境和超时字段。

## 6. 构建和签名

1. 在 `codex-main/codex-rs` 选择 macOS 对应 crate 和目标架构构建；
2. 将 Cargo `target/` 指向系统临时目录，例如 `/tmp/mind-sandbox-build`；
3. 只复制最终 sidecar 到 `schematic/sandbox/macos/bin/mind_sandbox_server`；
4. 对 sidecar 设置执行权限，和主应用使用同一签名身份完成签名与公证；
5. 打包时把 `schematic/sandbox/macos` 放入应用资源树，Windows 目录不进入 macOS 包；
6. 在干净 macOS 环境验证 App Translocation、非 ASCII 路径、Intel/Apple Silicon 架构和 hardened runtime。

构建失败、签名缺失或 sidecar 不可执行时，打包流程必须失败，不能生成一个看似完整但运行时才降级的安装包。

## 7. 验证清单

- 工作区内读、写和删除行为符合三种受限模式；
- 工作区外路径、符号链接和挂载点不能越界；
- 子进程树可以终止，超时和取消后不会遗留进程；
- 长输出、阻塞 stdin 和 sidecar 崩溃能够收敛到失败结果；
- 默认网络访问被拒绝，审批状态不会绕过 Seatbelt；
- 源码入口和打包入口都能找到正确的 macOS 侧车；
- macOS 包不包含 Windows `.exe` 和 Windows helper。

完成以上验证后，再把 macOS 的受限模式标记为可用，并补充对应的定向 pytest 和端到端 smoke test。
