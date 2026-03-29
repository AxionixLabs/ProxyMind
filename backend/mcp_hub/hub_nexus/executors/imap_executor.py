# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import time
import email
import base64
import typing
import asyncio
import imaplib
import contextlib
from email import policy
from backend.mcp_hub.hub_nexus.infra.core import ClockService
from backend.mcp_hub.hub_nexus.infra.result import ExecutorResultService
from backend.mcp_hub.hub_nexus.infra.pack_builder import PackBuilder
from backend.utilities import const


class ImapExecutor(object):

    @staticmethod
    async def execute(
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        action: str = "search",
        mailbox: str = "INBOX",
        criteria: str = "ALL",
        message_set: str = "1",
        fetch_parts: str = "(BODY.PEEK[])",
        parse_messages: bool = False,
        use_ssl: bool = True,
        timeout: float = 15.0,
        media_path: typing.Optional[str] = None,
        extract: typing.Optional[dict[str, str]] = None,
        asserts: typing.Optional[list[dict[str, typing.Any]]] = None,
        step_artifact_dir: typing.Optional[str] = None
    ) -> dict[str, typing.Any]:
        """执行 IMAP 登录、查询或抓取动作，并返回统一结果结构。"""
        t0 = time.perf_counter()
        last_err: typing.Optional[str] = None
        result_data: dict[str, typing.Any] = {}
        ok = False

        request_data = PackBuilder.build_request_imap(
            host=host,
            port=port,
            username=username,
            action=action,
            mailbox=mailbox,
            criteria=criteria,
            message_set=message_set,
            fetch_parts=fetch_parts,
            parse_messages=parse_messages,
            use_ssl=use_ssl,
            timeout=timeout,
            media_path=media_path
        )

        def _decode_part_bytes(part: email.message.Message, payload: bytes) -> str:
            """按 part 的 charset 解码字节，失败时兜底 replace。"""
            charset = None
            with contextlib.suppress(Exception):
                charset = part.get_content_charset()

            candidates = []
            if charset:
                candidates.append(charset)
            candidates.extend(["utf-8", "gb18030", "latin-1"])

            for enc in candidates:
                try:
                    return payload.decode(enc)
                except (LookupError, UnicodeDecodeError):
                    continue
            return payload.decode(const.CHARSET, errors="replace")

        def _extract_part_content(part: email.message.Message) -> str:
            """兼容旧 Message / 新 EmailMessage，统一提取文本内容。"""
            if not hasattr(part, "get_payload"):
                return ""

            payload_true = part.get_payload(decode=True)
            if isinstance(payload_true, bytes):
                text = _decode_part_bytes(part, payload_true).strip()
                if text:
                    return text
            elif isinstance(payload_true, str):
                text = payload_true.strip()
                if text:
                    return text

            payload_false = part.get_payload(decode=False)
            if isinstance(payload_false, bytes):
                text = _decode_part_bytes(part, payload_false).strip()
                if text:
                    return text
            elif isinstance(payload_false, str):
                text = payload_false.strip()
                if text:
                    return text

            return ""

        def _is_attachment(part: email.message.Message) -> bool:
            filename = None
            disposition = ""

            with contextlib.suppress(Exception):
                filename = part.get_filename()

            with contextlib.suppress(Exception):
                disposition = str(part.get_content_disposition() or "").lower()

            if filename:
                return True
            if disposition == "attachment":
                return True

            # 某些邮件 inline 也带文件名，本质也是附件
            if disposition == "inline" and filename:
                return True

            return False

        def _part_size(part: email.message.Message) -> int:
            with contextlib.suppress(Exception):
                payload = part.get_payload(decode=True)
                if isinstance(payload, bytes):
                    return len(payload)
                if isinstance(payload, str):
                    return len(payload.encode(const.CHARSET, errors="replace"))
            return 0

        def _parse_message(raw: bytes) -> dict[str, typing.Any]:
            msg = email.message_from_bytes(raw, policy=policy.default)

            text_parts: list[str] = []
            html_parts: list[str] = []
            _attachments: list[dict[str, typing.Any]] = []

            if hasattr(msg, "walk"):
                parts_iter = msg.walk()
            else:
                parts_iter = [msg]

            for part in parts_iter:
                # 跳过 multipart 容器本身
                with contextlib.suppress(Exception):
                    if part.is_multipart():
                        continue

                content_type = "application/octet-stream"
                with contextlib.suppress(Exception):
                    content_type = str(part.get_content_type() or "application/octet-stream").lower()

                filename = None
                with contextlib.suppress(Exception):
                    filename = part.get_filename()

                disposition = ""
                with contextlib.suppress(Exception):
                    disposition = str(part.get_content_disposition() or "").lower()

                if _is_attachment(part):
                    payload = part.get_payload(decode=True)
                    _attachments.append(
                        {
                            "filename"            : filename,
                            "content_type"        : content_type,
                            "content_disposition" : disposition or None,
                            "size"                : _part_size(part),
                            "base64"              : (
                                base64.b64encode(payload).decode("ascii")
                                if isinstance(payload, bytes)
                                and payload
                                and (
                                    content_type.startswith("image/")
                                    or content_type.startswith("video/")
                                )
                                else None
                            )
                        }
                    )
                    continue

                content = _extract_part_content(part)
                if not content:
                    continue

                if content_type.startswith("text/plain"):
                    text_parts.append(content)
                elif content_type.startswith("text/html"):
                    html_parts.append(content)
                else:
                    # 对没有 disposition 且无 filename 的 text/* 做兜底
                    if content_type.startswith("text/"):
                        text_parts.append(content)

            return {
                "subject"      : msg.get("Subject"),
                "from"         : msg.get("From"),
                "to"           : msg.get("To"),
                "cc"           : msg.get("Cc"),
                "bcc"          : msg.get("Bcc"),
                "date"         : msg.get("Date"),
                "message_id"   : msg.get("Message-ID"),
                "content_type" : msg.get_content_type() if hasattr(msg, "get_content_type") else None,
                "text"         : "\n\n".join(item for item in text_parts if item).strip() or None,
                "html"         : "\n\n".join(item for item in html_parts if item).strip() or None,
                "attachments"  : _attachments
            }

        def _normalize_fetch_items(fetch_data: typing.Any) -> list[typing.Any]:
            items: list[typing.Any] = []
            for item in (fetch_data or []):
                if isinstance(item, tuple):
                    entry = []
                    for sub in item:
                        if isinstance(sub, bytes):
                            entry.append(sub.decode(errors="replace"))
                        else:
                            entry.append(sub)
                    items.append(entry)
                elif isinstance(item, bytes):
                    items.append(item.decode(errors="replace"))
                else:
                    items.append(item)
            return items

        def _imap_call() -> dict[str, typing.Any]:
            imap_cls = imaplib.IMAP4_SSL if use_ssl else imaplib.IMAP4
            client = imap_cls(host, int(port), timeout=float(timeout))

            try:
                login_typ, login_data = client.login(username, password)
                result: dict[str, typing.Any] = {
                    "login": {
                        "type": login_typ,
                        "data": [item.decode(errors="replace") for item in (login_data or [])]
                    }
                }

                select_typ, select_data = client.select(mailbox)
                result["select"] = {
                    "type": select_typ,
                    "data": [item.decode(errors="replace") for item in (select_data or [])]
                }

                action_norm = str(action or "search").lower()

                if action_norm == "fetch":
                    fetch_typ, fetch_data = client.fetch(message_set, fetch_parts)
                    result["fetch"] = {
                        "type": fetch_typ,
                        "items": _normalize_fetch_items(fetch_data),
                    }

                    if parse_messages:
                        parsed_messages = []
                        for item in (fetch_data or []):
                            if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], bytes):
                                parsed_messages.append(_parse_message(item[1]))
                        result["parsed_messages"] = parsed_messages

                elif action_norm == "noop":
                    noop_typ, noop_data = client.noop()
                    result["noop"] = {
                        "type": noop_typ,
                        "data": [item.decode(errors="replace") for item in (noop_data or [])],
                    }

                else:
                    search_typ, search_data = client.search(None, criteria)
                    ids: list[str] = []
                    if search_data and search_data[0]:
                        ids = search_data[0].decode(errors="replace").split()

                    result["search"] = {
                        "type": search_typ, "ids": ids
                    }

                return result

            finally:
                with contextlib.suppress(Exception):
                    client.close()
                with contextlib.suppress(Exception):
                    client.logout()

        try:
            result_data = await asyncio.to_thread(_imap_call)
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
                tool="imap_media",
                step_artifact_dir=step_artifact_dir,
                timeout=timeout
            )
        return ExecutorResultService.finalize_pack(
            text=f"IMAP {action} {host}:{port}/{mailbox} ({elapsed_ms}ms)",
            ok=ok,
            request=request_data,
            response=PackBuilder.build_response_imap(
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
