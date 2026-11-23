# Evidence-qualified review outreach

`mkt review-ops` prepares small expert-review or warm-follow-up batches without sending them. It joins the existing local outreach evidence rules to Growovo's native queues, approvals, suppression, ledger and reply cases.

## What the admission layer checks

| Rule | Refusal boundary |
| --- | --- |
| Person, not an organization mask | Company accounts need a named employee, role, department and persona hop; generic mailboxes such as `contact@` are rejected |
| Fresh hook | `hook.checked_at` must be on the execution date. The operator must actually recheck the public contribution; a timestamp alone is not semantic verification |
| Cross-track cooldown | The same target cannot be contacted again within 30 days, regardless of campaign. After two earlier contacts, re-entry needs a recorded new-evidence reason |
| Company spacing | Named people at the same company cannot be contacted within three days of each other. Three failed rotations cool the account |
| Expert-review content | A specific file/directory, 2–3 concrete feedback examples, 60–130 words, 5–8 sentences, exactly one question and a transparent sender identity for email |
| Voice and integrity | Placeholders, em/en dashes, generic openers, template phrases, bullet-list pitches and duplicate bodies are rejected |
| Small batch | At most five accepted items per queue. A rejection is useful output, not a reason to relax the gate |

Legacy `sent_log.txt` and `suppress.txt` are read as additional history and suppression sources. Growovo's own committed ledger and DNC/contact suppressions are also included. Legacy sender scripts are not called.

## Prepare a batch

Candidates are a JSON array using `schema=growovo.review-candidate/1`. Each item carries `mode`, `person`, `hook`, `artifact`, `feedback_examples`, `sender`, `subject` and `body`; see `tests/test_review_outreach.py` for a complete synthetic example.

```bash
python3 mkt.py --workspace DATA review-ops check candidates.json \
  --legacy-root /path/to/newoutreach --out NEW_DIRECTORY \
  --campaign expert_review_v2
```

Email and GitHub candidates produce separate native queues:

- `expert_review_v2.json`: `email/message`;
- `expert_review_v2_github.json`: `github/reply`.

The report contains queue hashes and per-candidate refusal codes. Output directories must be new or empty, so a scheduled check cannot overwrite an approved queue. CLI stdout reports counts only; recipient addresses stay in the private files.

## Scheduled local run

```bash
python3 mkt.py review-ops run review.json
```

The config names `workspace`, `candidates`, `legacy_root`, `out_root` and `campaign`. Relative paths resolve from the config file. Each run creates `run-YYYYMMDDTHHMMSSZZZZ`, performs the same admission checks and projects feedback. It performs no network or external write.

To execute a queue later, use the normal Growovo path: lint it, bind it to a plan, obtain named human approval, dry-run, then explicitly enable the real action. `review-ops` never signs an approval, supplies credentials, bypasses an UNVERIFIED channel or retries an indeterminate result.

## Feedback writeback

```bash
python3 mkt.py --workspace DATA review-ops feedback
```

Only review/warm reply cases with a named human review and `human_label=interested|question` are projected. The command writes mode-0600 JSON and Markdown under `DATA/review-feedback/` with:

- `private=true` and `sanitization_required=true`;
- `repository_write=not_performed`;
- the private excerpt and human reviewer;
- a next action to sanitize, land an issue/spec/fixture through normal review, then return the public link.

Repeated projection of an unchanged reply is idempotent. Pending or unconfirmed replies are skipped. No issue, PR, public comment, email or repository push is created by this command.

## Outcome discipline

An accepted candidate or committed send is not a business result. Track separately:

- `reply`: an external reply, including a polite refusal;
- `material`: specific feedback adopted into a spec, validator or fixture;
- `artifact`: an external PR, fixture contribution or actual integration.

No reply and unknown remain distinct. Do not increase batch size to compensate for missing material feedback.
