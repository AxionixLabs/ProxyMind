# Agents

这个文件给自动化编码代理使用。执行任务时先读本文件，再读相关代码。

## 基本原则

- 先看现有实现，再做改动。
- 改动保持小而干净，不做无关重构。
- 不保留无意义兼容层，不需要的旧逻辑直接删除。
- 代码以可读性优先，避免绕、散、重复。
- 不用 `global`、`nonlocal`、`in locals()`。
- 拆模块不要用 mixin。
- 非测试模块里的函数 docstring 使用中文中性描述，不绑定具体业务。

## 架构边界

- `mind_app`、`mind_core`、`mind_nova` 等非 backend 模块不得导入 `backend` 包。
- `backend` 包只允许导入 `backend` 内部模块、标准库和第三方依赖，不得导入非 backend 包。
- 需要复用 backend 能力时，在 Mind 侧重写或迁移到 Mind 自己的模块。
- Mind 是主程序控制侧，Helix 是外部 provider，不要把 Helix 逻辑混进 Mind 顶层结构。
- `exec_env` 顶层以 Mind 为准；Helix 原始环境只放在 `providers.helix`。
- Mind 顶层 runtime 负责常见本地运行时和 shell/coding 工具。
- Helix provider 负责设备、媒体、性能等自身工具。

## 工具与环境

- Mind 侧 shell 工具只接 `rg`、`jq`、`ast-grep`。
- `rg`、`jq`、`ast-grep` 来自 Mind 的 `schematic/supports` 时，`source` 使用 `bundled`。
- Helix `requires` 下的工具不叫 `bundled`，使用 provider 语义。
- Helix 的 `adb`、`ffmpeg`、`ffprobe`、`k6`、`framix`、`memrix` 不上报到 Mind 顶层工具。
- 常见本地运行时只由 Mind 顶层上报，Helix 不重复上报。

## Coding 工具

- Mind 内置 coding 工具放在 `mind_app/native_coding` 和 `mind_app/client_tools/coding`。
- `shell_command`、`shell_calls`、`apply_patch` 是 Mind 内置 client tools。
- backend 不再注册 coding 工具，但 backend 原有代码可保留为 provider 侧代码。
- `apply_patch` 的失败原因必须通过工具结果返回，不在控制台打调试或 warning 日志。
