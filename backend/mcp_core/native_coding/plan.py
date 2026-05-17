# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import time
import typing
from backend.mcp_core.native_coding.base import NativeCodingComponent


class PlanTools(NativeCodingComponent):

    def update_plan(
        self,
        *,
        session_id: str | None = None,
        todos: list[dict[str, typing.Any]] | None = None,
        assumptions: list[str] | None = None,
        next_steps: list[str] | None = None,
        note: str | None = None,
        mode: str = "merge"
    ) -> dict[str, typing.Any]:
        session = self.sessions.get(str(session_id or "")) if session_id else self._latest_session()
        if not isinstance(session, dict):
            return self._fail("session_not_found", session_id=session_id)
        plan = session.setdefault("plan", self._empty_plan())
        mode_value = str(mode or "merge").strip().lower()
        if mode_value not in {"merge", "replace"}:
            mode_value = "merge"

        if mode_value == "replace":
            plan["todos"] = []
            plan["assumptions"] = []
            plan["next_steps"] = []
            plan["notes"] = []

        if todos is not None:
            plan["todos"] = self._merge_todos(plan.get("todos") or [], todos, replace=(mode_value == "replace"))
        if assumptions is not None:
            plan["assumptions"] = self._merge_strings(plan.get("assumptions") or [], assumptions)
        if next_steps is not None:
            plan["next_steps"] = self._merge_strings(plan.get("next_steps") or [], next_steps)
        if note:
            plan.setdefault("notes", []).append({
                "text": str(note),
                "at": time.time()
            })
        plan["updated_at"] = time.time()
        plan["summary"] = self._plan_summary(plan)
        return self._ok(
            f"native plan updated session_id={session.get('session_id')} todos={len(plan.get('todos') or [])}",
            session_id=session.get("session_id"),
            plan=plan
        )

    def get_plan(self, session_id: str | None = None) -> dict[str, typing.Any]:
        session = self.sessions.get(str(session_id or "")) if session_id else self._latest_session()
        if not isinstance(session, dict):
            return self._fail("session_not_found", session_id=session_id)
        plan = session.setdefault("plan", self._empty_plan())
        plan["summary"] = self._plan_summary(plan)
        return self._ok(
            f"native plan returned session_id={session.get('session_id')}",
            session_id=session.get("session_id"),
            plan=plan
        )

    @staticmethod
    def _empty_plan() -> dict[str, typing.Any]:
        return {
            "todos"       : [],
            "assumptions" : [],
            "next_steps"  : [],
            "notes"       : [],
            "updated_at"  : None,
            "summary": {
                "total"       : 0,
                "pending"     : 0,
                "in_progress" : 0,
                "completed"   : 0,
                "blocked"     : 0
            }
        }

    @staticmethod
    def _merge_todos(
        current: list[dict[str, typing.Any]],
        incoming: list[dict[str, typing.Any]],
        *,
        replace: bool
    ) -> list[dict[str, typing.Any]]:
        items = [] if replace else [dict(item) for item in current if isinstance(item, dict)]
        by_id = {str(item.get("id") or item.get("title") or ""): item for item in items}
        for raw in incoming or []:
            if not isinstance(raw, dict):
                continue
            title = str(raw.get("title") or raw.get("task") or raw.get("text") or "").strip()
            if not title:
                continue
            todo_id = str(raw.get("id") or title).strip()
            status = str(raw.get("status") or "pending").strip().lower()
            if status not in {"pending", "in_progress", "completed", "blocked"}:
                status = "pending"

            item = by_id.get(todo_id) or {
                "id"         : todo_id,
                "title"      : title,
                "created_at" : time.time()
            }
            item.update({
                "id"         : todo_id,
                "title"      : title,
                "status"     : status,
                "updated_at" : time.time()
            })

            if raw.get("details"):
                item["details"] = str(raw.get("details"))
            if raw.get("path"):
                item["path"] = str(raw.get("path"))
            if todo_id not in by_id:
                items.append(item)
                by_id[todo_id] = item
        return items

    @staticmethod

    def _merge_strings(current: list[str], incoming: list[str]) -> list[str]:
        items = [str(item) for item in (current or []) if str(item).strip()]
        for raw in incoming or []:
            value = str(raw or "").strip()
            if value and value not in items:
                items.append(value)
        return items

    @staticmethod

    def _plan_summary(plan: dict[str, typing.Any]) -> dict[str, int]:
        todos = [item for item in (plan.get("todos") or []) if isinstance(item, dict)]
        return {
            "total"       : len(todos),
            "pending"     : len([item for item in todos if item.get("status") == "pending"]),
            "in_progress" : len([item for item in todos if item.get("status") == "in_progress"]),
            "completed"   : len([item for item in todos if item.get("status") == "completed"]),
            "blocked"     : len([item for item in todos if item.get("status") == "blocked"])
        }


if __name__ == '__main__':
    pass
