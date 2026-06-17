---
name: helix-docs
description: Mind 本地后台服务 Helix 的 JSON API 文档，覆盖服务状态、keepalive、执行环境、本地后台日志、模型偏好、远程服务域名配置和 Agent 状态。
---

# Helix Docs

这个 skill 提供 Mind 本地后台服务 Helix 的独立 API 文档。`SKILL.md` 是精简入口；完整文档放在 `references/api.md`。

## Routing

| 问题 | 先读什么 |
| --- | --- |
| 查询 Helix 本地服务 API 有哪些 | [Helix API](references/api.md) |
| 检查服务状态、健康状态、版本、空闲状态或 keepalive | [Helix API](references/api.md) |
| 读取执行环境、本地后台日志、模型偏好、远程域名配置或 Agent 状态 | [Helix API](references/api.md) |

## Core Rules

- Helix 是 Mind 的本地后台服务。
- 基础地址为 `http://127.0.0.1:3333`。
- `PUT` 请求使用 `Content-Type: application/json`。
- 不要把 Helix API 当成 `mind ...` 任务写法的一部分；需要 Mind CLI、任务写法、星图协议或能力边界时，才使用 `mind-docs`。
- 回答具体字段、请求体或响应结构前，先读取 [Helix API](references/api.md)。
