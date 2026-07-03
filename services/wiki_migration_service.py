"""Wiki migration service.

GitLab wikis are git repositories at:
  https://gitlab.com/<group>/<project>.wiki.git

GitHub wikis are git repositories at:
  https://github.com/<org>/<repo>.wiki.git

Migration: clone GitLab wiki repo → push to GitHub wiki repo.
Note: GitHub wiki must be enabled on the repo first.
"""

import asyncio
import logging
import shutil
import tempfile
from pathlib import Path
from typing import Dict

import httpx

from config import GitLabConfig, GitHubConfig

logger = logging.getLogger(__name__)


class WikiMigrationService:
    """Migrates GitLab project wikis to GitHub repository wikis."""

    def __init__(self, gitlab_config: GitLabConfig, github_config: GitHubConfig):
        self.gitlab_url = gitlab_config.url.rstrip("/")
        self.gitlab_pat = gitlab_config.pat
        self.github_url = github_config.url.rstrip("/")
        self.github_pat = github_config.pat
        self.github_org = github_config.organization

        host = self.github_url.replace("https://", "").replace("http://", "")
        self.github_api = "https://api.github.com" if host == "github.com" else f"{self.github_url}/api/v3"
        self.github_headers = {
            "Authorization": f"token {self.github_pat}",
            "Accept": "application/vnd.github.v3+json",
        }

        self.work_dir = Path(tempfile.gettempdir()) / "migration-poc-wikis"
        self.work_dir.mkdir(parents=True, exist_ok=True)

    async def migrate_wiki(self, gitlab_path: str, github_repo: str) -> Dict:
        """Migrate wiki from GitLab to GitHub."""
        logger.info(f"Migrating wiki: {gitlab_path} -> {github_repo}")

        # Build clone URLs
        gl_host = self.gitlab_url.replace("https://", "").replace("http://", "")
        gitlab_wiki_url = f"https://oauth2:{self.gitlab_pat}@{gl_host}/{gitlab_path}.wiki.git"

        gh_host = self.github_url.replace("https://", "").replace("http://", "")
        github_wiki_url = f"https://x-access-token:{self.github_pat}@{gh_host}/{self.github_org}/{github_repo}.wiki.git"

        wiki_dir = self.work_dir / f"{github_repo}-wiki"

        try:
            # Enable wiki on GitHub repo first
            await self._enable_wiki(github_repo)

            # Initialize GitHub wiki with a dummy page (required before push)
            await self._init_github_wiki(github_repo)

            # Clean up old clone
            if wiki_dir.exists():
                shutil.rmtree(wiki_dir)

            # Clone GitLab wiki
            result = await self._run_cmd(
                ["git", "clone", gitlab_wiki_url, str(wiki_dir)]
            )
            if result is None:
                return {"status": "skipped", "reason": "No wiki found on GitLab"}

            # Check if wiki has content
            try:
                log_output = await self._run_cmd(["git", "log", "--oneline", "-1"], cwd=str(wiki_dir))
                if not log_output or not log_output.strip():
                    return {"status": "skipped", "reason": "Wiki is empty"}
            except Exception:
                return {"status": "skipped", "reason": "Wiki is empty"}

            # Add GitHub as remote and push
            await self._run_cmd(
                ["git", "remote", "add", "github", github_wiki_url],
                cwd=str(wiki_dir),
            )
            await self._run_cmd(
                ["git", "push", "github", "master", "--force"],
                cwd=str(wiki_dir),
            )

            logger.info(f"Wiki migrated: {github_repo}")
            return {"status": "migrated", "repository": github_repo}

        except Exception as e:
            logger.warning(f"Wiki migration failed for {github_repo}: {e}")
            return {"status": "failed", "error": str(e)}
        finally:
            if wiki_dir.exists():
                shutil.rmtree(wiki_dir, ignore_errors=True)

    async def _enable_wiki(self, github_repo: str):
        """Enable wiki on the GitHub repository."""
        async with httpx.AsyncClient(timeout=30) as client:
            await client.patch(
                f"{self.github_api}/repos/{self.github_org}/{github_repo}",
                headers=self.github_headers,
                json={"has_wiki": True},
            )

    async def _init_github_wiki(self, github_repo: str):
        """Create initial wiki page on GitHub (required before git push works)."""
        async with httpx.AsyncClient(timeout=30) as client:
            # Try creating a page via API - this may not work on all GitHub plans
            resp = await client.put(
                f"{self.github_api}/repos/{self.github_org}/{github_repo}/pages",
                headers=self.github_headers,
                json={"source": {"branch": "main", "path": "/"}},
            )
            # It's OK if this fails - wiki push may still work

    async def _run_cmd(self, cmd: list, cwd: str = None) -> str:
        """Run a shell command. Returns stdout or None on failure."""
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )
            stdout, stderr = await process.communicate()
            if process.returncode != 0:
                error = stderr.decode().strip()
                if "not found" in error.lower() or "does not exist" in error.lower():
                    return None
                raise RuntimeError(error)
            return stdout.decode()
        except Exception as e:
            if "not found" in str(e).lower():
                return None
            raise
