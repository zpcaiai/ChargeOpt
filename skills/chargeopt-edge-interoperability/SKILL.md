---
name: chargeopt-edge-interoperability
description: Implement and verify ChargeOpt edge connectivity and protocol interoperability across charging, storage, building, industrial, grid, and utility-meter devices. Use for OCPP, ISO 15118, Modbus, MQTT, BACnet, OPC UA, IEC 61850, IEC 104, DL/T 645, CJ/T 188, driver mappings, edge gateways, protocol conformance, or physical command delivery.
compatibility: chargeopt.edge_runtime, protocol ledger, task workers, mTLS/TLS, isolated OT networks
---

# ChargeOpt Edge Interoperability

Use one protocol-neutral ingress/command contract and adapter-specific conformance modules.

## Workflow

1. Inspect `edge_runtime.py`, `protocols.py`, task leases, command receipts, device tables, and current OCPP/Modbus/MQTT tests.
2. Define adapter capability, version, point mapping, command mapping, security profile, polling/subscription policy, and conformance status.
3. Implement parsing and normalization as pure functions; isolate sockets, certificates, and retries in runtime adapters.
4. Persist raw message hash, source/receive time, sequence, mapping version, quality, and idempotency key before derived processing.
5. Route commands through durable tasks, local safety validation, protocol adapter, equipment acknowledgement, and final receipt.
6. Add simulators, golden frames, malformed input, duplicate, reconnect, timeout, rollback, and offline-buffer tests.

## Adapter packs

- OCPP 1.6 compatibility plus OCPP 2.0.1/2.1 device model, smart charging, firmware, certificates, diagnostics, reservations, and ISO 15118/V2G capability.
- Modbus TCP/RTU register profiles with endian, scale, signedness, function-code allowlist, and write interlocks.
- MQTT with mTLS, topic allowlist, QoS policy, retained-message handling, schema version, and replay protection.
- BACnet/IP discovery, object/property mapping, COV subscription, priority array, relinquish default, and write priority.
- OPC UA secure endpoint, namespace/node mapping, subscription, method allowlist, and Energy Consumption Management semantics.
- IEC 61850/IEC 104 gateway contracts for DER, switchgear, protection, telemetry, and select-before-operate where applicable.
- DL/T 645 and CJ/T 188 meter collection with address, rate, checksum, freeze value, and rollover handling.

## Non-negotiable controls

- No cloud process writes directly to OT equipment.
- Require mutual identity, least-privilege commands, network allowlists, certificate rotation, and secret redaction.
- Reject unknown mapping versions and commands outside asset capabilities or safety envelope.
- Continue safe local operation during WAN loss; buffer evidence with bounded storage and deterministic replay.
- Separate protocol acknowledgement from physical success and verify resulting telemetry.

## Acceptance

- Adapter conformance matrices identify supported and unsupported profile features.
- Duplicate ingress and command retries cannot duplicate state transitions.
- Network loss, stale data, certificate expiry, malformed frames, and device reboot fail safely.
- Every command has task, adapter, equipment, and observed-effect evidence.
- Real vendor credentials and conformance reports remain external field inputs.
