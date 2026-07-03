"""Configuration loader for migration POC."""

import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import List

import yaml


@dataclass
class GitLabConfig:
    url: str
    pat: str
    api_version: str = "v4"


@dataclass
class GitHubConfig:
    url: str
    pat: str
    organization: str


@dataclass
class GEIConfig:
    binary_path: str = "/usr/local/bin/gh"


@dataclass
class SyncConfig:
    interval_hours: int = 6
    retry_count: int = 3
    retry_delay_seconds: int = 30


@dataclass
class ValidationConfig:
    schedule_cron: str = "0 2 * * *"


@dataclass
class LoggingConfig:
    level: str = "INFO"
    file: str = "migration.log"


@dataclass
class AppConfig:
    gitlab: GitLabConfig
    github: GitHubConfig
    gei: GEIConfig
    sync: SyncConfig
    validation: ValidationConfig
    repositories: List[str] = field(default_factory=list)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    demo_mode: bool = False


def load_config(config_path: str = None) -> AppConfig:
    """Load configuration from YAML file."""
    if config_path is None:
        config_path = os.environ.get(
            "MIGRATION_CONFIG_PATH",
            str(Path(__file__).parent / "config.yaml"),
        )

    with open(config_path, "r") as f:
        raw = yaml.safe_load(f)

    return AppConfig(
        gitlab=GitLabConfig(**raw.get("gitlab", {})),
        github=GitHubConfig(**raw.get("github", {})),
        gei=GEIConfig(**raw.get("gei", {})),
        sync=SyncConfig(**raw.get("sync", {})),
        validation=ValidationConfig(**raw.get("validation", {})),
        repositories=raw.get("repositories", []),
        logging=LoggingConfig(**raw.get("logging", {})),
        demo_mode=raw.get("demo_mode", False),
    )
