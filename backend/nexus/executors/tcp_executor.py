#  _____ ____ ____    _____                _
# |_   _/ ___|  _ \  | ____|_  _____ _   _| |_ ___  _ __
#   | || |   | |_) | |  _| \ \/ / __| | | | __/ _ \| '__|
#   | || |___|  __/  | |___ >  < (__| |_| | || (_) | |
#   |_| \____|_|     |_____/_/\_\___|\__,_|\__\___/|_|
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import time
import typing
import asyncio
import contextlib
from backend.nexus.infra.core import ClockService
from backend.nexus.infra.result import ExecutorResultService
from backend.nexus.infra.pack_builder import PackBuilder


class TcpExecutor(object):

    @staticmethod
    async def execute(
        *,
        host: str,
        port: int,
        body_text: typing.Optional[str] = None,
        sends: typing.Optional[list[str]] = None,
        encoding: str = "utf-8",
        timeout: float = 10.0,
        read_size: int = 4096,
        close_write: bool = True,
        max_reads: int = 1,
        read_until: typing.Optional[str] = None,
        extract: typing.Optional[dict[str, str]] = None,
        asserts: typing.Optional[list[dict[str, typing.Any]]] = None,
        step_artifact_dir: typing.Optional[str] = None
    ) -> dict[str, typing.Any]:
        """执行单次 TCP 连接、发送数据并读取响应。"""
        t0 = time.perf_counter()
        last_err: typing.Optional[str] = None
        response_text: typing.Optional[str] = None
        response_bytes = b""
        response_messages: list[str] = []
        ok = False

        # 不做任何“执行中落盘”
        _ = step_artifact_dir

        send_items = [str(item) for item in (sends or [])]
        if body_text is not None and not send_items:
            send_items = [body_text]

        request_data = PackBuilder.build_request_tcp(
            host=host,
            port=port,
            body_text=body_text,
            sends=send_items,
            encoding=encoding,
            timeout=timeout,
            read_size=read_size,
            close_write=close_write,
            max_reads=max_reads,
            read_until=read_until
        )

        writer = None
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, int(port)),
                timeout=float(timeout),
            )
            for item in send_items:
                writer.write(item.encode(encoding))
                await writer.drain()

            if close_write and writer.can_write_eof():
                writer.write_eof()

            if read_until:
                raw = await asyncio.wait_for(reader.readuntil(read_until.encode(encoding)), timeout=float(timeout))
                response_bytes = bytes(raw)
                response_messages = [response_bytes.decode(encoding, errors="replace")]
            else:
                for _ in range(max(1, int(max_reads))):
                    raw = await asyncio.wait_for(reader.read(int(read_size)), timeout=float(timeout))
                    if not raw: break
                    response_messages.append(raw.decode(encoding, errors="replace"))
                    response_bytes += raw

            response_text = "".join(
                response_messages
            ) if response_messages else response_bytes.decode(encoding, errors="replace")
            ok = True
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
        finally:
            if writer is not None:
                writer.close()
                with contextlib.suppress(Exception):
                    await writer.wait_closed()

        elapsed_ms = ClockService.ms_since(t0)
        return ExecutorResultService.finalize_pack(
            text=f"TCP {host}:{port} ({elapsed_ms}ms)",
            ok=ok,
            request=request_data,
            response=PackBuilder.build_response_tcp(
                elapsed_ms=elapsed_ms,
                body_text=response_text,
                content_length=len(response_bytes),
                body_hex=response_bytes.hex(),
                messages=response_messages,
                remote={"host": host, "port": int(port)}
            ),
            extract=extract,
            asserts=asserts,
            error=last_err
        )


if __name__ == '__main__':
    pass
