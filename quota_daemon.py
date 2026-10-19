#!/usr/bin/env python3
"""后台常驻：把 Claude Code / Codex 的剩余额度实时画到键盘 OLED 屏上（默认布局 A）。

数据来源
- Codex：常驻一个 `codex app-server`，每 2 分钟调用 account/rateLimits/read。
- Claude：每 5 分钟运行 usage/claude_usage.mjs（Claude Agent SDK 的用量接口，
  不调用模型），需要先用 SDK 自带的 claude 登录一次（见 README）。
- 键鼠超过 2 分钟没人碰时两边都暂停查询。

顺带共享键盘电量和额度
- 守护进程独占着接收器，其他程序（比如菜单栏电量工具）读不到键盘。所以键盘在线时
  每分钟查一次电量，写到 ~/Library/Caches/rog-oled/keyboard.json。
- 额度数据和键盘连接状态写到 ~/Library/Caches/rog-oled/status.json（菜单栏程序
  menubar.py 读它）；内容变化时立即写，否则每分钟写一次，兼作心跳。

发送策略
- 每 5 秒检查一次；画面变化才发送（倒计时按分钟走，最多每分钟一次）。
- 不执行"保存"命令，不写键盘存储；只在启动后第一次发送时保存一次。
- 键盘休眠、或键鼠超过 2 分钟没人碰时不发送；恢复后立即补发。

  python quota_daemon.py            # 前台运行（Ctrl+C 退出）
  python quota_daemon.py --once     # 只取一次数据、发一帧就退出
  python quota_daemon.py --no-keyboard   # 不连接键盘，只给菜单栏程序提供数据
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import select
import shutil
import signal
import subprocess
import time

import dashboard
from rog_oled import Keyboard, pack

HERE = os.path.dirname(os.path.abspath(__file__))
CLAUDE_USAGE_JS = os.path.join(HERE, "usage", "claude_usage.mjs")
TICK_S = 5
CODEX_EVERY_S = 120
CLAUDE_EVERY_S = 300  # 官方建议用量接口不要查得太频繁
IDLE_LIMIT_S = 120
BATTERY_EVERY_S = 60
BATTERY_FILE = os.path.expanduser("~/Library/Caches/rog-oled/keyboard.json")
STATUS_EVERY_S = 60
STATUS_FILE = os.path.expanduser("~/Library/Caches/rog-oled/status.json")

log = logging.getLogger("rog-oled")


def find_command(name: str) -> str | None:
    # launchd 的 PATH 很短，补上常见安装位置
    extra = [os.path.expanduser(p) for p in ("~/.local/bin", "~/.local/node/bin", "/opt/homebrew/bin", "/usr/local/bin")]
    return shutil.which(name, path=os.pathsep.join([os.environ.get("PATH", "")] + extra))


# ---------- Codex ----------

class CodexClient:
    """常驻的 codex app-server（JSON-RPC over stdio）；进程挂了就下次重启。"""

    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self.next_id = 1

    def _request(self, method: str, params, timeout: float = 20):
        rid = self.next_id
        self.next_id += 1
        self.proc.stdin.write(json.dumps({"id": rid, "method": method, "params": params}) + "\n")
        self.proc.stdin.flush()
        deadline = time.time() + timeout
        while time.time() < deadline:
            ready, _, _ = select.select([self.proc.stdout], [], [], 0.5)
            if not ready:
                continue
            line = self.proc.stdout.readline()
            if not line:
                raise RuntimeError("codex app-server 退出了")
            msg = json.loads(line)
            if msg.get("id") == rid:
                if "error" in msg:
                    raise RuntimeError(msg["error"])
                return msg.get("result")
        raise TimeoutError(method)

    def _start(self) -> None:
        path = find_command("codex")
        if not path:
            raise RuntimeError("找不到 codex 命令")
        self.proc = subprocess.Popen([path, "app-server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, text=True, bufsize=1)
        self._request("initialize", {"clientInfo": {"name": "rog-oled", "version": "0.1"}})
        self.proc.stdin.write(json.dumps({"method": "initialized"}) + "\n")
        self.proc.stdin.flush()

    def close(self) -> None:
        if self.proc:
            self.proc.terminate()
            self.proc = None

    def fetch(self) -> dict | None:
        """返回 {"5h"/"7d": {"used": 已用%, "resets_at": 秒}}；失败返回 None。"""
        try:
            if not self.proc or self.proc.poll() is not None:
                self._start()
            rl = (self._request("account/rateLimits/read", None) or {}).get("rateLimits") or {}
        except Exception as e:  # noqa: BLE001 — 任何失败都只记日志，下次再试
            log.warning("Codex 查询失败：%s", e)
            self.close()
            return None
        out = {}
        for slot in ("primary", "secondary"):
            w = rl.get(slot)
            if not w:
                continue
            key = "5h" if (w.get("windowDurationMins") or 0) <= 300 else "7d"
            out[key] = {"used": float(w["usedPercent"]), "resets_at": w.get("resetsAt")}
        return out


# ---------- Claude ----------

def fetch_claude() -> dict | None:
    """运行 claude_usage.mjs，返回 {"5h"/"7d": {"used", "resets_at"}}；失败返回 None。"""
    node = find_command("node")
    if not node:
        log.warning("Claude 查询失败：找不到 node")
        return None
    try:
        out = subprocess.run([node, CLAUDE_USAGE_JS], capture_output=True, text=True, timeout=60,
                             cwd=os.path.dirname(CLAUDE_USAGE_JS)).stdout
        r = json.loads(out.strip().splitlines()[-1])
    except (subprocess.TimeoutExpired, ValueError, IndexError) as e:
        log.warning("Claude 查询失败：%s", e)
        return None
    if not r.get("ok"):
        log.warning("Claude 查询失败：%s", r.get("reason"))
        return None
    return r["windows"]


# ---------- 渲染与发送 ----------

def to_view(windows: dict | None, now: float) -> dict:
    """{"5h": {"used", "resets_at"}} → 看板格式 {"5h": (剩余%, 重置时间)}；已过重置时间的窗口视为 100%。"""
    view = {}
    for k, w in (windows or {}).items():
        reset = w.get("resets_at")
        if reset and now >= reset:
            view[k] = (100.0, None)
        else:
            view[k] = (max(0.0, 100.0 - w["used"]), reset)
    return view


def idle_seconds() -> float:
    """距离上次键鼠操作的秒数。"""
    out = subprocess.run(["/usr/sbin/ioreg", "-c", "IOHIDSystem", "-r", "-d", "1"],
                         capture_output=True, text=True).stdout
    m = re.search(r'"HIDIdleTime" = (\d+)', out)
    return int(m.group(1)) / 1e9 if m else 0.0


def write_json(path: str, data: dict) -> None:
    """先写临时文件再改名，读的一方不会读到半截。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def write_battery(status: tuple[int, bool], now: float) -> None:
    write_json(BATTERY_FILE, {"battery": status[0], "charging": status[1], "updated_at": int(now)})


def render(claude: dict | None, codex: dict | None, now: float) -> list[int]:
    dashboard.NOW = now
    return pack(dashboard.layout_columns({"claude": to_view(claude, now), "codex": to_view(codex, now)}))


def run(once: bool = False, keyboard: bool = True) -> None:
    codex_client = CodexClient()
    try:
        _loop(codex_client, once, keyboard)
    finally:
        codex_client.close()


def _loop(codex_client: CodexClient, once: bool, keyboard: bool) -> None:
    """keyboard=False 时只取数、写 status.json，不碰接收器（只装 Mac 菜单栏时用）。"""
    codex, codex_at, codex_ok_at = None, 0.0, None
    claude, claude_at, claude_ok_at = None, 0.0, None
    kb: Keyboard | None = None
    # connected / asleep / no_receiver / error / disabled；离开电脑时保持上次的值
    kb_state = "unknown" if keyboard else "disabled"
    last_sent: list[int] | None = None
    saved_once = False
    battery_at = 0.0
    shared, shared_at = None, 0.0

    while True:
        now = time.time()
        active = once or idle_seconds() <= IDLE_LIMIT_S
        if active and now - codex_at >= CODEX_EVERY_S:  # 离开电脑时不查询
            fresh = codex_client.fetch()
            if fresh is not None:
                codex, codex_ok_at = fresh, int(now)
            codex_at = now
        if active and now - claude_at >= CLAUDE_EVERY_S:
            fresh = fetch_claude()
            if fresh is not None:
                claude, claude_ok_at = fresh, int(now)
            claude_at = now

        if keyboard:
            frame = render(claude, codex, now)
            try:
                if kb is None:
                    kb = Keyboard()
                if not active:
                    last_sent = None  # 你回来后立即补发
                elif not kb.is_connected():
                    kb_state = "asleep"
                    last_sent = None  # 键盘醒来后立即补发
                    if once:
                        log.warning("键盘没响应（可能在休眠）")
                        return
                else:
                    kb_state = "connected"
                    if now - battery_at >= BATTERY_EVERY_S:
                        status = kb.battery_status()
                        if status is not None:
                            write_battery(status, now)
                        battery_at = now
                    if frame != last_sent:
                        kb.send_banner(frame, "black", save=not saved_once)
                        saved_once = True
                        last_sent = frame
                        log.info("已刷新屏幕（Claude：%s；Codex：%s）", claude, codex)
            except (OSError, ValueError, SystemExit) as e:
                log.warning("键盘通信失败：%s", e)
                kb_state = "no_receiver" if isinstance(e, SystemExit) else "error"  # Keyboard() 找不到接收器时 sys.exit
                if kb:
                    kb.close()
                kb, last_sent = None, None

        state = {"claude": {"windows": claude, "fetched_at": claude_ok_at},
                 "codex": {"windows": codex, "fetched_at": codex_ok_at},
                 "keyboard": kb_state}
        if state != shared or now - shared_at >= STATUS_EVERY_S:
            write_json(STATUS_FILE, {**state, "updated_at": int(now)})
            shared, shared_at = state, now

        if once:
            break
        time.sleep(TICK_S)


def _on_sigterm(*_) -> None:
    raise KeyboardInterrupt  # launchd 停止服务时走同样的清理路径


def main() -> None:
    ap = argparse.ArgumentParser(description="键盘 OLED 额度看板（后台常驻）")
    ap.add_argument("--once", action="store_true", help="只发一帧就退出")
    ap.add_argument("--no-keyboard", action="store_true",
                    help="不连接键盘，只取数并写 status.json（给菜单栏程序用）")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%m-%d %H:%M:%S")
    signal.signal(signal.SIGTERM, _on_sigterm)
    try:
        run(args.once, keyboard=not args.no_keyboard)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
