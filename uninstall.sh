#!/bin/bash
# 停止并移除额度看板后台服务。
set -euo pipefail

LABEL="com.rog-oled.quota"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
echo "已停止并移除后台服务。"
