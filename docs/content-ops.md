# 内容运营能力（小红书 · 公众号 · 博客）

`core/content_ops.py` 把 2026-11 会话里形成的内容策略做成可测试的本地能力：渠道规则、brief、审稿和多渠道路排期。它不发布、不定时、不发送。

## 命令行

```bash
python3 mkt.py content rules xiaohongshu
python3 mkt.py content brief xiaohongshu --topic "给硬件装AI，先列10条指令" \
  --audience "硬件产品经理" --product Needle --evidence "官方 README，未实测" --cta B
python3 mkt.py content audit xiaohongshu --file draft.md --title "硬件加AI，先别装聊天框"
python3 mkt.py content audit xiaohongshu --file 14-首周14篇正文与CTA.md --titles xhs_titles.json
python3 mkt.py content plan --start 2026-11-07 --days 14
```

MCP：`mkt_content`（action = rules / brief / audit / review / plan），只读。

## 三渠道契约

| 渠道 | 频率 | 长度 | 标题 | CTA |
|---|---|---|---|---|
| 小红书 | 2/天，14/周 | 300—700 字 | ≤20 字（编辑预算） | 关注＋私信（A 送产品 / B 送方法 / C 约协助） |
| 公众号 | 2/周 | 1200—3500 字 | ≤64 字 | 评论区 / 关注 |
| 博客 | 1/周 | 800—4200 字 | ≤64 字 | 仓库 / 模板 / 更新记录 |

## 审稿

阻断项：元叙述（“这篇只回答一件事”）、CTA 标签、小红书站外联系方式、小红书标题超长、小红书缺 CTA、自有产品缺作者关系、无证据绝对化表述；公开渠道（小红书/公众号）出现 `E0—E5` 等证据编码或 `strict_master`/`corpus_role` 等内部字段名。

提醒：长度、开源项目缺交代、产品只在结尾出现、软性绝对化、行业黑话。

提示：专业词没括号解释、无证据/实测标记、小红书缺“今天能做的一步”。

审稿通过只代表符合本地写作契约，不代表平台会给流量、事实为真或读者会关注。

## 边界

- A/B/C 的私信 CTA 是用户指定写法；平台私信限制需按账号状态核实，不写微信号/加V/二维码。
- 价格与版本是设计目标，不是已上架售价。
- 第三方项目许可证逐项核对；AGPL 项目不做闭源付费封装。

## 整篇审稿：读者、论证与版本

结构检查通过后，仍需调用整篇审稿。机器只检查记录是否完整及与当前正文一致，不自动给“文章好读”打分。

```bash
python3 mkt.py content review blog --file draft.md > review.json
# 编辑/agent 整篇阅读，填写 outline、checks、claims、claim_coverage_note
python3 mkt.py content review blog --file draft.md --record review.json
```

MCP 使用 `mkt_content` 的 `action=review`；传 channel、text 生成待填清单，再带 record 校验。CLI/MCP 共用 `core/editorial_review.py`。

十项标准见 [读者优先审稿策略](playbooks/战术/reader-first-editorial.md)。改稿后 SHA256 变化，旧审稿记录失效；不能拿旧版本“0阻断”证明新版本可发布。不同人群相除算转化率、不同采样窗口算趋势、无识别设计下因果结论，会按审稿记录中的断言类型提示错误。程序不保证编辑没有漏报或错填断言。

审稿记录无需包含私人会话原文、账号凭据或用户数据。发布授权、回执与增长数据仍走原来的独立流程。
