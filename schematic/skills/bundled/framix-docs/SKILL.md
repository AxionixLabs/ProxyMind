---
name: framix-docs
description: Framix CLI 文档，用于移动应用录屏帧分析、启动或页面切换耗时判断、关键帧识别、模型训练数据准备、报告合并和 Framix 命令生成。
---

# Framix Docs

这个 skill 提供 Framix CLI 的使用文档。`SKILL.md` 是精简入口；详细说明放在 `references/*.md`。

## Routing

| 问题 | 先读什么 |
| --- | --- |
| 了解 Framix 能力、适用场景或产物 | [Framix 概览](references/overview.md) |
| 生成单条 Framix 命令或查询参数 | [Framix 常用命令](references/commands.md) |
| 处理完整分析、训练、合并流程 | [Framix 典型流程](references/workflows.md) |

## Core Rules

- 先确认输入对象是视频文件、任务 JSON、任务目录、模型目录、报告目录还是资源同步。
- 优先给出当前任务需要的最小可执行命令。
- 路径参数必须加引号，避免空格、中文或特殊字符导致命令解析失败。
- 除授权激活 `framix --apply ...` 外，示例命令必须包含 `--debug`，否则日志可能被动画输出吞掉。
- 不要编造本地路径、模型路径或报告路径。
- 说明结果时覆盖输入、处理方式、主要产物和风险。
