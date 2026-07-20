# Mind TUI Architecture

## 目标

TUI 使用单一、持久的 `prompt_toolkit.Application` 管理输入、正文、动画、菜单和审批。
业务行为与终端布局分离，不复用会创建独立 Application 或直接写终端的旧交互实现。

## 分层

### `core`

持有 TUI 内部状态和 `prompt_toolkit` 对象。

- `runtime.py`：Application 生命周期、布局、焦点和输入队列。
- `document.py`：稳定正文、动态正文和全局段间距。
- `input.py`：输入编辑、补全、历史和粘贴折叠。
- `menu.py`：通用选择菜单状态机。
- `approval.py`：审批状态机、超时和快捷键。
- `activity.py`：输入框上方的专属动画行。
- `render.py`：Rich renderable 与 prompt_toolkit fragments 的转换。

`core` 不得导入 `features`、`session` 或 Mind 主控制对象。

### `adapters`

把共享应用契约和输出契约接入 `core`。

- `application.py`：应用级展示事件进入正文文档。
- `output.py`：单轮流式正文、状态动画和记录文件控制。

适配器不负责命令分派，不创建第二个 Application。

### `features`

实现具体用户功能，例如 shell、diff、history、MCP、permissions 和 tools。

功能模块可以调用 Mind 能力并使用 `core` 的菜单或运行时接口，但不得管理主 Application
生命周期，也不得直接修改正文内部状态。

### `session`

管理长期交互会话和单轮模型调用。

- `loop.py`：读取排队输入并分派命令或模型轮次。
- `turn.py`：建立单轮 MCP 会话并调用共享 stream 链路。

`session` 可以依赖 `features` 和 `core`，其他层不得反向依赖 `session`。

## 依赖方向

```text
mind_entry
    -> tui public facade
        -> session -> features -> core
        -> adapters ---------> core

output factory
    -> tui adapters -> core
```

## 状态所有权

- 正文块和空行规则只属于 `TuiDocument`。
- 输入 buffer 和按键绑定只属于 `TuiInputModel` 与主 TextArea。
- footer 是布局的最后一行，不进入正文换行状态。
- 动画只写入专属状态区域，不追加正文块。
- 菜单和审批各自持有 Future、选择位置和局部按键绑定。
- 输入提交只写入消息队列，不启动新的 Application。

## 扩展规则

- 新命令优先放入现有 `features` 域模块，避免继续扩大 `session/loop.py`。
- 多个输出模式共用的 ContentSink 或 PresentationSink 放在 `mind_app.output` 或
  `mind_app.presentation`，不得复制 TUI 专用版本。
- 新视觉块通过应用或输出适配器进入 `TuiDocument`，不得直接写 stdout。
- 只有需要接管真实终端的外部交互式程序可以通过 `run_modal()` 暂时让出终端。
- 不增加 mixin，不用字符串拼接模拟布局空行。

