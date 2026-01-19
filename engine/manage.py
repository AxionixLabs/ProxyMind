#  __  __
# |  \/  | __ _ _ __   __ _  __ _  ___
# | |\/| |/ _` | '_ \ / _` |/ _` |/ _ \
# | |  | | (_| | | | | (_| | (_| |  __/
# |_|  |_|\__,_|_| |_|\__,_|\__, |\___|
#                           |___/
#

import typing
import asyncio
from loguru import logger
from engine.terminal import Terminal
from engine.tinker import MindError
from mindnova import const


class ServerManage(object):
    """ServerManage class."""

    def __init__(self):
        self.transports: typing.Optional[asyncio.subprocess.Process] = None

    async def input_stream(self) -> None:
        async for line in self.transports.stdout:
            stream = line.decode(const.CHARSET, const.IGNORE).strip()
            logger.debug(" ".join(stream.split()))

    async def error_stream(self) -> None:
        async for line in self.transports.stderr:
            stream = line.decode(const.CHARSET, const.IGNORE).strip()
            logger.debug(" ".join(stream.split()))

    async def mcp_begin(self, cmd: list[str]) -> None:
        if self.transports and self.transports.returncode is None:
            return None

        self.transports = await Terminal.cmd_link(cmd)

        asyncio.create_task(self.input_stream())
        asyncio.create_task(self.error_stream())

        for _ in range(5):
            if self.transports is not None:
                return logger.debug(
                    f"SYNC ▸ {const.APP_DESC} MCP neural core online."
                )
            await asyncio.sleep(1)

        raise MindError(f"Application startup failure")

    async def mcp_final(self) -> None:
        if not self.transports or self.transports.returncode is not None:
            return None

        logger.debug(f"SYNC ▸ {const.APP_DESC} MCP neural core shutting down...")

        self.transports.terminate()
        try:
            await asyncio.wait_for(self.transports.wait(), timeout=5)
        except asyncio.TimeoutError:
            self.transports.kill()

        logger.debug(f"SYNC ▸ {const.APP_DESC} MCP neural core offline.")


if __name__ == '__main__':
    pass
