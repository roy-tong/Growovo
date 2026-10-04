#!/usr/bin/env bash
# 启动营销自动化用的调试 Chrome 会话（模式与 x-ops-automation launch_x.command 相同，PROVEN）：
#  1) 首次运行：复制本机 Chrome 登录 profile（排除缓存）到 profiles/main/
#     ——Chrome 136+ 安全限制：默认 profile 下 --remote-debugging-port 会被忽略，
#       必须使用非默认 --user-data-dir。复制的 profile 含全部站点登录态（X/Gmail/Reddit/LinkedIn）。
#  2) 用该 profile 启动 Chrome 开 CDP 端口 9222
# 双击运行；保持 Chrome 开着，再执行 mkt 命令。
set -euo pipefail

PORT="${MKT_CDP_PORT:-9222}"
BASE="$(cd "$(dirname "$0")" && pwd)"
PROFILE_DIR="$BASE/profiles/main"
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
SRC_PROFILE="$HOME/Library/Application Support/Google/Chrome/Default"

if lsof -ti:"$PORT" >/dev/null 2>&1; then
  echo "端口 $PORT 已在监听（调试会话已就绪）。直接运行 mkt 命令即可。"
  exit 0
fi

if [[ ! -d "$PROFILE_DIR/Default" ]]; then
  echo "首次运行：复制 Chrome 登录 profile（保留各站点登录态，约 1-2GB）…"
  mkdir -p "$PROFILE_DIR/Default"
  rsync -a --quiet \
    --exclude='Cache' --exclude='Cache_Data' --exclude='Code Cache' --exclude='GPUCache' \
    --exclude='DawnGraphiteCache' --exclude='DawnWebGPUCache' --exclude='GrShaderCache' \
    --exclude='ShaderCache' --exclude='Service Worker' --exclude='CacheStorage' \
    --exclude='component_crx_cache' --exclude='OptimizationHints' --exclude='GraphiteDawnCache' \
    --exclude='media_cache' --exclude='Application Cache' --exclude='blob_storage' --exclude='File System' \
    --exclude='BrowserMetrics' --exclude='JumpListIcons*' --exclude='Top Sites' \
    --exclude='History*' --exclude='Visited Links' --exclude='Last Session' --exclude='Last Tabs' \
    --exclude='Network Action Predictor' --exclude='DIPS' --exclude='InterestActionHistory' \
    --exclude='url_param_scanner' --exclude='WebRTC Logs' --exclude='Extension State' \
    --exclude='FileTypePolicies' --exclude='Media History' --exclude='Notifications' \
    --exclude='PreviewThumbnail' --exclude='Reporting and NEL' --exclude='Sessions' \
    --exclude='SharedDictionary' --exclude='Storage' --exclude='Sync Data' --exclude='Translate' \
    --exclude='ZxcvbnData' --exclude='SafetyTips' --exclude='Subresource Filter' \
    "$SRC_PROFILE/" "$PROFILE_DIR/Default/" || true
  echo "profile 复制完成。"
fi

"$CHROME" --user-data-dir="$PROFILE_DIR" --remote-debugging-port="$PORT" \
  --no-first-run --no-default-browser-check >/dev/null 2>&1 &

for i in $(seq 1 30); do
  if curl -s "http://127.0.0.1:$PORT/json/version" >/dev/null 2>&1; then
    echo "Chrome 调试会话就绪（端口 ${PORT}）。"
    echo "确认各站点已登录后运行：python3 mkt.py check"
    exit 0
  fi
  sleep 1
done
echo "Chrome 调试端口未能就绪，请手动检查。" >&2
exit 1
