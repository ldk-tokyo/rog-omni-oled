#!/usr/bin/env python3
"""菜单栏常驻：在 Mac 菜单栏实时显示额度、键盘电量和系统状态。

数据来源
- Claude / Codex 额度和键盘连接状态：quota_daemon.py 写的 ~/Library/Caches/rog-oled/status.json
  （守护进程独占接收器，菜单栏程序不直接碰键盘）。只装 Mac 部分时守护进程以 --no-keyboard
  运行，菜单里不显示键盘那一行。
- 键盘电量：同目录下的 keyboard.json。
- CPU、内存、网络速度、日期时间：本机实时读取（psutil）。

菜单栏标题显示哪些项可以在"菜单栏显示"子菜单里勾选，保存在
~/Library/Application Support/rog-oled/menubar.json。每秒刷新一次，菜单展开时也照常刷新。

  python menubar.py
"""
from __future__ import annotations

import json
import os
import time

import objc
import psutil
from AppKit import (
    NSApplication, NSApplicationActivationPolicyAccessory, NSAttributedString, NSColor, NSFont,
    NSFontAttributeName, NSFontWeightRegular, NSFontWeightSemibold, NSForegroundColorAttributeName,
    NSMenu, NSMenuItem, NSMutableAttributedString, NSMutableParagraphStyle,
    NSParagraphStyleAttributeName, NSStatusBar, NSTextAlignmentLeft, NSTextTab,
    NSVariableStatusItemLength, NSWorkspace,
)
from Foundation import NSObject, NSRunLoop, NSRunLoopCommonModes, NSTimer, NSURL
from PyObjCTools import AppHelper

import dashboard
from quota_daemon import BATTERY_FILE, STATUS_EVERY_S, STATUS_FILE, to_view

TICK_S = 1
STALE_S = STATUS_EVERY_S * 3  # 这么久没更新 status.json，就认为守护进程没在运行
PREFS_FILE = os.path.expanduser("~/Library/Application Support/rog-oled/menubar.json")
LOG_FILE = os.path.expanduser("~/Library/Logs/rog-oled.log")

SERVICES = {"claude": "Claude", "codex": "Codex"}
WINDOW_NAMES = {"5h": "5 小时", "7d": "7 天"}
KEYBOARD_STATES = {"asleep": "休眠中", "no_receiver": "没找到 Omni 接收器", "error": "通信失败", "unknown": "状态未知"}
WEEKDAYS = "一二三四五六日"

# 菜单栏标题可选显示的项（顺序即显示顺序）
TITLE_ITEMS = [
    ("claude", "Claude 额度"),
    ("codex", "Codex 额度"),
    ("battery", "键盘电量"),
    ("cpu", "CPU"),
    ("mem", "内存"),
    ("net", "网络速度"),
    ("clock", "日期时间"),
]
DEFAULT_TITLE = ["claude", "codex", "battery"]


# ---------- 数据 ----------

def read_json(path: str) -> dict | None:
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def load_prefs() -> list[str]:
    items = (read_json(PREFS_FILE) or {}).get("title")
    return items if isinstance(items, list) else list(DEFAULT_TITLE)


def save_prefs(items: list[str]) -> None:
    os.makedirs(os.path.dirname(PREFS_FILE), exist_ok=True)
    with open(PREFS_FILE, "w") as f:
        json.dump({"title": items}, f)


class NetMeter:
    """物理网卡（en*）的收发速率；不计回环和 VPN 隧道，免得重复计算。"""

    def __init__(self) -> None:
        self.prev: tuple[float, int, int] | None = None
        self.rate = (0.0, 0.0)

    def sample(self) -> tuple[float, float]:
        now = time.monotonic()
        counters = psutil.net_io_counters(pernic=True)
        recv = sum(c.bytes_recv for name, c in counters.items() if name.startswith("en"))
        sent = sum(c.bytes_sent for name, c in counters.items() if name.startswith("en"))
        if self.prev and now > self.prev[0]:
            dt = now - self.prev[0]
            self.rate = (max(0, recv - self.prev[1]) / dt, max(0, sent - self.prev[2]) / dt)
        self.prev = (now, recv, sent)
        return self.rate


# ---------- 格式化 ----------

def fmt_rate(bps: float, compact: bool = False) -> str:
    """1.2 MB/s / 85 KB/s；compact=True 时是菜单栏用的短格式（1.2M / 85K）。"""
    for unit, size in (("M", 1 << 20), ("K", 1 << 10)):
        if bps >= size:
            v = bps / size
            s = f"{v:.1f}" if v < 10 else f"{v:.0f}"
            return f"{s}{unit}" if compact else f"{s} {unit}B/s"
    return f"{bps:.0f}B" if compact else f"{bps:.0f} B/s"


def fmt_clock(ts: float, now: float) -> str:
    """今天只写时间，明天写"明天 HH:MM"，再往后写月-日。"""
    ts = round(ts / 60) * 60  # 重置时间常是 16:59:59.85 这种，按分钟四舍五入
    day = lambda t: time.localtime(t)[:3]  # noqa: E731
    hm = time.strftime("%H:%M", time.localtime(ts))
    if day(ts) == day(now):
        return hm
    if day(ts) == day(now + 86400):
        return f"明天 {hm}"
    return time.strftime("%m-%d %H:%M", time.localtime(ts))


def fmt_today(now: float) -> str:
    t = time.localtime(now)
    return f"{t.tm_mon}月{t.tm_mday}日 星期{WEEKDAYS[t.tm_wday]} {time.strftime('%H:%M:%S', t)}"


def is_low(left: float | None) -> bool:
    return left is not None and left < dashboard.LOW


# ---------- 菜单栏 ----------

def menu_font(bold: bool = False) -> NSFont:
    size = NSFont.menuFontOfSize_(0).pointSize()
    return NSFont.monospacedDigitSystemFontOfSize_weight_(size, NSFontWeightSemibold if bold else NSFontWeightRegular)


def row_text(s: str, bold: bool = False, color=None) -> NSAttributedString:
    """菜单行：等宽数字 + 制表位，让几行的数值上下对齐。"""
    style = NSMutableParagraphStyle.alloc().init()
    style.setTabStops_([NSTextTab.alloc().initWithTextAlignment_location_options_(NSTextAlignmentLeft, x, {})
                        for x in (64, 132)])
    attrs = {NSFontAttributeName: menu_font(bold), NSParagraphStyleAttributeName: style}
    if color is not None:
        attrs[NSForegroundColorAttributeName] = color
    return NSAttributedString.alloc().initWithString_attributes_(s, attrs)


class MenuBar(NSObject):
    def init(self):
        self = objc.super(MenuBar, self).init()
        if self is None:
            return None
        self.title_items = load_prefs()
        self.net = NetMeter()
        self.net.sample()
        psutil.cpu_percent(None)  # 第一次调用只是建立基准

        self.status_item = NSStatusBar.systemStatusBar().statusItemWithLength_(NSVariableStatusItemLength)
        self.menu = NSMenu.alloc().init()
        self.menu.setAutoenablesItems_(False)
        self.menu.setDelegate_(self)

        self.rows = {}
        for svc, name in SERVICES.items():
            self.rows[svc] = self._row(name)
            for k in dashboard.WINDOWS:
                self.rows[svc, k] = self._row("")
        self.rows["fetched"] = self._row("")
        self.menu.addItem_(NSMenuItem.separatorItem())
        self.rows["keyboard"] = self._row("")
        self.kb_sep = NSMenuItem.separatorItem()  # 只装 Mac 部分时和键盘那一行一起隐藏
        self.menu.addItem_(self.kb_sep)
        self.rows["sys"] = self._row("")
        self.rows["net"] = self._row("")
        self.rows["clock"] = self._row("")
        self.menu.addItem_(NSMenuItem.separatorItem())

        title_menu = NSMenu.alloc().init()
        self.toggles = {}
        for key, label in TITLE_ITEMS:
            item = title_menu.addItemWithTitle_action_keyEquivalent_(label, "toggle:", "")
            item.setTarget_(self)
            item.setRepresentedObject_(key)
            self.toggles[key] = item
        parent = self.menu.addItemWithTitle_action_keyEquivalent_("菜单栏显示", None, "")
        self.menu.setSubmenu_forItem_(title_menu, parent)
        self.menu.addItemWithTitle_action_keyEquivalent_("打开日志", "openLog:", "").setTarget_(self)
        self.menu.addItemWithTitle_action_keyEquivalent_("退出", "quit:", "q").setTarget_(self)
        self.status_item.setMenu_(self.menu)

        timer = NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(TICK_S, self, "tick:", None, True)
        NSRunLoop.currentRunLoop().addTimer_forMode_(timer, NSRunLoopCommonModes)  # 菜单展开时也刷新
        self.refresh()
        return self

    @objc.python_method
    def _row(self, s: str) -> NSMenuItem:
        item = self.menu.addItemWithTitle_action_keyEquivalent_(s, None, "")
        item.setEnabled_(True)  # 纯展示，但不要显示成灰色
        return item

    # ----- 事件 -----

    def tick_(self, _timer) -> None:
        self.refresh()

    def menuWillOpen_(self, _menu) -> None:
        self.refresh()

    def toggle_(self, sender) -> None:
        key = sender.representedObject()
        if key in self.title_items:
            self.title_items.remove(key)
        else:
            self.title_items.append(key)
        order = [k for k, _ in TITLE_ITEMS]
        self.title_items.sort(key=order.index)
        save_prefs(self.title_items)
        self.refresh()

    def openLog_(self, _sender) -> None:
        NSWorkspace.sharedWorkspace().openURL_(NSURL.fileURLWithPath_(LOG_FILE))

    def quit_(self, _sender) -> None:
        NSApplication.sharedApplication().terminate_(None)

    # ----- 刷新 -----

    @objc.python_method
    def refresh(self) -> None:
        now = time.time()
        status = read_json(STATUS_FILE)
        running = bool(status) and now - status.get("updated_at", 0) <= STALE_S
        no_keyboard = running and status.get("keyboard") == "disabled"  # 守护进程以 --no-keyboard 运行
        views = {svc: to_view(((status or {}).get(svc) or {}).get("windows"), now) for svc in SERVICES}
        battery = read_json(BATTERY_FILE)
        cpu = psutil.cpu_percent(None)
        mem = psutil.virtual_memory()
        down, up = self.net.sample()

        self._update_quota_rows(views, status, now)
        self._update_keyboard_row(status, running, battery, now)
        used_gb = (mem.total - mem.available) / (1 << 30)
        self.rows["sys"].setAttributedTitle_(row_text(
            f"CPU\t{cpu:.0f}%\t内存 {used_gb:.1f} / {mem.total / (1 << 30):.0f} GB（{mem.percent:.0f}%）"))
        self.rows["net"].setAttributedTitle_(row_text(f"网络\t↓ {fmt_rate(down)}\t↑ {fmt_rate(up)}"))
        self.rows["clock"].setAttributedTitle_(row_text(fmt_today(now)))
        for key, item in self.toggles.items():
            item.setState_(1 if key in self.title_items else 0)
        self.toggles["battery"].setHidden_(no_keyboard)

        parts = []  # (文字, 是否告急)
        for key in self.title_items:
            if key in SERVICES:
                svc = views[key]
                left = dashboard.primary(svc)[1][0] if svc else None
                parts.append((f"{dashboard.TAGS[key]} {'--' if left is None else f'{left:.0f}%'}", is_low(left)))
            elif key == "battery" and not no_keyboard:
                b = (battery or {}).get("battery")
                parts.append((f"⌨ {'--' if b is None else f'{b}%'}", False))
            elif key == "cpu":
                parts.append((f"CPU {cpu:.0f}%", False))
            elif key == "mem":
                parts.append((f"MEM {mem.percent:.0f}%", False))
            elif key == "net":
                parts.append((f"↓{fmt_rate(down, True)} ↑{fmt_rate(up, True)}", False))
            elif key == "clock":
                t = time.localtime(now)
                parts.append((f"{t.tm_mon}/{t.tm_mday} 周{WEEKDAYS[t.tm_wday]} {time.strftime('%H:%M', t)}", False))
        self._set_title(parts or [("ROG", False)])

    @objc.python_method
    def _update_quota_rows(self, views: dict, status: dict | None, now: float) -> None:
        fetched = []
        for svc, name in SERVICES.items():
            view = views[svc]
            self.rows[svc].setAttributedTitle_(row_text(name if view else f"{name}\tno data", bold=True))
            for k in dashboard.WINDOWS:
                item = self.rows[svc, k]
                item.setHidden_(k not in view)
                if k not in view:
                    continue
                left, reset = view[k]
                when = f"{dashboard.fmt_left(reset - now)} 后重置（{fmt_clock(reset, now)}）" if reset else "已重置"
                color = NSColor.systemRedColor() if is_low(left) else None
                item.setAttributedTitle_(row_text(f"  {WINDOW_NAMES[k]}\t剩 {left:.0f}%\t{when}", color=color))
            at = ((status or {}).get(svc) or {}).get("fetched_at")
            if at:
                fetched.append(f"{name} {fmt_clock(at, now)}")
        self.rows["fetched"].setAttributedTitle_(
            row_text("更新于 " + " · ".join(fetched) if fetched else "还没有额度数据", color=NSColor.secondaryLabelColor()))

    @objc.python_method
    def _update_keyboard_row(self, status: dict | None, running: bool, battery: dict | None, now: float) -> None:
        state = status.get("keyboard", "unknown") if running else None
        self.rows["keyboard"].setHidden_(state == "disabled")
        self.kb_sep.setHidden_(state == "disabled")
        if not running:
            self.rows["keyboard"].setAttributedTitle_(
                row_text("后台服务未运行（运行 ./install.sh 启动）", color=NSColor.systemOrangeColor()))
            return
        b = battery or {}
        level = f"电量 {b['battery']}%{'（充电中）' if b.get('charging') else ''}" if "battery" in b else "电量未知"
        if state == "connected":
            text = f"键盘\t已连接\t{level}"
        else:
            if "updated_at" in b:
                level += f"（{fmt_clock(b['updated_at'], now)}）"
            text = f"键盘\t{KEYBOARD_STATES.get(state, state)}\t{level}"
        self.rows["keyboard"].setAttributedTitle_(row_text(text))

    @objc.python_method
    def _set_title(self, parts: list[tuple[str, bool]]) -> None:
        size = NSFont.menuBarFontOfSize_(0).pointSize()
        f = NSFont.monospacedDigitSystemFontOfSize_weight_(size, NSFontWeightRegular)
        title = NSMutableAttributedString.alloc().init()
        for i, (s, low) in enumerate(parts):
            if i:
                title.appendAttributedString_(NSAttributedString.alloc().initWithString_attributes_(
                    " · ", {NSFontAttributeName: f, NSForegroundColorAttributeName: NSColor.tertiaryLabelColor()}))
            attrs = {NSFontAttributeName: f}
            if low:
                attrs[NSForegroundColorAttributeName] = NSColor.systemRedColor()
            title.appendAttributedString_(NSAttributedString.alloc().initWithString_attributes_(s, attrs))
        self.status_item.button().setAttributedTitle_(title)


def main() -> None:
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)  # 不在程序坞显示图标
    controller = MenuBar.alloc().init()  # noqa: F841 — 保持引用，否则会被回收
    AppHelper.runEventLoop()


if __name__ == "__main__":
    main()
