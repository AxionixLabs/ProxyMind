# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass

import httpx

from protocol.schema.identifiers import (
    normalize_turn_id,
    resolve_request_id,
)
from protocol.schema.turn_inputs import TurnInput
from protocol.transport.auth import build_service_headers
from protocol.transport.endpoints import service_endpoints
from protocol.transport.reliable import post_json_reliably

TurnControlStatus = typing.Literal[
    "accepted",
    "turn_not_active",
    "turn_not_steerable",
    "turn_mismatch",
    "duplicate",
]

TurnRuntimeStatus = typing.Literal[
    "queued",
    "running",
    "waiting_tool",
    "waiting_approval",
    "waiting_user",
    "reconciliation_required",
    "finalizing",
    "completed",
    "failed",
    "interrupted",
    "cancelled",
]

_CONTROL_STATUSES: typing.Final[set[str]] = {
    "accepted",
    "turn_not_active",
    "turn_not_steerable",
    "turn_mismatch",
    "duplicate",
}

_TURN_RUNTIME_STATUSES: typing.Final[set[str]] = {
    "queued",
    "running",
    "waiting_tool",
    "waiting_approval",
    "waiting_user",
    "reconciliation_required",
    "finalizing",
    "completed",
    "failed",
    "interrupted",
    "cancelled",
}

_TERMINAL_TURN_STATUSES: typing.Final[set[str]] = {
    "completed",
    "failed",
    "interrupted",
    "cancelled",
}


class TurnControlRequestError(Exception):
    """描述轮次控制请求未得到有效响应。"""


class TurnStatusRequestError(Exception):
    """描述权威轮次状态请求失败或返回无效响应。"""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
    ) -> None:
        """保留请求失败对应的 HTTP 状态码。"""
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class TurnControlResponse(object):
    """描述服务端对轮次控制请求的处理结果。"""
    status: TurnControlStatus
    request_id: str
    turn_id: str
    client_message_id: str | None


@dataclass(frozen=True, slots=True)
class TurnReconcileResponse(object):
    """描述服务端对未确认轮次输入的归属快照。"""
    turn_id: str
    turn_status: str
    committed_ids: tuple[str, ...]
    pending_ids: tuple[str, ...]
    retry_ids: tuple[str, ...]
    unknown_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TurnStatusSnapshot(object):
    """描述服务端持久化逻辑轮次的权威状态。"""
    cid: str
    sid: str
    turn_id: str
    run_id: str
    status: TurnRuntimeStatus
    terminal: bool
    attempt: int
    version: int
    last_event_seq: int
    created_at: float
    updated_at: float
    error: str


async def get_turn_status(
    *,
    cid: str,
    sid: str,
    turn_id: str,
    timeout: float = 10.0,
) -> TurnStatusSnapshot:
    """查询持久化逻辑轮次的权威状态。"""
    normalized_cid = str(cid or "").strip()
    normalized_sid = str(sid or "").strip()

    if not normalized_cid:
        raise TurnStatusRequestError("turn status requires cid")
    if not normalized_sid:
        raise TurnStatusRequestError("turn status requires sid")
    try:
        normalized_turn_id = normalize_turn_id(turn_id)
    except ValueError as error:
        raise TurnStatusRequestError(str(error)) from error

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(
                service_endpoints.endpoint("/turn/status"),
                params={
                    "cid": normalized_cid,
                    "sid": normalized_sid,
                    "turn_id": normalized_turn_id,
                },
                headers=build_service_headers(),
            )
            response.raise_for_status()
    except httpx.HTTPStatusError as error:
        raise TurnStatusRequestError(
            "turn status request failed",
            status_code=error.response.status_code,
        ) from error
    except httpx.HTTPError as error:
        raise TurnStatusRequestError("turn status request failed") from error

    try:
        body = response.json()
    except (TypeError, ValueError) as error:
        raise TurnStatusRequestError(
            "turn status returned an invalid response"
        ) from error

    return _status_snapshot(
        body,
        expected_cid=normalized_cid,
        expected_sid=normalized_sid,
        expected_turn_id=normalized_turn_id,
    )


async def steer_turn(
    *,
    cid: str,
    sid: str,
    turn_id: str,
    turn_input: TurnInput,
    request_id: str | None = None,
    timeout: float = 10.0
) -> TurnControlResponse:
    """向活动逻辑轮次提交一项引导输入。"""
    normalized_turn_id = _turn_id(turn_id)

    payload = {
        "request_id": _command_request_id(request_id, prefix="steer"),
        "turn_id": normalized_turn_id,
        "client_message_id": turn_input.client_message_id,
        "input": turn_input.request_input(),
    }

    return await _post_control(
        "/turn/steer",
        cid=cid,
        sid=sid,
        payload=payload,
        expected_request_id=payload["request_id"],
        expected_turn_id=normalized_turn_id,
        expected_message_id=turn_input.client_message_id,
        timeout=timeout,
    )


async def interrupt_turn(
    *,
    cid: str,
    sid: str,
    turn_id: str,
    request_id: str | None = None,
    timeout: float = 10.0
) -> TurnControlResponse:
    """请求服务端中断匹配的活动逻辑轮次。"""
    normalized_turn_id = _turn_id(turn_id)

    normalized_request_id = _command_request_id(
        request_id,
        prefix="interrupt",
    )

    return await _post_control(
        "/turn/interrupt",
        cid=cid,
        sid=sid,
        payload={
            "request_id": normalized_request_id,
            "turn_id": normalized_turn_id,
        },
        expected_request_id=normalized_request_id,
        expected_turn_id=normalized_turn_id,
        expected_message_id=None,
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
    turn_status = str(body.get("turn_status") or "").strip()

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


def _command_request_id(value: str | None, *, prefix: str) -> str:
    """读取或生成一项轮次控制命令的幂等标识。"""
    try:
        return resolve_request_id(value, prefix=prefix)
    except ValueError as error:
        raise TurnControlRequestError(str(error)) from error


async def _post_control(
    path: str,
    *,
    cid: str,
    sid: str,
    payload: dict[str, typing.Any],
    expected_request_id: str,
    expected_turn_id: str,
    expected_message_id: str | None,
    timeout: float
) -> TurnControlResponse:
    """发送并校验一项轮次控制请求。"""
    if not expected_turn_id:
        raise TurnControlRequestError("turn control requires turn_id")

    if expected_message_id is not None and not expected_message_id:
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

    response_request_id = str(body.get("request_id") or "").strip()
    response_turn_id = str(body.get("turn_id") or "").strip()
    raw_message_id = body.get("client_message_id")

    response_message_id = (
        str(raw_message_id or "").strip()
        if raw_message_id is not None
        else None
    )

    if (
        status not in _CONTROL_STATUSES
        or response_request_id != expected_request_id
        or response_turn_id != expected_turn_id
        or response_message_id != expected_message_id
    ):
        raise TurnControlRequestError("turn control response does not match request")

    return TurnControlResponse(
        status=typing.cast(TurnControlStatus, status),
        request_id=response_request_id,
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
        response = await post_json_reliably(
            service_endpoints.endpoint(path),
            params={"cid": normalized_cid, "sid": normalized_sid},
            headers=build_service_headers(),
            payload=payload,
            timeout=timeout,
            client_factory=httpx.AsyncClient,
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


def _status_snapshot(
    body: typing.Any,
    *,
    expected_cid: str,
    expected_sid: str,
    expected_turn_id: str
) -> TurnStatusSnapshot:
    """校验状态响应并构建不可变快照。"""
    invalid_message = "turn status returned an invalid response"
    if not isinstance(body, dict) or body.get("ok") is not True:
        raise TurnStatusRequestError(invalid_message)

    run_id = body.get("run_id")
    status = body.get("status")
    terminal = body.get("terminal")
    error = body.get("error")
    attempt = body.get("attempt")
    version = body.get("version")
    event_seq = body.get("last_event_seq")
    created = body.get("created_at")
    updated = body.get("updated_at")

    numeric_values = (attempt, version, event_seq, created, updated)
    if (
        body.get("cid") != expected_cid
        or body.get("sid") != expected_sid
        or body.get("turn_id") != expected_turn_id
        or not isinstance(run_id, str)
        or not isinstance(status, str)
        or status not in _TURN_RUNTIME_STATUSES
        or not isinstance(terminal, bool)
        or terminal != (status in _TERMINAL_TURN_STATUSES)
        or not isinstance(error, str)
        or any(
        isinstance(value, bool) or not isinstance(value, (int, float))
        for value in numeric_values
    )
        or not isinstance(attempt, int)
        or attempt < 1
        or not isinstance(version, int)
        or version < 1
        or not isinstance(event_seq, int)
        or event_seq < 0
    ):
        raise TurnStatusRequestError(invalid_message)

    return TurnStatusSnapshot(
        cid=expected_cid,
        sid=expected_sid,
        turn_id=expected_turn_id,
        run_id=run_id,
        status=typing.cast(TurnRuntimeStatus, status),
        terminal=terminal,
        attempt=attempt,
        version=version,
        last_event_seq=event_seq,
        created_at=float(created),
        updated_at=float(updated),
        error=error,
    )


def _response_ids(body: dict[str, typing.Any], field: str) -> tuple[str, ...]:
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
