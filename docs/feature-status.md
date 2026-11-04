# Growovo: implementation status

Reviewed 2026-11-04. **Released baseline: v0.5.0.** Available means local implementation and local tests, not production validation on every platform or proof of business outcomes.

## Verification snapshot

- `python3 -m unittest discover -s tests`: **431 tests pass**.
- `python3 mkt.py demo`: plan validation, human approval, write pipeline and ledger pass.
- Real CLI checks also cover email queue approval and dry-run, X reply queue contract, manual work sheets, campaign isolation, opportunity discovery, value artifacts, journeys, account lifecycle and pricing experiments. No real account sends in tests.

## Released baseline capabilities (through v0.5.0)

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

## v0.5.0 depth additions

| Review item | Runnable depth added | Verification and remaining boundary |
|---|---|---|
| G1 Signal Interpretation | `core/signal_interpretation.py` validates host-Agent structure for source, subject, problem vs technical discussion, tool seeking, traceable alternative dissatisfaction, contact eligibility, commercial evidence, deterministic dedup and risks. `mkt signal-interpret` and `mkt_signal_interpretation` are read-only. `opportunity.discover` accepts `semantic_candidates` and only consumes qualified rows | Adversarial tests cover technical discussion, false/self-declared purchase intent, uncontactable subjects, missing alternative reasons and independent-source dedup. Legacy raw signals remain `unverified_signal`; tool seeking and `purchase_intent` never auto-upgrade to observed commercial intent |
| G2 value delivery chain | `core/value_delivery.py` implements research → solution → reproduction → asset → authorized delivery → user readback → completed. `mkt value-chain run` executes only an argv bound to an explicit command fingerprint, with `shell=False`, and records real exit code/output/timestamps/source_ref | Missing/failed reproduction cannot produce a verified asset. Unauthorized commands are not executed. Delivery only records an authorized external receipt; this module never sends. Sent/exposure is not use; completion requires a post-delivery, same-subject, sourced and verified value event |
| G3 Journey depth | `core/journey.py` now sorts by parsed real time, rejects late prior-stage backfill, distinguishes first/repeated activation, normalizes email identities and explicit `identity_map`, and separately exposes ordered multi-touch relationships. CLI/MCP accept identity mapping | Out-of-order, repeated activation, approximate, unknown and unverified events remain disclosed. Missing prior stages stay missing. Last event/channel receives no causal credit |
| G4 experience re-review | `core/experience.py` requires rights basis + confirmer + confirmation time + rights record, plus signed review time/record/source domains. `mkt exp assess` exposes eligibility, `review_grade`, rights details, review records and domains | `rights_cleared=True` or `human_reviewed=True` alone never qualifies. `internal_self_report` is experiment-only; formal strategy/generalization requires `traceable_review`. Legacy records remain readable but do not inherit eligibility |

## External validation still required

- Full platform deep-research DoD is not complete. Registry size and document count cannot substitute for current official rules, practitioner methods, failure cases and platform-specific execution validation.
- Real account, proxy and authorized live-write tests are outside CI. They require separate authorization, platform/action/transport checks and readback evidence.
- Signal interpretation, opportunity and journey modules are deterministic reasoning aids, not proof that host semantics are true, demand exists, growth is causal, or revenue will follow.
- Local value-chain fixtures prove the state machine and real subprocess accounting only. External delivery receipts, user completion and production reliability still need authorized real-world verification.
- Experience counts and freshness are build-time facts and must be regenerated with the pipeline and status page. Previous counts such as 108/117 were snapshots, not permanent facts.
- Synthetic demos and local fixtures do not prove external business results, paid demand, production reliability or exact-once delivery.
