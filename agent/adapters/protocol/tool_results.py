# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import copy
import typing
from dataclasses import dataclass

import httpx

from agent.ports import ProtocolCommandError
from protocol.client.tools import (
    ToolResultEnvelope,
    ToolResultRequestError,
    build_tool_result_payload,
)
from protocol.schema.identifiers import stable_request_id

_AsyncCall = typing.Callable[..., typing.Awaitable[typing.Any]]
_CommandError = ToolResultRequestError | ProtocolCommandError

_RETRYABLE_DELIVERY_CODES = frozenset({
    "tool_call_missing",
    "tool_call_not_ready",
    "tool_result_ack_invalid",
    "tool_result_ack_mismatch",
})

_QUERYABLE_CODES = frozenset({
    *_RETRYABLE_DELIVERY_CODES,
    "tool_result_reconciliation_required",
})

_RECONCILIATION_CONFLICT_CODES = frozenset({
    "request_id_conflict",
    "tool_call_already_completed",
    "tool_call_mismatch",
})


@dataclass(frozen=True, slots=True)
class _FrozenToolResult:
    """保存一次工具结果交付使用的不可替换请求快照。"""

    cid: str
    sid: str
    call_id: str
    tool_name: str
    ok: bool
    result: ToolResultEnvelope
    additional_context: tuple[str, ...]
    request_id: str
    payload: dict[str, typing.Any]

    @property
    def identity(self) -> tuple[str, str, str]:
        """返回单轮内工具结果的去重身份。"""
        return self.cid, self.sid, self.call_id

    @classmethod
    def create(
        cls,
        *,
        cid: str,
        sid: str,
        call_id: str,
        tool_name: str,
        ok: bool,
        result: typing.Mapping[str, typing.Any],
        additional_context: typing.Sequence[str],
        request_id: str | None,
    ) -> "_FrozenToolResult":
        """复制调用方数据并生成稳定服务端请求载荷。"""
        frozen_result = copy.deepcopy(dict(result))
        frozen_context = tuple(str(value) for value in additional_context)
        payload = build_tool_result_payload(
            cid=cid,
            sid=sid,
            call_id=call_id,
            name=tool_name,
            ok=ok,
            result=frozen_result,
            additional_context=frozen_context,
            request_id=request_id,
        )
        return cls(
            cid=str(cid),
            sid=str(sid),
            call_id=str(call_id),
            tool_name=str(tool_name),
            ok=bool(ok),
            result=payload["result"],
            additional_context=frozen_context,
            request_id=payload["request_id"],
            payload=payload,
        )


class ToolResultDelivery:
    """在单轮生命周期内冻结、去重、重试并对账客户端工具结果。"""

    def __init__(
        self,
        *,
        reconcile_known_effect: typing.Callable[
            [str],
            typing.Awaitable[bool],
        ],
        post_result: _AsyncCall,
        get_status: _AsyncCall,
        post_reconciliation: _AsyncCall,
        sleep: typing.Callable[[float], typing.Awaitable[typing.Any]] = (
            asyncio.sleep
        ),
    ) -> None:
        """绑定传输能力并初始化当前轮次的交付去重状态。"""
        self._reconcile_known_effect = reconcile_known_effect
        self._post_result = post_result
        self._get_status = get_status
        self._post_reconciliation = post_reconciliation
        self._sleep = sleep
        self._lock = asyncio.Lock()
        self._futures: dict[
            tuple[str, str, str],
            asyncio.Future[None],
        ] = {}
        self._payloads: dict[
            tuple[str, str, str],
            dict[str, typing.Any],
        ] = {}

    async def deliver(
        self,
        cid: str,
        sid: str,
        call_id: str,
        tool_name: str,
        ok: bool,
        tool_result: typing.Mapping[str, typing.Any],
        additional_context: typing.Sequence[str] = (),
        request_id: str | None = None,
    ) -> None:
        """冻结工具结果，并以唯一请求完成一次持久投递。"""
        frozen = _FrozenToolResult.create(
            cid=cid,
            sid=sid,
            call_id=call_id,
            tool_name=tool_name,
            ok=ok,
            result=tool_result,
            additional_context=additional_context,
            request_id=request_id,
        )
        loop = asyncio.get_running_loop()
        async with self._lock:
            previous_payload = self._payloads.get(frozen.identity)
            if previous_payload is not None and previous_payload != frozen.payload:
                raise ToolResultRequestError(
                    "tool_result_local_conflict",
                    "one call_id cannot receive different frozen results",
                    details={"call_id": call_id},
                )
            self._payloads[frozen.identity] = frozen.payload
            future = self._futures.get(frozen.identity)
            owns_delivery = future is None
            if owns_delivery:
                future = loop.create_future()
                self._futures[frozen.identity] = future

        if not owns_delivery:
            await future
            return

        try:
            await self._deliver_frozen(frozen)
        except asyncio.CancelledError:
            if not future.done():
                future.cancel()
            await self._release_failed_delivery(frozen.identity)
            raise
        except Exception as error:
            if not future.done():
                future.set_exception(error)
                future.exception()
            await self._release_failed_delivery(frozen.identity)
            raise
        else:
            if not future.done():
                future.set_result(None)

    async def _release_failed_delivery(
        self,
        identity: tuple[str, str, str],
    ) -> None:
        """释放失败交付的所有权，使相同冻结请求可以再次尝试。"""
        async with self._lock:
            self._futures.pop(identity, None)

    async def _deliver_frozen(self, frozen: _FrozenToolResult) -> None:
        """投递冻结结果并在暂态登记窗口内按原请求重试。"""
        first_error: _CommandError | None = None
        try:
            await self._post(frozen)
            return
        except (ToolResultRequestError, ProtocolCommandError) as delivery_error:
            first_error = delivery_error
            if (
                delivery_error.is_deterministic_terminal
                or delivery_error.code == "request_id_conflict"
            ):
                raise
            if not (
                delivery_error.retryable
                or delivery_error.code in _QUERYABLE_CODES
            ):
                raise

        if first_error is None:
            raise RuntimeError("tool result delivery did not produce an error")
        last_error = first_error
        for delay in (0.1, 0.25, 0.5):
            await self._sleep(delay)
            try:
                status = await self._get_status(
                    cid=frozen.cid,
                    sid=frozen.sid,
                    call_id=frozen.call_id,
                )
            except (ToolResultRequestError, ProtocolCommandError) as status_error:
                last_error = status_error
                if self._can_retry_delivery(first_error):
                    try:
                        await self._post(frozen)
                        return
                    except (ToolResultRequestError, ProtocolCommandError) as retry_error:
                        last_error = retry_error
                        if not (
                            retry_error.retryable
                            or retry_error.code in {
                                "tool_call_missing",
                                "tool_call_not_ready",
                            }
                        ):
                            raise
                continue

            if (
                status.get("reconciliation_required") is True
                and first_error.code not in _RECONCILIATION_CONFLICT_CODES
            ):
                last_error = await self._reconcile_status(
                    frozen,
                    first_error=first_error,
                    status=status,
                )
                if last_error is None:
                    return
                if not last_error.retryable:
                    raise last_error
                continue

            if status.get("result_received") is True:
                if status.get("request_id") == frozen.request_id:
                    return
                raise ToolResultRequestError(
                    "tool_result_request_conflict",
                    "authoritative result belongs to a different request",
                    details=status,
                )
            if status.get("tool_status") in {
                "execution_timed_out",
                "cancelled",
                "turn_closed",
            }:
                raise ToolResultRequestError(
                    f"tool_call_{status['tool_status']}",
                    "tool call is no longer waiting for a result",
                    details=status,
                )
            if not self._can_retry_delivery(first_error):
                raise first_error
            try:
                await self._post(frozen)
                return
            except (ToolResultRequestError, ProtocolCommandError) as retry_error:
                last_error = retry_error
                if not (
                    retry_error.retryable
                    or retry_error.code in _RETRYABLE_DELIVERY_CODES
                ):
                    raise
        raise last_error

    async def _post(self, frozen: _FrozenToolResult) -> None:
        """使用冻结字段提交一次工具结果请求。"""
        await self._post_result(
            frozen.cid,
            frozen.sid,
            frozen.call_id,
            frozen.tool_name,
            frozen.ok,
            frozen.result,
            additional_context=frozen.additional_context,
            request_id=frozen.request_id,
        )

    async def _reconcile_status(
        self,
        frozen: _FrozenToolResult,
        *,
        first_error: _CommandError,
        status: dict[str, typing.Any],
    ) -> _CommandError | None:
        """尝试核对权威状态并返回仍需继续处理的错误。"""
        effect_id = str(status.get("effect_id") or "").strip()
        if not effect_id:
            return ToolResultRequestError(
                "tool_result_reconciliation_required",
                "tool result requires effect reconciliation",
                retryable=False,
                details=status,
            )
        try:
            if await self._reconcile_effect(frozen, effect_id):
                return None
        except asyncio.CancelledError:
            raise
        except ProtocolCommandError as error:
            return error
        except (
                httpx.HTTPError,
                OSError,
                RuntimeError,
                TypeError,
                ValueError,
        ) as error:
            return ToolResultRequestError(
                "tool_result_reconciliation_failed",
                "tool result effect reconciliation failed",
                retryable=True,
                details={
                    **status,
                    "effect_id": effect_id,
                    "reconcile_error": f"{type(error).__name__}: {error}",
                },
            )
        return first_error

    async def _reconcile_effect(
        self,
        frozen: _FrozenToolResult,
        effect_id: str,
    ) -> bool:
        """用冻结结果核对服务端尚未确定的客户端效果。"""
        try:
            if await self._reconcile_known_effect(effect_id):
                return True
        except asyncio.CancelledError:
            raise
        except (OSError, RuntimeError, TypeError, ValueError):
            pass

        result_envelope = frozen.payload["result"]
        result_data = result_envelope.get("data")
        resolution: typing.Literal["failed", "committed"] = (
            "failed"
            if (
                result_envelope.get("ok") is False
                or (
                    isinstance(result_data, dict)
                    and result_data.get("executed") is False
                )
            )
            else "committed"
        )
        reconcile_error = ""
        if resolution == "failed":
            if isinstance(result_data, dict):
                reconcile_error = str(
                    result_data.get("error") or ""
                ).strip()
            if not reconcile_error:
                reconcile_error = str(
                    result_envelope.get("text") or ""
                ).strip()
            if not reconcile_error:
                reconcile_error = "client tool result reported failure"

        await self._post_reconciliation(
            effect_id=effect_id,
            request_id=stable_request_id(
                "effect_reconcile",
                effect_id,
                frozen.request_id,
            ),
            resolution=resolution,
            result_payload=copy.deepcopy(frozen.payload),
            error=reconcile_error,
            metadata={"source": "client_tool_result_delivery"},
        )
        return True

    @staticmethod
    def _can_retry_delivery(first_error: _CommandError) -> bool:
        """返回首次交付错误是否允许使用原请求再次提交。"""
        return (
            first_error.retryable
            or first_error.code in _RETRYABLE_DELIVERY_CODES
        )
