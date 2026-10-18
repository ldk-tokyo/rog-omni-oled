#!/usr/bin/env python3
"""在 Mac 上控制 ROG 键盘（经 ROG Omni 无线接收器）的 OLED 屏幕。

协议移植自 Delido/signalrgb-plugins（Apache-2.0）中的 ASUS_Keyboard_Protocol.js，
这是逆向得到的非官方协议。像素格式是实测确认的：横幅区 208×64 像素，
4 位灰度（16 级），每字节装 2 个像素（低半字节在左），按行排列，
拆成每包 59 字节发送。

用法示例：
  python rog_oled.py status
  python rog_oled.py text "Hello 你好"
  python rog_oled.py text "Hi" --preview preview.png --dry-run
  python rog_oled.py image logo.png
  python rog_oled.py animation 0        # 恢复内置的 ROG Logo 动画
"""
from __future__ import annotations

import argparse
import math
import os
import sys
import time

import hid
from PIL import Image, ImageDraw, ImageFont, ImageOps

VID = 0x0B05
PID_OMNI = 0x1ACE
USAGE_PAGE = 0xFF00  # 接收器上负责键盘的厂商自定义接口

WIDTH = 208           # 横幅区宽度（像素）
ROW_BYTES = WIDTH // 2  # 每字节 2 个像素
CHUNK = 59            # 每个数据包携带的字节数

# 横幅背景：名称 -> (编号, 画布高度)
STYLES = {
    "black": (0xFF, 64),
    "stripes": (0x00, 46),
    "rog-diagonal": (0x01, 46),
    "rog-repeat": (0x02, 46),
    "checkered": (0x03, 46),
    "chevron": (0x04, 46),
}

ANIMATIONS = [
    "ROG Logo",
    "Republic of Gamers",
    "Cityscape",
    "ROG Republic of Gamers",
    "Floating Hearts",
    "ROG Triangle",
]

FONT_CANDIDATES = [
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
]


# ---------- 设备通信 ----------

class Keyboard:
    def __init__(self) -> None:
        paths = [d["path"] for d in hid.enumerate(VID, PID_OMNI) if d["usage_page"] == USAGE_PAGE]
        if not paths:
            sys.exit("没找到 ROG Omni 接收器（0B05:1ACE），请确认接收器已插好。")
        self.dev = hid.device()
        self.dev.open_path(paths[0])

    def close(self) -> None:
        self.dev.close()

    def write(self, data: list[int], pause_ms: int = 0) -> None:
        self.dev.write(bytes(data + [0] * (64 - len(data))))
        if pause_ms:
            time.sleep(pause_ms / 1000)

    def query(self, cmd: list[int]) -> list[int] | None:
        while self.dev.read(64, 5):  # 清掉缓冲区里的旧回包
            pass
        self.write(cmd, 20)
        for _ in range(10):
            r = self.dev.read(64, 200)
            if r and r[0] == cmd[0] and r[1] == cmd[1]:
                return r
        return None

    def is_connected(self) -> bool:
        r = self.query([0x02, 0x12, 0x03])
        return bool(r) and r[5] == 1

    def wait_connected(self, timeout_s: float = 60) -> None:
        if self.is_connected():
            return
        print(f"键盘在休眠：按一下键盘（比如 Shift）唤醒它，最多等 {timeout_s:.0f} 秒…", flush=True)
        deadline = time.time() + timeout_s
        while not self.is_connected():
            if time.time() > deadline:
                sys.exit("等待超时，键盘一直没响应。")
            time.sleep(0.5)
        time.sleep(0.3)  # 刚唤醒，稍等一下再发数据

    def battery(self) -> int | None:
        s = self.battery_status()
        return s[0] if s else None

    def battery_status(self) -> tuple[int, bool] | None:
        """(电量%, 是否在充电)；第 9 字节是充电标志。"""
        r = self.query([0x02, 0x12, 0x01])
        return (r[6], bool(r[9])) if r else None

    def save(self) -> None:
        self.write([0x02, 0x50, 0x55], 5)

    def send_banner(self, data: list[int], style: str, save: bool = True) -> None:
        """data：pack() 打包好的字节；save=False 时不执行"保存"命令（不写键盘存储）。"""
        index, height = STYLES[style]
        packets = math.ceil(ROW_BYTES * height / CHUNK)
        data = data + [0] * (packets * CHUNK - len(data))

        header = [0x02, 0x62, 0x00, 0x00, 0x00, index, 0x01, 0xD0, 0x00, height,
                  0x00, packets & 0xFF, packets >> 8]
        self.write(header, 10)
        for i in range(packets):
            counter = packets - 1 - i  # 包序号倒数
            chunk = data[i * CHUNK:(i + 1) * CHUNK]
            # 无线中转需要每包间隔约 15ms
            self.write([0x02, 0x62, 0x01, counter & 0xFF, counter >> 8] + chunk, 15)
        self.write([0x02, 0x62, 0x02], 20)
        if save:
            self.save()

    def set_animation(self, index: int) -> None:
        self.write([0x02, 0x61, 0x00, 0x00, 0x00, index], 20)
        self.save()


# ---------- 画面渲染 ----------

def find_font(path: str | None) -> str:
    for p in [path] if path else FONT_CANDIDATES:
        if p and os.path.exists(p):
            return p
    sys.exit("找不到可用字体，请用 --font 指定一个 .ttf/.ttc 文件。")


def render_text(text: str, height: int, size: int | None, font_path: str) -> Image.Image:
    def fit(sz: int):
        font = ImageFont.truetype(font_path, sz)
        return font, font.getbbox(text)

    if size is None:  # 自动选能放下的最大字号
        size = height
        while size > 8:
            font, bbox = fit(size)
            if bbox[2] - bbox[0] <= WIDTH and bbox[3] - bbox[1] <= height:
                break
            size -= 1
    font, bbox = fit(size)
    if bbox[2] - bbox[0] > WIDTH:
        print(f"提示：文字太长，{size}px 下超出屏幕宽度，右侧会被裁掉。", file=sys.stderr)

    img = Image.new("L", (WIDTH, height), 0)
    draw = ImageDraw.Draw(img)  # 屏幕支持灰度，保留抗锯齿
    x = (WIDTH - (bbox[2] - bbox[0])) // 2 - bbox[0]
    y = (height - (bbox[3] - bbox[1])) // 2 - bbox[1]
    draw.text((max(x, -bbox[0]), y), text, font=font, fill=255)
    print(f"字号 {size}px，字体 {os.path.basename(font_path)}")
    return img


def render_image(path: str, height: int, invert: bool, dither: bool) -> Image.Image:
    src = ImageOps.contain(Image.open(path).convert("L"), (WIDTH, height))
    if invert:
        src = ImageOps.invert(src)
    canvas = Image.new("L", (WIDTH, height), 0)
    canvas.paste(src, ((WIDTH - src.width) // 2, (height - src.height) // 2))
    if not dither:
        return canvas
    # 抖动到屏幕实际的 16 级灰度，渐变更平滑
    palette = Image.new("P", (1, 1))
    palette.putpalette([round(i * 255 / 15) for i in range(16) for _ in range(3)])
    return canvas.convert("RGB").quantize(palette=palette, dither=Image.Dither.FLOYDSTEINBERG).convert("L")


def pack(img: Image.Image) -> list[int]:
    """L 图像（0–255）→ 16 级灰度，每字节 2 像素，低半字节在左。"""
    img = img.convert("L")
    w, h = img.size
    px = img.load()
    out = []
    for y in range(h):
        for x in range(0, w, 2):
            left = (px[x, y] * 15 + 127) // 255
            right = (px[x + 1, y] * 15 + 127) // 255 if x + 1 < w else 0
            out.append(right << 4 | left)
    return out


def save_preview(img: Image.Image, path: str, scale: int = 4) -> None:
    big = img.convert("L").resize((img.width * scale, img.height * scale), Image.NEAREST)
    ImageOps.colorize(big, black="#111111", white="#7fd4ff").save(path)
    print(f"预览图已保存：{path}")


# ---------- 命令行 ----------

def push(img: Image.Image, args: argparse.Namespace) -> None:
    if args.preview:
        save_preview(img, args.preview)
    if args.dry_run:
        return
    kb = Keyboard()
    try:
        kb.wait_connected()
        kb.send_banner(pack(img), args.style)
        print("已发送到键盘。")
    finally:
        kb.close()


def main() -> None:
    p = argparse.ArgumentParser(description="控制 ROG 键盘 OLED 屏幕（经 Omni 接收器）")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="查看键盘连接状态和电量")

    def add_common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--style", choices=STYLES, default="black", help="横幅背景（默认 black）")
        sp.add_argument("--preview", metavar="PNG", help="另存一张放大的预览图")
        sp.add_argument("--dry-run", action="store_true", help="只生成预览，不发送")

    t = sub.add_parser("text", help="显示一行文字")
    t.add_argument("text")
    t.add_argument("--size", type=int, help="字号（像素），默认自动")
    t.add_argument("--font", help="字体文件路径")
    add_common(t)

    i = sub.add_parser("image", help="显示一张图片（自动缩放、转 16 级灰度）")
    i.add_argument("path")
    i.add_argument("--invert", action="store_true", help="明暗反转")
    i.add_argument("--no-dither", action="store_true", help="不抖动，直接量化到 16 级灰度")
    add_common(i)

    a = sub.add_parser("animation", help="切回内置动画")
    a.add_argument("index", type=int, choices=range(len(ANIMATIONS)),
                   help="; ".join(f"{n}={name}" for n, name in enumerate(ANIMATIONS)))

    args = p.parse_args()

    if args.cmd == "status":
        kb = Keyboard()
        try:
            connected = kb.is_connected()
            print("键盘：", "已连接" if connected else "未响应（可能在休眠）")
            if connected:
                print("电量：", f"{kb.battery()}%")
        finally:
            kb.close()
    elif args.cmd == "text":
        height = STYLES[args.style][1]
        push(render_text(args.text, height, args.size, find_font(args.font)), args)
    elif args.cmd == "image":
        height = STYLES[args.style][1]
        push(render_image(args.path, height, args.invert, not args.no_dither), args)
    elif args.cmd == "animation":
        kb = Keyboard()
        try:
            kb.wait_connected()
            kb.set_animation(args.index)
            print(f"已切换到内置动画：{ANIMATIONS[args.index]}")
        finally:
            kb.close()


if __name__ == "__main__":
    main()
