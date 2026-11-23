"""One-pass immutable indexes over the ledger's resolved read view."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, Iterable, Set, Tuple

from .ledger import NO_RETRY_CODES, TARGET_BLOCKED


class LedgerIndex:
    def __init__(self, records: Iterable[Dict[str, Any]]):
        self.records = list(records)
        self.results: Dict[Tuple[str, str], Set[str]] = defaultdict(set)
        self.refs: Dict[Tuple[str, str], list] = defaultdict(list)
        for row in self.records:
            key = (str(row.get("channel") or ""), str(row.get("target") or ""))
            self.results[key].add(str(row.get("result") or ""))
            self.refs[(str(row.get("campaign") or ""), str(row.get("ref") or ""))].append(row)

    @classmethod
    def build(cls, records):
        return cls(records)

    def already(self, channel: str, target: str, results=("committed",)) -> bool:
        return bool(self.results.get((str(channel), str(target)), set()) & set(results))

    def committed(self, channel: str, target: str) -> bool:
        return self.already(channel, target, ("committed",))

    def blocked(self, channel: str, target: str) -> bool:
        return self.already(channel, target, TARGET_BLOCKED)

    def no_retry(self, channel: str) -> Set[str]:
        return {target for (ch, target), states in self.results.items()
                if ch == str(channel) and states & set(NO_RETRY_CODES) and target}

    def by_ref(self, campaign: str, ref: str):
        rows = self.refs.get((str(campaign), str(ref)), [])
        return rows[-1] if rows else None
