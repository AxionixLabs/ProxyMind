#  _   _       _       __  __          _ _
# | | | |_   _| |__   |  \/  | ___  __| (_) __ _ ___
# | |_| | | | | '_ \  | |\/| |/ _ \/ _` | |/ _` / __|
# |  _  | |_| | |_) | | |  | |  __/ (_| | | (_| \__ \
# |_| |_|\__,_|_.__/  |_|  |_|\___|\__,_|_|\__,_|___/
#
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
from backend.utilities import marked
from engine.terminal import Terminal

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
            return {
                "kind"      : "image",
                "local"     : str(p),
                "filename"  : p.name,
                "mime_type" : f"image/{suf.lstrip('.').replace('jpg', 'jpeg')}"
            }
        if suf in {".mp4", ".mkv", ".mov", ".webm"}:
            return {
                "kind"      : "file",
                "local"     : str(p),
                "filename"  : p.name,
                "mime_type" : "video/" + suf.lstrip(".")
            }
        if suf in {".mp3", ".wav", ".aac", ".m4a", ".ogg", ".flac"}:
            return {
                "kind"      : "file",
                "local"     : str(p),
                "filename"  : p.name,
                "mime_type" : "audio/" + suf.lstrip(".")
            }

        return {
            "kind"      : "file",
            "local"     : str(p),
            "filename"  : p.name,
            "mime_type" : "application/octet-stream"
        }

    async def switch(self, cmd: list[str]) -> str:
        self.out_fail = asyncio.Event()
        self.out_ring = deque(maxlen=10)

        cmd = [self.prefix] + cmd
        logger.info(f"[FFMPEG] {' '.join(cmd)}")

        switch_resp = await Terminal.cmd_line(cmd)
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
        output_image: typing.Optional[str] = None,
        *,
        at_sec: float = 0.0,
        overwrite: bool = True
    ) -> typing.Any:
        """
        视频截图：指定时间点截取一帧输出为图片。

        用途：
            - 生成封面图/缩略图
            - 快速验证视频内容

        Args:
            input_video  : 输入视频路径。
            output_image : 输出图片路径（如 .jpg/.png/.webp）。
            at_sec       : 截图时间点（秒）。
            overwrite    : 是否覆盖输出；True 会传 -y。
        """
        marked.ensure_f(input_video, "input_video")
        out_p = marked.ensure_o(input_video, output_image, "output_image", suffix=".png", overwrite=overwrite)

        cmd: list[str] = ["-y"] if overwrite else []

        cmd += ["-ss", str(float(at_sec)), "-i", input_video]
        cmd += ["-frames:v", "1", out_p]

        return await self.switch(cmd)

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
    ) -> typing.Any:
        """
        拆帧（视频 -> 图片序列）。

        Args:
            input_video  : 输入视频路径（.mp4/.mkv/...）
            output_dir   : 输出目录
            pattern      : 输出文件名模板（需包含 %d，例如 frame_%06d.png）
            fps          : 抽帧帧率（None=按原视频逐帧导出；设置例如 5 表示每秒 5 帧）
            image_format : 输出图片格式（jpg/png/webp）
            start_sec    : 从视频第几秒开始（可选）
            duration_sec : 截取多长时间范围进行拆帧（可选）
            scale_w      : 可选缩放（例如 720,None 表示等比缩放到宽 720）
            scale_h      : 可选缩放（例如 720,None 表示等比缩放到宽 720）
            overwrite    : 是否覆盖输出
        """
        marked.ensure_f(input_video, "input_video")

        video_path = Path(input_video).expanduser().resolve()
        uid_folder = f"{video_path.stem}_frames_{uuid.uuid4().hex[:6]}"

        if not output_dir or not str(output_dir).strip():
            out_path = video_path.parent / uid_folder
        else:
            base_dir = Path(output_dir).expanduser().resolve()
            marked.ensure_d(str(base_dir), "output_dir")
            out_path = base_dir / uid_folder

        out_path.mkdir(parents=True, exist_ok=True)
        marked.ensure_d(str(out_path), "output_dir")

        # 统一输出扩展名（防止 pattern 没写对）
        pat = pattern
        if not pat.lower().endswith(f".{image_format}"):
            pat = os.path.splitext(pat)[0] + f".{image_format}"

        vf: list[str] = []
        if fps is not None:
            vf.append(f"fps={float(fps)}")
        if scale_w is not None or scale_h is not None:
            w = scale_w if scale_w is not None else -1
            h = scale_h if scale_h is not None else -1
            vf.append(f"scale={int(w)}:{int(h)}")

        cmd: list[str] = []
        if overwrite:
            cmd.append("-y")
        if start_sec is not None:
            cmd += ["-ss", str(float(start_sec))]
        cmd += ["-i", input_video]
        if duration_sec is not None:
            cmd += ["-t", str(float(duration_sec))]

        if vf:
            cmd += ["-vf", ",".join(vf)]

        # 导出图片：用 -vsync 0 避免复制/补帧
        out_path = str(out_path / pat)
        cmd += ["-vsync", "0", out_path]
        return await self.switch(cmd)

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
            input_video  : 输入视频路径（必须存在）
            output_dir   : 输出根目录（可选；为空则默认在 input_video 同目录创建 `<stem>_keyframes_<short_uuid>/`）
            max_frames   : 最多返回多少张关键帧（最终 attachments 上限；>=1）
            uniform_n    : 均匀采样张数（按时长均分取点截图；0=关闭）
            image_format : 输出图片格式（jpg/png/webp）
            overwrite    : 是否覆盖输出（True 会传 -y）
        """
        marked.ensure_f(input_video, "input_video")

        max_frames = max(1, int(max_frames))
        uniform_n  = max(0, int(uniform_n))

        video_path = Path(input_video).expanduser().resolve()
        uid_folder = f"{video_path.stem}_keyframes_{uuid.uuid4().hex[:6]}"

        if not output_dir or not str(output_dir).strip():
            out_dir = video_path.parent / uid_folder
        else:
            base_dir = Path(output_dir).expanduser().resolve()
            marked.ensure_d(str(base_dir), "output_dir")
            out_dir = base_dir / uid_folder
        out_dir.mkdir(parents=True, exist_ok=True)

        logs: list[str] = []
        picked: list[Path] = []

        out = await self.ffmpeg_probe_video(input_video)
        m = re.search(r"Duration:\s*(\d{1,3}):(\d{2}):(\d{2}(?:\.\d+)?)", out or "", re.IGNORECASE)
        if m:
            hh = int(m.group(1))
            mm = int(m.group(2))
            ss = float(m.group(3))
            duration = hh * 3600.0 + mm * 60.0 + ss
        else:
            duration = 0.0

        if uniform_n > 0 and float(duration) > 0.05:
            for i in range(uniform_n):
                at = (float(duration) * (i + 1) / (uniform_n + 1))
                out_img = out_dir / f"uni_{i + 1:03d}.{image_format}"
                try:
                    await self.ffmpeg_extract_snapshot(
                        input_video=input_video,
                        output_image=str(out_img),
                        at_sec=float(at),
                        overwrite=overwrite
                    )
                    if out_img.exists():
                        picked.append(out_img)
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
        text = f"frames={len(attachments)} out_dir={out_dir}"

        return {
            "text"        : text,
            "attachments" : attachments,
            "data": {
                "ok"           : ok,
                "out_dir"      : str(out_dir),
                "count"        : len(attachments),
                "duration_sec" : float(duration),
                "uniform_n"    : uniform_n,
                "max_frames"   : max_frames,
                "files"        : [a.get("local") for a in attachments]
            },
            "logs": logs
        }

    # workflow: ==== MCP Tool ====
    async def ffmpeg_trim_video(
        self,
        input_video: str,
        output_video: typing.Optional[str] = None,
        *,
        start_sec: float = 0.0,
        end_sec: typing.Optional[float] = None,
        duration_sec: typing.Optional[float] = None,
        mode: typing.Literal["copy", "reencode"] = "copy",
        video_codec: str = "libx264",
        crf: int = 23,
        preset: str = "veryfast",
        overwrite: bool = True
    ) -> typing.Any:
        """
        视频截取（按时间范围）。

        选择：
          - mode="copy"：不重编码，快，但只在关键帧附近精确（常用于快速裁剪）
          - mode="reencode"：重编码，精确但更慢

        Args:
            input_video  : 输入视频
            output_video : 输出视频
            start_sec    : 起始秒
            end_sec      : 结束秒（可选；与 duration_sec 二选一）
            duration_sec : 截取时长（可选；与 end_sec 二选一）
            mode         : copy / reencode
            video_codec  : mode="reencode" 时生效
            crf          : mode="reencode" 时生效
            preset       : mode="reencode" 时生效
            overwrite    : 是否覆盖输出
        """
        marked.ensure_f(input_video, "input_video")
        out_p = marked.ensure_o(input_video, output_video, "output_video", overwrite=overwrite)

        cmd: list[str] = ["-y"] if overwrite else []

        cmd += ["-ss", str(float(start_sec))]
        cmd += ["-i", input_video]

        if duration_sec is not None:
            cmd += ["-t", str(float(duration_sec))]
        elif end_sec is not None:
            dur = max(0.0, float(end_sec) - float(start_sec))
            cmd += ["-t", str(dur)]

        if mode == "copy":
            cmd += ["-c", "copy"]
        else:
            cmd += ["-c:v", video_codec, "-preset", str(preset), "-crf", str(int(crf)), "-c:a", "copy"]
        cmd += [out_p]

        return await self.switch(cmd)

    # workflow: ==== MCP Tool ====
    async def ffmpeg_scale_video(
        self,
        input_video: str,
        output_video: typing.Optional[str] = None,
        *,
        scale_w: int | None = None,
        scale_h: int | None = None,
        video_codec: str = "libx264",
        crf: int = 23,
        preset: str = "veryfast",
        keep_audio: bool = True,
        overwrite: bool = True,
    ) -> typing.Any:
        """
        视频缩放并输出新视频（重编码）。

        Args:
            input_video  : 输入视频文件路径（必须存在）。
            output_video : 输出视频文件路径（会生成新文件）。
            scale_w      : 目标宽（None=不指定；与 scale_h 搭配，None 会用 -1 等比）。
            scale_h      : 目标高（None=不指定；与 scale_w 搭配，None 会用 -1 等比）。
            video_codec  : 视频编码器（默认 libx264）。
            crf          : 质量（越小越清晰/体积越大，常用 18~28）。
            preset       : 编码速度（ultrafast/veryfast/medium/slow...）。
            keep_audio   : True=保留音频（copy），False=去音轨。
            overwrite    : 是否覆盖输出（True 会传 -y）。
        """
        marked.ensure_f(input_video, "input_video")
        out_p = marked.ensure_o(input_video, output_video, "output_video", overwrite=overwrite)

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

        return await self.switch(cmd)

    # workflow: ==== MCP Tool ====
    async def ffmpeg_convert_video(
        self,
        input_video: str,
        output_video: typing.Optional[str] = None,
        *,
        fps: float = 60,
        video_codec: str = "libx264",
        crf: int = 23,
        preset: str = "veryfast",
        keep_audio: bool = True,
        overwrite: bool = True
    ) -> typing.Any:
        """
        转换视频帧率（重编码）。

        Args:
            input_video  : 输入视频
            output_video : 输出视频
            fps          : 目标帧率（例如 30 / 60）
            video_codec  : 视频编码器（默认 libx264）
            crf          : 质量（x264 常用 18~28，越小越清晰体积越大）
            preset       : 编码速度（ultrafast/veryfast/medium/slow...）
            keep_audio   : 是否保留音频（True=原样拷贝音频）
            overwrite    : 是否覆盖输出
        """
        marked.ensure_f(input_video, "input_video")
        out_p = marked.ensure_o(input_video, output_video, "output_video", overwrite=overwrite)

        cmd: list[str] = ["-y"] if overwrite else []
        cmd += ["-i", input_video]
        cmd += ["-vf", f"fps={float(fps)}"]
        cmd += ["-c:v", video_codec, "-preset", str(preset), "-crf", str(int(crf))]

        if keep_audio:
            cmd += ["-c:a", "copy"]
        else:
            cmd += ["-an"]

        cmd += [out_p]
        return await self.switch(cmd)

    # workflow: ==== MCP Tool ====
    async def ffmpeg_concat_video(
        self,
        list_file: str,
        output_video: typing.Optional[str] = None,
        *,
        overwrite: bool = True,
        reencode: bool = False,
        video_codec: str = "libx264",
        crf: int = 23,
        preset: str = "veryfast",
        audio_codec: str = "aac"
    ) -> typing.Any:
        """
        拼接视频（concat demuxer）。

        list_file 文件格式（每行一段）：
            file '/abs/path/1.mp4'
            file '/abs/path/2.mp4'

        Args:
            list_file    : concat 列表文件路径（必须存在且为文件）。
            output_video : 输出视频路径（生成新文件）。
            overwrite    : 是否覆盖输出；True 会传 -y。
            reencode     :
                False=-c copy（要求输入片段编码/参数一致；最快）
                True=重编码后拼接（更稳但慢）
            video_codec  : reencode=True 时用于视频重编码。
            crf          : reencode=True 时用于视频重编码。
            preset       : reencode=True 时用于视频重编码。
            audio_codec  : reencode=True 时用于音频重编码（默认 aac）。
        """
        marked.ensure_f(list_file, "list_file")
        out_p = marked.ensure_o(list_file, output_video, "output_video", suffix=".mp4", overwrite=overwrite)

        cmd: list[str] = ["-y"] if overwrite else []
        cmd += ["-f", "concat", "-safe", "0", "-i", list_file]

        if not reencode:
            cmd += ["-c", "copy"]
        else:
            cmd += ["-c:v", str(video_codec), "-preset", str(preset), "-crf", str(int(crf))]
            cmd += ["-c:a", str(audio_codec)]
        cmd += [out_p]

        return await self.switch(cmd)

    # workflow: ==== MCP Tool ====
    async def ffmpeg_remux_video(
        self,
        input_video: str,
        output_video: typing.Optional[str] = None,
        *,
        overwrite: bool = True
    ) -> typing.Any:
        """
        仅“换容器/封装”不重编码（最快）。

        用途：
            - mkv -> mp4 / mp4 -> mkv（不改画面质量、不改音轨编码）
            - 失败通常来自：输入不存在、容器/流不兼容、输出路径不可写

        Args:
            input_video  : 输入视频路径（必须存在且为文件）。
            output_video : 输出视频路径（会生成新文件；建议传绝对路径）。
            overwrite    : 是否覆盖同名输出文件；True 会传 -y。
        """
        marked.ensure_f(input_video, "input_video")
        in_suf = Path(input_video).suffix.lower()

        # 默认换容器：保证 out_suffix != in_suf
        if in_suf in {".mp4", ".m4v", ".mov"}:
            out_suffix = ".mkv"
        elif in_suf == ".mkv":
            out_suffix = ".mp4"
        else:
            out_suffix = ".mp4"  # 兜底：也可以改成 ".mkv"

        out_p = marked.ensure_o(input_video, output_video, "output_video", suffix=out_suffix, overwrite=overwrite)

        cmd: list[str] = ["-y"] if overwrite else []

        cmd += ["-i", input_video, "-c", "copy", out_p]

        return await self.switch(cmd)

    # workflow: ==== MCP Tool ====
    async def ffmpeg_mute_video(
        self,
        input_video: str,
        output_video: str,
        *,
        overwrite: bool = True
    ) -> typing.Any:
        """
        去音轨（输出静音视频；视频流不重编码，最快）。

        Args:
            input_video  : 输入视频文件路径（必须存在）。
            output_video : 输出视频文件路径（会生成新文件）。
            overwrite    : 是否覆盖输出（True 会传 -y）。
        """
        marked.ensure_f(input_video, "input_video")
        out_p = marked.ensure_o(input_video, output_video, "output_video", overwrite=overwrite)

        cmd: list[str] = ["-y"] if overwrite else []

        cmd += ["-i", input_video, "-c:v", "copy", "-an", out_p]

        return await self.switch(cmd)

    # workflow: ==== MCP Tool ====
    async def ffmpeg_probe_video(self, input_file: str) -> typing.Any:
        """
        ffmpeg 探测媒体信息（等价于：ffmpeg -i <input_file>）。

        Args:
            input_file : 输入媒体文件路径（音频/视频均可）

        Returns:
            typing.Any : ffmpeg 输出摘要（通常包含时长、码率、流信息等）
        """
        marked.ensure_f(input_file, "input_file")

        cmd = ["-i", input_file]
        return await self.switch(cmd)

    # workflow: ==== MCP Tool ====
    async def ffmpeg_extract_audio(
        self,
        input_video: str,
        output_audio: typing.Optional[str] = None,
        *,
        audio_codec: typing.Optional[str] = None,
        overwrite: bool = True
    ) -> typing.Any:
        """
        抽取音轨（从视频/媒体文件输出音频文件）。

        用途：
            - 从 mp4/mkv 抽音频为 mp3/aac/wav 等
            - 默认不输出视频（-vn）

        Args:
            input_video  : 输入媒体路径（音频/视频都可，但通常是视频）。
            output_audio : 输出音频文件路径（扩展名决定封装容器，如 .mp3/.aac/.wav）。
            audio_codec  : 指定音频编码器（None=让 ffmpeg 自选）。
            overwrite    : 是否覆盖输出；True 会传 -y。
        """
        marked.ensure_f(input_video, "input_video")
        out_p = marked.ensure_o(input_video, output_audio, "output_audio", suffix=".mp3", overwrite=overwrite)

        cmd: list[str] = ["-y"] if overwrite else []

        cmd += ["-i", input_video, "-vn"]
        if audio_codec:
            cmd += ["-c:a", audio_codec]
        cmd += [out_p]

        return await self.switch(cmd)

    # workflow: ==== MCP Tool ====
    async def ffmpeg_replace_audio(
        self,
        input_video: str,
        input_audio: str,
        output_video: typing.Optional[str] = None,
        *,
        keep_video: bool = True,
        audio_codec: str = "aac",
        overwrite: bool = True
    ) -> typing.Any:
        """
        替换视频音轨：保留画面，换成新的音频。

        用途：
            - 给视频换配音/换背景音乐
            - keep_video=True 时视频流 copy（不重编码，快）

        Args:
            input_video  : 输入视频（提供视频流）。
            input_audio  : 输入音频（提供音频流）。
            output_video : 输出视频（生成新文件）。
            keep_video   : True=视频流 copy（不重编码）；False=由 ffmpeg 默认策略处理（一般会重编码）。
            audio_codec  : 输出音频编码器（默认 aac；也可 opus、mp3 等）。
            overwrite    : 是否覆盖输出；True 会传 -y。
        """
        marked.ensure_f(input_video, "input_video")
        marked.ensure_f(input_audio, "input_audio")
        out_p = marked.ensure_o(input_video, output_video, "output_video", overwrite=overwrite)

        cmd: list[str] = ["-y"] if overwrite else []

        cmd += ["-i", input_video, "-i", input_audio]
        cmd += ["-map", "0:v:0", "-map", "1:a:0"]  # 取第1路视频 + 第2路音频
        if keep_video:
            cmd += ["-c:v", "copy"]
        cmd += ["-c:a", str(audio_codec)]
        cmd += ["-shortest"]  # 防止音视频时长不一致拖尾
        cmd += [out_p]

        return await self.switch(cmd)

    # workflow: ==== MCP Tool ====
    async def ffmpeg_convert_audio(
        self,
        input_file: str,
        output_file: typing.Optional[str] = None,
        *,
        audio_codec: typing.Optional[str] = None,
        sample_rate: typing.Optional[int] = None,
        channels: typing.Optional[int] = None,
        bitrate: typing.Optional[str] = None,
        overwrite: bool = True
    ) -> typing.Any:
        """
        转换音频格式/参数（任意输入 -> 指定输出文件）。

        常见：
          - mp3: audio_codec="libmp3lame"
          - aac: audio_codec="aac"
          - wav: audio_codec="pcm_s16le"

        Args:
            input_file  : 输入音频/视频文件
            output_file : 输出音频文件（扩展名决定容器，如 .mp3/.aac/.wav）
            audio_codec : 音频编码器（None=让 ffmpeg 自行选择）
            sample_rate : 采样率（例如 44100/48000）
            channels    : 声道数（1=mono, 2=stereo）
            bitrate     : 码率（例如 "128k", "192k"）
            overwrite   : 是否覆盖输出
        """
        marked.ensure_f(input_file, "input_file")
        out_p = marked.ensure_o(input_file, output_file, "output_file", suffix=".mp3", overwrite=overwrite)

        cmd: list[str] = ["-y"] if overwrite else []

        cmd += ["-i", input_file, "-vn"]  # -vn：不输出视频

        if audio_codec:
            cmd += ["-c:a", audio_codec]
        if sample_rate:
            cmd += ["-ar", str(int(sample_rate))]
        if channels:
            cmd += ["-ac", str(int(channels))]
        if bitrate:
            cmd += ["-b:a", str(bitrate)]

        cmd += [out_p]
        return await self.switch(cmd)


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
