"""FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from config import load_config
from database import init_db
from scheduler import start_scheduler, shutdown_scheduler
from api.migrations import router as migrations_router
from api.sync import router as sync_router
from api.validation import router as validation_router


config = load_config()

logging.basicConfig(
    level=getattr(logging, config.logging.level),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(config.logging.file),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown."""
    logger.info("Initializing database")
    init_db()
    logger.info("Starting scheduler")
    start_scheduler(config)
    yield
    logger.info("Shutting down scheduler")
    shutdown_scheduler()


app = FastAPI(
    title="GitLab to GHES Migration POC",
    description="Near zero-downtime migration using GEI with incremental sync",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(migrations_router, prefix="/migrate", tags=["Migration"])
app.include_router(sync_router, prefix="/sync", tags=["Synchronization"])
app.include_router(validation_router, prefix="/validate", tags=["Validation"])


@app.get("/status")
def get_status():
    """Get overall migration status."""
    from sqlalchemy import func
    from database import SessionLocal
    from models import Repository, MigrationStatus, SyncStatus

    db = SessionLocal()
    try:
        total = db.query(Repository).count()
        migrated = db.query(Repository).filter(
            Repository.migration_status == MigrationStatus.COMPLETED
        ).count()
        synced = db.query(Repository).filter(
            Repository.sync_status == SyncStatus.SYNCED
        ).count()
        failed = db.query(Repository).filter(
            Repository.migration_status == MigrationStatus.FAILED
        ).count()

        return {
            "total_repositories": total,
            "migrated": migrated,
            "synced": synced,
            "failed": failed,
            "pending": total - migrated - failed,
        }
    finally:
        db.close()


@app.get("/report")
def get_report():
    """Get migration report."""
    from database import SessionLocal
    from models import Repository

    db = SessionLocal()
    try:
        repos = db.query(Repository).all()
        return {
            "repositories": len(repos),
            "synced": sum(1 for r in repos if r.sync_status.value == "SYNCED"),
            "failed": sum(1 for r in repos if r.migration_status.value == "FAILED"),
            "validation": "PASS" if all(
                r.last_validation_result and r.last_validation_result.value == "PASS"
                for r in repos if r.last_validation_result
            ) else "FAIL",
            "details": [
                {
                    "name": r.name,
                    "migration_status": r.migration_status.value,
                    "sync_status": r.sync_status.value,
                    "last_sync_time": r.last_sync_time.isoformat() if r.last_sync_time else None,
                    "last_validation": r.last_validation_result.value if r.last_validation_result else None,
                }
                for r in repos
            ],
        }
    finally:
        db.close()
