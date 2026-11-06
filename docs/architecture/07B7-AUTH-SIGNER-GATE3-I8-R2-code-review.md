# G07-AUTH-SIGNER-GATE3-I8 Revision R2 — Static Code Review

**Decision: CHANGES_REQUIRED**  
**Scope:** Digest-Bound Durable Revocation Epoch Anchor Core  
**Reviewed:** 2026-11-03

## Reviewed artifacts

- Workorder: `.bridge/task.md` — SHA-256 `8ec5284372d014ba31a0dcc98a4b86e1e3dc7b906e286f15bb6e530f70943758`.
- Implementation: `src/services/trial_authorization_signer_service.py` — SHA-256 `2e6bb0bb290668a52d2006691943409141200f7fe9c70c97fca6949a66c3eaf9`.
- Tests: `tests/services/test_trial_authorization_signer_service.py` — SHA-256 `91912eb1ef88143b21828b69493269c95dbb9b53db97d9276aac20c3258a6058`.
- Submitted test log: `.bridge/ux-evidence/G07-AUTH-SIGNER-G3-I8/pytest.log` — SHA-256 `6fde71370fb6c1c5dcdc7bf6ca4aa07dd98bd603fd963ec5ca8dfb0bec07bd11`.
- Completion report: `.bridge/report.md` — SHA-256 at submission `4aa8592a8c9848cf0b6d2806d8be089f4781ceb95173be25d3e159b698f0fb4a`.
- Prior review: `docs/architecture/07B7-AUTH-SIGNER-GATE3-I8-R1-code-review.md` — SHA-256 `b32afd22f8765fba0524d58c4b558ddf0344ce48e8350436aff5d8a8565ad0cb`.

Submitted hashes match the workspace. The submitted log reports 181 passed, 0 failed, exit code 0. Tests were not rerun during this static review.

## R1 finding disposition

- `I8-R1-01` (FIFO blocking): **RESOLVED** — lock and anchor opens now include `O_NONBLOCK`, followed by regular-file checks.
- `I8-R1-02` (bounded stable read): **RESOLVED** — the reader caps input at 257 bytes, requires the read length to equal the initial `st_size`, probes for overflow, and compares device/inode/size/mtime after the read.
- `I8-R1-03` (zero-byte write): **RESOLVED** — `_write_all_bytes()` raises `ERR_SHORT_WRITE` on a zero or negative write while retrying `EINTR`.
- `I8-R1-04` (cleanup error handling): **PARTIALLY RESOLVED** — structured signer errors retain cleanup errors and temp unlink failures are surfaced. Raw write/fsync `OSError` can still remain the primary exception while cleanup failures are not attached.
- `I8-R1-05` (test-only directory boundary): **PARTIALLY RESOLVED** — a realpath-under-temp policy rejects ordinary non-temp paths and direct symlink final components, but validation and opening are separate pathname operations.

## Findings

### I8-R2-01 — A caller can forge a “verified” result and advance the anchor without signature verification — BLOCKER

`RevocationManifestVerificationResult` is a publicly constructible frozen dataclass. The anchor helper checks the exact class, `verified is True`, manifest type, and that the digest matches a recomputation of the manifest claims. It does not establish that `verify_signed_revocation_manifest()` produced the result or that the claims were signed. A caller can construct or `dataclasses.replace()` a result with a chosen valid manifest, `verified=True`, and the matching canonical digest; a higher epoch is then durably accepted without Ed25519 verification. Digest equality proves consistency of the supplied claims, not authenticity.

**Required change:** Make the verified-result boundary provenance-bearing and reject caller-constructed/replaced results, or change the anchor API to perform verification itself from raw manifest bytes using an explicitly trusted verification capability. Do not treat a public boolean plus a recomputable digest as proof of signature verification. Add a negative test that constructs a fully self-consistent forged higher-epoch result and verifies the anchor remains unchanged.

### I8-R2-02 — Temporary-directory policy remains subject to path-resolution race — HIGH

The code resolves `anchor_directory` with `realpath()` and checks that result is under the temporary root, then opens the original caller-supplied path separately. `O_NOFOLLOW` protects only the final path component; it does not anchor ancestor traversal to the path validated by `realpath()`. If an ancestor is replaced or redirected between validation and `open()`, the opened descriptor may refer outside the validated temp location. The current tests cover `/home` and a literal production-looking child path, but not an alias/race or a descriptor identity check.

**Required change:** Open and validate through a descriptor-relative no-follow traversal rooted at the already-open temporary-directory descriptor, or otherwise ensure the opened directory descriptor is proven to be the same object and remains within the allowed test root. Add a safe alias/race fixture without accessing production locations.

### I8-R2-03 — Raw persistence errors can escape unstructured and lose cleanup failures — HIGH

`_write_all_bytes()` propagates non-`EINTR` `OSError`, and file `os.fsync()` calls in bootstrap/advance are not wrapped in a stable `SignerLedgerError` code. If cleanup then also fails, the outer `finally` attaches cleanup errors only when `primary_exc` is already a `SignerLedgerError`; for a raw `OSError`, the cleanup failures are silently omitted and the raw exception escapes. This violates the workorder's structured durability-error and dual-error-preservation requirements.

**Required change:** Convert write and file-fsync failures to stable `SignerLedgerDurabilityError` codes while retaining the original exception as `primary_error`/`__cause__`; attach all cleanup failures regardless of the primary exception type. Add combined injected write/fsync plus close/unlock failure cases.

### I8-R2-04 — Required cross-process lock guarantee lacks an independent-process test — HIGH

The added concurrency test uses `ThreadPoolExecutor` in one process. It does not exercise `fcntl.flock()` contention across independent processes, even though the workorder requires independent-process serialization and re-read-under-lock behavior. Thread serialization is also supplied by the module-level `RLock`, so that test cannot demonstrate the OS lock's cross-process role.

**Required change:** Add a deterministic multiprocessing test using separate processes against one temporary anchor directory, proving no lost update or rollback and that each process observes the on-disk anchor under the OS lock. Keep it local and bounded.

## Review conclusion and safety boundary

R2 closes three R1 findings and improves the other two, but the forged verification result can bypass the signature trust boundary and the persistence/error and cross-process requirements remain incomplete. I8 R2 is therefore **not accepted** pending correction within the existing two-file allowlist.

No tests were run during this review. No Resolve, IPC, network, key, or production filesystem operations were performed. Preserve `ready_to_execute=False`; Gate G07 and Milestones M1/M2/M3 remain `OPEN`; Gate 3 remains incomplete; Gate 6 remains `NOT ACCEPTED` and incident-only. This review does not authorize production trust-root loading, signer service wiring, authorization issuance, or live operations.
