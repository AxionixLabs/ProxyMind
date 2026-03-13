#  _____                     _               ____            _     _
# | ____|_  _____  ___ _   _| |_ ___  _ __  |  _ \ ___  __ _(_)___| |_ _ __ _   _
# |  _| \ \/ / _ \/ __| | | | __/ _ \| '__| | |_) / _ \/ _` | / __| __| '__| | | |
# | |___ >  <  __/ (__| |_| | || (_) | |    |  _ <  __/ (_| | \__ \ |_| |  | |_| |
# |_____/_/\_\___|\___|\__,_|\__\___/|_|    |_| \_\___|\__, |_|___/\__|_|   \__, |
#                                                      |___/                |___/
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from backend.nexus.domain.models import NexusKind
from backend.nexus.executors.ftp_executor import FtpExecutor
from backend.nexus.executors.graphql_executor import GraphqlExecutor
from backend.nexus.executors.http_executor import HttpExecutor
from backend.nexus.executors.imap_executor import ImapExecutor
from backend.nexus.executors.sse_executor import SseExecutor
from backend.nexus.executors.smtp_executor import SmtpExecutor
from backend.nexus.executors.tcp_executor import TcpExecutor
from backend.nexus.executors.udp_executor import UdpExecutor
from backend.nexus.executors.ws_executor import WsExecutor


class NexusExecutorRegistry(object):
    """Protocol dispatch for normalized nexus request execution."""

    async def execute(
        self,
        *,
        kind: NexusKind,
        request: dict[str, typing.Any],
        env: dict[str, typing.Any],
        extract: typing.Optional[dict[str, str]] = None,
        asserts: typing.Optional[list[dict[str, typing.Any]]] = None,
    ) -> dict[str, typing.Any]:
        """根据 kind 把标准化请求路由到对应协议执行器。"""
        base_url = str(env.get("base_url") or "") or None
        base_headers = dict(env.get("headers") or {})
        base_timeout = float(env.get("timeout", 30.0))

        if kind == "http":
            return await HttpExecutor.execute(
                method=str(request.get("method", "GET")),
                url=str(request.get("url", "")),
                base_url=str(request.get("base_url") or base_url) or None,
                headers={**base_headers, **dict(request.get("headers") or {})},
                params=request.get("params"),
                json_body=request.get("json") or request.get("json_body"),
                body_text=request.get("body") or request.get("body_text"),
                form=request.get("form") if isinstance(request.get("form"), dict) else None,
                files=request.get("files") if isinstance(request.get("files"), list) else None,
                timeout=float(request.get("timeout", base_timeout)),
                retries=int(request.get("retries", 0)),
                follow_redirects=bool(request.get("follow_redirects", True)),
                extract=extract,
                asserts=asserts,
                save_response=bool(request.get("save_response", False)),
                save_dir=(str(request.get("save_dir")) if request.get("save_dir") else None),
            )

        if kind == "sse":
            return await SseExecutor.execute(
                method=str(request.get("method", "GET")),
                url=str(request.get("url", "")),
                base_url=str(request.get("base_url") or base_url) or None,
                headers={**base_headers, **dict(request.get("headers") or {})},
                params=request.get("params"),
                json_body=request.get("json") or request.get("json_body"),
                body_text=request.get("body") or request.get("body_text"),
                form=request.get("form") if isinstance(request.get("form"), dict) else None,
                files=request.get("files") if isinstance(request.get("files"), list) else None,
                timeout=float(request.get("timeout", base_timeout)),
                retries=int(request.get("retries", 0)),
                follow_redirects=bool(request.get("follow_redirects", True)),
                max_events=(None if request.get("max_events") is None else int(request.get("max_events"))),
                extract=extract,
                asserts=asserts,
                media_index=(None if request.get("media_index") is None else int(request.get("media_index"))),
                media_path=(str(request.get("media_path")) if request.get("media_path") else None),
                save_response=bool(request.get("save_response", False)),
                save_dir=(str(request.get("save_dir")) if request.get("save_dir") else None),
            )

        if kind == "graphql":
            return await GraphqlExecutor.execute(
                url=str(request.get("url", "")),
                query=str(request.get("query", "")),
                variables=request.get("variables") if isinstance(request.get("variables"), dict) else {},
                operation_name=request.get("operation_name") or request.get("operationName"),
                base_url=str(request.get("base_url") or base_url) or None,
                headers={**base_headers, **dict(request.get("headers") or {})},
                params=request.get("params"),
                timeout=float(request.get("timeout", base_timeout)),
                retries=int(request.get("retries", 0)),
                follow_redirects=bool(request.get("follow_redirects", True)),
                extract=extract,
                asserts=asserts,
                media_path=(str(request.get("media_path")) if request.get("media_path") else None),
                save_response=bool(request.get("save_response", False)),
                save_dir=(str(request.get("save_dir")) if request.get("save_dir") else None),
            )

        if kind == "tcp":
            raw_sends = request.get("sends", env.get("sends"))
            if isinstance(raw_sends, list):
                sends = [str(item) for item in raw_sends]
            elif raw_sends is None:
                sends = None
            else:
                sends = [str(raw_sends)]
            return await TcpExecutor.execute(
                host=str(request.get("host") or env.get("host") or ""),
                port=int(request.get("port", env.get("port", 0))),
                body_text=(None if request.get("body_text") is None else str(request.get("body_text"))),
                sends=sends,
                encoding=str(request.get("encoding", env.get("encoding", "utf-8"))),
                timeout=float(request.get("timeout", env.get("timeout", 10.0))),
                read_size=int(request.get("read_size", env.get("read_size", 4096))),
                close_write=bool(request.get("close_write", env.get("close_write", True))),
                max_reads=int(request.get("max_reads", env.get("max_reads", 1))),
                read_until=(None if (request.get("read_until") or env.get("read_until")) is None else str(request.get("read_until") or env.get("read_until"))),
                extract=extract,
                asserts=asserts,
            )

        if kind == "udp":
            return await UdpExecutor.execute(
                host=str(request.get("host") or env.get("host") or ""),
                port=int(request.get("port", env.get("port", 0))),
                body_text=str(request.get("body_text", env.get("body_text", ""))),
                encoding=str(request.get("encoding", env.get("encoding", "utf-8"))),
                timeout=float(request.get("timeout", env.get("timeout", 10.0))),
                read_size=int(request.get("read_size", env.get("read_size", 4096))),
                extract=extract,
                asserts=asserts,
            )

        if kind == "smtp":
            raw_to_addrs = request.get("to_addrs", env.get("to_addrs"))
            if isinstance(raw_to_addrs, list):
                to_addrs = [str(item) for item in raw_to_addrs]
            elif raw_to_addrs is None:
                to_addrs = None
            else:
                to_addrs = [str(raw_to_addrs)]
            return await SmtpExecutor.execute(
                host=str(request.get("host") or env.get("host") or ""),
                port=int(request.get("port", env.get("port", 25))),
                action=str(request.get("action", env.get("action", "noop"))),
                username=(None if (request.get("username") or env.get("username")) is None else str(request.get("username") or env.get("username"))),
                password=(None if (request.get("password") or env.get("password")) is None else str(request.get("password") or env.get("password"))),
                use_ssl=bool(request.get("use_ssl", env.get("use_ssl", False))),
                use_tls=bool(request.get("use_tls", env.get("use_tls", False))),
                from_addr=(None if (request.get("from_addr") or env.get("from_addr")) is None else str(request.get("from_addr") or env.get("from_addr"))),
                to_addrs=to_addrs,
                subject=(None if request.get("subject") is None else str(request.get("subject"))),
                body_text=(None if request.get("body_text") is None else str(request.get("body_text"))),
                html_body=(None if request.get("html_body") is None else str(request.get("html_body"))),
                attachments=request.get("attachments") if isinstance(request.get("attachments"), list) else None,
                timeout=float(request.get("timeout", env.get("timeout", 15.0))),
                extract=extract,
                asserts=asserts,
            )

        if kind == "imap":
            return await ImapExecutor.execute(
                host=str(request.get("host") or env.get("host") or ""),
                port=int(request.get("port", env.get("port", 993))),
                username=str(request.get("username") or env.get("username") or ""),
                password=str(request.get("password") or env.get("password") or ""),
                action=str(request.get("action", env.get("action", "search"))),
                mailbox=str(request.get("mailbox", env.get("mailbox", "INBOX"))),
                criteria=str(request.get("criteria", env.get("criteria", "ALL"))),
                message_set=str(request.get("message_set", env.get("message_set", "1"))),
                fetch_parts=str(request.get("fetch_parts", env.get("fetch_parts", "BODY.PEEK[HEADER]"))),
                parse_messages=bool(request.get("parse_messages", env.get("parse_messages", False))),
                use_ssl=bool(request.get("use_ssl", env.get("use_ssl", True))),
                timeout=float(request.get("timeout", env.get("timeout", 15.0))),
                extract=extract,
                asserts=asserts,
            )

        if kind == "ftp":
            return await FtpExecutor.execute(
                host=str(request.get("host") or env.get("host") or ""),
                port=int(request.get("port", env.get("port", 21))),
                username=str(request.get("username", env.get("username", "anonymous"))),
                password=str(request.get("password", env.get("password", "anonymous@"))),
                action=str(request.get("action", env.get("action", "list"))),
                path=str(request.get("path", env.get("path", "."))),
                payload_text=(None if request.get("payload_text") is None else str(request.get("payload_text"))),
                payload_base64=(None if request.get("payload_base64") is None else str(request.get("payload_base64"))),
                encoding=str(request.get("encoding", env.get("encoding", "utf-8"))),
                use_tls=bool(request.get("use_tls", env.get("use_tls", False))),
                timeout=float(request.get("timeout", env.get("timeout", 15.0))),
                extract=extract,
                asserts=asserts,
            )

        if kind != "ws":
            raise ValueError(f"unsupported nexus kind: {kind}")

        raw_sends = request.get("sends")
        if isinstance(raw_sends, list):
            sends = [str(item) for item in raw_sends]
        elif raw_sends is None:
            sends = []
        else:
            sends = [str(raw_sends)]

        return await WsExecutor.execute(
            url=str(request.get("url", "")),
            headers={**base_headers, **dict(request.get("headers") or {})},
            sends=sends,
            timeout=float(request.get("timeout", base_timeout)),
            max_messages=int(request.get("max_messages", 10)),
            extract=extract,
            asserts=asserts,
            media_index=(None if request.get("media_index") is None else int(request.get("media_index"))),
            media_path=(str(request.get("media_path")) if request.get("media_path") else None),
            save_response=bool(request.get("save_response", False)),
            save_dir=(str(request.get("save_dir")) if request.get("save_dir") else None),
        )
