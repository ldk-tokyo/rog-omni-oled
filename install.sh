#!/bin/bash
# 安装为开机自启的后台服务（launchd 用户代理）。重复运行即重装，也可以借此切换安装的部分。
#
#   ./install.sh              # 交互式选择要装的部分
#   ./install.sh --keyboard   # 只装键盘部分：OLED 屏上的额度看板
#   ./install.sh --mac        # 只装 Mac 部分：菜单栏（不需要键盘）
#   ./install.sh --all        # 两个都装
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$HOME/Library/Logs"
SVC_PATH="$HOME/.local/bin:$HOME/.local/node/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export PATH="$SVC_PATH:$PATH"

usage() {
  echo "用法：./install.sh [--keyboard | --mac | --all]"
  echo "  --keyboard  只装键盘部分：OLED 屏上的额度看板（需要 ROG 键盘和 Omni 接收器）"
  echo "  --mac       只装 Mac 部分：菜单栏显示额度和系统状态（不需要键盘）"
  echo "  --all       两个都装"
  echo "不带参数时会提示选择。"
}

MODE=""
case "${1:-}" in
  --keyboard) MODE=keyboard ;;
  --mac) MODE=mac ;;
  --all) MODE=all ;;
  -h|--help) usage; exit 0 ;;
  "") ;;
  *) usage >&2; exit 1 ;;
esac

if [ -z "$MODE" ]; then
  if [ -t 0 ]; then
    echo "要安装哪些部分？"
    echo "  1) 只装键盘部分：OLED 屏上的额度看板（需要 ROG 键盘和 Omni 接收器）"
    echo "  2) 只装 Mac 部分：菜单栏显示额度和系统状态（不需要键盘）"
    echo "  3) 两个都装"
    read -r -p "请输入 1、2 或 3 [默认 3]：" choice
    case "${choice:-3}" in
      1) MODE=keyboard ;;
      2) MODE=mac ;;
      3) MODE=all ;;
      *) echo "无效的选择：$choice" >&2; exit 1 ;;
    esac
  else
    MODE=all  # 非交互运行（比如脚本里调用）且没给参数时，两个都装
  fi
fi

if [ ! -x "$DIR/.venv/bin/python" ]; then
  python3 -m venv "$DIR/.venv"
fi
"$DIR/.venv/bin/pip" install -q --disable-pip-version-check -r "$DIR/requirements.txt"  # 已装好的会跳过，升级后补装新依赖
if [ ! -d "$DIR/usage/node_modules" ]; then  # Claude 用量查询依赖 Claude Agent SDK（Node）
  (cd "$DIR/usage" && npm install --no-audit --no-fund)
fi

mkdir -p "$HOME/Library/LaunchAgents" "$LOG_DIR"

stop_agent() {
  launchctl bootout "gui/$(id -u)/$1" 2>/dev/null || true
  for _ in $(seq 20); do  # 等旧服务完全退出，否则 bootstrap 会报 Input/output error
    launchctl print "gui/$(id -u)/$1" >/dev/null 2>&1 || break
    sleep 0.5
  done
}

# 用法：install_agent <标签> <日志文件> <KeepAlive 的值> <脚本> [脚本参数...]
install_agent() {
  local label=$1 log=$2 keepalive=$3
  shift 3
  local plist="$HOME/Library/LaunchAgents/$label.plist" args="" a
  for a in "$@"; do
    args="$args
    <string>$a</string>"
  done
  cat > "$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key>
  <array>
    <string>$DIR/.venv/bin/python</string>$args
  </array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>$SVC_PATH</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key>$keepalive
  <key>ThrottleInterval</key><integer>30</integer>
  <key>StandardOutPath</key><string>$log</string>
  <key>StandardErrorPath</key><string>$log</string>
</dict>
</plist>
EOF

  stop_agent "$label"
  launchctl bootstrap "gui/$(id -u)" "$plist"
}

remove_agent() {
  stop_agent "$1"
  rm -f "$HOME/Library/LaunchAgents/$1.plist"
}

# 守护进程两种模式都要装：键盘部分靠它驱动屏幕，Mac 部分靠它取额度数据
if [ "$MODE" = mac ]; then
  install_agent com.rog-oled.quota "$LOG_DIR/rog-oled.log" "<true/>" "$DIR/quota_daemon.py" --no-keyboard
else
  install_agent com.rog-oled.quota "$LOG_DIR/rog-oled.log" "<true/>" "$DIR/quota_daemon.py"
fi

if [ "$MODE" = keyboard ]; then
  remove_agent com.rog-oled.menubar  # 之前装过的话一并移除
else
  # 菜单栏程序：崩溃时自动重启；从菜单里"退出"则保持退出，下次登录时再启动
  install_agent com.rog-oled.menubar "$LOG_DIR/rog-oled-menubar.log" \
    "<dict><key>SuccessfulExit</key><false/></dict>" "$DIR/menubar.py"
fi

case "$MODE" in
  keyboard) echo "已安装并启动键盘部分（OLED 额度看板），日志在 $LOG_DIR/rog-oled.log" ;;
  mac) echo "已安装并启动 Mac 部分（菜单栏），日志在 $LOG_DIR/rog-oled.log 和 $LOG_DIR/rog-oled-menubar.log" ;;
  all) echo "已安装并启动键盘部分和 Mac 部分，日志在 $LOG_DIR/rog-oled.log 和 $LOG_DIR/rog-oled-menubar.log" ;;
esac

CLAUDE_BIN=$(ls "$DIR"/usage/node_modules/@anthropic-ai/claude-agent-sdk-darwin-*/claude 2>/dev/null | head -1)
# stdin 接 /dev/null：前面从终端读过选择后，claude 会一直等终端输入
AUTH=$([ -n "$CLAUDE_BIN" ] && "$CLAUDE_BIN" auth status 2>/dev/null </dev/null || true)
if [ -n "$CLAUDE_BIN" ] && ! printf '%s' "$AUTH" | grep -q '"loggedIn": true'; then
  echo
  echo "Claude 额度还需要登录一次（在浏览器里完成）："
  echo "  \"$CLAUDE_BIN\" auth login --claudeai"
fi
