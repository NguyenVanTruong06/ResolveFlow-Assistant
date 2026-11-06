# Gate 6 DEP-03-R2 — Static Code Review

**Workorder:** G07-07B7-GATE6-DEP03-R2 — LiveCanaryDispatchService Continuation  
**Disposition:** `CHANGES_REQUIRED`  
**Review mode:** Static source and artifact-integrity review. The authorized pytest command was not rerun. No daemon, host service, production state, Resolve SDK/API, or live operation was accessed.

## Artifact integrity

All six submitted SHA-256 values match the allowlisted files present in the workspace:

- `src/services/live_canary_dispatch_service.py`: `5da7464251fce4c54925e88520ab7d37a501c957bd4e9a293de392ea6ea113e1`
- `src/services/durable_dispatch_journal.py`: `67d76a84331d18e6425950fda326deb129a6fd52788ccb7d3d7f2ef59d3ec70a`
- `tests/services/test_live_canary_dispatch_service.py`: `033e4b6b97a193c877fad543e6068da9547b0807787c349634af5a6d20a5e4fb`
- `tests/integration/test_live_dispatch_oracle_integration.py`: `406a3783f187d35acb102b36619f53f660bb53ca4cd349cb2e102a24a2219fa0`
- `.bridge/ux-evidence/DEP03/pytest.log`: `c911f36978aa7c442db8fbc5e77e51a108fab4596f23e16dd0f0e051a713c2f9`
- `.bridge/report.md`: `4b3f20154b5227904ccea6e52dc23aa9948eefda04da0f6297c248854b73b556`

The submitted log reports 27 passing tests. This is recorded as submitted evidence; it was not independently rerun in this static review.

## Findings

### DEP03-R2-01 — Oracle verification key and approved target remain caller-overridable — HIGH

`LiveCanaryDispatchService.__init__()` stores `oracle_public_key` without checking its concrete type and accepts caller-provided `expected_target_project_uid` / `expected_target_timeline_uid` overrides (`live_canary_dispatch_service.py:155-173`). Later, the signature gate invokes `.verify()` on that object (`:230-237`) and the target gate compares against the overridable fields (`:265-277`). A duck-typed object with a no-op `verify()` can therefore make a forged response pass the signature check, and changing the expected UIDs weakens the fixed DEP-01 target restriction. The project/timeline names are only checked against the incident-name blacklist, not against the approved names.

**Required remediation:** Normalize and pin an exact backend Ed25519 public key at the trusted configuration boundary; reject custom verifier objects/subclasses and invalid key encodings before dispatch. Remove target UID overrides or reject any value other than the DEP-01 constants, and bind both approved names and UIDs. Add regressions using a no-op verifier object/subclass, alternate expected target UIDs, and mismatched target names; all must fail before adapter invocation.

### DEP03-R2-02 — Plan binding is not recomputed; digest failure falls back to the caller's signed claim — HIGH

`compute_canonical_plan_digest` is imported but never used. In `dispatch_live_canary()`, action digest computation catches every exception and substitutes `auth.action_payload_digest_sha256` (`live_canary_dispatch_service.py:506-519`). This makes an unavailable/failed local recomputation appear to match by construction. The service also never compares a recomputed canonical plan digest with `auth.plan_digest_sha256`, and does not enforce nominal `TrialExecutionAuthorization` / `TimelineExecutionPlan` types before using caller-supplied objects.

**Required remediation:** Require exact/approved nominal DTO types, recompute both plan and action digests without fallback, and compare them with the signed authorization and Oracle response before any journal reservation. Any exception or mismatch must fail closed. Add regression tests for plan-only tampering, plan digest mismatch, wrong DTO types/subclasses, and digest-computation exceptions with zero Oracle-positive escape, zero adapter calls, and no completed receipt.

### DEP03-R2-03 — Oracle verification response omits authorization-digest and proposal binding checks — MEDIUM

`_verify_oracle_verification_response()` verifies the response signature, request ID/nonce, target IDs, action digest, status, and validity window (`live_canary_dispatch_service.py:230-322`), but never checks `authorization_digest_sha256` against a local digest of the exact authorization or checks `resp.proposal_revision == auth.proposal_revision`. These are signed response claims and part of the accepted Oracle response contract; the dispatcher currently accepts a validly signed but incorrectly bound response.

**Required remediation:** Recompute the authorization digest from the exact authorization claims and compare it (constant-time where applicable), and verify proposal revision binding before proceeding. Add signed mismatch regressions that prove zero adapter calls and zero completion receipt.

### DEP03-R2-04 — Adapter result and clean-abort classification do not prove the terminal outcome — HIGH

After `execute_live_canary_action()` returns, every non-`None` value is treated as verified success and used to commit `COMPLETED` (`live_canary_dispatch_service.py:592-599, 643-698`); the returned status/readback/receipt bindings are not validated. Conversely, any exception whose text contains `PRE_DISPATCH_ABORT`, or whose class name merely contains `ZeroMutationProven`, is treated as proof of zero mutation (`:605-614`) and causes `ABORTED` plus target-lock release (`:617-641`). A malformed or substituted adapter result can therefore produce a false terminal receipt, and an exception message can claim a clean abort without proving no mutation occurred.

**Required remediation:** Validate a strict nominal adapter result/receipt with exact `COMPLETED` state and required execution, target, action, and physical-readback bindings before committing success. Recognize clean abort only through a trusted, explicit typed proof contract generated before the mutation boundary; arbitrary exception text/class names must be classified as in-doubt. Add negative tests for `None`, non-dict, failed/in-doubt status, mismatched receipt claims, and forged abort text/class, proving no false `COMPLETED`/`ABORTED` receipt and no unsafe lock release.

### DEP03-R2-05 — Journal filesystem operations are path-based, not descriptor-anchored — HIGH

`DurableDispatchJournal` resolves the supplied path before checking it (`durable_dispatch_journal.py:112-113`), then uses path-based `.exists()`, `.stat()`, `.read_bytes()`, `open()`, `os.replace()`, and `.unlink()` throughout validation, recovery, and persistence (`:158-230, :280-350, :463-501`). It does not retain a verified state-directory descriptor, validate opened file identity/type/owner/mode/link count before mutation, or anchor replacements and marker operations relative to that descriptor. `O_NOFOLLOW` on some leaf opens does not protect intermediate path components; the initial `.resolve()` also erases evidence that the supplied state path was a symlink.

This does not meet Workorder §3.4's descriptor-anchored `O_NOFOLLOW` requirement and leaves path-component replacement windows around ledger, receipt, WAL, and marker operations.

**Required remediation:** Anchor the state directory once with no-follow descriptor traversal and perform all child opens, stats, renames/replacements, unlinks, scans, and directory fsyncs relative to retained descriptors. Validate exact file types, owner/mode policy, inode/link-count constraints before writing or chmod. Preserve single-close and ambiguous-error behavior. Add deterministic component-swap and unsafe-node tests; if the required safe APIs cannot be implemented within the allowlist, stop and report the dependency.

### DEP03-R2-06 — Target lock inode can split during release — HIGH

`release_target_lock()` unlocks and closes the lock descriptor, then unlinks the named `.lock` file (`durable_dispatch_journal.py:486-501`). Another dispatcher can acquire the old inode after the first unlock but before unlink; unlink then removes the name while that process still holds the inode, allowing a third dispatcher to create a new inode at the same path and acquire a second lock. This defeats cross-process target exclusion.

**Required remediation:** Keep a stable lock inode for the lifetime of the state directory and release only the flock/descriptor; do not unlink it during normal release. Add a deterministic multi-process or instrumented inode race regression proving all contenders coordinate on the same inode and at most one can cross the adapter boundary.

## Decision and safety state

DEP-03-R2 is **not accepted** pending remediation of the six findings above. Do not issue a Gate 6 execution workorder or transition readiness based on this submission. After a remediation revision is accepted, issue a separate continuation workorder before proceeding.

Maintain `ready_to_execute = False`; Gate 6 remains `NOT ACCEPTED` and incident-only; Gate G07 and Milestones M1/M2/M3 remain `OPEN`. No live operations, host changes, production key/state access, or Resolve access are authorized by this review.
