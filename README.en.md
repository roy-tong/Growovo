<p align="center"><img src="docs/assets/logo.png" width="400" alt="Growovo"></p>
<p align="center"><a href="README.md">简体中文</a> · <strong>English</strong></p>

# Growovo

**You set the goal. Growovo carries out the work.**

From product facts and objectives to content, channel execution, follow-up and result readback, Growovo delivers the whole business workflow. You set the direction; Growovo turns every step into completed work.

[Website](https://roy-tong.github.io/Growovo/) · [Controlled-run example](https://roy-tong.github.io/Growovo/examples/controlled-run.en.html) · [Operations guide](docs/grow-autonomous-operations.md) · [Platform matrix](docs/platform-automation-matrix.md)

[![tests](https://github.com/roy-tong/Growovo/actions/workflows/tests.yml/badge.svg)](https://github.com/roy-tong/Growovo/actions/workflows/tests.yml)
[![PyPI](https://img.shields.io/pypi/v/growovo)](https://pypi.org/project/growovo/)
[![License](https://img.shields.io/badge/License-MIT-blue)](LICENSE)

## From content sent to business execution delivered

Publishing is only one step. Growovo connects objectives, content, channels, replies and readback so users receive a complete delivery and the next move:
- **Find the right people.** Start from the product and a vertical scene to discover audiences more likely to care.
- **Say something worth answering.** Create contextual content for each audience and channel instead of copying one message everywhere.
- **Keep the work moving.** Content, plans, execution and readback advance in one workflow.
- **Turn responses into next steps.** Replies, activation and sales return to the growth goal so you can decide what to continue or change.

## A growth team’s workflow for a one-person product

| Job | What Growovo helps you do |
|---|---|
| Positioning | Turn product facts into audiences, goals and channel strategy |
| Content | Draft and review posts, replies and outreach |
| Execution | Move work through Email, X, GitHub, Reddit, Hacker News, LinkedIn and Discord workflows |
| Follow-up | Bring pending replies and the next action to the front of the day |
| Learning | Build the next strategy from receipts and business events |

Email can run automatically, while X and community channels join your daily operating workflow. See the [platform automation matrix](docs/platform-automation-matrix.md) for the way each channel works.

## Run a growth example

```bash
pipx install growovo
mkt demo
mkt grow platforms
```

Or from source:

```bash
git clone https://github.com/roy-tong/Growovo.git && cd Growovo
python3 mkt.py demo
```

The offline synthetic example produces content checks, execution receipts and a daily result card. [Open the controlled-run proof](https://roy-tong.github.io/Growovo/examples/controlled-run.en.html) to see how one batch moves from start to result.

## From one launch to a daily practice

```bash
python3 mkt.py init
python3 mkt.py grow launch g1 --facts facts.json \
  --goal commercial.conversations --channels email
python3 mkt.py grow daily g1
python3 mkt.py grow review g1 --days 7
```

Start with one small batch, then connect content production, channel execution, replies and review into your own operating rhythm. CLI, MCP and Agent Skill share the same execution logic.

## Why we built Growovo

We once sent 73 tweets in one night and gained 0 new stars. The most useful lesson was not the zero. It was the product judgment behind it: send volume is not growth. Teams need a connected way to move from audience and content to replies and results.

[Read the launch autopsy](docs/launch-autopsy-20261105.md) · [Compare tools](docs/compare.md) · [Feature status](docs/feature-status.md)

## Keep exploring

- [Operations configuration](docs/grow-autonomous-operations.md)
- [GTM design](docs/gtm-os/07-object-motion-design.md)
- [MCP and Agent Skill](skills/growovo-gtm/SKILL.md)
- [Case library](docs/cases/README.en.md)
- [Contributing](CONTRIBUTING.md) · [llms.txt](llms.txt) · [AGENTS.md](AGENTS.md)

Growovo is MIT open source. Run your first result with `mkt demo`, then make it your growth workflow.
