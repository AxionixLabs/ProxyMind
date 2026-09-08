# -*- coding: utf-8 -*-

"""提供架构审计专用的仓库源码清单与 AST 缓存。"""

import ast
import functools
import os
from pathlib import Path


NON_REPOSITORY_DIRECTORY_NAMES = frozenset({
    ".git",
    ".mypy_cache",
    ".nox",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "env",
    "htmlcov",
    "pip-wheel-metadata",
    "venv",
})


def _discover_project_root(source: Path) -> Path:
    """通过稳定仓库标记定位项目根目录。"""
    for candidate in (source, *source.parents):
        if (
            (candidate / "ARCHITECTURE.md").is_file()
            and (candidate / "agent").is_dir()
            and (candidate / "tests").is_dir()
        ):
            return candidate
    raise RuntimeError(f"cannot locate project root from {source}")


PROJECT_ROOT = _discover_project_root(Path(__file__).resolve().parent)
TESTS_ROOT = PROJECT_ROOT / "tests"


@functools.lru_cache(maxsize=1)
def all_python_sources() -> tuple[Path, ...]:
    """返回仓库拥有的 Python 源码，不遍历本地环境和生成目录。"""
    sources: list[Path] = []
    for directory, directory_names, filenames in os.walk(
        PROJECT_ROOT,
        topdown=True,
    ):
        directory_names[:] = sorted(
            name
            for name in directory_names
            if name not in NON_REPOSITORY_DIRECTORY_NAMES
            and not name.endswith(".egg-info")
        )
        directory_path = Path(directory)
        sources.extend(
            directory_path / filename
            for filename in sorted(filenames)
            if filename.endswith(".py")
        )
    return tuple(sources)


@functools.lru_cache(maxsize=None)
def parsed_source(path: Path) -> ast.Module:
    """读取并解析单个 Python 源文件，避免重复 AST 扫描。"""
    return ast.parse(
        path.read_text(encoding="utf-8-sig"),
        filename=str(path),
    )


@functools.lru_cache(maxsize=1)
def production_python_sources() -> tuple[Path, ...]:
    """返回仓库内不属于测试或本地环境的 Python 源码。"""
    excluded_parts = {
        ".git",
        ".venv",
        "__pycache__",
        "backend",
        "build",
        "codex-main",
        "schematic",
        "test",
        "tests",
        "venv",
    }
    return tuple(
        path
        for path in all_python_sources()
        if not excluded_parts.intersection(
            path.relative_to(PROJECT_ROOT).parts
        )
        and not path.name.startswith("test_")
        and not path.stem.endswith("_test")
        and path.name != "conftest.py"
    )


def is_import_block_node(node: ast.stmt) -> bool:
    """判断顶层语句是否属于常规或类型检查导入区。"""
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        return True
    if not isinstance(node, ast.If) or node.orelse:
        return False
    type_checking_guard = (
        isinstance(node.test, ast.Name)
        and node.test.id == "TYPE_CHECKING"
    ) or (
        isinstance(node.test, ast.Attribute)
        and node.test.attr == "TYPE_CHECKING"
    )
    return type_checking_guard and all(
        isinstance(child, (ast.Import, ast.ImportFrom))
        for child in node.body
    )


def is_main_guard(node: ast.stmt) -> bool:
    """判断顶层语句是否为模块直接执行入口。"""
    if not isinstance(node, ast.If):
        return False
    test = node.test
    return (
        isinstance(test, ast.Compare)
        and isinstance(test.left, ast.Name)
        and test.left.id == "__name__"
        and len(test.ops) == 1
        and isinstance(test.ops[0], ast.Eq)
        and len(test.comparators) == 1
        and isinstance(test.comparators[0], ast.Constant)
        and test.comparators[0].value == "__main__"
    )


def forbidden_imports(
    package: str,
    forbidden_roots: set[str],
) -> list[str]:
    """返回包内指向禁止顶层包的绝对导入。"""
    package_root = PROJECT_ROOT / package
    violations: list[str] = []

    for path in package_root.rglob("*.py"):
        tree = parsed_source(path)
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


def forbidden_module_imports(
    package: str,
    forbidden_modules: set[str],
) -> list[str]:
    """返回包内指向禁止模块或其子模块的绝对导入。"""
    package_root = PROJECT_ROOT / package
    violations: list[str] = []

    for path in package_root.rglob("*.py"):
        tree = parsed_source(path)
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
