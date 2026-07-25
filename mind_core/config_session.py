# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from mind_core.config import (
    ConfigOverride,
    config_override,
    apply_config_overrides,
    normalize_config
)
from mind_core.config_store import ConfigStore


class ConfigSession(object):
    """管理当前进程的持久化配置和临时覆盖。"""

    def __init__(
        self,
        store: ConfigStore,
        overrides: tuple[ConfigOverride, ...] = ()
    ) -> None:
        self.store     = store
        self.overrides = tuple(overrides)

    def load(self, *, create: bool = True) -> dict[str, typing.Any]:
        """返回文件配置与进程覆盖合并后的有效快照。"""
        raw = self.store.read_raw(create=create)
        return normalize_config(apply_config_overrides(raw, self.overrides))

    def update(
        self,
        values: dict[tuple[str, ...], object]
    ) -> dict[str, typing.Any]:
        """校验并持久化多个配置字段，再返回有效快照。"""
        normalize_config(self.store.read_raw())

        validated = {
            path: config_override(path, value).value
            for path, value in values.items()
        }
        self.store.update(validated)
        return self.load()

    def feature_enabled(self, name: str) -> bool:
        """返回指定已注册功能在当前进程中的状态。"""
        features = self.load().get("features")
        if not isinstance(features, dict):
            return False
        return features.get(name) is True


if __name__ == "__main__":
    pass
