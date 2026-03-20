#  _____ _____
# |  ___|  ___| __ ___  _ __   ___  __ _
# | |_  | |_ | '_ ` _ \| '_ \ / _ \/ _` |
# |  _| |  _|| | | | | | |_) |  __/ (_| |
# |_|   |_|  |_| |_| |_| .__/ \___|\__, |
#                      |_|         |___/
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from mcp.server import FastMCP
from mcp.types import CallToolResult
from backend.mcp_hub.hub_manage import Requires
from backend.middlewares.mid_task import task_middleware
from backend.utilities.instance import Ins
from backend.utilities.pipeline import Idle
from backend.utilities.toolbox import broadcast


def bind(mcp: FastMCP, idle: Idle) -> None:

    @mcp.tool(meta={"hidden": False, "domain": "media", "class": "ffmpeg"})
    @task_middleware("ffmpeg_extract_snapshot")
    async def ffmpeg_extract_snapshot(
        input_video: str,
        output_dir: typing.Optional[str] = None,
        *,
        at_sec: float = 0.0,
        image_format: typing.Literal["jpg", "png", "webp"] = "png",
        overwrite: bool = True
    ) -> CallToolResult:
        """
        D: media
        C: ffmpeg
        A: ffmpeg_extract_snapshot
        P:
          input_video: str
          output_dir: str?=None        # 可选，输出目录；None 时使用默认落盘位置
          at_sec: float=0.0
          image_format: oneof(jpg|png|webp)="png"
          overwrite: bool=True
        R: CTR
        N:
          - 从视频中提取指定时间点的一张静态图片。
          - 该工具只输出单帧图片，不做批量抽帧或关键帧分析。
          - 输出文件路径由结果返回；`output_dir` 只决定落盘目录。
        """

        await Requires.connect_ffmpeg()

        args = {
            "input_video"  : input_video,
            "output_dir"   : output_dir,
            "at_sec"       : at_sec,
            "image_format" : image_format,
            "overwrite"    : overwrite
        }

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin(f"{Ins.ffmpeg.agent_id}.ffmpeg_extract_snapshot", args=args)
            try:
                return await Ins.ffmpeg.ffmpeg_extract_snapshot(**args)
            finally:
                await idle.job_final(job_id)

        return await broadcast(
            tool="ffmpeg_extract_snapshot",
            args=args,
            target_list=[Ins.ffmpeg],
            call=call,
            overrides=None
        )

    @mcp.tool(meta={"hidden": False, "domain": "media", "class": "ffmpeg"})
    @task_middleware("ffmpeg_extract_frames")
    async def ffmpeg_extract_frames(
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
    ) -> CallToolResult:
        """
        D: media
        C: ffmpeg
        A: ffmpeg_extract_frames
        P:
          input_video: str
          output_dir: str?=None
          pattern: str="frame_%06d.png"      # 需包含 %d；扩展名会对齐 image_format
          fps: float?=None
          image_format: oneof(jpg|png|webp)="png"
          start_sec: float?=None
          duration_sec: float?=None
          scale_w: int?=None                 # None 时等比用 -1
          scale_h: int?=None                 # None 时等比用 -1
          overwrite: bool=True
        R: CTR
        N:
          - 把视频导出为图片序列。
          - 支持按帧率抽帧、按时间窗口截取和按尺寸缩放。
          - 返回中只附带少量代表性附件；完整帧序列以落盘目录为准。
        """

        await Requires.connect_ffmpeg()

        args = {
            "input_video"  : input_video,
            "output_dir"   : output_dir,
            "pattern"      : pattern,
            "fps"          : fps,
            "image_format" : image_format,
            "start_sec"    : start_sec,
            "duration_sec" : duration_sec,
            "scale_w"      : scale_w,
            "scale_h"      : scale_h,
            "overwrite"    : overwrite
        }

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin(f"{Ins.ffmpeg.agent_id}.ffmpeg_extract_frames", args=args)
            try:
                return await Ins.ffmpeg.ffmpeg_extract_frames(**args)
            finally:
                await idle.job_final(job_id)

        return await broadcast(
            tool="ffmpeg_extract_frames",
            args=args,
            target_list=[Ins.ffmpeg],
            call=call,
            overrides=None
        )

    @mcp.tool(meta={"hidden": False, "domain": "media", "class": "ffmpeg"})
    @task_middleware("ffmpeg_extract_keyframes")
    async def ffmpeg_extract_keyframes(
        input_video: str,
        output_dir: typing.Optional[str] = None,
        *,
        max_frames: int = 12,
        uniform_n: int = 6,
        image_format: typing.Literal["jpg", "png", "webp"] = "png",
        overwrite: bool = True
    ) -> CallToolResult:
        """
        D: media
        C: ffmpeg
        A: ffmpeg_extract_keyframes
        P:
          input_video: str
          output_dir: str?=None
          max_frames: int=12            # attachments 上限（>=1）
          uniform_n: int=6              # 均匀采样张数（0=关闭）
          image_format: oneof(jpg|png|webp)="png"
          overwrite: bool=True
        R: CTR
        N:
          - 从视频中提取关键帧并输出为图片。
          - 返回的附件数量受 `max_frames` 约束，用于快速查看代表性画面。
          - 若需要完整图片序列，应改用 `ffmpeg_extract_frames`。
        """

        await Requires.connect_ffmpeg()

        args = {
            "input_video"  : input_video,
            "output_dir"   : output_dir,
            "max_frames"   : max_frames,
            "uniform_n"    : uniform_n,
            "image_format" : image_format,
            "overwrite"    : overwrite
        }

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin(f"{Ins.ffmpeg.agent_id}.ffmpeg_extract_keyframes", args=args)
            try:
                return await Ins.ffmpeg.ffmpeg_extract_keyframes(**args)
            finally:
                await idle.job_final(job_id)

        return await broadcast(
            tool="ffmpeg_extract_keyframes",
            args=args,
            target_list=[Ins.ffmpeg],
            call=call,
            overrides=None
        )

    @mcp.tool(meta={"hidden": False, "domain": "media", "class": "ffmpeg"})
    @task_middleware("ffmpeg_extract_scene")
    async def ffmpeg_extract_scene(
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
    ) -> CallToolResult:
        """
        D: media
        C: ffmpeg
        A: ffmpeg_extract_scene
        P:
          input_video: str
          output_dir: str?=None
          scene_th: float=0.35
          max_frames: int=12
          pattern: str="scene_%06d"
          image_format: oneof(jpg|png|webp)="png"
          start_sec: float?=None
          duration_sec: float?=None
          scale_w: int?=256
          scale_h: int?=None
          overwrite: bool=True
        R: CTR
        N:
          - 按场景变化强度从视频中抽取代表性画面。
          - `scene_th` 越大，命中的场景切换帧通常越少。
          - `max_frames` 只限制返回中的附件数量，不限制实际落盘帧数。
        """

        await Requires.connect_ffmpeg()

        args = {
            "input_video"  : input_video,
            "output_dir"   : output_dir,
            "scene_th"     : scene_th,
            "max_frames"   : max_frames,
            "pattern"      : pattern,
            "image_format" : image_format,
            "start_sec"    : start_sec,
            "duration_sec" : duration_sec,
            "scale_w"      : scale_w,
            "scale_h"      : scale_h,
            "overwrite"    : overwrite
        }

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin(f"{Ins.ffmpeg.agent_id}.ffmpeg_extract_scene", args=args)
            try:
                return await Ins.ffmpeg.ffmpeg_extract_scene(**args)
            finally:
                await idle.job_final(job_id)

        return await broadcast(
            tool="ffmpeg_extract_scene",
            args=args,
            target_list=[Ins.ffmpeg],
            call=call,
            overrides=None
        )

    @mcp.tool(meta={"hidden": False, "domain": "media", "class": "ffmpeg"})
    @task_middleware("ffmpeg_trim_video")
    async def ffmpeg_trim_video(
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
    ) -> CallToolResult:
        """
        D: media
        C: ffmpeg
        A: ffmpeg_trim_video
        P:
          input_video: str
          output_dir: str?=None
          start_sec: float=0.0
          end_sec: float?=None
          duration_sec: float?=None
          mode: oneof(copy|reencode)="copy"
          video_codec: str="libx264"
          crf: int=23
          preset: str="veryfast"
          output_format: oneof(mp4|mkv|mov|webm)?=None
          overwrite: bool=True
        R: CTR
        N:
          - 按时间范围裁剪视频。
          - `mode=copy` 更快但裁剪精度受关键帧限制；`mode=reencode` 更慢但时间边界更精确。
          - 该工具只处理时间裁剪，不改变画面尺寸或帧率。
        """

        await Requires.connect_ffmpeg()

        args = {
            "input_video"   : input_video,
            "output_dir"    : output_dir,
            "start_sec"     : start_sec,
            "end_sec"       : end_sec,
            "duration_sec"  : duration_sec,
            "mode"          : mode,
            "video_codec"   : video_codec,
            "crf"           : crf,
            "preset"        : preset,
            "output_format" : output_format,
            "overwrite"     : overwrite
        }

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin(f"{Ins.ffmpeg.agent_id}.ffmpeg_trim_video", args=args)
            try:
                return await Ins.ffmpeg.ffmpeg_trim_video(**args)
            finally:
                await idle.job_final(job_id)

        return await broadcast(
            tool="ffmpeg_trim_video",
            args=args,
            target_list=[Ins.ffmpeg],
            call=call,
            overrides=None
        )

    @mcp.tool(meta={"hidden": False, "domain": "media", "class": "ffmpeg"})
    @task_middleware("ffmpeg_scale_video")
    async def ffmpeg_scale_video(
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
    ) -> CallToolResult:
        """
        D: media
        C: ffmpeg
        A: ffmpeg_scale_video
        P:
          input_video: str
          output_dir: str?=None
          scale_w: int?=None
          scale_h: int?=None
          video_codec: str="libx264"
          crf: int=23
          preset: str="veryfast"
          keep_audio: bool=True
          output_format: oneof(mp4|mkv|mov|webm)?=None
          overwrite: bool=True
        R: CTR
        N:
          - 把视频缩放到新的尺寸并输出新文件。
          - 至少应给出一个目标边；另一个边留空时会按比例自动计算。
          - 该工具会重编码视频；是否保留音频由 `keep_audio` 决定。
        """

        await Requires.connect_ffmpeg()

        args = {
            "input_video"   : input_video,
            "output_dir"    : output_dir,
            "scale_w"       : scale_w,
            "scale_h"       : scale_h,
            "video_codec"   : video_codec,
            "crf"           : crf,
            "preset"        : preset,
            "keep_audio"    : keep_audio,
            "output_format" : output_format,
            "overwrite"     : overwrite
        }

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin(f"{Ins.ffmpeg.agent_id}.ffmpeg_scale_video", args=args)
            try:
                return await Ins.ffmpeg.ffmpeg_scale_video(**args)
            finally:
                await idle.job_final(job_id)

        return await broadcast(
            tool="ffmpeg_scale_video",
            args=args,
            target_list=[Ins.ffmpeg],
            call=call,
            overrides=None
        )

    @mcp.tool(meta={"hidden": False, "domain": "media", "class": "ffmpeg"})
    @task_middleware("ffmpeg_convert_video")
    async def ffmpeg_convert_video(
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
    ) -> CallToolResult:
        """
        D: media
        C: ffmpeg
        A: ffmpeg_convert_video
        P:
          input_video: str
          output_dir: str?=None
          fps: float=60
          video_codec: str="libx264"
          crf: int=23
          preset: str="veryfast"
          keep_audio: bool=True
          output_format: oneof(mp4|mkv|mov|webm)?=None
          overwrite: bool=True
        R: CTR
        N:
          - 把视频重编码到新的目标帧率。
          - 该工具会重编码视频流，画质与体积主要受 `video_codec`、`crf` 和 `preset` 影响。
          - 是否保留音频由 `keep_audio` 决定。
        """

        await Requires.connect_ffmpeg()

        args = {
            "input_video"   : input_video,
            "output_dir"    : output_dir,
            "fps"           : fps,
            "video_codec"   : video_codec,
            "crf"           : crf,
            "preset"        : preset,
            "keep_audio"    : keep_audio,
            "output_format" : output_format,
            "overwrite"     : overwrite
        }

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin(f"{Ins.ffmpeg.agent_id}.ffmpeg_convert_video", args=args)
            try:
                return await Ins.ffmpeg.ffmpeg_convert_video(**args)
            finally:
                await idle.job_final(job_id)

        return await broadcast(
            tool="ffmpeg_convert_video",
            args=args,
            target_list=[Ins.ffmpeg],
            call=call,
            overrides=None
        )

    @mcp.tool(meta={"hidden": False, "domain": "media", "class": "ffmpeg"})
    @task_middleware("ffmpeg_concat_video")
    async def ffmpeg_concat_video(
        list_file: str,
        output_dir: typing.Optional[str] = None,
        *,
        overwrite: bool = True,
        reencode: bool = False,
        video_codec: str = "libx264",
        crf: int = 23,
        preset: str = "veryfast",
        audio_codec: str = "aac",
        output_format: typing.Optional[typing.Literal["mp4", "mkv", "mov", "webm"]] = "mp4"
    ) -> CallToolResult:
        """
        D: media
        C: ffmpeg
        A: ffmpeg_concat_video
        P:
          list_file: str
          output_dir: str?=None
          overwrite: bool=True
          reencode: bool=False
          video_codec: str="libx264"
          crf: int=23
          preset: str="veryfast"
          audio_codec: str="aac"
          output_format: oneof(mp4|mkv|mov|webm)="mp4"
        R: CTR
        N:
          - 按 `list_file` 中的顺序拼接多段视频。
          - `reencode=False` 速度更快，但要求输入片段的编码参数足够一致；`reencode=True` 更稳。
          - 该工具只负责顺序拼接，不做自动对齐、补帧或内容理解。
        """

        await Requires.connect_ffmpeg()

        args = {
            "list_file"     : list_file,
            "output_dir"    : output_dir,
            "overwrite"     : overwrite,
            "reencode"      : reencode,
            "video_codec"   : video_codec,
            "crf"           : crf,
            "preset"        : preset,
            "audio_codec"   : audio_codec,
            "output_format" : output_format
        }

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin(f"{Ins.ffmpeg.agent_id}.ffmpeg_concat_video", args=args)
            try:
                return await Ins.ffmpeg.ffmpeg_concat_video(**args)
            finally:
                await idle.job_final(job_id)

        return await broadcast(
            tool="ffmpeg_concat_video",
            args=args,
            target_list=[Ins.ffmpeg],
            call=call,
            overrides=None
        )

    @mcp.tool(meta={"hidden": False, "domain": "media", "class": "ffmpeg"})
    @task_middleware("ffmpeg_remux_video")
    async def ffmpeg_remux_video(
        input_video: str,
        output_dir: typing.Optional[str] = None,
        *,
        output_format: typing.Optional[typing.Literal["mp4", "mkv", "mov", "webm"]] = None,
        overwrite: bool = True
    ) -> CallToolResult:
        """
        D: media
        C: ffmpeg
        A: ffmpeg_remux_video
        P:
          input_video: str
          output_dir: str?=None
          output_format: oneof(mp4|mkv|mov|webm)?=None
          overwrite: bool=True
        R: CTR
        N:
          - 仅更换媒体容器，不重编码音视频流。
          - 该工具适合在兼容的封装格式之间做快速 remux，不适合修复编码本身的问题。
          - `output_format` 不传时会选择一个与输入不同的容器格式。
        """

        await Requires.connect_ffmpeg()

        args = {
            "input_video"   : input_video,
            "output_dir"    : output_dir,
            "output_format" : output_format,
            "overwrite"     : overwrite
        }

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin(f"{Ins.ffmpeg.agent_id}.ffmpeg_remux_video", args=args)
            try:
                return await Ins.ffmpeg.ffmpeg_remux_video(**args)
            finally:
                await idle.job_final(job_id)

        return await broadcast(
            tool="ffmpeg_remux_video",
            args=args,
            target_list=[Ins.ffmpeg],
            call=call,
            overrides=None
        )

    @mcp.tool(meta={"hidden": False, "domain": "media", "class": "ffmpeg"})
    @task_middleware("ffmpeg_mute_video")
    async def ffmpeg_mute_video(
        input_video: str,
        output_dir: typing.Optional[str] = None,
        *,
        output_format: typing.Optional[typing.Literal["mp4", "mkv", "mov", "webm"]] = None,
        overwrite: bool = True
    ) -> CallToolResult:
        """
        D: media
        C: ffmpeg
        A: ffmpeg_mute_video
        P:
          input_video: str
          output_dir: str?=None
          output_format: oneof(mp4|mkv|mov|webm)?=None
          overwrite: bool=True
        R: CTR
        N:
          - 移除视频中的音轨并输出静音视频。
          - 该工具保留原视频画面，不做重新剪辑或重新编码视频流。
          - 如果只想替换音轨，应改用 `ffmpeg_replace_audio`。
        """

        await Requires.connect_ffmpeg()

        args = {
            "input_video"   : input_video,
            "output_dir"    : output_dir,
            "output_format" : output_format,
            "overwrite"     : overwrite
        }

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin(f"{Ins.ffmpeg.agent_id}.ffmpeg_mute_video", args=args)
            try:
                return await Ins.ffmpeg.ffmpeg_mute_video(**args)
            finally:
                await idle.job_final(job_id)

        return await broadcast(
            tool="ffmpeg_mute_video",
            args=args,
            target_list=[Ins.ffmpeg],
            call=call,
            overrides=None
        )

    @mcp.tool(meta={"hidden": False, "domain": "media", "class": "ffmpeg"})
    @task_middleware("ffmpeg_probe_video")
    async def ffmpeg_probe_video(
        input_file: str
    ) -> CallToolResult:
        """
        D: media
        C: ffmpeg
        A: ffmpeg_probe_video
        P:
          input_file: str
        R: CTR
        N:
          - 读取媒体文件的基础探测信息。
          - 该工具只做探测，不会生成新文件。
          - 返回中会包含解析出的时长等信息，以及底层探测输出。
        """

        await Requires.connect_ffmpeg()

        args = {
            "input_file" : input_file
        }

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin(f"{Ins.ffmpeg.agent_id}.ffmpeg_probe_video", args=args)
            try:
                return await Ins.ffmpeg.ffmpeg_probe_video(**args)
            finally:
                await idle.job_final(job_id)

        return await broadcast(
            tool="ffmpeg_probe_video",
            args=args,
            target_list=[Ins.ffmpeg],
            call=call,
            overrides=None
        )

    @mcp.tool(meta={"hidden": False, "domain": "media", "class": "ffmpeg"})
    @task_middleware("ffmpeg_extract_audio")
    async def ffmpeg_extract_audio(
        input_video: str,
        output_dir: typing.Optional[str] = None,
        *,
        audio_format: typing.Literal["mp3", "aac", "wav", "m4a", "ogg", "flac"] = "mp3",
        audio_codec: typing.Optional[str] = None,
        overwrite: bool = True
    ) -> CallToolResult:
        """
        D: media
        C: ffmpeg
        A: ffmpeg_extract_audio
        P:
          input_video: str
          output_dir: str?=None
          audio_format: oneof(mp3|aac|wav|m4a|ogg|flac)="mp3"
          audio_codec: str?=None
          overwrite: bool=True
        R: CTR
        N:
          - 从视频或媒体文件中提取音轨。
          - 该工具只输出音频文件，不保留视频画面。
          - 输出格式由 `audio_format` 决定，编码器未显式指定时由 ffmpeg 自行选择。
        """

        await Requires.connect_ffmpeg()

        args = {
            "input_video"  : input_video,
            "output_dir"   : output_dir,
            "audio_format" : audio_format,
            "audio_codec"  : audio_codec,
            "overwrite"    : overwrite
        }

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin(f"{Ins.ffmpeg.agent_id}.ffmpeg_extract_audio", args=args)
            try:
                return await Ins.ffmpeg.ffmpeg_extract_audio(**args)
            finally:
                await idle.job_final(job_id)

        return await broadcast(
            tool="ffmpeg_extract_audio",
            args=args,
            target_list=[Ins.ffmpeg],
            call=call,
            overrides=None
        )

    @mcp.tool(meta={"hidden": False, "domain": "media", "class": "ffmpeg"})
    @task_middleware("ffmpeg_replace_audio")
    async def ffmpeg_replace_audio(
        input_video: str,
        input_audio: str,
        output_dir: typing.Optional[str] = None,
        *,
        keep_video: bool = True,
        audio_codec: str = "aac",
        output_format: typing.Literal["mp4", "mkv", "mov", "webm"] = "mp4",
        overwrite: bool = True
    ) -> CallToolResult:
        """
        D: media
        C: ffmpeg
        A: ffmpeg_replace_audio
        P:
          input_video: str
          input_audio: str
          output_dir: str?=None
          keep_video: bool=True
          audio_codec: str="aac"
          output_format: oneof(mp4|mkv|mov|webm)="mp4"
          overwrite: bool=True
        R: CTR
        N:
          - 用新的音频文件替换原视频中的音轨。
          - 默认按较短的音视频轨道输出结果，避免超过任一输入长度。
          - 是否保留原视频流不重编码，由 `keep_video` 决定。
        """

        await Requires.connect_ffmpeg()

        args = {
            "input_video"   : input_video,
            "input_audio"   : input_audio,
            "output_dir"    : output_dir,
            "keep_video"    : keep_video,
            "audio_codec"   : audio_codec,
            "output_format" : output_format,
            "overwrite"     : overwrite
        }

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin(f"{Ins.ffmpeg.agent_id}.ffmpeg_replace_audio", args=args)
            try:
                return await Ins.ffmpeg.ffmpeg_replace_audio(**args)
            finally:
                await idle.job_final(job_id)

        return await broadcast(
            tool="ffmpeg_replace_audio",
            args=args,
            target_list=[Ins.ffmpeg],
            call=call,
            overrides=None
        )

    @mcp.tool(meta={"hidden": False, "domain": "media", "class": "ffmpeg"})
    @task_middleware("ffmpeg_convert_audio")
    async def ffmpeg_convert_audio(
        input_file: str,
        output_dir: typing.Optional[str] = None,
        *,
        audio_codec: typing.Optional[str] = None,
        sample_rate: typing.Optional[int] = None,
        channels: typing.Optional[int] = None,
        bitrate: typing.Optional[str] = None,
        output_format: typing.Literal["mp3", "aac", "wav", "m4a", "ogg", "flac"] = "mp3",
        overwrite: bool = True
    ) -> CallToolResult:
        """
        D: media
        C: ffmpeg
        A: ffmpeg_convert_audio
        P:
          input_file: str
          output_dir: str?=None
          audio_codec: str?=None
          sample_rate: int?=None
          channels: int?=None
          bitrate: str?=None
          output_format: oneof(mp3|aac|wav|m4a|ogg|flac)="mp3"
          overwrite: bool=True
        R: CTR
        N:
          - 把输入媒体转换为目标音频文件。
          - 输入可以是音频或视频，但输出始终是音频，不保留视频画面。
          - 采样率、声道数、码率和编码器由参数控制，未指定时由 ffmpeg 或容器默认值决定。
        """

        await Requires.connect_ffmpeg()

        args = {
            "input_file"    : input_file,
            "output_dir"    : output_dir,
            "audio_codec"   : audio_codec,
            "sample_rate"   : sample_rate,
            "channels"      : channels,
            "bitrate"       : bitrate,
            "output_format" : output_format,
            "overwrite"     : overwrite
        }

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin(f"{Ins.ffmpeg.agent_id}.ffmpeg_convert_audio", args=args)
            try:
                return await Ins.ffmpeg.ffmpeg_convert_audio(**args)
            finally:
                await idle.job_final(job_id)

        return await broadcast(
            tool="ffmpeg_convert_audio",
            args=args,
            target_list=[Ins.ffmpeg],
            call=call,
            overrides=None
        )


if __name__ == '__main__':
    pass
