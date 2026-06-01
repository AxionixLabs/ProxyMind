# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import time
import typing
from backend.mcp_core.native_coding.base import NativeCodingComponent
from backend.utilities import const


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
        artifacts: dict[str, typing.Any] | None = None,
        verify: bool = False,
        auto_repair: bool | str = False,
        run_id: str | None = None,
        record_step: bool = True
    ) -> dict[str, typing.Any]:
        """记录云端沙箱执行结果，并按需接入验证诊断和修复计划。"""
        sid = str(session_id or "").strip()
        session = self.sessions.get(sid)
        if not isinstance(session, dict):
            return self._fail("session_not_found", session_id=session_id)

        normalized_run_id = str(run_id or "").strip()
        runs = session.get("runs") if isinstance(session.get("runs"), list) else []
        if not normalized_run_id and len([item for item in runs if isinstance(item, dict)]) > 1:
            latest_run = self._select_run(session, run_id=None)
            return self._fail(
                "sandbox_result_run_id_required",
                session_id=sid,
                latest_run_id=(latest_run or {}).get("run_id") if isinstance(latest_run, dict) else None
            )

        run = self._select_run(session, run_id=normalized_run_id or None)
        if normalized_run_id and not isinstance(run, dict):
            return self._fail("run_not_found", session_id=sid, run_id=run_id)
        if normalized_run_id and self._is_non_latest_run(session, run):
            latest_run = self._select_run(session, run_id=None)
            return self._fail(
                "sandbox_result_non_latest_run_forbidden",
                session_id=sid,
                run_id=(run or {}).get("run_id"),
                latest_run_id=(latest_run or {}).get("run_id") if isinstance(latest_run, dict) else None
            )
        if isinstance(run, dict) and run.get("rolled_back"):
            return self._fail(
                "sandbox_result_run_rolled_back",
                session_id=sid,
                run_id=run.get("run_id"),
                run_index=run.get("run_index"),
                rollback_at=run.get("rollback_at")
            )
        cmd = [str(item) for item in (command or []) if str(item or "").strip()]
        if not cmd:
            return self._fail("command_required", session_id=sid)
        applied_artifacts = self._apply_sandbox_artifacts(artifacts, session=session, run=run)
        applied_data = applied_artifacts.get("data") if isinstance(applied_artifacts.get("data"), dict) else applied_artifacts
        if not bool(applied_data.get("ok")):
            return applied_artifacts
        ok = int(exit_code or 0) == 0 and not bool(timed_out)
        normalized_file_changes = self._merge_artifact_file_changes(file_changes, applied_artifacts)

        data: dict[str, typing.Any] = {
            "ok"                     : ok,
            "command"                : cmd,
            "cwd"                    : str(cwd or "."),
            "risk"                   : "sandbox",
            "category"               : "sandbox",
            "execution_target"       : "cloud_sandbox",
            "requires_cloud_sandbox" : False,
            "sandbox_provider"       : str(sandbox_provider or "cloud_sandbox"),
            "exit_code"              : int(exit_code or 0),
            "timed_out"              : bool(timed_out),
            "elapsed_ms"             : int(elapsed_ms or 0),
            "stdout"                 : self._clip_output(str(stdout or "")),
            "stderr"                 : self._clip_output(str(stderr or "")),
            "shell_file_changes"     : normalized_file_changes,
            "shell_write_detected"   : bool(normalized_file_changes.get("changed")),
            "sandbox_artifacts"      : applied_data if applied_data.get("applied") else None
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
                        prompt="修复云端沙箱验证失败",
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
            "recorded_at"      : time.time(),
            "command"          : cmd,
            "cwd"              : data["cwd"],
            "ok"               : ok,
            "exit_code"        : data["exit_code"],
            "timed_out"        : data["timed_out"],
            "elapsed_ms"       : data["elapsed_ms"],
            "sandbox_provider" : data["sandbox_provider"],
            "verify"           : bool(verify),
            "artifacts"        : data.get("sandbox_artifacts"),
            "diagnostics"      : data.get("diagnostics")
        }

        session.setdefault("sandbox_results", []).append(sandbox_record)
        if isinstance(run, dict):
            run.setdefault("sandbox_results", []).append(sandbox_record)
        if record_step:
            step = {
                "tool"      : "record_sandbox_result",
                "ok"        : ok,
                "data"      : data,
                "run_id"    : (run or {}).get("run_id"),
                "run_index" : (run or {}).get("run_index")
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
            f"沙箱结果已记录 ok={ok} verify={bool(verify)} provider={data['sandbox_provider']}",
            **data,
            repair_plan=repair_plan,
            **repair_state
        )

    def _apply_sandbox_artifacts(
        self,
        artifacts: dict[str, typing.Any] | None,
        *,
        session: dict[str, typing.Any],
        run: dict[str, typing.Any] | None
    ) -> dict[str, typing.Any]:
        """校验并应用云端沙箱回传的文件产物。"""
        if not artifacts:
            return {"ok": True, "applied": False}
        files = artifacts.get("files") if isinstance(artifacts, dict) else None
        if not isinstance(files, list):
            return self._fail("sandbox_artifacts_files_required")
        if len(files) > 100:
            return self._fail("sandbox_artifacts_too_many_files", count=len(files), max_files=100)

        planned: list[dict[str, typing.Any]] = []
        total_bytes = 0
        seen: set[str] = set()
        for raw in files:
            if not isinstance(raw, dict):
                return self._fail("sandbox_artifact_not_dict")
            action = str(raw.get("action") or "").strip().lower()
            if action not in {"create", "modify", "delete"}:
                return self._fail("sandbox_artifact_bad_action", action=action)
            if not str(raw.get("path") or "").strip():
                return self._fail("sandbox_artifact_path_required")
            try:
                target = self._resolve(str(raw.get("path")))
            except Exception as exc:
                return self._fail(
                    "sandbox_artifact_path_outside_workspace",
                    path=str(raw.get("path") or ""),
                    detail=str(exc)
                )
            rel = self._rel(target)
            if rel in seen:
                return self._fail("sandbox_artifact_duplicate_path", path=rel)
            seen.add(rel)

            content = "" if action == "delete" else str(raw.get("content") or "")
            encoded = content.encode(const.CHARSET, const.IGNORE)

            size = len(encoded)
            total_bytes += size
            if total_bytes > self.max_write_bytes:
                return self._fail("sandbox_artifacts_too_large", size=total_bytes, max_bytes=self.max_write_bytes)
            expected_bytes = raw.get("bytes")
            if expected_bytes is not None and int(expected_bytes) != size:
                return self._fail("sandbox_artifact_bytes_mismatch", path=rel, expected_bytes=int(expected_bytes), bytes=size)
            expected_sha = str(raw.get("sha256") or "").strip().lower()
            actual_sha = self._sha256(encoded)
            if action != "delete" and expected_sha and expected_sha != actual_sha:
                return self._fail("sandbox_artifact_sha256_mismatch", path=rel, expected_sha256=expected_sha, sha256=actual_sha)
            if action == "create" and target.exists():
                return self._fail("sandbox_artifact_file_exists", path=rel)
            if action in {"modify", "delete"} and not target.is_file():
                return self._fail("sandbox_artifact_file_not_found", path=rel)
            expected_old_sha = str(
                raw.get("expected_old_sha256")
                or raw.get("base_sha256")
                or raw.get("old_sha256")
                or ""
            ).strip().lower()
            if action in {"modify", "delete"} and expected_old_sha:
                current_sha = self._sha256(target.read_bytes())
                if current_sha != expected_old_sha:
                    return self._fail(
                        "sandbox_artifact_base_sha256_mismatch",
                        path=rel,
                        expected_old_sha256=expected_old_sha,
                        sha256=current_sha
                    )

            planned.append({
                "path"    : rel,
                "target"  : target,
                "action"  : action,
                "content" : content,
                "bytes"   : size,
                "sha256"  : actual_sha if action != "delete" else None
            })

        snapshot = self._create_artifact_snapshot(planned)
        applied: list[dict[str, typing.Any]] = []
        for item in planned:
            target = item["target"]
            action = item["action"]
            if action == "delete":
                target.unlink()
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(item["content"], encoding=const.CHARSET, newline="")
            applied.append({
                "path"   : item["path"],
                "action" : action,
                "bytes"  : item["bytes"],
                "sha256" : item["sha256"]
            })

        artifact_record = {
            "applied_at"  : time.time(),
            "file_count"  : len(applied),
            "total_bytes" : total_bytes,
            "files"       : applied,
            "snapshot"    : self._public_snapshot(snapshot)
        }
        session.setdefault("sandbox_artifacts", []).append(artifact_record)
        if isinstance(run, dict):
            run.setdefault("sandbox_artifacts", []).append(artifact_record)
            run.setdefault("artifact_snapshots", []).append(snapshot)

        return self._ok(
            f"沙箱产物已应用 files={len(applied)} bytes={total_bytes}",
            applied=True,
            file_count=len(applied),
            total_bytes=total_bytes,
            files=applied,
            snapshot=self._public_snapshot(snapshot)
        )

    def _create_artifact_snapshot(
        self,
        planned: list[dict[str, typing.Any]]
    ) -> dict[str, typing.Any]:
        """为即将应用的沙箱产物创建回滚快照。"""
        files: list[dict[str, typing.Any]] = []
        for item in planned:
            target = item["target"]
            if target.is_file():
                content = target.read_text(encoding=const.CHARSET, errors=const.IGNORE)
                files.append({
                    "path"    : self._rel(target),
                    "existed" : True,
                    "sha256"  : self._sha256(content.encode(const.CHARSET, const.IGNORE)),
                    "content" : content
                })
            else:
                files.append({
                    "path"    : self._rel(target),
                    "existed" : False,
                    "sha256"  : None,
                    "content" : ""
                })
        return {
            "created_at" : time.time(),
            "files"      : files,
            "file_count" : len(files)
        }

    @staticmethod
    def _merge_artifact_file_changes(
        file_changes: dict[str, typing.Any] | None,
        applied_artifacts: dict[str, typing.Any]
    ) -> dict[str, typing.Any]:
        """合并沙箱声明的文件变更和实际应用的产物变更。"""
        base = file_changes if isinstance(file_changes, dict) else {
            "changed"      : False,
            "change_count" : 0,
            "created"      : [],
            "modified"     : [],
            "deleted"      : [],
            "truncated"    : False
        }
        merged = {
            "changed"      : bool(base.get("changed")),
            "change_count" : int(base.get("change_count") or 0),
            "created"      : list(base.get("created") or []),
            "modified"     : list(base.get("modified") or []),
            "deleted"      : list(base.get("deleted") or []),
            "truncated"    : bool(base.get("truncated"))
        }
        data = applied_artifacts.get("data") if isinstance(applied_artifacts, dict) else {}
        if not isinstance(data, dict) or not data.get("applied"):
            if isinstance(applied_artifacts, dict) and applied_artifacts.get("applied"):
                data = applied_artifacts
            else:
                return merged
        for item in data.get("files") or []:
            if not isinstance(item, dict):
                continue
            path = str(item.get("path") or "")
            action = str(item.get("action") or "")
            if not path:
                continue
            if action == "create":
                bucket = "created"
            elif action == "delete":
                bucket = "deleted"
            else:
                bucket = "modified"
            if path not in merged[bucket]:
                merged[bucket].append(path)
        merged["changed"] = True
        merged["change_count"] = len(set(merged["created"] + merged["modified"] + merged["deleted"]))
        return merged

    @staticmethod
    def _select_run(
        session: dict[str, typing.Any],
        *,
        run_id: str | None
    ) -> dict[str, typing.Any] | None:
        """按 run_id 选择运行记录，未指定时返回最新记录。"""
        runs = session.get("runs") if isinstance(session.get("runs"), list) else []
        if run_id:
            for item in runs:
                if isinstance(item, dict) and item.get("run_id") == run_id:
                    return item
            return None
        return runs[-1] if runs and isinstance(runs[-1], dict) else None

    @staticmethod
    def _is_non_latest_run(
        session: dict[str, typing.Any],
        run: dict[str, typing.Any] | None
    ) -> bool:
        """判断给定运行记录是否不是当前最新记录。"""
        if not isinstance(run, dict):
            return False
        runs = session.get("runs") if isinstance(session.get("runs"), list) else []
        latest = runs[-1] if runs and isinstance(runs[-1], dict) else None
        return isinstance(latest, dict) and latest.get("run_id") != run.get("run_id")


if __name__ == '__main__':
    pass
