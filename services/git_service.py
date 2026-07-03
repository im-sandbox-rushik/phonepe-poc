"""Git operations service for mirror synchronization.

DESIGN NOTE:
GEI does not support incremental updates for GitLab-sourced migrations.
After the initial GEI migration, repository content synchronization is
performed via `git fetch` + `git push --mirror`. This is a hybrid approach:
- GEI handles the initial full migration (code + metadata)
- Git mirror handles incremental code sync
- Metadata (issues, PRs created post-migration) is NOT synced incrementally

This limitation should be addressed in production by either:
1. Waiting for GEI incremental support
2. Using GitLab/GitHub APIs to sync metadata separately
3. Accepting metadata drift until cutover
"""

import asyncio
import logging
import tempfile
from pathlib import Path
from typing import Optional

from config import GitLabConfig, GitHubConfig

logger = logging.getLogger(__name__)


class GitService:
    """Handles git clone, fetch, and mirror push operations."""

    def __init__(self, gitlab_config: GitLabConfig, github_config: GitHubConfig):
        self.gitlab_url = gitlab_config.url.rstrip("/")
        self.gitlab_pat = gitlab_config.pat
        self.github_url = github_config.url.rstrip("/")
        self.github_pat = github_config.pat
        self.github_org = github_config.organization
        self.work_dir = Path(tempfile.gettempdir()) / "migration-poc-repos"
        self.work_dir.mkdir(parents=True, exist_ok=True)

    def _gitlab_clone_url(self, project_path: str) -> str:
        """Build authenticated GitLab clone URL."""
        # Format: https://oauth2:TOKEN@gitlab.example.com/group/repo.git
        host = self.gitlab_url.replace("https://", "").replace("http://", "")
        return f"https://oauth2:{self.gitlab_pat}@{host}/{project_path}.git"

    def _github_push_url(self, repo_name: str) -> str:
        """Build authenticated GitHub push URL."""
        host = self.github_url.replace("https://", "").replace("http://", "")
        return f"https://x-access-token:{self.github_pat}@{host}/{self.github_org}/{repo_name}.git"

    async def mirror_sync(
        self, gitlab_project_path: str, github_repo_name: str
    ) -> dict:
        """
        Perform incremental sync: fetch from GitLab, push --mirror to GitHub.

        Returns dict with status and new HEAD SHA.
        """
        repo_dir = self.work_dir / github_repo_name
        gitlab_url = self._gitlab_clone_url(gitlab_project_path)
        github_url = self._github_push_url(github_repo_name)

        try:
            if not repo_dir.exists():
                # First time: bare clone from GitLab
                logger.info(f"Cloning bare repository: {github_repo_name}")
                await self._run_git(
                    ["git", "clone", "--bare", gitlab_url, str(repo_dir)]
                )
                # Add GitHub as push remote
                await self._run_git(
                    ["git", "remote", "add", "github", github_url],
                    cwd=str(repo_dir),
                )
            else:
                # Fetch latest from GitLab
                logger.info(f"Fetching updates for: {github_repo_name}")
                await self._run_git(
                    ["git", "fetch", "--all", "--prune"],
                    cwd=str(repo_dir),
                )

            # Push mirror to GitHub
            logger.info(f"Pushing mirror to GitHub: {github_repo_name}")
            await self._run_git(
                ["git", "push", "--mirror", "github"],
                cwd=str(repo_dir),
            )

            # Get HEAD SHA
            head_sha = await self._get_head_sha(str(repo_dir))

            logger.info(f"Synchronization Completed: {github_repo_name} ({head_sha})")
            return {"status": "SUCCESS", "head_sha": head_sha}

        except Exception as e:
            logger.error(f"Mirror sync failed for {github_repo_name}: {e}")
            return {"status": "FAILED", "error": str(e)}

    async def get_local_head_sha(self, repo_name: str) -> Optional[str]:
        """Get HEAD SHA from local bare clone."""
        repo_dir = self.work_dir / repo_name
        if not repo_dir.exists():
            return None
        return await self._get_head_sha(str(repo_dir))

    async def _get_head_sha(self, repo_path: str) -> Optional[str]:
        """Get HEAD SHA from a git repository."""
        try:
            result = await self._run_git(
                ["git", "rev-parse", "HEAD"], cwd=repo_path
            )
            return result.strip()
        except Exception:
            return None

    async def _run_git(self, cmd: list, cwd: str = None) -> str:
        """Run a git command and return stdout."""
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            error = stderr.decode().strip()
            raise RuntimeError(f"Git command failed: {' '.join(cmd)} - {error}")

        return stdout.decode()
