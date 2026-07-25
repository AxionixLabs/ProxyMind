# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import typing
import tomlkit
import tempfile
import contextlib
from collections.abc import MutableMapping
from pathlib import Path
from tomlkit.toml_document import TOMLDocument
from mind_nova import const
from mind_core.application_paths import default_application_home
from mind_core.provider_config import (
    DEFAULT_PROVIDER_NAME,
    DEFAULT_REASONING_EFFORT,
    DEFAULT_ROUTE_NAME
)

DEFAULT_CONFIG_TEXT = f"""[service]
domain = ""

[model.primary]
provider = "{DEFAULT_PROVIDER_NAME}"
route = "{DEFAULT_ROUTE_NAME}"
model = ""
apikey = ""
base_url = ""
reasoning_effort = "{DEFAULT_REASONING_EFFORT}"
enabled = false

[skills]
enabled = []
disabled = []

[features]

[hosted_tools.groups]
perf_engine = false
sandbox_cloud = false
"""


class ConfigStoreError(ValueError):
    """表示配置文档无法读取或写入。"""


def default_config_path() -> Path:
    """返回默认配置文件路径。"""
    return default_application_home() / "config.toml"


class ConfigStore(object):
    """保真读取并原子更新 TOML 配置文档。"""

    def __init__(self, path: Path) -> None:
        self.path = Path(path).expanduser()

    def ensure(self) -> Path:
        """确保默认配置文档存在。"""
        if not self.path.exists():
            self._write_text(DEFAULT_CONFIG_TEXT)
        return self.path

    def read_document(self, *, create: bool = True) -> TOMLDocument:
        """读取保留格式信息的 TOML 文档。"""
        target = self.ensure() if create else self.path
        try:
            text = target.read_text(encoding=const.CHARSET)
        except OSError as error:
            raise ConfigStoreError(
                f"config is not readable: {target}"
            ) from error

        try:
            return tomlkit.parse(text or DEFAULT_CONFIG_TEXT)
        except (TypeError, ValueError) as error:
            raise ConfigStoreError(
                f"config is invalid: {target} ({error})"
            ) from error

    def read_raw(self, *, create: bool = True) -> dict[str, object]:
        """读取不丢失未知字段的普通配置字典。"""
        return dict(self.read_document(create=create).unwrap())

    def update(self, values: dict[tuple[str, ...], object]) -> dict[str, object]:
        """更新指定点路径并保留其他格式和字段。"""
        document = self.read_document()
        for path, value in values.items():
            self._set_path(document, path, value)
        self._write_text(tomlkit.dumps(document))
        return dict(document.unwrap())

    @staticmethod
    def _set_path(
        document: TOMLDocument,
        path: tuple[str, ...],
        value: object,
    ) -> None:
        """在 TOML 文档中设置一个非空点路径。"""
        if not path or any(not component for component in path):
            raise ConfigStoreError("config path is empty")

        target: MutableMapping[str, typing.Any] = document
        for component in path[:-1]:
            child = target.get(component)
            if not isinstance(child, MutableMapping):
                child = tomlkit.table()
                target[component] = child
            target = child
        target[path[-1]] = tomlkit.item(value)

    def _write_text(self, text: str) -> None:
        """在同目录中原子替换配置文档。"""
        descriptor: int | None      = None
        temporary_path: Path | None = None

        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                dir=self.path.parent,
            )
            temporary_path = Path(temporary)
            with os.fdopen(
                descriptor,
                "w",
                encoding=const.CHARSET,
                newline="\n",
            ) as stream:
                descriptor = None
                stream.write(text)
                if text and not text.endswith("\n"):
                    stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, self.path)
        except OSError as error:
            raise ConfigStoreError(
                f"config is not writable: {self.path}"
            ) from error
        finally:
            if descriptor is not None:
                with contextlib.suppress(OSError):
                    os.close(descriptor)
            if temporary_path is not None:
                with contextlib.suppress(OSError):
                    temporary_path.unlink(missing_ok=True)


if __name__ == "__main__":
    pass
