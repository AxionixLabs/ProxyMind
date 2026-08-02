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

## 品牌与命名

- `Mind`、`mind` 是应用品牌和既有包名，不得当作领域语义写入新增或修改的代码。
- 展示字符串不得硬编码 `Mind` 或 `mind`，应用名称统一引用
  `mind_nova.const.APP_DESC` 或 `mind_nova.const.APP_NAME`。
- docstring 使用中性能力描述，不得出现硬编码的 `Mind` 或 `mind`。
- 新增或重命名的类名、函数名、方法名、属性名和常量名不得包含 `Mind` 或
  `mind`，应按实际职责命名。
- 既有包路径、稳定入口和外部契约中的 `Mind` 或 `mind` 保持不动；引用这些名称
  不算新增硬编码，但不得据此继续扩散品牌命名。

## 测试原则

- 所有测试用例使用 pytest 风格，不使用 `unittest.TestCase` 或 `unittest.main()`。
- 异步测试使用 pytest 对应的异步标记；mock 可以使用 `unittest.mock`。
- 小改动不要求机械新增测试；新增功能或复杂行为变化时才补测试。
- 测试只覆盖核心主流程，不为实现细节或低风险边界穷举用例。
- 修改后运行与影响范围匹配的现有测试；扩大测试范围应与改动风险相称。

## 架构边界

- `mind_app`、`mind_core`、`mind_nova` 等非 backend 模块不得导入 `backend` 包。
- `backend` 包只允许导入 `backend` 内部模块、标准库和第三方依赖，不得导入非 backend 包。
- 需要复用 backend 能力时，在 Mind 侧重写或迁移到 Mind 自己的模块。
- Mind 是主程序控制侧，Helix 是外部 provider，不要把 Helix 逻辑混进 Mind 顶层结构。
- `exec_env` 顶层以 Mind 为准；Helix 原始环境只放在 `providers.helix`。
- Mind 顶层 runtime 负责常见本地运行时和 shell/coding 工具。
- Helix provider 负责设备、媒体、性能等自身工具。

## Mind 分层

```text
mind.py
    -> mind_app.cli                     参数选择、进程启动和前端装配
        -> mind_app.controller.Mind     主程序状态与生命周期
            -> mind_app.modes           chat、batch、agent 用例编排
            -> mind_app.runtime         MCP、工具和本地运行时
        -> mind_app.tui                 持久交互前端
        -> mind_app.output              text、jsonl、rich 输出实现

mind_app -> mind_core -> mind_nova
mind_app -------------> mind_nova
```

- `mind_app` 是主程序控制和运行侧，可以依赖 `mind_core`、`mind_nova`。
- `mind_core` 持有配置、偏好、skills、共享终端设计、许可证和远程服务元数据，
  可以依赖 `mind_nova`，不得导入 `mind_app`。
- `mind_nova` 只持有请求协议、远程传输、服务认证、事件和标识，不得导入
  `mind_core` 或 `mind_app`，不得读取本地 skills/config。
- `engine.ports` 负责本地端口探测和占用进程清理，这类进程能力不放入 `mind_nova`。
- CLI 是具体前端的组合根。`Mind`、`modes`、`runtime` 不判断
  `tui/rich/text/json`，只依赖前端和输出能力边界。

### mind_app 目录

- `cli`：命令行参数选择、启动编排和具体前端装配。
- `cli/entry.py`：进程级事件循环、单次参数解析、入口错误输出和轻量命令路由。
- `cli/bootstrap.py`：需要 Controller 的普通命令与升级命令的运行时生命周期装配。
- `cli/help.py`：所有命令层级共用的帮助版式、终端配色和无色输出判断。
- Doctor 和 MCP Server 各自作为独立组合根，不进入普通应用 bootstrap。
- `controller.py`：Mind 主控制器，不使用与顶层 `mind_core` 冲突的模块名。
- `frontend`、`interaction`：跨前端的应用展示与交互契约。
- `output`、`presentation`：单轮输出控制、内容事件和展示模型。
- `tui`：prompt_toolkit 应用、TUI 状态和交互功能。
- `modes`：面向用户的运行模式，不持有具体终端实现。
- `runtime`：工具执行、MCP 生命周期和运行环境。
- `client_tools`、`native_coding`、`mcp`：可执行能力及其协议适配。
- `approval`、`history`：独立领域状态与持久化。
- `stream_events`、`stream_state`、`stream_render`、`stream_io`：分别持有流式事件、
  状态、渲染和记录职责，共同服务 Rich 与 TUI 输出。

### TUI 分层

- `tui/core` 持有单一 prompt_toolkit Application、布局、正文、输入、菜单、审批和动画状态。
- `tui/adapters` 把共享 frontend/output 契约接入 core，不分派命令，不创建第二个 Application。
- `tui/features` 实现 shell、diff、history、MCP、permissions、tools 等用户功能，
  不管理主 Application 生命周期，不直接修改正文内部状态。
- `tui/session` 管理长期交互循环和单轮模型调用，可以依赖 features/core，其他层不得反向依赖 session。
- `tui/prompting` 只持有 TUI 输入补全、ghost suggestion 和 skill token lexer。
- `tui/core` 不得导入 `features`、`session` 或 Mind 主控制器。

TUI 状态所有权：

- 正文块和空行规则只属于 `TuiDocument`。
- 输入 buffer、补全、历史和按键绑定只属于输入模型与主 TextArea。
- footer 是布局最后一行，不进入正文换行状态。
- 动画只写入专属状态区域，不追加正文块。
- 菜单和审批各自持有 Future、选择位置和局部按键绑定。
- 输入提交只写入消息队列，不启动新的 Application。

### 模块规则

- 包级 `__init__.py` 只导出稳定且轻量的契约，不聚合具体实现或启动运行时。
- 实现工厂从所属模块显式导入，例如 `output.rich`、`output.text`、`output.jsonl`。
- 类型契约与具体实现分离，导入协议不得加载工具注册表、终端实现或本地配置。
- 不增加只转发一次调用的 display、factory、facade 或兼容模块。
- 不按文件行数拆分；只有状态所有权、独立生命周期或依赖方向明确时才新增模块。
- 多个输出模式共用的 ContentSink 或 PresentationSink 放在 `mind_app.output` 或
  `mind_app.presentation`，不得复制 TUI 专用版本。
- 新视觉块通过应用或输出适配器进入 `TuiDocument`，不得直接写 stdout。
- 只有需要接管真实终端的外部交互程序可以通过 `run_modal()` 暂时让出终端。
- 新 TUI 命令优先放入现有 `features` 域模块，避免继续扩大 `session/loop.py`。
- `stream_events/state/render/io` 暂不增加共同父包，避免没有边界收益的机械移动。

## 工具与环境

- Mind 侧 shell 工具只接 `rg`、`jq`、`ast-grep`。
- `rg`、`jq`、`ast-grep` 来自 Mind 的 `schematic/supports` 时，`source` 使用 `bundled`。
- Helix `requires` 下的工具不叫 `bundled`，使用 provider 语义。
- Helix 的 `adb`、`ffmpeg`、`ffprobe`、`k6`、`framix`、`memrix` 不上报到 Mind 顶层工具。
- 常见本地运行时只由 Mind 顶层上报，Helix 不重复上报。

## Coding 工具

- Mind 内置 coding 工具放在 `mind_app/native_coding` 和 `mind_app/client_tools/coding`。
- `shell_command`、`apply_patch` 是 Mind 内置 client tools。
- backend 不再注册 coding 工具，但 backend 原有代码可保留为 provider 侧代码。
- `apply_patch` 的失败原因必须通过工具结果返回，不在控制台打调试或 warning 日志。
