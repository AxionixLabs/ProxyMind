# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import time
import base64
import typing
import asyncio
import smtplib
from email.message import EmailMessage
from backend.mcp_hub.hub_nexus.infra.core import ClockService
from backend.mcp_hub.hub_nexus.infra.result import ExecutorResultService
from backend.mcp_hub.hub_nexus.infra.pack_builder import PackBuilder


class SmtpExecutor(object):

    @staticmethod
    async def execute(
        *,
        host: str,
        port: int,
        action: str = "noop",
        username: typing.Optional[str] = None,
        password: typing.Optional[str] = None,
        use_ssl: bool = False,
        use_tls: bool = False,
        from_addr: typing.Optional[str] = None,
        to_addrs: typing.Optional[list[str]] = None,
        subject: typing.Optional[str] = None,
        body_text: typing.Optional[str] = None,
        html_body: typing.Optional[str] = None,
        attachments: typing.Optional[list[dict[str, typing.Any]]] = None,
        timeout: float = 15.0,
        extract: typing.Optional[dict[str, str]] = None,
        asserts: typing.Optional[list[dict[str, typing.Any]]] = None,
        step_artifact_dir: typing.Optional[str] = None
    ) -> dict[str, typing.Any]:
        """执行 SMTP 探测或发信动作，并返回统一结果结构。"""
        t0 = time.perf_counter()
        last_err: typing.Optional[str] = None
        result_data: dict[str, typing.Any] = {}
        ok = False

        # 不做任何“执行中落盘”
        _ = step_artifact_dir

        request_data = PackBuilder.build_request_smtp(
            host=host,
            port=port,
            action=action,
            username=username,
            use_ssl=use_ssl,
            use_tls=use_tls,
            from_addr=from_addr,
            to_addrs=to_addrs,
            subject=subject,
            body_text=body_text,
            html_body=html_body,
            attachments=attachments,
            timeout=timeout
        )

        def _attachment_payload(item: dict[str, typing.Any]) -> tuple[bytes, str, str]:
            content_type = str(item.get("content_type") or "application/octet-stream")
            maintype, subtype = (content_type.split("/", 1) + ["octet-stream"])[:2]

            if item.get("path"):
                with open(str(item["path"]), "rb") as fh:
                    payload = fh.read()
                return payload, maintype, subtype
            if item.get("base64") is not None:
                return base64.b64decode(str(item.get("base64") or ""), validate=False), maintype, subtype
            if item.get("text") is not None:
                return str(item.get("text") or "").encode("utf-8"), maintype, subtype
            return b"", maintype, subtype

        def _smtp_call() -> dict[str, typing.Any]:
            action_norm = str(action or "noop").strip().lower()
            if action_norm not in {"noop", "send"}:
                raise ValueError(f"unsupported smtp action: {action}")

            smtp_cls = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
            with smtp_cls(host, int(port), timeout=float(timeout)) as client:
                code, message = client.ehlo()
                result: dict[str, typing.Any] = {
                    "action": action_norm,
                    "ehlo": {
                        "code"    : code,
                        "message" : message.decode(errors="replace")
                    }
                }
                if use_tls and not use_ssl:
                    starttls_resp = client.starttls()
                    result["starttls"] = {
                        "code"    : starttls_resp[0],
                        "message" : starttls_resp[1].decode(errors="replace"),
                    }
                    code, message = client.ehlo()
                    result["ehlo_after_tls"] = {
                        "code"    : code,
                        "message" : message.decode(errors="replace")
                    }

                if username:
                    login_resp = client.login(username, password or "")
                    result["login"] = {
                        "code"    : login_resp[0],
                        "message" : login_resp[1].decode(errors="replace")
                    }

                if action_norm == "send":
                    msg = EmailMessage()
                    msg["From"] = from_addr or username or ""
                    msg["To"] = ", ".join(to_addrs or [])
                    msg["Subject"] = subject or ""
                    msg.set_content(body_text or "")
                    if html_body:
                        msg.add_alternative(html_body, subtype="html")
                    added_attachments: list[dict[str, typing.Any]] = []
                    for item in (attachments or []):
                        if not isinstance(item, dict):
                            continue
                        payload, maintype, subtype = _attachment_payload(item)
                        filename = str(item.get("filename") or "attachment.bin")
                        msg.add_attachment(payload, maintype=maintype, subtype=subtype, filename=filename)
                        added_attachments.append(
                            {
                                "filename"     : filename,
                                "content_type" : f"{maintype}/{subtype}",
                                "size"         : len(payload)
                            }
                        )
                    send_resp = client.send_message(msg)
                    result["send"] = {
                        "accepted": len(to_addrs or []) - len(send_resp),
                        "rejected": send_resp,
                    }
                    if html_body:
                        result["html"] = {"enabled": True}
                    if added_attachments:
                        result["attachments"] = added_attachments
                else:
                    noop_resp = client.noop()
                    result["noop"] = {
                        "code"    : noop_resp[0],
                        "message" : noop_resp[1].decode(errors="replace")
                    }
                return result

        try:
            result_data = await asyncio.to_thread(_smtp_call)
            ok = True
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"

        elapsed_ms = ClockService.ms_since(t0)
        return ExecutorResultService.finalize_pack(
            text=f"SMTP {action} {host}:{port} ({elapsed_ms}ms)",
            ok=ok,
            request=request_data,
            response=PackBuilder.build_response_smtp(
                elapsed_ms=elapsed_ms,
                result=result_data
            ),
            extract=extract,
            asserts=asserts,
            error=last_err
        )


if __name__ == '__main__':
    pass
