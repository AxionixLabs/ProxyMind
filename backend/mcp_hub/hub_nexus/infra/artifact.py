# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import re
import json
import typing
from pathlib import Path
from dataclasses import asdict
from backend.models.model_nexus import ArtifactRecord
from backend.mcp_hub.hub_nexus.infra.core import ClockService
from backend.utilities import const


class ArtifactService(object):
    """负责 mission/step artifact 目录管理与 manifest 落盘。"""

    @staticmethod
    def _slug(value: str, fallback: str) -> str:
        text = re.sub(r"[^0-9A-Za-z._-]+", "_", str(value or "").strip()).strip("._-")
        return text[:64] or fallback

    @staticmethod
    def artifact_enabled(request: dict[str, typing.Any]) -> bool:
        """当 request 配置了 artifact_dir 时启用 artifact 落盘。"""
        return bool(str((request or {}).get("artifact_dir") or "").strip())

    @staticmethod
    def normalize_base_dir(artifact_dir: str | None) -> Path:
        """把配置的 artifact_dir 解析成可写的绝对根目录。"""
        base_dir = Path(artifact_dir or ".").expanduser().resolve()
        base_dir.mkdir(parents=True, exist_ok=True)
        return base_dir

    @staticmethod
    def create_mission_artifact(
        *,
        artifact_dir: str,
        mission_id: str,
        protocol: str
    ) -> ArtifactRecord:
        """创建 mission 级 artifact 根目录及其 manifest 路径。"""
        base_dir = ArtifactService.normalize_base_dir(artifact_dir)

        artifact_id  = f"{mission_id}_mission"
        artifact_dir = base_dir / "nexus" / "artifacts" / mission_id

        steps_dir = artifact_dir / "steps"
        steps_dir.mkdir(parents=True, exist_ok=True)

        return ArtifactRecord(
            artifact_id=artifact_id,
            kind="mission",
            path=str(artifact_dir),
            manifest_path=str(artifact_dir / "mission.json"),
            mission_id=mission_id,
            protocol=protocol,
            created_ms=ClockService.ms_now()
        )

    @staticmethod
    def create_step_artifact(
        *,
        mission_id: str,
        protocol: str,
        step_index: int,
        step_name: str,
        artifact_dir: str,
        mission_artifact: typing.Optional[ArtifactRecord] = None
    ) -> ArtifactRecord:
        """创建 step 级 artifact 目录；如果已有 mission artifact 则挂在其下。"""
        step_slug   = ArtifactService._slug(step_name, f"step_{step_index + 1:03d}")
        artifact_id = f"{mission_id}_step_{step_index + 1:03d}"

        if mission_artifact is not None:
            root_dir = Path(mission_artifact.path)
        else:
            root_dir = ArtifactService.normalize_base_dir(artifact_dir) / "nexus" / "artifacts" / mission_id

        artifact_dir = root_dir / "steps" / f"{step_index + 1:03d}_{step_slug}"

        media_dir    = artifact_dir / "media"
        media_dir.mkdir(parents=True, exist_ok=True)

        return ArtifactRecord(
            artifact_id=artifact_id,
            kind="step",
            path=str(artifact_dir),
            manifest_path=str(artifact_dir / "artifact.json"),
            mission_id=mission_id,
            protocol=protocol,
            created_ms=ClockService.ms_now(),
            step_index=step_index,
            step_name=step_name,
            media_dir=str(media_dir),
            parent_artifact_id=mission_artifact.artifact_id if mission_artifact else None
        )

    @staticmethod
    def to_dict(record: typing.Optional[ArtifactRecord]) -> typing.Optional[dict[str, typing.Any]]:
        """把 artifact record 序列化为稳定的字典结构。"""
        if record is None:
            return None
        return asdict(record)

    @staticmethod
    def write_json(path: str, payload: dict[str, typing.Any]) -> None:
        """以可读格式持久化 JSON 内容。"""
        out_file = Path(path)
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False),
            encoding=const.CHARSET
        )

    @staticmethod
    def write_step_manifest(
        *,
        artifact: ArtifactRecord,
        pack: dict[str, typing.Any],
        step_name: str,
        step_ok: bool,
        elapsed_ms: int
    ) -> None:
        """把最终 step pack 写入 step artifact manifest。"""
        data = dict(pack.get("data") or {})

        payload = {
            "ok": step_ok,
            "artifact": ArtifactService.to_dict(artifact),
            "step": {
                "name"       : step_name,
                "type"       : artifact.protocol,
                "ok"         : step_ok,
                "elapsed_ms" : elapsed_ms
            },
            "text"           : pack.get("text"),
            "logs"           : list(pack.get("logs") or []),
            "data"           : data,
            "request"        : data.get("request") or {},
            "response"       : data.get("response") or {},
            "extract"        : data.get("extract"),
            "asserts"        : data.get("asserts"),
            "assert_summary" : data.get("assert_summary"),
            "assert_ok"      : data.get("assert_ok"),
            "error"          : data.get("error"),
            "attachments"    : list(pack.get("attachments") or [])
        }
        ArtifactService.write_json(artifact.manifest_path, payload)

    @staticmethod
    def write_mission_manifest(
        *,
        artifact: ArtifactRecord,
        mission_ok: bool,
        started_ms: int,
        finished_ms: int,
        request_payload: dict[str, typing.Any],
        final_ctx: dict[str, typing.Any],
        steps: list[dict[str, typing.Any]]
    ) -> None:
        """写入 mission 级 artifact 索引，用于串联所有 step artifact。"""
        payload = {
            "artifact": ArtifactService.to_dict(artifact),
            "mission": {
                "mission_id"  : artifact.mission_id,
                "kind"        : artifact.protocol,
                "ok"          : mission_ok,
                "started_ms"  : started_ms,
                "finished_ms" : finished_ms,
                "cost_ms"     : finished_ms - started_ms
            },
            "request"   : request_payload,
            "final_ctx" : final_ctx,
            "steps"     : steps
        }
        ArtifactService.write_json(artifact.manifest_path, payload)


if __name__ == '__main__':
    pass
