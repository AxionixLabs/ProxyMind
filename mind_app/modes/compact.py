# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio
from dataclasses import dataclass
from mind_nova.modes import RunMode
from mind_nova.requests.compact import (
    build_compact_payload,
    stream_compact_events
)
from ..runtime.execution import AgentContext
from ..runtime.hooks.compact import (
    CompactHookBlockedError,
    CompactHookEvents
)
from ..runtime.hooks.scope import (
    HookExecutionContext,
    HookExecutionScope
)
from engine.observability import (
    observe,
    observe_exception
)

if typing.TYPE_CHECKING:
    from ..controller import Mind

CompactOutcome = typing.Literal[
    "completed",
    "failed",
    "interrupted",
]
CompactProgress = typing.Callable[[str], None]


@dataclass(frozen=True, slots=True)
class CompactResult:
    """描述一次上下文压缩的稳定结果。"""
    outcome: CompactOutcome
    message: str
    before_items: int | None = None
    after_items: int | None = None

    @property
    def ok(self) -> bool:
        """返回上下文压缩是否完成。"""
        return self.outcome == "completed"


async def compact_conversation(
    mind: "Mind",
    *,
    run_mode: RunMode,
    pref_config: dict[str, typing.Any],
    source: str,
    trigger: str = "manual",
    on_progress: CompactProgress | None = None,
) -> CompactResult:
    """执行当前会话的上下文压缩及其生命周期 Hook。"""
    metadata = mind.conversation.snapshot()
    context = _hook_context(
        mind,
        metadata=metadata,
        run_mode=run_mode,
        pref_config=pref_config,
        source=source,
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
    )
    attempted = False

    observe(
        "compact.start",
        mode=run_mode,
        cid=metadata["cid"],
        sid=metadata["sid"],
        trigger=trigger,
    )

    try:
        await hook_events.begin(trigger)
        attempted = True

        payload = build_compact_payload({
            "mode": run_mode,
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
        )
        observe_exception("compact.failed", error)

    finally:
        if attempted:
            try:
                await mind.await_cleanup(hook_events.post_compact(
                    trigger=trigger,
                    outcome=result.outcome,
                    message=result.message,
                    before_items=result.before_items,
                    after_items=result.after_items,
                ))
            except Exception as error:
                observe_exception(
                    "hooks.post_compact.failed",
                    error,
                    level="WARNING",
                )

    return result


def _hook_context(
    mind: "Mind",
    *,
    metadata: dict[str, str],
    run_mode: RunMode,
    pref_config: dict[str, typing.Any],
    source: str,
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
        mode=run_mode,
        source=str(source or "").strip(),
        sandbox_mode=permissions.sandbox_mode,
        permission_mode=permissions.approval_policy,
        agent_id=agent.agent_id,
        agent_type=agent.agent_type,
        agent_depth=agent.depth,
        parent_agent_id=agent.parent_agent_id,
    )


def _optional_int(value: typing.Any) -> int | None:
    """把整数统计值规范化为可选整数。"""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


if __name__ == '__main__':
    pass
