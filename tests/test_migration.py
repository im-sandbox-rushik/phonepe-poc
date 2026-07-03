"""Unit tests for migration POC."""

import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime

from config import load_config, AppConfig, GitLabConfig, GitHubConfig, GEIConfig, SyncConfig, ValidationConfig
from database import Base, engine, SessionLocal, init_db
from models import Repository, MigrationStatus, SyncStatus, ValidationResult


@pytest.fixture
def test_config():
    """Create a test configuration."""
    return AppConfig(
        gitlab=GitLabConfig(url="https://gitlab.test.com", pat="test-pat"),
        github=GitHubConfig(url="https://ghes.test.com", pat="test-pat", organization="test-org"),
        gei=GEIConfig(),
        sync=SyncConfig(interval_hours=6, retry_count=3, retry_delay_seconds=1),
        validation=ValidationConfig(),
        repositories=["group/test-repo"],
    )


@pytest.fixture
def db_session():
    """Create a clean test database session."""
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    yield session
    session.close()
    Base.metadata.drop_all(bind=engine)


class TestModels:
    """Test database models."""

    def test_create_repository(self, db_session):
        repo = Repository(
            name="test-repo",
            gitlab_path="group/test-repo",
            gitlab_url="https://gitlab.test.com/group/test-repo",
        )
        db_session.add(repo)
        db_session.commit()

        fetched = db_session.query(Repository).filter_by(name="test-repo").first()
        assert fetched is not None
        assert fetched.migration_status == MigrationStatus.PENDING
        assert fetched.sync_status == SyncStatus.IDLE

    def test_update_migration_status(self, db_session):
        repo = Repository(
            name="test-repo",
            gitlab_path="group/test-repo",
            gitlab_url="https://gitlab.test.com/group/test-repo",
        )
        db_session.add(repo)
        db_session.commit()

        repo.migration_status = MigrationStatus.COMPLETED
        repo.github_url = "https://ghes.test.com/test-org/test-repo"
        repo.migration_id = "test-migration-id"
        db_session.commit()

        fetched = db_session.query(Repository).filter_by(name="test-repo").first()
        assert fetched.migration_status == MigrationStatus.COMPLETED
        assert fetched.github_url == "https://ghes.test.com/test-org/test-repo"


class TestGEIService:
    """Test GEI service."""

    @pytest.mark.asyncio
    async def test_gitlab_namespace_extraction(self):
        from services.gei_service import GEIService
        assert GEIService.gitlab_namespace("group/subgroup/repo") == "group/subgroup"
        assert GEIService.gitlab_namespace("group/repo") == "group"
        assert GEIService.gitlab_namespace("repo") == ""

    @pytest.mark.asyncio
    async def test_gitlab_repo_name_extraction(self):
        from services.gei_service import GEIService
        assert GEIService.gitlab_repo_name("group/subgroup/repo") == "repo"
        assert GEIService.gitlab_repo_name("group/repo") == "repo"
        assert GEIService.gitlab_repo_name("repo") == "repo"


class TestSyncService:
    """Test sync service."""

    @pytest.mark.asyncio
    async def test_sync_skips_unchanged_repo(self, test_config, db_session):
        from services.sync_service import SyncService

        # Create a migrated repo with a known SHA
        repo = Repository(
            name="test-repo",
            gitlab_path="group/test-repo",
            gitlab_url="https://gitlab.test.com/group/test-repo",
            migration_status=MigrationStatus.COMPLETED,
            last_head_sha="abc123",
        )
        db_session.add(repo)
        db_session.commit()

        sync_service = SyncService(test_config)

        # Mock GitLab to return same SHA
        with patch.object(
            sync_service.gitlab_service, "get_head_sha", new_callable=AsyncMock
        ) as mock_sha:
            mock_sha.return_value = "abc123"
            result = await sync_service._sync_repository(db_session, repo)

        assert result["action"] == "skipped"

    @pytest.mark.asyncio
    async def test_sync_detects_changed_repo(self, test_config, db_session):
        from services.sync_service import SyncService

        repo = Repository(
            name="test-repo",
            gitlab_path="group/test-repo",
            gitlab_url="https://gitlab.test.com/group/test-repo",
            migration_status=MigrationStatus.COMPLETED,
            last_head_sha="abc123",
        )
        db_session.add(repo)
        db_session.commit()

        sync_service = SyncService(test_config)

        with patch.object(
            sync_service.gitlab_service, "get_head_sha", new_callable=AsyncMock
        ) as mock_sha, patch.object(
            sync_service.git_service, "mirror_sync", new_callable=AsyncMock
        ) as mock_mirror:
            mock_sha.return_value = "def456"
            mock_mirror.return_value = {"status": "SUCCESS", "head_sha": "def456"}

            result = await sync_service._sync_repository(db_session, repo)

        assert result["action"] == "synced"
        assert result["sha"] == "def456"


class TestValidationService:
    """Test validation service."""

    @pytest.mark.asyncio
    async def test_validation_pass(self, test_config, db_session):
        from services.validation_service import ValidationService

        repo = Repository(
            name="test-repo",
            gitlab_path="group/test-repo",
            gitlab_url="https://gitlab.test.com/group/test-repo",
            migration_status=MigrationStatus.COMPLETED,
        )
        db_session.add(repo)
        db_session.commit()

        validation_service = ValidationService(test_config)

        with patch.object(
            validation_service.gitlab_service, "get_head_sha", new_callable=AsyncMock
        ) as mock_gl_sha, patch.object(
            validation_service.github_service, "get_head_sha", new_callable=AsyncMock
        ) as mock_gh_sha, patch.object(
            validation_service.gitlab_service, "get_branches", new_callable=AsyncMock
        ) as mock_gl_branches, patch.object(
            validation_service.github_service, "get_branches", new_callable=AsyncMock
        ) as mock_gh_branches, patch.object(
            validation_service.gitlab_service, "get_tags", new_callable=AsyncMock
        ) as mock_gl_tags, patch.object(
            validation_service.github_service, "get_tags", new_callable=AsyncMock
        ) as mock_gh_tags:
            mock_gl_sha.return_value = "abc123"
            mock_gh_sha.return_value = "abc123"
            mock_gl_branches.return_value = [{"name": "main"}, {"name": "dev"}]
            mock_gh_branches.return_value = [{"name": "main"}, {"name": "dev"}]
            mock_gl_tags.return_value = [{"name": "v1.0"}]
            mock_gh_tags.return_value = [{"name": "v1.0"}]

            result = await validation_service.validate_repository(
                "test-repo", "group/test-repo", db_session
            )

        assert result["overall"] == "PASS"
        assert result["sha"] == "PASS"
        assert result["branches"] == "PASS"
        assert result["tags"] == "PASS"

    @pytest.mark.asyncio
    async def test_validation_fail_sha_mismatch(self, test_config, db_session):
        from services.validation_service import ValidationService

        repo = Repository(
            name="test-repo",
            gitlab_path="group/test-repo",
            gitlab_url="https://gitlab.test.com/group/test-repo",
            migration_status=MigrationStatus.COMPLETED,
        )
        db_session.add(repo)
        db_session.commit()

        validation_service = ValidationService(test_config)

        with patch.object(
            validation_service.gitlab_service, "get_head_sha", new_callable=AsyncMock
        ) as mock_gl_sha, patch.object(
            validation_service.github_service, "get_head_sha", new_callable=AsyncMock
        ) as mock_gh_sha, patch.object(
            validation_service.gitlab_service, "get_branches", new_callable=AsyncMock
        ) as mock_gl_branches, patch.object(
            validation_service.github_service, "get_branches", new_callable=AsyncMock
        ) as mock_gh_branches, patch.object(
            validation_service.gitlab_service, "get_tags", new_callable=AsyncMock
        ) as mock_gl_tags, patch.object(
            validation_service.github_service, "get_tags", new_callable=AsyncMock
        ) as mock_gh_tags:
            mock_gl_sha.return_value = "abc123"
            mock_gh_sha.return_value = "xyz789"  # Mismatch
            mock_gl_branches.return_value = [{"name": "main"}]
            mock_gh_branches.return_value = [{"name": "main"}]
            mock_gl_tags.return_value = []
            mock_gh_tags.return_value = []

            result = await validation_service.validate_repository(
                "test-repo", "group/test-repo", db_session
            )

        assert result["sha"] == "WARN"


class TestConfig:
    """Test configuration loading."""

    def test_load_config(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
gitlab:
  url: "https://gitlab.example.com"
  pat: "test"
github:
  url: "https://ghes.example.com"
  pat: "test"
  organization: "org"
gei:
  binary_path: "/usr/bin/gh"
sync:
  interval_hours: 6
  retry_count: 3
  retry_delay_seconds: 30
validation:
  schedule_cron: "0 2 * * *"
repositories:
  - "group/repo1"
""")
        config = load_config(str(config_file))
        assert config.gitlab.url == "https://gitlab.example.com"
        assert config.github.organization == "org"
        assert len(config.repositories) == 1
