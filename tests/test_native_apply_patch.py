# -*- coding: utf-8 -*-

import tempfile
import unittest
from pathlib import Path

from mind_app.native_coding import NativeCoding


class NativeApplyPatchTest(unittest.TestCase):
    """覆盖原生补丁入口的关键结果。"""

    def test_missing_begin_returns_reason(self) -> None:
        """缺少起始标记时返回稳定失败原因。"""
        patch = "*** Add File: demo.txt\n+hello\n*** End Patch"

        with tempfile.TemporaryDirectory() as workspace:
            result = NativeCoding(root=workspace).apply_patch(patch=patch)

        self.assertFalse(result["ok"])
        self.assertEqual(result["data"]["reason"], "native_patch_missing_begin")

    def test_add_file_patch_writes_file(self) -> None:
        """合法新增文件补丁会写入工作区。"""
        patch = "\n".join([
            "*** Begin Patch",
            "*** Add File: demo.txt",
            "+hello",
            "*** End Patch",
        ])

        with tempfile.TemporaryDirectory() as workspace:
            result = NativeCoding(root=workspace).apply_patch(patch=patch)
            target = Path(workspace) / "demo.txt"

            self.assertTrue(result["ok"])
            self.assertEqual(target.read_text(encoding="utf-8"), "hello\n")
            self.assertEqual(result["data"]["file_count"], 1)
            self.assertEqual(result["data"]["created_files"][0]["path"], "demo.txt")


if __name__ == "__main__":
    unittest.main()
