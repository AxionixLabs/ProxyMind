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


if __name__ == '__main__':
    pass
