#!/usr/bin/env python3
"""GitHub 仓库体检数据采集：基础信息 / 社区健康文件 / releases / 贡献者集中度 /
OpenSSF Scorecard / AGENTS.md 与 llms.txt 存在性。

用法:
    python3 repo_audit.py owner/repo [--token GITHUB_TOKEN]

输出 markdown 报告到 stdout；单个端点失败不中断（标注 ⚠）。
公开仓库无需 token（限速 60 次/小时）；私有仓库需 token（repo 权限）。
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

API = "https://api.github.com"
UA = {"User-Agent": "github-project-ops-audit"}


def api(url, token=None):
    req = urllib.request.Request(url, headers={**UA, "Accept": "application/vnd.github+json"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def raw_head_ok(url):
    req = urllib.request.Request(url, method="HEAD", headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status == 200
    except Exception:
        return False


def days_ago(iso):
    try:
        d = datetime.strptime((iso or "")[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - d).days
    except Exception:
        return None


def safe(fn, label, out):
    try:
        return fn()
    except Exception as e:
        out.append(f"⚠ {label}: {e}")
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("slug", help="owner/repo")
    ap.add_argument("--token", default=os.environ.get("GITHUB_TOKEN"))
    a = ap.parse_args()
    if "/" not in a.slug:
        ap.error("slug 必须是 owner/repo 形式")
    o, r = a.slug.split("/", 1)
    t = a.token
    warns = []
    W = warns.append

    repo = safe(lambda: api(f"{API}/repos/{o}/{r}", t), "仓库信息", warns)
    if repo is None:
        print("\n".join(warns), file=sys.stderr)
        sys.exit(1)

    comm = safe(lambda: api(f"{API}/repos/{o}/{r}/community/profile", t), "社区健康档案", warns)
    rels = safe(lambda: api(f"{API}/repos/{o}/{r}/releases?per_page=30", t), "releases", warns)
    contribs = safe(lambda: api(f"{API}/repos/{o}/{r}/contributors?per_page=100", t), "贡献者", warns)
    score = safe(lambda: api(f"https://api.scorecard.dev/projects/github.com/{o}/{r}"), "Scorecard", warns)

    L = []
    L.append(f"# repo_audit 快照：{o}/{r}")
    L.append(f"采集时间：{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}｜口径：实测API")
    L.append("")

    L.append("## 基础")
    L.append(f"- 描述：{repo.get('description') or '（空 ⚠ 站内 SEO 扣分项）'}")
    L.append(f"- homepage：{repo.get('homepage') or '（空）'}")
    L.append(f"- stars：{repo.get('stargazers_count', '?')}｜forks：{repo.get('forks_count', '?')}｜open issues（含 PR）：{repo.get('open_issues_count', '?')}")
    if repo.get("stargazers_count") and repo.get("forks_count"):
        ratio = repo["stargazers_count"] / repo["forks_count"]
        flag = " ⚠ 超出常态区间，需查 star 质量（审计三信号）" if (ratio > 20 or ratio < 5) else ""
        L.append(f"- star/fork 比：{ratio:.1f}（常态约 5-20{flag}）")
    if repo.get("stargazers_count") and repo.get("open_issues_count"):
        L.append(f"- star/open-issue 比：{repo['stargazers_count'] / max(repo['open_issues_count'], 1):.0f}（万级 star 而两位数以下 issue → 存疑）")
    L.append(f"- 创建：{repo.get('created_at', '?')[:10]}｜最近 push：{repo.get('pushed_at', '?')[:10]}（{days_ago(repo.get('pushed_at'))} 天前）")
    lic = (repo.get("license") or {}).get("spdx_id")
    L.append(f"- 许可证：{lic or '（缺失 ⚠）'}｜topics：{repo.get('topics') or []}（≤20 个，单词形式）")
    if repo.get("archived"):
        L.append("- ⚠ 仓库已归档")
    L.append("")

    if comm:
        files = comm.get("files") or {}
        L.append(f"## 社区健康（health_percentage：{comm.get('health_percentage')}%）")
        m = {
            "readme": "README", "license": "LICENSE", "code_of_conduct": "Code of Conduct",
            "contributing": "CONTRIBUTING", "issue_template": "Issue 模板",
            "pull_request_template": "PR 模板",
        }
        for k, name in m.items():
            L.append(f"- {name}：{'✅' if files.get(k) else '❌'}")
        # 已知坑：community profile API 检测不到 .github/ISSUE_TEMPLATE/ 下的 YAML Issue Forms，复核一次
        if not files.get("issue_template"):
            has_forms = safe(lambda: api(f"{API}/repos/{o}/{r}/contents/.github/ISSUE_TEMPLATE", t), "issue 模板复核", warns)
            if isinstance(has_forms, list) and has_forms:
                L.append("- Issue 模板复核：✅ 实为 YAML Issue Forms（.github/ISSUE_TEMPLATE/），上面的 ❌ 是 API 误报")
        L.append("")

    if rels:
        L.append(f"## Releases（共取 {len(rels)} 条）")
        if rels:
            latest = rels[0]
            body = latest.get("body") or ""
            L.append(f"- 最新：{latest.get('tag_name')} @ {(latest.get('published_at') or '')[:10]}（{days_ago(latest.get('published_at'))} 天前）")
            flag = " ⚠ 过短，疑似裸发版" if len(body) < 200 else ""
            L.append(f"- 最新 release 说明长度：{len(body)} 字符{flag}；是否预发布：{latest.get('prerelease')}")
            d30 = [x for x in rels if (days_ago(x.get("published_at")) or 999) <= 90]
            L.append(f"- 近 90 天发布数：{len(d30)}（0 = 无传播节点）")
        L.append("")

    if contribs:
        total = sum(c.get("contributions", 0) for c in contribs)
        if total:
            top1 = contribs[0].get("contributions", 0) / total
            top5 = sum(c.get("contributions", 0) for c in contribs[:5]) / total
            L.append(f"## 贡献者集中度（近 100 名口径）")
            L.append(f"- top1 占比：{top1:.0%}｜top5 占比：{top5:.0%}")
            L.append(f"- 卡车因子参考：65% 热门项目 ≤2 人；top1 >80% 即单点项目（XZ 式攻击面）")
        L.append("")

    L.append("## AI-readiness")
    L.append(f"- AGENTS.md：{'✅' if raw_head_ok(f'https://raw.githubusercontent.com/{o}/{r}/HEAD/AGENTS.md') else '❌'}")
    root_llms = raw_head_ok(f"https://raw.githubusercontent.com/{o}/{r}/HEAD/llms.txt")
    hp = (repo.get("homepage") or "").rstrip("/")
    if hp.startswith("http"):
        site_llms = raw_head_ok(f"{hp}/llms.txt")
        # llms.txt 规范位置是文档站根；仓库根存在但站点 404 = 未部署
        L.append(f"- llms.txt：仓库根 {'✅' if root_llms else '❌'}｜文档站 {'✅' if site_llms else '❌ ⚠ 仓库有但未部署上站'}")
    else:
        L.append(f"- llms.txt：{'✅' if root_llms else '❌'}（无 homepage，仅查仓库根）")
    L.append(f"- FUNDING：{'✅' if raw_head_ok(f'https://raw.githubusercontent.com/{o}/{r}/HEAD/.github/FUNDING.yml') else '—'}")
    L.append("")

    if score:
        L.append(f"## OpenSSF Scorecard：{score.get('score')}/10")
        bad = [c for c in score.get("checks", []) if isinstance(c.get("score"), (int, float)) and c["score"] < 5]
        for c in bad[:10]:
            L.append(f"- ⚠ {c.get('name')}：{c.get('score')}（{(c.get('reason') or '')[:80]}）")
        if not bad:
            L.append("- 无 <5 的检查项")
        L.append("")

    if warns:
        L.append("## 采集告警")
        L.extend(f"- {w}" for w in warns)
    print("\n".join(L))


if __name__ == "__main__":
    main()
