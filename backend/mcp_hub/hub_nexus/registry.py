# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from backend.models.model_nexus import NexusKind
from backend.mcp_hub.hub_nexus.domain.merge import MergeService
from backend.mcp_hub.hub_nexus.executors.ftp_executor import FtpExecutor
from backend.mcp_hub.hub_nexus.executors.graphql_executor import GraphqlExecutor
from backend.mcp_hub.hub_nexus.executors.http_executor import HttpExecutor
from backend.mcp_hub.hub_nexus.executors.imap_executor import ImapExecutor
from backend.mcp_hub.hub_nexus.executors.sse_executor import SseExecutor
from backend.mcp_hub.hub_nexus.executors.smtp_executor import SmtpExecutor
from backend.mcp_hub.hub_nexus.executors.tcp_executor import TcpExecutor
from backend.mcp_hub.hub_nexus.executors.udp_executor import UdpExecutor
from backend.mcp_hub.hub_nexus.executors.ws_executor import WsExecutor


class NexusExecutorRegistry(object):
    """负责把标准化后的 nexus 请求分发到对应协议执行器。"""

    @staticmethod
    def _pick(request: dict[str, typing.Any], env: dict[str, typing.Any], key: str) -> typing.Any:
        """request 优先，其次 env。"""
        value = request.get(key)
        return env.get(key) if value is None else value

    @staticmethod
    def _pick_alias(source: dict[str, typing.Any], *keys: str) -> typing.Any:
        """按给定别名顺序取首个非 None 的值。"""
        for key in keys:
            value = source.get(key)
            if value is not None:
                return value
        return None

    @staticmethod
    def _as_str(value: typing.Any, default: str = "") -> str:
        """安全字符串化；None 返回默认值。"""
        return default if value is None else str(value)

    @staticmethod
    def _as_optional_str(value: typing.Any) -> typing.Optional[str]:
        """安全可空字符串化；None 保持 None。"""
        if value is None:
            return None
        text = str(value).strip()
        if not text or text.lower() == "none":
            return None
        return text

    @staticmethod
    def _as_int(value: typing.Any, default: int = 0) -> int:
        """安全整数化。"""
        if value is None:
            return default
        return int(value)

    @staticmethod
    def _as_float(value: typing.Any, default: float = 0.0) -> float:
        """安全浮点化。"""
        if value is None:
            return default
        return float(value)

    @staticmethod
    def _as_bool(value: typing.Any, default: bool = False) -> bool:
        """安全布尔化。"""
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            text = value.strip().lower()
            if text in {"true", "1", "yes", "y", "on"}:
                return True
            if text in {"false", "0", "no", "n", "off", ""}:
                return False
        raise ValueError(f"invalid boolean value: {value!r}")

    @staticmethod
    def _as_dict(value: typing.Any) -> dict[str, typing.Any]:
        """确保返回 dict。"""
        return dict(value) if isinstance(value, dict) else {}

    @staticmethod
    def _as_list(value: typing.Any) -> typing.Optional[list[typing.Any]]:
        """确保返回 list 或 None。"""
        return value if isinstance(value, list) else None

    @staticmethod
    def _as_str_list(value: typing.Any, none_as: typing.Optional[list[str]] = None) -> typing.Optional[list[str]]:
        """统一把单值/列表转成字符串列表。"""
        if value is None:
            return none_as
        if isinstance(value, list):
            return [str(item) for item in value]
        return [str(value)]

    @classmethod
    def _request_or_env_str(
        cls,
        request: dict[str, typing.Any],
        env: dict[str, typing.Any],
        key: str
    ) -> typing.Optional[str]:
        """request/env 中的可空字符串字段。"""
        return cls._as_optional_str(cls._pick(request, env, key))

    @classmethod
    def _request_or_env_int(
        cls,
        request: dict[str, typing.Any],
        env: dict[str, typing.Any],
        key: str,
        default: int
    ) -> int:
        """request/env 中的整数值。"""
        value = cls._pick(request, env, key)
        return cls._as_int(value, default)

    @classmethod
    def _request_or_env_float(
        cls,
        request: dict[str, typing.Any],
        env: dict[str, typing.Any],
        key: str,
        default: float
    ) -> float:
        """request/env 中的浮点值。"""
        value = cls._pick(request, env, key)
        return cls._as_float(value, default)

    @classmethod
    def _request_or_env_bool(
        cls,
        request: dict[str, typing.Any],
        env: dict[str, typing.Any],
        key: str,
        default: bool
    ) -> bool:
        """request/env 中的布尔值。"""
        value = cls._pick(request, env, key)
        return cls._as_bool(value, default)

    @classmethod
    def _request_or_env_alias(
        cls,
        request: dict[str, typing.Any],
        env: dict[str, typing.Any],
        *keys: str
    ) -> typing.Any:
        """按别名顺序从 request/env 中取首个非 None 的值。"""
        value = cls._pick_alias(request, *keys)
        return cls._pick_alias(env, *keys) if value is None else value

    @staticmethod
    async def execute(
        *,
        kind: NexusKind,
        request: dict[str, typing.Any],
        env: dict[str, typing.Any],
        extract: typing.Optional[dict[str, str]] = None,
        asserts: typing.Optional[list[dict[str, typing.Any]]] = None,
        step_artifact_dir: typing.Optional[str] = None,
    ) -> dict[str, typing.Any]:
        """根据 kind 把标准化请求路由到对应协议执行器。"""
        request = MergeService.materialize(env=env, request=request)
        env     = {}

        base_headers = NexusExecutorRegistry._as_dict(env.get("headers"))
        base_timeout = NexusExecutorRegistry._as_float(env.get("timeout"), 30.0)
        req_base_url = NexusExecutorRegistry._request_or_env_str(request, env, "base_url")
        req_url = NexusExecutorRegistry._as_str(
            NexusExecutorRegistry._pick(request, env, "url")
        )
        req_headers = {
            **base_headers,
            **NexusExecutorRegistry._as_dict(request.get("headers"))
        }
        req_params = NexusExecutorRegistry._request_or_env_alias(request, env, "params")
        req_json_body = NexusExecutorRegistry._request_or_env_alias(
            request, env, "json", "json_body"
        )
        req_body_text = NexusExecutorRegistry._request_or_env_alias(
            request, env, "body", "body_text"
        )
        req_form_raw = NexusExecutorRegistry._request_or_env_alias(request, env, "form")
        req_form = req_form_raw if isinstance(req_form_raw, dict) else None
        req_files_raw = NexusExecutorRegistry._request_or_env_alias(request, env, "files")
        req_files = req_files_raw if isinstance(req_files_raw, list) else None
        req_variables_raw = NexusExecutorRegistry._request_or_env_alias(request, env, "variables")
        req_variables = req_variables_raw if isinstance(req_variables_raw, dict) else {}

        if kind == "http":
            return await HttpExecutor.execute(
                method=NexusExecutorRegistry._as_str(
                    NexusExecutorRegistry._pick(request, env, "method"), "GET"
                ),
                url=req_url,
                base_url=req_base_url,
                headers=req_headers,
                params=req_params,
                json_body=req_json_body,
                body_text=req_body_text,
                form=req_form,
                files=req_files,
                timeout=NexusExecutorRegistry._request_or_env_float(request, env, "timeout", base_timeout),
                retries=NexusExecutorRegistry._request_or_env_int(request, env, "retries", 0),
                follow_redirects=NexusExecutorRegistry._request_or_env_bool(
                    request, env, "follow_redirects", True
                ),
                extract=extract,
                asserts=asserts,
                step_artifact_dir=step_artifact_dir
            )

        if kind == "sse":
            return await SseExecutor.execute(
                method=NexusExecutorRegistry._as_str(
                    NexusExecutorRegistry._pick(request, env, "method"), "GET"
                ),
                url=req_url,
                base_url=req_base_url,
                headers=req_headers,
                params=req_params,
                json_body=req_json_body,
                body_text=req_body_text,
                form=req_form,
                files=req_files,
                timeout=NexusExecutorRegistry._request_or_env_float(request, env, "timeout", base_timeout),
                retries=NexusExecutorRegistry._request_or_env_int(request, env, "retries", 0),
                follow_redirects=NexusExecutorRegistry._request_or_env_bool(
                    request, env, "follow_redirects", True
                ),
                max_events=(
                    None if NexusExecutorRegistry._pick(request, env, "max_events") is None
                    else NexusExecutorRegistry._request_or_env_int(request, env, "max_events", 0)
                ),
                extract=extract,
                asserts=asserts,
                media_index=(
                    None if NexusExecutorRegistry._pick(request, env, "media_index") is None
                    else NexusExecutorRegistry._request_or_env_int(request, env, "media_index", 0)
                ),
                media_path=NexusExecutorRegistry._request_or_env_str(request, env, "media_path"),
                step_artifact_dir=step_artifact_dir
            )

        if kind == "graphql":
            return await GraphqlExecutor.execute(
                url=req_url,
                query=NexusExecutorRegistry._as_str(
                    NexusExecutorRegistry._pick(request, env, "query")
                ),
                variables=req_variables,
                operation_name=NexusExecutorRegistry._as_optional_str(
                    NexusExecutorRegistry._request_or_env_alias(
                        request, env, "operation_name", "operationName"
                    )
                ),
                base_url=req_base_url,
                headers=req_headers,
                params=req_params,
                timeout=NexusExecutorRegistry._request_or_env_float(request, env, "timeout", base_timeout),
                retries=NexusExecutorRegistry._request_or_env_int(request, env, "retries", 0),
                follow_redirects=NexusExecutorRegistry._request_or_env_bool(
                    request, env, "follow_redirects", True
                ),
                extract=extract,
                asserts=asserts,
                media_path=NexusExecutorRegistry._request_or_env_str(request, env, "media_path"),
                step_artifact_dir=step_artifact_dir
            )

        if kind == "tcp":
            raw_sends = request.get("sends", env.get("sends"))
            sends = NexusExecutorRegistry._as_str_list(raw_sends, none_as=None)

            return await TcpExecutor.execute(
                host=NexusExecutorRegistry._as_str(request.get("host") or env.get("host")),
                port=NexusExecutorRegistry._request_or_env_int(request, env, "port", 0),
                body_text=NexusExecutorRegistry._request_or_env_str(request, env, "body_text"),
                sends=sends,
                encoding=NexusExecutorRegistry._as_str(
                    NexusExecutorRegistry._pick(request, env, "encoding"), "utf-8"
                ),
                timeout=NexusExecutorRegistry._request_or_env_float(request, env, "timeout", 10.0),
                read_size=NexusExecutorRegistry._request_or_env_int(request, env, "read_size", 4096),
                close_write=NexusExecutorRegistry._request_or_env_bool(request, env, "close_write", True),
                max_reads=NexusExecutorRegistry._request_or_env_int(request, env, "max_reads", 1),
                read_until=NexusExecutorRegistry._request_or_env_str(request, env, "read_until"),
                extract=extract,
                asserts=asserts,
                step_artifact_dir=step_artifact_dir
            )

        if kind == "udp":
            return await UdpExecutor.execute(
                host=NexusExecutorRegistry._as_str(request.get("host") or env.get("host")),
                port=NexusExecutorRegistry._request_or_env_int(request, env, "port", 0),
                body_text=NexusExecutorRegistry._as_str(
                    NexusExecutorRegistry._pick(request, env, "body_text")
                ),
                encoding=NexusExecutorRegistry._as_str(
                    NexusExecutorRegistry._pick(request, env, "encoding"), "utf-8"
                ),
                timeout=NexusExecutorRegistry._request_or_env_float(request, env, "timeout", 10.0),
                read_size=NexusExecutorRegistry._request_or_env_int(request, env, "read_size", 4096),
                extract=extract,
                asserts=asserts,
                step_artifact_dir=step_artifact_dir
            )

        if kind == "smtp":
            raw_to_addrs = request.get("to_addrs", env.get("to_addrs"))
            to_addrs = NexusExecutorRegistry._as_str_list(raw_to_addrs, none_as=None)
            raw_attachments = NexusExecutorRegistry._request_or_env_alias(request, env, "attachments")
            attachments = raw_attachments if isinstance(raw_attachments, list) else None

            return await SmtpExecutor.execute(
                host=NexusExecutorRegistry._as_str(request.get("host") or env.get("host")),
                port=NexusExecutorRegistry._request_or_env_int(request, env, "port", 25),
                action=NexusExecutorRegistry._as_str(
                    NexusExecutorRegistry._pick(request, env, "action"), "noop"
                ),
                username=NexusExecutorRegistry._request_or_env_str(request, env, "username"),
                password=NexusExecutorRegistry._request_or_env_str(request, env, "password"),
                use_ssl=NexusExecutorRegistry._request_or_env_bool(request, env, "use_ssl", False),
                use_tls=NexusExecutorRegistry._request_or_env_bool(request, env, "use_tls", False),
                from_addr=NexusExecutorRegistry._request_or_env_str(request, env, "from_addr"),
                to_addrs=to_addrs,
                subject=NexusExecutorRegistry._request_or_env_str(request, env, "subject"),
                body_text=NexusExecutorRegistry._request_or_env_str(request, env, "body_text"),
                html_body=NexusExecutorRegistry._request_or_env_str(request, env, "html_body"),
                attachments=attachments,
                timeout=NexusExecutorRegistry._request_or_env_float(request, env, "timeout", 15.0),
                extract=extract,
                asserts=asserts,
                step_artifact_dir=step_artifact_dir
            )

        if kind == "imap":
            return await ImapExecutor.execute(
                host=NexusExecutorRegistry._as_str(request.get("host") or env.get("host")),
                port=NexusExecutorRegistry._request_or_env_int(request, env, "port", 993),
                username=NexusExecutorRegistry._as_str(request.get("username") or env.get("username")),
                password=NexusExecutorRegistry._as_str(request.get("password") or env.get("password")),
                action=NexusExecutorRegistry._as_str(
                    NexusExecutorRegistry._pick(request, env, "action"), "search"
                ),
                mailbox=NexusExecutorRegistry._as_str(
                    NexusExecutorRegistry._pick(request, env, "mailbox"), "INBOX"
                ),
                criteria=NexusExecutorRegistry._as_str(
                    NexusExecutorRegistry._pick(request, env, "criteria"), "ALL"
                ),
                message_set=NexusExecutorRegistry._as_str(
                    NexusExecutorRegistry._pick(request, env, "message_set"), "1"
                ),
                fetch_parts=NexusExecutorRegistry._as_str(
                    NexusExecutorRegistry._pick(request, env, "fetch_parts"), "BODY.PEEK[HEADER]"
                ),
                parse_messages=NexusExecutorRegistry._request_or_env_bool(request, env, "parse_messages", False),
                use_ssl=NexusExecutorRegistry._request_or_env_bool(request, env, "use_ssl", True),
                timeout=NexusExecutorRegistry._request_or_env_float(request, env, "timeout", 15.0),
                media_path=NexusExecutorRegistry._request_or_env_str(request, env, "media_path"),
                extract=extract,
                asserts=asserts,
                step_artifact_dir=step_artifact_dir
            )

        if kind == "ftp":
            return await FtpExecutor.execute(
                host=NexusExecutorRegistry._as_str(request.get("host") or env.get("host")),
                port=NexusExecutorRegistry._request_or_env_int(request, env, "port", 21),
                username=NexusExecutorRegistry._as_str(
                    NexusExecutorRegistry._pick(request, env, "username"), "anonymous"
                ),
                password=NexusExecutorRegistry._as_str(
                    NexusExecutorRegistry._pick(request, env, "password"), "anonymous@"
                ),
                action=NexusExecutorRegistry._as_str(
                    NexusExecutorRegistry._pick(request, env, "action"), "list"
                ),
                path=NexusExecutorRegistry._as_str(
                    NexusExecutorRegistry._pick(request, env, "path"), "."
                ),
                payload_text=NexusExecutorRegistry._request_or_env_str(request, env, "payload_text"),
                payload_base64=NexusExecutorRegistry._request_or_env_str(request, env, "payload_base64"),
                encoding=NexusExecutorRegistry._as_str(
                    NexusExecutorRegistry._pick(request, env, "encoding"), "utf-8"
                ),
                use_tls=NexusExecutorRegistry._request_or_env_bool(request, env, "use_tls", False),
                timeout=NexusExecutorRegistry._request_or_env_float(request, env, "timeout", 15.0),
                media_path=NexusExecutorRegistry._request_or_env_str(request, env, "media_path"),
                extract=extract,
                asserts=asserts,
                step_artifact_dir=step_artifact_dir
            )

        if kind == "ws":
            raw_sends = NexusExecutorRegistry._request_or_env_alias(request, env, "sends")
            sends = NexusExecutorRegistry._as_str_list(raw_sends, none_as=[])

            return await WsExecutor.execute(
                url=req_url,
                headers=req_headers,
                sends=sends,
                timeout=NexusExecutorRegistry._request_or_env_float(request, env, "timeout", base_timeout),
                max_messages=NexusExecutorRegistry._request_or_env_int(
                    request, env, "max_messages", 10
                ),
                extract=extract,
                asserts=asserts,
                media_index=(
                    None if NexusExecutorRegistry._pick(request, env, "media_index") is None
                    else NexusExecutorRegistry._request_or_env_int(request, env, "media_index", 0)
                ),
                media_path=NexusExecutorRegistry._request_or_env_str(request, env, "media_path"),
                step_artifact_dir=step_artifact_dir
            )

        raise ValueError(f"unsupported nexus kind: {kind}")


if __name__ == '__main__':
    pass
