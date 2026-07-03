"""SQLAlchemy models for migration tracking."""

from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import Column, Integer, String, DateTime, Enum, Text
from database import Base


class MigrationStatus(str, PyEnum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class SyncStatus(str, PyEnum):
    IDLE = "IDLE"
    SYNCING = "SYNCING"
    SYNCED = "SYNCED"
    FAILED = "FAILED"


class ValidationResult(str, PyEnum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"


class Repository(Base):
    __tablename__ = "repositories"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False, unique=True)
    gitlab_path = Column(String(500), nullable=False)
    gitlab_url = Column(String(500), nullable=False)
    github_url = Column(String(500), nullable=True)
    migration_id = Column(String(255), nullable=True)
    migration_status = Column(
        Enum(MigrationStatus), default=MigrationStatus.PENDING
    )
    sync_status = Column(Enum(SyncStatus), default=SyncStatus.IDLE)
    last_sync_time = Column(DateTime, nullable=True)
    last_head_sha = Column(String(40), nullable=True)
    last_validation_result = Column(
        Enum(ValidationResult), nullable=True
    )
    last_validation_time = Column(DateTime, nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<Repository(name={self.name}, status={self.migration_status})>"


class MigrationLog(Base):
    __tablename__ = "migration_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    repository_name = Column(String(255), nullable=True)
    operation = Column(String(100), nullable=False)
    status = Column(String(50), nullable=False)
    message = Column(Text, nullable=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
