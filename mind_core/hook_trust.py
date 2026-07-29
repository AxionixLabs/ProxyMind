# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import re
import json
import typing
import tempfile
import contextlib
from dataclasses import (
    dataclass,
    field
)
from pathlib import Path
from types import MappingProxyType
from mind_core.hooks import HookDefinitionConfig
from mind_nova import const

HookTrustState = typing.Literal[
    "implicit",
    "trusted",
    "untrusted",
]

HOOK_TRUST_FILENAME = "hook-trust.json"
HOOK_TRUST_VERSION  = 1
HOOK_HASH_PATTERN   = re.compile(r"^[0-9a-f]{64}$")

IMPLICIT_TRUST_SCOPES = frozenset({
    "user",
    "profile",
    "cli",
    "managed",
})


class HookTrustStoreError(ValueError):
    """表示 Hook 信任状态无法安全读取或写入。"""


@dataclass(frozen=True, slots=True)
class HookTrustSnapshot:
    """保存一次原子读取获得的 Hook 信任记录。"""
    records: typing.Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """复制信任记录，避免解析期间被外部修改。"""
        object.__setattr__(
            self,
            "records",
            MappingProxyType(dict(self.records)),
        )

    def state(self, definition: HookDefinitionConfig) -> HookTrustState:
        """返回指定 Hook 定义在当前快照中的信任状态。"""
        if definition.source_scope in IMPLICIT_TRUST_SCOPES:
            return "implicit"
        if self.records.get(definition.key) == definition.content_hash:
            return "trusted"

        return "untrusted"


def default_hook_trust_path(config_path: Path) -> Path:
    """返回与用户配置位于同一目录的 Hook 信任文件路径。"""
    return Path(config_path).expanduser().parent / HOOK_TRUST_FILENAME


class HookTrustStore:
    """原子持久化基于内容哈希的 Hook 信任状态。"""

    def __init__(self, path: Path) -> None:
        self.path = Path(path).expanduser()

    def load(self) -> HookTrustSnapshot:
        """读取并严格校验当前信任记录。"""
        if not self.path.exists():
            return HookTrustSnapshot()
        try:
            raw = json.loads(self.path.read_text(encoding=const.CHARSET))
        except (OSError, json.JSONDecodeError) as error:
            raise HookTrustStoreError(
                f"hook trust store is invalid: {self.path}"
            ) from error

        if not isinstance(raw, dict) or raw.get("version") != HOOK_TRUST_VERSION:
            raise HookTrustStoreError(
                f"hook trust store version is invalid: {self.path}"
            )
        trusted = raw.get("trusted")
        if not isinstance(trusted, dict):
            raise HookTrustStoreError(
                f"hook trust records are invalid: {self.path}"
            )

        records: dict[str, str] = {}
        for raw_key, raw_hash in trusted.items():
            if not isinstance(raw_key, str) or not raw_key.strip():
                raise HookTrustStoreError(
                    f"hook trust key is invalid: {self.path}"
                )
            if (
                not isinstance(raw_hash, str)
                or not HOOK_HASH_PATTERN.fullmatch(raw_hash)
            ):
                raise HookTrustStoreError(
                    f"hook trust hash is invalid: {self.path}"
                )
            records[raw_key] = raw_hash
        return HookTrustSnapshot(records)

    def trust(self, definition: HookDefinitionConfig) -> HookTrustSnapshot:
        """信任指定 Hook 当前内容并返回更新后的快照。"""
        records = dict(self.load().records)
        records[definition.key] = definition.content_hash
        self._write(records)
        return HookTrustSnapshot(records)

    def revoke(self, definition: HookDefinitionConfig) -> HookTrustSnapshot:
        """撤销指定 Hook 的持久信任记录。"""
        records = dict(self.load().records)
        records.pop(definition.key, None)
        self._write(records)
        return HookTrustSnapshot(records)

    def _write(self, records: dict[str, str]) -> None:
        """在同目录中原子替换信任文件。"""
        descriptor: int | None      = None
        temporary_path: Path | None = None

        payload = json.dumps(
            {
                "version": HOOK_TRUST_VERSION,
                "trusted": dict(sorted(records.items())),
            },
            ensure_ascii=True,
            indent=2,
        )

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
                stream.write(payload)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())

            os.replace(temporary_path, self.path)

        except OSError as error:
            raise HookTrustStoreError(
                f"hook trust store is not writable: {self.path}"
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
