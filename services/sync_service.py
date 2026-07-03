"""Synchronization engine - orchestrates incremental sync."""

import asyncio
import logging
from datetime import datetime
from typing import List

from sqlalchemy.orm import Session

from config import AppConfig
from database import SessionLocal
from models import Repository, MigrationStatus, SyncStatus, MigrationLog
from services.factory import get_gitlab_service, get_git_service, get_github_service

logger = logging.getLogger(__name__)


class SyncService:
    """Orchestrates incremental synchronization of migrated repositories."""

    def __init__(self, config: AppConfig):
        self.config = config
        self.github_service = get_github_service(config)
        self.gitlab_service = get_gitlab_service(config)
        self.git_service = get_git_service(config, self.github_service)
        self.retry_count = config.sync.retry_count

    async def run_incremental_sync(self) -> dict:
        """
        Run incremental sync for all migrated repositories.

        For each repo:
        1. Get latest GitLab HEAD SHA
        2. Compare with stored SHA
        3. If changed: git fetch + git push --mirror
        4. Validate SHA match
        5. Update database
        """
        logger.info("Synchronization Started")
        db = SessionLocal()
        results = {"synced": 0, "skipped": 0, "failed": 0, "details": []}

        try:
            repos = db.query(Repository).filter(
                Repository.migration_status == MigrationStatus.COMPLETED
            ).all()

            for repo in repos:
                result = await self._sync_repository(db, repo)
                results["details"].append(result)
                if result["action"] == "synced":
                    results["synced"] += 1
                elif result["action"] == "skipped":
                    results["skipped"] += 1
                else:
                    results["failed"] += 1

            logger.info(
                f"Synchronization Completed: "
                f"synced={results['synced']}, "
                f"skipped={results['skipped']}, "
                f"failed={results['failed']}"
            )
        finally:
            db.close()

        return results

    async def _sync_repository(self, db: Session, repo: Repository) -> dict:
        """Sync a single repository with retry logic."""
        repo_name = repo.name
        gitlab_path = repo.gitlab_path

        for attempt in range(1, self.retry_count + 1):
            try:
                # Get latest GitLab HEAD SHA
                gitlab_sha = await self.gitlab_service.get_head_sha(gitlab_path)
                if gitlab_sha is None:
                    logger.warning(f"Could not get HEAD SHA for {gitlab_path}")
                    return {"repository": repo_name, "action": "failed", "error": "No HEAD SHA"}

                # Compare with stored SHA
                if gitlab_sha == repo.last_head_sha:
                    logger.info(f"Repository unchanged: {repo_name}")
                    return {"repository": repo_name, "action": "skipped"}

                logger.info(f"Repository Changed: {repo_name} ({repo.last_head_sha} -> {gitlab_sha})")

                # Perform mirror sync
                repo.sync_status = SyncStatus.SYNCING
                db.commit()

                sync_result = await self.git_service.mirror_sync(gitlab_path, repo_name)

                if sync_result["status"] == "SUCCESS":
                    # Validate SHA
                    new_sha = sync_result.get("head_sha")
                    repo.last_head_sha = new_sha or gitlab_sha
                    repo.last_sync_time = datetime.utcnow()
                    repo.sync_status = SyncStatus.SYNCED
                    repo.error_message = None
                    db.commit()

                    self._log_operation(
                        db, repo_name, "SYNC", "SUCCESS",
                        f"Synced to {repo.last_head_sha}"
                    )
                    return {"repository": repo_name, "action": "synced", "sha": repo.last_head_sha}
                else:
                    raise RuntimeError(sync_result.get("error", "Unknown sync error"))

            except Exception as e:
                if attempt < self.retry_count:
                    logger.warning(
                        f"Retry Attempt {attempt}/{self.retry_count} for {repo_name}: {e}"
                    )
                    await asyncio.sleep(self.config.sync.retry_delay_seconds)
                else:
                    logger.error(f"Sync failed for {repo_name} after {self.retry_count} attempts: {e}")
                    repo.sync_status = SyncStatus.FAILED
                    repo.error_message = str(e)
                    db.commit()
                    self._log_operation(db, repo_name, "SYNC", "FAILED", str(e))
                    return {"repository": repo_name, "action": "failed", "error": str(e)}

        return {"repository": repo_name, "action": "failed", "error": "Exhausted retries"}

    async def sync_single_repository(self, repo_name: str) -> dict:
        """Sync a single repository by name."""
        db = SessionLocal()
        try:
            repo = db.query(Repository).filter(Repository.name == repo_name).first()
            if not repo:
                return {"error": f"Repository {repo_name} not found"}
            if repo.migration_status != MigrationStatus.COMPLETED:
                return {"error": f"Repository {repo_name} not yet migrated"}
            return await self._sync_repository(db, repo)
        finally:
            db.close()

    def _log_operation(self, db: Session, repo_name: str, operation: str, status: str, message: str):
        """Log an operation to the migration_logs table."""
        log = MigrationLog(
            repository_name=repo_name,
            operation=operation,
            status=status,
            message=message,
        )
        db.add(log)
        db.commit()
