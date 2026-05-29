"""GitHub REST client for PR create/merge — run locally with the gh token.

The sandbox pushes the branch; the orchestrator (here) opens and merges the PR into the
INTEGRATION BRANCH. main is never targeted.
"""
import json
import urllib.request
import urllib.error

REPO = "marcosremar/babylon-cinema"
API = "https://api.github.com"


class GitHub:
    def __init__(self, token: str, repo: str = REPO):
        self.token = token
        self.repo = repo

    def _req(self, method: str, path: str, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            f"{API}{path}", data=data, method=method,
            headers={"Authorization": f"Bearer {self.token}",
                     "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read().decode()
                return json.loads(raw) if raw.strip() else {}
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"{method} {path} -> HTTP {e.code}: {e.read().decode()[:400]}")

    def create_pr(self, head: str, base: str, title: str, body: str) -> dict:
        return self._req("POST", f"/repos/{self.repo}/pulls",
                         {"head": head, "base": base, "title": title, "body": body})

    def merge_pr(self, number: int, method="squash") -> dict:
        return self._req("PUT", f"/repos/{self.repo}/pulls/{number}/merge",
                         {"merge_method": method})

    def pr_files(self, number: int) -> list[str]:
        files = self._req("GET", f"/repos/{self.repo}/pulls/{number}/files")
        return [f["filename"] for f in files]

    def file_exists(self, path: str, ref: str) -> bool:
        try:
            self._req("GET", f"/repos/{self.repo}/contents/{path}?ref={ref}")
            return True
        except RuntimeError as e:
            if "404" in str(e):
                return False
            raise

    def open_pr_for(self, head_branch: str, base: str) -> dict | None:
        # head must be qualified owner:branch for cross-fork, here same-repo so owner:branch
        owner = self.repo.split("/")[0]
        prs = self._req("GET", f"/repos/{self.repo}/pulls?state=open&head={owner}:{head_branch}&base={base}")
        return prs[0] if prs else None
