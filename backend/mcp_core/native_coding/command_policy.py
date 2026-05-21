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
        "truncate", "dd", "copy", "copy.exe", "xcopy", "xcopy.exe",
        "robocopy", "robocopy.exe", "move", "move.exe",
        "set-content", "add-content", "new-item", "copy-item",
        "move-item", "rename-item"
    }

    PACKAGE_COMMANDS  = {"pip", "pip3", "npm", "pnpm", "yarn", "bun", "cargo", "go"}
    NETWORK_COMMANDS  = {"curl", "wget"}
    SHELL_WRAPPERS    = {"cmd", "cmd.exe", "powershell", "powershell.exe", "pwsh", "pwsh.exe", "sh", "bash", "zsh"}
    INLINE_CODE_FLAGS = {"-c", "-command", "/c"}
    FILE_DELETE_COMMANDS = {
        "rm", "rmdir", "del", "erase", "remove-item", "ri", "rd"
    }
    LOCAL_DELETE_DIR_NAMES = {
        "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
        "htmlcov", "dist", "build"
    }
    LOCAL_DELETE_FILE_NAMES = {
        ".coverage"
    }
    LOCAL_DELETE_SUFFIXES = {
        ".pyc"
    }

    PYTHON_HEADS = {"python", "python.exe", "python3", "python3.exe", "py", "py.exe"}
    PIP_HEADS    = {"pip", "pip.exe", "pip3", "pip3.exe"}
    NODE_HEADS   = {"node", "node.exe"}
    NPM_HEADS    = {"npm", "npm.cmd", "npm.exe", "pnpm", "pnpm.cmd", "pnpm.exe"}
    YARN_HEADS   = {"yarn", "yarn.cmd", "yarn.exe"}
    BUN_HEADS    = {"bun", "bun.exe", "bun.cmd"}
    GIT_HEADS    = {"git", "git.exe", "git.cmd"}
    CARGO_HEADS  = {"cargo", "cargo.exe"}

    GO_HEADS = {"go", "go.exe"}
    NODE_RUNNERS = {
        "npx", "npx.cmd", "npx.exe", "tsx", "tsx.cmd", "tsx.exe",
        "deno", "deno.exe", "bunx", "bunx.exe", "bunx.cmd"
    }

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
        control_reason = self._control_operator_reason(command)
        if control_reason:
            return self._deny(
                "shell_control_operator_forbidden",
                risk="blocked",
                reasons=[control_reason],
                execution_target="blocked"
            )

        head = Path(command[0]).name.lower()
        if self._env_assignment_reason(command):
            return self._deny(
                "env_assignment_forbidden",
                risk="blocked",
                category="env_assignment",
                reasons=["pass_environment_via_explicit_tool_option"],
                execution_target="blocked"
            )
        wrapper_reason = self._shell_wrapper_reason(command)
        if wrapper_reason:
            if not allow_review:
                return self._deny(
                    "shell_wrapper_forbidden",
                    risk="approval",
                    category="shell_wrapper",
                    reasons=[wrapper_reason, "approval_required_for_shell_wrapper"],
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
                        reasons=[wrapper_reason, "approval_required_for_shell_wrapper"],
                        network_required=self._network_required(command, wrapper_reason),
                        writable_paths=["."]
                    )
                )
            return self._allow(
                risk="approval",
                category="shell_wrapper",
                reasons=[wrapper_reason, "allow_review"],
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
                    reasons=[wrapper_reason, "allow_review"],
                    network_required=self._network_required(command, wrapper_reason),
                    writable_paths=["."]
                )
            )
        inline_reason = self._inline_code_reason(command)
        if inline_reason:
            if not allow_review:
                return self._deny(
                    "inline_code_forbidden",
                    risk="approval",
                    category="inline_code",
                    reasons=[inline_reason, "approval_required_for_inline_code"],
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
                        reasons=[inline_reason, "approval_required_for_inline_code"],
                        network_required=self._network_required(command, inline_reason),
                        writable_paths=["."]
                    )
                )
            return self._allow(
                risk="approval",
                category="inline_code",
                reasons=[inline_reason, "allow_review"],
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
                    reasons=[inline_reason, "allow_review"],
                    network_required=self._network_required(command, inline_reason),
                    writable_paths=["."]
                )
            )

        if head in self.DANGEROUS_COMMANDS:
            if not allow_dangerous:
                return self._deny(
                    "dangerous_command_forbidden",
                    risk="approval",
                    category="dangerous",
                    reasons=[f"dangerous_command:{head}", "approval_required_for_dangerous_command"],
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
                        reasons=[f"dangerous_command:{head}", "approval_required_for_dangerous_command"],
                        network_required=False,
                        writable_paths=["."]
                    )
                )
            if head in self.FILE_DELETE_COMMANDS and self._local_delete_allowed(command, workdir):
                return self._allow(
                    risk="dangerous",
                    category="dangerous",
                    reasons=[f"dangerous_command:{head}", "allow_dangerous", "local_file_delete"],
                    project_types=project_types,
                    timeout_sec=timeout,
                    output_limit=output_limit,
                    long_task=long_task,
                    execution_target="local",
                    requires_cloud_sandbox=False
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
            if not allow_review:
                return self._deny(
                    "shell_write_forbidden",
                    risk="approval",
                    category="write",
                    reasons=[write_reason, "approval_required_for_shell_write"],
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
                        reasons=[write_reason, "approval_required_for_shell_write"],
                        network_required=False,
                        writable_paths=["."]
                    )
                )
            return self._allow(
                risk="approval",
                category="write",
                reasons=[write_reason, "allow_review"],
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
                    reasons=[write_reason, "allow_review"],
                    network_required=False,
                    writable_paths=["."]
                )
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
        effective_args = self._effective_subcommand_args(head, args)

        if head in self.NETWORK_COMMANDS:
            return f"network_command:{head}"

        if head in self.PIP_HEADS and effective_args[:1] in (["install"], ["uninstall"]):
            return f"dependency_change:{head}:{effective_args[0]}"
        if head in self.PYTHON_HEADS and len(args) >= 3 and args[0:2] == ["-m", "pip"] and args[2] in {"install", "uninstall"}:
            return f"dependency_change:python_m_pip:{args[2]}"
        if head in self.PYTHON_HEADS and len(args) >= 2 and args[0:2] == ["-m", "ensurepip"]:
            return "dependency_change:python_m_ensurepip"
        if head in self.PYTHON_HEADS and len(args) >= 2 and args[0] == "-m" and args[1] in {"pipenv", "poetry"}:
            return f"script_runner:python_m_{args[1]}"
        if head in self.NPM_HEADS and effective_args and effective_args[0] in {"install", "i", "add", "remove", "uninstall", "update"}:
            return f"dependency_change:{head}:{effective_args[0]}"
        if head in self.NPM_HEADS and effective_args and effective_args[0] in {"exec", "dlx", "create"}:
            return f"script_runner:{head}:{effective_args[0]}"
        if head in self.NPM_HEADS and effective_args and effective_args[0] == "run":
            script = effective_args[1] if len(effective_args) > 1 else ""
            if script not in {"test", "lint", "typecheck"}:
                return f"package_script:{head}:{script or 'missing'}"
        if head in self.YARN_HEADS and (not effective_args or effective_args[0] in {"add", "remove", "install", "upgrade"}):
            return f"dependency_change:yarn:{effective_args[0] if effective_args else 'install'}"
        if head in self.YARN_HEADS and effective_args and effective_args[0] in {"exec", "dlx", "create"}:
            return f"script_runner:{head}:{effective_args[0]}"
        if head in self.YARN_HEADS and effective_args and effective_args[0] == "run":
            script = effective_args[1] if len(effective_args) > 1 else ""
            if script not in {"test", "lint", "typecheck"}:
                return f"package_script:yarn:{script or 'missing'}"
        if head in self.BUN_HEADS and effective_args and effective_args[0] in {"add", "remove", "install", "update"}:
            return f"dependency_change:bun:{effective_args[0]}"
        if head in self.BUN_HEADS and effective_args and effective_args[0] in {"x", "create"}:
            return f"script_runner:{head}:{effective_args[0]}"
        if head in self.BUN_HEADS and effective_args and effective_args[0] == "run":
            script = effective_args[1] if len(effective_args) > 1 else ""
            if script not in {"test", "lint", "typecheck"}:
                return f"package_script:bun:{script or 'missing'}"
        if head in self.NODE_RUNNERS:
            return f"script_runner:{head}"
        if head in self.CARGO_HEADS and effective_args and effective_args[0] in {"add", "remove", "update", "install"}:
            return f"dependency_change:cargo:{effective_args[0]}"
        if head in self.GO_HEADS and effective_args and effective_args[0] in {"get", "install"}:
            return f"dependency_change:go:{effective_args[0]}"
        if head in self.GIT_HEADS and len(effective_args) >= 1 and effective_args[0] in self.REVIEW_GIT_SUBCOMMANDS:
            return f"git_write_subcommand:{effective_args[0]}"
        return None

    def _shell_wrapper_reason(self, cmd: list[str]) -> str | None:
        head = Path(cmd[0]).name.lower()
        if head not in self.SHELL_WRAPPERS:
            return None
        args = [str(item).lower() for item in cmd[1:]]
        if head in {"sh", "bash", "zsh"} and not any(item in {"-c", "-lc"} for item in args):
            return None
        return f"shell_wrapper:{head}"

    def _inline_code_reason(self, cmd: list[str]) -> str | None:
        head = Path(cmd[0]).name.lower()
        args = [str(item).lower() for item in cmd[1:]]
        if head in self.PYTHON_HEADS and any(item == "-c" for item in args):
            return f"inline_code:{head}:-c"
        if head in (self.NODE_HEADS | {"deno", "deno.exe"}) and any(item in {"-e", "--eval", "eval"} for item in args):
            return f"inline_code:{head}:eval"
        return None

    @staticmethod
    def _env_assignment_reason(cmd: list[str]) -> str | None:
        head = str(cmd[0] or "")
        if "=" not in head:
            return None
        name, value = head.split("=", 1)
        if not name or not value:
            return None
        if all(ch.isalnum() or ch == "_" for ch in name):
            return f"env_assignment:{name}"
        return None

    def _write_reason(self, cmd: list[str]) -> str | None:
        head = Path(cmd[0]).name.lower()
        args = [str(item).lower() for item in cmd[1:]]
        if head in self.WRITE_COMMANDS:
            return f"write_command:{head}"
        if head in {"rsync", "rsync.exe"}:
            return f"write_command:{head}"
        if head in {"tar", "tar.exe"} and any(self._tar_extract_flag(item) for item in args):
            return "write_command:tar_extract"
        if head in {"unzip", "unzip.exe"} and not any(item in {"-l", "-t", "-v", "-z"} for item in args):
            return "write_command:unzip_extract"
        if head in {"7z", "7z.exe", "7za", "7za.exe"} and args and args[0] in {"x", "e", "a", "d", "rn"}:
            return f"write_command:7z_{args[0]}"
        if head == "sed" and any(item.startswith("-i") for item in args):
            return "write_command:sed_in_place"
        if head == "perl" and any(item.startswith("-pi") or item.startswith("-i") for item in args):
            return "write_command:perl_in_place"
        return None

    def _local_delete_allowed(self, cmd: list[str], workdir: Path) -> bool:
        """判断删除命令是否只作用于已审批可本地处理的工作区路径。"""
        if not cmd:
            return False
        head = Path(str(cmd[0])).name.lower()
        if head not in self.FILE_DELETE_COMMANDS:
            return False

        recursive = False
        targets: list[Path] = []
        for raw in cmd[1:]:
            item = str(raw or "").strip()
            if not item:
                continue
            lower = item.lower()
            if lower in {"-r", "-rf", "-fr", "--recursive", "/s", "-recurse"}:
                recursive = True
                continue
            if lower in {"-f", "--force", "/q", "-force"}:
                continue
            if lower.startswith("-") or "*" in item or "?" in item:
                return False
            target = Path(item)
            if not target.is_absolute():
                target = workdir / target
            try:
                resolved = target.resolve()
            except OSError:
                return False
            if resolved == self.root or (resolved != self.root and self.root not in resolved.parents):
                return False
            try:
                rel_parts = resolved.relative_to(self.root).parts
            except ValueError:
                return False
            if ".git" in rel_parts:
                return False
            targets.append(resolved)

        return bool(targets) and all(
            self._local_delete_target_allowed(target, recursive=recursive)
            for target in targets
        )

    def _local_delete_target_allowed(self, target: Path, *, recursive: bool = False) -> bool:
        name = target.name
        lower_name = name.lower()
        if lower_name in self.LOCAL_DELETE_DIR_NAMES:
            return True
        if name in self.LOCAL_DELETE_FILE_NAMES:
            return True
        if any(lower_name.endswith(suffix) for suffix in self.LOCAL_DELETE_SUFFIXES):
            return True
        if target.exists() and target.is_file() and not recursive:
            return True
        return False

    @staticmethod
    def _control_operator_reason(cmd: list[str]) -> str | None:
        control_tokens = {";", "&&", "||", "|", ">", ">>", "<"}
        for item in cmd:
            text = str(item or "")
            if text in control_tokens:
                return f"shell_control_operator:{text}"
            if text.startswith((">", ">>", "<", "1>", "2>", "&>")):
                return f"shell_control_operator:{text}"
            if "$(" in text:
                return "shell_control_operator:$("
            if "`" in text:
                return "shell_control_operator:`"
        return None

    @staticmethod
    def _tar_extract_flag(arg: str) -> bool:
        if arg in {"--extract", "--get"}:
            return True
        if not arg.startswith("-") or arg.startswith("--"):
            return False
        return "x" in arg.lstrip("-")

    @staticmethod
    def _is_test_command(cmd: list[str], project_types: list[str]) -> bool:
        head = Path(cmd[0]).name.lower()
        args = [str(item).lower() for item in cmd[1:]]
        effective_args = CommandPolicy._effective_subcommand_args(head, args)
        projects = set(project_types)
        if head == "pytest" or (head in CommandPolicy.PYTHON_HEADS and args[:2] == ["-m", "pytest"]):
            return not projects or "python" in projects
        if head in CommandPolicy.PYTHON_HEADS and args[:2] == ["-m", "unittest"]:
            return not projects or "python" in projects
        if head in {"ruff", "mypy"}:
            return not projects or "python" in projects
        if head in (CommandPolicy.NPM_HEADS | CommandPolicy.YARN_HEADS | CommandPolicy.BUN_HEADS) and effective_args and effective_args[0] in {"test", "run"}:
            if effective_args[0] == "run" and len(effective_args) > 1 and effective_args[1] not in {"test", "lint", "typecheck"}:
                return False
            return not projects or "node" in projects
        if head in {"jest", "vitest"}:
            return not projects or "node" in projects
        if head == "tsc" and "--noemit" in args:
            return not projects or "node" in projects
        if head in CommandPolicy.GO_HEADS and effective_args[:1] == ["test"]:
            return not projects or "go" in projects
        if head in CommandPolicy.CARGO_HEADS and effective_args[:1] in (["test"], ["check"]):
            return not projects or "rust" in projects
        return False

    def _is_read_command(self, cmd: list[str]) -> bool:
        head = Path(cmd[0]).name.lower()
        args = [str(item).lower() for item in cmd[1:]]
        effective_args = self._effective_subcommand_args(head, args)
        if head in self.GIT_HEADS:
            return bool(effective_args and effective_args[0] in {"status", "diff", "log", "show", "rev-parse", "branch"})
        if head in (self.PIP_HEADS | self.NPM_HEADS | self.YARN_HEADS | self.BUN_HEADS | self.CARGO_HEADS | self.GO_HEADS):
            return bool(effective_args and effective_args[0] in {"--version", "-v", "version", "list", "info"})
        return head in self.READ_COMMANDS

    @staticmethod
    def _effective_subcommand_args(head: str, args: list[str]) -> list[str]:
        if head in CommandPolicy.GIT_HEADS:
            return CommandPolicy._strip_leading_options(
                args,
                options_with_values={"-c", "--git-dir", "--work-tree", "--namespace", "--config-env"},
                options_with_inline_values=("--git-dir=", "--work-tree=", "--namespace=", "--config-env="),
                options_without_values={"--no-pager", "--paginate", "--bare", "--no-optional-locks"}
            )
        if head in CommandPolicy.YARN_HEADS:
            stripped = CommandPolicy._strip_leading_options(
                args,
                options_with_values={"--cwd", "--workspace", "-w"},
                options_with_inline_values=("--cwd=", "--workspace="),
                options_without_values={"--silent", "--workspaces", "--include-workspace-root"}
            )
            return CommandPolicy._normalize_yarn_workspace_args(stripped)
        if head in CommandPolicy.BUN_HEADS:
            return CommandPolicy._strip_leading_options(
                args,
                options_with_values={"--cwd", "--filter", "-F"},
                options_with_inline_values=("--cwd=", "--filter="),
                options_without_values={"--silent"}
            )
        if head in {"pnpm", "pnpm.cmd", "pnpm.exe"}:
            return CommandPolicy._strip_leading_options(
                args,
                options_with_values={"--dir", "-c", "--filter", "-f", "--workspace", "-w"},
                options_with_inline_values=("--dir=", "--filter=", "--workspace="),
                options_without_values={"--silent", "--workspace-root"}
            )
        if head in CommandPolicy.NPM_HEADS:
            return CommandPolicy._strip_leading_options(
                args,
                options_with_values={"--prefix", "--workspace", "-w"},
                options_with_inline_values=("--prefix=", "--workspace="),
                options_without_values={"--silent", "--workspaces", "--include-workspace-root"}
            )
        return args

    @staticmethod
    def _normalize_yarn_workspace_args(args: list[str]) -> list[str]:
        if len(args) >= 3 and args[0] == "workspace":
            return args[2:]
        if len(args) >= 4 and args[0] == "workspaces" and args[1] == "foreach":
            index = 2
            while index < len(args):
                item = args[index]
                if item == "--":
                    return args[index + 1:]
                if item in {"--from", "--include", "--exclude", "-a", "--all", "-r", "--recursive"}:
                    index += 2 if item in {"--from", "--include", "--exclude"} else 1
                    continue
                if item.startswith("--from=") or item.startswith("--include=") or item.startswith("--exclude="):
                    index += 1
                    continue
                break
            if index < len(args) and args[index] == "run":
                return args[index:]
        return args

    @staticmethod
    def _strip_leading_options(
        args: list[str],
        *,
        options_with_values: set[str],
        options_with_inline_values: tuple[str, ...],
        options_without_values: set[str]
    ) -> list[str]:
        index = 0
        while index < len(args):
            item = args[index]
            if item == "--":
                return args[index + 1:]
            if item in options_with_values:
                index += 2
                continue
            if any(item.startswith(prefix) for prefix in options_with_inline_values):
                index += 1
                continue
            if item in options_without_values:
                index += 1
                continue
            break
        return args[index:]

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
        cwd_rel           = self._rel(cwd)
        materialization   = self._workspace_materialization(cwd)
        inline            = materialization.get("inline") or {}
        workspace_archive = materialization.get("workspace_archive") or {}
        repo_ref          = materialization.get("repo_ref") or {}
        outside_sandbox_request = self._outside_sandbox_request(
            cmd,
            cwd=cwd,
            timeout_sec=timeout_sec,
            reasons=reasons,
            network_required=network_required,
            writable_paths=writable_paths
        )

        return {
            "protocol_version" : 1,
            "request_kind"     : "command",
            "runtime"          : "native_coding",
            "command"          : list(cmd),
            "cwd"              : cwd_rel,
            "timeout_sec"      : int(timeout_sec),
            "network_required" : bool(network_required),
            "writable_paths"   : list(writable_paths),
            "workspace": {
                "kind" : "local_workspace",
                "root" : str(self.root),
                "cwd"  : cwd_rel,
                "materialization_required": True,
                "materialization": {
                    "strategy": materialization.get("strategy") or "cloud_runtime_attach_workspace",
                    "accepted_refs": [
                        "existing_sandbox_workspace_id",
                        "workspace_archive",
                        "repo_ref",
                        "inline_files"
                    ],
                    "preferred_refs"    : materialization.get("preferred_refs") or [],
                    "inline"            : inline,
                    "manifest"          : materialization.get("manifest") or {},
                    "workspace_archive" : workspace_archive,
                    "repo_ref"          : repo_ref,
                    "note"              : "command/cwd is not enough; cloud must materialize the same workspace before execution"
                }
            },
            "project_types"           : self.detect_project_types(cwd),
            "reasons"                 : list(reasons),
            "unavailable_next_action" : "request_outside_sandbox_execution",
            "outside_sandbox_request" : outside_sandbox_request
        }

    def _outside_sandbox_request(
        self,
        cmd: list[str],
        *,
        cwd: Path,
        timeout_sec: int,
        reasons: list[str],
        network_required: bool,
        writable_paths: list[str]
    ) -> dict[str, typing.Any]:
        return {
            "protocol_version"    : 1,
            "request_kind"        : "outside_sandbox_execution",
            "runtime"             : "native_coding",
            "command"             : list(cmd),
            "cwd"                 : self._rel(cwd),
            "timeout_sec"         : int(timeout_sec),
            "sandbox_permissions" : "require_escalated",
            "approval_required"   : True,
            "trigger"             : "cloud_sandbox_unavailable",
            "network_required"    : bool(network_required),
            "writable_paths"      : list(writable_paths),
            "reasons"             : list(reasons),
            "justification"       : "Cloud sandbox is unavailable; request user approval to run this command outside the sandbox."
        }

    def _workspace_materialization(self, cwd: Path) -> dict[str, typing.Any]:
        inline_files: list[dict[str, typing.Any]] = []

        inline_total_bytes   = 0
        inline_omitted_count = 0
        inline_truncated     = False

        manifest_files: list[dict[str, typing.Any]] = []
        manifest_total_bytes   = 0
        manifest_omitted_count = 0
        manifest_truncated     = False
        text_file_count        = 0
        binary_file_count      = 0

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
                    "path"  : rel,
                    "bytes" : size,
                    "text"  : looks_text
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
                "path"    : rel,
                "content" : content,
                "sha256"  : self._sha256(content.encode("utf-8")),
                "bytes"   : encoded_size
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
            "base"           : self._rel(cwd),
            "strategy"       : "inline_files" if inline_complete else "workspace_archive_or_repo_ref",
            "preferred_refs" : preferred_refs,
            "inline": {
                "kind"           : "inline_files",
                "files"          : inline_files,
                "file_count"     : len(inline_files),
                "total_bytes"    : inline_total_bytes,
                "truncated"      : inline_truncated,
                "omitted_count"  : inline_omitted_count,
                "complete"       : inline_complete,
                "max_files"      : self.MATERIALIZATION_MAX_FILES,
                "max_bytes"      : self.MATERIALIZATION_MAX_BYTES,
                "max_file_bytes" : self.MATERIALIZATION_MAX_FILE_BYTES
            },
            "manifest": {
                "kind"              : "workspace_manifest",
                "base"              : self._rel(cwd),
                "files"             : manifest_files,
                "file_count"        : len(manifest_files),
                "total_bytes"       : manifest_total_bytes,
                "text_file_count"   : text_file_count,
                "binary_file_count" : binary_file_count,
                "truncated"         : manifest_truncated,
                "omitted_count"     : manifest_omitted_count,
                "max_files"         : self.MATERIALIZATION_MANIFEST_MAX_FILES,
                "fingerprint"       : self._manifest_fingerprint(manifest_files)
            },
            "workspace_archive": {
                "kind"         : "workspace_archive",
                "required"     : archive_required,
                "available"    : False,
                "transport"    : "client_upload_required",
                "format"       : "tar.gz",
                "root"         : str(self.root),
                "cwd"          : self._rel(cwd),
                "exclude_dirs" : sorted(self.DEFAULT_EXCLUDES),
                "file_count"   : len(manifest_files),
                "total_bytes"  : manifest_total_bytes,
                "reason"       : "inline_materialization_incomplete" if archive_required else "inline_materialization_complete"
            },
            "repo_ref": repo_ref
        }

    def _workspace_repo_ref(self) -> dict[str, typing.Any]:
        git_dir = self.root / ".git"
        if not git_dir.exists():
            return {
                "kind"      : "repo_ref",
                "available" : False,
                "reason"    : "not_git_workspace"
            }
        head_path = git_dir / "HEAD"
        try:
            head_text = head_path.read_text(encoding="utf-8").strip()
        except OSError:
            return {
                "kind"      : "repo_ref",
                "available" : False,
                "reason"    : "head_unreadable"
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
            "kind"                        : "repo_ref",
            "available"                   : bool(commit),
            "root"                        : str(self.root),
            "branch"                      : branch,
            "commit"                      : commit,
            "requires_server_repo_access" : True,
            "note"                        : "repo_ref is usable only when cloud runtime can fetch or already has this repository"
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
        sandbox_request = payload.get("sandbox_request")
        if isinstance(sandbox_request, dict) and "outside_sandbox_request" not in payload:
            payload["outside_sandbox_request"] = sandbox_request.get("outside_sandbox_request")
        return payload

    @staticmethod
    def _deny(reason: str, *, risk: str, **data: typing.Any) -> dict[str, typing.Any]:
        payload: dict[str, typing.Any] = {"ok": False, "reason": reason, "risk": risk, **data}
        payload.setdefault("execution_target", "blocked")
        payload.setdefault("requires_cloud_sandbox", False)
        sandbox_request = payload.get("sandbox_request")
        if isinstance(sandbox_request, dict) and "outside_sandbox_request" not in payload:
            payload["outside_sandbox_request"] = sandbox_request.get("outside_sandbox_request")
        return payload


if __name__ == '__main__':
    pass
