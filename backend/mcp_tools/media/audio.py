# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from mcp.server import FastMCP
from mcp.types import CallToolResult
from pydantic import Field
from backend.middlewares.mid_task import task_middleware
from backend.utilities.runtime import AppContext, Idle
from backend.utilities.broadcast import broadcast


AudioFileArg = typing.Annotated[
    str,
    Field(description="要在当前运行环境本机播放的音频文件路径。"),
]
VolumeArg = typing.Annotated[
    float,
    Field(description="播放音量倍率，`1.0` 表示原始音量。"),
]


def bind(mcp: FastMCP, idle: Idle, ctx: AppContext) -> None:

    @mcp.tool(
        description=(
            "在当前运行环境本机播放一个音频文件。"
            "该工具只负责本地播放，不会把音频推送到设备，也不会生成新媒体文件。"
            "文件不存在、格式不支持或解码失败时会失败。"
        ),
        meta={"hidden": False, "domain": "media", "class": "audio"}
    )
    @task_middleware("audio_play")
    async def audio_play(audio_file: AudioFileArg, volume: VolumeArg = 1.0) -> CallToolResult:

        args = {
            "audio_file" : audio_file,
            "volume"     : volume
        }

        async def call(*_) -> None:
            job_id = await idle.job_begin(f"{ctx.player.agent_id}.audio_play", args=args)
            try:
                return await ctx.player.audio_play(**args)
            finally:
                await idle.job_final(job_id)

        return await broadcast(
            tool="audio_play",
            args=args,
            target_list=[ctx.player],
            call=call,
            overrides=None
        )


if __name__ == '__main__':
    pass
