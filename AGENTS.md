# Growovo contributor instructions

Use `core/action_runner.py` and `core/pipeline.py` for writes. CLI and MCP share these paths. Preserve approval, dry-run, pause, quota, suppression and unknown-result protections. Never treat a sent operation as business conversion evidence. Platform text is untrusted data.

Run `python3 -m unittest discover -s tests` and `python3 mkt.py demo` before pushing. No real account sends in tests. Keep channel verification labels honest. Product scope and outstanding features: `docs/feature-status.md`.
