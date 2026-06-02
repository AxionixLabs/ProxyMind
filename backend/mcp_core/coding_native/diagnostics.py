# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import re
import typing
from pathlib import Path
from backend.mcp_core.coding_native.base import NativeCodingComponent
from backend.utilities.trace import clip_text


class DiagnosticsTools(NativeCodingComponent):

    def _diagnose_verify_failure(
        self,
        data: dict[str, typing.Any] | None
    ) -> dict[str, typing.Any]:
        payload  = data if isinstance(data, dict) else {}
        ok       = bool(payload.get("ok"))
        stdout   = str(payload.get("stdout") or "")
        stderr   = str(payload.get("stderr") or "")
        combined = "\n".join(item for item in [stdout, stderr] if item)

        if ok:
            return {
                "ok"                   : True,
                "error_type"           : None,
                "references"           : [],
                "read_recommendations" : [],
                "suggested_steps"      : []
            }

        framework  = self._detect_verify_framework(payload, combined)
        references = self._extract_output_references(combined)

        read_recommendations = [
            {
                "path"       : item["path"],
                "line"       : item["line"],
                "start_line" : max(1, int(item["line"]) - 6),
                "max_lines"  : 18,
                "source"     : item.get("source"),
                "framework"  : framework
            }
            for item in references[:8]
        ]
        suggested_steps = [
            {
                "tool": "workspace_read_file",
                "args": {
                    "path"       : item["path"],
                    "start_line" : item["start_line"],
                    "max_lines"  : item["max_lines"]
                }
            }
            for item in read_recommendations
        ]

        return {
            "ok"                   : False,
            "framework"            : framework,
            "error_type"           : self._classify_verify_error(payload, combined, framework=framework),
            "references"           : references,
            "read_recommendations" : read_recommendations,
            "suggested_steps"      : suggested_steps,
            "context"              : [],
            "auto_read_count"      : 0,
            "repair_prompt"        : "",
            "output_tail"          : self._tail_output(combined, max_chars=4000)
        }

    def _collect_verify_diagnostic_context(
        self,
        diagnostics: dict[str, typing.Any]
    ) -> list[dict[str, typing.Any]]:
        context: list[dict[str, typing.Any]] = []
        seen: set[tuple[str, int, int]] = set()

        steps = diagnostics.get("suggested_steps") if isinstance(diagnostics, dict) else []

        for step in (steps or [])[:8]:
            if not isinstance(step, dict) or step.get("tool") != "workspace_read_file":
                continue
            args = step.get("args") if isinstance(step.get("args"), dict) else {}
            path = str(args.get("path") or "").strip()
            if not path:
                continue

            start_line = max(1, int(args.get("start_line") or 1))
            max_lines  = max(1, min(int(args.get("max_lines") or 18), 80))

            key = (path, start_line, max_lines)
            if key in seen:
                continue
            seen.add(key)

            result = self.read_file(
                path=path,
                start_line=start_line,
                max_lines=max_lines
            )
            data = result.get("data") if isinstance(result, dict) else {}
            if bool(data.get("ok")):
                context.append({
                    "ok"          : True,
                    "path"        : data.get("path"),
                    "start_line"  : data.get("start_line"),
                    "max_lines"   : max_lines,
                    "total_lines" : data.get("total_lines"),
                    "sha256"      : data.get("sha256"),
                    "content"     : data.get("content")
                })
            else:
                context.append({
                    "ok"         : False,
                    "path"       : path,
                    "start_line" : start_line,
                    "max_lines"  : max_lines,
                    "reason"     : data.get("reason")
                })
        return context

    def _build_repair_prompt(
        self,
        verify_data: dict[str, typing.Any],
        diagnostics: dict[str, typing.Any]
    ) -> str:
        command = verify_data.get("command")

        command_text = " ".join(
            str(item) for item in command
        ) if isinstance(command, list) else str(command or "")

        lines = [
            "Repair the failing verification with the smallest safe code change.",
            "",
            f"Verification command: {command_text}",
            f"Exit code: {verify_data.get('exit_code')}",
            f"Error type: {diagnostics.get('error_type')}",
            "",
            "Constraints:",
            "- Use workspace_apply_unified_patch for targeted edits when possible.",
            "- Include expected_sha256 for every edited existing file.",
            "- Do not edit unrelated files.",
            "- Re-run the same verification command after patching.",
            "",
            "Failure output tail:",
            "```text",
            self._clip_output(str(diagnostics.get("output_tail") or ""), max_chars=2500),
            "```",
            "",
            "Relevant file context:"
        ]

        context = diagnostics.get("context") if isinstance(diagnostics, dict) else []

        ok_context = [item for item in (context or []) if isinstance(item, dict) and item.get("ok")]
        if not ok_context:
            lines.extend([
                "- No file context was auto-read. Use suggested_steps before patching."
            ])

        for item in ok_context[:6]:
            path = str(item.get("path") or "")
            start_line = int(item.get("start_line") or 1)
            sha256 = str(item.get("sha256") or "")
            content = str(item.get("content") or "")
            lines.extend([
                "",
                f"File: {path}",
                f"Start line: {start_line}",
                f"SHA256: {sha256}",
                "```text",
                self._numbered_content(content, start_line=start_line),
                "```"
            ])

        if ok_context:
            sha_items = [
                f'"{item.get("path")}": "{item.get("sha256")}"'
                for item in ok_context[:6]
                if item.get("path") and item.get("sha256")
            ]
            if sha_items:
                lines.extend([
                    "",
                    "Expected SHA256 map for patching:",
                    "```json",
                    "{",
                    "  " + ",\n  ".join(sha_items),
                    "}",
                    "```"
                ])

        lines.extend([
            "",
            "Return the next native coding steps as JSON-like tool calls:",
            "1. workspace_apply_unified_patch with the minimal unified diff.",
            "2. native_coding_loop or shell_exec to re-run verification."
        ])
        return self._clip_output("\n".join(lines), max_chars=12000)

    def _build_repair_plan(
        self,
        *,
        prompt: str,
        verify_command: list[str] | None,
        verify_data: dict[str, typing.Any],
        diagnostics: dict[str, typing.Any],
        session_id: str,
        run_id: str
    ) -> dict[str, typing.Any]:
        context    = diagnostics.get("context") if isinstance(diagnostics, dict) else []
        ok_context = [item for item in (context or []) if isinstance(item, dict) and item.get("ok")]

        sha_map = {
            str(item.get("path")): str(item.get("sha256"))
            for item in ok_context
            if item.get("path") and item.get("sha256")
        }
        affected_files = [
            {
                "path"        : item.get("path"),
                "sha256"      : item.get("sha256"),
                "start_line"  : item.get("start_line"),
                "total_lines" : item.get("total_lines")
            }
            for item in ok_context
            if item.get("path")
        ]
        next_steps: list[dict[str, typing.Any]] = [
            {
                "tool": "workspace_apply_unified_patch",
                "args": {
                    "patch": "",
                    "expected_sha256": sha_map,
                    "force": False
                },
                "status": "model_required",
                "reason": "fill patch with the smallest unified diff that fixes the verification failure"
            }
        ]
        if verify_command:
            next_steps.append({
                "tool": "shell_exec",
                "args": {
                    "command": verify_command,
                    "cwd": ".",
                    "timeout_sec": 300
                },
                "status": "ready",
                "reason": "rerun the failing verification after patching"
            })
        return {
            "ok": False,
            "status": "model_required",
            "session_id": session_id,
            "source_run_id": run_id,
            "prompt": prompt,
            "goal": "repair_failed_verification",
            "framework": diagnostics.get("framework"),
            "error_type": diagnostics.get("error_type"),
            "verify_command": verify_command,
            "affected_files": affected_files,
            "expected_sha256": sha_map,
            "read_recommendations": diagnostics.get("read_recommendations") or [],
            "context_count": len(ok_context),
            "repair_prompt": diagnostics.get("repair_prompt") or self._build_repair_prompt(verify_data, diagnostics),
            "next_steps": next_steps,
            "model_request": {
                "input": diagnostics.get("repair_prompt") or "",
                "expected_output": "native coding steps containing workspace_apply_unified_patch and verification rerun",
                "execute_after_model": False
            }
        }

    def _extract_output_references(
        self,
        output: str
    ) -> list[dict[str, typing.Any]]:
        references: list[dict[str, typing.Any]] = []
        seen: set[tuple[str, int]] = set()
        patterns = [
            {
                "source": "python_traceback",
                "regex": re.compile(r'File "([^"]+)", line (\d+)'),
                "path_group": 1,
                "line_group": 2
            },
            {
                "source": "typescript_paren",
                "regex": re.compile(
                    r"(?m)(^|[\s(])([A-Za-z0-9_./\\-]+"
                    r"\.(?:ts|tsx|js|jsx|vue|svelte))\((\d+),(\d+)\):"
                    r"(?:\s*(error|warning)\s+([A-Z]+[0-9]+):)?\s*(.*)$"
                ),
                "path_group": 2,
                "line_group": 3,
                "column_group": 4,
                "severity_group": 5,
                "code_group": 6,
                "message_group": 7
            },
            {
                "source": "rust_arrow",
                "regex": re.compile(
                    r"(?m)-->\s+([A-Za-z0-9_./\\-]+\.rs):(\d+):(\d+)"
                ),
                "path_group": 1,
                "line_group": 2,
                "column_group": 3
            },
            {
                "source": "stack_trace",
                "regex": re.compile(
                    r"(?m)(^|[\s(])([A-Za-z0-9_./\\-]+"
                    r"\.(?:js|jsx|ts|tsx|py|go|rs)):(\d+):(\d+)\)"
                ),
                "path_group": 2,
                "line_group": 3,
                "column_group": 4
            },
            {
                "source": "lint_code",
                "regex": re.compile(
                    r"(?m)(^|[\s(])([A-Za-z0-9_./\\-]+"
                    r"\.(?:py|pyi|js|ts|tsx|jsx)):(\d+):(\d+):\s*"
                    r"([A-Z]+[0-9]+|[A-Z][0-9]{3}|[A-Z]+[A-Z0-9_]*)\s+(.*)$"
                ),
                "path_group": 2,
                "line_group": 3,
                "column_group": 4,
                "code_group": 5,
                "message_group": 6
            },
            {
                "source": "path_line",
                "regex": re.compile(
                    r"(?m)(^|[\s(])([A-Za-z0-9_./\\-]+"
                    r"\.(?:py|pyi|js|ts|tsx|jsx|go|rs|json|toml|yaml|yml|md|txt|css|html))"
                    r":(\d+)(?::(\d+))?(?::\s*([A-Z]+[0-9]+|[A-Z][0-9]{3}|[A-Z]+[A-Z0-9_]*))?"
                    r"(?::\s*(.*))?$"
                ),
                "path_group": 2,
                "line_group": 3,
                "column_group": 4,
                "code_group": 5,
                "message_group": 6
            },
        ]

        for pattern in patterns:

            regex  = pattern["regex"]
            source = str(pattern["source"])

            for match in regex.finditer(output or ""):

                raw_path = match.group(int(pattern["path_group"]))
                raw_line = match.group(int(pattern["line_group"]))

                normalized = self._normalize_output_path(raw_path)
                if not normalized:
                    continue

                line = max(1, int(raw_line))

                key = (normalized, line)
                if key in seen:
                    continue
                seen.add(key)

                reference: dict[str, typing.Any] = {
                    "path"   : normalized,
                    "line"   : line,
                    "source" : source
                }

                column_group = pattern.get("column_group")
                if column_group and match.group(int(column_group)):
                    reference["column"] = max(1, int(match.group(int(column_group))))
                severity_group = pattern.get("severity_group")
                if severity_group and match.group(int(severity_group)):
                    reference["severity"] = str(match.group(int(severity_group))).lower()
                code_group = pattern.get("code_group")
                if code_group and match.group(int(code_group)):
                    reference["code"] = str(match.group(int(code_group))).strip()
                message_group = pattern.get("message_group")
                if message_group and match.group(int(message_group)):
                    reference["message"] = clip_text(str(match.group(int(message_group))).strip(), limit=500)
                references.append(reference)
                if len(references) >= 20:
                    return references
        return references

    def _normalize_output_path(
        self,
        raw_path: str
    ) -> str | None:
        raw = str(raw_path or "").strip()
        if raw.startswith("./"):
            raw = raw[2:]
        if not raw or raw.startswith("<"):
            return None
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = self.root / raw
        try:
            resolved = candidate.resolve()
        except OSError:
            return None
        if resolved != self.root and self.root not in resolved.parents:
            return None
        if not resolved.exists() or not resolved.is_file():
            return None
        if not self._looks_text(resolved):
            return None
        return self._rel(resolved)

    @staticmethod
    def _detect_verify_framework(
        data: dict[str, typing.Any],
        output: str
    ) -> str:
        command = data.get("command")
        command_text = " ".join(str(item).lower() for item in command) if isinstance(command, list) else ""
        text = (output or "").lower()
        if "pytest" in command_text or "pytest" in text or "short test summary info" in text:
            return "pytest"
        if "vitest" in command_text or "vitest" in text:
            return "vitest"
        if "jest" in command_text or "jest" in text:
            return "jest"
        if "tsc" in command_text or "typescript" in text or re.search(r"\bts\d{4}\b", text):
            return "tsc"
        if "cargo test" in command_text or "cargo check" in command_text or "rustc" in text or re.search(r"error\[e\d+]", text):
            return "cargo"
        if command_text.startswith("go test") or "go test" in command_text or "panic:" in text:
            return "go_test"
        if "ruff" in command_text or re.search(r"\b[a-z]\d{3}\b", text):
            return "ruff"
        if "mypy" in command_text or "mypy" in text:
            return "mypy"
        if "python" in command_text:
            return "python"
        return "generic"

    @staticmethod
    def _classify_verify_error(
        data: dict[str, typing.Any],
        output: str,
        *,
        framework: str = "generic"
    ) -> str:
        text = output or ""
        if data.get("timed_out"):
            return "timeout"
        if framework in {"pytest", "jest", "vitest", "go_test", "cargo"}:
            return f"{framework}_failure"
        if framework in {"tsc", "ruff", "mypy"}:
            return f"{framework}_error"
        if "SyntaxError" in text or "IndentationError" in text:
            return "syntax_error"
        if "ModuleNotFoundError" in text or "ImportError" in text:
            return "import_error"
        if "AssertionError" in text:
            return "assertion_error"
        if "Traceback (most recent call last)" in text:
            return "runtime_error"
        if "FAILED" in text or " failed" in text.lower():
            return "test_failure"
        return "command_failed"

    @staticmethod
    def _tail_output(
        output: str,
        *,
        max_chars: int
    ) -> str:
        text = str(output or "")
        if len(text) <= max_chars:
            return text
        return f"...[truncated {len(text) - max_chars} chars]\n{text[-max_chars:]}"

    @staticmethod
    def _numbered_content(
        content: str,
        *,
        start_line: int
    ) -> str:
        lines = str(content or "").splitlines()
        width = max(4, len(str(start_line + len(lines))))
        return "\n".join(
            f"{line_no:>{width}} | {line}"
            for line_no, line in enumerate(lines, start=start_line)
        )


if __name__ == '__main__':
    pass
