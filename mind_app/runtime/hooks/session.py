# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio
from dataclasses import replace
from agent.domain.hooks import (
    SESSION_END_REASONS,
    SessionEndReason,
)
from agent.application.hooks.context import HookExecutionContext
from observability import (
    observe,
    observe_exception,
)
from agent.harness.hooks.scope import HookExecutionScope

SessionScopeFactory = typing.Callable[
    [HookExecutionContext],
    HookExecutionScope,
]

SessionCleanup = typing.Callable[[str], typing.Awaitable[None]]

SessionEndPreparation = typing.Callable[[], None]


class SessionLifecycleGateway:
    """统一关闭根会话并分发会话结束事件。"""

    def __init__(
        self,
        *,
        scope_factory: SessionScopeFactory,
        cleanup_session: SessionCleanup
    ) -> None:
        self._scope_factory = scope_factory
        self._cleanup_session = cleanup_session
        self._ended_lifecycle_id: int | None = None
        self._lock: asyncio.Lock = asyncio.Lock()

    async def end(
        self,
        lifecycle_id: int,
        context: HookExecutionContext,
        *,
        reason: SessionEndReason,
        transcript_path: str,
        last_assistant_message: str,
        before_dispatch: SessionEndPreparation | None = None
    ) -> bool:
        """结束一个根会话生命周期，并保证同一生命周期只执行一次。"""
        normalized_reason = str(reason or "").strip()
        if normalized_reason not in SESSION_END_REASONS:
            raise ValueError("session end reason is invalid")

        async with self._lock:
            if lifecycle_id == self._ended_lifecycle_id:
                return False

            try:
                try:
                    if before_dispatch is not None:
                        before_dispatch()
                finally:
                    scope = self._scope_factory(replace(
                        context,
                        transcript_path=str(transcript_path or "") or None,
                    ))
                    if scope.has_matching("SessionEnd", "other"):
                        await scope.dispatch(
                            "SessionEnd",
                            payload={"reason": "other"},
                            match_value="other",
                            diagnostics={
                                "reason": normalized_reason,
                                "last_assistant_message": str(
                                    last_assistant_message or ""
                                ),
                            },
                        )
            except asyncio.CancelledError:
                raise
            except Exception as error:
                observe_exception(
                    "hooks.session_end.failed",
                    error,
                    level="WARNING",
                    session_id=context.session_id,
                    reason=normalized_reason,
                )
            finally:
                try:
                    await self._cleanup_session(context.session_id)
                except Exception as error:
                    observe_exception(
                        "hooks.session_cleanup.failed",
                        error,
                        level="WARNING",
                        session_id=context.session_id,
                    )
                self._ended_lifecycle_id = lifecycle_id

            observe(
                "session.ended",
                session_id=context.session_id,
                reason=normalized_reason,
            )
            return True


if __name__ == '__main__':
    pass
