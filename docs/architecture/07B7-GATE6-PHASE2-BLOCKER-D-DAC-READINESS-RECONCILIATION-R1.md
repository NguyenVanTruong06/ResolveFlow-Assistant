# Gate 6 Phase 2 Blocker D DAC Readiness & Trust-Root Alignment Review

**Document ID:** `07B7-GATE6-PHASE2-BLOCKER-D-DAC-READINESS-RECONCILIATION-R1`  
**Revision:** `R2`  
**Status:** `ASSESSMENT_DOCUMENT_ONLY`  
**Parent Workorder:** `G07-07B7-GATE6-PHASE2-BLOCKER-D-DAC-READINESS-RECONCILIATION-R1`  
**Date:** 2026-11-05  
**Governing Standards:** DEP-01 R4 Amended V3 (`07B7-GATE6-DEP01-live-dispatch-oracle-design.md`), DEP-02A R7 (`07B7-GATE6-DEP02A-R7-code-review.md`), DEP-02A-DAC-REALIGN (`07B7-GATE6-DEP02A-DAC-REALIGN-code-review.md`), DEP-02B R6, DEP-02C R2, DEP-03 R5, DEP-04 UPSTREAM R1-R4, Gate 4 Authority Design R10 (`07B7-GATE6-PHASE2-GATE4-AUTHORITY-DESIGN-R1.md`), Gate 4 Custody Design R4, Gate 4 Custody Decision Record R3, Gate 5 Release Authority Design R4 (`07B7-GATE6-PHASE2-GATE5-RELEASE-AUTHORITY-DESIGN-R1.md`)  
**Operating Mode:** `DOCUMENTATION_AND_STATIC_SOURCE_REVIEW_ONLY; NO_IMPLEMENTATION; NO_TESTS; NO_SCRIPT_EXECUTION_OR_DRY_RUN; NO_HOST_OR_STATE_ACCESS; NO_SYSTEMD_LIFECYCLE; NO_SOCKET_OR_NETWORK; NO_KEY_ACCESS_OR_GENERATION; NO_RESOLVE; NO_LIVE_OPERATIONS; READY_TO_EXECUTE_FALSE`

---

## 1. Executive Summary & Review Scope

### 1.1 Purpose of the Reconciliation Review
Under Plan 07B7 post-incident hardening, Blocker D governs host Data Access Control (DAC), POSIX principals, file permissions, directory hierarchy, and systemd service sandboxing for the three operating system execution principals.

Following the formal acceptance of **Gate 5 Release Authority Design R4** (`ACCEPTED_FOR_DESIGN_ONLY`), this assessment performs a rigorous, source-grounded static audit of all repository configuration artifacts associated with Blocker D:
1. `deploy/scripts/provision_resolveflow_principals.sh` (Revision R7, SHA-256 `80777c6789f9882b19de1465bc9896bfba516d100a63d8f09330666a20cbffbd`)
2. `deploy/scripts/verify_resolveflow_dac.sh` (Revision R7, SHA-256 `dfe179e4a13f23e8bdba4c29885b727fd944a53efae7175a0f695fc665a603b9`)
3. `deploy/systemd/resolveflow-signer.service` (SHA-256 `e98c7e997f7faeaef3945903b41d08fc34e40e6c525f05b0b3017a4c4146a066`)
4. `deploy/systemd/resolveflow-dispatcher.service` (SHA-256 `21f70df95eaad82119ebac63c46a94ebc28c6b16e8ede263d1f54fdcfe087792`)

### 1.2 Boundary of Assessment: Repository Source vs. Host State
This assessment evaluates **strictly repository configuration and design artifacts**. It does **not** evaluate, query, or infer live host state:
- No scripts were executed or sourced.
- No files on `/var/lib/resolveflow`, `/run/resolveflow`, `/etc/resolveflow`, or `/tmp` were inspected or modified.
- No user accounts, group memberships, or systemd daemons were queried on the host.
- No assumption is made that any path, account, or service currently exists or complies on the host.

### 1.3 Core Finding & Architectural Disposition
The repository configuration artifacts reflect the state of **DEP-02A (Revision R7)**. While they faithfully implement the initial DEP-01 / DEP-02A requirements (the Three-Principal model, setgid `02750` runtime socket directories, descriptor-anchored empty preflight checks, and `0600` singleton lock checks), **they pre-date and do not incorporate the subsequent architectural requirements established in Gate 4 Authority Design R10 and Gate 5 Release Authority Design R4**:
1. **Missing Trust-Root Infrastructure (`/etc/resolveflow`):** Neither provisioning script, verification script, nor systemd service unit defines, provisions, verifies, or binds the root-anchored public key trust paths required by Gate 4 (`/etc/resolveflow/preflight_verifier_public_key.pem`), Gate 5 (`/etc/resolveflow/tech_lead_public_key.pem`), or Oracle public key distribution (`/etc/resolveflow/oracle_public_key.pem`).
2. **Missing Dispatcher Durable State Directory (`/var/lib/resolveflow/dispatcher`):** Accepted Gate 4 and Gate 5 designs require `_resolveflow_dispatcher:902` to maintain durable state containing `.locks/`, `execution_journal.wal`, `receipt.json`, `execution.log`, `target_ledger.v2.json`, `consumed_preflight_runs.jsonl`, and `consumed_execution_workorders.jsonl`. However, `provision_resolveflow_principals.sh` only provisions `/var/lib/resolveflow/signer` and explicitly enforces a fail-closed parent whitelist rejecting any child other than `signer`. Furthermore, `resolveflow-dispatcher.service` omits `/var/lib/resolveflow/dispatcher` from `ReadWritePaths`, which under `ProtectSystem=strict` would conditionally prevent write access (`EROFS`) if the path exists, or fail with `ENOENT` if absent.
3. **Placeholder Service Execution (`ExecStart=/bin/false`):** Both systemd units remain stubs with `ExecStart=/bin/false` pending complete daemon implementation and packaging.

**Conclusion:** Blocker D is **NOT READY** for host provisioning. It remains strictly `ARTIFACT_ONLY_AND_UNRESOLVED` pending a dedicated packaging realignment workorder.

---

## 2. Artifact-to-Requirement Reconciliation Matrix

The following matrix cross-references each architectural requirement established across DEP-01, DEP-02A, Gate 4, and Gate 5 against the actual code checked into the repository configuration artifacts:

| Subsystem Component | Accepted Architecture Specification | Provisioning Script (`provision_resolveflow_principals.sh`) | Verification Script (`verify_resolveflow_dac.sh`) | Signer Service Unit (`resolveflow-signer.service`) | Dispatcher Service Unit (`resolveflow-dispatcher.service`) | Alignment Status |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Signer Principal** | UID `901`, GID `901`, name `_resolveflow_signer`. Zero supplementary groups. | Enforces UID 901, GID 901; verifies zero supplementary groups (lines 25-28, 115-120, 352). | Enforces UID 901, GID 901; verifies zero supplementary groups (lines 32-35, 98-103). | Declares `User=_resolveflow_signer`, `Group=_resolveflow_signer` (lines 9-10). | N/A | **ALIGNED (Artifact Only)** |
| **Dispatcher Principal** | UID `902`, GID `902`, name `_resolveflow_dispatcher`. Zero supplementary groups. | Enforces UID 902, GID 902; verifies zero supplementary groups (lines 30-33, 115-120, 353). | Enforces UID 902, GID 902; verifies zero supplementary groups (lines 37-40, 98-103). | N/A | Declares `User=_resolveflow_dispatcher`, `Group=_resolveflow_dispatcher` (lines 10-11). | **ALIGNED (Artifact Only)** |
| **Client Group & Runner** | GID `903`, name `resolveflow_clients`. Runner (`interactive_user:1000`) added to GID 903. | Provisions GID 903; adds `$RUNNER_USER` to supplementary group `resolveflow_clients` (lines 35-38, 334, 360). | Verifies GID 903 exists and `$RUNNER_USER` is in 903; ensures runner NOT in 901 or 902 (lines 42-45, 113-128). | N/A | N/A | **ALIGNED (Artifact Only)** |
| **Signer State Directory** | `/var/lib/resolveflow/signer`, `0700`, owner `901:901`. Leaf subdirs: `keystore`, `nonces`, `anchors` (empty). | Provisions `0700`, `901:901`; descriptor-anchored empty check on leaf subdirs (lines 226-242, 437-453). | Verifies `0700`, `901:901`, descriptor-anchored leaf empty checks (lines 329-340). | `ReadWritePaths=/var/lib/resolveflow/signer` (line 43). | `InaccessiblePaths=/var/lib/resolveflow/signer` (line 56). | **ALIGNED (Artifact Only)** |
| **Dispatcher State Directory** | `/var/lib/resolveflow/dispatcher`, `0700`, owner `902:902` for durable WAL, ledgers, and `.locks/`. | **OMITTED.** Whitelist strictly enforces children == `{'signer'}` (lines 225, 455). | **OMITTED.** Verifier checks children == `{'signer'}` (line 327). | N/A | **OMITTED.** Not in `ReadWritePaths`; blocked by `ProtectSystem=strict` (lines 37, 49). | **CRITICAL MISMATCH (`BLOCKER-D-F01`)** |
| **Runtime Socket Dirs** | `/run/resolveflow/{oracle, signer_client, dispatcher_client}`, mode `02750` setgid. | Provisions `02750` setgid with descriptor pinning (lines 244-260, 461-478). | Verifies `02750` setgid with descriptor pinning (lines 349-373). | `ReadWritePaths=... /oracle /signer_client`, `InaccessiblePaths=... /dispatcher_client` (lines 43, 47). | `ReadWritePaths=... /dispatcher_client`, `ReadOnlyPaths=... /oracle`, `InaccessiblePaths=... /signer_client` (lines 49, 51, 57). | **ALIGNED (Artifact Only)** |
| **Socket Nodes (AF_UNIX)** | `signer_oracle.sock` (901:902, 0660); `signer_public.sock` (901:903, 0660); `dispatcher.sock` (902:903, 0660). | Does not create sockets (left to daemon runtime initialization). | Verifies descriptor-relative `0660`, `ISSOCK`, exact owner/group if present (lines 231-250, 352-371). | UMask=0117 ensures new inodes conform to 0660 / 0600 (line 11). | UMask=0117 ensures new inodes conform to 0660 / 0600 (line 12). | **ALIGNED (Artifact Only)** |
| **Singleton Lock Files** | `signer_oracle.lock` (901:902, 0600); `signer_public.lock` (901:903, 0600); `dispatcher.lock` (902:903, 0600). | Does not create locks (left to daemon runtime initialization). | Verifies descriptor-relative `0600`, `ISREG`, exact owner/group if present (lines 211-230, 290-298). | Sandboxed via `ReadWritePaths` and UMask 0117. | Sandboxed via `ReadWritePaths` and UMask 0117. | **ALIGNED (Artifact Only)** |
| **System Trust Root Dir** | `/etc/resolveflow`, mode `0755`, owner `0:0`. | **OMITTED.** Not referenced or created anywhere in script. | **OMITTED.** Not verified anywhere in script. | N/A (read-only system default). | N/A (read-only system default, but not explicit in `ReadOnlyPaths`). | **CRITICAL MISMATCH (`BLOCKER-D-F02`)** |
| **Gate 4 Trust Anchor** | `/etc/resolveflow/preflight_verifier_public_key.pem`, `0644`, `0:0`. | **OMITTED.** | **OMITTED.** | N/A | **OMITTED.** | **CRITICAL MISMATCH (`BLOCKER-D-F02`)** |
| **Gate 5 Trust Anchor** | `/etc/resolveflow/tech_lead_public_key.pem`, `0644`, `0:0`. | **OMITTED.** | **OMITTED.** | N/A | **OMITTED.** | **CRITICAL MISMATCH (`BLOCKER-D-F02`)** |
| **Oracle Trust Anchor** | `/etc/resolveflow/oracle_public_key.pem`, `0644`, `0:0`. | **OMITTED.** (Legacy directory `/signer/anchors` created instead). | **OMITTED.** (Legacy directory `/signer/anchors` verified instead). | N/A | **OMITTED.** | **CRITICAL MISMATCH (`BLOCKER-D-F02`)** |
| **Service Execution Binary** | Real module execution via entrypoint script or daemon module. | N/A | N/A | `ExecStart=/bin/false` (line 22). | `ExecStart=/bin/false` (line 29). | **INCOMPLETE PACKAGING (`BLOCKER-D-F03`)** |

---

## 3. Trust-Root Inventory & Packaging Gap Analysis

The post-incident architecture relies on Class A system-wide trust anchors. However, an inventory of repository configuration files reveals that the trust-root architecture is currently **completely absent from host deployment scripts**:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                      TRUST-ROOT REPOSITORY INVENTORY                        │
├─────────────────────────────────────────────────────────────────────────────┤
│ 1. Gate 4 Preflight Verifier Public Key:                                    │
│    - Specified Path: /etc/resolveflow/preflight_verifier_public_key.pem         │
│    - DAC Specification: Mode 0644, Owner root:root (0:0).                   │
│    - Governing Document: Gate 4 Authority Design R10 §4.1                   │
│    - Provisioning Artifact Status: COMPLETELY UNREFERENCED / MISSING        │
│    - Verifier Artifact Status: COMPLETELY UNREFERENCED / MISSING            │
│    - Governance Status: UNRESOLVED_PENDING_GOVERNANCE_APPROVAL (Blocker A)  │
│                                                                             │
│ 2. Gate 5 Technical Lead Release Public Key:                                │
│    - Specified Path: /etc/resolveflow/tech_lead_public_key.pem              │
│    - DAC Specification: Mode 0644, Owner root:root (0:0).                   │
│    - Governing Document: Gate 5 Release Authority Design R4 §4.1            │
│    - Provisioning Artifact Status: COMPLETELY UNREFERENCED / MISSING        │
│    - Verifier Artifact Status: COMPLETELY UNREFERENCED / MISSING            │
│    - Governance Status: UNRESOLVED_PENDING_GOVERNANCE_PROVISIONING          │
│                                                                             │
│ 3. Signer Oracle Public Key Distribution:                                   │
│    - Specified Path: /etc/resolveflow/oracle_public_key.pem                 │
│    - DAC Specification: Mode 0644, Owner root:root (0:0).                   │
│    - Governing Document: DEP-04 UPSTREAM R1 Design §1.2                     │
│    - Provisioning Artifact Status: COMPLETELY UNREFERENCED / MISSING        │
│    - Verifier Artifact Status: COMPLETELY UNREFERENCED / MISSING            │
│    - Governance Status: PROPOSED IN UPSTREAM DESIGN; UNPROVISIONED          │
│                                                                             │
│ 4. Legacy Anomaly: Signer Anchors Directory:                                │
│    - Artifact Path: /var/lib/resolveflow/signer/anchors (0700, 901:901)     │
│    - Provisioning Artifact Status: PROVISIONED & ENFORCED EMPTY             │
│    - Verifier Artifact Status: VERIFIED EMPTY                               │
│    - Architectural Utility: UNUSED / DEPRECATED by Class A /etc trust root  │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 3.1 Architectural Conflict: System Trust Root (`/etc`) vs. Signer State (`/var/lib`)
1. In early DEP-01 drafts, trust anchors were tentatively envisioned under `/var/lib/resolveflow/signer/anchors`.
2. Under subsequent Gate 4 (§4.4) and Gate 5 (§4.4) reviews, storing public keys under `_resolveflow_signer:901` was rejected because `_resolveflow_dispatcher:902` and `interactive_user:1000` are strictly denied access to `/var/lib/resolveflow/signer` (`InaccessiblePaths=/var/lib/resolveflow/signer`).
3. Gate 4 R10 and Gate 5 R4 formally established that public trust roots must reside in `/etc/resolveflow/` owned by `root:root` with mode `0644`.
4. However, `provision_resolveflow_principals.sh` and `verify_resolveflow_dac.sh` were never updated to reflect this transition. They continue to provision the empty `/var/lib/resolveflow/signer/anchors` directory while omitting `/etc/resolveflow/` entirely.

---

## 4. Dispatcher Durable State Directory Gap (`/var/lib/resolveflow/dispatcher`)

### 4.1 Durable State Architecture Grounded in Source Contracts
In `src/services/durable_dispatch_journal.py` (lines 188–192, 327), Gate 4 Authority Design R10 (§2.3, §6.2, §7.1), and Gate 5 Release Authority Design R4 (§6.3), the Dispatcher daemon (`_resolveflow_dispatcher:902`) maintains its state directory `/var/lib/resolveflow/dispatcher` (mode `0700`, owner `_resolveflow_dispatcher:_resolveflow_dispatcher`).

The actual filesystem structure defined by repository code consists of:
1. **Direct Subdirectory:**
   - `.locks/` (mode `0700`, owner `902:902`, created by `DurableDispatchJournal.__init__()`, containing target locks).
2. **Direct State Files:**
   - `execution_journal.wal` (mode `0600`, owner `902:902`, write-ahead execution log).
   - `receipt.json` (mode `0600`, owner `902:902`, execution receipts).
   - `execution.log` (mode `0600`, owner `902:902`, runtime execution logs).
   - `target_ledger.v2.json` (mode `0600`, owner `902:902`, target reservation ledger).
   - `consumed_preflight_runs.jsonl` (mode `0600`, owner `902:902`, preflight replay ledger per Gate 4 R10 §7.1).
   - `consumed_execution_workorders.jsonl` (mode `0600`, owner `902:902`, execution replay ledger per Gate 5 R4 §6.3).

**Lifecycle Distinction (Setup vs. Runtime):**
- **At Initial Host Provisioning (Setup State):** `/var/lib/resolveflow/dispatcher` must be created as an empty directory (`0700`, `902:902`) with initial entries `set()`, matching the fail-closed empty directory policy enforced across `/var/lib/resolveflow/signer/*`.
- **At Daemon Runtime (Active State):** Permitted entries inside `/var/lib/resolveflow/dispatcher` are strictly `{".locks", "execution_journal.wal", "receipt.json", "execution.log", "target_ledger.v2.json", "consumed_preflight_runs.jsonl", "consumed_execution_workorders.jsonl"}`.

### 4.2 Concrete Mismatch in Provisioning and Verification Scripts
In `deploy/scripts/provision_resolveflow_principals.sh`:
- Line 225:
  ```python
  rf_lib_fd = inspect_and_open_if_exists(lib_fd, "resolveflow", 0, 0, 0o755, "/var/lib/resolveflow")
  if rf_lib_fd is not None:
      enforce_direct_children(rf_lib_fd, "/var/lib/resolveflow", {"signer"})
  ```
- Line 455:
  ```python
  enforce_direct_children(rf_lib_fd, "/var/lib/resolveflow", {"signer"})
  ```
The provisioning script explicitly checks that the only direct child of `/var/lib/resolveflow` is `signer`. If `/var/lib/resolveflow/dispatcher` were manually created or staged on the host, `provision_resolveflow_principals.sh` would immediately abort with:
`FATAL [PREFLIGHT FAIL-CLOSED]: Pre-existing directory '/var/lib/resolveflow' contains unreviewed entries: ['dispatcher'].`

Similarly, in `deploy/scripts/verify_resolveflow_dac.sh`:
- Line 327:
  ```python
  rf_lib_fd = verify_node(lib_fd, "resolveflow", 0, 0, 0o755, "/var/lib/resolveflow", {"signer"})
  ```
The verifier treats the presence of `dispatcher` under `/var/lib/resolveflow` as an integrity violation (`Directory '/var/lib/resolveflow' contains unreviewed direct entries: ['dispatcher']`).

### 4.3 Systemd Sandbox Collision (`resolveflow-dispatcher.service`)
In `deploy/systemd/resolveflow-dispatcher.service`:
- Line 37: `ProtectSystem=strict` mounts `/var` read-only.
- Line 49: `ReadWritePaths=/run/resolveflow/dispatcher_client /var/tmp/davinci_socket /tmp/.X11-unix`

**Static Impact Analysis (BLOCKER-D-RECON-R1-02):**
Because `/var/lib/resolveflow/dispatcher` is **not** included in `ReadWritePaths`:
- If `/var/lib/resolveflow/dispatcher` exists on the host, `ProtectSystem=strict` would conditionally prevent write access, causing writes to fail with `OSError: [Errno 30] Read-only file system` (`EROFS`).
- If `/var/lib/resolveflow/dispatcher` does not exist on the host, initialization attempts would fail with `FileNotFoundError: [Errno 2] No such file or directory` (`ENOENT`).
In either case, this represents a static configuration mismatch in `resolveflow-dispatcher.service` that precludes durable state persistence.

---

## 5. Systemd Service Unit Packaging Status (`ExecStart=/bin/false`)

Both `resolveflow-signer.service` and `resolveflow-dispatcher.service` contain explicit packaging dependency statements explaining that their execution entrypoints are intentionally inert:

### 5.1 Signer Service Analysis (`resolveflow-signer.service`)
- Line 22: `ExecStart=/bin/false`
- Rationale documented in lines 17-21:
  ```ini
  # DEP02A-03 Packaging & Runtime Dependency Statement:
  # The daemon module 'src.services.trial_authorization_signer_daemon' does not yet exist in the repository
  # and is not installed by 'package_deb.sh' (which packages only the standalone executable).
  # Per Workorder G07-07B7-GATE6-DEP02A Section 3.4 and Code Review finding DEP02A-03, ExecStart is
  # left incomplete as /bin/false pending a follow-on packaging / daemon implementation workorder (DEP-02B).
  ```
- **Current Repository Fact:** `src/services/trial_authorization_signer_daemon.py` was subsequently implemented under workorder DEP-02B R6. However, the systemd unit was never updated from `/bin/false` to invoke Python with that module.

### 5.2 Dispatcher Service Analysis (`resolveflow-dispatcher.service`)
- Line 29: `ExecStart=/bin/false`
- Rationale documented in lines 24-28:
  ```ini
  # DEP02A-03 Packaging & Runtime Dependency Statement:
  # The daemon module 'src.services.live_canary_dispatch_service' does not yet exist in the repository
  # and is not installed by 'package_deb.sh' (which packages only the standalone executable).
  # Per Workorder G07-07B7-GATE6-DEP02A Section 3.4 and Code Review finding DEP02A-03, ExecStart is
  # left incomplete as /bin/false pending a follow-on packaging / daemon implementation workorder (DEP-03).
  ```
- **Current Repository Fact:** `src/services/live_canary_dispatch_service.py` was implemented under workorder DEP-03 R5. However, no daemon wrapper script or service runner exists, and `resolveflow-dispatcher.service` remains stubbed with `/bin/false`.

---

## 6. Severity-Ranked Findings Table

The following findings summarize all concrete mismatches identified between repository configuration artifacts and accepted architectural requirements:

| Finding ID | Severity | File Reference & Location | Concrete Mismatch Description | Affected Security Boundary | Recommended Smallest Next-Step |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **BLOCKER-D-F01** | **CRITICAL** | `deploy/scripts/provision_resolveflow_principals.sh:225, 455`<br/>`deploy/scripts/verify_resolveflow_dac.sh:327`<br/>`deploy/systemd/resolveflow-dispatcher.service:49` | Missing `/var/lib/resolveflow/dispatcher` (`0700`, `902:902`). Provisioning and verifier enforce parent whitelist `{"signer"}` rejecting any other child. Dispatcher service unit omits path from `ReadWritePaths`, leading to `EROFS` or `ENOENT` on journal/ledger writes. | Dispatcher durable state boundary (`.locks/`, `execution_journal.wal`, `receipt.json`, `target_ledger.v2.json`, `consumed_preflight_runs.jsonl`, `consumed_execution_workorders.jsonl`). | Update provisioning to create `/var/lib/resolveflow/dispatcher` (`0700`, `902:902`, initial entries `set()`); expand parent whitelist to `{"signer", "dispatcher"}`; update verifier to accept initial empty set or active runtime entries; add path to Dispatcher `ReadWritePaths`. |
| **BLOCKER-D-F02** | **CRITICAL** | `deploy/scripts/provision_resolveflow_principals.sh`<br/>`deploy/scripts/verify_resolveflow_dac.sh`<br/>`deploy/systemd/resolveflow-dispatcher.service` | Complete absence of `/etc/resolveflow` directory and public trust anchor specifications (`preflight_verifier_public_key.pem`, `tech_lead_public_key.pem`, `oracle_public_key.pem`). | System Class A trust anchor root validation across Gate 4, Gate 5, and Dispatcher. | Authorize dedicated packaging realignment workorder to define `/etc/resolveflow` (`0755`, `0:0`) and descriptor-anchored verifier checks for trust anchor files (`0644`, `0:0`). |
| **BLOCKER-D-F03** | **MEDIUM** | `deploy/systemd/resolveflow-signer.service:22`<br/>`deploy/systemd/resolveflow-dispatcher.service:29` | Both systemd service units contain placeholder `ExecStart=/bin/false`. Units are not executable or deployable in current state. | Daemon service lifecycle management (`resolveflow.target`). | Create daemon execution entrypoint wrappers and update `ExecStart` in service units under follow-on packaging workorders. |
| **BLOCKER-D-F04** | **LOW** | `deploy/scripts/provision_resolveflow_principals.sh:237, 447`<br/>`deploy/scripts/verify_resolveflow_dac.sh:337` | Provisioning and verification scripts create and check `/var/lib/resolveflow/signer/anchors` (`0700`, `901:901`), an obsolete anchor location superseded by `/etc/resolveflow`. | Cleanliness and clarity of state directory layout. | Deprecate and remove `/var/lib/resolveflow/signer/anchors` from provisioning and verifier scripts in packaging realignment. |

---

## 7. Strict Separation: Repository Artifact Facts vs. Target Requirements vs. Host Evidence Gaps

To preserve rigorous verification integrity, the table below partitions architectural elements into three non-overlapping categories:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ 1. VERIFIED REPOSITORY FACTS (Checked-in source artifacts):                 │
│    - provision_resolveflow_principals.sh and verify_resolveflow_dac.sh      │
│      exist in deploy/scripts/ (Revision R7).                                │
│    - Systemd units exist in deploy/systemd/ with ExecStart=/bin/false.      │
│    - Sockets are defined as mode 02750 setgid directories with 0660 sockets │
│      and 0600 singleton locks.                                              │
│    - /etc/resolveflow and /var/lib/resolveflow/dispatcher do NOT exist in   │
│      any checked-in provisioning or verifier script.                        │
├─────────────────────────────────────────────────────────────────────────────┤
│ 2. FUTURE TARGET REQUIREMENTS (Accepted architecture design specs):         │
│    - Gate 4 trust root at /etc/resolveflow/preflight_verifier_public_key.pem│
│    - Gate 5 trust root at /etc/resolveflow/tech_lead_public_key.pem         │
│    - Oracle public trust root at /etc/resolveflow/oracle_public_key.pem     │
│    - Dispatcher durable state at /var/lib/resolveflow/dispatcher            │
│    - Real daemon execution entrypoints replacing /bin/false stubs           │
├─────────────────────────────────────────────────────────────────────────────┤
│ 3. HOST EVIDENCE GAPS (Cannot be known without authorized host inspection):  │
│    - Whether UID 901 or UID 902 exists in host /etc/passwd or LDAP          │
│    - Whether GID 901, 902, or 903 exists in host /etc/group                 │
│    - Whether /var/lib/resolveflow or /run/resolveflow exists on the host    │
│    - Whether any pre-existing permissions or files conflict on the host     │
│    - Whether systemd on the host has loaded or enabled resolveflow units    │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Zero Host Claims Assertion:**  
This assessment asserts **zero claims** regarding the live host filesystem, host accounts, or running processes. All evaluations are strictly confined to the text and logic of files within the git workspace.

---

## 8. Dependency Map & Sequential Follow-On Workorders

### 8.1 Positioning in Plan 07B7 Phase 2 DAG
The positioning of this Blocker D review within the Plan 07B7 Phase 2 DAG is illustrated below:

```
[Phase 2 Boundary Design R8 (ACCEPTED)]
       │
       ├───► [Receipt/WAL Binding Design R3 (ACCEPTED)]
       │            │
       │            └───► [Post-Incident Runbook R5 (ACCEPTED)]
       │                         │
       │                         ├───► [Blocker A: Gate 4 Preflight Authority Design R10 (ACCEPTED)]
       │                         │            │
       │                         │            ├───► [Gate 4 Custody Options R4 (ACCEPTED)]
       │                         │            │
       │                         │            └───► [Gate 4 Custody Decision R3 (NO DECISION / UNRESOLVED)]
       │                         │
       │                         ├───► [Blocker B: Gate 5 Release Authority Design R4 (ACCEPTED FOR DESIGN ONLY)]
       │                         │
       │                         ├───► [Blocker C: Signer Oracle Orphan Governance] (STRICTLY UNRESOLVED)
       │                         │
       │                         └───► [Blocker D: DAC Readiness Reconciliation R2 (THIS ASSESSMENT)]
       │                                      │
       │                                      └───► (NOT READY FOR HOST PROVISIONING; ARTIFACTS REQUIRE REALIGNMENT)
       │
       ▼
[ALL BLOCKERS UNRESOLVED]
       │
       X  <─── [DISCONNECTED / NO EDGES]
       │
       ▼
[WO5: Live Canary Execution Directive]
```

### 8.2 Inter-Blocker Independence
- **Blocker D vs. Blocker A:** Blocker D (Host DAC & Packaging) is independent of Blocker A (Gate 4 verifier custody mechanism). Blocker D can package the `/etc/resolveflow/` directory and define trust anchor descriptor checks regardless of whether Gate 4 selects a hardware token or an offline station.
- **Blocker D vs. Blocker B:** Gate 5 R4 established the specification for `/etc/resolveflow/tech_lead_public_key.pem`. Blocker D incorporates this path into host packaging requirements.
- **Blocker D vs. Blocker C:** Blocker C resolves Signer Oracle crash recovery and orphan nonces. Independent of Blocker D host filesystem layout.

### 8.3 Recommended Sequential Follow-On Workorders for Blocker D
To resolve the gaps identified in this assessment, the following sequential workorders are recommended:
1. **`WO-BLOCKER-D-PACKAGING-REALIGN-R1` (Static Configuration Workorder):**
   - Update `deploy/scripts/provision_resolveflow_principals.sh` to provision `/etc/resolveflow` (`0755`, `root:root`) and `/var/lib/resolveflow/dispatcher` (`0700`, `902:902`, initial entries `set()`), expanding the parent whitelist of `/var/lib/resolveflow` to `{"signer", "dispatcher"}`.
   - Update `deploy/scripts/verify_resolveflow_dac.sh` to verify `/etc/resolveflow` and `/var/lib/resolveflow/dispatcher` (supporting both initial empty state and active runtime entries).
   - Update `deploy/systemd/resolveflow-dispatcher.service` to include `/var/lib/resolveflow/dispatcher` in `ReadWritePaths`.
   - Deprecate `/var/lib/resolveflow/signer/anchors`.
2. **`WO-BLOCKER-D-HOST-DRYRUN-R1` (Host Preflight Inspection Workorder):**
   - Under an authorized workorder, execute `verify_resolveflow_dac.sh` in read-only mode to capture actual host baseline status (`STATUS=NOT_PROVISIONED` vs `STATUS=FAILED_MISMATCH`).
3. **`WO-BLOCKER-D-HOST-PROVISION-R1` (Host Provisioning Workorder):**
   - Execute `provision_resolveflow_principals.sh --apply` with root privileges. Re-run `verify_resolveflow_dac.sh` to assert `STATUS=VERIFIED_OK`.

---

## 9. Governance Recommendation & Formal Non-Authorization

### 9.1 Summary Recommendation to Technical Lead & Governing Quorum
It is recommended that Technical Lead Codex:
1. **Accept this document as `ACCEPTED_FOR_ASSESSMENT_ONLY`:** Formally approve Revision R2 as the definitive audit and gap analysis of Blocker D configuration artifacts.
2. **Declare Blocker D `NOT_READY_FOR_HOST_PROVISIONING`:** Confirm that host provisioning cannot be authorized until the packaging and directory gaps (`BLOCKER-D-F01`, `BLOCKER-D-F02`) are realigned in repository source.
3. **Maintain Strict Block on Host Mutation:** Reaffirm that this assessment grants zero authorization to execute provisioning scripts, modify host files, create users/groups, or install systemd services.

### 9.2 Strict Declaration of Non-Authorization
- **This assessment does NOT authorize host provisioning, script execution, or systemd deployment.**
- **No private or public keys, trust roots, sockets, or journals are created or accessed.**
- **Blockers A, B, C, and D remain completely unresolved in the Phase 2 DAG.**
- **Node WO5 remains completely disconnected with strictly zero incoming edges.**

### 9.3 Preserved Incident Safety Invariants
- `ready_to_execute = False` is strictly maintained.
- Gate 6 strictly remains `NOT ACCEPTED` / incident-only.
- Gate G07, Milestones M1, M2, and M3 strictly remain `OPEN`.
- Node `WO5 (Live Canary Execution Directive)` in decision DAG remains completely disconnected with strictly zero incoming edges.
- Target project `RF_TRIAL_AEC8F0A7A365D5F5` (UID `4fd4272d-f0bc-445c-9490-5723cd6831c2`) and timeline `RF_TRIAL_TL_AEC8F0A7A365D5F5` (UID `ba6a6775-8ec4-41a4-bf2f-704de902e68d`) on `DISPLAY=:1` remain untouched and empty.
- Zero private key generation, zero host provisioning, zero systemd changes, zero socket creation, zero network traffic, zero Resolve API/UI interactions.
