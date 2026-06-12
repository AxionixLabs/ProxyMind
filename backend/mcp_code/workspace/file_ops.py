# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from backend.mcp_code.base import NativeCodingComponent
from backend.utilities import const


class WorkspaceFileTools(NativeCodingComponent):
    """提供工作区文本文件写入能力。"""

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


if __name__ == '__main__':
    pass
