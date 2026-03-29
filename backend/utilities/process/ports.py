# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import sys
import shutil
import typing
import asyncio
from engine.terminal import Terminal


async def port_listen(port: int, *, host: str = "127.0.0.1") -> bool:
    """探测指定地址端口是否仍可成功绑定。"""
    try:
        server = await asyncio.start_server(lambda r, w: None, host=host, port=port)
    except OSError:
        return False
    else:
        server.close()
        await server.wait_closed()
        return True


async def kill_port(port: int) -> typing.Any:
    """尝试杀掉当前占用目标端口的监听进程。"""
    if sys.platform == "win32":
        pwsh = shutil.which("pwsh") or shutil.which("powershell")
        if not pwsh:
            return None
        cmd = [
            pwsh, "-Command", "Get-NetTCPConnection", "-LocalPort", f"{port}",
            "-ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }"
        ]
        return await Terminal.cmd_line(cmd)

    cmd = f"lsof -tiTCP:{port} -sTCP:LISTEN | xargs -r kill -9"
    return await Terminal.cmd_line_shell(cmd)


if __name__ == '__main__':
    pass
