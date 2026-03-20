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
          - 在当前运行环境本机播放一个音频文件。
          - 该工具只负责本地播放，不会把音频推送到设备，也不会生成新媒体文件。
          - 文件不存在、格式不支持或解码失败时会失败。
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
