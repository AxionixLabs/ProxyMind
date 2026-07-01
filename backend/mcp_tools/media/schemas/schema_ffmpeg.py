# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from pydantic import Field

VideoPathArg = typing.Annotated[
    str,
    Field(description="输入视频文件路径。")
]
MediaPathArg = typing.Annotated[
    str,
    Field(description="输入媒体文件路径，可以是音频或视频。")
]
AudioPathArg = typing.Annotated[
    str,
    Field(description="输入音频文件路径。")
]
OutputDirArg = typing.Annotated[
    typing.Optional[str],
    Field(description="输出目录；为空时由底层工具按默认规则选择落盘位置。")
]
OverwriteArg = typing.Annotated[
    bool,
    Field(description="目标文件已存在时是否允许覆盖。")
]
AtSecArg = typing.Annotated[
    float,
    Field(description="提取单帧时使用的时间点，单位秒。")
]
ImageFormatArg = typing.Annotated[
    typing.Literal["jpg", "png", "webp"],
    Field(description="输出图片格式。")
]
PatternArg = typing.Annotated[
    str,
    Field(description="批量输出文件名模式，如 `frame_%06d.png`。")
]
OptionalFpsArg = typing.Annotated[
    typing.Optional[float],
    Field(description="按指定帧率抽帧或重编码；为空时保持默认行为。")
]
StartSecArg = typing.Annotated[
    typing.Optional[float],
    Field(description="处理窗口起始时间，单位秒。")
]
DurationSecArg = typing.Annotated[
    typing.Optional[float],
    Field(description="处理窗口持续时间，单位秒。")
]
ScaleWArg = typing.Annotated[
    typing.Optional[int],
    Field(description="目标宽度；为空时由另一边或原始比例推导。")
]
ScaleHArg = typing.Annotated[
    typing.Optional[int],
    Field(description="目标高度；为空时由另一边或原始比例推导。")
]
MaxFramesArg = typing.Annotated[
    int,
    Field(description="返回或保留的代表性帧数量上限。")
]
UniformNArg = typing.Annotated[
    int,
    Field(description="关键帧结果中均匀抽样保留的目标数量。")
]
SceneThresholdArg = typing.Annotated[
    float,
    Field(description="场景变化阈值；值越大，命中的切换帧通常越少。")
]
TrimStartArg = typing.Annotated[
    float,
    Field(description="裁剪起始时间，单位秒。")
]
EndSecArg = typing.Annotated[
    typing.Optional[float],
    Field(description="裁剪结束时间，单位秒。")
]
TrimModeArg = typing.Annotated[
    typing.Literal["copy", "reencode"],
    Field(description="时间裁剪模式；`copy` 更快，`reencode` 更精确。")
]
VideoCodecArg = typing.Annotated[
    str,
    Field(description="视频编码器名称，如 `libx264`。")
]
CrfArg = typing.Annotated[
    int,
    Field(description="视频质量参数；通常值越低画质越高、体积越大。")
]
PresetArg = typing.Annotated[
    str,
    Field(description="编码速度预设，如 `veryfast`。")
]
VideoOutputFormatArg = typing.Annotated[
    typing.Optional[typing.Literal["mp4", "mkv", "mov", "webm"]],
    Field(description="输出视频容器格式；为空时按工具默认规则选择。")
]
KeepAudioArg = typing.Annotated[
    bool,
    Field(description="重编码或缩放视频时是否保留音轨。")
]
TargetFpsArg = typing.Annotated[
    float,
    Field(description="目标视频帧率。")
]
ListFileArg = typing.Annotated[
    str,
    Field(description="concat 清单文件路径，文件内按顺序列出待拼接片段。")
]
ReencodeArg = typing.Annotated[
    bool,
    Field(description="拼接时是否统一重编码；为 false 时尝试直接拼接原始流。")
]
AudioCodecArg = typing.Annotated[
    str,
    Field(description="音频编码器名称，如 `aac`。")
]
RequiredVideoFormatArg = typing.Annotated[
    typing.Literal["mp4", "mkv", "mov", "webm"],
    Field(description="输出视频容器格式。")
]
ProbeInputArg = typing.Annotated[
    str,
    Field(description="要探测的媒体文件路径。")
]
AudioFormatArg = typing.Annotated[
    typing.Literal["mp3", "aac", "wav", "m4a", "ogg", "flac"],
    Field(description="输出音频格式。")
]
OptionalAudioCodecArg = typing.Annotated[
    typing.Optional[str],
    Field(description="音频编码器名称；为空时由 ffmpeg 或容器默认值决定。")
]
KeepVideoArg = typing.Annotated[
    bool,
    Field(description="替换音轨时是否尽量保留原视频流不重编码。")
]
SampleRateArg = typing.Annotated[
    typing.Optional[int],
    Field(description="目标采样率，例如 44100 或 48000。")
]
ChannelsArg = typing.Annotated[
    typing.Optional[int],
    Field(description="目标声道数，例如 1 或 2。")
]
BitrateArg = typing.Annotated[
    typing.Optional[str],
    Field(description="目标音频码率，如 `128k`。")
]


if __name__ == '__main__':
    pass
