# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import shutil
import sys
import typing

from infrastructure.platform.terminal import Terminal


async def port_available(port: int, *, host: str = "127.0.0.1") -> bool:
    """判断指定本地端口是否可以绑定。"""
    try:
        server = await asyncio.start_server(
            lambda reader, writer: None,
            host=host,
            port=port,
        )
    except OSError:
        return False

    server.close()
    await server.wait_closed()
    return True


async def terminate_port_process(port: int) -> typing.Any:
    """终止占用指定本地端口的进程。"""
    if sys.platform == "win32":
        powershell = shutil.which("pwsh") or shutil.which("powershell")
        if not powershell:
            return None
        command = [
            powershell,
            "-Command",
            "Get-NetTCPConnection",
            "-LocalPort",
            str(port),
            "-ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }",
        ]
        return await Terminal.cmd_line(command)

    command = f"lsof -tiTCP:{port} -sTCP:LISTEN | xargs -r kill -9"
    return await Terminal.cmd_line_shell(command)


if __name__ == '__main__':
    pass
