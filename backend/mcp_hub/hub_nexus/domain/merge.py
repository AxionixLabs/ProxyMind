# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import copy
import typing


class MergeService(object):
    """Materialize the final protocol request from env defaults and per-item overrides."""

    _DICT_MERGE_KEYS = {"headers", "json", "json_body", "params", "form", "variables"}

    _ALIAS_GROUPS = (
        ("json", "json_body"),
        ("body_text", "body"),
        ("operation_name", "operationName"),
    )

    @classmethod
    def materialize(
        cls,
        *,
        env: typing.Optional[dict[str, typing.Any]] = None,
        request: typing.Optional[dict[str, typing.Any]] = None,
    ) -> dict[str, typing.Any]:
        """Merge env defaults and request overrides into a single executor-ready request."""
        env_dict = dict(env or {})
        request_dict = dict(request or {})
        merged = cls._merge_mapping(env_dict, request_dict)
        cls._normalize_alias_groups(merged)
        return merged

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

    @classmethod
    def _normalize_alias_groups(cls, payload: dict[str, typing.Any]) -> None:
        for canonical, alias in cls._ALIAS_GROUPS:
            cls._normalize_alias_pair(payload, canonical=canonical, alias=alias)

    @classmethod
    def _normalize_alias_pair(
        cls,
        payload: dict[str, typing.Any],
        *,
        canonical: str,
        alias: str,
    ) -> None:
        canonical_value = payload.get(canonical)
        alias_value = payload.get(alias)

        if isinstance(canonical_value, dict) and isinstance(alias_value, dict):
            payload[canonical] = cls._merge_mapping(alias_value, canonical_value)
            payload.pop(alias, None)
            return

        if canonical_value is None and alias_value is not None:
            payload[canonical] = alias_value
        payload.pop(alias, None)


if __name__ == '__main__':
    pass
