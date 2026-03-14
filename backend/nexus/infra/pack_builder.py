#  ____            _      ____        _ _     _
# |  _ \ __ _  ___| | __ | __ ) _   _(_) | __| | ___ _ __
# | |_) / _` |/ __| |/ / |  _ \| | | | | |/ _` |/ _ \ '__|
# |  __/ (_| | (__|   <  | |_) | |_| | | | (_| |  __/ |
# |_|   \__,_|\___|_|\_\ |____/ \__,_|_|_|\__,_|\___|_|
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing


class PackBuilder(object):

    @staticmethod
    def build_pack(
        *,
        text: str,
        ok: bool,
        request: dict[str, typing.Any],
        response: dict[str, typing.Any],
        attachments: typing.Optional[list[dict[str, typing.Any]]] = None,
        logs: typing.Optional[list[str]] = None,
        error: typing.Optional[str] = None,
        extra_data: typing.Optional[dict[str, typing.Any]] = None
    ) -> dict[str, typing.Any]:
        """组装协议执行后的统一返回包。"""
        data: dict[str, typing.Any] = {
            "ok"       : ok,
            "request"  : request,
            "response" : response
        }
        if error is not None:
            data["error"] = error
        if extra_data:
            data.update(extra_data)

        return {
            "text"        : text,
            "attachments" : attachments or [],
            "data"        : data,
            "logs"        : logs or []
        }

    @staticmethod
    def files_meta(
        files: typing.Optional[list[dict[str, typing.Any]]]
    ) -> list[dict[str, typing.Any]]:
        """提取文件上传参数中的可展示元信息。"""
        return [
            {
                "field"        : item.get("field"),
                "filename"     : item.get("filename"),
                "content_type" : item.get("content_type"),
                "path"         : item.get("path")
            }
            for item in (files or []) if isinstance(item, dict)
        ]

    @staticmethod
    def attachments_meta(
        attachments: typing.Optional[list[dict[str, typing.Any]]]
    ) -> list[dict[str, typing.Any]]:
        """提取附件参数中的可展示元信息。"""
        return [
            {
                "filename"     : item.get("filename"),
                "content_type" : item.get("content_type"),
                "path"         : item.get("path")
            }
            for item in (attachments or []) if isinstance(item, dict)
        ]

    @staticmethod
    def build_gql_extra(
        *,
        query: str,
        variables: dict[str, typing.Any],
        operation_name: typing.Optional[str],
        errors: typing.Any
    ) -> dict[str, typing.Any]:
        """构造 GraphQL 专属调试信息。"""
        return {
            "graphql": {
                "query"          : query,
                "variables"      : variables,
                "operation_name" : operation_name,
                "errors"         : errors
            }
        }

    @staticmethod
    def build_request_http_like(
        *,
        method: str,
        url: str,
        headers: typing.Optional[dict[str, str]] = None,
        params: typing.Optional[dict[str, typing.Any]] = None,
        json_body: typing.Optional[dict[str, typing.Any]] = None,
        body_text: typing.Optional[str] = None,
        timeout: float = 30.0,
        retries: int = 0,
        follow_redirects: bool = True,
        form: typing.Optional[dict[str, typing.Any]] = None,
        files: typing.Optional[list[dict[str, typing.Any]]] = None
    ) -> dict[str, typing.Any]:
        """构造 HTTP 类协议的标准请求快照。"""
        return {
            "method"           : method,
            "url"              : url,
            "headers"          : dict(headers or {}),
            "params"           : params,
            "json"             : json_body,
            "body_text"        : body_text,
            "timeout"          : timeout,
            "retries"          : retries,
            "follow_redirects" : follow_redirects,
            "form"             : form,
            "files"            : PackBuilder.files_meta(files)
        }

    @staticmethod
    def build_request_ws(
        *,
        url: str,
        headers: typing.Optional[dict[str, str]] = None,
        sends: typing.Optional[list[str]] = None,
        timeout: float = 60.0,
        max_messages: int = 10
    ) -> dict[str, typing.Any]:
        """构造 WebSocket 协议的标准请求快照。"""
        return {
            "url"          : url,
            "headers"      : dict(headers or {}),
            "sends"        : sends or [],
            "timeout"      : timeout,
            "max_messages" : max_messages
        }

    @staticmethod
    def build_request_tcp(
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
        read_until: typing.Optional[str] = None
    ) -> dict[str, typing.Any]:
        """构造 TCP 协议的标准请求快照。"""
        return {
            "host"        : host,
            "port"        : int(port),
            "body_text"   : body_text,
            "sends"       : [str(item) for item in (sends or [])],
            "encoding"    : encoding,
            "timeout"     : float(timeout),
            "read_size"   : int(read_size),
            "close_write" : bool(close_write),
            "max_reads"   : int(max_reads),
            "read_until"  : read_until
        }

    @staticmethod
    def build_request_udp(
        *,
        host: str,
        port: int,
        body_text: str = "",
        encoding: str = "utf-8",
        timeout: float = 10.0,
        read_size: int = 4096
    ) -> dict[str, typing.Any]:
        """构造 UDP 协议的标准请求快照。"""
        return {
            "host"      : host,
            "port"      : int(port),
            "body_text" : body_text,
            "encoding"  : encoding,
            "timeout"   : float(timeout),
            "read_size" : int(read_size)
        }

    @staticmethod
    def build_request_smtp(
        *,
        host: str,
        port: int,
        action: str = "noop",
        username: typing.Optional[str] = None,
        use_ssl: bool = False,
        use_tls: bool = False,
        from_addr: typing.Optional[str] = None,
        to_addrs: typing.Optional[list[str]] = None,
        subject: typing.Optional[str] = None,
        body_text: typing.Optional[str] = None,
        html_body: typing.Optional[str] = None,
        attachments: typing.Optional[list[dict[str, typing.Any]]] = None,
        timeout: float = 15.0
    ) -> dict[str, typing.Any]:
        """构造 SMTP 协议的标准请求快照。"""
        return {
            "host"        : host,
            "port"        : int(port),
            "action"      : action,
            "username"    : username,
            "use_ssl"     : bool(use_ssl),
            "use_tls"     : bool(use_tls),
            "from_addr"   : from_addr,
            "to_addrs"    : list(to_addrs or []),
            "subject"     : subject,
            "body_text"   : body_text,
            "html_body"   : html_body,
            "attachments" : PackBuilder.attachments_meta(attachments),
            "timeout"     : float(timeout)
        }

    @staticmethod
    def build_request_imap(
        *,
        host: str,
        port: int,
        username: str,
        action: str = "search",
        mailbox: str = "INBOX",
        criteria: str = "ALL",
        message_set: str = "1",
        fetch_parts: str = "(BODY.PEEK[])",
        parse_messages: bool = False,
        use_ssl: bool = True,
        timeout: float = 15.0,
        media_path: typing.Optional[str] = None
    ) -> dict[str, typing.Any]:
        """构造 IMAP 协议的标准请求快照。"""
        return {
            "host"           : host,
            "port"           : int(port),
            "username"       : username,
            "action"         : action,
            "mailbox"        : mailbox,
            "criteria"       : criteria,
            "message_set"    : message_set,
            "fetch_parts"    : fetch_parts,
            "parse_messages" : bool(parse_messages),
            "use_ssl"        : bool(use_ssl),
            "timeout"        : float(timeout),
            "media_path"     : media_path
        }

    @staticmethod
    def build_request_ftp(
        *,
        host: str,
        port: int,
        username: str = "anonymous",
        action: str = "list",
        path: str = ".",
        payload_text: typing.Optional[str] = None,
        payload_base64: typing.Optional[str] = None,
        encoding: str = "utf-8",
        use_tls: bool = False,
        timeout: float = 15.0,
        media_path: typing.Optional[str] = None
    ) -> dict[str, typing.Any]:
        """构造 FTP 协议的标准请求快照。"""
        return {
            "host"           : host,
            "port"           : int(port),
            "username"       : username,
            "action"         : action,
            "path"           : path,
            "payload_text"   : payload_text,
            "payload_base64" : payload_base64,
            "encoding"       : encoding,
            "use_tls"        : bool(use_tls),
            "timeout"        : float(timeout),
            "media_path"     : media_path
        }

    @staticmethod
    def build_response_http_like(
        *,
        status: typing.Optional[int],
        headers: typing.Optional[dict[str, typing.Any]],
        elapsed_ms: int,
        body_text: typing.Optional[str],
        body_json: typing.Any,
        content_type: typing.Optional[str],
        content_length: typing.Optional[int],
        media: typing.Optional[list[dict[str, typing.Any]]] = None
    ) -> dict[str, typing.Any]:
        """构造 HTTP 类协议的标准响应快照。"""
        return {
            "status"         : status,
            "headers"        : dict(headers or {}),
            "elapsed_ms"     : elapsed_ms,
            "body_text"      : body_text,
            "body_json"      : body_json,
            "content_type"   : content_type,
            "content_length" : content_length,
            "media"          : media or []
        }

    @staticmethod
    def build_response_sse(
        *,
        status: typing.Optional[int],
        headers: typing.Optional[dict[str, typing.Any]],
        elapsed_ms: int,
        events: typing.Optional[list[dict[str, typing.Any]]] = None,
        content_type: typing.Optional[str] = None,
        content_length: typing.Optional[int] = None,
        media: typing.Optional[list[dict[str, typing.Any]]] = None
    ) -> dict[str, typing.Any]:
        """构造 SSE 协议的标准响应快照。"""
        return {
            "status"         : status,
            "headers"        : dict(headers or {}),
            "elapsed_ms"     : elapsed_ms,
            "events"         : events or [],
            "content_type"   : content_type,
            "content_length" : content_length,
            "body_text"      : None,
            "body_json"      : None,
            "media"          : media or []
        }

    @staticmethod
    def build_response_ws(
        *,
        elapsed_ms: int,
        messages: typing.Optional[list[str]] = None,
        error: typing.Optional[str] = None,
        media: typing.Optional[list[dict[str, typing.Any]]] = None
    ) -> dict[str, typing.Any]:
        """构造 WebSocket 协议的标准响应快照。"""
        return {
            "status"         : None,
            "headers"        : {},
            "elapsed_ms"     : elapsed_ms,
            "messages"       : messages or [],
            "error"          : error,
            "content_type"   : None,
            "content_length" : None,
            "body_text"      : None,
            "body_json"      : None,
            "media"          : media or []
        }

    @staticmethod
    def build_response_tcp(
        *,
        elapsed_ms: int,
        body_text: typing.Optional[str],
        content_length: int,
        body_hex: str,
        messages: typing.Optional[list[str]] = None,
        remote: typing.Optional[dict[str, typing.Any]] = None
    ) -> dict[str, typing.Any]:
        """构造 TCP 协议的标准响应快照。"""
        return {
            "status"         : None,
            "headers"        : {},
            "elapsed_ms"     : elapsed_ms,
            "body_text"      : body_text,
            "body_json"      : None,
            "content_type"   : "application/octet-stream",
            "content_length" : content_length,
            "body_hex"       : body_hex,
            "messages"       : messages or [],
            "remote"         : dict(remote or {})
        }

    @staticmethod
    def build_response_udp(
        *,
        elapsed_ms: int,
        body_text: typing.Optional[str],
        content_length: int,
        body_hex: str,
        remote: typing.Optional[dict[str, typing.Any]] = None
    ) -> dict[str, typing.Any]:
        """构造 UDP 协议的标准响应快照。"""
        return {
            "status"         : None,
            "headers"        : {},
            "elapsed_ms"     : elapsed_ms,
            "body_text"      : body_text,
            "body_json"      : None,
            "content_type"   : "application/octet-stream",
            "content_length" : content_length,
            "body_hex"       : body_hex,
            "remote"         : dict(remote or {})
        }

    @staticmethod
    def build_response_smtp(
        *,
        elapsed_ms: int,
        result: typing.Optional[dict[str, typing.Any]] = None
    ) -> dict[str, typing.Any]:
        """构造 SMTP 协议的标准响应快照。"""
        result = dict(result or {})
        return {
            "status"         : None,
            "headers"        : {},
            "elapsed_ms"     : elapsed_ms,
            "body_text"      : None,
            "body_json"      : None,
            "content_type"   : "application/json",
            "content_length" : None,
            "action"         : result.get("action"),
            "ehlo"           : result.get("ehlo"),
            "starttls"       : result.get("starttls"),
            "ehlo_after_tls" : result.get("ehlo_after_tls"),
            "login"          : result.get("login"),
            "noop"           : result.get("noop"),
            "send"           : result.get("send"),
            "html"           : result.get("html"),
            "attachments"    : result.get("attachments") or []
        }

    @staticmethod
    def build_response_imap(
        *,
        elapsed_ms: int,
        result: typing.Optional[dict[str, typing.Any]] = None,
        media: typing.Optional[list[dict[str, typing.Any]]] = None
    ) -> dict[str, typing.Any]:
        """构造 IMAP 协议的标准响应快照。"""
        result = dict(result or {})
        return {
            "status"          : None,
            "headers"         : {},
            "elapsed_ms"      : elapsed_ms,
            "body_text"       : None,
            "body_json"       : None,
            "content_type"    : "application/json",
            "content_length"  : None,
            "login"           : result.get("login"),
            "select"          : result.get("select"),
            "search"          : result.get("search"),
            "fetch"           : result.get("fetch"),
            "noop"            : result.get("noop"),
            "parsed_messages" : result.get("parsed_messages") or [],
            "media"           : media or []
        }

    @staticmethod
    def build_response_ftp(
        *,
        elapsed_ms: int,
        result: typing.Optional[dict[str, typing.Any]] = None,
        media: typing.Optional[list[dict[str, typing.Any]]] = None
    ) -> dict[str, typing.Any]:
        """构造 FTP 协议的标准响应快照。"""
        result = dict(result or {})
        return {
            "status"          : None,
            "headers"         : {},
            "elapsed_ms"      : elapsed_ms,
            "body_text"       : None,
            "body_json"       : None,
            "content_type"    : "application/json",
            "content_length"  : None,
            "welcome"         : result.get("welcome"),
            "list"            : result.get("list"),
            "download_text"   : result.get("download_text"),
            "download_binary" : result.get("download_binary"),
            "upload_text"     : result.get("upload_text"),
            "upload_binary"   : result.get("upload_binary"),
            "delete"          : result.get("delete"),
            "mkdir"           : result.get("mkdir"),
            "media"           : media or []
        }


if __name__ == '__main__':
    pass
