"""Permissions and Access migration service.

Migrates GitLab project members/permissions to GitHub team/collaborator access:
- GitLab Members → GitHub repository collaborators
- GitLab Group roles → GitHub permission levels
- Creates GitHub teams if needed

Role mapping:
  GitLab Guest (10)      → GitHub pull (read)
  GitLab Reporter (20)   → GitHub pull (read)
  GitLab Developer (30)  → GitHub push (write)
  GitLab Maintainer (40) → GitHub maintain
  GitLab Owner (50)      → GitHub admin
"""

import logging
from typing import Dict, List, Optional

import httpx

from config import GitLabConfig, GitHubConfig

logger = logging.getLogger(__name__)

# GitLab access level → GitHub permission
ROLE_MAP = {
    10: "pull",       # Guest → read
    20: "pull",       # Reporter → read
    30: "push",       # Developer → write
    40: "maintain",   # Maintainer → maintain
    50: "admin",      # Owner → admin
}


class PermissionsMigrationService:
    """Migrates GitLab project members to GitHub collaborators/teams."""

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

    async def migrate_permissions(self, gitlab_path: str, github_repo: str) -> Dict:
        """Migrate project members and their access levels."""
        logger.info(f"Migrating permissions: {gitlab_path} -> {github_repo}")
        results = {
            "total": 0,
            "migrated": 0,
            "skipped": 0,
            "failed": 0,
            "details": [],
        }

        # Get GitLab project members (includes inherited from group)
        members = await self._get_gitlab_members(gitlab_path)
        results["total"] = len(members)

        for member in members:
            username = member.get("username", "")
            access_level = member.get("access_level", 10)
            github_permission = ROLE_MAP.get(access_level, "pull")

            result = await self._add_collaborator(github_repo, username, github_permission)
            results["details"].append({
                "gitlab_user": username,
                "gitlab_role": access_level,
                "github_permission": github_permission,
                "status": result,
            })
            if result == "added":
                results["migrated"] += 1
            elif result == "skipped":
                results["skipped"] += 1
            else:
                results["failed"] += 1

        logger.info(
            f"Permissions migration: {results['migrated']} added, "
            f"{results['skipped']} skipped, {results['failed']} failed"
        )
        return results

    async def migrate_group_to_team(self, gitlab_group: str, github_repo: str) -> Dict:
        """Create a GitHub team from a GitLab group and grant repo access."""
        logger.info(f"Migrating group '{gitlab_group}' as GitHub team")

        # Get group members
        group_members = await self._get_gitlab_group_members(gitlab_group)

        # Create team
        team_name = gitlab_group.replace("/", "-")
        team = await self._create_github_team(team_name)

        results = {"team": team_name, "members_added": 0, "total": len(group_members)}

        if not team:
            return {**results, "error": "Failed to create team"}

        # Add members to team
        for member in group_members:
            added = await self._add_team_member(team["slug"], member["username"])
            if added:
                results["members_added"] += 1

        # Grant team access to repo
        await self._grant_team_repo_access(team["slug"], github_repo, "push")

        return results

    async def _get_gitlab_members(self, gitlab_path: str) -> List[Dict]:
        """Get all project members including inherited."""
        encoded = gitlab_path.replace("/", "%2F")
        members = []
        async with httpx.AsyncClient(timeout=30) as client:
            # Direct members
            resp = await client.get(
                f"{self.gitlab_api}/projects/{encoded}/members/all",
                headers=self.gitlab_headers,
                params={"per_page": 100},
            )
            if resp.status_code == 200:
                members = resp.json()
        return members

    async def _get_gitlab_group_members(self, group_path: str) -> List[Dict]:
        """Get all group members."""
        encoded = group_path.replace("/", "%2F")
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{self.gitlab_api}/groups/{encoded}/members",
                headers=self.gitlab_headers,
                params={"per_page": 100},
            )
            if resp.status_code == 200:
                return resp.json()
        return []

    async def _add_collaborator(self, github_repo: str, username: str, permission: str) -> str:
        """Add a collaborator to a GitHub repo. Returns 'added', 'skipped', or 'failed'."""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.put(
                f"{self.github_api}/repos/{self.github_org}/{github_repo}/collaborators/{username}",
                headers=self.github_headers,
                json={"permission": permission},
            )
            if resp.status_code in (201, 204):
                return "added"
            elif resp.status_code == 422:
                return "skipped"  # User not found on GitHub
            else:
                logger.warning(f"Add collaborator {username} failed: {resp.status_code}")
                return "failed"

    async def _create_github_team(self, team_name: str) -> Optional[Dict]:
        """Create a GitHub team in the organization."""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self.github_api}/orgs/{self.github_org}/teams",
                headers=self.github_headers,
                json={
                    "name": team_name,
                    "privacy": "closed",
                },
            )
            if resp.status_code == 201:
                return resp.json()
            elif resp.status_code == 422:
                # Team exists, get it
                resp2 = await client.get(
                    f"{self.github_api}/orgs/{self.github_org}/teams/{team_name}",
                    headers=self.github_headers,
                )
                if resp2.status_code == 200:
                    return resp2.json()
            return None

    async def _add_team_member(self, team_slug: str, username: str) -> bool:
        """Add a member to a GitHub team."""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.put(
                f"{self.github_api}/orgs/{self.github_org}/teams/{team_slug}/memberships/{username}",
                headers=self.github_headers,
                json={"role": "member"},
            )
            return resp.status_code in (200, 201)

    async def _grant_team_repo_access(self, team_slug: str, repo_name: str, permission: str) -> bool:
        """Grant a team access to a repository."""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.put(
                f"{self.github_api}/orgs/{self.github_org}/teams/{team_slug}/repos/{self.github_org}/{repo_name}",
                headers=self.github_headers,
                json={"permission": permission},
            )
            return resp.status_code == 204
