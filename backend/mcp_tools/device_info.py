#  ____             _            ___        __
# |  _ \  _____   _(_) ___ ___  |_ _|_ __  / _| ___
# | | | |/ _ \ \ / / |/ __/ _ \  | || '_ \| |_ / _ \
# | |_| |  __/\ V /| | (_|  __/  | || | | |  _| (_) |
# |____/ \___| \_/ |_|\___\___| |___|_| |_|_|  \___/
#

import asyncio
from loguru import logger
from mcp.server import FastMCP
from backend.middlewares.mid_task import task_middleware
from engine.manage import DeviceManage


def bind(mcp: FastMCP, manage: DeviceManage) -> None:

    @mcp.tool()
    @task_middleware("get_serial")
    async def get_serial() -> None:
        pass

    @mcp.tool()
    @task_middleware("get_model")
    async def get_model() -> None:
        pass

    @mcp.tool()
    @task_middleware("get_brand")
    async def get_brand() -> None:
        pass

    @mcp.tool()
    @task_middleware("get_sdk")
    async def get_sdk() -> None:
        pass

    @mcp.tool()
    @task_middleware("get_resolution")
    async def get_resolution() -> None:
        pass

    @mcp.tool()
    @task_middleware("get_battery")
    async def get_battery() -> None:
        pass

    @mcp.tool()
    @task_middleware("get_network")
    async def get_network() -> None:
        pass

    @mcp.tool()
    @task_middleware("get_orientation")
    async def get_orientation() -> None:
        pass

    @mcp.tool()
    @task_middleware("get_language")
    async def get_language() -> None:
        pass


if __name__ == '__main__':
    pass

