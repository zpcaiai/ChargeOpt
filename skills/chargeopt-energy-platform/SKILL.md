---
name: chargeopt-energy-platform
description: Orchestrate the complete expansion of ChargeOpt into a charging-station, battery-storage, and campus multi-energy optimization platform. Use this skill whenever the user asks for the overall roadmap, asks to implement everything step by step, requests a production-readiness audit, or spans more than one ChargeOpt energy domain. It selects the earliest dependency-complete specialist skill and enforces industrial evidence and release gates.
compatibility: ChargeOpt Python 3.12, FastAPI, PostgreSQL/Neon, Vercel, edge workers, scipy HiGHS
---

# ChargeOpt Energy Platform Orchestrator

Treat the product as one shared energy control plane with charging, storage, and campus asset packs. Do not create three disconnected applications.

## Required reading

1. Read `references/program.md` completely.
2. Read `../README.md` for the skill registry.
3. Inspect the current repository, migrations, tests, deployment workflow, and dirty worktree.
4. Load the specialist skill for the earliest incomplete workstream.

## Orchestration workflow

1. Build a current capability matrix using repository evidence. Mark each capability `absent`, `partial`, `code-complete`, `shadow-qualified`, or `field-qualified`.
2. Select one dependency-complete batch from `references/program.md`. Do not mark external or elapsed-time gates complete from synthetic tests.
3. Produce a scoped implementation plan covering domain, migration, RLS, repository, API, edge behavior, UI, audit, tests, operations, and documentation.
4. Implement vertically. A batch should expose one usable and testable workflow rather than disconnected schemas or algorithms.
5. Run focused tests after each layer and full quality gates before a commit or deployment.
6. Update the capability matrix with evidence links, residual risks, and external inputs.
7. Commit and push only when explicitly requested, staging only owned files and verifying the remote SHA and CI state.

## Global invariants

- Preserve fail-closed tenant RLS, RBAC, immutable evidence, idempotency, and audit trails.
- Separate pure computation from PostgreSQL persistence and physical execution.
- Route every field command through approval, durable task, edge safety checks, and equipment receipt.
- Label synthetic, replay, shadow, observed, and field-qualified evidence without promotion by assertion.
- Keep safety constraints hard. Feasibility restoration may expose service shortfall but cannot silently relax electrical, thermal, SOC, comfort, or process limits.
- Version topology, point mappings, tariffs, models, optimization policies, drivers, and market rules.
- Keep algorithms replaceable through explicit contracts; record algorithm version and canonical input hash.
- Preserve unrelated user changes and existing public API behavior.

## Completion report

Report:

- completed workstream and batch
- files, migrations, APIs, workers, and UX added
- tests, coverage, load checks, migration checks, and runtime smoke evidence
- evidence class achieved
- remaining code gaps
- remaining customer, vendor, utility, or market inputs

Never report the complete platform as field-ready until the industrial-assurance skill has real commissioning evidence and the required consecutive shadow period.
