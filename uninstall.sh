#!/bin/bash
# 停止并移除额度看板和菜单栏程序的后台服务。
set -euo pipefail

for LABEL in com.rog-oled.quota com.rog-oled.menubar; do
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
done
echo "已停止并移除后台服务。"
