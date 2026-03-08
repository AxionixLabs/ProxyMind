#   ____            __ _
#  / ___|_ __ __ _ / _| |_
# | |   | '__/ _` | |_| __|
# | |___| | | (_| |  _| |_
#  \____|_|  \__,_|_|  \__|
#

import sys
import time
import uuid
import base64
import typing
import shutil
import asyncio
from engine.terminal import Terminal


async def port_listen(port: int, *, host: str = "127.0.0.1") -> bool:
    try:
        server = await asyncio.start_server(lambda r, w: None, host=host, port=port)
    except OSError:
        return False  # 已占用 / 无权限 / 不可绑定
    else:
        server.close()
        await server.wait_closed()
        return True   # 可绑定


async def kill_port(port: int) -> typing.Any:
    if sys.platform == "win32":
        pwsh = shutil.which("pwsh") or shutil.which("powershell")
        if not pwsh: return None
        cmd = [
            pwsh, "-Command", "Get-NetTCPConnection", "-LocalPort", f"{port}",
            "-ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }"
        ]
        return await Terminal.cmd_line(cmd)

    else:
        cmd = f"lsof -tiTCP:{port} -sTCP:LISTEN | xargs -r kill -9"
        return await Terminal.cmd_line_shell(cmd)


def b36(n: int) -> str:
    chars = "0123456789abcdefghijklmnopqrstuvwxyz"
    if n <= 0: return "0"
    s = []
    while n:
        n, r = divmod(n, 36)
        s.append(chars[r])
    return "".join(reversed(s))


def new_cid(prefix: str = "cid") -> str:
    # 秒级时间 + 8 位随机：cid_kr3f2n_1a2b3c4d
    ts   = b36(int(time.time()))
    rand = uuid.uuid4().hex[:8]
    return f"{prefix}_{ts}_{rand}"


def new_sid(cid: str, prefix: str = "sid") -> str:
    # 用 cid 做关联，sid 带毫秒 + 6 位随机：sid_kr3f2n_m3ab9e_7f2c1a
    ts_ms = b36(int(time.time() * 1000))
    rand = uuid.uuid4().hex[:6]
    return f"{prefix}_{cid.split('_', 2)[1]}_{ts_ms}_{rand}"


def short_uid(length: int = 8) -> str:
    raw = uuid.uuid4().bytes
    return (
        base64.b32encode(raw)
        .decode("utf-8")
        .rstrip("=")
        .lower()[:length]
    )


if __name__ == '__main__':
    pass
