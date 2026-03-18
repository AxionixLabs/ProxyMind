#  ____  _
# |  _ \| |__   ___  _ __   ___
# | |_) | '_ \ / _ \| '_ \ / _ \
# |  __/| | | | (_) | | | |  __/
# |_|   |_| |_|\___/|_| |_|\___|
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import re
import time
import typing
import asyncio
import numpy as np
from PIL import Image
from engine.terminal import Terminal


class Phone(object):
    """Phone class."""

    def __init__(self, serial: str):
        self.serial = serial

        self.agent_id: str = self.serial

        self.brand    : typing.Optional[str] = None
        self.model    : typing.Optional[str] = None
        self.version  : typing.Optional[str] = None
        self.hardware : typing.Optional[str] = None
        self.sdk      : typing.Optional[str] = None
        self.abi      : typing.Optional[str] = None

        self.locale   : typing.Optional[str] = None
        self.timezone : typing.Optional[str] = None

        self.debuggable : typing.Optional[bool] = None
        self.secure     : typing.Optional[bool] = None

    @property
    def prefix(self) -> list[str]:
        return ["adb", "-s", self.serial]

    @property
    def device_info(self) -> dict:
        return {
            "serial"     : self.serial,
            "brand"      : self.brand,
            "model"      : self.model,
            "version"    : self.version,
            "hardware"   : self.hardware,
            "sdk"        : self.sdk,
            "abi"        : self.abi,
            "locale"     : self.locale,
            "timezone"   : self.timezone,
            "debuggable" : self.debuggable,
            "secure"     : self.secure
        }

    @staticmethod
    def device_semantics(device_snap: dict) -> dict:
        """将“设备快照 snap(dict)”转换为两种更适合大模型/检索的语义描述。"""

        def brief(v: typing.Optional[bool]) -> typing.Optional[str]:
            """结构化 KV 串（稳定、可做 embedding / recall 的输入）"""
            if v is None: return None
            return "true" if v else "false"

        def kv(items: list[tuple[str, typing.Any]]) -> str:
            """人类可读摘要（日志/终端展示友好）"""
            parts: list[str] = []
            for k, v in items:
                if v is None: continue
                if isinstance(v, str) and not v.strip(): continue
                parts.append(f"{k}={v}")
            return "; ".join(parts)

        wm     = device_snap.get("wm_size") or {}
        screen = f"{wm.get('w')}x{wm.get('h')}" if (wm.get("w") and wm.get("h")) else None

        battery   = device_snap.get("battery")
        battery_s = f"{battery}%" if battery is not None else None

        online    = device_snap.get("online") is True
        locked    = device_snap.get("screen_lock") is True
        screen_on = device_snap.get("screen_on") is True

        semantic_kv = kv([
            ("kind",       "device"),
            ("serial",     device_snap.get("serial")),
            ("brand",      device_snap.get("brand")),
            ("model",      device_snap.get("model")),
            ("android",    device_snap.get("version")),
            ("sdk",        device_snap.get("sdk")),
            ("abi",        device_snap.get("abi")),
            ("hw",         device_snap.get("hardware")),
            ("locale",     device_snap.get("locale")),
            ("tz",         device_snap.get("timezone")),
            ("screen",     screen),
            ("battery",    battery_s),
            ("online",     brief(online)),
            ("locked",     brief(locked)),
            ("screen_on",  brief(screen_on)),
            ("secure",     brief(device_snap.get("secure") is True)),
            ("debuggable", brief(device_snap.get("debuggable") is True)),
            ("emulator",   brief(device_snap.get("emulator") is True)),
        ])

        semantic_brief = (
            f"{device_snap.get('serial') or 'unknown'}: "
            f"{'在线' if online else '离线'} / "
            f"{'锁屏' if locked else '未锁屏'} / "
            f"{'亮屏' if screen_on else '灭屏'} / "
            f"电量{battery_s or 'unknown'} / "
            f"屏幕{screen.replace('x', '×') if screen else 'unknown'} / "
            f"Android{device_snap.get('version') or '?'}(SDK{device_snap.get('sdk') or '?'}) / "
            f"{(device_snap.get('brand') or '').strip()} {(device_snap.get('model') or '').strip()}".strip()
        )

        return {
            "snapshot"       : device_snap,
            "semantic_kv"    : semantic_kv,
            "semantic_brief" : semantic_brief
        }

    # workflow: ==== Info ====
    async def st_load_info(self) -> None:
        """从 adb getprop 加载并填充设备属性。"""
        cmd = self.prefix + [
            "shell", "getprop"
        ]
        if not (resp := await Terminal.cmd_line(cmd)):
            return None

        pick: typing.Callable[
            [str], str
        ] = lambda x: m.group(1) if (
            m := re.search(rf"\[{re.escape(x)}]: \[(.*?)]", resp)
        ) else "unknown"

        self.brand    = pick("ro.product.brand")
        self.model    = pick("ro.product.model")
        self.version  = pick("ro.build.version.release")
        self.hardware = pick("ro.hardware")
        self.sdk      = pick("ro.build.version.sdk")
        self.abi      = pick("ro.product.cpu.abi")

        self.locale   = pick("persist.sys.locale")
        self.timezone = pick("persist.sys.timezone")

        self.debuggable = pick("ro.debuggable") == "1"
        self.secure     = pick("ro.secure") == "1"

    # workflow: ==== Info ====
    async def st_battery(self) -> int | None:
        """读取电池电量百分比（level/scale）。"""
        cmd = self.prefix + ["shell", "dumpsys", "battery"]
        if not (resp := await Terminal.cmd_line(cmd)):
            return None

        m_level = re.search(r"(?m)^\s*level:\s*(\d+)\s*$", resp)
        m_scale = re.search(r"(?m)^\s*scale:\s*(\d+)\s*$", resp)
        if not (m_level and m_scale):
            return None

        level = int(m_level.group(1))
        scale = int(m_scale.group(1)) or 100

        return int(round(level * 100 / scale))

    # workflow: ==== Info ====
    async def st_wm_size(self) -> tuple[int, int] | None:
        """获取物理屏幕分辨率。"""
        cmd = self.prefix + [
            "shell", "wm", "size"
        ]
        if not (resp := await Terminal.cmd_line(cmd)):
            return None

        if not (m := re.search(r"Physical size:\s*(\d+)x(\d+)", resp)):
            return None

        return int(m.group(1)), int(m.group(2))

    # workflow: ==== Info ====
    async def is_online(self) -> bool:
        """是否能真正访问互联网。"""
        resp = await Terminal.cmd_line(
            self.prefix + ["shell", "ping", "-c", "1", "1.1.1.1"]
        )
        return bool(resp and "1 packets transmitted" in resp)

    # workflow: ==== Info ====
    async def is_emulator(self) -> bool:
        """根据硬件/机型特征判断是否为模拟器。"""
        return (
            "goldfish" in self.hardware or "ranchu" in self.hardware or "sdk" in self.model
        )

    # workflow: ==== Info ====
    async def is_screen_lock(self) -> bool:
        """检查是否正在显示锁屏。"""
        cmd = self.prefix + [
            "shell", "dumpsys", "window", "policy", "|", "grep", "mInputRestricted"
        ]
        return "true" in await Terminal.cmd_line(cmd)

    # workflow: ==== Info ====
    async def is_screen_on(self) -> bool:
        """检查屏幕是否处于点亮状态。"""
        cmd = self.prefix + [
            "shell", "dumpsys", "deviceidle", "|", "grep", "mScreenOn"
        ]
        return "true" in await Terminal.cmd_line(cmd)

    # workflow: ==== Keyevent ====
    async def key_event(self, keycode: int, longpress: bool = False) -> typing.Any:
        """向设备发送 Android 系统按键事件（支持普通按键与长按）。"""
        cmd = self.prefix + [
            "shell", "input", "keyevent"
        ]
        if longpress: cmd += ["--longpress"]
        cmd += [str(keycode)]

        return await Terminal.cmd_line(cmd)

    # workflow: ==== System ====
    async def screen_set(self, on: bool, settle: float = 0.2) -> None:
        """统一控制屏幕电源态。"""
        if on == await self.is_screen_on():
            return None

        await self.key_event(26)
        await asyncio.sleep(settle)

    # workflow: ==== System ====
    async def bluetooth_set(self, status: typing.Literal["enable", "disable"]) -> typing.Any:
        """控制蓝牙状态。"""
        cmd = self.prefix + [
            "shell", "svc", "bluetooth", status
        ]
        return await Terminal.cmd_line(cmd)

    # workflow: ==== System ====
    async def wifi_set(self, status: typing.Literal["enable", "disable"]) -> typing.Any:
        """控制 WiFi 状态。"""
        cmd = self.prefix + [
            "shell", "svc", "wifi", status
        ]
        return await Terminal.cmd_line(cmd)

    # workflow: ==== System ====
    async def data_set(self, status: typing.Literal["enable", "disable"]) -> typing.Any:
        """控制移动数据状态。"""
        cmd = self.prefix + [
            "shell", "svc", "data", status
        ]
        return await Terminal.cmd_line(cmd)

    # workflow: ==== APP ====
    async def wait_foreground(
        self,
        package: str,
        wait_s: float,
        poll: float,
        stable_hits: int
    ) -> tuple[bool, dict[str, typing.Any], int]:
        """等待指定 package 进入前台（稳定命中 stable_hits 次）。"""
        hit = 0
        last_focus: dict[str, typing.Any] = {"package": None, "activity": None, "raw": ""}

        deadline = time.time() + float(wait_s)
        while time.time() < deadline:
            current_package, current_activity, raw = await self.focus()
            last_focus = {"package": current_package, "activity": current_activity, "raw": raw}

            if current_package == package:
                hit += 1
                if hit >= int(stable_hits):
                    return True, last_focus, hit
            else:
                hit = 0

            await asyncio.sleep(float(poll))

        return False, last_focus, hit

    # workflow: ==== UI ====
    async def focus(self) -> tuple[typing.Optional[str], typing.Optional[str], str]:
        """获取当前前台焦点信息（package / activity / raw）。"""
        cmd = self.prefix + [
            "shell", "dumpsys", "window", "|", "grep", "mCurrentFocus"
        ]

        if not (resp := await Terminal.cmd_line(cmd)):
            return None, None, ""

        raw: str = str(resp).strip()

        package: typing.Optional[str]  = None
        activity: typing.Optional[str] = None

        # 1) 优先：package/activity（component）
        if m := re.search(r"([a-zA-Z0-9._]+/[a-zA-Z0-9._$]+)", raw):
            activity = m.group(1)
            package = activity.split("/", 1)[0]
        else:
            # 2) 退化：u0 com.xxx.app ...
            if m := re.search(r"\bu\d+\s+([a-zA-Z0-9._]+)\b", raw):
                package = m.group(1)

        return package, activity, raw

    # workflow: ==== UI ====
    @staticmethod
    def similarity(img_path1: str, img_path2: str) -> float:
        """0~1：越大越相似（基于灰度 + downscale + 加权MSE，含轻微裁剪增强）。"""
        def prepare(path: str) -> np.ndarray:
            with Image.open(path) as im:
                im = im.convert("L")
                width, height = im.size

                # 裁剪边缘，减少状态栏/导航栏/吸顶影响
                left   = int(width * crop_left)
                right  = int(width * (1.0 - crop_right))
                top    = int(height * crop_top)
                bottom = int(height * (1.0 - crop_bottom))

                # 兜底：裁剪不能把图裁没
                if (right - left) >= 4 and (bottom - top) >= 4:
                    im = im.crop((left, top, right, bottom))

                # 再缩放到固定大小
                im = im.resize(size)

                return np.asarray(im, dtype=np.float32)

        size: tuple[int, int] = (96, 96)

        # 裁剪比例：去掉顶部/底部固定栏（默认较温和，可按实际 UI 调）
        crop_top: float    = 0.15
        crop_bottom: float = 0.12
        crop_left: float   = 0.00
        crop_right: float  = 0.00

        # 中心权重：越大越强调中心（1.0=无权重）
        center_weight: float = 1.8

        a1 = prepare(img_path1)
        a2 = prepare(img_path2)

        # 中心加权（边缘权重低一点，中心权重高一点）
        h, w = a1.shape
        yy, xx = np.mgrid[0:h, 0:w]
        cy, cx = max(1e-6, (h - 1) / 2.0), max(1e-6, (w - 1) / 2.0)

        # 归一化半径：中心0，边缘~1
        r = np.sqrt(((yy - cy) / cy) ** 2 + ((xx - cx) / cx) ** 2)
        r = np.clip(r, 0.0, 1.0)

        # 权重：中心 = center_weight，边缘 = 1.0（平滑过渡）
        weights = 1.0 + (center_weight - 1.0) * (1.0 - r) ** 2

        diff = a1 - a2
        mse = float(np.sum((diff * diff) * weights)) / float(np.sum(weights))

        # 归一化：像素范围 0~255，最大 MSE=255^2
        sim = 1.0 - min(1.0, mse / (255.0 * 255.0))
        return float(sim)


if __name__ == '__main__':
    pass
