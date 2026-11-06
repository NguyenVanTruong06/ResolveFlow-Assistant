# Gate 6 Phase 2: Durable Dispatch Journal Receipt/WAL Binding Architecture (Revision R3)

**Workorder:** `G07-07B7-GATE6-PHASE2-RECEIPT-WAL-BINDING-DESIGN-R1` (Remediation Revision R3)  
**Document Status:** `DESIGN_SPECIFICATION_ONLY; NO_IMPLEMENTATION; UNRESOLVED_PREREQUISITE`  
**Classification:** Technical Hardening Architecture & WAL Schema Specification  
**Governing Standard:** DEP-01 R4, DEP-02A/B/C, DEP-03 R5, DEP-04 UPSTREAM R1-R4, Phase 2 Boundary Design R1 R8  
**Execution Context:** `DOCUMENTATION_AND_STATIC_SOURCE_REVIEW_ONLY; NO_IMPLEMENTATION; NO_TESTS; NO_HOST_OR_STATE_ACCESS; NO_SOCKET_OR_NETWORK; NO_RESOLVE; NO_LIVE_OPERATIONS; READY_TO_EXECUTE_FALSE`

---

## 1. Executive Summary & Problem Formulation

### 1.1 Context and Background
In the ResolveFlow-Assistant architecture, the Dispatcher process (`_resolveflow_dispatcher`, UID 902) guarantees crash consistency and execution safety through descriptor-anchored durable records managed by `DurableDispatchJournal` (`src/services/durable_dispatch_journal.py`). 

During the static review of `G07-07B7-GATE6-PHASE2-BOUNDARY-DESIGN-R1` (Findings `PHASE2-BOUNDARY-R5-01`, `PHASE2-BOUNDARY-R6-01`, and `PHASE2-BOUNDARY-R7-01`), a specific crash-consistency gap was identified at **Boundary 5A-1**:
- During normal dispatch execution (`LiveCanaryDispatchService.dispatch_live_canary()` in `src/services/live_canary_dispatch_service.py:1035–1062`), after DaVinci Resolve mutation and readback verification succeed, the Dispatcher calls `journal.commit_terminal()`.
- Inside `commit_terminal()` (`src/services/durable_dispatch_journal.py:763–819`), the following sequential durable operations occur in accepted source:
  1. Append WAL record `ACTION_COMMITTED` to `execution_journal.wal` and fsync (`:786–794`).
  2. Append WAL record `FINISH(status="COMPLETED")` to `execution_journal.wal` and fsync (`:795–804`).
  3. Atomically publish `receipt.json` (mode `0600`) via descriptor-anchored temporary file, fsync, and replace (`:806–809`).
  4. Atomically publish `target_ledger.v2.json` with `status: "EXHAUSTED"`, `terminal_result: "COMPLETED"`, and fsync parent directory (`:811–818`).
- If a process crash or power interruption occurs between Step 3 (receipt publication) and Step 4 (target ledger update), the disk contains:
  - WAL: `FINISH(COMPLETED)` committed.
  - Receipt: `receipt.json` durable.
  - Target ledger: `target_ledger.v2.json` remains in pre-terminal state `ACTIVE`.
  - Notification state: wire transmission has not been attempted (unrecorded boundary).

### 1.2 The Root Cause of Unbound Local Hash Inference
Under the accepted schema version 1 (`schema_version: 1`), WAL records store metadata but contain **no receipt digest**:
```json
{"schema_version":1,"seq":2,"record_type":"FINISH","status":"COMPLETED","execution_id":"E1","timestamp_iso":"2026-11-05T10:00:00Z"}
```
Consequently, on restart recovery:
1. `DurableDispatchJournal._recover_from_files()` (`:518–589`) inspects `execution_journal.wal` and `target_ledger.v2.json`. It sees that `E1` is completed, removing it from `unmatched_pending`.
2. However, the journal has **no durable cryptographic binding** connecting `receipt.json` to the WAL `FINISH` record.
3. If recovery were to compute `sha256(receipt.json)` locally, there is no value in the WAL to verify it against. A local hash proves only that bytes were hashed; it cannot prove that `receipt.json` was the specific receipt committed by the crashed process for `E1`.
4. Therefore, under accepted schema v1, **automated restart repair is prohibited**, and an `ACTIVE` target ledger with terminal WAL requires fail-closed offline administrative reconciliation.

### 1.3 Objective of this Design
This document defines an optional schema revision (Schema Version 2) that binds the exact persisted bytes of `receipt.json` to the terminal WAL record. It establishes the byte sequence, hash algorithm, WAL fields, durability order, crash/recovery matrix, operational boundaries, and provides an authoritative governance recommendation on whether to implement this revision for Gate 6.

### 1.4 Remediation History (Revision R2 Summary)
Revision R2 resolved the initial three review findings (`PHASE2-RECEIPT-WAL-R1-01`, `PHASE2-RECEIPT-WAL-R1-02`, `PHASE2-RECEIPT-WAL-R1-03`) by:
- Grounding notification behavior in source reality (`live_canary_dispatch_service.py:656–688` has zero intent logging; wire boundary is ambiguous requiring administrative reconciliation).
- Introducing Crash Boundary 1B for separately fsynced `ACTION_COMMITTED` and `FINISH` records.
- Formally defining schemas for all three terminal branches (`COMPLETED`, `ABORTED`, `IN_DOUBT`) and mixed v1/v2 WAL validation.

### 1.5 Remediation History (Revision R3 Updates)
Revision R3 resolves both findings issued in `docs/architecture/07B7-GATE6-PHASE2-RECEIPT-WAL-BINDING-R2-code-review.md`:
- **`PHASE2-RECEIPT-WAL-R2-01` (HIGH — Action Payload Digest in Receipt Schema):**
  - Clarified that in accepted source (`live_canary_dispatch_service.py:1037–1044`, `:984–991`, `:1190–1197` and `live_resolve_canary_adapter.py:1348–1361`), `receipt.json` does NOT contain `action_payload_digest_sha256` either at top level or under `adapter_result`.
  - Formulated the exact receipt schema upgrade required for proposed Schema v2: adding top-level `action_payload_digest_sha256: str`, populated from `auth.action_payload_digest_sha256`.
  - Defined exact equality checks in recovery across `receipt.json`, `ACTION_PENDING`, and terminal records, and documented that current source cannot satisfy this binding without modifying `LiveCanaryDispatchService`.
- **`PHASE2-RECEIPT-WAL-R2-02` (HIGH — Regular-File Writes Are Not All-or-Nothing Primitives):**
  - Explicitly removed all claims that regular-file batch writes or `write()` syscalls provide atomic all-or-nothing persistence across power/kernel crashes.
  - Defined fail-closed recovery for every prefix and torn-tail outcome:
    1. Torn/corrupt trailing line: caught by `parse_strict_json()`, raises `DispatchJournalCorruptionError(ERR_CORRUPT_WAL)`, halting startup fail-closed.
    2. Durable `ACTION_COMMITTED` prefix without `FINISH`: tracked via `unfinalized_committed` in recovery, latching `IN_DOUBT_LOCKED` and publishing tombstone marker; zero automated repair or notification.
    3. Durable `ACTION_COMMITTED` with torn `FINISH` tail: fails closed on parse error (`ERR_CORRUPT_WAL`).
  - Specified an alternative Single Unified Terminal Record (`TERMINAL_COMMIT`) architecture and showed that torn-record recovery remains mandatory under all approaches.

---

## 2. Exact Receipt Binding Specification

### 2.1 Receipt Filename and Storage Location
- **Accepted Filename:** `receipt.json` (strictly defined by `DurableDispatchJournal.RECEIPT_FILE_NAME = "receipt.json"` in `src/services/durable_dispatch_journal.py:191`).
- **File System Path:** `/var/lib/resolveflow/dispatcher/state/receipt.json`.
- **Directory Mode & Ownership:** mode `0700`, owner `_resolveflow_dispatcher:_resolveflow_dispatcher` (UID `902:902`).
- **File Mode & Ownership:** mode `0600`, owner `_resolveflow_dispatcher:_resolveflow_dispatcher` (UID `902:902`).
- **Access Constraint:** Accessible exclusively via open directory file descriptor `self._state_dir_fd` with `O_NOFOLLOW` and `O_CLOEXEC`.

### 2.2 Exact Canonical Byte Sequence Hashed
To eliminate parsing ambiguity, whitespace variance, key-ordering divergence, or float serialization discrepancies, the receipt digest is computed strictly over the canonical UTF-8 byte stream that is actually written to disk:
1. The receipt dictionary `receipt_data` is serialized using Python's standard library `json.dumps()` with strict canonical sorting and compact separators:
   ```python
   canonical_receipt_bytes = json.dumps(
       receipt_data,
       sort_keys=True,
       separators=(",", ":"),
       ensure_ascii=True,
   ).encode("utf-8")
   ```
2. The exact byte sequence passed to the digest algorithm is byte-identical to the payload passed to `_atomic_publish_file(self._state_dir_fd, self.RECEIPT_FILE_NAME, canonical_receipt_bytes)`.
3. Zero whitespace, zero trailing newlines, and zero carriage returns are permitted beyond what strict canonical JSON emits.

### 2.3 Digest Algorithm and Encoding
- **Cryptographic Hash Function:** SHA-256 (`hashlib.sha256`).
- **Encoding:** Exactly 64 lowercase hexadecimal ASCII characters (`[0-9a-f]{64}`), obtained via `.hexdigest()`.
- **Field Name in WAL:** `receipt_digest_sha256`.

### 2.4 Semantic Bindings and Action Digest Verification Invariant

#### 2.4.1 Current Accepted Source Reality (`src/services/`)
In the accepted codebase:
- `LiveCanaryDispatchService.dispatch_live_canary()` (`src/services/live_canary_dispatch_service.py:1037–1044`) constructs `receipt_data` containing only:
  `execution_id`, `status: "COMPLETED"`, `adapter_result`, `target_project_uid`, `target_timeline_uid`, and `timestamp_iso`.
- The `adapter_result` dictionary returned by `LiveResolveCanaryAdapter` (`src/services/live_resolve_canary_adapter.py:1348–1361`) contains `action_id` and `action_idempotency_hash`, but does **NOT** contain `action_payload_digest_sha256`.
- The `receipt_data` constructed for `ABORTED` (`live_canary_dispatch_service.py:984–991` and `:1190–1197`) contains `execution_id`, `status: "ABORTED"`, `reason`, target UIDs, and timestamp, with **zero** action payload digest.
- **Consequence:** Under accepted source, existing receipt files do NOT contain `action_payload_digest_sha256`. A local receipt hash binds whatever bytes are in `receipt.json`, but cannot verify an action payload claim that does not exist in the receipt structure.

#### 2.4.2 Proposed Schema v2 Receipt Schema Upgrade (Future Implementation Prerequisite)
To enforce cryptographic action-to-receipt binding in Schema v2, the receipt schema emitted by `LiveCanaryDispatchService` must be explicitly upgraded to include `action_payload_digest_sha256`:
1. **Schema Field:** Top-level field `action_payload_digest_sha256: str` (strictly 64 lowercase hexadecimal characters).
2. **Trusted Source Value:** Populated directly from `auth.action_payload_digest_sha256` in `TrialExecutionAuthorization`. (This value was already verified against the canonical plan action and Signer Oracle authorization during admission).
3. **Upgraded Receipt Structure for `COMPLETED`:**
   ```json
   {
     "execution_id": "ba6a6775-8ec4-41a4-bf2f-704de902e68d",
     "status": "COMPLETED",
     "action_payload_digest_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
     "adapter_result": { ... },
     "target_project_uid": "4fd4272d-f0bc-445c-9490-5723cd6831c2",
     "target_timeline_uid": "ba6a6775-8ec4-41a4-bf2f-704de902e68d",
     "timestamp_iso": "2026-11-05T10:00:00Z"
   }
   ```
4. **Upgraded Receipt Structure for `ABORTED` (when `receipt_data` is provided):**
   ```json
   {
     "execution_id": "ba6a6775-8ec4-41a4-bf2f-704de902e68d",
     "status": "ABORTED",
     "action_payload_digest_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
     "reason": "Pre-execution abort requested",
     "target_project_uid": "4fd4272d-f0bc-445c-9490-5723cd6831c2",
     "target_timeline_uid": "ba6a6775-8ec4-41a4-bf2f-704de902e68d",
     "timestamp_iso": "2026-11-05T10:00:00Z"
   }
   ```

#### 2.4.3 Semantic Invariant Verification Rules in Recovery (Schema v2)
Upon restart recovery under Schema v2, the recovery logic must verify all of the following:
1. `execution_id`: `receipt.json["execution_id"] == FINISH.execution_id == ACTION_PENDING.execution_id`.
2. `status`: `receipt.json["status"] == FINISH.status`.
3. `target_project_uid`: `receipt.json["target_project_uid"] == FINISH.target_project_uid == ACTION_PENDING.target_project_uid`.
4. `target_timeline_uid`: `receipt.json["target_timeline_uid"] == FINISH.target_timeline_uid == ACTION_PENDING.target_timeline_uid`.
5. `action_payload_digest_sha256`:
   - For `COMPLETED` branch: `receipt.json["action_payload_digest_sha256"]` must strictly equal `ACTION_PENDING.action_payload_digest_sha256`.
   - For `ABORTED` branch with receipt: `receipt.json["action_payload_digest_sha256"]` must strictly equal `ACTION_PENDING.action_payload_digest_sha256`.
   - If `FINISH.receipt_digest_sha256` is `null` (degraded abort without receipt): recovery asserts that `receipt.json` does not exist.
6. `receipt_digest_sha256`: `hashlib.sha256(canonical_receipt_bytes).hexdigest() == FINISH.receipt_digest_sha256`.

### 2.5 What the Digest Proves vs. What It Does Not Prove
- **What the Digest Proves:**
  - **Crash-Consistent Atomic Linkage:** It proves that the on-disk `receipt.json` file is byte-identical to the receipt generated and approved by the Dispatcher process before `commit_terminal()` wrote its terminal WAL entry.
  - **Truncation and Corruption Detection:** It detects partial writes, torn file replacement, or bit rot in `receipt.json` occurring across crashes.
  - **Protection Against Unrelated Artifacts:** It prevents recovery from accidentally mistaking an old, stale, or misplaced `receipt.json` file from a prior execution for the receipt of the current execution.
- **What the Digest Does NOT Prove:**
  - **Not a Digital Signature:** SHA-256 is an unkeyed cryptographic digest, NOT an asymmetric digital signature (such as Ed25519).
  - **No Protection Against Host DAC Compromise:** If an attacker or compromised process obtains write access to the Dispatcher state directory (`UID 902`), they can simultaneously overwrite `execution_journal.wal` and `receipt.json` with matching SHA-256 hashes. Security against modification rests strictly on Linux POSIX DAC (mode `0700/0600`, UID `902`).
  - **No Protection Against Malicious Dispatcher:** The digest does not prove that DaVinci Resolve actually performed the mutation correctly; that guarantee is provided by readback verification and audit logs, not the journal hash.

---

## 3. WAL Schema & Versioning Architecture Across All Terminal Branches

### 3.1 Schema Version Increment
To maintain strict backward compatibility and prevent corrupting existing v1 ledgers, the WAL schema version must be explicitly incremented:
- Current Accepted Version: `schema_version: 1`
- Proposed Binding Version: `schema_version: 2`

### 3.2 Record-by-Record Schema Specifications for Terminal Branches

#### 3.2.1 Branch 1: `COMPLETED` (Verified Success)
The `COMPLETED` path executes when DaVinci Resolve mutation and readback verification succeed (`live_canary_dispatch_service.py:1035–1050`). In this branch, `receipt_data` is mandatory.

The WAL sequence comprises two separately recorded events:
1. **`ACTION_COMMITTED` (Schema v2):**
   ```json
   {
     "schema_version": 2,
     "seq": 3,
     "record_type": "ACTION_COMMITTED",
     "execution_id": "ba6a6775-8ec4-41a4-bf2f-704de902e68d",
     "timestamp_iso": "2026-11-05T10:00:00Z"
   }
   ```
   - Mandatory fields: `schema_version` (int = 2), `seq` (int > 0), `record_type` ("ACTION_COMMITTED"), `execution_id` (36-char UUID), `timestamp_iso` (ISO-8601 UTC).
   - Semantic role: Marks that Resolve mutation succeeded and readback verified; execution has crossed the point of no return.

2. **`FINISH` (Schema v2, status `COMPLETED`):**
   ```json
   {
     "schema_version": 2,
     "seq": 4,
     "record_type": "FINISH",
     "status": "COMPLETED",
     "execution_id": "ba6a6775-8ec4-41a4-bf2f-704de902e68d",
     "receipt_digest_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
     "target_project_uid": "4fd4272d-f0bc-445c-9490-5723cd6831c2",
     "target_timeline_uid": "ba6a6775-8ec4-41a4-bf2f-704de902e68d",
     "timestamp_iso": "2026-11-05T10:00:00Z"
   }
   ```
   - Mandatory fields: `schema_version` (int = 2), `seq` (int > 0), `record_type` ("FINISH"), `status` ("COMPLETED"), `execution_id` (UUID), `receipt_digest_sha256` (64 lowercase hex chars), `target_project_uid` (string), `target_timeline_uid` (string), `timestamp_iso` (ISO-8601 UTC).
   - Artifact requirement: `receipt.json` MUST be published atomically with content matching `receipt_digest_sha256`.
   - Target ledger: `target_ledger.v2.json` published with `status: "EXHAUSTED"`, `terminal_result: "COMPLETED"`.

#### 3.2.2 Branch 2: `ABORTED` (Clean Abort Before Mutation)
The `ABORTED` path executes when dispatch is cleanly aborted before mutation (e.g., `abort_unexecuted_dispatch()` in `live_canary_dispatch_service.py:1081–1225` or pre-mutation check failure).

The WAL sequence comprises two separately recorded events:
1. **`ACTION_ABORTED` (Schema v2):**
   ```json
   {
     "schema_version": 2,
     "seq": 3,
     "record_type": "ACTION_ABORTED",
     "execution_id": "ba6a6775-8ec4-41a4-bf2f-704de902e68d",
     "reason": "Pre-execution abort requested",
     "timestamp_iso": "2026-11-05T10:00:00Z"
   }
   ```
   - Mandatory fields: `schema_version` (int = 2), `seq` (int > 0), `record_type` ("ACTION_ABORTED"), `execution_id` (UUID), `timestamp_iso` (ISO-8601 UTC).
   - Optional/Permitted fields: `reason` (string, describes abort cause).
   - Semantic role: Marks that execution was safely aborted without mutating DaVinci Resolve.

2. **`FINISH` (Schema v2, status `ABORTED`):**
   ```json
   {
     "schema_version": 2,
     "seq": 4,
     "record_type": "FINISH",
     "status": "ABORTED",
     "execution_id": "ba6a6775-8ec4-41a4-bf2f-704de902e68d",
     "receipt_digest_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
     "target_project_uid": "4fd4272d-f0bc-445c-9490-5723cd6831c2",
     "target_timeline_uid": "ba6a6775-8ec4-41a4-bf2f-704de902e68d",
     "timestamp_iso": "2026-11-05T10:00:00Z"
   }
   ```
   - Handling of `receipt_data` on `ABORTED`:
     - **With Receipt Data (Standard Clean Abort):** When `commit_terminal(..., status="ABORTED", receipt_data=...)` receives `receipt_data`, `receipt_digest_sha256` is strictly required and contains the 64-hex SHA-256 of `receipt.json`.
     - **Without Receipt Data (Degraded Abort):** If `receipt_data` is `None` / omitted, `receipt_digest_sha256` must be explicitly serialized as `null`. In this case, recovery verifies that no `receipt.json` exists for this execution.
   - Target ledger: `target_ledger.v2.json` published with `status: "EXHAUSTED"`, `terminal_result: "ABORTED"`.

#### 3.2.3 Branch 3: `IN_DOUBT` (Ambiguous Mutation or Infrastructure Fault)
In the accepted codebase (`src/services/durable_dispatch_journal.py:846–870`), the `IN_DOUBT` branch has a distinct, specialized behavior:
1. **NO WAL FINISH RECORD IS WRITTEN:** The source explicitly documents:
   ```python
   elif terminal_state == "IN_DOUBT":
       # Branch 3: In-Doubt
       # a) NO WAL FINISH IS WRITTEN!
   ```
2. **Target Ledger Locked:** `commit_terminal()` immediately publishes `target_ledger.v2.json` with:
   - `status: "IN_DOUBT_LOCKED"`
   - `terminal_result: "IN_DOUBT"`
   - `in_doubt_at_iso: now_iso`
3. **Persistent Tombstone Marker:** `commit_terminal()` atomically publishes `{target_hash}.in_doubt` into `locks_dir_fd`:
   ```json
   {
     "target_hash": "...",
     "target_project_uid": "...",
     "target_timeline_uid": "...",
     "execution_id": "...",
     "reason": "TERMINAL_IN_DOUBT_CLASSIFICATION",
     "timestamp_iso": "..."
   }
   ```
4. **Zero Receipt Publication:** No `receipt.json` is generated or published.
5. **Architectural Disposition:** **`IN_DOUBT` is strictly OUTSIDE the receipt-binding schema.** Because no WAL `FINISH` record is ever written and no receipt exists, Schema v2 receipt digest rules do not apply to `IN_DOUBT`. Recovery enforces locking exclusively via `target_ledger.v2.json` (`status: "IN_DOUBT_LOCKED"`) and `{target_hash}.in_doubt` marker files.

### 3.3 Strict JSON Parsing and Validation Policy
1. Parsing must use strict parser `parse_strict_json()` (`src/utils/strict_json.py`):
   - Prohibit trailing commas.
   - Prohibit comments, NaN, Infinity, and raw multi-line strings.
   - Prohibit duplicate keys within the same JSON object.
2. Field Presence and Type Enforcement:
   - All specified mandatory fields are strictly required. Absence of any mandatory field raises `DispatchJournalCorruptionError`.
   - Any unknown, unrecognized, or extraneous field raises `DispatchJournalCorruptionError`.
3. Sequence and Monotonicity Enforcement:
   - Each WAL line must have `seq` strictly equal to `len(self._wal_records) + 1`. Any gap, duplicate, or regression raises `DispatchJournalCorruptionError`.
4. Exactly One Terminal `FINISH` per Execution:
   - A second `FINISH` record for an already-finished `execution_id` raises `DispatchJournalCorruptionError` (`ERR_DUPLICATE_FINISH_RECORD`).

### 3.4 Mixed Schema Version (v1 / v2) Validation and Progression Policy
To support journals during migration or reading historical WAL records:
1. **Valid Version Values:** Every WAL record must have `schema_version` in `{1, 2}`. Any other value raises `DispatchJournalCorruptionError(ERR_UNSUPPORTED_SCHEMA_VERSION)`.
2. **Non-Regressive Progression:** Once a record with `schema_version: 2` is encountered in `execution_journal.wal`, all subsequent records in the log MUST have `schema_version: 2`. A v1 record appearing after a v2 record raises `DispatchJournalCorruptionError(ERR_SCHEMA_VERSION_REGRESSION)`.
3. **Validation by Record Version:**
   - If `schema_version == 1`: validate against legacy v1 rules (`receipt_digest_sha256` is absent; mandatory fields are `schema_version`, `seq`, `record_type`, `execution_id`, `timestamp_iso`, and `status` for `FINISH`).
   - If `schema_version == 2`: validate against v2 rules (`receipt_digest_sha256`, `target_project_uid`, `target_timeline_uid` are mandatory for `FINISH`).
4. **Strict Prohibition on Backfilling:** Recovery must **NEVER** silently infer, backfill, or synthesize a SHA-256 digest for a legacy v1 record.
5. **Fail-Closed Rule for Legacy Inconsistencies:** If recovery encounters a v1 `FINISH` record alongside an un-exhausted (`ACTIVE`) target ledger, automated ledger repair is strictly prohibited. The target must be latched into fail-closed exclusion, and the discrepancy must be routed to offline administrative reconciliation.

---

## 4. Durability Write Ordering, Crash Boundaries & Operational Realities

### 4.1 Current Accepted Source Behavior vs. Proposed Future Protocol
A rigorous safety analysis requires strictly separating **what current source actually does** from **what a future protocol revision would propose**:

#### 4.1.1 Current Accepted Source Behavior (`src/services/`):
1. **Separate Fsyncs for WAL Records:** `commit_terminal()` calls `_append_wal_record()` separately for `ACTION_COMMITTED` (or `ACTION_ABORTED`) and `FINISH` (`src/services/durable_dispatch_journal.py:786–804`). Each call writes the line, calls `os.fsync(wal_fd)`, closes `wal_fd`, and calls `os.fsync(state_dir_fd)` (`:402–420`).
   - **Crash Boundary Result:** There is an explicit, durable crash boundary between `ACTION_COMMITTED` fsync and `FINISH` fsync.
2. **Zero Notification-Intent Persistence:** `LiveCanaryDispatchService._notify_oracle_terminal()` (`src/services/live_canary_dispatch_service.py:656–688`) directly constructs `term_req` and calls `self._send_oracle_request()`. It writes **zero** records to `execution.log`, `execution_journal.wal`, or disk before wire transmission.
   - **Crash Boundary Result:** A crash immediately before `_send_oracle_request()`, during socket transport, or after Oracle processes the request but before response reception is **completely unrecorded on disk**.
   - **Classification:** The wire notification outcome across this unrecorded boundary is **strictly ambiguous**. The absence of an intent record in current source **CANNOT** prove "zero send". Any crash across this boundary requires **offline administrative reconciliation** with Signer Oracle logs; automated resend is strictly prohibited.

#### 4.1.2 Proposed Future Protocol (Schema v2 Hardening Extension):
If Schema v2 is implemented with comprehensive crash-resilience:
1. **Regular-File Writes Are Not All-or-Nothing Primitives:**
   - Under POSIX and Linux storage controller semantics, appending multiple JSON lines (or bytes) to a regular file via `write()` does **NOT** provide transactional "all-or-nothing" atomicity. If a crash or power cut occurs during the write or prior to complete fsync, the disk may contain a prefix of bytes or a torn trailing line.
   - Therefore, multi-record batch append cannot eliminate intermediate states by fiat. A crash during append will manifest either as a torn/corrupt trailing line or as an unfinalized record prefix. Recovery must explicitly handle every prefix and torn-tail outcome (defined in §4.3).
2. **Durable Notification Intent & Acknowledgment Logging:** To eliminate ambiguity across the wire boundary, `LiveCanaryDispatchService` must persist intent and ack records:
   - Before transmission: append and fsync `NOTIFY_ORACLE_INTENT` to `execution.log`.
   - After verified response: append and fsync `NOTIFY_ORACLE_ACK` to `execution.log`.
   - Recovery semantics under this proposed extension:
     - Fully terminalized local state + zero `NOTIFY_ORACLE_INTENT` -> proven non-send, exactly one first send permitted.
     - `NOTIFY_ORACLE_INTENT` present without `NOTIFY_ORACLE_ACK` -> ambiguous send, resend strictly prohibited, latch `IN_DOUBT_LOCKED` / administrative reconciliation.

### 4.2 Sequence of Operations: Current vs. Proposed

```mermaid
sequenceDiagram
    autonumber
    participant D as Dispatcher Logic
    participant WAL as execution_journal.wal
    participant RCPT as receipt.json
    participant TGT as target_ledger.v2.json
    participant DIR as state/ Directory FD
    participant LOG as execution.log
    participant ORCL as Signer Oracle (AF_UNIX)

    Note over D: --- PHASE A: Pre-Commit Serialization & Hashing ---
    Note over D: 1. Serialize canonical receipt_bytes (with action_payload_digest_sha256)
    Note over D: 2. Compute receipt_digest_sha256

    Note over D: --- PHASE B: WAL Persistence ---
    alt Current Source Per-Record Append
        D->>WAL: 3a. Append ACTION_COMMITTED & fsync(wal_fd) & fsync(dir_fd)
        Note over WAL: [CRASH BOUNDARY 1B: ACTION_COMMITTED durable, no FINISH]
        D->>WAL: 3b. Append FINISH(digest) & fsync(wal_fd) & fsync(dir_fd)
    else Proposed Single Unified Terminal Record
        D->>WAL: 3. Append TERMINAL_COMMIT(digest, action_digest) & fsync
        Note over WAL: [CRASH: If torn, fails closed via ERR_CORRUPT_WAL; if absent, IN_DOUBT]
    end

    Note over D: --- PHASE C: Atomic Publication of receipt.json ---
    D->>RCPT: 4. Write canonical bytes to tmp_receipt & fdatasync
    D->>RCPT: 5. os.replace(tmp_receipt, "receipt.json")
    D->>DIR: 6. os.fsync(dir_fd)
    Note over RCPT: [CRASH BOUNDARY 5A-1: receipt.json durable, target_ledger ACTIVE]

    Note over D: --- PHASE D: Atomic Publication of target_ledger.v2.json ---
    D->>TGT: 7. Write EXHAUSTED state to tmp_ledger & fdatasync
    D->>TGT: 8. os.replace(tmp_ledger, "target_ledger.v2.json")
    D->>DIR: 9. os.fsync(dir_fd)
    Note over TGT: target_ledger EXHAUSTED durably published

    Note over D: --- PHASE E: Terminal Notification Boundary ---
    alt Current Source Reality (Unrecorded Boundary)
        Note over D,ORCL: Zero intent/ack logging on disk!
        D->>ORCL: 10. Send OracleTerminalNotificationRequest
        Note over D,ORCL: [CRASH BOUNDARY 5B: Unrecorded send; ambiguous outcome]
        ORCL-->>D: 11. OracleTerminalNotificationResponse(ACCEPTED)
    else Proposed Future Intent/Ack Protocol
        D->>LOG: 10a. Append NOTIFY_ORACLE_INTENT & fsync(dir_fd)
        D->>ORCL: 10b. Send OracleTerminalNotificationRequest
        ORCL-->>D: 11a. OracleTerminalNotificationResponse(ACCEPTED)
        D->>LOG: 11b. Append NOTIFY_ORACLE_ACK & fsync(dir_fd)
    end

    Note over D: 12. Release target lock / flock
```

### 4.3 Crash Boundary 1B & Regular-File Append Failure Analysis
Under accepted source, `_append_wal_record()` executes an `fsync` after `ACTION_COMMITTED` and before `FINISH`. Furthermore, regular-file writes do not possess all-or-nothing crash atomicity.

#### 4.3.1 Exhaustive Durable Prefix and Torn-Tail States:
A crash during or between WAL terminal record appends can leave exactly three possible durable states on disk:

1. **State 1: Torn or Partial Line at Tail (Torn WAL Tail):**
   - Disk contains valid preceding records, followed by partial bytes (e.g., truncated JSON string, unclosed braces).
   - **Recovery Semantics:** `_recover_from_files()` invokes `parse_strict_json(sline)`. Strict JSON parsing fails on partial syntax, raising `DispatchJournalCorruptionError(ERR_CORRUPT_WAL)`.
   - **Resolution:** The journal immediately poisons itself. Service startup **halts fail-closed**. Recovery **NEVER** silently discards torn lines, synthesizes missing bytes, or proceeds with partial state.

2. **State 2: Clean `ACTION_COMMITTED` Durable, `FINISH` Completely Absent (Crash Boundary 1B):**
   - Disk contains syntactically complete `ACTION_COMMITTED`, but `FINISH` was not appended or not fsynced.
   - **Vulnerability in Current Recovery (`_recover_from_files()` :548–556):**
     ```python
     if rec_type == "ACTION_PENDING":
         unmatched_pending[exec_id] = rec
     elif rec_type in ("ACTION_COMMITTED", "ACTION_ABORTED", "FINISH"):
         unmatched_pending.pop(exec_id, None)
     ```
     `ACTION_COMMITTED` pops `exec_id` from `unmatched_pending`, causing `unmatched_pending` to be empty. While `target_ledger.v2.json` remains `ACTIVE` (preventing new reservations), the execution is left unfinalized without a `FINISH` record or receipt.
   - **Fail-Closed Resolution for State 2:**
     - Recovery tracks `unfinalized_committed: Dict[str, Dict[str, Any]]`.
     - `ACTION_COMMITTED` adds `exec_id` to `unfinalized_committed`; a valid `FINISH` record removes it.
     - If any execution remains in `unfinalized_committed` at EOF:
       - Recovery **MUST NOT** synthesize a `FINISH` record.
       - Recovery **MUST NOT** repair `target_ledger.v2.json` to `EXHAUSTED`.
       - Recovery **MUST NOT** authorize Oracle notification.
       - Recovery **MUST** latch the target into `IN_DOUBT_LOCKED`, publish `{target_hash}.in_doubt` tombstone (`reason: "CRASH_RECOVERY_UNFINISHED_COMMITTED_ACTION"`), and require offline administrative reconciliation.

3. **State 3: Clean `ACTION_COMMITTED` Durable, Followed by Torn `FINISH` Tail:**
   - Disk contains complete `ACTION_COMMITTED`, followed by partial bytes of the `FINISH` record.
   - **Recovery Semantics:** Handled identically to State 1: `parse_strict_json()` fails on the torn `FINISH` bytes and raises `DispatchJournalCorruptionError(ERR_CORRUPT_WAL)`, halting startup fail-closed. Recovery does not inspect preceding records to guess terminal status.

#### 4.3.2 Alternative Architecture: Single Unified Terminal Record (`TERMINAL_COMMIT`)
Rather than writing two separate records (`ACTION_COMMITTED` then `FINISH`), a future Schema v2 can unify terminal commitment into a **single self-contained WAL record**:
```json
{
  "schema_version": 2,
  "seq": 3,
  "record_type": "TERMINAL_COMMIT",
  "status": "COMPLETED",
  "execution_id": "ba6a6775-8ec4-41a4-bf2f-704de902e68d",
  "receipt_digest_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  "action_payload_digest_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  "target_project_uid": "4fd4272d-f0bc-445c-9490-5723cd6831c2",
  "target_timeline_uid": "ba6a6775-8ec4-41a4-bf2f-704de902e68d",
  "timestamp_iso": "2026-11-05T10:00:00Z"
}
```
- **Durability Behavior:** Appended and fsynced in a single operation.
- **Crash Outcomes:**
  - If a crash occurs before `TERMINAL_COMMIT` bytes reach disk: WAL contains only `ACTION_PENDING`, which recovery latches into `IN_DOUBT_LOCKED`.
  - If a crash occurs during write: tail bytes are torn, causing `ERR_CORRUPT_WAL` and halting startup.
  - If completely fsynced: terminal commitment and receipt digest are simultaneously durable.
- In all designs, all-or-nothing atomicity is achieved by **fail-closed rejection of partial/torn records**, never by assuming regular-file `write()` atomicity.

---

## 5. Comprehensive Crash/Recovery Matrix

The following matrix enumerates every crash scenario across durable steps in Schema v2, distinguishing current accepted source behavior from proposed future extensions:

| Crash Scenario | Signer Oracle Ledger State | Dispatcher Journal & WAL State | Kernel flock vs. Durable Target Exclusion | DaVinci Resolve Mutation Status | Exact Risk Assessment & Determination | Required Authorization & Restart Action |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Crash 1A: Crash before any WAL commit fsync** | `is_execution_verified: True`, `is_terminal: False` | WAL has unmatched `ACTION_PENDING`. No `ACTION_COMMITTED` or `FINISH`. Target ledger `ACTIVE`. | Kernel flock released on crash. Target is **PERMANENTLY LOGICALLY IN_DOUBT_LOCKED** (`is_target_locked_or_in_doubt() == True`). | Ambiguous. Mutation boundary was crossed or in progress. | **Unmatched Action Intent / Permanent In-Doubt:** In-memory capability lost on crash. Fail-closed recovery treats unmatched `ACTION_PENDING` as `IN_DOUBT_LOCKED`. Reconstructs `.in_doubt` tombstone. | Target permanently locked in doubt. Administrative reconciliation required with physical operator inspection. Zero automated retry. |
| **Crash 1B: Crash after `ACTION_COMMITTED` fsync, before `FINISH` fsync** *(Finding R1-02, R2-02)* | `is_execution_verified: True`, `is_terminal: False` | WAL has `ACTION_PENDING` and complete `ACTION_COMMITTED`. Zero `FINISH`. Zero `receipt.json`. Target ledger `ACTIVE`. | Kernel flock released on crash. Target remains durably locked (`ACTIVE`). | Mutation succeeded, but terminalization was interrupted before receipt binding. | **Unfinished Committed Transaction (Prefix State 2):** WAL records mutation commitment without a terminal record, receipt, or digest. Automated repair is prohibited. | **Fail-Closed In-Doubt Latching:** Recovery detects unfinalized committed execution, transitions target to `IN_DOUBT_LOCKED`, and publishes `.in_doubt` marker. Strictly requires offline administrative reconciliation. Zero automated notification. |
| **Crash 2: Crash after WAL `FINISH` fsync, before `receipt.json` atomic replacement** | `is_execution_verified: True`, `is_terminal: False` | WAL has `FINISH(receipt_digest_sha256)`. `receipt.json` does NOT exist. Target ledger is `ACTIVE`. | Kernel flock released on crash. Target remains durably excluded (`status: "ACTIVE"`). | Mutation succeeded and committed in WAL, but receipt file publication failed or was interrupted. | **Missing Durable Receipt Proof:** WAL asserts completion with specific digest, but required receipt file is absent. Automated ledger repair cannot verify non-existent receipt bytes. | **Latch `IN_DOUBT_LOCKED`:** Create `.in_doubt` tombstone (`reason: "MISSING_RECEIPT_AFTER_WAL_FINISH"`). Prohibit automated repair. Strictly requires offline administrative reconciliation. Zero automated send. |
| **Crash 3: Boundary 5A-1 (Crash after `receipt.json` publish, before target ledger `EXHAUSTED` publish)** | `is_execution_verified: True`, `is_terminal: False` | WAL has `FINISH(receipt_digest_sha256)`. `receipt.json` exists on disk. Target ledger still `ACTIVE`. | Kernel flock released on crash. Target remains durably excluded (`status: "ACTIVE"`). | Mutation succeeded; timeline contains verified clip. | **Pre-Terminal Target Ledger with Verified Cryptographic Receipt Binding:** Recovery reads `receipt.json`, verifies `sha256(receipt_bytes) == wal.receipt_digest_sha256`, and verifies execution and target UIDs and action digest. | **Safe Automated Ledger Repair (Schema v2):** (1) Recovery repairs `target_ledger.v2.json` to `status: "EXHAUSTED"`, `terminal_result: "COMPLETED"`, and fsyncs directory; (2) Next action depends on notification protocol (see Crashes 7 & 8). |
| **Crash 4: Corrupt or Mismatched `receipt.json` at Boundary 5A-1** | `is_execution_verified: True`, `is_terminal: False` | WAL has `FINISH(receipt_digest_sha256)`. `receipt.json` exists, but `sha256(bytes) != wal.receipt_digest_sha256`. Target ledger `ACTIVE`. | Kernel flock released on crash. Target remains durably excluded. | Timeline contains clip, but on-disk receipt is corrupted, truncated, or tampered. | **Receipt Digest Mismatch:** Fails cryptographic integrity check. The file on disk does not match the journal's committed transaction. | **Latch `IN_DOUBT_LOCKED`:** Create `.in_doubt` tombstone (`reason: "RECEIPT_DIGEST_MISMATCH"`). Prohibit automated repair. Strictly requires offline administrative reconciliation. |
| **Crash 5: Semantic Binding Mismatch (Wrong execution, target UIDs, or action digest)** *(Finding R2-01)* | `is_execution_verified: True`, `is_terminal: False` | WAL has `FINISH`. `receipt.json` exists, but `execution_id`, target UIDs, or `action_payload_digest_sha256` do not match WAL. | Kernel flock released on crash. Target remains durably excluded. | Receipt does not belong to the recorded execution context. | **Semantic Context Mismatch:** Receipt claims do not bind to the journal transaction. | **Latch `IN_DOUBT_LOCKED`:** Create `.in_doubt` tombstone (`reason: "RECEIPT_SEMANTIC_BINDING_MISMATCH"`). Prohibit automated repair. Requires administrative reconciliation. |
| **Crash 6: Torn WAL Tail at Recovery** *(Finding R2-02)* | Unknown | WAL contains partial line, invalid JSON, or syntax corruption at end of file (Prefix State 1 or 3). | Kernel flock released on crash. Journal enters poisoned state. | Indeterminate. | **Corrupted Write-Ahead Log:** Crash during write left un-fsynced partial bytes. | **Fail-Closed Halting:** `_recover_from_files()` raises `DispatchJournalCorruptionError(ERR_CORRUPT_WAL)`. Zero automated recovery. Halts service startup. |
| **Crash 7: Boundary 5B under CURRENT SOURCE (Unrecorded Notification Boundary)** *(Finding R1-01)* | `is_execution_verified: True`, `is_terminal: Unknown` | WAL has `FINISH`. `receipt.json` is durable. Target ledger is `EXHAUSTED`. Zero notification logging on disk. | Kernel flock released on crash. Target is **durably exhausted** (`status: "EXHAUSTED"`). | Mutation succeeded; timeline contains verified clip. | **Ambiguous Wire Transmission:** Current source has zero durable notification intent/ack records. Impossible to know locally whether request was sent or received. | **Administrative Reconciliation Only:** Automated send/resend strictly PROHIBITED. Target remains permanently `EXHAUSTED`. Operator must check Signer Oracle logs to verify notification receipt. |
| **Crash 8A: Boundary 5B under PROPOSED FUTURE PROTOCOL (Pre-Intent Crash)** *(Finding R1-01)* | `is_execution_verified: True`, `is_terminal: False` | Local state terminalized (`EXHAUSTED`). Proposed audit log has zero `NOTIFY_ORACLE_INTENT`. | Target is durably `EXHAUSTED`. | Mutation succeeded; timeline contains verified clip. | **Definite Non-Send:** Durable log proves notification transmission was not yet initiated. | **Exactly ONE First Send Permitted:** Under proposed protocol, service transmits initial `OracleTerminalNotificationRequest(COMPLETED)`. |
| **Crash 8B: Boundary 5B under PROPOSED FUTURE PROTOCOL (Post-Intent Crash)** *(Finding R1-01)* | `is_execution_verified: True`, `is_terminal: Unknown` | Local state terminalized. Proposed audit log has `NOTIFY_ORACLE_INTENT` fsynced, but zero `NOTIFY_ORACLE_ACK`. | Target is durably `EXHAUSTED`. | Mutation succeeded; timeline contains verified clip. | **Ambiguous Wire Send:** Notification was initiated; receipt by Oracle unconfirmed. Resend risk violates DEP-03. | **NO RESEND / NO RETRY:** Latch target into `IN_DOUBT_LOCKED`. Prohibit automated retry. Strictly requires offline administrative reconciliation with Oracle. |
| **Crash 9: Legacy Schema v1 Record Encountered** *(Finding R1-03)* | `is_execution_verified: True`, `is_terminal: False` | WAL has `schema_version: 1` `FINISH` record (no `receipt_digest_sha256`). Target ledger is `ACTIVE`. | Kernel flock released on crash. Target remains durably excluded. | Succeeded in v1 runtime, but cannot be cryptographically verified. | **Unbound Legacy Transaction:** Cannot bind receipt to WAL without digest. Silent backfill is strictly prohibited. | **Administrative Reconciliation Only:** Automated repair prohibited. Requires operator verification of receipt and manual ledger transition to `EXHAUSTED`. |
| **Crash 10: Crash during `IN_DOUBT` Terminalization** *(Finding R1-03)* | `is_execution_verified: True`, `is_terminal: Unknown` | No WAL FINISH. Target ledger is `IN_DOUBT_LOCKED` and/or `.in_doubt` marker exists. Zero receipt. | Kernel flock released on crash. Target is **PERMANENTLY LOGICALLY IN_DOUBT_LOCKED**. | Timeline state indeterminate or aborted. | **Permanently Latched In-Doubt:** Target is durably locked by ledger and tombstone marker. Outside receipt binding. | **Fail-Closed Lock:** Service startup loads `.in_doubt` marker and refuses any dispatch to this target (`ERR_TARGET_NOT_AVAILABLE`). Administrative reset required. |

---

## 6. Trust & Operational Boundary

### 6.1 Descriptor-Relative Validation and POSIX DAC
All interactions with the durable state directory must maintain strict descriptor-relative safety to prevent symlink attacks, directory traversal, or time-of-check to time-of-use (TOCTOU) race conditions:
1. Directory file descriptors (`self._state_dir_fd`, `self._locks_dir_fd`) are opened at initialization with flags `os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC`.
2. All operations must use descriptor-relative syscalls (`openat`, `unlinkat`, `renameat2` / `os.replace`, `fstat`).
3. Every opened descriptor must be validated via `self._validate_node_mode_and_ownership(fd, name)`:
   - Must be a regular file (`stat.S_ISREG`).
   - Mode must be strictly `0600` (`st_mode & 0o777 == 0o600`).
   - Owner UID must strictly equal current process UID (`902`).
   - Owner GID must strictly equal current process GID (`902`).

### 6.2 Atomic Publication Guarantees (The 4-Phase Protocol)
To ensure that partial writes never corrupt durable state:
- **Phase 1 (Creation):** Open temporary file in the same directory using `dir_fd` with `O_CREAT | O_EXCL | O_WRONLY | O_CLOEXEC`.
- **Phase 2 (Payload & Flush):** Write complete byte buffer, issue `os.fdatasync(tmp_fd)`, and close `tmp_fd`.
- **Phase 3 (Atomic Replacement):** Call `os.replace(tmp_name, target_name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)`. Under POSIX semantics, `rename`/`replace` within the same filesystem is atomic; readers either see the old file or the new file, never a partial state.
- **Phase 4 (Directory Flush):** Call `os.fsync(dir_fd)` to commit directory entry changes to the physical storage controller.

### 6.3 One-Shot Target Semantics and Replay Prevention
A fundamental invariant of ResolveFlow canary execution is that targets are strictly **one-shot**:
1. When `target_ledger.v2.json` reaches `status: "EXHAUSTED"`, the target project and timeline enter permanent terminal exhaustion.
2. `DurableDispatchJournal.is_target_locked_or_in_doubt()` explicitly includes `"EXHAUSTED"` in its exclusion check (`src/services/durable_dispatch_journal.py:614`):
   ```python
   if status in ("IN_DOUBT_LOCKED", "ACTIVE", "RESERVED", "EXHAUSTED"):
       return True
   ```
3. `DurableDispatchJournal.reserve_target()` explicitly rejects any target in `"EXHAUSTED"` state (`:663`):
   ```python
   if prev_status in ("RESERVED", "ACTIVE", "EXHAUSTED", "IN_DOUBT_LOCKED"):
       raise DispatchJournalSecurityError(f"ERR_TARGET_NOT_AVAILABLE: Target status is {prev_status}")
   ```
4. Therefore, terminalizing a target to `EXHAUSTED` releases the active process's kernel `flock`, but **permanently preserves durable logical target exclusion**. The target cannot be re-dispatched by any automated process unless an administrative target reset is explicitly performed.

---

## 7. Decision, Governance Recommendation & Follow-on Scope

### 7.1 Architectural Tradeoff Analysis

| Evaluation Dimension | Option A: Retain Administrative-Only Reconciliation (Current Accepted Architecture) | Option B: Implement Schema v2 with Receipt-WAL Binding & Protocol Extensions |
| :--- | :--- | :--- |
| **Codebase Mutation** | **Zero mutation.** Codebase remains completely frozen and identical to accepted DEP-04 decoupled state. | **Extensive mutation:** modifies `DurableDispatchJournal` (WAL v2, recovery guards), `LiveCanaryDispatchService` (receipt schema upgrade with action digest, intent logging), `LiveResolveCanaryAdapter`, and test suites. |
| **Regression Risk** | **Zero risk.** No changes to existing journal serialization, parsing, crash handling, receipt formats, or recovery logic. | **High risk:** Changes core WAL and receipt structures; requires complex prefix/torn-tail recovery tracking; risk of introducing subtle edge-case recovery bugs. |
| **Canary Suitability** | **Ideal for single-action canary:** Gate 6 is an isolated, supervised test with human operator physically present. | Over-engineered for a single-action test; benefits continuous unattended background batch editing. |
| **Probability of Occurrence** | Extremely low. Boundary 5A-1 requires process crash specifically within a ~2ms window between two disk writes. | Addresses the 2ms crash window, but introduces new complexity across all dispatch finalizations. |
| **Operational Overhead** | In the unlikely event of a Boundary 5A-1 crash, operator inspects timeline and manually terminalizes target. | Fully automated recovery for Boundary 5A-1, but still requires administrative reconciliation for Crash 1B, Crash 2, Crash 4, and Crash 7. |

### 7.2 Governance Recommendation
**RECOMMENDATION: RETAIN OPTION A (ADMINISTRATIVE-ONLY RECONCILIATION) FOR GATE 6.**

**Detailed Justification:**
1. **Scope Proportionality:** Gate 6 is a strictly bounded, single-action canary dispatch executed against an empty test project (`RF_TRIAL_AEC8F0A7A365D5F5`) and empty timeline (`RF_TRIAL_TL_AEC8F0A7A365D5F5`). A human operator is physically present and supervising execution on `DISPLAY=:1`.
2. **Current Source Reality:** Current accepted source lacks both the action payload digest in `receipt.json` and durable notification intent logging in `LiveCanaryDispatchService`. Furthermore, regular-file writes do not provide crash-atomic batch durability, necessitating complex prefix and torn-tail state machines.
3. **Fail-Closed Safety:** Under the current accepted architecture (`Phase 2 Boundary Design R1 R8`), crashes across both Boundary 5A-1 and Boundary 5B fail closed safely:
   - Target remains durably locked (`status: "ACTIVE"` or `"EXHAUSTED"`).
   - Replay is prevented (`is_target_locked_or_in_doubt() == True`).
   - Zero unauthorized mutation can occur.
   - The operator inspects `receipt.json`, DaVinci Resolve, and Signer Oracle logs, verifies the inserted clip, and performs administrative reconciliation.
4. **Minimizing Codebase Churn:** Modifying the core WAL schema, receipt data contracts, transaction ordering, and notification protocols across multiple services at this stage introduces unnecessary regression risks into the hardened dispatch foundation.
5. **Production Deferral:** Schema Version 2, receipt action digest upgrades, and durable notification intent/ack logging are recommended for deferral to when ResolveFlow transitions from single-canary trials to continuous, unattended background batch editing (post-Gate 6).

### 7.3 Follow-on Implementation Specifications (If Option B Is Selected)
If Tech Lead Codex decides to proceed with implementing Schema Version 2 despite the recommendation above, the following follow-on workorder must be formulated:
1. **Workorder Identifier:** `G07-07B7-GATE6-DEP04-JOURNAL-V2-BINDING-R1`.
2. **Authorized File Allowlist:**
   - `src/services/durable_dispatch_journal.py` (WAL v2, unified `TERMINAL_COMMIT` or prefix recovery guards).
   - `src/services/live_canary_dispatch_service.py` (receipt schema upgrade with `action_payload_digest_sha256`, intent/ack persistence).
   - `tests/services/test_durable_dispatch_journal.py` (unit tests).
   - `tests/services/test_live_canary_dispatch_service.py` (receipt binding & notification intent tests).
   - `.bridge/task.md` (status tracking).
   - `.bridge/report.md` (completion report).
3. **Required Test Coverage:**
   - Test v2 serialization: verify `receipt_digest_sha256` present in terminal records.
   - Test receipt schema upgrade: verify `action_payload_digest_sha256` present in `receipt.json` and strictly verified.
   - Test Crash Boundary 1B: simulate crash after `ACTION_COMMITTED`; verify recovery latches `IN_DOUBT_LOCKED`.
   - Test torn WAL tail: simulate partial trailing bytes; verify recovery raises `ERR_CORRUPT_WAL` and halts startup.
   - Test Crash Boundary 5A-1: simulate crash after receipt publish; verify recovery repairs `target_ledger.v2.json` to `EXHAUSTED`.
   - Test digest mismatch: tamper with `receipt.json`; verify recovery latches `IN_DOUBT_LOCKED`.
   - Test legacy v1 compatibility: verify v1 records are parsed without error and routed to administrative reconciliation without digest backfill.

### 7.4 Confirmation of Critical Safety Invariants
- `ready_to_execute = False` is strictly maintained across all configuration files, services, and candidate runners.
- Gate 6 strictly remains `NOT ACCEPTED` and incident-only.
- Gate G07, Milestones M1, M2, and M3 strictly remain `OPEN`.
- Node `WO5 (Live Canary Execution Directive)` in the decision DAG remains completely disconnected with strictly zero incoming edges. Acceptance of this design does NOT authorize or schedule Gate 6 live canary execution.
- Target project `RF_TRIAL_AEC8F0A7A365D5F5` and target timeline `RF_TRIAL_TL_AEC8F0A7A365D5F5` on `DISPLAY=:1` remain untouched and empty.
- Zero Resolve API/SDK call, zero IPC dispatch, zero private key generation, zero host provisioning, zero systemd changes, zero network traffic.
