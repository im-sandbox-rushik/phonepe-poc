"""Service factory - returns real or mock services based on config."""

from config import AppConfig


def get_gitlab_service(config: AppConfig):
    """Get GitLab service (real or mock)."""
    if config.demo_mode:
        from services.demo_services import MockGitLabService
        return MockGitLabService(config.repositories)
    else:
        from services.gitlab_service import GitLabService
        return GitLabService(config.gitlab)


def get_github_service(config: AppConfig):
    """Get GitHub service (real or mock)."""
    if config.demo_mode:
        from services.demo_services import MockGitHubService
        return MockGitHubService(config.github.organization)
    else:
        from services.github_service import GitHubService
        return GitHubService(config.github)


def get_gei_service(config: AppConfig, github_service=None):
    """Get GEI service (real or mock)."""
    if config.demo_mode:
        from services.demo_services import MockGEIService
        if github_service is None:
            github_service = get_github_service(config)
        return MockGEIService(github_service, config.github.organization)
    else:
        from services.gei_service import GEIService
        return GEIService(config.gei, config.gitlab, config.github)


def get_git_service(config: AppConfig, github_service=None):
    """Get Git service (real or mock)."""
    if config.demo_mode:
        from services.demo_services import MockGitService
        if github_service is None:
            github_service = get_github_service(config)
        return MockGitService(github_service)
    else:
        from services.git_service import GitService
        return GitService(config.gitlab, config.github)
