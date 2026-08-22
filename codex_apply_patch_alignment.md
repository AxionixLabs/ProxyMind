# `apply_patch` 视觉与行为完全对齐方案

> 复核日期：2026-08-22  
> 参照实现：`D:\codex-main\codex-rs\tui`  
> 目标实现：`D:\PycharmProjects\ProxyMind`  
> 本文是实现规范，不直接修改运行代码。

## 1. 目标

本文只讨论 `apply_patch` 的终端展示，不讨论普通 Markdown 代码块、shell 命令输出或
`/diff` 独立命令。

最终体验必须满足：

- 一次 `apply_patch` 调用对应一个 patch cell，不因开始事件和结束事件产生重复块。
- 展示内容来自结构化 patch 结果，而不是把原始 patch 文本直接当作普通输出。
- 单文件和多文件使用 Codex 的文件摘要、增删统计、行号和 gutter 布局。
- 成功、失败、审批、取消、重试和终端 resize 都有稳定的视觉状态。
- 主画面、transcript overlay、raw transcript 使用同一份 patch 事实，但允许不同投影。
- 颜色只表达语义：默认正文、辅助信息、增加、删除、错误、活动状态，不使用装饰色。

不在本文范围内：

- 修改 patch 解析器或实际文件写入逻辑。
- 修改 `/diff` 命令的本轮净差异展示；它仍然是独立的用户命令。
- 为 patch cell 增加鼠标编辑、拖拽或点击操作。

## 2. 参照与当前差异

### 2.1 Codex 参照实现

| 责任 | 文件 |
| --- | --- |
| patch 开始时创建历史 cell | `codex-rs/tui/src/chatwidget/tool_lifecycle.rs::on_patch_apply_begin` |
| 文件摘要与增删统计 | `codex-rs/tui/src/history_cell/patches.rs`、`codex-rs/tui/src/diff_render.rs` |
| 单文件/多文件布局 | `codex-rs/tui/src/diff_render.rs::render_changes_block` |
| 行号、gutter、代码高亮 | `codex-rs/tui/src/diff_render.rs::render_change` |
| patch 失败展示 | `codex-rs/tui/src/history_cell/patches.rs::new_patch_apply_failure` |
| 颜色规范 | `codex-rs/tui/styles.md`、`codex-rs/tui/src/style.rs` |
| 视觉快照 | `codex-rs/tui/src/snapshots/*diff_render*apply*.snap` |

Codex 的成功 patch 快照形态为：

```text
• Edited example.txt (+1 -1)
    1  line one
    2 -line two
    2 +line two changed
    3  line three
```

多文件时才出现文件子节点：

```text
• Edited 2 files (+2 -1)
  └ a.txt (+1 -1)
    1 -one
    1 +one changed

  └ b.txt (+1 -0)
    1 +new
```

### 2.2 ProxyMind 当前行为

当前实现的主要路径是：

| 阶段 | 文件 | 当前行为 |
| --- | --- | --- |
| 开始事件 | `mind_app/stream_events/tool_traces/native.py` | 生成 `• Applying patch` |
| 结果事件 | `mind_app/stream_events/tool_traces/native.py` | 生成 `• Added/Edited/Deleted ...` |
| patch 预览 | `mind_app/stream_events/tool_traces/native_patch.py` | 生成文件树、hunk 和编号行 |
| TUI 写入 | `mind_app/tui/adapters/presentation.py` | 开始块和结果块都追加到 document |
| 样式拆分 | `mind_app/stream_events/tool_traces/render/title.py` | 标题、路径、hunk、行号和增删行分开着色 |

因此当前可能显示为：

```text
• Applying patch

• Edited sample.py (+1 -1)
└─ sample.py (+1 -1)
@@ -1 +1 @@
   1 -old value
   1 +new value
```

这与 Codex 的主要差异是：

1. 同一调用被拆成了两个视觉块。
2. 单文件标题重复显示路径和增删统计。
3. hunk 行 `@@ ... @@` 变成了可见正文。
4. patch 预览有固定屏幕行数限制；Codex 的已提交 diff 作为历史 cell 保留完整内容，
   由 viewport/scrollback 处理高度。
5. ProxyMind 使用自定义 Hex 颜色，Codex 优先使用终端默认色和 ANSI 语义色。

## 3. 统一渲染模型

### 3.1 一个调用对应一个 cell

使用 `call_id` 作为 patch cell 的稳定身份。渲染层禁止通过“追加一块新文本”表达同一
调用的状态变化。

```text
PatchCell
├── call_id
├── phase
├── raw_patch
├── files[]
├── total_added
├── total_removed
├── error
├── approval
├── display_cache[width, theme_revision]
└── transcript_projection
```

结构化文件模型：

```text
PatchFile
├── action: add | delete | update | rename
├── old_path
├── new_path
├── old_line_count
├── new_line_count
├── hunks[]
└── added / removed

PatchLine
├── kind: context | add | remove
├── old_line: int | None
├── new_line: int | None
├── text
└── syntax_spans[]
```

`raw_patch` 只用于 raw transcript、重排和审计；屏幕不能从 raw 文本重新猜测文件统计。

### 3.2 状态机

```text
IDLE
  │ patch_call_started
  ▼
APPROVAL_PENDING ── approve ──▶ APPLYING
  │                         ┌──────┴──────┐
  ├─ reject/cancel ────────▶│             │
  ▼                         ▼             ▼
CANCELLED              APPLIED        FAILED
                            │             │
                            └──────┬──────┘
                                   ▼
                                STABLE
```

当服务端在 `APPLYING` 阶段已经提供了结构化文件变化，主画面直接显示最终 diff 形态；
如果暂时只有开始事件，则同一个 cell 显示临时状态，后续原地更新为 diff，不能追加第二个
cell。

状态与事件规则：

| 状态/事件 | cell 是否创建 | 屏幕行为 | transcript 行为 |
| --- | --- | --- | --- |
| `patch_call_started` | 创建 | 建立一个 active patch cell | 记录原始 patch 和调用信息 |
| `approval_requested` | 更新 | 显示审批面板，不重复创建 patch cell | 记录审批请求 |
| `approved` | 更新 | cell 进入 `APPLYING`，显示活动标记 | 记录决定 |
| `rejected` | 更新并结束 | 显示取消/拒绝，不显示成功 diff | 保留拒绝原因 |
| `patch_apply_begin(changes)` | 更新 | 直接显示 Codex 风格摘要和 diff | 保存结构化变化 |
| `patch_apply_completed` | 更新并提交 | 保持当前 diff，不追加 `Done!` 或完成块 | 标记成功和耗时 |
| `patch_apply_failed(error)` | 更新并提交 | 替换为错误标题和诊断内容 | 保留完整错误 |
| `retry_started` | 旧 cell 提交，新 call_id 建新 cell | 旧结果不被覆盖 | 两次调用可分别审计 |
| `resize` | 不变 | 从结构化源按新宽度重排 | 不修改 raw transcript |
| `mouse` | 不变 | 忽略 | 不产生事件 |

### 3.3 ProxyMind 事件映射

目标映射如下：

| ProxyMind 当前事件 | 目标动作 |
| --- | --- |
| `ToolStartView(name=apply_patch)` | `get_or_create_patch_cell(call_id)` |
| `NativeToolResultView(name=apply_patch)` | `update_patch_cell(call_id, result)` |
| `ok=True` | 将同一 cell 标记 `APPLIED -> STABLE` |
| `ok=False` | 将同一 cell 替换为失败投影 |
| 不带 `call_id` 的旧事件 | 使用当前 active patch 作为兼容兜底，但记录诊断日志 |

兼容兜底只能存在于事件适配层，不能扩散到渲染器；渲染器始终只消费一个完整的
`PatchCell`。

## 4. 目标展示规范

### 4.1 成功单文件

新增：

```text
• Added new_file.txt (+2 -0)
    1 +alpha
    2 +beta
```

删除：

```text
• Deleted old_file.txt (+0 -3)
    1 -first
    2 -second
    3 -third
```

修改：

```text
• Edited example.txt (+1 -1)
    1  line one
    2 -line two
    2 +line two changed
    3  line three
```

重命名：

```text
• Edited old_name.rs → new_name.rs (+1 -1)
    1  A
    2 -B
    2 +B changed
```

单文件时：

- 标题已经包含路径和统计，不再输出文件子标题。
- 不显示 `@@` hunk 标题。
- 不显示原始 `*** Begin Patch` / `*** End Patch`。
- 上下文行、删除行和新增行全部保留。
- 没有额外的 `Done!`、`Patch applied` 或成功状态行。

### 4.2 成功多文件

```text
• Edited 2 files (+2 -1)
  └ a.txt (+1 -1)
    1 -one
    1 +one changed

  └ b.txt (+1 -0)
    1 +new
```

规则：

- 文件顺序按规范化显示路径排序。
- 总标题统计所有文件的增删行数。
- 每个文件子标题使用相对当前工作目录的路径。
- 文件之间保留一行空行，文件内部不额外插入空行。
- rename 使用 `old → new`，统计归入同一个文件节点。

### 4.3 临时执行状态

当结构化变化尚未到达时，同一 active cell 显示：

```text
• Applying patch
```

收到 `patch_apply_begin(changes)` 后，原地变为成功 diff 标题；不得出现：

```text
• Applying patch
• Edited file.py (+1 -1)
```

如果产品需要明确显示“正在执行”，活动标记只放在标题 bullet 或当前 cell 的状态样式
中，不创建第二块。

### 4.4 失败

```text
✘ Failed to apply patch
  reason: context did not match
  file: src/example.py
  hunk: @@ -10,4 +10,5 @@
  line: 12
```

规则：

- 标题使用红色错误符号和粗体。
- 只展示有价值的诊断字段：reason、error、file、hunk、line、expected、actual、nearby。
- expected/actual/nearby 使用缩进子行，不输出完整原始 JSON。
- 失败 cell 不显示绿色增删统计。
- 失败后不追加普通的 `Patch` 或 `Function Invoked` 块。

### 4.5 审批面板

审批是 patch cell 上方的临时交互面板，不属于成功 diff 本体：

```text
Would you like to make the following edits?

Reason: The model wants to apply changes

› 1. Yes, proceed (y)
  2. Yes, and don't ask again for these files (a)
  3. No, and tell Codex what to do differently (esc)

Press enter to confirm or esc to cancel
```

键盘规则：

- `Enter`：确认当前选项。
- `y`：确认一次。
- `a`：确认并记住当前文件范围的授权。
- `Esc`：拒绝并关闭。
- `Up`/`Down`、`Ctrl-P`/`Ctrl-N`：移动选项。
- `Mouse`：忽略，不提供点击授权路径。

审批完成后，面板关闭，patch cell 继续使用原来的 `call_id`。

## 5. 宽度、换行和滚动

### 5.1 可用宽度

内容宽度必须先扣除 cell 前缀，再交给 diff renderer：

```text
usable_width = terminal_width - prefix_width
```

推荐前缀：

- 主标题：`• `，宽度 2。
- 多文件子标题：`  └ `，宽度 4。
- diff 内容：`    `，宽度 4。

### 5.2 行号和 gutter

行号宽度取当前文件最大行号的显示宽度：

```text
line_number_width = display_width(str(max(old_line, new_line)))
row = "    " + right_align(line_number, line_number_width)
      + " " + marker + content
```

`marker` 只能是：

- ` `：context
- `-`：remove
- `+`：add

删除和新增行可能使用相同的新行号，这是正常的 unified diff 语义。

### 5.3 长行

- 以终端显示宽度而不是 Python/Rust 字符数计算。
- 换行时保留 gutter 语义，续行不重复假的行号。
- 不按 `-`、`_` 等标点强制断词。
- 样式 span 在换行后必须保持。
- resize 时从结构化 patch 重新渲染，不对已经换行的文本二次换行。

### 5.4 高度和截断

- 已提交 patch cell 不使用固定 18 行屏幕预览限制。
- 完整内容进入 transcript/scrollback，viewport 只负责显示可见区域。
- 活动 cell 高度随内容变化，重绘前清空旧区域，避免残留字符。
- 只有在外部输出模式明确要求限制时才截断；截断必须显示省略行并保留完整 raw transcript。

## 6. 样式和颜色规范

### 6.1 语义 token

| Token | 语义 | Codex 对齐样式 |
| --- | --- | --- |
| `patch.title` | `Added/Deleted/Edited` 标题 | 默认前景色 + bold |
| `patch.bullet` | `•`、`└` | dim |
| `patch.path` | 文件路径 | 默认前景色 |
| `patch.count` | `(+N -M)` | 括号默认；`+N` green，`-M` red |
| `patch.context` | 上下文行 | 默认前景色 |
| `patch.add` | 新增行和 `+` | green |
| `patch.remove` | 删除行和 `-` | red |
| `patch.line_number` | 行号 | dim；浅色主题使用可读的深色前景 |
| `patch.error` | 失败标题、错误字段 | red + bold |
| `patch.activity` | 临时执行状态 | cyan + bold |
| `patch.syntax.*` | 代码 token | 当前语法主题；不覆盖增删语义 |

### 6.2 颜色优先级

颜色叠加顺序固定为：

```text
终端主题可读性
  > patch add/remove 语义
  > syntax token
  > bold/dim/underline 修饰
```

新增/删除行的背景只作为辅助信息，不能覆盖文字前景色，也不能让语法颜色不可读。

### 6.3 TrueColor 和低色深终端

TrueColor 推荐使用 Codex 的低饱和背景：

```text
dark add line background:    #213A2B
dark remove line background: #4A221D
light add line background:   #DAFBE1
light remove line background:#FFEBE9
light add gutter background: #ACEEBB
light remove gutter background:#FFCECB
```

ANSI-256 使用最接近的调色板颜色；ANSI-16 或无法查询主题时只使用前景色：

```text
add    = ANSI green
remove = ANSI red
error  = ANSI red + bold
active = ANSI cyan + bold
```

禁止：

- 用黄色表示 hunk 或普通 patch 状态。
- 用蓝色表示增删行。
- 用黑色/白色覆盖终端默认前景。
- 使用高饱和整块背景导致代码 token 失去对比度。

### 6.4 ProxyMind token 映射

现有 ProxyMind 样式应按以下方向收敛：

| 当前样式 | 对齐目标 |
| --- | --- |
| `ACTION_EDIT_STYLE` | `patch.title`，默认前景 + bold |
| `PREVIEW_STYLE` | `patch.bullet` 或 `patch.line_number`，dim |
| `PREVIEW_PATH_STYLE` | `patch.path`，默认前景或轻度 bold |
| `PREVIEW_LINE_STYLE` | `patch.line_number`，dim |
| `DELTA_ADD_STYLE` | `patch.add`，ANSI green 或主题绿色 |
| 删除行自定义 `#FF8A8A` | `patch.remove`，ANSI red；浅色主题再选择可读深红 |
| `PREVIEW_HUNK_STYLE` | 删除；hunk 不进入最终 Codex 风格正文 |

## 7. 主画面、transcript 和 raw 投影

### 7.1 主画面

主画面使用富样式 patch cell：

- 显示标题、路径、统计、行号、gutter、语法颜色。
- 根据终端宽度重排。
- 成功后作为稳定 scrollback 保留。

### 7.2 transcript overlay

transcript overlay 使用同一份结构化 patch，但可选择：

- 保留主画面的行号和增删样式；或
- 使用更适合复制的纯文本 diff。

无论选择哪种投影，不能重新从已经换行的屏幕文本解析 patch。

### 7.3 raw transcript/JSONL

raw 记录必须保留：

- `call_id`
- 原始 patch 参数
- 结构化文件列表和增删统计
- 状态、错误、退出原因和耗时

raw 输出不应包含 ANSI 转义序列，也不应因为屏幕截断而丢失原始 patch。

## 8. 实现边界

建议按以下模块收敛：

| 模块 | 目标改动 |
| --- | --- |
| `mind_app/presentation/models.py` | 为 patch view 保留稳定 `call_id` 和 lifecycle 数据 |
| `mind_app/presentation/tool_views.py` | 开始/结果视图都携带同一调用身份 |
| `mind_app/stream_events/tool_traces/native.py` | 将 patch 结果转换为结构化摘要，不生成重复完成文本 |
| `mind_app/stream_events/tool_traces/native_patch.py` | 输出 `PatchFile/PatchLine` 等中立模型，保留完整 raw source |
| `mind_app/stream_events/tool_traces/render/title.py` | 实现标题、文件节点、行号和颜色 token 投影 |
| `mind_app/tui/adapters/presentation.py` | 按 `call_id` 更新 active patch cell，而不是无条件 append |
| `mind_app/tui/core/document.py` | 支持 active patch cell 原地更新、稳定提交和宽度重排 |
| `mind_app/tui/features/diff.py` | 保持 `/diff` 独立，不复用 patch cell 的生命周期 |

实现顺序：

1. 先统一 `call_id` 和 patch cell 状态机。
2. 再替换单文件/多文件文本布局。
3. 再实现宽度感知的行号、gutter 和完整 diff 保留。
4. 最后替换颜色 token 和主题适配。

不要先修改颜色；在 cell 生命周期未统一前，颜色变化无法解决重复块问题。

## 9. 验收快照矩阵

每个场景都应同时验证：纯文本内容、样式 token、显示宽度、transcript 内容和状态变化。

| 场景 | 必须验证 |
| --- | --- |
| 新增单文件 | 标题、`+N -0`、行号、无重复文件节点 |
| 删除单文件 | `Deleted`、删除行、红色语义 |
| 修改单文件 | context、remove、add 行号正确 |
| 多文件 | 总统计、文件排序、`└` 节点和空行 |
| 重命名 | `old → new`、新路径高亮规则 |
| 长行 | 宽度不溢出、续行不重复伪行号 |
| 窄终端 | 标题和路径不重叠，cell 可滚动 |
| patch 开始后成功 | 一个 cell 原地更新，不出现两个标题 |
| patch 开始后失败 | 一个失败 cell，不出现成功摘要 |
| 审批确认 | `Enter/y/a/Esc` 状态正确，面板关闭后复用 call_id |
| 审批取消 | 不生成成功 diff，不污染后续调用 |
| 重试 | 旧 cell 稳定，新 call_id 新建 cell |
| resize | 从结构化源重排，样式和行号保持 |
| transcript overlay | 不丢失 patch 内容，不重复解析屏幕文本 |
| raw transcript | 保留原始 patch，不含 ANSI，不受屏幕截断影响 |
| mouse event | 不改变 patch 状态 |

推荐使用 snapshot 测试固定以下宽度：`40`、`60`、`80`、`120`。

## 10. 完成定义

只有满足以下条件，才算完成对齐：

- 一次 patch 调用在主画面只出现一个稳定 cell。
- 成功单文件输出与 Codex 快照的文本结构一致。
- 多文件摘要、路径、统计和空行规则一致。
- hunk 标题和原始 patch fence 不出现在最终主画面。
- resize 不产生残留字符、重复行或错误行号。
- 失败和取消不会误显示绿色成功状态。
- 颜色在 dark/light、TrueColor/ANSI-256/ANSI-16 下都保持语义可读。
- 主画面、transcript、raw 输出的内容边界和生命周期一致，只在投影层有意差异。

