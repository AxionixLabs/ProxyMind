# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import httpx
import typing
import asyncio
from agent.ports import (
    ApprovalSnapshotCallback,
    ModelCapabilityError,
    ProtocolCommandError,
    ModelEventStream,
    ReconnectStatusCallback,
)
from agent.protocol import (
    CanonicalItem,
    ConversationForkReceipt,
    ForkPrompt,
    ModelEvent,
    ModelStreamEndReason,
    ModelStreamRequest,
    SteerTurnInput,
    TurnControlReceipt,
    TurnReconcileReceipt,
    TurnStatusSnapshot,
    validate_model_event,
)
from agent.protocol.json_value import ThawedJsonValue
from protocol.client.effects import post_effect_reconciliation as _post_effect_reconciliation
from protocol.client.tools import (
    ToolApprovalRequestError,
    ToolResultRequestError,
    get_tool_result_status as _get_tool_result_status,
    post_tool_approval as _post_tool_approval,
    post_tool_result as _post_tool_result,
    renew_tool_result as _renew_tool_result,
)
from protocol.client.turn_control import (
    TurnControlRequestError,
    TurnStatusRequestError,
    get_turn_status as _get_turn_status,
    interrupt_turn as _interrupt_turn,
    reconcile_turn_inputs as _reconcile_turn_inputs,
    steer_turn as _steer_turn,
)
from protocol.client.fork import (
    ConversationForkRequestError,
    ResubmittablePrompt as _WireResubmittablePrompt,
    request_conversation_fork as _request_conversation_fork,
)
from protocol.schema.turn_inputs import TurnInput as _WireTurnInput
from .items import CanonicalItemReducer
from protocol.client.chat import stream_chat

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
        try:
            response = await _interrupt_turn(
                cid=cid,
                sid=sid,
                turn_id=turn_id,
                request_id=request_id,
            )
        except TurnControlRequestError as error:
            raise ProtocolCommandError(
                "turn_control_request_failed",
                str(error) or "turn control request failed",
                retryable=True,
            ) from error
        except (TypeError, ValueError) as error:
            raise ProtocolCommandError(
                "protocol_command_validation_error",
                str(error) or "turn control request is invalid",
            ) from error
        return _control_receipt(response)

    async def steer_turn(
        self,
        *,
        cid: str,
        sid: str,
        turn_id: str,
        turn_input: SteerTurnInput,
        request_id: str | None = None,
    ) -> TurnControlReceipt:
        """提交一项引导输入并返回不透明的稳定控制回执。"""
        if not isinstance(turn_input, SteerTurnInput):
            raise ProtocolCommandError(
                "protocol_command_validation_error",
                "steer turn input is invalid",
            )
        try:
            values = turn_input.request_values()
            response = await _steer_turn(
                cid=cid,
                sid=sid,
                turn_id=turn_id,
                turn_input=_WireTurnInput(
                    client_message_id=values["client_message_id"],
                    text=values["text"],
                    attachments=tuple(values["attachments"]),
                    extras=values["extras"],
                ),
                request_id=request_id,
            )
        except TurnControlRequestError as error:
            raise ProtocolCommandError(
                "turn_control_request_failed",
                str(error) or "turn steer request failed",
                retryable=True,
            ) from error
        except (TypeError, ValueError) as error:
            raise ProtocolCommandError(
                "protocol_command_validation_error",
                str(error) or "turn steer request is invalid",
            ) from error
        return _control_receipt(response)

    async def reconcile_turn_inputs(
        self,
        *,
        cid: str,
        sid: str,
        turn_id: str,
        client_message_ids: typing.Sequence[str],
    ) -> TurnReconcileReceipt:
        """查询未确认引导输入的权威归属。"""
        try:
            response = await _reconcile_turn_inputs(
                cid=cid,
                sid=sid,
                turn_id=turn_id,
                client_message_ids=client_message_ids,
            )
        except TurnControlRequestError as error:
            raise ProtocolCommandError(
                "turn_reconciliation_failed",
                str(error) or "turn reconciliation request failed",
                retryable=True,
            ) from error
        except (TypeError, ValueError) as error:
            raise ProtocolCommandError(
                "protocol_command_validation_error",
                str(error) or "turn reconciliation request is invalid",
            ) from error
        return TurnReconcileReceipt(
            turn_id=response.turn_id,
            turn_status=response.turn_status,
            committed_ids=tuple(response.committed_ids),
            pending_ids=tuple(response.pending_ids),
            retry_ids=tuple(response.retry_ids),
            unknown_ids=tuple(response.unknown_ids),
        )

    async def get_turn_status(
        self,
        *,
        cid: str,
        sid: str,
        turn_id: str,
    ) -> TurnStatusSnapshot:
        """读取服务端持久化 Turn 的权威状态快照。"""
        try:
            response = await _get_turn_status(
                cid=cid,
                sid=sid,
                turn_id=turn_id,
            )
        except TurnStatusRequestError as error:
            details: dict[str, typing.Any] = {}
            if error.status_code is not None:
                details["status_code"] = error.status_code
            raise ProtocolCommandError(
                "turn_status_request_failed",
                str(error) or "turn status request failed",
                retryable=bool(
                    error.status_code is None
                    or error.status_code >= 500
                    or error.status_code in {408, 425, 429}
                ),
                details=details,
            ) from error
        except (TypeError, ValueError) as error:
            raise ProtocolCommandError(
                "protocol_command_validation_error",
                str(error) or "turn status request is invalid",
            ) from error
        return TurnStatusSnapshot(
            cid=response.cid,
            sid=response.sid,
            turn_id=response.turn_id,
            run_id=response.run_id,
            status=response.status,
            terminal=response.terminal,
            attempt=response.attempt,
            version=response.version,
            last_event_seq=response.last_event_seq,
            created_at=response.created_at,
            updated_at=response.updated_at,
            error=response.error,
        )

    async def fork_session(
        self,
        *,
        cid: str,
        sid: str,
        request_id: str,
        prompt_source: typing.Literal["none", "server", "client"],
        before_turn_id: str | None = None,
    ) -> ConversationForkReceipt:
        """原子复制会话上下文并返回已校验的分支回执。"""
        try:
            response = await _request_conversation_fork(
                cid=cid,
                sid=sid,
                request_id=request_id,
                prompt_source=prompt_source,
                before_turn_id=before_turn_id,
            )
        except ConversationForkRequestError as error:
            details: dict[str, typing.Any] = {}
            if error.status_code:
                details["status_code"] = error.status_code
            raise ProtocolCommandError(
                error.code or "conversation_fork_failed",
                str(error) or "conversation fork request failed",
                retryable=error.retryable,
                details=details,
            ) from error
        except (TypeError, ValueError) as error:
            raise ProtocolCommandError(
                "protocol_command_validation_error",
                str(error) or "conversation fork request is invalid",
            ) from error

        raw_prompt = response.get("prompt")
        prompt: ForkPrompt | None = None
        if raw_prompt is not None:
            if not isinstance(raw_prompt, _WireResubmittablePrompt):
                raise ProtocolCommandError(
                    "protocol_command_error",
                    "conversation fork returned an invalid prompt",
                )
            prompt = ForkPrompt(
                message=raw_prompt.message,
                attachments=tuple(raw_prompt.attachments),
                extras=raw_prompt.extras,
            )

        raw_prompt_source = str(response.get("prompt_source") or "").strip()
        if raw_prompt_source not in {"none", "server", "client"}:
            raise ProtocolCommandError(
                "protocol_command_error",
                "conversation fork returned an invalid prompt source",
            )
        try:
            return ConversationForkReceipt(
                request_id=str(response["request_id"]),
                source_cid=str(response["source_cid"]),
                source_sid=str(response["source_sid"]),
                prompt_source=raw_prompt_source,
                cid=str(response["cid"]),
                sid=str(response["sid"]),
                copied_items=int(response["copied_items"]),
                copied_turns=(
                    int(response["copied_turns"])
                    if response.get("copied_turns") is not None
                    else None
                ),
                before_turn_id=(
                    str(response["before_turn_id"])
                    if response.get("before_turn_id") is not None
                    else None
                ),
                prompt=prompt,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ProtocolCommandError(
                "protocol_command_error",
                "conversation fork returned an incomplete receipt",
            ) from error

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
        try:
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
        except ToolResultRequestError as error:
            details = dict(error.details)
            if error.status_code:
                details.setdefault("status_code", error.status_code)
            if error.trace_id:
                details.setdefault("trace_id", error.trace_id)
            raise ProtocolCommandError(
                error.code,
                str(error) or "tool result delivery failed",
                retryable=error.retryable,
                details=details,
            ) from error
        except (TypeError, ValueError) as error:
            raise ProtocolCommandError(
                "protocol_command_validation_error",
                str(error) or "tool result request is invalid",
            ) from error

    async def get_tool_result_status(
        self,
        *,
        cid: str,
        sid: str,
        call_id: str,
    ) -> dict[str, ThawedJsonValue]:
        """读取工具结果权威状态并返回已校验的数据快照。"""
        try:
            return await _get_tool_result_status(
                cid=cid,
                sid=sid,
                call_id=call_id,
            )
        except ToolResultRequestError as error:
            details = dict(error.details)
            if error.status_code:
                details.setdefault("status_code", error.status_code)
            if error.trace_id:
                details.setdefault("trace_id", error.trace_id)
            raise ProtocolCommandError(
                error.code,
                str(error) or "tool result status failed",
                retryable=error.retryable,
                details=details,
            ) from error
        except (TypeError, ValueError) as error:
            raise ProtocolCommandError(
                "protocol_command_validation_error",
                str(error) or "tool result status request is invalid",
            ) from error

    async def renew_tool_result(
        self,
        *,
        cid: str,
        sid: str,
        turn_id: str,
        call_id: str,
        name: str,
        extension_seconds: int = 60,
        request_id: str | None = None,
    ) -> dict[str, ThawedJsonValue]:
        """续期托管工具执行预算并归一化 wire 错误。"""
        try:
            response = await _renew_tool_result(
                cid=cid,
                sid=sid,
                turn_id=turn_id,
                call_id=call_id,
                name=name,
                extension_seconds=extension_seconds,
                request_id=request_id,
            )
        except ToolResultRequestError as error:
            details = dict(error.details)
            if error.status_code:
                details.setdefault("status_code", error.status_code)
            if error.trace_id:
                details.setdefault("trace_id", error.trace_id)
            raise ProtocolCommandError(
                error.code,
                str(error) or "tool result renewal failed",
                retryable=error.retryable,
                details=details,
            ) from error
        except (TypeError, ValueError) as error:
            raise ProtocolCommandError(
                "protocol_command_validation_error",
                str(error) or "tool result renewal request is invalid",
            ) from error
        if not isinstance(response, dict):
            raise ProtocolCommandError(
                "protocol_command_error",
                "tool result renewal returned an invalid response",
            )
        return dict(response)

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
        try:
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
        except ToolApprovalRequestError as error:
            details: dict[str, typing.Any] = {}
            if error.status_code:
                details["status_code"] = error.status_code
            raise ProtocolCommandError(
                error.code,
                str(error) or "tool approval request failed",
                details=details,
            ) from error
        except (TypeError, ValueError) as error:
            raise ProtocolCommandError(
                "protocol_command_validation_error",
                str(error) or "tool approval request is invalid",
            ) from error

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
        try:
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
        except (httpx.HTTPError, OSError) as error:
            raise ProtocolCommandError(
                "effect_reconciliation_transport_error",
                str(error) or "effect reconciliation transport failed",
                retryable=True,
            ) from error
        except (RuntimeError, TypeError, ValueError) as error:
            raise ProtocolCommandError(
                "effect_reconciliation_error",
                str(error) or "effect reconciliation failed",
            ) from error


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


def _control_receipt(response: typing.Any) -> TurnControlReceipt:
    """校验并转换轮次控制 wire 回执。"""
    status = str(getattr(response, "status", "") or "").strip()
    if status not in {
        "accepted",
        "turn_not_active",
        "turn_not_steerable",
        "turn_mismatch",
        "duplicate",
    }:
        raise ProtocolCommandError(
            "protocol_command_error",
            "turn control returned an invalid status",
        )
    request_id = str(getattr(response, "request_id", "") or "").strip()
    response_turn_id = str(getattr(response, "turn_id", "") or "").strip()
    raw_message_id = getattr(response, "client_message_id", None)
    message_id = (
        str(raw_message_id or "").strip()
        if raw_message_id is not None
        else None
    )
    if not request_id or not response_turn_id:
        raise ProtocolCommandError(
            "protocol_command_error",
            "turn control returned an incomplete receipt",
        )
    return TurnControlReceipt(
        status=status,
        request_id=request_id,
        turn_id=response_turn_id,
        client_message_id=message_id,
    )


if __name__ == '__main__':
    pass
