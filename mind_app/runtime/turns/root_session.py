# -*- coding: utf-8 -*-

import typing

from agent.application.turns.context import TurnContext
from agent.harness.hooks.scope import HookExecutionScope
from agent.ports import (
    HookExecutionScopePort,
    PermissionGrantReader,
    RootTurnSessionPort,
    TurnStartResultPort,
)
from mind_app.runtime.turns.executor import resolve_turn_hook_scope

if typing.TYPE_CHECKING:
    from mind_app.controller import Mind


class ControllerRootTurnSession(RootTurnSessionPort):
    """把 Controller 的根会话资源适配为根轮次准备端口。"""

    def __init__(self, controller: "Mind") -> None:
        """绑定组合根提供的根轮次会话边界。"""
        self._controller = controller

    @property
    def workspace_root(self) -> str:
        """返回根轮次绑定的工作区。"""
        return str(self._controller.history_workspace or "")

    @property
    def permission_grants(self) -> PermissionGrantReader | None:
        """返回当前会话的权限授予读取端口。"""
        return self._controller.permission_grants

    @property
    def output_record_path(self) -> str:
        """返回根轮次输出记录路径。"""
        report = self._controller.report
        return str(report.output_record_path or "")

    async def begin_conversation_turn(
        self,
        cid: str | None,
        sid: str | None,
        *,
        title: str,
        source: str,
    ) -> TurnStartResultPort:
        """登记或续用根会话并返回轮次边界快照。"""
        return await self._controller.begin_conversation_turn(
            cid=cid,
            sid=sid,
            title=title,
            source=source,
        )

    def transcript_path_for_session(self, sid: str) -> str:
        """返回指定会话的 Transcript 路径。"""
        return self._controller.transcripts.path_for_session(sid)

    def hook_scope(self, context: TurnContext) -> HookExecutionScopePort:
        """为当前轮次创建固定 Hook 作用域。"""
        scope = resolve_turn_hook_scope(self._controller, context)
        if not isinstance(scope, HookExecutionScope):
            raise RuntimeError("root turn hook scope is invalid")
        return scope


__all__ = ("ControllerRootTurnSession",)
