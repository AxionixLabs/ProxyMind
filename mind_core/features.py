# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from dataclasses import dataclass


class FeatureConfigError(ValueError):
    """表示功能开关名称或值无效。"""


@dataclass(frozen=True, slots=True)
class FeatureDefinition(object):
    """描述一个可由配置覆盖的功能开关。"""

    name: str
    default: bool


class FeatureRegistry(object):
    """校验并解析已注册的功能开关。"""

    def __init__(self, definitions: tuple[FeatureDefinition, ...]) -> None:
        self._definitions = {
            definition.name: definition
            for definition in definitions
        }

    def names(self) -> tuple[str, ...]:
        """返回按名称排序的功能开关。"""
        return tuple(sorted(self._definitions))

    def require(self, name: str) -> FeatureDefinition:
        """返回指定功能定义，不存在时抛出配置错误。"""
        key = str(name or "").strip()

        definition = self._definitions.get(key)
        if definition is None:
            available = ", ".join(self.names()) or "none"
            raise FeatureConfigError(
                f"unknown feature: {key or '<empty>'}; available: {available}"
            )

        return definition

    def resolve(self, raw: object) -> dict[str, bool]:
        """把文件配置合并到功能开关默认值。"""
        values = {
            name: definition.default
            for name, definition in self._definitions.items()
        }

        if raw is None:
            return values
        if not isinstance(raw, dict):
            raise FeatureConfigError("features must be a table")

        for raw_name, value in raw.items():
            name = str(raw_name or "").strip()
            self.require(name)
            if not isinstance(value, bool):
                raise FeatureConfigError(
                    f"features.{name} must be a boolean"
                )
            values[name] = value

        return values


FEATURE_REGISTRY = FeatureRegistry(())


if __name__ == "__main__":
    pass
