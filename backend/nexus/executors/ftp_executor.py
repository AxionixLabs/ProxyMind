#  _____ _____ ____    _____                _
# |  ___|_   _|  _ \  | ____|_  _____ _   _| |_ ___  _ __
# | |_    | | | |_) | |  _| \ \/ / __| | | | __/ _ \| '__|
# |  _|   | | |  __/  | |___ >  < (__| |_| | || (_) | |
# |_|     |_| |_|     |_____/_/\_\___|\__,_|\__\___/|_|
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import io
import time
import base64
import ftplib
import mimetypes
import typing
import asyncio
from backend.nexus.infra.core import ClockService
from backend.nexus.infra.result import ExecutorResultService
from backend.nexus.infra.pack_builder import PackBuilder


class FtpExecutor(object):

    @staticmethod
    async def execute(
        *,
        host: str,
        port: int,
        username: str = "anonymous",
        password: str = "anonymous@",
        action: str = "list",
        path: str = ".",
        payload_text: typing.Optional[str] = None,
        payload_base64: typing.Optional[str] = None,
        encoding: str = "utf-8",
        use_tls: bool = False,
        timeout: float = 15.0,
        media_path: typing.Optional[str] = None,
        extract: typing.Optional[dict[str, str]] = None,
        asserts: typing.Optional[list[dict[str, typing.Any]]] = None,
        step_artifact_dir: typing.Optional[str] = None
    ) -> dict[str, typing.Any]:
        """执行 FTP 列表、上传、下载或目录操作，并返回统一结果结构。"""
        t0 = time.perf_counter()
        last_err: typing.Optional[str] = None
        result_data: dict[str, typing.Any] = {}
        ok = False

        request_data = PackBuilder.build_request_ftp(
            host=host,
            port=port,
            username=username,
            action=action,
            path=path,
            payload_text=payload_text,
            payload_base64=payload_base64,
            encoding=encoding,
            use_tls=use_tls,
            timeout=timeout,
            media_path=media_path
        )

        def _ftp_call() -> dict[str, typing.Any]:
            ftp_cls = ftplib.FTP_TLS if use_tls else ftplib.FTP
            with ftp_cls() as client:
                client.connect(host, int(port), timeout=float(timeout))
                client.login(username, password)
                if use_tls and isinstance(client, ftplib.FTP_TLS):
                    client.prot_p()

                action_norm = str(action or "list").lower()
                result: dict[str, typing.Any] = {"welcome": client.getwelcome()}
                if action_norm == "download_text":
                    out = io.BytesIO()
                    client.retrbinary(f"RETR {path}", out.write)
                    result["download_text"] = out.getvalue().decode(encoding, errors="replace")

                elif action_norm == "download_binary":
                    out = io.BytesIO()
                    client.retrbinary(f"RETR {path}", out.write)
                    payload = out.getvalue()
                    mime_type, _ = mimetypes.guess_type(path)
                    result["download_binary"] = {
                        "path"      : path,
                        "filename"  : path.rsplit("/", 1)[-1],
                        "mime_type" : mime_type,
                        "base64"    : base64.b64encode(payload).decode("ascii"),
                        "size"      : len(payload)
                    }

                elif action_norm == "upload_text":
                    bio  = io.BytesIO((payload_text or "").encode(encoding))
                    resp = client.storbinary(f"STOR {path}", bio)
                    result["upload_text"] = {"reply": resp}

                elif action_norm == "upload_binary":
                    payload = base64.b64decode(str(payload_base64 or ""), validate=False)
                    bio     = io.BytesIO(payload)
                    resp    = client.storbinary(f"STOR {path}", bio)
                    result["upload_binary"] = {"reply": resp, "size": len(payload)}

                elif action_norm == "delete":
                    result["delete"] = {"reply": client.delete(path)}

                elif action_norm == "mkdir":
                    result["mkdir"] = {"reply": client.mkd(path)}

                else:
                    lines: list[str] = []
                    client.retrlines(f"LIST {path}", lines.append)
                    result["list"] = lines

                return result

        try:
            result_data = await asyncio.to_thread(_ftp_call)
            ok = True
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"

        elapsed_ms = ClockService.ms_since(t0)
        media_list: list[dict[str, typing.Any]] = []
        attachments: list[dict[str, typing.Any]] = []
        media_logs: list[str] = []
        if ok:
            media_list, attachments, media_logs = await ExecutorResultService.collect_media(
                source_kind="json_body",
                source=result_data,
                media_path=media_path,
                tool="ftp_media",
                step_artifact_dir=step_artifact_dir,
                timeout=timeout
            )
        return ExecutorResultService.finalize_pack(
            text=f"FTP {action} {host}:{port} {path} ({elapsed_ms}ms)",
            ok=ok,
            request=request_data,
            response=PackBuilder.build_response_ftp(
                elapsed_ms=elapsed_ms,
                result=result_data,
                media=media_list
            ),
            extract=extract,
            asserts=asserts,
            attachments=attachments,
            logs=media_logs,
            error=last_err
        )


if __name__ == '__main__':
    pass
