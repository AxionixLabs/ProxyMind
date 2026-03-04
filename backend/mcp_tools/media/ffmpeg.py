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
          output_dir: str?=None        # 可选，输出目录；None 时由引擎选择默认目录
          at_sec: float=0.0
          image_format: oneof(jpg|png|webp)="png"
          overwrite: bool=True
        R: CTR
        N:
          - 视频取帧：从指定时间点导出单帧图片（封面/缩略图）
          - 输出文件名/最终路径由引擎生成并在结果中返回（attachments/data）
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
          - 视频导出为图片序列：支持抽帧(fps)/截取(start+duration)/缩放(scale_w/scale_h)
          - output_dir=None 时由引擎选择默认目录；完整输出模板/目录在 data.output_tpl/data.output_dir
          - 返回仅附带少量帧附件（控大小），全量帧在 output_dir
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
          - 抽关键帧：按时长均匀采样 -> 合并去重 -> 截断到 max_frames
          - output_dir=None 时由引擎选择默认目录；输出目录/文件列表在 data.output_dir/data.files
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
          - 场景变化抽帧：按画面变化 select='gt(scene,th)' 选帧并输出图片序列（th 越大越少帧）
          - max_frames 仅限制返回/attachments 数量，不影响实际写盘帧数；输出目录/文件列表在 data.output_dir/data.files
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
          - 按时间范围裁剪视频：end_sec 与 duration_sec 二选一（都不填=从 start_sec 到结尾）
          - mode=copy 不重编码（快但仅关键帧附近精确）；mode=reencode 重编码更精确（video_codec/crf/preset 生效）
          - output_format=None 时沿用输入容器后缀；输出文件/目录在 data.output_file/data.output_dir
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
          - 缩放并输出新视频（重编码）：scale_w/scale_h 任一为 None 时用 -1 等比
          - keep_audio=True 保留音频（copy）；False 去音轨
          - output_format=None 时沿用输入容器后缀；输出文件/目录在 data.output_file/data.output_dir
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
          - 重编码转换视频帧率；画质/体积主要受 crf 与 preset 影响
          - keep_audio=True 保留音频（copy）；False 去音轨
          - output_format=None 时沿用输入容器后缀；输出文件/目录在 data.output_file/data.output_dir
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
          - concat demuxer 拼接：list_file 每行 `file '/abs/path/x.mp4'`
          - reencode=False 走 -c copy（要求片段参数一致，最快）；reencode=True 重编码更稳（video/audio 编码参数生效）
          - 输出到 output_dir 下独立目录以避免并发覆盖；输出文件/目录在 data.output_file/data.output_dir
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
          - 仅换容器/封装（-c copy，不重编码，最快），如 mkv <-> mp4
          - output_format=None 时按输入后缀自动选择“不同的”容器
          - 输出到 output_dir 下独立目录以避免并发覆盖；输出文件/目录在 data.output_file/data.output_dir
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
          - 去音轨：输出静音视频（-c:v copy + -an，不重编码，最快）
          - output_format=None 时沿用输入容器后缀（无后缀时兜底 mp4）
          - 输出到 output_dir 下独立目录以避免并发覆盖；输出文件/目录在 data.output_file/data.output_dir
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
          - 仅探测不输出文件：等价 `ffmpeg -i <input_file>`
          - 无论成功/异常均返回多模态结构
          - duration_sec 会从输出中解析（data.duration_sec），raw 为完整输出
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
          - 抽取音轨：从媒体文件导出音频（-vn，不输出视频）
          - 输出文件名/后缀由内部生成（audio_format 决定后缀）；audio_codec=None 时由 ffmpeg 自选
          - output_dir=None 时由引擎选择默认目录；输出文件/目录在 data.output_file/data.output_dir
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
          - 保留画面替换为新音频（配音/换 BGM）；默认以较短轨道为准（-shortest）
          - keep_video=True 视频流 copy 不重编码；False 时由 ffmpeg 自行处理（通常会重编码）
          - 输出文件名/后缀由内部生成（output_format 决定后缀）；输出文件/目录在 data.output_file/data.output_dir
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
          - 任意音频/视频 → 音频文件（-vn，不输出视频）
          - 输出文件名/后缀由内部生成（output_format 决定后缀）；audio_codec=None 时由 ffmpeg 自选
          - output_dir=None 时由引擎选择默认目录；输出文件/目录在 data.output_file/data.output_dir
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
