# ChargeOpt OS

ChargeOpt OS is a production-grade Enterprise platform for ultra-fast charging, PV-storage-charging stations, fleet depots, and VPP aggregation workflows.

**Stack:** FastAPI · uvicorn · psycopg3 · PostgreSQL · pydantic-settings · structlog · Prometheus · Docker · GitHub Actions · Vercel

## Features

- Multi-tenant station and asset domain model
- Deterministic 24 h telemetry with load, storage SOC, PV, queue, tariff, revenue
- Operating cockpit: station economics, demand peaks, storage dispatch, margin
- Station detail, load forecast, storage plan, dynamic pricing hints, alert triage
- Storage ROI simulator (NPV, IRR, payback)
- VPP resource aggregation and demand-response decomposition
- Auditable dispatch recommendation records
- **Production additions:** PostgreSQL persistence, pydantic-based config, structured JSON logs, Prometheus `/metrics`, `/health` probe, API-Key auth, CORS, per-IP rate limiting, request-ID propagation, Docker + compose, CI/CD pipeline

## Quick Start (in-memory, no DB)

```bash
cp .env.example .env
pip install -r requirements.txt

# Option A – uvicorn directly
uvicorn chargeopt.app:app --reload

# Option B – module entry point (respects config Workers/Host/Port)
python -m chargeopt

# Option C – CLI (after pip install -e .)
chargeopt
```

Open [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs) for the interactive API.

## Docker Compose (PostgreSQL)

```bash
# 1. Create secrets files (git-ignored)
mkdir -p secrets
echo "chargeopt_dev_password" > secrets/db_password.txt
echo "admin_dev_password"     > secrets/pgadmin_password.txt

# 2. Start all services
docker compose up --build
```

- App: [http://localhost:8000](http://localhost:8000)
- pgAdmin: [http://localhost:5050](http://localhost:5050) (admin@chargeopt.local / admin)

## Legacy stdlib server (local demo only)

```bash
python3 -m chargeopt.server
```

## Configuration

Copy `.env.example` to `.env` and fill in values.  Key variables:

| Variable | Default | Description |
|---|---|---|
| `ENVIRONMENT` | `development` | `development` \| `staging` \| `production` |
| `DATABASE_URL` | _(blank)_ | PostgreSQL DSN; blank = in-memory mode |
| `API_KEY` | _(blank)_ | Shared secret for `X-API-Key` header; blank = auth disabled |
| `CORS_ORIGINS` | `*` | Comma-separated allowed origins |
| `RATE_LIMIT_PER_MINUTE` | `120` | Per-IP request cap |
| `LOG_LEVEL` | `info` | `debug` \| `info` \| `warning` \| `error` |
| `METRICS_ENABLED` | `true` | Expose `/metrics` (Prometheus) |

## Database Migrations

```bash
# Apply migrations against DATABASE_URL
python scripts/migrate.py

# Skip migrations (read-only replicas, Vercel Preview)
CHARGEOPT_SKIP_DB_MIGRATION=1 python scripts/migrate.py
```

Migrations are idempotent SQL files in `migrations/`:
- `001_init.sql` – schema + tables + indexes
- `002_seed.sql` – reference data + sample records
- `003_control_plane.sql` – telemetry ingest ledger, dispatch status workflow, persisted ROI simulations, and production seed telemetry

## Test

```bash
pytest                          # unit + integration + coverage
pytest -v tests/test_api.py     # HTTP layer only
pytest --no-cov                 # skip coverage
```

Coverage gate: **≥ 70 %** (configured in `pyproject.toml`).

## Lint

```bash
ruff check chargeopt/ tests/
ruff format chargeopt/ tests/
```

## API

All analytics routes are versioned under `/api/v1/`. Legacy `/api/*` aliases are kept for backward compatibility.

```text
GET /health
GET /metrics                          (Prometheus text format)

GET /api/v1/overview
GET /api/v1/stations
GET /api/v1/stations/{station_id}
GET /api/v1/dispatch
GET /api/v1/vpp
GET /api/v1/roi?capacity_kwh=1200&power_kw=600&capex_per_kwh=1150&vpp=true
GET /api/v1/audit?limit=50&offset=0
```

Error responses conform to **RFC 7807** (`application/problem+json`).  
All `/api/v1/*` endpoints accept optional `X-API-Key` header when `API_KEY` is set.

## CI/CD (GitHub Actions)

`.github/workflows/ci.yml` runs on every push/PR:

1. **lint** – ruff check + format
2. **test** – pytest with coverage upload to Codecov + artifact
3. **build** – Docker image build + push to GHCR (`ghcr.io/<owner>/chargeopt`)
4. **scan** – Trivy vulnerability scan; results uploaded to GitHub Security tab (SARIF)
5. **deploy-vercel** – pull Vercel production env, build, run Neon migrations with `DATABASE_URL`, then `vercel deploy --prebuilt --prod`

Required GitHub secrets: `VERCEL_TOKEN`, `VERCEL_ORG_ID`, `VERCEL_PROJECT_ID`, `DATABASE_URL`. `DATABASE_URL` is used only inside the deploy job and is not stored in the repository.
GHCR push uses the built-in `GITHUB_TOKEN` (no extra secret needed).

## Deploy To Vercel With Neon

- `api/index.py` re-exports the FastAPI `app` for Vercel's ASGI runtime.
- GitHub Actions runs `python scripts/migrate.py` before production deployment so Neon tables are created/updated automatically on `main` pushes.
- Set `DATABASE_URL` in both Vercel Production environment variables and GitHub Actions secrets. Vercel uses it at runtime; GitHub Actions uses it to apply migrations before deployment.
- Set `API_KEY` in production before enabling write APIs. Without it, production write endpoints return `503` and read endpoints remain available.

Production URL: **https://chargeopt-os.vercel.app**

## Next Milestones

1. RBAC + tenant scoping (JWT, per-tenant row-level security).
2. MQTT/OCPP/Modbus adapters behind gateway interfaces.
3. Persist forecast, optimisation, and dispatch plan versions.
4. Replace rule-based engine with MILP/MPC (CVXPY or Pyomo).
5. Signed command approval, edge validation, local rollback, and execution receipts.
