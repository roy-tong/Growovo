# live X 验收 · 干跑彩排记录（G-4 工件）· 2026-11-17

- 正常干跑：`python3 scripts/acceptance_live_x.py --dry-run` → **exit 0**（完整 JSON 见同目录 .json，含六方分离表与证据路径）
- 回读不一致彩排：`--dry-run --simulate-readback-mismatch` → **exit 1**（强制 indeterminate 停机，禁止重发）
- 真实 X 动作**未执行**：需人工运行并显式 `--authorize-by <人名>`，用隔离 profile + 独立 CDP 端口（如 9223）+ 专用测试推文；脚本对 9222 与 campaign profiles/ 显式拒绝。
- 证据分级：本文件为 local 离线干跑（synthetic）；live_external_write/receipt_verified 待用户执行后由脚本落日志。
