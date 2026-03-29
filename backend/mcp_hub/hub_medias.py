# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import os
import re
import uuid
import typing
import asyncio
import contextlib
from pathlib import Path
from collections import deque
from loguru import logger
from backend.models.model_base import Attachment
from backend.utilities import (
    marked, toolbox
)
from backend.utilities.flux import Flux

try:
    os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "hide"
    import pygame
except ImportError:
    raise ImportError("AudioPlayer requires pygame. install it first.")


class FFmpeg(object):

    def __init__(self):
        self.__prefix: str = "ffmpeg"
        self.agent_id: str = self.__prefix

        self.out_fail: typing.Optional[asyncio.Event] = None
        self.out_ring: typing.Optional[deque[str]] = None

    @property
    def prefix(self) -> str:
        return self.__prefix

    @staticmethod
    def attach_of(path: str) -> dict:
        p = Path(path)
        suf = p.suffix.lower()
        if suf in {".png", ".jpg", ".jpeg", ".webp"}:
            return Attachment(
                kind="image",
                local=str(p),
                filename=p.name,
                mime_type=f"image/{suf.lstrip('.').replace('jpg', 'jpeg')}"
            ).to_dict()
        if suf in {".mp4", ".mkv", ".mov", ".webm"}:
            return Attachment(
                kind="file",
                local=str(p),
                filename=p.name,
                mime_type="video/" + suf.lstrip(".")
            ).to_dict()
        if suf in {".mp3", ".wav", ".aac", ".m4a", ".ogg", ".flac"}:
            return Attachment(
                kind="file",
                local=str(p),
                filename=p.name,
                mime_type="audio/" + suf.lstrip(".")
            ).to_dict()

        return Attachment(
            kind="file",
            local=str(p),
            filename=p.name,
            mime_type="application/octet-stream"
        ).to_dict()

    @staticmethod
    def mk_out_file(out_dir: Path, input_path: str, op: str, suffix: str) -> str:
        """
        生成唯一输出文件路径（带后缀）。
        """
        def _stem(path: str) -> str:
            """从输入路径生成安全 stem（去掉奇怪字符）。"""
            stem = Path(path).stem or "input"
            stem = re.sub(r"[^a-zA-Z0-9_\-.]+", "_", stem)
            return stem[:80]  # 防止过长

        def _suffix(s: str) -> str:
            """标准化后缀，保证以 . 开头。"""
            if not s: return ""
            return s if s.startswith(".") else "." + s

        suf = _suffix(suffix)
        name = f"{_stem(input_path)}_{op}_{uuid.uuid4().hex[:6]}{suf}"
        return str((out_dir / name).resolve())

    async def switch(self, cmd: list[str]) -> str:
        self.out_fail = asyncio.Event()
        self.out_ring = deque(maxlen=10)

        cmd = [self.prefix] + cmd
        logger.info(f"[FFMPEG] {' '.join(cmd)}")

        switch_resp = await Flux.cmd_line(cmd)
        logger.info(f"[FFMPEG] \n{switch_resp}")

        frame_re = re.compile(r"frame.*fps.*speed.*", re.IGNORECASE)
        error_re = re.compile(r"error", re.IGNORECASE)
        sight_re = re.compile(r"(\w+)=\s*([\w.\-:/x]+)", re.IGNORECASE)

        tail_n = 20  # 兜底返回/上报最后 N 行

        # 统一清洗行（去空行）
        line = [ln for ln in (switch_resp or "").splitlines() if ln.strip()]
        tail = "\n".join(line[-tail_n:]) if line else ""

        last_frame_msg: str | None = None

        # 顺序扫描：out_ring 永远保留最后 10 行（旧 -> 新）
        for message in line:
            self.out_ring.append(message)

            # 命中 error：立即上报 out_ring + tail
            if error_re.search(string=message):
                logger.error(f"[FFMPEG] \n{self.out_ring}")

                raise marked.subproc_fail(
                    source=f"{self.prefix}.stream",
                    out_ring=self.out_ring,  # 统一上下文（<=10 行，含命中行）
                    cmd=" ".join(cmd)        # 便于排查
                )

            # 命中进度行：更新“最后一次看到的 frame 状态”
            if matched := frame_re.search(string=message):
                pairs = sight_re.findall(matched.group())
                last_frame_msg = " ".join([f"{k}={v}" for k, v in pairs])

        # 循环结束：优先返回最后一次 frame 状态（通常就是最终进度）
        if last_frame_msg:
            logger.info(f"[FFMPEG] {last_frame_msg}")
            return last_frame_msg

        # ===== 兜底：既没解析到 frame，也没命中 error =====
        fallback = tail or (switch_resp or "").strip() or "ffmpeg finished, but no parsable output"
        logger.info(f"[FFMPEG] \n{fallback}")
        return fallback

    # workflow: ==== MCP Tool ====
    async def ffmpeg_extract_snapshot(
        self,
        input_video: str,
        output_dir: typing.Optional[str] = None,
        *,
        at_sec: float = 0.0,
        image_format: typing.Literal["jpg", "png", "webp"] = "png",
        overwrite: bool = True
    ) -> dict[str, typing.Any]:
        """
        视频截图：指定时间点截取一帧输出为图片。

        用途：
            - 生成封面图/缩略图
            - 快速验证视频内容

        Args:
            input_video   : 输入视频路径（必须存在且为文件）
            output_dir    : 输出根目录（可选；为空时建议由上游注入 report.toolkit_path）
            at_sec        : 截图时间点（秒）
            image_format  : 输出图片格式（jpg/png/webp）
            overwrite     : 是否覆盖输出（True 会传 -y）

        Returns:
            dict: {text, attachments, data, logs}
        """
        marked.ensure_f(input_video, "input_video")

        out_dir = toolbox.mk_out_dir(output_dir, engine="ffmpeg", tool="ffmpeg_extract_snapshot")

        suf = "." + str(image_format).lower()
        out_p = self.mk_out_file(out_dir, input_video, op="snap", suffix=suf)

        cmd: list[str] = ["-y"] if overwrite else []
        cmd += ["-ss", str(float(at_sec)), "-i", input_video]
        cmd += ["-frames:v", "1", out_p]

        resp = await self.switch(cmd)

        ok = Path(out_p).exists()
        attachments = [self.attach_of(out_p)] if ok else []

        return {
            "text"        : f"ffmpeg_extract_snapshot {'ok' if ok else 'ERROR'} output={out_p}",
            "attachments" : attachments,
            "data": {
                "ok"           : ok,
                "output_dir"   : str(out_dir),
                "output_file"  : out_p,
                "at_sec"       : float(at_sec),
                "image_format" : str(image_format)
            },
            "logs": [resp]
        }

    # workflow: ==== MCP Tool ====
    async def ffmpeg_extract_frames(
        self,
        input_video: str,
        output_dir: typing.Optional[str] = None,
        *,
        pattern: str = "frame_%06d.png",
        fps: typing.Optional[float] = None,
        image_format: typing.Literal["jpg", "png", "webp"] = "png",
        start_sec: typing.Optional[float] = None,
        duration_sec: typing.Optional[float] = None,
        scale_w: typing.Optional[int] = None,
        scale_h: typing.Optional[int] = None,
        overwrite: bool = True
    ) -> dict[str, typing.Any]:
        """
        拆帧（视频 -> 图片序列）。

        Args:
            input_video  : 输入视频路径（必须存在且为文件）
            output_dir   : 输出根目录（可选；为空时建议由上游注入 report.toolkit_path）
            pattern      : 输出文件名模板（需包含 %d，例如 frame_%06d.png；扩展名会强制对齐 image_format）
            fps          : 抽帧帧率（None=不强制；例如 5 表示每秒 5 帧）
            image_format : 输出图片格式（jpg/png/webp）
            start_sec    : 从视频第几秒开始（可选；传入会使用 -ss）
            duration_sec : 抽帧的时间范围长度（可选；传入会使用 -t，与 start_sec 组合形成区间）
            scale_w      : 输出图片缩放宽（可选；None=不指定；与 scale_h 搭配，None 会用 -1 等比）
            scale_h      : 输出图片缩放高（可选；None=不指定；与 scale_w 搭配，None 会用 -1 等比）
            overwrite    : 是否覆盖输出（True 会传 -y）

        Returns:
            dict: {text, attachments, data, logs}
        """
        marked.ensure_f(input_video, "input_video")

        out_dir = toolbox.mk_out_dir(output_dir, engine="ffmpeg", tool="ffmpeg_extract_frames")

        # 统一输出扩展名（防止 pattern 没写对）
        pat = (pattern or "frame_%06d").strip()
        if not pat.lower().endswith(f".{image_format}"):
            pat = os.path.splitext(pat)[0] + f".{image_format}"

        vf: list[str] = []
        if fps is not None:
            vf.append(f"fps={float(fps)}")
        if scale_w is not None or scale_h is not None:
            w = int(scale_w) if scale_w is not None else -1
            h = int(scale_h) if scale_h is not None else -1
            vf.append(f"scale={w}:{h}")

        cmd: list[str] = ["-y"] if overwrite else []
        if start_sec is not None:
            cmd += ["-ss", str(float(start_sec))]
        cmd += ["-i", input_video]
        if duration_sec is not None:
            cmd += ["-t", str(float(duration_sec))]
        if vf:
            cmd += ["-vf", ",".join(vf)]

        # 导出图片：用 -vsync 0 避免复制
        out_tpl = str((out_dir / pat).resolve())
        cmd += ["-vsync", "0", out_tpl]

        resp = await self.switch(cmd)

        # 收集输出帧（限制一下数量，避免返回过大）
        files = sorted(out_dir.glob(f"*.{image_format}"), key=lambda p: p.name)
        max_return = 1
        files = files[:max_return]

        attachments = [self.attach_of(str(p)) for p in files]
        ok = len(attachments) > 0

        return {
            "text": (
                f"ffmpeg_extract_frames {'ok' if ok else 'ERROR'} "
                f"frames={len(attachments)} out_dir={out_dir}"
            ),
            "attachments": attachments,
            "data": {
                "ok"           : ok,
                "output_dir"   : str(out_dir),
                "pattern"      : pat,
                "image_format" : str(image_format),
                "fps"          : (None if fps is None else float(fps)),
                "start_sec"    : (None if start_sec is None else float(start_sec)),
                "duration_sec" : (None if duration_sec is None else float(duration_sec)),
                "scale_w"      : (None if scale_w is None else int(scale_w)),
                "scale_h"      : (None if scale_h is None else int(scale_h)),
                "count"        : len(attachments),
                "files"        : [str(p) for p in files],
                "output_tpl"   : out_tpl
            },
            "logs": [resp]
        }

    # workflow: ==== MCP Tool ====
    async def ffmpeg_extract_keyframes(
        self,
        input_video: str,
        output_dir: typing.Optional[str] = None,
        *,
        max_frames: int = 12,
        uniform_n: int = 6,
        image_format: typing.Literal["jpg", "png", "webp"] = "png",
        overwrite: bool = True
    ) -> dict[str, typing.Any]:
        """
        抽关键帧：均匀采样，合并去重后截断到 max_frames。

        Args:
            input_video  : 输入视频路径（必须存在且为文件）
            output_dir   : 输出根目录（可选；为空时建议由上游注入 report.toolkit_path）
            max_frames   : 最多返回多少张关键帧（最终 attachments 上限；>=1）
            uniform_n    : 均匀采样张数（按时长均分取点截图；0=关闭）
            image_format : 输出图片格式（jpg/png/webp）
            overwrite    : 是否覆盖输出（True 会传 -y）

        Returns:
            dict: {text, attachments, data, logs}
        """
        marked.ensure_f(input_video, "input_video")

        max_frames = max(1, int(max_frames))
        uniform_n  = max(0, int(uniform_n))

        out_dir = toolbox.mk_out_dir(output_dir, engine="ffmpeg", tool="ffmpeg_extract_keyframes")

        logs: list[str] = []
        picked: list[Path] = []

        # 解析时长
        out = await self.ffmpeg_probe_video(input_video)
        duration = out.get("data", {}).get("duration_sec") or 0.0

        # --- A) 均匀采样：直接在这里截图到固定文件名（uni_001...）---
        if uniform_n > 0 and float(duration) > 0.05:
            for i in range(uniform_n):
                at = (float(duration) * (i + 1) / (uniform_n + 1))
                out_img = out_dir / f"uni_{i + 1:03d}.{image_format}"

                try:
                    cmd: list[str] = ["-y"] if overwrite else []
                    cmd += ["-ss", str(float(at)), "-i", input_video]
                    cmd += ["-frames:v", "1", str(out_img)]

                    await self.switch(cmd)

                    if out_img.exists():
                        picked.append(out_img)
                    else:
                        logs.append(f"uniform[{i}] output_not_found: {out_img}")

                except Exception as e:
                    logs.append(f"uniform[{i}] {type(e).__name__}: {e}")

        # --- 合并去重（保序）---
        uniq: list[Path] = []
        seen: set[str] = set()
        for p in picked:
            sp = str(p)
            if sp in seen:
                continue
            if p.exists():
                uniq.append(p)
                seen.add(sp)

        uniq = uniq[:max_frames]

        attachments = [self.attach_of(str(p)) for p in uniq]  # 自动判定 kind=image
        ok = len(attachments) > 0

        return {
            "text": (
                f"ffmpeg_extract_keyframes {'ok' if ok else 'ERROR'} "
                f"frames={len(attachments)} out_dir={out_dir}"
            ),
            "attachments" : attachments,
            "data": {
                "ok"           : ok,
                "output_dir"   : str(out_dir),
                "count"        : len(attachments),
                "duration_sec" : float(duration),
                "uniform_n"    : uniform_n,
                "max_frames"   : max_frames,
                "image_format" : str(image_format),
                "files"        : [a.get("local") for a in attachments]
            },
            "logs": []
        }

    # workflow: ==== MCP Tool ====
    async def ffmpeg_extract_scene(
        self,
        input_video: str,
        output_dir: typing.Optional[str] = None,
        *,
        scene_th: float = 0.35,
        max_frames: int = 12,
        pattern: str = "scene_%06d",
        image_format: typing.Literal["jpg", "png", "webp"] = "png",
        start_sec: typing.Optional[float] = None,
        duration_sec: typing.Optional[float] = None,
        scale_w: typing.Optional[int] = 256,
        scale_h: typing.Optional[int] = None,
        overwrite: bool = True
    ) -> dict[str, typing.Any]:
        """
        场景变化抽帧（按画面变化挑关键帧输出为图片序列）。

        Args:
            input_video   : 输入视频路径（必须存在且为文件）
            output_dir    : 输出根目录（可选；为空时建议由上游注入 report.toolkit_path）
            scene_th      : 场景变化阈值（select='gt(scene,th)'；越大越“挑剔”，输出帧越少）
            max_frames    : 返回/挂载到 attachments 的最大帧数（仅截断返回，不影响实际写盘帧数）
            pattern       : 输出文件名模板（不含扩展名；例如 "scene_%06d"）
            image_format  : 输出图片格式（jpg/png/webp）
            start_sec     : 从视频第几秒开始抽帧（可选；-ss）
            duration_sec  : 抽帧的时间范围长度（可选；-t）
            scale_w       : 输出图片缩放宽（None=不缩放；默认 256）
            scale_h       : 输出图片缩放高（None=等比；与 scale_w 搭配，None 会用 -1）
            overwrite     : 是否覆盖输出（True 会传 -y）

        Returns:
            dict: {text, attachments, data, logs}
        """
        marked.ensure_f(input_video, "input_video")

        # 归一化参数
        scene_th   = float(scene_th)
        max_frames = max(0, int(max_frames))
        fmt        = str(image_format).lower()

        out_dir = toolbox.mk_out_dir(output_dir, engine="ffmpeg", tool="ffmpeg_extract_scene")

        # 输出模板：<out_dir>/<pattern>.<fmt>
        out_tpl = str((out_dir / f"{pattern}.{fmt}").resolve())

        # vf: scene select + scale
        vf: list[str] = [f"select='gt(scene,{scene_th})'"]
        if scale_w is not None or scale_h is not None:
            w = int(scale_w) if scale_w is not None else -1
            h = int(scale_h) if scale_h is not None else -1
            vf.append(f"scale={w}:{h}")

        cmd: list[str] = ["-y"] if overwrite else []
        if start_sec is not None:
            cmd += ["-ss", str(float(start_sec))]
        cmd += ["-i", input_video]
        if duration_sec is not None:
            cmd += ["-t", str(float(duration_sec))]

        cmd += ["-vf", ",".join(vf)]
        cmd += ["-vsync", "vfr"]    # 避免恒定帧率导致复制帧
        cmd += ["-frame_pts", "1"]  # 让输出文件名与时间更相关
        cmd += [out_tpl]

        resp = await self.switch(cmd)

        # 收集输出文件（按名字排序），并截断 max_frames
        files = sorted(out_dir.glob(f"*.{fmt}"), key=lambda p: p.name)
        if max_frames > 0:
            files = files[:max_frames]
        else:
            files = []

        attachments = [self.attach_of(str(p)) for p in files]
        ok = len(attachments) > 0

        return {
            "text": (
                f"ffmpeg_extract_scene {'ok' if ok else 'ERROR'} "
                f"frames={len(attachments)} out_dir={out_dir}"
            ),
            "attachments": attachments,
            "data": {
                "ok"           : ok,
                "output_dir"   : str(out_dir),
                "count"        : len(attachments),
                "scene_th"     : scene_th,
                "max_frames"   : max_frames,
                "pattern"      : pattern,
                "image_format" : fmt,
                "start_sec"    : (None if start_sec is None else float(start_sec)),
                "duration_sec" : (None if duration_sec is None else float(duration_sec)),
                "scale_w"      : (None if scale_w is None else int(scale_w)),
                "scale_h"      : (None if scale_h is None else int(scale_h)),
                "files"        : [a.get("local") for a in attachments]
            },
            "logs": [resp]
        }

    # workflow: ==== MCP Tool ====
    async def ffmpeg_trim_video(
        self,
        input_video: str,
        output_dir: typing.Optional[str] = None,
        *,
        start_sec: float = 0.0,
        end_sec: typing.Optional[float] = None,
        duration_sec: typing.Optional[float] = None,
        mode: typing.Literal["copy", "reencode"] = "copy",
        video_codec: str = "libx264",
        crf: int = 23,
        preset: str = "veryfast",
        output_format: typing.Optional[typing.Literal["mp4", "mkv", "mov", "webm"]] = None,
        overwrite: bool = True
    ) -> dict[str, typing.Any]:
        """
        视频截取（按时间范围），输出到 output_dir 下的独立目录（避免并发覆盖）。

        选择：
          - mode="copy"：不重编码，快，但只在关键帧附近精确（常用于快速裁剪）
          - mode="reencode"：重编码，精确但更慢

        Args:
            input_video    : 输入视频路径（必须存在且为文件）
            output_dir     : 输出根目录（可选；为空时建议由上游注入 report.toolkit_path）
            start_sec      : 起始秒
            end_sec        : 结束秒（可选；与 duration_sec 二选一）
            duration_sec   : 截取时长（可选；与 end_sec 二选一）
            mode           : copy / reencode
            video_codec    : mode="reencode" 时生效
            crf            : mode="reencode" 时生效
            preset         : mode="reencode" 时生效
            output_format  : 输出容器（mp4/mkv/mov/webm；None=沿用 input_video 后缀）
            overwrite      : 是否覆盖输出（True 会传 -y）

        Returns:
            dict: {text, attachments, data, logs}
        """
        marked.ensure_f(input_video, "input_video")

        out_dir = toolbox.mk_out_dir(output_dir, engine="ffmpeg", tool="ffmpeg_trim_video")

        # 决定输出后缀：优先 output_format，否则沿用输入容器
        in_suf = Path(input_video).suffix.lower()
        if output_format:
            suf = "." + str(output_format).lower().lstrip(".")
        else:
            suf = in_suf if in_suf else ".mp4"  # 极端兜底：正常情况下 ensure_f 已保证有后缀

        out_p = self.mk_out_file(out_dir, input_video, op="trim", suffix=suf)

        cmd: list[str] = ["-y"] if overwrite else []
        cmd += ["-ss", str(float(start_sec)), "-i", input_video]

        used_dur: float | None = None
        if duration_sec is not None:
            used_dur = max(0.0, float(duration_sec))
            cmd += ["-t", str(used_dur)]
        elif end_sec is not None:
            used_dur = max(0.0, float(end_sec) - float(start_sec))
            cmd += ["-t", str(used_dur)]

        if mode == "copy":
            cmd += ["-c", "copy"]
        else:
            cmd += [
                "-c:v", str(video_codec),
                "-preset", str(preset),
                "-crf", str(int(crf)),
                "-c:a", "copy"
            ]

        cmd += [out_p]

        resp = await self.switch(cmd)

        ok = Path(out_p).exists()
        attachments = [self.attach_of(out_p)] if ok else []

        return {
            "text"        : f"ffmpeg_trim_video {'ok' if ok else 'ERROR'} output={out_p}",
            "attachments" : attachments,
            "data": {
                "ok"            : ok,
                "output_dir"    : str(out_dir),
                "output_file"   : out_p,
                "input_video"   : input_video,
                "start_sec"     : float(start_sec),
                "end_sec"       : (None if end_sec is None else float(end_sec)),
                "duration_sec"  : used_dur,
                "mode"          : str(mode),
                "output_suffix" : suf,
                "video_codec"   : (None if mode == "copy" else str(video_codec)),
                "crf"           : (None if mode == "copy" else int(crf)),
                "preset"        : (None if mode == "copy" else str(preset))
            },
            "logs": [resp]
        }

    # workflow: ==== MCP Tool ====
    async def ffmpeg_scale_video(
        self,
        input_video: str,
        output_dir: typing.Optional[str] = None,
        *,
        scale_w: int | None = None,
        scale_h: int | None = None,
        video_codec: str = "libx264",
        crf: int = 23,
        preset: str = "veryfast",
        keep_audio: bool = True,
        output_format: typing.Optional[typing.Literal["mp4", "mkv", "mov", "webm"]] = None,
        overwrite: bool = True,
    ) -> dict[str, typing.Any]:
        """
        视频缩放并输出新视频（重编码），输出到 output_dir 下的独立目录（避免并发覆盖）。

        Args:
            input_video    : 输入视频文件路径（必须存在且为文件）
            output_dir     : 输出根目录（可选；为空时建议由上游注入 report.toolkit_path）
            scale_w        : 目标宽（None=不指定；与 scale_h 搭配，None 会用 -1 等比）
            scale_h        : 目标高（None=不指定；与 scale_w 搭配，None 会用 -1 等比）
            video_codec    : 视频编码器（默认 libx264）
            crf            : 质量（越小越清晰/体积越大，常用 18~28）
            preset         : 编码速度（ultrafast/veryfast/medium/slow...）
            keep_audio     : True=保留音频（copy），False=去音轨
            output_format  : 输出容器（mp4/mkv/mov/webm；None=沿用 input_video 后缀）
            overwrite      : 是否覆盖输出（True 会传 -y）

        Returns:
            dict: {text, attachments, data, logs}
        """
        marked.ensure_f(input_video, "input_video")

        out_dir = toolbox.mk_out_dir(output_dir, engine="ffmpeg", tool="ffmpeg_scale_video")

        in_suf = Path(input_video).suffix.lower()
        if output_format:
            suf = "." + str(output_format).lower().lstrip(".")
        else:
            suf = in_suf if in_suf else ".mp4"  # 极端兜底：正常情况下 ensure_f 已保证有后缀

        out_p = self.mk_out_file(out_dir, input_video, op="scale", suffix=suf)

        vf: list[str] = []
        if scale_w is not None or scale_h is not None:
            w = int(scale_w) if scale_w is not None else -1
            h = int(scale_h) if scale_h is not None else -1
            vf.append(f"scale={w}:{h}")

        cmd: list[str] = ["-y"] if overwrite else []
        cmd += ["-i", input_video]
        if vf:
            cmd += ["-vf", ",".join(vf)]
        cmd += ["-c:v", str(video_codec), "-preset", str(preset), "-crf", str(int(crf))]

        if keep_audio:
            cmd += ["-c:a", "copy"]
        else:
            cmd += ["-an"]

        cmd += [out_p]

        resp = await self.switch(cmd)

        ok = Path(out_p).exists()
        attachments = [self.attach_of(out_p)] if ok else []

        return {
            "text"        : f"ffmpeg_scale_video {'ok' if ok else 'ERROR'} output={out_p}",
            "attachments" : attachments,
            "data": {
                "ok"            : ok,
                "output_dir"    : str(out_dir),
                "output_file"   : out_p,
                "input_video"   : input_video,
                "scale_w"       : (None if scale_w is None else int(scale_w)),
                "scale_h"       : (None if scale_h is None else int(scale_h)),
                "video_codec"   : str(video_codec),
                "crf"           : int(crf),
                "preset"        : str(preset),
                "keep_audio"    : bool(keep_audio),
                "output_suffix" : suf
            },
            "logs": [resp]
        }

    # workflow: ==== MCP Tool ====
    async def ffmpeg_convert_video(
        self,
        input_video: str,
        output_dir: typing.Optional[str] = None,
        *,
        fps: float = 60,
        video_codec: str = "libx264",
        crf: int = 23,
        preset: str = "veryfast",
        keep_audio: bool = True,
        output_format: typing.Optional[typing.Literal["mp4", "mkv", "mov", "webm"]] = None,
        overwrite: bool = True
    ) -> dict[str, typing.Any]:
        """
        转换视频帧率（重编码），输出到 output_dir 下的独立目录（避免并发覆盖）。

        Args:
            input_video    : 输入视频文件路径（必须存在且为文件）
            output_dir     : 输出根目录（可选；为空时建议由上游注入 report.toolkit_path）
            fps            : 目标帧率（例如 30 / 60）
            video_codec    : 视频编码器（默认 libx264）
            crf            : 质量（x264 常用 18~28，越小越清晰体积越大）
            preset         : 编码速度（ultrafast/veryfast/medium/slow...）
            keep_audio     : True=保留音频（copy），False=去音轨
            output_format  : 输出容器（mp4/mkv/mov/webm；None=沿用 input_video 后缀）
            overwrite      : 是否覆盖输出（True 会传 -y）

        Returns:
            dict: {text, attachments, data, logs}
        """
        marked.ensure_f(input_video, "input_video")

        out_dir = toolbox.mk_out_dir(output_dir, engine="ffmpeg", tool="ffmpeg_convert_video")

        # 决定输出后缀：优先 output_format，否则沿用输入容器
        in_suf = Path(input_video).suffix.lower()
        if output_format:
            suf = "." + str(output_format).lower().lstrip(".")
        else:
            suf = in_suf if in_suf else ".mp4"  # 兜底：正常情况下 ensure_f 已保证有后缀

        out_p = self.mk_out_file(out_dir, input_video, op="convert", suffix=suf)

        cmd: list[str] = ["-y"] if overwrite else []
        cmd += ["-i", input_video]
        cmd += ["-vf", f"fps={float(fps)}"]
        cmd += ["-c:v", str(video_codec), "-preset", str(preset), "-crf", str(int(crf))]

        if keep_audio:
            cmd += ["-c:a", "copy"]
        else:
            cmd += ["-an"]

        cmd += [out_p]

        resp = await self.switch(cmd)

        ok = Path(out_p).exists()
        attachments = [self.attach_of(out_p)] if ok else []

        return {
            "text"        : f"ffmpeg_convert_video {'ok' if ok else 'ERROR'} output={out_p}",
            "attachments" : attachments,
            "data": {
                "ok"            : ok,
                "output_dir"    : str(out_dir),
                "output_file"   : out_p,
                "input_video"   : input_video,
                "fps"           : float(fps),
                "video_codec"   : str(video_codec),
                "crf"           : int(crf),
                "preset"        : str(preset),
                "keep_audio"    : bool(keep_audio),
                "output_suffix" : suf
            },
            "logs": [resp]
        }

    # workflow: ==== MCP Tool ====
    async def ffmpeg_concat_video(
        self,
        list_file: str,
        output_dir: typing.Optional[str] = None,
        *,
        overwrite: bool = True,
        reencode: bool = False,
        video_codec: str = "libx264",
        crf: int = 23,
        preset: str = "veryfast",
        audio_codec: str = "aac",
        output_format: typing.Optional[typing.Literal["mp4", "mkv", "mov", "webm"]] = "mp4",
    ) -> dict[str, typing.Any]:
        """
        拼接视频（concat demuxer），输出到 output_dir 下的独立目录（避免并发覆盖）。

        list_file 文件格式（每行一段）：
            file '/abs/path/1.mp4'
            file '/abs/path/2.mp4'

        Args:
            list_file     : concat 列表文件路径（必须存在且为文件）
            output_dir    : 输出根目录（可选；为空时建议由上游注入 report.toolkit_path）
            overwrite     : 是否覆盖输出（True 会传 -y）
            reencode      :
                False=-c copy（要求输入片段编码/参数一致；最快）
                True=重编码后拼接（更稳但慢）
            video_codec   : reencode=True 时用于视频重编码
            crf           : reencode=True 时用于视频重编码
            preset        : reencode=True 时用于视频重编码
            audio_codec   : reencode=True 时用于音频重编码（默认 aac）
            output_format : 输出容器（mp4/mkv/mov/webm；默认 mp4）

        Returns:
            dict: {text, attachments, data, logs}
        """
        marked.ensure_f(list_file, "list_file")

        out_dir = toolbox.mk_out_dir(output_dir, engine="ffmpeg", tool="ffmpeg_concat_video")

        # 输出后缀（这里推荐固定 mp4；如你要“智能”，也可以从 list_file 内容推断第一个片段后缀）
        suf = "." + str(output_format or "mp4").lower().lstrip(".")
        out_p = self.mk_out_file(out_dir, list_file, op="concat", suffix=suf)

        cmd: list[str] = ["-y"] if overwrite else []
        cmd += ["-f", "concat", "-safe", "0", "-i", list_file]

        if not reencode:
            cmd += ["-c", "copy"]
        else:
            cmd += ["-c:v", str(video_codec), "-preset", str(preset), "-crf", str(int(crf))]
            cmd += ["-c:a", str(audio_codec)]

        cmd += [out_p]

        resp = await self.switch(cmd)

        ok = Path(out_p).exists()
        attachments = [self.attach_of(out_p)] if ok else []

        return {
            "text"        : f"ffmpeg_concat_video {'ok' if ok else 'ERROR'} output={out_p}",
            "attachments" : attachments,
            "data": {
                "ok"            : ok,
                "output_dir"    : str(out_dir),
                "output_file"   : out_p,
                "list_file"     : list_file,
                "overwrite"     : bool(overwrite),
                "reencode"      : bool(reencode),
                "video_codec"   : (None if not reencode else str(video_codec)),
                "crf"           : (None if not reencode else int(crf)),
                "preset"        : (None if not reencode else str(preset)),
                "audio_codec"   : (None if not reencode else str(audio_codec)),
                "output_suffix" : suf
            },
            "logs": [resp]
        }

    # workflow: ==== MCP Tool ====
    async def ffmpeg_remux_video(
        self,
        input_video: str,
        output_dir: typing.Optional[str] = None,
        *,
        output_format: typing.Optional[typing.Literal["mp4", "mkv", "mov", "webm"]] = None,
        overwrite: bool = True
    ) -> dict[str, typing.Any]:
        """
        仅“换容器/封装”不重编码（最快），输出到 output_dir 下的独立目录（避免并发覆盖）。

        用途：
            - mkv -> mp4 / mp4 -> mkv（不改画面质量、不改音轨编码）
            - 失败通常来自：输入不存在、容器/流不兼容、输出路径不可写

        Args:
            input_video    : 输入视频路径（必须存在且为文件）
            output_dir     : 输出根目录（可选；为空时建议由上游注入 report.toolkit_path）
            output_format  : 目标容器（mp4/mkv/mov/webm；None=按输入后缀自动挑一个“不同的”）
            overwrite      : 是否覆盖输出（True 会传 -y）

        Returns:
            dict: {text, attachments, data, logs}
        """
        marked.ensure_f(input_video, "input_video")

        in_suf = Path(input_video).suffix.lower()

        # 选择输出后缀：优先 output_format；否则“换一个容器”
        if output_format:
            out_suffix = "." + str(output_format).lower().lstrip(".")
        else:
            if in_suf in {".mp4", ".m4v", ".mov"}:
                out_suffix = ".mkv"
            elif in_suf == ".mkv":
                out_suffix = ".mp4"
            else:
                out_suffix = ".mp4"  # 兜底（也可改成 .mkv）

        out_dir = toolbox.mk_out_dir(output_dir, engine="ffmpeg", tool="ffmpeg_remux_video")
        out_p = self.mk_out_file(out_dir, input_video, op="remux", suffix=out_suffix)

        cmd: list[str] = ["-y"] if overwrite else []
        cmd += ["-i", input_video, "-c", "copy", out_p]

        resp = await self.switch(cmd)

        ok = Path(out_p).exists()
        attachments = [self.attach_of(out_p)] if ok else []

        return {
            "text"        : f"ffmpeg_remux_video {'ok' if ok else 'ERROR'} output={out_p}",
            "attachments" : attachments,
            "data": {
                "ok"            : ok,
                "output_dir"    : str(out_dir),
                "output_file"   : out_p,
                "input_video"   : input_video,
                "input_suffix"  : in_suf,
                "output_suffix" : out_suffix,
                "output_format" : (None if output_format is None else str(output_format)),
                "overwrite"     : bool(overwrite)
            },
            "logs": [resp]
        }

    # workflow: ==== MCP Tool ====
    async def ffmpeg_mute_video(
        self,
        input_video: str,
        output_dir: typing.Optional[str] = None,
        *,
        output_format: typing.Optional[typing.Literal["mp4", "mkv", "mov", "webm"]] = None,
        overwrite: bool = True
    ) -> dict[str, typing.Any]:
        """
        去音轨（输出静音视频；视频流不重编码，最快），输出到 output_dir 下的独立目录（避免并发覆盖）。

        Args:
            input_video    : 输入视频文件路径（必须存在且为文件）
            output_dir     : 输出根目录（可选；为空时建议由上游注入 report.toolkit_path）
            output_format  : 输出容器（mp4/mkv/mov/webm；None=沿用输入后缀；无后缀时兜底 mp4）
            overwrite      : 是否覆盖输出（True 会传 -y）

        Returns:
            dict: {text, attachments, data, logs}
        """
        marked.ensure_f(input_video, "input_video")

        in_suf = Path(input_video).suffix.lower()

        # 输出后缀：优先 output_format；否则沿用输入；再兜底 mp4
        if output_format:
            out_suffix = "." + str(output_format).lower().lstrip(".")
        else:
            out_suffix = (in_suf if in_suf else ".mp4")

        out_dir = toolbox.mk_out_dir(output_dir, engine="ffmpeg", tool="ffmpeg_mute_video")
        out_p = self.mk_out_file(out_dir, input_video, op="mute", suffix=out_suffix)

        cmd: list[str] = ["-y"] if overwrite else []
        cmd += ["-i", input_video, "-c:v", "copy", "-an", out_p]

        resp = await self.switch(cmd)

        ok = Path(out_p).exists()
        attachments = [self.attach_of(out_p)] if ok else []

        return {
            "text"        : f"ffmpeg_mute_video {'ok' if ok else 'ERROR'} output={out_p}",
            "attachments" : attachments,
            "data": {
                "ok"            : ok,
                "output_dir"    : str(out_dir),
                "output_file"   : out_p,
                "input_video"   : input_video,
                "input_suffix"  : in_suf,
                "output_suffix" : out_suffix,
                "output_format" : (None if output_format is None else str(output_format)),
                "overwrite"     : bool(overwrite)
            },
            "logs": [resp]
        }

    # workflow: ==== MCP Tool ====
    async def ffmpeg_probe_video(
        self,
        input_file: str
    ) -> dict[str, typing.Any]:
        """
        ffmpeg 探测媒体信息（等价于：ffmpeg -i <input_file>）。

        说明：
            - 不输出文件，不需要 output_dir
            - 不调用 ffprobe，只用 ffmpeg -i 的输出
            - 无论成功/异常，都返回多模态结构 {text, attachments, data, logs}

        Args:
            input_file : 输入媒体文件路径（音频/视频均可）

        Returns:
            dict: {text, attachments, data, logs}
        """
        marked.ensure_f(input_file, "input_file")

        cmd = ["-i", input_file]

        try:
            resp = await self.switch(cmd)
        except Exception as e:
            return {
                "text"        : f"ffmpeg_probe_video ERROR {type(e).__name__}: {e}",
                "attachments" : [],
                "data": {
                    "ok"           : False,
                    "input_file"   : input_file,
                    "duration_sec" : None,
                    "error"        : f"{type(e).__name__}: {e}"
                },
                "logs": []
            }

        # 从 ffmpeg 输出里提取 Duration: HH:MM:SS.xx
        m = re.search(
            r"Duration:\s*(\d{1,3}):(\d{2}):(\d{2}(?:\.\d+)?)",
            resp or "",
            re.IGNORECASE
        )
        duration_sec: float | None = None
        if m:
            hh = int(m.group(1))
            mm = int(m.group(2))
            ss = float(m.group(3))
            duration_sec = hh * 3600.0 + mm * 60.0 + ss

        ok = duration_sec is not None

        return {
            "text"        : f"ffmpeg_probe_video {'ok' if ok else 'done'} duration_sec={duration_sec}",
            "attachments" : [],
            "data": {
                "ok"           : ok,
                "input_file"   : input_file,
                "duration_sec" : duration_sec,
                "raw"          : (resp or "").strip()
            },
            "logs": [resp]
        }

    # workflow: ==== MCP Tool ====
    async def ffmpeg_extract_audio(
        self,
        input_video: str,
        output_dir: typing.Optional[str] = None,
        *,
        audio_format: typing.Literal["mp3", "aac", "wav", "m4a", "ogg", "flac"] = "mp3",
        audio_codec: typing.Optional[str] = None,
        overwrite: bool = True
    ) -> dict[str, typing.Any]:
        """
        抽取音轨（从视频/媒体文件导出音频）。

        用途：
            - 从 mp4/mkv/mov 等媒体抽取音频为 mp3/aac/wav...
            - 默认不输出视频（-vn）

        Args:
            input_video   : 输入媒体路径（必须存在且为文件）
            output_dir    : 输出根目录（可选；为空时建议由上游注入 report.toolkit_path）
            audio_format  : 输出音频格式（mp3/aac/wav/m4a/ogg/flac），用于决定输出后缀/容器
            audio_codec   : 指定音频编码器（None=让 ffmpeg 自选；可传 "aac"/"libmp3lame"/"pcm_s16le" 等）
            overwrite     : 是否覆盖输出（True 会传 -y）

        Returns:
            dict: {text, attachments, data, logs}
        """
        marked.ensure_f(input_video, "input_video")

        out_dir = toolbox.mk_out_dir(output_dir, engine="ffmpeg", tool="ffmpeg_extract_audio")

        # 输出文件：由内部生成（后缀由 audio_format 决定）
        suf = "." + str(audio_format).lower()
        out_p = self.mk_out_file(out_dir, input_video, op="audio", suffix=suf)

        cmd: list[str] = ["-y"] if overwrite else []
        cmd += ["-i", input_video, "-vn"]

        if audio_codec:
            cmd += ["-c:a", str(audio_codec)]

        cmd += [out_p]

        resp = await self.switch(cmd)

        ok = Path(out_p).exists()
        attachments = [self.attach_of(out_p)] if ok else []

        return {
            "text"        : f"ffmpeg_extract_audio {'ok' if ok else 'ERROR'} output={out_p}",
            "attachments" : attachments,
            "data": {
                "ok"           : ok,
                "output_dir"   : str(out_dir),
                "output_file"  : out_p,
                "audio_format" : str(audio_format),
                "audio_codec"  : (None if not audio_codec else str(audio_codec))
            },
            "logs": [resp]
        }

    # workflow: ==== MCP Tool ====
    async def ffmpeg_replace_audio(
        self,
        input_video: str,
        input_audio: str,
        output_dir: typing.Optional[str] = None,
        *,
        keep_video: bool = True,
        audio_codec: str = "aac",
        output_format: typing.Literal["mp4", "mkv", "mov", "webm"] = "mp4",
        overwrite: bool = True
    ) -> dict[str, typing.Any]:
        """
        替换视频音轨：保留画面，换成新的音频。

        用途：
            - 给视频换配音/换背景音乐
            - keep_video=True 时视频流 copy（不重编码，快）

        Args:
            input_video    : 输入视频（必须存在且为文件）
            input_audio    : 输入音频（必须存在且为文件）
            output_dir     : 输出根目录（可选；为空时建议由上游注入 report.toolkit_path）
            keep_video     : True=视频流 copy（不重编码）；False=让 ffmpeg 自行处理（通常会重编码）
            audio_codec    : 输出音频编码器（默认 aac；也可 opus、mp3 等）
            output_format  : 输出视频容器（mp4/mkv/mov/webm），用于决定输出后缀
            overwrite      : 是否覆盖输出（True 会传 -y）

        Returns:
            dict: {text, attachments, data, logs}
        """
        marked.ensure_f(input_video, "input_video")
        marked.ensure_f(input_audio, "input_audio")

        out_dir = toolbox.mk_out_dir(output_dir, engine="ffmpeg", tool="ffmpeg_replace_audio")

        # 输出文件：内部生成（后缀由 output_format 决定）
        suf = "." + str(output_format).lower()
        out_p = self.mk_out_file(out_dir, input_video, op="replace_audio", suffix=suf)

        cmd: list[str] = ["-y"] if overwrite else []
        cmd += ["-i", input_video, "-i", input_audio]
        cmd += ["-map", "0:v:0", "-map", "1:a:0"]  # 取第 1 路视频 + 第 2 路音频

        if keep_video:
            cmd += ["-c:v", "copy"]

        cmd += ["-c:a", str(audio_codec)]
        cmd += ["-shortest"]  # 防止音视频时长不一致拖尾
        cmd += [out_p]

        resp = await self.switch(cmd)

        ok = Path(out_p).exists()
        attachments = [self.attach_of(out_p)] if ok else []

        return {
            "text"        : f"ffmpeg_replace_audio {'ok' if ok else 'ERROR'} output={out_p}",
            "attachments" : attachments,
            "data": {
                "ok"            : ok,
                "output_dir"    : str(out_dir),
                "output_file"   : out_p,
                "input_video"   : input_video,
                "input_audio"   : input_audio,
                "keep_video"    : bool(keep_video),
                "audio_codec"   : str(audio_codec),
                "output_format" : str(output_format)
            },
            "logs": [resp]
        }

    # workflow: ==== MCP Tool ====
    async def ffmpeg_convert_audio(
        self,
        input_file: str,
        output_dir: typing.Optional[str] = None,
        *,
        audio_codec: typing.Optional[str] = None,
        sample_rate: typing.Optional[int] = None,
        channels: typing.Optional[int] = None,
        bitrate: typing.Optional[str] = None,
        output_format: typing.Literal["mp3", "aac", "wav", "m4a", "ogg", "flac"] = "mp3",
        overwrite: bool = True
    ) -> dict[str, typing.Any]:
        """
        转换音频格式/参数（任意输入 -> 指定输出文件）。

        常见：
          - mp3: audio_codec="libmp3lame"
          - aac: audio_codec="aac"
          - wav: audio_codec="pcm_s16le"

        Args:
            input_file     : 输入音频/视频文件（必须存在且为文件）
            output_dir     : 输出根目录（可选；为空时建议由上游注入 report.toolkit_path）
            audio_codec    : 音频编码器（None=让 ffmpeg 自行选择）
            sample_rate    : 采样率（例如 44100/48000）
            channels       : 声道数（1=mono, 2=stereo）
            bitrate        : 码率（例如 "128k", "192k"）
            output_format  : 输出音频容器/后缀（mp3/aac/wav/m4a/ogg/flac）
            overwrite      : 是否覆盖输出（True 会传 -y）

        Returns:
            dict: {text, attachments, data, logs}
        """
        marked.ensure_f(input_file, "input_file")

        out_dir = toolbox.mk_out_dir(output_dir, engine="ffmpeg", tool="ffmpeg_convert_audio")

        # 输出文件：内部生成（后缀由 output_format 决定）
        suf = "." + str(output_format).lower()
        out_p = self.mk_out_file(out_dir, input_file, op="convert_audio", suffix=suf)

        cmd: list[str] = ["-y"] if overwrite else []
        cmd += ["-i", input_file, "-vn"]  # -vn：不输出视频

        if audio_codec:
            cmd += ["-c:a", str(audio_codec)]
        if sample_rate is not None:
            cmd += ["-ar", str(int(sample_rate))]
        if channels is not None:
            cmd += ["-ac", str(int(channels))]
        if bitrate:
            cmd += ["-b:a", str(bitrate)]

        cmd += [out_p]

        resp = await self.switch(cmd)

        ok = Path(out_p).exists()
        attachments = [self.attach_of(out_p)] if ok else []

        return {
            "text"        : f"ffmpeg_convert_audio {'ok' if ok else 'ERROR'} output={out_p}",
            "attachments" : attachments,
            "data": {
                "ok"            : ok,
                "output_dir"    : str(out_dir),
                "output_file"   : out_p,
                "input_file"    : input_file,
                "audio_codec"   : (None if not audio_codec else str(audio_codec)),
                "sample_rate"   : (None if sample_rate is None else int(sample_rate)),
                "channels"      : (None if channels is None else int(channels)),
                "bitrate"       : (None if not bitrate else str(bitrate)),
                "output_format" : str(output_format)
            },
            "logs": [resp]
        }


class Player(object):

    def __init__(self):
        self.agent_id = "player"

    # workflow: ==== MCP Tool ====
    @staticmethod
    async def audio_play(audio_file: str, volume: float = 1.0, *_, **__) -> None:
        marked.ensure_f(
            audio_file, "audio_file 为空，无法播放，请传入有效的本地音频文件路径（如 .mp3/.wav）。"
        )

        # ===== 播放（失败也不可重试：需要用户排查环境/文件）=====
        volume_update = min(1.0, max(0.1, volume))
        try:
            pygame.mixer.init()
            pygame.mixer.music.load(audio_file)
            pygame.mixer.music.set_volume(volume_update)
            pygame.mixer.music.play()

            while pygame.mixer.music.get_busy():
                await asyncio.sleep(0.1)

        except (pygame.error, OSError, ValueError) as e:
            logger.error(e)
            raise marked.except_tip(
                "音频播放失败，请确认音频格式/编码是否受支持、文件是否损坏、以及运行环境是否具备可用的音频输出设备。", e
            )
        finally:
            # 释放资源，避免长期占用音频设备
            with contextlib.suppress(Exception):
                pygame.mixer.music.stop()
            with contextlib.suppress(Exception):
                pygame.mixer.quit()


if __name__ == '__main__':
    pass
