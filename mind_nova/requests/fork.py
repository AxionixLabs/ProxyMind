# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import httpx
import typing
from copy import deepcopy
from dataclasses import dataclass
from engine.channel import Channel
from mind_nova.modes import RunMode
from mind_nova.requests.payload import resolve_transport_mode
from mind_nova.services import service_endpoints


class ConversationForkRequestError(Exception):
    """描述远端会话分支请求的可展示失败。"""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 0,
        code: str = "",
        retryable: bool = False
    ) -> None:
        super().__init__(message)

        self.message     = message
        self.status_code = int(status_code or 0)
        self.code        = str(code or "").strip()
        self.retryable   = bool(retryable)


@dataclass(frozen=True, slots=True)
class ResubmittablePrompt(object):
    """保存服务端返回的可重提交输入。"""
    message: str
    attachments: tuple[dict[str, typing.Any], ...]
    extras: dict[str, typing.Any]


def build_fork_payload(
    *,
    mode: RunMode,
    cid: str,
    sid: str,
    request_id: str,
    before_turn_id: str | None = None
) -> dict[str, str]:
    """构建远端会话分支请求载荷。"""
    payload = {
        "request_id" : str(request_id or "").strip(),
        "mode"       : resolve_transport_mode(mode),
        "cid"        : str(cid or "").strip(),
        "sid"        : str(sid or "").strip(),
    }
    boundary = str(before_turn_id or "").strip()
    if boundary:
        payload["before_turn_id"] = boundary
    return payload


async def request_conversation_fork(
    *,
    mode: RunMode,
    cid: str,
    sid: str,
    request_id: str,
    before_turn_id: str | None = None,
    require_prompt: bool = True,
    timeout: float = 30.0
) -> dict[str, typing.Any]:
    """请求服务端复制当前会话上下文。"""
    payload = build_fork_payload(
        mode=mode,
        cid=cid,
        sid=sid,
        request_id=request_id,
        before_turn_id=before_turn_id,
    )

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                service_endpoints.endpoint("/fork"),
                headers=Channel.make_headers(),
                json=payload,
            )
    except httpx.RequestError as error:
        raise ConversationForkRequestError(
            "Conversation fork request failed. Try /fork again.",
            retryable=True,
        ) from error

    body = _response_object(response)
    if response.status_code >= 400:
        code, message = _error_detail(body)
        if response.status_code == 409 and code == "source_busy":
            raise ConversationForkRequestError(
                "Conversation is busy. Try /fork again after the current turn finishes.",
                status_code=response.status_code,
                code=code,
                retryable=True,
            )
        raise ConversationForkRequestError(
            message or f"Conversation fork failed with HTTP {response.status_code}.",
            status_code=response.status_code,
            code=code,
            retryable=response.status_code in {408, 429} or response.status_code >= 500,
        )

    data = body.get("data")
    if body.get("ok") is not True or not isinstance(data, dict):
        raise ConversationForkRequestError(
            "Conversation fork returned an invalid response.",
        )

    expected = {
        "request_id" : payload["request_id"],
        "mode"       : payload["mode"],
        "source_cid" : payload["cid"],
        "source_sid" : payload["sid"],
    }

    if "before_turn_id" in payload:
        expected["before_turn_id"] = payload["before_turn_id"]

    if any(str(data.get(key) or "").strip() != value for key, value in expected.items()):
        raise ConversationForkRequestError(
            "Conversation fork response does not match the source request.",
        )

    target_cid   = str(data.get("cid") or "").strip()
    target_sid   = str(data.get("sid") or "").strip()
    copied_items = data.get("copied_items")
    copied_turns = data.get("copied_turns")
    bounded      = "before_turn_id" in payload

    if (
        not target_cid
        or not target_sid
        or isinstance(copied_items, bool)
        or not isinstance(copied_items, int)
        or copied_items < (0 if bounded else 1)
        or (
            bounded
            and (
                isinstance(copied_turns, bool)
                or not isinstance(copied_turns, int)
                or copied_turns < 0
            )
        )
    ):
        raise ConversationForkRequestError(
            "Conversation fork returned an invalid response.",
        )

    prompt = data.get("prompt")

    if bounded:
        try:
            prompt = _parse_resubmittable_prompt(prompt)
        except (TypeError, ValueError) as error:
            if not require_prompt:
                prompt = None
            else:
                raise ConversationForkRequestError(
                    "Conversation fork returned an invalid prompt.",
                ) from error
    elif prompt is not None:
        raise ConversationForkRequestError(
            "Conversation fork returned an unexpected prompt.",
        )

    data = dict(data)
    data["prompt"] = prompt

    return data


def _parse_resubmittable_prompt(value: typing.Any) -> ResubmittablePrompt:
    """校验并复制服务端返回的可重提交输入。"""
    if not isinstance(value, dict):
        raise TypeError("fork prompt must be an object")

    message     = value.get("message")
    attachments = value.get("attachments")
    extras      = value.get("extras")

    if attachments is None:
        attachments = []
    if extras is None:
        extras = {}

    if not isinstance(message, str):
        raise TypeError("fork prompt message must be a string")
    if not isinstance(attachments, list):
        raise TypeError("fork prompt attachments must be a list")
    if not isinstance(extras, dict):
        raise TypeError("fork prompt extras must be an object")
    if any(not isinstance(item, dict) for item in attachments):
        raise TypeError("fork prompt attachments must contain objects")

    return ResubmittablePrompt(
        message=message,
        attachments=tuple(deepcopy(item) for item in attachments),
        extras=deepcopy(extras),
    )


def _response_object(response: httpx.Response) -> dict[str, typing.Any]:
    """安全读取 JSON 对象响应。"""
    try:
        value = response.json()
    except (ValueError, TypeError):
        return {}

    return value if isinstance(value, dict) else {}


def _error_detail(body: dict[str, typing.Any]) -> tuple[str, str]:
    """提取结构化错误代码和消息。"""
    details = body.get("details")
    if isinstance(details, dict):
        return (
            str(details.get("code") or "").strip(),
            str(details.get("message") or "").strip(),
        )

    return "", str(details or "").strip()


if __name__ == '__main__':
    pass
