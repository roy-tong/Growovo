# Email 发送管线 · 2026-11-17 实战沉淀

> 一次实战打通的完整记录：WorkingPlan 供应商 BD（10 封分销合作邀请，含 6.9MB PDF 附件）从 Gmail 网页版自动化失败到 SMTP+SOCKS 全部成功。沉淀为 `channels/email_smtp.py` 的三项能力与环境事实库。

## 结论一页

| 事项 | 结论 |
| --- | --- |
| 本机发 Email 的正解 | **SMTP（smtp.gmail.com:465）经系统代理（SOCKS5 127.0.0.1:7890）**，凭据 GMAIL_APP_PASSWORD（~/.am_env） |
| Gmail 网页版自动化 | 能填能挂附件但**点发送不可靠**（DOM 多窗漂移），只作只读回读（sent 校验、回复扫描）用 |
| 事务语义 | 发送前异常 = FAILED；提交后异常（超时/连接重置/回执丢失）= INDETERMINATE，整批中止、CLI 非零、账本禁自动重试，人工核实后 `mkt ledger resolve` |
| 本次战绩 | canary 1 + 批量 9 = 10 封，全部 SMTP accepted；纪律：随机间隔 60-90s、连续 2 失败熔断、日志幂等 |

## 环境事实（这台机器）

1. **直连 SMTP 不通**：`smtp.gmail.com` 465/587 直连均超时（国内网络环境），HTTP(S) 走系统代理正常。系统代理：ApexCore，`127.0.0.1:7890`，HTTP+SOCKS 同端口（`scutil --proxy` 可查）。
2. **凭据**：Gmail App Password（16 位），存 `~/.am_env` 的 `GMAIL_APP_PASSWORD`（newoutreach 惯例）；Growovo 侧等价变量为 `MKT_EMAIL_APP_PASSWORD`。
3. **大附件**：6.9MB PDF 经代理上传，socket timeout 60s 会超时，**需 ≥300s**。

## 新增能力（channels/email_smtp.py）

| 环境变量 | 作用 | 缺省 |
| --- | --- | --- |
| `MKT_EMAIL_SOCKS` | SOCKS5 代理 `host:port`（如 `127.0.0.1:7890`）；设了走代理，不设直连 | 空=直连 |
| `MKT_EMAIL_TIMEOUT` | SMTP socket 超时秒数；带大附件时设 300 | 60 |

- 附件：队列 item 新增 `attachments: ["/path/to/file.pdf"]`，按扩展名推断 MIME（pdf/png/jpg/gif/txt/html，兜底 octet-stream）。相对路径按队列文件所在目录解析。
- **发送前检查**：附件不存在、不是文件、或空文件时，`send_queue` 直接 `SystemExit`；dry-run 也跑同一检查——不要等到 `--go` 才炸在第一条。
- 实现细节：SOCKS 用 PySocks 按需导入（无代理环境零依赖），`MKT_EMAIL_SOCKS` 格式不对或没装 PySocks 都直接给可读错误；重载 `SMTP_SSL._get_socket` 实现（Python 3.9 smtplib 无 socket factory 参数）。类级 `_ctx = ssl.create_default_context()`——实例 `self._context` 在 connect 前不可用（踩过）。
- **带附件的邮件必须用 `EmailMessage`**：`MIMEText` 不是 multipart，`add_attachment` 只存在于 `EmailMessage`；旧写法在第一次带附件实跑时就会 `AttributeError`（2026-11-17 复核发现，已由 `tests/test_email_smtp.py` 锁住）。

## 发送纪律（沿用 newoutreach，一次都别省）

1. **canary 先行**：批量前先单发 1 封，SMTP accepted 后再放全量。
2. **随机间隔** 60-90s（矩阵档 30-60s 只用于 50+/日的成熟波）。
3. **熔断**：连续 2 封失败停发复盘；单波退信率 >5% 全停。
4. **幂等**：sent_log 按 campaign+recipient 去重，重跑不重发。
5. **suppress 名单**检查在发前，不在发后。

## Gmail 网页版自动化的适用边界（失败经验，别再踩）

- 能做到：全 evaluate 填写（收件人 combobox/主题/正文 textarea 用 native setter + input 事件）、**粘贴事件注入附件**（`ClipboardEvent('paste', {clipboardData: DataTransfer+File})`，需附件源带 CORS 头的本地 http 服务）。
- 做不到（可接受成本内）：稳定点发送——compose 是多窗堆叠 DOM（反复 goto 会叠出 N 个 dialog）、按钮 aria-label 带不可见方向控制符、actionability 检查对隐藏窗误中。
- 定位：网页版只用于**只读**（读回复、扫已发送回读校验），写操作一律走 SMTP。

## 本次实跑数据

- 任务：WorkingPlan 供应商 BD（agent服务商解决方案/ai输出/WorkingPlan供应商BD-20261117/）
- 发送器：`send_wp_bd.py`（canary+批量，含熔断与 sent_log，模式同本通道）
- 结果：10/10 sent（wps、kimi、aiqicha、chanjing、wondercv、fagougou、ivysci、cubox、immersivetranslate、vistring）
- 附件：上海彰迅公司简介-2026.pdf（6.88MB）每封必带
