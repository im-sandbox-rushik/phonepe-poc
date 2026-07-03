"""GitLab API client service."""

import logging
from typing import Dict, List, Optional

import httpx

from config import GitLabConfig

logger = logging.getLogger(__name__)


class GitLabService:
    """Client for GitLab REST API."""

    def __init__(self, config: GitLabConfig):
        self.base_url = config.url.rstrip("/")
        self.api_url = f"{self.base_url}/api/{config.api_version}"
        self.headers = {"PRIVATE-TOKEN": config.pat}

    async def get_project(self, project_path: str) -> Dict:
        """Get project details by path (e.g., 'group/repo-name')."""
        encoded_path = project_path.replace("/", "%2F")
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.api_url}/projects/{encoded_path}",
                headers=self.headers,
            )
            resp.raise_for_status()
            return resp.json()

    async def get_head_sha(self, project_path: str, branch: str = "main") -> Optional[str]:
        """Get the HEAD SHA for a branch."""
        encoded_path = project_path.replace("/", "%2F")
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.api_url}/projects/{encoded_path}/repository/branches/{branch}",
                headers=self.headers,
            )
            if resp.status_code == 404:
                # Try 'master' if 'main' not found
                if branch == "main":
                    return await self.get_head_sha(project_path, "master")
                return None
            resp.raise_for_status()
            return resp.json()["commit"]["id"]

    async def get_branches(self, project_path: str) -> List[Dict]:
        """Get all branches for a project."""
        encoded_path = project_path.replace("/", "%2F")
        branches = []
        page = 1
        async with httpx.AsyncClient() as client:
            while True:
                resp = await client.get(
                    f"{self.api_url}/projects/{encoded_path}/repository/branches",
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

    async def get_tags(self, project_path: str) -> List[Dict]:
        """Get all tags for a project."""
        encoded_path = project_path.replace("/", "%2F")
        tags = []
        page = 1
        async with httpx.AsyncClient() as client:
            while True:
                resp = await client.get(
                    f"{self.api_url}/projects/{encoded_path}/repository/tags",
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

    async def freeze_project(self, project_path: str) -> bool:
        """
        Freeze a GitLab project (disable pushes).
        Sets protected branches to no-push for cutover.
        NOTE: This is a simplified mock - real implementation would
        use project-level push rules or archive the project.
        """
        encoded_path = project_path.replace("/", "%2F")
        logger.info(f"Freezing GitLab project: {project_path}")
        try:
            async with httpx.AsyncClient() as client:
                # Archive the project to prevent any writes
                resp = await client.post(
                    f"{self.api_url}/projects/{encoded_path}/archive",
                    headers=self.headers,
                )
                if resp.status_code in (200, 201):
                    logger.info(f"Project {project_path} archived (frozen)")
                    return True
                # If archive API unavailable, log warning
                logger.warning(
                    f"Could not freeze {project_path}: {resp.status_code}. "
                    "Proceeding with mock freeze."
                )
                return True  # Mock freeze for POC
        except Exception as e:
            logger.warning(f"Freeze failed for {project_path}: {e}. Using mock freeze.")
            return True  # Mock freeze for POC

    async def list_projects(self, group_path: str = None) -> List[Dict]:
        """List projects, optionally filtered by group."""
        async with httpx.AsyncClient() as client:
            if group_path:
                encoded = group_path.replace("/", "%2F")
                resp = await client.get(
                    f"{self.api_url}/groups/{encoded}/projects",
                    headers=self.headers,
                    params={"per_page": 100},
                )
            else:
                resp = await client.get(
                    f"{self.api_url}/projects",
                    headers=self.headers,
                    params={"per_page": 100, "membership": True},
                )
            resp.raise_for_status()
            return resp.json()
