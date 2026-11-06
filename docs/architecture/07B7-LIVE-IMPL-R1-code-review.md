# Static Code Review — G07-07B7-LIVE-IMPL Revision R1

**Decision: `CHANGES_REQUIRED`**  
**Review mode:** Static source and handoff review only. No tests, Resolve application, live IPC, dispatch, or network operations were run.

## Reviewed artifacts

| Artifact | Submitted SHA-256 | Review result |
|---|---|---|
| `src/services/live_resolve_canary_adapter.py` | `49f8021fdc7cb08e583054958a7bd969da51047a5ae9a5ad9cf935b5db2a17cd` | Hash matches |
| `tests/services/test_live_resolve_canary_adapter.py` | `da3283cd6d35baa2024b492f949baf45aaea40ab1b9793981dd5c82e9d2782d1` | Hash matches |
| `.bridge/report.md` | `9a51943e3e994a65ebeb651f0f7902c2f2e1beb595c6b3dde45a49f21a723724` | Hash matches |
| Focused test log | `0bb52a4edbdad4cedba84ba0d7646bcf2c8e91f7b0bd892ac13011915fbe3623` | Hash matches; reported 33/33, not rerun |
| Combined test log | `78f1ca4ce145bdff06df9514ff71021ac462b62acd29fd2219139452cb3aa5c0` | Hash matches; reported 202/202, not rerun |

The review checked the R1 source against the prior findings in `07B7-LIVE-IMPL-code-review.md` and the accepted readiness plan, especially its restart crash-boundary and independent-proof requirements. Test counts are recorded as submitted evidence only.

## Findings

### LIVE-IMPL-R1-01 — BLOCKER: Proof publication can overwrite an already consumed record

**Location:** `src/services/live_resolve_canary_adapter.py:2093-2143`

The writer checks for an existing record, then attempts `os.link(tmp_path, dest_path)`. Its exception handler catches every `OSError`, including `EEXIST` from a concurrent or newly appeared destination, and falls back to `os.replace(tmp_path, dest_path)`. That replacement overwrites the existing consume-once record. The check-then-publish race therefore defeats the stated no-overwrite and replay-prevention invariant; the fallback also uses path-based operations rather than the anchored proof directory descriptor.

**Required correction:** Publish the destination atomically with no-replace semantics using names relative to `_proof_dir_fd`. Treat `EEXIST` as `ERR_PROOF_ALREADY_CONSUMED`; never fall back to replacement. Limit any platform fallback to an operation that preserves the same no-replace guarantee.

### LIVE-IMPL-R1-02 — BLOCKER: Restart repair terminalizes from local WAL without required independent evidence

**Location:** `src/services/live_resolve_canary_adapter.py:2001-2072`

At Boundary 4, an `ACTION_COMMITTED` entry is treated as sufficient to append `FINISH(COMPLETED)`, publish a completed receipt, exhaust the ledger, and release the target lock. This branch performs neither the plan-required fresh physical readback nor independent proof verification. At Boundary 5, any `FINISH` is trusted to select a terminal state; in particular, `FINISH(ABORTED)` with `HALTED_ON_ACTION_FAILURE` is completed without loading and verifying the required durable clean proof. The accepted plan explicitly says local WAL alone cannot establish the physical outcome and requires proof/readback checks for these crash boundaries.

**Impact:** After restart, stale or inconsistent local history can cause the coordinator to mark a trial terminal, exhaust its target, and release its lock while the actual Resolve timeline is unverified. An unauthenticated `FINISH` can also bypass the independent-proof gate for a post-dispatch failure.

**Required correction:** Revalidate the complete WAL grammar and identity envelope under the target lock. For committed success, obtain and verify the plan-required fresh physical observation before terminalization. For `HALTED_ON_ACTION_FAILURE`, require the durable proof verifier and all authorization/pending bindings. Any missing, malformed, or inconsistent evidence must leave the receipt/ledger nonterminal and the target locked in doubt.

### LIVE-IMPL-R1-03 — HIGH: Proof verifiers still accept synthesized authorization context

**Location:** `src/services/live_resolve_canary_adapter.py:1332-1373, 1603-1637`

Both `verify_and_consume_proof()` and `verify_durable_proof_record()` declare `authorization` optional. When it is omitted and the coordinator has no bound authorization, each method constructs a duck-typed authorization from `pending_entry`, the raw proof, or the durable record, including fallback identity values. The proof or WAL then supplies claims used to validate itself, rather than being checked against the signed authorization owned by the coordinator. This contradicts the R1 report's claim that Stage 2 authorization is mandatory and the accepted single-authority verification contract. The verifiers also accept an alternate `json.dumps()` HMAC encoding after canonical verification fails (lines 1529-1538 and 1644-1668), so they do not enforce one canonical byte representation.

**Required correction:** Require a validated `TrialExecutionAuthorization` at both verifier boundaries, or resolve it exclusively from a coordinator-owned binding and reject when absent. Do not construct authorization claims from proof/WAL data. Accept only the specified canonical serializer output for each HMAC.

### LIVE-IMPL-R1-04 — BLOCKER: Post-dispatch readback can succeed without proving the media identity

**Location:** `src/services/live_resolve_canary_adapter.py:890-904; 1051-1073`

The readback path checks the item name only when it is truthy, and accepts any mismatching name beginning with `canary_clip`. It checks media path only when both `GetMediaPoolItem()` and its `File Path` result are present. If identity fields are unavailable, matching geometry and item count are enough to continue to `ACTION_COMMITTED` and `COMPLETED`. The dispatched-item ID also falls back to the fabricated value `canary_item_uid_001`. Separately, source-file digest validation is skipped when the expected digest is absent or `os.path.isfile(expected_path)` is false.

**Impact:** The adapter can report successful insertion without an observed item ID or readback proof that the timeline item is the exact authorized source. This leaves the source-binding requirement unenforced on a reachable success branch.

**Required correction:** Require and validate the actual Resolve item identifier, exact media-pool source path, and authorized digest before commit. Missing identity data, a missing/non-regular source file, or any mismatch must take the in-doubt path; remove synthetic IDs and permissive name-prefix acceptance.

### LIVE-IMPL-R1-05 — HIGH: Receipt publication and readback are not fully descriptor-anchored or equal

**Location:** `src/services/live_resolve_canary_adapter.py:2203-2211, 2248-2289, 2298-2332`

The temporary file is opened relative to `receipts_dir_fd`, but publication uses `os.replace(tmp_path, receipt_path)` with path strings, so the replace does not remain anchored to the descriptor that was opened and validated. The readback then checks selected fields individually instead of enforcing the exact schema and full DTO equality claimed by the report. In particular, it does not compare the nested `published_at_iso` field and does not reject extra keys. The receipt may therefore pass readback without matching the complete serialized DTO.

**Required correction:** Use descriptor-relative `os.replace` arguments (`src_dir_fd`/`dst_dir_fd`) with basenames, and perform exact-key schema validation plus complete deserialization and equality against the intended `TrialExecutionReceipt`. Preserve the in-doubt state on any durability or readback failure.

## Decision and gate state

Gate 3 Revision R1 is **not accepted**. Findings R1-01, R1-02, and R1-04 are blockers to consume-once proof persistence, safe recovery, and proof of the dispatched source. Findings R1-03 and R1-05 also leave the authorization and terminal receipt contracts weaker than the accepted plan requires. The submitted passing tests do not close these static findings.

No Resolve runtime or live dispatch was exercised. Preserve `ready_to_execute=False`; Gate G07 and Milestones M1/M2/M3 remain `OPEN`. Gate 4 preflight and any subsequent live execution work remain pending until a revised implementation is reviewed and accepted.
