# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import re
import typing
import fnmatch
from pathlib import Path
from backend.mcp_core.native_coding.base import NativeCodingComponent
from backend.utilities.trace import clip_text


class RepoMapTools(NativeCodingComponent):

    def repo_map(
        self,
        *,
        path: str = ".",
        glob: str | None = None,
        max_files: int = 200,
        max_symbols: int = 1000
    ) -> dict[str, typing.Any]:
        base = self._resolve(path)
        if not base.exists():
            return self._fail("path_not_found", path=path)

        max_files   = max(1, min(int(max_files or 200), 2000))
        max_symbols = max(1, min(int(max_symbols or 1000), 10000))

        files: list[dict[str, typing.Any]] = []
        symbols: list[dict[str, typing.Any]] = []
        imports: list[dict[str, typing.Any]] = []

        for item in self._walk(base, recursive=True):
            if len(files) >= max_files or len(symbols) >= max_symbols:
                break
            if not item.is_file() or self._is_excluded(item) or not self._looks_text(item):
                continue

            rel = self._rel(item)

            if glob and not fnmatch.fnmatch(rel, glob) and not fnmatch.fnmatch(item.name, glob):
                continue

            raw     = item.read_bytes()[:self.max_read_bytes]
            content = self._decode(raw)
            parsed  = self._parse_symbol_file(rel, content)

            if not parsed["symbols"] and not parsed["imports"]:
                continue

            files.append({
                "path"         : rel,
                "language"     : parsed["language"],
                "symbol_count" : len(parsed["symbols"]),
                "import_count" : len(parsed["imports"]),
                "symbols"      : parsed["symbols"][:50],
                "imports"      : parsed["imports"][:50]
            })

            symbols.extend(parsed["symbols"])
            imports.extend(parsed["imports"])

        return self._ok(
            f"repo map ok files={len(files)} symbols={len(symbols)} imports={len(imports)}",
            path=self._rel(base),
            files=files,
            symbols=symbols[:max_symbols],
            imports=imports[:max_symbols],
            file_count=len(files),
            symbol_count=len(symbols),
            import_count=len(imports),
            truncated=len(files) >= max_files or len(symbols) >= max_symbols
        )

    def find_symbol(
        self,
        *,
        query: str,
        path: str = ".",
        glob: str | None = None,
        max_matches: int = 50
    ) -> dict[str, typing.Any]:
        needle = str(query or "").strip()

        if not needle:
            return self._fail("query_empty")

        mapped = self.repo_map(
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

        return self._ok(
            f"repo symbol search ok matches={len(matches)} query={needle}",
            query=needle,
            matches=matches,
            match_count=len(matches),
            truncated=len(matches) >= max_matches
        )

    def _parse_symbol_file(
        self,
        path: str,
        content: str
    ) -> dict[str, typing.Any]:
        language = self._language_for_path(path)

        symbols: list[dict[str, typing.Any]] = []
        imports: list[dict[str, typing.Any]] = []

        scope_stack: list[tuple[int, str]] = []

        for lineno, line in enumerate(str(content or "").splitlines(), start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or stripped.startswith("//"):
                continue

            indent = len(line) - len(line.lstrip(" "))

            if language == "python":
                while scope_stack and indent <= scope_stack[-1][0]:
                    scope_stack.pop()
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

                match = re.match(r"^(?:async\s+)?def\s+([A-Za-z_]\w*)\s*\(", stripped)
                kind  = "function"

                if not match:
                    match = re.match(r"^class\s+([A-Za-z_]\w*)\b", stripped)
                    kind = "class"

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
                            lineno,
                            stripped
                        )
                    )
                    if kind == "class":
                        scope_stack.append((indent, qualified))
                    continue

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
                    ("function", r"^(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>"),
                    ("method", r"^(?:async\s+)?([A-Za-z_$][\w$]*)\s*\([^)]*\)\s*\{")
                ]
                for kind, pattern in patterns:
                    match = re.match(pattern, stripped)
                    if match:
                        name = match.group(1)
                        symbols.append(
                            self._symbol_record(path, language, kind, name, name, lineno, stripped)
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

        return {
            "language" : language,
            "symbols"  : symbols,
            "imports"  : imports
        }

    @staticmethod
    def _language_for_path(
        path: str
    ) -> str:
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

        return "text"

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
        return {
            "path"   : path,
            "line"   : line,
            "module" : clip_text(str(module or "").strip(), limit=300),
            "names"  : clip_text(str(names or "").strip(), limit=300)
        }


if __name__ == '__main__':
    pass
