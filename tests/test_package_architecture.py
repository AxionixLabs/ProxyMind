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
    forbidden = {
        "applications",
        "backend",
        "engine",
        "mind_app",
        "mind_core",
        "protocol",
        "server",
    }
    violations = [
        *_forbidden_imports("agent/protocol", forbidden),
        *_forbidden_imports("agent/domain", forbidden),
        *_forbidden_imports("agent/ports", forbidden),
        *_forbidden_imports("agent/harness", forbidden),
        *_forbidden_imports("agent/application", forbidden),
        *_forbidden_imports("agent/stores", forbidden),
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


def test_legacy_application_uses_only_agent_application_entry() -> None:
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
                    if module != "agent.application":
                        relative = path.relative_to(PROJECT_ROOT)
                        violations.append(
                            f"{relative}:{node.lineno} -> {module}"
                        )

    assert not violations, (
        "legacy application bypasses agent.application:\n"
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
