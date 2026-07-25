# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from pathlib import Path
from mind_core.config import (
    ConfigOverride,
    config_override,
)
from mind_core.config_layers import (
    ConfigLayer,
    ConfigResolution,
    ConfigResolver
)
from mind_core.config_store import ConfigStore


class ConfigSession(object):
    """管理当前进程的持久化配置和临时覆盖。"""

    def __init__(
        self,
        store: ConfigStore,
        overrides: tuple[ConfigOverride, ...] = (),
        *,
        profile: str | None = None,
        workspace: Path | None = None
    ) -> None:
        self.store = store
        self.resolver = ConfigResolver(
            store,
            overrides,
            profile=profile,
            workspace=workspace,
        )

    def resolve(self, *, create: bool = True) -> ConfigResolution:
        """返回包含来源信息的有效配置结果。"""
        return self.resolver.resolve(create=create)

    def load(self, *, create: bool = True) -> dict[str, typing.Any]:
        """返回文件配置与进程覆盖合并后的有效快照。"""
        return self.resolve(create=create).config

    def layers(self, *, create: bool = True) -> tuple[ConfigLayer, ...]:
        """返回当前参与解析的配置来源。"""
        return self.resolve(create=create).layers

    def update_user(
        self,
        values: dict[tuple[str, ...], object]
    ) -> dict[str, typing.Any]:
        """完整校验后更新用户级配置并返回有效快照。"""
        validated = {
            path: config_override(path, value).value
            for path, value in values.items()
        }
        self.store.update(
            validated,
            validate=self._validate_user_candidate,
        )
        return self.load()

    def delete_user(
        self,
        paths: typing.Iterable[tuple[str, ...]]
    ) -> dict[str, typing.Any]:
        """完整校验后删除用户级配置路径并返回有效快照。"""
        self.store.delete(
            paths,
            validate=self._validate_user_candidate,
        )
        return self.load()

    def _validate_user_candidate(
        self,
        candidate: dict[str, object],
    ) -> None:
        """验证候选用户配置在当前分层上下文中有效。"""
        self.resolver.resolve_user_config(dict(candidate))


if __name__ == "__main__":
    pass
