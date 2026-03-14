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

    @staticmethod
    def _pick(request: dict[str, typing.Any], env: dict[str, typing.Any], key: str) -> typing.Any:
        """request 优先，其次 env。"""
        value = request.get(key)
        return env.get(key) if value is None else value

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
        return bool(value)

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

    @staticmethod
    async def execute(
        *,
        kind: NexusKind,
        request: dict[str, typing.Any],
        env: dict[str, typing.Any],
        extract: typing.Optional[dict[str, str]] = None,
        asserts: typing.Optional[list[dict[str, typing.Any]]] = None,
    ) -> dict[str, typing.Any]:
        """根据 kind 把标准化请求路由到对应协议执行器。"""
        request = dict(request or {})
        env = dict(env or {})

        base_url = NexusExecutorRegistry._as_optional_str(env.get("base_url"))
        base_headers = NexusExecutorRegistry._as_dict(env.get("headers"))
        base_timeout = NexusExecutorRegistry._as_float(env.get("timeout"), 30.0)

        if kind == "http":
            req_base_url = NexusExecutorRegistry._as_optional_str(
                request.get("base_url", base_url)
            )
            return await HttpExecutor.execute(
                method=NexusExecutorRegistry._as_str(request.get("method"), "GET"),
                url=NexusExecutorRegistry._as_str(request.get("url")),
                base_url=req_base_url,
                headers={**base_headers, **NexusExecutorRegistry._as_dict(request.get("headers"))},
                params=request.get("params"),
                json_body=request.get("json") or request.get("json_body"),
                body_text=request.get("body") or request.get("body_text"),
                form=request.get("form") if isinstance(request.get("form"), dict) else None,
                files=request.get("files") if isinstance(request.get("files"), list) else None,
                timeout=NexusExecutorRegistry._as_float(request.get("timeout"), base_timeout),
                retries=NexusExecutorRegistry._as_int(request.get("retries"), 0),
                follow_redirects=NexusExecutorRegistry._as_bool(request.get("follow_redirects"), True),
                extract=extract,
                asserts=asserts,
                save_response=NexusExecutorRegistry._as_bool(request.get("save_response"), False),
                save_dir=NexusExecutorRegistry._as_optional_str(request.get("save_dir"))
            )

        if kind == "sse":
            req_base_url = NexusExecutorRegistry._as_optional_str(
                request.get("base_url", base_url)
            )
            return await SseExecutor.execute(
                method=NexusExecutorRegistry._as_str(request.get("method"), "GET"),
                url=NexusExecutorRegistry._as_str(request.get("url")),
                base_url=req_base_url,
                headers={**base_headers, **NexusExecutorRegistry._as_dict(request.get("headers"))},
                params=request.get("params"),
                json_body=request.get("json") or request.get("json_body"),
                body_text=request.get("body") or request.get("body_text"),
                form=request.get("form") if isinstance(request.get("form"), dict) else None,
                files=request.get("files") if isinstance(request.get("files"), list) else None,
                timeout=NexusExecutorRegistry._as_float(request.get("timeout"), base_timeout),
                retries=NexusExecutorRegistry._as_int(request.get("retries"), 0),
                follow_redirects=NexusExecutorRegistry._as_bool(request.get("follow_redirects"), True),
                max_events=(
                    None if request.get("max_events") is None
                    else NexusExecutorRegistry._as_int(request.get("max_events"))
                ),
                extract=extract,
                asserts=asserts,
                media_index=(
                    None if request.get("media_index") is None
                    else NexusExecutorRegistry._as_int(request.get("media_index"))
                ),
                media_path=NexusExecutorRegistry._as_optional_str(request.get("media_path")),
                save_response=NexusExecutorRegistry._as_bool(request.get("save_response"), False),
                save_dir=NexusExecutorRegistry._as_optional_str(request.get("save_dir"))
            )

        if kind == "graphql":
            req_base_url = NexusExecutorRegistry._as_optional_str(
                request.get("base_url", base_url)
            )
            return await GraphqlExecutor.execute(
                url=NexusExecutorRegistry._as_str(request.get("url")),
                query=NexusExecutorRegistry._as_str(request.get("query")),
                variables=request.get("variables") if isinstance(request.get("variables"), dict) else {},
                operation_name=request.get("operation_name") or request.get("operationName"),
                base_url=req_base_url,
                headers={**base_headers, **NexusExecutorRegistry._as_dict(request.get("headers"))},
                params=request.get("params"),
                timeout=NexusExecutorRegistry._as_float(request.get("timeout"), base_timeout),
                retries=NexusExecutorRegistry._as_int(request.get("retries"), 0),
                follow_redirects=NexusExecutorRegistry._as_bool(request.get("follow_redirects"), True),
                extract=extract,
                asserts=asserts,
                media_path=NexusExecutorRegistry._as_optional_str(request.get("media_path")),
                save_response=NexusExecutorRegistry._as_bool(request.get("save_response"), False),
                save_dir=NexusExecutorRegistry._as_optional_str(request.get("save_dir"))
            )

        if kind == "tcp":
            raw_sends = request.get("sends", env.get("sends"))
            sends = NexusExecutorRegistry._as_str_list(raw_sends, none_as=None)

            return await TcpExecutor.execute(
                host=NexusExecutorRegistry._as_str(request.get("host") or env.get("host")),
                port=NexusExecutorRegistry._request_or_env_int(request, env, "port", 0),
                body_text=NexusExecutorRegistry._as_optional_str(request.get("body_text")),
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
                asserts=asserts
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
                asserts=asserts
            )

        if kind == "smtp":
            raw_to_addrs = request.get("to_addrs", env.get("to_addrs"))
            to_addrs = NexusExecutorRegistry._as_str_list(raw_to_addrs, none_as=None)

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
                subject=NexusExecutorRegistry._as_optional_str(request.get("subject")),
                body_text=NexusExecutorRegistry._as_optional_str(request.get("body_text")),
                html_body=NexusExecutorRegistry._as_optional_str(request.get("html_body")),
                attachments=request.get("attachments") if isinstance(request.get("attachments"), list) else None,
                timeout=NexusExecutorRegistry._request_or_env_float(request, env, "timeout", 15.0),
                extract=extract,
                asserts=asserts
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
                extract=extract,
                asserts=asserts
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
                payload_text=NexusExecutorRegistry._as_optional_str(request.get("payload_text")),
                payload_base64=NexusExecutorRegistry._as_optional_str(request.get("payload_base64")),
                encoding=NexusExecutorRegistry._as_str(
                    NexusExecutorRegistry._pick(request, env, "encoding"), "utf-8"
                ),
                use_tls=NexusExecutorRegistry._request_or_env_bool(request, env, "use_tls", False),
                timeout=NexusExecutorRegistry._request_or_env_float(request, env, "timeout", 15.0),
                extract=extract,
                asserts=asserts
            )

        if kind == "ws":
            raw_sends = request.get("sends")
            sends = NexusExecutorRegistry._as_str_list(raw_sends, none_as=[])

            return await WsExecutor.execute(
                url=NexusExecutorRegistry._as_str(request.get("url")),
                headers={**base_headers, **NexusExecutorRegistry._as_dict(request.get("headers"))},
                sends=sends,
                timeout=NexusExecutorRegistry._as_float(request.get("timeout"), base_timeout),
                max_messages=NexusExecutorRegistry._as_int(request.get("max_messages"), 10),
                extract=extract,
                asserts=asserts,
                media_index=(
                    None if request.get("media_index") is None
                    else NexusExecutorRegistry._as_int(request.get("media_index"))
                ),
                media_path=NexusExecutorRegistry._as_optional_str(request.get("media_path")),
                save_response=NexusExecutorRegistry._as_bool(request.get("save_response"), False),
                save_dir=NexusExecutorRegistry._as_optional_str(request.get("save_dir"))
            )

        raise ValueError(f"unsupported nexus kind: {kind}")


if __name__ == '__main__':
    pass
