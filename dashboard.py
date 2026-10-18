#!/usr/bin/env python3
"""额度看板布局草案：Claude Code / Codex 剩余额度，208×64、16 级灰度。

  python dashboard.py mockups        # 生成放大预览图 mockups/clean.png（A/B）和 mockups/hires.png（D/E/F）
  python dashboard.py push B         # 把某个布局发到键盘（A / B / D / E / F，以及 B-low 等告急态）
"""
from __future__ import annotations

import math
import os
import sys
import time
from functools import lru_cache

from PIL import Image, ImageChops, ImageDraw, ImageFont

W, H = 208, 64
SS = 4  # 图形超采样倍数（抗锯齿）

# 灰度（0–255，发送时量化为 16 级）
FULL, MID, DIM, FAINT = 255, 150, 70, 34
LOW = 20  # 剩余低于 20% 视为告急

FONT_DIR = "/System/Library/Fonts/Supplemental/"
DIN_COND = FONT_DIR + "DIN Condensed Bold.ttf"
DIN_ALT = FONT_DIR + "DIN Alternate Bold.ttf"

NAMES = {"claude": "CLAUDE", "codex": "CODEX"}
TAGS = {"claude": "CC", "codex": "CX"}
WINDOWS = ("5h", "7d")


# ---------- 绘图工具 ----------

@lru_cache(maxsize=None)
def font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def text(d: ImageDraw.ImageDraw, xy: tuple[float, float], s: str, size: int, fill: int = FULL,
         anchor: str = "ls", path: str = DIN_COND, tracking: int = 0) -> None:
    """抗锯齿文字；tracking > 0 时逐字加字距（只支持左对齐锚点，按整像素步进保证间距均匀）。"""
    f = font(path, size)
    if not tracking:
        d.text(xy, s, font=f, fill=fill, anchor=anchor)
        return
    x, y = xy
    for c in s:
        d.text((x, y), c, font=f, fill=fill, anchor=anchor)
        x += round(f.getlength(c)) + tracking


def text_width(s: str, size: int, path: str = DIN_COND) -> float:
    return font(path, size).getlength(s)


def ink_span(s: str, size: int, path: str = DIN_COND) -> tuple[int, int]:
    """文字实际墨迹的左右边界（相对绘制起点），用于把 % 号贴紧数字。"""
    im = Image.new("L", (size * len(s) + 20, size * 2), 0)
    ImageDraw.Draw(im).text((10, size * 3 // 2), s, font=font(path, size), fill=255, anchor="ls")
    box = im.getbbox() or (10, 0, 10, 0)
    return box[0] - 10, box[2] - 10


def supersampled(paint) -> Image.Image:
    """在 4 倍画布上作图再缩小，得到抗锯齿的图层。paint 收到的坐标单位是 1/SS 像素。"""
    big = Image.new("L", (W * SS, H * SS), 0)
    paint(ImageDraw.Draw(big))
    return big.resize((W, H), Image.BOX)


def overlay(img: Image.Image, layer: Image.Image) -> None:
    img.paste(ImageChops.lighter(img, layer))


RING_START, RING_SPAN = 135, 270  # 从左下顺时针扫到右下，底部留缺口


def ring(d: ImageDraw.ImageDraw, cx: float, cy: float, r: float, w: float,
         frac: float | None, segs: int = 0) -> None:
    """弧形仪表（超采样画布）：暗轨道 + 亮弧；frac=None 时轨道画成虚线。"""
    s = SS
    box = [(cx - r) * s, (cy - r) * s, (cx + r) * s, (cy + r) * s]
    if frac is None:
        for a in range(RING_START, RING_START + RING_SPAN, 15):
            d.arc(box, a, a + 6, fill=DIM, width=round(w * s))
        return
    d.arc(box, RING_START, RING_START + RING_SPAN, fill=FAINT, width=round(w * s))
    if frac > 0:
        d.arc(box, RING_START, RING_START + RING_SPAN * min(frac, 1.0), fill=FULL, width=round(w * s))
    for i in range(1, segs):  # 分段缝隙
        a = math.radians(RING_START + RING_SPAN * i / segs)
        r0, r1 = r - w - 0.5, r + 0.5
        d.line([(cx + r0 * math.cos(a)) * s, (cy + r0 * math.sin(a)) * s,
                (cx + r1 * math.cos(a)) * s, (cy + r1 * math.sin(a)) * s], fill=0, width=s)


def ring_ticks(d: ImageDraw.ImageDraw, cx: float, cy: float, r0: float, r1: float, n: int = 5) -> None:
    s = SS
    for i in range(n):
        a = math.radians(RING_START + RING_SPAN * i / (n - 1))
        d.line([(cx + r0 * math.cos(a)) * s, (cy + r0 * math.sin(a)) * s,
                (cx + r1 * math.cos(a)) * s, (cy + r1 * math.sin(a)) * s], fill=MID, width=s)


def icon(d: ImageDraw.ImageDraw, name: str, cx: float, cy: float, size: float) -> None:
    """服务图标（超采样画布）：Claude = 放射星芒，Codex = 终端框里的 >_ 。"""
    s, r = SS, size / 2
    if name == "claude":
        for k in range(3):  # 三条线交叉成六芒星号
            a = math.pi / 2 + math.pi * k / 3
            dx, dy = r * math.cos(a), r * math.sin(a)
            d.line([(cx - dx) * s, (cy - dy) * s, (cx + dx) * s, (cy + dy) * s], fill=FULL, width=round(1.5 * s))
    else:
        d.rounded_rectangle([(cx - r) * s, (cy - r) * s, (cx + r) * s, (cy + r) * s],
                            radius=2 * s, outline=FULL, width=s)
        d.line([(cx - r + 2) * s, (cy - 2) * s, (cx - r + 4) * s, cy * s, (cx - r + 2) * s, (cy + 2) * s],
               fill=FULL, width=s)
        d.line([(cx - 0.5) * s, (cy + 2) * s, (cx + r - 2) * s, (cy + 2) * s], fill=FULL, width=s)


def segbar_h(d: ImageDraw.ImageDraw, x: int, y: int, n: int, h: int, frac: float | None,
             block: int = 3, gap: int = 1) -> None:
    """横向分段条：亮块 = 剩余，最后一块按比例调灰度，暗块保留微光。"""
    for i in range(n):
        bx = x + i * (block + gap)
        d.rectangle([bx, y, bx + block - 1, y + h - 1], fill=_seg_level(i, n, frac))


def segbar_v(d: ImageDraw.ImageDraw, x: int, y_bottom: int, w: int, n: int, frac: float | None,
             seg: int = 3, gap: int = 1) -> None:
    """纵向分段柱，从下往上亮。"""
    for i in range(n):
        by = y_bottom - i * (seg + gap) - seg + 1
        d.rectangle([x, by, x + w - 1, by + seg - 1], fill=_seg_level(i, n, frac))


def _seg_level(i: int, n: int, frac: float | None) -> int:
    if frac is None:
        return FAINT if i % 2 == 0 else 0  # 没有这个窗口：隔块微光
    lit = max(0.0, min(1.0, frac)) * n
    if i < int(lit):
        return FULL
    if i == int(lit):
        return round(FAINT + (FULL - FAINT) * (lit - i))
    return FAINT


def value_text(d: ImageDraw.ImageDraw, xy: tuple[float, float], left: float | None, size: int,
               anchor: str = "rs") -> None:
    """百分比数值；告急时反白。"""
    if left is None:
        text(d, xy, "--", size, DIM, anchor)
        return
    s = f"{left:.0f}%"
    if left < LOW:
        l, t, r, b = d.textbbox(xy, s, font=font(DIN_COND, size), anchor=anchor)
        d.rectangle([l - 2, t - 1, r + 1, b + 1], fill=FULL)
        text(d, xy, s, size, 0, anchor)
    else:
        text(d, xy, s, size, FULL, anchor)


def fmt_left(seconds: float, spaced: bool = False) -> str:
    """重置倒计时：38m / 2h14m / 3d18h；spaced=True 时单位之间留空格（2h 14m），小屏上更好认。"""
    sep = " " if spaced else ""
    m = max(0, int(seconds // 60))
    if m < 60:
        return f"{m}m"
    h, m = divmod(m, 60)
    if h < 24:
        return f"{h}h{sep}{m:02d}m"
    dd, h = divmod(h, 24)
    return f"{dd}d{sep}{h}h"


def primary(svc: dict) -> tuple[str, tuple[float, float]]:
    """最紧张的窗口（剩余最少）。"""
    key = min(svc, key=lambda k: svc[k][0])
    return key, svc[key]


def frac_of(svc: dict, k: str) -> float | None:
    return svc[k][0] / 100 if k in svc else None


# ---------- 示例数据 ----------
# 示例数据：dashboard.py mockups / push 用来预览布局。
NOW = time.time()
SAMPLE = {
    "claude": {"5h": (72, NOW + 2 * 3600 + 14 * 60), "7d": (61, NOW + 3 * 86400 + 4 * 3600)},
    "codex": {"7d": (90, NOW + 3 * 86400 + 17 * 3600)},
}
SAMPLE_FULL = {  # 刚重置：两个都是 100%，检查最宽的情况
    "claude": {"5h": (100, NOW + 5 * 3600), "7d": (100, NOW + 6 * 86400 + 23 * 3600)},
    "codex": {"7d": (100, NOW + 6 * 86400 + 23 * 3600)},
}
SAMPLE_LOW = {
    "claude": {"5h": (12, NOW + 38 * 60), "7d": (44, NOW + 3 * 86400 + 4 * 3600)},
    "codex": SAMPLE["codex"],
}


# ---------- 布局 D：双环仪表 ----------
# 每个服务一个仪表：外环 = 7 天，内环 = 5 小时，环心 = 最紧张窗口；右侧是读数面板。

def layout_d(data: dict) -> Image.Image:
    img = Image.new("L", (W, H), 0)
    cols = [(name, g * 104) for g, name in enumerate(("claude", "codex"))]

    def paint(d: ImageDraw.ImageDraw) -> None:
        for name, x0 in cols:
            svc, cx, cy = data[name], x0 + 32, 32
            ring(d, cx, cy, 30.5, 4, frac_of(svc, "7d"), segs=20)
            ring(d, cx, cy, 22, 2.5, frac_of(svc, "5h"))
            ring_ticks(d, cx, cy, 24.5, 26)
            icon(d, name, cx, cy + 26, 10)
            if primary(svc)[1][0] < LOW:  # 告急：环心点亮成实心圆
                d.ellipse([(cx - 17) * SS, (cy - 17) * SS, (cx + 17) * SS, (cy + 17) * SS], fill=FULL)
    overlay(img, supersampled(paint))

    d = ImageDraw.Draw(img)
    for name, x0 in cols:
        svc, cx, cy = data[name], x0 + 32, 32
        key, (left, _) = primary(svc)
        ink = 0 if left < LOW else FULL
        num = f"{left:.0f}"
        text(d, (cx, cy + 6), num, 24 if len(num) < 3 else 18, ink, "ms", DIN_ALT)
        text(d, (cx, cy + 16), key.upper(), 11, 0 if left < LOW else MID, "ms")

        px, pr = x0 + 67, x0 + 101  # 读数面板
        text(d, (px, 9), NAMES[name], 12, FULL)
        d.line([px, 12, pr, 12], fill=DIM)
        d.line([px, 12, px + 8, 12], fill=FULL)
        for i, k in enumerate(WINDOWS):
            y = 16 + i * 24
            text(d, (px, y + 8), k.upper(), 10, MID)
            value_text(d, (pr + 1, y + 9), svc[k][0] if k in svc else None, 13)
            segbar_h(d, px, y + 12, 9, 2, frac_of(svc, k))
            text(d, (px, y + 22), ("T-" + fmt_left(svc[k][1] - NOW)) if k in svc else "NO LIMIT", 9,
                 MID if k in svc else DIM)
    for y in range(0, H, 3):
        d.point((103, y), fill=FAINT)
    return img


# ---------- 布局 E：赛博面板 ----------
# 顶栏 + 反白切角标签 + 分段条 + 数值 + 倒计时。

def layout_e(data: dict) -> Image.Image:
    img = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(img)

    text(d, (0, 9), "QUOTA MONITOR", 11, FULL)
    text(d, (W, 9), time.strftime("%H:%M"), 11, FULL, "rs")
    for x in range(86, 170, 3):
        d.point((x, 5), fill=FAINT)
    for i in range(3):  # 状态灯
        d.rectangle([174 + i * 4, 3, 175 + i * 4, 6], fill=(FULL, MID, DIM)[i])
    d.line([0, 11, W - 1, 11], fill=DIM)
    d.line([0, 11, 76, 11], fill=FULL)

    for g, name in enumerate(("claude", "codex")):
        top, svc = 14 + g * 25, data[name]
        d.rectangle([0, top, 19, top + 22], fill=FULL)
        d.polygon([(16, top), (19, top), (19, top + 3)], fill=0)  # 切角
        text(d, (10, top + 16), TAGS[name], 13, 0, "ms")
        for j, k in enumerate(WINDOWS):
            cy = top + 5 + j * 12
            text(d, (24, cy + 4), k.upper(), 11, MID)
            segbar_h(d, 40, cy - 3, 26, 7, frac_of(svc, k))
            value_text(d, (175, cy + 5), svc[k][0] if k in svc else None, 14)
            if k in svc:
                text(d, (W, cy + 4), fmt_left(svc[k][1] - NOW), 11, MID, "rs")
    return img


# ---------- 布局 F：能量柱 ----------
# 四根纵向分段柱：Claude 5H / 7D | Codex 5H / 7D，右侧读数。

def layout_f(data: dict) -> Image.Image:
    img = Image.new("L", (W, H), 0)
    groups = [(name, g * 106) for g, name in enumerate(("claude", "codex"))]

    def paint(d: ImageDraw.ImageDraw) -> None:
        for name, x0 in groups:
            icon(d, name, x0 + 5, 5, 10)
    overlay(img, supersampled(paint))

    d = ImageDraw.Draw(img)
    for name, x0 in groups:
        svc = data[name]
        text(d, (x0 + 13, 9), NAMES[name], 12, FULL)
        d.line([x0, 12, x0 + 101, 12], fill=DIM)
        d.line([x0, 12, x0, 14], fill=DIM)
        d.line([x0 + 101, 12, x0 + 101, 14], fill=DIM)
        for j, k in enumerate(WINDOWS):
            cx0 = x0 + j * 51
            segbar_v(d, cx0 + 2, 62, 13, 11, frac_of(svc, k))
            for t in range(0, 11, 2):  # 刻度
                d.point((cx0 + 17, 62 - t * 4 - 1), fill=DIM if t % 10 else MID)
            value_text(d, (cx0 + 50, 32), svc[k][0] if k in svc else None, 20)
            text(d, (cx0 + 50, 45), k.upper(), 11, MID, "rs")
            text(d, (cx0 + 50, 59), fmt_left(svc[k][1] - NOW) if k in svc else "N/A", 11,
                 MID if k in svc else DIM, "rs")
    return img


# ---------- 简洁版组件 ----------

class Scene:
    """先在超采样图层上画所有图形（抗锯齿），再在上面写字。"""

    def __init__(self) -> None:
        self.shapes: list = []
        self.texts: list = []

    def shape(self, fn) -> None:
        self.shapes.append(fn)

    def text(self, *args, **kwargs) -> None:
        self.texts.append(lambda d: text(d, *args, **kwargs))

    def render(self) -> Image.Image:
        img = Image.new("L", (W, H), 0)
        overlay(img, supersampled(lambda L: [fn(L) for fn in self.shapes]))
        d = ImageDraw.Draw(img)
        for fn in self.texts:
            fn(d)
        return img


def clock_icon(d: ImageDraw.ImageDraw, cx: float, cy: float, r: float = 3.5, fill: int = MID,
               stroke: float = 1) -> None:
    s, w = SS, round(stroke * SS)
    d.ellipse([(cx - r) * s, (cy - r) * s, (cx + r) * s, (cy + r) * s], outline=fill, width=w)
    d.line([cx * s, cy * s, cx * s, (cy - r + 1.5) * s], fill=fill, width=w)
    d.line([cx * s, cy * s, (cx + r - 1.5) * s, cy * s], fill=fill, width=w)


def pill_bar(d: ImageDraw.ImageDraw, x: float, y: float, w: float, h: float, frac: float | None,
             fill: int = FULL) -> None:
    """圆头进度条：暗轨道 + 亮填充；frac=None 时画成虚线轨道。"""
    s = SS
    if frac is None:
        for xx in range(int(x), int(x + w), 4):
            d.rectangle([xx * s, y * s, (xx + 2) * s - 1, (y + h) * s - 1], fill=FAINT)
        return
    d.rounded_rectangle([x * s, y * s, (x + w) * s - 1, (y + h) * s - 1], radius=h / 2 * s, fill=FAINT)
    if frac > 0:
        fw = max(h, w * min(frac, 1.0))
        d.rounded_rectangle([x * s, y * s, (x + fw) * s - 1, (y + h) * s - 1], radius=h / 2 * s, fill=fill)


def reset_label(sc: Scene, right: float, baseline: float, svc: dict, k: str, size: int,
                fill: int = MID, min_x: float = -1e9, none_fill: int = DIM) -> None:
    """右对齐的"时钟图标 + 倒计时"；图标放不下（会碰到 min_x 左侧的内容）时省略图标。"""
    if k not in svc:
        sc.text((right, baseline), "no limit", size, none_fill, "rs")
        return
    if svc[k][1] is None:  # 重置时间未知（例如窗口已重置、还没拿到新数据）
        sc.text((right, baseline), "--", size, fill, "rs")
        return
    s = fmt_left(svc[k][1] - NOW, spaced=True)
    sc.text((right, baseline), s, size, fill, "rs")
    left_ink = right - text_width(s, size) + ink_span(s, size)[0]
    r = max(3.5, size * 0.27)  # 图标跟随字号放大
    cy = baseline - size * 0.36  # 与大写字母的垂直中心对齐
    if left_ink - 2 * r - 3 >= min_x + 4:
        sc.shape(lambda L: clock_icon(L, left_ink - r - 3, cy, r, fill, 1.2 if size >= 14 else 1))


# ---------- 简洁版 B：左右双栏，大数字 = 最紧张的窗口 ----------
# 可读性规则：小字不低于 15px（大写高 11 像素）；文字只用两档亮度（数值全亮、标签 200）；
# 每栏只有三行信息：名称 / 大数字 + 倒计时 / 另一个窗口。

LABEL = 200


def layout_b(data: dict) -> Image.Image:
    sc = Scene()
    for g, name in enumerate(("claude", "codex")):
        x0, svc = g * 106, data.get(name) or {}
        r = x0 + 101

        # 第 1 行：图标 + 名称
        sc.shape(lambda L, n=name, x0=x0: icon(L, n, x0 + 5.5, 6.5, 11))
        sc.text((x0 + 15, 12), NAMES[name], 15, FULL)

        if not svc:  # 还没有数据
            sc.text((x0, 41), "--", 34, LABEL, path=DIN_ALT)
            sc.text((r, 41), "no data", 15, LABEL, "rs")
            sc.shape(lambda L, x0=x0: pill_bar(L, x0, 45, 102, 5, 0))
            continue

        key, (left, _) = primary(svc)
        other = next((k for k in WINDOWS if k != key), None)

        # 第 2 行：大数字（最紧张的窗口），右侧是窗口名和重置倒计时
        num, low = f"{left:.0f}", left < LOW
        size = 34 if len(num) < 3 else 30
        nl, nr = ink_span(num, size, DIN_ALT)
        pl, pr = ink_span("%", 16, DIN_ALT)
        nx = x0 + (3 if low else 0) - nl          # 让数字墨迹从栏左边缘开始
        px = nx + nr + 2 - pl                     # % 号紧贴数字
        if low:  # 告急：数字放进亮色胶囊，字变黑
            sc.shape(lambda L, x0=x0, end=px + pr: L.rounded_rectangle(
                [x0 * SS, 14 * SS, (end + 3) * SS, 44 * SS], radius=3 * SS, fill=FULL))
        sc.text((nx, 41), num, size, 0 if low else FULL, path=DIN_ALT)
        sc.text((px, 41), "%", 16, 0 if low else FULL, path=DIN_ALT)
        sc.text((r, 26), key.upper(), 15, LABEL, "rs")
        reset_label(sc, r, 41, svc, key, 17, FULL, min_x=px + pr)

        # 进度条
        sc.shape(lambda L, x0=x0, f=left / 100: pill_bar(L, x0, 45, 102, 5, f))

        # 第 3 行：另一个窗口
        if other:
            sc.text((x0, 63), other.upper(), 15, LABEL)
            if other in svc:
                ox = x0 + ink_span(other.upper(), 15)[1] + 5
                sc.text((ox, 63), f"{svc[other][0]:.0f}%", 15, FULL)
            reset_label(sc, r, 63, svc, other, 15, LABEL, none_fill=LABEL)

    img = sc.render()
    ImageDraw.Draw(img).line([103, 1, 103, 62], fill=DIM)
    return img


# ---------- 简洁版 A：上下分块，每个窗口一行 ----------

def layout_a(data: dict) -> Image.Image:
    sc = Scene()
    for g, name in enumerate(("claude", "codex")):
        y0, svc = g * 33, data[name]
        sc.shape(lambda L, n=name, y0=y0: icon(L, n, 5, y0 + 5.5, 10))
        sc.text((14, y0 + 10), NAMES[name], 12, FULL)
        for j, k in enumerate(WINDOWS):
            y = y0 + 12 + j * 10
            sc.text((14, y + 8), k.upper(), 11, MID)
            sc.shape(lambda L, y=y, f=frac_of(svc, k): pill_bar(L, 32, y + 2.5, 108, 4, f))
            left = svc[k][0] if k in svc else None
            sc.texts.append(lambda d, y=y, left=left: value_text(d, (166, y + 9), left, 12))
            reset_label(sc, W, y + 8, svc, k, 11)

    img = sc.render()
    for x in range(0, W, 3):
        img.putpixel((x, 31), FAINT)
    return img


LAYOUTS = {
    "B": ("B 左右双栏（高清）", lambda: layout_b(SAMPLE)),
    "B-low": ("B 告急状态", lambda: layout_b(SAMPLE_LOW)),
    "B-full": ("B 刚重置（100%，最宽的情况）", lambda: layout_b(SAMPLE_FULL)),
    "A": ("A 上下分块（高清）", lambda: layout_a(SAMPLE)),
    "A-low": ("A 告急状态", lambda: layout_a(SAMPLE_LOW)),
    "D": ("D 双环仪表：外环 7 天、内环 5 小时", lambda: layout_d(SAMPLE)),
    "D-low": ("D 告急状态", lambda: layout_d(SAMPLE_LOW)),
    "E": ("E 赛博面板：分段条 + 数值 + 倒计时", lambda: layout_e(SAMPLE)),
    "E-low": ("E 告急状态", lambda: layout_e(SAMPLE_LOW)),
    "F": ("F 能量柱：四根纵向分段柱", lambda: layout_f(SAMPLE)),
    "F-low": ("F 告急状态", lambda: layout_f(SAMPLE_LOW)),
}


# ---------- 预览 ----------

def oled_zoom(img: Image.Image, scale: int = 4) -> Image.Image:
    """放大成点阵并量化到 16 级，模拟键盘上的实际观感。"""
    out = Image.new("RGB", (img.width * scale, img.height * scale), (8, 8, 10))
    d = ImageDraw.Draw(out)
    px = img.load()
    for y in range(img.height):
        for x in range(img.width):
            v = (px[x, y] * 15 + 127) // 255 * 17
            c = (max(v, 16), max(v, 16), max(v, 19))
            d.rectangle([x * scale, y * scale, x * scale + scale - 2, y * scale + scale - 2], fill=c)
    return out


def mockups(path: str, keys: list[str]) -> None:
    label_font = ImageFont.truetype("/System/Library/Fonts/Hiragino Sans GB.ttc", 26)
    tiles = [(LAYOUTS[k][0], oled_zoom(LAYOUTS[k][1]())) for k in keys]
    tw, th = tiles[0][1].size
    pad, label_h, cols = 30, 44, 2
    rows = (len(tiles) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * tw + (cols + 1) * pad, rows * (th + label_h + pad) + pad), (245, 245, 247))
    d = ImageDraw.Draw(sheet)
    for i, (title, tile) in enumerate(tiles):
        x = pad + (i % cols) * (tw + pad)
        y = pad + (i // cols) * (th + label_h + pad)
        d.text((x, y), title, font=label_font, fill=(30, 30, 30))
        sheet.paste(tile, (x, y + label_h))
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    sheet.save(path)
    print(f"已生成：{path}")


def main() -> None:
    if len(sys.argv) >= 2 and sys.argv[1] == "mockups":
        mockups("mockups/clean.png", ["B", "B-low", "B-full", "A"])
        mockups("mockups/hires.png", ["D", "D-low", "E", "E-low", "F", "F-low"])
    elif len(sys.argv) == 3 and sys.argv[1] == "push" and sys.argv[2] in LAYOUTS:
        from rog_oled import Keyboard, pack
        kb = Keyboard()
        try:
            kb.wait_connected()
            kb.send_banner(pack(LAYOUTS[sys.argv[2]][1]()), "black")
            print(f"已发送布局 {sys.argv[2]}")
        finally:
            kb.close()
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
