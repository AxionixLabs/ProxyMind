# -*- coding: utf-8 -*-

"""审计 Agent 运行时端口、Harness 编排与适配器依赖方向。"""


import ast
from tests.architecture.source_inventory import (
    PROJECT_ROOT,
    all_python_sources as _all_python_sources,
    parsed_source as _parsed_source,
)


def test_turn_execution_contract_is_owned_by_application() -> None:
    """确保 TurnExecution 只由 application 持有，Harness runner 不定义值对象。"""
    legacy_path = PROJECT_ROOT / "mind_app" / "runtime" / "turns" / "executor.py"
    runner_path = PROJECT_ROOT / "agent" / "harness" / "execution" / "turn_runner.py"
    target_path = PROJECT_ROOT / "agent" / "application" / "turns" / "execution.py"
    ports_path = PROJECT_ROOT / "agent" / "ports" / "hooks.py"
    assert not legacy_path.is_file(), "legacy runtime turn executor still exists"
    assert runner_path.is_file(), "Harness turn runner is missing"
    assert target_path.is_file(), "application turn execution contract is missing"
    assert ports_path.is_file(), "hook execution scope port is missing"

    violations: list[str] = []
    for path in _all_python_sources():
        tree = _parsed_source(path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.level != 0:
                continue
            if node.module != "mind_app.runtime.turns.executor":
                continue
            violations.append(f"{path.relative_to(PROJECT_ROOT)}:{node.lineno}")
    assert not violations, "legacy turn executor imports remain:\n" + "\n".join(violations)

    target_tree = ast.parse(target_path.read_text(encoding="utf-8-sig"), filename=str(target_path))
    target_classes = {
        node.name
        for node in target_tree.body
        if isinstance(node, ast.ClassDef)
    }
    assert target_classes == {"TurnExecution"}
    target_violations: list[str] = []
    for node in ast.walk(target_tree):
        modules: tuple[str, ...] = ()
        if isinstance(node, ast.Import):
            modules = tuple(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            modules = (node.module or "",)
        for module in modules:
            if module.partition(".")[0] in {"mind_app", "mind_core", "engine", "server", "infrastructure"}:
                target_violations.append(
                    f"{target_path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                )
    assert not target_violations, "application TurnExecution crosses its boundary:\n" + "\n".join(target_violations)

    port_tree = ast.parse(ports_path.read_text(encoding="utf-8-sig"), filename=str(ports_path))
    port_classes = {
        node.name
        for node in port_tree.body
        if isinstance(node, ast.ClassDef)
    }
    assert "HookExecutionScopePort" in port_classes


def test_turn_executor_uses_runtime_port_without_controller_reflection() -> None:
    """确保 Harness runner 只消费 Turn 运行时端口，不反射具体 Controller。"""
    target_path = PROJECT_ROOT / "agent" / "harness" / "execution" / "turn_runner.py"
    tree = ast.parse(target_path.read_text(encoding="utf-8-sig"), filename=str(target_path))

    imported_modules: list[str] = []
    imported_names: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            imported_modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            imported_modules.append(node.module or "")
            imported_names.extend(alias.name for alias in node.names)

    assert "mind_app.controller" not in imported_modules
    assert "TurnExecutionRuntimePort" in imported_names
    imported_roots = {
        module.partition(".")[0]
        for module in imported_modules
    }
    assert not imported_roots & {
        "backend",
        "engine",
        "frontends",
        "infrastructure",
        "mind_app",
        "mind_core",
        "protocol",
        "server",
    }

    reflected = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "getattr"
    ]
    assert not reflected


def test_root_turn_preparation_uses_session_port() -> None:
    """确保根轮次准备只读取显式会话端口。"""
    legacy_root = PROJECT_ROOT / "mind_app" / "runtime" / "turns"
    target_path = (
        PROJECT_ROOT / "agent" / "harness" / "execution" / "root_runner.py"
    )
    assert not any(legacy_root.glob("*.py")), "legacy runtime turns sources remain"
    tree = ast.parse(target_path.read_text(encoding="utf-8-sig"), filename=str(target_path))
    imported_roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(
                alias.name.partition(".")[0]
                for alias in node.names
            )
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            imported_roots.add((node.module or "").partition(".")[0])
    assert not imported_roots & {
        "backend",
        "engine",
        "frontends",
        "infrastructure",
        "mind_app",
        "mind_core",
        "server",
    }
    prepare = next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "prepare_root_turn"
    )

    assert prepare.args.args[0].arg == "session"
    controller_accesses = [
        node
        for node in ast.walk(prepare)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "controller"
    ]
    assert not controller_accesses


def test_subagent_runtime_does_not_store_controller_owner() -> None:
    """确保 Subagent runtime 的流式 owner 使用显式执行端口。"""
    target_path = PROJECT_ROOT / "agent" / "harness" / "agents" / "runtime.py"
    legacy_path = PROJECT_ROOT / "mind_app" / "runtime" / "subagents" / "runtime.py"
    assert not legacy_path.exists()
    tree = ast.parse(target_path.read_text(encoding="utf-8-sig"), filename=str(target_path))

    stored_controller = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
        and node.attr == "_controller"
    ]
    assert not stored_controller

    concrete_execution_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in {"execute_turn", "stream_turn"}
    ]
    assert not concrete_execution_calls


def test_mcp_session_contract_is_owned_by_agent_ports() -> None:
    """确保 MCP 会话只由 agent ports 定义，runtime 不保留协议契约。"""
    legacy_path = PROJECT_ROOT / "mind_app" / "runtime" / "mcp" / "contracts.py"
    target_path = PROJECT_ROOT / "agent" / "ports" / "mcp_session.py"
    assert not legacy_path.is_file(), "legacy MCP session contract still exists"
    assert target_path.is_file(), "agent MCP session port is missing"

    violations: list[str] = []
    for path in _all_python_sources():
        tree = _parsed_source(path)
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            if "mind_app.runtime.mcp.contracts" in modules:
                violations.append(f"{path.relative_to(PROJECT_ROOT)}:{node.lineno}")
    assert not violations, "legacy MCP session imports remain:\n" + "\n".join(violations)

    target_tree = ast.parse(target_path.read_text(encoding="utf-8-sig"), filename=str(target_path))
    target_classes = {
        node.name
        for node in target_tree.body
        if isinstance(node, ast.ClassDef)
    }
    assert target_classes == {"McpSessionPort"}
    target_violations: list[str] = []
    for node in ast.walk(target_tree):
        modules: tuple[str, ...] = ()
        if isinstance(node, ast.Import):
            modules = tuple(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            modules = (node.module or "",)
        for module in modules:
            if module.partition(".")[0] in {"mind_app", "mind_core", "engine", "server", "infrastructure"}:
                target_violations.append(
                    f"{target_path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                )
    assert not target_violations, "MCP session port crosses its boundary:\n" + "\n".join(target_violations)


def test_turn_and_subagent_execution_ports_are_owned_by_agent_ports() -> None:
    """确保 Turn/Subagent 调用协议不由具体 runtime executor 定义。"""
    targets = {
            PROJECT_ROOT / "agent" / "ports" / "turns.py": {
                "EventReportPort",
                "TurnCleanupPort",
                "TurnEventReportHandle",
                "TurnEventReportingPort",
                "TurnExecutionRuntimePort",
                "TurnStartResultPort",
                "RootTurnSessionPort",
                "TurnInputEventHandler",
                "TurnOperation",
                "TurnSessionContextPort",
                "TurnSessionStatePort",
                "TurnResultPort",
        },
        PROJECT_ROOT / "agent" / "ports" / "subagents.py": {
            "SubagentExecutionPort",
            "SubagentStreamPort",
            "SubagentOperation",
            "SubagentTurnRunner",
            "SubagentCleanupPort",
            "SubagentControlPort",
            "SubagentRuntimeHostPort",
        },
    }
    legacy_path = PROJECT_ROOT / "mind_app" / "runtime" / "turns" / "executor.py"
    implementation_paths = (
        PROJECT_ROOT / "agent" / "harness" / "execution" / "turn_runner.py",
        PROJECT_ROOT / "agent" / "harness" / "agents" / "runtime.py",
    )
    assert not legacy_path.is_file(), "legacy runtime executor still exists"

    for target_path, expected_classes in targets.items():
        assert target_path.is_file(), f"execution port is missing: {target_path}"
        tree = ast.parse(target_path.read_text(encoding="utf-8-sig"), filename=str(target_path))
        classes = {
            node.name
            for node in tree.body
            if isinstance(node, ast.ClassDef)
        }
        assert classes == expected_classes
        violations: list[str] = []
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            for module in modules:
                if module.partition(".")[0] in {"mind_app", "mind_core", "engine", "server", "infrastructure"}:
                    violations.append(
                        f"{target_path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                    )
        assert not violations, "execution port crosses its boundary:\n" + "\n".join(violations)

    legacy_violations: list[str] = []
    for path in implementation_paths:
        tree = _parsed_source(path)
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name in {
                "TurnResult",
                "TurnOperation",
                "SubagentExecutionPort",
                "SubagentOperation",
            }:
                legacy_violations.append(f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {node.name}")
    assert not legacy_violations, "runtime execution contracts remain:\n" + "\n".join(legacy_violations)


def test_subagent_hook_events_are_owned_by_application() -> None:
    """确保子 Agent Hook 生命周期聚合不依赖 runtime scope 实现。"""
    legacy_path = PROJECT_ROOT / "mind_app" / "runtime" / "hooks" / "subagent.py"
    target_path = PROJECT_ROOT / "agent" / "application" / "hooks" / "subagent.py"
    assert not legacy_path.is_file(), "legacy subagent hook module still exists"
    assert target_path.is_file(), "application subagent hook module is missing"

    target_tree = ast.parse(target_path.read_text(encoding="utf-8-sig"), filename=str(target_path))
    target_classes = {
        node.name
        for node in target_tree.body
        if isinstance(node, ast.ClassDef)
    }
    assert target_classes == {"SubagentHookEvents"}
    violations: list[str] = []
    for node in ast.walk(target_tree):
        modules: tuple[str, ...] = ()
        if isinstance(node, ast.Import):
            modules = tuple(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            modules = (node.module or "",)
        for module in modules:
            if module.partition(".")[0] in {"mind_app", "mind_core", "engine", "server", "infrastructure"}:
                violations.append(
                    f"{target_path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                )
    assert not violations, "application subagent hooks cross their boundary:\n" + "\n".join(violations)

    import_violations: list[str] = []
    for path in _all_python_sources():
        tree = _parsed_source(path)
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            if "mind_app.runtime.hooks.subagent" in modules:
                import_violations.append(f"{path.relative_to(PROJECT_ROOT)}:{node.lineno}")
    assert not import_violations, "legacy subagent hook imports remain:\n" + "\n".join(import_violations)


def test_subagent_runner_is_owned_by_harness_without_package_cycle() -> None:
    """确保 SubagentRunner 由 Harness 持有且包初始化不预加载组件。"""
    legacy_path = PROJECT_ROOT / "mind_app" / "runtime" / "subagents" / "runner.py"
    target_path = PROJECT_ROOT / "agent" / "harness" / "execution" / "subagent_runner.py"
    package_path = PROJECT_ROOT / "agent" / "harness" / "__init__.py"
    assert not legacy_path.is_file(), "legacy subagent runner still exists"
    assert target_path.is_file(), "harness subagent runner is missing"
    assert package_path.is_file(), "harness package initializer is missing"

    target_tree = ast.parse(target_path.read_text(encoding="utf-8-sig"), filename=str(target_path))
    target_classes = {
        node.name
        for node in target_tree.body
        if isinstance(node, ast.ClassDef)
    }
    assert target_classes == {"SubagentRunner"}
    violations: list[str] = []
    for node in ast.walk(target_tree):
        modules: tuple[str, ...] = ()
        if isinstance(node, ast.Import):
            modules = tuple(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            modules = (node.module or "",)
        for module in modules:
            if module.partition(".")[0] in {"mind_app", "mind_core", "engine", "server", "infrastructure"}:
                violations.append(
                    f"{target_path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                )
    assert not violations, "harness subagent runner crosses legacy boundary:\n" + "\n".join(violations)

    package_tree = ast.parse(package_path.read_text(encoding="utf-8-sig"), filename=str(package_path))
    package_imports = [
        node
        for node in package_tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
    ]
    assert not package_imports, "harness package initializer must not preload components"


def test_subagent_submission_execution_is_owned_by_harness() -> None:
    """确保已分配提交的执行协调不回流到 mind_app runtime。"""
    target_path = PROJECT_ROOT / "agent" / "harness" / "execution" / "subagent_submission.py"
    runtime_path = PROJECT_ROOT / "agent" / "harness" / "agents" / "runtime.py"
    legacy_path = PROJECT_ROOT / "mind_app" / "runtime" / "subagents" / "runtime.py"
    assert target_path.is_file(), "harness subagent submission executor is missing"
    assert runtime_path.is_file(), "subagent runtime is missing"
    assert not legacy_path.exists(), "legacy subagent runtime remains"

    target_tree = ast.parse(target_path.read_text(encoding="utf-8-sig"), filename=str(target_path))
    target_classes = {
        node.name
        for node in target_tree.body
        if isinstance(node, ast.ClassDef)
    }
    assert target_classes == {"SubagentSubmissionExecutor", "SubagentTurnFailedError"}

    violations: list[str] = []
    for node in ast.walk(target_tree):
        modules: tuple[str, ...] = ()
        if isinstance(node, ast.Import):
            modules = tuple(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            modules = (node.module or "",)
        for module in modules:
            if module.partition(".")[0] in {"mind_app", "mind_core", "engine", "server", "infrastructure"}:
                violations.append(
                    f"{target_path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                )
    assert not violations, "harness subagent submission crosses legacy boundary:\n" + "\n".join(violations)

    runtime_tree = ast.parse(runtime_path.read_text(encoding="utf-8-sig"), filename=str(runtime_path))
    runtime_definitions = {
        node.name
        for node in runtime_tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "SubagentTurnFailedError" not in runtime_definitions
    assert "_execute_submission" not in runtime_definitions


def test_subagent_runtime_only_orchestrates_injected_ports() -> None:
    """确保 SubagentRuntime 不重新拥有流式和 Turn 执行实现。"""
    runtime_path = PROJECT_ROOT / "agent" / "harness" / "agents" / "runtime.py"
    legacy_path = PROJECT_ROOT / "mind_app" / "runtime" / "subagents" / "runtime.py"
    assert not legacy_path.exists(), "legacy SubagentRuntime path remains"
    tree = ast.parse(runtime_path.read_text(encoding="utf-8-sig"), filename=str(runtime_path))

    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            imported_modules.add(node.module or "")

    forbidden = {
        "mind_app.runtime.turns",
        "frontends.output",
        "agent.adapters.agents.execution",
    }
    assert not any(
        module == target or module.startswith(f"{target}.")
        for module in imported_modules
        for target in forbidden
    ), "SubagentRuntime owns legacy execution dependencies"

    runtime_definitions = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert runtime_definitions == {"SubagentRuntime", "_normalize_task"}
    runtime_class = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "SubagentRuntime"
    )
    init_method = next(
        node
        for node in runtime_class.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "__init__"
    )
    init_arguments = {
        argument.arg
        for argument in (*init_method.args.args, *init_method.args.kwonlyargs)
    }
    assert not init_arguments.intersection({"executor", "turn_runner"})


def test_subagent_stream_execution_is_owned_by_adapter() -> None:
    """确保具体流式 Subagent 执行器由 adapter 持有且不反向加载旧应用。"""
    legacy_path = PROJECT_ROOT / "mind_app" / "runtime" / "subagents" / "executor.py"
    target_path = PROJECT_ROOT / "agent" / "adapters" / "agents" / "execution.py"
    assert not legacy_path.is_file(), "legacy subagent executor still exists"
    assert target_path.is_file(), "subagent execution adapter is missing"

    target_tree = ast.parse(target_path.read_text(encoding="utf-8-sig"), filename=str(target_path))
    target_classes = {
        node.name
        for node in target_tree.body
        if isinstance(node, ast.ClassDef)
    }
    assert target_classes == {"StreamSubagentExecution"}
    violations: list[str] = []
    for node in ast.walk(target_tree):
        modules: tuple[str, ...] = ()
        if isinstance(node, ast.Import):
            modules = tuple(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            modules = (node.module or "",)
        for module in modules:
            if module.partition(".")[0] in {"mind_app", "mind_core", "engine", "server", "infrastructure"}:
                violations.append(
                    f"{target_path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                )
    assert not violations, "subagent adapter crosses legacy boundary:\n" + "\n".join(violations)

    import_violations: list[str] = []
    for path in _all_python_sources():
        tree = _parsed_source(path)
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            if "mind_app.runtime.subagents.executor" in modules:
                import_violations.append(f"{path.relative_to(PROJECT_ROOT)}:{node.lineno}")
    assert not import_violations, "legacy subagent executor imports remain:\n" + "\n".join(import_violations)


def test_subagent_turn_adapter_receives_output_factory() -> None:
    """确保子 Agent Turn 适配器不自行选择具体输出实现。"""
    legacy_path = (
        PROJECT_ROOT / "mind_app" / "runtime" / "turns" / "subagent_adapter.py"
    )
    target_path = (
        PROJECT_ROOT / "agent" / "adapters" / "protocol" / "subagent_stream.py"
    )
    assert not legacy_path.is_file(), "legacy subagent turn adapter remains"
    tree = ast.parse(target_path.read_text(encoding="utf-8-sig"), filename=str(target_path))
    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            imported_modules.add(node.module or "")

    assert "frontends.output.silent" not in imported_modules
    execution_class = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and node.name == "ProtocolSubagentStream"
    )
    init_method = next(
        node
        for node in execution_class.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "__init__"
    )
    init_arguments = {
        argument.arg
        for argument in (*init_method.args.args, *init_method.args.kwonlyargs)
    }
    assert "session_factory" in init_arguments


def test_tool_progress_policy_and_dispatch_have_single_owners() -> None:
    """确保进度策略归 domain，投递归工具执行编排，旧支持模块全部删除。"""
    legacy_paths = (
        PROJECT_ROOT / "mind_app" / "runtime" / "tools" / "notify.py",
        PROJECT_ROOT / "mind_app" / "runtime" / "tools" / "policy.py",
        PROJECT_ROOT / "mind_app" / "runtime" / "mcp" / "tool_progress.py",
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
        "mind_app.runtime.mcp.tool_progress",
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

    assert not violations, "legacy tool support imports remain:\n" + "\n".join(violations)
    policy = (
        PROJECT_ROOT / "agent" / "domain" / "tool_policy.py"
    ).read_text(encoding="utf-8-sig")
    projection = (
        PROJECT_ROOT / "agent" / "application" / "views" / "tool_execution.py"
    ).read_text(encoding="utf-8-sig")
    assert "def supports_progress_notifications(" in policy
    assert "async def show_tool_progress(" in projection


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


def test_hook_command_executor_is_owned_by_platform_infrastructure() -> None:
    """确保 Hook 子进程执行器不再由 runtime hooks 持有。"""
    legacy_path = PROJECT_ROOT / "mind_app" / "runtime" / "hooks" / "command.py"
    target_path = PROJECT_ROOT / "infrastructure" / "platform" / "hook_command.py"

    assert not legacy_path.is_file(), "legacy runtime HookCommandExecutor still exists"
    assert target_path.is_file(), "platform HookCommandExecutor is missing"

    target_tree = ast.parse(
        target_path.read_text(encoding="utf-8-sig"),
        filename=str(target_path),
    )
    target_classes = {
        node.name
        for node in target_tree.body
        if isinstance(node, ast.ClassDef)
    }
    assert {"HookCommandError", "HookCommandOutput", "HookCommandExecutor"}.issubset(
        target_classes
    )

    legacy_imports: list[str] = []
    for path in _all_python_sources():
        tree = _parsed_source(path)
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            if "mind_app.runtime.hooks.command" in modules:
                legacy_imports.append(f"{path.relative_to(PROJECT_ROOT)}:{node.lineno}")
    assert not legacy_imports, "legacy HookCommandExecutor imports remain:\n" + "\n".join(
        legacy_imports
    )

    runtime_path = PROJECT_ROOT / "agent" / "harness" / "hooks" / "runtime.py"
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
        "agent/harness/hooks/runtime.py",
        "agent/harness/hooks/registry.py",
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


def test_hook_runtime_and_registry_are_harness_owned() -> None:
    """确保 Hook 执行状态和 registry 生命周期归 Harness 且不反向依赖平台。"""
    legacy_paths = (
        PROJECT_ROOT / "mind_app" / "runtime" / "hooks" / "runtime.py",
        PROJECT_ROOT / "mind_app" / "runtime" / "hooks" / "registry.py",
    )
    target_paths = (
        PROJECT_ROOT / "agent" / "harness" / "hooks" / "runtime.py",
        PROJECT_ROOT / "agent" / "harness" / "hooks" / "registry.py",
    )

    assert not any(path.is_file() for path in legacy_paths), (
        "legacy Hook runtime sources still exist: "
        + ", ".join(str(path.relative_to(PROJECT_ROOT)) for path in legacy_paths if path.is_file())
    )
    assert all(path.is_file() for path in target_paths), "Harness Hook sources are missing"

    violations: list[str] = []
    for path in target_paths:
        tree = _parsed_source(path)
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            for module in modules:
                if module.partition(".")[0] in {
                    "engine",
                    "infrastructure",
                    "mind_app",
                    "mind_core",
                    "mind_nova",
                    "server",
                }:
                    violations.append(f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}")
    assert not violations, "Harness Hook implementation crosses boundaries:\n" + "\n".join(
        violations
    )


def test_execution_policy_is_split_between_domain_and_config() -> None:
    """确保执行策略值对象与规则文件解析分别归属 domain/config。"""
    legacy_manager = (
        PROJECT_ROOT / "mind_app" / "native_coding" / "exec" / "exec_policy.py"
    )
    manager_path = (
        PROJECT_ROOT / "infrastructure" / "config" / "execution_policy_manager.py"
    )
    assert not legacy_manager.is_file(), "legacy execution policy manager still exists"
    assert manager_path.is_file(), "infrastructure execution policy manager is missing"
    requirement_path = (
        PROJECT_ROOT
        / "agent"
        / "domain"
        / "execution_policy"
        / "requirements.py"
    )
    assert requirement_path.is_file(), "execution policy requirement domain is missing"

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
        "mind_app.native_coding.exec.exec_policy",
        "mind_app.native_coding.exec.execpolicy",
        "mind_app.native_coding.exec.execpolicy.decision",
        "mind_app.native_coding.exec.execpolicy.rule",
        "mind_app.native_coding.exec.execpolicy.policy",
        "mind_app.native_coding.exec.execpolicy.parser",
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
        "legacy execution policy imports remain:\n" + "\n".join(violations)
    )

    domain_root = PROJECT_ROOT / "agent" / "domain" / "execution_policy"
    domain_violations: list[str] = []
    for path in domain_root.rglob("*.py"):
        tree = _parsed_source(path)
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

    manager_source = manager_path.read_text(encoding="utf-8-sig")
    requirement_source = requirement_path.read_text(encoding="utf-8-sig")
    assert "class ExecApprovalRequirement" not in manager_source
    assert "class ExecPolicyAmendment" not in manager_source
    assert "class ExecutionPolicyRequirement" in requirement_source
    assert "class ExecutionPolicyAmendment" in requirement_source
