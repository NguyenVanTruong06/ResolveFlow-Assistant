# Gate 3-I15 Static Code Review — Local-Test Operator Consent Verification & Atomic Challenge Consumption Core

**Decision: CHANGES_REQUIRED**

## Scope and evidence

Reviewed the issued I15 workorder, implementation, tests, completion report, and submitted focused test log. The supplied workorder, source, test, and evidence hashes match the workspace. The report is present and records the requested I15 submission; its current SHA-256 is `9ef8c5c8ebf09f993ef51de3713e927dcb66ed8b6fcfe92bedeb520cb22e0bf4`.

| Artifact | SHA-256 | Result |
|---|---|---|
| `.bridge/task.md` | `2048764a0d9e4f4eb13e6ebcf335644d4ad90c59af2d4cedcfebcf5d7df24e68` | Match |
| `src/services/trial_authorization_signer_service.py` | `7bd4c874418bd242fcf3e8c14ec049c5ff9fee033cf450d5c3d900feb021977e` | Match |
| `tests/services/test_trial_authorization_signer_service.py` | `4620dd218cea0f6075985d497ddf0b78d4c5799552ab56b1de6678fe4b0a6716` | Match |
| `.bridge/ux-evidence/G07-AUTH-SIGNER-G3-I15/pytest.log` | `cf41cee31f5d5234f5cd98d8cf8bfd094d101d2b3959fb08e8397850cf1400ff` | Match |

The supplied log reports **307 passed, 2 warnings in 2.95s**, exit code 0. Tests were not rerun; this is a static review. No Resolve, IPC, network, or live operation was performed.

## Findings

### I15-01 — HIGH: Nonce ledger descriptors are never closed

In `verify_and_consume_challenge_for_local_test()`, the method constructs `SignerConsumedNonceLedger` and calls `consume_once(record_to_append)` (around `src/services/trial_authorization_signer_service.py:4940`), but never calls `ledger.close()` or uses the ledger as a context manager. This occurs on both success and exceptions from `consume_once()`. The ledger owns raw directory and file descriptors; dropping the Python reference does not close those descriptors. Repeated consumption attempts can therefore leak descriptors, and the method cannot satisfy the workorder requirement that successful reporting depend on a successful close path.

**Required correction:** close the ledger on every path after successful construction. Preserve both append and cleanup errors. If append succeeds but close fails, poison/remove the challenge, return no success result, and report the persistence outcome as ambiguous; do not retry close on an ambiguous numeric descriptor. Add tests that assert closure after success and injected append failure, plus close-failure injection after a successful append proving no success is returned and the challenge is poisoned.

### I15-02 — HIGH: Lock-held challenge expiry check ignores monotonic elapsed time

The method captures `start_mono`, and under the registry lock computes `elapsed_mono = self._monotonic_clock() - start_mono` (around line 4934), but never uses that value. The lock-held expiry gate checks only `current_lock_utc > exp_dt`. If the operation is delayed past the challenge TTL while the wall clock moves backward, the UTC comparison can still pass and the method can consume a challenge after its intended lifetime. This also leaves the monotonic check suggested by the captured value unenforced at the last trusted decision point.

**Required correction:** include a monotonic elapsed-time bound in the lock-held gate, calibrated to the challenge's remaining lifetime at operation start, as well as the current UTC expiry comparison. On either expiry condition, purge the active entry and append no ledger record. Add a deterministic test where monotonic time crosses the TTL while the injected UTC clock moves backward or remains within the expiry boundary.

### I15-03 — MEDIUM: Operator public key is not strictly normalized before I11 filesystem access

The public-key gate at `src/services/trial_authorization_signer_service.py:4765` uses `isinstance(operator_public_key, Ed25519PublicKey)`, which accepts custom subclasses. The method then calls I11 and performs filesystem reads before `verify_operator_consent()` eventually applies `_normalize_and_validate_ed25519_public_key()` and rejects a custom subclass. The workorder requires exact normalized key validation before verifier or filesystem access, so malformed nominal input currently crosses that preflight boundary and triggers I11 work before rejection.

**Required correction:** normalize and validate the operator public key at the method's initial input gate using the accepted provider-backed exact-type validation, then pass only the normalized key into consent verification. Add a custom-subclass negative test asserting rejection occurs with zero I11 calls and zero ledger/registry mutation.

## Positive observations

- I11 is invoked in read-only mode and the operator consent verifier receives the same captured UTC instant.
- Revocation checks cover Director, Operator, and preallocated authorization IDs; verifier-result claims are cross-bound to the active I14 context.
- The ledger append is serialized under the authority lock, and reported append exceptions poison the challenge rather than enabling an in-memory retry.
- The implementation remains local-test-only and does not issue authorization, load signer private keys, or dispatch actions.

## Disposition

Fix I15-01 through I15-03 within the existing source/test allowlist and resubmit the focused evidence. Preserve `ready_to_execute=False`, Gate G07 and M1/M2/M3 `OPEN`, and Gate 6 `NOT ACCEPTED` and incident-only. I15 acceptance remains limited to local-test consent verification and durable one-time challenge consumption; it grants no authorization-signing or live-operation authority.
