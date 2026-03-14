#  _   _ ____  ____    _____                _
# | | | |  _ \|  _ \  | ____|_  _____ _   _| |_ ___  _ __
# | | | | | | | |_) | |  _| \ \/ / __| | | | __/ _ \| '__|
# | |_| | |_| |  __/  | |___ >  < (__| |_| | || (_) | |
#  \___/|____/|_|     |_____/_/\_\___|\__,_|\__\___/|_|
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import time
import socket
import typing
import asyncio
from backend.nexus.infra.core import ClockService
from backend.nexus.infra.result import ExecutorResultService
from backend.nexus.infra.pack_builder import PackBuilder


class UdpExecutor(object):

    @staticmethod
    async def execute(
        *,
        host: str,
        port: int,
        body_text: str = "",
        encoding: str = "utf-8",
        timeout: float = 10.0,
        read_size: int = 4096,
        extract: typing.Optional[dict[str, str]] = None,
        asserts: typing.Optional[list[dict[str, typing.Any]]] = None,
        step_artifact_dir: typing.Optional[str] = None
    ) -> dict[str, typing.Any]:
        """执行单次 UDP 收发，并返回统一结果结构。"""
        t0 = time.perf_counter()
        last_err: typing.Optional[str] = None
        response_text: typing.Optional[str] = None
        response_bytes = b""
        remote: dict[str, typing.Any] = {"host": host, "port": int(port)}
        ok = False

        # 不做任何“执行中落盘”
        _ = step_artifact_dir

        def _exchange() -> tuple[bytes, tuple[str, int]]:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sock.settimeout(float(timeout))
                payload = (body_text or "").encode(encoding)
                sock.sendto(payload, (host, int(port)))
                data, addr = sock.recvfrom(int(read_size))
                return data, addr
            finally:
                sock.close()

        request_data = PackBuilder.build_request_udp(
            host=host,
            port=port,
            body_text=body_text,
            encoding=encoding,
            timeout=timeout,
            read_size=read_size
        )

        try:
            response_bytes, addr = await asyncio.to_thread(_exchange)
            remote = {"host": addr[0], "port": int(addr[1])}
            response_text = response_bytes.decode(encoding, errors="replace")
            ok = True
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"

        elapsed_ms = ClockService.ms_since(t0)
        return ExecutorResultService.finalize_pack(
            text=f"UDP {host}:{port} ({elapsed_ms}ms)",
            ok=ok,
            request=request_data,
            response=PackBuilder.build_response_udp(
                elapsed_ms=elapsed_ms,
                body_text=response_text,
                content_length=len(response_bytes),
                body_hex=response_bytes.hex(),
                remote=remote
            ),
            extract=extract,
            asserts=asserts,
            error=last_err
        )


if __name__ == '__main__':
    pass
