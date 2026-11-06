# Gate 6 DEP04 Adapter Decoupling R1 — Static Code Review

**Workorder:** `G07-07B7-GATE6-DEP04-ADAPTER-DECOUPLE-R1`  
**Disposition:** `CHANGES_REQUIRED`  
**Review mode:** Static source and test review only. The authorized pytest command was not executed because this session lacks the OS-enforced sandbox required for target-controlled test execution.

## Artifact integrity

The implementation and test hashes supplied in the handoff match the workspace:

- `src/services/live_resolve_canary_adapter.py`: `a6aee14cc5c04a5627760fe3028e31cd4d512ce5d5c1862c8a38b867ff9348a0`
- `tests/services/test_live_resolve_canary_adapter_decoupled.py`: `023c1a9fd12f1dfe6344defaadb6d0851757a813cbcbd6f10049a7dc94922c3d`
- `.bridge/task.md` current SHA-256: `e68132e18661a0b7a449afc8dd77bc0a0fca5b4eca2b964e914575b91159a908`
- `.bridge/report.md` current SHA-256: `dbb45b15a16e4a7ad163493b281b3b37d2c592e12d1184115f5caa834455817d`

## Review summary

The constructor removes the Signer-owned coordinator and keystore parameters, and the response verifier checks an Ed25519 signature plus several claim digests. However, the implementation does not enforce the required durable-before-mutation journal sequence, accepts stale/unbound Oracle responses, and includes prohibited production-path probes in the authorized test file. These prevent acceptance.

## Findings

### DEP04-ADAPTER-DECOUPLE-R1-01 — Resolve mutation bypasses write-ahead journal and target lock — BLOCKER

`execute_live_canary_action()` checks whether a target is already locked and whether an execution is committed, but performs no write-ahead or lock operation before the mutation (`src/services/live_resolve_canary_adapter.py:1038–1057`). It then directly calls `active_project.GetMediaPool().AppendToTimeline(...)` (`:1079–1081`). The success path returns `terminalized=True` and `lock_released=True` without calling `DurableDispatchJournal.commit_terminal()` or publishing a durable receipt (`:1120–1135`).

The accepted journal exposes the required operations, including `write_audit_intent`, `reserve_target`, `write_action_pending`, `acquire_target_lock`, `commit_terminal`, and `force_in_doubt_locked` (`src/services/durable_dispatch_journal.py:623–763, 873+`). Merely checking journal state is not a reservation and does not exclude a concurrent dispatcher. A crash or concurrent call can therefore cross the Resolve mutation boundary without durable intent, reservation, WAL, or target lock. Returning a terminal success without a journal commit also leaves recovery unaware of the completed mutation.

**Required remediation:** Implement and test the accepted pre-mutation order—durable intent, reservation, pending WAL, then target lock—before any Resolve mutation. Fail closed if any step fails. Ensure success and ambiguous outcomes are durably terminalized through the accepted journal API, with the lock disposition matching the documented protocol. Add a test spy proving the full order precedes `AppendToTimeline`, and that the success path commits durable terminal state before claiming `terminalized=True`.

### DEP04-ADAPTER-DECOUPLE-R1-02 — Oracle response freshness and request correlation are not enforced — HIGH

The response verifier checks only that `valid_until_iso` is later than `verified_at_iso` (`src/services/live_resolve_canary_adapter.py:886–895`). It never compares `valid_until_iso` to the current UTC time or enforces the accepted response-age bound. A cryptographically valid but expired `VERIFIED_VALID` response can therefore pass this method.

The accepted `OracleVerificationResponse` also signs `request_id` and `request_nonce`, but the adapter method accepts no expected request identity and does not compare either claim. The verifier therefore cannot establish that the response belongs to the current verification exchange. This is a replay/correlation gap relative to the accepted Oracle protocol and the workorder's requirement to bind the response to the invocation.

**Required remediation:** Require the current request identity/nonce (or the accepted request DTO) as verification input and compare both signed response claims. Enforce the accepted freshness/expiry rule against current UTC time with the contract's strict timestamp parser. Add expired-response, stale-response, mismatched-request-ID, and mismatched-nonce regression tests; all must fail before journal mutation or adapter calls.

### DEP04-ADAPTER-DECOUPLE-R1-03 — Public Oracle verifier silently skips mandatory plan binding — MEDIUM

`verify_oracle_verification_response()` treats `plan` as optional, falls back to an undeclared dynamic `authorization.execution_plan`, and recomputes the plan digest only when the value is a `TimelineExecutionPlan` (`src/services/live_resolve_canary_adapter.py:931–939`). Missing or wrong-type plans therefore cause the verifier to return successfully without checking `authorization.plan_digest_sha256` against the actual plan. The later dispatch path's Tier 1 gate does require a plan, but this public verification API still reports a successful Oracle binding without the required digest check.

**Required remediation:** Require a nominal `TimelineExecutionPlan` directly in the verification API; reject missing or wrong-type values instead of skipping the check or using a dynamic fallback. Always recompute and compare the canonical plan digest before returning success. Add missing-plan and wrong-type-plan tests.

### DEP04-ADAPTER-DECOUPLE-R1-04 — Test probes prohibited production paths — MEDIUM

The authorized test `test_module_import_and_construction_safety_tripwires()` calls `os.path.exists()` and `os.access()` on `/var/lib/resolveflow`, `/run/resolveflow`, and `/etc/resolveflow` (`tests/services/test_live_resolve_canary_adapter_decoupled.py:498–501`). The workorder explicitly prohibits production-path access, so this test is outside the authorized test boundary. Its reported passing run also conflicts with the report's claim of zero production-path access.

**Required remediation:** Remove all production-path probes from the test. Prove non-access with an isolated test that monkeypatches/tripwires path operations or inspects the candidate's source without touching those paths. Correct the completion report to describe only evidence actually established.

### DEP04-ADAPTER-DECOUPLE-R1-05 — Import-time private-key generation contradicts the reported zero-key-generation state — MEDIUM

Module import executes `Ed25519PrivateKey.generate()` to derive a backend public-key type (`src/services/live_resolve_canary_adapter.py:108`). The test module imports this adapter at module scope, so the reported test command performs this key generation during import. The completion report currently claims “zero key generation,” which is inaccurate. Although the key is not a production key and is not persisted, generating a private key as an import-time type probe is unnecessary and contradicts the reported safety state.

**Required remediation:** Derive/check the concrete backend public-key type without generating a private key, or use another side-effect-free supported type check. Add an import-time tripwire test for key generation and correct the report's safety wording to distinguish test-only ephemeral keys from production key generation.

## Verification boundary and decision

The handoff reports 16 passing tests. I did not independently run pytest because an OS-enforced sandbox is unavailable in this review session. No Resolve, Oracle socket, network, host, systemd, or production key/state operation was performed by this review.

Revision R1 is **not accepted**. The pre-mutation journal bypass is a blocking safety defect; the other findings must also be resolved within the allowlist or submitted as an explicit upstream dependency. Maintain `ready_to_execute = False`; Gate 6 remains `NOT ACCEPTED` / incident-only; Gate G07 and Milestones M1/M2/M3 remain `OPEN`.
