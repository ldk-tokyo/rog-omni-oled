#!/bin/bash
# 把额度看板安装为开机自启的后台服务（launchd 用户代理）。重复运行即重装。
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
LABEL="com.rog-oled.quota"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Library/Logs/rog-oled.log"
SVC_PATH="$HOME/.local/bin:$HOME/.local/node/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export PATH="$SVC_PATH:$PATH"

if [ ! -x "$DIR/.venv/bin/python" ]; then
  python3 -m venv "$DIR/.venv"
  "$DIR/.venv/bin/pip" install -q -r "$DIR/requirements.txt"
fi
if [ ! -d "$DIR/usage/node_modules" ]; then  # Claude 用量查询依赖 Claude Agent SDK（Node）
  (cd "$DIR/usage" && npm install --no-audit --no-fund)
fi

mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$DIR/.venv/bin/python</string>
    <string>$DIR/quota_daemon.py</string>
  </array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>$SVC_PATH</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
EOF

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
for _ in $(seq 20); do  # 等旧服务完全退出，否则 bootstrap 会报 Input/output error
  launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || break
  sleep 0.5
done
launchctl bootstrap "gui/$(id -u)" "$PLIST"

echo "已安装并启动，日志在 $LOG"

CLAUDE_BIN=$(ls "$DIR"/usage/node_modules/@anthropic-ai/claude-agent-sdk-darwin-*/claude 2>/dev/null | head -1)
if [ -n "$CLAUDE_BIN" ] && ! "$CLAUDE_BIN" auth status 2>/dev/null | grep -q '"loggedIn": true'; then
  echo
  echo "Claude 额度还需要登录一次（在浏览器里完成）："
  echo "  \"$CLAUDE_BIN\" auth login --claudeai"
fi
