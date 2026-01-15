#  _____ _ _         ____            _             _
# |  ___(_) | ___   / ___|___  _ __ | |_ _ __ ___ | |
# | |_  | | |/ _ \ | |   / _ \| '_ \| __| '__/ _ \| |
# |  _| | | |  __/ | |__| (_) | | | | |_| | | (_) | |
# |_|   |_|_|\___|  \____\___/|_| |_|\__|_|  \___/|_|
#

import typing
import asyncio
from loguru import logger
from mcp.server import FastMCP
from backend.middlewares.mid_task import task_middleware
from engine.manage import DeviceManage


def bind(mcp: FastMCP, manage: DeviceManage) -> None:

    @mcp.tool()
    @task_middleware("pull")
    async def pull(remote: str, local: str) -> typing.Any:
        """Class: file; Action: adb pull; Args: remote(str), local(str); Use: 拉取设备文件到本机; Return: list[device_result]; Notes: local 建议带目录, 多设备时会写同一路径需注意冲突."""
        device_list = await manage.refresh()

        logger.info(f"Pull remote={remote} -> local={local}")
        return await asyncio.gather(
            *(device.pull(remote, local) for device in device_list), return_exceptions=True
        )

    @mcp.tool()
    @task_middleware("push")
    async def push(local: str, remote: str) -> typing.Any:
        """Class: file; Action: adb push; Args: local(str), remote(str); Use: 推送本机文件到设备; Return: list[device_result]; Notes: remote 需可写权限(如 /sdcard/...)."""
        device_list = await manage.refresh()

        logger.info(f"Push local={local} -> remote={remote}")
        return await asyncio.gather(
            *(device.push(local, remote) for device in device_list), return_exceptions=True
        )

    @mcp.tool()
    @task_middleware("remove")
    async def remove(path: str) -> typing.Any:
        """Class: file; Action: 删除设备端文件(rm -f); Args: path(str); Use: 清理截图/日志/临时文件; Return: list[device_result]; Notes: 仅删除文件(不删目录), 不存在则忽略."""
        device_list = await manage.refresh()

        logger.info(f"Remove {path}")
        return await asyncio.gather(
            *(device.remove(path) for device in device_list), return_exceptions=True
        )


if __name__ == '__main__':
    pass
