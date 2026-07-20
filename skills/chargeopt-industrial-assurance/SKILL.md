---
name: chargeopt-industrial-assurance
description: Harden and qualify ChargeOpt for industrial unattended operation through OT cybersecurity, secrets and certificates, HA/DR, backups, observability, SLOs, performance, fault injection, commissioning, runbooks, vendor conformance, shadow operation, and go-live approval. Use for production readiness, industrial standards, security reviews, disaster recovery, high availability, load tests, commissioning, field qualification, or claims that the platform is fully complete.
compatibility: Vercel/Neon control plane, Kubernetes/edge workers, OT gateways, CI/CD and operational evidence
---

# ChargeOpt Industrial Assurance

Separate software readiness, deployment readiness, site commissioning, and market authorization. Passing unit tests closes only the first category.

## Workflow

1. Build a threat model and failure-mode inventory across cloud, database, worker, network, gateway, device, meter, optimizer, market, and operator.
2. Define SLOs, recovery objectives, capacity targets, escalation, and evidence retention with the customer.
3. Implement preventive, detective, and recovery controls with automated tests and runbooks.
4. Run fresh deploy, upgrade, backup/restore, failover, network partition, stale data, duplicate, overload, solver, and device fault exercises.
5. Complete vendor protocol conformance, point-to-point checks, meter acceptance, protection review, and site commissioning.
6. Run consecutive shadow operation and compare recommendations, commands, receipts, effects, service, safety, and economics.
7. Require signed maker-checker go-live approval with unresolved risks and rollback plan.

## Assurance domains

- Identity and tenancy: SSO/MFA where required, RBAC, RLS, service identities, session controls, segregation of duties, and access reviews.
- OT security: zones/conduits, allowlists, jump access, mTLS, secure boot/attestation where available, local least privilege, and no direct cloud device writes.
- Secrets/software supply chain: managed secrets, rotation, SBOM, dependency/code/container scanning, signed artifacts, provenance, and controlled promotion.
- Availability: redundant workers/gateways, leases, idempotency, backpressure, offline operation, queue limits, circuit breakers, and safe local mode.
- Data resilience: PITR, immutable evidence retention, backup encryption, restore tests, replication, and documented RPO/RTO.
- Observability: metrics, traces, structured logs, audit, synthetic checks, time sync, alert routing, SLO/error budget, and customer status.
- Performance: realistic tenants/sites/points/sessions/scenarios, burst ingest, solver concurrency, market deadlines, soak, and resource limits.
- Safety: HAZOP/FMEA inputs, protection boundaries, emergency stop, command preconditions, rollback, alarm response, and incident review.

## Qualification evidence

- Vendor model/firmware/driver mapping and protocol conformance report.
- Meter calibration and settlement acceptance.
- Unbalanced AC/protection and interconnection study where applicable.
- Commissioning checklist, point-to-point record, fault injection, restore drill, and operator training.
- Consecutive qualified shadow days with no unresolved critical incident and agreed KPI/SLO results.
- Market eligibility, participant credentials, settlement account, and customer authorization.

## Acceptance

- CI, deployment, authenticated runtime, database, workers, edge, devices, and market paths are verified separately.
- Recovery drills meet customer-approved RPO/RTO and retain evidence.
- Critical failures transition to a defined safe state without silent data or command loss.
- Go-live readiness is machine-readable and fails closed on every missing external input.
- Never label synthetic or abbreviated test evidence as completed field qualification.
