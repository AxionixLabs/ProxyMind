# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import os
import re
import time
import base64
import typing
import asyncio
import secrets
import tempfile
from pathlib import Path
from backend.models.model_device import (
    Attachment, SemanticResult
)
from backend.mcp_hub.hub_device.phone import Phone
from backend.mcp_hub.hub_device.combo import Combo
from backend.mcp_hub.hub_device.widget import Widget
from backend.utilities import const


class Device(object):
    """设备工具语义层。"""

    def __init__(self, serial: str):
        self.serial = serial

        self.agent_id: str = self.serial

        self.phone: Phone = Phone(serial)
        self.combo: Combo = Combo(self.phone)

    @property
    def device_props(self) -> dict[str, typing.Any]:
        return self.phone.device_props

    @staticmethod
    def _sh_quote_single(text: str) -> str:
        return "'" + str(text or "").replace("'", r"'\''") + "'"

    def __str__(self):
        """返回调试展示文本。"""
        props = self.device_props
        return (
            f"<Device {props.get('brand')} {props.get('model')} "
            f"serial={self.serial} version={props.get('version')} hardware={props.get('hardware')} "
            f"sdk={props.get('sdk')} abi={props.get('abi')} locale={props.get('locale')} "
            f"timezone={props.get('timezone')} debuggable={props.get('debuggable')} secure={props.get('secure')}>"
        )

    __repr__ = __str__

    @staticmethod
    def device_semantics(device_snap: dict) -> dict:
        """构建设备快照的语义描述。"""

        def brief(v: typing.Optional[bool]) -> typing.Optional[str]:
            if v is None:
                return None
            return "true" if v else "false"

        def kv(items: list[tuple[str, typing.Any]]) -> str:
            parts: list[str] = []
            for k, v in items:
                if v is None:
                    continue
                if isinstance(v, str) and not v.strip():
                    continue
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
            "kv"    : semantic_kv,
            "brief" : semantic_brief
        }

    async def refresh_device_props(self) -> dict[str, typing.Any]:
        """刷新并返回设备属性。"""
        return await self.phone.refresh_device_props()

    # workflow: ==== Info Control MCP Tool ====
    async def device_snapshot(self) -> dict[str, typing.Any]:
        """返回设备当前快照。"""
        props = await self.phone.refresh_device_props()

        battery     = await self.phone.battery()
        wm_size     = await self.phone.wm_size()
        online      = await self.phone.is_online()
        emulator    = await self.phone.is_emulator()
        screen_lock = await self.phone.is_screen_locked()
        screen_on   = await self.phone.is_screen_on()

        information = props | {
            "battery"     : battery,
            "wm_size"     : {"w": wm_size[0], "h": wm_size[1]} if wm_size else None,
            "online"      : online,
            "emulator"    : emulator,
            "screen_lock" : screen_lock,
            "screen_on"   : screen_on
        }
        semantic = self.device_semantics(information)

        return SemanticResult(
            ok=True,
            text=semantic["brief"],
            data={
                "serial"      : information.get("serial"),
                "brand"       : information.get("brand"),
                "model"       : information.get("model"),
                "version"     : information.get("version"),
                "sdk"         : information.get("sdk"),
                "battery"     : information.get("battery"),
                "wm_size"     : information.get("wm_size"),
                "online"      : information.get("online"),
                "emulator"    : information.get("emulator"),
                "screen_lock" : information.get("screen_lock"),
                "screen_on"   : information.get("screen_on")
            }
        ).to_dict()

    # workflow: ==== App Control MCP Tool ====
    async def app_deep_link(self, url: str) -> typing.Any:
        """通过深度链接启动应用。"""
        raw = await self.phone.app_deep_link(url)
        return SemanticResult(
            ok=True,
            text="深度链接已执行。",
            data={"raw": raw}
        ).to_dict()

    # workflow: ==== App Control MCP Tool ====
    async def app_start(self, package: str, activity: typing.Optional[str] = None) -> typing.Any:
        """启动指定应用。"""
        raw = await self.phone.app_start(package, activity)
        return SemanticResult(
            ok=True,
            text="应用启动命令已执行。",
            data={"raw": raw}
        ).to_dict()

    # workflow: ==== App Control MCP Tool ====
    async def app_stop(self, package: str) -> typing.Any:
        """强制停止指定应用。"""
        raw = await self.phone.app_stop(package)
        return SemanticResult(
            ok=True,
            text="应用停止命令已执行。",
            data={"raw": raw}
        ).to_dict()

    # workflow: ==== App Control MCP Tool ====
    async def app_clear(self, package: str) -> typing.Any:
        """清除指定应用的数据。"""
        raw = await self.phone.app_clear(package)
        return SemanticResult(
            ok=True,
            text="应用数据清理命令已执行。",
            data={"raw": raw}
        ).to_dict()

    # workflow: ==== App Control MCP Tool ====
    async def app_foreground(self, package: str, activity: typing.Optional[str] = None) -> dict[str, typing.Any]:
        """确保应用位于前台。"""
        return await self.combo.app_foreground(package, activity)

    async def file_logcat_link(self) -> asyncio.subprocess.Process:
        """连接 logcat 流，供内部注入流程消费。"""
        return await self.phone.logcat_link()

    # workflow: ==== File Control MCP Tool ====
    async def file_logcat_dump(
        self,
        keywords: typing.Optional[list[str]] = None,
        tags: typing.Optional[list[str]] = None,
        level: str = "W",
        max_lines: int = 200,
        saved: typing.Optional[str] = None
    ) -> dict[str, typing.Any]:
        """导出一次性 logcat 快照。"""
        max_summary_lines = 2000
        max_process_lines = 20000

        raw = await self.phone.logcat_dump(tags, level)
        lines = (raw or "").splitlines()

        if len(lines) > max_process_lines:
            lines = lines[-max_process_lines:]

        ks: list[str] = []
        if keywords:
            ks = [str(k).strip() for k in keywords if k and str(k).strip()]

        if ks:
            pattern = re.compile("|".join(re.escape(k) for k in ks), re.IGNORECASE)
            lines = [ln for ln in lines if pattern.search(ln)]

        all_lines = list(lines)

        try:
            requested_max_lines = int(max_lines) if max_lines is not None else 200
        except (TypeError, ValueError):
            requested_max_lines = 200

        if requested_max_lines < 0:
            requested_max_lines = 0

        effective_max_lines = min(requested_max_lines, max_summary_lines)

        summary_lines = all_lines

        if 0 < effective_max_lines < len(summary_lines):
            summary_lines = summary_lines[-effective_max_lines:]
        elif effective_max_lines == 0:
            summary_lines = []

        content = "\n".join(summary_lines).strip()
        if not content:
            content = "logcat empty"

        attachments: list[Attachment] = []
        saved_path: typing.Optional[str] = None

        if saved and str(saved).strip():
            p = Path(str(saved)).expanduser()

            if p.suffix:
                out = p
            else:
                p.mkdir(parents=True, exist_ok=True)
                out = p / f"logcat_{time.strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(4)}.log"

            if not out.suffix:
                out = out.with_suffix(".log")

            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text("\n".join(all_lines), const.CHARSET, const.IGNORE)
            saved_path = str(out.resolve())

            attachments.append(
                Attachment(
                    kind="file",
                    local=saved_path,
                    filename=Path(saved_path).name,
                    mime_type="text/plain"
                )
            )

        if saved_path:
            text = (
                f"logcat captured: {len(all_lines)} lines; "
                f"summary={len(summary_lines)} lines; saved={saved_path}"
            )
        else:
            text = (
                f"logcat captured: {len(all_lines)} lines; "
                f"summary={len(summary_lines)} lines"
            )

        return SemanticResult(
            ok=True,
            text=text,
            attachments=attachments,
            data={
                "count"   : len(all_lines),
                "summary" : len(summary_lines),
                "saved"   : saved_path,
                "content" : content
            }
        ).to_dict()

    # workflow: ==== File Control MCP Tool ====
    async def file_logcat_clean(self, *_, **__) -> dict[str, typing.Any]:
        """清空 logcat 日志。"""
        raw = await self.phone.logcat_clean()
        return SemanticResult(
            ok=True,
            text="logcat cleaned",
            data={"raw": raw}
        ).to_dict()

    # workflow: ==== Info Control MCP Tool ====
    async def grep_packages(
        self,
        keyword: typing.Optional[str] = None,
        scope: typing.Literal["user", "system", "all"] = "user"
    ) -> dict:
        """按范围和关键字筛选应用包名。"""
        kw = (keyword or "").strip()
        if not kw:
            resp = await self.phone.list_packages(scope)
            pkgs = self.phone.parse_package_list(resp)
            return SemanticResult(
                ok=True,
                text="\n".join(pkgs),
                data={"count": len(pkgs), "packages": pkgs}
            ).to_dict()

        resp = await self.phone.grep_packages(kw, scope)
        pkgs = self.phone.parse_package_list(resp)
        return SemanticResult(
            ok=True,
            text="\n".join(pkgs),
            data={"count": len(pkgs), "packages": pkgs}
        ).to_dict()

    # workflow: ==== System Control MCP Tool ====
    async def open_notification(self) -> typing.Any:
        """打开通知栏。"""
        raw = await self.phone.open_notification()
        return SemanticResult(
            ok=True,
            text="通知栏已打开。",
            data={"raw": raw}
        ).to_dict()

    # workflow: ==== System Control MCP Tool ====
    async def open_quick_settings(self) -> typing.Any:
        """打开快捷设置面板。"""
        raw = await self.phone.open_quick_settings()
        return SemanticResult(
            ok=True,
            text="快捷设置面板已打开。",
            data={"raw": raw}
        ).to_dict()

    # workflow: ==== System Control MCP Tool ====
    async def set_screen(self, on: bool) -> dict[str, typing.Any]:
        """设置屏幕开关。"""
        return await self.combo.set_screen(on)

    # workflow: ==== System Control MCP Tool ====
    async def reboot(
        self,
        mode: typing.Literal["", "recovery", "bootloader", "edl"] = "",
        wait: bool = False,
        wait_timeout: float = 120.0
    ) -> dict[str, typing.Any]:
        """重启设备。"""
        raw = await self.phone.reboot(mode)

        data: dict[str, typing.Any] = {"raw": raw, "mode": mode}

        # 仅普通重启才支持 wait-for-device
        if wait and mode == "":
            try:
                await asyncio.wait_for(
                    self.phone.wait_for_device(), timeout=wait_timeout
                )
                return SemanticResult(
                    ok=True,
                    text="设备已触发重启，并已重新上线。",
                    data=data
                ).to_dict()
            except Exception as e:
                err = f"{type(e).__name__}: {e}"
                data["error"] = err
                data["wait_timeout"] = wait_timeout
                return SemanticResult(
                    ok=False,
                    text=f"设备已触发重启，但等待重新上线失败：{err}",
                    data=data
                ).to_dict()

        # 不等待：只表示命令已下发
        return SemanticResult(
            ok=True,
            text="已下发 adb reboot 指令。",
            data=data
        ).to_dict()

    # workflow: ==== System Control MCP Tool ====
    async def swipe_unlock(self) -> dict[str, typing.Any]:
        """点亮屏幕并上滑解锁。"""
        return await self.combo.swipe_unlock()

    # workflow: ==== UI Interaction MCP Tool ====
    async def scroll(
        self,
        direction: typing.Literal["up", "down", "left", "right"],
        x: int,
        y: int,
        duration: int = 300
    ) -> dict[str, typing.Any]:
        """按方向滚动。"""
        raw = await self.phone.scroll_by_direction(direction, x, y, duration)

        text_map = {
            "up"    : "向上滚动已执行。",
            "down"  : "向下滚动已执行。",
            "left"  : "向左滚动已执行。",
            "right" : "向右滚动已执行。"
        }

        return SemanticResult(
            ok=True,
            text=text_map[direction],
            data={"raw": raw}
        ).to_dict()

    # workflow: ==== UI Interaction MCP Tool ====
    async def scroll_into_view(
        self,
        by: typing.Literal["id", "desc", "text", "bbox", "xpath"],
        value: str | list,
        match: typing.Literal["eq", "contains", "regex"] = "eq",
        ignore_case: bool = False,
        direction: typing.Literal["down", "up", "left", "right"] = "down",
        timeout: float = 12.0,
        max_swipes: int = 12,
        should_click: bool = False
    ) -> dict[str, typing.Any]:
        """将目标元素滚动到可见区域。"""
        return await self.combo.scroll_into_view(
            by, value, match, ignore_case, direction, timeout, max_swipes, should_click
        )

    # workflow: ==== UI Interaction MCP Tool ====
    async def click(
        self,
        by: typing.Literal["id", "desc", "text", "bbox", "xpath"],
        value: str | list,
        match: typing.Literal["eq", "contains", "regex"] = "eq",
        ignore_case: bool = False
    ) -> dict[str, typing.Any]:
        """点击匹配到的目标元素。"""
        if not (widget := await self.phone.find_ui_widget(by, value, match, ignore_case)):
            return SemanticResult(
                ok=False,
                text="未找到可点击的节点。",
                data={"node": None, "clicked": False}
            ).to_dict()

        if not widget.center:
            return SemanticResult(
                ok=False,
                text="找到节点但缺少可点击坐标（center）。",
                data={"node": widget.to_node(), "clicked": False}
            ).to_dict()

        raw = await self.phone.tap(*widget.center)

        return SemanticResult(
            ok=True,
            text="点击完成。",
            data={"raw": raw, "node": widget.to_node(), "clicked": True}
        ).to_dict()

    # workflow: ==== UI Interaction MCP Tool ====
    async def key_event(self, keycode: int, longpress: bool = False) -> dict[str, typing.Any]:
        """发送系统按键。"""
        raw = await self.phone.send_keyevent(keycode, longpress)
        return SemanticResult(
            ok=True,
            text="按键已发送。",
            data={"raw": raw}
        ).to_dict()

    # workflow: ==== Info Control MCP Tool ====
    async def screenshot(self, local: str) -> dict[str, typing.Any]:
        """保存截图到本地。"""
        return await self.combo.screenshot(local)

    # workflow: ==== UI Interaction MCP Tool ====
    async def input_text(self, text: str) -> typing.Any:
        """向当前焦点输入文本。"""
        ime = await self.combo.ensure_ime()

        if not ime.get("ok"):
            return ime

        raw = await self.phone.input_text("" if text is None else str(text))
        return SemanticResult(
            ok=True,
            text="文本输入已执行。",
            data={"raw": raw}
        ).to_dict()

    # workflow: ==== UI Interaction MCP Tool ====
    async def clear_text(self) -> typing.Any:
        """清空当前焦点输入框文本。"""
        ime = await self.combo.ensure_ime()

        if not ime.get("ok"):
            return ime

        raw = await self.phone.clear_text()
        return SemanticResult(
            ok=True,
            text="文本已清空。",
            data={"raw": raw}
        ).to_dict()

    # workflow: ==== UI Interaction MCP Tool ====
    async def current_focus(self) -> dict[str, typing.Any]:
        """获取当前前台焦点。"""
        focus = await self.phone.focus_info()
        package = focus.get("package")
        activity = focus.get("activity")
        raw = focus.get("raw", "")

        ok = bool(package or activity)

        return SemanticResult(
            ok=ok,
            text=f"当前Focus：package={package or ''} activity={activity or ''}".strip(),
            data={
                "package"  : package,
                "activity" : activity,
                "raw"      : raw
            }
        ).to_dict()

    # workflow: ==== UI Interaction MCP Tool ====
    async def current_widgets(
        self,
        view: typing.Literal["interactive", "credible", "all"] = "all"
    ) -> dict[str, typing.Any]:
        """获取当前页面控件清单。"""
        def keep_node(w: Widget) -> bool:
            """按视图模式筛选控件。"""
            if view == "interactive":
                return any((w.clickable, w.focusable, w.scrollable))
            elif view == "credible":
                return any((w.id, w.desc, w.text))
            elif view == "all":
                return True

            return False

        if not (xml := await self.phone.ui_xml()):
            return SemanticResult(
                ok=False,
                text="未获取到 UI XML。",
                data={"count": 0}
            ).to_dict()

        widget_list = self.phone.parse_widgets(xml)
        if not widget_list:
            return SemanticResult(
                ok=True,
                text="当前页面没有可用控件。",
                data={"count": 0}
            ).to_dict()

        lines: list[str] = [
            item.semantic for item in widget_list if keep_node(item)
        ]
        out = "\n".join(lines).strip()

        return SemanticResult(
            ok=True,
            text=out if out else "未发现可用控件。",
            data={"count": len(lines)}
        ).to_dict()

    # workflow: ==== UI Interaction MCP Tool ====
    async def find_element(
        self,
        by: typing.Literal["id", "desc", "text", "bbox", "xpath"],
        value: str | list,
        match: typing.Literal["eq", "contains", "regex"] = "eq",
        ignore_case: bool = False
    ) -> dict[str, typing.Any]:
        """查找当前页面中的目标控件。"""
        if by == "xpath":
            return SemanticResult(
                ok=False,
                text="by=xpath 暂不支持（Android uiautomator dump 非标准 XPath）。",
                data={"node": None}
            ).to_dict()

        if found_node := await self.phone.find_ui_widget(by, value, match, ignore_case):
            return SemanticResult(
                ok=True,
                text="已找到目标控件。",
                data={
                    "node"   : found_node.to_node()
                }
            ).to_dict()

        return SemanticResult(
            ok=False,
            text="未找到目标控件。",
            data={"node": None}
        ).to_dict()

    # workflow: ==== UI Interaction MCP Tool ====
    async def heal_element(self, locator: str, *_, **__) -> dict[str, typing.Any]:
        """执行元素自愈流程。"""
        page_id, page_dump, wm = await asyncio.gather(
            self.current_focus(), self.phone.ui_xml(), self.phone.wm_size()
        )
        w, h = wm if wm else (0, 0)
        payload = {
            "serial"    : self.serial,
            "page_id"   : page_id.get("data", {}).get("package") or "",
            "station"   : "android",
            "locator"   : locator,
            "page_dump" : page_dump or "",
            "wm_size"   : {"w": w, "h": h}
        }

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            new_local = await self.combo.save_screenshot(tmp.name)
            with open(new_local, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
            payload["screenshot_base64"]   = b64
            payload["screenshot_data_url"] = f"data:image/png;base64,{b64}"

        os.remove(new_local)

        return SemanticResult(
            ok=True,
            text="元素定位诊断。",
            data=payload
        ).to_dict()

    # workflow: ==== UI Interaction MCP Tool ====
    async def wait_element(
        self,
        by: typing.Literal["id", "desc", "text", "bbox", "xpath"],
        value: str | list,
        match: typing.Literal["eq", "contains", "regex"] = "eq",
        ignore_case: bool = False,
        timeout: float = 10.0,
        state: typing.Literal["exists", "gone"] = "exists"
    ) -> dict[str, typing.Any]:
        return await self.combo.wait_element(by, value, match, ignore_case, timeout, state=state)

    # workflow: ==== UI Interaction MCP Tool ====
    async def scroll_to_edge(self, edge: typing.Literal["top", "bottom"]) -> dict[str, typing.Any]:
        """滚动到边界。"""
        return await self.combo.scroll_to_edge(edge)

    # workflow: ==== Monkey ====
    async def monkey_stop(self) -> dict[str, typing.Any]:
        """向设备发送 monkey 停止命令，不等待确认结果。"""
        script = """
ps -A | grep com.android.commands.monkey | while read -r _ pid _; do
  kill -9 "$pid" 2>/dev/null || true
done
printf 'stop_sent=1\\n'
""".strip()
        await self.phone.shell_script(script)
        return SemanticResult(
            ok=True,
            text="停止命令已发送。",
            data={
                "reason": "stop_command_sent"
            }
        ).to_dict()


if __name__ == '__main__':
    pass
