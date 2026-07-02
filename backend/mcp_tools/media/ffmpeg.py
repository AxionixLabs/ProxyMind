# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

from mcp.server import FastMCP
from mcp.types import CallToolResult
from backend.mcp_hub.hub_manage import Requires
from backend.middlewares.mid_task import task_middleware
from backend.mcp_tools.media.schemas.schema_ffmpeg import (
    VideoPathArg,
    MediaPathArg,
    AudioPathArg,
    OutputDirArg,
    OverwriteArg,
    AtSecArg,
    ImageFormatArg,
    PatternArg,
    OptionalFpsArg,
    StartSecArg,
    DurationSecArg,
    ScaleWArg,
    ScaleHArg,
    MaxFramesArg,
    UniformNArg,
    SceneThresholdArg,
    TrimStartArg,
    EndSecArg,
    TrimModeArg,
    VideoCodecArg,
    CrfArg,
    PresetArg,
    VideoOutputFormatArg,
    KeepAudioArg,
    TargetFpsArg,
    ListFileArg,
    ReencodeArg,
    AudioCodecArg,
    RequiredVideoFormatArg,
    ProbeInputArg,
    AudioFormatArg,
    OptionalAudioCodecArg,
    KeepVideoArg,
    SampleRateArg,
    ChannelsArg,
    BitrateArg
)
from backend.utilities.runtime import (
    AppContext, Idle
)
from backend.utilities.tool_result import build_tool_result


def bind(mcp: FastMCP, idle: Idle, ctx: AppContext) -> None:

    @mcp.tool(
        description=(
            "从视频中提取指定时间点的一张静态图片。"
            "该工具只输出单帧图片，不做批量抽帧或关键帧分析。"
            "输出文件路径由结果返回；`output_dir` 只决定落盘目录。"
        ),
        meta={"hidden": False, "domain": "media", "class": "ffmpeg"}
    )
    @task_middleware("ffmpeg_extract_snapshot")
    async def ffmpeg_extract_snapshot(
        input_video: VideoPathArg,
        output_dir: OutputDirArg = None,
        *,
        at_sec: AtSecArg = 0.0,
        image_format: ImageFormatArg = "png",
        overwrite: OverwriteArg = True
    ) -> CallToolResult:

        await Requires.connect_ffmpeg()

        args = {
            "input_video"  : input_video,
            "output_dir"   : output_dir,
            "at_sec"       : at_sec,
            "image_format" : image_format,
            "overwrite"    : overwrite
        }

        job_id = await idle.job_begin(f"{ctx.ffmpeg.agent_id}.ffmpeg_extract_snapshot", args=args)
        try:
            raw = await ctx.ffmpeg.ffmpeg_extract_snapshot(**args)
        finally:
            await idle.job_final(job_id)

        return build_tool_result(tool="ffmpeg_extract_snapshot", args=args, raw=raw, target=ctx.ffmpeg.agent_id)

    @mcp.tool(
        description=(
            "把视频导出为图片序列。"
            "支持按帧率抽帧、按时间窗口截取和按尺寸缩放。"
            "返回中只附带少量代表性附件；完整帧序列以落盘目录为准。"
        ),
        meta={"hidden": False, "domain": "media", "class": "ffmpeg"}
    )
    @task_middleware("ffmpeg_extract_frames")
    async def ffmpeg_extract_frames(
        input_video: VideoPathArg,
        output_dir: OutputDirArg = None,
        *,
        pattern: PatternArg = "frame_%06d.png",
        fps: OptionalFpsArg = None,
        image_format: ImageFormatArg = "png",
        start_sec: StartSecArg = None,
        duration_sec: DurationSecArg = None,
        scale_w: ScaleWArg = None,
        scale_h: ScaleHArg = None,
        overwrite: OverwriteArg = True
    ) -> CallToolResult:

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

        job_id = await idle.job_begin(f"{ctx.ffmpeg.agent_id}.ffmpeg_extract_frames", args=args)
        try:
            raw = await ctx.ffmpeg.ffmpeg_extract_frames(**args)
        finally:
            await idle.job_final(job_id)

        return build_tool_result(tool="ffmpeg_extract_frames", args=args, raw=raw, target=ctx.ffmpeg.agent_id)

    @mcp.tool(
        description=(
            "从视频中提取关键帧并输出为图片。"
            "返回的附件数量受 `max_frames` 约束，用于快速查看代表性画面。"
            "若需要完整图片序列，应改用 `ffmpeg_extract_frames`。"
        ),
        meta={"hidden": False, "domain": "media", "class": "ffmpeg"}
    )
    @task_middleware("ffmpeg_extract_keyframes")
    async def ffmpeg_extract_keyframes(
        input_video: VideoPathArg,
        output_dir: OutputDirArg = None,
        *,
        max_frames: MaxFramesArg = 12,
        uniform_n: UniformNArg = 6,
        image_format: ImageFormatArg = "png",
        overwrite: OverwriteArg = True
    ) -> CallToolResult:

        await Requires.connect_ffmpeg()

        args = {
            "input_video"  : input_video,
            "output_dir"   : output_dir,
            "max_frames"   : max_frames,
            "uniform_n"    : uniform_n,
            "image_format" : image_format,
            "overwrite"    : overwrite
        }

        job_id = await idle.job_begin(f"{ctx.ffmpeg.agent_id}.ffmpeg_extract_keyframes", args=args)
        try:
            raw = await ctx.ffmpeg.ffmpeg_extract_keyframes(**args)
        finally:
            await idle.job_final(job_id)

        return build_tool_result(tool="ffmpeg_extract_keyframes", args=args, raw=raw, target=ctx.ffmpeg.agent_id)

    @mcp.tool(
        description=(
            "按场景变化强度从视频中抽取代表性画面。"
            "`scene_th` 越大，命中的场景切换帧通常越少。"
            "`max_frames` 只限制返回中的附件数量，不限制实际落盘帧数。"
        ),
        meta={"hidden": False, "domain": "media", "class": "ffmpeg"}
    )
    @task_middleware("ffmpeg_extract_scene")
    async def ffmpeg_extract_scene(
        input_video: VideoPathArg,
        output_dir: OutputDirArg = None,
        *,
        scene_th: SceneThresholdArg = 0.35,
        max_frames: MaxFramesArg = 12,
        pattern: PatternArg = "scene_%06d",
        image_format: ImageFormatArg = "png",
        start_sec: StartSecArg = None,
        duration_sec: DurationSecArg = None,
        scale_w: ScaleWArg = 256,
        scale_h: ScaleHArg = None,
        overwrite: OverwriteArg = True
    ) -> CallToolResult:

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

        job_id = await idle.job_begin(f"{ctx.ffmpeg.agent_id}.ffmpeg_extract_scene", args=args)
        try:
            raw = await ctx.ffmpeg.ffmpeg_extract_scene(**args)
        finally:
            await idle.job_final(job_id)

        return build_tool_result(tool="ffmpeg_extract_scene", args=args, raw=raw, target=ctx.ffmpeg.agent_id)

    @mcp.tool(
        description=(
            "按时间范围裁剪视频。"
            "`mode=copy` 更快但裁剪精度受关键帧限制；`mode=reencode` 更慢但时间边界更精确。"
            "该工具只处理时间裁剪，不改变画面尺寸或帧率。"
        ),
        meta={"hidden": False, "domain": "media", "class": "ffmpeg"}
    )
    @task_middleware("ffmpeg_trim_video")
    async def ffmpeg_trim_video(
        input_video: VideoPathArg,
        output_dir: OutputDirArg = None,
        *,
        start_sec: TrimStartArg = 0.0,
        end_sec: EndSecArg = None,
        duration_sec: DurationSecArg = None,
        mode: TrimModeArg = "copy",
        video_codec: VideoCodecArg = "libx264",
        crf: CrfArg = 23,
        preset: PresetArg = "veryfast",
        output_format: VideoOutputFormatArg = None,
        overwrite: OverwriteArg = True
    ) -> CallToolResult:

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

        job_id = await idle.job_begin(f"{ctx.ffmpeg.agent_id}.ffmpeg_trim_video", args=args)
        try:
            raw = await ctx.ffmpeg.ffmpeg_trim_video(**args)
        finally:
            await idle.job_final(job_id)

        return build_tool_result(tool="ffmpeg_trim_video", args=args, raw=raw, target=ctx.ffmpeg.agent_id)

    @mcp.tool(
        description=(
            "把视频缩放到新的尺寸并输出新文件。"
            "至少应给出一个目标边；另一个边留空时会按比例自动计算。"
            "该工具会重编码视频；是否保留音频由 `keep_audio` 决定。"
        ),
        meta={"hidden": False, "domain": "media", "class": "ffmpeg"}
    )
    @task_middleware("ffmpeg_scale_video")
    async def ffmpeg_scale_video(
        input_video: VideoPathArg,
        output_dir: OutputDirArg = None,
        *,
        scale_w: ScaleWArg = None,
        scale_h: ScaleHArg = None,
        video_codec: VideoCodecArg = "libx264",
        crf: CrfArg = 23,
        preset: PresetArg = "veryfast",
        keep_audio: KeepAudioArg = True,
        output_format: VideoOutputFormatArg = None,
        overwrite: OverwriteArg = True,
    ) -> CallToolResult:

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

        job_id = await idle.job_begin(f"{ctx.ffmpeg.agent_id}.ffmpeg_scale_video", args=args)
        try:
            raw = await ctx.ffmpeg.ffmpeg_scale_video(**args)
        finally:
            await idle.job_final(job_id)

        return build_tool_result(tool="ffmpeg_scale_video", args=args, raw=raw, target=ctx.ffmpeg.agent_id)

    @mcp.tool(
        description=(
            "把视频重编码到新的目标帧率。"
            "该工具会重编码视频流，画质与体积主要受 `video_codec`、`crf` 和 `preset` 影响。"
            "是否保留音频由 `keep_audio` 决定。"
        ),
        meta={"hidden": False, "domain": "media", "class": "ffmpeg"}
    )
    @task_middleware("ffmpeg_convert_video")
    async def ffmpeg_convert_video(
        input_video: VideoPathArg,
        output_dir: OutputDirArg = None,
        *,
        fps: TargetFpsArg = 60,
        video_codec: VideoCodecArg = "libx264",
        crf: CrfArg = 23,
        preset: PresetArg = "veryfast",
        keep_audio: KeepAudioArg = True,
        output_format: VideoOutputFormatArg = None,
        overwrite: OverwriteArg = True
    ) -> CallToolResult:

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

        job_id = await idle.job_begin(f"{ctx.ffmpeg.agent_id}.ffmpeg_convert_video", args=args)
        try:
            raw = await ctx.ffmpeg.ffmpeg_convert_video(**args)
        finally:
            await idle.job_final(job_id)

        return build_tool_result(tool="ffmpeg_convert_video", args=args, raw=raw, target=ctx.ffmpeg.agent_id)

    @mcp.tool(
        description=(
            "按 `list_file` 中的顺序拼接多段视频。"
            "`reencode=False` 速度更快，但要求输入片段的编码参数足够一致；`reencode=True` 更稳。"
            "该工具只负责顺序拼接，不做自动对齐、补帧或内容理解。"
        ),
        meta={"hidden": False, "domain": "media", "class": "ffmpeg"}
    )
    @task_middleware("ffmpeg_concat_video")
    async def ffmpeg_concat_video(
        list_file: ListFileArg,
        output_dir: OutputDirArg = None,
        *,
        overwrite: OverwriteArg = True,
        reencode: ReencodeArg = False,
        video_codec: VideoCodecArg = "libx264",
        crf: CrfArg = 23,
        preset: PresetArg = "veryfast",
        audio_codec: AudioCodecArg = "aac",
        output_format: VideoOutputFormatArg = "mp4"
    ) -> CallToolResult:

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

        job_id = await idle.job_begin(f"{ctx.ffmpeg.agent_id}.ffmpeg_concat_video", args=args)
        try:
            raw = await ctx.ffmpeg.ffmpeg_concat_video(**args)
        finally:
            await idle.job_final(job_id)

        return build_tool_result(tool="ffmpeg_concat_video", args=args, raw=raw, target=ctx.ffmpeg.agent_id)

    @mcp.tool(
        description=(
            "仅更换媒体容器，不重编码音视频流。"
            "该工具适合在兼容的封装格式之间做快速 remux，不适合修复编码本身的问题。"
            "`output_format` 不传时会选择一个与输入不同的容器格式。"
        ),
        meta={"hidden": False, "domain": "media", "class": "ffmpeg"}
    )
    @task_middleware("ffmpeg_remux_video")
    async def ffmpeg_remux_video(
        input_video: VideoPathArg,
        output_dir: OutputDirArg = None,
        *,
        output_format: VideoOutputFormatArg = None,
        overwrite: OverwriteArg = True
    ) -> CallToolResult:

        await Requires.connect_ffmpeg()

        args = {
            "input_video"   : input_video,
            "output_dir"    : output_dir,
            "output_format" : output_format,
            "overwrite"     : overwrite
        }

        job_id = await idle.job_begin(f"{ctx.ffmpeg.agent_id}.ffmpeg_remux_video", args=args)
        try:
            raw = await ctx.ffmpeg.ffmpeg_remux_video(**args)
        finally:
            await idle.job_final(job_id)

        return build_tool_result(tool="ffmpeg_remux_video", args=args, raw=raw, target=ctx.ffmpeg.agent_id)

    @mcp.tool(
        description=(
            "移除视频中的音轨并输出静音视频。"
            "该工具保留原视频画面，不做重新剪辑或重新编码视频流。"
            "如果只想替换音轨，应改用 `ffmpeg_replace_audio`。"
        ),
        meta={"hidden": False, "domain": "media", "class": "ffmpeg"}
    )
    @task_middleware("ffmpeg_mute_video")
    async def ffmpeg_mute_video(
        input_video: VideoPathArg,
        output_dir: OutputDirArg = None,
        *,
        output_format: VideoOutputFormatArg = None,
        overwrite: OverwriteArg = True
    ) -> CallToolResult:

        await Requires.connect_ffmpeg()

        args = {
            "input_video"   : input_video,
            "output_dir"    : output_dir,
            "output_format" : output_format,
            "overwrite"     : overwrite
        }

        job_id = await idle.job_begin(f"{ctx.ffmpeg.agent_id}.ffmpeg_mute_video", args=args)
        try:
            raw = await ctx.ffmpeg.ffmpeg_mute_video(**args)
        finally:
            await idle.job_final(job_id)

        return build_tool_result(tool="ffmpeg_mute_video", args=args, raw=raw, target=ctx.ffmpeg.agent_id)

    @mcp.tool(
        description=(
            "读取媒体文件的基础探测信息。"
            "该工具只做探测，不会生成新文件。"
            "返回中会包含解析出的时长等信息，以及底层探测输出。"
        ),
        meta={"hidden": False, "domain": "media", "class": "ffmpeg"}
    )
    @task_middleware("ffmpeg_probe_video")
    async def ffmpeg_probe_video(
        input_file: ProbeInputArg
    ) -> CallToolResult:

        await Requires.connect_ffmpeg()

        args = {
            "input_file" : input_file
        }

        job_id = await idle.job_begin(f"{ctx.ffmpeg.agent_id}.ffmpeg_probe_video", args=args)
        try:
            raw = await ctx.ffmpeg.ffmpeg_probe_video(**args)
        finally:
            await idle.job_final(job_id)

        return build_tool_result(tool="ffmpeg_probe_video", args=args, raw=raw, target=ctx.ffmpeg.agent_id)

    @mcp.tool(
        description=(
            "从视频或媒体文件中提取音轨。"
            "该工具只输出音频文件，不保留视频画面。"
            "输出格式由 `audio_format` 决定，编码器未显式指定时由 ffmpeg 自行选择。"
        ),
        meta={"hidden": False, "domain": "media", "class": "ffmpeg"}
    )
    @task_middleware("ffmpeg_extract_audio")
    async def ffmpeg_extract_audio(
        input_video: VideoPathArg,
        output_dir: OutputDirArg = None,
        *,
        audio_format: AudioFormatArg = "mp3",
        audio_codec: OptionalAudioCodecArg = None,
        overwrite: OverwriteArg = True
    ) -> CallToolResult:

        await Requires.connect_ffmpeg()

        args = {
            "input_video"  : input_video,
            "output_dir"   : output_dir,
            "audio_format" : audio_format,
            "audio_codec"  : audio_codec,
            "overwrite"    : overwrite
        }

        job_id = await idle.job_begin(f"{ctx.ffmpeg.agent_id}.ffmpeg_extract_audio", args=args)
        try:
            raw = await ctx.ffmpeg.ffmpeg_extract_audio(**args)
        finally:
            await idle.job_final(job_id)

        return build_tool_result(tool="ffmpeg_extract_audio", args=args, raw=raw, target=ctx.ffmpeg.agent_id)

    @mcp.tool(
        description=(
            "用新的音频文件替换原视频中的音轨。"
            "默认按较短的音视频轨道输出结果，避免超过任一输入长度。"
            "是否保留原视频流不重编码，由 `keep_video` 决定。"
        ),
        meta={"hidden": False, "domain": "media", "class": "ffmpeg"}
    )
    @task_middleware("ffmpeg_replace_audio")
    async def ffmpeg_replace_audio(
        input_video: VideoPathArg,
        input_audio: AudioPathArg,
        output_dir: OutputDirArg = None,
        *,
        keep_video: KeepVideoArg = True,
        audio_codec: AudioCodecArg = "aac",
        output_format: RequiredVideoFormatArg = "mp4",
        overwrite: OverwriteArg = True
    ) -> CallToolResult:

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

        job_id = await idle.job_begin(f"{ctx.ffmpeg.agent_id}.ffmpeg_replace_audio", args=args)
        try:
            raw = await ctx.ffmpeg.ffmpeg_replace_audio(**args)
        finally:
            await idle.job_final(job_id)

        return build_tool_result(tool="ffmpeg_replace_audio", args=args, raw=raw, target=ctx.ffmpeg.agent_id)

    @mcp.tool(
        description=(
            "把输入媒体转换为目标音频文件。"
            "输入可以是音频或视频，但输出始终是音频，不保留视频画面。"
            "采样率、声道数、码率和编码器由参数控制，未指定时由 ffmpeg 或容器默认值决定。"
        ),
        meta={"hidden": False, "domain": "media", "class": "ffmpeg"}
    )
    @task_middleware("ffmpeg_convert_audio")
    async def ffmpeg_convert_audio(
        input_file: MediaPathArg,
        output_dir: OutputDirArg = None,
        *,
        audio_codec: OptionalAudioCodecArg = None,
        sample_rate: SampleRateArg = None,
        channels: ChannelsArg = None,
        bitrate: BitrateArg = None,
        output_format: AudioFormatArg = "mp3",
        overwrite: OverwriteArg = True
    ) -> CallToolResult:

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

        job_id = await idle.job_begin(f"{ctx.ffmpeg.agent_id}.ffmpeg_convert_audio", args=args)
        try:
            raw = await ctx.ffmpeg.ffmpeg_convert_audio(**args)
        finally:
            await idle.job_final(job_id)

        return build_tool_result(tool="ffmpeg_convert_audio", args=args, raw=raw, target=ctx.ffmpeg.agent_id)


if __name__ == '__main__':
    pass
