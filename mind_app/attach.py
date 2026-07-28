# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import glob
import base64
import typing
import mimetypes
from pathlib import Path
from engine.errors import AppError


class Attach(object):
    """附件状态与上传编排。"""

    TEXT_ATTACHMENT_SUFFIXES: typing.ClassVar[frozenset[str]] = frozenset({
        ".txt", ".md", ".markdown", ".json", ".yaml", ".yml",
        ".csv", ".log", ".xml", ".html", ".htm", ".cfg", ".ini"
    })

    IMAGE_ATTACHMENT_SUFFIXES: typing.ClassVar[frozenset[str]] = frozenset({
        ".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"
    })

    INVISIBLE_PATH_CHARS: typing.ClassVar[frozenset[str]] = frozenset({
        "\ufeff",
        "\u200b",
        "\u200c",
        "\u200d",
        "\u200e",
        "\u200f",
        "\u202a",
        "\u202b",
        "\u202c",
        "\u202d",
        "\u202e",
        "\u2066",
        "\u2067",
        "\u2068",
        "\u2069",
    })

    INVISIBLE_PATH_TRANSLATION: typing.ClassVar[dict[int, None]] = {
        ord(char): None for char in INVISIBLE_PATH_CHARS
    }

    def __init__(self) -> None:
        """初始化待上传附件列表。"""
        self.pending: list[dict[str, typing.Any]] = []

    @classmethod
    def _classify_attachment(cls, path: Path) -> tuple[str, str]:
        """根据文件名和 MIME 类型判断附件类型。"""
        mime_type = mimetypes.guess_type(path.name)[0] or ""
        suffix    = path.suffix.lower()

        if mime_type.startswith("image/") or suffix in cls.IMAGE_ATTACHMENT_SUFFIXES:
            return "image", mime_type or "image/png"

        if mime_type.startswith("text/") or suffix in cls.TEXT_ATTACHMENT_SUFFIXES:
            return "file", mime_type or "text/plain"

        return "file", mime_type or "application/octet-stream"

    @staticmethod
    def _clean_invisible_path_chars(value: str) -> str:
        """移除复制路径时常见的不可见 Unicode 控制符。"""
        return str(value or "").translate(Attach.INVISIBLE_PATH_TRANSLATION)

    @staticmethod
    def _strip_wrapped_quotes(value: str) -> str:
        """移除路径参数外层成对引号。"""
        text = Attach._clean_invisible_path_chars(value).strip()
        if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
            return Attach._clean_invisible_path_chars(text[1:-1]).strip()

        return text

    @staticmethod
    def _dedupe_paths(paths: list[Path]) -> list[Path]:
        """按路径字符串去重并保留原有顺序。"""
        seen: set[str]     = set()
        result: list[Path] = []

        for path in paths:
            key = str(path)
            if key in seen:
                continue
            seen.add(key)
            result.append(path)

        return result

    def _resolve_attachment_path(self, raw_path: str, *, must_exist: bool) -> Path:
        """解析附件路径，并按需校验文件是否存在。"""
        text = self._strip_wrapped_quotes(raw_path)
        if not text:
            raise AppError("attach invalid: /attach <path>")

        path = Path(text).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path

        try:
            path = path.resolve(strict=False)
        except (OSError, RuntimeError):
            path = path.absolute()

        if must_exist and not path.exists():
            raise AppError(f"attach file not found: {path}")

        return path

    def _resolve_glob_base(self, raw_path: str) -> str:
        """解析 glob 表达式的基准路径字符串。"""
        text = self._strip_wrapped_quotes(raw_path)
        if not text:
            raise AppError("attach invalid: /attach <path>")

        path = Path(text).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path

        return str(path)

    def _expand_attachment_inputs(self, raw_path: str) -> list[Path]:
        """把文件、目录或 glob 输入展开为文件路径列表。"""
        pattern = self._resolve_glob_base(raw_path)

        if glob.has_magic(pattern):
            matched = [
                Path(item).resolve(strict=False)
                for item in sorted(glob.glob(pattern, recursive=True))
                if Path(item).is_file()
            ]
            matched = self._dedupe_paths(matched)
            if matched:
                return matched
            raise AppError(f"attach no files matched: {pattern}")

        path = self._resolve_attachment_path(raw_path, must_exist=True)
        if path.is_dir():
            items = [
                child.resolve(strict=False)
                for child in sorted(path.iterdir(), key=lambda item: item.name.lower())
                if child.is_file()
            ]
            if items:
                return self._dedupe_paths(items)
            raise AppError(f"attach directory has no files: {path}")

        return [path]

    def has_pending_attachments(self) -> bool:
        """判断是否存在待上传附件。"""
        return bool(self.pending)

    def pending_attachments_snapshot(self) -> list[dict[str, typing.Any]]:
        """返回待上传附件的浅拷贝快照。"""
        return [dict(item) for item in self.pending]

    def add_pending_attachments(self, raw_path: str) -> dict[str, typing.Any]:
        """添加文件、目录或 glob 匹配到的附件。"""
        candidates = self._expand_attachment_inputs(raw_path)

        added: list[dict[str, typing.Any]]    = []
        existing: list[dict[str, typing.Any]] = []
        skipped: list[dict[str, typing.Any]]  = []

        for path in candidates:
            local = str(path)

            try:
                kind, mime_type = self._classify_attachment(path)
            except AppError as error:
                skipped.append({
                    "local"    : local,
                    "filename" : path.name,
                    "error"    : str(error)
                })
                continue

            for item in self.pending:
                if item.get("local") == local:
                    existing.append(dict(item))
                    break
            else:
                payload = {
                    "local"     : local,
                    "kind"      : kind,
                    "filename"  : path.name,
                    "mime_type" : mime_type,
                    "size"      : int(path.stat().st_size)
                }
                self.pending.append(payload)
                added.append(dict(payload))

        if not added and not existing:
            if skipped:
                raise AppError(
                    "attach no supported files found in selection: "
                    f"{', '.join(item['filename'] for item in skipped[:3])}"
                )
            raise AppError("attach no files were added")

        return {
            "added"    : added,
            "existing" : existing,
            "skipped"  : skipped
        }

    def remove_pending_attachment(self, query: str) -> dict[str, typing.Any]:
        """按序号或路径移除一个待上传附件。"""
        text = self._strip_wrapped_quotes(query)
        if not text:
            raise AppError("detach invalid: /detach <index|path>")
        if text.startswith("<") and text.endswith(">"):
            raise AppError("detach invalid: /detach <index|path>")

        if text.isdigit():
            index = int(text) - 1
            if 0 <= index < len(self.pending):
                return self.pending.pop(index)
            raise AppError(f"detach index out of range: {text}")

        local = str(self._resolve_attachment_path(text, must_exist=False))
        for index, item in enumerate(self.pending):
            if item.get("local") == local:
                return self.pending.pop(index)

        raise AppError(f"detach missing attachment: {local}")

    def clear_pending_attachments(self) -> int:
        """清空待上传附件并返回清理数量。"""
        count = len(self.pending)
        self.pending.clear()
        return count

    def consume_pending_attachments(self) -> list[dict[str, str]]:
        """把待发送文件转换为对话请求可直接携带的附件。"""
        attachments: list[dict[str, str]] = []

        for item in self.pending:
            local = str(item.get("local") or "")
            if not local:
                continue

            path = Path(local)
            try:
                content = path.read_bytes()
            except OSError as error:
                raise AppError(
                    f"attach file could not be read: {path}"
                ) from error

            mime_type = str(
                item.get("mime_type")
                or mimetypes.guess_type(path.name)[0]
                or "application/octet-stream"
            )
            data = base64.b64encode(content).decode("ascii")
            attachments.append({
                "kind": str(item.get("kind") or "file"),
                "filename": str(item.get("filename") or path.name),
                "mime_type": mime_type,
                "data_url": f"data:{mime_type};base64,{data}",
            })

        self.pending.clear()
        return attachments


if __name__ == '__main__':
    pass
