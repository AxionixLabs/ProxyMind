# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass

from agent.domain.policies import PermissionSettings


class DesktopError(RuntimeError):
    """携带桌面能力的失败分类，不推断动作是否已执行。"""

    def __init__(self, code: str, detail: str) -> None:
        """保存经过边界校验的错误码和说明。"""
        self.code = code
        self.detail = detail
        super().__init__(detail)


@dataclass(frozen=True, slots=True)
class DesktopBounds:
    """描述声明坐标系中的矩形。"""

    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True, slots=True)
class DesktopCapabilities:
    """描述当前原生进程实际支持的操作和坐标单位。"""

    platform: str
    actions: tuple[str, ...]
    coordinate_space: str
    capture_supported: bool
    input_supported: bool


@dataclass(frozen=True, slots=True)
class DesktopWindow:
    """描述会话内不透明窗口引用及其观察时状态。"""

    reference: str
    title: str
    pid: int
    bounds: DesktopBounds
    display_scale: float
    minimized: bool


@dataclass(frozen=True, slots=True)
class DesktopWindows:
    """描述有界窗口枚举结果及截断事实。"""

    windows: tuple[DesktopWindow, ...]
    truncated: bool


@dataclass(frozen=True, slots=True)
class DesktopControl:
    """描述单次观察中的控件及相对目标窗口的位置。"""

    index: int
    parent: int
    name: str
    role: str
    automation_id: str
    enabled: bool
    offscreen: bool
    password: bool
    bounds: DesktopBounds | None


@dataclass(frozen=True, slots=True)
class DesktopImage:
    """持有通过协议边界校验的图片格式、编码数据及像素尺寸。"""

    width: int
    height: int
    content: bytes
    mime_type: typing.Literal["image/png", "image/jpeg"]


@dataclass(frozen=True, slots=True)
class DesktopObservation:
    """描述同一次窗口观察的控件树、截图和不透明观察引用。"""

    window: str
    reference: str
    captured_at: str
    bounds: DesktopBounds
    display_scale: float
    coordinate_space: str
    controls: tuple[DesktopControl, ...]
    tree_truncated: bool
    tree_error: str | None
    image: DesktopImage | None


class DesktopSessionPort(typing.Protocol):
    """定义一个原生桌面进程的观察端口。

    Harness 创建并关闭实例，负责会话调度；实现方隔离平台与私有 IPC，校验所有外部
    结果。引用仅属于本实例，关闭、超时或通信失败后不得继续使用，不自动重放请求。
    """

    @property
    def capabilities(self) -> DesktopCapabilities:
        """返回握手确认的实际能力。"""
        ...

    async def list_windows(self) -> DesktopWindows:
        """枚举当前可定位的窗口。"""
        ...

    async def observe(self, window: str, *, screenshot: bool) -> DesktopObservation:
        """读取窗口真实状态，明确返回控件树不可用与截断事实。"""
        ...

    async def validate_observation(self, window: str, observation: str) -> None:
        """验证会话内最新观察的时效与窗口状态；成功不表示授权或承诺后续状态不变。"""
        ...

    async def close(self) -> None:
        """幂等关闭原生进程并回收传输资源。"""
        ...

    async def activate(self, window: str) -> "DesktopInputResult":
        """激活已枚举的窗口，验证真实前台状态，不隐式生成按键。"""
        ...

    async def input(self, command: "DesktopInput") -> "DesktopInputResult":
        """执行绑定最新观察的一次输入，原生层校验目标并在中断时释放按下状态。"""
        ...


@dataclass(frozen=True, slots=True)
class DesktopScope:
    """冻结桌面引用所属的会话、工作目录和权限信封，任一变化均重建原生会话。"""

    session_id: str
    cwd: str
    permissions: PermissionSettings


@dataclass(frozen=True, slots=True)
class ListDesktopWindows:
    """请求枚举当前可定位窗口。"""


@dataclass(frozen=True, slots=True)
class ObserveDesktopWindow:
    """请求指定窗口的控件树和可选截图。"""

    window: str
    screenshot: bool


@dataclass(frozen=True, slots=True)
class ValidateDesktopObservation:
    """请求校验本会话已交付观察的有效性。"""

    window: str
    observation: str


@dataclass(frozen=True, slots=True)
class DesktopPoint:
    """描述本次观察截图内的像素坐标，转换由原生观察所有者完成。"""

    x: int
    y: int


@dataclass(frozen=True, slots=True)
class DesktopClick:
    """点击本次观察中的控件索引或截图坐标。"""

    target: DesktopPoint | int
    button: typing.Literal["left", "right", "middle"]
    count: int


@dataclass(frozen=True, slots=True)
class DesktopMove:
    """把指针移动到截图坐标。"""

    target: DesktopPoint
    duration_ms: int


@dataclass(frozen=True, slots=True)
class DesktopDrag:
    """在目标窗口内按住左键拖拽，终止时释放左键。"""

    start: DesktopPoint
    end: DesktopPoint
    duration_ms: int


@dataclass(frozen=True, slots=True)
class DesktopScroll:
    """在截图坐标滚动，正数表示向上或向右的滚轮格数。"""

    target: DesktopPoint
    delta_x: int
    delta_y: int


@dataclass(frozen=True, slots=True)
class DesktopKey:
    """按下并释放一个命名按键或组合键，不跨请求持有键状态。"""

    keys: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DesktopText:
    """向已获焦点的目标窗口发送有界 Unicode 文本，不使用剪贴板。"""

    text: str


DesktopOperation: typing.TypeAlias = DesktopClick | DesktopMove | DesktopDrag | DesktopScroll | DesktopKey | DesktopText


@dataclass(frozen=True, slots=True)
class DesktopInput:
    """绑定窗口、最新观察和单次输入；无论结果如何都不得重用原观察执行下一次输入。"""

    window: str
    observation: str
    operation: DesktopOperation


@dataclass(frozen=True, slots=True)
class ActivateDesktopWindow:
    """请求显式激活一个已枚举窗口。"""

    window: str


@dataclass(frozen=True, slots=True)
class DesktopInputResult:
    """说明输入已提交系统，不声称目标应用已经完成预期业务行为。"""

    events_submitted: int
    requires_observation: bool


DesktopCommand: typing.TypeAlias = ListDesktopWindows | ObserveDesktopWindow | ValidateDesktopObservation | ActivateDesktopWindow | DesktopInput
DesktopResult: typing.TypeAlias = DesktopWindows | DesktopObservation | DesktopInputResult | None


class DesktopExecutionPort(typing.Protocol):
    """定义 Application 调用已授权观察的端口，由 Harness 隔离会话并串行调度。

    实现方不得保存调用级授权或隐式重放失败请求；reset 只关闭指定桌面会话。
    """

    async def execute(self, scope: DesktopScope, command: DesktopCommand) -> DesktopResult:
        """在指定冻结信封内执行已校验的观察命令。"""
        ...

    async def reset_session(self, session_id: str) -> None:
        """关闭指定桌面会话，使它的全部窗口与观察引用失效。"""
        ...


class DesktopSessionLifecyclePort(typing.Protocol):
    """供 Harness 会话结束和进程退出回收桌面资源，不提供执行入口。"""

    async def close_session(self, session_id: str) -> None:
        """取消活动观察并幂等回收指定会话，禁止排队请求在关闭后启动。"""
        ...

    async def close(self) -> None:
        """停止接收请求并回收全部原生会话，调用方取消等待不取消回收。"""
        ...


class DesktopSessionFactory(typing.Protocol):
    """由组合根注入的原生会话工厂，Infrastructure 实现平台和资产选择。

    只在授权后的首次调用创建进程；启动失败或取消必须回收尚未交付的资源。
    """

    async def __call__(self) -> DesktopSessionPort:
        """创建一个独立原生会话并校验其能力。"""
        ...


if __name__ == '__main__':
    pass
