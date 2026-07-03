"""APScheduler configuration for periodic sync and validation."""

import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger

from config import AppConfig

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler = None


def start_scheduler(config: AppConfig):
    """Initialize and start the APScheduler."""
    global _scheduler
    _scheduler = AsyncIOScheduler()

    # Every N hours: Run Incremental Sync
    _scheduler.add_job(
        _run_sync_job,
        trigger=IntervalTrigger(hours=config.sync.interval_hours),
        id="incremental_sync",
        name="Incremental Sync",
        kwargs={"config": config},
        replace_existing=True,
    )

    # Daily at 2 AM: Run Validation
    _scheduler.add_job(
        _run_validation_job,
        trigger=CronTrigger(hour=2, minute=0),
        id="daily_validation",
        name="Daily Validation",
        kwargs={"config": config},
        replace_existing=True,
    )

    _scheduler.start()
    logger.info(
        f"Scheduler started: sync every {config.sync.interval_hours}h, "
        f"validation daily at 2 AM"
    )


def shutdown_scheduler():
    """Shut down the scheduler gracefully."""
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler shut down")


async def _run_sync_job(config: AppConfig):
    """Scheduled job: run incremental sync."""
    from services.sync_service import SyncService

    logger.info("Scheduled sync job triggered")
    sync_service = SyncService(config)
    try:
        result = await sync_service.run_incremental_sync()
        logger.info(f"Scheduled sync completed: {result}")
    except Exception as e:
        logger.error(f"Scheduled sync failed: {e}")


async def _run_validation_job(config: AppConfig):
    """Scheduled job: run validation."""
    from services.validation_service import ValidationService

    logger.info("Scheduled validation job triggered")
    validation_service = ValidationService(config)
    try:
        results = await validation_service.validate_all()
        passed = sum(1 for r in results if r.get("overall") == "PASS")
        logger.info(f"Scheduled validation completed: {passed}/{len(results)} passed")
    except Exception as e:
        logger.error(f"Scheduled validation failed: {e}")
