<p align="center">
  <img src="docs/assets/logo.png" width="400" alt="Growovo">
</p>

<p align="center"><strong>Delegate the repetitive growth work. Keep the business moving.</strong><br>
<em>AI-native growth operations with clear authorization, persistent relationships and separate outcome evidence.</em></p>

<p align="center"><a href="README.md">简体中文</a> · <strong>English</strong></p>

Growovo is an AI-native growth service for independent developers, open-source maintainers and small teams without a dedicated operations function. Provide product facts, a target audience and authorized channels. Growovo finds qualified opportunities, handles routine operations, follows up relationships, and keeps execution, engagement, activation and retention on separate evidence layers.

[OVO unified website](https://roy-tong.github.io/ovo/en.html) · [Controlled-run example](https://roy-tong.github.io/Growovo/examples/controlled-run.en.html) · [Current capabilities](docs/feature-status.md) · [中文 README](README.md)

```bash
pipx install growovo
mkt demo    # Zero network and accounts: authorization, offline receipts, three refusals and a result card; writes ./data/demo
```

## A general agent can write the strategy. Growovo handles the daily work.

A useful growth service does not generate more marketing copy. It removes the daily need to monitor, filter, reply, follow up and organize the business yourself.

### Three growth services

| Service | You provide | Growovo works toward |
| --- | --- | --- |
| **Managed Growth** | A live product, operating goals and channel authorization | Continuous market observation, qualified opportunities, authorized actions, follow-ups and operating reviews |
| **First Users** | A product link, target users and necessary permissions | Real demand discovery, first-use progression, onboarding help and activation evidence |
| **Distribution Intelligence** | A website, open-source project or agent application | SEO/GEO checks, agent discovery and installation tests, first value and repeat measurement |

Start with **Managed Growth**: first prove that it removes repetitive work, then evaluate growth outcomes. First Users is a goal-oriented service, not a promise of a fixed number of users.

## The first win: the business moves without your constant supervision

A useful first result is not a longer GTM document. It is the work you no longer have to do:

- **Real events create real work.** A support question, reply, product failure or qualified opportunity becomes the next business task.
- **Existing users are not treated as new leads.** Conversation history, current relationship state, failed trials and follow-ups inform the next move.
- **Execution has a next step.** Platform readback, real replies, first value and repeat use remain separate observations.
- **No qualified opportunity can mean no external action.** The system does not manufacture outreach to satisfy a volume target.

You decide whom to serve, what is allowed and when to continue or stop. New accounts, new permissions, high-impact claims and irreversible commitments require the appropriate review. Unknown external results are never automatically retried.

## Let the agent choose the path. Keep execution deterministic.

```text
Real market event
    ↓
Host agent: assess the opportunity and next move
    ↓
Growovo: authorization, quota, suppression, execution, readback and recovery
    ↓
Real interaction / product event / activation or retention evidence
    ↓
The next operating or product decision
```

CLI, MCP and the persistent runtime share the same business state. Ask for progress, pause, resume or change the goal in natural language without learning internal cycle, lease or ledger concepts.

## A negative launch changed the metric

One historical launch sent **73 posts in 11 hours and produced zero new GitHub Stars**. It did not run through Growovo’s ledger that night. The result does not prove that X never works, but it does prove that send volume is not adoption.

The measurement now follows a different ladder:

**Real task → authorized execution → qualified interaction → first value → repeat use → production integration.**

[Read the full launch autopsy](docs/launch-autopsy-20261105.md), or open a [replayable controlled execution case](https://roy-tong.github.io/Growovo/examples/controlled-run.en.html).

## Start with a real product

Run the offline demo first, then choose the real channels to connect:

```bash
# Inspect current channel capabilities
python3 mkt.py grow platforms

# A person grants authorization; the agent never authorizes itself
python3 mkt.py policy enable --actions email:message --by "$USER"

# Create the operating plan
python3 mkt.py grow launch g1 --facts facts.json \
  --goal commercial.conversations --channels email

# Arm after reviewing the scope; background claims depend on the actual host
python3 mkt.py grow arm g1
python3 mkt.py grow daemon g1

# Read business progress, exceptions and next actions
python3 mkt.py grow status g1 --explain --json
python3 mkt.py grow daily g1
python3 mkt.py grow review g1 --days 7
```

Official APIs and SMTP are preferred. Existing write primitives may run through WebMonitor-managed tabs inside a named owner-attested window. Unauthorized accounts, CAPTCHA/challenge bypass and risk-control evasion remain forbidden. See the [platform automation matrix](docs/platform-automation-matrix.md).

## Evidence today, proof still needed

- **Available now:** persistent operating state, authorization and quotas, real channel readback, inbound replies, lifecycle and follow-up state, growth experiments, proof exports and local agent-adoption tests.
- **Next to validate:** seven-day delegated operations, real activation and retention, independent agent discovery and selection, and a second valid operating batch.
- **Adoption measurement:** execution, qualified interactions, human time, first value and reuse stay separate. Sends, impressions and Stars do not prove growth.

See [feature status](docs/feature-status.md) for implementation boundaries. A local demo, synthetic event or successful process launch is not external adoption.

## Continue

- [Autonomous operations setup](docs/grow-autonomous-operations.md)
- [Growth Service](docs/growth-service.md)
- [Real cases](docs/cases/README.en.md)
- [Platform automation matrix](docs/platform-automation-matrix.md)
- [Agent Skill](skills/growovo-gtm/SKILL.md) · [llms.txt](llms.txt) · [AGENTS.md](AGENTS.md)
- Community: [Discord](https://discord.gg/UqrM8MCXb) · [X / @elliwoodtong](https://x.com/elliwoodtong)

Growovo is MIT open source. Bring one real product and one operating goal. Start with the work you least want to repeat next week.

**Verification labels:** PROVEN / PORTED / UNVERIFIED / DISABLED describe technical verification, not business growth or external adoption. Execution, engagement, activation and incremental growth are measured separately.
