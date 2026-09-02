# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import typing
from pathlib import Path
from infrastructure.platform.process_sessions import (
    ProcessSession,
    ProcessSessionManager,
    ProcessSessionSpec,
)


class UserShellExecution(object):
    """管理用户显式 Shell 的本地执行和事件读取。"""

    def __init__(
        self,
        *,
        root: Path,
        sessions: ProcessSessionManager,
        relative_path: typing.Callable[[Path], str]
    ) -> None:
        """保存工作区、共享进程会话表和路径展示转换器。"""
        self._root = root
        self._sessions = sessions
        self._relative_path = relative_path

    async def start_user_shell_session(
        self,
        *,
        command: str,
        args: typing.Sequence[str],
        timeout_sec: int = 3600,
        idle_timeout_sec: int = 1800,
        owner_cid: str = "",
        owner_sid: str = ""
    ) -> dict[str, typing.Any]:
        """启动用户显式请求的 Shell 会话。"""
        cmd = str(command or "").strip()
        resolved_args = tuple(
            str(item)
            for item in args
            if str(item or "").strip()
        )

        if not cmd or not resolved_args:
            return {"ok": False, "reason": "command_empty"}

        session = await self._sessions.start(ProcessSessionSpec(
            command=cmd,
            args=resolved_args,
            cwd=str(self._root),
            display_cwd=self._relative_path(self._root),
            runtime={
                "name": os.path.basename(resolved_args[0]),
                "executable": resolved_args[0],
                "source": "local_user",
            },
            origin="tui_shell",
            timeout_sec=max(1, int(timeout_sec or 3600)),
            idle_timeout_sec=max(1, int(idle_timeout_sec or 1800)),
            owner_cid=str(owner_cid or "").strip(),
            owner_sid=str(owner_sid or "").strip(),
            stdin_enabled=False,
        ))
        return await self.exec_session_output_snapshot(
            session_id=session.session_id,
            max_output_chars=12000,
        )

    async def exec_session_output_snapshot(
        self,
        *,
        session_id: str,
        max_output_chars: int = 12000
    ) -> dict[str, typing.Any]:
        """返回用户 Shell 会话的只读输出快照。"""
        if self._user_shell_session(session_id) is None:
            return {
                "ok": False,
                "reason": "user_shell_session_not_found",
                "session_id": str(session_id or "").strip(),
            }
        return await self._sessions.output_snapshot(
            session_id,
            max_output_chars=max_output_chars,
        )

    async def running_exec_sessions(self) -> dict[str, typing.Any]:
        """返回仅属于用户 Shell 来源的进程会话摘要。"""
        snapshot = await self._sessions.running_snapshot()
        items = [
            item
            for item in snapshot.get("items", [])
            if isinstance(item, dict)
               and item.get("origin") == "tui_shell"
        ]
        background_items = [
            item for item in items if item.get("background")
        ]
        snapshot.update({
            "count": len(items),
            "items": items,
            "background_count": len(background_items),
            "background_items": background_items,
            "user_shell_items": [
                item for item in items if not item.get("background")
            ],
            "revision": self._sessions.change_revision,
        })
        return snapshot

    async def wait_exec_session_update(
        self,
        session_id: str,
        *,
        revision: int,
        timeout_sec: float,
    ) -> dict[str, typing.Any]:
        """等待用户 Shell 的输出或完成事件。"""
        if self._user_shell_session(session_id) is None:
            return {
                "changed": True,
                "event": "failed",
                "delta": [],
                "delta_reset": True,
                "snapshot": {
                    "ok": False,
                    "reason": "user_shell_session_not_found",
                    "session_id": str(session_id or "").strip(),
                },
            }
        changed = await self._sessions.wait_for_update(
            session_id,
            revision=revision,
            timeout_sec=timeout_sec,
        )
        if not changed:
            return {"changed": False}

        delta = await self._sessions.output_delta(
            session_id,
            revision=revision,
        )
        snapshot = delta.get("snapshot")
        if not isinstance(snapshot, dict):
            return {
                "changed": True,
                "event": "failed",
                "delta": delta.get("items", []),
                "delta_reset": bool(delta.get("reset")),
                "snapshot": {
                    "ok": False,
                    "reason": "exec_session_update_unavailable",
                    "session_id": str(session_id or "").strip(),
                },
            }
        if str(snapshot.get("status") or "").strip() == "exited":
            snapshot = await self.exec_session_output_snapshot(
                session_id=session_id,
                max_output_chars=120000,
            )
        return {
            "changed": True,
            "event": (
                "completed"
                if str(snapshot.get("status") or "").strip() == "exited"
                else "delta"
            ),
            "delta": delta.get("items", []),
            "delta_reset": bool(delta.get("reset")),
            "snapshot": snapshot,
        }

    async def control_exec_session(
        self,
        *,
        session_id: str,
        control: str
    ) -> dict[str, typing.Any]:
        """对用户 Shell 会话执行中断或终止控制。"""
        session = self._user_shell_session(session_id)
        if session is None:
            return {
                "ok": False,
                "reason": "exec_session_not_found",
                "session_id": str(session_id or "").strip(),
            }

        reason = await self._sessions.apply(session, control=control)
        if reason is not None:
            return {
                "ok": False,
                "reason": reason,
                "session_id": session.session_id,
            }

        return await self.exec_session_output_snapshot(
            session_id=session.session_id,
            max_output_chars=12000,
        )

    async def mark_exec_session_background(self, session_id: str) -> bool:
        """把用户 Shell 会话标记为后台运行。"""
        if self._user_shell_session(session_id) is None:
            return False
        return await self._sessions.mark_background(session_id)

    def _user_shell_session(self, session_id: str) -> ProcessSession | None:
        """返回属于用户 Shell 来源的会话，其他来源视为不可见。"""
        session = self._sessions.get(session_id)
        if session is None or session.origin != "tui_shell":
            return None
        return session


if __name__ == '__main__':
    pass
