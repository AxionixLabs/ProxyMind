# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import time
import typing
from backend.mcp_core.native_coding.base import NativeCodingComponent


class SandboxResultTools(NativeCodingComponent):
    """记录云端沙箱执行结果，并复用本地诊断链路。"""

    def record_sandbox_result(
        self,
        *,
        session_id: str,
        command: list[str],
        cwd: str = ".",
        exit_code: int = 0,
        stdout: str = "",
        stderr: str = "",
        elapsed_ms: int | None = None,
        timed_out: bool = False,
        sandbox_provider: str = "cloud_sandbox",
        file_changes: dict[str, typing.Any] | None = None,
        verify: bool = False,
        auto_repair: bool | str = False,
        run_id: str | None = None,
        record_step: bool = True
    ) -> dict[str, typing.Any]:
        sid = str(session_id or "").strip()
        session = self.sessions.get(sid)
        if not isinstance(session, dict):
            return self._fail("session_not_found", session_id=session_id)

        run = self._select_run(session, run_id=run_id)
        if run_id and not isinstance(run, dict):
            return self._fail("run_not_found", session_id=sid, run_id=run_id)
        cmd = [str(item) for item in (command or []) if str(item or "").strip()]
        if not cmd:
            return self._fail("command_required", session_id=sid)
        ok = int(exit_code or 0) == 0 and not bool(timed_out)
        data: dict[str, typing.Any] = {
            "ok": ok,
            "command": cmd,
            "cwd": str(cwd or "."),
            "risk": "sandbox",
            "category": "sandbox",
            "execution_target": "cloud_sandbox",
            "requires_cloud_sandbox": False,
            "sandbox_provider": str(sandbox_provider or "cloud_sandbox"),
            "exit_code": int(exit_code or 0),
            "timed_out": bool(timed_out),
            "elapsed_ms": int(elapsed_ms or 0),
            "stdout": self._clip_output(str(stdout or "")),
            "stderr": self._clip_output(str(stderr or "")),
            "shell_file_changes": file_changes if isinstance(file_changes, dict) else {
                "changed": False,
                "change_count": 0,
                "created": [],
                "modified": [],
                "deleted": [],
                "truncated": False
            },
            "shell_write_detected": bool((file_changes or {}).get("changed")) if isinstance(file_changes, dict) else False
        }
        if verify:
            diagnostics = self._diagnose_verify_failure(data)
            if not diagnostics.get("ok"):
                context = self._collect_verify_diagnostic_context(diagnostics)
                diagnostics["context"] = context
                diagnostics["auto_read_count"] = len([item for item in context if item.get("ok")])
                diagnostics["repair_prompt"] = self._build_repair_prompt(data, diagnostics)
                if self._normalize_auto_repair(auto_repair) == "plan":
                    diagnostics["repair_plan"] = self._build_repair_plan(
                        prompt="repair cloud sandbox verification failure",
                        verify_command=cmd,
                        verify_data=data,
                        diagnostics=diagnostics,
                        session_id=sid,
                        run_id=(run or {}).get("run_id") or str(run_id or "")
                    )
                session["diagnostic_context"] = context
                if isinstance(run, dict):
                    run["diagnostic_context"] = context
                for item in context:
                    if item.get("ok") and item.get("path"):
                        self._append_unique(session, "read_files", str(item.get("path")))
                        self._append_unique(session, "diagnostic_reads", str(item.get("path")))
                        if isinstance(run, dict):
                            self._append_unique(run, "diagnostic_reads", str(item.get("path")))
            data["diagnostics"] = diagnostics

        sandbox_record = {
            "recorded_at": time.time(),
            "command": cmd,
            "cwd": data["cwd"],
            "ok": ok,
            "exit_code": data["exit_code"],
            "timed_out": data["timed_out"],
            "elapsed_ms": data["elapsed_ms"],
            "sandbox_provider": data["sandbox_provider"],
            "verify": bool(verify),
            "diagnostics": data.get("diagnostics")
        }
        session.setdefault("sandbox_results", []).append(sandbox_record)
        if isinstance(run, dict):
            run.setdefault("sandbox_results", []).append(sandbox_record)
        if record_step:
            step = {
                "tool": "record_sandbox_result",
                "ok": ok,
                "data": data,
                "run_id": (run or {}).get("run_id"),
                "run_index": (run or {}).get("run_index")
            }
            self._record_step(session, step, run=run)
        if verify:
            self._record_verify(session, {"data": data}, run=run)
        repair_plan = (data.get("diagnostics") or {}).get("repair_plan") if isinstance(data.get("diagnostics"), dict) else None
        repair_state = self._repair_state(
            ok=ok,
            failed_steps=[],
            verify={"data": data},
            repair_plan=repair_plan
        )
        return self._ok(
            f"sandbox result recorded ok={ok} verify={bool(verify)} provider={data['sandbox_provider']}",
            **data,
            repair_plan=repair_plan,
            **repair_state
        )

    @staticmethod
    def _select_run(
        session: dict[str, typing.Any],
        *,
        run_id: str | None
    ) -> dict[str, typing.Any] | None:
        runs = session.get("runs") if isinstance(session.get("runs"), list) else []
        if run_id:
            for item in runs:
                if isinstance(item, dict) and item.get("run_id") == run_id:
                    return item
            return None
        return runs[-1] if runs and isinstance(runs[-1], dict) else None


if __name__ == '__main__':
    pass
