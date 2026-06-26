# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import uuid
import httpx
import typing
import mimetypes
from pathlib import Path
from engine.channel import Channel
from mind_nova.services import service_endpoints
from mind_nova import const


async def upload_file_stream(
    path: str,
    agent_id: str,
    prefix: str = "uploads",
    timeout: float = 60.0,
    progress_callback: typing.Optional[
        typing.Callable[
            [dict[str, typing.Any]], typing.Awaitable[None]
        ]
    ] = None,
    chunk_size: int = 64 * 1024
) -> dict[str, typing.Any]:
    """流式上传本地文件到服务端。"""

    def upload_progress_payload(
        current_uploaded_bytes: int,
        current_total_bytes: int,
        progress_started_at: float,
        *,
        done: bool,
        phase: str = "uploading"
    ) -> dict[str, typing.Any]:

        elapsed_sec = max(0.0, time.monotonic() - progress_started_at)
        speed       = (float(current_uploaded_bytes) / elapsed_sec) if elapsed_sec > 0 else 0.0

        percent = 1.0 if current_total_bytes <= 0 and done else (
            min(1.0, float(current_uploaded_bytes) / float(current_total_bytes)) if current_total_bytes > 0 else 0.0
        )

        return {
            "uploaded_bytes"      : int(current_uploaded_bytes),
            "total_bytes"         : int(current_total_bytes),
            "percent"             : percent,
            "elapsed_sec"         : elapsed_sec,
            "speed_bytes_per_sec" : speed,
            "phase"               : phase,
            "done"                : done
        }

    def multipart_field(
        part_boundary: str,
        name: str,
        value: str
    ) -> bytes:
        return (
            f"--{part_boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n"
        ).encode(const.CHARSET)

    def multipart_file_header(
        part_boundary: str,
        name: str,
        filename: str,
        content_type: str
    ) -> bytes:
        return (
            f"--{part_boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode(const.CHARSET)

    def multipart_closing(
        part_boundary: str
    ) -> bytes:
        return f"\r\n--{part_boundary}--\r\n".encode(const.CHARSET)

    async def body() -> typing.AsyncGenerator[bytes, None]:
        yield field_agent
        yield field_prefix
        yield field_file

        with p.open("rb") as f:
            while True:
                chunk = f.read(max(1, int(chunk_size)))
                if not chunk:
                    break

                yield chunk
                upload_state["uploaded_bytes"] += len(chunk)

                if progress_callback is not None:
                    await progress_callback(
                        upload_progress_payload(
                            upload_state["uploaded_bytes"], file_size, started_at, done=False, phase="uploading"
                        )
                    )

        yield closing
        upload_state["uploaded_bytes"] = file_size

        if progress_callback is not None:
            await progress_callback(
                upload_progress_payload(file_size, file_size, started_at, done=False, phase="processing")
            )

    if not (p := Path(path).expanduser()).exists() or not p.is_file():
        raise RuntimeError(f"upload_file_stream: file not exists: {p}")

    headers = Channel.make_headers()
    headers.pop("Content-Type", None)

    ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"

    boundary     = uuid.uuid4().hex
    field_agent  = multipart_field(boundary, "agent_id", agent_id)
    field_prefix = multipart_field(boundary, "prefix", prefix)
    field_file   = multipart_file_header(boundary, "file", p.name, ctype)
    closing      = multipart_closing(boundary)

    file_size = int(p.stat().st_size)

    headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    headers["Content-Length"] = str(
        len(field_agent) + len(field_prefix) + len(field_file) + file_size + len(closing)
    )

    started_at   = time.monotonic()
    upload_state = {"uploaded_bytes": 0}

    if progress_callback is not None:
        await progress_callback(
            upload_progress_payload(upload_state["uploaded_bytes"], file_size, started_at, done=False, phase="uploading")
        )

    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(service_endpoints.endpoint("/upload"), headers=headers, content=body())
        r.raise_for_status()

        if progress_callback is not None:
            await progress_callback(upload_progress_payload(file_size, file_size, started_at, done=True, phase="done"))

        return r.json()


if __name__ == '__main__':
    pass
