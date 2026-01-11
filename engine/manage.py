#  __  __
# |  \/  | __ _ _ __   __ _  __ _  ___
# | |\/| |/ _` | '_ \ / _` |/ _` |/ _ \
# | |  | | (_| | | | | (_| | (_| |  __/
# |_|  |_|\__,_|_| |_|\__,_|\__, |\___|
#                           |___/
#

import sys
import typing
import asyncio
from pathlib import Path
from loguru import logger
from engine.terminal import Terminal
from utils import const


class ServerManage(object):

    def __init__(self, program: Path | str):
        self.program = str(program)
        self.transports: typing.Optional[asyncio.subprocess.Process] = None

    async def input_stream(self) -> None:
        async for line in self.transports.stdout:
            logger.debug(line.decode(const.CHARSET, const.IGNORE).strip())

    async def error_stream(self) -> None:
        async for line in self.transports.stderr:
            logger.debug(line.decode(const.CHARSET, const.IGNORE).strip())

    async def mcp_begin(self) -> None:
        if self.transports and self.transports.returncode is None:
            return None

        cmd = [sys.executable, self.program]  # todo
        self.transports = await Terminal.cmd_link(cmd)

        asyncio.create_task(self.input_stream())
        asyncio.create_task(self.error_stream())

        await asyncio.sleep(1)

        logger.info(f"Ⓜ️ {const.APP_DESC} MCP started ...")

    async def mcp_final(self) -> None:
        if not self.transports or self.transports.returncode is not None:
            return None

        logger.info(f"☣️ {const.APP_DESC} MCP Stopping ...")

        self.transports.terminate()
        try:
            await asyncio.wait_for(self.transports.wait(), timeout=5)
        except asyncio.TimeoutError:
            self.transports.kill()

        logger.info(f"♻️ {const.APP_DESC} MCP stopped ...")


if __name__ == '__main__':
    pass
