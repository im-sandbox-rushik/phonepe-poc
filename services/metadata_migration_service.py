"""Full metadata migration orchestrator.

Coordinates migration of ALL metadata types from GitLab to GitHub:
1. Labels
2. Milestones
3. Issues (with comments)
4. Merge Requests / PRs (with comments)
5. Releases
6. Wiki
7. CI/CD (.gitlab-ci.yml → GitHub Actions)
8. Webhooks
9. Permissions (members → collaborators/teams)
"""

import logging
import time
from typing import Dict, List

from config import AppConfig
from database import SessionLocal
from models import Repository, MigrationStatus, MigrationLog
from services.mr_migration_service import MRMigrationService
from services.issues_migration_service import IssuesMigrationService
from services.permissions_migration_service import PermissionsMigrationService
from services.wiki_migration_service import WikiMigrationService
from services.releases_migration_service import ReleasesMigrationService
from services.cicd_migration_service import CICDMigrationService
from services.webhooks_migration_service import WebhooksMigrationService

logger = logging.getLogger(__name__)


class MetadataMigrationService:
    """Orchestrates full metadata migration for all data types."""

    def __init__(self, config: AppConfig):
        self.config = config
        self.mr_service = MRMigrationService(config.gitlab, config.github)
        self.issues_service = IssuesMigrationService(config.gitlab, config.github)
        self.permissions_service = PermissionsMigrationService(config.gitlab, config.github)
        self.wiki_service = WikiMigrationService(config.gitlab, config.github)
        self.releases_service = ReleasesMigrationService(config.gitlab, config.github)
        self.cicd_service = CICDMigrationService(config.gitlab, config.github)
        self.webhooks_service = WebhooksMigrationService(config.gitlab, config.github)

    async def migrate_all_metadata(self, gitlab_path: str, github_repo: str) -> Dict:
        """Run full metadata migration for a single repository."""
        logger.info(f"=== Full Metadata Migration: {gitlab_path} -> {github_repo} ===")
        start_time = time.time()

        results = {
            "repository": github_repo,
            "gitlab_path": gitlab_path,
            "labels": None,
            "milestones": None,
            "issues": None,
            "merge_requests": None,
            "releases": None,
            "wiki": None,
            "cicd": None,
            "webhooks": None,
            "permissions": None,
        }

        # 1. Labels (must be first - issues/MRs reference them)
        logger.info(f"[1/9] Migrating labels...")
        try:
            results["labels"] = await self.issues_service.migrate_labels(gitlab_path, github_repo)
        except Exception as e:
            logger.error(f"Labels migration failed: {e}")
            results["labels"] = {"status": "failed", "error": str(e)}

        # 2. Milestones (must be before issues - issues reference them)
        logger.info(f"[2/9] Migrating milestones...")
        try:
            results["milestones"] = await self.issues_service.migrate_milestones(gitlab_path, github_repo)
        except Exception as e:
            logger.error(f"Milestones migration failed: {e}")
            results["milestones"] = {"status": "failed", "error": str(e)}

        # 3. Issues (with comments)
        logger.info(f"[3/9] Migrating issues...")
        try:
            results["issues"] = await self.issues_service.migrate_issues(gitlab_path, github_repo)
        except Exception as e:
            logger.error(f"Issues migration failed: {e}")
            results["issues"] = {"status": "failed", "error": str(e)}

        # 4. Merge Requests / PRs (with comments)
        logger.info(f"[4/9] Migrating merge requests...")
        try:
            results["merge_requests"] = await self.mr_service.migrate_merge_requests(gitlab_path, github_repo)
        except Exception as e:
            logger.error(f"MR migration failed: {e}")
            results["merge_requests"] = {"status": "failed", "error": str(e)}

        # 5. Releases
        logger.info(f"[5/9] Migrating releases...")
        try:
            results["releases"] = await self.releases_service.migrate_releases(gitlab_path, github_repo)
        except Exception as e:
            logger.error(f"Releases migration failed: {e}")
            results["releases"] = {"status": "failed", "error": str(e)}

        # 6. Wiki
        logger.info(f"[6/9] Migrating wiki...")
        try:
            results["wiki"] = await self.wiki_service.migrate_wiki(gitlab_path, github_repo)
        except Exception as e:
            logger.error(f"Wiki migration failed: {e}")
            results["wiki"] = {"status": "failed", "error": str(e)}

        # 7. CI/CD conversion
        logger.info(f"[7/9] Migrating CI/CD...")
        try:
            results["cicd"] = await self.cicd_service.migrate_cicd(gitlab_path, github_repo)
        except Exception as e:
            logger.error(f"CI/CD migration failed: {e}")
            results["cicd"] = {"status": "failed", "error": str(e)}

        # 8. Webhooks
        logger.info(f"[8/9] Migrating webhooks...")
        try:
            results["webhooks"] = await self.webhooks_service.migrate_webhooks(gitlab_path, github_repo)
        except Exception as e:
            logger.error(f"Webhooks migration failed: {e}")
            results["webhooks"] = {"status": "failed", "error": str(e)}

        # 9. Permissions
        logger.info(f"[9/9] Migrating permissions...")
        try:
            results["permissions"] = await self.permissions_service.migrate_permissions(gitlab_path, github_repo)
        except Exception as e:
            logger.error(f"Permissions migration failed: {e}")
            results["permissions"] = {"status": "failed", "error": str(e)}

        elapsed = time.time() - start_time
        results["execution_time"] = f"{int(elapsed)}s"
        logger.info(f"=== Metadata migration complete for {github_repo} in {int(elapsed)}s ===")

        return results

    async def migrate_all_repos_metadata(self) -> Dict:
        """Run metadata migration for all migrated repositories."""
        logger.info("Starting full metadata migration for all repositories")
        start_time = time.time()

        db = SessionLocal()
        try:
            repos = db.query(Repository).filter(
                Repository.migration_status == MigrationStatus.COMPLETED
            ).all()

            if not repos:
                return {"error": "No migrated repositories found"}

            all_results = []
            for repo in repos:
                result = await self.migrate_all_metadata(repo.gitlab_path, repo.name)
                all_results.append(result)

                # Log to DB
                log = MigrationLog(
                    repository_name=repo.name,
                    operation="METADATA_MIGRATION",
                    status="COMPLETED",
                    message=f"Metadata migrated in {result.get('execution_time', 'N/A')}",
                )
                db.add(log)
                db.commit()

            elapsed = time.time() - start_time
            return {
                "total_repositories": len(all_results),
                "results": all_results,
                "total_execution_time": f"{int(elapsed)}s",
            }
        finally:
            db.close()
