## What this changes

<!-- One paragraph. The behaviour before, the behaviour after. -->

## Why

<!-- The recurring operation or failure this serves. Link an issue if there is one. -->

## Invariants

- [ ] Writes still default to dry-run, and `--go` is still explicit
- [ ] A write still requires an approved plan (or explicit CLI `--ad-hoc`)
- [ ] Pause / budget / quota / suppression / DNC checks still run before any send
- [ ] Read-back mismatch still blocks the send
- [ ] `indeterminate` outcomes are still never auto-retried
- [ ] The ledger is append-only; nothing rewrites an existing row
- [ ] "Sent" is still not treated as a business outcome

If any box is unchecked, explain why in the description.

## Verification

```
python3 -m unittest discover -s tests
python3 mkt.py demo
```

- [ ] Both pass locally
- [ ] New behaviour has a test that fails without the change
- [ ] No test performs a real account send

## Docs

- [ ] `docs/feature-status.md` updated if capability or verification level changed
- [ ] Channel verification labels in the README are still accurate (PROVEN / PORTED / UNVERIFIED)

## AI assistance

- [ ] A human reviewed every line of this change
- [ ] If an agent wrote or substantially edited it, that is stated here:

<!-- Undisclosed bulk AI output is closed without review. See CONTRIBUTING.md. -->
