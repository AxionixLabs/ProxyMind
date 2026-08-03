# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio
from dataclasses import (
    dataclass,
    replace
)
from mind_core.hooks import (
    CompactOutcome,
    CompactResultSource,
    CompactTriggerReason,
    CompactTriggerSource
)
from mind_nova.requests.compact import (
    build_compact_payload,
    stream_compact_events
)
from .execution import AgentContext
from .hooks.compact import (
    CompactHookBlockedError,
    CompactHookEvents
)
from .hooks.scope import (
    HookExecutionContext,
    HookExecutionScope
)
from .hooks.turn import TurnHookEvents
from engine.observability import (
    observe,
    observe_exception
)

if typing.TYPE_CHECKING:
    from ..controller import Mind
    from .hooks.models import HookDecision

CompactProgress = typing.Callable[[str], None]


@dataclass(frozen=True, slots=True)
class CompactResult:
    """描述一次上下文压缩的稳定结果。"""
    outcome: CompactOutcome
    message: str
    before_items: int | None = None
    after_items: int | None = None
    summary: str = ""
    transcript_path: str = ""
    trigger: CompactTriggerReason = "manual"
    trigger_source: CompactTriggerSource = "client"
    result_source: CompactResultSource = "fallback"
    continue_execution: bool = True

    @property
    def ok(self) -> bool:
        """返回上下文压缩是否完成。"""
        return self.outcome == "completed" and self.continue_execution


async def compact_conversation(
    mind: "Mind",
    *,
    pref_config: dict[str, typing.Any],
    source: str,
    trigger: CompactTriggerReason = "manual",
    trigger_source: CompactTriggerSource = "client",
    on_progress: CompactProgress | None = None
) -> CompactResult:
    """执行当前会话的上下文压缩及其生命周期 Hook。"""
    metadata        = mind.conversation.snapshot()
    transcript_path = mind.transcripts.path_for_session(metadata["sid"])

    context = _hook_context(
        mind,
        metadata=metadata,
        pref_config=pref_config,
        source=source,
        transcript_path=transcript_path,
    )

    try:
        scope = mind.hook_scope(context)
    except (OSError, TypeError, ValueError) as error:
        observe_exception(
            "hooks.resolve.failed",
            error,
            level="WARNING",
        )
        scope = HookExecutionScope.empty(context)

    hook_events = CompactHookEvents(scope)

    result = CompactResult(
        outcome="failed",
        message="Context compaction failed. Please try again.",
        summary="Context compaction failed. Please try again.",
        transcript_path=transcript_path,
        trigger=trigger,
        trigger_source=trigger_source,
    )

    attempted: bool = False

    transcript = mind.transcripts.writer(
        transcript_path,
        session_id=metadata["sid"],
    )
    transcript.open()

    observe(
        "compact.start",
        cid=metadata["cid"],
        sid=metadata["sid"],
        trigger=trigger,
    )

    try:
        await hook_events.begin(
            trigger,
            trigger_source=trigger_source,
        )
        attempted = True

        payload = build_compact_payload({
            "cid": metadata["cid"],
            "sid": metadata["sid"],
            "llm_conf": pref_config,
            "strategy": "memento",
        })

        async for event in stream_compact_events(payload):
            event_type = str(event.get("type") or "")
            message = str(event.get("message") or "").strip()

            if event_type == "conversation.compact.started":
                if on_progress is not None:
                    on_progress(message)
                observe("compact.remote.started")
                continue

            if event_type == "conversation.compact.failed":
                result = CompactResult(
                    outcome="failed",
                    message=(
                        message
                        or "Context compaction failed. Please try again."
                    ),
                    summary=str(event.get("summary") or message or "").strip(),
                    transcript_path=transcript_path,
                    trigger=trigger,
                    trigger_source=trigger_source,
                )
                observe(
                    "compact.failed",
                    level="ERROR",
                    reason=message or "remote_failed",
                )
                break

            if event_type == "conversation.compact":
                result = CompactResult(
                    outcome="completed",
                    message=message or "Context compacted.",
                    before_items=_optional_int(event.get("before_items")),
                    after_items=_optional_int(event.get("after_items")),
                    summary=str(
                        event.get("summary") or message or "Context compacted."
                    ).strip(),
                    transcript_path=transcript_path,
                    trigger=trigger,
                    trigger_source=trigger_source,
                    result_source="server",
                )
                observe(
                    "compact.complete",
                    before_items=result.before_items,
                    after_items=result.after_items,
                )
                break
        else:
            observe(
                "compact.failed",
                level="ERROR",
                reason="missing_terminal_event",
            )

    except CompactHookBlockedError as error:
        result = CompactResult(
            outcome="failed",
            message=f"Context compaction blocked: {error}",
            summary=f"Context compaction blocked: {error}",
            transcript_path=transcript_path,
            trigger=trigger,
            trigger_source=trigger_source,
        )
        observe(
            "compact.hook_blocked",
            level="WARNING",
            trigger=trigger,
        )

    except asyncio.CancelledError:
        result = CompactResult(
            outcome="interrupted",
            message="Context compaction interrupted.",
            summary="Context compaction interrupted.",
            transcript_path=transcript_path,
            trigger=trigger,
            trigger_source=trigger_source,
        )
        observe("compact.interrupted", level="WARNING")
        raise

    except Exception as error:
        message = str(error).strip()
        detail = (
            f": {type(error).__name__}: {message}"
            if message
            else f": {type(error).__name__}"
        )
        result = CompactResult(
            outcome="failed",
            message=f"Context compaction failed{detail}",
            summary=f"Context compaction failed{detail}",
            transcript_path=transcript_path,
            trigger=trigger,
            trigger_source=trigger_source,
        )
        observe_exception("compact.failed", error)

    finally:
        if attempted:
            transcript.append(
                (
                    "context.compacted"
                    if result.outcome == "completed"
                    else "context.compaction.failed"
                ),
                actor="system",
                payload={
                    "outcome": result.outcome,
                    "trigger": trigger,
                    "before_items": result.before_items,
                    "after_items": result.after_items,
                    "summary": result.summary,
                },
            )
            if result.outcome == "completed":
                try:
                    post_decision = await mind.await_cleanup(
                        hook_events.post_compact(
                            trigger=trigger,
                            trigger_source=trigger_source,
                            result_source=result.result_source,
                            outcome=result.outcome,
                            message=result.message,
                            summary=result.summary,
                            transcript_path=result.transcript_path,
                            before_items=result.before_items,
                            after_items=result.after_items,
                        )
                    )
                except Exception as error:
                    observe_exception(
                        "hooks.post_compact.failed",
                        error,
                        level="WARNING",
                    )
                else:
                    result = _apply_post_compact_decision(
                        result,
                        post_decision,
                    )

                if result.ok:
                    result = await _run_compact_session_start(
                        mind,
                        scope,
                        result,
                    )

        transcript.close()

    return result


def _apply_post_compact_decision(
    result: CompactResult,
    decision: "HookDecision"
) -> CompactResult:
    """把压缩后 Hook 的控制结果应用到稳定返回值。"""
    message = result.message

    if not decision.allowed:
        reason  = decision.reason or "continuation denied by hook"
        message = f"{message} Post-compact continuation blocked: {reason}"

    return replace(
        result,
        message=message,
        continue_execution=result.continue_execution and decision.allowed,
    )


async def _run_compact_session_start(
    mind: "Mind",
    scope: HookExecutionScope,
    result: CompactResult
) -> CompactResult:
    """在成功压缩后分发压缩来源的会话启动事件。"""
    try:
        decision = await mind.await_cleanup(
            TurnHookEvents(scope).session_start(source="compact")
        )
    except Exception as error:
        observe_exception(
            "hooks.compact_session_start.failed",
            error,
            level="WARNING",
        )
        return result

    if decision.additional_context:
        mind.conversation.queue_turn_context(decision.additional_context)

    if decision.allowed:
        return result

    reason = decision.reason or "continuation denied by hook"
    return replace(
        result,
        message=f"{result.message} Compact session start blocked: {reason}",
        continue_execution=False,
    )


def _hook_context(
    mind: "Mind",
    *,
    metadata: dict[str, str],
    pref_config: dict[str, typing.Any],
    source: str,
    transcript_path: str
) -> HookExecutionContext:
    """从压缩操作创建生命周期 Hook 公共上下文。"""
    agent = AgentContext.root(metadata["sid"])
    primary = pref_config.get("primary")
    model = (
        str(primary.get("model") or "").strip()
        if isinstance(primary, dict)
        else ""
    )
    permissions = mind.permissions

    return HookExecutionContext(
        session_id=agent.root_session_id,
        conversation_id=metadata["cid"],
        cwd=str(mind.history_workspace),
        model=model,
        source=str(source or "").strip(),
        sandbox_mode=permissions.sandbox_mode,
        permission_mode=permissions.approval_policy,
        agent_id=agent.agent_id,
        agent_type=agent.agent_type,
        agent_depth=agent.depth,
        parent_agent_id=agent.parent_agent_id,
        root_session_id=agent.root_session_id,
        transcript_path=transcript_path or None,
    )


def _optional_int(value: typing.Any) -> int | None:
    """将整数统计值规范化为可选值。"""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


if __name__ == '__main__':
    pass
