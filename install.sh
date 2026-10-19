#!/bin/bash
# 把额度看板和菜单栏程序安装为开机自启的后台服务（launchd 用户代理）。重复运行即重装。
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$HOME/Library/Logs"
SVC_PATH="$HOME/.local/bin:$HOME/.local/node/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export PATH="$SVC_PATH:$PATH"

if [ ! -x "$DIR/.venv/bin/python" ]; then
  python3 -m venv "$DIR/.venv"
fi
"$DIR/.venv/bin/pip" install -q --disable-pip-version-check -r "$DIR/requirements.txt"  # 已装好的会跳过，升级后补装新依赖
if [ ! -d "$DIR/usage/node_modules" ]; then  # Claude 用量查询依赖 Claude Agent SDK（Node）
  (cd "$DIR/usage" && npm install --no-audit --no-fund)
fi

mkdir -p "$HOME/Library/LaunchAgents" "$LOG_DIR"

# 用法：install_agent <标签> <脚本> <日志文件> <KeepAlive 的值>
install_agent() {
  local label=$1 script=$2 log=$3 keepalive=$4
  local plist="$HOME/Library/LaunchAgents/$label.plist"
  cat > "$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key>
  <array>
    <string>$DIR/.venv/bin/python</string>
    <string>$DIR/$script</string>
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

  launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
  for _ in $(seq 20); do  # 等旧服务完全退出，否则 bootstrap 会报 Input/output error
    launchctl print "gui/$(id -u)/$label" >/dev/null 2>&1 || break
    sleep 0.5
  done
  launchctl bootstrap "gui/$(id -u)" "$plist"
}

install_agent com.rog-oled.quota quota_daemon.py "$LOG_DIR/rog-oled.log" "<true/>"
# 菜单栏程序：崩溃时自动重启；从菜单里"退出"则保持退出，下次登录时再启动
install_agent com.rog-oled.menubar menubar.py "$LOG_DIR/rog-oled-menubar.log" \
  "<dict><key>SuccessfulExit</key><false/></dict>"

echo "已安装并启动看板和菜单栏程序，日志在 $LOG_DIR/rog-oled.log 和 $LOG_DIR/rog-oled-menubar.log"

CLAUDE_BIN=$(ls "$DIR"/usage/node_modules/@anthropic-ai/claude-agent-sdk-darwin-*/claude 2>/dev/null | head -1)
if [ -n "$CLAUDE_BIN" ] && ! "$CLAUDE_BIN" auth status 2>/dev/null | grep -q '"loggedIn": true'; then
  echo
  echo "Claude 额度还需要登录一次（在浏览器里完成）："
  echo "  \"$CLAUDE_BIN\" auth login --claudeai"
fi
