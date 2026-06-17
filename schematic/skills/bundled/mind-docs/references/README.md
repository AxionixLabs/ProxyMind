---
name: readme
description: Mind 文档中心入口。
---

# Mind 超级文档中心

## Routing

| 问题 | 先读什么 |
| --- | --- |
| 不确定该用 `--chat` / `--fast` / `--plan` / `--xtra` | [CLI 与运行模式](decisions/cli.md) |
| 不确定能力是本地还是云端 | [云端与本地边界](concepts/builtin-and-hosted.md) |
| 失败后怎么报错和降级 | [错误与回退](contracts/errors-and-fallbacks.md) |
| 写接口验证、回归、压测 | [Nexus 场景](playbooks/nexus.md) |
| 需要稳定的接口样例和断言写法 | [Nexus 示例对象](recipes/nexus-examples.md) |
| 写设备巡检、页面交互、截图日志 | [Device 场景](playbooks/device.md) |
| 做视频处理、抽帧、转码 | [Media 场景](playbooks/media.md) |
| 在云端执行脚本或小项目 | [Sandbox 场景](playbooks/sandbox.md) |
| 分析录屏体感和最慢阶段 | [Framix 场景](playbooks/framix.md) |
| 分析内存、FPS、jank 趋势 | [Memrix 场景](playbooks/memrix.md) |
| 固化成多任务回归材料 | [`--code` 星图写法](contracts/blueprint.md) |

## Core Rules

- 对外只输出 `mind ...` 命令和自然语言任务。
- 不把内部工具名、事件名、MCP 名称当最终接口。
- 一条任务至少写明目标、范围、通过条件、产出。
- 长任务、多轮任务、回归任务优先收束成 `--code`。

## Stable Templates

```bash
mind --fast "对 https://api.example.com/profile 做 GET，请求携带测试 token，校验状态码 200，断言 response.body_json.ok=true，提取 user_id、nickname 和 trace_id，并返回摘要。"
```

## Failure Handling

- 路由不确定时，先回 [CLI 与运行模式](decisions/cli.md)。
- 多步骤任务不要硬塞一行命令，改为 [`--code` 星图](contracts/blueprint.md)。

## Related

- [CLI 与运行模式](decisions/cli.md)
- [云端与本地边界](concepts/builtin-and-hosted.md)
- [错误与回退](contracts/errors-and-fallbacks.md)
- [`--code` 星图写法](contracts/blueprint.md)
- [Nexus 场景](playbooks/nexus.md)
- [Nexus 示例对象](recipes/nexus-examples.md)
- [Framix 场景](playbooks/framix.md)
- [Memrix 场景](playbooks/memrix.md)
