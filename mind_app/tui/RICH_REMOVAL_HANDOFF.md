# TUI Rich Removal Handoff

## 任务目标

让 TUI 模式的完整导入和运行链路不加载 Rich，同时保留 `rich` 输出模式。

“完成”不是指 `mind_app/tui` 中没有 Rich import，而是执行以下代码后，
`sys.modules` 中不存在 `rich` 及其子模块：

```python
from mind_app.cli.frontend import resolve_cli_frontend

resolve_cli_frontend("tui")
```

Rich 模式继续作为独立输出适配器存在，不要求从项目依赖中删除 Rich。

## 当前状态

TUI 的 Application、输入队列、布局、正文位置、动画区域、菜单和审批由
prompt_toolkit 管理，不再复用旧 REPL Application。

但是 TUI 仍把 Rich renderable 作为内部展示协议，因此存在以下依赖。

### 直接依赖

- `tui/core/render.py` 创建内存 `rich.console.Console`，经过 ANSI 转换为
  prompt_toolkit fragments。
- `tui/adapters/application.py` 在 TUI 启动前使用 Rich Console，并用内存
  Console 生成启动标识。
- `tui/adapters/output.py` 使用 `rich.text.Text` 生成正文块和光标。
- `tui/core/activity.py` 使用 Rich Text 生成动画帧。
- `tui/features/diff.py`、`tui/features/summary.py` 使用 Rich Text。

### 间接依赖

- `tui/adapters/session.py` 使用 `TerminalPresentationSink`。
- `TerminalPresentationSink` 依赖 `presentation.rich` 渲染链。
- `stream_state/text.py` 返回 Rich Text、Group 和 Rich Markdown renderable。
- `stream_state/markdown.py` 完全基于 Rich Markdown、Syntax、Segment 和 Theme。
- `stream_state/status.py` 返回 Rich Text/Span，并调用 Rich Design renderer。
- `mind_core.design` 的启动标识、状态帧和动画均依赖 Rich。
- `mind_app/controller.py` 顶层导入 `Design` 并保存 `mind.design`。
- `mind_app/cli/frontend.py` 顶层导入 Rich 输出工厂和 Console sink。
- `mind_app/frontend/__init__.py` 聚合导出 Console sink，导入包时可能提前加载 Rich。

## 目标结构

```text
PresentationView
        |
        v
Neutral StyledBlock / StyledText
        |
        +-- TuiPresentationSink  -> prompt_toolkit fragments
        |
        +-- RichPresentationSink -> rich.Text / rich.Markdown
```

共享层只生成语义展示数据和中立样式。prompt_toolkit 与 Rich 的具体对象只存在于
各自适配器中。

## 设计约束

- 不复制工具、审批、plan、batch、progress 的业务渲染规则。
- 不把 Rich 样式字符串伪装成中立协议。
- 不在共享 presentation、stream state 中导入 prompt_toolkit。
- 不让 TUI Document 保存 `typing.Any` renderable。
- 不使用 ANSI 作为 TUI 内部协议。
- Rich 模式和 TUI 模式必须继续使用同一套语义 View builders。
- 保持现有全局换行、输入队列、动画专属行和 footer 状态所有权。

## 中立展示模型

建议在 `mind_app/presentation` 下建立轻量模型，例如：

```python
@dataclass(frozen=True, slots=True)
class TextStyle:
    foreground: str | None = None
    background: str | None = None
    bold: bool = False
    dim: bool = False
    italic: bool = False
    underline: bool = False
    reverse: bool = False


@dataclass(frozen=True, slots=True)
class TextSpan:
    text: str
    style: TextStyle = TextStyle()


@dataclass(frozen=True, slots=True)
class StyledBlock:
    spans: tuple[TextSpan, ...]
    plain_text: str
    preserve_spans: bool = False
    direct: bool = False
```

现有 `presentation.rich.models.RenderedBlock` 和 `display_parts` 已接近这个结构，
应迁移和类型化，不要另起一套平行模型。

当前类似 `"bold #AFC7D8"`、`"dim #7F8C9A"` 的值属于 Rich 语法。
中立模型应保存颜色和布尔属性，分别由适配器映射为：

- prompt_toolkit：`fg:#AFC7D8 bold`
- Rich：`bold #AFC7D8`

## 分阶段实施

### 阶段 1：中立 presentation block

1. 将 `RenderedBlock` 从 `presentation/rich/models.py` 迁到共享 presentation。
2. 将字典形式的 `display_parts` 替换为类型化 `TextSpan`。
3. 把 `presentation/rich/*_views.py` 中不依赖 Rich 的渲染器迁到共享 renderers。
4. 保持 `PresentationView` builders 不变。

阶段完成条件：共享 renderer 不导入 Rich，也不导入 prompt_toolkit。

### 阶段 2：独立 TuiPresentationSink

新增 `tui/adapters/presentation.py`：

- 接收 `PresentationView`。
- 调用共享 renderer 得到 `StyledBlock`。
- 将中立 style 转为 prompt_toolkit fragments。
- 写入 `TuiOutputControl` 或 `TuiRuntime`。

修改 `tui/adapters/session.py`，不再使用 `TerminalPresentationSink`。

Rich 输出侧建立或保留单独的 Rich presentation sink，将同一 `StyledBlock` 转成
Rich Text。

阶段完成条件：工具展示链不经过 `presentation.rich`。

### 阶段 3：TextState 纯状态化

保留 `stream_state/text.py` 中：

- `display_text`、`raw_text`
- segments/spans
- 输出边界和换行状态
- 可见文本窗口
- `append()`、`clear()` 等状态操作

移出或删除：

- `renderable()`
- `renderable_for_text()`
- `final_renderable()`
- `_mixed_final_renderable()` 中的 Rich 对象构造
- 对 Rich Group、Text、Markdown 的导入

TextState 应返回纯文本、spans 或 StyledBlock。Rich/TUI 适配器分别负责最终渲染。

阶段完成条件：`stream_state/text.py` 不导入 Rich。

### 阶段 4：TUI Markdown renderer

TUI 不能继续调用 `stream_state/markdown.py`。为保持正文效果，使用
`markdown-it-py` 解析 Markdown token，再转换成中立 spans 或 prompt_toolkit fragments。

最低支持范围：

- 标题、段落
- 粗体、斜体
- 有序列表、无序列表
- 引用
- 行内代码、代码块
- 链接
- 正文段间距

不要用正则手写 Markdown 解析器。Rich Markdown renderer 继续只服务 Rich 输出。

阶段完成条件：TUI 最终正文不调用 `stream_state/markdown.py`。

### 阶段 5：TUI 内部只使用 fragments

调整以下模块：

- `tui/core/document.py`
- `tui/core/runtime.py`
- `tui/core/activity.py`
- `tui/adapters/output.py`
- `tui/features/diff.py`
- `tui/features/summary.py`

`TuiDocument` 应保存 `FormattedText` 或中立 `StyledBlock`，不保存
`typing.Any renderable`。

删除 `tui/core/render.py` 中的 `Console -> ANSI -> fragments` 转换桥。

阶段完成条件：`rg "from rich|import rich" mind_app/tui` 无结果。

### 阶段 6：移除 Design 和启动前 Console

`TuiApplicationSink` 不再创建 Console。推荐流程：

1. TUI runtime 在发送 intro/application event 前启动；或
2. runtime 未启动时缓存 ApplicationView，open 后统一写入 Document。

启动标题、runtime update、error、worked footer 直接生成中立 block/fragments。

TUI 状态动画自行生成 prompt_toolkit fragments，不调用：

- `Design.status_line_renderable`
- `Design.stream_mode_live`
- `UploadProgressLiveReporter` 的 Rich renderable
- 其他返回 Rich Text/Live 的 Design API

`controller.py` 不应顶层导入 Rich Design。可把 Rich 动画能力放入非 TUI
FrontendRuntime，或仅在 Rich fallback 分支局部导入。

阶段完成条件：构造 TUI frontend 不加载 `mind_core.design` 的 Rich 模块。

### 阶段 7：切断 CLI eager imports

检查并调整：

- `mind_app/cli/frontend.py`
- `mind_app/frontend/__init__.py`
- `mind_app/frontend/sinks.py`
- `mind_app/output/rich.py`

TUI 分支不能因为包级聚合或顶层 import 加载 Console sink、Rich output factory 或
Rich Design。具体实现使用分支内局部导入或独立 composition module。

阶段完成条件：仅导入和构造 TUI frontend 时，`sys.modules` 中没有 Rich。

## 推荐工作单元

按以下边界逐次完成，每一步都保持可编译：

1. 中立 style/block 类型和共享 renderer。
2. TuiPresentationSink。
3. TextState 去 Rich。
4. TUI Markdown。
5. TUI activity/output/features fragments 化。
6. ApplicationSink、Design 和 CLI lazy import。
7. 删除转换桥和残留模块。

不要一次性重写 TUI runtime、输入系统和 presentation；它们的状态所有权已经稳定。

## 易错点

- 中文宽度必须使用 `prompt_toolkit.utils.get_cwidth`，不要用 `len()` 计算布局宽度。
- prompt_toolkit 与 Rich 的 style 语法不同，不能直接透传字符串。
- Markdown 最终落版和流式增量展示是两个阶段，不要在每个 delta 上重新解析完整 Markdown。
- footer、动画行不进入 `TuiDocument` 的正文换行状态。
- active block 提交时必须保持原 `gap_before`，否则输入框和正文会跳动。
- 鼠标复制依赖 prompt_toolkit 输出画布，不要改回全屏清屏式刷新。
- 启动前事件不能回退到 `print()` 或 Console，否则会重新出现双渲染和位置漂移。
- Rich 输出模式仍需要 Rich，不要把 Rich renderer 迁入 TUI 或直接删除项目依赖。

## 验收命令

直接依赖扫描：

```powershell
rg -n "from rich|import rich" mind_app\tui
```

TUI 依赖闭包检查：

```powershell
python -c "import sys; from mind_app.cli.frontend import resolve_cli_frontend; resolve_cli_frontend('tui'); leaked=[name for name in sys.modules if name == 'rich' or name.startswith('rich.')]; assert not leaked, leaked; print('TUI Rich-free')"
```

编译检查：

```powershell
python -m compileall -q mind.py mind_app mind_core mind_nova engine server
```

旧转换桥检查：

```powershell
rg -n "renderable_fragments|Text\.from_ansi|force_terminal=True" mind_app\tui
```

测试目录当前已删除，因此不要把“没有可运行测试”误判为实现完成。至少完成上述导入、
依赖和编译检查，并手动验证启动标题、流式正文、工具展示、审批、输入排队、footer、
中文换行和鼠标复制。

## 当前工作区提示

当前工作区包含尚未提交的架构迁移，包括 CLI/TUI 分层、`mind_app.controller`、
轻量 output/MCP 契约以及多个模块重命名。后续实现应沿用新路径，不要恢复：

- `mind_app.mind_core`
- `mind_app.mind_entry`
- `mind_app.stream_ui`
- `mind_core.prompting`
- `mind_core.live_session`
- `mind_nova.request`
- `mind_nova.craft`

开始工作前先阅读仓库根目录 `Agents.md`，并检查当前 `git status`，不得回退其他未提交改动。
