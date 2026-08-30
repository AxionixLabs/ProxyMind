# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import sys
import typing
from dataclasses import dataclass
from pathlib import Path
from metadata import const

ApplicationMode      = typing.Literal["source", "packaged"]
PACKAGED_ENTRY_NAMES = {const.APP_NAME, f"{const.APP_NAME}.exe"}
SOURCE_ENTRY_NAME    = f"{const.APP_NAME}.py"
APP_HOME_ENV         = f"{const.APP_NAME.upper()}_HOME"


def default_application_home() -> Path:
    """返回默认应用数据目录。"""
    configured = os.environ.get(APP_HOME_ENV)

    return Path(
        configured or Path.home() / f".{const.APP_NAME}"
    ).expanduser()


def is_packaged_executable(path: str | Path) -> bool:
    """根据可执行文件路径判断是否为独立应用入口。"""
    return Path(path).name.strip().lower() in PACKAGED_ENTRY_NAMES


@dataclass(frozen=True, slots=True)
class ApplicationLayout(object):
    """描述当前应用入口及其本地资源目录。"""
    mode: ApplicationMode
    platform: str
    executable: Path
    root: Path
    supports: Path

    @property
    def packaged(self) -> bool:
        """返回入口是否为独立打包产物。"""
        return self.mode == "packaged"


def _packaged_executable(entry_path: Path, executable: str | Path | None) -> Path:
    """解析独立打包产物的真实可执行文件路径。"""
    runtime_executable = Path(
        sys.executable if executable is None else executable
    ).expanduser()

    if is_packaged_executable(runtime_executable):
        return runtime_executable.resolve()

    return entry_path.expanduser().resolve()


def resolve_application_layout(
    *,
    entry_file: str | Path | None = None,
    argv0: str | Path | None = None,
    executable: str | Path | None = None,
    platform: str | None = None
) -> ApplicationLayout:
    """根据源码或打包入口解析统一的应用资源布局。"""
    entry_path = Path(sys.argv[0] if argv0 is None else argv0)
    entry_name = entry_path.name.strip().lower()

    if (
        argv0 is None
        and entry_name != SOURCE_ENTRY_NAME
        and not is_packaged_executable(entry_path)
        and entry_file is not None
    ):
        source_entry = Path(entry_file)
        if source_entry.name.strip().lower() == SOURCE_ENTRY_NAME:
            entry_path = source_entry
            entry_name = SOURCE_ENTRY_NAME

    current_platform = (sys.platform if platform is None else platform).strip().lower()

    if entry_name == SOURCE_ENTRY_NAME:
        resolved_entry = Path(entry_file or entry_path).expanduser().resolve()
        mode: ApplicationMode = "source"
    elif is_packaged_executable(entry_path):
        resolved_entry = _packaged_executable(entry_path, executable)
        mode = "packaged"
    else:
        raise ValueError(f"unsupported entry: {entry_name or '<empty>'}")

    root = resolved_entry.parent

    support_name = {
        "win32": "windows",
        "darwin": "macos",
    }.get(current_platform, current_platform or "unsupported")

    supports = root / const.SCHEMATIC / const.SUPPORTS / support_name

    return ApplicationLayout(
        mode=mode,
        platform=current_platform,
        executable=resolved_entry,
        root=root,
        supports=supports,
    )


if __name__ == '__main__':
    pass
