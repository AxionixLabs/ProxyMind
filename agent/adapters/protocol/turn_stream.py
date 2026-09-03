# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import time
import typing

from agent.adapters.protocol.activity_events import (
    TurnActivityProjector,
    normalize_turn_terminal_status,
)
from agent.adapters.protocol.approval_events import ApprovalEventHandler
from agent.adapters.protocol.approval_reviews import ApprovalReviewEventHandler
from agent.adapters.protocol.model_events import ModelStreamEventHandler
from agent.adapters.protocol.model_request import (
    build_model_stream_request,
    extend_request_context,
)
from agent.adapters.protocol.recovery_events import handle_stream_gap
from agent.adapters.protocol.tool_dispatch import StreamToolDispatcher
from agent.adapters.protocol.tool_events import ToolEventHandler
from agent.adapters.protocol.tool_results import ToolResultDelivery
from agent.adapters.protocol.turn_interrupts import (
    cancel_reconciliation_turn,
    interrupt_approval_cancelled_turn,
)
from agent.adapters.protocol.turn_setup import prepare_stream_turn
from agent.application.tools.execution import ToolExecutionAdapter
from agent.application.turns.exception_text import friendly_exception_text
from agent.application.turns.execution import (
    TurnExecution,
    create_continuation_execution,
)
from agent.application.turns.lifecycle import handle_lifecycle_event
from agent.application.turns.presentation import (
    FailureProjectionMode,
    StreamTurnPresentation,
)
from agent.application.turns.run_result import RunResult
from agent.application.turns.stream_outcome import StreamTurnOutcome
from agent.application.turns.transcript import (
    build_turn_input_payload,
    record_turn_started,
)
from agent.harness.execution.turn_finalizer import StreamTurnFinalizer
from agent.harness.execution.turn_runner import turn_continuation_count
from agent.harness.hooks.tool_lifecycle import ToolCallCoordinator
from agent.harness.hooks.turn_lifecycle import (
    PromptHookBlockedError,
    TurnHookEvents,
)
from agent.harness.tools.client_calls import ClientToolCallRunner
from agent.harness.tools.plan_calls import PlanToolCallRunner
from agent.ports import (
    ApprovalCoordinatorPort,
    ApprovalLedger,
    ApprovalReviewFeedPort,
    EffectJournalFactory,
    ExecutionPolicy,
    LocalEffectReconciliationRequired,
    McpSessionPort,
    ModelCapability,
    ModelCapabilityError,
    ModelEventStream,
    ProtocolCommandClient,
    TurnCleanupPort,
    ProtocolCommandError,
    TurnSessionContextPort,
    TurnSessionStatePort,
)
from agent.ports import OutputControlPort
from agent.ports import OutputSessionFactory
from observability import (
    observe,
    observe_exception,
)
from protocol.client.tools import ToolResultRequestError
from protocol.schema.stream_events import (
    StreamGapEvent,
    ToolApprovalRequiredEvent,
    ToolApprovalReviewEvent,
    TurnDoneEvent,
    TurnFailedEvent,
    TurnInputAcceptedEvent,
    TurnLogicalSettledEvent,
    TurnReconciliationRequiredEvent,
)
from protocol.schema.turn_inputs import TurnInput

MAX_STOP_CONTINUATIONS = 3


async def stream_turn(
    session: McpSessionPort,
    pref_config: dict[str, typing.Any],
    tools: list[dict[str, typing.Any]],
    *_,
    turn_execution: TurnExecution,
    model_capability: ModelCapability | None = None,
    protocol_client: ProtocolCommandClient | None = None,
    effect_journal_factory: EffectJournalFactory | None = None,
    tool_execution: ToolExecutionAdapter | None = None,
    session_factory: OutputSessionFactory | None = None,
    **kwargs
) -> RunResult:
    """处理流式事件、工具调用和输出上报。"""
    prepared = prepare_stream_turn(
        turn_execution,
        kwargs,
        session_factory=session_factory,
    )

    if not isinstance(model_capability, ModelCapability):
        raise RuntimeError("model capability is required")
    if not isinstance(protocol_client, ProtocolCommandClient):
        raise RuntimeError("protocol command client is required")
    if not callable(effect_journal_factory):
        raise RuntimeError("effect journal factory is required")
    if not isinstance(tool_execution, ToolExecutionAdapter):
        raise RuntimeError("tool execution adapter is required")

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

    presentation = output_session.presentation
    content = output_session.content

    first_event: bool = True
    outcome = StreamTurnOutcome()
    run_presentation = StreamTurnPresentation(
        outcome=outcome,
        content=content,
        presentation=presentation,
        event_report=ev_report,
    )

    failed_tool_context: list[str] = []

    approval_ledger = turn_context.approval_ledger
    if not isinstance(approval_ledger, ApprovalLedger):
        raise RuntimeError("approval ledger is required")
    cleanup = turn_context.cleanup
    if not isinstance(cleanup, TurnCleanupPort):
        raise RuntimeError("turn cleanup port is required")
    execution_policy = turn_context.execution_policy
    if not isinstance(execution_policy, ExecutionPolicy):
        raise RuntimeError("execution policy is required")
    approval_coordinator = turn_context.approval_coordinator
    if not isinstance(approval_coordinator, ApprovalCoordinatorPort):
        raise RuntimeError("approval coordinator is required")
    session_context = turn_context.session_context
    session_state = turn_context.session_state
    if turn_context.agent.depth == 0 and not isinstance(
        session_state,
        TurnSessionStatePort,
    ):
        raise RuntimeError("turn session state is required")

    prompt_blocked: bool = False

    turn_hook_events: TurnHookEvents | None = None

    event_stream = None
    assistant_text: str = ""

    transcript_factory = turn_context.transcript_factory
    if not callable(transcript_factory):
        raise RuntimeError("transcript factory is required")
    transcript = transcript_factory(
        turn_context.transcript_path,
        session_id=turn_context.sid,
        turn_id=turn_context.turn_id,
    )

    activity_projector = TurnActivityProjector(
        output_session.context,
        output_session.activity,
    )
    approval_review_handler = (
        ApprovalReviewEventHandler(
            approval_coordinator,
            session_id=turn_context.sid,
            run_id=turn_context.turn_id,
        )
        if isinstance(approval_coordinator, ApprovalReviewFeedPort)
        else None
    )

    async def project_terminal_activity() -> None:
        """在稳定终态内容上屏前幂等收敛当前 Turn 的活动展示。"""
        if not output_session.is_open:
            return None
        if activity_projector.terminal_status is not None:
            return None
        await activity_projector.turn_terminal(
            normalize_turn_terminal_status(outcome.status)
        )

    model_events = ModelStreamEventHandler(
        transcript=transcript,
        content=content,
        activity=activity_projector,
    )
    turn_state_stores = [approval_ledger]
    permission_grants = turn_context.permission_grants
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
        retry_activity_close=activity_projector.close_retries,
        stream_end=callbacks.stream_end,
        output_session=output_session,
        await_cleanup=cleanup.await_cleanup,
        continuation_count=turn_continuation_count(turn_execution),
    )

    try:
        transcript.open()
        record_turn_started(transcript, turn_execution)

        await output_session.open()
        await activity_projector.request_model_wait("initial")

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
            workdir=(
                session_context.workspace_root
                if isinstance(session_context, TurnSessionContextPort)
                else turn_context.cwd
            ),
            permissions=turn_context.permissions,
            turn_id=str(kwargs.get("turn_id") or ""),
            hook_warnings=(
                session_context.hook_startup_warnings
                if (
                    isinstance(session_context, TurnSessionContextPort)
                    and turn_context.agent.depth == 0
                    and turn_context.session_started
                )
                else ()
            ),
        )

        tool_call_coordinator = ToolCallCoordinator(
            hook_scope,
            transcript=transcript,
            command_sessions=(
                session_context.command_hook_sessions
                if isinstance(session_context, TurnSessionContextPort)
                else None
            ),
            failure_context_sink=failed_tool_context.extend,
        )
        approval_handler = ApprovalEventHandler(
            turn_context=turn_context,
            execution_policy=execution_policy,
            approval_coordinator=approval_coordinator,
            tools=tools,
            ledger=approval_ledger,
            coordinator=tool_call_coordinator,
            activity=activity_projector,
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

        extend_request_context(
            kwargs,
            additional_context=begin_result.additional_context,
        )

        async def interrupt_nested_turn(call_id: str) -> bool:
            """中断由嵌套本地工具审批取消的当前轮次。"""
            return await interrupt_approval_cancelled_turn(
                protocol_client,
                cid=turn_context.cid,
                sid=turn_context.sid,
                turn_id=turn_context.turn_id,
                call_id=call_id,
            )

        client_tool_runner = ClientToolCallRunner(
            session=session,
            output_control=output_control,
            presentation=presentation,
            tools=tools,
            pref_config=pref_config,
            tool_call_coordinator=tool_call_coordinator,
            tool_execution=tool_execution,
            activity=activity_projector,
            effect_journal=effect_journal_factory(),
            effect_reconciler=protocol_client.post_effect_reconciliation,
            patch_preview=turn_context.patch_preview,
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
            presentation=presentation,
            tools=tools,
            turn_context=turn_context,
            pref_config=pref_config,
            tool_call_coordinator=tool_call_coordinator,
            tool_execution=tool_execution,
        )
        tool_event_handler = ToolEventHandler(
            turn_context=turn_context,
            execution_policy=execution_policy,
            approval_coordinator=approval_coordinator,
            patch_preview=turn_context.patch_preview,
            tools=tools,
            ledger=approval_ledger,
            coordinator=tool_call_coordinator,
            client_runner=client_tool_runner,
            plan_runner=plan_tool_runner,
            tool_execution=tool_execution,
            activity=activity_projector,
            presentation=presentation,
            transcript=transcript,
            post_result=tool_result_delivery.deliver,
            interrupt_turn=interrupt_nested_turn,
        )
        tool_dispatcher = StreamToolDispatcher(
            handler=tool_event_handler,
            activity=activity_projector,
        )

        model_request = build_model_stream_request(
            turn_context,
            pref_config=pref_config,
            message=message,
            tools=tools,
            options=kwargs,
        )
        event_stream = model_capability.stream(
            model_request,
            on_recovery_status=activity_projector.transport_recovery_changed,
            on_approval_snapshot=approval_handler.restore_snapshot,
        )
        if not isinstance(event_stream, ModelEventStream):
            raise TypeError("model capability returned an invalid event stream")

        async for event in event_stream:
            event_count += 1

            review_event_is_current = True
            if approval_review_handler is not None:
                review_event_is_current = (
                    await approval_review_handler.observe_presentation(event)
                )

            if ev_report:
                ev_report.bind_event(event)

            if first_event:
                observe(
                    "stream.first_event",
                    event_type=event.type,
                    latency_ms=int((time.perf_counter() - started_at) * 1000),
                )
                first_event = False

            event_type = event.type

            if await model_events.handle(event, projection=event_stream):
                continue

            if isinstance(event, ToolApprovalReviewEvent):
                if approval_review_handler is None:
                    raise RuntimeError(
                        "approval review feed is required for review events"
                    )
                if review_event_is_current:
                    await approval_review_handler.handle(event)
                continue

            if isinstance(event, StreamGapEvent):
                gap_decision = await handle_stream_gap(
                    event,
                    activity=activity_projector,
                    outcome=outcome,
                    presentation=run_presentation,
                )
                if gap_decision == "stop":
                    break
                continue

            if event_type == "turn.start":
                if callbacks.input_event is not None:
                    callbacks.input_event(event)
                continue

            if event_type == "turn.thinking":
                await activity_projector.request_model_wait("server_thinking")
                continue

            if isinstance(event, TurnFailedEvent):

                if tool_dispatcher.batch_active:
                    raise ValueError("turn.failed arrived before tool.calls.done")

                outcome.record_failed_event(event)
                await activity_projector.turn_terminal("failed")

                observe(
                    "stream.turn_failed",
                    level="ERROR",
                    error=outcome.error,
                    error_type=event.error_type or None,
                    error_source=event.error_source or None,
                    retryable=event.retryable,
                    stop_reason=event.stop_reason,
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
                    await activity_projector.request_model_wait("tool_result")
                    continue

                cancelled = await cancel_reconciliation_turn(
                    protocol_client,
                    cid=str(metadata.get("cid") or ""),
                    sid=str(metadata.get("sid") or ""),
                    turn_id=turn_context.turn_id,
                    effect_id=event.effect_id,
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
                await activity_projector.turn_terminal(
                    "reconciliation_required"
                )

                observe(
                    "stream.reconciliation_required",
                    level="ERROR",
                    turn_id=turn_context.turn_id,
                    effect_id=event.effect_id,
                    error=outcome.error,
                    turn_released=cancelled,
                )
                await run_presentation.emit_failure(
                    "turn.reconciliation_required",
                    mode=FailureProjectionMode.PROJECTION_ONLY,
                )
                break

            if isinstance(event, TurnDoneEvent):

                if tool_dispatcher.batch_active:
                    raise ValueError("turn.done arrived before tool.calls.done")

                outcome.record_done_event(event)
                await activity_projector.turn_terminal(
                    normalize_turn_terminal_status(event.status)
                )

                if event.status == "interrupted":
                    outcome.confirm_interrupt()
                    if callbacks.interrupted is not None:
                        callbacks.interrupted()

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
                if isinstance(event, TurnLogicalSettledEvent):
                    await activity_projector.logical_settled()
                continue

            if isinstance(event, ToolApprovalRequiredEvent):
                if not review_event_is_current:
                    continue
                await approval_handler.handle(event)
                continue

            tool_dispatch = await tool_dispatcher.dispatch(event)
            if tool_dispatch.status == "interrupted":
                outcome.interrupt(tool_dispatch.error)
                outcome.confirm_interrupt()
                break
            if tool_dispatch.status == "handled":
                continue

            if await handle_lifecycle_event(
                event,
                presentation=presentation,
            ):
                await activity_projector.request_model_wait("lifecycle")
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
        await project_terminal_activity()
        await run_presentation.emit_failure(
            failure_phase,
            mode=FailureProjectionMode.PROJECTION_ONLY,
        )

    except LocalEffectReconciliationRequired as error:
        outcome.require_reconciliation(str(error))
        observe(
            "stream.local_effect_reconciliation_required",
            level="ERROR",
            turn_id=turn_context.turn_id,
            effect_id=error.effect_id,
        )
        await project_terminal_activity()
        await run_presentation.emit_failure(
            "turn.reconciliation_required",
            effect_id=error.effect_id,
        )

    except PromptHookBlockedError as error:
        outcome.fail(
            str(error),
            additional_context=error.additional_context,
        )
        prompt_blocked = True

        if outcome.additional_context and turn_context.agent.depth == 0:
            session_state.queue_turn_context(outcome.additional_context)

        observe(
            "stream.prompt_blocked",
            level="WARNING",
            turn_id=turn_context.turn_id,
        )

        await project_terminal_activity()
        await run_presentation.emit_failure(
            "turn.prompt_blocked",
            mode=FailureProjectionMode.PROJECTION_ONLY,
        )

    except ModelCapabilityError as error:
        outcome.fail(
            error.message,
            error_code=error.code,
            error_details=error.details,
            additional_context=failed_tool_context,
        )
        if outcome.additional_context and turn_context.agent.depth == 0:
            session_state.queue_turn_context(outcome.additional_context)
        observe(
            "stream.model_capability_failed",
            level="ERROR",
            turn_id=turn_context.turn_id,
            code=error.code,
            retryable=error.retryable,
            details=error.details,
        )

        if output_session.is_open:
            await project_terminal_activity()
            await run_presentation.emit_failure("turn.failed")

    except asyncio.CancelledError:
        outcome.interrupt()
        if failed_tool_context and turn_context.agent.depth == 0:
            session_state.queue_turn_context(failed_tool_context)
        observe(
            "stream.interrupted",
            level="WARNING",
            events=event_count,
            elapsed_ms=int((time.perf_counter() - started_at) * 1000),
        )
        await project_terminal_activity()
        raise

    except Exception as e:
        outcome.fail(
            friendly_exception_text(e),
            additional_context=failed_tool_context,
        )
        if outcome.additional_context and turn_context.agent.depth == 0:
            session_state.queue_turn_context(outcome.additional_context)
        observe_exception(
            "stream.failed",
            e,
            events=event_count,
            elapsed_ms=int((time.perf_counter() - started_at) * 1000),
        )

        if output_session.is_open:
            await project_terminal_activity()
            await run_presentation.emit_failure("turn.failed")

    else:
        outcome.settle_stream()
        if not outcome.has_terminal_status:
            await project_terminal_activity()
            await run_presentation.emit_failure(
                "turn.incomplete",
                mode=FailureProjectionMode.PROJECTION_ONLY,
            )

        if outcome.is_completed and turn_context.agent.depth == 0:
            model_events.flush_pending()
            assistant_text = event_stream.assistant_text
            session_state.remember_assistant_reply(assistant_text)

        await run_presentation.emit_result(event_stream.sources)

        observe(
            "stream.complete",
            outcome=outcome.observation_outcome,
            events=event_count,
            elapsed_ms=int((time.perf_counter() - started_at) * 1000),
            usage=outcome.usage or None,
        )

    finally:
        if approval_review_handler is not None:
            try:
                await cleanup.await_cleanup(approval_review_handler.close())
            except asyncio.CancelledError:
                raise
            except Exception as error:
                observe_exception("stream.approval_review_close_failed", error)
        if event_stream is not None:
            try:
                await cleanup.await_cleanup(event_stream.aclose())
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
        await project_terminal_activity()
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
            session,
            pref_config,
            tools,
            turn_execution=create_continuation_execution(
                turn_execution,
                stop_decision.continuation_prompt,
                additional_context=stop_decision.additional_context,
            ),
            model_capability=model_capability,
            protocol_client=protocol_client,
            effect_journal_factory=effect_journal_factory,
            tool_execution=tool_execution,
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
