---
name: memrix-docs
description: Memrix CLI 文档，用于 Android 应用内存和 I/O 采样、Perfetto 帧率流畅度分析、性能评分、HTML 报告生成、设备选择和 Memrix 命令生成。
---

# Memrix Docs

这个 skill 提供 Memrix CLI 的使用文档。`SKILL.md` 是精简入口；详细说明放在 `references/*.md`。

## Routing

| 问题 | 先读什么 |
| --- | --- |
| 了解 Memrix 能力、适用场景或产物 | [Memrix 概览](references/overview.md) |
| 生成单条 Memrix 命令或查询参数 | [Memrix 常用命令](references/commands.md) |
| 处理采集、报告、评分或排障流程 | [Memrix 典型流程](references/workflows.md) |

## Core Rules

- 先确认测试目标是应用包名、设备序列号、Perfetto trace 文件、输出目录还是已有报告目录。
- 需要测试 Android 应用时，必须提供 `--focus <包名>`。
- 多设备场景必须补充 `--imply <设备序列号>`。
- 需要自动生成报告时，优先追加 `--atlas`。
- 需要按规则评分时，追加 `--align`。
- 除授权激活 `memrix --apply ...` 外，Memrix 示例命令必须包含 `--debug`，否则日志可能被动画输出吞掉。
- 不要编造包名、设备序列号或本地路径。
