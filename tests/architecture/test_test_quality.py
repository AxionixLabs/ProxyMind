# -*- coding: utf-8 -*-

"""审计自动化测试的确定性和外部状态隔离。"""

import ast
from pathlib import Path

from tests.architecture.source_inventory import PROJECT_ROOT
from tests.architecture.source_inventory import TESTS_ROOT
from tests.architecture.source_inventory import parsed_source


def _automated_test_sources() -> tuple[Path, ...]:
    """返回排除人工联调入口后的自动化测试源码。"""
    return tuple(
        path
        for path in TESTS_ROOT.rglob("*.py")
        if "manual" not in path.relative_to(TESTS_ROOT).parts
    )


def _relative_location(path: Path, node: ast.AST) -> str:
    """返回稳定的违规位置。"""
    return f"{path.relative_to(PROJECT_ROOT)}:{node.lineno}"


def _import_aliases(tree: ast.Module) -> dict[str, str]:
    """返回模块内导入名称到正式模块路径的映射。"""
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for imported in node.names:
                local_name = imported.asname or imported.name.partition(".")[0]
                canonical = imported.name if imported.asname else local_name
                aliases[local_name] = canonical
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            for imported in node.names:
                local_name = imported.asname or imported.name
                aliases[local_name] = f"{node.module}.{imported.name}"
    return aliases


def _node_path(node: ast.AST) -> str:
    """返回名称或属性节点的点分路径。"""
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return ".".join(reversed(parts))


def _canonical_path(node: ast.AST, aliases: dict[str, str]) -> str:
    """按当前模块导入把节点路径还原为正式路径。"""
    path = _node_path(node)
    root, separator, remainder = path.partition(".")
    canonical_root = aliases.get(root, root)
    if not separator:
        return canonical_root
    return f"{canonical_root}.{remainder}"


def _is_os_environ(node: ast.AST, aliases: dict[str, str]) -> bool:
    """判断节点是否引用进程环境映射。"""
    return _canonical_path(node, aliases) == "os.environ"


def _contains_os_environ_target(
    node: ast.AST,
    aliases: dict[str, str],
) -> bool:
    """判断赋值目标是否直接修改进程环境。"""
    return any(
        _is_os_environ(child, aliases)
        or (
            isinstance(child, ast.Subscript)
            and _is_os_environ(child.value, aliases)
        )
        for child in ast.walk(node)
    )


def test_automated_tests_do_not_write_process_environment_directly() -> None:
    """确保环境值通过注入或可恢复的边界 fixture 设置。"""
    forbidden_methods = {
        "clear",
        "pop",
        "popitem",
        "setdefault",
        "update",
    }
    violations: list[str] = []

    for path in _automated_test_sources():
        tree = parsed_source(path)
        aliases = _import_aliases(tree)
        for node in ast.walk(tree):
            targets: tuple[ast.AST, ...] = ()
            if isinstance(node, ast.Assign):
                targets = tuple(node.targets)
            elif isinstance(node, ast.AnnAssign):
                targets = (node.target,)
            elif isinstance(node, ast.AugAssign):
                targets = (node.target,)
            elif isinstance(node, ast.Delete):
                targets = tuple(node.targets)
            if any(
                _contains_os_environ_target(target, aliases)
                for target in targets
            ):
                violations.append(_relative_location(path, node))
                continue
            if not isinstance(node, ast.Call):
                continue
            call_path = _canonical_path(node.func, aliases)
            if call_path in {"os.putenv", "os.unsetenv"}:
                violations.append(_relative_location(path, node))
            elif (
                isinstance(node.func, ast.Attribute)
                and _is_os_environ(node.func.value, aliases)
                and node.func.attr in forbidden_methods
            ):
                violations.append(_relative_location(path, node))
            elif (
                call_path == "unittest.mock.patch.dict"
                and node.args
                and _is_os_environ(node.args[0], aliases)
            ):
                violations.append(_relative_location(path, node))

    assert violations == [], (
        "tests write process environment directly:\n" + "\n".join(violations)
    )


def test_automated_tests_do_not_use_retry_or_quarantine_markers() -> None:
    """确保不稳定测试通过修复同步和所有权问题收口。"""
    forbidden_markers = {
        "flaky",
        "quarantine",
        "rerun",
        "reruns",
    }
    violations: list[str] = []

    for path in _automated_test_sources():
        tree = parsed_source(path)
        aliases = _import_aliases(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute):
                continue
            if node.attr in forbidden_markers and _canonical_path(
                node,
                aliases,
            ).startswith("pytest.mark."):
                violations.append(_relative_location(path, node))

    pytest_config = (PROJECT_ROOT / "pytest.ini").read_text(encoding="utf-8")
    if "--reruns" in pytest_config:
        violations.append("pytest.ini -> --reruns")

    assert violations == [], (
        "tests use retry or quarantine mechanisms:\n" + "\n".join(violations)
    )


def test_scenario_and_fake_facilities_do_not_wait_for_real_time() -> None:
    """确保领域场景和 fake 仅使用事件或零延迟让步同步。"""
    violations: list[str] = []
    roots = (
        TESTS_ROOT / "fakes",
        TESTS_ROOT / "scenarios",
    )

    for root in roots:
        for path in root.rglob("*.py"):
            tree = parsed_source(path)
            aliases = _import_aliases(tree)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if _canonical_path(node.func, aliases) not in {
                    "anyio.sleep",
                    "asyncio.sleep",
                    "time.sleep",
                }:
                    continue
                delay = node.args[0] if node.args else None
                if (
                    not isinstance(delay, ast.Constant)
                    or not isinstance(delay.value, (int, float))
                    or delay.value != 0
                ):
                    violations.append(_relative_location(path, node))

    assert violations == [], (
        "scenario or fake facilities wait for real time:\n"
        + "\n".join(violations)
    )


def test_automated_tests_do_not_use_unseeded_module_randomness() -> None:
    """确保状态空间测试通过显式 Random 实例固定 seed。"""
    random_calls = {
        "random.choice",
        "random.choices",
        "random.getrandbits",
        "random.randint",
        "random.random",
        "random.randrange",
        "random.sample",
        "random.shuffle",
        "random.uniform",
    }
    violations: list[str] = []

    for path in _automated_test_sources():
        tree = parsed_source(path)
        aliases = _import_aliases(tree)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and _canonical_path(node.func, aliases) in random_calls
            ):
                violations.append(_relative_location(path, node))

    assert violations == [], (
        "tests use module-level randomness without an explicit seed:\n"
        + "\n".join(violations)
    )


def test_automated_tests_do_not_call_public_network_entrypoints() -> None:
    """确保自动化测试只使用注入 transport 或显式本地服务。"""
    direct_network_calls = {
        "httpx.delete",
        "httpx.get",
        "httpx.patch",
        "httpx.post",
        "httpx.put",
        "requests.delete",
        "requests.get",
        "requests.patch",
        "requests.post",
        "requests.put",
        "socket.create_connection",
        "urllib.request.urlopen",
        "urllib.request.urlretrieve",
    }
    violations: list[str] = []

    for path in _automated_test_sources():
        tree = parsed_source(path)
        aliases = _import_aliases(tree)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and _canonical_path(node.func, aliases) in direct_network_calls
            ):
                violations.append(_relative_location(path, node))

    assert violations == [], (
        "tests call public network entrypoints directly:\n"
        + "\n".join(violations)
    )
