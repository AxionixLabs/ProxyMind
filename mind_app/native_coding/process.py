# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio
from loguru import logger
from mind_app.native_coding.trace import (
    clip_text,
    summarize_command
)


class Flux(object):
    """进程执行辅助对象。"""

    @staticmethod
    async def cmd_link_shell_exec(
        cmd: str,
        *,
        shell: typing.Optional[list[str]] = None,
        cwd: typing.Optional[str] = None,
        env: typing.Optional[dict[str, str]] = None,
        stdin: typing.Any = None
    ) -> asyncio.subprocess.Process:
        """使用执行选项启动 shell 字符串命令。"""
        prefix = [str(item) for item in (shell or []) if str(item or "").strip()]
        if prefix:
            process = await asyncio.create_subprocess_exec(
                *prefix,
                cmd,
                cwd=cwd or None,
                env=env,
                stdin=stdin,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
        else:
            process = await asyncio.create_subprocess_shell(
                cmd,
                cwd=cwd or None,
                env=env,
                stdin=stdin,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

        logger.debug(
            f"process link mode=shell pid={process.pid} cwd={clip_text(cwd or '', 120)} "
            f"cmd={summarize_command(cmd)}"
        )
        return process


if __name__ == '__main__':
    pass

