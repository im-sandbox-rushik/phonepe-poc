"""GitHub Enterprise Importer (GEI) service.

DESIGN NOTE (IMPORTANT):
- GEI (gh gei) only supports GitHub-to-GitHub migrations.
- It does NOT support GitLab as a source.
- For GitLab → GitHub migration, the correct approach is:
  1. Create target repo on GitHub via API
  2. git clone --bare from GitLab
  3. git push --mirror to GitHub
- This handles all git content (commits, branches, tags).
- GitLab-specific metadata (MRs, issues) requires separate API migration.
- This POC focuses on repository content migration with near-zero downtime.
"""

import asyncio
import logging
import uuid
import tempfile
from pathlib import Path
from typing import Optional

import httpx

from config import GEIConfig, GitLabConfig, GitHubConfig

logger = logging.getLogger(__name__)


class GEIService:
    """Manages migrations from GitLab to GitHub via git mirror."""

    def __init__(
        self,
        gei_config: GEIConfig,
        gitlab_config: GitLabConfig,
        github_config: GitHubConfig,
    ):
        self.gh_binary = gei_config.binary_path
        self.gitlab_url = gitlab_config.url.rstrip("/")
        self.gitlab_pat = gitlab_config.pat
        self.github_url = github_config.url.rstrip("/")
        self.github_pat = github_config.pat
        self.github_org = github_config.organization
        self.work_dir = Path(tempfile.gettempdir()) / "migration-poc-repos"
        self.work_dir.mkdir(parents=True, exist_ok=True)

        # Determine GitHub API URL
        host = self.github_url.replace("https://", "").replace("http://", "")
        if host == "github.com":
            self.github_api_url = "https://api.github.com"
        else:
            self.github_api_url = f"{self.github_url}/api/v3"

    async def migrate_repository(
        self, gitlab_project_path: str, target_repo_name: str
    ) -> dict:
        """
        Migrate a repository from GitLab to GitHub:
        1. Create target repo on GitHub (if not exists)
        2. git clone --bare from GitLab
        3. git push --mirror to GitHub
        """
        migration_id = str(uuid.uuid4())
        logger.info(
            f"Migration Started: {gitlab_project_path} -> "
            f"{self.github_org}/{target_repo_name} (ID: {migration_id})"
        )

        try:
            # Step 1: Create repo on GitHub
            created = await self._create_github_repo(target_repo_name)
            if not created:
                logger.info(f"Repo {target_repo_name} already exists on GitHub, will push content")

            # Step 2: Clone bare from GitLab
            repo_dir = self.work_dir / target_repo_name
            gitlab_clone_url = self._gitlab_clone_url(gitlab_project_path)
            github_push_url = self._github_push_url(target_repo_name)

            # Remove old clone if exists
            if repo_dir.exists():
                import shutil
                shutil.rmtree(repo_dir)

            logger.info(f"Cloning from GitLab: {gitlab_project_path}")
            await self._run_cmd(
                ["git", "clone", "--bare", gitlab_clone_url, str(repo_dir)]
            )

            # Step 3: Push mirror to GitHub
            logger.info(f"Pushing mirror to GitHub: {target_repo_name}")
            await self._run_cmd(
                ["git", "remote", "add", "github", github_push_url],
                cwd=str(repo_dir),
            )
            await self._run_cmd(
                ["git", "push", "--mirror", "github"],
                cwd=str(repo_dir),
            )

            github_url = f"{self.github_url}/{self.github_org}/{target_repo_name}"
            logger.info(f"Migration Completed: {target_repo_name}")
            return {
                "migration_id": migration_id,
                "status": "COMPLETED",
                "github_url": github_url,
                "output": f"Successfully migrated {gitlab_project_path} to {github_url}",
            }

        except Exception as e:
            logger.error(f"Migration Failed: {target_repo_name} - {e}")
            return {
                "migration_id": migration_id,
                "status": "FAILED",
                "github_url": None,
                "error": str(e),
            }

    async def _create_github_repo(self, repo_name: str) -> bool:
        """Create a repository in the GitHub organization. Returns True if created."""
        headers = {
            "Authorization": f"token {self.github_pat}",
            "Accept": "application/vnd.github.v3+json",
        }
        body = {
            "name": repo_name,
            "private": True,
            "auto_init": False,
        }
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.github_api_url}/orgs/{self.github_org}/repos",
                headers=headers,
                json=body,
            )
            if resp.status_code == 201:
                logger.info(f"Created GitHub repo: {self.github_org}/{repo_name}")
                return True
            elif resp.status_code == 422:
                # Already exists
                return False
            else:
                logger.warning(f"GitHub repo creation response: {resp.status_code} {resp.text}")
                return False

    def _gitlab_clone_url(self, project_path: str) -> str:
        """Build authenticated GitLab clone URL."""
        host = self.gitlab_url.replace("https://", "").replace("http://", "")
        return f"https://oauth2:{self.gitlab_pat}@{host}/{project_path}.git"

    def _github_push_url(self, repo_name: str) -> str:
        """Build authenticated GitHub push URL."""
        host = self.github_url.replace("https://", "").replace("http://", "")
        return f"https://x-access-token:{self.github_pat}@{host}/{self.github_org}/{repo_name}.git"

    async def _run_cmd(self, cmd: list, cwd: str = None) -> str:
        """Run a shell command."""
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            error = stderr.decode().strip() or stdout.decode().strip()
            raise RuntimeError(f"Command failed: {' '.join(cmd[:3])}... - {error}")
        return stdout.decode()

    async def check_migration_status(self, migration_id: str) -> Optional[str]:
        """Check migration status (synchronous in this implementation)."""
        return "COMPLETED"

    @staticmethod
    def gitlab_namespace(project_path: str) -> str:
        """Extract namespace from GitLab project path."""
        parts = project_path.rsplit("/", 1)
        return parts[0] if len(parts) > 1 else ""

    @staticmethod
    def gitlab_repo_name(project_path: str) -> str:
        """Extract repo name from GitLab project path."""
        return project_path.rsplit("/", 1)[-1]
        parts = project_path.rsplit("/", 1)
        return parts[0] if len(parts) > 1 else ""

    @staticmethod
    def gitlab_repo_name(project_path: str) -> str:
        """Extract repo name from GitLab project path."""
        return project_path.rsplit("/", 1)[-1]
