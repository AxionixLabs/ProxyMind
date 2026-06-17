from pathlib import Path
import os
import sys

from mind_nova import const
from mind_core.paths import schematic_root


def bundled_skills_root() -> Path:
    """返回内置 skills 根目录。"""
    return schematic_root() / "skills" / "bundled"


def project_skills_root() -> Path:
    """返回当前工作区的项目级 skills 根目录。"""
    return Path.cwd().resolve() / ".mind" / "skills"


def user_skills_root() -> Path:
    """返回当前用户的 skills 根目录。"""
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
        return base / const.APP_DESC / "skills"

    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / const.APP_DESC / "skills"

    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / const.APP_NAME / "skills"
