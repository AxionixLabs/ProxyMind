---
name: nexus
description: 协议与接口任务对象，覆盖单次校验、批量回放、压测和结果汇总的任务写法。
---

# Nexus 场景

## What

`Nexus` 用来把协议任务写成可验证、可回看、可汇总的对象。

它最常见的用途：

- 接口校验。
- 批量回放。
- 压测与结果汇总。

## When To Use

- 你要做 HTTP/SSE/WS/GraphQL/TCP/UDP/SMTP/IMAP/FTP 类协议任务。
- 你要拿到 token、trace_id、user_id 这类后续要复用的结果。
- 你要写失败率、P95、吞吐等阈值。

## Decision Table

| 任务形态 | 推荐模式 |
| --- | --- |
| 单次接口验证 | `mind --fast --mcp` |
| 批量回归、样本回放 | `mind --fast --mcp --code` |
| 长链路接口编排 | `mind --fast --mcp` |

## Core Rules

- 指定方法、URL、请求体或关键请求头。
- 指定通过条件：状态码、字段值、错误码、响应耗时。
- 指定要回收的关键结果：token、trace_id、user_id、intent、summary。
- 压测时必须写阈值：失败率、P95、吞吐或持续时间。
- 多步骤回归不要堆在一行里，优先改用 `--code`。
- 一旦改用 `--code`，任务块边界统一按 [`--code` 星图写法](blueprint.md) 执行。
- 使用策略要与当前工具集对齐：单次执行、批量回放、执行前预览、执行前校验是不同能力，示例里要把这几类意图区分开。
- 批量回放的稳定心智模型仍然是“共享环境 + 多个请求项 + 并发与失败策略”，但自然语言示例不需要下沉成参数对象。

## Good Examples

```bash
mind --fast --mcp "对 https://api.example.com/login 做 POST，请求体包含 username 和 password，校验状态码 200，断言 response.body_json.ok=true，拿到 token、user_id 和 trace_id，并返回摘要。"
```

```bash
mind --fast --mcp "对 https://api.example.com/order 做压测，20 个虚拟用户持续 60 秒，失败率低于 1%，P95 小于 300ms，输出 summary。"
```

```bash
mind --fast --mcp --code nexus_regression.md
```

补充示例：

```text
如果你把接口回归写进 `nexus_regression.md`，任务块写法和收尾规则统一遵循 `--code` 星图规范。
```

```bash
mind --fast --mcp "对 https://api.example.com/login 做 POST，请求体包含 username 和 password；校验状态码 200 和业务成功标记；拿到 token、user_id、trace_id，并返回摘要。"
```

```bash
mind --fast --mcp "先只校验这组 HTTP 回归样本的结构是否完整：公共鉴权头放在共享层，每条样本只保留自己的差异内容；检查并发设置和失败策略是否合理，返回展开后的预览摘要，不实际发请求。"
```

## Bad Examples

```bash
mind --fast --mcp "测一下登录接口。"
```

问题：

- 没写目标。
- 没写通过条件。
- 没写要回收的关键结果。

```text
把接口回归写进 `--code` 文件，却没有按星图规范处理任务块边界。
```

问题：

- 容易漏掉块结束符。
- 多块回归时边界容易错乱。

## Checklist

- 是否写清协议、方法和目标。
- 是否写清状态码、字段断言或性能阈值。
- 是否写清要回收的关键结果。
- 多步骤任务是否已经升级成 `--code`。
- 示例是否区分了“单次执行”“批量回放”“先校验后执行”这几类使用策略。

## Failure Handling

- 阈值缺失时，直接指出缺少失败率、P95、吞吐或持续时间。
- 动态变量复杂或链路较长时，转到 [Nexus 示例对象](nexus-examples.md) 或 [`--code` 星图写法](blueprint.md)。

## Related

- [Nexus 示例对象](nexus-examples.md)
- [高质量任务 Rubric](task-quality.md)
- [任务写法反模式](task-writing.md)
- [`--code` 星图写法](blueprint.md)
- [Common 场景](common.md)
- [错误与回退](errors-and-fallbacks.md)

