# Growovo: implementation status

Reviewed 2026-11-04. **Working baseline: v0.10.0 (2026-11-04).** Available means local implementation and local tests, not production validation on every platform or proof of business outcomes.

> Business architecture: [GTM 业务操作系统 v5](gtm-os/README.md) covers role families 07–46, work-order/SOP/deliverable/handoff contracts, KPI definitions and templates. R0/R1 are now locally implemented; see [implementation and remaining boundaries](gtm-os/05-implementation.md). Registry coverage is not equivalent to all 40 professional SOPs being executable or externally effective.

## Verification snapshot

- `python3 -m unittest discover -s tests`: **631 tests pass**.
- `python3 mkt.py demo`: plan validation, human approval, write pipeline and ledger pass.
- `python3 mkt.py work-demo`: context → objective → WorkOrder → SOP → accepted deliverables → receiver handoff → approved plan/queue → independent KPI observation passes; no queue execution or send ledger.
- Isolated wheel build and target install: v0.9.0 was verified; the 0.10.0 wheel rebuild is not yet re-run in this snapshot.
- Real CLI checks also cover email queue approval and dry-run, X reply queue contract, manual work sheets, campaign isolation, opportunity discovery, value artifacts, journeys, account lifecycle and pricing experiments. No real account sends in tests.

## Baseline capabilities (through v0.10.0)

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
| Sprint planning | `core/sprint.py` + `mkt sprint plan` + `mkt_sprint_plan`: N-day compression, parallel workstreams, hourly blocks, front-loaded daily targets, six levers, three checkpoints, platform daily-capacity checks and red lines | A sprint is a signal test, not market validation; platform limits are conservative, not platform maxima; planned actions are not executed. 12 dedicated tests; full suite 486 pass. No external sends |
| Business narrative | `mkt channel plan` and `mkt deal next|funnel|quote` render business language by default: scene → pain → current substitute → why this venue → what to say → proof → signal → falsifier; stage meaning → what we know/need → why this action → risk → walk-away; pricing rationale. JSON keeps scores/stage codes | Narrative is still generated from deterministic fields, not a model; evidence levels are hand-labeled per venue; the renderer cannot verify a user's quote or a payment. |
| Channel model | `core/channels.py` + `mkt channel venues|plan|score` + `mkt_channel_venues` / `mkt_channel_plan`: venue-level registry (12 venues) with audience, access, promo policy, signal density, competition, speed, risk and evidence; 1 primary + 1 launch + 2 support + 2 listen-only; bounded economics, capacity, experiments and rules | A channel plan is a scheduling hypothesis, not channel effectiveness. Venue size is an order of magnitude, not audience reach. Promotion-banned venues are listen-only. No platform writes. 11 dedicated tests |
| Conversion pipeline | `core/conversion.py` + `mkt deal add|advance|qualify|list|next|funnel|quote|objections` + `mkt_deals` / `mkt_deal_next`: 14 stages with explicit transitions, evidence gates for quote/SOW/payment, five-criterion qualification, one next action per stage, nine objections, four offers, one-page SOW, funnel and stuck deals | A stage is a record, not a forecast. `paid` requires evidence; a quote is not revenue; a sample is not payment. `deals.jsonl` is append-only per advance. 16 dedicated tests |
| Goal selection | `core/goals.py` + `mkt goal list/plan/select` + `mkt_goals` / `mkt_goal_plan`; selection contract feeds `strategy --selection` and `cadence --selection` | 声量/使用/商业三类、9 个目标；适配按产品类型/阶段/已有账号判，平台分工限 1 主 + 1 发射 + 2 辅助；可选 1 个辅目标，两个都达成才算成功。默认目标是排期形状，不是预测；平台亲和度是规划启发式，不是渠道效果证明 |
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

## v0.10.0 additions

| Area | Local implementation | Verification and remaining boundary |
|---|---|---|
| GTM design | `core/gtm_design.py` + `mkt gtm design/lint/taxonomy` + read-only `mkt_gtm_design` MCP tool: object × objective × motion × channel, explicit override, low-confidence clarification, constraint checks (authorized channels, assets, budget, region, data sources), outcome contracts with `observable`/`partially_observable`/`unverified` | Design only. No execution, no sends, no channel-effectiveness proof. Object/objective are signal heuristics, not validated segmentation. Proposed motions and channels are rule-checked, not approved |
| Design handoff | `gtm_design.handoff` + `mkt gtm handoff`: design → goal family mapping and optional candidate Workstream via orchestration | Candidate plan only; the Workstream may infer a different motion from product facts than the design selected |
| Design contract | Primary/leading signals, guardrails, explicit not-success lists, observation window and stop rule per objective; `unverified` when no data source is supplied | Window is a planning range, not a statistical power calculation. `current` stays `unmeasured`; platform receipts never become business results |
| Agent proposal path | `--proposal` accepts candidate motions with reasons and channels; incompatible/unknown entries are rejected with visible reasons | The agent proposes, rules only check feasibility. Rejections do not imply the alternative is proven |

## v0.9.0 additions

| Area | Local implementation | Verification and remaining boundary |
|---|---|---|
| Minimum-role orchestration | `core/orchestration.py` and `mkt orchestrate`; product/goal/task → required roles, optional triggers, WorkOrder DAG and handoffs | Candidate orchestration only. One full developer SOP; other selected steps are marked requires_expansion. No default 40-role team |
| Per-deliverable expression | Audience-specific decision memo/runbook/tutorial/sales handoff/customer/partner formats; channel overlays for email/GitHub/X/LinkedIn/小红书/公众号/landing/presentation; artifact-specific required content and tone | Writing rules are not current official platform-rule verification. Internal technical/revenue/POC artifacts are not rewritten as social copy |
| Standing routine authorization | `core/execution_policy.py`; workspace-scoped closed action set, exact account matching, 30-day default expiry (`--days 0` opt-in permanent), revoke and fail-closed validation; `mkt policy` CLI management | Routine only replaces per-run plan approval. Empty account scope is default-only, not wildcard. MCP cannot create/revoke policy |
| Routine execution integration | `planlint`, `action_runner`, `pipeline`, `queue_runner`, email SMTP and MCP support explicit `routine` with independent `routine-policy` receipt | Payment/contract/discount/crisis/unknown actions cannot be authorized. Dry-run, pause, DNC, quota, readback, channel verification and unknown-result reconciliation remain |
| Documentation and tests | `docs/gtm-os/06-orchestration-and-routine.md`; orchestration, policy core, CLI and MCP pass-through tests | All sends are fake/dry-run. Current workspace policy was enabled by the user's direct instruction for default-account routine actions; no live sends performed |

## v0.8.0 additions

| Area | Local implementation | Verification and remaining boundary |
|---|---|---|
| Capability and template registry | `core/capabilities.py` / `capability_data.json` and `core/templates.py` / `template_data.json`; `mkt capability` and `mkt template`; 18 domains, 40 role families and 32 templates with cross-reference lint | All roles have SOP summaries and contracts in the registry. Only `sop-devrel-first-success@1.0.0` is currently a full executable Step Contract; no claim of 40 completed professional automations |
| Context, objective and WorkOrder | `core/work.py`; immutable context snapshots, versioned objectives, WorkOrder lint/ready/dependencies/state/next | `user_declared` remains distinct from Askovo research. Local `by` signatures are not enterprise identity |
| SOP run and deliverable acceptance | `core/sop.py`, `core/deliverables.py`; evidence-gated steps, required human approvals, template instances, QA reviews, R3/R4 author/reviewer separation and revisions | External correctness and business usefulness are not inferred from fixture approval |
| Receiver handoff | `core/handoffs.py`; accepted artifacts, named receiver, rejection return step and SLA projection | Creation or notification is not receiver acceptance |
| KPI measurement | `core/metrics.py` / `metric_data.json`; versioned count/ratio metrics, filters, dedupe, scope, maturity and deterministic recalculation | Current calculator supports counts and numerator/denominator ratios, not general BI. Zero denominator and pending maturity return null |
| Work-to-execution bridge | `core/work_actions.py`; accepted deliverable + current action_request step → existing plan/queue with provenance in execution fingerprint | No direct transport and no sends. Existing plan approval, dry-run/go, pause, quota, suppression and unknown-result gates remain mandatory |
| Conversion and result integrity | `core/conversion.py`: event-append projections, cohort funnel, all-stage evidence, qualification/quote guards; `core/result.py`: TaskOutcome-aware three-layer results; execute evidence grade is `unverified` | Paid retains evidence-only compatibility with optional verification method/ref. A cohort rate is descriptive, not causal |
| CLI, MCP and demo | New CLI surfaces; read-only `mkt_capabilities`, `mkt_templates`, `mkt_work_order`, `mkt_metric_measure`; `mkt work-demo`; JSON registries included in package data | MCP cannot create work, approve deliverables, accept handoffs or self-authorize. No full Web UI or enterprise RBAC claimed |

## v0.7.0 additions

| Review item | Runnable depth added | Verification and remaining boundary |
|---|---|---|
| Channel as a product surface | `core/channels.py`; venue registry; score; plan; economics; capacity; experiments; render. CLI + MCP read-only | 12 venues are a starting library, not a complete market map. Scores are planning heuristics. Economics is assumption ranges; real reply/qualified/sample/paid rates must be filled back. |
| Conversion as a product surface | `core/conversion.py`; 14-stage machine; `Deals` append-only store; qualification; next action; objections; offers; quote; funnel; render. CLI + MCP read-only | Stage evidence is operator-supplied; the module does not verify payment or delivery. `paid` requires an evidence string but does not check a bank record. Funnel is descriptive, not causal. |

## v0.6.0 additions

| Review item | Runnable depth added | Verification and remaining boundary |
|---|---|---|
| Goal selection | `core/goals.py` has a 9-goal catalog across awareness / adoption / commercial, applicability by product type/stage/owned platform, platform affinity ranking, role limits, target/window validation, a goal-selection contract and business-language rendering. `mkt goal list/plan/select` with optional `--secondary`; MCP `mkt_goals` / `mkt_goal_plan` are read-only. `strategy build --selection` keeps the chosen primary instead of letting old retro numbers override it; `cadence --selection` uses the goal plan's two-week actions instead of the generic 20/week template | Goal achieved is not demand, payment willingness or repeatability. Platform affinity is a planning heuristic, not channel effectiveness. Default targets are shapes, not forecasts; missing baselines are shown as missing. 37 dedicated tests; full suite 474 pass. No external sends or business validation |

## v0.5.0 depth additions

| Review item | Runnable depth added | Verification and remaining boundary |
|---|---|---|
| G1 Signal Interpretation | `core/signal_interpretation.py` validates host-Agent structure for source, subject, problem vs technical discussion, tool seeking, traceable alternative dissatisfaction, contact eligibility, commercial evidence, deterministic dedup and risks. `mkt signal-interpret` and `mkt_signal_interpretation` are read-only. `opportunity.discover` accepts `semantic_candidates` and only consumes qualified rows | Adversarial tests cover technical discussion, false/self-declared purchase intent, uncontactable subjects, missing alternative reasons and independent-source dedup. Legacy raw signals remain `unverified_signal`; tool seeking and `purchase_intent` never auto-upgrade to observed commercial intent |
| G2 value delivery chain | `core/value_delivery.py` implements research → solution → reproduction → asset → authorized delivery → user readback → completed. `mkt value-chain run` executes only an argv bound to an explicit command fingerprint, with `shell=False`, and records real exit code/output/timestamps/source_ref | Missing/failed reproduction cannot produce a verified asset. Unauthorized commands are not executed. Delivery only records an authorized external receipt; this module never sends. Sent/exposure is not use; completion requires a post-delivery, same-subject, sourced and verified value event |
| G3 Journey depth | `core/journey.py` now sorts by parsed real time, rejects late prior-stage backfill, distinguishes first/repeated activation, normalizes email identities and explicit `identity_map`, and separately exposes ordered multi-touch relationships. CLI/MCP accept identity mapping | Out-of-order, repeated activation, approximate, unknown and unverified events remain disclosed. Missing prior stages stay missing. Last event/channel receives no causal credit |
| G4 experience re-review | `core/experience.py` requires rights basis + confirmer + confirmation time + rights record, plus signed review time/record/source domains. `mkt exp assess` exposes eligibility, `review_grade`, rights details, review records and domains | `rights_cleared=True` or `human_reviewed=True` alone never qualifies. `internal_self_report` is experiment-only; formal strategy/generalization requires `traceable_review`. Legacy records remain readable but do not inherit eligibility |

| G5 corpus → rules | 605-article corpus (小红书/B站/公众号/Reddit/Discord) distilled into `experience.jsonl` (210 claims: 28 manual, 182 extracted, 40 topic-level), then written into `platforms.py` as `cold_start.checklist` / `stages` / `scale_gate` / `evidence` and rendered by `strategy`. Six tests pin the wording | Claims are still self-reported practitioner experience; freshness is 91/210 inside 60 days, so most of it is directional, not proof. Discord yielded only 4 channels |

## External validation still required

- Full platform deep-research DoD is not complete. Registry size and document count cannot substitute for current official rules, practitioner methods, failure cases and platform-specific execution validation.
- Real account, proxy and authorized live-write tests are outside CI. They require separate authorization, platform/action/transport checks and readback evidence.
- Signal interpretation, opportunity and journey modules are deterministic reasoning aids, not proof that host semantics are true, demand exists, growth is causal, or revenue will follow.
- Local value-chain fixtures prove the state machine and real subprocess accounting only. External delivery receipts, user completion and production reliability still need authorized real-world verification.
- Experience counts and freshness are build-time facts and must be regenerated with the pipeline and status page. Previous counts such as 108/117 were snapshots, not permanent facts.
- Synthetic demos and local fixtures do not prove external business results, paid demand, production reliability or exact-once delivery.
