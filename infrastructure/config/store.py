# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import contextlib
import os
import tempfile
import typing
from collections.abc import (
    Callable,
    MutableMapping,
)
from pathlib import Path

import tomlkit
from tomlkit.items import (
    AoT,
    Comment,
    Table,
    Whitespace,
)
from tomlkit.toml_document import TOMLDocument

from infrastructure.config.paths import default_config_home
from infrastructure.config.providers import (
    DEFAULT_PROVIDER_ID,
    DEFAULT_PROVIDER_KIND,
    DEFAULT_REASONING_EFFORT,
    DEFAULT_ROUTE_NAME,
)
from metadata import const

ConfigBody = list[tuple[typing.Any, typing.Any]]

TableLocation = tuple[Table, ConfigBody | None]

DEFAULT_CONFIG_TEXT = f"""model_provider = "{DEFAULT_PROVIDER_ID}"
project_root_markers = [".git"]
approvals_reviewer = "user"
network_access = "restricted"

[model_providers.{DEFAULT_PROVIDER_ID}]
name = "{DEFAULT_PROVIDER_ID}"
kind = "{DEFAULT_PROVIDER_KIND}"
model = ""
route = "{DEFAULT_ROUTE_NAME}"
reasoning_effort = "{DEFAULT_REASONING_EFFORT}"
api_key = ""
base_url = ""

[service]
domain = ""

[skills]
enabled = []
disabled = []

[features]
js_repl = false
subagents = false
exec_permission_approvals = false
request_permissions_tool = false

[hooks]

[agents]
max_concurrent_threads_per_session = 4
max_depth = 1
default_fork_turns = 5
max_fork_context_chars = 40000

[mcp_servers]

[hosted_tools.groups]
perf_engine = false
sandbox_cloud = false

[projects]
"""


class ConfigStoreError(ValueError):
    """表示配置文档无法读取或写入。"""


def default_config_path() -> Path:
    """返回默认配置文件路径。"""
    return default_config_home() / "config.toml"


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

    def read_raw(self, *, create: bool = True) -> dict[str, typing.Any]:
        """在 TOML 适配边界读取不丢失未知字段的动态配置字典。"""
        return dict(self.read_document(create=create).unwrap())

    def update(
        self,
        values: dict[tuple[str, ...], object],
        *,
        delete_paths: typing.Iterable[tuple[str, ...]] = (),
        validate: Callable[[dict[str, object]], None] | None = None
    ) -> dict[str, object]:
        """校验候选文档后更新指定点路径并保留其他格式。"""
        document = self.read_document()

        for path in delete_paths:
            self._delete_path(document, path)
        for path, value in values.items():
            self._set_path(document, path, value)

        self._ensure_table_group_spacing(document)

        candidate = dict(document.unwrap())

        if validate is not None:
            validate(candidate)
        self._write_text(tomlkit.dumps(document))

        return candidate

    def delete(
        self,
        paths: typing.Iterable[tuple[str, ...]],
        *,
        validate: Callable[[dict[str, object]], None] | None = None
    ) -> dict[str, object]:
        """校验候选文档后删除指定点路径并保留其他格式。"""
        document = self.read_document()

        for path in paths:
            self._delete_path(document, path)

        self._ensure_table_group_spacing(document)
        candidate = dict(document.unwrap())

        if validate is not None:
            validate(candidate)
        self._write_text(tomlkit.dumps(document))

        return candidate

    @staticmethod
    def _set_path(
        document: TOMLDocument,
        path: tuple[str, ...],
        value: object
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

    @classmethod
    def _ensure_table_group_spacing(cls, document: TOMLDocument) -> None:
        """在相邻配置表之间保留一个空行。"""
        root_tables = [
            (index, item)
            for index, (key, item) in enumerate(document.body)
            if key is not None and isinstance(item, (AoT, Table))
        ]
        tables = [
            table
            for _index, item in root_tables
            for table in cls._rendered_tables(item, document.body)
        ]
        if root_tables and tables:
            cls._separate_first_table(
                document.body,
                root_tables[0][0],
                tables[0][0],
            )
        for (previous, _body), (following, body) in zip(
            tables,
            tables[1:],
        ):
            if cls._separate_boundary_comments(
                previous,
                following,
                body,
            ):
                continue
            previous_body = previous.value.body
            if (
                previous_body
                and isinstance(previous_body[-1][1], Whitespace)
            ):
                continue
            if "\n" not in following.trivia.indent:
                following.trivia.indent = f"\n{following.trivia.indent}"

    @classmethod
    def _separate_first_table(
        cls,
        body: ConfigBody,
        table_index: int,
        table: Table
    ) -> None:
        """在顶层字段与第一个配置表之间保留一个空行。"""
        insert_at = table_index
        while (
            insert_at > 0
            and isinstance(body[insert_at - 1][1], Comment)
        ):
            insert_at -= 1
        if insert_at == 0:
            return
        if insert_at < table_index:
            cls._ensure_blank_line_at(body, insert_at)
            return
        if (
            not isinstance(body[insert_at - 1][1], Whitespace)
            and "\n" not in table.trivia.indent
        ):
            table.trivia.indent = f"\n{table.trivia.indent}"

    @classmethod
    def _rendered_tables(
        cls,
        item: object,
        body: ConfigBody | None
    ) -> typing.Iterator[TableLocation]:
        """按序返回配置组中实际输出的表及其所在容器。"""
        if isinstance(item, AoT):
            for table in item.body:
                yield table, None
                for child_key, child in table.value.body:
                    if child_key is not None and isinstance(
                        child,
                        (AoT, Table),
                    ):
                        yield from cls._rendered_tables(
                            child,
                            table.value.body,
                        )
            return
        if not isinstance(item, Table):
            return
        if not item.is_super_table():
            yield item, body
        for child_key, child in item.value.body:
            if child_key is not None and isinstance(child, (AoT, Table)):
                yield from cls._rendered_tables(child, item.value.body)

    @classmethod
    def _separate_boundary_comments(
        cls,
        previous: Table,
        following: Table,
        body: ConfigBody | None
    ) -> bool:
        """分隔相邻表之间的连续注释并识别已有空行。"""
        if body is not None:
            table_index = next(
                (
                    index
                    for index, (_key, item) in enumerate(body)
                    if item is following
                ),
                None,
            )
            if table_index:
                insert_at = table_index
                while (
                    insert_at > 0
                    and isinstance(body[insert_at - 1][1], Comment)
                ):
                    insert_at -= 1
                if insert_at < table_index:
                    cls._ensure_blank_line_at(body, insert_at)
                    return True
                if isinstance(body[insert_at - 1][1], Whitespace):
                    return True

        previous_body = previous.value.body
        if previous_body and isinstance(previous_body[-1][1], Comment):
            insert_at = len(previous_body)
            while (
                insert_at > 0
                and isinstance(previous_body[insert_at - 1][1], Comment)
            ):
                insert_at -= 1
            cls._ensure_blank_line_at(previous_body, insert_at)
            return True
        return False

    @staticmethod
    def _ensure_blank_line_at(body: ConfigBody, index: int) -> None:
        """在容器指定位置之前保留一个空行。"""
        following = body[index][1]
        if "\n" in following.trivia.indent:
            return
        if index == 0:
            following.trivia.indent = f"\n{following.trivia.indent}"
            return
        previous = body[index - 1][1]
        if isinstance(previous, Whitespace):
            return
        trail = previous.trivia.trail
        if trail.endswith("\n\n"):
            return
        previous.trivia.trail = (
            f"{trail}\n" if trail.endswith("\n") else f"{trail}\n\n"
        )

    @staticmethod
    def _delete_path(document: TOMLDocument, path: tuple[str, ...]) -> None:
        """从 TOML 文档中删除一个已经存在的点路径。"""
        if not path or any(not component for component in path):
            raise ConfigStoreError("config path is empty")

        target: MutableMapping[str, typing.Any] = document
        for component in path[:-1]:
            child = target.get(component)
            if not isinstance(child, MutableMapping):
                raise ConfigStoreError(
                    f"config path does not exist: {'.'.join(path)}"
                )
            target = child
        if path[-1] not in target:
            raise ConfigStoreError(
                f"config path does not exist: {'.'.join(path)}"
            )
        del target[path[-1]]

    def _write_text(self, text: str) -> None:
        """在同目录中原子替换配置文档。"""
        descriptor: int | None = None
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


if __name__ == '__main__':
    pass
