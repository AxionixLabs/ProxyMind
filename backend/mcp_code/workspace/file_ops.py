# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
import fnmatch
from backend.mcp_code.base import NativeCodingComponent
from backend.utilities import const


class WorkspaceFileTools(NativeCodingComponent):
    """提供工作区文件读写、复制、移动和删除能力。"""

    @staticmethod
    def _read_line_window(
        target: typing.Any,
        *,
        start_line: int,
        max_lines: int
    ) -> tuple[str, int | None, bool]:
        """读取指定行窗口，避免大文件整文件进入内存。"""
        selected: list[str] = []

        last_line      = 0
        stop_after     = start_line + max_lines - 1
        line_truncated = False

        with target.open("r", encoding=const.CHARSET, errors=const.IGNORE, newline=None) as fh:
            for lineno, line in enumerate(fh, start=1):
                last_line = lineno
                if lineno < start_line:
                    continue
                if lineno > stop_after:
                    line_truncated = True
                    break
                selected.append(line.rstrip("\r\n"))

        total_lines = None if line_truncated else last_line
        return "\n".join(selected), total_lines, line_truncated

    @staticmethod
    def _read_file_truncation_reasons(
        *,
        input_truncated: bool,
        output_truncated: bool,
        line_truncated: bool,
        line_window: bool,
        total_lines: int | None
    ) -> list[str]:
        """返回文件读取被截断的具体原因。"""
        reasons: list[str] = []

        if input_truncated and not line_window:
            reasons.append("byte_limit")
        elif input_truncated and line_window and total_lines is None:
            reasons.append("large_file_window")
        if output_truncated:
            reasons.append("output_byte_limit")
        if line_truncated:
            reasons.append("line_window")

        return reasons

    def _file_state(
        self,
        target: typing.Any
    ) -> dict[str, typing.Any]:
        """返回单个文件的存在性、大小和 SHA256 摘要。"""
        if not target.exists() or not target.is_file():
            return {
                "exists" : False,
                "bytes"  : None,
                "sha256" : None
            }
        payload = target.read_bytes()
        return {
            "exists" : True,
            "bytes"  : len(payload),
            "sha256" : self.sha256_bytes(payload)
        }

    def list_file(
        self,
        *,
        path: str = ".",
        glob: str | None = None,
        recursive: bool = True,
        max_matches: int = 100
    ) -> dict[str, typing.Any]:
        """列出工作区内的文件路径。"""
        base = self.resolve_path(path)
        if not base.exists():
            return self.fail_result("path_not_found", path=path)
        if not base.is_dir():
            return self.fail_result("path_not_directory", path=path)

        limit = max(1, min(int(max_matches or 100), 1000))

        files: list[dict[str, typing.Any]] = []

        for item in self.walk_paths(base, recursive=bool(recursive)):
            if len(files) >= limit:
                break
            if self.is_excluded_path(item):
                continue

            rel = self.relative_path(item)
            if glob and not fnmatch.fnmatch(rel, glob) and not fnmatch.fnmatch(item.name, glob):
                continue

            files.append({
                "path"      : rel,
                "file_kind" : "dir" if item.is_dir() else "file",
                "size"      : item.stat().st_size if item.is_file() else None,
            })

        return self.ok_result(
            f"workspace list file ok path={self.relative_path(base)} files={len(files)}",
            path=self.relative_path(base),
            glob=glob,
            recursive=bool(recursive),
            files=files,
            file_count=len(files),
            truncated=len(files) >= limit,
            max_matches=limit
        )

    def read_file(
        self,
        *,
        path: str,
        start_line: int | None = None,
        max_lines: int | None = None,
        max_bytes: int | None = None
    ) -> dict[str, typing.Any]:
        """读取文本文件内容；大文件只读取预览或指定行窗口，避免全量进入内存。"""
        target = self.resolve_path(path)
        if not target.is_file():
            return self.fail_result("file_not_found", path=path)
        if not self.looks_text(target):
            return self.fail_result(
                "file_not_text",
                path=self.relative_path(target),
                size=target.stat().st_size
            )

        limit = max(1, min(int(max_bytes or self.max_read_bytes), self.max_read_bytes))
        size  = target.stat().st_size

        line_window = start_line is not None or max_lines is not None

        sha256: str | None     = None
        sha256_available: bool = False

        if size <= limit:

            full_raw         = target.read_bytes()
            sha256           = self.sha256_bytes(full_raw)
            sha256_available = True
            full_text        = self.decode_bytes(full_raw)
            full_lines       = full_text.splitlines()

            total_lines: int | None = len(full_lines)

            if line_window:
                start           = max(1, int(start_line or 1))
                count           = max(1, min(int(max_lines or 200), 2000))
                sliced          = full_lines[start - 1:start - 1 + count]
                text            = "\n".join(sliced)
                line_truncated  = start - 1 + count < total_lines
            else:
                start           = 1
                text            = full_text
                line_truncated  = False

            input_truncated = False

        elif line_window:
            start = max(1, int(start_line or 1))
            count = max(1, min(int(max_lines or 200), 2000))

            text, total_lines, line_truncated = self._read_line_window(
                target,
                start_line=start,
                max_lines=count
            )
            input_truncated = True

        else:
            with target.open("rb") as fh:
                raw = fh.read(limit + 1)

            start           = 1
            text            = self.decode_bytes(raw[:limit])
            total_lines     = None
            line_truncated  = False
            input_truncated = len(raw) > limit or size > limit

        content_raw = text.encode(const.CHARSET, const.IGNORE)

        output_truncated = len(content_raw) > limit
        if output_truncated:
            text = self.decode_bytes(content_raw[:limit])

        end_line = start + len(text.splitlines()) - 1 if text else start

        byte_truncated = input_truncated or output_truncated

        truncation_reasons = self._read_file_truncation_reasons(
            input_truncated=input_truncated,
            output_truncated=output_truncated,
            line_truncated=line_truncated,
            line_window=line_window,
            total_lines=total_lines
        )

        return self.ok_result(
            f"workspace read ok path={self.relative_path(target)} bytes={min(size, limit)} truncated={byte_truncated}",
            path=self.relative_path(target),
            content=text,
            size=size,
            sha256=sha256,
            sha256_available=sha256_available,
            window_start=start,
            window_end=end_line,
            total_lines_estimated=total_lines,
            truncated=bool(truncation_reasons),
            byte_truncated=byte_truncated,
            line_truncated=line_truncated,
            truncation_reasons=truncation_reasons
        )

    def write_file(
        self,
        *,
        path: str,
        content: str,
        create_dirs: bool = True,
        overwrite: bool = True,
        expected_sha256: str | None = None,
        force: bool = False
    ) -> dict[str, typing.Any]:
        """创建或整体覆盖文本文件，支持父目录创建和 sha256 冲突保护。"""
        target  = self.resolve_path(path)
        payload = str(content or "")
        size    = len(payload.encode(const.CHARSET, const.IGNORE))
        before  = self._file_state(target)

        if size > self.max_write_bytes:
            return self.fail_result("content_too_large", size=size, max_bytes=self.max_write_bytes)
        if target.exists() and not overwrite:
            return self.fail_result("file_exists", path=self.relative_path(target))
        if conflict := self.conflict_guard(target, expected_sha256=expected_sha256, force=force):
            return conflict

        if create_dirs:
            target.parent.mkdir(parents=True, exist_ok=True)

        target.write_text(payload, encoding=const.CHARSET, newline="")

        after = self._file_state(target)

        return self.ok_result(
            f"workspace write ok path={self.relative_path(target)} bytes={size}",
            path=self.relative_path(target),
            bytes=size,
            changed=before != after,
            bytes_before=before.get("bytes"),
            bytes_after=after.get("bytes"),
            sha256_before=before.get("sha256"),
            sha256_after=after.get("sha256"),
            sha256=after.get("sha256")
        )

    def move_file(
        self,
        *,
        source_path: str,
        target_path: str,
        overwrite: bool = False,
        create_dirs: bool = True,
        expected_sha256: str | None = None,
        force: bool = False
    ) -> dict[str, typing.Any]:
        """移动工作区内文件，支持覆盖控制、父目录创建和源文件 sha256 校验。"""
        try:
            source = self.resolve_path(source_path)
            target = self.resolve_path(target_path)
        except ValueError as exc:
            return self.fail_result("path_outside_workspace", error=str(exc))

        if not source.is_file():
            return self.fail_result("source_file_not_found", source_path=source_path)
        if target.exists() and target.is_dir():
            return self.fail_result("target_is_directory", target_path=self.relative_path(target))
        if target.exists() and not overwrite:
            return self.fail_result("target_exists", target_path=self.relative_path(target))

        payload = source.read_bytes()
        sha     = self.sha256_bytes(payload)
        target_before = self._file_state(target)

        if expected_sha256 and not force and expected_sha256 != sha:
            return self.fail_result(
                "file_changed_since_read",
                source_path=self.relative_path(source),
                expected_sha256=expected_sha256,
                actual_sha256=sha
            )

        if create_dirs:
            target.parent.mkdir(parents=True, exist_ok=True)
        elif not target.parent.exists():
            return self.fail_result("target_parent_not_found", target_path=self.relative_path(target))

        overwritten = target.exists()

        source.replace(target)
        source_after = self._file_state(source)
        target_after = self._file_state(target)

        return self.ok_result(
            f"workspace move ok source={self.relative_path(source)} target={self.relative_path(target)} bytes={len(payload)}",
            source_path=self.relative_path(source),
            target_path=self.relative_path(target),
            bytes=len(payload),
            changed=True,
            bytes_before=target_before.get("bytes"),
            bytes_after=target_after.get("bytes"),
            sha256_before=target_before.get("sha256"),
            sha256_after=target_after.get("sha256"),
            sha256=sha,
            source_exists_after=source_after.get("exists"),
            overwritten=overwritten
        )

    def copy_file(
        self,
        *,
        source_path: str,
        target_path: str,
        overwrite: bool = False,
        create_dirs: bool = True,
        expected_sha256: str | None = None,
        force: bool = False
    ) -> dict[str, typing.Any]:
        """复制工作区内文件，支持覆盖控制、父目录创建和源文件 sha256 校验。"""
        try:
            source = self.resolve_path(source_path)
            target = self.resolve_path(target_path)
        except ValueError as exc:
            return self.fail_result("path_outside_workspace", error=str(exc))

        if not source.is_file():
            return self.fail_result("source_file_not_found", source_path=source_path)
        if target.exists() and target.is_dir():
            return self.fail_result("target_is_directory", target_path=self.relative_path(target))
        if target.exists() and not overwrite:
            return self.fail_result("target_exists", target_path=self.relative_path(target))

        payload       = source.read_bytes()
        sha           = self.sha256_bytes(payload)
        target_before = self._file_state(target)

        if expected_sha256 and not force and expected_sha256 != sha:
            return self.fail_result(
                "file_changed_since_read",
                source_path=self.relative_path(source),
                expected_sha256=expected_sha256,
                actual_sha256=sha
            )

        if create_dirs:
            target.parent.mkdir(parents=True, exist_ok=True)
        elif not target.parent.exists():
            return self.fail_result("target_parent_not_found", target_path=self.relative_path(target))

        overwritten = target.exists()
        target.write_bytes(payload)
        target_after = self._file_state(target)

        return self.ok_result(
            f"workspace copy ok source={self.relative_path(source)} target={self.relative_path(target)} bytes={len(payload)}",
            source_path=self.relative_path(source),
            target_path=self.relative_path(target),
            bytes=len(payload),
            changed=target_before != target_after,
            bytes_before=target_before.get("bytes"),
            bytes_after=target_after.get("bytes"),
            sha256_before=target_before.get("sha256"),
            sha256_after=target_after.get("sha256"),
            sha256=sha,
            overwritten=overwritten
        )

    def delete_file(
        self,
        *,
        path: str,
        expected_sha256: str | None = None,
        force: bool = False
    ) -> dict[str, typing.Any]:
        """删除工作区内单个文件，删除前可按 sha256 校验当前内容。"""
        try:
            target = self.resolve_path(path)
        except ValueError as exc:
            return self.fail_result("path_outside_workspace", error=str(exc))

        if not target.exists():
            return self.fail_result("file_not_found", path=path)
        if not target.is_file():
            return self.fail_result("target_not_file", path=self.relative_path(target))

        payload = target.read_bytes()
        sha     = self.sha256_bytes(payload)
        before  = self._file_state(target)

        if expected_sha256 and not force and expected_sha256 != sha:
            return self.fail_result(
                "file_changed_since_read",
                path=self.relative_path(target),
                expected_sha256=expected_sha256,
                actual_sha256=sha
            )

        target.unlink()
        after = self._file_state(target)

        return self.ok_result(
            f"workspace delete ok path={self.relative_path(target)} bytes={len(payload)}",
            path=self.relative_path(target),
            bytes=len(payload),
            changed=before != after,
            bytes_before=before.get("bytes"),
            bytes_after=after.get("bytes"),
            sha256_before=before.get("sha256"),
            sha256_after=after.get("sha256"),
            sha256=sha
        )


if __name__ == '__main__':
    pass
