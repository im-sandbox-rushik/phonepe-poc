"""Webhooks migration service.

Migrates GitLab project webhooks to GitHub repository webhooks.

Mapping:
  GitLab push_events          → GitHub push
  GitLab merge_requests_events → GitHub pull_request
  GitLab issues_events         → GitHub issues
  GitLab tag_push_events       → GitHub create (tag)
  GitLab pipeline_events       → GitHub workflow_run
  GitLab note_events           → GitHub issue_comment
  GitLab releases_events       → GitHub release
"""

import logging
from typing import Dict, List

import httpx

from config import GitLabConfig, GitHubConfig

logger = logging.getLogger(__name__)

# GitLab webhook events → GitHub webhook events
EVENT_MAP = {
    "push_events": "push",
    "merge_requests_events": "pull_request",
    "issues_events": "issues",
    "tag_push_events": "create",
    "pipeline_events": "workflow_run",
    "note_events": "issue_comment",
    "releases_events": "release",
    "job_events": "workflow_job",
    "deployment_events": "deployment",
    "wiki_page_events": "gollum",
}


class WebhooksMigrationService:
    """Migrates GitLab webhooks to GitHub webhooks."""

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

    async def migrate_webhooks(self, gitlab_path: str, github_repo: str) -> Dict:
        """Migrate all webhooks from GitLab to GitHub."""
        logger.info(f"Migrating webhooks: {gitlab_path} -> {github_repo}")
        gitlab_hooks = await self._get_gitlab_webhooks(gitlab_path)
        results = {"total": len(gitlab_hooks), "migrated": 0, "skipped": 0, "failed": 0, "details": []}

        for hook in gitlab_hooks:
            try:
                gh_hook = await self._create_github_webhook(github_repo, hook)
                if gh_hook:
                    results["migrated"] += 1
                    results["details"].append({
                        "url": hook["url"],
                        "status": "migrated",
                        "events": gh_hook.get("events", []),
                    })
                else:
                    results["skipped"] += 1
                    results["details"].append({"url": hook["url"], "status": "skipped"})
            except Exception as e:
                logger.warning(f"Webhook migration failed for {hook['url']}: {e}")
                results["failed"] += 1
                results["details"].append({"url": hook["url"], "status": "failed", "error": str(e)})

        logger.info(f"Webhooks migration: {results['migrated']} migrated, {results['failed']} failed")
        return results

    async def _get_gitlab_webhooks(self, gitlab_path: str) -> List[Dict]:
        """Get all project webhooks from GitLab."""
        encoded = gitlab_path.replace("/", "%2F")
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{self.gitlab_api}/projects/{encoded}/hooks",
                headers=self.gitlab_headers,
                params={"per_page": 100},
            )
            if resp.status_code == 200:
                return resp.json()
        return []

    async def _create_github_webhook(self, github_repo: str, gitlab_hook: Dict) -> Dict:
        """Create a GitHub webhook from a GitLab webhook config."""
        # Map GitLab events to GitHub events
        github_events = []
        for gl_event, gh_event in EVENT_MAP.items():
            if gitlab_hook.get(gl_event, False):
                github_events.append(gh_event)

        if not github_events:
            github_events = ["push"]  # Default

        payload = {
            "name": "web",
            "active": True,
            "events": github_events,
            "config": {
                "url": gitlab_hook["url"],
                "content_type": "json",
                "insecure_ssl": "1" if not gitlab_hook.get("enable_ssl_verification", True) else "0",
            },
        }

        # Include secret token if available
        if gitlab_hook.get("token"):
            payload["config"]["secret"] = gitlab_hook["token"]

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self.github_api}/repos/{self.github_org}/{github_repo}/hooks",
                headers=self.github_headers,
                json=payload,
            )
            if resp.status_code == 201:
                return resp.json()
            elif resp.status_code == 422:
                return None  # Already exists
            else:
                logger.warning(f"Webhook creation failed: {resp.status_code}")
                return None
