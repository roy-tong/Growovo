#!/bin/bash
# 四通道并行采集：小红书 / B站 / 公众号 / Reddit
# 各通道独立进程、独立预算；任一通道失败不影响其他通道。
set -u
cd "$(dirname "$0")/.." || exit 1
LOG=/tmp/harvest_all
mkdir -p "$LOG"

echo "[$(date +%H:%M)] 启动四通道" | tee "$LOG/main.log"

# 1) 小红书：CDP 点卡片，日预算 120
nohup python3 -u scripts/harvest_plan.py --platform xhs --budget 400 --gap 15 \
  > "$LOG/xhs.log" 2>&1 &

# 2) B站：搜索→下音频→Vosk 转写
nohup python3 -u scripts/harvest_bilibili.py --all --limit 100 --max-seconds 900 \
  > "$LOG/bili.log" 2>&1 &

# 3) 公众号：微信读书通道，多订阅各取最新
nohup python3 -u /tmp/wx_bulk.py > "$LOG/wechat.log" 2>&1 &

# 4) Reddit：redlib 镜像 + 浏览器
nohup python3 -u scripts/harvest_reddit.py --all --limit 10 \
  > "$LOG/reddit.log" 2>&1 &

wait
echo "[$(date +%H:%M)] 四通道结束" | tee -a "$LOG/main.log"
