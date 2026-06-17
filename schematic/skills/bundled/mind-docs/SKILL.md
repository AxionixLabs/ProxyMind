---
name: mind-docs
description: Mind 任务写法、运行模式、接口验证、设备巡检、媒体处理、性能采样、错误回退和星图协议文档。任务涉及 Mind 能力、CLI 模式、流程质量、接口校验、Android/设备操作、媒体处理、性能分析或失败恢复时使用。
---

# Mind Docs

这个 skill 提供随包内置的 Mind 文档。`SKILL.md` 是精简入口；完整文档放在 `references/*.md`。

## Routing

| 问题 | 先读什么 |
| --- | --- |
| 不确定该用 `--chat` / `--fast` / `--plan` / `--xtra` / `--agent` | [CLI 与运行模式](references/cli.md) |
| 不确定能力是本地还是云端 | [云端与本地边界](references/builtin-and-hosted.md) |
| 失败后怎么报错和降级 | [错误与回退](references/errors-and-fallbacks.md) |
| 写接口验证、回归、压测 | [Nexus 场景](references/nexus.md) |
| 需要稳定的接口样例和断言写法 | [Nexus 示例对象](references/nexus-examples.md) |
| 写设备巡检、页面交互、截图日志 | [Device 场景](references/device.md) |
| 做视频处理、抽帧、转码 | [Media 场景](references/media.md) |
| 在云端执行脚本或小项目 | [Sandbox 场景](references/sandbox.md) |
| 分析录屏体感和最慢阶段 | [Framix 场景](references/framix.md) |
| 分析内存、FPS、jank 趋势 | [Memrix 场景](references/memrix.md) |
| 固化成多任务回归材料 | [`--code` 星图写法](references/blueprint.md) |
| 写代码修改任务 | [Coding 场景](references/coding.md) |
| 写等待、状态检查、摘要、签名或安全辅助任务 | [Common 场景](references/common.md) |
| 检查任务质量或常见反模式 | [高质量任务 Rubric](references/task-quality.md)、[任务写法反模式](references/task-writing.md) |

## Core Rules

- 对外表述尽量使用 `mind ...` 命令和自然语言任务。
- 不把内部工具名、事件名、MCP 名称作为用户侧稳定接口。
- 一条任务尽量写明目标、范围、通过条件、产出。
- 长任务、多轮任务、回归任务优先收束成 `--code`。
- Helix 是 Mind 的本地后台服务，相关 JSON API 文档在 `helix-docs` skill，避免和 Mind 任务写法混淆。
- 不要猜测尚未读取的参考文档细节。依赖某个规则、示例或契约前，先读取对应 Markdown 文件。

## Stable Template

```bash
mind --fast "对 https://api.example.com/profile 做 GET，请求携带测试 token，校验状态码 200，断言 response.body_json.ok=true，提取 user_id、nickname 和 trace_id，并返回摘要。"
```
