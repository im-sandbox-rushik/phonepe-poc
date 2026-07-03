"""Migration API endpoints."""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import List, Optional

from config import load_config
from database import get_db
from models import Repository, MigrationStatus, MigrationLog
from services.factory import get_gitlab_service, get_gei_service, get_github_service

logger = logging.getLogger(__name__)
router = APIRouter()


class MigrateRequest(BaseModel):
    repositories: Optional[List[str]] = None  # If None, use config


class MigrateResponse(BaseModel):
    total: int
    successful: int
    failed: int
    details: list


@router.post("", response_model=MigrateResponse)
async def migrate_repositories(request: MigrateRequest = None, db: Session = Depends(get_db)):
    """
    POST /migrate
    Trigger initial GEI migration for configured repositories.
    """
    config = load_config()
    repo_paths = (request.repositories if request and request.repositories else config.repositories)

    if not repo_paths:
        raise HTTPException(status_code=400, detail="No repositories configured")

    github_service = get_github_service(config)
    gitlab_service = get_gitlab_service(config)
    gei_service = get_gei_service(config, github_service)

    results = {"total": len(repo_paths), "successful": 0, "failed": 0, "details": []}

    for project_path in repo_paths:
        repo_name = project_path.rsplit("/", 1)[-1]

        # Check if already migrated
        existing = db.query(Repository).filter(Repository.name == repo_name).first()
        if existing and existing.migration_status == MigrationStatus.COMPLETED:
            results["details"].append({
                "repository": repo_name,
                "status": "ALREADY_MIGRATED",
                "github_url": existing.github_url,
            })
            results["successful"] += 1
            continue

        # Get GitLab project info
        try:
            project = await gitlab_service.get_project(project_path)
            gitlab_url = project.get("web_url", f"{config.gitlab.url}/{project_path}")
        except Exception as e:
            logger.error(f"Failed to get GitLab project {project_path}: {e}")
            results["failed"] += 1
            results["details"].append({
                "repository": repo_name,
                "status": "FAILED",
                "error": f"GitLab API error: {e}",
            })
            continue

        # Run GEI migration
        logger.info(f"Migration Started: {repo_name}")
        migration_result = await gei_service.migrate_repository(project_path, repo_name)

        # Create or update DB record
        if not existing:
            existing = Repository(
                name=repo_name,
                gitlab_path=project_path,
                gitlab_url=gitlab_url,
            )
            db.add(existing)

        existing.migration_id = migration_result["migration_id"]

        if migration_result["status"] == "COMPLETED":
            existing.migration_status = MigrationStatus.COMPLETED
            existing.github_url = migration_result["github_url"]
            # Get initial HEAD SHA
            head_sha = await gitlab_service.get_head_sha(project_path)
            existing.last_head_sha = head_sha
            existing.last_sync_time = datetime.utcnow()
            results["successful"] += 1
            logger.info(f"Migration Completed: {repo_name}")
        else:
            existing.migration_status = MigrationStatus.FAILED
            existing.error_message = migration_result.get("error")
            results["failed"] += 1

        db.commit()

        # Log operation
        log = MigrationLog(
            repository_name=repo_name,
            operation="MIGRATION",
            status=migration_result["status"],
            message=migration_result.get("error") or "Success",
        )
        db.add(log)
        db.commit()

        results["details"].append({
            "repository": repo_name,
            "status": migration_result["status"],
            "migration_id": migration_result["migration_id"],
            "github_url": migration_result.get("github_url"),
        })

    return results
