#     _             _ _
#    / \  _   _  __| (_) ___
#   / _ \| | | |/ _` | |/ _ \
#  / ___ \ |_| | (_| | | (_) |
# /_/   \_\__,_|\__,_|_|\___/
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

from mcp.server import FastMCP
from mcp.types import CallToolResult
from backend.middlewares.mid_task import task_middleware
from backend.utilities.instance import Ins
from backend.utilities.pipeline import Idle
from backend.utilities.toolbox import broadcast


def bind(mcp: FastMCP, idle: Idle) -> None:

    @mcp.tool(meta={"hidden": False, "domain": "media", "class": "audio"})
    @task_middleware("audio_play")
    async def audio_play(audio_file: str, volume: float = 1.0) -> CallToolResult:
        """
        D: media
        C: audio
        A: audio_play
        P:
          audio_file: str
          volume: float=1.0
        R: CTR
        N:
          - 本机播放指定音频文件（volume 为播放音量系数）
          - 文件不存在/格式不支持/解码失败会报错
        """

        args = {
            "audio_file" : audio_file,
            "volume"     : volume
        }

        async def call(*_) -> None:
            job_id = await idle.job_begin(f"{Ins.player.agent_id}.audio_play", args=args)
            try:
                return await Ins.player.audio_play(**args)
            finally:
                await idle.job_final(job_id)

        return await broadcast(
            tool="audio_play",
            args=args,
            target_list=[Ins.player],
            call=call,
            overrides=None
        )


if __name__ == '__main__':
    pass
