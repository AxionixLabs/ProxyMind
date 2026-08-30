# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import typing
import asyncio
from collections.abc import Mapping
from agent.application import (
    LocalEffectReconciliationRequired,
    ModelCapability,
    ModelCapabilityError,
    ModelEventStream,
    ModelStreamRequest,
    ProtocolCommandClient,
    ProtocolCommandError,
    TurnControlReceipt,
)
from mind_app.approval.ledger import ApprovalCallLedger
from mind_app.mcp.contracts import McpSessionLike
from mind_app.paths import effect_journal_db_path
from mind_nova.identifiers import stable_request_id
from mind_nova.requests.turn_control import (
    TurnControlRequestError,
    interrupt_turn
)
from mind_nova.turn_inputs import TurnInput
from mind_app.frontend.contracts import WaitRetryState
from mind_nova.stream_events import (
    ToolApprovalRequiredEvent,
    ToolBuiltinDoneEvent,
    ToolCallEvent,
    ToolCallsDoneEvent,
    ToolCallsStartEvent,
    ToolOutputEvent,
    TurnDoneEvent,
    TurnFailedEvent,
    TurnInputAcceptedEvent,
    TurnLogicalSettledEvent,
    TurnReconciliationRequiredEvent
)
from mind_nova.requests.tools import (
    ToolResultRequestError,
    get_tool_result_status,
    post_tool_approval,
    post_tool_result
)
from mind_nova.requests.effects import post_effect_reconciliation
from ...output import OutputControlPort
from .result import RunResult
from ..hooks.tool import ToolCallCoordinator
from ..hooks.models import StopHookDecision
from ..hooks.turn import (
    PromptHookBlockedError,
    TurnHookEvents
)
from ..support.session_policy import friendly_exception_text
from ..tools.client_call import ClientToolCallRunner
from ..tools.plan_call import PlanToolCallRunner
from .executor import (
    TurnExecution,
    build_turn_input_payload,
    create_continuation_execution,
    record_turn_started,
    turn_continuation_count
)
from ..support.idle_status import IdleStatusTimer
from .stream_approval import ApprovalEventHandler
from .stream_tools import (
    ToolCallBatchBuffer,
    ToolEventHandler,
)
from .stream_setup import prepare_stream_turn
from .stream_outcome import StreamTurnOutcome
from .stream_model import ModelStreamEventHandler
from .stream_effects import ToolResultDelivery
from .stream_finalize import StreamTurnFinalizer
from .stream_presentation import (
    FailureProjectionMode,
    StreamTurnPresentation,
)
from ...stream_events.lifecycle import handle_lifecycle_event
from engine.observability import (
    observe,
    observe_exception
)

if typing.TYPE_CHECKING:
    from ...controller import Mind

MAX_STOP_CONTINUATIONS = 3


class _RetryingStatus(object):
    """合并传输重连与 provider 重试状态后通知展示层。"""

    def __init__(
        self,
        sink: typing.Callable[[WaitRetryState], None] | None,
    ) -> None:
        """绑定状态回调并初始化两个独立重试原因。"""
        self.sink      = sink
        self.transport = False
        self.provider  = False

        self.state: WaitRetryState = "idle"

    def set_transport(self, retrying: bool) -> None:
        """更新事件传输重连状态。"""
        self.transport = bool(retrying)
        self._refresh()

    def set_provider(self, retrying: bool) -> None:
        """更新供应商流重试状态。"""
        self.provider = bool(retrying)
        self._refresh()

    def close(self) -> None:
        """清除所有重试原因并结束可见状态。"""
        self.transport = False
        self.provider = False
        self._refresh()

    def _refresh(self) -> None:
        """按传输优先级合并重试来源并通知展示层。"""
        state: WaitRetryState = (
            "transport"
            if self.transport
            else "provider"
            if self.provider
            else "idle"
        )
        if state == self.state:
            return
        self.state = state
        if self.sink is not None:
            self.sink(state)


def _extend_request_context(
    kwargs: dict[str, typing.Any],
    *,
    additional_context: typing.Iterable[str] = (),
    system_message: str = ""
) -> None:
    """把 Hook 注入文本合并到请求参数。"""
    contexts = [
        text
        for value in additional_context
        for text in [str(value or "").strip()]
        if text
    ]
    if contexts:
        existing = kwargs.get("additional_context")
        merged = list(existing) if isinstance(existing, list) else []
        merged.extend(contexts)
        kwargs["additional_context"] = merged

    system_text = str(system_message or "").strip()
    if system_text:
        kwargs["system_message"] = _join_text(
            str(kwargs.get("system_message") or ""),
            system_text,
        )


def _join_text(*values: str) -> str:
    """合并非空文本段。"""
    return "\n\n".join(
        text
        for value in values
        for text in [str(value or "").strip()]
        if text
    )


async def _cancel_reconciliation_turn(
    *,
    cid: str,
    sid: str,
    turn_id: str,
    effect_id: str,
    interrupt_command: typing.Callable[..., typing.Awaitable[TurnControlReceipt]],
) -> bool:
    """使用稳定中断命令释放无法自动核对的持久轮次。"""
    request_id = stable_request_id(
        "reconciliation_cancel",
        cid,
        sid,
        turn_id,
        effect_id,
    )
    for attempt in range(2):
        try:
            response = await interrupt_command(
                cid=cid,
                sid=sid,
                turn_id=turn_id,
                request_id=request_id,
            )
            return response.status in {"accepted", "turn_not_active"}
        except (TurnControlRequestError, ProtocolCommandError):
            if attempt == 0:
                continue
            return False
    return False


async def _interrupt_approval_cancelled_turn(
    *,
    cid: str,
    sid: str,
    turn_id: str,
    call_id: str,
    interrupt_command: typing.Callable[..., typing.Awaitable[TurnControlReceipt]],
) -> bool:
    """中断本地审批取消对应的逻辑轮次。"""
    request_id = stable_request_id(
        "approval_cancel",
        cid,
        sid,
        turn_id,
        call_id,
    )
    for attempt in range(2):
        try:
            response = await interrupt_command(
                cid=cid,
                sid=sid,
                turn_id=turn_id,
                request_id=request_id,
            )
            return response.status in {"accepted", "turn_not_active"}
        except (TurnControlRequestError, ProtocolCommandError):
            if attempt == 0:
                continue
            return False
    return False


async def stream_turn(
    mind: "Mind",
    session: McpSessionLike,
    pref_config: dict[str, typing.Any],
    tools: list[dict[str, typing.Any]],
    *_,
    turn_execution: TurnExecution,
    **kwargs
) -> RunResult:
    """处理流式事件、工具调用和输出上报。"""
    prepared = prepare_stream_turn(mind, turn_execution, kwargs)
    runtime_services = getattr(mind, "runtime_services", None)
    model_capability = getattr(runtime_services, "model_capability", None)
    if not isinstance(model_capability, ModelCapability):
        raise RuntimeError("model capability is required")
    if not isinstance(model_capability, ProtocolCommandClient):
        raise RuntimeError("protocol command client is required")
    protocol_client = model_capability
    callbacks = prepared.callbacks
    reentry_kwargs = prepared.continuation_kwargs
    ev_report = prepared.event_report
    turn_context = prepared.context
    hook_scope = prepared.hook_scope
    message = prepared.message
    kwargs = prepared.request_kwargs
    output_session = prepared.output_session
    metadata = kwargs["metadata"]

    started_at = prepared.started_at

    event_count: int = 0

    output_control: OutputControlPort = output_session.control

    status_control = output_session.status
    presentation   = output_session.presentation
    content        = output_session.content

    first_frame: bool = True
    outcome = StreamTurnOutcome()
    run_presentation = StreamTurnPresentation(
        outcome=outcome,
        status_control=status_control,
        content=content,
        presentation=presentation,
        event_report=ev_report,
    )

    failed_tool_context: list[str] = []

    tool_batch_buffer = ToolCallBatchBuffer()

    configured_approval_ledger = getattr(mind, "approval_call_ledger", None)
    if isinstance(configured_approval_ledger, ApprovalCallLedger):
        approval_ledger = configured_approval_ledger
    else:
        approval_ledger = ApprovalCallLedger()
        setattr(mind, "approval_call_ledger", approval_ledger)

    prompt_blocked: bool = False

    turn_hook_events: TurnHookEvents | None = None

    stop_decision = StopHookDecision.stop()
    event_stream  = None
    assistant_text = ""

    transcript = mind.transcripts.writer(
        turn_context.transcript_path,
        session_id=turn_context.sid,
        turn_id=turn_context.turn_id,
    )

    idle_wait = IdleStatusTimer(
        lambda: status_control.begin_reply_wait_status(delay_sec=0.0), delay_sec=0.9
    )

    retrying_status = _RetryingStatus(callbacks.retry_state)
    model_events = ModelStreamEventHandler(
        transcript=transcript,
        content=content,
        status_control=status_control,
        provider_retry_sink=retrying_status.set_provider,
        idle_reschedule=idle_wait.reschedule,
    )
    turn_state_stores = [approval_ledger]
    permission_grants = getattr(mind, "permission_grants", None)
    if permission_grants is not None:
        turn_state_stores.insert(0, permission_grants)
    turn_finalizer = StreamTurnFinalizer(
        cid=turn_context.cid,
        sid=turn_context.sid,
        turn_id=turn_context.turn_id,
        outcome=outcome,
        turn_state_stores=turn_state_stores,
        transcript=transcript,
        model_output=model_events,
        retry_state_close=retrying_status.close,
        stream_end=callbacks.stream_end,
        idle_wait=idle_wait,
        output_control=output_control,
        await_cleanup=mind.await_cleanup,
        continuation_count=turn_continuation_count(turn_execution),
    )

    try:
        transcript.open()
        record_turn_started(transcript, turn_execution)

        await output_control.open()

        observe(
            "stream.start",
            cid=metadata.get("cid"),
            sid=metadata.get("sid"),
            turn_id=turn_context.turn_id,
            agent_id=turn_context.agent.agent_id,
            tools=len(tools),
            skills=len(kwargs.get("skills") or []),
        )

        await run_presentation.emit_started(
            metadata=metadata,
            message=message,
            pref_config=pref_config,
            workdir=str(getattr(mind, "history_workspace", "") or ""),
            permissions=turn_context.permissions,
            turn_id=str(kwargs.get("turn_id") or ""),
            hook_warnings=(
                getattr(mind, "hook_startup_warnings", ())
                if turn_context.agent.depth == 0
                and turn_context.session_started
                else ()
            ),
        )

        tool_call_coordinator = ToolCallCoordinator(
            hook_scope,
            transcript=transcript,
            command_sessions=getattr(mind, "command_hook_sessions", None),
            failure_context_sink=failed_tool_context.extend,
        )
        approval_handler = ApprovalEventHandler(
            controller=mind,
            turn_context=turn_context,
            tools=tools,
            ledger=approval_ledger,
            coordinator=tool_call_coordinator,
            status_control=status_control,
            presentation=presentation,
            post_approval=protocol_client.post_tool_approval,
        )

        active_turn_hook_events = TurnHookEvents(hook_scope)
        turn_hook_events = active_turn_hook_events

        begin_result = await active_turn_hook_events.begin(message)
        if begin_result.message != message:
            transcript.append(
                "message.updated",
                actor="user",
                payload={"content": begin_result.message, "source": "hook"},
            )

        message = begin_result.message

        _extend_request_context(
            kwargs,
            additional_context=begin_result.additional_context,
        )

        async def interrupt_nested_turn(call_id: str) -> bool:
            """中断由嵌套本地工具审批取消的当前轮次。"""
            return await _interrupt_approval_cancelled_turn(
                cid=turn_context.cid,
                sid=turn_context.sid,
                turn_id=turn_context.turn_id,
                call_id=call_id,
                interrupt_command=protocol_client.interrupt_turn,
            )

        client_tool_runner = ClientToolCallRunner(
            session=session,
            output_control=output_control,
            status_control=status_control,
            presentation=presentation,
            tools=tools,
            pref_config=pref_config,
            tool_call_coordinator=tool_call_coordinator,
            effect_journal=runtime_services.create_effect_journal(
                effect_journal_db_path()
            ),
            effect_reconciler=protocol_client.post_effect_reconciliation,
            patch_preview=getattr(
                mind.workspace_runtime.coding,
                "preview_patch",
                None,
            ),
            interrupt_turn=interrupt_nested_turn,
        )
        tool_result_delivery = ToolResultDelivery(
            reconcile_known_effect=client_tool_runner.reconcile_known_effect,
            post_result=protocol_client.post_tool_result,
            get_status=protocol_client.get_tool_result_status,
            post_reconciliation=protocol_client.post_effect_reconciliation,
        )
        plan_tool_runner = PlanToolCallRunner(
            session=session,
            output_control=output_control,
            status_control=status_control,
            presentation=presentation,
            tools=tools,
            turn_context=turn_context,
            pref_config=pref_config,
            tool_call_coordinator=tool_call_coordinator,
        )
        tool_event_handler = ToolEventHandler(
            controller=mind,
            turn_context=turn_context,
            tools=tools,
            ledger=approval_ledger,
            coordinator=tool_call_coordinator,
            client_runner=client_tool_runner,
            plan_runner=plan_tool_runner,
            status_control=status_control,
            presentation=presentation,
            transcript=transcript,
            post_result=tool_result_delivery.deliver,
            interrupt_turn=interrupt_nested_turn,
        )

        request_options = dict(kwargs)
        raw_attachments = request_options.pop("attachments", ())
        attachments = (
            tuple(raw_attachments)
            if isinstance(raw_attachments, (tuple, list))
            else ()
        )
        timeout = request_options.pop("timeout", 60.0)
        environment_snapshot = request_options.pop("exec_env", None)
        if (
            environment_snapshot is not None
            and not isinstance(environment_snapshot, Mapping)
        ):
            raise TypeError("exec_env must be an object")
        request_options["permissions"] = {
            "sandbox_mode": turn_context.permissions.sandbox_mode,
            "approval_policy": turn_context.permissions.approval_policy,
            "approvals_reviewer": turn_context.permissions.approvals_reviewer,
            "network_access": turn_context.permissions.network_access,
        }
        raw_turn_id = request_options.pop("turn_id", turn_context.turn_id)
        if raw_turn_id != turn_context.turn_id:
            raise ValueError("model request turn_id does not match Turn context")
        raw_metadata = request_options.pop("metadata", {})
        if not isinstance(raw_metadata, Mapping):
            raise TypeError("model request metadata must be an object")
        request_metadata = dict(raw_metadata)
        for field_name, expected in (
            ("cid", turn_context.cid),
            ("sid", turn_context.sid),
        ):
            existing = request_metadata.pop(field_name, expected)
            if existing != expected:
                raise ValueError(
                    f"model request {field_name} does not match Turn context"
                )
        model_request = ModelStreamRequest(
            cid=turn_context.cid,
            sid=turn_context.sid,
            turn_id=turn_context.turn_id,
            pref_config=pref_config,
            message=message,
            tools=tuple(tools),
            attachments=attachments,
            environment_snapshot=environment_snapshot,
            metadata=request_metadata,
            options=request_options,
            timeout=timeout,
        )
        event_stream = model_capability.stream(
            model_request,
            on_reconnect_status=retrying_status.set_transport,
            on_approval_snapshot=approval_handler.restore_snapshot,
        )
        if not isinstance(event_stream, ModelEventStream):
            raise TypeError("model capability returned an invalid event stream")

        async for event in event_stream:
            event_count += 1
            await idle_wait.cancel()

            if ev_report:
                ev_report.bind_event(event)

            if first_frame:
                observe(
                    "stream.first_event",
                    event_type=event.type,
                    latency_ms=int((time.perf_counter() - started_at) * 1000),
                )
                if (
                    turn_context.agent.depth == 0
                    and not mind.frontend.runtime.active
                ):
                    await mind.stop_anim("wait")
                first_frame = False

            event_type = event.type

            if await model_events.handle(event, projection=event_stream):
                continue

            if event_type == "turn.start":
                if callbacks.input_event is not None:
                    callbacks.input_event(event)
                continue

            if event_type == "turn.thinking":
                await status_control.begin_reply_wait_status()
                continue

            if isinstance(event, TurnFailedEvent):

                if tool_batch_buffer.active:
                    raise ValueError("turn.failed arrived before tool.calls.done")

                outcome.record_failed_event(event)

                observe(
                    "stream.turn_failed",
                    level="ERROR",
                    error=outcome.error,
                    error_type=event.error_type or None,
                    error_source=event.error_source or None,
                    retryable=event.retryable,
                    stop_reason=event.stop_reason,
                )
                if turn_context.agent.depth == 0:
                    await mind.await_cleanup(
                        mind.stop_anim("wait", settle=False)
                    )
                await run_presentation.emit_failure(
                    "turn.failed",
                    mode=FailureProjectionMode.TERMINAL,
                )
                continue

            if isinstance(event, TurnReconciliationRequiredEvent):
                try:
                    recovered = await client_tool_runner.reconcile_known_effect(
                        event.effect_id
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as recovery_error:
                    recovered = False
                    observe(
                        "stream.reconciliation_auto_recovery_failed",
                        level="WARNING",
                        turn_id=turn_context.turn_id,
                        effect_id=event.effect_id,
                        error=f"{type(recovery_error).__name__}: {recovery_error}",
                    )
                if recovered:
                    observe(
                        "stream.reconciliation_auto_recovered",
                        turn_id=turn_context.turn_id,
                        effect_id=event.effect_id,
                    )
                    await status_control.begin_reply_wait_status()
                    continue

                cancelled = await _cancel_reconciliation_turn(
                    cid=str(metadata.get("cid") or ""),
                    sid=str(metadata.get("sid") or ""),
                    turn_id=turn_context.turn_id,
                    effect_id=event.effect_id,
                    interrupt_command=protocol_client.interrupt_turn,
                )

                reconciliation_error = (
                    event.error or "effect outcome requires reconciliation"
                )

                if not cancelled:
                    reconciliation_error = (
                        f"{reconciliation_error}; "
                        "failed to release the suspended turn"
                    )
                outcome.require_reconciliation(reconciliation_error)

                observe(
                    "stream.reconciliation_required",
                    level="ERROR",
                    turn_id=turn_context.turn_id,
                    effect_id=event.effect_id,
                    error=outcome.error,
                    turn_released=cancelled,
                )
                if turn_context.agent.depth == 0:
                    await mind.await_cleanup(
                        mind.stop_anim("wait", settle=False)
                    )
                await run_presentation.emit_failure(
                    "turn.reconciliation_required",
                    mode=FailureProjectionMode.PROJECTION_ONLY,
                )
                break

            if isinstance(event, TurnDoneEvent):

                if tool_batch_buffer.active:
                    raise ValueError("turn.done arrived before tool.calls.done")

                outcome.record_done_event(event)

                if event.status == "interrupted":
                    if callbacks.interrupted is not None:
                        callbacks.interrupted()

                if turn_context.agent.depth == 0:
                    await mind.await_cleanup(
                        mind.stop_anim("wait", settle=False)
                    )
                await status_control.end_status(immediate=True)

                continue

            if isinstance(event, (TurnInputAcceptedEvent, TurnLogicalSettledEvent)):
                if callbacks.input_event is not None:
                    accepted_input = callbacks.input_event(event)
                    if (
                        isinstance(event, TurnInputAcceptedEvent)
                        and isinstance(accepted_input, TurnInput)
                    ):
                        transcript.append(
                            "message.created",
                            actor="user",
                            payload=build_turn_input_payload(
                                accepted_input.text,
                                attachments=accepted_input.attachments,
                                extras=accepted_input.extras,
                            ),
                        )
                continue

            if event_type == "tool.builtin.call":
                await status_control.begin_tool_status()
                continue

            if isinstance(event, ToolBuiltinDoneEvent):
                await status_control.end_status()
                continue

            if isinstance(event, ToolCallsStartEvent):
                tool_batch_buffer.begin(event)
                await status_control.begin_reply_wait_status(delay_sec=0.15, animate_after_sec=0.85)
                continue

            if isinstance(event, ToolCallsDoneEvent):
                ready_calls = tool_batch_buffer.complete(event)
                await status_control.begin_reply_wait_status(delay_sec=0.75)
                for ready_call in ready_calls:
                    tool_handling = await tool_event_handler.handle_call(ready_call)
                    if tool_handling.status == "interrupted":
                        outcome.interrupt(tool_handling.error)
                        break
                if outcome.is_interrupted:
                    break
                continue

            if isinstance(event, ToolApprovalRequiredEvent):
                await approval_handler.handle(event)
                continue

            if isinstance(event, ToolCallEvent):
                for ready_call in tool_batch_buffer.accept(event):
                    tool_handling = await tool_event_handler.handle_call(ready_call)
                    if tool_handling.status == "interrupted":
                        outcome.interrupt(tool_handling.error)
                        break
                if outcome.is_interrupted:
                    break
                continue

            if isinstance(event, ToolOutputEvent):
                await tool_event_handler.handle_output(event)
                continue

            if await handle_lifecycle_event(
                event,
                presentation=presentation,
                status_control=status_control,
            ):
                continue

            continue

    except (ToolResultRequestError, ProtocolCommandError) as error:
        if error.is_deterministic_terminal:
            outcome.interrupt(f"{error.code}: {error}")
            failure_phase = "turn.tool_result_delivery_stopped"
        else:
            outcome.require_reconciliation(f"{error.code}: {error}")
            failure_phase = "turn.tool_result_delivery_failed"
        observe(
            "stream.tool_result_delivery_failed",
            level="ERROR",
            turn_id=turn_context.turn_id,
            call_id=error.details.get("call_id"),
            code=error.code,
            status_code=error.status_code,
            trace_id=error.trace_id,
        )
        if turn_context.agent.depth == 0:
            await mind.await_cleanup(mind.stop_anim("wait"))
        await run_presentation.emit_failure(
            failure_phase,
        )

    except LocalEffectReconciliationRequired as error:
        outcome.require_reconciliation(str(error))
        observe(
            "stream.local_effect_reconciliation_required",
            level="ERROR",
            turn_id=turn_context.turn_id,
            effect_id=error.effect_id,
        )
        if turn_context.agent.depth == 0:
            await mind.await_cleanup(mind.stop_anim("wait"))
        await run_presentation.emit_failure("turn.reconciliation_required")

    except PromptHookBlockedError as error:
        outcome.fail(
            str(error),
            additional_context=error.additional_context,
        )
        prompt_blocked = True

        if outcome.additional_context and turn_context.agent.depth == 0:
            mind.conversation.queue_turn_context(outcome.additional_context)

        observe(
            "stream.prompt_blocked",
            level="WARNING",
            turn_id=turn_context.turn_id,
        )

        await run_presentation.emit_failure("turn.prompt_blocked")

    except ModelCapabilityError as error:
        outcome.fail(
            error.message,
            error_code=error.code,
            error_details=error.details,
            additional_context=failed_tool_context,
        )
        if outcome.additional_context and turn_context.agent.depth == 0:
            mind.conversation.queue_turn_context(outcome.additional_context)
        observe(
            "stream.model_capability_failed",
            level="ERROR",
            turn_id=turn_context.turn_id,
            code=error.code,
            retryable=error.retryable,
            details=error.details,
        )

        if turn_context.agent.depth == 0:
            await mind.await_cleanup(mind.stop_anim("wait"))

        await run_presentation.emit_failure("turn.failed")

    except asyncio.CancelledError:
        outcome.interrupt()
        if failed_tool_context and turn_context.agent.depth == 0:
            mind.conversation.queue_turn_context(failed_tool_context)
        observe(
            "stream.interrupted",
            level="WARNING",
            events=event_count,
            elapsed_ms=int((time.perf_counter() - started_at) * 1000),
        )
        raise

    except Exception as e:
        outcome.fail(
            friendly_exception_text(e),
            additional_context=failed_tool_context,
        )
        if outcome.additional_context and turn_context.agent.depth == 0:
            mind.conversation.queue_turn_context(outcome.additional_context)
        observe_exception(
            "stream.failed",
            e,
            events=event_count,
            elapsed_ms=int((time.perf_counter() - started_at) * 1000),
        )

        if turn_context.agent.depth == 0:
            await mind.await_cleanup(mind.stop_anim("wait"))

        await run_presentation.emit_failure("turn.failed")

    else:
        outcome.settle_stream()
        if not outcome.has_terminal_status:
            await run_presentation.emit_failure("turn.incomplete")

        if outcome.is_completed and turn_context.agent.depth == 0:
            model_events.flush_pending()
            assistant_text = event_stream.assistant_text
            mind.remember_last_assistant_reply(assistant_text)

        await run_presentation.emit_result(event_stream.sources)

        observe(
            "stream.complete",
            outcome=outcome.observation_outcome,
            events=event_count,
            elapsed_ms=int((time.perf_counter() - started_at) * 1000),
            usage=outcome.usage or None,
        )

    finally:
        if event_stream is not None:
            try:
                await mind.await_cleanup(event_stream.aclose())
            except asyncio.CancelledError:
                raise
            except Exception as error:
                observe_exception("stream.model_close_failed", error)
            assistant_text = event_stream.assistant_text

        stream_end_reason = (
            getattr(event_stream, "end_reason", None)
            if event_stream is not None
            else None
        )
        stop_decision = await turn_finalizer.finalize(
            stream_end_reason=stream_end_reason or (
                "cancelled" if event_stream is not None else None
            ),
            hook_events=turn_hook_events,
            prompt_blocked=prompt_blocked,
            assistant_text=assistant_text,
        )

    result = outcome.build_result(assistant_text)

    if stop_decision.should_continue and outcome.continuation_allowed:
        continuation_count = turn_continuation_count(turn_execution)
        if continuation_count >= MAX_STOP_CONTINUATIONS:
            observe(
                "hooks.stop.limit_reached",
                level="WARNING",
                turn_id=turn_context.turn_id,
                continuation_count=continuation_count,
                hook_keys=list(stop_decision.hook_keys),
            )
            return result
        return await stream_turn(
            mind,
            session,
            pref_config,
            tools,
            turn_execution=create_continuation_execution(
                turn_execution,
                stop_decision.continuation_prompt,
                additional_context=stop_decision.additional_context,
            ),
            **reentry_kwargs,
        )

    if stop_decision.should_continue:
        observe(
            "hooks.stop.continuation_denied",
            level="WARNING",
            turn_id=turn_context.turn_id,
            outcome=outcome.status,
            can_continue=outcome.can_continue,
            stop_reason=outcome.terminal_meta.get("stop_reason"),
        )

    return result


if __name__ == '__main__':
    pass
