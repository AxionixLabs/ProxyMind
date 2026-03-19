#  __  __ _         _
# |  \/  (_)___ ___(_) ___  _ __
# | |\/| | / __/ __| |/ _ \| '_ \
# | |  | | \__ \__ \ | (_) | | | |
# |_|  |_|_|___/___/_|\___/|_| |_|
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import time
import typing
import asyncio
from .domain.context import ContextMergeService
from backend.models.model_nexus import (
    ArtifactRecord,
    NexusBatchItem,
    NexusBatchRequest,
    NexusKind,
    NexusRequest,
    RunRecord,
    StepResult
)
from .domain.template import TemplateService
from .registry import NexusExecutorRegistry
from .infra.artifact import ArtifactService
from .infra.core import ClockService
from .infra.serialize import StepSerializer
from .repository import MemoryRunRepository


class NexusMissionService(object):
    """负责编排 nexus 单请求与批量请求的应用服务。"""

    def __init__(
        self,
        executor_registry: NexusExecutorRegistry,
        run_repository: MemoryRunRepository
    ) -> None:
        """注入协议执行分发器与运行记录仓储。"""
        self.executor_registry = executor_registry
        self.run_repository = run_repository

    async def execute_request(
        self,
        *,
        kind: NexusKind,
        request: NexusRequest
    ) -> dict[str, typing.Any]:
        """把单请求包装成单项 batch，并复用统一批处理流程。"""
        batch = NexusBatchRequest(
            items=[
                NexusBatchItem(
                    name=request.name,
                    request=dict(request.request or {}),
                    extract=request.extract,
                    asserts=request.asserts
                )
            ],
            template_vars=dict(request.template_vars or {}),
            concurrency=1,
            fail_fast=True
        )
        return await self.execute_batch(kind=kind, batch=batch)

    async def execute_batch(
        self,
        *,
        kind: NexusKind,
        batch: NexusBatchRequest
    ) -> dict[str, typing.Any]:
        """执行批量请求，负责模板渲染、并发控制、结果汇总与运行记录落库。"""
        if not batch.items:
            return {
                "text"        : f"kind={kind} invalid batch: items is empty",
                "attachments" : [],
                "data": {
                    "ok"   : False,
                    "kind" : kind,
                    "summary": {
                        "total"   : 0,
                        "pass"    : 0,
                        "fail"    : 0,
                        "cost_ms" : 0
                    },
                    "steps"     : [],
                    "final_ctx" : dict(batch.template_vars or {}),
                    "request": {
                        "env"           : dict(batch.env or {}),
                        "template_vars" : dict(batch.template_vars or {}),
                        "fail_fast"     : bool(batch.fail_fast),
                        "concurrency"   : max(1, int(batch.concurrency or 1)),
                        "items"         : []
                    },
                    "error"    : "items is empty",
                    "evidence" : {"steps": []}
                },
                "logs": []
            }

        started_ms = ClockService.ms_now()
        mission_id = f"nexus_{started_ms}"

        ctx   = dict(batch.template_vars or {})
        env_r = TemplateService.render(dict(batch.env or {}), ctx) if batch.env else {}

        concurrency     = max(1, int(batch.concurrency or 1))
        fail_fast       = bool(batch.fail_fast)
        allow_ctx_merge = concurrency == 1

        mission_artifact = self._prepare_mission_artifact(
            mission_id=mission_id,
            kind=kind,
            batch=batch,
            ctx=ctx
        )

        sem = asyncio.Semaphore(concurrency)

        async def mission_once(i: int, item: NexusBatchItem) -> tuple[int, StepResult]:
            async with sem:
                name = str(item.name or f"{kind}_{i + 1:03d}")
                req_r = TemplateService.render(dict(item.request or {}), ctx)
                extract_r = TemplateService.render(item.extract, ctx) if item.extract else None
                asserts_r = TemplateService.render(item.asserts, ctx) if item.asserts else None
                t0 = time.perf_counter()
                step_artifact = self._prepare_step_artifact(
                    mission_id=mission_id,
                    kind=kind,
                    index=i,
                    name=name,
                    request=req_r,
                    mission_artifact=mission_artifact
                )

                pack = await self.executor_registry.execute(
                    kind=kind,
                    request=req_r,
                    env=dict(env_r or {}),
                    extract=extract_r,
                    asserts=asserts_r,
                    step_artifact_dir=step_artifact.path if step_artifact else None,
                )
                return i, self._build_step_result(
                    name=name,
                    kind=kind,
                    pack=pack,
                    ctx=ctx,
                    allow_ctx_merge=allow_ctx_merge,
                    started_at=t0,
                    artifact=step_artifact
                )

        tasks = [asyncio.create_task(mission_once(i, item)) for i, item in enumerate(batch.items)]

        if fail_fast:
            pending = set(tasks)
            done_ordered: list[tuple[int, StepResult]] = []
            while pending:
                done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    idx, step_result = await task
                    done_ordered.append((idx, step_result))
                    if not step_result.ok:
                        for future in pending:
                            future.cancel()
                        pending = set()
                        break
            done_ordered.sort(key=lambda x: x[0])
            step_results = [step_result for _, step_result in done_ordered]
        else:
            results = await asyncio.gather(*tasks, return_exceptions=False)
            results.sort(key=lambda x: x[0])
            step_results = [step_result for _, step_result in results]

        ok_run = all(step.ok for step in step_results) if step_results else False
        finished_ms = ClockService.ms_now()

        record_payload = {
            "kind" : kind,
            "env"  : dict(batch.env or {}),
            "vars" : dict(batch.template_vars or {}),
            "options": {
                "fail_fast"   : fail_fast,
                "concurrency" : concurrency
            },
            "items": [
                {
                    "name"    : item.name,
                    "request" : dict(item.request or {}),
                    "extract" : item.extract,
                    "asserts" : item.asserts
                }
                for item in batch.items
            ]
        }

        self.run_repository.save(
            RunRecord(
                mission_id=mission_id,
                ok=ok_run,
                started_ms=started_ms,
                finished_ms=finished_ms,
                payload=record_payload,
                final_ctx=ctx,
                steps=step_results,
                artifact=ArtifactService.to_dict(mission_artifact)
            )
        )

        attachments: list[dict[str, typing.Any]] = []
        for step in step_results:
            attachments.extend((step.detail or {}).get("attachments") or [])

        mission_steps = [StepSerializer.to_dict(step) for step in step_results]
        if mission_artifact is not None:
            ArtifactService.write_mission_manifest(
                artifact=mission_artifact,
                mission_ok=ok_run,
                started_ms=started_ms,
                finished_ms=finished_ms,
                request_payload={
                    "env"           : dict(batch.env or {}),
                    "template_vars" : dict(batch.template_vars or {}),
                    "fail_fast"     : fail_fast,
                    "concurrency"   : concurrency,
                    "items": [
                        {
                            "name"    : item.name,
                            "request" : dict(item.request or {}),
                            "extract" : item.extract,
                            "asserts" : item.asserts
                        }
                        for item in batch.items
                    ]
                },
                final_ctx=ctx,
                steps=mission_steps
            )

        return {
            "text"        : f"kind={kind} total={len(step_results)} mission_id={mission_id}",
            "attachments" : attachments,
            "data": {
                "ok"         : ok_run,
                "mission_id" : mission_id,
                "kind"       : kind,
                "artifact"   : ArtifactService.to_dict(mission_artifact),
                "summary": {
                    "total"   : len(step_results),
                    "pass"    : sum(1 for step in step_results if step.ok),
                    "fail"    : sum(1 for step in step_results if not step.ok),
                    "cost_ms" : finished_ms - started_ms
                },
                "steps"     : mission_steps,
                "final_ctx" : ctx,
                "request": {
                    "env"           : dict(batch.env or {}),
                    "template_vars" : dict(batch.template_vars or {}),
                    "fail_fast"     : fail_fast,
                    "concurrency"   : concurrency,
                    "items": [
                        {
                            "name"    : item.name,
                            "request" : dict(item.request or {}),
                            "extract" : item.extract,
                            "asserts" : item.asserts
                        }
                        for item in batch.items
                    ]
                },
                "evidence": {
                    "artifact" : ArtifactService.to_dict(mission_artifact),
                    "steps"    : mission_steps
                }
            },
            "logs": []
        }

    @staticmethod
    def _prepare_mission_artifact(
        *,
        mission_id: str,
        kind: str,
        batch: NexusBatchRequest,
        ctx: dict[str, typing.Any]
    ) -> typing.Optional[ArtifactRecord]:
        """当启用落盘的 step 最终收敛到同一个 artifact_dir 时，创建共享 mission artifact。"""
        artifact_dirs: set[str] = set()

        for item in batch.items:
            request_r = TemplateService.render(dict(item.request or {}), ctx)
            if not ArtifactService.artifact_enabled(request_r):
                continue
            artifact_dir = str(request_r.get("artifact_dir") or "").strip()
            if not artifact_dir:
                continue
            artifact_dirs.add(artifact_dir)

        if len(artifact_dirs) != 1:
            return None

        return ArtifactService.create_mission_artifact(
            artifact_dir=artifact_dirs.pop(),
            mission_id=mission_id,
            protocol=kind
        )

    @staticmethod
    def _prepare_step_artifact(
        *,
        mission_id: str,
        kind: str,
        index: int,
        name: str,
        request: dict[str, typing.Any],
        mission_artifact: typing.Optional[ArtifactRecord],
    ) -> typing.Optional[ArtifactRecord]:
        """当渲染后的请求开启落盘时，为该 step 创建 artifact。"""
        if not ArtifactService.artifact_enabled(request):
            return None

        artifact_dir = str(request.get("artifact_dir") or "").strip()
        if not artifact_dir:
            return None

        return ArtifactService.create_step_artifact(
            mission_id=mission_id,
            protocol=kind,
            step_index=index,
            step_name=name,
            artifact_dir=artifact_dir,
            mission_artifact=mission_artifact
        )

    @staticmethod
    def _build_step_result(
        *,
        name: str,
        kind: str,
        pack: dict[str, typing.Any],
        ctx: dict[str, typing.Any],
        allow_ctx_merge: bool,
        started_at: float,
        artifact: typing.Optional[ArtifactRecord]
    ) -> StepResult:
        """把单步协议执行结果转换成统一的 StepResult 结构。"""
        data = pack.get("data") or {}

        ContextMergeService.merge_step_extract(data, ctx, allow_ctx_merge)
        elapsed_ms = ClockService.ms_since(started_at)
        if artifact is not None:
            ArtifactService.write_step_manifest(
                artifact=artifact,
                pack=pack,
                step_name=name,
                step_ok=bool(data.get("ok")),
                elapsed_ms=elapsed_ms
            )

        return StepResult(
            name=name,
            type=kind,
            ok=bool(data.get("ok")),
            elapsed_ms=elapsed_ms,
            detail={
                "request"        : data.get("request") or {},
                "response"       : data.get("response") or {},
                "extract"        : data.get("extract"),
                "asserts"        : data.get("asserts"),
                "assert_summary" : data.get("assert_summary"),
                "assert_ok"      : data.get("assert_ok"),
                "attachments"    : pack.get("attachments"),
                "artifact"       : ArtifactService.to_dict(artifact)
            },
            artifact=ArtifactService.to_dict(artifact)
        )

if __name__ == '__main__':
    pass
