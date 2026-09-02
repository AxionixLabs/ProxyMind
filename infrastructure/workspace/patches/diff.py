# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import difflib
import hashlib
from dataclasses import dataclass
from metadata import const


@dataclass(frozen=True, slots=True)
class RenderedFileDiff:
    """记录单个文件差异的渲染结果。"""

    path: str
    text: str
    omitted: bool = False


class DiffRenderer:
    """把两份文本内容渲染为统一差异文本。"""

    MAX_CONTENT_BYTES = 512_000
    MAX_DIFF_BYTES = 512_000
    TIMEOUT_SEC = 0.10

    @classmethod
    def render_file(
        cls,
        *,
        old_path: str,
        new_path: str,
        old_content: str | None,
        new_content: str | None
    ) -> RenderedFileDiff:
        """渲染单个文件的统一差异文本。"""
        started = time.perf_counter()

        old_text = old_content
        new_text = new_content

        old_label = cls._git_path(old_path, prefix="a")
        new_label = cls._git_path(new_path, prefix="b")

        header = cls._header_lines(
            old_path=old_path,
            new_path=new_path,
            old_content=old_text,
            new_content=new_text
        )

        if cls._too_large(old_text) or cls._too_large(new_text):
            text = "".join([*header, "diff omitted: content too large\n"])
            return RenderedFileDiff(path=new_path or old_path, text=text, omitted=True)

        hunks = list(difflib.unified_diff(
            cls._split_lines(old_text),
            cls._split_lines(new_text),
            fromfile=old_label,
            tofile=new_label,
            lineterm="\n"
        ))

        if time.perf_counter() - started > cls.TIMEOUT_SEC:
            text = "".join([*header, "diff omitted: render timeout\n"])
            return RenderedFileDiff(path=new_path or old_path, text=text, omitted=True)

        body = hunks[2:] if len(hunks) >= 2 else []
        text = "".join([*header, *body])

        if len(text.encode(const.CHARSET, const.IGNORE)) > cls.MAX_DIFF_BYTES:
            text = "".join([*header, "diff omitted: rendered diff too large\n"])
            return RenderedFileDiff(path=new_path or old_path, text=text, omitted=True)

        return RenderedFileDiff(path=new_path or old_path, text=text)

    @classmethod
    def render_many(
        cls,
        entries: list[dict[str, str | None]]
    ) -> str:
        """渲染多个文件差异并拼接为单个文本。"""
        chunks: list[str] = []
        for entry in entries:
            rendered = cls.render_file(
                old_path=str(entry.get("old_path") or ""),
                new_path=str(entry.get("new_path") or ""),
                old_content=entry.get("old_content"),
                new_content=entry.get("new_content")
            )
            if rendered.text:
                chunks.append(rendered.text)
        return "".join(chunks)

    @classmethod
    def _header_lines(
        cls,
        *,
        old_path: str,
        new_path: str,
        old_content: str | None,
        new_content: str | None
    ) -> list[str]:
        """生成文件差异文本头部。"""
        old_git = cls._git_path(old_path, prefix="a")
        new_git = cls._git_path(new_path, prefix="b")

        old_sha = cls._git_blob_oid(old_content)
        new_sha = cls._git_blob_oid(new_content)

        lines = [f"diff --git {old_git} {new_git}\n"]

        if old_content is None:
            lines.append("new file mode 100644\n")
        elif new_content is None:
            lines.append("deleted file mode 100644\n")
        elif old_path != new_path:
            if old_content == new_content:
                lines.append("similarity index 100%\n")
            lines.append(f"rename from {old_path}\n")
            lines.append(f"rename to {new_path}\n")

        lines.append(f"index {old_sha[:7]}..{new_sha[:7]} 100644\n")
        lines.append(f"--- {old_git if old_content is not None else '/dev/null'}\n")
        lines.append(f"+++ {new_git if new_content is not None else '/dev/null'}\n")
        return lines

    @staticmethod
    def _split_lines(content: str | None) -> list[str]:
        """按保留换行符的方式拆分文本。"""
        if content is None:
            return []
        return str(content).splitlines(keepends=True)

    @staticmethod
    def _git_path(path: str, *, prefix: str) -> str:
        """生成差异文本中的路径标签。"""
        clean = str(path or "").replace("\\", "/")
        return f"{prefix}/{clean}" if clean else "/dev/null"

    @staticmethod
    def _too_large(content: str | None) -> bool:
        """判断文本内容是否超过渲染上限。"""
        if content is None:
            return False
        return len(str(content).encode(const.CHARSET, const.IGNORE)) > DiffRenderer.MAX_CONTENT_BYTES

    @staticmethod
    def _git_blob_oid(content: str | None) -> str:
        """计算内容标识。"""
        data = (content or "").encode(const.CHARSET, const.IGNORE)
        header = f"blob {len(data)}\0".encode("ascii")
        return hashlib.sha1(header + data).hexdigest()


if __name__ == '__main__':
    pass
