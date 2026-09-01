# -*- coding: utf-8 -*-

import typing

from agent.application.turns.context import TurnContext
from agent.harness.hooks.scope import resolve_hook_scope
from agent.ports import (
    ApprovalLedger,
    HookExecutionScopePort,
    PermissionGrantReader,
    RootTurnSessionPort,
    TurnStartResultPort,
)
from agent.domain.policies import PermissionSettings

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

    @property
    def permissions(self) -> PermissionSettings:
        """返回当前根会话的默认权限设置。"""
        return self._controller.permissions

    @property
    def approval_ledger(self) -> ApprovalLedger | None:
        """返回根轮次使用的审批调用账本。"""
        return self._controller.approval_call_ledger

    async def fresh_pref_config(
        self,
        *,
        ttl_sec: float,
    ) -> dict[str, typing.Any]:
        """读取当前有效的偏好配置快照。"""
        return await self._controller.fresh_pref_config(ttl_sec=ttl_sec)

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
        return resolve_hook_scope(self._controller, context)


__all__ = ("ControllerRootTurnSession",)
