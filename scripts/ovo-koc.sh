#!/usr/bin/env bash
# ovo KOC 战役统一入口：固定仓库目录 + 打开 CDP 身份观测。
# 真实发送需要身份门禁（AC-H）；不设 MKT_CDP_IDENTITY=1 时一律 fail closed。
set -euo pipefail
cd "$(dirname "$0")/.."
export MKT_CDP_IDENTITY="${MKT_CDP_IDENTITY:-1}"
export X_CDP_URL="${X_CDP_URL:-http://127.0.0.1:9222}"
export MKT_CDP_IDENTITY_URL="${MKT_CDP_IDENTITY_URL:-$X_CDP_URL}"
exec python3 mkt.py "$@"
