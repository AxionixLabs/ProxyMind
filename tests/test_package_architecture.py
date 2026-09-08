# -*- coding: utf-8 -*-

"""审计全仓源码清单、模块格式、基础依赖方向和测试树入口。"""


import ast
from tests.architecture.source_inventory import (
    NON_REPOSITORY_DIRECTORY_NAMES,
    PROJECT_ROOT,
    TESTS_ROOT,
    all_python_sources as _all_python_sources,
    forbidden_imports as _forbidden_imports,
    forbidden_module_imports as _forbidden_module_imports,
    is_import_block_node as _is_import_block_node,
    is_main_guard as _is_main_guard,
    parsed_source as _parsed_source,
    production_python_sources as _production_python_sources,
)


def test_python_source_inventory_excludes_non_repository_directories() -> None:
    """确保架构审计不会读取虚拟环境、缓存或生成目录。"""
    violations: list[str] = []
    for path in _all_python_sources():
        relative = path.relative_to(PROJECT_ROOT)
        if NON_REPOSITORY_DIRECTORY_NAMES.intersection(relative.parts) or any(
            part.endswith(".egg-info")
            for part in relative.parts
        ):
            violations.append(str(relative))

    assert not violations, (
        "architecture inventory includes non-repository sources:\n"
        + "\n".join(violations)
    )


def test_tests_root_contains_only_stable_entrypoints() -> None:
    """确保业务测试不会重新回到 tests 根目录平铺。"""
    allowed_files = {
        "README.md",
        "conftest.py",
        "test_package_architecture.py",
    }
    unexpected = sorted(
        path.name
        for path in TESTS_ROOT.iterdir()
        if path.is_file() and path.name not in allowed_files
    )

    assert unexpected == []


def test_tests_do_not_create_generic_utility_packages() -> None:
    """确保共享测试代码按真实能力命名而不是进入通用收纳包。"""
    forbidden_packages = {
        "common",
        "support",
        "utils",
    }
    present = sorted(
        path.name
        for path in TESTS_ROOT.iterdir()
        if path.is_dir() and path.name in forbidden_packages
    )

    assert present == []


def test_client_tests_do_not_import_backend() -> None:
    """确保客户端测试树不直接验证独立 backend 服务实现。"""
    violations: list[str] = []
    for path in TESTS_ROOT.rglob("*.py"):
        tree = _parsed_source(path)
        for node in ast.walk(tree):
            imported: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                imported = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                imported = (node.module or "",)
            for module in imported:
                if module.partition(".")[0] == "backend":
                    relative = path.relative_to(PROJECT_ROOT)
                    violations.append(f"{relative}:{node.lineno} -> {module}")

    assert violations == []


def test_movable_tests_do_not_derive_root_from_file_depth() -> None:
    """确保可迁移测试从 pytest fixture 接收仓库根。"""
    violations: list[str] = []
    stable_entrypoint = TESTS_ROOT / "test_package_architecture.py"
    for path in TESTS_ROOT.rglob("*.py"):
        if path == stable_entrypoint:
            continue
        tree = _parsed_source(path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Subscript):
                continue
            parents = node.value
            if (
                isinstance(parents, ast.Attribute)
                and parents.attr == "parents"
                and any(
                    isinstance(child, ast.Name) and child.id == "__file__"
                    for child in ast.walk(parents.value)
                )
            ):
                relative = path.relative_to(PROJECT_ROOT)
                violations.append(f"{relative}:{node.lineno}")

    assert violations == []


def test_python_sources_start_with_canonical_mind_header() -> None:
    """确保生产源码以统一编码与 Mind 商标注释开头。"""
    canonical_header = (
        "# -*- coding: utf-8 -*-",
        "# Notes: ==== Mind™ ====",
    )
    legacy_notes = "# Notes: ==== Mind(TM) ===="
    violations: list[str] = []

    for path in _production_python_sources():
        relative = path.relative_to(PROJECT_ROOT)
        lines = path.read_text(encoding="utf-8-sig").splitlines()
        header_index = int(bool(lines) and lines[0].startswith("#!"))
        if tuple(lines[header_index:header_index + 2]) != canonical_header:
            violations.append(str(relative))
            continue
        if legacy_notes in lines:
            violations.append(f"{relative} retains legacy Mind notes")

    assert not violations, "production modules lack the canonical header:\n" + "\n".join(
        violations
    )


def test_production_exports_follow_complete_import_block() -> None:
    """确保生产模块的公开声明紧随完整顶层导入区。"""
    violations: list[str] = []

    for path in _production_python_sources():
        tree = ast.parse(
            path.read_text(encoding="utf-8-sig"),
            filename=str(path),
        )
        import_block_indexes = [
            index
            for index, node in enumerate(tree.body)
            if _is_import_block_node(node)
        ]
        export_indexes = [
            index
            for index, node in enumerate(tree.body)
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "__all__"
                for target in node.targets
            )
        ]
        if not export_indexes:
            continue

        relative = path.relative_to(PROJECT_ROOT)
        if len(export_indexes) != 1:
            violations.append(f"{relative} declares __all__ more than once")
            continue
        first_statement_index = int(
            bool(tree.body) and ast.get_docstring(tree, clean=False) is not None
        )
        expected_index = (
            import_block_indexes[-1] + 1
            if import_block_indexes
            else first_statement_index
        )
        if export_indexes[0] != expected_index:
            violations.append(
                f"{relative} must declare __all__ after its complete import block"
            )

    assert not violations, "invalid production __all__ placement:\n" + "\n".join(
        violations
    )


def test_production_modules_end_with_one_main_guard() -> None:
    """确保非包生产模块以唯一的直接执行入口收尾。"""
    expected_suffix = "\n\n\nif __name__ == '__main__':\n    pass\n"
    violations: list[str] = []

    for path in _production_python_sources():
        if path.name == "__init__.py":
            continue
        source = path.read_text(encoding="utf-8-sig")
        tree = ast.parse(source, filename=str(path))
        main_guards = [node for node in tree.body if _is_main_guard(node)]
        relative = path.relative_to(PROJECT_ROOT)
        if len(main_guards) != 1 or tree.body[-1] is not main_guards[0]:
            violations.append(f"{relative} must end with exactly one main guard")
            continue
        main_guard = main_guards[0]
        if (
            len(main_guard.body) == 1
            and isinstance(main_guard.body[0], ast.Pass)
            and not source.endswith(expected_suffix)
        ):
            violations.append(f"{relative} has a non-canonical inert main guard")

    assert not violations, "production modules have invalid main guards:\n" + (
        "\n".join(violations)
    )


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


def test_historical_source_packages_are_retired() -> None:
    """确保阶段 5 已退役包不能重新承载生产源码。"""
    retired_packages = (
        "engine",
        "mind_app",
        "mind_core",
        "mind_nova",
    )
    remaining = tuple(
        path.relative_to(PROJECT_ROOT).as_posix()
        for package in retired_packages
        for path in (PROJECT_ROOT / package).rglob("*.py")
    )

    assert not remaining, "retired package sources returned:\n" + "\n".join(
        remaining
    )


def test_agent_harness_core_does_not_import_legacy_packages() -> None:
    legacy_forbidden = {
        "applications",
        "backend",
        "engine",
        "frontends",
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
        *_forbidden_imports(
            "agent/stores",
            {*legacy_forbidden, "infrastructure"},
        ),
    ]

    assert not violations, "agent harness imports legacy code:\n" + "\n".join(
        violations
    )
