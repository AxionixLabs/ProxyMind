# -*- coding: utf-8 -*-

import ast
from pathlib import Path


PROJECT_ROOT = Path(__file__).parents[1]


def _forbidden_imports(
    package: str,
    forbidden_roots: set[str],
) -> list[str]:
    package_root = PROJECT_ROOT / package
    violations: list[str] = []

    for path in package_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            imported: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                imported = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                imported = (node.module or "",)

            for module in imported:
                root = module.partition(".")[0]
                if root in forbidden_roots:
                    relative = path.relative_to(PROJECT_ROOT)
                    violations.append(f"{relative}:{node.lineno} -> {module}")

    return violations


def _forbidden_module_imports(
    package: str,
    forbidden_modules: set[str],
) -> list[str]:
    package_root = PROJECT_ROOT / package
    violations: list[str] = []

    for path in package_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            imported: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                imported = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                imported = (node.module or "",)

            for module in imported:
                if any(
                    module == forbidden
                    or module.startswith(f"{forbidden}.")
                    for forbidden in forbidden_modules
                ):
                    relative = path.relative_to(PROJECT_ROOT)
                    violations.append(f"{relative}:{node.lineno} -> {module}")

    return violations


def test_transport_protocol_package_is_independent() -> None:
    violations = _forbidden_imports(
        "protocol",
        {"backend", "engine", "mind_app", "mind_core", "server"},
    )

    assert not violations, "protocol crosses its package boundary:\n" + "\n".join(
        violations
    )


def test_protocol_layers_remain_one_directional() -> None:
    """约束 wire SDK 的 schema、transport、client 依赖方向。"""
    schema_violations = _forbidden_module_imports(
        "protocol/schema",
        {"protocol.client", "protocol.transport", "agent", "mind_app", "mind_core", "engine"},
    )
    transport_violations = _forbidden_module_imports(
        "protocol/transport",
        {"protocol.client", "agent", "mind_app", "mind_core", "engine"},
    )

    violations = [*schema_violations, *transport_violations]
    assert not violations, "protocol layer direction is invalid:\n" + "\n".join(
        violations
    )


def test_shared_core_does_not_depend_on_application_or_services() -> None:
    violations = _forbidden_imports(
        "mind_core",
        {"backend", "mind_app", "server"},
    )

    assert not violations, "mind_core crosses its package boundary:\n" + "\n".join(
        violations
    )


def test_application_does_not_import_packaged_backend() -> None:
    violations = _forbidden_imports("mind_app", {"backend"})

    assert not violations, "mind_app imports backend:\n" + "\n".join(violations)


def test_packaged_backend_is_self_contained() -> None:
    violations = _forbidden_imports(
        "backend",
        {"engine", "mind_app", "mind_core", "protocol", "server"},
    )

    assert not violations, "backend imports application code:\n" + "\n".join(
        violations
    )


def test_agent_harness_core_does_not_import_legacy_packages() -> None:
    legacy_forbidden = {
        "applications",
        "backend",
        "engine",
        "mind_app",
        "mind_core",
        "server",
    }
    protocol_forbidden = {*legacy_forbidden, "protocol"}
    violations = [
        *_forbidden_imports("agent/protocol", protocol_forbidden),
        *_forbidden_imports("agent/domain", legacy_forbidden),
        *_forbidden_imports("agent/ports", legacy_forbidden),
        *_forbidden_imports("agent/harness", legacy_forbidden),
        *_forbidden_imports("agent/application", legacy_forbidden),
        *_forbidden_imports("agent/stores", legacy_forbidden),
    ]

    assert not violations, "agent harness imports legacy code:\n" + "\n".join(
        violations
    )


def test_agent_capabilities_depend_only_on_protocol_transport() -> None:
    violations = _forbidden_imports(
        "agent/capabilities",
        {
            "applications",
            "backend",
            "engine",
            "mind_app",
            "mind_core",
            "server",
        },
    )

    assert not violations, "agent capabilities cross adapter boundaries:\n" + (
        "\n".join(violations)
    )


def test_infrastructure_does_not_depend_on_legacy_runtime() -> None:
    """基础设施实现不能重新依赖已进入退役流程的业务包。"""
    violations = _forbidden_imports(
        "infrastructure",
        {"engine", "mind_app", "mind_core", "server"},
    )

    assert not violations, "infrastructure imports legacy runtime:\n" + (
        "\n".join(violations)
    )


def test_retired_packages_have_no_production_imports() -> None:
    """已退役的历史包不能从生产代码重新进入导入图。"""
    ignored_roots = {
        "backend",
        "codex-main",
        "schematic",
        "tests",
        "venv",
        "website",
    }
    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        relative = path.relative_to(PROJECT_ROOT)
        if relative.parts and relative.parts[0] in ignored_roots:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            for module in modules:
                if module.partition(".")[0] in {"engine", "mind_nova"}:
                    violations.append(f"{relative}:{node.lineno} -> {module}")

    assert not violations, "production imports retired package:\n" + "\n".join(
        violations
    )


def test_migrated_configuration_modules_have_no_legacy_sources_or_imports() -> None:
    """确保已迁移的配置职责不会回到 mind_core 旧路径。"""
    legacy_paths = (
        PROJECT_ROOT / "mind_core" / "agent_config.py",
        PROJECT_ROOT / "mind_core" / "feature_config.py",
        PROJECT_ROOT / "mind_core" / "provider_config.py",
    )
    assert not any(path.exists() for path in legacy_paths), (
        "migrated configuration source still exists: "
        + ", ".join(
            str(path.relative_to(PROJECT_ROOT))
            for path in legacy_paths
            if path.exists()
        )
    )

    legacy_modules = {
        "mind_core.agent_config",
        "mind_core.feature_config",
        "mind_core.provider_config",
    }
    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            for module in modules:
                if module in legacy_modules:
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                    )

    assert not violations, (
        "legacy configuration imports remain:\n" + "\n".join(violations)
    )


def test_skills_resources_have_no_legacy_package_or_imports() -> None:
    """确保技能资源发现不回到 mind_core。"""
    legacy_root = PROJECT_ROOT / "mind_core" / "skills"
    assert not legacy_root.exists(), "legacy skills package still exists"

    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            for module in modules:
                if module == "mind_core.skills" or module.startswith("mind_core.skills."):
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                    )

    assert not violations, "legacy skills imports remain:\n" + "\n".join(violations)


def test_project_trust_has_no_legacy_source_or_imports() -> None:
    """确保项目边界信任事实只由配置基础设施解析。"""
    legacy_path = PROJECT_ROOT / "mind_core" / "project_trust.py"
    assert not legacy_path.exists(), "legacy project trust source still exists"

    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            for module in modules:
                if module == "mind_core.project_trust":
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                    )

    assert not violations, "legacy project trust imports remain:\n" + "\n".join(violations)


def test_permission_policy_has_no_legacy_source_or_imports() -> None:
    """确保权限策略只由 Harness domain 持有。"""
    legacy_path = PROJECT_ROOT / "mind_core" / "permissions.py"
    assert not legacy_path.exists(), "legacy permission policy source still exists"

    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            for module in modules:
                if module == "mind_core.permissions":
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                    )

    assert not violations, "legacy permission policy imports remain:\n" + "\n".join(violations)


def test_hook_modules_have_no_legacy_sources_or_imports() -> None:
    """确保 Hook domain 和发现基础设施不回到 mind_core。"""
    legacy_paths = (
        PROJECT_ROOT / "mind_core" / "hooks.py",
        PROJECT_ROOT / "mind_core" / "hook_trust.py",
        PROJECT_ROOT / "mind_core" / "hook_discovery.py",
    )
    assert not any(path.exists() for path in legacy_paths), (
        "legacy hook source still exists: "
        + ", ".join(
            str(path.relative_to(PROJECT_ROOT))
            for path in legacy_paths
            if path.exists()
        )
    )

    legacy_modules = {
        "mind_core.hooks",
        "mind_core.hook_trust",
        "mind_core.hook_discovery",
    }
    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            for module in modules:
                if module in legacy_modules:
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                    )

    assert not violations, "legacy hook imports remain:\n" + "\n".join(violations)


def test_service_config_has_no_legacy_source_or_imports() -> None:
    """确保服务域名配置不反向依赖 mind_core。"""
    legacy_path = PROJECT_ROOT / "mind_core" / "service_config.py"
    assert not legacy_path.exists(), "legacy service config source still exists"

    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            for module in modules:
                if module == "mind_core.service_config":
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                    )

    assert not violations, "legacy service config imports remain:\n" + "\n".join(violations)


def test_preference_projection_has_no_legacy_source_or_imports() -> None:
    """确保偏好投影和状态不回到 mind_core。"""
    legacy_path = PROJECT_ROOT / "mind_core" / "preference.py"
    assert not legacy_path.exists(), "legacy preference source still exists"

    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            for module in modules:
                if module == "mind_core.preference":
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                    )

    assert not violations, "legacy preference imports remain:\n" + "\n".join(violations)


def test_config_store_has_no_legacy_source_or_imports() -> None:
    """确保 TOML 存储只由配置基础设施持有。"""
    legacy_path = PROJECT_ROOT / "mind_core" / "config_store.py"
    assert not legacy_path.exists(), "legacy config store source still exists"

    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            for module in modules:
                if module == "mind_core.config_store":
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                    )

    assert not violations, "legacy config store imports remain:\n" + "\n".join(violations)


def test_configuration_layers_have_no_legacy_sources_or_imports() -> None:
    """确保配置 schema、分层和会话只由基础设施配置包持有。"""
    legacy_paths = (
        PROJECT_ROOT / "mind_core" / "config.py",
        PROJECT_ROOT / "mind_core" / "config_layers.py",
        PROJECT_ROOT / "mind_core" / "config_session.py",
    )
    assert not any(path.exists() for path in legacy_paths), (
        "legacy configuration sources still exist: "
        + ", ".join(
            str(path.relative_to(PROJECT_ROOT))
            for path in legacy_paths
            if path.exists()
        )
    )

    legacy_modules = {
        "mind_core.config",
        "mind_core.config_layers",
        "mind_core.config_session",
    }
    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            for module in modules:
                if module in legacy_modules:
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                    )

    assert not violations, (
        "legacy configuration imports remain:\n" + "\n".join(violations)
    )


def test_runtime_paths_have_no_legacy_application_module() -> None:
    """确保用户数据目录和运行时数据库路径由配置基础设施持有。"""
    legacy_path = PROJECT_ROOT / "mind_app" / "paths.py"
    assert not legacy_path.is_file(), "legacy application paths module still exists"

    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            for module in modules:
                if module == "mind_app.paths" or module.startswith("mind_app.paths."):
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                    )

    assert not violations, "legacy application path imports remain:\n" + "\n".join(violations)


def test_runtime_asset_and_attachment_boundaries_have_no_legacy_sources() -> None:
    """确保升级资产和附件输入状态不再挂在应用根目录。"""
    legacy_paths = (
        PROJECT_ROOT / "mind_app" / "assets.py",
        PROJECT_ROOT / "mind_app" / "attach.py",
    )
    assert not any(path.is_file() for path in legacy_paths), (
        "legacy asset/attachment sources still exist: "
        + ", ".join(
            str(path.relative_to(PROJECT_ROOT))
            for path in legacy_paths
            if path.is_file()
        )
    )

    legacy_modules = {
        "mind_app.assets",
        "mind_app.attach",
    }
    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            for module in modules:
                if module in legacy_modules or any(
                    module.startswith(f"{legacy}.") for legacy in legacy_modules
                ):
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                    )

    assert not violations, (
        "legacy asset/attachment imports remain:\n" + "\n".join(violations)
    )


def test_mcp_runtime_has_no_legacy_root_package_or_imports() -> None:
    """确保 MCP 配置、连接、会话和结果工具统一归入 runtime/mcp。"""
    legacy_root = PROJECT_ROOT / "mind_app" / "mcp"
    legacy_sources = tuple(legacy_root.rglob("*.py"))
    assert not legacy_sources, (
        "legacy MCP sources still exist: "
        + ", ".join(
            str(path.relative_to(PROJECT_ROOT))
            for path in legacy_sources
        )
    )

    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            for module in modules:
                if module == "mind_app.mcp" or module.startswith("mind_app.mcp."):
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                    )

    assert not violations, "legacy MCP imports remain:\n" + "\n".join(violations)


def test_process_encoding_has_one_platform_owner() -> None:
    """确保进程输出解码只由平台基础设施实现。"""
    legacy_path = PROJECT_ROOT / "mind_app" / "native_coding" / "encoding.py"
    assert not legacy_path.is_file(), "legacy native coding encoding still exists"

    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            for module in modules:
                if module == "mind_app.native_coding.encoding":
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                    )

    assert not violations, (
        "legacy native coding encoding imports remain:\n" + "\n".join(violations)
    )


def test_workspace_process_boundaries_have_no_legacy_sources() -> None:
    """确保进程树、工作区命令和 Git 差异由平台基础设施持有。"""
    legacy_paths = (
        PROJECT_ROOT / "mind_app" / "runtime" / "processes.py",
        PROJECT_ROOT / "mind_app" / "native_coding" / "workspace_command.py",
        PROJECT_ROOT / "mind_app" / "native_coding" / "git_diff.py",
    )
    assert not any(path.is_file() for path in legacy_paths), (
        "legacy workspace/process sources still exist: "
        + ", ".join(
            str(path.relative_to(PROJECT_ROOT))
            for path in legacy_paths
            if path.is_file()
        )
    )

    legacy_modules = {
        "mind_app.runtime.processes",
        "mind_app.native_coding.workspace_command",
        "mind_app.native_coding.git_diff",
    }
    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            for module in modules:
                if module in legacy_modules:
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                    )

    assert not violations, (
        "legacy workspace/process imports remain:\n" + "\n".join(violations)
    )


def test_command_safety_has_platform_ownership() -> None:
    """确保跨平台危险命令识别不再由 native coding 包持有。"""
    legacy_root = PROJECT_ROOT / "mind_app" / "native_coding" / "exec" / "command_safety"
    legacy_sources = tuple(legacy_root.rglob("*.py"))
    assert not legacy_sources, (
        "legacy command safety sources still exist: "
        + ", ".join(
            str(path.relative_to(PROJECT_ROOT))
            for path in legacy_sources
        )
    )

    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            for module in modules:
                if (
                    module == "mind_app.native_coding.exec.command_safety"
                    or module.startswith("mind_app.native_coding.exec.command_safety.")
                ):
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                    )

    assert not violations, (
        "legacy command safety imports remain:\n" + "\n".join(violations)
    )


def test_process_execution_substrate_has_platform_ownership() -> None:
    """确保进程捕获、解码、沙箱 sidecar 和 shell 解析由平台基础设施持有。"""
    legacy_paths = (
        PROJECT_ROOT / "mind_app" / "native_coding" / "exec" / "process_capture.py",
        PROJECT_ROOT / "mind_app" / "native_coding" / "exec" / "output_decoder.py",
        PROJECT_ROOT / "mind_app" / "native_coding" / "exec" / "sandbox_client.py",
        PROJECT_ROOT / "mind_app" / "native_coding" / "exec" / "shell_runtime.py",
    )
    assert not any(path.is_file() for path in legacy_paths), (
        "legacy process execution sources still exist: "
        + ", ".join(
            str(path.relative_to(PROJECT_ROOT))
            for path in legacy_paths
            if path.is_file()
        )
    )

    legacy_prefix = "mind_app.native_coding.exec."
    legacy_modules = {
        f"{legacy_prefix}process_capture",
        f"{legacy_prefix}output_decoder",
        f"{legacy_prefix}sandbox_client",
        f"{legacy_prefix}shell_runtime",
    }
    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            for module in modules:
                if module in legacy_modules:
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                    )

    assert not violations, (
        "legacy process execution imports remain:\n" + "\n".join(violations)
    )


def test_hook_output_spill_has_platform_ownership() -> None:
    """确保 Hook 大输出的临时文件生命周期由平台基础设施持有。"""
    legacy_path = PROJECT_ROOT / "mind_app" / "runtime" / "hooks" / "output_spill.py"
    assert not legacy_path.is_file(), "legacy Hook output spill source still exists"

    legacy_modules = {"mind_app.runtime.hooks.output_spill"}
    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            for module in modules:
                if module in legacy_modules:
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                    )

    assert not violations, (
        "legacy Hook output spill imports remain:\n" + "\n".join(violations)
    )

    platform_path = PROJECT_ROOT / "infrastructure" / "platform" / "hook_output_spill.py"
    tree = ast.parse(
        platform_path.read_text(encoding="utf-8-sig"),
        filename=str(platform_path),
    )
    forbidden = {"agent", "mind_app", "mind_core", "observability"}
    platform_violations: list[str] = []
    for node in ast.walk(tree):
        modules: tuple[str, ...] = ()
        if isinstance(node, ast.Import):
            modules = tuple(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            modules = (node.module or "",)
        for module in modules:
            if module.partition(".")[0] in forbidden:
                platform_violations.append(
                    f"{platform_path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                )

    assert not platform_violations, (
        "Hook output spill platform adapter crosses its boundary:\n"
        + "\n".join(platform_violations)
    )


def test_javascript_repl_has_platform_ownership() -> None:
    """确保 JavaScript 内核进程生命周期由平台基础设施持有。"""
    legacy_paths = (
        PROJECT_ROOT / "mind_app" / "native_coding" / "js_repl" / "runtime.py",
        PROJECT_ROOT / "mind_app" / "native_coding" / "js_repl" / "__init__.py",
    )
    assert not any(path.is_file() for path in legacy_paths), (
        "legacy JavaScript REPL sources still exist: "
        + ", ".join(
            str(path.relative_to(PROJECT_ROOT))
            for path in legacy_paths
            if path.is_file()
        )
    )

    legacy_modules = {
        "mind_app.native_coding.js_repl",
        "mind_app.native_coding.js_repl.runtime",
    }
    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            for module in modules:
                if module in legacy_modules:
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                    )

    assert not violations, (
        "legacy JavaScript REPL imports remain:\n" + "\n".join(violations)
    )


def test_runtime_environment_helpers_have_platform_ownership() -> None:
    """确保 shell 工具路由和工作区探测不再由 runtime 持有。"""
    legacy_paths = (
        PROJECT_ROOT / "mind_app" / "runtime" / "environment" / "shell_tools.py",
        PROJECT_ROOT / "mind_app" / "runtime" / "environment" / "workspace.py",
    )
    assert not any(path.is_file() for path in legacy_paths), (
        "legacy runtime environment sources still exist: "
        + ", ".join(
            str(path.relative_to(PROJECT_ROOT))
            for path in legacy_paths
            if path.is_file()
        )
    )

    legacy_modules = {
        "mind_app.runtime.environment.shell_tools",
        "mind_app.runtime.environment.workspace",
    }
    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            for module in modules:
                if module in legacy_modules:
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                    )

    assert not violations, (
        "legacy runtime environment imports remain:\n" + "\n".join(violations)
    )


def test_workspace_runtime_owner_belongs_to_harness() -> None:
    """确保工作区资源生命周期不再由 legacy runtime 或 Controller 隐式装配。"""
    legacy_path = (
        PROJECT_ROOT
        / "mind_app"
        / "runtime"
        / "environment"
        / "coding_lifecycle.py"
    )
    assert not legacy_path.is_file(), "legacy workspace runtime owner still exists"

    legacy_modules = {
        "mind_app.runtime.environment.coding_lifecycle",
    }
    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            for module in modules:
                if module in legacy_modules:
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                    )

    assert not violations, (
        "legacy workspace runtime imports remain:\n" + "\n".join(violations)
    )


def test_environment_snapshot_capture_has_application_ownership() -> None:
    """确保环境快照采集核心脱离 runtime 目录和 Controller 实现。"""
    legacy_path = (
        PROJECT_ROOT
        / "mind_app"
        / "runtime"
        / "environment"
        / "snapshot.py"
    )
    assert not legacy_path.is_file(), "legacy environment snapshot source still exists"

    legacy_modules = {
        "mind_app.runtime.environment.snapshot",
    }
    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            for module in modules:
                if module in legacy_modules:
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                    )

    assert not violations, (
        "legacy environment snapshot imports remain:\n" + "\n".join(violations)
    )

    application_path = PROJECT_ROOT / "agent" / "application" / "environment.py"
    tree = ast.parse(
        application_path.read_text(encoding="utf-8-sig"),
        filename=str(application_path),
    )
    forbidden = {"infrastructure", "mind_app", "mind_core", "observability"}
    application_violations: list[str] = []
    for node in ast.walk(tree):
        modules: tuple[str, ...] = ()
        if isinstance(node, ast.Import):
            modules = tuple(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            modules = (node.module or "",)
        for module in modules:
            if module.partition(".")[0] in forbidden:
                application_violations.append(
                    f"{application_path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                )

    assert not application_violations, (
        "environment application core crosses its boundary:\n"
        + "\n".join(application_violations)
    )


def test_hook_models_have_application_ownership() -> None:
    """确保 Hook 运行值对象不再由 runtime 目录持有。"""
    legacy_path = PROJECT_ROOT / "mind_app" / "runtime" / "hooks" / "models.py"
    assert not legacy_path.is_file(), "legacy hook model source still exists"

    legacy_modules = {"mind_app.runtime.hooks.models"}
    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            for module in modules:
                if module in legacy_modules:
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                    )

    assert not violations, (
        "legacy hook model imports remain:\n" + "\n".join(violations)
    )

    application_path = PROJECT_ROOT / "agent" / "application" / "hook_models.py"
    tree = ast.parse(
        application_path.read_text(encoding="utf-8-sig"),
        filename=str(application_path),
    )
    forbidden = {"infrastructure", "mind_app", "mind_core", "observability"}
    application_violations: list[str] = []
    for node in ast.walk(tree):
        modules: tuple[str, ...] = ()
        if isinstance(node, ast.Import):
            modules = tuple(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            modules = (node.module or "",)
        for module in modules:
            if module.partition(".")[0] in forbidden:
                application_violations.append(
                    f"{application_path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                )

    assert not application_violations, (
        "hook application models cross their boundary:\n"
        + "\n".join(application_violations)
    )


def test_hook_protocol_has_application_ownership() -> None:
    """确保 Hook stdin/stdout schema 不再由 runtime 目录持有。"""
    legacy_path = PROJECT_ROOT / "mind_app" / "runtime" / "hooks" / "protocol.py"
    assert not legacy_path.is_file(), "legacy hook protocol source still exists"

    legacy_modules = {"mind_app.runtime.hooks.protocol"}
    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            for module in modules:
                if module in legacy_modules:
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                    )

    assert not violations, (
        "legacy hook protocol imports remain:\n" + "\n".join(violations)
    )

    application_path = PROJECT_ROOT / "agent" / "application" / "hook_protocol.py"
    tree = ast.parse(
        application_path.read_text(encoding="utf-8-sig"),
        filename=str(application_path),
    )
    forbidden = {"infrastructure", "mind_app", "mind_core", "observability"}
    application_violations: list[str] = []
    for node in ast.walk(tree):
        modules: tuple[str, ...] = ()
        if isinstance(node, ast.Import):
            modules = tuple(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            modules = (node.module or "",)
        for module in modules:
            if module.partition(".")[0] in forbidden:
                application_violations.append(
                    f"{application_path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                )

    assert not application_violations, (
        "hook application protocol crosses its boundary:\n"
        + "\n".join(application_violations)
    )


def test_hook_catalog_has_application_ownership() -> None:
    """确保 Hook 管理目录值对象不再由 runtime 目录持有。"""
    legacy_path = PROJECT_ROOT / "mind_app" / "runtime" / "hooks" / "catalog.py"
    assert not legacy_path.is_file(), "legacy hook catalog source still exists"

    legacy_modules = {"mind_app.runtime.hooks.catalog"}
    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            for module in modules:
                if module in legacy_modules:
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                    )

    assert not violations, (
        "legacy hook catalog imports remain:\n" + "\n".join(violations)
    )

    application_path = PROJECT_ROOT / "agent" / "application" / "hook_catalog.py"
    tree = ast.parse(
        application_path.read_text(encoding="utf-8-sig"),
        filename=str(application_path),
    )
    forbidden = {"infrastructure", "mind_app", "mind_core", "observability"}
    application_violations: list[str] = []
    for node in ast.walk(tree):
        modules: tuple[str, ...] = ()
        if isinstance(node, ast.Import):
            modules = tuple(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            modules = (node.module or "",)
        for module in modules:
            if module.partition(".")[0] in forbidden:
                application_violations.append(
                    f"{application_path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                )

    assert not application_violations, (
        "hook application catalog crosses its boundary:\n"
        + "\n".join(application_violations)
    )
    assert application_path.is_file(), "application hook catalog source is missing"


def test_hook_matching_belongs_to_domain() -> None:
    """确保 Hook 匹配规则归入 domain 且不反向依赖 application。"""
    legacy_path = PROJECT_ROOT / "mind_app" / "runtime" / "hooks" / "matching.py"
    assert not legacy_path.is_file(), "legacy hook matching source still exists"

    legacy_modules = {"mind_app.runtime.hooks.matching"}
    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            for module in modules:
                if module in legacy_modules:
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                    )

    assert not violations, (
        "legacy hook matching imports remain:\n" + "\n".join(violations)
    )

    domain_path = PROJECT_ROOT / "agent" / "domain" / "hook_matching.py"
    tree = ast.parse(
        domain_path.read_text(encoding="utf-8-sig"),
        filename=str(domain_path),
    )
    forbidden = {"agent.application", "infrastructure", "mind_app", "mind_core"}
    domain_violations: list[str] = []
    for node in ast.walk(tree):
        modules: tuple[str, ...] = ()
        if isinstance(node, ast.Import):
            modules = tuple(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            modules = (node.module or "",)
        for module in modules:
            if module == "agent.application" or module.startswith("agent.application."):
                domain_violations.append(
                    f"{domain_path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                )
            elif module.partition(".")[0] in {"infrastructure", "mind_app", "mind_core"}:
                domain_violations.append(
                    f"{domain_path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                )

    assert not domain_violations, (
        "hook domain matching crosses its boundary:\n"
        + "\n".join(domain_violations)
    )
    assert domain_path.is_file(), "domain hook matching source is missing"


def test_hook_output_and_events_have_application_ownership() -> None:
    """确保 Hook 输出归一化和事件目录不再由 runtime 持有。"""
    legacy_modules = {
        "mind_app.runtime.hooks.effects",
        "mind_app.runtime.hooks.events",
    }
    for legacy_name in legacy_modules:
        legacy_path = PROJECT_ROOT / Path(*legacy_name.split("."))
        assert not legacy_path.with_suffix(".py").is_file(), (
            f"legacy Hook module still exists: {legacy_name}"
        )

    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            for module in modules:
                if module in legacy_modules:
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                    )

    assert not violations, (
        "legacy Hook output/event imports remain:\n" + "\n".join(violations)
    )

    forbidden = {"infrastructure", "mind_app", "mind_core", "observability"}
    for relative_path in ("hook_output.py", "hook_events.py"):
        application_path = PROJECT_ROOT / "agent" / "application" / relative_path
        tree = ast.parse(
            application_path.read_text(encoding="utf-8-sig"),
            filename=str(application_path),
        )
        application_violations: list[str] = []
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            for module in modules:
                if module.partition(".")[0] in forbidden:
                    application_violations.append(
                        f"{application_path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                    )

        assert not application_violations, (
            f"Hook application module crosses its boundary: {relative_path}\n"
            + "\n".join(application_violations)
        )


def test_hook_result_projection_has_application_ownership() -> None:
    """确保后置 Hook 工具结果投影不再由 runtime 持有。"""
    legacy_path = PROJECT_ROOT / "mind_app" / "runtime" / "hooks" / "results.py"
    assert not legacy_path.is_file(), "legacy Hook result projection still exists"

    legacy_modules = {"mind_app.runtime.hooks.results"}
    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            for module in modules:
                if module in legacy_modules:
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                    )

    assert not violations, (
        "legacy Hook result projection imports remain:\n"
        + "\n".join(violations)
    )

    application_path = PROJECT_ROOT / "agent" / "application" / "hook_result.py"
    tree = ast.parse(
        application_path.read_text(encoding="utf-8-sig"),
        filename=str(application_path),
    )
    forbidden = {"infrastructure", "mind_app", "mind_core", "observability"}
    application_violations: list[str] = []
    for node in ast.walk(tree):
        modules: tuple[str, ...] = ()
        if isinstance(node, ast.Import):
            modules = tuple(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            modules = (node.module or "",)
        for module in modules:
            if module.partition(".")[0] in forbidden:
                application_violations.append(
                    f"{application_path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                )

    assert not application_violations, (
        "Hook application result projection crosses its boundary:\n"
        + "\n".join(application_violations)
    )


def test_tool_mode_policy_belongs_to_domain() -> None:
    """确保工具可见性策略归入 domain 且不反向依赖 runtime。"""
    legacy_path = PROJECT_ROOT / "mind_app" / "runtime" / "tools" / "mode_policy.py"
    assert not legacy_path.is_file(), "legacy tool mode policy source still exists"

    legacy_modules = {"mind_app.runtime.tools.mode_policy"}
    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            for module in modules:
                if module in legacy_modules:
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                    )

    assert not violations, (
        "legacy tool mode policy imports remain:\n" + "\n".join(violations)
    )

    domain_path = PROJECT_ROOT / "agent" / "domain" / "tool_policy.py"
    tree = ast.parse(
        domain_path.read_text(encoding="utf-8-sig"),
        filename=str(domain_path),
    )
    domain_violations: list[str] = []
    forbidden_roots = {"infrastructure", "mind_app", "mind_core", "observability"}
    for node in ast.walk(tree):
        modules: tuple[str, ...] = ()
        if isinstance(node, ast.Import):
            modules = tuple(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            modules = (node.module or "",)
        for module in modules:
            if module == "agent.application" or module.startswith("agent.application."):
                domain_violations.append(
                    f"{domain_path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                )
            elif module.partition(".")[0] in forbidden_roots:
                domain_violations.append(
                    f"{domain_path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                )

    assert not domain_violations, (
        "tool mode policy crosses its domain boundary:\n"
        + "\n".join(domain_violations)
    )


def test_turn_result_and_session_identity_boundaries_are_explicit() -> None:
    """确保运行结果、会话身份和空闲计时器不再由 runtime support 持有。"""
    legacy_paths = (
        PROJECT_ROOT / "mind_app" / "runtime" / "turns" / "result.py",
        PROJECT_ROOT / "mind_app" / "runtime" / "turns" / "stream_outcome.py",
        PROJECT_ROOT / "mind_app" / "runtime" / "support" / "session_identity.py",
        PROJECT_ROOT / "mind_app" / "runtime" / "support" / "idle_status.py",
        PROJECT_ROOT / "mind_app" / "runtime" / "support" / "rwlock.py",
    )
    assert not any(path.is_file() for path in legacy_paths), (
        "legacy turn/support sources still exist: "
        + ", ".join(
            str(path.relative_to(PROJECT_ROOT))
            for path in legacy_paths
            if path.is_file()
        )
    )

    legacy_modules = {
        "mind_app.runtime.turns.result",
        "mind_app.runtime.turns.stream_outcome",
        "mind_app.runtime.support.session_identity",
        "mind_app.runtime.support.idle_status",
        "mind_app.runtime.support.rwlock",
    }
    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            for module in modules:
                if module in legacy_modules:
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                    )

    assert not violations, "legacy turn/support imports remain:\n" + "\n".join(violations)

    for relative_path in (
        "run_result.py",
        "stream_outcome.py",
        "session_identity.py",
    ):
        application_path = PROJECT_ROOT / "agent" / "application" / relative_path
        assert application_path.is_file(), (
            f"application source is missing: {relative_path}"
        )
        tree = ast.parse(
            application_path.read_text(encoding="utf-8-sig"),
            filename=str(application_path),
        )
        forbidden = {
            "infrastructure",
            "mind_app",
            "mind_core",
            "observability",
            "protocol",
        }
        application_violations: list[str] = []
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            for module in modules:
                if module.partition(".")[0] in forbidden:
                    application_violations.append(
                        f"{application_path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                    )
        assert not application_violations, (
            f"application turn module crosses its boundary: {relative_path}\n"
            + "\n".join(application_violations)
        )

    platform_path = PROJECT_ROOT / "infrastructure" / "platform" / "idle_status.py"
    assert platform_path.is_file(), "platform idle status source is missing"
    platform_violations = _forbidden_imports(
        "infrastructure/platform",
        {"engine", "mind_app", "mind_core", "server"},
    )
    assert not platform_violations, (
        "platform idle status crosses its boundary:\n"
        + "\n".join(platform_violations)
    )


def test_tool_progress_has_mcp_ownership_and_dead_policy_is_removed() -> None:
    """确保 MCP 进度通知归入 MCP runtime 且无调用者的策略模块已删除。"""
    legacy_paths = (
        PROJECT_ROOT / "mind_app" / "runtime" / "tools" / "notify.py",
        PROJECT_ROOT / "mind_app" / "runtime" / "tools" / "policy.py",
    )
    assert not any(path.is_file() for path in legacy_paths), (
        "legacy tool support sources still exist: "
        + ", ".join(
            str(path.relative_to(PROJECT_ROOT))
            for path in legacy_paths
            if path.is_file()
        )
    )

    legacy_modules = {
        "mind_app.runtime.tools.notify",
        "mind_app.runtime.tools.policy",
    }
    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            for module in modules:
                if module in legacy_modules:
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                    )

    assert not violations, "legacy tool support imports remain:\n" + "\n".join(violations)
    assert (
        PROJECT_ROOT / "mind_app" / "runtime" / "mcp" / "tool_progress.py"
    ).is_file(), "MCP tool progress source is missing"


def test_hook_execution_ports_are_owned_by_agent_ports() -> None:
    """确保 Hook 执行端口不由具体 runtime 模块定义或携带实现依赖。"""
    ports_path = PROJECT_ROOT / "agent" / "ports" / "hooks.py"
    assert ports_path.is_file(), "Hook execution ports source is missing"

    tree = ast.parse(
        ports_path.read_text(encoding="utf-8-sig"),
        filename=str(ports_path),
    )
    violations: list[str] = []
    for node in ast.walk(tree):
        modules: tuple[str, ...] = ()
        if isinstance(node, ast.Import):
            modules = tuple(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            modules = (node.module or "",)
        for module in modules:
            if module.partition(".")[0] in {
                "infrastructure",
                "mind_app",
                "mind_core",
                "observability",
                "protocol",
            }:
                violations.append(
                    f"{ports_path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                )

    assert not violations, "Hook ports cross their boundary:\n" + "\n".join(violations)

    runtime_path = PROJECT_ROOT / "mind_app" / "runtime" / "hooks" / "runtime.py"
    runtime_tree = ast.parse(
        runtime_path.read_text(encoding="utf-8-sig"),
        filename=str(runtime_path),
    )
    local_protocols = {
        node.name
        for node in runtime_tree.body
        if isinstance(node, ast.ClassDef)
        and node.name in {"HookCommandRunner", "HookContextSpiller"}
    }
    assert not local_protocols, (
        "Hook execution ports remain defined in runtime: "
        + ", ".join(sorted(local_protocols))
    )

    for relative_path in (
        "mind_app/runtime/hooks/runtime.py",
        "mind_app/runtime/hooks/registry.py",
    ):
        source_path = PROJECT_ROOT / relative_path
        source_tree = ast.parse(
            source_path.read_text(encoding="utf-8-sig"),
            filename=str(source_path),
        )
        reflected_executor_checks = [
            node
            for node in ast.walk(source_tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "isinstance"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Name)
            and node.args[1].id == "HookCommandExecutor"
        ]
        assert not reflected_executor_checks, (
            "Hook lifecycle must use explicit ports: "
            + relative_path
        )


def test_execution_policy_is_split_between_domain_and_config() -> None:
    """确保执行策略值对象与规则文件解析分别归属 domain/config。"""
    legacy_root = PROJECT_ROOT / "mind_app" / "native_coding" / "exec" / "execpolicy"
    legacy_sources = tuple(legacy_root.rglob("*.py"))
    assert not legacy_sources, (
        "legacy execution policy sources still exist: "
        + ", ".join(
            str(path.relative_to(PROJECT_ROOT))
            for path in legacy_sources
        )
    )

    legacy_modules = {
        "mind_app.native_coding.exec.execpolicy",
        "mind_app.native_coding.exec.execpolicy.decision",
        "mind_app.native_coding.exec.execpolicy.rule",
        "mind_app.native_coding.exec.execpolicy.policy",
        "mind_app.native_coding.exec.execpolicy.parser",
    }
    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            for module in modules:
                if module in legacy_modules:
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                    )

    assert not violations, (
        "legacy execution policy imports remain:\n" + "\n".join(violations)
    )

    domain_root = PROJECT_ROOT / "agent" / "domain" / "execution_policy"
    domain_violations: list[str] = []
    for path in domain_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            for module in modules:
                if module.startswith(("mind_app", "infrastructure")):
                    domain_violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                    )

    assert not domain_violations, (
        "execution policy domain imports infrastructure/application code:\n"
        + "\n".join(domain_violations)
    )


def test_terminal_presentation_has_no_legacy_design_sources_or_imports() -> None:
    """确保终端展示能力只由 presentation/terminal 持有。"""
    legacy_design_root = PROJECT_ROOT / "mind_core" / "design"
    legacy_paths = (
        *legacy_design_root.rglob("*.py"),
        PROJECT_ROOT / "mind_app" / "runtime" / "design.py",
    )
    assert not any(path.is_file() for path in legacy_paths), (
        "legacy terminal design sources still exist: "
        + ", ".join(
            str(path.relative_to(PROJECT_ROOT))
            for path in legacy_paths
            if path.is_file()
        )
    )

    legacy_modules = {
        "mind_core.design",
        "mind_app.runtime.design",
    }
    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            for module in modules:
                if any(
                    module == legacy or module.startswith(f"{legacy}.")
                    for legacy in legacy_modules
                ):
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                    )

    assert not violations, "legacy terminal design imports remain:\n" + "\n".join(violations)


def test_frontend_contracts_have_no_legacy_package_or_imports() -> None:
    """确保通用前端契约和 sink 已归入 presentation 边界。"""
    legacy_root = PROJECT_ROOT / "mind_app" / "frontend"
    legacy_sources = tuple(legacy_root.rglob("*.py"))
    assert not legacy_sources, (
        "legacy frontend sources still exist: "
        + ", ".join(
            str(path.relative_to(PROJECT_ROOT))
            for path in legacy_sources
        )
    )

    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            for module in modules:
                if module == "mind_app.frontend" or module.startswith("mind_app.frontend."):
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                    )

    assert not violations, "legacy frontend imports remain:\n" + "\n".join(violations)


def test_presentation_output_has_no_legacy_package_or_imports() -> None:
    """确保单轮输出会话和 sink 已归入 presentation/output 边界。"""
    legacy_root = PROJECT_ROOT / "mind_app" / "output"
    legacy_sources = tuple(legacy_root.rglob("*.py"))
    assert not legacy_sources, (
        "legacy output sources still exist: "
        + ", ".join(
            str(path.relative_to(PROJECT_ROOT))
            for path in legacy_sources
        )
    )

    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            for module in modules:
                if module == "mind_app.output" or module.startswith("mind_app.output."):
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                    )

    assert not violations, "legacy output imports remain:\n" + "\n".join(violations)


def test_presentation_stream_has_no_legacy_packages_or_facades() -> None:
    """确保流事件展示、输出记录和边界状态只存在于 presentation。"""
    legacy_roots = (
        PROJECT_ROOT / "mind_app" / "stream_events",
        PROJECT_ROOT / "mind_app" / "stream_io",
        PROJECT_ROOT / "mind_app" / "stream_state",
    )
    legacy_sources = tuple(
        path
        for root in legacy_roots
        for path in root.rglob("*.py")
    )
    assert not legacy_sources, (
        "legacy stream presentation sources still exist: "
        + ", ".join(
            str(path.relative_to(PROJECT_ROOT))
            for path in legacy_sources
        )
    )

    legacy_modules = (
        "mind_app.stream_events",
        "mind_app.stream_io",
        "mind_app.stream_state",
        "mind_app.presentation.stream.tool_trace",
    )
    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            for module in modules:
                if any(
                    module == legacy or module.startswith(f"{legacy}.")
                    for legacy in legacy_modules
                ):
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                    )

    assert not violations, (
        "legacy stream presentation imports remain:\n" + "\n".join(violations)
    )


def test_approval_state_stores_have_no_legacy_package_or_imports() -> None:
    """确保审批调用账本和权限授权存储由 agent/stores 持有。"""
    legacy_paths = (
        PROJECT_ROOT / "mind_app" / "approval" / "ledger.py",
        PROJECT_ROOT / "mind_app" / "approval" / "permission_grants.py",
    )
    assert not any(path.is_file() for path in legacy_paths), (
        "legacy approval state sources still exist: "
        + ", ".join(
            str(path.relative_to(PROJECT_ROOT))
            for path in legacy_paths
            if path.is_file()
        )
    )

    legacy_modules = {
        "mind_app.approval.ledger",
        "mind_app.approval.permission_grants",
    }
    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            for module in modules:
                if module in legacy_modules:
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                    )

    assert not violations, "legacy approval state imports remain:\n" + "\n".join(violations)


def test_agent_application_does_not_load_concrete_composition() -> None:
    violations = _forbidden_module_imports(
        "agent/application",
        {
            "agent.capabilities",
            "agent.composition",
            "agent.stores",
        },
    )

    assert not violations, "agent application loads concrete adapters:\n" + (
        "\n".join(violations)
    )


def test_legacy_application_does_not_import_model_transport() -> None:
    violations = _forbidden_module_imports(
        "mind_app",
        {"protocol.client.chat"},
    )

    assert not violations, "legacy application imports model transport:\n" + (
        "\n".join(violations)
    )


def test_legacy_application_uses_application_or_owned_state_entry() -> None:
    """限制旧应用只能使用 application 用例或明确归属的 state store。"""
    allowed_modules = {
        "agent.application",
        "agent.application.hook_catalog",
        "agent.application.hook_events",
        "agent.application.hook_protocol",
        "agent.application.hook_models",
        "agent.application.hook_output",
        "agent.application.hook_result",
        "agent.domain.hook_matching",
        "agent.domain.tool_policy",
        "agent.domain.execution_policy",
        "agent.ports",
        "agent.protocol.json_value",
        "agent.stores.approval_ledger",
        "agent.stores.permission_grants",
    }
    violations: list[str] = []
    package_root = PROJECT_ROOT / "mind_app"

    for path in package_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            imported: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                imported = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                imported = (node.module or "",)

            for module in imported:
                if module == "agent" or module.startswith("agent."):
                    if module not in allowed_modules:
                        relative = path.relative_to(PROJECT_ROOT)
                        violations.append(
                            f"{relative}:{node.lineno} -> {module}"
                        )

    assert not violations, (
        "legacy application imports an unowned agent boundary:\n"
        + "\n".join(violations)
    )


def test_product_logging_enters_observability_boundary() -> None:
    """禁止业务包重新拥有标准 logging logger。"""
    violations: list[str] = []
    excluded_parts = {"tests", "venv", "codex-main", "backend", "observability"}

    for path in PROJECT_ROOT.rglob("*.py"):
        relative_parts = set(path.relative_to(PROJECT_ROOT).parts)
        if relative_parts & excluded_parts:
            continue

        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                if any(alias.name == "logging" for alias in node.names):
                    violations.append(f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> logging")
                if any(alias.name == "loguru" for alias in node.names):
                    violations.append(f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> loguru")
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                if (node.module or "").partition(".")[0] == "logging":
                    violations.append(f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> logging")
                if (node.module or "").partition(".")[0] == "loguru":
                    violations.append(f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> loguru")

            if isinstance(node, ast.Assign):
                targets = [*node.targets]
                if any(
                    isinstance(target, ast.Name) and target.id == "_LOGGER"
                    for target in targets
                ):
                    violations.append(f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> _LOGGER")

            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "getLogger"
            ):
                violations.append(f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> getLogger")

    assert not violations, "production logging bypasses observability:\n" + "\n".join(violations)


def test_run_report_has_observability_ownership() -> None:
    """确保报告文件 sink 不再由 mind_app 持有。"""
    legacy_path = PROJECT_ROOT / "mind_app" / "reporting.py"
    assert not legacy_path.is_file(), "legacy RunReport module still exists"

    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            for module in modules:
                if module == "mind_app.reporting" or module.startswith("mind_app.reporting."):
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                    )

    assert not violations, "legacy reporting imports remain:\n" + "\n".join(violations)


def test_event_report_lifecycle_has_protocol_client_ownership() -> None:
    """确保事件报告生命周期不再由 runtime turns 持有。"""
    legacy_path = (
        PROJECT_ROOT
        / "mind_app"
        / "runtime"
        / "turns"
        / "event_reporting.py"
    )
    client_path = PROJECT_ROOT / "protocol" / "client" / "reports.py"

    assert not legacy_path.is_file(), "legacy event reporting source still exists"
    assert client_path.is_file(), "protocol client report lifecycle is missing"

    legacy_modules = {
        "mind_app.runtime.turns.event_reporting",
    }
    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            for module in modules:
                if module in legacy_modules:
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                    )

    assert not violations, "legacy event reporting imports remain:\n" + "\n".join(
        violations
    )


def test_hook_registry_is_composed_at_the_process_root() -> None:
    """确保入口只消费 Hook registry port，不直接装配具体实现。"""
    entry_paths = (
        PROJECT_ROOT / "mind_app" / "controller.py",
        PROJECT_ROOT / "mind_app" / "cli" / "bootstrap.py",
        PROJECT_ROOT / "mind_app" / "runtime" / "mcp" / "server.py",
    )
    violations: list[str] = []
    for path in entry_paths:
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module == "mind_app.runtime.hooks.registry":
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} imports concrete HookRegistry"
                    )
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "HookRegistry"
            ):
                violations.append(
                    f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} constructs concrete HookRegistry"
                )

    assert not violations, "Hook registry construction escaped composition root:\n" + (
        "\n".join(violations)
    )


def test_controller_does_not_expose_runtime_facades() -> None:
    controller_path = PROJECT_ROOT / "mind_app" / "controller.py"
    tree = ast.parse(
        controller_path.read_text(encoding="utf-8-sig"),
        filename=str(controller_path),
    )
    controller = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "Mind"
    )
    methods = {
        node.name
        for node in controller.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assigned_attributes = {
        node.attr
        for node in ast.walk(controller)
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store)
    }
    called_names = {
        node.func.id
        for node in ast.walk(controller)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    closes_borrowed_report = any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "close"
        and isinstance(node.func.value, ast.Attribute)
        and node.func.value.attr == "report"
        for node in ast.walk(controller)
    )

    assert not {
        "bind_server_manager",
        "bind_service_runtime_context",
        "calling",
        "cancel_service_runtime_startup",
        "keepalive_task_done",
        "pause_subscription_listener",
        "reboot_runtime",
        "require_service_runtime_context",
        "restart_external_mcp_runtime",
        "run_service_runtime_startup",
        "run_turn_lifecycle",
        "start_keepalive_supervisor",
        "start_config_service",
        "start_external_mcp_runtime",
        "start_subscription_listener",
        "stop_keepalive_supervisor",
        "stop_config_service",
        "stop_service_runtime",
        "stop_external_mcp_runtime",
        "stop_subscription_listener",
        "stream_turn",
    } & methods
    assert not (
        PROJECT_ROOT / "mind_app" / "runtime" / "support" / "calling.py"
    ).exists()
    assert "event_reports" not in assigned_attributes
    assert not {
        "_native_coding_close_tasks",
        "_service_start_lock",
        "_service_start_task",
        "config_service",
        "exec_policy_manager",
        "keepalive_stop",
        "keepalive_task",
        "native_coding",
        "server_manager",
        "service_runtime_context",
        "stop_runtime_on_exit",
        "user_shell",
    } & assigned_attributes
    assert "RunReport" not in called_names
    assert not closes_borrowed_report
