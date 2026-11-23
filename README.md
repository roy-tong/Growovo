<p align="center"><img src="docs/assets/logo.png" width="400" alt="Growovo"></p>
<p align="center"><strong>简体中文</strong> · <a href="README.en.md">English</a></p>

# Growovo

**你定目标，Growovo 把业务执行到底**

从产品事实与目标出发，Growovo 把内容做出来、把渠道执行跑起来、把回复跟下去，再把结果回读给你。它提供端到端交付的业务执行：你决定方向，Growovo 让每一步真正发生。

[官网](https://roy-tong.github.io/Growovo/) · [受控执行样例](https://roy-tong.github.io/Growovo/examples/controlled-run.html) · [自动运营说明](docs/grow-autonomous-operations.md) · [平台矩阵](docs/platform-automation-matrix.md)

[![tests](https://github.com/roy-tong/Growovo/actions/workflows/tests.yml/badge.svg)](https://github.com/roy-tong/Growovo/actions/workflows/tests.yml)
[![PyPI](https://img.shields.io/pypi/v/growovo)](https://pypi.org/project/growovo/)
[![License](https://img.shields.io/badge/License-MIT-blue)](LICENSE)

## 从一批内容发出去，到一套业务执行交付

发出内容只是过程。Growovo 把目标、内容、渠道、回复和回读连成一条执行链，让用户拿到完整交付与下一步：
- **找到对的人**：从产品事实和垂直场景出发，发现更可能关心你的人。
- **说出值得回应的话**：为不同对象和渠道生成有上下文的内容，而不是同一段文案到处复制。
- **让行动持续发生**：内容、计划、执行和回读在同一个工作流里推进。
- **把回应变成下一步**：回复、激活和成交回到增长目标，帮助你决定继续、调整还是换一种做法。

## 一个人的产品，也可以有一套增长团队的工作方式

| 工作 | Growovo 帮你做什么 |
|---|---|
| 定位 | 把产品事实转成受众、目标与渠道策略 |
| 内容 | 生成并检查帖子、回复和外联内容 |
| 执行 | 在 Email、X、GitHub、Reddit、Hacker News、LinkedIn 与 Discord 等工作流中推进 |
| 跟进 | 把待回复对象和下一动作放到每天的工作前面 |
| 学习 | 从真实回执与业务事件中积累下一轮策略 |

Email 可以自动运行，X 与社区渠道可以接入你的日常运营流程。每个渠道的具体使用方式见[平台自动执行矩阵](docs/platform-automation-matrix.md)。

## 跑一个增长样例

```bash
pipx install growovo
mkt demo
mkt grow platforms
```

也可以从源码运行：

```bash
git clone https://github.com/roy-tong/Growovo.git && cd Growovo
python3 mkt.py demo
```

离线合成样例会生成内容检查、执行回执和日结果卡。[打开受控执行证明页](https://roy-tong.github.io/Growovo/examples/controlled-run.html)，看看一个批次如何从开始走到结果。

## 从一次发布，到每天发生

```bash
python3 mkt.py init
python3 mkt.py grow launch g1 --facts facts.json \
  --goal commercial.conversations --channels email
python3 mkt.py grow daily g1
python3 mkt.py grow review g1 --days 7
```

你可以从一个小批次开始，再让内容生产、渠道执行、回复处理与结果复盘逐步连成自己的运营节奏。CLI、MCP 和 Agent Skill 使用同一套执行逻辑。

## 为什么做 Growovo

我们曾在一晚发出 73 条推文，新增 star 为 0。那次实验留下的最大收获不是一个失败数字，而是一个产品判断：发布量不是增长，团队需要的是从受众、内容到回复和结果的完整工作方式。

[读完整发布复盘](docs/launch-autopsy-20261105.md) · [与其他工具对比](docs/compare.md) · [功能状态](docs/feature-status.md)

## 继续探索

- [自动运营配置](docs/grow-autonomous-operations.md)
- [GTM 设计](docs/gtm-os/07-object-motion-design.md)
- [MCP 与 Agent Skill](skills/growovo-gtm/SKILL.md)
- [案例库](docs/cases/README.md)
- [贡献指南](CONTRIBUTING.md) · [llms.txt](llms.txt) · [AGENTS.md](AGENTS.md)

Growovo 以 MIT 开源。先从 `mkt demo` 跑出一个结果，再把它变成你的增长工作流。
