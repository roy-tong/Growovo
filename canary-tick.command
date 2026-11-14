#!/usr/bin/env bash
# 前台常驻 tick：每 30 分钟跑一次 canary 闭环（Ctrl-C 停止）。
# 用途：launchd 被 macOS 文档目录保护拦住时，用这个脚本完成 7 天读数校准。
set -u
cd "$(dirname "$0")"
mkdir -p data/grow/canary
while true; do
  python3 mkt.py grow tick canary --deliver >> data/grow/canary/tick.log 2>&1
  sleep 1800
done
