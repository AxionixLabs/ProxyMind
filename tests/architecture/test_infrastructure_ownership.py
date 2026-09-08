# -*- coding: utf-8 -*-

"""审计基础设施、平台、配置与外部适配器的唯一所有权。"""


import ast
from tests.architecture.source_inventory import (
    PROJECT_ROOT,
    all_python_sources as _all_python_sources,
    forbidden_imports as _forbidden_imports,
    forbidden_module_imports as _forbidden_module_imports,
    parsed_source as _parsed_source,
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
    for path in _all_python_sources():
        relative = path.relative_to(PROJECT_ROOT)
        if relative.parts and relative.parts[0] in ignored_roots:
            continue
        tree = _parsed_source(path)
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
    for path in _all_python_sources():
        tree = _parsed_source(path)
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
    for path in _all_python_sources():
        tree = _parsed_source(path)
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
    for path in _all_python_sources():
        tree = _parsed_source(path)
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
    for path in _all_python_sources():
        tree = _parsed_source(path)
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
    for path in _all_python_sources():
        tree = _parsed_source(path)
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
    for path in _all_python_sources():
        tree = _parsed_source(path)
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
    for path in _all_python_sources():
        tree = _parsed_source(path)
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
    for path in _all_python_sources():
        tree = _parsed_source(path)
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
    for path in _all_python_sources():
        tree = _parsed_source(path)
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
    for path in _all_python_sources():
        tree = _parsed_source(path)
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
    for path in _all_python_sources():
        tree = _parsed_source(path)
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
    for path in _all_python_sources():
        tree = _parsed_source(path)
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


def test_mcp_stdio_adapter_is_owned_by_frontends() -> None:
    """确保 stdio MCP 入站适配器已迁入前端边界。"""
    legacy_path = PROJECT_ROOT / "mind_app" / "runtime" / "mcp" / "server.py"
    target_path = PROJECT_ROOT / "frontends" / "mcp" / "server.py"
    assert not legacy_path.is_file(), "legacy MCP stdio adapter still exists"
    assert target_path.is_file(), "frontend MCP stdio adapter is missing"

    tree = ast.parse(
        target_path.read_text(encoding="utf-8-sig"),
        filename=str(target_path),
    )
    definitions = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert {
        "McpServerRuntime",
        "create_mcp_server",
        "run_mcp_server",
    } <= definitions

    target_modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            target_modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            target_modules.append(node.module or "")
    assert "mind_app.runtime.turns.root" not in target_modules
    assert "mind_app.interaction.environment" not in target_modules

    violations: list[str] = []
    legacy_module = "mind_app.runtime.mcp.server"
    for path in _all_python_sources():
        source_tree = _parsed_source(path)
        for node in ast.walk(source_tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            if legacy_module in modules:
                violations.append(f"{path.relative_to(PROJECT_ROOT)}:{node.lineno}")
    assert not violations, "legacy MCP stdio imports remain:\n" + "\n".join(violations)


def test_mcp_lifecycle_owner_is_harness_owned() -> None:
    """确保 MCP 生命周期所有者只依赖 Harness 端口和注入工厂。"""
    legacy_path = PROJECT_ROOT / "mind_app" / "runtime" / "mcp" / "lifecycle.py"
    owner_path = PROJECT_ROOT / "agent" / "harness" / "mcp" / "owner.py"

    assert not legacy_path.is_file(), "legacy MCP lifecycle owner still exists"
    assert owner_path.is_file(), "Harness MCP lifecycle owner is missing"

    tree = ast.parse(owner_path.read_text(encoding="utf-8-sig"), filename=str(owner_path))
    classes = {
        node.name
        for node in tree.body
        if isinstance(node, ast.ClassDef)
    }
    assert "McpRuntimeOwner" in classes
    assert "McpRuntime" not in classes

    violations: list[str] = []
    legacy_module = "mind_app.runtime.mcp." + "lifecycle"
    for path in _all_python_sources():
        source_tree = _parsed_source(path)
        for node in ast.walk(source_tree):
            imported_module = ""
            if isinstance(node, ast.ImportFrom) and node.level == 0:
                imported_module = node.module or ""
            elif isinstance(node, ast.Import):
                imported_module = next(
                    (
                        alias.name
                        for alias in node.names
                        if alias.name == legacy_module
                    ),
                    "",
                )
            if imported_module == legacy_module:
                violations.append(
                    f"{path.relative_to(PROJECT_ROOT)}:{node.lineno}"
                )
    assert not violations, "legacy MCP lifecycle imports remain:\n" + "\n".join(
        violations
    )

    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(
            alias.name.startswith("mind_app") for alias in node.names
        ):
            violations.append(f"{owner_path.relative_to(PROJECT_ROOT)}:{node.lineno}")
        if isinstance(node, ast.ImportFrom) and node.level == 0:
            module = node.module or ""
            if module.startswith("mind_app"):
                violations.append(f"{owner_path.relative_to(PROJECT_ROOT)}:{node.lineno}")
    assert not violations, "Harness MCP owner crosses legacy boundary:\n" + "\n".join(
        violations
    )

    port_source = (
        PROJECT_ROOT / "agent" / "ports" / "mcp_runtime.py"
    ).read_text(encoding="utf-8-sig")
    runtime_source = (
        PROJECT_ROOT / "infrastructure" / "mcp" / "external_runtime.py"
    ).read_text(encoding="utf-8-sig")
    assert "class McpRuntimeContext" in port_source
    assert "class McpRuntimeHost" not in port_source
    assert "self._host" not in runtime_source
    assert "config_session" not in runtime_source


def test_helix_lifecycle_adapter_is_owned_by_infrastructure() -> None:
    """确保服务生命周期和具体 Helix capability 均由 infrastructure 持有。"""
    adapter_path = PROJECT_ROOT / "infrastructure" / "services" / "helix_capability.py"
    owner_path = PROJECT_ROOT / "infrastructure" / "services" / "runtime_owner.py"
    legacy_path = PROJECT_ROOT / "mind_app" / "runtime" / "mcp" / "service_lifecycle.py"

    assert adapter_path.is_file(), "Helix lifecycle adapter is missing"
    assert owner_path.is_file(), "service runtime owner is missing"
    assert not legacy_path.is_file(), "legacy service runtime owner still exists"
    owner_source = owner_path.read_text(encoding="utf-8-sig")
    assert "class ServiceRuntimeOwner" in owner_source
    assert "mind_app" not in owner_source

    violations: list[str] = []
    for path in _all_python_sources():
        tree = _parsed_source(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 0:
                module = node.module or ""
                if module == "mind_app.runtime.mcp.service_lifecycle":
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno}"
                    )

    assert not violations, "legacy Helix adapter imports remain:\n" + "\n".join(
        violations
    )


def test_service_runtime_setup_is_infrastructure_owned() -> None:
    """确保服务 setup 归基础设施、前台启动编排归可替换前端。"""
    setup_path = PROJECT_ROOT / "infrastructure" / "services" / "runtime_setup.py"
    environment_path = (
        PROJECT_ROOT
        / "infrastructure"
        / "services"
        / "helix_environment.py"
    )
    runtime_path = PROJECT_ROOT / "frontends" / "helix" / "runtime.py"
    legacy_paths = (
        PROJECT_ROOT / "mind_app" / "runtime" / "mcp" / "service_runtime.py",
        PROJECT_ROOT / "mind_app" / "runtime" / "mcp" / "service_exec_env.py",
    )
    assert setup_path.is_file(), "service runtime setup module is missing"
    assert environment_path.is_file(), "Helix environment adapter is missing"
    assert runtime_path.is_file(), "frontend Helix orchestration is missing"
    assert not any(path.is_file() for path in legacy_paths)

    setup_source = setup_path.read_text(encoding="utf-8-sig")
    environment_source = environment_path.read_text(encoding="utf-8-sig")
    runtime_source = runtime_path.read_text(encoding="utf-8-sig")
    assert "mind_app" not in setup_source
    assert "mind_app" not in environment_source
    assert "mind_app" not in runtime_source

    moved_names = {
        "runtime_status",
        "resolve_service_runtime",
        "prepend_runtime_paths",
        "verify_runtime_paths",
        "authorize_runtime_files",
        "service_runtime_asset_missing",
        "ensure_runtime_started",
    }
    runtime_tree = ast.parse(
        runtime_path.read_text(encoding="utf-8-sig"),
        filename=str(runtime_path),
    )
    definitions = {
        node.name
        for node in runtime_tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert not definitions.intersection(moved_names)

    violations: list[str] = []
    for path in _all_python_sources():
        tree = _parsed_source(path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.level != 0:
                continue
            if node.module not in {
                "mind_app.runtime.mcp.service_runtime",
                "mind_app.runtime.mcp.service_exec_env",
            }:
                continue
            violations.append(
                f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> "
                f"{node.module}"
            )
    assert not violations, "legacy Helix imports remain:\n" + "\n".join(violations)


def test_external_mcp_infrastructure_has_responsibility_modules() -> None:
    """确保 MCP 配置、SDK 会话和连接生命周期归属基础设施。"""
    target_root = PROJECT_ROOT / "infrastructure" / "mcp"
    assert {
        path.name
        for path in target_root.glob("*.py")
    } == {
        "__init__.py",
        "approval.py",
        "approval_policy.py",
        "composite_session.py",
        "errors.py",
        "external_group.py",
        "external_runtime.py",
        "external_status.py",
        "hook_runner.py",
        "local_session.py",
        "local_tool_factory.py",
        "local_tool_registry.py",
        "nested_tool_results.py",
        "registry.py",
        "settings.py",
        "tool_catalog.py",
        "tool_execution.py",
        "tool_invocation.py",
        "tool_results.py",
        "tool_runtime.py",
        "transport.py",
        "values.py",
    }

    legacy_paths = (
        PROJECT_ROOT / "mind_app" / "runtime" / "mcp" / "config.py",
        PROJECT_ROOT / "mind_app" / "runtime" / "mcp" / "errors.py",
        PROJECT_ROOT / "mind_app" / "runtime" / "mcp" / "external.py",
        PROJECT_ROOT / "mind_app" / "runtime" / "mcp" / "group.py",
        PROJECT_ROOT / "mind_app" / "runtime" / "mcp" / "local.py",
        PROJECT_ROOT / "mind_app" / "runtime" / "mcp" / "registry.py",
        PROJECT_ROOT / "mind_app" / "runtime" / "mcp" / "session_adapter.py",
        PROJECT_ROOT / "mind_app" / "runtime" / "mcp" / "status.py",
        PROJECT_ROOT / "mind_app" / "runtime" / "mcp" / "tool_progress.py",
        PROJECT_ROOT / "mind_app" / "runtime" / "mcp" / "tool_result.py",
        PROJECT_ROOT / "mind_app" / "runtime" / "mcp" / "tool_runtime.py",
        PROJECT_ROOT / "mind_app" / "runtime" / "mcp" / "tool_store.py",
        PROJECT_ROOT / "mind_app" / "runtime" / "mcp" / "tools.py",
    )
    assert not any(path.is_file() for path in legacy_paths)

    violations = _forbidden_imports(
        "infrastructure/mcp",
        {"backend", "engine", "frontends", "mind_app", "mind_core", "server"},
    )
    assert not violations, "MCP infrastructure crosses its boundary:\n" + (
        "\n".join(violations)
    )

    legacy_imports = _forbidden_module_imports(
        ".",
        {
            "mind_app.runtime.mcp.config",
            "mind_app.runtime.mcp.errors",
            "mind_app.runtime.mcp.external",
            "mind_app.runtime.mcp.group",
            "mind_app.runtime.mcp.local",
            "mind_app.runtime.mcp.registry",
            "mind_app.runtime.mcp.session_adapter",
            "mind_app.runtime.mcp.status",
            "mind_app.runtime.mcp.tool_progress",
            "mind_app.runtime.mcp.tool_result",
            "mind_app.runtime.mcp.tool_runtime",
            "mind_app.runtime.mcp.tool_store",
            "mind_app.runtime.mcp.tools",
        },
    )
    assert not legacy_imports, "legacy MCP infrastructure imports remain:\n" + (
        "\n".join(legacy_imports)
    )


def test_local_tool_contracts_have_single_ownership_boundary() -> None:
    """确保本地工具契约与 MCP 注册表不在旧能力包中重复实现。"""
    legacy_paths = (
        PROJECT_ROOT / "mind_app" / "builtin_tools" / "registry.py",
        PROJECT_ROOT / "mind_app" / "builtin_tools" / "permissions.py",
        PROJECT_ROOT / "mind_app" / "builtin_tools" / "types.py",
        PROJECT_ROOT / "mind_app" / "client_tools" / "registry.py",
        PROJECT_ROOT / "mind_app" / "client_tools" / "result.py",
        PROJECT_ROOT / "mind_app" / "client_tools" / "planning.py",
        PROJECT_ROOT / "mind_app" / "client_tools" / "subagents.py",
        PROJECT_ROOT / "mind_app" / "client_tools" / "types.py",
        PROJECT_ROOT / "mind_app" / "client_tools" / "update_plan.py",
        PROJECT_ROOT / "mind_app" / "client_tools" / "view_image.py",
        PROJECT_ROOT / "mind_app" / "client_tools" / "coding" / "schemas.py",
        PROJECT_ROOT / "mind_app" / "client_tools" / "coding" / "native.py",
        PROJECT_ROOT / "mind_app" / "client_tools" / "coding" / "__init__.py",
        PROJECT_ROOT / "mind_app" / "client_tools" / "factory.py",
        PROJECT_ROOT / "mind_app" / "client_tools" / "__init__.py",
        PROJECT_ROOT / "mind_app" / "native_coding" / "execution_authorization.py",
    )
    assert not any(path.is_file() for path in legacy_paths)
    assert not any(
        (PROJECT_ROOT / "mind_app" / "builtin_tools").glob("*.py")
    )

    legacy_modules = {
        "mind_app.builtin_tools.registry",
        "mind_app.builtin_tools.permissions",
        "mind_app.builtin_tools.types",
        "mind_app.client_tools.registry",
        "mind_app.client_tools.result",
        "mind_app.client_tools.planning",
        "mind_app.client_tools.subagents",
        "mind_app.client_tools.types",
        "mind_app.client_tools.update_plan",
        "mind_app.client_tools.view_image",
        "mind_app.client_tools.coding.schemas",
        "mind_app.client_tools.coding.native",
        "mind_app.client_tools.factory",
        "mind_app.native_coding.execution_authorization",
    }
    violations = _forbidden_module_imports(".", legacy_modules)
    assert not violations, "legacy local tool imports remain:\n" + "\n".join(
        violations
    )

    tool_boundary_violations = _forbidden_imports(
        "agent/application/tools",
        {"agent.harness", "agent.stores", "infrastructure", "mind_app"},
    )
    assert not tool_boundary_violations, (
        "application tools cross their execution boundary:\n"
        + "\n".join(tool_boundary_violations)
    )

    assert not any(
        (PROJECT_ROOT / "mind_app" / "client_tools").glob("**/*.py")
    )

    factory_path = (
        PROJECT_ROOT / "infrastructure" / "mcp" / "local_tool_factory.py"
    )
    factory_tree = ast.parse(
        factory_path.read_text(encoding="utf-8-sig"),
        filename=str(factory_path),
    )
    factory_classes = {
        node.name
        for node in factory_tree.body
        if isinstance(node, ast.ClassDef)
    }
    assert not factory_classes, "client tool factory must not own registry state"


def test_tool_runtime_is_composed_at_process_root() -> None:
    """确保组合根注入工具实现且执行资源拥有动态会话状态。"""
    reverse_imports = _forbidden_module_imports(
        "mind_app",
        {"infrastructure.mcp.tool_runtime"},
    )
    assert not reverse_imports, "legacy application constructs tool runtime:\n" + (
        "\n".join(reverse_imports)
    )

    composition = (PROJECT_ROOT / "mind.py").read_text(encoding="utf-8-sig")
    host = (PROJECT_ROOT / "composition.py").read_text(encoding="utf-8-sig")
    resources = (
        PROJECT_ROOT / "agent" / "harness" / "execution" / "resources.py"
    ).read_text(encoding="utf-8-sig")
    runtime = (
        PROJECT_ROOT / "infrastructure" / "mcp" / "tool_runtime.py"
    ).read_text(encoding="utf-8-sig")

    assert "def create_tool_runtime(" in composition
    assert "create_tool_runtime=create_tool_runtime" in composition
    assert "create_client_tool_registry=build_client_tool_registry" in composition
    assert "create_builtin_tool_registry=build_builtin_tool_registry" in composition
    assert "ToolRuntimeSources(" in resources
    assert "ToolRuntimeSources(" not in host
    assert "CompositeToolRuntime" not in host
    assert "build_client_tool_registry" not in host
    assert "build_builtin_tool_registry" not in host
    assert "def with_mcp_session(" not in host
    assert "def link_service_mcp(" not in host
    assert "def unlink_service_mcp(" not in host
    assert "def is_service_mcp_linked(" not in host
    assert "self.external_mcp" not in host
    assert "self.client_tools" not in host
    assert "self.builtin_tools" not in host
    assert "self.event_reporting" not in host
    assert "ClientToolProvider" not in runtime
    assert "BuiltinToolProvider" not in runtime
    assert "ExternalMcpProvider" not in runtime
    assert "ServiceMcpProvider" not in runtime


def test_frontend_output_sanitizer_has_no_legacy_source() -> None:
    """确保前端输出脱敏不再由旧应用平铺模块持有。"""
    target_path = PROJECT_ROOT / "frontends" / "output" / "sanitize.py"
    legacy_path = PROJECT_ROOT / "mind_app" / "stream_sanitize.py"
    assert target_path.is_file()
    assert not legacy_path.is_file()

    legacy_imports = _forbidden_module_imports(
        ".",
        {"mind_app.stream_sanitize"},
    )
    assert not legacy_imports, "legacy sanitizer imports remain:\n" + (
        "\n".join(legacy_imports)
    )


def test_process_encoding_has_one_platform_owner() -> None:
    """确保进程输出解码只由平台基础设施实现。"""
    legacy_path = PROJECT_ROOT / "mind_app" / "native_coding" / "encoding.py"
    assert not legacy_path.is_file(), "legacy native coding encoding still exists"

    violations: list[str] = []
    for path in _all_python_sources():
        tree = _parsed_source(path)
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
    for path in _all_python_sources():
        tree = _parsed_source(path)
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


def test_workspace_coding_has_infrastructure_ownership() -> None:
    """确保工作区编码实现按领域与基础设施边界归位。"""
    legacy_root = PROJECT_ROOT / "mind_app" / "native_coding"
    assert not any(legacy_root.rglob("*.py"))

    legacy_imports = _forbidden_module_imports(
        ".",
        {"mind_app.native_coding"},
    )
    assert not legacy_imports, "legacy workspace imports remain:\n" + (
        "\n".join(legacy_imports)
    )

    domain_violations = _forbidden_imports(
        "agent/domain/patches",
        {
            "backend",
            "engine",
            "frontends",
            "infrastructure",
            "metadata",
            "mind_app",
            "mind_core",
            "observability",
            "protocol",
            "server",
        },
    )
    assert not domain_violations, "patch domain crosses boundaries:\n" + (
        "\n".join(domain_violations)
    )

    infrastructure_violations = _forbidden_imports(
        "infrastructure/workspace",
        {"backend", "engine", "frontends", "mind_app", "mind_core", "server"},
    )
    assert not infrastructure_violations, (
        "workspace infrastructure crosses boundaries:\n"
        + "\n".join(infrastructure_violations)
    )

    runtime = (PROJECT_ROOT / "infrastructure" / "workspace" / "runtime.py")
    source = runtime.read_text(encoding="utf-8-sig")
    assert "class WorkspaceCoding(" in source
    assert "class NativeCoding(" not in source

    composition = (PROJECT_ROOT / "mind.py").read_text(encoding="utf-8-sig")
    assert "from infrastructure.workspace.runtime import WorkspaceCoding" in composition
    assert "from mind_app.native_coding" not in composition


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
    for path in _all_python_sources():
        tree = _parsed_source(path)
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
    process_sessions = (
        PROJECT_ROOT / "infrastructure" / "platform" / "process_sessions.py"
    )
    assert process_sessions.is_file(), "platform process session manager is missing"

    legacy_paths = (
        PROJECT_ROOT / "mind_app" / "native_coding" / "exec" / "process_capture.py",
        PROJECT_ROOT / "mind_app" / "native_coding" / "exec" / "process_session.py",
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
        f"{legacy_prefix}process_session",
        f"{legacy_prefix}output_decoder",
        f"{legacy_prefix}sandbox_client",
        f"{legacy_prefix}shell_runtime",
    }
    violations: list[str] = []
    for path in _all_python_sources():
        tree = _parsed_source(path)
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

    workspace_runtime_path = (
        PROJECT_ROOT / "infrastructure" / "workspace" / "runtime.py"
    )
    workspace_runtime_tree = ast.parse(
        workspace_runtime_path.read_text(encoding="utf-8-sig"),
        filename=str(workspace_runtime_path),
    )
    workspace_runtime_imports = {
        node.module or ""
        for node in ast.walk(workspace_runtime_tree)
        if isinstance(node, ast.ImportFrom) and node.level == 0
    }
    assert "infrastructure.platform.sandbox" not in workspace_runtime_imports
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "SandboxClient"
        for node in ast.walk(workspace_runtime_tree)
    )

    composition_path = PROJECT_ROOT / "mind.py"
    composition_source = composition_path.read_text(encoding="utf-8-sig")
    assert "SandboxClient(" in composition_source
    assert "ProcessSessionManager(" in composition_source


def test_hook_output_spill_has_platform_ownership() -> None:
    """确保 Hook 大输出的临时文件生命周期由平台基础设施持有。"""
    legacy_path = PROJECT_ROOT / "mind_app" / "runtime" / "hooks" / "output_spill.py"
    assert not legacy_path.is_file(), "legacy Hook output spill source still exists"

    legacy_modules = {"mind_app.runtime.hooks.output_spill"}
    violations: list[str] = []
    for path in _all_python_sources():
        tree = _parsed_source(path)
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


def test_javascript_sidecar_has_explicit_adapter_ownership() -> None:
    """确保不可变 Bundle、Python Adapter 和 Harness 生命周期边界分离。"""
    assert (PROJECT_ROOT / "sidecars" / "js_repl" / "kernel.js").is_file()
    assert (
        PROJECT_ROOT / "sidecars" / "js_repl" / "vendor" / "meriyah.umd.min.js"
    ).is_file()
    assert not (PROJECT_ROOT / "js_repl").exists()
    assert not (
        PROJECT_ROOT / "infrastructure" / "platform" / "javascript_repl.py"
    ).exists()

    workspace = (
        PROJECT_ROOT / "infrastructure" / "workspace" / "runtime.py"
    ).read_text(encoding="utf-8-sig")
    workspace_port = (
        PROJECT_ROOT / "agent" / "ports" / "workspace.py"
    ).read_text(encoding="utf-8-sig")
    composition = (PROJECT_ROOT / "mind.py").read_text(encoding="utf-8-sig")
    host = (PROJECT_ROOT / "composition.py").read_text(encoding="utf-8-sig")

    assert "JavaScriptSidecarProvider" not in workspace
    assert "close_js_repl_session" not in workspace
    assert "WorkspaceJavaScriptPort" not in workspace_port
    assert "def create_javascript_provider(" in composition
    assert "javascript_execution=javascript" in composition
    assert "javascript_lifecycle=javascript" in composition
    assert "close_javascript=self.javascript_lifecycle.close" in host


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
    for path in _all_python_sources():
        tree = _parsed_source(path)
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
    for path in _all_python_sources():
        tree = _parsed_source(path)
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
    for path in _all_python_sources():
        tree = _parsed_source(path)
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

    application_path = PROJECT_ROOT / "agent" / "application" / "turns" / "environment.py"
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
