# Vendor 来源

引入自 x-ops-automation（github: roy-tong/x-ops-automation，commit 8309263，MIT）。

**不改写原则**（02 架构文档 §6.1）：本目录脚本的选择器、节流、回读校验、防错人逻辑
均为真实账号实测产物。上游修复选择器时同步本目录，不在本仓库内改写。

vendor 自带的安全（动作内节流/校验/防重发）保留；框架层（adapter.py）只补
跨渠道账本与门禁，不重复节流。
