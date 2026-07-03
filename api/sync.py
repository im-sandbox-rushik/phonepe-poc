"""Synchronization and Cutover API endpoints."""

import logging
import time
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from config import load_config
from database import get_db
from models import Repository, MigrationStatus, MigrationLog
from services.factory import get_gitlab_service, get_github_service
from services.sync_service import SyncService
from services.validation_service import ValidationService

logger = logging.getLogger(__name__)
router = APIRouter()


class SyncRequest(BaseModel):
    repository: Optional[str] = None  # If None, sync all


@router.post("")
async def trigger_sync(request: SyncRequest = None):
    """
    POST /sync
    Trigger incremental synchronization.
    """
    config = load_config()
    sync_service = SyncService(config)

    if request and request.repository:
        result = await sync_service.sync_single_repository(request.repository)
    else:
        result = await sync_service.run_incremental_sync()

    return result


@router.post("/cutover")
async def cutover(db: Session = Depends(get_db)):
    """
    POST /sync/cutover
    Execute final cutover procedure:
    1. Freeze GitLab (archive projects)
    2. Run final sync
    3. Run validation
    4. Generate report
    5. Enable GitHub repositories
    """
    config = load_config()
    start_time = time.time()

    gitlab_service = get_gitlab_service(config)
    github_service = get_github_service(config)
    sync_service = SyncService(config)
    validation_service = ValidationService(config)

    repos = db.query(Repository).filter(
        Repository.migration_status == MigrationStatus.COMPLETED
    ).all()

    if not repos:
        raise HTTPException(status_code=400, detail="No migrated repositories found")

    logger.info("Cutover Started")
    report = {
        "repositories": len(repos),
        "steps": [],
        "validation_results": [],
        "overall": "PENDING",
    }

    # Step 1: Freeze GitLab
    logger.info("Step 1: Freezing GitLab repositories")
    freeze_results = []
    for repo in repos:
        frozen = await gitlab_service.freeze_project(repo.gitlab_path)
        freeze_results.append({"repository": repo.name, "frozen": frozen})
    report["steps"].append({"step": "freeze_gitlab", "results": freeze_results})

    # Step 2: Final Sync
    logger.info("Step 2: Running final synchronization")
    sync_result = await sync_service.run_incremental_sync()
    report["steps"].append({"step": "final_sync", "results": sync_result})

    # Step 3: Validation
    logger.info("Step 3: Running validation")
    validation_results = await validation_service.validate_all()
    report["validation_results"] = validation_results
    report["steps"].append({"step": "validation", "results": validation_results})

    # Step 4: Enable GitHub
    logger.info("Step 4: Enabling GitHub repositories")
    enable_results = []
    for repo in repos:
        enabled = await github_service.enable_repository(repo.name)
        enable_results.append({"repository": repo.name, "enabled": enabled})
    report["steps"].append({"step": "enable_github", "results": enable_results})

    # Determine overall status
    all_passed = all(
        r.get("overall") == "PASS" for r in validation_results
    )
    report["overall"] = "PASS" if all_passed else "FAIL"

    # Calculate execution time
    elapsed = time.time() - start_time
    minutes = int(elapsed // 60)
    seconds = int(elapsed % 60)
    report["execution_time"] = f"{minutes}m {seconds}s"

    # Log cutover
    log = MigrationLog(
        operation="CUTOVER",
        status=report["overall"],
        message=f"Cutover completed in {report['execution_time']}",
    )
    db.add(log)
    db.commit()

    logger.info(f"Cutover Completed: {report['overall']} in {report['execution_time']}")
    return report
