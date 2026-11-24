from __future__ import annotations

import base64
import concurrent.futures
import datetime as dt
import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request


API = "https://api.github.com"


def _base64url(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _app_jwt(app_id, private_key_path, now=None):
    moment = int(time.time() if now is None else now)
    header = _base64url(json.dumps({"alg": "RS256", "typ": "JWT"}, separators=(",", ":")).encode("utf-8"))
    payload = _base64url(json.dumps(
        {"iat": moment - 60, "exp": moment + 9 * 60, "iss": str(app_id)},
        separators=(",", ":")).encode("utf-8"))
    signing_input = f"{header}.{payload}".encode("ascii")
    result = subprocess.run(
        ["/usr/bin/openssl", "dgst", "-sha256", "-sign", str(private_key_path)],
        input=signing_input, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if result.returncode != 0:
        raise RuntimeError("GitHub App 私钥签名失败；请检查私钥路径和文件权限")
    signature = _base64url(result.stdout)
    return f"{header}.{payload}.{signature}"


def _expires(value):
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0


class GitHubAppClient:
    def __init__(self, app_id=None, installation_id=None, private_key_path=None,
                 transport=None, jwt_factory=None):
        self.app_id = str(app_id or os.environ.get("MKT_GITHUB_APP_ID") or "")
        self.installation_id = str(installation_id or os.environ.get("MKT_GITHUB_APP_INSTALLATION_ID") or "")
        self.private_key_path = str(private_key_path or os.environ.get("MKT_GITHUB_APP_PRIVATE_KEY_PATH") or "")
        self.transport = transport or self._urllib_transport
        self.jwt_factory = jwt_factory or (lambda: _app_jwt(self.app_id, self.private_key_path))
        self._token = ""
        self._token_expires = 0
        self._token_lock = threading.Lock()

    @classmethod
    def from_env(cls):
        return cls()

    def _urllib_transport(self, method, path, token=None, payload=None):
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "growovo-github-app",
                   "X-GitHub-Api-Version": "2022-11-28"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        data = None
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(API + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            raise RuntimeError(f"GitHub App API HTTP {exc.code}: {detail}") from exc
        return json.loads(raw.decode("utf-8")) if raw else {}

    def _request(self, method, path, token=None, payload=None):
        return self.transport(method, path, token=token, payload=payload)

    def installation_token(self):
        supplied = os.environ.get("MKT_GITHUB_APP_INSTALLATION_TOKEN")
        if supplied:
            return supplied
        if self._token and self._token_expires > time.time() + 60:
            return self._token
        with self._token_lock:
            if self._token and self._token_expires > time.time() + 60:
                return self._token
            if not all((self.app_id, self.installation_id, self.private_key_path)):
                raise ValueError("GitHub App 需要 APP_ID、INSTALLATION_ID 和 PRIVATE_KEY_PATH")
            response = self._request(
                "POST", f"/app/installations/{self.installation_id}/access_tokens",
                token=self.jwt_factory())
            token = str(response.get("token") or "")
            if not token:
                raise RuntimeError("GitHub App 未返回 installation token")
            self._token = token
            self._token_expires = _expires(response.get("expires_at"))
            return token

    def _get(self, path):
        return self._request("GET", path, token=self.installation_token())

    def _pages(self, path):
        page = 1
        while True:
            separator = "&" if "?" in path else "?"
            rows = self._get(f"{path}{separator}per_page=100&page={page}")
            if not isinstance(rows, list):
                raise RuntimeError("GitHub App 分页接口未返回数组")
            for row in rows:
                yield row
            if len(rows) < 100:
                return
            page += 1

    def sync_issue_interactions(self, slug):
        issues = [row for row in self._pages(f"/repos/{slug}/issues?state=open")
                  if not row.get("pull_request")]

        def read_comments(issue):
            number = issue["number"]
            return issue, list(self._pages(f"/repos/{slug}/issues/{number}/comments"))

        if len(issues) > 1:
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                pages = list(pool.map(read_comments, issues))
        else:
            pages = [read_comments(issue) for issue in issues]
        rows = []
        for issue, comments in pages:
            number = issue["number"]
            title = str(issue.get("title") or "")
            body = str(issue.get("body") or "")
            base = {
                "source": "github_app", "parent_id": f"{slug}#{number}",
                "conversation_key": f"{slug}#{number}",
                "repository": slug, "installation_id": self.installation_id,
                "trust": "untrusted_external_content",
            }
            rows.append(dict(base, external_id=f"issue-{number}", author=(issue.get("user") or {}).get("login") or "unknown",
                             actor_id=str((issue.get("user") or {}).get("id") or ""),
                             body=title + ("\n\n" + body if body else ""),
                             received_at=issue.get("created_at") or issue.get("updated_at"),
                             url=issue.get("html_url") or "", external_object_type="issue"))
            for comment in comments:
                rows.append(dict(base, external_id=f"issue-comment-{comment['id']}",
                                 author=(comment.get("user") or {}).get("login") or "unknown",
                                 actor_id=str((comment.get("user") or {}).get("id") or ""),
                                 body=str(comment.get("body") or ""), received_at=comment.get("created_at"),
                                 url=comment.get("html_url") or "", external_object_type="issue_comment"))
        return [row for row in rows if row.get("received_at")]

    def find_issue_comment(self, slug, number, comment_id):
        return self._get(f"/repos/{slug}/issues/comments/{comment_id}")

    def create_issue(self, slug, title, text, token=None):
        self._slug(slug)
        response = self._request(
            "POST", f"/repos/{slug}/issues", token=token or self.installation_token(),
            payload={"title": title, "body": text})
        if not response.get("number") or not response.get("html_url"):
            raise RuntimeError("GitHub App 未返回完整 Issue 回执")
        return response

    def get_issue(self, slug, number, token=None):
        self._slug(slug)
        return self._request(
            "GET", f"/repos/{slug}/issues/{number}", token=token or self.installation_token())

    def _slug(self, slug):
        owner, separator, name = str(slug or "").partition("/")
        if not separator or not owner or not name:
            raise ValueError("repository 应为 owner/repo")
        return owner, name

    def _graphql(self, query, variables=None, token=None):
        response = self._request(
            "POST", "/graphql", token=token or self.installation_token(),
            payload={"query": query, "variables": variables or {}})
        if response.get("errors"):
            raise RuntimeError("GitHub GraphQL 返回错误：" + json.dumps(response["errors"], ensure_ascii=False)[:300])
        return response

    def _discussion_page(self, slug, number, after=None, token=None):
        owner, name = self._slug(slug)
        query = """
        query Discussion($owner: String!, $name: String!, $number: Int!, $after: String) {
          repository(owner: $owner, name: $name) {
            discussion(number: $number) {
              id number title body url createdAt closed
              author { login }
              comments(first: 100, after: $after) {
                nodes { id body url createdAt author { login } }
                pageInfo { hasNextPage endCursor }
              }
            }
          }
        }
        """
        response = self._graphql(query, {"owner": owner, "name": name, "number": number, "after": after}, token=token)
        discussion = (response.get("data") or {}).get("repository", {}).get("discussion")
        if not discussion:
            raise ValueError(f"Discussion 不存在或不可访问 {slug}#{number}")
        return discussion

    def discussion(self, slug, number, token=None):
        return self._discussion_page(slug, number, token=token)

    def _discussion_comments(self, slug, number, token=None):
        after = None
        while True:
            discussion = self._discussion_page(slug, number, after=after, token=token)
            comments = discussion.get("comments") or {}
            for row in comments.get("nodes") or []:
                yield row
            page = comments.get("pageInfo") or {}
            if not page.get("hasNextPage"):
                return
            after = page.get("endCursor")

    def sync_discussion_interactions(self, slug):
        owner, name = self._slug(slug)
        query = """
        query Discussions($owner: String!, $name: String!, $after: String) {
          repository(owner: $owner, name: $name) {
            discussions(first: 100, after: $after) {
              nodes { id number title body url createdAt closed author { login } }
              pageInfo { hasNextPage endCursor }
            }
          }
        }
        """
        output = []
        after = None
        while True:
            response = self._graphql(query, {"owner": owner, "name": name, "after": after})
            discussions = (response.get("data") or {}).get("repository", {}).get("discussions") or {}
            for discussion in discussions.get("nodes") or []:
                if discussion.get("closed"):
                    continue
                number = discussion["number"]
                base = {
                    "source": "github_app", "parent_id": f"{slug}/discussion#{number}",
                    "conversation_key": f"{slug}/discussion#{number}",
                    "repository": slug, "installation_id": self.installation_id,
                    "trust": "untrusted_external_content",
                }
                title = str(discussion.get("title") or "")
                body = str(discussion.get("body") or "")
                output.append(dict(base, external_id=f"discussion-{number}",
                                   author=(discussion.get("author") or {}).get("login") or "unknown",
                                   actor_id="", body=title + ("\n\n" + body if body else ""),
                                   received_at=discussion.get("createdAt"), url=discussion.get("url") or "",
                                   external_object_type="discussion"))
                for comment in self._discussion_comments(slug, number):
                    output.append(dict(base, external_id=f"discussion-comment-{comment['id']}",
                                       author=(comment.get("author") or {}).get("login") or "unknown",
                                       actor_id="", body=str(comment.get("body") or ""),
                                       received_at=comment.get("createdAt"), url=comment.get("url") or "",
                                       external_object_type="discussion_comment"))
            page = discussions.get("pageInfo") or {}
            if not page.get("hasNextPage"):
                break
            after = page.get("endCursor")
        return [row for row in output if row.get("received_at")]

    def sync_community_interactions(self, slug):
        return self.sync_issue_interactions(slug) + self.sync_discussion_interactions(slug)

    def add_discussion_comment(self, slug, number, text, token=None):
        discussion = self.discussion(slug, number, token=token)
        query = """
        mutation AddComment($input: AddDiscussionCommentInput!) {
          addDiscussionComment(input: $input) {
            comment { id url body author { login } }
          }
        }
        """
        response = self._graphql(query, {"input": {"discussionId": discussion["id"], "body": text}}, token=token)
        comment = (response.get("data") or {}).get("addDiscussionComment", {}).get("comment")
        if not comment or not comment.get("id") or not comment.get("url"):
            raise RuntimeError("GitHub Discussion 未返回完整评论回执")
        return comment

    def discussion_comment(self, slug, number, comment_id, token=None):
        query = """
        query Comment($id: ID!) {
          node(id: $id) {
            ... on DiscussionComment { id url body author { login } }
          }
        }
        """
        response = self._graphql(query, {"id": comment_id}, token=token)
        return (response.get("data") or {}).get("node") or {}

    def find_discussion_comments(self, slug, number, token=None):
        return list(self._discussion_comments(slug, number, token=token))


    def get_repository(self, slug, token=None):
        self._slug(slug)
        return self._request("GET", f"/repos/{slug}", token=token or self.installation_token())
