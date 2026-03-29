# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from dataclasses import (
    dataclass, field
)

NexusKind = typing.Literal[
    "http", "sse", "ws", "graphql", "tcp", "udp", "smtp", "imap", "ftp"
]


@dataclass
class SseEvent:
    event: typing.Optional[str] = None
    data: str = ""
    id: typing.Optional[str] = None


@dataclass
class StepResult:
    name: str
    type: str
    ok: bool
    elapsed_ms: int
    detail: dict[str, typing.Any] = field(default_factory=dict)
    artifact: typing.Optional[dict[str, typing.Any]] = None


@dataclass
class NexusRequest:
    request: dict[str, typing.Any]
    name: typing.Optional[str] = None
    template_vars: dict[str, typing.Any] = field(default_factory=dict)
    extract: typing.Optional[dict[str, str]] = None
    asserts: typing.Optional[list[dict[str, typing.Any]]] = None


@dataclass
class NexusBatchItem:
    request: dict[str, typing.Any]
    name: typing.Optional[str] = None
    extract: typing.Optional[dict[str, str]] = None
    asserts: typing.Optional[list[dict[str, typing.Any]]] = None


@dataclass
class NexusBatchRequest:
    items: list[NexusBatchItem]
    env: dict[str, typing.Any] = field(default_factory=dict)
    template_vars: dict[str, typing.Any] = field(default_factory=dict)
    concurrency: int = 1
    fail_fast: bool = True


@dataclass
class RunRecord:
    mission_id: str
    ok: bool
    started_ms: int
    finished_ms: int
    payload: dict[str, typing.Any]
    final_ctx: dict[str, typing.Any]
    steps: list[StepResult]
    artifact: typing.Optional[dict[str, typing.Any]] = None


@dataclass
class ArtifactRecord:
    artifact_id: str
    kind: typing.Literal["mission", "step"]
    path: str
    manifest_path: str
    mission_id: str
    protocol: str
    created_ms: int
    step_index: typing.Optional[int] = None
    step_name: typing.Optional[str] = None
    media_dir: typing.Optional[str] = None
    parent_artifact_id: typing.Optional[str] = None


if __name__ == '__main__':
    pass
