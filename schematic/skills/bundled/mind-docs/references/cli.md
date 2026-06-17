---
name: cli
description: 选择 Mind CLI 主模式和命令形态的决策对象，负责把任务路由到正确入口。
---

# Mind CLI 与运行模式

## What

这份文档解决一个最基础的问题：这条任务应该以什么命令形态发出去。

你只需要记住的输出形态：

- `mind --chat "..."`
- `mind --fast "..."`
- `mind --plan "..."`
- `mind --xtra "..."`
- `mind --agent`
- `mind --chat --code <source...>`
- `mind --fast --code <source...>`
- `mind --plan --code <source...>`
- `mind --xtra --code <source...>`

## When To Use

- 你还没决定该用哪个主模式。
- 你担心把探索性任务写成了单步命令。
- 你要判断一段复杂任务是否应该升级成 `--code`。

## When Not To Use

- 你已经在别的文档里确定了模式和输出形态，这时不需要回到这里重做选择。
- 你想讨论某个具体场景的写法细节，例如接口断言或设备证据链，这时应转到对应 playbook。

## Decision Table

| 输入特征 | 推荐模式 | 不推荐 |
| --- | --- | --- |
| 目标不清、需要先澄清、要多轮探索 | `--chat` | 直接用 `--fast` |
| 单次验证、单接口校验、一次性动作 | `--fast` | 写成长回归流程 |
| 巡检、回归、批量回放、要证据链 | `--plan` | 把多步骤压成 `--fast` |
| 增强探索、跨域串联、需要整理较宽证据 | `--xtra` | 强行压成单步 `--fast` |
| 订阅式 Agent 会话、远端持续执行 | `--agent` | 用一次性模式代替 |
| 同一命令里有多个任务块、循环、批量回放 | `--chat/--fast/--plan/--xtra` + `--code` | 直接堆成长段自然语言 |
| 只是要先把问题讲清楚，还不能确定执行细节 | `--chat` | 抢先选 `--plan` |
| 你已经知道验证目标、阈值和产出，而且只需单步完成 | `--fast` | 先写成长解释 |
| 你需要“开始-执行-验收-收束”的完整闭环 | `--plan` | 只给单次命令 |

## Core Rules

- 一条命令只用一个主模式。
- `--code` 不是独立模式，必须附着在 `--chat/--fast/--plan/--xtra` 上。
- `--agent` 是独立主模式，用于订阅式会话；不要和 `--code` 或 `--attach` 混用。
- `--code` 可以接一个或多个 source：本地文件、`-` 标准输入、`inline:<内容>` 或 HTTP(S) URL。
- `--attach` 只用于单次 `--chat` / `--fast` / `--xtra`，不和 `--plan` 或 `--code` 一起使用。
- 对外输出的是任务意图，不是内部实现方式。
- 先写目标对象、动作、通过条件、产出，再补边界。
- 如果使用 `--code` 文件，任务块分隔规则统一按 [`--code` 星图写法](blueprint.md) 执行。
- 模式选不准时，先澄清任务目标、边界和产出，再选择合适模式。
- 如果任务里出现多步链路、失败分支、循环样本或夜间回归，优先考虑 `--plan` 或 `--code`。
- 如果你无法一句话说明“通过条件是什么”，不要急着选 `--fast`。

## Good Examples

```bash
mind --chat "梳理这个仓库的核心模块、入口文件和主要数据流；指出最可能的 3 个高风险改动点；不要改代码，只返回结构化摘要。"
```

```bash
mind --fast "对 https://api.example.com/profile 做 GET 请求，校验状态码 200，断言 response.body_json.ok=true，提取 user_id、nickname 和 trace_id，并返回摘要。"
```

```bash
mind --plan "执行一次登录到下单的巡检流程：启动 app，进入登录页，输入测试账号密码并提交，等待订单页主标题出现；若失败立即截图并导出日志；返回通过/失败结论、失败步骤和证据路径。"
```

```bash
mind --xtra "跨仓库梳理登录、鉴权和订单链路的关键接口、调用关系和高风险点；只返回结构化证据摘要、待确认问题和建议下一步。"
```

```bash
mind --chat --code nightly_regression.md
```

```bash
mind --chat --code smoke.md api_regression.md
```

```bash
mind --agent
```

## Bad Examples

```bash
mind --chat "看看这个项目。"
```

问题：

- 目标对象太大且无边界。
- 没有产出要求。
- 无法判断是探索还是交付。

```bash
mind --fast "测一下接口。"
```

问题：

- 没写方法、URL、阈值、提取字段。
- `--fast` 被浪费在空泛描述上。

```bash
mind --plan "对 https://api.example.com/profile 做 GET。"
```

问题：

- 目标像单步验证，却误用了重模式。
- 没把 `--plan` 的证据链价值写出来。

## Checklist

- 是否已经选定唯一主模式。
- 是否明确目标对象、动作、通过条件、产出。
- 是否需要 `--code` 承载多任务或循环。
- 是否把边界写清楚，例如只读、只改某模块、失败即停。
- 是否避免暴露内部工具名。
- 所选模式是否和任务长度、证据要求、回归需求相匹配。

## Failure Handling

- 模式选不准时，先回到决策表重新确认任务目标、边界和产出。
- 一行命令塞不下时，转成 [`--code` 星图写法](blueprint.md)。
- 边界不清时，回读 [云端与本地边界](builtin-and-hosted.md)。
- 失败回报和降级方式不清时，回读 [错误与回退](errors-and-fallbacks.md)。
- 输出仍然空泛时，先回 [高质量任务 Rubric](task-quality.md) 和 [任务写法反模式](task-writing.md)。
- 明显选错模式时，先纠正模式，再细化任务正文，不要在错误模式上继续加细节。

## Related

- [Mind 超级文档中心](../SKILL.md)
- [高质量任务 Rubric](task-quality.md)
- [任务写法反模式](task-writing.md)
- [`--code` 星图写法](blueprint.md)
- [云端与本地边界](builtin-and-hosted.md)
- [错误与回退](errors-and-fallbacks.md)

