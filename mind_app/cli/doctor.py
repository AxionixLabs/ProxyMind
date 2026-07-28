# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import sys
import shutil
import typing
from dataclasses import dataclass
from pathlib import Path
from engine.errors import AppError
from mind_app.mcp.config import normalize_mcp_servers
from mind_app.presentation.models import (
    StyledBlock,
    TextSpan,
    TextStyle
)
from mind_app.runtime.environment.shell_tools import (
    SHELL_TOOL_LAYOUT,
    executable_name
)
from mind_app.frontend.contracts import ApplicationView
from mind_app.paths import (
    mind_config_path,
    mind_home
)
from mind_app.runtime.mcp.service_runtime import (
    ServiceRuntimeSpec,
    resolve_service_runtime
)
from mind_core.config import (
    ConfigOverride
)
from mind_core.config_session import ConfigSession
from mind_core.config_store import ConfigStore
from mind_core.application_paths import (
    ApplicationMode,
    resolve_application_layout
)
from mind_nova import const
from .commands import DoctorCommand
from .frontend import resolve_cli_frontend
from .selection import resolve_cli_output_mode

DoctorStatus   = typing.Literal["pass", "warn", "fail"]
MINIMUM_PYTHON = (3, 11)
DOCTOR_TOOLS   = ("rg", "jq", "ast-grep")


@dataclass(frozen=True, slots=True)
class DoctorContext(object):
    """保存本地诊断所需的只读路径和运行形态。"""
    platform: str
    entry_mode: ApplicationMode
    entry_root: Path
    home: Path
    config_path: Path
    supports: Path
    packaged: bool
    runtime_spec: ServiceRuntimeSpec | None
    config_overrides: tuple[ConfigOverride, ...] = ()
    config_profile: str | None = None


@dataclass(frozen=True, slots=True)
class DoctorCheck(object):
    """描述一项本地环境检查结果。"""
    key: str
    name: str
    status: DoctorStatus
    summary: str
    detail: str = ""

    def to_dict(self) -> dict[str, str]:
        """返回不包含敏感配置值的序列化结果。"""
        result = {
            "key": self.key,
            "name": self.name,
            "status": self.status,
            "summary": self.summary,
        }
        if self.detail:
            result["detail"] = self.detail
        return result


@dataclass(frozen=True, slots=True)
class DoctorReport(object):
    """汇总本地环境诊断结果。"""
    checks: tuple[DoctorCheck, ...]
    version: str = const.APP_VERSION

    @property
    def ok(self) -> bool:
        """返回诊断是否没有失败项。"""
        return all(check.status != "fail" for check in self.checks)

    @property
    def exit_code(self) -> int:
        """返回适合命令行进程使用的退出码。"""
        return 0 if self.ok else 1

    def count(self, status: DoctorStatus) -> int:
        """统计指定状态的检查项数量。"""
        return sum(check.status == status for check in self.checks)

    def to_dict(self) -> dict[str, object]:
        """返回结构化诊断报告。"""
        return {
            "type": "doctor.report",
            "version": self.version,
            "ok": self.ok,
            "exit_code": self.exit_code,
            "summary": {
                "passed": self.count("pass"),
                "warnings": self.count("warn"),
                "failed": self.count("fail"),
            },
            "checks": [check.to_dict() for check in self.checks],
        }


def _platform_check(context: DoctorContext) -> DoctorCheck:
    """检查当前平台是否属于正式支持范围。"""
    if context.platform in {"win32", "darwin"}:
        return DoctorCheck(
            "platform",
            "Platform",
            "pass",
            f"{context.platform} is supported",
        )
    return DoctorCheck(
        "platform",
        "Platform",
        "fail",
        f"{context.platform} is not supported",
        f"{const.APP_DESC} currently supports Windows and macOS.",
    )


def _python_check() -> DoctorCheck:
    """检查 Python 解释器版本。"""
    current = sys.version_info[:3]
    version = ".".join(str(part) for part in current)

    if current >= MINIMUM_PYTHON:
        return DoctorCheck("python", "Python", "pass", version)

    minimum = ".".join(str(part) for part in MINIMUM_PYTHON)

    return DoctorCheck(
        "python",
        "Python",
        "fail",
        version,
        f"Python {minimum} or newer is required.",
    )


def _entry_layout_check(context: DoctorContext) -> DoctorCheck:
    """检查入口根目录和平台资源目录是否符合统一布局。"""
    if not context.entry_root.is_dir():
        return DoctorCheck(
            "entry_layout",
            "Entry layout",
            "fail",
            f"{context.entry_mode} root is unavailable",
            str(context.entry_root),
        )
    if not context.supports.is_dir():
        status: DoctorStatus = "fail" if context.packaged else "warn"
        return DoctorCheck(
            "entry_layout",
            "Entry layout",
            status,
            f"{context.entry_mode} supports directory is unavailable",
            str(context.supports),
        )

    return DoctorCheck(
        "entry_layout",
        "Entry layout",
        "pass",
        context.entry_mode,
        f"root={context.entry_root}; supports={context.supports}",
    )


def _home_check(context: DoctorContext) -> DoctorCheck:
    """检查应用用户目录是否存在且可读写。"""
    home  = context.home
    label = f"{const.APP_DESC} home"

    if not home.exists():
        return DoctorCheck(
            "mind_home",
            label,
            "warn",
            "not created yet",
            str(home),
        )

    if not home.is_dir():
        return DoctorCheck(
            "mind_home",
            label,
            "fail",
            "path is not a directory",
            str(home),
        )

    if not os.access(home, os.R_OK | os.W_OK):
        return DoctorCheck(
            "mind_home",
            label,
            "fail",
            "directory is not readable and writable",
            str(home),
        )

    return DoctorCheck("mind_home", label, "pass", str(home))


def _config_check(context: DoctorContext) -> DoctorCheck:
    """检查主配置文件是否可解析及主模型是否启用。"""
    target = context.config_path
    if not target.exists():
        return DoctorCheck(
            "config",
            "Config",
            "warn",
            "config.toml is not created yet",
            str(target),
        )
    if not target.is_file():
        return DoctorCheck(
            "config",
            "Config",
            "fail",
            "config.toml is not a file",
            str(target),
        )

    try:
        resolution = ConfigSession(
            ConfigStore(target),
            context.config_overrides,
            profile=context.config_profile,
            workspace=Path.cwd(),
        ).resolve(create=False)
    except (TypeError, ValueError) as error:
        return DoctorCheck(
            "config",
            "Config",
            "fail",
            "config.toml cannot be loaded",
            f"{type(error).__name__}: {error}",
        )

    config = resolution.config

    layer_summary = " > ".join(
        layer.scope for layer in resolution.layers
    )

    detail = f"{target}; layers={layer_summary}"

    if resolution.project_root is not None:
        detail += (
            f"; project={resolution.project_root}; "
            f"trusted={str(resolution.project_trusted).lower()}"
        )

    model_value = config.get("model", {})

    model: dict[str, object] = (
        model_value if isinstance(model_value, dict) else {}
    )

    primary_value = model.get("primary", {})

    primary: dict[str, object] = (
        primary_value if isinstance(primary_value, dict) else {}
    )

    provider   = str(primary.get("provider") or "").strip()
    model_name = str(primary.get("model") or "").strip()
    enabled    = bool(primary.get("enabled"))

    if enabled and provider and model_name:
        return DoctorCheck(
            "config",
            "Config",
            "pass",
            f"primary model enabled ({provider}/{model_name})",
            detail,
        )
    return DoctorCheck(
        "config",
        "Config",
        "warn",
        "primary model is disabled or incomplete",
        detail,
    )


def _mcp_config_check(context: DoctorContext) -> DoctorCheck:
    """检查有效配置中的外部 MCP 服务。"""
    target = context.config_path
    if not target.exists():
        return DoctorCheck(
            "external_mcp",
            "External MCP",
            "warn",
            "config.toml is not created yet",
            str(target),
        )

    try:
        resolution = ConfigSession(
            ConfigStore(target),
            context.config_overrides,
            profile=context.config_profile,
            workspace=Path.cwd(),
        ).resolve(create=False)
        servers = normalize_mcp_servers(
            resolution.config.get("mcp_servers")
        )
    except (OSError, TypeError, ValueError) as error:
        return DoctorCheck(
            "external_mcp",
            "External MCP",
            "fail",
            "MCP configuration is invalid",
            str(error),
        )

    enabled = sum(server.get("enabled", True) is not False for server in servers)
    layers  = " > ".join(layer.scope for layer in resolution.layers)

    return DoctorCheck(
        "external_mcp",
        "External MCP",
        "pass",
        f"{len(servers)} configured, {enabled} enabled",
        f"{target}; layers={layers}",
    )


def _helix_check(context: DoctorContext) -> DoctorCheck:
    """检查当前运行形态下的 Helix 组件。"""
    spec = context.runtime_spec
    if spec is None:
        return DoctorCheck(
            "helix",
            "Helix runtime",
            "warn",
            "runtime cannot be resolved on this platform",
        )
    if not context.packaged:
        return DoctorCheck(
            "helix",
            "Helix runtime",
            "pass",
            "development Python module",
            " ".join(spec.launch_command),
        )

    executable = Path(spec.executable)
    if executable.is_file():
        return DoctorCheck(
            "helix",
            "Helix runtime",
            "pass",
            str(executable),
        )
    return DoctorCheck(
        "helix",
        "Helix runtime",
        "warn",
        "runtime asset is not installed",
        str(executable),
    )


def _tool_check(context: DoctorContext, tool: str) -> DoctorCheck:
    """检查一个内置 coding 工具的 bundled 或系统命令。"""
    folder_name, command_name = SHELL_TOOL_LAYOUT[tool]

    bundled = context.supports / folder_name / executable_name(command_name)
    if bundled.is_file():
        return DoctorCheck(
            f"tool.{tool}",
            tool,
            "pass",
            "bundled",
            str(bundled),
        )

    system_command = shutil.which(command_name)
    if system_command:
        return DoctorCheck(
            f"tool.{tool}",
            tool,
            "pass",
            "system",
            system_command,
        )
    return DoctorCheck(
        f"tool.{tool}",
        tool,
        "warn",
        "not found",
        str(bundled),
    )


def diagnose(context: DoctorContext) -> DoctorReport:
    """执行不会创建文件、启动进程或访问网络的本地诊断。"""
    checks = [
        _platform_check(context),
        _python_check(),
        _entry_layout_check(context),
        _home_check(context),
        _config_check(context),
        _mcp_config_check(context),
        _helix_check(context),
    ]
    checks.extend(_tool_check(context, tool) for tool in DOCTOR_TOOLS)
    return DoctorReport(checks=tuple(checks))


def render_doctor_report(report: DoctorReport) -> StyledBlock:
    """把诊断报告转换为跨终端文本展示模型。"""
    label_styles: dict[DoctorStatus, TextStyle] = {
        "pass": TextStyle(foreground="#66C2A5", bold=True),
        "warn": TextStyle(foreground="#FFD75F", bold=True),
        "fail": TextStyle(foreground="#FF6B6B", bold=True),
    }

    title = f"{const.APP_DESC} doctor {report.version}\n"

    plain_parts = [title]

    spans: list[TextSpan] = [
        TextSpan(
            title,
            TextStyle(foreground="#AFC7D8", bold=True),
        )
    ]

    for check in report.checks:
        label = f"[{check.status.upper():4}]"
        line  = f"{label} {check.name}: {check.summary}\n"

        plain_parts.append(line)

        spans.extend((
            TextSpan(label, label_styles[check.status]),
            TextSpan(f" {check.name}: {check.summary}\n"),
        ))

        if check.detail:
            detail = f"       {check.detail}\n"
            plain_parts.append(detail)
            spans.append(TextSpan(detail, TextStyle(dim=True)))

    summary = (
        f"Summary: {report.count('pass')} passed, "
        f"{report.count('warn')} warnings, "
        f"{report.count('fail')} failed"
    )
    plain_parts.append(summary)

    spans.append(TextSpan(summary, TextStyle(bold=True)))

    return StyledBlock(
        plain_text="".join(plain_parts),
        spans=tuple(spans),
    )


def run_doctor_command(
    command: DoctorCommand,
    *,
    entry_file: str | None,
    config_overrides: tuple[ConfigOverride, ...] = (),
    config_profile: str | None = None
) -> int:
    """解析只读诊断上下文并输出检查结果。"""
    output_mode = resolve_cli_output_mode(command)
    frontend    = resolve_cli_frontend(output_mode)

    try:
        layout = resolve_application_layout(entry_file=entry_file)
    except ValueError as error:
        raise AppError(f"Application entry is unsupported: {error}") from error

    runtime_spec = None

    if layout.platform in {"win32", "darwin"}:
        runtime_spec = resolve_service_runtime(
            platform=layout.platform,
            supports=str(layout.supports),
            level=const.SHOW_LEVEL,
            packaged=layout.packaged,
        )

    report = diagnose(DoctorContext(
        platform=layout.platform,
        entry_mode=layout.mode,
        entry_root=layout.root,
        home=mind_home(),
        config_path=mind_config_path(),
        supports=layout.supports,
        packaged=layout.packaged,
        runtime_spec=runtime_spec,
        config_overrides=config_overrides,
        config_profile=config_profile,
    ))
    frontend.application.emit(ApplicationView(
        type="json" if command.output_format == "json" else "doctor",
        renderable=(
            report.to_dict()
            if command.output_format == "json"
            else render_doctor_report(report)
        ),
    ))
    return report.exit_code


if __name__ == '__main__':
    pass
