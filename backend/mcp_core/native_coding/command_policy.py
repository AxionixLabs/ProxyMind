# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from pathlib import Path
from backend.mcp_core.native_coding.base import NativeCodingComponent


class CommandPolicy(NativeCodingComponent):
    """shell 执行与 native loop 预检共用的命令策略。"""

    MAX_TIMEOUT_SEC        = 600
    LONG_TASK_TIMEOUT_SEC  = 300
    DEFAULT_OUTPUT_LIMIT   = 24000
    LONG_TASK_OUTPUT_LIMIT = 12000

    MATERIALIZATION_MAX_FILES               = 80
    MATERIALIZATION_MAX_BYTES               = 256_000
    MATERIALIZATION_MAX_FILE_BYTES          = 32_000
    MATERIALIZATION_MANIFEST_MAX_FILES      = 5000
    MATERIALIZATION_MANIFEST_HASH_MAX_BYTES = 2_000_000

    READ_COMMANDS = {
        "cat", "head", "tail", "ls", "pwd", "find", "grep", "rg", "wc",
        "sed", "awk", "rustc",
        "pytest", "ruff", "mypy", "tsc", "jest", "vitest"
    }
    WRITE_COMMANDS = {
        "touch", "tee", "cp", "mv", "mkdir", "install", "patch",
        "truncate", "dd"
    }
    PACKAGE_COMMANDS = {"pip", "pip3", "npm", "pnpm", "yarn", "bun", "cargo", "go"}
    NETWORK_COMMANDS = {"curl", "wget"}

    def check_command_policy(
        self,
        cmd: list[str],
        *,
        cwd: str = ".",
        timeout_sec: int = 60,
        allow_review: bool = False,
        allow_dangerous: bool = False
    ) -> dict[str, typing.Any]:
        command = [str(item) for item in (cmd or []) if str(item or "").strip()]
        if not command:
            return self._deny("command_empty", risk="blocked", execution_target="blocked")

        timeout = max(1, int(timeout_sec or 60))
        if timeout > self.MAX_TIMEOUT_SEC and not allow_review:
            return self._deny(
                "timeout_requires_approval",
                risk="approval",
                reasons=[f"timeout_sec:{timeout}", f"max_without_approval:{self.MAX_TIMEOUT_SEC}"],
                approval_required=True,
                execution_target="approval_required"
            )
        timeout = min(timeout, self.MAX_TIMEOUT_SEC)
        long_task = timeout >= self.LONG_TASK_TIMEOUT_SEC
        output_limit = self.LONG_TASK_OUTPUT_LIMIT if long_task else self.DEFAULT_OUTPUT_LIMIT

        try:
            workdir = self._resolve(cwd)
        except Exception as exc:
            return self._deny(
                "cwd_invalid",
                risk="blocked",
                reasons=[f"{type(exc).__name__}: {exc}"],
                execution_target="blocked"
            )

        project_types = self.detect_project_types(workdir)
        joined = " ".join(command)
        if any(op in joined for op in self.CONTROL_OPERATORS):
            return self._deny(
                "shell_control_operator_forbidden",
                risk="blocked",
                reasons=["shell_control_operator"],
                execution_target="blocked"
            )

        head = Path(command[0]).name.lower()
        if head in self.DANGEROUS_COMMANDS:
            if not allow_dangerous:
                return self._deny(
                    "dangerous_command_forbidden",
                    risk="blocked",
                    reasons=[f"dangerous_command:{head}"],
                    execution_target="blocked"
                )
            return self._allow(
                risk="dangerous",
                category="dangerous",
                reasons=[f"dangerous_command:{head}", "allow_dangerous"],
                project_types=project_types,
                timeout_sec=timeout,
                output_limit=output_limit,
                long_task=long_task,
                execution_target="cloud_sandbox",
                requires_cloud_sandbox=True,
                sandbox_request=self._sandbox_request(
                    command,
                    cwd=workdir,
                    timeout_sec=timeout,
                    reasons=[f"dangerous_command:{head}"],
                    network_required=False,
                    writable_paths=["."]
                )
            )

        approval_reason = self._approval_reason(command)
        if approval_reason:
            if not allow_review:
                return self._deny(
                    "approval_required",
                    risk="approval",
                    category="approval",
                    reasons=[approval_reason],
                    approval_required=True,
                    project_types=project_types,
                    timeout_sec=timeout,
                    output_limit=output_limit,
                    long_task=long_task,
                    execution_target="approval_required",
                    requires_cloud_sandbox=True,
                    sandbox_request=self._sandbox_request(
                        command,
                        cwd=workdir,
                        timeout_sec=timeout,
                        reasons=[approval_reason],
                        network_required=self._network_required(command, approval_reason),
                        writable_paths=["."]
                    )
                )
            return self._allow(
                risk="approval",
                category="approval",
                reasons=[approval_reason, "allow_review"],
                approval_required=False,
                project_types=project_types,
                timeout_sec=timeout,
                output_limit=output_limit,
                long_task=long_task,
                execution_target="cloud_sandbox",
                requires_cloud_sandbox=True,
                sandbox_request=self._sandbox_request(
                    command,
                    cwd=workdir,
                    timeout_sec=timeout,
                    reasons=[approval_reason, "allow_review"],
                    network_required=self._network_required(command, approval_reason),
                    writable_paths=["."]
                )
            )

        write_reason = self._write_reason(command)
        if write_reason:
            return self._deny(
                "shell_write_forbidden",
                risk="blocked",
                category="write",
                reasons=[write_reason, "use_workspace_write_or_patch_tools"],
                execution_target="blocked"
            )

        if self._is_test_command(command, project_types):
            return self._allow(
                risk="test",
                category="test",
                reasons=["project_test_command"],
                project_types=project_types,
                timeout_sec=timeout,
                output_limit=output_limit,
                long_task=long_task,
                execution_target="cloud_sandbox" if long_task else "local",
                requires_cloud_sandbox=bool(long_task),
                sandbox_request=self._sandbox_request(
                    command,
                    cwd=workdir,
                    timeout_sec=timeout,
                    reasons=["long_test_command"],
                    network_required=False,
                    writable_paths=["."]
                ) if long_task else None
            )

        category = "read" if self._is_read_command(command) else "safe"
        return self._allow(
            risk=category,
            category=category,
            reasons=(["long_task"] if long_task else []),
            project_types=project_types,
            timeout_sec=timeout,
            output_limit=output_limit,
            long_task=long_task,
            execution_target="cloud_sandbox" if long_task else "local",
            requires_cloud_sandbox=bool(long_task),
            sandbox_request=self._sandbox_request(
                command,
                cwd=workdir,
                timeout_sec=timeout,
                reasons=["long_task"],
                network_required=False,
                writable_paths=["."]
            ) if long_task else None
        )

    def detect_project_types(self, cwd: Path | None = None) -> list[str]:
        base = cwd or self.root
        markers = {
            "python": ["pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "Pipfile"],
            "node": ["package.json", "pnpm-lock.yaml", "yarn.lock", "package-lock.json", "bun.lockb"],
            "go": ["go.mod"],
            "rust": ["Cargo.toml"],
            "java": ["pom.xml", "build.gradle", "build.gradle.kts"]
        }
        found: list[str] = []
        for project_type, names in markers.items():
            if any((base / name).exists() or (self.root / name).exists() for name in names):
                found.append(project_type)
        return found

    def _approval_reason(self, cmd: list[str]) -> str | None:
        head = Path(cmd[0]).name.lower()
        args = [str(item).lower() for item in cmd[1:]]

        if head in self.NETWORK_COMMANDS:
            return f"network_command:{head}"

        if head in {"pip", "pip3"} and args[:1] in (["install"], ["uninstall"]):
            return f"dependency_change:{head}:{args[0]}"
        if head in {"python", "python3"} and len(args) >= 3 and args[0:2] == ["-m", "pip"] and args[2] in {"install", "uninstall"}:
            return f"dependency_change:python_m_pip:{args[2]}"
        if head in {"npm", "pnpm"} and args and args[0] in {"install", "i", "add", "remove", "uninstall", "update"}:
            return f"dependency_change:{head}:{args[0]}"
        if head == "yarn" and (not args or args[0] in {"add", "remove", "install", "upgrade"}):
            return f"dependency_change:yarn:{args[0] if args else 'install'}"
        if head == "bun" and args and args[0] in {"add", "remove", "install", "update"}:
            return f"dependency_change:bun:{args[0]}"
        if head == "cargo" and args and args[0] in {"add", "remove", "update", "install"}:
            return f"dependency_change:cargo:{args[0]}"
        if head == "go" and args and args[0] in {"get", "install"}:
            return f"dependency_change:go:{args[0]}"
        if head == "git" and len(args) >= 1 and args[0] in self.REVIEW_GIT_SUBCOMMANDS:
            return f"git_write_subcommand:{args[0]}"
        return None

    def _write_reason(self, cmd: list[str]) -> str | None:
        head = Path(cmd[0]).name.lower()
        args = [str(item).lower() for item in cmd[1:]]
        if head in self.WRITE_COMMANDS:
            return f"write_command:{head}"
        if head == "sed" and any(item.startswith("-i") for item in args):
            return "write_command:sed_in_place"
        if head == "perl" and any(item.startswith("-pi") or item.startswith("-i") for item in args):
            return "write_command:perl_in_place"
        return None

    @staticmethod
    def _is_test_command(cmd: list[str], project_types: list[str]) -> bool:
        head = Path(cmd[0]).name.lower()
        args = [str(item).lower() for item in cmd[1:]]
        projects = set(project_types)
        if head == "pytest" or (head in {"python", "python3"} and args[:2] == ["-m", "pytest"]):
            return not projects or "python" in projects
        if head in {"python", "python3"} and args[:2] == ["-m", "unittest"]:
            return not projects or "python" in projects
        if head in {"ruff", "mypy"}:
            return not projects or "python" in projects
        if head in {"npm", "pnpm", "yarn", "bun"} and args and args[0] in {"test", "run"}:
            if args[0] == "run" and len(args) > 1 and args[1] not in {"test", "lint", "typecheck"}:
                return False
            return not projects or "node" in projects
        if head in {"jest", "vitest"}:
            return not projects or "node" in projects
        if head == "tsc" and "--noemit" in args:
            return not projects or "node" in projects
        if head == "go" and args[:1] == ["test"]:
            return not projects or "go" in projects
        if head == "cargo" and args[:1] in (["test"], ["check"]):
            return not projects or "rust" in projects
        return False

    def _is_read_command(self, cmd: list[str]) -> bool:
        head = Path(cmd[0]).name.lower()
        args = [str(item).lower() for item in cmd[1:]]
        if head == "git":
            return bool(args and args[0] in {"status", "diff", "log", "show", "rev-parse", "branch"})
        if head in {"pip", "pip3", "npm", "pnpm", "yarn", "bun", "cargo", "go"}:
            return bool(args and args[0] in {"--version", "-v", "version", "list", "info"})
        return head in self.READ_COMMANDS

    def _sandbox_request(
        self,
        cmd: list[str],
        *,
        cwd: Path,
        timeout_sec: int,
        reasons: list[str],
        network_required: bool,
        writable_paths: list[str]
    ) -> dict[str, typing.Any]:
        cwd_rel = self._rel(cwd)
        materialization = self._workspace_materialization(cwd)
        inline = materialization.get("inline") or {}
        workspace_archive = materialization.get("workspace_archive") or {}
        repo_ref = materialization.get("repo_ref") or {}
        return {
            "protocol_version": 1,
            "request_kind": "command",
            "runtime": "native_coding",
            "command": list(cmd),
            "cwd": cwd_rel,
            "timeout_sec": int(timeout_sec),
            "network_required": bool(network_required),
            "writable_paths": list(writable_paths),
            "workspace": {
                "kind": "local_workspace",
                "root": str(self.root),
                "cwd": cwd_rel,
                "materialization_required": True,
                "materialization": {
                    "strategy": materialization.get("strategy") or "cloud_runtime_attach_workspace",
                    "accepted_refs": [
                        "existing_sandbox_workspace_id",
                        "workspace_archive",
                        "repo_ref",
                        "inline_files"
                    ],
                    "preferred_refs": materialization.get("preferred_refs") or [],
                    "inline": inline,
                    "manifest": materialization.get("manifest") or {},
                    "workspace_archive": workspace_archive,
                    "repo_ref": repo_ref,
                    "note": "command/cwd is not enough; cloud must materialize the same workspace before execution"
                }
            },
            "project_types": self.detect_project_types(cwd),
            "reasons": list(reasons)
        }

    def _workspace_materialization(self, cwd: Path) -> dict[str, typing.Any]:
        inline_files: list[dict[str, typing.Any]] = []
        inline_total_bytes = 0
        inline_omitted_count = 0
        inline_truncated = False
        manifest_files: list[dict[str, typing.Any]] = []
        manifest_total_bytes = 0
        manifest_omitted_count = 0
        manifest_truncated = False
        text_file_count = 0
        binary_file_count = 0
        base = self.root
        for path in self._walk(base, recursive=True):
            if not path.is_file():
                continue
            rel = self._rel(path)
            try:
                size = path.stat().st_size
            except OSError:
                inline_omitted_count += 1
                manifest_omitted_count += 1
                continue

            looks_text = self._looks_text(path)
            if looks_text:
                text_file_count += 1
            else:
                binary_file_count += 1
            manifest_total_bytes += size
            if len(manifest_files) >= self.MATERIALIZATION_MANIFEST_MAX_FILES:
                manifest_omitted_count += 1
                manifest_truncated = True
            else:
                item: dict[str, typing.Any] = {
                    "path": rel,
                    "bytes": size,
                    "text": looks_text
                }
                if size <= self.MATERIALIZATION_MANIFEST_HASH_MAX_BYTES:
                    try:
                        item["sha256"] = self._sha256(path.read_bytes())
                    except OSError:
                        item["sha256_error"] = "read_failed"
                else:
                    item["sha256_omitted"] = "file_too_large"
                manifest_files.append(item)

            if not looks_text:
                inline_omitted_count += 1
                continue
            if size > self.MATERIALIZATION_MAX_FILE_BYTES:
                inline_omitted_count += 1
                continue
            if len(inline_files) >= self.MATERIALIZATION_MAX_FILES:
                inline_omitted_count += 1
                inline_truncated = True
                continue
            if inline_total_bytes + size > self.MATERIALIZATION_MAX_BYTES:
                inline_omitted_count += 1
                inline_truncated = True
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                try:
                    content = path.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    inline_omitted_count += 1
                    continue
            except OSError:
                inline_omitted_count += 1
                continue
            encoded_size = len(content.encode("utf-8"))
            inline_total_bytes += encoded_size
            inline_files.append({
                "path": rel,
                "content": content,
                "sha256": self._sha256(content.encode("utf-8")),
                "bytes": encoded_size
            })

        inline_complete = not inline_truncated and inline_omitted_count == 0
        archive_required = not inline_complete
        repo_ref = self._workspace_repo_ref()
        preferred_refs = ["inline_files"] if inline_complete else [
            "existing_sandbox_workspace_id",
            "workspace_archive",
            "repo_ref",
            "inline_files"
        ]
        return {
            "base": self._rel(cwd),
            "strategy": "inline_files" if inline_complete else "workspace_archive_or_repo_ref",
            "preferred_refs": preferred_refs,
            "inline": {
                "kind": "inline_files",
                "files": inline_files,
                "file_count": len(inline_files),
                "total_bytes": inline_total_bytes,
                "truncated": inline_truncated,
                "omitted_count": inline_omitted_count,
                "complete": inline_complete,
                "max_files": self.MATERIALIZATION_MAX_FILES,
                "max_bytes": self.MATERIALIZATION_MAX_BYTES,
                "max_file_bytes": self.MATERIALIZATION_MAX_FILE_BYTES
            },
            "manifest": {
                "kind": "workspace_manifest",
                "base": self._rel(cwd),
                "files": manifest_files,
                "file_count": len(manifest_files),
                "total_bytes": manifest_total_bytes,
                "text_file_count": text_file_count,
                "binary_file_count": binary_file_count,
                "truncated": manifest_truncated,
                "omitted_count": manifest_omitted_count,
                "max_files": self.MATERIALIZATION_MANIFEST_MAX_FILES,
                "fingerprint": self._manifest_fingerprint(manifest_files)
            },
            "workspace_archive": {
                "kind": "workspace_archive",
                "required": archive_required,
                "available": False,
                "transport": "client_upload_required",
                "format": "tar.gz",
                "root": str(self.root),
                "cwd": self._rel(cwd),
                "exclude_dirs": sorted(self.DEFAULT_EXCLUDES),
                "file_count": len(manifest_files),
                "total_bytes": manifest_total_bytes,
                "reason": "inline_materialization_incomplete" if archive_required else "inline_materialization_complete"
            },
            "repo_ref": repo_ref
        }

    def _workspace_repo_ref(self) -> dict[str, typing.Any]:
        git_dir = self.root / ".git"
        if not git_dir.exists():
            return {
                "kind": "repo_ref",
                "available": False,
                "reason": "not_git_workspace"
            }
        head_path = git_dir / "HEAD"
        try:
            head_text = head_path.read_text(encoding="utf-8").strip()
        except OSError:
            return {
                "kind": "repo_ref",
                "available": False,
                "reason": "head_unreadable"
            }
        branch: str | None = None
        commit: str | None = None
        if head_text.startswith("ref: "):
            ref = head_text[5:].strip()
            branch = ref.rsplit("/", 1)[-1] if ref else None
            ref_path = git_dir / ref
            try:
                commit = ref_path.read_text(encoding="utf-8").strip()
            except OSError:
                commit = None
        elif head_text:
            commit = head_text
        return {
            "kind": "repo_ref",
            "available": bool(commit),
            "root": str(self.root),
            "branch": branch,
            "commit": commit,
            "requires_server_repo_access": True,
            "note": "repo_ref is usable only when cloud runtime can fetch or already has this repository"
        }

    def _manifest_fingerprint(self, files: list[dict[str, typing.Any]]) -> str:
        rows = []
        for item in files:
            rows.append(
                "|".join([
                    str(item.get("path") or ""),
                    str(item.get("bytes") or 0),
                    str(item.get("sha256") or "")
                ])
            )
        return self._sha256("\n".join(rows).encode("utf-8"))

    @staticmethod
    def _network_required(cmd: list[str], reason: str | None = None) -> bool:
        head = Path(cmd[0]).name.lower() if cmd else ""
        if head in CommandPolicy.NETWORK_COMMANDS:
            return True
        text = str(reason or "")
        return text.startswith("dependency_change:") or text.startswith("network_command:")

    @staticmethod
    def _allow(**data: typing.Any) -> dict[str, typing.Any]:
        payload: dict[str, typing.Any] = {"ok": True, **data}
        payload.setdefault("execution_target", "local")
        payload.setdefault("requires_cloud_sandbox", False)
        return payload

    @staticmethod
    def _deny(reason: str, *, risk: str, **data: typing.Any) -> dict[str, typing.Any]:
        payload: dict[str, typing.Any] = {"ok": False, "reason": reason, "risk": risk, **data}
        payload.setdefault("execution_target", "blocked")
        payload.setdefault("requires_cloud_sandbox", False)
        return payload


if __name__ == '__main__':
    pass
