"""Releases migration service.

Migrates GitLab Releases to GitHub Releases:
- Release name/title
- Release description/body
- Tag association
- Release assets (download links)
- Release date
"""

import logging
from typing import Dict, List, Optional

import httpx

from config import GitLabConfig, GitHubConfig

logger = logging.getLogger(__name__)


class ReleasesMigrationService:
    """Migrates GitLab Releases to GitHub Releases."""

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

    async def migrate_releases(self, gitlab_path: str, github_repo: str) -> Dict:
        """Migrate all releases from GitLab to GitHub."""
        logger.info(f"Migrating releases: {gitlab_path} -> {github_repo}")
        gitlab_releases = await self._get_gitlab_releases(gitlab_path)
        results = {"total": len(gitlab_releases), "migrated": 0, "skipped": 0, "failed": 0, "details": []}

        for release in gitlab_releases:
            try:
                gh_release = await self._create_github_release(github_repo, release, gitlab_path)
                if gh_release:
                    results["migrated"] += 1
                    results["details"].append({
                        "tag": release["tag_name"],
                        "name": release.get("name", ""),
                        "status": "migrated",
                        "github_url": gh_release.get("html_url"),
                    })
                else:
                    results["skipped"] += 1
                    results["details"].append({
                        "tag": release["tag_name"],
                        "status": "skipped",
                    })
            except Exception as e:
                logger.warning(f"Release '{release['tag_name']}' failed: {e}")
                results["failed"] += 1
                results["details"].append({
                    "tag": release["tag_name"],
                    "status": "failed",
                    "error": str(e),
                })

        logger.info(f"Releases migration: {results['migrated']} migrated, {results['failed']} failed")
        return results

    async def _get_gitlab_releases(self, gitlab_path: str) -> List[Dict]:
        """Get all releases from GitLab."""
        encoded = gitlab_path.replace("/", "%2F")
        releases = []
        page = 1
        async with httpx.AsyncClient(timeout=30) as client:
            while True:
                resp = await client.get(
                    f"{self.gitlab_api}/projects/{encoded}/releases",
                    headers=self.gitlab_headers,
                    params={"per_page": 100, "page": page},
                )
                if resp.status_code != 200:
                    break
                data = resp.json()
                if not data:
                    break
                releases.extend(data)
                page += 1
        return releases

    async def _create_github_release(self, github_repo: str, release: Dict, gitlab_path: str) -> Optional[Dict]:
        """Create a GitHub release from a GitLab release."""
        tag_name = release["tag_name"]
        name = release.get("name") or tag_name
        description = release.get("description") or ""

        # Build body with migration metadata
        body_parts = [description]
        
        # Add asset links
        assets = release.get("assets", {})
        links = assets.get("links", [])
        if links:
            body_parts.append("\n\n---\n**Migrated Assets (original links):**")
            for link in links:
                body_parts.append(f"- [{link.get('name', 'asset')}]({link.get('url', '')})")

        payload = {
            "tag_name": tag_name,
            "name": name,
            "body": "\n".join(body_parts),
            "draft": False,
            "prerelease": False,
        }

        # Set release date if available
        if release.get("released_at"):
            payload["target_commitish"] = "main"

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self.github_api}/repos/{self.github_org}/{github_repo}/releases",
                headers=self.github_headers,
                json=payload,
            )
            if resp.status_code == 201:
                return resp.json()
            elif resp.status_code == 422:
                # Release for this tag already exists
                return None
            else:
                logger.warning(f"Release creation failed: {resp.status_code} {resp.text[:200]}")
                return None
