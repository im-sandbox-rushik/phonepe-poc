"""Metadata migration API endpoints."""

import logging
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from config import load_config
from database import get_db
from models import Repository, MigrationStatus
from services.metadata_migration_service import MetadataMigrationService

logger = logging.getLogger(__name__)
router = APIRouter()


class MetadataRequest(BaseModel):
    repository: Optional[str] = None  # If None, migrate all
    # Optionally specify which metadata types to migrate
    labels: bool = True
    milestones: bool = True
    issues: bool = True
    merge_requests: bool = True
    releases: bool = True
    wiki: bool = True
    cicd: bool = True
    webhooks: bool = True
    permissions: bool = True


@router.post("")
async def migrate_metadata(request: MetadataRequest = None, db: Session = Depends(get_db)):
    """
    POST /metadata
    Migrate all metadata (MRs, issues, labels, wiki, CI/CD, webhooks, permissions)
    for migrated repositories.
    """
    config = load_config()
    metadata_service = MetadataMigrationService(config)

    if request and request.repository:
        # Single repo
        repo = db.query(Repository).filter(Repository.name == request.repository).first()
        if not repo:
            return {"error": f"Repository '{request.repository}' not found"}
        if repo.migration_status != MigrationStatus.COMPLETED:
            return {"error": f"Repository '{request.repository}' not yet migrated"}

        result = await metadata_service.migrate_all_metadata(repo.gitlab_path, repo.name)
        return result
    else:
        # All repos
        result = await metadata_service.migrate_all_repos_metadata()
        return result


@router.post("/labels")
async def migrate_labels_only(request: MetadataRequest = None, db: Session = Depends(get_db)):
    """Migrate only labels."""
    config = load_config()
    from services.issues_migration_service import IssuesMigrationService
    service = IssuesMigrationService(config.gitlab, config.github)

    repos = _get_repos(request, db)
    results = []
    for repo in repos:
        r = await service.migrate_labels(repo.gitlab_path, repo.name)
        results.append({"repository": repo.name, **r})
    return {"results": results}


@router.post("/issues")
async def migrate_issues_only(request: MetadataRequest = None, db: Session = Depends(get_db)):
    """Migrate only issues (with labels and milestones)."""
    config = load_config()
    from services.issues_migration_service import IssuesMigrationService
    service = IssuesMigrationService(config.gitlab, config.github)

    repos = _get_repos(request, db)
    results = []
    for repo in repos:
        await service.migrate_labels(repo.gitlab_path, repo.name)
        await service.migrate_milestones(repo.gitlab_path, repo.name)
        r = await service.migrate_issues(repo.gitlab_path, repo.name)
        results.append({"repository": repo.name, **r})
    return {"results": results}


@router.post("/merge-requests")
async def migrate_mrs_only(request: MetadataRequest = None, db: Session = Depends(get_db)):
    """Migrate only merge requests."""
    config = load_config()
    from services.mr_migration_service import MRMigrationService
    service = MRMigrationService(config.gitlab, config.github)

    repos = _get_repos(request, db)
    results = []
    for repo in repos:
        r = await service.migrate_merge_requests(repo.gitlab_path, repo.name)
        results.append({"repository": repo.name, **r})
    return {"results": results}


@router.post("/releases")
async def migrate_releases_only(request: MetadataRequest = None, db: Session = Depends(get_db)):
    """Migrate only releases."""
    config = load_config()
    from services.releases_migration_service import ReleasesMigrationService
    service = ReleasesMigrationService(config.gitlab, config.github)

    repos = _get_repos(request, db)
    results = []
    for repo in repos:
        r = await service.migrate_releases(repo.gitlab_path, repo.name)
        results.append({"repository": repo.name, **r})
    return {"results": results}


@router.post("/wiki")
async def migrate_wiki_only(request: MetadataRequest = None, db: Session = Depends(get_db)):
    """Migrate only wikis."""
    config = load_config()
    from services.wiki_migration_service import WikiMigrationService
    service = WikiMigrationService(config.gitlab, config.github)

    repos = _get_repos(request, db)
    results = []
    for repo in repos:
        r = await service.migrate_wiki(repo.gitlab_path, repo.name)
        results.append({"repository": repo.name, **r})
    return {"results": results}


@router.post("/cicd")
async def migrate_cicd_only(request: MetadataRequest = None, db: Session = Depends(get_db)):
    """Migrate only CI/CD pipelines."""
    config = load_config()
    from services.cicd_migration_service import CICDMigrationService
    service = CICDMigrationService(config.gitlab, config.github)

    repos = _get_repos(request, db)
    results = []
    for repo in repos:
        r = await service.migrate_cicd(repo.gitlab_path, repo.name)
        results.append({"repository": repo.name, **r})
    return {"results": results}


@router.post("/webhooks")
async def migrate_webhooks_only(request: MetadataRequest = None, db: Session = Depends(get_db)):
    """Migrate only webhooks."""
    config = load_config()
    from services.webhooks_migration_service import WebhooksMigrationService
    service = WebhooksMigrationService(config.gitlab, config.github)

    repos = _get_repos(request, db)
    results = []
    for repo in repos:
        r = await service.migrate_webhooks(repo.gitlab_path, repo.name)
        results.append({"repository": repo.name, **r})
    return {"results": results}


@router.post("/permissions")
async def migrate_permissions_only(request: MetadataRequest = None, db: Session = Depends(get_db)):
    """Migrate only permissions."""
    config = load_config()
    from services.permissions_migration_service import PermissionsMigrationService
    service = PermissionsMigrationService(config.gitlab, config.github)

    repos = _get_repos(request, db)
    results = []
    for repo in repos:
        r = await service.migrate_permissions(repo.gitlab_path, repo.name)
        results.append({"repository": repo.name, **r})
    return {"results": results}


def _get_repos(request, db):
    """Helper to get repos based on request."""
    if request and request.repository:
        repos = db.query(Repository).filter(
            Repository.name == request.repository,
            Repository.migration_status == MigrationStatus.COMPLETED,
        ).all()
    else:
        repos = db.query(Repository).filter(
            Repository.migration_status == MigrationStatus.COMPLETED
        ).all()
    return repos
