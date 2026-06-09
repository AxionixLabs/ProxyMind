# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import os
import re
import time
import typing
import asyncio
from loguru import logger
from backend.mcp_code.base import NativeCodingComponent
from backend.mcp_code.command_runtime import NativeCommandRuntime
from backend.mcp_code.runtime_resolution import RuntimeResolver
from backend.utilities.process import Flux
from backend.utilities.trace import summarize_command


class ShellExecTools(NativeCodingComponent):
    """提供受控 shell 执行能力。"""

    READ_ONLY_COMMANDS = {
        "cat",
        "dir",
        "echo",
        "find",
        "git",
        "grep",
        "head",
        "ls",
        "python",
        "python3",
        "py",
        "rg",
        "tail",
        "type",
        "where"
    }

    READ_ONLY_PYTHON_FLAGS = {
        "-c",
        "-m",
        "--version",
        "-V"
    }

    READ_ONLY_VERSION_COMMANDS = {
        "go"     : {"version"},
        "node"   : {"--version", "-v"},
        "npm"    : {"--version", "-v", "version"},
        "npx"    : {"--version", "-v"},
        "java"   : {"-version", "--version"},
        "javac"  : {"-version", "--version"},
        "mvn"    : {"-version", "--version", "-v"},
        "gradle" : {"-version", "--version", "-v"}
    }

    READ_ONLY_GIT_SUBCOMMANDS = {
        "branch",
        "diff",
        "log",
        "rev-parse",
        "show",
        "status"
    }

    SHELL_WRITE_COMMAND_TO_TOOL = {
        "cp"          : "workspace_copy_file",
        "copy"        : "workspace_copy_file",
        "copy-item"   : "workspace_copy_file",
        "mv"          : "workspace_move_file",
        "move"        : "workspace_move_file",
        "move-item"   : "workspace_move_file",
        "rename"      : "workspace_move_file",
        "rename-item" : "workspace_move_file",
        "rm"          : "workspace_delete_file",
        "del"         : "workspace_delete_file",
        "erase"       : "workspace_delete_file",
        "remove-item" : "workspace_delete_file",
        "ri"          : "workspace_delete_file",
        "touch"       : "workspace_write_file",
        "tee"         : "workspace_write_file"
    }

    INLINE_SCRIPT_FLAGS = {"-c", "-e", "-r"}

    @staticmethod
    def _shell_output_next_steps(
        *,
        cmd: list[str],
        cwd: str,
        timeout_sec: int,
        stdout_truncated: bool,
        stderr_truncated: bool
    ) -> list[dict[str, typing.Any]]:
        """根据输出截断状态生成可选的后续执行建议。"""
        if not stdout_truncated and not stderr_truncated:
            return []

        return [
            {
                "tool": "shell_exec",
                "args": {
                    "command": cmd,
                    "cwd": cwd,
                    "timeout_sec": timeout_sec
                },
                "reason": "rerun_with_narrower_or_larger_output"
            }
        ]

    @classmethod
    def _audit_mode_for_command(
        cls,
        cmd: list[str],
        *,
        audit_files: bool
    ) -> str:
        """根据命令类型选择审计强度。"""
        if not audit_files:
            return "off"
        if not cmd:
            return "full"

        executable = os.path.basename(str(cmd[0])).lower()
        if executable.endswith(".exe"):
            executable = executable[:-4]

        if executable == "git":
            subcommand = next((str(item).lower() for item in cmd[1:] if not str(item).startswith("-")), "")
            return "metadata" if subcommand in cls.READ_ONLY_GIT_SUBCOMMANDS else "full"

        if executable in {"python", "python3", "py"}:
            if len(cmd) >= 2 and str(cmd[1]) in cls.READ_ONLY_PYTHON_FLAGS:
                return "metadata"
            return "full"

        version_flags = cls.READ_ONLY_VERSION_COMMANDS.get(executable)
        if version_flags:
            normalized_args = {str(item).lower() for item in cmd[1:]}
            if normalized_args and normalized_args.issubset(version_flags):
                return "metadata"
            return "full"

        if executable in cls.READ_ONLY_COMMANDS:
            return "metadata"

        return "full"

    @classmethod
    def _shell_write_intent(
        cls,
        cmd: list[str]
    ) -> dict[str, typing.Any] | None:
        """识别明显用于修改工作区文件的 shell 命令。"""
        if not cmd:
            return None

        executable = os.path.basename(str(cmd[0])).lower()
        if executable.endswith(".exe"):
            executable = executable[:-4]

        suggested_tool = cls.SHELL_WRITE_COMMAND_TO_TOOL.get(executable)
        if suggested_tool:
            return {
                "reason"         : "shell_file_operation_command",
                "suggested_tool" : suggested_tool,
                "suggested_args" : {},
                "message"        : "use workspace file tools instead of shell file operations"
            }

        lowered_args = [str(item).lower() for item in cmd[1:]]
        joined       = " ".join(lowered_args)

        if executable in {"sed", "gsed"} and any(item == "-i" or item.startswith("-i") for item in lowered_args):
            return {
                "reason"         : "shell_in_place_edit_command",
                "suggested_tool" : "workspace_apply_patch",
                "suggested_args" : {},
                "message"        : "use workspace_apply_patch for in-place text edits"
            }

        if script_intent := cls._inline_script_write_intent(executable, cmd):
            return script_intent

        if ">" in lowered_args or ">>" in lowered_args or "|" in lowered_args and "tee" in joined:
            return {
                "reason"         : "shell_redirection_file_write",
                "suggested_tool" : "workspace_write_file",
                "suggested_args" : {},
                "message"        : "use workspace_write_file instead of shell redirection"
            }

        return None

    @classmethod
    def _inline_script_write_intent(
        cls,
        executable: str,
        cmd: list[str]
    ) -> dict[str, typing.Any] | None:
        """识别常见脚本解释器的内联写文件表达式。"""
        if len(cmd) < 3:
            return None

        flag_index = next(
            (
                index for index, item in enumerate(cmd[1:], start=1)
                if str(item).lower() in cls.INLINE_SCRIPT_FLAGS
            ),
            None
        )
        if flag_index is None or flag_index + 1 >= len(cmd):
            return None

        script = str(cmd[flag_index + 1])
        patterns_by_executable = {
            "python"  : [r"\bopen\s*\([^)]*['\"](?:w|a|x|wb|ab|xb)\+?['\"]"],
            "python3" : [r"\bopen\s*\([^)]*['\"](?:w|a|x|wb|ab|xb)\+?['\"]"],
            "py"      : [r"\bopen\s*\([^)]*['\"](?:w|a|x|wb|ab|xb)\+?['\"]"],
            "node"    : [
                r"\b(?:fs\.)?(?:writeFileSync|appendFileSync|createWriteStream)\s*\(",
                r"\brequire\s*\(\s*['\"]fs['\"]\s*\)\s*\.\s*(?:writeFileSync|appendFileSync|createWriteStream)\s*\("
            ],
            "ruby"    : [r"\bFile\.(?:write|open)\s*\(", r"\bIO\.write\s*\("],
            "perl"    : [r"\bopen\s*\([^)]*,\s*['\"]?>", r"\b(?:print|say)\s+\w+\s+"],
            "php"     : [r"\bfile_put_contents\s*\(", r"\bfopen\s*\([^)]*,\s*['\"](?:w|a|x|c)"]
        }

        patterns = patterns_by_executable.get(executable)
        if not patterns:
            return None
        if not any(re.search(pattern, script) for pattern in patterns):
            return None

        return {
            "reason"         : "shell_inline_script_file_write",
            "suggested_tool" : "workspace_write_file",
            "suggested_args" : {},
            "message"        : "use workspace file tools for inline script file writes"
        }

    def _capture_shell_audit(self, mode: str) -> dict[str, typing.Any] | None:
        """按审计模式采集文件指纹。"""
        if mode == "off":
            return None
        return self.capture_file_fingerprints(hash_files=mode == "full")

    def _record_shell_result(self, data: dict[str, typing.Any]) -> None:
        """记录最近一次 shell_exec 结果，供 change_summary 汇总验证证据。"""
        if not isinstance(data, dict):
            return
        record = dict(data)

        self.core.last_shell_result = record

        history = getattr(self.core, "validation_history", None)
        if not isinstance(history, list):
            history = []
            self.core.validation_history = history
        history.append(record)
        del history[:-20]

    async def shell_exec(
        self,
        *,
        command: list[str],
        cwd: str = ".",
        timeout_sec: int = 60,
        execution: dict[str, typing.Any] | None = None,
        audit_files: bool = True
    ) -> dict[str, typing.Any]:
        """按执行元数据运行 shell 命令，必要时返回云端沙盒交接结果。"""
        cmd = [str(item) for item in (command or []) if str(item or "").strip()]
        if not cmd:
            return self._fail("command_empty")

        if write_intent := self._shell_write_intent(cmd):
            result = self._fail(
                write_intent["reason"],
                command=cmd,
                suggested_tool=write_intent.get("suggested_tool"),
                suggested_args=write_intent.get("suggested_args") or {},
                message=write_intent.get("message"),
                shell_write_detected=True,
                execution_target="blocked",
                requires_cloud_sandbox=False
            )
            self._record_shell_result(result.get("data") or {})
            return result

        policy = self.execution_metadata_policy(
            execution,
            command=cmd,
            cwd=cwd,
            timeout_sec=timeout_sec
        )
        if not policy["ok"]:
            result = self._fail(
                policy["reason"],
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
                execution=policy.get("execution"),
                grant_id=policy.get("grant_id")
            )
            self._record_shell_result(result.get("data") or {})
            return result

        workdir = self._resolve(cwd)
        if not workdir.is_dir():
            result = self._fail("cwd_not_directory", cwd=cwd, command=cmd)
            self._record_shell_result(result.get("data") or {})
            return result

        if policy.get("execution_target") == "cloud_sandbox":
            result = self._ok(
                "shell exec requires cloud sandbox",
                ok=False,
                command=cmd,
                cwd=self._rel(workdir),
                risk=policy.get("risk"),
                category=policy.get("category"),
                risk_reasons=policy.get("reasons") or [],
                approval_required=bool(policy.get("approval_required")),
                execution_target="cloud_sandbox",
                requires_cloud_sandbox=True,
                execution=policy.get("execution"),
                grant_id=policy.get("grant_id"),
                project_types=policy.get("project_types") or [],
                long_task=bool(policy.get("long_task")),
                timeout_sec=policy.get("timeout_sec"),
                output_limit=policy.get("output_limit")
            )
            self._record_shell_result(result.get("data") or {})
            return result

        effective_timeout = int(policy.get("timeout_sec") or timeout_sec or 60)
        output_limit      = int(policy.get("output_limit") or self.max_output_chars)
        env               = os.environ.copy()

        runtime = RuntimeResolver.resolve_shell_command(cmd, env=env)
        if not runtime.get("ok"):
            cloud_supported = bool(runtime.get("cloud_sandbox_supported"))
            result = self._ok(
                "shell exec runtime unavailable",
                ok=False,
                command=cmd,
                cwd=self._rel(workdir),
                risk=policy.get("risk"),
                category=policy.get("category"),
                risk_reasons=[
                    *(policy.get("reasons") or []),
                    str(runtime.get("reason") or "local_runtime_unavailable")
                ],
                approval_required=bool(policy.get("approval_required")),
                execution_target=runtime.get("execution_target") or "local",
                requires_cloud_sandbox=bool(runtime.get("requires_cloud_sandbox")),
                cloud_sandbox_supported=cloud_supported,
                execution=policy.get("execution"),
                grant_id=policy.get("grant_id"),
                project_types=policy.get("project_types") or [],
                long_task=bool(policy.get("long_task")),
                timeout_sec=policy.get("timeout_sec"),
                output_limit=policy.get("output_limit"),
                reason=runtime.get("reason"),
                suggested_next_action=runtime.get("suggested_next_action"),
                runtime=runtime
            )
            self._record_shell_result(result.get("data") or {})
            return result

        exec_cmd = list(runtime.get("command") or cmd)
        if not runtime.get("changed"):
            exec_cmd = NativeCommandRuntime.resolve_command(exec_cmd, env=env)

        audit_mode   = self._audit_mode_for_command(cmd, audit_files=audit_files)
        audit_before = self._capture_shell_audit(audit_mode)
        started      = time.perf_counter()

        proc = await Flux.cmd_link_exec(exec_cmd, cwd=str(workdir), env=env)

        timed_out = False

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=max(1, effective_timeout)
            )
        except asyncio.TimeoutError:
            timed_out = True
            proc.kill()
            stdout, stderr = await proc.communicate()

        elapsed_ms  = int((time.perf_counter() - started) * 1000)
        audit_after = self._capture_shell_audit(audit_mode)

        shell_file_changes = self.diff_file_fingerprints(audit_before, audit_after) if audit_mode != "off" else {
            "changed"        : False,
            "change_count"   : 0,
            "created"        : [],
            "modified"       : [],
            "deleted"        : [],
            "created_count"  : 0,
            "modified_count" : 0,
            "deleted_count"  : 0,
            "truncated"      : False
        }

        raw_stdout = self._decode(stdout or b"")
        raw_stderr = self._decode(stderr or b"")
        out_text   = self._clip_output(raw_stdout, max_chars=output_limit)
        err_text   = self._clip_output(raw_stderr, max_chars=output_limit)
        exit_code  = int(proc.returncode or 0)

        ok = (exit_code == 0) and not timed_out

        stdout_truncated = len(raw_stdout) > output_limit
        stderr_truncated = len(raw_stderr) > output_limit

        logger.debug(
            f"native shell exit ok={ok} rc={exit_code} elapsed_ms={elapsed_ms} "
            f"cmd={summarize_command(cmd)}"
        )
        data = {
            "ok": ok,
            "command": cmd,
            "resolved_command": exec_cmd,
            "cwd": self._rel(workdir),
            "risk": policy.get("risk"),
            "category": policy.get("category"),
            "risk_reasons": policy.get("reasons") or [],
            "approval_required": bool(policy.get("approval_required")),
            "execution_target": policy.get("execution_target"),
            "requires_cloud_sandbox": bool(policy.get("requires_cloud_sandbox")),
            "execution": policy.get("execution"),
            "grant_id": policy.get("grant_id"),
            "runtime": runtime.get("runtime") if runtime.get("changed") else None,
            "project_types": policy.get("project_types") or [],
            "long_task": bool(policy.get("long_task")),
            "timeout_sec": effective_timeout,
            "output_limit": output_limit,
            "stdout_truncated": stdout_truncated,
            "stderr_truncated": stderr_truncated,
            "truncated": stdout_truncated or stderr_truncated,
            "recommended_next_steps": self._shell_output_next_steps(
                cmd=cmd,
                cwd=self._rel(workdir),
                timeout_sec=effective_timeout,
                stdout_truncated=stdout_truncated,
                stderr_truncated=stderr_truncated
            ),
            "file_audit_enabled": audit_mode != "off",
            "file_audit_mode": audit_mode,
            "shell_file_changes": shell_file_changes,
            "shell_write_detected": bool(shell_file_changes.get("changed")),
            "exit_code": exit_code,
            "timed_out": timed_out,
            "elapsed_ms": elapsed_ms,
            "stdout": out_text,
            "stderr": err_text
        }
        self._record_shell_result(data)

        return {
            "text"        : f"shell exec {'ok' if ok else 'failed'} exit_code={exit_code} elapsed_ms={elapsed_ms}",
            "attachments" : [],
            "data"        : data,
            "logs"        : []
        }


if __name__ == '__main__':
    pass
