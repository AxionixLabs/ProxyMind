# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import time
import uuid
import typing
from backend.mcp_core.coding_native.base import NativeCodingComponent


class SessionTools(NativeCodingComponent):
    """维护变更会话状态，并提供会话查询和轻量创建能力。"""

    @staticmethod
    def _session_has_run(
        session: dict[str, typing.Any],
        run_id: str
    ) -> bool:
        """判断会话中是否包含指定运行记录。"""
        return any(
            isinstance(item, dict) and item.get("run_id") == run_id
            for item in session.get("runs") or []
        )

    def session_snapshot(
        self,
        session_id: str | None = None
    ) -> dict[str, typing.Any]:
        """返回指定变更会话；未指定时返回最近会话列表。"""
        if session_id:
            item = self.sessions.get(str(session_id))
            if not isinstance(item, dict):
                return self._fail("session_not_found", session_id=session_id)
            return self._ok(
                f"coding session returned session_id={session_id}",
                session=self._public_session(item)
            )

        sessions = list(self.sessions.values())[-10:]
        return self._ok(
            f"coding sessions count={len(self.sessions)}",
            sessions=[
                {
                    "session_id"  : item.get("session_id"),
                    "ok"          : item.get("ok"),
                    "prompt"      : item.get("prompt"),
                    "started_at"  : item.get("started_at"),
                    "finished_at" : item.get("finished_at"),
                    "summary"     : item.get("summary")
                }
                for item in sessions
            ],
            count=len(self.sessions)
        )

    def _public_session(
        self,
        session: dict[str, typing.Any]
    ) -> dict[str, typing.Any]:
        """生成可对外返回的会话结构。"""
        public = dict(session)

        runs: list[dict[str, typing.Any]] = []
        for run in session.get("runs") or []:
            if not isinstance(run, dict):
                continue
            item = dict(run)
            if isinstance(item.get("snapshot"), dict):
                item["snapshot"] = self._public_snapshot(item["snapshot"])
            runs.append(item)
        public["runs"] = runs

        return public

    def _begin_session(
        self,
        *,
        prompt: str = "",
        session_id: str | None = None
    ) -> dict[str, typing.Any]:
        """创建或复用一个不执行任务的变更会话。"""
        sid     = str(session_id or "").strip() or f"native_{uuid.uuid4().hex[:10]}"
        session = self.sessions.get(sid)

        if isinstance(session, dict):
            if prompt:
                session["prompt"] = str(prompt)
            session.setdefault("plan", self._empty_plan())
            session.setdefault("runs", [])
            session.setdefault("summary", {})
            return session

        session = {
            "session_id"  : sid,
            "prompt"      : str(prompt or ""),
            "started_at"  : time.time(),
            "finished_at" : None,
            "ok"          : None,
            "runs"        : [],
            "run_count"   : 0,
            "last_run_id" : None,
            "plan"        : self._empty_plan(),
            "summary"     : {},
            "status"      : ""
        }
        self.sessions[sid] = session
        return session


if __name__ == '__main__':
    pass
