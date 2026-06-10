# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import re
import typing
import fnmatch
from pathlib import Path
from backend.mcp_code.base import NativeCodingComponent
from backend.utilities.trace import clip_text


class RepoMapTools(NativeCodingComponent):
    """提供轻量级仓库符号索引和符号搜索能力。"""

    SUPPORTED_LANGUAGES = {
        "python",
        "javascript",
        "typescript",
        "go",
        "rust",
        "java",
        "kotlin",
        "swift",
        "c",
        "cpp",
        "csharp",
        "vue",
        "svelte"
    }

    PARSER_LEVEL = "regex"

    PARSER_LIMITATIONS = [
        "not_ast_or_lsp",
        "complex_multiline_or_dynamic_syntax_may_be_missed",
        "nested_scope_is_best_effort",
        "generated_or_truncated_files_are_not_fully_indexed"
    ]

    @staticmethod
    def _language_for_path(
        path: str
    ) -> str:
        """根据文件后缀判断符号索引使用的语言类别。"""
        suffix = Path(path).suffix.lower()

        if suffix in {".py", ".pyi"}:
            return "python"
        if suffix in {".ts", ".tsx"}:
            return "typescript"
        if suffix in {".js", ".jsx"}:
            return "javascript"
        if suffix == ".go":
            return "go"
        if suffix == ".rs":
            return "rust"
        if suffix == ".java":
            return "java"
        if suffix in {".kt", ".kts"}:
            return "kotlin"
        if suffix == ".swift":
            return "swift"
        if suffix in {".c", ".h"}:
            return "c"
        if suffix in {".cc", ".cpp", ".cxx", ".hh", ".hpp", ".hxx", ".mm"}:
            return "cpp"
        if suffix == ".cs":
            return "csharp"
        if suffix == ".vue":
            return "vue"
        if suffix == ".svelte":
            return "svelte"

        return "text"

    @staticmethod
    def _compiled_import(
        language: str,
        stripped: str
    ) -> str:
        """提取编译型语言的 import/use/include 目标。"""
        patterns = {
            "java"    : r"^import\s+(?:static\s+)?(.+?);$",
            "kotlin"  : r"^import\s+(.+)$",
            "swift"   : r"^import\s+(.+)$",
            "c"       : r"^#include\s+[<\"](.+?)[>\"]",
            "cpp"     : r"^#include\s+[<\"](.+?)[>\"]",
            "csharp"  : r"^using\s+(.+?);$"
        }
        pattern = patterns.get(language)
        if not pattern:
            return ""
        match = re.match(pattern, stripped)
        return str(match.group(1)).strip() if match else ""

    @staticmethod
    def _compiled_symbol_patterns(
        language: str
    ) -> list[tuple[str, str]]:
        """返回编译型语言的符号匹配规则。"""
        if language == "java":
            return [
                ("class", r"^(?:@\w+(?:\([^)]*\))?\s+)*(?:public|private|protected|abstract|final|static|\s)*\s*class\s+([A-Za-z_]\w*)\b"),
                ("interface", r"^(?:@\w+(?:\([^)]*\))?\s+)*(?:public|private|protected|\s)*\s*interface\s+([A-Za-z_]\w*)\b"),
                ("enum", r"^(?:@\w+(?:\([^)]*\))?\s+)*(?:public|private|protected|\s)*\s*enum\s+([A-Za-z_]\w*)\b"),
                ("method", r"^(?:@\w+(?:\([^)]*\))?\s+)*(?:public|private|protected|static|final|synchronized|abstract|native|\s)+(?:<[^>]+>\s*)?[\w<>\[\], ?]+\s+([A-Za-z_]\w*)\s*\(")
            ]
        if language == "kotlin":
            return [
                ("class", r"^(?:data\s+|sealed\s+|open\s+|abstract\s+)?class\s+([A-Za-z_]\w*)\b"),
                ("interface", r"^interface\s+([A-Za-z_]\w*)\b"),
                ("object", r"^object\s+([A-Za-z_]\w*)\b"),
                ("function", r"^(?:suspend\s+)?fun\s+([A-Za-z_]\w*)\s*\(")
            ]
        if language == "swift":
            return [
                ("class", r"^(?:public\s+|private\s+|final\s+|open\s+)*class\s+([A-Za-z_]\w*)\b"),
                ("struct", r"^(?:public\s+|private\s+)*struct\s+([A-Za-z_]\w*)\b"),
                ("enum", r"^(?:public\s+|private\s+)*enum\s+([A-Za-z_]\w*)\b"),
                ("protocol", r"^(?:public\s+|private\s+)*protocol\s+([A-Za-z_]\w*)\b"),
                ("function", r"^(?:public\s+|private\s+|static\s+|class\s+)*func\s+([A-Za-z_]\w*)\s*\(")
            ]
        if language in {"c", "cpp"}:
            return [
                ("class", r"^(?:template\s*<[^>]+>\s*)?(?:class|struct)\s+([A-Za-z_]\w*)\b"),
                ("enum", r"^enum(?:\s+class)?\s+([A-Za-z_]\w*)\b"),
                ("function", r"^(?:static\s+|inline\s+|virtual\s+|constexpr\s+|extern\s+)*[A-Za-z_][\w:<>,~*&\s]+\s+([A-Za-z_]\w*)\s*\([^;]*\)\s*(?:\{|;|const\b)")
            ]
        if language == "csharp":
            return [
                ("namespace", r"^namespace\s+([A-Za-z_][\w.]*)\b"),
                ("class", r"^(?:\[[^\]]+\]\s*)*(?:public|private|protected|internal|abstract|sealed|static|\s)*\s*class\s+([A-Za-z_]\w*)\b"),
                ("interface", r"^(?:public|private|protected|internal|\s)*\s*interface\s+([A-Za-z_]\w*)\b"),
                ("enum", r"^(?:public|private|protected|internal|\s)*\s*enum\s+([A-Za-z_]\w*)\b"),
                ("method", r"^(?:\[[^\]]+\]\s*)*(?:public|private|protected|internal|static|virtual|override|async|\s)+[\w<>\[\], ?]+\s+([A-Za-z_]\w*)\s*\(")
            ]

        return []

    @staticmethod
    def _attach_symbol_read_windows(
        matches: list[dict[str, typing.Any]]
    ) -> None:
        """给符号命中补充读取窗口建议。"""
        for item in matches:
            path = str(item.get("path") or "")
            line = item.get("line")
            if not path or not isinstance(line, int):
                continue
            item["read_window"] = {
                "tool": "workspace_read_file",
                "args": {
                    "path": path,
                    "start_line": max(1, line - 20),
                    "max_lines": 80
                },
                "reason": "read_symbol_window"
            }

    @staticmethod
    def _reference_record(
        *,
        symbol: str,
        path: str,
        line: int,
        text: str,
        call_candidate: bool
    ) -> dict[str, typing.Any]:
        """创建轻量引用记录。"""
        return {
            "symbol": symbol,
            "path": path,
            "line": line,
            "text": clip_text(text, limit=300),
            "call_candidate": call_candidate,
            "read_window": {
                "tool": "workspace_read_file",
                "args": {
                    "path": path,
                    "start_line": max(1, line - 20),
                    "max_lines": 80
                },
                "reason": "read_reference_window"
            }
        }

    @staticmethod
    def _is_call_candidate(
        name: str,
        line: str
    ) -> bool:
        """判断一行引用是否像函数、方法或构造调用。"""
        escaped = re.escape(name)
        text    = str(line or "")

        patterns = [
            rf"\b{escaped}\s*\(",
            rf"\.\s*{escaped}\s*\(",
            rf"\bnew\s+{escaped}\s*\(",
            rf"<{escaped}\b"
        ]

        return any(re.search(pattern, text) for pattern in patterns)

    @staticmethod
    def _repo_map_next_steps(
        *,
        path: str,
        glob: str | None,
        truncated: bool,
        max_symbols: int,
        symbols: list[dict[str, typing.Any]]
    ) -> list[dict[str, typing.Any]]:
        """根据 repo map 结果生成后续建议。"""
        steps: list[dict[str, typing.Any]] = []

        if truncated:
            steps.append({
                "tool": "workspace_search",
                "args": {
                    "query": [str(item.get("name") or "") for item in symbols[:10] if item.get("name")],
                    "path": path,
                    "glob": glob,
                    "mode": "symbol",
                    "max_matches": min(max_symbols * 2, 1000)
                },
                "reason": "symbol_index_truncated_use_workspace_search"
            })

        symbol_windows = []

        for item in symbols[:8]:

            path_value = str(item.get("path") or "")
            line       = item.get("line")

            if path_value and isinstance(line, int):
                symbol_windows.append({
                    "tool": "workspace_read_file",
                    "args": {"path": path_value, "start_line": max(1, line - 20), "max_lines": 80},
                    "reason": "read_indexed_symbol_window"
                })

        if len(symbol_windows) > 1:
            steps.append({
                "tool": "native_parallel_read",
                "args": {"items": symbol_windows},
                "reason": "read_indexed_symbol_windows"
            })

        return steps[:8]

    @staticmethod
    def _symbol_next_steps(
        matches: list[dict[str, typing.Any]]
    ) -> list[dict[str, typing.Any]]:
        """根据符号搜索结果生成后续读取建议。"""
        items = [
            item["read_window"]
            for item in matches
            if isinstance(item, dict) and isinstance(item.get("read_window"), dict)
        ][:8]

        steps = items[:5]

        if len(items) > 1:
            steps.append({
                "tool": "native_parallel_read",
                "args": {"items": items},
                "reason": "read_multiple_symbol_windows"
            })

        return steps

    @staticmethod
    def _symbol_record(
        path: str,
        language: str,
        kind: str,
        name: str,
        qualified_name: str,
        line: int,
        signature: str
    ) -> dict[str, typing.Any]:
        """创建符号索引记录。"""
        return {
            "path"           : path,
            "language"       : language,
            "kind"           : kind,
            "name"           : name,
            "qualified_name" : qualified_name,
            "line"           : line,
            "signature"      : clip_text(signature, limit=300)
        }

    @staticmethod
    def _symbol_import(
        path: str,
        line: int,
        module: str,
        names: str
    ) -> dict[str, typing.Any]:
        """创建导入索引记录。"""
        return {
            "path"   : path,
            "line"   : line,
            "module" : clip_text(str(module or "").strip(), limit=300),
            "names"  : clip_text(str(names or "").strip(), limit=300)
        }

    @staticmethod
    def _symbol_statement(
        lines: list[str],
        lineno: int,
        *,
        max_lines: int = 6
    ) -> str:
        """合并少量后续行，支持轻量多行签名匹配。"""
        start = max(0, lineno - 1)

        parts: list[str] = []
        balance: int     = 0

        for raw in lines[start:start + max_lines]:
            stripped = raw.strip()
            if not stripped:
                break
            parts.append(stripped)

            balance += stripped.count("(") + stripped.count("[") + stripped.count("<")
            balance -= stripped.count(")") + stripped.count("]") + stripped.count(">")

            if balance <= 0 and (
                stripped.endswith(("{", ":", ";", "=>"))
                or re.search(r"\)\s*(?:\{|:|=>|;)?$", stripped)
                or len(parts) == 1
            ):
                break

        return " ".join(parts)

    def _repo_map(
        self,
        *,
        path: str = ".",
        glob: str | None = None,
        max_files: int = 200,
        max_symbols: int = 1000
    ) -> dict[str, typing.Any]:
        """构建工作区的轻量符号索引，并返回索引覆盖诊断。"""
        base = self.resolve_path(path)
        if not base.exists():
            return self.fail_result("path_not_found", path=path)

        max_files   = max(1, min(int(max_files or 200), 2000))
        max_symbols = max(1, min(int(max_symbols or 1000), 10000))

        files: list[dict[str, typing.Any]]           = []
        symbols: list[dict[str, typing.Any]]         = []
        imports: list[dict[str, typing.Any]]         = []
        indexed_files: list[dict[str, typing.Any]]   = []
        skipped_files: list[dict[str, typing.Any]]   = []
        truncated_files: list[dict[str, typing.Any]] = []

        for item in self.walk_paths(base, recursive=True):
            if len(files) >= max_files or len(symbols) >= max_symbols:
                break
            if not item.is_file() or self.is_excluded_path(item) or not self.looks_text(item):
                if item.is_file() and not self.is_excluded_path(item):
                    skipped_files.append({"path": self.relative_path(item), "reason": "file_not_text"})
                continue

            rel = self.relative_path(item)

            if glob and not fnmatch.fnmatch(rel, glob) and not fnmatch.fnmatch(item.name, glob):
                skipped_files.append({"path": rel, "reason": "glob_mismatch"})
                continue

            size     = item.stat().st_size
            raw      = item.read_bytes()[:self.max_read_bytes]
            content  = self.decode_bytes(raw)
            parsed   = self._parse_symbol_file(rel, content)
            language = str(parsed["language"])

            if size > self.max_read_bytes:
                truncated_files.append({
                    "path"            : rel,
                    "language"        : language,
                    "size"            : size,
                    "indexed_bytes"   : self.max_read_bytes,
                    "unindexed_bytes" : size - self.max_read_bytes
                })

            if not parsed["symbols"] and not parsed["imports"]:
                skipped_files.append({
                    "path"     : rel,
                    "language" : language,
                    "reason"   : "no_symbols_or_imports"
                })
                continue

            file_item = {
                "path"          : rel,
                "language"      : language,
                "size"          : size,
                "indexed_bytes" : min(size, self.max_read_bytes),
                "truncated"     : size > self.max_read_bytes,
                "symbol_count"  : len(parsed["symbols"]),
                "import_count"  : len(parsed["imports"]),
                "symbols"       : parsed["symbols"][:50],
                "imports"       : parsed["imports"][:50]
            }
            files.append(file_item)

            indexed_files.append({
                "path"          : rel,
                "language"      : language,
                "size"          : size,
                "indexed_bytes" : min(size, self.max_read_bytes),
                "truncated"     : size > self.max_read_bytes,
                "symbol_count"  : len(parsed["symbols"]),
                "import_count"  : len(parsed["imports"])
            })

            symbols.extend(parsed["symbols"])
            imports.extend(parsed["imports"])

        truncated = (
            len(files) >= max_files
            or len(symbols) >= max_symbols
            or bool(truncated_files)
        )

        return self.ok_result(
            f"repo map ok files={len(files)} symbols={len(symbols)} imports={len(imports)}",
            path=self.relative_path(base),
            files=files,
            symbols=symbols[:max_symbols],
            imports=imports[:max_symbols],
            indexed_files=indexed_files[:max_files],
            skipped_files=skipped_files[:200],
            truncated_files=truncated_files[:200],
            indexed_file_count=len(indexed_files),
            skipped_file_count=len(skipped_files),
            truncated_file_count=len(truncated_files),
            supported_languages=sorted(self.SUPPORTED_LANGUAGES),
            parser_level=self.PARSER_LEVEL,
            parser_limitations=list(self.PARSER_LIMITATIONS),
            file_count=len(files),
            symbol_count=len(symbols),
            import_count=len(imports),
            truncated=truncated,
            recommended_next_steps=self._repo_map_next_steps(
                path=self.relative_path(base),
                glob=glob,
                truncated=truncated,
                max_symbols=max_symbols,
                symbols=symbols
            )
        )

    def find_symbol(
        self,
        *,
        query: str,
        path: str = ".",
        glob: str | None = None,
        max_matches: int = 50
    ) -> dict[str, typing.Any]:
        """在内部符号索引中查找名称，并补充引用和读取窗口。"""
        needle = str(query or "").strip()

        if not needle:
            return self.fail_result("query_empty")

        mapped = self._repo_map(
            path=path,
            glob=glob,
            max_files=1000,
            max_symbols=max(50, int(max_matches or 50) * 20)
        )

        data = mapped.get("data") or {}

        if not data.get("ok"):
            return mapped

        lowered = needle.lower()
        matches = [
            item for item in data.get("symbols") or []
            if lowered in str(item.get("name") or "").lower()
            or lowered in str(item.get("qualified_name") or "").lower()
        ][:max(1, min(int(max_matches or 50), 200))]
        self._attach_symbol_read_windows(matches)

        reference_data = self._symbol_references(matches, path=path, glob=glob)

        return self.ok_result(
            f"repo symbol search ok matches={len(matches)} query={needle}",
            query=needle,
            matches=matches,
            match_count=len(matches),
            truncated=len(matches) >= max_matches,
            indexed_files=data.get("indexed_files") or [],
            skipped_files=data.get("skipped_files") or [],
            truncated_files=data.get("truncated_files") or [],
            indexed_file_count=data.get("indexed_file_count", 0),
            skipped_file_count=data.get("skipped_file_count", 0),
            truncated_file_count=data.get("truncated_file_count", 0),
            supported_languages=data.get("supported_languages") or [],
            parser_level=data.get("parser_level") or self.PARSER_LEVEL,
            parser_limitations=data.get("parser_limitations") or list(self.PARSER_LIMITATIONS),
            references=reference_data["references"],
            reference_count=len(reference_data["references"]),
            call_candidates=reference_data["call_candidates"],
            call_candidate_count=len(reference_data["call_candidates"]),
            reference_search_truncated_files=reference_data["truncated_files"],
            reference_search_truncated_file_count=len(reference_data["truncated_files"]),
            recommended_next_steps=self._symbol_next_steps(matches)
        )

    def _parse_symbol_file(
        self,
        path: str,
        content: str
    ) -> dict[str, typing.Any]:
        """按文件语言提取符号和导入信息，解析结果为正则级近似。"""
        language = self._language_for_path(path)

        symbols: list[dict[str, typing.Any]] = []
        imports: list[dict[str, typing.Any]] = []

        scope_stack: list[tuple[int, str]] = []
        pending_decorator_line: int | None = None
        pending_decorator_balance: int     = 0

        lines = str(content or "").splitlines()

        for lineno, line in enumerate(lines, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or stripped.startswith("//"):
                continue

            indent    = len(line) - len(line.lstrip(" "))
            statement = self._symbol_statement(lines, lineno)

            if language == "python":
                while scope_stack and indent <= scope_stack[-1][0]:
                    scope_stack.pop()
                if stripped.startswith("@"):
                    pending_decorator_line = pending_decorator_line or lineno
                    pending_decorator_balance += stripped.count("(") + stripped.count("[")
                    pending_decorator_balance -= stripped.count(")") + stripped.count("]")
                    pending_decorator_balance = max(0, pending_decorator_balance)
                    continue
                if pending_decorator_line and pending_decorator_balance > 0:
                    pending_decorator_balance += stripped.count("(") + stripped.count("[")
                    pending_decorator_balance -= stripped.count(")") + stripped.count("]")
                    pending_decorator_balance = max(0, pending_decorator_balance)
                    continue
                import_match = re.match(r"^(?:from\s+([\w.]+)\s+import\s+(.+)|import\s+(.+))$", stripped)
                if import_match:
                    imports.append(
                        self._symbol_import(
                            path,
                            lineno,
                            import_match.group(1) or "",
                            import_match.group(2) or import_match.group(3) or ""
                        )
                    )
                    continue

                signature   = statement
                symbol_line = pending_decorator_line or lineno
                match       = re.match(r"^(?:async\s+)?def\s+([A-Za-z_]\w*)\s*\(", signature)

                kind = "function"

                if not match:
                    match = re.match(r"^class\s+([A-Za-z_]\w*)\b", signature)
                    kind = "class"
                if not match:
                    match = re.match(r"^([A-Za-z_]\w*)\s*=\s*(?:lambda\b|type\s*\(|dataclasses\.make_dataclass\s*\()", stripped)
                    kind = "dynamic"

                if match:

                    name      = match.group(1)
                    parent    = scope_stack[-1][1] if scope_stack else ""
                    qualified = f"{parent}.{name}" if parent else name

                    symbols.append(
                        self._symbol_record(
                            path,
                            language,
                            kind if not parent else "method",
                            name,
                            qualified,
                            symbol_line,
                            signature
                        )
                    )
                    if kind == "class":
                        scope_stack.append((indent, qualified))
                    pending_decorator_line = None
                    pending_decorator_balance = 0
                    continue
                pending_decorator_line = None
                pending_decorator_balance = 0

            elif language in {"javascript", "typescript"}:
                import_match = re.match(r"^import\s+(.+?)\s+from\s+['\"](.+?)['\"]", stripped)
                if not import_match:
                    import_match = re.match(r"^import\s+['\"](.+?)['\"]", stripped)
                if import_match:
                    imports.append(
                        self._symbol_import(
                            path,
                            lineno,
                            import_match.group(2) if import_match.lastindex and import_match.lastindex >= 2 else import_match.group(1),
                            import_match.group(1)
                        )
                    )
                    continue
                patterns = [
                    ("class", r"^(?:export\s+)?(?:default\s+)?class\s+([A-Za-z_$][\w$]*)\b"),
                    ("function", r"^(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\("),
                    ("hook", r"^(?:export\s+)?(?:const|let|var)\s+(use[A-Z][A-Za-z0-9_$]*)\s*="),
                    ("function", r"^(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:function\b|[^=]*=>)"),
                    ("method", r"^(?:async\s+)?([A-Za-z_$][\w$]*)\s*\([^)]*\)\s*\{"),
                    ("method", r"^([A-Za-z_$][\w$]*)\s*:\s*(?:async\s*)?(?:function\b|\([^)]*\)\s*=>|[A-Za-z_$][\w$]*\s*=>)")
                ]
                for kind, pattern in patterns:
                    match = re.match(pattern, statement)
                    if match:
                        name = match.group(1)
                        symbols.append(
                            self._symbol_record(path, language, kind, name, name, lineno, statement)
                        )
                        break

            elif language == "go":
                import_match = re.match(r"^import\s+(?:\w+\s+)?\"(.+?)\"", stripped)
                if import_match:
                    imports.append(
                        self._symbol_import(path, lineno, import_match.group(1), import_match.group(1))
                    )
                    continue

                match = re.match(r"^func\s+(?:\(([^)]+)\)\s*)?([A-Za-z_]\w*)\s*\(", stripped)
                if match:
                    receiver  = (match.group(1) or "").strip()
                    name      = match.group(2)
                    kind      = "method" if receiver else "function"
                    qualified = f"{receiver}.{name}" if receiver else name

                    symbols.append(
                        self._symbol_record(path, language, kind, name, qualified, lineno, stripped)
                    )
                    continue

                match = re.match(r"^type\s+([A-Za-z_]\w*)\s+(?:struct|interface)\b", stripped)
                if match:
                    name = match.group(1)
                    symbols.append(
                        self._symbol_record(path, language, "type", name, name, lineno, stripped)
                    )

            elif language == "rust":
                import_match = re.match(r"^use\s+(.+?);$", stripped)
                if import_match:
                    imports.append(
                        self._symbol_import(path, lineno, import_match.group(1), import_match.group(1))
                    )
                    continue

                patterns = [
                    ("function", r"^(?:pub(?:\([^)]+\))?\s+)?(?:async\s+)?fn\s+([A-Za-z_]\w*)\s*\("),
                    ("struct", r"^(?:pub\s+)?struct\s+([A-Za-z_]\w*)\b"),
                    ("enum", r"^(?:pub\s+)?enum\s+([A-Za-z_]\w*)\b"),
                    ("trait", r"^(?:pub\s+)?trait\s+([A-Za-z_]\w*)\b"),
                    ("impl", r"^impl(?:<[^>]+>)?\s+(.+?)\s*\{")
                ]

                for kind, pattern in patterns:
                    match = re.match(pattern, stripped)
                    if match:
                        name = match.group(1).strip()
                        symbols.append(
                            self._symbol_record(path, language, kind, name, name, lineno, stripped)
                        )
                        break

            elif language in {"java", "kotlin", "swift", "c", "cpp", "csharp"}:
                import_match = self._compiled_import(language, stripped)
                if import_match:
                    imports.append(self._symbol_import(path, lineno, import_match, import_match))
                    continue

                for kind, pattern in self._compiled_symbol_patterns(language):
                    match = re.match(pattern, statement)
                    if not match:
                        continue
                    name = match.group(1).strip()
                    symbols.append(
                        self._symbol_record(path, language, kind, name, name, lineno, statement)
                    )
                    break

            elif language in {"vue", "svelte"}:
                import_match = re.match(r"^import\s+(.+?)\s+from\s+['\"](.+?)['\"]", stripped)
                if import_match:
                    imports.append(self._symbol_import(path, lineno, import_match.group(2), import_match.group(1)))
                    continue

                patterns = [
                    ("component", r"^(?:export\s+default\s+)?(?:defineComponent\s*\(|class\s+([A-Za-z_$][\w$]*)\b)"),
                    ("setup", r"^(?:async\s+)?setup\s*\("),
                    ("function", r"^(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\("),
                    ("function", r"^(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:function\b|[^=]*=>)")
                ]
                for kind, pattern in patterns:
                    match = re.match(pattern, statement)
                    if match:
                        name = match.group(1) if match.lastindex else None
                        name = name or ("setup" if kind == "setup" else Path(path).stem)
                        symbols.append(
                            self._symbol_record(path, language, kind, name, name, lineno, statement)
                        )
                        break

        return {
            "language" : language,
            "symbols"  : symbols,
            "imports"  : imports
        }

    def _symbol_references(
        self,
        matches: list[dict[str, typing.Any]],
        *,
        path: str,
        glob: str | None,
        max_references: int = 120
    ) -> dict[str, typing.Any]:
        """按符号名扫描轻量引用和调用候选。"""
        symbols = [
            item for item in matches
            if isinstance(item, dict) and str(item.get("name") or "").strip()
        ]
        if not symbols:
            return {"references": [], "call_candidates": [], "truncated_files": []}

        names = sorted({str(item.get("name") or "").strip() for item in symbols}, key=len, reverse=True)

        definitions = {
            (
                str(item.get("name") or ""),
                str(item.get("path") or ""),
                int(item.get("line") or 0)
            )
            for item in symbols
        }

        base = self.resolve_path(path)

        references: list[dict[str, typing.Any]] = []
        call_candidates: list[dict[str, typing.Any]] = []
        truncated_files: list[dict[str, typing.Any]] = []

        for item in self.walk_paths(base, recursive=True):
            if len(references) >= max_references:
                break
            if not item.is_file() or self.is_excluded_path(item) or not self.looks_text(item):
                continue

            rel = self.relative_path(item)
            if glob and not fnmatch.fnmatch(rel, glob) and not fnmatch.fnmatch(item.name, glob):
                continue

            size = item.stat().st_size
            raw = item.read_bytes()[:self.max_read_bytes]

            if size > self.max_read_bytes:
                truncated_files.append({
                    "path": rel,
                    "size": size,
                    "searched_bytes": self.max_read_bytes,
                    "unsearched_bytes": size - self.max_read_bytes
                })

            for lineno, line in enumerate(self.decode_bytes(raw).splitlines(), start=1):
                for name in names:
                    if not re.search(rf"\b{re.escape(name)}\b", line):
                        continue
                    if (name, rel, lineno) in definitions:
                        continue

                    reference = self._reference_record(
                        symbol=name,
                        path=rel,
                        line=lineno,
                        text=line.strip(),
                        call_candidate=self._is_call_candidate(name, line)
                    )
                    references.append(reference)
                    if reference["call_candidate"]:
                        call_candidates.append(reference)
                    break

                if len(references) >= max_references:
                    break

        for symbol in symbols:
            name = str(symbol.get("name") or "")

            symbol["reference_count"] = sum(1 for item in references if item.get("symbol") == name)
            symbol["call_candidate_count"] = sum(1 for item in call_candidates if item.get("symbol") == name)

        return {
            "references": references,
            "call_candidates": call_candidates,
            "truncated_files": truncated_files[:200]
        }


if __name__ == '__main__':
    pass
