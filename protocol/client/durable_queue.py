# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

import httpx

from protocol.client.payload import build_chat_payload
from protocol.schema.durable_queue import (
    QueueMutationResponse,
    QueueReorderResponse,
    QueueSnapshot,
    QueueStartResponse,
    parse_queue_mutation,
    parse_queue_reorder,
    parse_queue_snapshot,
    parse_queue_start,
)
from protocol.schema.json_value import (
    JsonObject,
    JsonValue,
)
from protocol.schema.identifiers import (
    normalize_submission_id,
    normalize_turn_id,
    resolve_request_id,
)
from protocol.transport.auth import build_service_headers
from protocol.transport.endpoints import service_endpoints
from protocol.transport.reliable import (
    get_json_reliably,
    is_retryable_status,
    post_json_reliably,
    send_json_reliably,
)


class DurableQueueRequestError(Exception):
    """描述持久队列请求失败或响应不符合正式契约。"""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str = "",
        retryable: bool = False,
    ) -> None:
        """保存稳定错误代码和 HTTP 状态。"""
        super().__init__(message)
        self.status_code = status_code
        self.code = str(code or "").strip()
        self.retryable = bool(retryable)


async def add_queue_submission(
    *,
    cid: str,
    sid: str,
    turn_id: str,
    client_message_id: str,
    submission_id: str,
    pref_config: dict[str, JsonValue],
    message: str,
    tools: list[dict[str, JsonValue]],
    attachments: list[dict[str, JsonValue]] | None = None,
    environment_snapshot: dict[str, JsonValue] | None = None,
    metadata: dict[str, JsonValue] | None = None,
    options: dict[str, JsonValue] | None = None,
    request_id: str | None = None,
    timeout: float = 10.0,
) -> QueueMutationResponse:
    """把冻结的 AgentRequest 显式加入服务端持久队列。"""
    coordinates = _session_coordinates(cid=cid, sid=sid)
    normalized_submission_id = _submission_id(submission_id)
    normalized_request_id = _request_id(request_id, prefix="queue_add")
    normalized_message_id = _client_message_id(client_message_id)
    request = await _agent_request(
        cid=coordinates[0],
        sid=coordinates[1],
        turn_id=turn_id,
        pref_config=pref_config,
        message=message,
        tools=tools,
        attachments=attachments,
        environment_snapshot=environment_snapshot,
        metadata=metadata,
        options=options,
    )
    body = await _request_json(
        "POST",
        "",
        cid=coordinates[0],
        sid=coordinates[1],
        payload={
            "request_id": normalized_request_id,
            "submission_id": normalized_submission_id,
            "client_message_id": normalized_message_id,
            "request": request,
        },
        timeout=timeout,
    )
    try:
        return parse_queue_mutation(
            body,
            expected_cid=coordinates[0],
            expected_sid=coordinates[1],
            expected_request_id=normalized_request_id,
            expected_submission_id=normalized_submission_id,
        )
    except (TypeError, ValueError) as error:
        raise DurableQueueRequestError("queue add returned an invalid response") from error


async def list_queue_submissions(
    *,
    cid: str,
    sid: str,
    timeout: float = 10.0,
) -> QueueSnapshot:
    """读取服务端拥有的当前持久队列快照。"""
    coordinates = _session_coordinates(cid=cid, sid=sid)
    body = await _request_json(
        "GET",
        "",
        cid=coordinates[0],
        sid=coordinates[1],
        payload=None,
        timeout=timeout,
    )
    try:
        return parse_queue_snapshot(
            body,
            expected_cid=coordinates[0],
            expected_sid=coordinates[1],
        )
    except (TypeError, ValueError) as error:
        raise DurableQueueRequestError("queue list returned an invalid response") from error


async def update_queue_submission(
    *,
    cid: str,
    sid: str,
    submission_id: str,
    turn_id: str,
    pref_config: dict[str, JsonValue],
    message: str,
    tools: list[dict[str, JsonValue]],
    attachments: list[dict[str, JsonValue]] | None = None,
    environment_snapshot: dict[str, JsonValue] | None = None,
    metadata: dict[str, JsonValue] | None = None,
    options: dict[str, JsonValue] | None = None,
    request_id: str | None = None,
    timeout: float = 10.0,
) -> QueueMutationResponse:
    """原子替换尚未启动的持久队列输入快照。"""
    coordinates = _session_coordinates(cid=cid, sid=sid)
    normalized_submission_id = _submission_id(submission_id)
    normalized_request_id = _request_id(request_id, prefix="queue_update")
    request = await _agent_request(
        cid=coordinates[0],
        sid=coordinates[1],
        turn_id=turn_id,
        pref_config=pref_config,
        message=message,
        tools=tools,
        attachments=attachments,
        environment_snapshot=environment_snapshot,
        metadata=metadata,
        options=options,
    )
    body = await _request_json(
        "PATCH",
        f"/{normalized_submission_id}",
        cid=coordinates[0],
        sid=coordinates[1],
        payload={
            "request_id": normalized_request_id,
            "request": request,
        },
        timeout=timeout,
    )
    try:
        return parse_queue_mutation(
            body,
            expected_cid=coordinates[0],
            expected_sid=coordinates[1],
            expected_request_id=normalized_request_id,
            expected_submission_id=normalized_submission_id,
        )
    except (TypeError, ValueError) as error:
        raise DurableQueueRequestError(
            "queue update returned an invalid response"
        ) from error


async def delete_queue_submission(
    *,
    cid: str,
    sid: str,
    submission_id: str,
    request_id: str | None = None,
    timeout: float = 10.0,
) -> QueueMutationResponse:
    """删除尚未启动的持久队列提交。"""
    coordinates = _session_coordinates(cid=cid, sid=sid)
    normalized_submission_id = _submission_id(submission_id)
    normalized_request_id = _request_id(request_id, prefix="queue_delete")
    body = await _request_json(
        "DELETE",
        f"/{normalized_submission_id}",
        cid=coordinates[0],
        sid=coordinates[1],
        payload={"request_id": normalized_request_id},
        timeout=timeout,
    )
    try:
        return parse_queue_mutation(
            body,
            expected_cid=coordinates[0],
            expected_sid=coordinates[1],
            expected_request_id=normalized_request_id,
            expected_submission_id=normalized_submission_id,
        )
    except (TypeError, ValueError) as error:
        raise DurableQueueRequestError(
            "queue delete returned an invalid response"
        ) from error


async def reorder_queue_submissions(
    *,
    cid: str,
    sid: str,
    submission_ids: typing.Sequence[str],
    request_id: str | None = None,
    timeout: float = 10.0,
) -> QueueReorderResponse:
    """原子提交服务端持久队列的完整新顺序。"""
    coordinates = _session_coordinates(cid=cid, sid=sid)
    normalized_ids = tuple(_submission_id(value) for value in submission_ids)
    if not normalized_ids:
        raise DurableQueueRequestError("queue reorder requires submission_ids")
    if len(normalized_ids) > 256:
        raise DurableQueueRequestError("queue reorder accepts at most 256 submissions")
    if len(normalized_ids) != len(set(normalized_ids)):
        raise DurableQueueRequestError("queue reorder submission_ids must be unique")
    normalized_request_id = _request_id(request_id, prefix="queue_reorder")
    body = await _request_json(
        "POST",
        "/reorder",
        cid=coordinates[0],
        sid=coordinates[1],
        payload={
            "request_id": normalized_request_id,
            "submission_ids": list(normalized_ids),
        },
        timeout=timeout,
    )
    try:
        return parse_queue_reorder(
            body,
            expected_request_id=normalized_request_id,
            expected_submission_ids=normalized_ids,
        )
    except (TypeError, ValueError) as error:
        raise DurableQueueRequestError(
            "queue reorder returned an invalid response"
        ) from error


async def start_queue_submission(
    *,
    cid: str,
    sid: str,
    submission_id: str,
    request_id: str | None = None,
    timeout: float = 10.0,
) -> QueueStartResponse:
    """在 Session idle 时原子启动 FIFO 队首提交。"""
    coordinates = _session_coordinates(cid=cid, sid=sid)
    normalized_submission_id = _submission_id(submission_id)
    normalized_request_id = _request_id(request_id, prefix="queue_start")
    body = await _request_json(
        "POST",
        "/start",
        cid=coordinates[0],
        sid=coordinates[1],
        payload={
            "request_id": normalized_request_id,
            "submission_id": normalized_submission_id,
        },
        timeout=timeout,
    )
    try:
        return parse_queue_start(
            body,
            expected_request_id=normalized_request_id,
            expected_submission_id=normalized_submission_id,
        )
    except (TypeError, ValueError) as error:
        raise DurableQueueRequestError(
            "queue start returned an invalid response"
        ) from error


async def _agent_request(
    *,
    cid: str,
    sid: str,
    turn_id: str,
    pref_config: dict[str, JsonValue],
    message: str,
    tools: list[dict[str, JsonValue]],
    attachments: list[dict[str, JsonValue]] | None,
    environment_snapshot: dict[str, JsonValue] | None,
    metadata: dict[str, JsonValue] | None,
    options: dict[str, JsonValue] | None,
) -> JsonObject:
    """复用 chat builder 构造完全相同的 AgentRequest 快照。"""
    normalized_turn_id = _turn_id(turn_id)
    request_options = dict(options or {})
    request_metadata = dict(metadata or {})
    request_metadata.update({"cid": cid, "sid": sid})
    request_options["metadata"] = request_metadata
    request_options["turn_id"] = normalized_turn_id
    if environment_snapshot is not None:
        request_options["exec_env"] = dict(environment_snapshot)
    try:
        return await build_chat_payload(
            pref_config,
            message,
            tools,
            attachments=attachments,
            **request_options,
        )
    except (TypeError, ValueError) as error:
        raise DurableQueueRequestError(str(error)) from error


async def _request_json(
    method: str,
    path: str,
    *,
    cid: str,
    sid: str,
    payload: JsonObject | None,
    timeout: float,
) -> JsonValue:
    """可靠发送 Queue 请求并保留结构化服务端错误。"""
    url = service_endpoints.endpoint(f"/queue{path}")
    headers = build_service_headers()
    params = {"cid": cid, "sid": sid}
    try:
        if method == "GET":
            response = await get_json_reliably(
                url,
                params=params,
                headers=headers,
                timeout=timeout,
                client_factory=httpx.AsyncClient,
            )
        elif method == "POST":
            if payload is None:
                raise DurableQueueRequestError("queue POST requires a payload")
            response = await post_json_reliably(
                url,
                params=params,
                headers=headers,
                payload=payload,
                timeout=timeout,
                client_factory=httpx.AsyncClient,
            )
        else:
            if payload is None:
                raise DurableQueueRequestError("queue mutation requires a payload")
            response = await send_json_reliably(
                method,
                url,
                params=params,
                headers=headers,
                payload=payload,
                timeout=timeout,
                client_factory=httpx.AsyncClient,
            )
    except DurableQueueRequestError:
        raise
    except (httpx.TransportError, OSError) as error:
        raise DurableQueueRequestError(
            "durable queue transport failed",
            retryable=True,
        ) from error

    body = _response_body(response)
    if response.status_code >= 400:
        code, message = _error_detail(body)
        raise DurableQueueRequestError(
            message or "durable queue request failed",
            status_code=response.status_code,
            code=code,
            retryable=is_retryable_status(response.status_code),
        )
    return body


def _response_body(response: httpx.Response) -> JsonValue:
    """读取 JSON 响应，拒绝非 JSON 载荷。"""
    try:
        body = response.json()
    except (TypeError, ValueError) as error:
        raise DurableQueueRequestError(
            "durable queue returned an invalid response"
        ) from error
    if not isinstance(body, (dict, list, str, int, float, bool)) and body is not None:
        raise DurableQueueRequestError("durable queue returned an invalid response")
    return body


def _error_detail(body: JsonValue) -> tuple[str, str]:
    """从统一错误 envelope 读取稳定代码与消息。"""
    if not isinstance(body, dict):
        return "", ""
    detail = body.get("detail")
    if not isinstance(detail, dict):
        detail = body.get("details")
    if isinstance(detail, dict):
        code = detail.get("code")
        message = detail.get("message")
        return (
            code.strip() if isinstance(code, str) else "",
            message.strip() if isinstance(message, str) else "",
        )
    return "", detail.strip() if isinstance(detail, str) else ""


def _session_coordinates(*, cid: str, sid: str) -> tuple[str, str]:
    """校验 Queue Session 坐标。"""
    normalized_cid = str(cid or "").strip()
    normalized_sid = str(sid or "").strip()
    if not normalized_cid:
        raise DurableQueueRequestError("durable queue requires cid")
    if not normalized_sid:
        raise DurableQueueRequestError("durable queue requires sid")
    return normalized_cid, normalized_sid


def _turn_id(value: str) -> str:
    """校验 Queue item 预分配的 Turn identity。"""
    try:
        return normalize_turn_id(value)
    except ValueError as error:
        raise DurableQueueRequestError(str(error)) from error


def _submission_id(value: str) -> str:
    """校验 Queue submission identity。"""
    try:
        return normalize_submission_id(value)
    except ValueError as error:
        raise DurableQueueRequestError(str(error)) from error


def _request_id(value: str | None, *, prefix: str) -> str:
    """读取或生成 Queue 命令幂等 identity。"""
    try:
        return resolve_request_id(value, prefix=prefix)
    except ValueError as error:
        raise DurableQueueRequestError(str(error)) from error


def _client_message_id(value: str) -> str:
    """校验 Queue 输入 exactly-once identity。"""
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > 128:
        raise DurableQueueRequestError(
            "client_message_id must contain 1-128 characters"
        )
    return normalized


if __name__ == '__main__':
    pass
