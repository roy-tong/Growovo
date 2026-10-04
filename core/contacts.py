#!/usr/bin/env python3
"""线索库操作（07 完善清单：客群层缺操作入口的补齐）。

contacts.jsonl 统一跨平台身份（schema 见 data/schemas.md）。
资格线（03/05 文档）：证据分级低于 B 不入队；公司账号须先富化到员工（b2b-account-mapping）。
"""
import json
from pathlib import Path

PLATFORM_KEYS = {"email", "x", "linkedin", "reddit", "github"}


class Contacts:
    def __init__(self, path):
        self.path = Path(path)

    def all(self):
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out

    def _write(self, rows):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    def add(self, name="", org="", title="", platforms=None, source="",
            hooks=None, tier=2, evidence_grade="", contact_id=None):
        """入池。重复判定：任一平台身份撞车即拒绝（幂等）。"""
        platforms = platforms or {}
        rows = self.all()
        for r in rows:
            hit = [k for k, v in platforms.items() if r.get("platforms", {}).get(k) == v and v]
            if hit:
                raise ValueError(f"已存在（{','.join(hit)} 撞车）: {r.get('id')} {r.get('name')}")
        contact_id = contact_id or f"contact-{len(rows) + 1:04d}"
        rec = {
            "id": contact_id, "name": name, "org": org, "title": title,
            "platforms": platforms, "source": source,
            "hooks": hooks or [],
            "verification": {"grade": evidence_grade},
            "state": {"dnc": False, "bounced": None, "replies": 0, "last_touch": {}},
            "tier": tier,
        }
        rows.append(rec)
        self._write(rows)
        return rec

    def find(self, *, platform=None, value=None, org=None, text=None, include_dnc=False):
        rows = self.all()
        out = []
        for r in rows:
            if not include_dnc and r.get("state", {}).get("dnc"):
                continue
            if platform and value:
                if r.get("platforms", {}).get(platform) != value:
                    continue
            if org and org.lower() not in (r.get("org") or "").lower():
                continue
            if text:
                blob = json.dumps(r, ensure_ascii=False).lower()
                if text.lower() not in blob:
                    continue
            out.append(r)
        return out

    def mark(self, contact_id, **state_updates):
        """更新 state 字段（dnc=True / bounced='550' / replies=+1 / warm=True…）。"""
        rows = self.all()
        hit = False
        for r in rows:
            if r.get("id") == contact_id:
                st = r.setdefault("state", {})
                for k, v in state_updates.items():
                    if k == "replies" and v in ("+1", 1):
                        st[k] = st.get(k, 0) + 1
                    else:
                        st[k] = v
                hit = True
        if not hit:
            raise ValueError(f"未找到 {contact_id}")
        self._write(rows)
        return next(r for r in rows if r["id"] == contact_id)

    def import_legacy(self, path, fmt="er-batch"):
        """导入旧格式线索。er-batch：{email,name,org,source_pool,...} → 统一 schema。"""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        rows = data if isinstance(data, list) else data.get("items", [])
        added = skipped = 0
        for r in rows:
            try:
                self.add(
                    name=r.get("name", ""), org=r.get("org", r.get("source_pool", "")),
                    platforms={"email": r.get("email", "")} if r.get("email") else {},
                    source=r.get("source_pool", "legacy-import"),
                    hooks=[h for h in [r.get("hook", "")] if h],
                    tier=1 if r.get("email_status") == "verified" else 2,
                    evidence_grade=r.get("verification_source", ""),
                )
                added += 1
            except ValueError:
                skipped += 1
        return {"added": added, "skipped_duplicate": skipped}
