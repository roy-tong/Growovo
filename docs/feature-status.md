# Growovo: implementation status

Reviewed 2026-11-04 against the local integration branch. **Available means local implementation and local tests, not production validation on every platform or proof of business outcomes.** Many changes in this branch are not yet committed.

## Verification snapshot

- `python3 -m unittest discover -s tests`: **402 tests pass**.
- `python3 mkt.py demo`: plan validation, human approval, write pipeline and ledger pass.
- Real CLI checks also cover email queue approval and dry-run, X reply queue contract, manual work sheets, campaign isolation, opportunity discovery, value artifacts, journeys, account lifecycle and pricing experiments. No real account sends in tests.

## Current capabilities

| Area | Local implementation | Verification and remaining boundary |
|---|---|---|
| G01–G04 task/plan, registry and account checks | Existing capabilities retained | Planning remains Agent-assisted; account authorization is not platform permission |
| G05, G08–G13 execution, playbooks, gates, state, readback, queue and approval | Existing API/browser execution retained. `mkt workflow` replays versioned approved recipes; `mkt schedule` handles due queue waves and interrupted/unknown states without blind retries | Per-channel PROVEN/PORTED/UNVERIFIED limits still apply |
| G06–G07 semantic and visual recovery | `core/locators.py` semantic ladder; visual recovery remains an operator-supplied provider hook | Stagehand/Midscene are not bundled; absent visual provider stops as `visual_unavailable` |
| G14–G15 operations memory and optimization | `mkt memory`, `mkt workflow learn`, `mkt scorecard`, `mkt optimize` | Optimization is a candidate experiment, requires human decision, never automatic apply |
| G16 third-party playbook marketplace | Deferred | No marketplace work claimed |
| Deep retrospective | `core/logscan.py` + `core/retro.py` + `core/narrative.py` + `mkt retro` | Large local scans work, including a 10,174-file case. Structured action→receipt→reply→outcome evidence is preferred; repeated prose is not independent proof |
| Experience pipeline | `core/experience.py` + `mkt exp` + harvest/build/render scripts + `mkt_experience` | 129 raw records in the current snapshot. Raw records do not automatically become eligible. Official rules, reproducible methods, single-expert experience, short-term tactics and own tests have different eligibility rules. No fabricated fresh coverage |
| Experience eligibility | `eligible()` / `assess_eligibility()` require canonical URL, access rights, relevance, independent support where applicable, human review, ASR status and type-specific freshness | `as_prompt_lines`, strategy and MCP use eligible records only. Expired, unreviewed, irrelevant, URL-less or single-source claims cannot be presented as general rules. Single-expert input can only generate an experiment |
| Voice and value content | `core/voice.py` has 37 platform profiles with tone, sentence style, openers and taboos; `mkt voice` and `mkt_voice_lint` | `check_preservation` checks facts, URLs, numbers and disclosures. Profile skeletons do not prove content value or platform acceptance |
| Platform source | `core/platforms.py` has 37 unique platform entries and 6 write-action mappings | Registration is not research completion or execution verification. Every platform must pass strategy, cadence and manual-sheet contract tests; generic Verovo numbers, duplicate `_CORE` and Star gates were removed |
| Platform × Motion × Vertical | `core/playbook_model.py` composes platform rules, reusable growth motions and vertical-specific value/acceptance constraints | A composed playbook is a candidate workflow, not evidence of channel effectiveness or platform permission |
| Strategy | `core/gtm.py`, `core/strategy.py`, `core/cadence.py` | No mandatory numeric `goals`; cold start, scale and risk parameters are configurable learning inputs. Fourth week is retrospective/next iteration, not a fixed product verdict |
| Opportunity Discovery | `core/opportunity.py` + `mkt opportunity` + `mkt_opportunity` | Explicit IDs/tags/text containment only; no semantic market inference. Complaints do not equal purchase intent. Product issues take priority over acquisition |
| Value Artifact | `core/value_artifact.py` + `mkt artifact` + `mkt_value_artifact` | Tutorials, examples, reproducible tests, templates, comparisons and real cases require scenario, source-backed value, reproduction steps, limitations and observation events. Exposure/sending is not user value |
| Journey | `core/journey.py` + `mkt journey` + `mkt_journey` | Discovery→engagement→trial→activation→retention→payment filtered by campaign+subject+time_window. Approximate/unverified events and historical campaigns cannot create progress |
| Capability/competition/evolution evaluation | `core/evaluation.py` + `mkt evaluate` | Same-condition metric comparisons, external unseen-case contract, regression/evolution records. No composite winner score or proof of generalization |
| Execute bridge | `core/execute.py` + `mkt execute` | Strategy produces generic plan+queue or manual sheet. Verovo-specific ICP is removed. X generates `reply`, not unsupported `message` queues. Plan/queue/manual artifacts are not execution results |
| Execution approval | `core/approval.py` and `core/planlint.py` | Approval fingerprint binds plan + normalized queue + targets/body/action/platform + account + workspace. Tampering, wrong account/workspace, expiry/revoke and missing queue fail closed. `success_criteria` is an optional learning object, not a product gate |
| Result projection | `core/result.py`, `core/scope.py`, `mkt result` | Real `event_type/approximated` is read correctly. campaign/subject/time_window filters apply to ledger, outcomes and followups without full-history fallback. Dynamic verified value events are supported; dry-run/suppressed/unknown states are not success |
| Revision | `core/revise.py` + `mkt revise` | Zero current actions yields a scope check, not historical scaling. Candidate explanations are separate from verified mechanisms; no automatic channel switch from replies without payment |
| Progress | `core/progress.py`, `mkt progress` | Daily/weekly outputs use current-scope facts; zero current actions cannot be presented as current-stage success |
| Intent learning experiment | `core/intent.py` + `mkt intent` | Configurable weeks and optional weekly target. Sample insufficiency and safety anomalies remain; output is learning observation, not pass/dead channel verdict |
| Account registry | `core/accounts.py` + `mkt account` | Real authorized, pseudonymous and brand identities, ownership/authorization evidence, multi-account permission, candidate→warming→eligible→limited/paused→retired, health, cooldown, takeover and audit. No login or sends |
| Proxy registry | `core/network.py` + `mkt network` | Stores only environment-variable names and account bindings; never reads credentials, cannot raise quotas, evade bans or alter authorization. No network operations in tests |
| Pricing experiments | `core/billing.py` + `mkt billing` | Action/outcome remains a candidate; explicit subscription/hybrid experiments and acceptance logs are supported. Accepted quote is not payment or product value |
| Follow-up | `core/followups.py` + `mkt followup` | Optional campaign field supports scoped projections. Due follow-ups stay ahead of new actions; local records never send |
| Measurement | `core/task_outcome.py` + GitHub benchmark | 33 synthetic keyword-baseline fixtures remain a failure-mode benchmark, not expert triage proof. `action_runner.record_ledger_outcome` records outcomes but does not independently verify delivery |

## External validation still required

- Full platform deep-research DoD is not complete. Registry size and document count cannot substitute for current official rules, practitioner methods, failure cases and platform-specific execution validation.
- Real account, proxy and authorized live-write tests are outside CI. They require separate authorization, platform/action/transport checks and readback evidence.
- The current opportunity and journey modules are deterministic reasoning aids, not proof of demand, causal growth, market size or revenue.
- Experience counts and freshness are build-time facts and must be regenerated with the pipeline and status page. Previous counts such as 108/117 were snapshots, not permanent facts.
- Synthetic demos and local fixtures do not prove external business results, paid demand, production reliability or exact-once delivery.
