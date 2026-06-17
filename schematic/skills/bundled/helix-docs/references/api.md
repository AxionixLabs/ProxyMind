---
name: helix
description: Mind 本地后台服务 Helix 的 JSON API，覆盖服务状态、执行环境、本地后台日志、模型配置、域名配置和 Agent 示例状态。
---

# Helix 本地后台服务 API
本文档说明 Mind 本地后台服务 Helix 提供的 JSON API。

## 概览

如果你在找“Helix 本地后台服务 API 有哪些”，先看这一节。

- 服务状态：`/ready`、`/healthz`、`/version`、`/api/idle`、`/api/keepalive`
- 执行环境：`/api/runtime/exec-env`
- 本地后台日志：`/api/logs`
- 模型配置：`/api/pref`
- 域名配置：`/api/service-config`
- Agent 示例状态：`/api/agent`

## 基础地址
所有接口地址均基于此基础地址： http://127.0.0.1:3333
所有 `PUT` 请求都使用： Content-Type: application/json

## 健康检查与运行环境

### GET `/ready`
检查本地服务进程是否已就绪。

响应示例：
{
  "ready": true
}

### GET `/healthz`
检查服务健康状态和传输方式。

响应示例：
{
  "ok": true,
  "service": "helix mcp",
  "transport": "streamable-http"
}

### GET `/version` 
读取服务版本信息。

响应示例：
{
  "ok": true,
  "service": "helix mcp",
  "version": "1.0.0"
}

### GET `/api/runtime/exec-env`
读取本地服务进程上报的执行环境信息。

主要响应字段：
{
  "ok": true,
  "data": {
    "platform": {},
    "shell": {},
    "runtimes": {},
    "tools": {},
    "env": {},
    "workspace": {},
    "service_manifest": {
      "available": true,
      "path": ".../web/service.md",
      "sha256": "",
      "content": "",
      "truncated": false
    }
  }
}

## 空闲状态与日志

### GET `/api/idle`
查看当前活跃任务、会话数量和空闲时间。

主要响应字段：
{
  "ttl_sec": 1800.0,
  "active_runtime_jobs": 0,
  "active_sessions": 0,
  "active_total": 0,
  "idle_sec": 12.3,
  "jobs": [],
  "sessions": {}
}

### GET `/api/keepalive`
刷新服务活跃时间，避免服务因空闲而关闭。

主要响应字段：
{
  "ok": true,
  "service": "helix keepalive",
  "ttl_sec": 1800.0,
  "idle_sec": 0.0,
  "keepalive_sec": 300.0,
  "active_total": 0
}

### GET `/api/logs`
读取最近的服务日志。

主要响应字段：
{
  "ok": true,
  "data": {
    "exists": true,
    "path": "path/to/helix.log",
    "lines": ["..."],
    "line_count": 120,
    "truncated": false,
    "size": 4096
  }
}

## 模型偏好配置

### GET `/api/pref`
读取模型偏好配置。

主要响应字段：
{
  "ok": true,
  "data": {
    "schema_version": 2,
    "profile_key": "default",
    "primary": {
      "api": "OpenAI",
      "base_url": "",
      "route": "responses",
      "apikey": "",
      "model": "",
      "type": "Text",
      "notes": ""
    },
    "secondary": null
  }
}

### PUT `/api/pref`
保存模型偏好配置。

请求体：
{
  "primary": {
    "api": "OpenAI",
    "base_url": "https://api.openai.com/v1",
    "route": "responses",
    "apikey": "sk-...",
    "model": "gpt-4.1",
    "type": "Text",
    "notes": ""
  },
  "secondary": null
}

说明：
`route` 支持 `responses` 和 `chat_completions`。
`secondary` 可以为 `null`。
缺失字段会被自动补齐为默认值。
响应结构与 `GET /api/pref` 一致。

## 远程服务配置

### GET `/api/service-config`
读取远程服务域名配置。

主要响应字段：
{
  "ok": true,
  "data": {
    "schema_version": 1,
    "domain": "https://example.com",
    "configured": true
  }
}

### PUT `/api/service-config`
保存远程服务域名配置。

请求体：
{
  "domain": "https://example.com"
}

## Agent 示例状态

### GET `/api/agent`

用途：读取本地 Agent 示例状态。

主要响应字段：
{
  "ok": true,
  "data": {
    "session_id": "",
    "credential": "",
    "credential_tail": "",
    "has_credential": false,
    "mind_call": null,
    "updated_at_ms": 0
  }
}

### PUT `/api/agent`
保存本地 Agent 示例状态。

请求体：
{
  "session_id": "session-xxx",
  "credential": "secret-token",
  "mind_call": {
    "name": "tool_name",
    "arguments": {}
  }
}

说明：
`session_id` 和 `credential` 均为字符串。
`mind_call` 可以是对象，也可以是 `null`。
响应会返回 `credential_tail` 和 `has_credential`。
响应结构与 `GET /api/agent` 一致。
