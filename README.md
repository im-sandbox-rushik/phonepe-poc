# Near Zero-Downtime GitLab → GitHub (GHEC) Migration POC

**Working, tested solution** for near zero-downtime migration from GitLab to GitHub Enterprise Cloud (GHEC) with incremental synchronization and automated validation.

## Live Test Results (July 3, 2026)

Successfully migrated **7 repositories** from GitLab.com to GitHub.com:

| # | Repository | GitLab (Source) | GitHub (Target) | Status |
|---|-----------|----------------|-----------------|--------|
| 1 | unique-nuget-package | https://gitlab.com/ranjiths-infomagnus-group/unique-nuget-package | https://github.com/im-sandbox-rushik/unique-nuget-package | ✅ PASS |
| 2 | shared-lib | https://gitlab.com/ranjiths-infomagnus-group/shared-lib | https://github.com/im-sandbox-rushik/shared-lib | ✅ PASS |
| 3 | dummy_aws_project | https://gitlab.com/ranjiths-infomagnus-group/dummy_aws_project | https://github.com/im-sandbox-rushik/dummy_aws_project | ✅ PASS |
| 4 | gitlab-game-demo | https://gitlab.com/ranjiths-infomagnus-group/gitlab-game-demo | https://github.com/im-sandbox-rushik/gitlab-game-demo | ✅ PASS |
| 5 | ranjith_demo2 | https://gitlab.com/ranjiths-infomagnus-group/ranjith_demo2 | https://github.com/im-sandbox-rushik/ranjith_demo2 | ✅ PASS |
| 6 | ranjith_demo | https://gitlab.com/ranjiths-infomagnus-group/ranjith_demo | https://github.com/im-sandbox-rushik/ranjith_demo | ✅ PASS |
| 7 | ranjiths-infomagnus-project | https://gitlab.com/ranjiths-infomagnus-group/ranjiths-infomagnus-project | https://github.com/im-sandbox-rushik/ranjiths-infomagnus-project | ✅ PASS |

**Validation Results:**
- HEAD SHA: **PASS** (all 7 repos)
- Branch count: **PASS** (all 7 repos)
- Tag count: **PASS** (all 7 repos)
- Overall: **PASS**

---

## Architecture

```
┌─────────────────┐                         ┌──────────────────┐
│   GitLab.com    │                         │  GitHub.com      │
│   (Source)      │                         │  (GHEC Target)   │
│                 │                         │                  │
│ ranjiths-       │    git clone --bare     │ im-sandbox-      │
│ infomagnus-     │ ◄──────────────────┐    │ rushik/           │
│ group/          │                    │    │                  │
└─────────────────┘                    │    └──────────────────┘
                                       │           ▲
                                 ┌─────┴───────┐   │
                                 │  Migration  │   │
                                 │   Engine    │───┘
                                 │  (FastAPI)  │  git push --mirror
                                 │             │
                                 │ • SQLite DB │
                                 │ • Scheduler │
                                 │ • REST APIs │
                                 └─────────────┘
```

### Migration Strategy

| Phase | Method | What it does |
|-------|--------|-------------|
| Initial Migration | `git clone --bare` + `git push --mirror` | Full repo content (commits, branches, tags) |
| Incremental Sync | `git fetch --all` + `git push --mirror` | Delta sync every 6 hours |
| Validation | GitLab API + GitHub API comparison | Verifies SHA, branches, tags match |
| Cutover | Freeze GitLab → Final Sync → Validate → Enable GitHub | Near-zero downtime |

### Important Design Note: Why Not GEI?

GEI (`gh gei migrate-repo`) **does not support GitLab as a source** — it only supports GitHub-to-GitHub migrations. Therefore this POC uses:

1. **GitHub API** to create target repositories
2. **`git clone --bare`** to get full repo content from GitLab
3. **`git push --mirror`** to push all refs to GitHub

This approach migrates all git content (commits, branches, tags, refs). GitLab-specific metadata (Merge Requests, Issues, CI/CD configs) requires separate API-based migration which is outside this POC scope.

---

## Quick Start

### Prerequisites

- Python 3.12+
- Git
- GitLab PAT (with `read_api`, `read_repository` scopes)
- GitHub PAT (with `repo`, `admin:org` scopes)

### Install & Run

```bash
cd migration-poc
pip install -r requirements.txt
# Edit config.yaml with your credentials
uvicorn app:app --host 0.0.0.0 --port 8000
```

### Configuration

Edit `config.yaml`:

```yaml
gitlab:
  url: "https://gitlab.com"
  pat: "glpat-your-token"
  api_version: "v4"

github:
  url: "https://github.com"
  pat: "ghp_your-token"
  organization: "your-org"

sync:
  interval_hours: 6
  retry_count: 3
  retry_delay_seconds: 2

demo_mode: false  # Set true to run without real infrastructure

repositories:
  - "group/repo1"
  - "group/repo2"
```

### Docker

```bash
docker-compose up --build
```

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/migrate` | Initial migration (clone from GitLab, push to GitHub) |
| `POST` | `/sync` | Incremental sync (detect changes, mirror push) |
| `POST` | `/validate` | Validate SHA, branches, tags match |
| `POST` | `/sync/cutover` | Full cutover: freeze → sync → validate → enable |
| `GET` | `/status` | Migration status overview |
| `GET` | `/report` | Detailed migration report |

---

## End-to-End Execution (Tested)

### Step 1: Migrate

```bash
curl -X POST http://localhost:8000/migrate
```

**Response:**
```json
{
  "total": 7,
  "successful": 7,
  "failed": 0,
  "details": [
    {"repository": "shared-lib", "status": "COMPLETED", "github_url": "https://github.com/im-sandbox-rushik/shared-lib"},
    ...
  ]
}
```

### Step 2: Incremental Sync

```bash
curl -X POST http://localhost:8000/sync
```

**Response:**
```json
{
  "synced": 0,
  "skipped": 7,
  "failed": 0,
  "details": [
    {"repository": "shared-lib", "action": "skipped"},
    ...
  ]
}
```

When a repo has new commits in GitLab, sync detects the SHA change and mirrors it:
```json
{"repository": "payments-api", "action": "synced", "sha": "99d365c4..."}
```

### Step 3: Validate

```bash
curl -X POST http://localhost:8000/validate
```

**Response:**
```json
{
  "results": [
    {"repository": "shared-lib", "sha": "PASS", "branches": "PASS", "tags": "PASS", "overall": "PASS"},
    {"repository": "unique-nuget-package", "sha": "PASS", "branches": "PASS", "tags": "PASS", "overall": "PASS"},
    ...
  ]
}
```

### Step 4: Status & Report

```bash
curl http://localhost:8000/status
```

```json
{
  "total_repositories": 7,
  "migrated": 7,
  "synced": 0,
  "failed": 0,
  "pending": 0
}
```

### Step 5: Cutover

```bash
curl -X POST http://localhost:8000/sync/cutover
```

Executes: Freeze GitLab → Final Sync → Validation → Enable GitHub → Report

---

## Automated Scheduling

| Schedule | Job | Description |
|----------|-----|-------------|
| Every 6 hours | Incremental Sync | Detects changed repos, mirrors to GitHub |
| Daily at 2 AM | Validation | Verifies all repos still in sync |

Powered by APScheduler. Runs automatically when the server starts.

---

## Sync Engine Logic

```
For each migrated repository:

  1. GET GitLab HEAD SHA (via API)
  2. Compare with stored SHA in database
  3. If unchanged → SKIP
  4. If changed:
     a. git fetch --all --prune (from GitLab)
     b. git push --mirror (to GitHub)
     c. Validate new SHA
     d. Update database
  5. Retry up to 3 times on failure
```

---

## Validation Checks

| Check | Method | Pass Criteria |
|-------|--------|---------------|
| HEAD SHA | Compare default branch SHA | Exact match |
| Branches | Count branches on both sides | Equal count |
| Tags | Count tags on both sides | Equal count |

Results: `PASS` / `WARN` (minor diff) / `FAIL`

---

## Tests

```bash
pytest -v
```

9 tests covering: models, GEI service, sync engine, validation logic, config loading.

---

## Project Structure

```
migration-poc/
├── app.py                      # FastAPI application entry point
├── config.py                   # Configuration loader (YAML)
├── config.yaml                 # Credentials and repo list
├── database.py                 # SQLAlchemy + SQLite setup
├── models.py                   # Repository, MigrationLog models
├── scheduler.py                # APScheduler (6h sync, daily validation)
├── services/
│   ├── factory.py              # Service factory (real vs demo mode)
│   ├── gitlab_service.py       # GitLab REST API client
│   ├── github_service.py       # GitHub REST API client
│   ├── gei_service.py          # Migration engine (create repo + git mirror)
│   ├── git_service.py          # Git operations (fetch, push --mirror)
│   ├── sync_service.py         # Sync orchestrator with retry
│   ├── validation_service.py   # SHA/branch/tag validation
│   └── demo_services.py        # Mock services for demo mode
├── api/
│   ├── migrations.py           # POST /migrate
│   ├── sync.py                 # POST /sync, POST /sync/cutover
│   └── validation.py           # POST /validate
├── tests/
│   └── test_migration.py       # Unit tests (9 tests)
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── README.md
```

---

## Extending to Production

To support thousands of repositories:

1. Replace SQLite with PostgreSQL
2. Add Celery/Redis for distributed task queue
3. Implement worker pools for parallel sync
4. Add Prometheus metrics and Grafana dashboards
5. Deploy on Kubernetes with horizontal scaling
6. Add webhook listeners for real-time change detection
7. Implement proper secret management (Vault/Azure KeyVault)
8. Add GitLab metadata migration (MRs → PRs, Issues) via API
