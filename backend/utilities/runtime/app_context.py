# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from backend.mcp_core.core_framix import Framix
from backend.mcp_core.core_memrix import Memrix
from backend.mcp_core.core_nexus import Nexus
from backend.mcp_hub.hub_medias import (
    FFmpeg, Player
)
from backend.utilities.state import (
    VideoQueue,
    PathSessionStore,
    ItemSessionStore
)


class AppContext(object):

    def __init__(self):
        """初始化全局运行上下文中的服务实例和共享状态仓库。"""
        self.fx_reports = PathSessionStore("fx_report_session")
        self.mx_reports = ItemSessionStore("mx_report_session")
        self.video_queue = VideoQueue()

        self.framix: Framix = Framix(
            fx_report_store=self.fx_reports
        )
        self.memrix: Memrix = Memrix(
            mx_report_store=self.mx_reports
        )

        self.nexus: Nexus = Nexus()

        self.ffmpeg: FFmpeg = FFmpeg()
        self.player: Player = Player()

    def instance_snapshots(self) -> dict[str, dict[str, typing.Any]]:
        """汇总当前运行上下文中的各类实例快照。"""
        return {
            "instance": {
                **self.video_list_snapshot(),
                **self.fx_report_snapshot(),
                **self.mx_report_snapshot()
            }
        }

    async def video_list_append(self, path: str) -> None:
        """向内部视频队列追加一个待处理文件路径。"""
        await self.video_queue.append(path)

    async def video_list_take_all(self) -> list[str]:
        """原子取出当前视频队列中的全部文件，并清空队列。"""
        return await self.video_queue.take_all()

    def fx_report_snapshot(
        self,
        session_id: typing.Optional[str] = None,
        head_n: int = 5,
        tail_n: int = 5
    ) -> dict[str, typing.Any]:
        """生成 Framix 报告会话状态快照。"""
        return self.fx_reports.snapshot(session_id=session_id, head_n=head_n, tail_n=tail_n)

    def mx_report_snapshot(
        self,
        session_id: typing.Optional[str] = None,
        head_n: int = 5,
        tail_n: int = 5
    ) -> dict[str, typing.Any]:
        """生成 Memrix 报告会话状态快照。"""
        return self.mx_reports.snapshot(session_id=session_id, head_n=head_n, tail_n=tail_n)

    def video_list_snapshot(self) -> dict[str, typing.Any]:
        """生成视频队列当前状态的摘要快照。"""
        return self.video_queue.snapshot()


app_ctx = AppContext()
