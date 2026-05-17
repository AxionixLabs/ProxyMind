# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import time
import uuid
import typing
from pathlib import Path
from loguru import logger
from backend.mcp_core.native_coding.base import NativeCodingComponent
from backend.utilities import const


class SessionTools(NativeCodingComponent):
    async def native_loop(
        self,
        *,
        prompt: str,
        steps: list[dict[str, typing.Any]] | None = None,
        verify_command: list[str] | None = None,
        stop_on_fail: bool = True,
        max_steps: int = 20,
        session_id: str | None = None,
        plan_update: dict[str, typing.Any] | None = None,
        auto_repair: bool | str = False
    ) -> dict[str, typing.Any]:
        started = time.perf_counter()
        session = self._begin_session(prompt=prompt, session_id=session_id)
        if isinstance(plan_update, dict) and plan_update:
            self.update_plan(session_id=session["session_id"], **plan_update)
        run = self._begin_run(session, prompt=prompt)
        session["repair_plan"] = None
        requested_steps = steps if isinstance(steps, list) else []
        step_limit = max(1, min(int(max_steps or 20), 50))
        limited_steps = requested_steps[:step_limit]
        executed: list[dict[str, typing.Any]] = []
        preflight = self.preflight_native_steps(limited_steps)
        run["preflight"] = preflight

        if not preflight.get("ok"):
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            summary = self._finish_session(
                session,
                ok=False,
                status="",
                diff="",
                elapsed_ms=elapsed_ms,
                run=run
            )
            payload = self._ok(
                f"native coding loop preflight failed steps=0 elapsed_ms={elapsed_ms}",
                prompt=prompt,
                ok=False,
                session_id=session["session_id"],
                run_id=run["run_id"],
                run_index=run["run_index"],
                steps=[],
                summary=summary,
                run=run,
                preflight=preflight,
                stopped_on_fail=True,
                status="",
                verify=None,
            verify_diagnostics=None,
            has_repair_plan=False,
            next_action="stop",
            repair_status="preflight_failed",
            last_verify_ok=None,
            diff="",
            elapsed_ms=elapsed_ms,
            truncated=len(requested_steps) > step_limit
            )
            payload["data"]["ok"] = False
            return payload

        snapshot = self._create_run_snapshot(preflight)
        run["snapshot"] = snapshot

        for index, step in enumerate(limited_steps, start=1):
            item = await self.run_native_step(step, index=index)
            item["run_id"] = run["run_id"]
            item["run_index"] = run["run_index"]
            self._record_step(session, item, run=run)
            executed.append(item)
            if stop_on_fail and not item.get("ok"):
                break

        status = await self.git_status()
        verify = None
        verify_diagnostics = None
        if verify_command:
            verify = await self.shell_exec(command=verify_command, cwd=".", timeout_sec=120)
            verify_data = verify.get("data") if isinstance(verify, dict) else {}
            verify_diagnostics = self._diagnose_verify_failure(verify_data)
            if not verify_diagnostics.get("ok"):
                diagnostic_context = self._collect_verify_diagnostic_context(verify_diagnostics)
                verify_diagnostics["context"] = diagnostic_context
                verify_diagnostics["auto_read_count"] = len([
                    item for item in diagnostic_context if item.get("ok")
                ])
                verify_diagnostics["repair_prompt"] = self._build_repair_prompt(
                    verify_data,
                    verify_diagnostics
                )
                repair_intent = self._normalize_auto_repair(auto_repair)
                if repair_intent == "plan":
                    repair_plan = self._build_repair_plan(
                        prompt=prompt,
                        verify_command=verify_command,
                        verify_data=verify_data,
                        diagnostics=verify_diagnostics,
                        session_id=session["session_id"],
                        run_id=run["run_id"]
                    )
                    verify_diagnostics["repair_plan"] = repair_plan
                    run["repair_plan"] = repair_plan
                    session["repair_plan"] = repair_plan
                session["diagnostic_context"] = diagnostic_context
                run["diagnostic_context"] = diagnostic_context
                for item in diagnostic_context:
                    if item.get("ok") and item.get("path"):
                        self._append_unique(session, "read_files", str(item.get("path")))
                        self._append_unique(session, "diagnostic_reads", str(item.get("path")))
                        self._append_unique(run, "diagnostic_reads", str(item.get("path")))
            if isinstance(verify_data, dict):
                verify_data["diagnostics"] = verify_diagnostics
            self._record_verify(session, verify, run=run)
        diff = await self.git_diff()
        failed = [item for item in executed if not item.get("ok")]
        verify_ok = True if verify is None else bool((verify.get("data") or {}).get("ok"))
        ok = (not failed) and verify_ok
        repair_state = self._repair_state(
            ok=ok,
            failed_steps=failed,
            verify=verify,
            repair_plan=(verify_diagnostics or {}).get("repair_plan") if isinstance(verify_diagnostics, dict) else None
        )
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        summary = self._finish_session(
            session,
            ok=ok,
            status=(status.get("data") or {}).get("stdout"),
            diff=(diff.get("data") or {}).get("stdout"),
            elapsed_ms=elapsed_ms,
            run=run
        )

        text = (
            f"native coding loop ok steps={len(executed)} elapsed_ms={elapsed_ms}"
            if ok else
            f"native coding loop failed steps={len(executed)} elapsed_ms={elapsed_ms}"
        )
        payload = self._ok(
            text,
            prompt=prompt,
            ok=ok,
            session_id=session["session_id"],
            run_id=run["run_id"],
            run_index=run["run_index"],
            steps=executed,
            summary=summary,
            run=run,
            preflight=preflight,
            stopped_on_fail=bool(failed and stop_on_fail),
            status=(status.get("data") or {}).get("stdout"),
            verify=(verify.get("data") if verify else None),
            verify_diagnostics=verify_diagnostics,
            repair_plan=(verify_diagnostics or {}).get("repair_plan") if isinstance(verify_diagnostics, dict) else None,
            **repair_state,
            diff=(diff.get("data") or {}).get("stdout"),
            elapsed_ms=elapsed_ms,
            truncated=len(requested_steps) > step_limit
        )
        payload["data"]["ok"] = ok
        return payload

    @staticmethod
    def _repair_state(
        *,
        ok: bool,
        failed_steps: list[dict[str, typing.Any]] | None = None,
        verify: dict[str, typing.Any] | None = None,
        repair_plan: dict[str, typing.Any] | None = None
    ) -> dict[str, typing.Any]:
        has_plan = isinstance(repair_plan, dict) and bool(repair_plan)
        last_verify_ok = bool((verify.get("data") or {}).get("ok")) if isinstance(verify, dict) else None
        if ok:
            status = "passed"
            next_action = "stop"
        elif has_plan:
            status = "needs_model"
            next_action = "generate_repair_steps"
        elif failed_steps:
            status = "step_failed"
            next_action = "inspect_failure"
        elif last_verify_ok is False:
            status = "verify_failed"
            next_action = "inspect_diagnostics"
        else:
            status = "failed"
            next_action = "inspect_failure"
        return {
            "has_repair_plan": has_plan,
            "next_action": next_action,
            "repair_status": status,
            "last_verify_ok": last_verify_ok
        }

    @staticmethod
    def _normalize_auto_repair(value: typing.Any) -> str:
        if value is True:
            return "plan"
        if value is False or value is None:
            return "off"
        text = str(value or "").strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return "plan"
        if text in {"plan", "off"}:
            return text
        return "off"

    def session_snapshot(self, session_id: str | None = None) -> dict[str, typing.Any]:
        if session_id:
            item = self.sessions.get(session_id)
            if not item:
                return self._fail("session_not_found", session_id=session_id)
            return self._ok(
                f"native coding session returned session_id={session_id}",
                session=self._public_session(item)
            )

        sessions = list(self.sessions.values())[-10:]
        return self._ok(
            f"native coding sessions count={len(self.sessions)}",
            sessions=[
                {
                    "session_id": item.get("session_id"),
                    "ok": item.get("ok"),
                    "prompt": item.get("prompt"),
                    "started_at": item.get("started_at"),
                    "finished_at": item.get("finished_at"),
                    "summary": item.get("summary")
                }
                for item in sessions
            ],
            count=len(self.sessions)
        )

    def _public_session(self, session: dict[str, typing.Any]) -> dict[str, typing.Any]:
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

    async def run_native_step(
        self,
        step: dict[str, typing.Any],
        *,
        index: int
    ) -> dict[str, typing.Any]:
        """执行一个白名单内的原生编码步骤。"""
        if not isinstance(step, dict):
            return {
                "index": index,
                "tool": None,
                "ok": False,
                "reason": "step_not_dict",
                "data": None
            }

        tool = str(step.get("tool") or "").strip()
        args = step.get("args") or {}
        if not isinstance(args, dict):
            args = {}

        if tool not in self.LOOP_TOOLS:
            return {
                "index": index,
                "tool": tool,
                "ok": False,
                "reason": "tool_not_allowed",
                "data": None
            }

        try:
            result = await self._dispatch_native_tool(tool, args)
        except Exception as exc:
            logger.exception(f"native loop step failed tool={tool} index={index}")
            return {
                "index": index,
                "tool": tool,
                "ok": False,
                "reason": f"{type(exc).__name__}: {exc}",
                "data": None
            }

        data = result.get("data") if isinstance(result, dict) else None
        ok = bool(data.get("ok")) if isinstance(data, dict) and "ok" in data else False
        return self._ok(
            "native step complete",
            index=index,
            tool=tool,
            ok=ok,
            result_text=result.get("text") if isinstance(result, dict) else None,
            data=data
        )["data"]

    def preflight_native_steps(
        self,
        steps: list[dict[str, typing.Any]]
    ) -> dict[str, typing.Any]:
        checks: list[dict[str, typing.Any]] = []
        virtual_files: dict[str, str | None] = {}
        for index, step in enumerate(steps or [], start=1):
            check = self._preflight_native_step(step, index=index, virtual_files=virtual_files)
            if check.get("ok"):
                self._update_preflight_virtual_files(check, virtual_files)
            checks.append(self._sanitize_preflight_check(check))
        failures = [item for item in checks if not item.get("ok")]
        return {
            "ok": not failures,
            "check_count": len(checks),
            "failure_count": len(failures),
            "checks": checks
        }

    def _preflight_native_step(
        self,
        step: dict[str, typing.Any],
        *,
        index: int,
        virtual_files: dict[str, str | None]
    ) -> dict[str, typing.Any]:
        if not isinstance(step, dict):
            return self._preflight_fail(index, None, "step_not_dict")
        tool = str(step.get("tool") or "").strip()
        args = step.get("args") or {}
        if not isinstance(args, dict):
            return self._preflight_fail(index, tool, "args_not_dict")
        if tool not in self.LOOP_TOOLS:
            return self._preflight_fail(index, tool, "tool_not_allowed")

        try:
            if tool == "workspace_root":
                return self._preflight_ok(index, tool)
            if tool == "workspace_list_files":
                self._preflight_resolve(args.get("path") or ".")
                return self._preflight_ok(index, tool)
            if tool == "workspace_read_file":
                target = self._preflight_resolve(args.get("path"))
                rel = self._rel(target)
                if rel in virtual_files:
                    if virtual_files[rel] is None:
                        return self._preflight_fail(index, tool, "file_not_found", path=rel)
                    return self._preflight_ok(index, tool, path=rel)
                if not target.is_file():
                    return self._preflight_fail(index, tool, "file_not_found", path=rel)
                return self._preflight_ok(index, tool, path=self._rel(target))
            if tool == "workspace_search_text":
                if not str(args.get("query") or ""):
                    return self._preflight_fail(index, tool, "query_empty")
                self._preflight_resolve(args.get("path") or ".")
                return self._preflight_ok(index, tool)
            if tool == "repo_map":
                self._preflight_resolve(args.get("path") or ".")
                return self._preflight_ok(index, tool)
            if tool == "repo_find_symbol":
                if not str(args.get("query") or ""):
                    return self._preflight_fail(index, tool, "query_empty")
                self._preflight_resolve(args.get("path") or ".")
                return self._preflight_ok(index, tool)
            if tool == "workspace_write_file":
                return self._preflight_write_file(index=index, tool=tool, args=args)
            if tool == "workspace_apply_patch":
                return self._preflight_apply_patch(index=index, tool=tool, args=args, virtual_files=virtual_files)
            if tool == "workspace_apply_unified_patch":
                return self._preflight_apply_unified_patch(index=index, tool=tool, args=args)
            if tool == "shell_exec":
                return self._preflight_shell_exec(index=index, tool=tool, args=args)
            if tool == "git_status":
                return self._preflight_ok(index, tool)
            if tool == "git_diff":
                if args.get("path"):
                    self._preflight_resolve(args.get("path"))
                return self._preflight_ok(index, tool)
            if tool == "change_summary":
                return self._preflight_ok(index, tool)
            if tool == "rollback_run":
                if not str(args.get("session_id") or ""):
                    return self._preflight_fail(index, tool, "session_id_required")
                return self._preflight_ok(index, tool)
            if tool == "native_plan":
                action = str(args.get("action") or "get").strip().lower()
                if action not in {"get", "update"}:
                    return self._preflight_fail(index, tool, "invalid_plan_action", action=action)
                return self._preflight_ok(index, tool, action=action)
            if tool == "record_sandbox_result":
                if not str(args.get("session_id") or ""):
                    return self._preflight_fail(index, tool, "session_id_required")
                if not isinstance(args.get("command"), list) or not args.get("command"):
                    return self._preflight_fail(index, tool, "command_required")
                return self._preflight_ok(
                    index,
                    tool,
                    session_id=args.get("session_id"),
                    command=args.get("command"),
                    verify=bool(args.get("verify", False))
                )
        except Exception as exc:
            return self._preflight_fail(index, tool, f"{type(exc).__name__}: {exc}")

        return self._preflight_fail(index, tool, "tool_not_allowed")

    def _preflight_write_file(
        self,
        *,
        index: int,
        tool: str,
        args: dict[str, typing.Any]
    ) -> dict[str, typing.Any]:
        target = self._preflight_resolve(args.get("path"))
        content = str(args.get("content") or "")
        size = len(content.encode(const.CHARSET, const.IGNORE))
        if size > self.max_write_bytes:
            return self._preflight_fail(index, tool, "content_too_large", size=size, max_bytes=self.max_write_bytes)
        if target.exists() and not bool(args.get("overwrite", True)):
            return self._preflight_fail(index, tool, "file_exists", path=self._rel(target))
        if not bool(args.get("create_dirs", True)) and not target.parent.exists():
            return self._preflight_fail(index, tool, "parent_directory_missing", path=self._rel(target.parent))
        if conflict := self._conflict_guard(
            target,
            expected_sha256=args.get("expected_sha256"),
            force=bool(args.get("force", False))
        ):
            return self._preflight_fail(index, tool, (conflict.get("data") or {}).get("reason") or "sha256_conflict")
        return self._preflight_ok(
            index,
            tool,
            path=self._rel(target),
            bytes=size,
            projected_content=content
        )

    def _preflight_apply_patch(
        self,
        *,
        index: int,
        tool: str,
        args: dict[str, typing.Any],
        virtual_files: dict[str, str | None]
    ) -> dict[str, typing.Any]:
        target = self._preflight_resolve(args.get("path"))
        rel = self._rel(target)
        if rel in virtual_files:
            if virtual_files[rel] is None:
                return self._preflight_fail(index, tool, "file_not_found", path=rel)
            current = str(virtual_files[rel] or "")
        else:
            if not target.is_file():
                return self._preflight_fail(index, tool, "file_not_found", path=rel)
            if conflict := self._conflict_guard(
                target,
                expected_sha256=args.get("expected_sha256"),
                force=bool(args.get("force", False))
            ):
                data = conflict.get("data") or {}
                return self._preflight_fail(index, tool, data.get("reason") or "sha256_conflict", path=rel)
            current = target.read_text(encoding=const.CHARSET, errors=const.IGNORE)
        old_text = str(args.get("old_text") or "")
        if not old_text:
            return self._preflight_fail(index, tool, "old_text_empty", path=rel)
        found = current.count(old_text)
        expected = max(1, int(args.get("expected_replacements") or 1))
        if found != expected:
            return self._preflight_fail(
                index,
                tool,
                "replacement_count_mismatch",
                path=rel,
                found=found,
                expected=expected
            )
        new_text = str(args.get("new_text") or "")
        projected = current.replace(old_text, new_text, expected)
        size = len(projected.encode(const.CHARSET, const.IGNORE))
        if size > self.max_write_bytes:
            return self._preflight_fail(index, tool, "content_too_large", path=rel, size=size)
        return self._preflight_ok(
            index,
            tool,
            path=rel,
            replacements=expected,
            projected_content=projected
        )

    def _preflight_apply_unified_patch(
        self,
        *,
        index: int,
        tool: str,
        args: dict[str, typing.Any]
    ) -> dict[str, typing.Any]:
        planned = self._plan_unified_patch(
            patch=str(args.get("patch") or ""),
            expected_sha256=args.get("expected_sha256"),
            force=bool(args.get("force", False))
        )
        if not planned.get("ok"):
            data = dict(planned.get("data") or {})
            data.pop("reason", None)
            return self._preflight_fail(index, tool, planned.get("reason") or "unified_patch_invalid", **data)
        items = planned.get("planned") or []
        return self._preflight_ok(
            index,
            tool,
            file_count=len(items),
            hunk_count=sum(int(item.get("hunks") or 0) for item in items),
            paths=[item.get("path") for item in items],
            files=[
                {
                    "path": item.get("path"),
                    "action": item.get("action"),
                    "sha256": item.get("sha256")
                }
                for item in items
            ]
        )

    def _preflight_shell_exec(
        self,
        *,
        index: int,
        tool: str,
        args: dict[str, typing.Any]
    ) -> dict[str, typing.Any]:
        cmd = [str(item) for item in (args.get("command") or []) if str(item or "").strip()]
        if not cmd:
            return self._preflight_fail(index, tool, "command_empty")
        policy = self.check_command_policy(
            cmd,
            cwd=str(args.get("cwd") or "."),
            timeout_sec=int(args.get("timeout_sec") or 60),
            allow_review=bool(args.get("allow_review", False)),
            allow_dangerous=bool(args.get("allow_dangerous", False))
        )
        if not policy.get("ok"):
            return self._preflight_fail(
                index,
                tool,
                policy.get("reason") or "command_not_allowed",
                command=cmd,
                risk=policy.get("risk"),
                category=policy.get("category"),
                reasons=policy.get("reasons") or [],
                approval_required=bool(policy.get("approval_required")),
                project_types=policy.get("project_types") or [],
                execution_target=policy.get("execution_target"),
                requires_cloud_sandbox=bool(policy.get("requires_cloud_sandbox")),
                sandbox_request=policy.get("sandbox_request")
            )
        cwd = self._preflight_resolve(args.get("cwd") or ".")
        if not cwd.is_dir():
            return self._preflight_fail(index, tool, "cwd_not_directory", cwd=self._rel(cwd))
        return self._preflight_ok(
            index,
            tool,
            command=cmd,
            cwd=self._rel(cwd),
            risk=policy.get("risk"),
            category=policy.get("category"),
            reasons=policy.get("reasons") or [],
            execution_target=policy.get("execution_target"),
            requires_cloud_sandbox=bool(policy.get("requires_cloud_sandbox")),
            sandbox_request=policy.get("sandbox_request"),
            project_types=policy.get("project_types") or [],
            long_task=bool(policy.get("long_task")),
            timeout_sec=policy.get("timeout_sec"),
            output_limit=policy.get("output_limit")
        )

    def _preflight_resolve(self, path: typing.Any) -> Path:
        return self._resolve(str(path or "."))

    def _preflight_resolve_required_file(self, path: typing.Any) -> Path:
        if not str(path or "").strip():
            raise ValueError("path_required")
        target = self._resolve(str(path))
        if not target.is_file():
            raise FileNotFoundError(str(path))
        return target

    @staticmethod

    def _update_preflight_virtual_files(
        check: dict[str, typing.Any],
        virtual_files: dict[str, str | None]
    ) -> None:
        tool = str(check.get("tool") or "")
        path = check.get("path")
        if not path:
            return
        if tool in {"workspace_write_file", "workspace_apply_patch"}:
            virtual_files[str(path)] = str(check.get("projected_content") or "")
        elif tool == "workspace_apply_unified_patch":
            for item in check.get("files") or []:
                if not isinstance(item, dict) or not item.get("path"):
                    continue
                if item.get("action") == "delete":
                    virtual_files[str(item["path"])] = None

    @staticmethod

    def _sanitize_preflight_check(check: dict[str, typing.Any]) -> dict[str, typing.Any]:
        sanitized = dict(check)
        sanitized.pop("projected_content", None)
        return sanitized

    @staticmethod

    def _preflight_ok(index: int, tool: str, **data: typing.Any) -> dict[str, typing.Any]:
        return {"index": index, "tool": tool, "ok": True, **data}

    @staticmethod

    def _preflight_fail(index: int, tool: str | None, reason: str, **data: typing.Any) -> dict[str, typing.Any]:
        return {"index": index, "tool": tool, "ok": False, "reason": reason, **data}

    async def _dispatch_native_tool(
        self,
        tool: str,
        args: dict[str, typing.Any]
    ) -> dict[str, typing.Any]:
        if tool == "workspace_root":
            return self.workspace_root()
        if tool == "workspace_list_files":
            return self.list_files(**args)
        if tool == "workspace_read_file":
            return self.read_file(**args)
        if tool == "workspace_search_text":
            return self.search_text(**args)
        if tool == "repo_map":
            return self.repo_map(**args)
        if tool == "repo_find_symbol":
            return self.find_symbol(**args)
        if tool == "workspace_write_file":
            return self.write_file(**args)
        if tool == "workspace_apply_patch":
            return self.apply_patch(**args)
        if tool == "workspace_apply_unified_patch":
            return self.apply_unified_patch(**args)
        if tool == "shell_exec":
            return await self.shell_exec(**args)
        if tool == "git_status":
            return await self.git_status()
        if tool == "git_diff":
            return await self.git_diff(**args)
        if tool == "change_summary":
            return await self.change_summary(**args)
        if tool == "rollback_run":
            return self.rollback_run(**args)
        if tool == "native_plan":
            action = str(args.get("action") or "get").strip().lower()
            if action == "update":
                return self.update_plan(
                    session_id=args.get("session_id"),
                    todos=args.get("todos"),
                    assumptions=args.get("assumptions"),
                    next_steps=args.get("next_steps"),
                    note=args.get("note"),
                    mode=args.get("mode") or "merge"
                )
            return self.get_plan(session_id=args.get("session_id"))
        if tool == "record_sandbox_result":
            return self.record_sandbox_result(**args, record_step=False)
        return self._fail("tool_not_allowed", tool=tool)

    def _begin_session(
        self,
        *,
        prompt: str,
        session_id: str | None
    ) -> dict[str, typing.Any]:
        sid = str(session_id or "").strip() or f"native_{uuid.uuid4().hex[:10]}"
        session = self.sessions.get(sid)
        if not isinstance(session, dict):
            session = {
                "session_id": sid,
                "prompt": str(prompt or ""),
                "started_at": time.time(),
                "finished_at": None,
                "ok": None,
                "steps": [],
                "read_files": [],
                "written_files": [],
                "patched_files": [],
                "searched": [],
                "shell_commands": [],
                "shell_file_changes": [],
                "verify": None,
                "verify_diagnostics": None,
                "repair_plan": None,
                "sandbox_results": [],
                "diagnostic_context": [],
                "diagnostic_reads": [],
                "runs": [],
                "run_count": 0,
                "last_run_id": None,
                "plan": self._empty_plan(),
                "status": "",
                "diff": "",
                "summary": {}
            }
            self.sessions[sid] = session
        else:
            session["prompt"] = str(prompt or session.get("prompt") or "")
            session.setdefault("steps", [])
            session.setdefault("read_files", [])
            session.setdefault("written_files", [])
            session.setdefault("patched_files", [])
            session.setdefault("searched", [])
            session.setdefault("shell_commands", [])
            session.setdefault("shell_file_changes", [])
            session.setdefault("repair_plan", None)
            session.setdefault("sandbox_results", [])
            session.setdefault("diagnostic_context", [])
            session.setdefault("diagnostic_reads", [])
            session.setdefault("runs", [])
            session.setdefault("run_count", len(session.get("runs") or []))
            session.setdefault("last_run_id", None)
            session.setdefault("plan", self._empty_plan())
        return session

    @staticmethod
    def _begin_run(
        session: dict[str, typing.Any],
        *,
        prompt: str
    ) -> dict[str, typing.Any]:
        runs = session.setdefault("runs", [])
        run_index = int(session.get("run_count") or len(runs)) + 1
        run_id = f"{session['session_id']}_run_{run_index}"
        run = {
            "run_id": run_id,
            "run_index": run_index,
            "kind": "initial" if run_index == 1 else "repair",
            "prompt": str(prompt or ""),
            "started_at": time.time(),
            "finished_at": None,
            "ok": None,
            "steps": [],
            "shell_commands": [],
            "shell_file_changes": [],
            "verify": None,
            "verify_diagnostics": None,
            "repair_plan": None,
            "sandbox_results": [],
            "diagnostic_context": [],
            "diagnostic_reads": [],
            "status": "",
            "diff": "",
            "elapsed_ms": None,
            "summary": {}
        }
        runs.append(run)
        session["run_count"] = run_index
        session["last_run_id"] = run_id
        return run

    def _record_step(
        self,
        session: dict[str, typing.Any],
        step: dict[str, typing.Any],
        *,
        run: dict[str, typing.Any] | None = None
    ) -> None:
        tool = str(step.get("tool") or "")
        data = step.get("data") if isinstance(step.get("data"), dict) else {}
        record = {
            "run_id": step.get("run_id"),
            "run_index": step.get("run_index"),
            "index": step.get("index"),
            "tool": tool,
            "ok": bool(step.get("ok")),
            "reason": step.get("reason")
        }
        session.setdefault("steps", []).append(record)
        if isinstance(run, dict):
            run.setdefault("steps", []).append(record)

        if tool == "workspace_read_file" and data.get("path"):
            self._append_unique(session, "read_files", str(data.get("path")))
        elif tool == "workspace_write_file" and data.get("path"):
            self._append_unique(session, "written_files", str(data.get("path")))
        elif tool == "workspace_apply_patch" and data.get("path"):
            self._append_unique(session, "patched_files", str(data.get("path")))
            self._append_unique(session, "written_files", str(data.get("path")))
        elif tool == "workspace_apply_unified_patch":
            for item in data.get("files") or []:
                if isinstance(item, dict) and item.get("path"):
                    self._append_unique(session, "patched_files", str(item.get("path")))
                    self._append_unique(session, "written_files", str(item.get("path")))
        elif tool == "workspace_search_text":
            query = data.get("query")
            if query:
                self._append_unique(session, "searched", str(query))
        elif tool in {"shell_exec", "record_sandbox_result"}:
            command = data.get("command")
            if isinstance(command, list):
                command_record = {
                    "command": command,
                    "ok": bool(data.get("ok")),
                    "exit_code": data.get("exit_code"),
                    "risk": data.get("risk"),
                    "execution_target": data.get("execution_target"),
                    "requires_cloud_sandbox": bool(data.get("requires_cloud_sandbox")),
                    "sandbox_provider": data.get("sandbox_provider"),
                    "shell_write_detected": bool(data.get("shell_write_detected")),
                    "file_change_count": (data.get("shell_file_changes") or {}).get("change_count", 0)
                }
                session.setdefault("shell_commands", []).append(command_record)
                if isinstance(run, dict):
                    run.setdefault("shell_commands", []).append(command_record)
                file_changes = data.get("shell_file_changes")
                if isinstance(file_changes, dict) and file_changes.get("changed"):
                    change_record = {
                        "command": command,
                        "run_id": step.get("run_id"),
                        "run_index": step.get("run_index"),
                        "step_index": step.get("index"),
                        "change_count": file_changes.get("change_count", 0),
                        "created": list(file_changes.get("created") or []),
                        "modified": list(file_changes.get("modified") or []),
                        "deleted": list(file_changes.get("deleted") or []),
                        "truncated": bool(file_changes.get("truncated"))
                    }
                    session.setdefault("shell_file_changes", []).append(change_record)
                    if isinstance(run, dict):
                        run.setdefault("shell_file_changes", []).append(change_record)

    def _record_verify(
        self,
        session: dict[str, typing.Any],
        verify: dict[str, typing.Any],
        *,
        run: dict[str, typing.Any] | None = None
    ) -> None:
        data = verify.get("data") if isinstance(verify, dict) else {}
        verify_record = {
            "ok": bool(data.get("ok")),
            "command": data.get("command"),
            "exit_code": data.get("exit_code"),
            "stdout": self._clip_output(str(data.get("stdout") or ""), max_chars=2000),
            "stderr": self._clip_output(str(data.get("stderr") or ""), max_chars=2000),
            "diagnostics": data.get("diagnostics")
        }
        file_changes = data.get("shell_file_changes")
        if isinstance(file_changes, dict):
            verify_record["shell_write_detected"] = bool(data.get("shell_write_detected"))
            verify_record["shell_file_changes"] = file_changes
        session["verify"] = verify_record
        session["verify_diagnostics"] = data.get("diagnostics")
        session["repair_plan"] = (data.get("diagnostics") or {}).get("repair_plan") if isinstance(data.get("diagnostics"), dict) else None
        if isinstance(run, dict):
            run["verify"] = verify_record
            run["verify_diagnostics"] = data.get("diagnostics")
            run["repair_plan"] = (data.get("diagnostics") or {}).get("repair_plan") if isinstance(data.get("diagnostics"), dict) else None

    def _finish_session(
        self,
        session: dict[str, typing.Any],
        *,
        ok: bool,
        status: str | None,
        diff: str | None,
        elapsed_ms: int,
        run: dict[str, typing.Any] | None = None
    ) -> dict[str, typing.Any]:
        session["ok"] = ok
        session["finished_at"] = time.time()
        session["status"] = status or ""
        session["diff"] = self._clip_output(diff or "", max_chars=8000)
        run_summary = None
        if isinstance(run, dict):
            run["ok"] = ok
            run["finished_at"] = session["finished_at"]
            run["status"] = session["status"]
            run["diff"] = session["diff"]
            run["elapsed_ms"] = elapsed_ms
            run_summary = {
                "run_id": run.get("run_id"),
                "run_index": run.get("run_index"),
                "kind": run.get("kind"),
                "ok": ok,
                "elapsed_ms": elapsed_ms,
                "step_count": len(run.get("steps") or []),
                "preflight": run.get("preflight"),
                "snapshot": self._public_snapshot(run.get("snapshot") or {}),
                "verify": run.get("verify"),
                "repair_plan": run.get("repair_plan"),
                **self._repair_state(
                    ok=ok,
                    failed_steps=[item for item in (run.get("steps") or []) if not item.get("ok")],
                    verify=run.get("verify"),
                    repair_plan=run.get("repair_plan")
                ),
                "shell_commands": list(run.get("shell_commands") or []),
                "shell_file_changes": list(run.get("shell_file_changes") or []),
                "sandbox_results": list(run.get("sandbox_results") or []),
                "diagnostic_reads": list(run.get("diagnostic_reads") or []),
                "status": run["status"],
                "diff": run["diff"]
            }
            run["summary"] = run_summary
        steps = session.get("steps") if isinstance(session.get("steps"), list) else []
        failed_steps = [item for item in steps if not item.get("ok")]
        repair_state = self._repair_state(
            ok=ok,
            failed_steps=failed_steps,
            verify=session.get("verify"),
            repair_plan=session.get("repair_plan")
        )
        summary = {
            "ok": ok,
            "elapsed_ms": elapsed_ms,
            "run_id": run.get("run_id") if isinstance(run, dict) else None,
            "run_index": run.get("run_index") if isinstance(run, dict) else None,
            "run_count": session.get("run_count"),
            "last_run_id": session.get("last_run_id"),
            "step_count": len(steps),
            "failed_count": len(failed_steps),
            "read_files": list(session.get("read_files") or []),
            "written_files": list(session.get("written_files") or []),
            "patched_files": list(session.get("patched_files") or []),
            "searched": list(session.get("searched") or []),
            "shell_commands": list(session.get("shell_commands") or []),
            "shell_file_changes": list(session.get("shell_file_changes") or []),
            "sandbox_results": list(session.get("sandbox_results") or []),
            "verify": session.get("verify"),
            "verify_diagnostics": session.get("verify_diagnostics"),
            "repair_plan": session.get("repair_plan"),
            **repair_state,
            "diagnostic_context": list(session.get("diagnostic_context") or []),
            "diagnostic_reads": list(session.get("diagnostic_reads") or []),
            "plan": session.get("plan"),
            "current_run": run_summary,
            "preflight": run.get("preflight") if isinstance(run, dict) else None,
            "runs": [
                {
                    "run_id": item.get("run_id"),
                    "run_index": item.get("run_index"),
                    "kind": item.get("kind"),
                    "ok": item.get("ok"),
                    "step_count": len(item.get("steps") or []),
                    "preflight_ok": bool((item.get("preflight") or {}).get("ok")) if item.get("preflight") else None,
                    "snapshot_file_count": (item.get("snapshot") or {}).get("file_count") if item.get("snapshot") else 0,
                    "rolled_back": bool(item.get("rolled_back")),
                    "verify_ok": bool((item.get("verify") or {}).get("ok")) if item.get("verify") else None,
                    "repair_status": self._repair_state(
                        ok=bool(item.get("ok")),
                        failed_steps=[step for step in (item.get("steps") or []) if not step.get("ok")],
                        verify=item.get("verify"),
                        repair_plan=item.get("repair_plan")
                    ).get("repair_status")
                }
                for item in (session.get("runs") or [])
                if isinstance(item, dict)
            ],
            "status": session["status"],
            "diff": session["diff"]
        }
        session["summary"] = summary
        return summary

    @staticmethod

    def _append_unique(
        session: dict[str, typing.Any],
        key: str,
        value: str
    ) -> None:
        items = session.setdefault(key, [])
        if value not in items:
            items.append(value)


if __name__ == '__main__':
    pass
