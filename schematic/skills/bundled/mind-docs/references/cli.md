---
name: cli
description: 选择 Mind CLI 领域入口、Helix provider 和命令形态的决策对象，负责把任务路由到正确入口。
---

# Mind CLI 与运行模式

## What

这份文档解决一个最基础的问题：这条任务应该以什么命令形态发出去。

你只需要记住的输出形态：

- `mind --chat "..." --mcp`
- `mind --fast "..." --mcp`
- `mind --xtra "..."`
- `mind --agent`
- `mind --fast --mcp --code <source...>`
- `mind --chat --mcp --code <source...>`
- `mind --xtra --code <source...>`

## When To Use

- 你还没决定该用哪个领域入口。
- 你担心把接口、多媒体、Android、Framix、Memrix 或编码协作路由错。
- 你要判断一段复杂任务是否应该升级成 `--code`。

## When Not To Use

- 你已经在别的文档里确定了入口和输出形态，这时不需要回到这里重做选择。
- 你想讨论某个具体场景的写法细节，例如接口断言或设备证据链，这时应转到对应 playbook。

## Decision Table

| 输入特征 | 推荐入口 | 不推荐 |
| --- | --- | --- |
| Android、设备 UI、Framix、Memrix 的单次探索或状态查询 | `--chat --mcp` | `--xtra` |
| Android / 设备 UI 需要按顺序执行多条指令 | `--chat --mcp` | `--fast` |
| 接口、协议、压测、媒体处理、多媒体文件任务 | `--fast --mcp` | `--chat` |
| 接口或媒体批量回归、样本回放 | `--fast --mcp --code` | `--chat --code` |
| Android 多步骤巡检需要固化为批跑材料 | `--chat --mcp --code` | `--fast --code` |
| 外接 MCP、Mind native coding、代码修改、第三方服务协作 | `--xtra` | `--chat/--fast` |
| 外接 MCP 或编码任务需要批量执行 | `--xtra --code` | 叠加 `--mcp` |
| 订阅式 Agent 会话、远端持续执行 | `--agent` | 用一次性入口代替 |

## Core Rules

- `chat` 是 Android、Framix、Memrix 等 Helix MCP 执行面。
- `fast` 是接口、协议、多媒体等 Helix MCP 执行面。
- `xtra` 没有 Helix MCP；它只面向 Mind native coding tools 和已连接 external MCP tools。
- `--mcp` 只用于启动并挂载 Helix provider；不要和 `xtra` 绑定成默认写法。
- 一条命令只用一个主入口。
- `--code` 不是独立入口，必须附着在对应领域入口上。
- `--agent` 是独立主入口，用于订阅式会话；不要和 `--code` 或 `--attach` 混用。
- `--code` 可以接一个或多个 source：本地文件、`-` 标准输入、`inline:<内容>` 或 HTTP(S) URL。
- `--attach` 只用于单次 `--chat` / `--fast` / `--xtra`，不和 `--code` 一起使用。
- 对外输出的是任务意图，不是内部实现方式。
- 先写目标对象、动作、通过条件、产出，再补边界。
- 如果使用 `--code` 文件，任务块分隔规则统一按 [`--code` 星图写法](blueprint.md) 执行。
- 入口选不准时，先澄清任务目标、领域、边界和产出，再选择合适入口。

## Good Examples

```bash
mind --chat "读取当前 Android 前台页面状态，返回包名、页面摘要和关键可见元素。" --mcp
```

```bash
mind --fast "对 https://api.example.com/profile 做 GET 请求，校验状态码 200，提取 user_id、nickname 和 trace_id，并返回摘要。" --mcp
```

```bash
mind --chat "启动 xx 应用，进入登录页，输入测试账号密码并点击登录；等待首页主标题出现；若失败立即截图并导出日志；返回通过/失败结论、失败步骤和证据路径。" --mcp
```

```bash
mind --fast "从 demo.mp4 裁剪 00:00:05-00:00:12，提取 6 张关键帧，返回输出路径和摘要。" --mcp
```

```bash
mind --xtra "梳理这个仓库的核心模块、入口文件和主要数据流；指出最可能的 3 个高风险改动点；不要改代码，只返回结构化摘要。"
```

```bash
mind --fast --mcp --code api_regression.md
```

```bash
mind --xtra --code coding_refactor.md
```

```bash
mind --agent
```

## Bad Examples

```bash
mind --chat "测一下登录接口。" --mcp
```

问题：

- 接口属于 `fast` 领域，不属于 `chat`。
- 没写方法、URL、阈值、提取字段。

```bash
mind --xtra "启动 Android 应用并点击登录。"
```

问题：

- Android 属于 `chat` 的 Helix MCP 执行面。
- `xtra` 没有 Helix MCP。

```bash
mind --fast "从首页点击到详情页，再返回首页并截图。" --mcp
```

问题：

- 设备 UI 顺序操作属于 `chat`，不是接口/媒体的 `fast`。
- 多步骤路径应优先使用 `chat`。

## Checklist

- 是否已经选定唯一主入口。
- 是否明确任务领域：Android/Framix/Memrix、接口/媒体、外接 MCP/coding、订阅。
- 是否明确目标对象、动作、通过条件、产出。
- 是否需要 `--code` 承载多任务或循环。
- 是否把 `xtra` 限定在 external MCP 与 coding，而不是拿它调用 Helix MCP。
- 是否把边界写清楚，例如只读、只改某模块、失败即停。
- 是否避免暴露内部工具名。
- 所选入口是否和任务领域、任务长度、证据要求、回归需求相匹配。

## Failure Handling

- 入口选不准时，先回到决策表重新确认任务领域、边界和产出。
- 一行命令塞不下时，转成 [`--code` 星图写法](blueprint.md)。
- 边界不清时，回读 [云端与本地边界](builtin-and-hosted.md)。
- 失败回报和降级方式不清时，回读 [错误与回退](errors-and-fallbacks.md)。
- 输出仍然空泛时，先回 [高质量任务 Rubric](task-quality.md) 和 [任务写法反模式](task-writing.md)。
- 明显选错入口时，先纠正入口，再细化任务正文，不要在错误入口上继续加细节。

## Related

- [Mind 超级文档中心](../SKILL.md)
- [高质量任务 Rubric](task-quality.md)
- [任务写法反模式](task-writing.md)
- [`--code` 星图写法](blueprint.md)
- [云端与本地边界](builtin-and-hosted.md)
- [错误与回退](errors-and-fallbacks.md)
