"""Demo/Mock implementations for running without real infrastructure.

When demo_mode=true in config.yaml, these mock services are used instead
of hitting real GitLab/GHES APIs. This allows full end-to-end demonstration
of the migration flow.
"""

import asyncio
import hashlib
import logging
import random
import uuid
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Simulated repository state (changes over time to demonstrate sync)
_demo_state: Dict[str, Dict] = {}


def _init_demo_state(repositories: List[str]):
    """Initialize simulated repository state."""
    global _demo_state
    for path in repositories:
        repo_name = path.rsplit("/", 1)[-1]
        sha = hashlib.sha1(f"{repo_name}-{datetime.utcnow().isoformat()}".encode()).hexdigest()
        _demo_state[path] = {
            "name": repo_name,
            "path": path,
            "head_sha": sha,
            "branches": ["main", "develop", f"feature/{repo_name}-v2"],
            "tags": ["v1.0.0", "v1.1.0"],
            "web_url": f"https://gitlab.example.com/{path}",
        }


def _simulate_change(path: str):
    """Randomly simulate a change in a repo (30% chance)."""
    if path in _demo_state and random.random() < 0.3:
        new_sha = hashlib.sha1(f"{path}-{uuid.uuid4()}".encode()).hexdigest()
        _demo_state[path]["head_sha"] = new_sha
        logger.info(f"[DEMO] Simulated change in {path}: new SHA {new_sha[:8]}")


class MockGitLabService:
    """Mock GitLab API - returns simulated data."""

    def __init__(self, repositories: List[str]):
        if not _demo_state:
            _init_demo_state(repositories)

    async def get_project(self, project_path: str) -> Dict:
        await asyncio.sleep(0.1)  # Simulate network
        if project_path not in _demo_state:
            _init_demo_state([project_path])
        state = _demo_state[project_path]
        return {
            "id": abs(hash(project_path)) % 10000,
            "name": state["name"],
            "path_with_namespace": project_path,
            "web_url": state["web_url"],
            "default_branch": "main",
        }

    async def get_head_sha(self, project_path: str, branch: str = "main") -> Optional[str]:
        await asyncio.sleep(0.05)
        _simulate_change(project_path)  # May change SHA to simulate real activity
        if project_path in _demo_state:
            return _demo_state[project_path]["head_sha"]
        return None

    async def get_branches(self, project_path: str) -> List[Dict]:
        await asyncio.sleep(0.05)
        if project_path in _demo_state:
            return [{"name": b} for b in _demo_state[project_path]["branches"]]
        return []

    async def get_tags(self, project_path: str) -> List[Dict]:
        await asyncio.sleep(0.05)
        if project_path in _demo_state:
            return [{"name": t} for t in _demo_state[project_path]["tags"]]
        return []

    async def freeze_project(self, project_path: str) -> bool:
        await asyncio.sleep(0.2)
        logger.info(f"[DEMO] Froze project: {project_path}")
        return True


class MockGitHubService:
    """Mock GitHub API - mirrors what was 'migrated'."""

    # Stores what has been "pushed" to GitHub
    _github_state: Dict[str, Dict] = {}

    def __init__(self, organization: str):
        self.organization = organization

    async def get_repository(self, repo_name: str) -> Optional[Dict]:
        await asyncio.sleep(0.05)
        return self._github_state.get(repo_name)

    async def get_head_sha(self, repo_name: str, branch: str = "main") -> Optional[str]:
        await asyncio.sleep(0.05)
        state = self._github_state.get(repo_name)
        if state:
            return state.get("head_sha")
        return None

    async def get_branches(self, repo_name: str) -> List[Dict]:
        await asyncio.sleep(0.05)
        state = self._github_state.get(repo_name)
        if state:
            return [{"name": b} for b in state.get("branches", [])]
        return []

    async def get_tags(self, repo_name: str) -> List[Dict]:
        await asyncio.sleep(0.05)
        state = self._github_state.get(repo_name)
        if state:
            return [{"name": t} for t in state.get("tags", [])]
        return []

    async def repo_exists(self, repo_name: str) -> bool:
        return repo_name in self._github_state

    async def enable_repository(self, repo_name: str) -> bool:
        await asyncio.sleep(0.1)
        logger.info(f"[DEMO] Enabled repository on GitHub: {repo_name}")
        return True

    def register_migration(self, repo_name: str, gitlab_path: str):
        """Register a repo as migrated (called by mock GEI)."""
        if gitlab_path in _demo_state:
            state = _demo_state[gitlab_path]
            self._github_state[repo_name] = {
                "name": repo_name,
                "head_sha": state["head_sha"],
                "branches": list(state["branches"]),
                "tags": list(state["tags"]),
                "html_url": f"https://ghes.example.com/{self.organization}/{repo_name}",
            }

    def update_mirror(self, repo_name: str, gitlab_path: str):
        """Update GitHub state to match GitLab (simulates mirror push)."""
        if gitlab_path in _demo_state:
            state = _demo_state[gitlab_path]
            self._github_state[repo_name] = {
                "name": repo_name,
                "head_sha": state["head_sha"],
                "branches": list(state["branches"]),
                "tags": list(state["tags"]),
                "html_url": f"https://ghes.example.com/{self.organization}/{repo_name}",
            }


class MockGEIService:
    """Mock GEI - simulates gh gei migrate-repo."""

    def __init__(self, github_service: MockGitHubService, github_org: str):
        self.github_service = github_service
        self.github_org = github_org

    async def migrate_repository(self, gitlab_project_path: str, target_repo_name: str) -> dict:
        migration_id = str(uuid.uuid4())
        logger.info(f"[DEMO] GEI Migration Started: {gitlab_project_path} -> {target_repo_name}")

        # Simulate GEI execution time
        await asyncio.sleep(0.5)

        # Register in mock GitHub
        self.github_service.register_migration(target_repo_name, gitlab_project_path)

        github_url = f"https://ghes.example.com/{self.github_org}/{target_repo_name}"
        logger.info(f"[DEMO] GEI Migration Completed: {target_repo_name}")

        return {
            "migration_id": migration_id,
            "status": "COMPLETED",
            "github_url": github_url,
            "output": f"Successfully migrated {gitlab_project_path}",
        }


class MockGitService:
    """Mock Git operations - simulates fetch + mirror push."""

    def __init__(self, github_service: MockGitHubService):
        self.github_service = github_service

    async def mirror_sync(self, gitlab_project_path: str, github_repo_name: str) -> dict:
        logger.info(f"[DEMO] git fetch from GitLab: {gitlab_project_path}")
        await asyncio.sleep(0.3)

        logger.info(f"[DEMO] git push --mirror to GitHub: {github_repo_name}")
        await asyncio.sleep(0.2)

        # Update GitHub state to match GitLab
        self.github_service.update_mirror(github_repo_name, gitlab_project_path)

        head_sha = _demo_state.get(gitlab_project_path, {}).get("head_sha")
        return {"status": "SUCCESS", "head_sha": head_sha}
