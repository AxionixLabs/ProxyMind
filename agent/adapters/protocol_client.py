# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import httpx
import typing
import asyncio
from agent.ports import (
    ApprovalSnapshotCallback,
    ModelCapabilityError,
    ModelEventStream,
    ReconnectStatusCallback,
)
from agent.protocol import (
    CanonicalItem,
    ModelEvent,
    ModelStreamEndReason,
    ModelStreamRequest,
    TurnControlReceipt,
    validate_model_event,
)
from agent.protocol.json_value import ThawedJsonValue
from mind_nova.requests.effects import (
    post_effect_reconciliation as _post_effect_reconciliation,
)
from mind_nova.requests.tools import (
    get_tool_result_status as _get_tool_result_status,
    post_tool_approval as _post_tool_approval,
    post_tool_result as _post_tool_result,
)
from mind_nova.requests.turn_control import (
    interrupt_turn as _interrupt_turn,
)
from .item_reducer import CanonicalItemReducer
from mind_nova.requests.chat import stream_chat

SessionIdentity: typing.TypeAlias = tuple[str, str]


class _WireEventStream(typing.Protocol):
    """描述既有 mind.chat 传输提供的最小流生命周期。"""

    end_reason: str | None
    last_event_seq: int

    def __aiter__(self) -> typing.AsyncIterator[ModelEvent]:
        """返回正式协议事件迭代器。"""
        ...

    async def aclose(self) -> None:
        """关闭底层传输与恢复任务。"""
        ...


class ProtocolEventCursorStore:
    """持有 Protocol Client 已完整确认的 Session 事件水位。"""

    def __init__(self) -> None:
        """初始化空的进程级 Session 水位集合。"""
        self._event_sequences: dict[SessionIdentity, int] = {}

    def current(self, *, cid: str, sid: str) -> int:
        """返回指定 Session 当前已确认的事件序号。"""
        identity = self._identity(cid=cid, sid=sid)
        return self._event_sequences.get(identity, 0)

    def advance(self, *, cid: str, sid: str, event_seq: int) -> int:
        """单调推进指定 Session 的确认水位并返回最终值。"""
        identity = self._identity(cid=cid, sid=sid)
        if (
            isinstance(event_seq, bool)
            or not isinstance(event_seq, int)
            or event_seq < 0
        ):
            raise ValueError("event_seq must be a non-negative integer")
        current = self._event_sequences.get(identity, 0)
        resolved = max(current, event_seq)
        self._event_sequences[identity] = resolved
        return resolved

    @staticmethod
    def _identity(*, cid: str, sid: str) -> SessionIdentity:
        """校验并返回稳定的远端 Session 身份。"""
        normalized_cid = str(cid or "").strip()
        normalized_sid = str(sid or "").strip()
        if not normalized_cid or not normalized_sid:
            raise ValueError("cid and sid are required")
        return normalized_cid, normalized_sid


class ProtocolModelEventStream:
    """校验 mind.chat 事件，并在完整结算后提交 Session 水位。"""

    def __init__(
        self,
        stream: _WireEventStream,
        *,
        cid: str,
        sid: str,
        turn_id: str,
        event_cursors: ProtocolEventCursorStore,
        item_reducer: CanonicalItemReducer | None = None,
    ) -> None:
        """绑定单 Turn 传输及其 Protocol Client 状态所有者。"""
        self._stream = stream
        self._cid = cid
        self._sid = sid
        self._turn_id = turn_id
        self._event_cursors = event_cursors
        self._item_reducer = (
            item_reducer
            if item_reducer is not None
            else CanonicalItemReducer(cid=cid, sid=sid, turn_id=turn_id)
        )
        self._current_item: CanonicalItem | None = None
        self._closed = False

    @property
    def end_reason(self) -> ModelStreamEndReason | None:
        """返回底层传输记录的稳定结束原因。"""
        value = getattr(self._stream, "end_reason", None)
        if value in {"settled", "fatal", "cancelled", "protocol_error"}:
            return value
        return None

    @property
    def last_event_seq(self) -> int:
        """返回底层传输已去重并确认的最新事件序号。"""
        value = getattr(self._stream, "last_event_seq", 0)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("protocol stream cursor must be non-negative")
        return value

    @property
    def canonical_items(self) -> tuple[CanonicalItem, ...]:
        """返回当前 Turn 未被替换的 Canonical Item 快照。"""
        return self._item_reducer.canonical_items

    @property
    def canonical_item_history(self) -> tuple[CanonicalItem, ...]:
        """返回包含旧展示 Attempt 的完整 Item 审计快照。"""
        return self._item_reducer.item_history

    @property
    def pending_approval_items(self) -> tuple[CanonicalItem, ...]:
        """返回快照优先级归约后仍等待决定的审批 Item。"""
        return self._item_reducer.pending_approval_items

    @property
    def assistant_text(self) -> str:
        """返回 reducer 归约后的当前 canonical 正文。"""
        return self._item_reducer.assistant_text

    @property
    def current_item(self) -> CanonicalItem | None:
        """返回最近交付事件对应的归约 Item；控制或忽略事件返回空。"""
        return self._current_item

    @property
    def sources(self) -> tuple[ThawedJsonValue, ...]:
        """返回当前 active Canonical Items 聚合后的来源。"""
        return self._item_reducer.sources

    def __aiter__(self) -> typing.AsyncIterator[ModelEvent]:
        """返回捕获并归一化协议失败的异步事件迭代器。"""
        return self._iterate()

    async def _iterate(self) -> typing.AsyncIterator[ModelEvent]:
        """校验事件坐标，并确保迭代结束时关闭远端资源。"""
        try:
            async for event in self._stream:
                validated = validate_model_event(event)
                self._validate_identity(validated)
                self._current_item = self._item_reducer.apply(validated)
                yield validated
        except asyncio.CancelledError:
            raise
        except ModelCapabilityError:
            raise
        except Exception as error:
            raise _classify_model_error(error) from error
        finally:
            await self.aclose()

    async def aclose(self) -> None:
        """幂等关闭传输，并只为已逻辑结算的流提交水位。"""
        if self._closed:
            return
        self._closed = True
        try:
            await self._stream.aclose()
            if self.end_reason == "settled":
                self._event_cursors.advance(
                    cid=self._cid,
                    sid=self._sid,
                    event_seq=self.last_event_seq,
                )
        except asyncio.CancelledError:
            raise
        except ModelCapabilityError:
            raise
        except Exception as error:
            raise _classify_model_error(error) from error

    def _validate_identity(self, event: ModelEvent) -> None:
        """拒绝跨 Session 或跨 Turn 的传输事件。"""
        if (
            event.cid != self._cid
            or event.sid != self._sid
            or event.turn_id != self._turn_id
        ):
            raise ValueError("model event does not match request coordinates")


class MindChatProtocolClient:
    """实现首个独立 mind.chat Protocol Client 生产切片。

    客户端负责冻结请求到 wire 参数的映射、Session 事件水位和模型流生命周期；
    后续 turn control、工具结果与审批命令应复用本边界，不回流到前端控制器。
    """

    def __init__(
        self,
        event_cursors: ProtocolEventCursorStore | None = None,
    ) -> None:
        """绑定进程级 Session 水位；未注入时创建独立所有者。"""
        self._event_cursors = (
            event_cursors
            if event_cursors is not None
            else ProtocolEventCursorStore()
        )

    def stream(
        self,
        request: ModelStreamRequest,
        *,
        on_reconnect_status: ReconnectStatusCallback | None = None,
        on_approval_snapshot: ApprovalSnapshotCallback | None = None,
    ) -> ModelEventStream:
        """按冻结请求创建具备恢复水位的正式协议事件流。"""
        try:
            item_reducer = CanonicalItemReducer(
                cid=request.cid,
                sid=request.sid,
                turn_id=request.turn_id,
            )

            async def restore_approval_snapshot(snapshot: object) -> None:
                """先提交快照权威状态，再通知调用方执行本地审批恢复。"""
                item_reducer.apply_approval_snapshot(snapshot)
                if on_approval_snapshot is None:
                    return
                callback_result = on_approval_snapshot(snapshot)
                if callback_result is not None:
                    await callback_result

            request_options = request.option_values()
            metadata = request.metadata_value()
            metadata.update({"cid": request.cid, "sid": request.sid})
            request_options["metadata"] = metadata
            request_options["turn_id"] = request.turn_id
            environment_snapshot = request.environment_snapshot_value()
            if environment_snapshot is not None:
                request_options["exec_env"] = environment_snapshot
            stream = stream_chat(
                request.pref_config_value(),
                request.message,
                request.tool_values(),
                attachments=request.attachment_values() or None,
                timeout=request.timeout,
                on_reconnect_status=on_reconnect_status,
                on_approval_snapshot=restore_approval_snapshot,
                initial_event_seq=self._event_cursors.current(
                    cid=request.cid,
                    sid=request.sid,
                ),
                **request_options,
            )
        except asyncio.CancelledError:
            raise
        except ModelCapabilityError:
            raise
        except Exception as error:
            raise _classify_model_error(error) from error
        return ProtocolModelEventStream(
            stream,
            cid=request.cid,
            sid=request.sid,
            turn_id=request.turn_id,
            event_cursors=self._event_cursors,
            item_reducer=item_reducer,
        )

    async def interrupt_turn(
        self,
        *,
        cid: str,
        sid: str,
        turn_id: str,
        request_id: str | None = None,
    ) -> TurnControlReceipt:
        """提交匹配活动轮次的中断命令并隐藏 wire 回执类型。"""
        response = await _interrupt_turn(
            cid=cid,
            sid=sid,
            turn_id=turn_id,
            request_id=request_id,
        )
        status = str(response.status or "").strip()
        if status == "accepted":
            normalized_status = "accepted"
        elif status == "turn_not_active":
            normalized_status = "turn_not_active"
        elif status == "turn_not_steerable":
            normalized_status = "turn_not_steerable"
        elif status == "turn_mismatch":
            normalized_status = "turn_mismatch"
        elif status == "duplicate":
            normalized_status = "duplicate"
        else:
            raise ModelCapabilityError(
                "protocol_command_error",
                "turn interrupt returned an invalid status",
            )
        return TurnControlReceipt(
            status=normalized_status,
            request_id=response.request_id,
            turn_id=response.turn_id,
            client_message_id=response.client_message_id,
        )

    async def post_tool_result(
        self,
        cid: str,
        sid: str,
        call_id: str,
        name: str,
        ok: bool,
        result: typing.Mapping[str, typing.Any],
        additional_context: typing.Sequence[str] = (),
        request_id: str | None = None,
    ) -> None:
        """提交客户端工具结果并隐藏 wire ACK 结构。"""
        await _post_tool_result(
            cid,
            sid,
            call_id,
            name,
            ok,
            result,
            additional_context=additional_context,
            request_id=request_id,
        )

    async def get_tool_result_status(
        self,
        *,
        cid: str,
        sid: str,
        call_id: str,
    ) -> dict[str, ThawedJsonValue]:
        """读取工具结果权威状态并返回已校验的数据快照。"""
        return await _get_tool_result_status(
            cid=cid,
            sid=sid,
            call_id=call_id,
        )

    async def post_tool_approval(
        self,
        cid: str,
        sid: str,
        call_id: str,
        approval_id: str,
        decision: str,
        *,
        turn_id: str,
        kind: str = "command",
        approval: typing.Mapping[str, typing.Any] | None = None,
        request_id: str | None = None,
        execpolicy_amendment_id: str | None = None,
        reason: str | None = None,
        additional_context: typing.Sequence[str] = (),
    ) -> None:
        """提交审批决定并隐藏具体 wire ACK 类型。"""
        await _post_tool_approval(
            cid,
            sid,
            call_id,
            approval_id,
            decision,
            turn_id=turn_id,
            kind=kind,
            approval=approval,
            request_id=request_id,
            execpolicy_amendment_id=execpolicy_amendment_id,
            reason=reason,
            additional_context=additional_context,
        )

    async def post_effect_reconciliation(
        self,
        *,
        effect_id: str,
        request_id: str,
        resolution: typing.Literal["committed", "failed", "retry"],
        result_payload: typing.Mapping[str, typing.Any] | None = None,
        error: str = "",
        metadata: typing.Mapping[str, typing.Any] | None = None,
    ) -> None:
        """提交外部效果核对结论并隐藏 wire 响应结构。"""
        await _post_effect_reconciliation(
            effect_id=effect_id,
            request_id=request_id,
            resolution=resolution,
            result_payload=(
                dict(result_payload) if result_payload is not None else None
            ),
            error=error,
            metadata=dict(metadata) if metadata is not None else None,
        )


def _classify_model_error(error: BaseException) -> ModelCapabilityError:
    """将传输异常映射为不依赖具体 HTTP 客户端的稳定能力错误。"""
    if isinstance(error, ModelCapabilityError):
        return error
    details: dict[str, typing.Any] = {
        "exception_type": type(error).__name__,
    }
    if isinstance(error, httpx.HTTPStatusError):
        status_code = error.response.status_code if error.response else None
        if status_code is not None:
            details["status_code"] = status_code
        return ModelCapabilityError(
            "model_transport_http_error",
            str(error).strip() or f"HTTP {status_code or 0}",
            retryable=bool(status_code is not None and (
                status_code >= 500 or status_code in {408, 425, 429}
            )),
            details=details,
        )
    if isinstance(error, (TimeoutError, httpx.TimeoutException)):
        return ModelCapabilityError(
            "model_transport_timeout",
            str(error).strip() or "model transport timed out",
            retryable=True,
            details=details,
        )
    if isinstance(error, (httpx.HTTPError, OSError)):
        return ModelCapabilityError(
            "model_transport_error",
            str(error).strip() or "model transport failed",
            retryable=True,
            details=details,
        )
    if isinstance(error, (TypeError, ValueError)):
        return ModelCapabilityError(
            "model_protocol_error",
            str(error).strip() or "model protocol is invalid",
            details=details,
        )
    return ModelCapabilityError(
        "model_capability_error",
        str(error).strip() or "model capability failed",
        details=details,
    )


if __name__ == '__main__':
    pass
