# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import httpx
import typing
from dataclasses import dataclass
from engine.channel import Channel
from mind_nova.identifiers import normalize_turn_id
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


@dataclass(frozen=True, slots=True)
class TurnReconcileResponse(object):
    """描述服务端对未确认轮次输入的归属快照。"""
    turn_id: str
    turn_status: str
    committed_ids: tuple[str, ...]
    pending_ids: tuple[str, ...]
    retry_ids: tuple[str, ...]
    unknown_ids: tuple[str, ...]


async def steer_turn(
    *,
    cid: str,
    sid: str,
    turn_id: str,
    turn_input: TurnInput,
    timeout: float = 10.0,
) -> TurnControlResponse:
    """向活动逻辑轮次提交一项引导输入。"""
    normalized_turn_id = _turn_id(turn_id)

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


async def interrupt_turn(
    *,
    cid: str,
    sid: str,
    turn_id: str,
    timeout: float = 10.0
) -> TurnControlResponse:
    """请求服务端中断匹配的活动逻辑轮次。"""
    normalized_turn_id = _turn_id(turn_id)

    return await _post_control(
        "/turn/interrupt",
        cid=cid,
        sid=sid,
        payload={"turn_id": normalized_turn_id},
        expected_turn_id=normalized_turn_id,
        expected_message_id="interrupt",
        timeout=timeout,
    )


async def reconcile_turn_inputs(
    *,
    cid: str,
    sid: str,
    turn_id: str,
    client_message_ids: typing.Iterable[str],
    timeout: float = 10.0
) -> TurnReconcileResponse:
    """查询未确认轮次输入的服务端归属。"""
    normalized_turn_id = _turn_id(turn_id)

    requested_ids = tuple(dict.fromkeys(
        value
        for item in client_message_ids
        for value in [str(item or "").strip()]
        if value
    ))

    if not requested_ids:
        raise TurnControlRequestError(
            "turn reconciliation requires client_message_ids"
        )

    body = await _post_json(
        "/turn/reconcile",
        cid=cid,
        sid=sid,
        payload={
            "turn_id": normalized_turn_id,
            "client_message_ids": list(requested_ids),
        },
        timeout=timeout,
    )

    response_turn_id = str(body.get("turn_id") or "").strip()
    turn_status      = str(body.get("turn_status") or "").strip()

    classifications = {
        field: _response_ids(body, field)
        for field in (
            "committed_ids",
            "pending_ids",
            "retry_ids",
            "unknown_ids",
        )
    }

    classified_ids = [
        item
        for values in classifications.values()
        for item in values
    ]

    if (
        response_turn_id != normalized_turn_id
        or not turn_status
        or len(classified_ids) != len(set(classified_ids))
        or set(classified_ids) != set(requested_ids)
        or (turn_status == "settled" and classifications["pending_ids"])
    ):
        raise TurnControlRequestError(
            "turn reconciliation response does not match request"
        )

    return TurnReconcileResponse(
        turn_id=response_turn_id,
        turn_status=turn_status,
        committed_ids=classifications["committed_ids"],
        pending_ids=classifications["pending_ids"],
        retry_ids=classifications["retry_ids"],
        unknown_ids=classifications["unknown_ids"],
    )


def _turn_id(value: str) -> str:
    """按当前服务端协议校验逻辑轮次标识。"""
    try:
        return normalize_turn_id(value)
    except ValueError as error:
        raise TurnControlRequestError(str(error)) from error


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
    if not expected_turn_id:
        raise TurnControlRequestError("turn control requires turn_id")
    if not expected_message_id:
        raise TurnControlRequestError(
            "turn control requires client_message_id"
        )

    body = await _post_json(
        path,
        cid=cid,
        sid=sid,
        payload=payload,
        timeout=timeout,
    )

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


async def _post_json(
    path: str,
    *,
    cid: str,
    sid: str,
    payload: dict[str, typing.Any],
    timeout: float,
) -> dict[str, typing.Any]:
    """发送轮次请求并返回已经校验的对象响应。"""
    normalized_cid = str(cid or "").strip()
    normalized_sid = str(sid or "").strip()

    if not normalized_cid:
        raise TurnControlRequestError("turn control requires cid")
    if not normalized_sid:
        raise TurnControlRequestError("turn control requires sid")

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                service_endpoints.endpoint(path),
                params={"cid": normalized_cid, "sid": normalized_sid},
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
    return body


def _response_ids(
    body: dict[str, typing.Any],
    field: str
) -> tuple[str, ...]:
    """读取响应中的消息标识列表。"""
    raw = body.get(field)
    if not isinstance(raw, list):
        raise TurnControlRequestError(
            "turn reconciliation returned an invalid response"
        )

    values = tuple(str(item or "").strip() for item in raw)

    if any(not item for item in values) or len(values) != len(set(values)):
        raise TurnControlRequestError(
            "turn reconciliation returned an invalid response"
        )

    return values


if __name__ == '__main__':
    pass
