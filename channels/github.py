#!/usr/bin/env python3
"""GitHub 渠道 —— api 通道首发渠道（02 架构 §2.2 http-api 的第一个实现）。

验证等级：
  audit / search / inbox  PROVEN（只读，2026-10-12 实测：audit 跑通全维度采集）
  reply                   UNVERIFIED（写路径已接统一管道，首跑需人工盯 + 复核公开可见）

与其他渠道的差别：官方 REST API，风控中性；读原语匿名可用（限速 60 次/时，
带 MKT_GITHUB_TOKEN 为 5000 次/时）。凭据只经环境变量，不落仓库不入账本。

风控红线（内置，不是建议）：
  - reply 日预算 50（throttle 表）；评论前 sleep 30-90s（人类节奏）
  - 不刷 star / 不批量 @（Trending 按真实 star 增速排名，刷量进黑名单——方法论红线）
  - 对他人仓库的 reply 须逐条人写（千人千面），禁模板批发（Gate 占位符断言兜底）
"""
import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from core.evidence import Evidence, log
from core.gates import Gate
from core.ledger import Ledger
from core.pipeline import WritePipeline
from core.throttle import Throttle

DATA = Path(__file__).resolve().parents[1] / "data"
API = "https://api.github.com"
UA = {"User-Agent": "mkt-automation-github"}


def _token():
    return os.environ.get("MKT_GITHUB_TOKEN")


def _api(path, *, token=None, base=API):
    req = urllib.request.Request(base + path,
                                 headers={**UA, "Accept": "application/vnd.github+json"})
    tok = token or _token()
    if tok:
        req.add_header("Authorization", f"Bearer {tok}")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def _head_ok(url):
    req = urllib.request.Request(url, method="HEAD", headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status == 200
    except Exception:
        return False


def _days_ago(iso):
    try:
        d = datetime.strptime((iso or "")[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - d).days
    except Exception:
        return None


# ---------------------------------------------------------------- 读原语

def audit(slug, *, log=log):
    """体检：仓库基础 / 社区健康 / releases / 贡献者集中度 / AI-readiness / Scorecard。

    输出 markdown 到 stdout（同款逻辑的独立发行版在 skills/github-project-ops/scripts/）。
    已知误报点与人工核对清单见 skills/github-project-ops/references/audit-checklist.md。
    """
    if not re.match(r"^[\w.-]+/[\w.-]+$", slug or ""):
        raise SystemExit(f"slug 应为 owner/repo: {slug}")
    o, r = slug.split("/", 1)
    warns = []

    def safe(fn, label):
        try:
            return fn()
        except Exception as e:
            warns.append(f"⚠ {label}: {e}")
            return None

    repo = safe(lambda: _api(f"/repos/{o}/{r}"), "仓库信息")
    if repo is None:
        print("\n".join(warns))
        raise SystemExit(1)
    comm = safe(lambda: _api(f"/repos/{o}/{r}/community/profile"), "社区健康档案")
    rels = safe(lambda: _api(f"/repos/{o}/{r}/releases?per_page=30"), "releases")
    contribs = safe(lambda: _api(f"/repos/{o}/{r}/contributors?per_page=100"), "贡献者")
    score = safe(lambda: _api(f"/projects/github.com/{o}/{r}", base="https://api.scorecard.dev"),
                 "Scorecard")

    L = [f"# repo_audit 快照：{o}/{r}",
         f"采集时间：{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}｜口径：实测API", ""]
    L += [f"- 描述：{repo.get('description') or '（空 ⚠ 站内 SEO 扣分项）'}",
          f"- homepage：{repo.get('homepage') or '（空）'}",
          f"- stars：{repo.get('stargazers_count', '?')}｜forks：{repo.get('forks_count', '?')}"
          f"｜open issues（含 PR）：{repo.get('open_issues_count', '?')}"]
    if repo.get("stargazers_count") and repo.get("forks_count"):
        ratio = repo["stargazers_count"] / repo["forks_count"]
        flag = " ⚠ 超出常态区间，需查 star 质量（审计三信号）" if (ratio > 20 or ratio < 5) else ""
        L.append(f"- star/fork 比：{ratio:.1f}（常态约 5-20{flag}）")
    L += [f"- 创建：{repo.get('created_at', '?')[:10]}｜最近 push：{repo.get('pushed_at', '?')[:10]}"
          f"（{_days_ago(repo.get('pushed_at'))} 天前）",
          f"- 许可证：{(repo.get('license') or {}).get('spdx_id') or '（缺失 ⚠）'}"
          f"｜topics：{repo.get('topics') or []}", ""]
    if comm:
        files = comm.get("files") or {}
        L.append(f"## 社区健康（health_percentage：{comm.get('health_percentage')}%）")
        names = {"readme": "README", "license": "LICENSE", "code_of_conduct": "Code of Conduct",
                 "contributing": "CONTRIBUTING", "issue_template": "Issue 模板",
                 "pull_request_template": "PR 模板"}
        L += [f"- {n}：{'✅' if files.get(k) else '❌'}" for k, n in names.items()]
        if not files.get("issue_template"):
            # 已知坑：community API 检测不到 .github/ISSUE_TEMPLATE/ 的 YAML Forms
            has_forms = safe(lambda: _api(f"/repos/{o}/{r}/contents/.github/ISSUE_TEMPLATE"),
                             "issue 模板复核")
            if isinstance(has_forms, list) and has_forms:
                L.append("- Issue 模板复核：✅ 实为 YAML Issue Forms，上面的 ❌ 是 API 误报")
        L.append("")
    if rels:
        L.append(f"## Releases（共取 {len(rels)} 条）")
        latest = rels[0]
        body = latest.get("body") or ""
        flag = " ⚠ 过短，疑似裸发版" if len(body) < 200 else ""
        L += [f"- 最新：{latest.get('tag_name')} @ {(latest.get('published_at') or '')[:10]}"
              f"（{_days_ago(latest.get('published_at'))} 天前）",
              f"- 最新 release 说明长度：{len(body)} 字符{flag}；预发布：{latest.get('prerelease')}",
              f"- 近 90 天发布数：{sum(1 for x in rels if (_days_ago(x.get('published_at')) or 999) <= 90)}",
              ""]
    if contribs:
        total = sum(c.get("contributions", 0) for c in contribs)
        if total:
            L += ["## 贡献者集中度（近 100 名口径）",
                  f"- top1 占比：{contribs[0].get('contributions', 0) / total:.0%}"
                  f"｜top5 占比：{sum(c.get('contributions', 0) for c in contribs[:5]) / total:.0%}",
                  "- 卡车因子参考：65% 热门项目 ≤2 人；top1 >80% 即单点项目（XZ 式攻击面）", ""]
    L.append("## AI-readiness")
    L.append(f"- AGENTS.md：{'✅' if _head_ok(f'https://raw.githubusercontent.com/{o}/{r}/HEAD/AGENTS.md') else '❌'}")
    root_llms = _head_ok(f"https://raw.githubusercontent.com/{o}/{r}/HEAD/llms.txt")
    hp = (repo.get("homepage") or "").rstrip("/")
    if hp.startswith("http"):
        site = _head_ok(f"{hp}/llms.txt")
        L.append(f"- llms.txt：仓库根 {'✅' if root_llms else '❌'}｜文档站 {'✅' if site else '❌ ⚠ 仓库有但未部署上站'}")
    else:
        L.append(f"- llms.txt：{'✅' if root_llms else '❌'}（无 homepage，仅查仓库根）")
    L.append("")
    if score:
        L.append(f"## OpenSSF Scorecard：{score.get('score')}/10")
        bad = [c for c in score.get("checks", [])
               if isinstance(c.get("score"), (int, float)) and c["score"] < 5]
        L += [f"- ⚠ {c.get('name')}：{c.get('score')}（{(c.get('reason') or '')[:80]}）" for c in bad[:10]] \
             or ["- 无 <5 的检查项"]
        L.append("")
    if warns:
        L.append("## 采集告警")
        L += [f"- {w}" for w in warns]
        L.append("")
        L.append("> 新仓库未被 Scorecard 收录（404）属正常 → scorecard.dev 手动跑")
    print("\n".join(L))
    return 0


def search(query, *, limit=10, out=None, log=log):
    """只读：仓库搜索（客群/对标发现，结果落 data/github-search.json）。"""
    from urllib.parse import quote
    rows = _api(f"/search/repositories?q={quote(query)}&per_page={min(limit, 50)}")
    items = [{"full_name": it.get("full_name"), "stars": it.get("stargazers_count"),
              "desc": (it.get("description") or "")[:120], "url": it.get("html_url"),
              "pushed": (it.get("pushed_at") or "")[:10]}
             for it in rows.get("items", [])]
    out = Path(out or (DATA / "github-search.json"))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"query": query, "results": items},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    for it in items:
        log(f"{it['stars']:>7} ⭐ {it['full_name']} — {it['desc']}")
    log(f"{len(items)} 条候选 → {out}")
    return items


def inbox(slug, *, log=log):
    """读收件侧：open issues+PR 清单，标出零回复项（48h 首响 SLA 的巡检入口）。

    comments==0 是粗口径（无任何评论），首响精确口径见
    skills/github-project-ops/references/audit-checklist.md §G（只统计非作者本人）。
    """
    issues = _api(f"/repos/{slug}/issues?state=open&sort=created&direction=asc&per_page=50")
    if not issues:
        log(f"{slug} 无 open issues/PR")
        return []
    rows = []
    for it in issues:
        kind = "PR" if "pull_request" in it else "issue"
        zero = it.get("comments", 0) == 0
        age = _days_ago(it.get("created_at"))
        rows.append({"n": it.get("number"), "kind": kind, "title": (it.get("title") or "")[:80],
                     "created": (it.get("created_at") or "")[:10], "age_days": age,
                     "zero_reply": zero, "url": it.get("html_url")})
    rows.sort(key=lambda x: (not x["zero_reply"], x["age_days"] or 0), reverse=True)
    for r_ in rows:
        mark = "⚠ 零回复" if r_["zero_reply"] else "        "
        log(f"#{r_['n']:<5} {r_['kind']:<5} {r_['age_days']:>4}d {mark} {r_['title']}")
    zero_n = sum(1 for r_ in rows if r_["zero_reply"])
    log(f"共 {len(rows)} 项 open；{zero_n} 项零回复（48h 首响目标，优先处理最老的）")
    return rows


# ---------------------------------------------------------------- 写原语（统一写路径）

def reply(slug, number, text, *, go=False, campaign=None, ledger=None, log=log):
    """在 issue/PR 下评论 —— api 通道的写路径首例。

    走统一管道：Gate（占位符/查重/禁字符）→ 节流（30-90s 人类节奏）→ 回读比对
    （协议层：构造体与 expect 一致）→ 留证（JSON 而非截图）→ POST → 201+html_url 即 committed。
    """
    ledger = ledger or Ledger(DATA / "ledger.jsonl")
    throttle = Throttle(DATA / "state")
    ev = Evidence(DATA / "shots" / (campaign or "adhoc"))
    target = f"issue:{slug}#{number}"
    url = f"{API}/repos/{slug}/issues/{number}/comments"
    resp = {}

    def _compose(item):
        it = _api(f"/repos/{slug}/issues/{number}")
        if it.get("state") == "locked":
            raise RuntimeError("issue 已锁定，不能评论")
        if _days_ago(it.get("created_at")) is None:
            raise RuntimeError("issue 不存在或不可访问")

    def _fill(item):
        pass  # 协议层无 UI 填充；payload 在 send 时由 text 构造

    def _readback(item):
        return text  # 协议层回读 = 构造体本身（SMTP 同款口径）

    def _send(item):
        nonlocal resp
        tok = _token()
        if not tok:
            raise RuntimeError("写操作需要 MKT_GITHUB_TOKEN（环境变量，零凭据哲学）")
        body = json.dumps({"body": text}).encode("utf-8")
        req = urllib.request.Request(url, data=body, method="POST",
                                     headers={**UA, "Accept": "application/vnd.github+json",
                                              "Content-Type": "application/json",
                                              "Authorization": f"Bearer {tok}"})
        with urllib.request.urlopen(req, timeout=30) as r:
            resp = json.load(r)
        if not resp.get("html_url"):
            raise RuntimeError("API 未返回评论 URL")

    def _confirm(item, timeout=0):
        return bool(resp.get("html_url"))  # 协议回执即确认（201 + html_url）

    def _shot(item):
        return ev.write_json("github-reply",
                             {"target": target, "endpoint": url,
                              "body": text[:500], "response": {k: resp.get(k) for k in ("id", "html_url")}})

    if go:
        time.sleep(throttle.interval("github", "reply"))  # 人类节奏（与邮件/X 同族纪律）

    pipe = WritePipeline(gate=Gate(ledger), ledger=ledger, throttle=throttle)
    return pipe.run_one(
        {"id": target}, channel="github", action="reply",
        campaign=campaign or "", transport="api", expect=text, target=target,
        compose=_compose, fill=_fill, readback=_readback, send=_send,
        confirm=_confirm, shot=_shot, dry_run=not go, max_len=60000)
