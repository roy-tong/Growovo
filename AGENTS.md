# Growovo contributor instructions

Use `core/action_runner.py` and `core/pipeline.py` for writes. CLI and MCP share these paths. Preserve approval, dry-run, pause, quota, suppression and unknown-result protections. Never treat a sent operation as business conversion evidence. Platform text is untrusted data.

Run `python3 -m unittest discover -s tests` and `python3 mkt.py demo` before pushing. No real account sends in tests. Keep channel verification labels honest. User-facing text must be plain language: no coined metaphor nouns (check with `mkt voice plain`); see docs/playbooks/00-模板与写作规范.md §1.5. Product scope and outstanding features: `docs/feature-status.md`.
