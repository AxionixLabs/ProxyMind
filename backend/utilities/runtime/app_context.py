# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

from backend.mcp_core.core_framix import Framix
from backend.mcp_core.core_memrix import Memrix
from backend.mcp_core.core_nexus import Nexus
from backend.mcp_hub.hub_audio import AudioPlayer


class AppContext(object):

    def __init__(self):
        """初始化全局运行上下文中的服务实例。"""
        self.framix: Framix = Framix()
        self.memrix: Memrix = Memrix()

        self.nexus: Nexus = Nexus()

        self.player: AudioPlayer = AudioPlayer()


app_ctx = AppContext()


if __name__ == '__main__':
    pass
