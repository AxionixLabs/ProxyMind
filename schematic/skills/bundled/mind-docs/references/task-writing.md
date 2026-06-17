---
name: task-anti-patterns
description: 任务写法常见反模式库，帮助模型把空泛、越界、暴露内部能力名的任务改写成稳定外部表达。
---

# 任务写法反模式

## What

这份文档收集模型最常见的误写方式。

用途不是批评，而是快速定位“为什么这条任务看起来像在做事，其实不可执行”。

## When To Use

- 模型总写出“帮我看看”“调用某个工具”这种句子。
- 一条任务看起来很长，但仍然不能验收。
- 你想把差例子快速改写成好任务。

## Core Rules

- 不要空泛。
- 不要暴露内部能力名。
- 不要只写动作，不写通过条件。
- 不要只写过程，不写产出。
- 不要默认允许改所有地方。

## Bad Examples

```bash
mind --chat "看看这个项目。"
```

问题：

- 范围过大。
- 没有目标和产出。

```bash
mind --chat "调用 hosted tool 查文档。"
```

问题：

- 暴露内部能力名。
- 不具备外部稳定语义。

```bash
mind --fast "测一下接口。"
```

问题：

- 没写 URL、方法、断言、提取字段。

## Good Rewrite

```bash
mind --chat "梳理这个仓库的核心模块、入口文件和主要数据流；指出最可能的 3 个高风险改动点；不要改代码，只返回结构化摘要。"
```

```bash
mind --chat "查找 Mind CLI 的运行模式和常用命令，返回最短可执行示例。"
```

```bash
mind --fast "对 https://api.example.com/profile 做 GET 请求，校验状态码 200，断言 response.body_json.ok=true，提取 user_id、nickname 和 trace_id，并返回摘要。"
```

## Checklist

- 是否仍然出现“看看”“测一下”“处理一下”这类空泛动词。
- 是否出现内部名词，例如 builtin、hosted tool、MCP 工具名。
- 是否缺少通过条件或产出。
- 是否缺少边界。

## Failure Handling

- 发现反模式时，不要继续扩写原句，直接按 [高质量任务 Rubric](task-quality.md) 的 6 个维度重写。

## Related

- [高质量任务 Rubric](task-quality.md)
- [CLI 与运行模式](cli.md)
- [云端与本地边界](builtin-and-hosted.md)
- [错误与回退](errors-and-fallbacks.md)

