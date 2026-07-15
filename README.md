# ChargeOpt OS

ChargeOpt OS is a production-grade control plane for ultra-fast charging, PV-storage-charging stations, fleet depots, and unattended VPP aggregation trading.

**Stack:** FastAPI · uvicorn · psycopg3 · PostgreSQL · pydantic-settings · structlog · Prometheus · Docker · GitHub Actions · Vercel

## Features

- Multi-tenant station and asset domain model
- Deterministic 24 h telemetry with load, storage SOC, PV, queue, tariff, revenue
- Operating cockpit: station economics, demand peaks, storage dispatch, margin
- Station detail, load forecast, storage plan, dynamic pricing hints, alert triage
- Storage ROI simulator (NPV, IRR, payback)
- Revenue diagnostics that compare actual operation with a counterfactual no-EMS baseline and prove monthly profit lift
- VPP resource aggregation and demand-response decomposition
- Calibrated P10/P50/P90 flexibility forecasts and risk-constrained portfolio bid blocks
- Idempotent signed market submission, immutable hash-chained order events, fills, cancellation states, and delivery schedules
- Automated trade-to-site dispatch, interval meter evidence, imbalance settlement, and finance evidence roots
- Five-minute GitHub Actions autopilot with policy gates, duplicate-cycle protection, circuit breaking, and failure compensation
- Auditable dispatch recommendation records
- Login sessions, RBAC permissions, tenant-scoped repository reads, and Postgres RLS policy foundations
- OCPP / Modbus / MQTT gateway message normalization and protocol message ledger
- Async task queue with worker leases/retries, dispatch approval workflow, edge command receipts, and VPP settlement ledger
- Risk-constrained rolling MPC/MILP dynamic-programming optimizer with persisted optimization run evidence
- Persisted revenue-proof snapshots for monthly ROI audit and customer business reviews
- **Production additions:** PostgreSQL persistence, pydantic-based config, structured JSON logs, Prometheus `/metrics`, `/health` probe, API-Key/Bearer auth, CORS, per-IP rate limiting, request-ID propagation, Docker + compose, CI/CD pipeline

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
| `API_KEY` | _(blank)_ | Shared secret for `X-API-Key`; production also supports `/api/v1/auth/login` bearer sessions |
| `CORS_ORIGINS` | `*` | Comma-separated allowed origins |
| `RATE_LIMIT_PER_MINUTE` | `120` | Per-IP request cap |
| `LOG_LEVEL` | `info` | `debug` \| `info` \| `warning` \| `error` |
| `METRICS_ENABLED` | `true` | Expose `/metrics` (Prometheus) |
| `EDGE_GATEWAY_URL` | _(blank)_ | Edge gateway execution endpoint used by `chargeopt-worker` |
| `EDGE_GATEWAY_TOKEN` | _(blank)_ | Optional bearer token for the edge gateway |
| `WORKER_POLL_INTERVAL_SECONDS` | `5` | Poll interval for the long-running worker |
| `CRON_SECRET` | _(blank)_ | Shared bearer secret used by the production scheduler; required for unattended cycles |
| `MARKET_WEBHOOK_SECRET` | _(blank)_ | HMAC secret for signed trade-fill callbacks |
| `VPP_AUTOMATION_ENABLED` | `false` | Enables scheduled portfolio forecasting and bidding |
| `VPP_MAX_ORDERS_PER_CYCLE` | `8` | Hard cap on new orders created per tenant and cycle |
| `<CREDENTIAL_REF>_TOKEN` | _(blank)_ | Live market gateway token resolved from a connection row |
| `<CREDENTIAL_REF>_SIGNING_SECRET` | _(blank)_ | Independent HMAC key for outbound market orders |

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
- `004_industrial_control_plane.sql` – users/sessions/RBAC, tenant RLS policies, protocol devices/messages, task queue, dispatch approvals, edge receipts, optimization runs, and VPP settlements
- `005_operational_closure.sql` – task worker leases/retries/timeout diagnostics and persisted revenue-proof evidence snapshots
- `006_force_rls.sql` – FORCE ROW LEVEL SECURITY on tenant-scoped operational tables, including task, receipt, optimization, settlement, and proof ledgers
- `007_vpp_trading_platform.sql` – market connections, versioned risk policy, probabilistic forecasts, orders/events/trades, delivery schedules, interval meters, settlement batches, circuit breakers, automation runs, outbox, immutable audit trigger, and forced tenant RLS

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
GET /ready
GET /metrics                          (Prometheus text format)

GET /api/v1/overview
GET /api/v1/stations
GET /api/v1/stations/{station_id}
GET /api/v1/dispatch
GET /api/v1/vpp
GET /api/v1/vpp/trading/dashboard
GET /api/v1/roi?capacity_kwh=1200&power_kw=600&capex_per_kwh=1150&vpp=true
GET /api/v1/revenue-diagnostics?station_id=st-hq-hongqiao
GET /api/v1/audit?limit=50&offset=0

POST /api/v1/auth/login
GET  /api/v1/auth/me
POST /api/v1/protocols/{ocpp|modbus|mqtt}/messages
POST /api/v1/revenue-diagnostics/runs
POST /api/v1/tasks
POST /api/v1/tasks/claim
POST /api/v1/tasks/{task_id}/complete
POST /api/v1/tasks/reap-expired
POST /api/v1/dispatch/recommendations/{id}/approval
POST /api/v1/dispatch/recommendations/{id}/approve
POST /api/v1/dispatch/recommendations/{id}/reject
POST /api/v1/edge/receipts
POST /api/v1/optimization/runs
POST /api/v1/vpp/settlements
POST /api/v1/vpp/trading/automation/run
POST /api/v1/vpp/trading/trades
POST /api/v1/vpp/trading/market-webhook
POST /api/v1/vpp/trading/meter-intervals
POST /api/v1/vpp/trading/settlement-batches
POST /api/v1/vpp/trading/circuit-breaker
GET  /api/cron/vpp-cycle
```

Error responses conform to **RFC 7807** (`application/problem+json`).  
Production endpoints require either a valid `Authorization: Bearer <token>` from `/api/v1/auth/login` or a valid `X-API-Key`.

Migration `008` disables the public bootstrap credential and revokes its sessions. Provision or rotate an administrator interactively with `DATABASE_URL=... python scripts/manage_user.py --email <email> --tenant-id <tenant> --role tenant_admin`; passwords are never accepted as command-line arguments or stored in the repository.

## CI/CD (GitHub Actions)

`.github/workflows/ci.yml` runs on every push/PR:

1. **lint** – ruff check + format
2. **test** – pytest with coverage upload to Codecov + artifact
3. **build** – Docker image build + push to GHCR (`ghcr.io/<owner>/chargeopt`)
4. **scan** – Trivy vulnerability scan; results uploaded to GitHub Security tab (SARIF)
5. **migrate-neon** – run `python scripts/migrate.py` against Neon using the `DATABASE_URL` GitHub secret
6. **deploy-vercel** – optional Vercel CLI deploy when `VERCEL_TOKEN` is configured; otherwise Vercel Git integration handles the push deployment

Required GitHub secret for automatic schema creation: `DATABASE_URL`. Optional CLI deploy secrets: `VERCEL_TOKEN`, `VERCEL_ORG_ID`, `VERCEL_PROJECT_ID`. `DATABASE_URL` is used only inside the migration job and is not stored in the repository. `migrate-neon` fails closed when `DATABASE_URL` is missing so push-to-production schema creation cannot silently skip.
GHCR push uses the built-in `GITHUB_TOKEN` (no extra secret needed).

## Deploy To Vercel With Neon

- `api/index.py` re-exports the FastAPI `app` for Vercel's ASGI runtime.
- GitHub Actions runs `python scripts/migrate.py` before production deployment so Neon tables are created/updated automatically on `main` pushes.
- Set `DATABASE_URL` in both Vercel Production environment variables and GitHub Actions secrets. Vercel uses it at runtime; GitHub Actions uses it to apply migrations before deployment.
- Set `API_KEY` in production for machine-to-machine access, or use `/api/v1/auth/login` for human/operator access.
- Set the same `CRON_SECRET` in Vercel Production and the GitHub `production` environment, then set `VPP_AUTOMATION_ENABLED=true`. `.github/workflows/vpp-cycle.yml` calls the protected endpoint every five minutes with retry, timeout, and concurrency controls. This remains deployable on Vercel Hobby; a dedicated scheduler can call the same idempotent endpoint for stricter timing SLAs.
- Keep a connection in `sandbox` mode for certification drills. For live mode, set its `base_url`, `credential_ref`, venue participant ID, and the matching token/signing-secret environment variables.
- Field equipment must connect through an authenticated gateway that posts OCPP/Modbus/MQTT-normalized payloads to `/api/v1/protocols/{protocol}/messages`; direct charger control remains gated by approval + task queue + edge receipt.

Production URL: **https://chargeopt-os.vercel.app**

## Revenue Proof Engine

The moat metric is not "the algorithm exists"; it is whether ChargeOpt can repeatedly prove:

> Same station, same tariff and operating context, with ChargeOpt vs. without ChargeOpt: monthly profit lift in CNY.

`GET /api/v1/revenue-diagnostics` implements that proof loop:

- Builds a counterfactual no-EMS baseline for each station.
- Attributes monthly impact across tariff arbitrage, demand-charge reduction, throughput uplift, queue-loss avoidance, VPP revenue, and battery degradation.
- Reports p90 confidence bands so sales claims are not presented as exact point estimates.
- Produces a moat scorecard covering operating data hours, device adapters, ROI case count, and monthly profit proof.
- Supports station filtering for a single-site sales review or monthly customer business review.

`POST /api/v1/revenue-diagnostics/runs` persists the same proof payload to PostgreSQL as an auditable evidence snapshot. This is the monthly customer-business-review artifact that turns the product claim into a ledgered ROI case.

The optimizer used by `/api/v1/optimization/runs` is `risk-constrained-mpc-milp-dp-v2`: a serverless-safe rolling-horizon dynamic program over discrete charge/discharge actions. It enforces SOC, transformer, ramp, VPP reserve, degradation, and service-pressure constraints without requiring a heavy commercial solver in Vercel.

## Unattended VPP Trading

Every five-minute cycle has one durable `tenant_id + cycle_key`, so retries cannot double-submit. The autopilot builds a calibrated portfolio forecast, derives conservative sell blocks from P10 flexibility, evaluates the active versioned risk policy, writes an idempotent order, and submits it through either the deterministic sandbox venue or a signed REST market gateway.

Market fills are accepted only through authenticated operator APIs or a timestamped HMAC webhook. A fill atomically creates station delivery schedules and leased `dispatch.execute` tasks. The edge worker records equipment receipts; a failed or rolled-back VPP command opens the global tenant breaker and blocks new orders. Interval meter evidence then produces trade-level performance, imbalance costs, penalties, net revenue, and a batch evidence root hash.

The system fails closed when telemetry is stale, forecast confidence is below policy, order/daily limits are exceeded, the breaker is open, a state transition is illegal, credentials are missing, or webhook signatures are invalid.

## Execution Closure

Approved dispatch recommendations create `task_queue` work items. Run the worker next to the site gateway:

```bash
EDGE_GATEWAY_URL=https://edge-gateway.example.com/chargeopt/tasks/execute \
EDGE_GATEWAY_TOKEN=replace-me \
chargeopt-worker --worker-id site-hq-edge-1 --task-type dispatch.execute
```

For one-shot validation without touching field equipment:

```bash
chargeopt-worker --once --dry-run --worker-id smoke-worker --task-type dispatch.execute
```

The worker claims due work, posts the command payload to the authenticated edge gateway, and records an edge receipt when the gateway returns `succeeded`, `failed`, `rolled_back`, `accepted`, or `running`. If the gateway is unreachable or `EDGE_GATEWAY_URL` is missing outside dry-run mode, the task is completed through the retry path with the error captured in `last_error`.

Low-level APIs remain available for custom workers:

1. Claim work with `POST /api/v1/tasks/claim` using a stable `worker_id`.
2. Execute the command through the site gateway or EMS adapter.
3. Complete unreachable/retryable failures through `POST /api/v1/tasks/{task_id}/complete`.
4. Send equipment-level receipts through `POST /api/v1/edge/receipts`.

Expired worker leases can be requeued or failed with `POST /api/v1/tasks/reap-expired`. Each state transition writes audit evidence.

## Deployment Boundaries

- The protocol layer is an authenticated gateway API, not a direct vendor cloud connector. Site-specific OCPP brokers, Modbus TCP polling, and MQTT credentials must be configured outside the repository and forward signed payloads into the API.
- The market adapter is a hardened gateway contract, not a claim of certification for every provincial/grid venue. Each launch still requires venue-specific protocol mapping, certificates, participant onboarding, sandbox conformance, and regulatory acceptance.
- The optimizer is dependency-free and serverless-safe. Station dispatch is a discrete rolling MPC search; portfolio bids use quantile/CVaR-style risk reserves. Replace the implementation behind the same persisted evidence contract when a licensed commercial solver is required by a customer or regulator.
- GitHub Actions cannot create `DATABASE_URL` automatically without repository secret permissions. Add `DATABASE_URL` under GitHub repository secrets before relying on push-to-production migrations.
