"""Validation service - verifies migration integrity."""

import logging
from datetime import datetime
from typing import Dict, List

from sqlalchemy.orm import Session

from config import AppConfig
from database import SessionLocal
from models import Repository, MigrationStatus, ValidationResult, MigrationLog
from services.factory import get_gitlab_service, get_github_service

logger = logging.getLogger(__name__)


class ValidationService:
    """Validates that migrated repositories match source."""

    def __init__(self, config: AppConfig):
        self.config = config
        self.gitlab_service = get_gitlab_service(config)
        self.github_service = get_github_service(config)

    async def validate_all(self) -> List[Dict]:
        """Validate all migrated repositories."""
        logger.info("Validation Started")
        db = SessionLocal()
        results = []

        try:
            repos = db.query(Repository).filter(
                Repository.migration_status == MigrationStatus.COMPLETED
            ).all()

            for repo in repos:
                result = await self.validate_repository(repo.name, repo.gitlab_path, db)
                results.append(result)

            passed = sum(1 for r in results if r["overall"] == "PASS")
            logger.info(f"Validation Passed: {passed}/{len(results)} repositories")
        finally:
            db.close()

        return results

    async def validate_repository(
        self, repo_name: str, gitlab_path: str, db: Session = None
    ) -> Dict:
        """
        Validate a single repository.
        Checks: HEAD SHA, branch count, tag count.
        """
        close_db = False
        if db is None:
            db = SessionLocal()
            close_db = True

        try:
            result = {
                "repository": repo_name,
                "sha": "FAIL",
                "branches": "FAIL",
                "tags": "FAIL",
                "overall": "FAIL",
            }

            # Get GitLab data
            gitlab_sha = await self.gitlab_service.get_head_sha(gitlab_path)
            gitlab_branches = await self.gitlab_service.get_branches(gitlab_path)
            gitlab_tags = await self.gitlab_service.get_tags(gitlab_path)

            # Get GitHub data
            github_sha = await self.github_service.get_head_sha(repo_name)
            github_branches = await self.github_service.get_branches(repo_name)
            github_tags = await self.github_service.get_tags(repo_name)

            # Compare SHA
            if gitlab_sha and github_sha and gitlab_sha == github_sha:
                result["sha"] = "PASS"
            elif gitlab_sha and github_sha:
                result["sha"] = "WARN"
                logger.warning(
                    f"SHA mismatch for {repo_name}: "
                    f"GitLab={gitlab_sha}, GitHub={github_sha}"
                )

            # Compare branch count
            gl_branch_count = len(gitlab_branches)
            gh_branch_count = len(github_branches)
            if gl_branch_count == gh_branch_count:
                result["branches"] = "PASS"
            elif abs(gl_branch_count - gh_branch_count) <= 1:
                # Allow 1 branch difference (e.g., default branch naming)
                result["branches"] = "WARN"
            else:
                logger.warning(
                    f"Branch count mismatch for {repo_name}: "
                    f"GitLab={gl_branch_count}, GitHub={gh_branch_count}"
                )

            # Compare tag count
            gl_tag_count = len(gitlab_tags)
            gh_tag_count = len(github_tags)
            if gl_tag_count == gh_tag_count:
                result["tags"] = "PASS"
            elif abs(gl_tag_count - gh_tag_count) <= 1:
                result["tags"] = "WARN"
            else:
                logger.warning(
                    f"Tag count mismatch for {repo_name}: "
                    f"GitLab={gl_tag_count}, GitHub={gh_tag_count}"
                )

            # Determine overall result
            values = [result["sha"], result["branches"], result["tags"]]
            if all(v == "PASS" for v in values):
                result["overall"] = "PASS"
            elif "FAIL" in values:
                result["overall"] = "FAIL"
            else:
                result["overall"] = "WARN"

            # Update database
            repo = db.query(Repository).filter(Repository.name == repo_name).first()
            if repo:
                repo.last_validation_result = ValidationResult(result["overall"])
                repo.last_validation_time = datetime.utcnow()
                db.commit()

            # Log
            log = MigrationLog(
                repository_name=repo_name,
                operation="VALIDATION",
                status=result["overall"],
                message=str(result),
            )
            db.add(log)
            db.commit()

            return result

        finally:
            if close_db:
                db.close()

    async def validate_single(self, repo_name: str) -> Dict:
        """Validate a single repository by name."""
        db = SessionLocal()
        try:
            repo = db.query(Repository).filter(Repository.name == repo_name).first()
            if not repo:
                return {"error": f"Repository {repo_name} not found"}
            return await self.validate_repository(repo_name, repo.gitlab_path, db)
        finally:
            db.close()
