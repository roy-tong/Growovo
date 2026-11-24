from __future__ import annotations

from pathlib import Path

from .storage import AtomicJSONStore


class ManagedTabs:
    def __init__(self, data_dir):
        self.store = AtomicJSONStore(
            Path(data_dir) / "state" / "managed-tabs.json",
            schema="1", default={"tabs": []})

    def get(self, session, site):
        rows = self.store.load().get("tabs") or []
        for row in reversed(rows):
            if row.get("session") == str(session) and row.get("site") == str(site):
                return dict(row)
        return None

    def remember(self, session, site, tab_id, group_title, url):
        payload = self.store.load()
        rows = [row for row in (payload.get("tabs") or [])
                if not ((row.get("session") == str(session) and row.get("site") == str(site))
                        or (row.get("session") == str(session)
                            and int(row.get("tabId") or 0) == int(tab_id)))]
        row = {
            "session": str(session), "site": str(site), "tabId": int(tab_id),
            "group_title": str(group_title), "url": str(url),
        }
        rows.append(row)
        self.store.save(dict(payload, tabs=rows))
        return dict(row)

    def forget(self, session, site, tab_id):
        payload = self.store.load()
        rows = [row for row in (payload.get("tabs") or [])
                if not (row.get("session") == str(session)
                        and row.get("site") == str(site)
                        and int(row.get("tabId") or 0) == int(tab_id))]
        self.store.save(dict(payload, tabs=rows))
