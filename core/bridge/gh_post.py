#!/usr/bin/env python3
"""GitHub public comment poster / candidate finder via the gh CLI.

  python3 gh_post.py post   --repo owner/name --issue 123 --file draft.md
  python3 gh_post.py verify --repo owner/name --issue 123 --prefix "first 40 chars"
  python3 gh_post.py issue  --repo owner/name --issue 123
  python3 gh_post.py search --q 'is:issue is:open ...' --limit 30

`gh` lives at ~/.local/bin/gh on this machine and is not on PATH.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

GH = os.environ.get("MKT_GH_BIN") or shutil.which("gh") or "/Users/tongxiarui/.local/bin/gh"


def run(args, stdin=None, timeout=120):
    p = subprocess.run([GH] + args, capture_output=True, text=True, input=stdin, timeout=timeout)
    if p.returncode != 0:
        return {"ok": False, "err": (p.stderr or p.stdout).strip()[:600], "code": p.returncode}
    return {"ok": True, "out": p.stdout.strip()}


def post(repo, issue, path):
    r = run(["api", "--method", "POST", "repos/%s/issues/%s/comments" % (repo, issue),
             "-F", "body=@" + os.path.abspath(path)])
    if not r["ok"]:
        return r
    try:
        d = json.loads(r["out"])
    except ValueError:
        return {"ok": False, "err": "unparseable response: " + r["out"][:200]}
    return {"ok": True, "url": d.get("html_url"), "id": d.get("id"),
            "created_at": d.get("created_at")}


def verify(repo, issue, prefix):
    r = run(["api", "repos/%s/issues/%s/comments?per_page=100" % (repo, issue)])
    if not r["ok"]:
        return r
    try:
        rows = json.loads(r["out"])
    except ValueError:
        return {"ok": False, "err": "unparseable response"}
    hits = [c for c in rows if prefix[:60] in (c.get("body") or "")]
    return {"ok": True, "found": bool(hits),
            "url": hits[0].get("html_url") if hits else None,
            "count": len(hits)}


def issue(repo, num, timeout=20):
    r = run(["api", "repos/%s/issues/%s" % (repo, num)], timeout=timeout)
    if not r["ok"]:
        return r
    d = json.loads(r["out"])
    return {"ok": True, "title": d.get("title"), "state": d.get("state"),
            "locked": bool(d.get("locked")),
            "comments": d.get("comments"), "author": (d.get("user") or {}).get("login"),
            "created_at": d.get("created_at"), "body": (d.get("body") or "")[:2500],
            "url": d.get("html_url"), "labels": [l.get("name") for l in d.get("labels", [])]}


def search(q, limit):
    r = run(["api", "-X", "GET", "search/issues", "-f", "q=" + q,
             "-f", "per_page=%d" % limit, "-f", "sort=created", "-f", "order=desc"])
    if not r["ok"]:
        return r
    d = json.loads(r["out"])
    rows = []
    for it in d.get("items", []):
        rows.append({
            "repo": (it.get("repository_url") or "").split("/repos/")[-1],
            "number": it.get("number"),
            "title": it.get("title"),
            "url": it.get("html_url"),
            "comments": it.get("comments"),
            "created_at": it.get("created_at"),
            "author": (it.get("user") or {}).get("login"),
            "is_pr": bool(it.get("pull_request")),
            "body_len": len(it.get("body") or ""),
            "body": (it.get("body") or "")[:700],
        })
    return {"ok": True, "total": d.get("total_count"), "items": rows}


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("post")
    a.add_argument("--repo", required=True)
    a.add_argument("--issue", required=True)
    a.add_argument("--file", required=True)
    b = sub.add_parser("verify")
    b.add_argument("--repo", required=True)
    b.add_argument("--issue", required=True)
    b.add_argument("--prefix", required=True)
    c = sub.add_parser("issue")
    c.add_argument("--repo", required=True)
    c.add_argument("--issue", required=True)
    d = sub.add_parser("search")
    d.add_argument("--q", required=True)
    d.add_argument("--limit", type=int, default=30)
    args = p.parse_args()
    if args.cmd == "post":
        out = post(args.repo, args.issue, args.file)
    elif args.cmd == "verify":
        out = verify(args.repo, args.issue, args.prefix)
    elif args.cmd == "issue":
        out = issue(args.repo, args.issue)
    else:
        out = search(args.q, args.limit)
    print(json.dumps(out, ensure_ascii=False))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
