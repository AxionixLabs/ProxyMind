#  _   _      _
# | | | | ___| |_ __   ___ _ __ ___
# | |_| |/ _ \ | '_ \ / _ \ '__/ __|
# |  _  |  __/ | |_) |  __/ |  \__ \
# |_| |_|\___|_| .__/ \___|_|  |___/
#              |_|
#

import asyncio
from loguru import logger
from mcp.server import FastMCP
from backend.mcp_core.middleware import exception_middleware


def bind(mcp: FastMCP) -> None:

    @mcp.tool()
    @exception_middleware("sleep")
    async def sleep(delay: float) -> None:
        """
        等待指定秒数。

        参数：
        - delay: 等待时间（秒，可为小数）

        行为：
        - 当前任务仅按时间延迟，不检测页面状态、不判断加载完成

        返回：
        - 无返回值

        示例：
        sleep(1.5)

        Agent 使用语义：
        仅用于插入固定时间延迟，不能保证页面已加载完成。
        不应作为页面状态判断手段使用。

        约束：
        - sleep 只是时间等待，不代表页面就绪
        - 后续操作仍可能失败，需要结合点击结果或页面验证
        """

        logger.info(f"Wait {delay}")
        return await asyncio.sleep(delay)


if __name__ == '__main__':
    pass
