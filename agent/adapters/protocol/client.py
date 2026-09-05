# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import typing

import httpx

from agent.ports import (
    ApprovalSnapshotCallback,
    ModelCapabilityError,
    ProtocolCommandError,
    ModelEventStream,
    RecoveryStatusCallback,
)
from agent.protocol import (
    CanonicalItem,
    ConversationForkReceipt,
    DurableQueueInput,
    DurableQueueItem,
    DurableQueueMutationReceipt,
    DurableQueueReorderReceipt,
    DurableQueueSnapshot,
    DurableQueueStartReceipt,
    ForkPrompt,
    ModelEvent,
    ModelStreamEndReason,
    ModelStreamRequest,
    SteerTurnInput,
    TurnControlReceipt,
    TurnCompletedSnapshot,
    TurnReconcileReceipt,
    TurnStatusSnapshot,
    TurnObservationRequest,
    validate_model_event,
)
from agent.protocol.json_value import (
    JsonValue,
    ThawedJsonValue,
)
from protocol.client.chat import observe_turn as _observe_turn
from protocol.client.chat import stream_chat
from protocol.client.effects import post_effect_reconciliation as _post_effect_reconciliation
from protocol.client.fork import (
    ConversationForkRequestError,
    ResubmittablePrompt as _WireResubmittablePrompt,
    request_conversation_fork as _request_conversation_fork,
)
from protocol.client.durable_queue import (
    DurableQueueRequestError,
    add_queue_submission as _add_queue_submission,
    delete_queue_submission as _delete_queue_submission,
    list_queue_submissions as _list_queue_submissions,
    reorder_queue_submissions as _reorder_queue_submissions,
    start_queue_submission as _start_queue_submission,
    update_queue_submission as _update_queue_submission,
)
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
from protocol.schema.turn_inputs import TurnInput as _WireTurnInput
from protocol.schema.durable_queue import DurableQueueItem as _WireQueueItem
from protocol.schema.tool_approval import ToolApprovalSnapshot as _WireApprovalSnapshot
from .items import CanonicalItemReducer

SessionIdentity: typing.TypeAlias = tuple[str, str]
SessionTurnIdentity: typing.TypeAlias = tuple[str, str, str]


class _QueueRequestValues(typing.TypedDict):
    """描述 ModelStreamRequest 到 Queue wire builder 的参数。"""

    cid: str
    sid: str
    turn_id: str
    pref_config: dict[str, ThawedJsonValue]
    message: str
    tools: list[dict[str, ThawedJsonValue]]
    attachments: list[dict[str, ThawedJsonValue]]
    environment_snapshot: dict[str, ThawedJsonValue] | None
    metadata: dict[str, ThawedJsonValue]
    options: dict[str, ThawedJsonValue]


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

    def request_recovery_probe(self) -> None:
        """请求底层传输立即核对当前 Turn 的权威状态。"""
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
        on_close: typing.Callable[[], None] | None = None,
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
        self._on_close = on_close

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

    def request_recovery_probe(self) -> None:
        """请求活动 wire stream 立即进入状态核对或重连。"""
        self._stream.request_recovery_probe()

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
        finally:
            if self._on_close is not None:
                self._on_close()

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
        self._active_streams: dict[
            SessionTurnIdentity,
            ProtocolModelEventStream,
        ] = {}

    def stream(
        self,
        request: ModelStreamRequest,
        *,
        on_recovery_status: RecoveryStatusCallback | None = None,
        on_approval_snapshot: ApprovalSnapshotCallback | None = None,
    ) -> ModelEventStream:
        """按冻结请求创建具备恢复水位的正式协议事件流。"""
        try:
            item_reducer = CanonicalItemReducer(
                cid=request.cid,
                sid=request.sid,
                turn_id=request.turn_id,
            )

            async def restore_approval_snapshot(
                snapshot: _WireApprovalSnapshot,
            ) -> None:
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
                on_recovery_status=on_recovery_status,
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
        identity = (request.cid, request.sid, request.turn_id)
        model_stream = ProtocolModelEventStream(
            stream,
            cid=request.cid,
            sid=request.sid,
            turn_id=request.turn_id,
            event_cursors=self._event_cursors,
            item_reducer=item_reducer,
            on_close=lambda: self._active_streams.pop(identity, None),
        )
        self._active_streams[identity] = model_stream
        return model_stream

    def observe(
        self,
        request: TurnObservationRequest,
        *,
        on_recovery_status: RecoveryStatusCallback | None = None,
        on_approval_snapshot: ApprovalSnapshotCallback | None = None,
    ) -> ModelEventStream:
        """只通过 attach 观察已经由独立命令提交的 Turn。"""
        try:
            item_reducer = CanonicalItemReducer(
                cid=request.cid,
                sid=request.sid,
                turn_id=request.turn_id,
            )

            async def restore_approval_snapshot(
                snapshot: _WireApprovalSnapshot,
            ) -> None:
                """先提交快照权威状态，再通知调用方执行本地审批恢复。"""
                item_reducer.apply_approval_snapshot(snapshot)
                if on_approval_snapshot is None:
                    return
                callback_result = on_approval_snapshot(snapshot)
                if callback_result is not None:
                    await callback_result

            stream = _observe_turn(
                cid=request.cid,
                sid=request.sid,
                turn_id=request.turn_id,
                timeout=request.timeout,
                on_recovery_status=on_recovery_status,
                on_approval_snapshot=restore_approval_snapshot,
                initial_event_seq=self._event_cursors.current(
                    cid=request.cid,
                    sid=request.sid,
                ),
            )
        except asyncio.CancelledError:
            raise
        except ModelCapabilityError:
            raise
        except Exception as error:
            raise _classify_model_error(error) from error
        identity = (request.cid, request.sid, request.turn_id)
        model_stream = ProtocolModelEventStream(
            stream,
            cid=request.cid,
            sid=request.sid,
            turn_id=request.turn_id,
            event_cursors=self._event_cursors,
            item_reducer=item_reducer,
            on_close=lambda: self._active_streams.pop(identity, None),
        )
        self._active_streams[identity] = model_stream
        return model_stream

    async def interrupt_turn(
        self,
        *,
        cid: str,
        sid: str,
        turn_id: str,
        request_id: str | None = None,
    ) -> TurnControlReceipt:
        """提交匹配活动轮次的中断命令并隐藏 wire 回执类型。"""
        identity = (cid, sid, turn_id)
        active_stream = self._active_streams.get(identity)
        if active_stream is not None:
            active_stream.request_recovery_probe()
        try:
            response = await _interrupt_turn(
                cid=cid,
                sid=sid,
                turn_id=turn_id,
                request_id=request_id,
            )
        except TurnControlRequestError as error:
            raise _turn_control_command_error(
                error,
                fallback_message="turn control request failed",
            ) from error
        except (TypeError, ValueError) as error:
            raise ProtocolCommandError(
                "protocol_command_validation_error",
                str(error) or "turn control request is invalid",
            ) from error
        active_stream = self._active_streams.get(identity)
        if active_stream is not None:
            active_stream.request_recovery_probe()
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
            raise _turn_control_command_error(
                error,
                fallback_message="turn steer request failed",
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
            raise _turn_control_command_error(
                error,
                fallback_code="turn_reconciliation_failed",
                fallback_message="turn reconciliation request failed",
            ) from error
        except (TypeError, ValueError) as error:
            raise ProtocolCommandError(
                "protocol_command_validation_error",
                str(error) or "turn reconciliation request is invalid",
            ) from error
        receipt = TurnReconcileReceipt(
            turn_id=response.turn_id,
            turn_exists=response.turn_exists,
            terminal=(
                TurnCompletedSnapshot(
                    turn_id=response.terminal.turn_id,
                    status=response.terminal.status,
                    error=response.terminal.error,
                    last_event_seq=response.terminal.last_event_seq,
                    completed_at=response.terminal.completed_at,
                )
                if response.terminal is not None
                else None
            ),
            committed_ids=tuple(response.committed_ids),
            pending_ids=tuple(response.pending_ids),
            retry_ids=tuple(response.retry_ids),
            unknown_ids=tuple(response.unknown_ids),
        )
        if receipt.terminal is not None:
            self._event_cursors.advance(
                cid=cid,
                sid=sid,
                event_seq=receipt.terminal.last_event_seq,
            )
        return receipt

    async def get_turn_status(
        self,
        *,
        cid: str,
        sid: str,
        turn_id: str,
    ) -> TurnStatusSnapshot:
        """读取权威 Turn 状态，并用终态快照收敛 Session 事件水位。"""
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
        snapshot = TurnStatusSnapshot(
            cid=response.cid,
            sid=response.sid,
            turn_id=response.turn_id,
            run_id=response.run_id,
            status=response.status,
            terminal=(
                TurnCompletedSnapshot(
                    turn_id=response.terminal.turn_id,
                    status=response.terminal.status,
                    error=response.terminal.error,
                    last_event_seq=response.terminal.last_event_seq,
                    completed_at=response.terminal.completed_at,
                )
                if response.terminal is not None
                else None
            ),
            attempt=response.attempt,
            version=response.version,
            last_event_seq=response.last_event_seq,
            created_at=response.created_at,
            updated_at=response.updated_at,
        )
        if snapshot.terminal is not None:
            self._event_cursors.advance(
                cid=snapshot.cid,
                sid=snapshot.sid,
                event_seq=snapshot.terminal.last_event_seq,
            )
        return snapshot

    async def add_queue_submission(
        self,
        request: ModelStreamRequest,
        *,
        submission_id: str,
        client_message_id: str,
        request_id: str | None = None,
    ) -> DurableQueueMutationReceipt:
        """显式添加一个服务端持久队列提交。"""
        try:
            response = await _add_queue_submission(
                **_queue_request_values(request),
                client_message_id=client_message_id,
                submission_id=submission_id,
                request_id=request_id,
            )
        except DurableQueueRequestError as error:
            raise _queue_command_error(error) from error
        except (TypeError, ValueError) as error:
            raise ProtocolCommandError(
                "protocol_command_validation_error",
                str(error) or "durable queue add is invalid",
            ) from error
        return DurableQueueMutationReceipt(
            request_id=response.request_id,
            queue_version=response.queue_version,
            item=_queue_item(response.item),
        )

    async def list_queue_submissions(
        self,
        *,
        cid: str,
        sid: str,
    ) -> DurableQueueSnapshot:
        """读取服务端权威持久队列快照。"""
        try:
            response = await _list_queue_submissions(cid=cid, sid=sid)
        except DurableQueueRequestError as error:
            raise _queue_command_error(error) from error
        except (TypeError, ValueError) as error:
            raise ProtocolCommandError(
                "protocol_command_validation_error",
                str(error) or "durable queue list is invalid",
            ) from error
        return DurableQueueSnapshot(
            cid=response.cid,
            sid=response.sid,
            queue_version=response.queue_version,
            items=tuple(_queue_item(item) for item in response.items),
        )

    async def update_queue_submission(
        self,
        request: ModelStreamRequest,
        *,
        submission_id: str,
        request_id: str | None = None,
    ) -> DurableQueueMutationReceipt:
        """替换尚未启动提交的冻结执行请求。"""
        try:
            response = await _update_queue_submission(
                **_queue_request_values(request),
                submission_id=submission_id,
                request_id=request_id,
            )
        except DurableQueueRequestError as error:
            raise _queue_command_error(error) from error
        except (TypeError, ValueError) as error:
            raise ProtocolCommandError(
                "protocol_command_validation_error",
                str(error) or "durable queue update is invalid",
            ) from error
        return DurableQueueMutationReceipt(
            request_id=response.request_id,
            queue_version=response.queue_version,
            item=_queue_item(response.item),
        )

    async def delete_queue_submission(
        self,
        *,
        cid: str,
        sid: str,
        submission_id: str,
        request_id: str | None = None,
    ) -> DurableQueueMutationReceipt:
        """删除尚未启动的持久队列提交。"""
        try:
            response = await _delete_queue_submission(
                cid=cid,
                sid=sid,
                submission_id=submission_id,
                request_id=request_id,
            )
        except DurableQueueRequestError as error:
            raise _queue_command_error(error) from error
        except (TypeError, ValueError) as error:
            raise ProtocolCommandError(
                "protocol_command_validation_error",
                str(error) or "durable queue delete is invalid",
            ) from error
        return DurableQueueMutationReceipt(
            request_id=response.request_id,
            queue_version=response.queue_version,
            item=_queue_item(response.item),
        )

    async def reorder_queue_submissions(
        self,
        *,
        cid: str,
        sid: str,
        submission_ids: typing.Sequence[str],
        request_id: str | None = None,
    ) -> DurableQueueReorderReceipt:
        """原子替换服务端持久队列的完整 FIFO 顺序。"""
        try:
            response = await _reorder_queue_submissions(
                cid=cid,
                sid=sid,
                submission_ids=submission_ids,
                request_id=request_id,
            )
        except DurableQueueRequestError as error:
            raise _queue_command_error(error) from error
        except (TypeError, ValueError) as error:
            raise ProtocolCommandError(
                "protocol_command_validation_error",
                str(error) or "durable queue reorder is invalid",
            ) from error
        return DurableQueueReorderReceipt(
            request_id=response.request_id,
            queue_version=response.queue_version,
            submission_ids=response.submission_ids,
        )

    async def start_queue_submission(
        self,
        *,
        cid: str,
        sid: str,
        submission_id: str,
        request_id: str | None = None,
    ) -> DurableQueueStartReceipt:
        """在 Session idle 时原子启动 FIFO 队首。"""
        try:
            response = await _start_queue_submission(
                cid=cid,
                sid=sid,
                submission_id=submission_id,
                request_id=request_id,
            )
        except DurableQueueRequestError as error:
            raise _queue_command_error(error) from error
        except (TypeError, ValueError) as error:
            raise ProtocolCommandError(
                "protocol_command_validation_error",
                str(error) or "durable queue start is invalid",
            ) from error
        return DurableQueueStartReceipt(
            request_id=response.request_id,
            queue_version=response.queue_version,
            submission_id=response.submission_id,
            turn_id=response.turn_id,
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


def _queue_request_values(request: ModelStreamRequest) -> _QueueRequestValues:
    """把冻结模型请求复制为 Queue wire builder 参数。"""
    if not isinstance(request, ModelStreamRequest):
        raise TypeError("durable queue requires ModelStreamRequest")
    return {
        "cid": request.cid,
        "sid": request.sid,
        "turn_id": request.turn_id,
        "pref_config": request.pref_config_value(),
        "message": request.message,
        "tools": request.tool_values(),
        "attachments": request.attachment_values(),
        "environment_snapshot": request.environment_snapshot_value(),
        "metadata": request.metadata_value(),
        "options": request.option_values(),
    }


def _queue_item(item: _WireQueueItem) -> DurableQueueItem:
    """把已校验 wire Queue item 转换为 Agent 稳定投影。"""
    return DurableQueueItem(
        queue_seq=item.queue_seq,
        cid=item.cid,
        sid=item.sid,
        submission_id=item.submission_id,
        client_message_id=item.client_message_id,
        turn_id=item.turn_id,
        position=item.position,
        status=item.status,
        input=DurableQueueInput(
            text=item.input.text,
            attachments=item.input.attachments,
            extras=item.input.extras,
        ),
        created_at=item.created_at,
        updated_at=item.updated_at,
        started_at=item.started_at,
        deleted_at=item.deleted_at,
    )


def _queue_command_error(error: DurableQueueRequestError) -> ProtocolCommandError:
    """把 Queue wire 错误映射为稳定协议能力错误。"""
    details: dict[str, JsonValue] = {}
    if error.status_code is not None:
        details["status_code"] = error.status_code
    if error.code:
        details["server_code"] = error.code
    return ProtocolCommandError(
        error.code or "durable_queue_request_failed",
        str(error) or "durable queue request failed",
        retryable=error.retryable,
        details=details,
    )


def _turn_control_command_error(
    error: TurnControlRequestError,
    *,
    fallback_code: str = "turn_control_request_failed",
    fallback_message: str,
) -> ProtocolCommandError:
    """把 Turn 控制 wire 错误映射为稳定协议能力错误。"""
    details: dict[str, JsonValue] = {}
    if error.status_code is not None:
        details["status_code"] = error.status_code
    if error.code:
        details["server_code"] = error.code
    return ProtocolCommandError(
        error.code or fallback_code,
        str(error) or fallback_message,
        retryable=error.retryable,
        details=details,
    )


def _classify_model_error(error: BaseException) -> ModelCapabilityError:
    """将传输异常映射为不依赖具体 HTTP 客户端的稳定能力错误。"""
    if isinstance(error, ModelCapabilityError):
        return error
    details: dict[str, typing.Any] = {
        "exception_type": type(error).__name__,
    }
    if isinstance(error, httpx.HTTPStatusError):
        response = error.response
        status_code = response.status_code if response is not None else None
        if status_code is not None:
            details["status_code"] = status_code
        error_code = "model_transport_http_error"
        message = str(error).strip() or f"HTTP {status_code or 0}"
        if response is not None:
            try:
                body = response.json()
            except (TypeError, ValueError):
                body = None
            body_details = body.get("details") if isinstance(body, dict) else None
            if not isinstance(body_details, dict) and isinstance(body, dict):
                body_details = body.get("detail")
            if isinstance(body_details, dict):
                for field_name in (
                    "code",
                    "message",
                    "active_turn_id",
                    "active_status",
                ):
                    value = body_details.get(field_name)
                    if isinstance(value, str) and value.strip():
                        details[field_name] = value.strip()
                error_code = str(
                    details.get("code") or error_code
                )
                message = str(
                    details.get("message") or message
                )
            if isinstance(body, dict):
                trace_id = body.get("trace_id")
                if isinstance(trace_id, str) and trace_id.strip():
                    details["trace_id"] = trace_id.strip()
        return ModelCapabilityError(
            error_code,
            message,
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
