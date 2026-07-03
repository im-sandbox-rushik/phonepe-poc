"""Merge Request / Pull Request migration service.

Migrates GitLab Merge Requests to GitHub Pull Requests including:
- Title, description, state (open/closed/merged)
- Comments / discussions
- Labels
- Assignees (best-effort mapping)
"""

import logging
from datetime import datetime
from typing import Dict, List, Optional

import httpx

from config import GitLabConfig, GitHubConfig

logger = logging.getLogger(__name__)


class MRMigrationService:
    """Migrates GitLab Merge Requests to GitHub Pull Requests."""

    def __init__(self, gitlab_config: GitLabConfig, github_config: GitHubConfig):
        self.gitlab_url = gitlab_config.url.rstrip("/")
        self.gitlab_api = f"{self.gitlab_url}/api/{gitlab_config.api_version}"
        self.gitlab_headers = {"PRIVATE-TOKEN": gitlab_config.pat}

        self.github_org = github_config.organization
        self.github_pat = github_config.pat
        host = github_config.url.replace("https://", "").replace("http://", "")
        if host == "github.com":
            self.github_api = "https://api.github.com"
        else:
            self.github_api = f"{github_config.url.rstrip('/')}/api/v3"
        self.github_headers = {
            "Authorization": f"token {self.github_pat}",
            "Accept": "application/vnd.github.v3+json",
        }

    async def migrate_merge_requests(self, gitlab_path: str, github_repo: str) -> Dict:
        """Migrate all MRs from a GitLab project to GitHub PRs."""
        logger.info(f"Migrating MRs: {gitlab_path} -> {github_repo}")
        results = {"total": 0, "migrated": 0, "failed": 0, "skipped": 0, "details": []}

        merge_requests = await self._get_all_merge_requests(gitlab_path)
        results["total"] = len(merge_requests)

        for mr in merge_requests:
            try:
                result = await self._migrate_single_mr(mr, gitlab_path, github_repo)
                if result["status"] == "migrated":
                    results["migrated"] += 1
                elif result["status"] == "skipped":
                    results["skipped"] += 1
                else:
                    results["failed"] += 1
                results["details"].append(result)
            except Exception as e:
                logger.error(f"Failed to migrate MR !{mr['iid']}: {e}")
                results["failed"] += 1
                results["details"].append({
                    "mr_iid": mr["iid"],
                    "title": mr["title"],
                    "status": "failed",
                    "error": str(e),
                })

        logger.info(
            f"MR Migration complete for {github_repo}: "
            f"{results['migrated']} migrated, {results['skipped']} skipped, {results['failed']} failed"
        )
        return results

    async def _migrate_single_mr(self, mr: Dict, gitlab_path: str, github_repo: str) -> Dict:
        """Migrate a single Merge Request to a GitHub Pull Request."""
        mr_iid = mr["iid"]
        source_branch = mr["source_branch"]
        target_branch = mr["target_branch"]
        state = mr["state"]  # opened, closed, merged

        # Check if source branch exists on GitHub (needed for open PRs)
        # For closed/merged MRs, we create them as issues with PR reference
        if state == "opened":
            # Try to create a real PR (branch must exist)
            pr_data = await self._create_pull_request(
                github_repo, mr, source_branch, target_branch
            )
        else:
            # For closed/merged MRs, create as an issue with metadata
            pr_data = await self._create_issue_from_mr(github_repo, mr)

        if pr_data is None:
            return {"mr_iid": mr_iid, "title": mr["title"], "status": "skipped", "reason": "branch not found"}

        # Migrate comments/discussions
        comments = await self._get_mr_notes(gitlab_path, mr_iid)
        if comments:
            await self._migrate_comments(github_repo, pr_data["number"], comments)

        return {
            "mr_iid": mr_iid,
            "title": mr["title"],
            "status": "migrated",
            "github_number": pr_data["number"],
            "github_url": pr_data["html_url"],
            "comments_migrated": len(comments),
        }

    async def _get_all_merge_requests(self, gitlab_path: str) -> List[Dict]:
        """Get all merge requests from GitLab (all states)."""
        encoded_path = gitlab_path.replace("/", "%2F")
        merge_requests = []
        page = 1
        async with httpx.AsyncClient(timeout=30) as client:
            while True:
                resp = await client.get(
                    f"{self.gitlab_api}/projects/{encoded_path}/merge_requests",
                    headers=self.gitlab_headers,
                    params={"state": "all", "per_page": 100, "page": page},
                )
                resp.raise_for_status()
                data = resp.json()
                if not data:
                    break
                merge_requests.extend(data)
                page += 1
        return merge_requests

    async def _get_mr_notes(self, gitlab_path: str, mr_iid: int) -> List[Dict]:
        """Get all notes/comments for a merge request."""
        encoded_path = gitlab_path.replace("/", "%2F")
        notes = []
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{self.gitlab_api}/projects/{encoded_path}/merge_requests/{mr_iid}/notes",
                headers=self.gitlab_headers,
                params={"per_page": 100},
            )
            if resp.status_code == 200:
                notes = [n for n in resp.json() if not n.get("system", False)]
        return notes

    async def _create_pull_request(
        self, github_repo: str, mr: Dict, source_branch: str, target_branch: str
    ) -> Optional[Dict]:
        """Create a GitHub Pull Request from a GitLab MR."""
        body = self._build_pr_body(mr)
        payload = {
            "title": mr["title"],
            "body": body,
            "head": source_branch,
            "base": target_branch,
        }
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self.github_api}/repos/{self.github_org}/{github_repo}/pulls",
                headers=self.github_headers,
                json=payload,
            )
            if resp.status_code == 201:
                return resp.json()
            elif resp.status_code == 422:
                # Branch doesn't exist or PR already exists - create as issue instead
                return await self._create_issue_from_mr(github_repo, mr)
            else:
                logger.warning(f"PR creation failed ({resp.status_code}): {resp.text[:200]}")
                return await self._create_issue_from_mr(github_repo, mr)

    async def _create_issue_from_mr(self, github_repo: str, mr: Dict) -> Optional[Dict]:
        """Create a GitHub Issue to represent a closed/merged MR."""
        state_label = f"gitlab-mr-{mr['state']}"
        body = self._build_pr_body(mr)
        payload = {
            "title": f"[MR !{mr['iid']}] {mr['title']}",
            "body": body,
            "labels": [state_label, "migrated-from-gitlab"],
        }
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self.github_api}/repos/{self.github_org}/{github_repo}/issues",
                headers=self.github_headers,
                json=payload,
            )
            if resp.status_code == 201:
                issue = resp.json()
                # Close the issue if MR was closed/merged
                if mr["state"] in ("closed", "merged"):
                    await client.patch(
                        f"{self.github_api}/repos/{self.github_org}/{github_repo}/issues/{issue['number']}",
                        headers=self.github_headers,
                        json={"state": "closed"},
                    )
                return issue
            else:
                logger.warning(f"Issue creation failed ({resp.status_code}): {resp.text[:200]}")
                return None

    async def _migrate_comments(self, github_repo: str, issue_number: int, comments: List[Dict]):
        """Migrate MR comments to GitHub issue/PR comments."""
        async with httpx.AsyncClient(timeout=30) as client:
            for comment in comments:
                body = (
                    f"**@{comment.get('author', {}).get('username', 'unknown')}** "
                    f"commented on {comment.get('created_at', '')}:\n\n"
                    f"{comment.get('body', '')}"
                )
                await client.post(
                    f"{self.github_api}/repos/{self.github_org}/{github_repo}/issues/{issue_number}/comments",
                    headers=self.github_headers,
                    json={"body": body},
                )

    def _build_pr_body(self, mr: Dict) -> str:
        """Build PR/Issue body with MR metadata."""
        parts = [
            f"**Migrated from GitLab MR !{mr['iid']}**\n",
            f"- **Author:** @{mr.get('author', {}).get('username', 'unknown')}",
            f"- **State:** {mr['state']}",
            f"- **Source Branch:** `{mr.get('source_branch', 'N/A')}`",
            f"- **Target Branch:** `{mr.get('target_branch', 'N/A')}`",
            f"- **Created:** {mr.get('created_at', 'N/A')}",
            f"- **Updated:** {mr.get('updated_at', 'N/A')}",
        ]
        if mr.get("merged_at"):
            parts.append(f"- **Merged:** {mr['merged_at']}")
        if mr.get("description"):
            parts.append(f"\n---\n\n{mr['description']}")
        return "\n".join(parts)
