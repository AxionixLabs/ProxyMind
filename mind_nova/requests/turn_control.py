# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import httpx
import typing
from dataclasses import dataclass
from engine.channel import Channel
from mind_nova.services import service_endpoints
from mind_nova.turn_inputs import TurnInput


TurnControlStatus = typing.Literal[
    "accepted",
    "turn_not_active",
    "turn_not_steerable",
    "turn_mismatch",
    "duplicate",
]

_CONTROL_STATUSES: typing.Final[set[str]] = {
    "accepted",
    "turn_not_active",
    "turn_not_steerable",
    "turn_mismatch",
    "duplicate",
}


class TurnControlRequestError(Exception):
    """描述轮次控制请求未得到有效响应。"""


@dataclass(frozen=True, slots=True)
class TurnControlResponse(object):
    """描述服务端对轮次控制请求的处理结果。"""
    status: TurnControlStatus
    turn_id: str
    client_message_id: str


async def steer_turn(
    *,
    cid: str,
    sid: str,
    turn_id: str,
    turn_input: TurnInput,
    timeout: float = 10.0,
) -> TurnControlResponse:
    """向活动逻辑轮次提交一项引导输入。"""
    normalized_turn_id = str(turn_id or "").strip()

    payload = {
        "turn_id": normalized_turn_id,
        "client_message_id": turn_input.client_message_id,
        "input": turn_input.request_input(),
    }

    return await _post_control(
        "/turn/steer",
        cid=cid,
        sid=sid,
        payload=payload,
        expected_turn_id=normalized_turn_id,
        expected_message_id=turn_input.client_message_id,
        timeout=timeout,
    )


async def follow_up_turn(
    *,
    cid: str,
    sid: str,
    turn_id: str,
    turn_input: TurnInput,
    timeout: float = 10.0
) -> TurnControlResponse:
    """向活动逻辑轮次提交一项后续输入。"""
    normalized_turn_id = str(turn_id or "").strip()

    payload = {
        "turn_id": normalized_turn_id,
        "client_message_id": turn_input.client_message_id,
        "input": turn_input.request_input(),
    }

    return await _post_control(
        "/turn/follow-up",
        cid=cid,
        sid=sid,
        payload=payload,
        expected_turn_id=normalized_turn_id,
        expected_message_id=turn_input.client_message_id,
        timeout=timeout,
    )


async def interrupt_turn(
    *,
    cid: str,
    sid: str,
    turn_id: str,
    timeout: float = 10.0
) -> TurnControlResponse:
    """请求服务端中断匹配的活动逻辑轮次。"""
    normalized_turn_id = str(turn_id or "").strip()

    return await _post_control(
        "/turn/interrupt",
        cid=cid,
        sid=sid,
        payload={"turn_id": normalized_turn_id},
        expected_turn_id=normalized_turn_id,
        expected_message_id="interrupt",
        timeout=timeout,
    )


async def _post_control(
    path: str,
    *,
    cid: str,
    sid: str,
    payload: dict[str, typing.Any],
    expected_turn_id: str,
    expected_message_id: str,
    timeout: float
) -> TurnControlResponse:
    """发送并校验一项轮次控制请求。"""
    normalized_cid = str(cid or "").strip()
    normalized_sid = str(sid or "").strip()

    if not normalized_cid:
        raise TurnControlRequestError("turn control requires cid")
    if not normalized_sid:
        raise TurnControlRequestError("turn control requires sid")
    if not expected_turn_id:
        raise TurnControlRequestError("turn control requires turn_id")
    if not expected_message_id:
        raise TurnControlRequestError(
            "turn control requires client_message_id"
        )

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                service_endpoints.endpoint(path),
                params={
                    "cid": normalized_cid,
                    "sid": normalized_sid,
                },
                headers=Channel.make_headers(),
                json=payload,
            )
            response.raise_for_status()
    except httpx.HTTPError as error:
        raise TurnControlRequestError(
            f"turn control request failed: {path}"
        ) from error

    try:
        body = response.json()
    except (TypeError, ValueError) as error:
        raise TurnControlRequestError(
            "turn control returned an invalid response"
        ) from error

    if not isinstance(body, dict) or body.get("ok") is not True:
        raise TurnControlRequestError("turn control returned an invalid response")

    status = str(body.get("status") or "").strip()

    response_turn_id    = str(body.get("turn_id") or "").strip()
    response_message_id = str(body.get("client_message_id") or "").strip()

    if (
        status not in _CONTROL_STATUSES
        or response_turn_id != expected_turn_id
        or response_message_id != expected_message_id
    ):
        raise TurnControlRequestError("turn control response does not match request")

    return TurnControlResponse(
        status=typing.cast(TurnControlStatus, status),
        turn_id=response_turn_id,
        client_message_id=response_message_id,
    )


if __name__ == '__main__':
    pass
