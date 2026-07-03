"""GitHub Enterprise Server API client service."""

import logging
from typing import Dict, List, Optional

import httpx

from config import GitHubConfig

logger = logging.getLogger(__name__)


class GitHubService:
    """Client for GitHub Enterprise Server REST API."""

    def __init__(self, config: GitHubConfig):
        self.base_url = config.url.rstrip("/")
        # github.com uses api.github.com; GHES uses {url}/api/v3
        if "github.com" == self.base_url.replace("https://", "").replace("http://", ""):
            self.api_url = "https://api.github.com"
        else:
            self.api_url = f"{self.base_url}/api/v3"
        self.organization = config.organization
        self.headers = {
            "Authorization": f"token {config.pat}",
            "Accept": "application/vnd.github.v3+json",
        }

    async def get_repository(self, repo_name: str) -> Optional[Dict]:
        """Get repository details."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.api_url}/repos/{self.organization}/{repo_name}",
                headers=self.headers,
            )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()

    async def get_head_sha(self, repo_name: str, branch: str = "main") -> Optional[str]:
        """Get HEAD SHA for a branch."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.api_url}/repos/{self.organization}/{repo_name}/git/ref/heads/{branch}",
                headers=self.headers,
            )
            if resp.status_code == 404:
                if branch == "main":
                    return await self.get_head_sha(repo_name, "master")
                return None
            resp.raise_for_status()
            return resp.json()["object"]["sha"]

    async def get_branches(self, repo_name: str) -> List[Dict]:
        """Get all branches."""
        branches = []
        page = 1
        async with httpx.AsyncClient() as client:
            while True:
                resp = await client.get(
                    f"{self.api_url}/repos/{self.organization}/{repo_name}/branches",
                    headers=self.headers,
                    params={"per_page": 100, "page": page},
                )
                resp.raise_for_status()
                data = resp.json()
                if not data:
                    break
                branches.extend(data)
                page += 1
        return branches

    async def get_tags(self, repo_name: str) -> List[Dict]:
        """Get all tags."""
        tags = []
        page = 1
        async with httpx.AsyncClient() as client:
            while True:
                resp = await client.get(
                    f"{self.api_url}/repos/{self.organization}/{repo_name}/tags",
                    headers=self.headers,
                    params={"per_page": 100, "page": page},
                )
                resp.raise_for_status()
                data = resp.json()
                if not data:
                    break
                tags.extend(data)
                page += 1
        return tags

    async def repo_exists(self, repo_name: str) -> bool:
        """Check if repository exists in the organization."""
        repo = await self.get_repository(repo_name)
        return repo is not None

    async def enable_repository(self, repo_name: str) -> bool:
        """
        Enable a repository (unarchive / make accessible).
        Used after cutover to ensure the repo is live on GitHub.
        """
        async with httpx.AsyncClient() as client:
            resp = await client.patch(
                f"{self.api_url}/repos/{self.organization}/{repo_name}",
                headers=self.headers,
                json={"archived": False, "private": False},
            )
            if resp.status_code == 200:
                logger.info(f"Repository {repo_name} enabled on GitHub")
                return True
            logger.warning(f"Could not enable {repo_name}: {resp.status_code}")
            return False
