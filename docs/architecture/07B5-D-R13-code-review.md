# G07-07B5-D-R13 — Codex Review

**Decision: CHANGES_REQUIRED**  
**Reviewed:** 2026-11-02  
**Submitted report SHA-256:** `066c59d7638d719e8a6d6c496803637888ca2bcca5d56e39c26135ac7f6a88cb`  
**Design SHA-256:** `77074844628d5246828d82ab16c91ebb27f8fa30e8e451d98a1411eb8cff2cbe`

## Review scope

Static review only. All four submitted reference hashes (R12 review, R13 task, report and design) were recomputed and match disk. Compared the proposed FSA and payload validation against its pseudocode and the existing journal contracts. No tests, app, Resolve, IPC, FusionScript, or network activity was run.

## Findings

### R13-01 — Trial identity is not validated on every journal record (blocking)

The prose requires the five-field trial identity envelope in every record. In the proposed validator, the `START` branch compares only `authorization_id` and `execution_id`; it does not compare `target_project_uid`, `target_timeline_uid`, or `plan_idempotency_key` to the authorization. FINISH branches do not validate the identity envelope. The post-FINISH `RECOVERY_NOTE` path validates only `note` and immediately continues, bypassing identity and record-specific checks entirely. A WAL can therefore contain contradictory target/plan identity while still reaching a terminal classification.

**Required:** apply one shared strict identity-envelope validator to every parsed record before any stage transition, including START, every FINISH and every RECOVERY_NOTE. Compare all five fields against both authorization and execution context; define exact stage-specific `action_params` allowlists.

### R13-02 — Rollback failure is classified as an ordinary terminal failure (blocking)

`ROLLBACK_FAILED -> FINISH(FAILED)` transitions to `FINISHED_FAILED`, which returns `TERMINAL_RECONCILED_FAILED`. A failed compensation does not establish that the inserted clip was removed; the target may still be mutated or uncertain. Treating this like an ordinary `ACTION_FAILED` terminal could let downstream recovery release the target or report a clean failure despite a possible surviving clip.

**Required:** classify rollback failure as a distinct unresolved/in-doubt result that retains target exclusion and requires physical/operator reconciliation, unless the design supplies authoritative evidence proving the resulting timeline state. Do not map it to the same result as an action that failed before a committed insertion.

### R13-03 — Signed source path is not bound to the WAL action path (blocking)

`validate_action_payload_projection()` validates `clip_path` only as a nonblank string. It checks the digest and frame geometry against authorization, but does not require `clip_path == auth.source_file_path` (or compare a canonical path identity bound by the authorization). Thus a different source path can pass while carrying the authorized digest value.

**Required:** bind the exact authorized source path and its approved canonicalization policy to `clip_path` for every action-stage projection, alongside the source digest/stat identity. Reject path mismatch before any terminal classification.

### R13-04 — Trusted directory identity is optional at the absence boundary (blocking)

The pseudocode declares `trusted_journals_dev` and `trusted_journals_ino` optional and performs each comparison only when the value is non-`None`. If either/both are omitted, an opened replacement directory can be treated as the trusted anchor; an `ENOENT` child lookup there can then produce `PENDING_ABSENT_CONFIRMED`.

**Required:** absence classification must require a previously established, owned directory identity or an already-held trusted directory descriptor. Missing, malformed or partial trust identity must fail closed; never silently skip the device/inode binding.

## Decision and next workorder

R13 resolves the prior rollback-order contradiction, adds required payload fields and uses a directory-relative lookup, but the identity, rollback-failure, source-path and optional-anchor gaps prevent acceptance. R13 is **not accepted**. Do not start G07-07B6-I. Preserve `ready_to_execute=False`, the mock-only tripwire, and G07/M1/M2/M3 OPEN.

The next bounded task is **G07-07B5-D-R14 — Per-Record Identity, Source Binding & In-Doubt Rollback Authority**. The DESIGN ONLY workorder is in `.bridge/task.md`.
