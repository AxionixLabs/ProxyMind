# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import copy
import typing


class MergeService(object):
    """根据环境默认值与单项覆盖项生成最终协议请求。"""

    _DICT_MERGE_KEYS = {"headers", "json", "params", "form", "variables"}

    @classmethod
    def materialize(
        cls,
        *,
        env: typing.Optional[dict[str, typing.Any]] = None,
        request: typing.Optional[dict[str, typing.Any]] = None,
    ) -> dict[str, typing.Any]:
        """将环境默认值与请求覆盖项合并为可直接交给执行器的请求。"""
        env_dict = dict(env or {})
        request_dict = dict(request or {})
        return cls._merge_mapping(env_dict, request_dict)

    @classmethod
    def _merge_mapping(
        cls,
        base: dict[str, typing.Any],
        override: dict[str, typing.Any],
    ) -> dict[str, typing.Any]:
        merged: dict[str, typing.Any] = {}
        keys = set(base.keys()) | set(override.keys())

        for key in keys:
            base_value = base.get(key)
            override_value = override.get(key)

            if override_value is None:
                if base_value is not None:
                    merged[key] = copy.deepcopy(base_value)
                continue

            if key in cls._DICT_MERGE_KEYS and isinstance(base_value, dict) and isinstance(override_value, dict):
                merged[key] = cls._merge_mapping(base_value, override_value)
                continue

            merged[key] = copy.deepcopy(override_value)

        return merged

if __name__ == '__main__':
    pass
