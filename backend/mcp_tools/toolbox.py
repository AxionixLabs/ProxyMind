#  _____           _ _
# |_   _|__   ___ | | |__   _____  __
#   | |/ _ \ / _ \| | '_ \ / _ \ \/ /
#   | | (_) | (_) | | |_) | (_) >  <
#   |_|\___/ \___/|_|_.__/ \___/_/\_\
#

import asyncio
from loguru import logger
from mcp.server import FastMCP
from backend.middlewares.mid_task import task_middleware


def bind(mcp: FastMCP) -> None:

    @mcp.tool()
    @task_middleware("sleep")
    async def sleep(delay: float) -> None:
        """Class: flow; Action: 固定等待; Args: delay(seconds float); Use: 稳定节奏/等待动画; Return: None; Notes: 仅时间延迟≠页面就绪."""

        logger.info(f"Wait {delay}")
        return await asyncio.sleep(delay)


if __name__ == '__main__':
    pass
