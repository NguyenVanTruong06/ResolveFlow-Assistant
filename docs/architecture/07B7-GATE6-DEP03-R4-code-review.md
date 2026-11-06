# Gate 6 DEP-03-R2 Revision R4 — Static Code Review

**Workorder:** G07-07B7-GATE6-DEP03-R2 — LiveCanaryDispatchService Continuation  
**Disposition:** `CHANGES_REQUIRED`  
**Review mode:** Static source and artifact-integrity review. The authorized pytest command was not rerun. No daemon, host service, production state, Resolve SDK/API, or live operation was accessed.

## Artifact integrity

All six submitted SHA-256 values match the allowlisted files present in the workspace:

- `src/services/live_canary_dispatch_service.py`: `58287992165310b62a173cde2759396fc3297cfaa562c261d1b03b0e7c17e1ea`
- `src/services/durable_dispatch_journal.py`: `0d08297e36d100a2800c29f2f33c7f0ed629f1506fefafc2465153878284cf8a`
- `tests/services/test_live_canary_dispatch_service.py`: `a4fb66d40e742a329c99e354e3ff80ae01c2538b380d5d98711a2c9e080383b1`
- `tests/integration/test_live_dispatch_oracle_integration.py`: `31bba0c531c49f82261b58eeb396c49c88e640a33b8d7b51c5a7941230bace46`
- `.bridge/ux-evidence/DEP03/pytest.log`: `f11156f48ad142ed67e7aba097f130093c23c26bdc7f89308b9f313f294918c9`
- `.bridge/report.md`: `b4fcaac3881882ef417785f9fba10d1393ac1559eca00f223cbdc6013eea17d2`

The submitted log reports 39 passing tests. This is recorded as submitted evidence; it was not independently rerun in this static review.

## Findings

### DEP03-R4-01 — Clean-abort capability does not prove the actual mutation boundary — HIGH

The dispatcher creates a secret-backed capability and passes it to an adapter when the adapter accepts it (`live_canary_dispatch_service.py:884-920`). The proof HMAC and execution/target/action bindings prevent a caller without that capability from fabricating a valid proof. However, `mutation_boundary_crossed` changes only when the capability holder calls `enter_mutation_boundary()` (`:144-150`), and `create_clean_abort_error()` permits proof creation until that voluntary call (`:152-176`). The dispatcher never marks the boundary itself. The reviewed `LiveResolveCanaryAdapter.execute_live_canary_action()` does not accept or use this capability, so the production mutation path is not instrumented by it.

An adapter that received the capability can therefore execute or begin mutation without calling `enter_mutation_boundary()`, then request a valid clean-abort proof. The dispatcher will classify that exception as `ABORTED`, persist the terminal state, notify Oracle, and release the target lock (`:971-985`, `:992-1017`). The HMAC proves possession of a dispatcher-issued object, but does not prove the claimed event occurred before the actual mutation boundary.

**Required remediation:** Couple boundary invalidation to the actual adapter mutation transition through a trusted protocol that the dispatcher can enforce, or disable the `ABORTED`/unlock path until such integration exists and classify adapter exceptions as `IN_DOUBT`. The adapter must not be able to mint a clean-abort proof after mutation by omitting a voluntary notification. Add a regression in which an adapter receives the legitimate capability, crosses/executes its mutation step without calling the voluntary marker, and then attempts a clean-abort proof; it must remain `IN_DOUBT`, with no `ABORTED` receipt and target lock retained.

## Previous findings

The reviewed R4 code addresses the three R3 findings: clean-abort proofs are now HMAC-bound to execution/target/action claims; successful adapter results require a matching action ID or idempotency hash; and the journal checks exact `0700`/`0600` modes on the reviewed node paths. Artifact hashes match the submission. The remaining finding blocks acceptance.

## Decision and safety state

Revision R4 is **not accepted** pending remediation of `DEP03-R4-01`. Do not issue a Gate 6 live-execution workorder or transition readiness based on this submission. Maintain `ready_to_execute = False`; Gate 6 remains `NOT ACCEPTED` and incident-only; Gate G07 and Milestones M1/M2/M3 remain `OPEN`. No live operation, host change, production key/state access, or Resolve access was performed as part of this review.
