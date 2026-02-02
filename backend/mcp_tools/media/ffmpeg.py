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

    @mcp.tool()
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
        """Class: ffmpeg; Action: 拆帧; Args: input_video(str)=输入视频, output_dir(str|None)=输出目录, pattern(str)=文件名模板, fps(float|None)=抽帧帧率, image_format(jpg|png|webp)=图片格式, start_sec(float|None)=起始秒, duration_sec(float|None)=时长秒, scale_w/int|None=缩放宽, scale_h/int|None=缩放高, overwrite(bool)=覆盖输出; Use: 将视频导出为图片序列，支持抽帧/截取/缩放; Return: CallToolResult(text + structuredContent); Notes: 输入路径不可用会失败。"""
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
            tool="ffmpeg_extract_frames", args=args, target_list=[Ins.ffmpeg], call=call
        )

    @mcp.tool()
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
        """Class: ffmpeg; Action: 转换音频; Args: input_file(str)=输入音频/视频路径, output_file(str|None)=输出音频路径(扩展名决定容器如.mp3/.aac/.wav), audio_codec(str|None)=音频编码器(None自动选择), sample_rate(int|None)=采样率(如44100/48000), channels(int|None)=声道数(1/2), bitrate(str|None)=码率(如128k/192k), overwrite(bool=True)=覆盖输出; Use: 任意音频/视频→指定音频文件并可调整编码/采样率/声道/码率; Return: CallToolResult(text + structuredContent); Notes: 输出路径不可写或格式/编码不支持会失败。"""
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
            tool="ffmpeg_convert_audio", args=args, target_list=[Ins.ffmpeg], call=call
        )

    @mcp.tool()
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
        """Class: ffmpeg; Action: 转换帧率; Args: input_video(str)=输入视频路径, output_video(str|None)=输出视频路径, fps(float)=目标帧率(如30/60), video_codec(str='libx264')=视频编码器, crf(int=23)=画质(越小越清晰体积越大), preset(str='veryfast')=编码速度档(越快越省时但可能更大/更糙), keep_audio(bool=True)=是否保留音频(True拷贝音频/False去音频), overwrite(bool=True)=覆盖输出; Use: 通过重编码调整视频帧率; Return: CallToolResult(text + structuredContent); Notes: 会重编码视频，输出质量/体积主要受crf/preset影响。"""
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
            tool="ffmpeg_convert_video", args=args, target_list=[Ins.ffmpeg], call=call
        )

    @mcp.tool()
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
        """Class: ffmpeg; Action: 视频截取; Args: input_video(str)=输入视频路径, output_video(str|None)=输出视频路径, start_sec(float=0)=起始秒, end_sec(float|None)=结束秒(与duration_sec二选一), duration_sec(float|None)=截取时长秒(与end_sec二选一), mode('copy'|'reencode'='copy')=copy快但可能不帧级精确/reencode慢但精确, video_codec(str='libx264')=编码器(仅reencode), crf(int=23)=画质(仅reencode,越小越清晰), preset(str='veryfast')=速度档(仅reencode), overwrite(bool=True)=覆盖输出; Use: 按时间范围裁剪视频; Return: CallToolResult(text + structuredContent); Notes: end_sec与duration_sec二选一(都不填=从start_sec到结尾)。"""
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
            tool="ffmpeg_trim_video", args=args, target_list=[Ins.ffmpeg], call=call
        )

    @mcp.tool()
    @task_middleware("ffmpeg_remux_video")
    async def ffmpeg_remux_video(
        input_video: str,
        output_video: typing.Optional[str] = None,
        *,
        overwrite: bool = True
    ) -> CallToolResult:
        """Class: ffmpeg; Action: 视频换封装(remux); Args: input_video(str)=输入视频路径, output_video(str|None)=输出视频路径(生成新文件), overwrite(bool=True)=覆盖输出; Use: 仅换容器/封装不重编码（最快，mkv<->mp4 等）; Return: CallToolResult(text + structuredContent); Notes: 流/容器不兼容或输出不可写会失败。"""
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
            tool="ffmpeg_remux_video", args=args, target_list=[Ins.ffmpeg], call=call
        )

    @mcp.tool()
    @task_middleware("ffmpeg_extract_audio")
    async def ffmpeg_extract_audio(
        input_video: str,
        output_audio: typing.Optional[str] = None,
        *,
        audio_codec: typing.Optional[str] = None,
        overwrite: bool = True
    ) -> CallToolResult:
        """Class: ffmpeg; Action: 抽取音轨; Args: input_video(str)=输入媒体路径(常为视频), output_audio(str|None)=输出音频路径(生成新文件), audio_codec(str|None)=音频编码器(None=自选), overwrite(bool=True)=覆盖输出; Use: 从媒体文件输出音频文件（-vn，不输出视频）; Return: CallToolResult(text + structuredContent); Notes: 格式由 output_audio 扩展名决定。"""
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

    @mcp.tool()
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
        """Class: ffmpeg; Action: 替换视频音轨; Args: input_video(str)=输入视频路径, input_audio(str)=输入音频路径, output_video(str|None)=输出视频路径(生成新文件), keep_video(bool=True)=视频copy不重编码, audio_codec(str='aac')=输出音频编码器, overwrite(bool=True)=覆盖输出; Use: 保留画面替换为新音频（可做配音/换BGM）; Return: CallToolResult(text + structuredContent); Notes: 默认 -shortest 以较短轨道为准。"""
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
            tool="ffmpeg_replace_audio", args=args, target_list=[Ins.ffmpeg], call=call
        )

    @mcp.tool()
    @task_middleware("ffmpeg_video_snapshot")
    async def ffmpeg_video_snapshot(
        input_video: str,
        output_image: typing.Optional[str] = None,
        *,
        at_sec: float = 0.0,
        overwrite: bool = True
    ) -> CallToolResult:
        """Class: ffmpeg; Action: 视频取帧截图; Args: input_video(str)=输入视频路径, output_image(str|None)=输出图片路径(生成新文件), at_sec(float=0)=截图时间点(秒), overwrite(bool=True)=覆盖输出; Use: 从视频指定时间点导出单帧图片（封面/缩略图）; Return: CallToolResult(text + structuredContent); Notes: Action 为“视频取帧截图”以避免与 adb screenshot 混淆。"""
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
            tool="ffmpeg_video_snapshot", args=args, target_list=[Ins.ffmpeg], call=call
        )

    @mcp.tool()
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
        """Class: ffmpeg; Action: 视频拼接(concat); Args: list_file(str)=concat列表文件路径, output_video(str|None)=输出视频路径(生成新文件), overwrite(bool=True)=覆盖输出, reencode(bool=False)=是否重编码拼接, video_codec(str='libx264')=重编码视频编码器, crf(int=23)=重编码质量, preset(str='veryfast')=重编码速度, audio_codec(str='aac')=重编码音频编码器; Use: 按 list_file 顺序合并片段；reencode=False 最快但要求片段参数一致; Return: CallToolResult(text + structuredContent); Notes: list_file 每行格式：file '/abs/path/x.mp4'。"""
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
            tool="ffmpeg_concat_video", args=args, target_list=[Ins.ffmpeg], call=call
        )

    @mcp.tool()
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
        """Class: ffmpeg; Action: 视频缩放转码(scale+encode); Args: input_video(str)=输入视频路径, output_video(str|None)=输出视频路径, scale_w(int|None)=目标宽(None=不指定/等比用-1), scale_h(int|None)=目标高(None=不指定/等比用-1), video_codec(str='libx264')=视频编码器, crf(int=23)=画质, preset(str='veryfast')=速度档位, keep_audio(bool=True)=保留音频(copy), overwrite(bool=True)=覆盖输出; Use: 输出新视频并缩放分辨率（常用于压体积/统一规格）; Return: CallToolResult(text + structuredContent); Notes: 会重编码视频；crf/preset 影响质量与速度。"""
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
            tool="ffmpeg_scale_video", args=args, target_list=[Ins.ffmpeg], call=call
        )

    @mcp.tool()
    @task_middleware("ffmpeg_mute_video")
    async def ffmpeg_mute_video(
        input_video: str,
        output_video: typing.Optional[str] = None,
        *,
        overwrite: bool = True
    ) -> CallToolResult:
        """Class: ffmpeg; Action: 视频去音轨(mute/remove-audio); Args: input_video(str)=输入视频路径, output_video(str|None)=输出视频路径, overwrite(bool=True)=覆盖输出; Use: 输出静音视频（视频流 copy 不重编码，速度最快）; Return: CallToolResult(text + structuredContent); Notes: 仅移除音轨；不会改变画面质量/编码。"""
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
            tool="ffmpeg_mute_video", args=args, target_list=[Ins.ffmpeg], call=call
        )

    @mcp.tool()
    @task_middleware("ffmpeg_probe_video")
    async def ffmpeg_probe_video(input_file: str) -> CallToolResult:
        """Class: ffmpeg; Action: 探测信息; Args: input_file(str)=媒体文件路径; Use: ffmpeg -i 快速打印媒体信息（编码/时长/分辨率/音轨等）; Return: CallToolResult(text + structuredContent); Notes: 仅探测不生成输出文件。"""
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
            tool="ffmpeg_probe_video", args=args, target_list=[Ins.ffmpeg], call=call
        )


if __name__ == '__main__':
    pass
