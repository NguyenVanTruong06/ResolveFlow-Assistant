# G07-AUTH-SIGNER-GATE3-I6 Revision R1 — Static Code Review

**Decision: CHANGES_REQUIRED**  
**Scope:** Durable Signer Nonce Ledger Core  
**Reviewed:** 2026-11-03

## Reviewed artifacts

- Workorder: `.bridge/task.md` — SHA-256 `011a5b0149e653947c46ba89b940a770aad9b7cdf9383370706a1d601c526d32`.
- Implementation: `src/services/trial_authorization_signer_service.py` — SHA-256 `08a73071c14be9824c4f4f0837e5cbc4f2c45bb42c3e3b1744873320c52d00a2`.
- Tests: `tests/services/test_trial_authorization_signer_service.py` — SHA-256 `6d445922b9cde77f3a07b8ef829f1a4983c34cf2e02b55049a0d4f1fe66f68bc`.
- Submitted test log: `.bridge/ux-evidence/G07-AUTH-SIGNER-G3-I6/pytest.log` — SHA-256 `c603714ca9bbe4737cededb656d47bb669dd6efb9a9c0226141bebcc894540ce`.
- Completion report: `.bridge/report.md` — SHA-256 at submission `ca195b93cbeec07198164f6452a8cde598ef48f6d2dd2332d8511746dceda46d`.

Submitted hashes match the workspace. The submitted log records 150 passed, 0 failed, exit code 0. Tests were not rerun during this static review.

## Findings

### I6-R1-01 — Caller-controlled filename can escape the anchored ledger directory — BLOCKER

`SignerConsumedNonceLedger.__init__()` accepts `filename` without validating it as a single basename (`:956-968`). It later passes that string to `os.open(..., dir_fd=self._dir_fd)` (`:1020-1034`). An absolute path ignores `dir_fd`; a value containing `../` can traverse outside the supplied temporary directory. This defeats the workorder's descriptor-anchoring and local-temp-only boundary and can create/open an unintended persistent file.

**Required correction:** Remove the filename override or require an exact safe basename with no separators, `.`/`..`, NUL, or absolute-path form before any filesystem operation. Add negative tests proving absolute and traversal names are rejected without touching outside paths.

### I6-R1-02 — Constructor silently changes an existing directory's permissions — HIGH

When `create` is true, the constructor checks `exists()`, calls `makedirs()`, then unconditionally `chmod(..., 0700)` (`:980-986`). This silently repairs a pre-existing directory, contrary to the explicit “Never repair permissions silently” requirement. The later descriptor check rejects group/world permissions but does not require the owner permission bits to equal `0700` (`:1007-1018`).

**Required correction:** Do not chmod an existing directory. Create a missing directory with an atomic, race-safe operation and verify its resulting mode/owner; reject an existing directory that does not satisfy the exact required mode and ownership. Add tests that the constructor rejects a pre-existing non-0700 directory and leaves its mode unchanged, plus exact-mode checks for owner permission bits.

### I6-R1-03 — Ledger creation is not exclusive/atomic as specified — HIGH

The ledger is opened with `O_CREAT` but without `O_EXCL` (`:1025-1034`), and the preceding `islink()` check is a path-based precheck (`:1020-1023`). The workorder specifically requires atomic exclusive creation when absent. Concurrent initializers therefore do not follow the specified create-or-open-existing protocol, and the precheck does not establish the creation event or its ownership of the created entry.

**Required correction:** Attempt descriptor-relative `O_CREAT | O_EXCL` creation first; on `EEXIST`, reopen the existing entry with `O_NOFOLLOW` and validate it through `fstat`. Track whether this instance created the file so directory fsync is tied to the actual creation. Add concurrent-initialization coverage and verify no pre-existing bytes are replaced.

### I6-R1-04 — Nonce record subclasses can override canonical serialization — MEDIUM

Both `consume_once()` and `contains()` accept `isinstance(record, ConsumedAuthorizationNonceRecord)` (`:1200-1205`, `:1279-1284`). A subclass can override `canonical_bytes()` or field access behavior; `consume_once()` then trusts that virtual method (`:1237-1238`) while collision checks read fields from the same caller object. This conflicts with the workorder's exact built-in type-gate requirement for the record contract.

**Required correction:** Require `type(record) is ConsumedAuthorizationNonceRecord` at both public entry points, or serialize validated primitive fields through a non-overridable module-level serializer. Add a subclass overriding `canonical_bytes()` negative test and confirm zero file mutation.

### I6-R1-05 — Unlock cleanup error masks the primary operation error — HIGH

In `consume_once()`, a collision, corruption, write, or fsync exception may be raised in the protected body, but the `finally` block replaces it with `ERR_LEDGER_UNLOCK_FAILED` if unlock also fails (`:1217-1277`). The workorder requires cleanup failures to be exposed without hiding the primary error. The same pattern exists in `contains()` (`:1296-1314`) for scan errors.

**Required correction:** Preserve the primary exception and attach the unlock failure as structured cleanup context (including an exception note/cause or a documented aggregate error). Keep the corrupted/closed latch. Add fault-injection tests for primary-operation failure plus unlock failure, asserting that both failures remain observable.

## Positive review notes

- The main append path uses a process-local lock before `flock(LOCK_EX)`, re-scans under the OS lock, rejects the three specified collision dimensions, retries `EINTR` during writes, and fsyncs before returning (`:1207-1266`).
- Startup scan rejects malformed records and missing final newline without truncation; close disowns numeric descriptors before calling `os.close()` and records `CLOSE_OUTCOME_UNKNOWN` (`:1125-1198`, `:1316-1346`).
- The submitted tests cover round-trip, collisions, concurrent instances, malformed/torn records, selected filesystem modes/symlinks, closed-state behavior, and several injected I/O failures. They do not cover the filename escape, exclusive create race, permission non-repair/exact mode, record subclass, or preservation of simultaneous primary and unlock failures identified above.

## Disposition and safety boundary

Do not mark I6 R1 accepted until `I6-R1-01` through `I6-R1-05` are corrected and reviewed within the existing two-file allowlist. This review concerns the bounded local persistence primitive; it does not assert that a runtime authorization flow is wired or reachable.

`ready_to_execute=False`; Gate G07 and Milestones M1/M2/M3 remain `OPEN`. Gate 6 remains `NOT ACCEPTED` and incident-only. No tests, Resolve, IPC, key access, or network operations were performed during this review.
