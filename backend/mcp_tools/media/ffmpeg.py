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
    @task_middleware("ffmpeg_extract_frames")
    async def ffmpeg_extract_frames(
        input_video: str,
        output_dir: typing.Optional[str] = None,
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
          pattern: str="frame_%06d.png"
          fps: float?=None
          image_format: oneof(jpg|png|webp)="png"
          start_sec: float?=None
          duration_sec: float?=None
          scale_w: int?=None
          scale_h: int?=None
          overwrite: bool=True
        R: CTR
        N:
          - 视频导出为图片序列：支持抽帧(fps)/截取(start+duration)/缩放(scale_w/scale_h)
          - 输入路径不可用/输出不可写/编码不支持会失败
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
    @task_middleware("ffmpeg_convert_audio")
    async def ffmpeg_convert_audio(
        input_file: str,
        output_file: typing.Optional[str] = None,
        audio_codec: typing.Optional[str] = None,
        sample_rate: typing.Optional[int] = None,
        channels: typing.Optional[int] = None,
        bitrate: typing.Optional[str] = None,
        overwrite: bool = True
    ) -> CallToolResult:
        """
        D: media
        C: ffmpeg
        A: ffmpeg_convert_audio
        P:
          input_file: str
          output_file: str?=None
          audio_codec: str?=None
          sample_rate: int?=None
          channels: int?=None
          bitrate: str?=None
          overwrite: bool=True
        R: CTR
        N:
          - 任意音频/视频 → 音频文件（容器由 output_file 扩展名决定）
          - 可选调整编码器/采样率/声道/码率；输出不可写/格式或编码不支持会失败
        """

        await Requires.connect_ffmpeg()

        args = {
            "input_file"  : input_file,
            "output_file" : output_file,
            "audio_codec" : audio_codec,
            "sample_rate" : sample_rate,
            "channels"    : channels,
            "bitrate"     : bitrate,
            "overwrite"   : overwrite
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

    @mcp.tool(meta={"hidden": False, "domain": "media", "class": "ffmpeg"})
    @task_middleware("ffmpeg_convert_video")
    async def ffmpeg_convert_video(
        input_video: str,
        output_video: typing.Optional[str] = None,
        fps: float = 60,
        video_codec: str = "libx264",
        crf: int = 23,
        preset: str = "veryfast",
        keep_audio: bool = True,
        overwrite: bool = True
    ) -> CallToolResult:
        """
        D: media
        C: ffmpeg
        A: ffmpeg_convert_video
        P:
          input_video: str
          output_video: str?=None
          fps: float=60
          video_codec: str="libx264"
          crf: int=23
          preset: str="veryfast"
          keep_audio: bool=True
          overwrite: bool=True
        R: CTR
        N:
          - 重编码转换视频帧率；画质/体积主要受 crf 与 preset 影响
          - keep_audio=True 保留音频；False 去音频；输出不可写/编码不支持会失败
        """

        await Requires.connect_ffmpeg()

        args = {
            "input_video"  : input_video,
            "output_video" : output_video,
            "fps"          : fps,
            "video_codec"  : video_codec,
            "crf"          : crf,
            "preset"       : preset,
            "keep_audio"   : keep_audio,
            "overwrite"    : overwrite
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
    @task_middleware("ffmpeg_trim_video")
    async def ffmpeg_trim_video(
        input_video: str,
        output_video: typing.Optional[str] = None,
        start_sec: float = 0.0,
        end_sec: typing.Optional[float] = None,
        duration_sec: typing.Optional[float] = None,
        mode: typing.Literal["copy", "reencode"] = "copy",
        video_codec: str = "libx264",
        crf: int = 23,
        preset: str = "veryfast",
        overwrite: bool = True
    ) -> CallToolResult:
        """
        D: media
        C: ffmpeg
        A: ffmpeg_trim_video
        P:
          input_video: str
          output_video: str?=None
          start_sec: float=0.0
          end_sec: float?=None
          duration_sec: float?=None
          mode: oneof(copy|reencode)="copy"
          video_codec: str="libx264"
          crf: int=23
          preset: str="veryfast"
          overwrite: bool=True
        R: CTR
        N:
          - 按时间范围裁剪视频；end_sec 与 duration_sec 二选一（都不填=从 start_sec 到结尾）
          - mode=copy 速度最快但可能不帧级精确；mode=reencode 更精确但较慢（crf/preset 生效）
        """

        await Requires.connect_ffmpeg()

        args = {
            "input_video"  : input_video,
            "output_video" : output_video,
            "start_sec"    : start_sec,
            "end_sec"      : end_sec,
            "duration_sec" : duration_sec,
            "mode"         : mode,
            "video_codec"  : video_codec,
            "crf"          : crf,
            "preset"       : preset,
            "overwrite"    : overwrite
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
    @task_middleware("ffmpeg_remux_video")
    async def ffmpeg_remux_video(
        input_video: str,
        output_video: typing.Optional[str] = None,
        *,
        overwrite: bool = True
    ) -> CallToolResult:
        """
        D: media
        C: ffmpeg
        A: ffmpeg_remux_video
        P:
          input_video: str
          output_video: str?=None
          overwrite: bool=True
        R: CTR
        N:
          - 仅换容器/封装（不重编码，最快），如 mkv <-> mp4
          - 流/容器不兼容或输出不可写会失败
        """

        await Requires.connect_ffmpeg()

        args = {
            "input_video"  : input_video,
            "output_video" : output_video,
            "overwrite"    : overwrite
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
    @task_middleware("ffmpeg_extract_audio")
    async def ffmpeg_extract_audio(
        input_video: str,
        output_audio: typing.Optional[str] = None,
        *,
        audio_codec: typing.Optional[str] = None,
        overwrite: bool = True
    ) -> CallToolResult:
        """
        D: media
        C: ffmpeg
        A: ffmpeg_extract_audio
        P:
          input_video: str
          output_audio: str?=None
          audio_codec: str?=None
          overwrite: bool=True
        R: CTR
        N:
          - 从媒体文件抽取/导出音频（-vn，不输出视频）
          - 输出格式由 output_audio 扩展名决定；输出不可写/编码不支持会失败
        """

        await Requires.connect_ffmpeg()

        args = {
            "input_video"  : input_video,
            "output_audio" : output_audio,
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
            tool="ffmpeg_extract_audio", args=args, target_list=[Ins.ffmpeg], call=call
        )

    @mcp.tool(meta={"hidden": False, "domain": "media", "class": "ffmpeg"})
    @task_middleware("ffmpeg_replace_audio")
    async def ffmpeg_replace_audio(
        input_video: str,
        input_audio: str,
        output_video: typing.Optional[str] = None,
        *,
        keep_video: bool = True,
        audio_codec: str = "aac",
        overwrite: bool = True
    ) -> CallToolResult:
        """
        D: media
        C: ffmpeg
        A: ffmpeg_replace_audio
        P:
          input_video: str
          input_audio: str
          output_video: str?=None
          keep_video: bool=True
          audio_codec: str="aac"
          overwrite: bool=True
        R: CTR
        N:
          - 保留画面替换为新音频（配音/换 BGM）；默认以较短轨道为准（-shortest）
          - keep_video=True 视频流 copy 不重编码；False 时可能重编码视频（依实现）
        """

        await Requires.connect_ffmpeg()

        args = {
            "input_video"  : input_video,
            "input_audio"  : input_audio,
            "output_video" : output_video,
            "keep_video"   : keep_video,
            "audio_codec"  : audio_codec,
            "overwrite"    : overwrite
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
    @task_middleware("ffmpeg_video_snapshot")
    async def ffmpeg_video_snapshot(
        input_video: str,
        output_image: typing.Optional[str] = None,
        *,
        at_sec: float = 0.0,
        overwrite: bool = True
    ) -> CallToolResult:
        """
        D: media
        C: ffmpeg
        A: ffmpeg_video_snapshot
        P:
          input_video: str
          output_image: str?=None
          at_sec: float=0.0
          overwrite: bool=True
        R: CTR
        N:
          - 从视频指定时间点导出单帧图片（封面/缩略图）
          - 这里的 ffmpeg_video_snapshot 指“视频取帧”，避免与 adb screenshot 混淆
        """

        await Requires.connect_ffmpeg()

        args = {
            "input_video"  : input_video,
            "output_image" : output_image,
            "at_sec"       : at_sec,
            "overwrite"    : overwrite
        }

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin(f"{Ins.ffmpeg.agent_id}.ffmpeg_video_snapshot", args=args)
            try:
                return await Ins.ffmpeg.ffmpeg_video_snapshot(**args)
            finally:
                await idle.job_final(job_id)

        return await broadcast(
            tool="ffmpeg_video_snapshot",
            args=args,
            target_list=[Ins.ffmpeg],
            call=call,
            overrides=None
        )

    @mcp.tool(meta={"hidden": False, "domain": "media", "class": "ffmpeg"})
    @task_middleware("ffmpeg_concat_video")
    async def ffmpeg_concat_video(
        list_file: str,
        output_video: typing.Optional[str] = None,
        *,
        overwrite: bool = True,
        reencode: bool = False,
        video_codec: str = "libx264",
        crf: int = 23,
        preset: str = "veryfast",
        audio_codec: str = "aac"
    ) -> CallToolResult:
        """
        D: media
        C: ffmpeg
        A: ffmpeg_concat_video
        P:
          list_file: str
          output_video: str?=None
          overwrite: bool=True
          reencode: bool=False
          video_codec: str="libx264"
          crf: int=23
          preset: str="veryfast"
          audio_codec: str="aac"
        R: CTR
        N:
          - 按 list_file 顺序拼接片段；reencode=False 最快但要求片段参数一致
          - list_file 每行格式：file '/abs/path/x.mp4'
        """

        await Requires.connect_ffmpeg()

        args = {
            "list_file"    : list_file,
            "output_video" : output_video,
            "overwrite"    : overwrite,
            "reencode"     : reencode,
            "video_codec"  : video_codec,
            "crf"          : crf,
            "preset"       : preset,
            "audio_codec"  : audio_codec
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
    @task_middleware("ffmpeg_scale_video")
    async def ffmpeg_scale_video(
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
    ) -> CallToolResult:
        """
        D: media
        C: ffmpeg
        A: ffmpeg_scale_video
        P:
          input_video: str
          output_video: str?=None
          scale_w: int?=None
          scale_h: int?=None
          video_codec: str="libx264"
          crf: int=23
          preset: str="veryfast"
          keep_audio: bool=True
          overwrite: bool=True
        R: CTR
        N:
          - 输出新视频并缩放分辨率（常用于压体积/统一规格）；会重编码视频（crf/preset 影响质量/速度）
          - keep_audio=True 保留音频（copy）；False 时去音频或重编码音频（依实现）
        """

        await Requires.connect_ffmpeg()

        args = {
            "input_video"  : input_video,
            "output_video" : output_video,
            "scale_w"      : scale_w,
            "scale_h"      : scale_h,
            "video_codec"  : video_codec,
            "crf"          : crf,
            "preset"       : preset,
            "keep_audio"   : keep_audio,
            "overwrite"    : overwrite
        }

        async def call(*_) -> None:
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
    @task_middleware("ffmpeg_mute_video")
    async def ffmpeg_mute_video(
        input_video: str,
        output_video: typing.Optional[str] = None,
        *,
        overwrite: bool = True
    ) -> CallToolResult:
        """
        D: media
        C: ffmpeg
        A: ffmpeg_mute_video
        P:
          input_video: str
          output_video: str?=None
          overwrite: bool=True
        R: CTR
        N:
          - 输出静音视频：仅移除音轨（视频流 copy 不重编码，最快）
          - 不改变画面质量/编码参数
        """

        await Requires.connect_ffmpeg()

        args = {
            "input_video"  : input_video,
            "output_video" : output_video,
            "overwrite"    : overwrite
        }

        async def call(*_) -> None:
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
    async def ffmpeg_probe_video(input_file: str) -> CallToolResult:
        """
        D: media
        C: ffmpeg
        A: ffmpeg_probe_video
        P:
          input_file: str
        R: CTR
        N:
          - 探测媒体信息（编码/时长/分辨率/音轨等），仅探测不生成输出文件
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


if __name__ == '__main__':
    pass
