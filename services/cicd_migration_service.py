"""CI/CD pipeline conversion service.

Converts GitLab CI/CD (.gitlab-ci.yml) to GitHub Actions (.github/workflows/).

Mapping:
  GitLab stages       → GitHub Actions jobs with needs/dependencies
  GitLab jobs         → GitHub Actions steps
  GitLab variables    → GitHub Actions env / secrets
  GitLab services     → GitHub Actions services containers
  GitLab artifacts    → GitHub Actions upload-artifact
  GitLab cache        → GitHub Actions cache
  GitLab rules/only   → GitHub Actions on: conditions
  GitLab image        → GitHub Actions container
"""

import logging
import re
from typing import Dict, List, Optional

import httpx
import yaml

from config import GitLabConfig, GitHubConfig

logger = logging.getLogger(__name__)


class CICDMigrationService:
    """Converts .gitlab-ci.yml to GitHub Actions workflows."""

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

    async def migrate_cicd(self, gitlab_path: str, github_repo: str) -> Dict:
        """Convert .gitlab-ci.yml and push as GitHub Actions workflow."""
        logger.info(f"Migrating CI/CD: {gitlab_path} -> {github_repo}")

        # Get .gitlab-ci.yml content
        gitlab_ci_content = await self._get_gitlab_ci_file(gitlab_path)
        if not gitlab_ci_content:
            return {"status": "skipped", "reason": "No .gitlab-ci.yml found"}

        try:
            # Parse GitLab CI
            gitlab_ci = yaml.safe_load(gitlab_ci_content)
            if not gitlab_ci:
                return {"status": "skipped", "reason": "Empty .gitlab-ci.yml"}

            # Convert to GitHub Actions
            workflow = self._convert_to_github_actions(gitlab_ci)
            workflow_yaml = yaml.dump(workflow, default_flow_style=False, sort_keys=False)

            # Push the workflow file to the repo
            pushed = await self._push_workflow_file(github_repo, workflow_yaml, gitlab_ci_content)

            return {
                "status": "migrated" if pushed else "failed",
                "workflow_file": ".github/workflows/ci.yml",
                "original_file": ".gitlab-ci.yml",
                "jobs_converted": len(workflow.get("jobs", {})),
            }
        except Exception as e:
            logger.error(f"CI/CD conversion failed: {e}")
            return {"status": "failed", "error": str(e)}

    async def _get_gitlab_ci_file(self, gitlab_path: str) -> Optional[str]:
        """Get .gitlab-ci.yml content from GitLab."""
        encoded_path = gitlab_path.replace("/", "%2F")
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{self.gitlab_api}/projects/{encoded_path}/repository/files/.gitlab-ci.yml/raw",
                headers=self.gitlab_headers,
                params={"ref": "main"},
            )
            if resp.status_code == 200:
                return resp.text
            # Try master branch
            resp = await client.get(
                f"{self.gitlab_api}/projects/{encoded_path}/repository/files/.gitlab-ci.yml/raw",
                headers=self.gitlab_headers,
                params={"ref": "master"},
            )
            if resp.status_code == 200:
                return resp.text
        return None

    def _convert_to_github_actions(self, gitlab_ci: Dict) -> Dict:
        """Convert GitLab CI config to GitHub Actions workflow."""
        workflow = {
            "name": "CI (Migrated from GitLab CI)",
            "on": {"push": {"branches": ["main", "master"]}, "pull_request": {"branches": ["main"]}},
            "jobs": {},
        }

        # Extract global settings
        global_image = gitlab_ci.get("image")
        global_variables = gitlab_ci.get("variables", {})
        stages = gitlab_ci.get("stages", ["build", "test", "deploy"])
        global_before_script = gitlab_ci.get("before_script", [])

        # Reserved keys that are not jobs
        reserved_keys = {
            "image", "variables", "stages", "before_script", "after_script",
            "cache", "services", "include", "default", "workflow",
        }

        # Convert each job
        stage_jobs = {stage: [] for stage in stages}

        for job_name, job_config in gitlab_ci.items():
            if job_name in reserved_keys or job_name.startswith("."):
                continue
            if not isinstance(job_config, dict):
                continue

            gh_job = self._convert_job(job_name, job_config, global_image, global_variables, global_before_script)
            workflow["jobs"][job_name.replace(":", "-").replace("/", "-")] = gh_job

            # Track stage dependencies
            job_stage = job_config.get("stage", "test")
            if job_stage in stage_jobs:
                stage_jobs[job_stage].append(job_name.replace(":", "-").replace("/", "-"))

        # Add stage dependencies (needs)
        prev_stage_jobs = []
        for stage in stages:
            current_jobs = stage_jobs.get(stage, [])
            if prev_stage_jobs:
                for job_id in current_jobs:
                    if job_id in workflow["jobs"]:
                        workflow["jobs"][job_id]["needs"] = list(prev_stage_jobs)
            prev_stage_jobs = current_jobs

        return workflow

    def _convert_job(self, job_name: str, job_config: Dict, global_image: str,
                     global_variables: Dict, global_before_script: List) -> Dict:
        """Convert a single GitLab job to GitHub Actions job."""
        gh_job = {"runs-on": "ubuntu-latest"}

        # Container image
        image = job_config.get("image") or global_image
        if image:
            gh_job["container"] = {"image": image}

        # Variables → env
        env = {**global_variables, **job_config.get("variables", {})}
        if env:
            gh_job["env"] = env

        # Services
        services = job_config.get("services", [])
        if services:
            gh_job["services"] = {}
            for svc in services:
                if isinstance(svc, str):
                    svc_name = svc.split(":")[0].split("/")[-1]
                    gh_job["services"][svc_name] = {"image": svc}
                elif isinstance(svc, dict):
                    svc_name = svc.get("alias") or svc.get("name", "service").split(":")[0].split("/")[-1]
                    gh_job["services"][svc_name] = {"image": svc.get("name", "")}

        # Steps
        steps = [{"uses": "actions/checkout@v4"}]

        # Before script
        before_scripts = job_config.get("before_script", global_before_script)
        if before_scripts:
            steps.append({
                "name": "Setup",
                "run": "\n".join(before_scripts),
            })

        # Main script
        script = job_config.get("script", [])
        if script:
            steps.append({
                "name": job_name,
                "run": "\n".join(script) if isinstance(script, list) else script,
            })

        # After script
        after_script = job_config.get("after_script", [])
        if after_script:
            steps.append({
                "name": "Cleanup",
                "run": "\n".join(after_script),
                "if": "always()",
            })

        # Artifacts → upload-artifact
        artifacts = job_config.get("artifacts", {})
        if artifacts and artifacts.get("paths"):
            steps.append({
                "uses": "actions/upload-artifact@v4",
                "with": {
                    "name": f"{job_name}-artifacts",
                    "path": "\n".join(artifacts["paths"]),
                },
            })

        # Cache
        cache = job_config.get("cache", {})
        if cache and cache.get("paths"):
            cache_step = {
                "uses": "actions/cache@v4",
                "with": {
                    "path": "\n".join(cache["paths"]),
                    "key": cache.get("key", f"${{{{ runner.os }}}}-{job_name}"),
                },
            }
            steps.insert(1, cache_step)  # After checkout

        gh_job["steps"] = steps

        # Rules/only → if condition
        rules = job_config.get("rules", [])
        only = job_config.get("only", [])
        if only:
            if "merge_requests" in only:
                gh_job["if"] = "github.event_name == 'pull_request'"
            elif "main" in only or "master" in only:
                gh_job["if"] = "github.ref == 'refs/heads/main'"

        # Allow failure
        if job_config.get("allow_failure"):
            gh_job["continue-on-error"] = True

        return gh_job

    async def _push_workflow_file(self, github_repo: str, workflow_yaml: str, original_content: str) -> bool:
        """Push the GitHub Actions workflow file to the repo."""
        import base64

        async with httpx.AsyncClient(timeout=30) as client:
            # Create .github/workflows/ci.yml
            content_b64 = base64.b64encode(workflow_yaml.encode()).decode()
            resp = await client.put(
                f"{self.github_api}/repos/{self.github_org}/{github_repo}/contents/.github/workflows/ci.yml",
                headers=self.github_headers,
                json={
                    "message": "ci: add GitHub Actions workflow (migrated from .gitlab-ci.yml)",
                    "content": content_b64,
                },
            )
            if resp.status_code not in (200, 201):
                # File may already exist, try to update
                get_resp = await client.get(
                    f"{self.github_api}/repos/{self.github_org}/{github_repo}/contents/.github/workflows/ci.yml",
                    headers=self.github_headers,
                )
                if get_resp.status_code == 200:
                    sha = get_resp.json().get("sha")
                    resp = await client.put(
                        f"{self.github_api}/repos/{self.github_org}/{github_repo}/contents/.github/workflows/ci.yml",
                        headers=self.github_headers,
                        json={
                            "message": "ci: update GitHub Actions workflow (migrated from .gitlab-ci.yml)",
                            "content": content_b64,
                            "sha": sha,
                        },
                    )

            # Also store original .gitlab-ci.yml for reference
            original_b64 = base64.b64encode(original_content.encode()).decode()
            await client.put(
                f"{self.github_api}/repos/{self.github_org}/{github_repo}/contents/.gitlab-ci.yml.bak",
                headers=self.github_headers,
                json={
                    "message": "docs: archive original .gitlab-ci.yml",
                    "content": original_b64,
                },
            )

            return resp.status_code in (200, 201)
