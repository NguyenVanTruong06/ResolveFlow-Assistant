# Gate 6 DEP-01 Live Dispatcher & Oracle Boundary Design Review

- **Review date:** 2026-11-05
- **Decision:** `CHANGES_REQUIRED`
- **Reviewed workorder:** `G07-07B7-GATE6-DEP01 — Live Dispatcher & Oracle Boundary Design`
- **Workorder SHA-256:** `9fb570d7551108de234e7c56e96f4637ea3b3a2e8965c1d8ca90173d746c3f32`
- **Design:** `docs/architecture/07B7-GATE6-DEP01-live-dispatch-oracle-design.md`
- **Design SHA-256:** `045745e4e8eab3137073083f446dc10100ea0ad8070c526d7358b3f7a315dd8c`
- **Submission report SHA-256 at review:** `368fbf864c89d8f91ad048d348ff22f4b3a8892df9eba571021719a3469a4481`
- **Review mode:** Static design review only. No tests, Resolve imports/connections, IPC, harness, key access, or live operations were performed.

## Decision summary

The design correctly treats DEP-01 as target architecture, explicitly records that required system principals and runtime paths do not exist on the host, keeps `TrustedCanaryDispatchService` mock-only, and preserves the Gate 6 stop boundary. The design is not ready to serve as the implementation contract because the wire schema has contradictory field names, the DAC table does not establish a consistent access path for the stated clients, and the one-shot/in-doubt guarantees are not durable across process restart. The proposed follow-on allowlists also omit required deployment and integration artifacts or leave them unspecified.

These are design defects, not evidence that a live boundary currently exists. Gate 6 remains `NOT ACCEPTED` and incident-only.

## Findings

### DEP01-01 — HIGH — Oracle request/response correlation fields are inconsistent

**Evidence:** The declared request and response DTOs use `request_id` and `request_nonce` (design §§3.3.1–3.3.2, lines 151–175). The 11-point gate instead compares `response.request_id == req.id` and `response.nonce == req.nonce` (§4.2, line 244). Those names are absent from the declared DTOs.

**Impact:** Implementers can choose different wire fields or omit correlation checks, making the central anti-replay/request-binding check ambiguous and potentially causing the dispatcher to compare missing or caller-controlled values.

**Required correction:** Use one exact field vocabulary throughout schema, signature claims, examples, and invariant table: `req.request_id`, `req.request_nonce`, `response.request_id`, and `response.request_nonce`. Specify byte-for-byte equality and include one canonical request/response example (or equivalent exact field list) showing which claims are covered by the Ed25519 signature. Keep schema version and unknown/duplicate-field rejection explicit.

### DEP01-02 — HIGH — Socket DAC does not define a coherent access matrix for all three principals

**Evidence:** The runtime directory is specified as `_resolveflow_signer:resolveflow` mode `0750`, with the public socket `_resolveflow_signer:resolveflow` mode `0660` and Oracle socket `_resolveflow_signer:_resolveflow_dispatcher` mode `0660` (§2.2, lines 99–101). The runner is `interactive_user`, has “zero special privileges,” and must use the public endpoint (§2.1 and §3.1), but the design does not say how that UID traverses `/run/resolveflow`. The dispatcher is described with primary group `resolveflow` (§2.1) while the Oracle socket uses group `_resolveflow_dispatcher`; the required group memberships/ACLs and the dispatcher’s access to the public endpoint are not resolved.

**Impact:** A deployment following the document may either make the required clients unable to connect or broaden socket access beyond the intended principal boundary. `SO_PEERCRED` on the Oracle listener does not fix an inconsistent filesystem traversal/socket permission matrix.

**Required correction:** Add a UID/GID and supplementary-group/ACL matrix covering directory traversal, connect permission for each socket, and filesystem mutation rights for signer, dispatcher, and interactive runner. State explicitly which principal can reach each endpoint and how the runner can submit challenge/consent without gaining Oracle access or Resolve IPC. Ensure the chosen directory/socket ownership is internally consistent and remains compatible with Plan R7 §2; if not, record a plan deviation for governance rather than silently changing it.

### DEP01-03 — BLOCKER — At-most-once execution and IN_DOUBT lock are not specified to survive restart

**Evidence:** The one-shot invariant checks `execution_id not in _dispatched_execution_ids ledger` (§4.2, line 254), but persistence and recovery semantics for that ledger are not defined. The target lock is an in-memory lock plus `flock` (§5.3, line 298), and the WAL transitions to `ACTION_PENDING` without an explicit fsync/commit requirement for that transition (§5.3, lines 299–300). The state machine says an in-doubt lock is “permanently retained” and redispatch forbidden (§5.5, lines 330–335), but a process crash releases process-owned `flock`; no durable execution-ID reservation/recovery rule is specified. The Oracle pipeline also checks authorization HMAC/freshness/revocation but does not state that an authorization becomes unusable after a completed dispatch.

**Impact:** After a successful mutation and process restart, the same still-valid authorization may not be recognized as already dispatched. After a crash, the claimed permanent target lock cannot be inferred from an in-memory lock or OS flock alone. This leaves replay and duplicate mutation behavior underspecified at the exact failure boundary the design is meant to contain.

**Required correction:** Define a durable, signer/dispatcher-owned execution journal keyed by `execution_id` and target UID, with an atomic/fsync-committed reservation before any Resolve mutation. Specify recovery for every durable state, including restart behavior for `ACTION_PENDING`, `COMPLETED`, and `ABORTED`; persist terminal IDs so replay remains rejected after restart; and define how an `IN_DOUBT` record blocks the target after the original process and its flock are gone. Clarify whether the signer Oracle tracks/consumes execution IDs or whether the dispatcher journal is authoritative, and ensure both sides cannot disagree. The audit intent and journal reservation must have explicit durability ordering before the adapter call.

### DEP01-04 — MEDIUM — Follow-on workorders lack the exact deployment/test/harness allowlists required by DEP-01

**Evidence:** DEP-02 proposes only two service modules and two unit-test files (§7, lines 370–377), although its objective includes a daemon/service deployment and socket lifecycle. No service-unit/configuration file or integration-test path is listed. DEP-03 lists an integration test but no deployment/service definition or exact mock-boundary fixture requirements (§7, lines 379–385). DEP-04 names `run_gate6.py` and generic evidence outputs without an exact repository/runtime path or complete file allowlist (§7, lines 387–392).

**Impact:** The dependency sequence cannot be issued as a bounded implementation plan without later expanding allowlists ad hoc. In particular, source-level service code alone cannot demonstrate the intended OS principal, DAC, startup ordering, and lifecycle boundary.

**Required correction:** For each follow-on workorder, enumerate exact paths for source, tests, service definitions/configuration, and evidence. If privileged provisioning (system users/groups, `/var/lib`, `/run`, systemd installation) cannot be safely included, define it as a separate bounded provisioning/readiness workorder with its own owner, allowlist, and review gate. For DEP-04, pin the exact harness path and evidence directory and make its issuance conditional on DEP-02/03 acceptance plus a fresh, separately authorized Gate 2/Gate 4 state. No current document acceptance authorizes those operations.

## Required disposition

Revise the DEP-01 design and submit a new revision for review. Do not issue implementation or execution authorization based on this revision. Acceptance of a future design revision, if granted, can authorize only the next bounded implementation workorder, not Gate 6 execution.

## Safety state

- `ready_to_execute = False`.
- Gate G07 and Milestones M1/M2/M3 remain `OPEN`.
- Gate 6 remains `NOT ACCEPTED` and incident-only.
- No tests, Resolve SDK imports, Resolve IPC/UI activity, harness creation, key access, or live operations were performed for this review.
