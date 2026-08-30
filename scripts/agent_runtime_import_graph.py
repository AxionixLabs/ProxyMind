# -*- coding: utf-8 -*-

import argparse
import ast
from dataclasses import dataclass
from pathlib import Path


RUNTIME_PACKAGE_ROOTS = (
    "agent",
    "applications",
    "infrastructure",
    "mind_app",
    "mind_core",
    "protocol",
    "mind_npm",
    "metadata",
    "observability",
    "server",
)
LEGACY_PACKAGE_ROOTS = ("engine", "mind_nova")
ENTRY_FILES = (
    "build.py",
    "mind.py",
    "setup.py",
)
GRAPH_PATH = "AGENT_RUNTIME_IMPORT_GRAPH.md"


@dataclass(frozen=True, slots=True)
class ImportEdge:
    """表示一条跨边界导入及其证据。"""

    source: str
    target: str
    files: tuple[str, ...]
    statements: int


def _source_boundary(path: Path, repository_root: Path) -> str:
    """返回源文件所属的顶层边界。"""
    relative = path.relative_to(repository_root)
    if len(relative.parts) == 1:
        return relative.stem
    return relative.parts[0]


def _source_files(repository_root: Path) -> tuple[Path, ...]:
    """返回纳入运行时导入图的 Python 源文件。"""
    files: list[Path] = []
    for package in RUNTIME_PACKAGE_ROOTS:
        package_root = repository_root / package
        if package_root.exists():
            files.extend(package_root.rglob("*.py"))
    files.extend(
        path
        for name in ENTRY_FILES
        if (path := repository_root / name).exists()
    )
    return tuple(sorted(files))


def _imported_roots(tree: ast.AST) -> tuple[tuple[str, int], ...]:
    """返回 AST 中的绝对导入根和行号。"""
    imported: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        modules: tuple[str, ...] = ()
        if isinstance(node, ast.Import):
            modules = tuple(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            modules = (node.module or "",)

        imported.extend(
            (module.partition(".")[0], node.lineno)
            for module in modules
            if module
        )
    return tuple(imported)


def collect_import_edges(repository_root: Path) -> tuple[ImportEdge, ...]:
    """汇总第一方运行时边界之间的绝对导入。"""
    known_roots = frozenset(
        (*RUNTIME_PACKAGE_ROOTS, *LEGACY_PACKAGE_ROOTS,
         *(Path(name).stem for name in ENTRY_FILES))
    )
    evidence: dict[tuple[str, str], list[tuple[str, int]]] = {}

    for path in _source_files(repository_root):
        source = _source_boundary(path, repository_root)
        relative = path.relative_to(repository_root).as_posix()
        tree = ast.parse(
            path.read_text(encoding="utf-8-sig"),
            filename=str(path),
        )
        for target, line_number in _imported_roots(tree):
            if target not in known_roots or target == source:
                continue
            evidence.setdefault((source, target), []).append(
                (relative, line_number)
            )

    return tuple(
        ImportEdge(
            source=source,
            target=target,
            files=tuple(sorted({path for path, _line in entries})),
            statements=len(entries),
        )
        for (source, target), entries in sorted(evidence.items())
    )


def _cyclic_groups(
    nodes: tuple[str, ...],
    edges: tuple[ImportEdge, ...],
) -> tuple[tuple[str, ...], ...]:
    """返回跨边界导入图中的强连通组。"""
    reachability = {node: set() for node in nodes}
    for edge in edges:
        reachability[edge.source].add(edge.target)

    changed = True
    while changed:
        changed = False
        for node in nodes:
            expanded = set(reachability[node])
            for target in tuple(reachability[node]):
                expanded.update(reachability.get(target, ()))
            if expanded != reachability[node]:
                reachability[node] = expanded
                changed = True

    groups = {
        frozenset(
            candidate
            for candidate in nodes
            if (
                candidate != node
                and candidate in reachability[node]
                and node in reachability[candidate]
            )
        ) | {node}
        for node in nodes
        if node in reachability[node]
    }
    return tuple(
        sorted(tuple(sorted(group)) for group in groups if len(group) > 1)
    )


def render_import_graph(repository_root: Path) -> str:
    """生成确定性的第一方运行时导入图文档。"""
    nodes = tuple(sorted({*RUNTIME_PACKAGE_ROOTS, *(Path(name).stem for name in ENTRY_FILES)}))
    edges = collect_import_edges(repository_root)
    cycles = _cyclic_groups(nodes, edges)
    legacy_engine = tuple(
        edge
        for edge in edges
        if edge.target == "engine"
    )

    lines = [
        "# Agent Harness 第一方导入图",
        "",
        "> 由 `scripts/agent_runtime_import_graph.py` 根据当前源码生成，请勿手工编辑。",
        "",
        "## 范围",
        "",
        "- 包含生产与构建边界：`" + "`、`".join(nodes) + "`。",
        "- 仅记录第一方边界之间的绝对 Python 导入；包内相对导入不展开。",
        "- `backend/` 按独立打包边界单独验收，不纳入本图。",
        "- `tests/`、`website/`、`schematic/`、`codex-main/` 和 `venv/` 不是运行时边界，不纳入本图。",
        "- 根目录 `server/` 是客户端内置的 `ConfigServiceRuntime`，只提供配置 UI/健康检查；它不是 `mind.chat` 线上服务端，也不拥有 Harness 状态。",
        "",
        "## 边界图",
        "",
        "```mermaid",
        "flowchart LR",
    ]
    if edges:
        lines.extend(
            f"    {edge.source} --> {edge.target}"
            for edge in edges
        )
    else:
        lines.append("    empty[\"无跨边界导入\"]")
    lines.extend([
        "```",
        "",
        "## 直接跨边界依赖",
        "",
        "| 源边界 | 目标边界 | 导入文件数 | 导入语句数 | 证据文件 |",
        "| --- | --- | ---: | ---: | --- |",
    ])
    lines.extend(
        "| `{}` | `{}` | {} | {} | {} |".format(
            edge.source,
            edge.target,
            len(edge.files),
            edge.statements,
            "<br>".join(f"`{path}`" for path in edge.files),
        )
        for edge in edges
    )
    if not edges:
        lines.append("| - | - | 0 | 0 | - |")

    lines.extend([
        "",
        "## 循环与反向依赖",
        "",
        "### 跨边界循环",
        "",
    ])
    if cycles:
        lines.extend(f"- `{' -> '.join(group)} -> {group[0]}`" for group in cycles)
    else:
        lines.append("- 未发现跨边界循环。")

    lines.extend([
        "",
        "### `engine` 残留引用",
        "",
    ])
    if legacy_engine:
        lines.extend(
            f"- `{edge.source} -> engine`：" + "、".join(
                f"`{path}`" for path in edge.files
            )
            for edge in legacy_engine
        )
    else:
        lines.append("- 未发现 `engine` 残留导入；旧源包已删除。")

    lines.extend([
        "",
        "## 复核命令",
        "",
        "```powershell",
        r".\venv\Scripts\python.exe scripts\agent_runtime_import_graph.py --check",
        "```",
        "",
    ])
    return "\n".join(lines)


def _repository_root() -> Path:
    """返回当前脚本所属的仓库根目录。"""
    return Path(__file__).resolve().parents[1]


def main() -> int:
    """生成或校验已提交的导入图。"""
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="写入导入图")
    mode.add_argument("--check", action="store_true", help="校验导入图")
    arguments = parser.parse_args()

    repository_root = _repository_root()
    graph_path = repository_root / GRAPH_PATH
    rendered = render_import_graph(repository_root)

    if arguments.write:
        graph_path.write_text(rendered, encoding="utf-8", newline="\n")
        print(f"wrote {graph_path.relative_to(repository_root)}")
        return 0

    current = graph_path.read_text(encoding="utf-8") if graph_path.exists() else ""
    if current == rendered:
        print("agent harness import graph is current")
        return 0

    print(
        "agent harness import graph is stale; run "
        r".\venv\Scripts\python.exe scripts\agent_runtime_import_graph.py --write"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
