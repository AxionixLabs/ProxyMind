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

    async def native_repair_loop(
        self,
        *,
        session_id: str,
        steps: list[dict[str, typing.Any]],
        source_run_id: str | None = None,
        stop_on_fail: bool = True,
        max_steps: int = 20,
        auto_repair: bool | str = False,
        auto_rollback: bool | str = "verify_failed"
    ) -> dict[str, typing.Any]:
        sid = str(session_id or "").strip()
        session = self.sessions.get(sid)

        if not isinstance(session, dict):
            return self._fail("session_not_found", session_id=session_id)
        if source_run_id and not self._session_has_run(session, source_run_id):
            return self._fail("source_run_not_found", session_id=sid, source_run_id=source_run_id)

        validation = self.validate_repair_steps(steps)
        if not bool(validation.get("ok")):
            return self._fail(
                "repair_steps_invalid",
                session_id=sid,
                source_run_id=source_run_id,
                validation=validation
            )

        executable_steps, verify_step = self._prepare_repair_steps(steps)

        verify_args    = verify_step.get("args") if isinstance(verify_step.get("args"), dict) else {}
        verify_command = verify_args.get("command") if isinstance(verify_args.get("command"), list) else None

        if not verify_command:
            return self._fail(
                "repair_verify_command_required",
                session_id=sid,
                source_run_id=source_run_id,
                validation=validation
            )

        prompt = f"repair native coding session {sid}"
        if source_run_id:
            prompt = f"{prompt} source_run={source_run_id}"
        result = await self.native_loop(
            prompt=prompt,
            steps=executable_steps,
            verify_command=[str(item) for item in verify_command],
            stop_on_fail=stop_on_fail,
            max_steps=max_steps,
            session_id=sid,
            plan_update={
                "note": f"native repair loop source_run={source_run_id or 'latest'}",
                "next_steps": ["inspect repair verify result"]
            },
            auto_repair=auto_repair,
            auto_rollback=auto_rollback
        )
        data = result.get("data") if isinstance(result, dict) else {}
        if isinstance(data, dict):
            data["repair_loop"] = True
            data["repair_validation"] = validation
            data["repair_source_run_id"] = source_run_id
            data["repair_verify_step"] = verify_step
            data["repair_executable_step_count"] = len(executable_steps)
            data["repair_rollback_policy"] = self._normalize_auto_rollback(auto_rollback)
            metadata = self._record_repair_loop_metadata(
                session=session,
                run_id=str(data.get("run_id") or ""),
                source_run_id=source_run_id,
                validation=validation,
                verify_step=verify_step,
                executable_step_count=len(executable_steps),
                rollback_policy=self._normalize_auto_rollback(auto_rollback)
            )
            summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
            if isinstance(summary, dict):
                summary["repair_loops"] = list(session.get("repair_loops") or [])
                current_run = summary.get("current_run") if isinstance(summary.get("current_run"), dict) else {}
                if isinstance(current_run, dict):
                    current_run["repair_loop"] = metadata
            run_payload = data.get("run") if isinstance(data.get("run"), dict) else {}
            if isinstance(run_payload, dict):
                run_payload["repair_loop"] = metadata
        return result

    @staticmethod
    def _session_has_run(session: dict[str, typing.Any], run_id: str) -> bool:
        return any(
            isinstance(item, dict) and item.get("run_id") == run_id
            for item in session.get("runs") or []
        )

    @staticmethod
    def _record_repair_loop_metadata(
        *,
        session: dict[str, typing.Any],
        run_id: str,
        source_run_id: str | None,
        validation: dict[str, typing.Any],
        verify_step: dict[str, typing.Any],
        executable_step_count: int,
        rollback_policy: str
    ) -> dict[str, typing.Any]:
        metadata = {
            "run_id"                : run_id,
            "source_run_id"         : source_run_id,
            "validation"            : validation,
            "verify_step"           : verify_step,
            "executable_step_count" : executable_step_count,
            "rollback_policy"       : rollback_policy
        }
        session.setdefault("repair_loops", []).append(metadata)
        for item in session.get("runs") or []:
            if isinstance(item, dict) and item.get("run_id") == run_id:
                item["repair_loop"] = metadata
                summary = item.get("summary")
                if isinstance(summary, dict):
                    summary["repair_loop"] = metadata
                break
        return metadata

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
        auto_repair: bool | str = False,
        auto_rollback: bool | str = False
    ) -> dict[str, typing.Any]:
        started = time.perf_counter()
        session = self._begin_session(prompt=prompt, session_id=session_id)
        if isinstance(plan_update, dict) and plan_update:
            self.update_plan(session_id=session["session_id"], **plan_update)

        run = self._begin_run(session, prompt=prompt)
        session["repair_plan"] = None

        requested_steps = steps if isinstance(steps, list) else []
        step_limit      = max(1, min(int(max_steps or 20), 50))
        limited_steps   = requested_steps[:step_limit]

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

        diff      = await self.git_diff()
        failed    = [item for item in executed if not item.get("ok")]
        verify_ok = True if verify is None else bool((verify.get("data") or {}).get("ok"))
        ok        = (not failed) and verify_ok

        rollback = None
        rollback_policy = self._normalize_auto_rollback(auto_rollback)
        rollback_reason = self._auto_rollback_reason(
            policy=rollback_policy,
            failed_steps=failed,
            verify_ok=verify_ok,
            verify=verify
        )
        if rollback_reason:
            rollback = self.rollback_run(session_id=session["session_id"], run_id=run["run_id"])
            run["auto_rollback"] = {
                "enabled" : True,
                "policy"  : rollback_policy,
                "reason"  : rollback_reason,
                "ok"      : bool((rollback.get("data") or {}).get("ok")) if isinstance(rollback, dict) else False,
                "result"  : rollback.get("data") if isinstance(rollback, dict) else None
            }
            status = await self.git_status()
            diff = await self.git_diff()
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
            auto_rollback=bool(rollback_reason),
            rollback=rollback.get("data") if isinstance(rollback, dict) else None,
            rollback_policy=rollback_policy,
            rollback_reason=rollback_reason,
            **repair_state,
            diff=(diff.get("data") or {}).get("stdout"),
            elapsed_ms=elapsed_ms,
            truncated=len(requested_steps) > step_limit
        )
        payload["data"]["ok"] = ok
        return payload

    @staticmethod
    def _prepare_repair_steps(
        steps: list[dict[str, typing.Any]]
    ) -> tuple[list[dict[str, typing.Any]], dict[str, typing.Any]]:
        shell_indexes = [
            index
            for index, step in enumerate(steps or [])
            if isinstance(step, dict) and str(step.get("tool") or "") == "shell_exec"
        ]
        verify_index = shell_indexes[-1] if shell_indexes else -1
        verify_step = dict(steps[verify_index]) if verify_index >= 0 else {}
        executable_steps = [
            step
            for index, step in enumerate(steps or [])
            if index != verify_index and isinstance(step, dict)
        ]
        return executable_steps, verify_step

    @staticmethod
    def _normalize_auto_rollback(value: typing.Any) -> str:
        if value is True:
            return "always"
        if value is False or value is None:
            return "off"
        text = str(value or "").strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return "always"
        if text in {"always", "step_failed", "verify_failed", "off"}:
            return text
        return "off"

    @staticmethod
    def _auto_rollback_reason(
        *,
        policy: str,
        failed_steps: list[dict[str, typing.Any]],
        verify_ok: bool,
        verify: dict[str, typing.Any] | None
    ) -> str | None:
        if policy == "off":
            return None
        has_failed_steps = bool(failed_steps)
        has_verify_failure = verify is not None and not verify_ok
        if policy == "always" and (has_failed_steps or has_verify_failure):
            return "step_failed" if has_failed_steps else "verify_failed"
        if policy == "step_failed" and has_failed_steps:
            return "step_failed"
        if policy == "verify_failed" and has_verify_failure:
            return "verify_failed"
        return None

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
        elif any(SessionTools._step_requires_cloud_sandbox(item) for item in failed_steps or []):
            status = "awaiting_sandbox"
            next_action = "record_sandbox_result"
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
            "has_repair_plan" : has_plan,
            "next_action"     : next_action,
            "repair_status"   : status,
            "last_verify_ok"  : last_verify_ok
        }

    @staticmethod
    def _step_requires_cloud_sandbox(step: dict[str, typing.Any]) -> bool:
        data = step.get("data") if isinstance(step.get("data"), dict) else {}
        if step.get("tool") == "record_sandbox_result":
            return False
        if bool(data.get("requires_cloud_sandbox")):
            return True
        if data.get("sandbox_request"):
            return True
        if bool(step.get("requires_cloud_sandbox")):
            return True
        return bool(step.get("sandbox_request"))

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
                "index"  : index,
                "tool"   : None,
                "ok"     : False,
                "reason" : "step_not_dict",
                "data"   : None
            }

        tool = str(step.get("tool") or "").strip()
        args = step.get("args") or {}
        if not isinstance(args, dict):
            args = {}

        if tool not in self.LOOP_TOOLS:
            return {
                "index"  : index,
                "tool"   : tool,
                "ok"     : False,
                "reason" : "tool_not_allowed",
                "data"   : None
            }

        try:
            result = await self._dispatch_native_tool(tool, args)
        except Exception as exc:
            logger.exception(f"native loop step failed tool={tool} index={index}")
            return {
                "index"  : index,
                "tool"   : tool,
                "ok"     : False,
                "reason" : f"{type(exc).__name__}: {exc}",
                "data"   : None
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
            "ok"            : not failures,
            "check_count"   : len(checks),
            "failure_count" : len(failures),
            "checks"        : checks
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
            if tool == "workspace_copy_file":
                return self._preflight_copy_file(index=index, tool=tool, args=args, virtual_files=virtual_files)
            if tool == "workspace_move_file":
                return self._preflight_move_file(index=index, tool=tool, args=args, virtual_files=virtual_files)
            if tool == "workspace_delete_file":
                return self._preflight_delete_file(index=index, tool=tool, args=args, virtual_files=virtual_files)
            if tool == "workspace_apply_patch":
                return self._preflight_apply_patch(index=index, tool=tool, args=args, virtual_files=virtual_files)
            if tool == "workspace_apply_unified_patch":
                return self._preflight_apply_unified_patch(
                    index=index,
                    tool=tool,
                    args=args,
                    virtual_files=virtual_files
                )
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

    def _preflight_move_file(
        self,
        *,
        index: int,
        tool: str,
        args: dict[str, typing.Any],
        virtual_files: dict[str, str | None]
    ) -> dict[str, typing.Any]:
        source = self._preflight_resolve(args.get("source_path"))
        target = self._preflight_resolve(args.get("target_path"))
        source_rel = self._rel(source)
        target_rel = self._rel(target)
        overwrite = bool(args.get("overwrite", False))

        if source_rel in virtual_files:
            projected = virtual_files[source_rel]
            if projected is None:
                return self._preflight_fail(index, tool, "source_file_not_found", source_path=source_rel)
            payload = str(projected or "")
            sha = self._sha256(payload.encode(const.CHARSET, const.IGNORE))
            if args.get("expected_sha256") and not bool(args.get("force", False)) and args.get("expected_sha256") != sha:
                return self._preflight_fail(
                    index,
                    tool,
                    "file_changed_since_read",
                    source_path=source_rel,
                    expected_sha256=args.get("expected_sha256"),
                    actual_sha256=sha
                )
        else:
            if not source.is_file():
                return self._preflight_fail(index, tool, "source_file_not_found", source_path=source_rel)
            if conflict := self._conflict_guard(
                source,
                expected_sha256=args.get("expected_sha256"),
                force=bool(args.get("force", False))
            ):
                data = conflict.get("data") or {}
                return self._preflight_fail(index, tool, data.get("reason") or "sha256_conflict", path=source_rel)
            payload = source.read_text(encoding=const.CHARSET, errors=const.IGNORE)

        if target.exists() and target.is_dir():
            return self._preflight_fail(index, tool, "target_is_directory", target_path=target_rel)
        virtual_target_exists = target_rel in virtual_files and virtual_files[target_rel] is not None
        real_target_exists = target.exists() and target_rel not in virtual_files
        if (virtual_target_exists or real_target_exists) and not overwrite:
            return self._preflight_fail(index, tool, "target_exists", target_path=target_rel)
        if not bool(args.get("create_dirs", True)) and not target.parent.exists():
            return self._preflight_fail(index, tool, "target_parent_not_found", target_path=self._rel(target.parent))

        return self._preflight_ok(
            index,
            tool,
            source_path=source_rel,
            target_path=target_rel,
            bytes=len(payload.encode(const.CHARSET, const.IGNORE)),
            overwritten=bool(virtual_target_exists or real_target_exists),
            projected_source_content=None,
            projected_target_content=payload
        )

    def _preflight_copy_file(
        self,
        *,
        index: int,
        tool: str,
        args: dict[str, typing.Any],
        virtual_files: dict[str, str | None]
    ) -> dict[str, typing.Any]:
        source = self._preflight_resolve(args.get("source_path"))
        target = self._preflight_resolve(args.get("target_path"))
        source_rel = self._rel(source)
        target_rel = self._rel(target)
        overwrite = bool(args.get("overwrite", False))

        if source_rel in virtual_files:
            projected = virtual_files[source_rel]
            if projected is None:
                return self._preflight_fail(index, tool, "source_file_not_found", source_path=source_rel)
            payload = str(projected or "")
            sha = self._sha256(payload.encode(const.CHARSET, const.IGNORE))
            if args.get("expected_sha256") and not bool(args.get("force", False)) and args.get("expected_sha256") != sha:
                return self._preflight_fail(
                    index,
                    tool,
                    "file_changed_since_read",
                    source_path=source_rel,
                    expected_sha256=args.get("expected_sha256"),
                    actual_sha256=sha
                )
        else:
            if not source.is_file():
                return self._preflight_fail(index, tool, "source_file_not_found", source_path=source_rel)
            if conflict := self._conflict_guard(
                source,
                expected_sha256=args.get("expected_sha256"),
                force=bool(args.get("force", False))
            ):
                data = conflict.get("data") or {}
                return self._preflight_fail(index, tool, data.get("reason") or "sha256_conflict", path=source_rel)
            payload = source.read_text(encoding=const.CHARSET, errors=const.IGNORE)

        if target.exists() and target.is_dir():
            return self._preflight_fail(index, tool, "target_is_directory", target_path=target_rel)

        virtual_target_exists = target_rel in virtual_files and virtual_files[target_rel] is not None
        real_target_exists = target.exists() and target_rel not in virtual_files

        if (virtual_target_exists or real_target_exists) and not overwrite:
            return self._preflight_fail(index, tool, "target_exists", target_path=target_rel)
        if not bool(args.get("create_dirs", True)) and not target.parent.exists():
            return self._preflight_fail(index, tool, "target_parent_not_found", target_path=self._rel(target.parent))

        return self._preflight_ok(
            index,
            tool,
            source_path=source_rel,
            target_path=target_rel,
            bytes=len(payload.encode(const.CHARSET, const.IGNORE)),
            overwritten=bool(virtual_target_exists or real_target_exists),
            projected_target_content=payload
        )

    def _preflight_delete_file(
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
            projected = virtual_files[rel]
            if projected is None:
                return self._preflight_fail(index, tool, "file_not_found", path=rel)
            payload = str(projected or "")
            sha = self._sha256(payload.encode(const.CHARSET, const.IGNORE))
            if args.get("expected_sha256") and not bool(args.get("force", False)) and args.get("expected_sha256") != sha:
                return self._preflight_fail(
                    index,
                    tool,
                    "file_changed_since_read",
                    path=rel,
                    expected_sha256=args.get("expected_sha256"),
                    actual_sha256=sha
                )
        else:
            if not target.exists():
                return self._preflight_fail(index, tool, "file_not_found", path=rel)
            if not target.is_file():
                return self._preflight_fail(index, tool, "target_not_file", path=rel)
            if conflict := self._conflict_guard(
                target,
                expected_sha256=args.get("expected_sha256"),
                force=bool(args.get("force", False))
            ):
                data = conflict.get("data") or {}
                return self._preflight_fail(index, tool, data.get("reason") or "sha256_conflict", path=rel)
            payload = target.read_text(encoding=const.CHARSET, errors=const.IGNORE)

        return self._preflight_ok(
            index,
            tool,
            path=rel,
            bytes=len(payload.encode(const.CHARSET, const.IGNORE)),
            projected_content=None
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
        args: dict[str, typing.Any],
        virtual_files: dict[str, str | None]
    ) -> dict[str, typing.Any]:
        planned = self._plan_unified_patch(
            patch=str(args.get("patch") or ""),
            expected_sha256=args.get("expected_sha256"),
            force=bool(args.get("force", False)),
            virtual_files=virtual_files
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
                    "path"   : item.get("path"),
                    "action" : item.get("action"),
                    "sha256" : item.get("sha256")
                }
                for item in items
            ],
            projected_files=[
                {
                    "path"    : item.get("path"),
                    "action"  : item.get("action"),
                    "content" : item.get("content")
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
                suggested_tool=policy.get("suggested_tool"),
                suggested_args=policy.get("suggested_args") or {},
                approval_required=bool(policy.get("approval_required")),
                project_types=policy.get("project_types") or [],
                execution_target=policy.get("execution_target"),
                requires_cloud_sandbox=bool(policy.get("requires_cloud_sandbox")),
                sandbox_request=policy.get("sandbox_request"),
                outside_sandbox_request=policy.get("outside_sandbox_request")
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
            outside_sandbox_request=policy.get("outside_sandbox_request"),
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
        if tool in {"workspace_write_file", "workspace_apply_patch"}:
            if not path:
                return
            virtual_files[str(path)] = str(check.get("projected_content") or "")
        elif tool == "workspace_copy_file":
            target_path = check.get("target_path")
            if target_path:
                virtual_files[str(target_path)] = str(check.get("projected_target_content") or "")
        elif tool == "workspace_move_file":
            source_path = check.get("source_path")
            target_path = check.get("target_path")
            if source_path:
                virtual_files[str(source_path)] = None
            if target_path:
                virtual_files[str(target_path)] = str(check.get("projected_target_content") or "")
        elif tool == "workspace_delete_file":
            if path:
                virtual_files[str(path)] = None
        elif tool == "workspace_apply_unified_patch":
            for item in check.get("projected_files") or []:
                if not isinstance(item, dict) or not item.get("path"):
                    continue
                if item.get("action") == "delete":
                    virtual_files[str(item["path"])] = None
                else:
                    virtual_files[str(item["path"])] = str(item.get("content") or "")

    @staticmethod

    def _sanitize_preflight_check(check: dict[str, typing.Any]) -> dict[str, typing.Any]:
        sanitized = dict(check)
        sanitized.pop("projected_content", None)
        sanitized.pop("projected_files", None)
        sanitized.pop("projected_source_content", None)
        sanitized.pop("projected_target_content", None)
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
        if tool == "workspace_copy_file":
            return self.copy_file(**args)
        if tool == "workspace_move_file":
            return self.move_file(**args)
        if tool == "workspace_delete_file":
            return self.delete_file(**args)
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
                "session_id"         : sid,
                "prompt"             : str(prompt or ""),
                "started_at"         : time.time(),
                "finished_at"        : None,
                "ok"                 : None,
                "steps"              : [],
                "read_files"         : [],
                "written_files"      : [],
                "copied_files"       : [],
                "moved_files"        : [],
                "deleted_files"      : [],
                "patched_files"      : [],
                "searched"           : [],
                "shell_commands"     : [],
                "shell_file_changes" : [],
                "verify"             : None,
                "verify_diagnostics" : None,
                "repair_plan"        : None,
                "repair_loops"       : [],
                "sandbox_results"    : [],
                "sandbox_artifacts"  : [],
                "diagnostic_context" : [],
                "diagnostic_reads"   : [],
                "runs"               : [],
                "run_count"          : 0,
                "last_run_id"        : None,
                "plan"               : self._empty_plan(),
                "status"             : "",
                "diff"               : "",
                "summary"            : {}
            }
            self.sessions[sid] = session
        else:
            session["prompt"] = str(prompt or session.get("prompt") or "")
            session.setdefault("steps", [])
            session.setdefault("read_files", [])
            session.setdefault("written_files", [])
            session.setdefault("copied_files", [])
            session.setdefault("moved_files", [])
            session.setdefault("deleted_files", [])
            session.setdefault("patched_files", [])
            session.setdefault("searched", [])
            session.setdefault("shell_commands", [])
            session.setdefault("shell_file_changes", [])
            session.setdefault("repair_plan", None)
            session.setdefault("repair_loops", [])
            session.setdefault("sandbox_results", [])
            session.setdefault("sandbox_artifacts", [])
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
        runs      = session.setdefault("runs", [])
        run_index = int(session.get("run_count") or len(runs)) + 1
        run_id    = f"{session['session_id']}_run_{run_index}"

        run = {
            "run_id"             : run_id,
            "run_index"          : run_index,
            "kind"               : "initial" if run_index == 1 else "repair",
            "prompt"             : str(prompt or ""),
            "started_at"         : time.time(),
            "finished_at"        : None,
            "ok"                 : None,
            "steps"              : [],
            "shell_commands"     : [],
            "shell_file_changes" : [],
            "copied_files"       : [],
            "moved_files"        : [],
            "deleted_files"      : [],
            "verify"             : None,
            "verify_diagnostics" : None,
            "repair_plan"        : None,
            "sandbox_results"    : [],
            "sandbox_artifacts"  : [],
            "diagnostic_context" : [],
            "diagnostic_reads"   : [],
            "status"             : "",
            "diff"               : "",
            "elapsed_ms"         : None,
            "summary"            : {}
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
        if data.get("execution_target"):
            record["execution_target"] = data.get("execution_target")
        if data.get("requires_cloud_sandbox") is not None:
            record["requires_cloud_sandbox"] = bool(data.get("requires_cloud_sandbox"))
        session.setdefault("steps", []).append(record)
        if isinstance(run, dict):
            run.setdefault("steps", []).append(record)

        if tool == "workspace_read_file" and data.get("path"):
            self._append_unique(session, "read_files", str(data.get("path")))
        elif tool == "workspace_write_file" and data.get("path"):
            self._append_unique(session, "written_files", str(data.get("path")))
        elif tool == "workspace_copy_file" and data.get("source_path") and data.get("target_path"):
            copy_record = {
                "source_path": str(data.get("source_path")),
                "target_path": str(data.get("target_path")),
                "overwritten": bool(data.get("overwritten")),
                "bytes": data.get("bytes"),
                "sha256": data.get("sha256")
            }
            session.setdefault("copied_files", []).append(copy_record)
            if isinstance(run, dict):
                run.setdefault("copied_files", []).append(copy_record)
            self._append_unique(session, "read_files", str(data.get("source_path")))
            self._append_unique(session, "written_files", str(data.get("target_path")))
        elif tool == "workspace_move_file" and data.get("source_path") and data.get("target_path"):
            move_record = {
                "source_path": str(data.get("source_path")),
                "target_path": str(data.get("target_path")),
                "overwritten": bool(data.get("overwritten")),
                "bytes": data.get("bytes"),
                "sha256": data.get("sha256")
            }
            session.setdefault("moved_files", []).append(move_record)
            if isinstance(run, dict):
                run.setdefault("moved_files", []).append(move_record)
            self._append_unique(session, "written_files", str(data.get("target_path")))
        elif tool == "workspace_delete_file" and data.get("path"):
            delete_record = {
                "path": str(data.get("path")),
                "bytes": data.get("bytes"),
                "sha256": data.get("sha256")
            }
            session.setdefault("deleted_files", []).append(delete_record)
            if isinstance(run, dict):
                run.setdefault("deleted_files", []).append(delete_record)
        elif tool == "workspace_apply_patch" and data.get("path"):
            self._append_unique(session, "patched_files", str(data.get("path")))
            self._append_unique(session, "written_files", str(data.get("path")))
        elif tool == "workspace_apply_unified_patch":
            for item in data.get("files") or []:
                if isinstance(item, dict) and item.get("path"):
                    self._append_unique(session, "patched_files", str(item.get("path")))
                    self._append_unique(session, "written_files", str(item.get("path")))
            self._record_unified_patch_changes(session, data, run=run)
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
                "copied_files": list(run.get("copied_files") or []),
                "moved_files": list(run.get("moved_files") or []),
                "deleted_files": list(run.get("deleted_files") or []),
                "created_files": list(run.get("created_files") or []),
                "modified_files": list(run.get("modified_files") or []),
                "shell_file_changes": list(run.get("shell_file_changes") or []),
                "sandbox_results": list(run.get("sandbox_results") or []),
                "sandbox_artifacts": list(run.get("sandbox_artifacts") or []),
                "diagnostic_reads": list(run.get("diagnostic_reads") or []),
                "auto_rollback": run.get("auto_rollback"),
                "rolled_back": bool(run.get("rolled_back")),
                "rollback": run.get("rollback"),
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
            "copied_files": list(session.get("copied_files") or []),
            "moved_files": list(session.get("moved_files") or []),
            "deleted_files": list(session.get("deleted_files") or []),
            "created_files": list(session.get("created_files") or []),
            "modified_files": list(session.get("modified_files") or []),
            "patched_files": list(session.get("patched_files") or []),
            "searched": list(session.get("searched") or []),
            "shell_commands": list(session.get("shell_commands") or []),
            "shell_file_changes": list(session.get("shell_file_changes") or []),
            "sandbox_results": list(session.get("sandbox_results") or []),
            "sandbox_artifacts": list(session.get("sandbox_artifacts") or []),
            "auto_rollback": run.get("auto_rollback") if isinstance(run, dict) else None,
            "rollbacks": list(session.get("rollbacks") or []),
            "verify": session.get("verify"),
            "verify_diagnostics": session.get("verify_diagnostics"),
            "repair_plan": session.get("repair_plan"),
            "repair_loops": list(session.get("repair_loops") or []),
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
        summary["repair_hints"] = self._repair_hints(summary)
        summary["final_summary_context"] = self._final_summary_context(summary)
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

    def _record_unified_patch_changes(
        self,
        session: dict[str, typing.Any],
        data: dict[str, typing.Any],
        *,
        run: dict[str, typing.Any] | None = None
    ) -> None:
        for key, target_key in (
            ("created_files", "created_files"),
            ("updated_files", "modified_files"),
            ("deleted_files", "deleted_files"),
        ):
            for item in data.get(key) or []:
                if not isinstance(item, dict) or not item.get("path"):
                    continue
                record = self._patch_change_record(item)
                session.setdefault(target_key, []).append(record)
                if isinstance(run, dict):
                    run.setdefault(target_key, []).append(record)

    @staticmethod
    def _patch_change_record(item: dict[str, typing.Any]) -> dict[str, typing.Any]:
        return {
            "path"          : str(item.get("path")),
            "action"        : item.get("action"),
            "hunks"         : item.get("hunks"),
            "added_lines"   : item.get("added_lines"),
            "removed_lines" : item.get("removed_lines"),
            "replacements"  : item.get("replacements"),
            "sha256_before" : item.get("sha256_before"),
            "sha256_after"  : item.get("sha256_after")
        }

    @staticmethod
    def _final_summary_context(summary: dict[str, typing.Any]) -> dict[str, typing.Any]:
        verify = summary.get("verify") if isinstance(summary.get("verify"), dict) else None
        return {
            "ok": bool(summary.get("ok")),
            "next_action": summary.get("next_action"),
            "repair_status": summary.get("repair_status"),
            "repair_hints": list(summary.get("repair_hints") or []),
            "changed_files": {
                "created": list(summary.get("created_files") or []),
                "modified": list(summary.get("modified_files") or []),
                "deleted": list(summary.get("deleted_files") or []),
                "moved": list(summary.get("moved_files") or []),
                "copied": list(summary.get("copied_files") or []),
                "patched": list(summary.get("patched_files") or []),
                "written": list(summary.get("written_files") or [])
            },
            "verification": {
                "ran": verify is not None,
                "ok": bool(verify.get("ok")) if verify else None,
                "command": verify.get("command") if verify else None,
                "exit_code": verify.get("exit_code") if verify else None
            },
            "rollback": {
                "auto_rollback": summary.get("auto_rollback"),
                "rollbacks": list(summary.get("rollbacks") or [])
            },
            "shell": {
                "commands": list(summary.get("shell_commands") or []),
                "file_changes": list(summary.get("shell_file_changes") or [])
            }
        }

    @staticmethod
    def _repair_hints(summary: dict[str, typing.Any]) -> list[dict[str, typing.Any]]:
        hints: list[dict[str, typing.Any]] = []
        preflight = summary.get("preflight") if isinstance(summary.get("preflight"), dict) else {}
        for check in preflight.get("checks") or []:
            if isinstance(check, dict) and not check.get("ok"):
                hints.append(SessionTools._hint_from_failure(check, source="preflight"))

        current_run = summary.get("current_run") if isinstance(summary.get("current_run"), dict) else {}
        for step in current_run.get("steps") or []:
            if isinstance(step, dict) and not step.get("ok"):
                hints.append(SessionTools._hint_from_failure(step, source="step"))

        diagnostics = summary.get("verify_diagnostics")
        if isinstance(diagnostics, dict) and diagnostics and not diagnostics.get("ok"):
            hints.append({
                "source": "verify",
                "reason": diagnostics.get("error_type") or "verification_failed",
                "next_action": "inspect_diagnostics",
                "message": "Verification failed; inspect recommended reads, patch the smallest affected area, then rerun verification.",
                "recommended_reads": list(diagnostics.get("read_recommendations") or []),
                "suggested_steps": list(diagnostics.get("suggested_steps") or []),
                "repair_plan": diagnostics.get("repair_plan")
            })
        return [item for item in hints if isinstance(item, dict)]

    @staticmethod
    def _hint_from_failure(item: dict[str, typing.Any], *, source: str) -> dict[str, typing.Any]:
        data = item.get("data") if isinstance(item.get("data"), dict) else item
        reason = item.get("reason") or data.get("reason")
        hint: dict[str, typing.Any] = {
            "source": source,
            "tool": item.get("tool"),
            "reason": reason,
            "next_action": "inspect_failure",
            "message": "Inspect the failure payload and retry with the smallest safe native tool call."
        }
        if reason == "workspace_tool_required":
            hint.update({
                "next_action": "use_suggested_workspace_tool",
                "message": "Use the suggested workspace tool instead of shell_exec for workspace file changes.",
                "suggested_tool": data.get("suggested_tool") or item.get("suggested_tool"),
                "suggested_args": data.get("suggested_args") or item.get("suggested_args") or {}
            })
        elif reason == "file_not_found":
            path = data.get("path") or item.get("path")
            hint.update({
                "next_action": "locate_file",
                "message": "Locate the intended file before editing.",
                "suggested_steps": [
                    {"tool": "workspace_list_files", "args": {"path": ".", "recursive": True, "max_items": 200}},
                    {"tool": "workspace_search_text", "args": {"query": str(path or ""), "path": "."}}
                ]
            })
        elif reason == "file_changed_since_read":
            path = data.get("path") or data.get("source_path") or item.get("path")
            hint.update({
                "next_action": "refresh_file_snapshot",
                "message": "The file changed since it was read; read it again and use the current sha256.",
                "suggested_steps": [{"tool": "workspace_read_file", "args": {"path": path}}] if path else []
            })
        elif str(reason or "").startswith("unified_patch_context_"):
            path = data.get("path") or item.get("path")
            start_line = max(1, int(data.get("target_line") or 1) - 6)
            hint.update({
                "next_action": "regenerate_patch_with_context",
                "message": "Regenerate the unified patch with current nearby context; ambiguous patches need more context lines.",
                "path": path,
                "hunk": data.get("hunk"),
                "hunk_header": data.get("hunk_header"),
                "expected_sequence": data.get("expected_sequence") or [],
                "actual_sequence": data.get("actual_sequence") or [],
                "nearby": data.get("nearby") or [],
                "suggested_steps": [
                    {
                        "tool": "workspace_read_file",
                        "args": {"path": path, "start_line": start_line, "max_lines": 18}
                    }
                ] if path else []
            })
        return hint


if __name__ == '__main__':
    pass
