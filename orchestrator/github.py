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

    def _req(self, method: str, path: str, body=None, retries=4):
        import time, random
        data = json.dumps(body).encode() if body is not None else None
        last = None
        for attempt in range(retries):
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
                code = e.code
                body_txt = e.read().decode()[:400]
                last = RuntimeError(f"{method} {path} -> HTTP {code}: {body_txt}")
                if code == 401:
                    raise last from e
                # 403 with rate-limit header or "API rate limit" = rate-limited, retry
                if code == 403 and "rate limit" in body_txt.lower() and attempt < retries - 1:
                    wait = min(60, 10 * (attempt + 1))
                    time.sleep(wait + random.uniform(0, 2))
                    continue
                if code in (502, 503, 504) and attempt < retries - 1:
                    time.sleep(min(30, 2 ** (attempt + 1)) + random.uniform(0, 1))
                    continue
                raise last
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last = RuntimeError(f"{method} {path} -> {type(e).__name__}: {e}")
                if attempt < retries - 1:
                    time.sleep(min(30, 2 ** (attempt + 1)) + random.uniform(0, 1))
                    continue
                raise last
        raise last

    def create_pr(self, head: str, base: str, title: str, body: str) -> dict:
        existing = self.open_pr_for(head, base)
        if existing:
            return existing
        return self._req("POST", f"/repos/{self.repo}/pulls",
                         {"head": head, "base": base, "title": title, "body": body})

    def merge_pr(self, number: int, method="squash") -> dict:
        try:
            return self._req("PUT", f"/repos/{self.repo}/pulls/{number}/merge",
                             {"merge_method": method})
        except RuntimeError as e:
            msg = str(e)
            is_conflict = ("merge conflict" in msg.lower() or
                           "refusing to allow a github app" in msg.lower())
            if is_conflict:
                raise RuntimeError(f"MERGE_CONFLICT PR#{number}: {msg}") from e
            raise

    def pr_files(self, number: int) -> list[str]:
        """Return ALL changed filenames in a PR (paginated, up to 300)."""
        files = []
        page = 1
        while True:
            batch = self._req("GET", f"/repos/{self.repo}/pulls/{number}/files?per_page=100&page={page}")
            if not batch:
                break
            if not isinstance(batch, list):
                import logging
                logging.error(f"pr_files #{number} page {page}: expected list, got {type(batch).__name__}: {str(batch)[:200]}")
                break
            files.extend(f["filename"] for f in batch)
            if len(batch) < 100:
                break
            page += 1
            if page > 3:  # safety: max 300 files
                break
        return files

    def ref_exists(self, ref: str) -> bool:
        """Check if a git ref (e.g. 'heads/main') exists on the remote."""
        try:
            self._req("GET", f"/repos/{self.repo}/git/ref/{ref}")
            return True
        except RuntimeError as e:
            msg = str(e)
            if "404" in msg or "422" in msg:
                return False
            raise

    def file_exists(self, path: str, ref: str) -> bool:
        try:
            self._req("GET", f"/repos/{self.repo}/contents/{path}?ref={ref}")
            return True
        except RuntimeError as e:
            if "404" in str(e):
                return False
            raise

    def open_pr_for(self, head_branch: str, base: str) -> dict | None:
        owner = self.repo.split("/")[0]
        prs = self._req("GET", f"/repos/{self.repo}/pulls?state=open&head={owner}:{head_branch}&base={base}")
        if not prs:
            return None
        # pick the newest PR if multiple exist (e.g. from retries)
        return sorted(prs, key=lambda p: p["number"], reverse=True)[0]

    def branch_exists(self, branch: str) -> bool:
        """Check if a branch exists on the remote."""
        try:
            self._req("GET", f"/repos/{self.repo}/branches/{branch}")
            return True
        except RuntimeError as e:
            if "404" in str(e):
                return False
            raise

    def merge_branch_to_ib(self, branch: str, base: str) -> dict:
        """Merge a branch INTO another branch (used for post-merge compat: IB -> PR branch)."""
        return self._req("POST", f"/repos/{self.repo}/merges",
                         {"base": base, "head": branch})
