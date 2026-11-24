# Growovo: a scheduler that knows when not to send

*Eligibility filters, stable action identity, UTC budgets, stale-draft pruning, adaptive ordering, and channel recovery.*

A scheduler's most informative output is often a skipped row with a precise reason.

`already_done` means the target ledger says this reply already exists. `target_isolated` means an earlier incident made the target unsafe. `stale_row` means the approved moment has passed. `daily_quota_exhausted` means scheduling the row would only fail later. These are not error strings to be hidden behind a progress bar. They are the control plane.

Growovo's engagement cycle treats planning as a deterministic eligibility computation before any browser action is considered. Only after that computation does an adaptive executor decide which eligible row to run next. This separation makes two kinds of bugs distinguishable: a row that should never have been eligible, and an eligible row whose live order was inefficient.

## Action identity must survive editing

Before filtering, queue rows are normalized into a common shape: channel, action, target, text, actor, and source. Reply identity is based on channel, action, and target; original-post identity also includes the first 60 characters of text.

The stable queue ID is:

```python
text_part = "" if action == "reply" else text[:60]
raw = f"{channel}|{action}|{target}|{text_part}"
stable_id = "q:" + sha1(raw)[:12]
```

A reply can be rewritten without becoming a new business action. This matters for operations logs and deduplication: filling an empty inbox draft or improving its wording must not produce two independent-looking attempts to answer the same thread. Original posts need different treatment because the same account may intentionally publish different content to the same destination.

Deduplication uses the same semantics. Two reply rows for one target are duplicates even if their text differs. The ledger check then answers a separate question: has a committed reply already been observed for that target? Queue duplication and external execution history are related but not interchangeable.

## Planning is a pipeline of refusals

The planner reads committed ledger history, current policy, queue content, and local budgets. Its filter order makes the failure modes inspectable:

| Stage | Representative refusal |
| --- | --- |
| Channel hold | `channel_hold` |
| Draft age | `stale_row` |
| Supported action | `unsupported` |
| Content present and allowed | `text_empty`, content-rule reason |
| Target contract | `target_required`, `channel_not_allowed` |
| Queue and target identity | `duplicate_in_queue`, `target_isolated`, `already_done` |
| Configured budgets | `night_quota_exhausted`, `hour_quota_exhausted`, `daily_quota_exhausted` |
| Independent safety cap | `safety_cap_missing`, `safety_cap_exhausted` |
| Wave composition | `wave_target_met`, `actor_cap`, `wave_cap` |
| Time budget | `wave_budget` |

Every rejected row is returned with channel, action, target, and reason. Planning itself has no side effects.

Only committed rows with `transport=browser-attach` or a legacy blank transport consume nightly and hourly usage. Failed actions do not consume the same counters. Per-person caps apply to replies, not original posts; otherwise publishing three original posts as the owner would exhaust the supposed “two external people per night” budget.

Two budget layers are deliberately independent. Setting `quota.enforce=false` disables the configured nightly/hourly quota refusal, but it does not disable the independent safety-cap layer. A policy change intended to reduce accidental scheduling friction cannot silently remove the final stop condition.

## Budget days are an accounting decision

A nightly window from 19:00 to 07:00 crosses local midnight. Splitting usage by local calendar date creates two bad outcomes: early evening inherits usage from that morning, while midnight appears to offer a fresh daily allowance.

The default budget boundary is therefore UTC midnight, 08:00 in Shanghai, so the whole night shares one budget day. Legacy local-date files that overlap the UTC window are added conservatively instead of being ignored during migration. A caller can explicitly choose `day_boundary=local`; the state filename changes so the two accounting systems are not confused.

Per-run slicing is mechanical:

```python
Throttle(...).slice_batches("email", "message", 120)
# cap = 50 → [range(0, 50), range(50, 100), range(100, 120)]
```

The actual lengths are `[50, 50, 20]`. This is different from `check_run`, which refuses the whole batch above the cap. Slicing lets the caller continue with deferred rows rather than treating capacity as an all-or-nothing failure.

Reservation happens before the external write under a cross-process lock. If the write then fails or becomes unknown, the reservation remains consumed. That is conservative on purpose: an unknown external result cannot be treated as unused capacity and immediately spent again.

These are local operating budgets, not claims about a platform's official limits. The code keeps separate reference values for official limits and uses its own lower budgets for planning.

## Stale drafts expire; conversations do not

The default non-inbox draft lifetime is twelve hours. The age comes from `queued_at`; legacy rows can fall back to midnight of a `night-YYYYMMDD` campaign. Rows with neither timestamp are treated as fresh rather than being discarded without evidence.

Inbox replies never expire through this rule. An old inbound question is still a conversation that needs handling, although its content and platform state still require review. This avoids a common automation bug: pruning the people who answered while retaining a fresh batch of cold promotional copy.

Dry runs report stale rows without changing the queue. A real run prunes stale non-inbox rows, while deferred rows remain available for a later wave. The difference between “expired content” and “temporarily unrunnable content” is therefore preserved in storage.

## The static plan is not the live order

Once rows are eligible, `choose_next` recomputes readiness after every action:

```python
channel_ready = last_channel[channel] + channel_gap
global_ready = last_any + global_gap
ready = max(channel_ready, global_ready)
```

The winner is selected by three ordered rules:

1. earliest `ready` timestamp;
2. inbox row wins a tie;
3. the row with the shortest observed duration wins a remaining tie.

Duration estimates use an exponential moving average:

```python
new_average = old_average * 0.75 + observed_seconds * 0.25
```

A row with no duration observation does not invent a fast estimate. A positive measured duration only wins against an unknown or longer duration.

I called the selector directly with two equal-ready rows. The actual decisions were:

```text
queue row + inbox row at the same ready time
→ x / inbox_first

30.0-second GitHub row + 12.5-second X row at the same ready time
→ x / 12.5 seconds / shortest_observed_duration
```

This is why the dry-run plan is an initial reservation rather than a promise of final execution order. A deferred channel is removed from the live pending set, allowing another channel to use the gap without reducing configured cooldowns. Every adaptive decision and its timing inputs are recorded in the wave summary.

Background supply prefetch is similarly constrained. Read-only discovery can run beside execution when new candidates are needed, but X search is marked `shared_browser_tab` and is not run concurrently with the sending tab. Prefetch failure does not rewrite action results.

## A channel needs a circuit breaker, not a retry loop

Stop state has three values:

```text
closed     no stop file; normal scheduling allowed
open       cooling, unresolved, or unreadable timestamp; no sends
half_open  cooldown elapsed; at most one probe action this wave
```

A stop file without a readable timestamp stays `open`: the system cannot prove that cooling has finished. If an external result is `indeterminate`, the stop record carries `requires_human_resolution` and an unresolved reference. While the original ledger row remains indeterminate, later waves stay open.

After cooldown, the wave keeps only one probe for the channel and defers the rest with `stop_probe_pending`. A committed probe clears the stop file. Any other result keeps it. The system does not redirect the same content through another channel to make the failure less visible.

Infrastructure absence is deliberately different from a platform stop. A missing or hidden browser tab defers that channel without creating a stop file or charging an action. Other channels continue. An unknown external outcome, by contrast, stops the affected channel pending named resolution.

## A second batch tests the reasons, not just the count

The next authorized batch is useful only if it preserves these distinctions. It should show whether a rewritten reply keeps its identity, whether suppression and target isolation still win over fresh content, whether unresolved results block another attempt, and whether a new day inherits the correct legacy usage.

Useful second-batch evidence includes:

- skipped rows and their reason codes, not only committed counts;
- queue IDs before and after draft edits;
- reserved versus actually observed actions;
- stale and deferred rows left in storage;
- adaptive scheduling decisions;
- stop states before and after any probe;
- human reconciliation minutes.

I ran the engagement-cycle suite for this article: 69 tests passed. They cover reply identity after rewrites, already-replied targets, actor caps, stale pruning, daily and hourly budgets, missing tabs, unknown-result stops, one-probe recovery, queue locking, adaptive timing, and second-wave behavior. The transports and accounts are simulated; these tests establish scheduling behavior, not platform effectiveness or growth outcomes.

The design tradeoff is that a Growovo wave may choose to do very little. An empty send count can mean missing eligibility, insufficient evidence, a closed budget, or a deliberate stop. A good scheduler makes those states more visible, rather than treating them as a quota that must be filled.

### Source and verification links

- [`engagement_cycle.py`: eligibility, identity, adaptive ordering, and stop recovery](https://github.com/roy-tong/Growovo/blob/main/core/engagement_cycle.py)
- [`throttle.py`: UTC budget accounting, slicing, and reservation](https://github.com/roy-tong/Growovo/blob/main/core/throttle.py)
- [`inbox_replies.py`: inbound reply collection](https://github.com/roy-tong/Growovo/blob/main/core/inbox_replies.py)
- [`supply_prefetch.py`: constrained read-ahead](https://github.com/roy-tong/Growovo/blob/main/core/supply_prefetch.py)
- [Engagement-cycle tests](https://github.com/roy-tong/Growovo/blob/main/tests/test_engagement_cycle.py)
- [Growovo implementation status](https://github.com/roy-tong/Growovo/blob/main/docs/feature-status.md)
