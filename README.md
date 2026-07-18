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
- Transactional outbox publishing, market reconciliation, stale-lease recovery, and horizontally replicated VPP workers
- Auditable dispatch recommendation records
- Login sessions, RBAC permissions, tenant-scoped repository reads, and fail-closed Postgres RLS through a non-owner application role
- Deployable mTLS OCPP 1.6, TLS MQTT, and isolated-network Modbus TCP edge runtime with protocol ledger and command receipts
- Async task queue with worker leases/retries, dispatch approval workflow, edge command receipts, and VPP settlement ledger
- HiGHS mixed-integer rolling MPC with binary charge/discharge modes, physical constraints, and persisted solver evidence
- Adaptive ensemble forecasting with split-conformal intervals, synchronized residual block scenarios, and optional external time-series foundation-model inference
- Wasserstein-radius robust scenario MILP with explicit CVaR, battery SOH/temperature degradation cost, three-phase radial LinDistFlow projection, and bounded ADMM portfolio coordination
- Conservative offline fitted-Q evaluation with Mahalanobis OOD rejection and hard physical action projection; learning outputs remain shadow-only
- Model registry, quantile backtests, drift/quality gates, shadow promotion, and maker-checker model approval
- Immutable settlement event chain with approval, dispute, export, payment, and reversal workflows
- SLO incidents, 30-day immutable shadow qualification, dual-worker manifests, and scheduled Neon point-in-time restore drills
- Persisted revenue-proof snapshots for monthly ROI audit and customer business reviews
- Versioned charging-station digital twin with asset topology, immutable device historian, quality codes, state estimation, trust gating, electro-thermal/queue simulation, deterministic replay, diagnostics, calibration, maintenance closure, AIPW causal studies, and field qualification
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
| `CHARGEOPT_REQUIRE_EXACT_SOLVER` | production=`true` | Block dispatch if the HiGHS MILP solver is unavailable |
| `CHARGEOPT_TSF_ENDPOINT` | _(blank)_ | Optional HTTPS endpoint for an externally hosted time-series foundation model |
| `CHARGEOPT_TSF_TOKEN` | _(blank)_ | Bearer token paired with `CHARGEOPT_TSF_ENDPOINT`; partial configuration fails closed |
| `CHARGEOPT_TSF_MODEL` | `chronos-2` | Model identifier sent to the external forecast adapter |
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
- `008_disable_default_credentials.sql` – disables bootstrap users and sessions
- `009_reliable_vpp_operations.sql` – leased transactional outbox, reconciliation evidence, operational heartbeats, stale task recovery, and default-deny RLS policies
- `010_mlops_registry.sql` – model artifacts, data lineage, evaluation evidence, active-version uniqueness, and maker-checker lifecycle state
- `011_settlement_workflow.sql` – immutable settlement events/lines, disputes, exports, payments, and reversal adjustments
- `012_operational_assurance.sql` – live-market external input gates, incidents, SLO measurements, restore drills, and immutable shadow evidence
- `013_application_rls_role.sql` – non-owner `chargeopt_app` role with no RLS bypass; every runtime connection assumes this role
- `014_ingress_receipt_idempotency.sql` – durable protocol-message and edge-receipt idempotency keys
- `015_digital_twin.sql` – versioned asset graph, device historian, state estimates, model calibration, simulation/replay, diagnostics, maintenance actions, causal evidence, and field qualification
- `016_advanced_ems.sql` – append-only advanced EMS evidence ledger with idempotency, audit linkage, fail-closed tenant RLS, and immutable forecast/dispatch/network/coordination/policy records

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
GET /api/v1/vpp/trading/live-readiness
GET /api/v1/models
GET /api/v1/digital-twin/stations/{station_id}
GET /api/v1/digital-twin/stations/{station_id}/topology
GET /api/v1/digital-twin/stations/{station_id}/maintenance
GET /api/v1/digital-twin/qualification
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
POST /api/v1/models
POST /api/v1/models/{id}/evaluations
POST /api/v1/models/{id}/promote
POST /api/v1/digital-twin/topologies
POST /api/v1/digital-twin/topologies/{id}/activate
POST /api/v1/digital-twin/measurements
POST /api/v1/digital-twin/simulations
POST /api/v1/digital-twin/calibrations
POST /api/v1/digital-twin/trajectory-comparisons
POST /api/v1/digital-twin/causal-studies
POST /api/v1/digital-twin/optimization
POST /api/v1/digital-twin/maintenance/{id}/transition
POST /api/v1/digital-twin/commissioning/fault-injection
POST /api/v1/digital-twin/qualification/evidence
POST /api/v1/vpp/settlements
POST /api/v1/vpp/trading/automation/run
POST /api/v1/vpp/trading/trades
POST /api/v1/vpp/trading/market-webhook
POST /api/v1/vpp/trading/meter-intervals
POST /api/v1/vpp/trading/settlement-batches
POST /api/v1/vpp/trading/settlement-batches/{id}/{approve|dispute|resolve-dispute|export|paid|reverse}
POST /api/v1/vpp/trading/circuit-breaker
GET  /api/cron/vpp-cycle
GET  /api/cron/assurance
```

Error responses conform to **RFC 7807** (`application/problem+json`).  
Production endpoints require either a valid `Authorization: Bearer <token>` from `/api/v1/auth/login` or a valid `X-API-Key`.

Migration `008` disables the public bootstrap credential and revokes its sessions. Provision or rotate an administrator interactively with `DATABASE_URL=... python scripts/manage_user.py --email <email> --tenant-id <tenant> --role tenant_admin`; passwords are never accepted as command-line arguments or stored in the repository.

## CI/CD (GitHub Actions)

`.github/workflows/ci.yml` runs on every push/PR:

1. **lint** – ruff check + format
2. **test** – pytest with coverage upload to Codecov + artifact
3. **build** – Docker image build + push to GHCR (`ghcr.io/<owner>/chargeopt`)
4. **scan** – blocking Trivy high/critical vulnerability scan with SARIF upload
5. **migrate-neon** – run `python scripts/migrate.py` against Neon using the `DATABASE_URL` GitHub secret
6. **deploy-vercel** – optional Vercel CLI deploy when `VERCEL_TOKEN` is configured; otherwise Vercel Git integration handles the push deployment

Required GitHub secret for automatic schema creation: `DATABASE_URL`. Optional CLI deploy secrets: `VERCEL_TOKEN`, `VERCEL_ORG_ID`, `VERCEL_PROJECT_ID`. `DATABASE_URL` is used only inside the migration job and is not stored in the repository. `migrate-neon` fails closed when `DATABASE_URL` is missing so push-to-production schema creation cannot silently skip.
GHCR push uses the built-in `GITHUB_TOKEN` (no extra secret needed).

Operational assurance additionally requires `CHARGEOPT_PRODUCTION_URL` and `CRON_SECRET`. Weekly point-in-time recovery drills require `NEON_API_KEY`, `NEON_PROJECT_ID`, and optionally `NEON_PARENT_BRANCH_ID`.

## Deploy To Vercel With Neon

- `api/index.py` re-exports the FastAPI `app` for Vercel's ASGI runtime.
- GitHub Actions runs `python scripts/migrate.py` before production deployment so Neon tables are created/updated automatically on `main` pushes.
- Set `DATABASE_URL` in both Vercel Production environment variables and GitHub Actions secrets. Vercel uses it at runtime; GitHub Actions uses it to apply migrations before deployment.
- Set `API_KEY` in production for machine-to-machine access, or use `/api/v1/auth/login` for human/operator access.
- Set the same `CRON_SECRET` in Vercel Production and GitHub, then set `VPP_AUTOMATION_ENABLED=true`. GitHub is the fallback scheduler; `deploy/k8s/vpp-workers.yaml` runs two lease-safe workers for stricter availability.
- Keep a connection in `sandbox` mode during qualification. Live mode remains blocked until market certificate status, trading qualification, device credential attestation, secret-backed gateway credentials, and 30 consecutive qualified shadow days are all present.
- Install the field package with `pip install '.[edge]'`, configure `config/edge.example.json`, and run `chargeopt-edge --config /etc/chargeopt/edge.json`. Certificates and vendor credentials remain in the site secret store.
- The HA worker deployment expects `edge-gateway-url` and `edge-gateway-token` keys in the external `chargeopt-production` Secret; missing gateway credentials leave dispatch fail-closed.

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

The legacy revenue-diagnostics baseline is explicitly labeled an engineering counterfactual. It does not claim causal identification. `/api/v1/digital-twin/causal-studies` implements an actual augmented inverse-propensity weighted estimator with covariate adjustment, overlap checks, confidence intervals, and a deterministic placebo gate; insufficient samples or poor overlap cannot produce an auditable uplift claim.

## Charging-Station Digital Twin

The twin separates immutable device facts from versioned derived state. A station topology models transformers, buses, meters, chargers, connectors, PCS, batteries, PV inverters, sensors, and gateways. Measurements carry source/receive timestamps, normalized units, quality codes, evidence hashes, and idempotency keys. State estimates expose residuals, confidence intervals, a trust score, and an autonomy gate.

The deterministic simulator models battery energy, dynamic conversion efficiency, thermal derating, transformer thermal loading, PV curtailment, and charging queues. Scenario runs, calibration evidence, predicted-versus-realized comparisons, root-cause diagnostics, maintenance actions, and commissioning fault-injection results are persisted with tenant RLS and audit evidence. Autonomous twin-aware optimization remains blocked until state trust is sufficient and the station has real field qualification, including 30 consecutive qualified shadow days.

The optimizer used by `/api/v1/optimization/runs` is `scipy-highs-milp-mpc-v1`. It solves binary charge/discharge exclusivity, SOC dynamics and bounds, transformer limits, ramp limits, demand peak, degradation, service pressure, and terminal VPP reserve as a mixed-integer program. Each run stores solver status, objective, MIP gap, and node count. Production fails closed when the exact solver is unavailable; the labeled discrete fallback is development-only.

## Advanced EMS Optimization

The `/api/v1/ems/*` surface adds replayable, risk-aware decision support without bypassing the dispatch approval plane:

- `POST /ems/forecasts` fits seasonal, robust-trend, and Fourier-ridge members, weights them by holdout error, calibrates finite-sample intervals, and generates synchronized residual block scenarios. An optional HTTPS adapter can add a separately hosted time-series foundation model; model use is explicit per request and partial credentials fail closed.
- `POST /ems/dispatch-runs` solves a shared, non-anticipative scenario MILP with Wasserstein uncertainty inflation, expected energy/demand cost, CVaR tail cost, SOC/ramp/transformer limits, terminal reserve, and SOH/temperature-aware battery degradation.
- `POST /ems/network-projections` projects proposed station load through a radial, phase-decoupled LinDistFlow model with line, transformer, power-factor, and voltage limits. This result is deliberately marked `ac_certified=false`; a venue/site-specific AC study remains an external commissioning input.
- `POST /ems/portfolio-coordination` uses bounded consensus ADMM to allocate a portfolio target while preserving local resource bounds and recording primal/dual convergence evidence.
- `POST /ems/offline-policy/evaluations` trains conservative linear fitted-Q models only from safe transitions, rejects out-of-distribution states, and projects actions into hard physical bounds. These outputs are always `shadow_advisory_only` and never authorize field control.

With PostgreSQL enabled, every run is written to the immutable `ems_evidence_runs` ledger with tenant RLS, an idempotency key, full algorithm version, evidence class, canonical input hash, request/result payloads, actor, and audit entry. Without PostgreSQL, responses are explicitly labeled transient and are not represented as persisted evidence.

## Unattended VPP Trading

Every five-minute cycle has one durable `tenant_id + cycle_key`, so retries cannot double-submit. The autopilot builds synchronized station scenarios, derives conservative sell blocks from P10 flexibility, records empirical delivery-shortfall VaR/CVaR, evaluates the active versioned risk policy, and atomically writes both the order and its outbox event. Replicated workers publish with leased retries and idempotency keys, reconcile remote state, and open the breaker on dead letters or mismatches.

Market fills are accepted only through authenticated operator APIs or a timestamped HMAC webhook. A fill atomically creates station delivery schedules and leased `dispatch.execute` tasks. The edge worker records equipment receipts; a failed or rolled-back VPP command opens the global tenant breaker and blocks new orders. Interval meter evidence then produces trade-level performance, imbalance costs, penalties, net revenue, and a batch evidence root hash.

The system fails closed when telemetry is stale, forecast confidence is below policy, order/daily limits are exceeded, the breaker is open, a state transition is illegal, credentials are missing, webhook signatures are invalid, RLS context is absent, external market eligibility is unverified, or the 30-day shadow gate is incomplete.

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

## External Launch Inputs

The software workflow is implemented, but it deliberately cannot manufacture legal or physical-world authorization. A customer launch must provide the target market's participant qualification and certificate, the exact venue gateway mapping/conformance result, device-vendor credentials and register/profile maps, revenue-meter acceptance, settlement account details, and 30 elapsed days of qualified shadow evidence. `/api/v1/vpp/trading/live-readiness` reports each missing input and the live adapter refuses submission until every gate passes.
