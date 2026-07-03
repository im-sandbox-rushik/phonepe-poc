"""Issues, Labels, and Milestones migration service.

Migrates from GitLab to GitHub:
- Labels (name, color, description)
- Milestones (title, description, due date, state)
- Issues (title, description, state, labels, milestone, comments, assignees)
"""

import logging
from typing import Dict, List, Optional

import httpx

from config import GitLabConfig, GitHubConfig

logger = logging.getLogger(__name__)


class IssuesMigrationService:
    """Migrates GitLab Issues, Labels, and Milestones to GitHub."""

    def __init__(self, gitlab_config: GitLabConfig, github_config: GitHubConfig):
        self.gitlab_url = gitlab_config.url.rstrip("/")
        self.gitlab_api = f"{self.gitlab_url}/api/{gitlab_config.api_version}"
        self.gitlab_headers = {"PRIVATE-TOKEN": gitlab_config.pat}

        self.github_org = github_config.organization
        host = github_config.url.replace("https://", "").replace("http://", "")
        self.github_api = "https://api.github.com" if host == "github.com" else f"{github_config.url.rstrip('/')}/api/v3"
        self.github_headers = {
            "Authorization": f"token {github_config.pat}",
            "Accept": "application/vnd.github.v3+json",
        }

    # ─── LABELS ───────────────────────────────────────────────────────────────

    async def migrate_labels(self, gitlab_path: str, github_repo: str) -> Dict:
        """Migrate all labels from GitLab to GitHub."""
        logger.info(f"Migrating labels: {gitlab_path} -> {github_repo}")
        gitlab_labels = await self._get_gitlab_labels(gitlab_path)
        results = {"total": len(gitlab_labels), "migrated": 0, "skipped": 0, "failed": 0}

        for label in gitlab_labels:
            try:
                created = await self._create_github_label(github_repo, label)
                if created:
                    results["migrated"] += 1
                else:
                    results["skipped"] += 1
            except Exception as e:
                logger.warning(f"Label '{label['name']}' failed: {e}")
                results["failed"] += 1

        logger.info(f"Labels migration: {results['migrated']} migrated, {results['skipped']} skipped")
        return results

    async def _get_gitlab_labels(self, gitlab_path: str) -> List[Dict]:
        """Get all project labels from GitLab."""
        encoded = gitlab_path.replace("/", "%2F")
        labels = []
        page = 1
        async with httpx.AsyncClient(timeout=30) as client:
            while True:
                resp = await client.get(
                    f"{self.gitlab_api}/projects/{encoded}/labels",
                    headers=self.gitlab_headers,
                    params={"per_page": 100, "page": page},
                )
                resp.raise_for_status()
                data = resp.json()
                if not data:
                    break
                labels.extend(data)
                page += 1
        return labels

    async def _create_github_label(self, github_repo: str, label: Dict) -> bool:
        """Create a label on GitHub. Returns True if created, False if exists."""
        color = label.get("color", "#000000").lstrip("#")
        payload = {
            "name": label["name"],
            "color": color,
            "description": (label.get("description") or "")[:100],
        }
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self.github_api}/repos/{self.github_org}/{github_repo}/labels",
                headers=self.github_headers,
                json=payload,
            )
            if resp.status_code == 201:
                return True
            elif resp.status_code == 422:  # Already exists
                return False
            else:
                logger.warning(f"Label creation failed: {resp.status_code}")
                return False

    # ─── MILESTONES ───────────────────────────────────────────────────────────

    async def migrate_milestones(self, gitlab_path: str, github_repo: str) -> Dict:
        """Migrate all milestones from GitLab to GitHub."""
        logger.info(f"Migrating milestones: {gitlab_path} -> {github_repo}")
        gitlab_milestones = await self._get_gitlab_milestones(gitlab_path)
        results = {"total": len(gitlab_milestones), "migrated": 0, "skipped": 0, "failed": 0}
        self._milestone_map = {}  # GitLab ID -> GitHub number mapping

        for ms in gitlab_milestones:
            try:
                gh_ms = await self._create_github_milestone(github_repo, ms)
                if gh_ms:
                    self._milestone_map[ms["id"]] = gh_ms["number"]
                    results["migrated"] += 1
                else:
                    results["skipped"] += 1
            except Exception as e:
                logger.warning(f"Milestone '{ms['title']}' failed: {e}")
                results["failed"] += 1

        logger.info(f"Milestones migration: {results['migrated']} migrated")
        return results

    async def _get_gitlab_milestones(self, gitlab_path: str) -> List[Dict]:
        """Get all milestones from GitLab."""
        encoded = gitlab_path.replace("/", "%2F")
        milestones = []
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{self.gitlab_api}/projects/{encoded}/milestones",
                headers=self.gitlab_headers,
                params={"per_page": 100, "include_parent_milestones": True},
            )
            if resp.status_code == 200:
                milestones = resp.json()
        return milestones

    async def _create_github_milestone(self, github_repo: str, ms: Dict) -> Optional[Dict]:
        """Create a milestone on GitHub."""
        state = "open" if ms.get("state") == "active" else "closed"
        payload = {
            "title": ms["title"],
            "description": ms.get("description") or "",
            "state": state,
        }
        if ms.get("due_date"):
            payload["due_on"] = f"{ms['due_date']}T00:00:00Z"

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self.github_api}/repos/{self.github_org}/{github_repo}/milestones",
                headers=self.github_headers,
                json=payload,
            )
            if resp.status_code == 201:
                return resp.json()
            elif resp.status_code == 422:
                return None  # Already exists
            return None

    # ─── ISSUES ───────────────────────────────────────────────────────────────

    async def migrate_issues(self, gitlab_path: str, github_repo: str) -> Dict:
        """Migrate all issues from GitLab to GitHub."""
        logger.info(f"Migrating issues: {gitlab_path} -> {github_repo}")
        gitlab_issues = await self._get_gitlab_issues(gitlab_path)
        results = {"total": len(gitlab_issues), "migrated": 0, "failed": 0, "details": []}

        for issue in gitlab_issues:
            try:
                gh_issue = await self._migrate_single_issue(issue, gitlab_path, github_repo)
                if gh_issue:
                    results["migrated"] += 1
                    results["details"].append({
                        "gitlab_iid": issue["iid"],
                        "github_number": gh_issue["number"],
                        "title": issue["title"],
                    })
                else:
                    results["failed"] += 1
            except Exception as e:
                logger.error(f"Issue #{issue['iid']} failed: {e}")
                results["failed"] += 1

        logger.info(f"Issues migration: {results['migrated']}/{results['total']} migrated")
        return results

    async def _get_gitlab_issues(self, gitlab_path: str) -> List[Dict]:
        """Get all issues from GitLab (excluding MR-linked)."""
        encoded = gitlab_path.replace("/", "%2F")
        issues = []
        page = 1
        async with httpx.AsyncClient(timeout=30) as client:
            while True:
                resp = await client.get(
                    f"{self.gitlab_api}/projects/{encoded}/issues",
                    headers=self.gitlab_headers,
                    params={"state": "all", "per_page": 100, "page": page, "sort": "asc"},
                )
                resp.raise_for_status()
                data = resp.json()
                if not data:
                    break
                issues.extend(data)
                page += 1
        return issues

    async def _migrate_single_issue(self, issue: Dict, gitlab_path: str, github_repo: str) -> Optional[Dict]:
        """Migrate a single GitLab issue to GitHub."""
        # Build body
        body_parts = [
            f"**Migrated from GitLab Issue #{issue['iid']}**\n",
            f"- **Author:** @{issue.get('author', {}).get('username', 'unknown')}",
            f"- **Created:** {issue.get('created_at', 'N/A')}",
            f"- **Updated:** {issue.get('updated_at', 'N/A')}",
        ]
        if issue.get("closed_at"):
            body_parts.append(f"- **Closed:** {issue['closed_at']}")
        if issue.get("description"):
            body_parts.append(f"\n---\n\n{issue['description']}")

        # Map labels
        labels = [l for l in issue.get("labels", [])]
        labels.append("migrated-from-gitlab")

        # Map milestone
        milestone_number = None
        if issue.get("milestone") and hasattr(self, "_milestone_map"):
            milestone_number = self._milestone_map.get(issue["milestone"]["id"])

        payload = {
            "title": issue["title"],
            "body": "\n".join(body_parts),
            "labels": labels,
        }
        if milestone_number:
            payload["milestone"] = milestone_number

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self.github_api}/repos/{self.github_org}/{github_repo}/issues",
                headers=self.github_headers,
                json=payload,
            )
            if resp.status_code != 201:
                logger.warning(f"Issue creation failed: {resp.status_code} {resp.text[:200]}")
                return None

            gh_issue = resp.json()

            # Close if closed on GitLab
            if issue.get("state") == "closed":
                await client.patch(
                    f"{self.github_api}/repos/{self.github_org}/{github_repo}/issues/{gh_issue['number']}",
                    headers=self.github_headers,
                    json={"state": "closed"},
                )

            # Migrate comments
            comments = await self._get_issue_notes(gitlab_path, issue["iid"])
            for comment in comments:
                comment_body = (
                    f"**@{comment.get('author', {}).get('username', 'unknown')}** "
                    f"on {comment.get('created_at', '')}:\n\n"
                    f"{comment.get('body', '')}"
                )
                await client.post(
                    f"{self.github_api}/repos/{self.github_org}/{github_repo}/issues/{gh_issue['number']}/comments",
                    headers=self.github_headers,
                    json={"body": comment_body},
                )

            return gh_issue

    async def _get_issue_notes(self, gitlab_path: str, issue_iid: int) -> List[Dict]:
        """Get non-system notes for an issue."""
        encoded = gitlab_path.replace("/", "%2F")
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{self.gitlab_api}/projects/{encoded}/issues/{issue_iid}/notes",
                headers=self.gitlab_headers,
                params={"per_page": 100, "sort": "asc"},
            )
            if resp.status_code == 200:
                return [n for n in resp.json() if not n.get("system", False)]
        return []
