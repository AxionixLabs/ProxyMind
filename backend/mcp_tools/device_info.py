#  ____             _            ___        __
# |  _ \  _____   _(_) ___ ___  |_ _|_ __  / _| ___
# | | | |/ _ \ \ / / |/ __/ _ \  | || '_ \| |_ / _ \
# | |_| |  __/\ V /| | (_|  __/  | || | | |  _| (_) |
# |____/ \___| \_/ |_|\___\___| |___|_| |_|_|  \___/
#

from loguru import logger
from mcp.server import FastMCP
from backend.middlewares.mid_task import task_middleware
from engine.manage import DeviceManage


def bind(mcp: FastMCP, manage: DeviceManage) -> None:

    @mcp.tool()
    @task_middleware("device_info")
    async def device_info() -> list[dict]:
        device_list = await manage.refresh()

        logger.info(f"Get device info")
        return [device.device_info for device in device_list]


if __name__ == '__main__':
    pass

