# Gate 6 Phase 2 Blocker B: Gate 5 Supervised Launcher & Runtime Process-Binding Architecture Design (Revision R3)

**Document ID:** `07B7-GATE6-PHASE2-BLOCKER-B-GATE5-PROCESS-BINDING-DESIGN-R1`  
**Revision:** `R3`  
**Workorder:** `G07-07B7-GATE6-PHASE2-BLOCKER-B-GATE5-PROCESS-BINDING-DESIGN-R1`  
**Date:** 2026-11-05  
**Parent:** Plan 07B7 Phase 2 Hardening, Blocker B, prerequisite B-2. Follows accepted Gate 5 Release Authority Design R4 (`07B7-GATE6-PHASE2-GATE5-RELEASE-AUTHORITY-DESIGN-R1.md`), accepted Gate 5 Readiness Assessment R3 (`07B7-GATE6-PHASE2-BLOCKER-B-GATE5-READINESS-ASSESSMENT-R1.md`), accepted Gate 5 Trust Anchor Policy Design R3 (`07B7-GATE6-PHASE2-BLOCKER-B-GATE5-TRUST-ANCHOR-POLICY-DESIGN-R1.md`), accepted Phase 2 Boundary Design R8, and accepted Phase 2 Blocker Status Checkpoint R2.  
**Mode:** `REPOSITORY_ONLY_STATIC_ARCHITECTURE_DESIGN; READ_ONLY_INPUTS; NO_SOURCE_OR_CONFIG_EDITS; NO_TESTS_OR_IMPORTS_OR_COMPILATION; NO_HOST_OR_PRODUCTION_STATE_ACCESS; NO_KEY_OR_TRUST_ROOT_CONTENT_ACCESS; NO_SYSTEMD_LIFECYCLE; NO_SOCKET_OR_NETWORK; NO_RESOLVE; NO_LIVE_OPERATIONS; READY_TO_EXECUTE_FALSE`.

---

## 1. Executive Summary & Problem Formulation

Under Plan 07B7 post-incident hardening, Blocker B governs Gate 5 Technical Lead Release Authority. Gate 5 ensures that no candidate execution workflow can mutate the DaVinci Resolve canary target without cryptographic authorization from the Technical Lead.

### 1.1 The Core Process-Binding Gap (Prerequisite B-2)
In the accepted Gate 5 Release Authority Design R4 (§3.5), the Technical Lead cryptographically signs `SignedExecutionWorkorderRecord`, which binds the field `reviewed_candidate_sha256` (the 64-char lowercase hexadecimal SHA-256 digest of the audited candidate execution harness).

However, as formally established in Gate 5 Readiness Assessment R3 (§4):
1. **Static Hash vs. Runtime Memory:** A cryptographic signature over `reviewed_candidate_sha256` certifies artifact approval at review time. It does **not** assert kernel-level control or in-memory execution attestation over the operating system process connecting to the `_resolveflow_dispatcher` admission socket.
2. **IPC Boundary Reality:** On the AF_UNIX admission socket, `SO_PEERCRED` authenticates only caller POSIX numeric credentials: PID, UID, and GID (confirming `interactive_user:1000`). `SO_PEERCRED` does **not** authenticate what script, bytecode, or native libraries are loaded into the peer's memory space.
3. **Interpreter Indirection:** In standard Python execution environments, `/proc/<peer_pid>/exe` points simply to the shared `/usr/bin/python3` interpreter binary, completely obscuring the script identity.
4. **Time-of-Check to Time-of-Use (TOCTOU):** Statically checking the hash of a script file on disk prior to execution does not guarantee that the file remains unchanged between the check and execution, nor does it prevent concurrent unconfined processes under UID 1000 from presenting valid envelopes over the socket.

### 1.2 Objective of Prerequisite B-2
This architecture design specifies how a future trusted execution boundary can cryptographically and structurally bind the audited candidate artifact (`reviewed_candidate_sha256`) to the exact process that performs the authorized dispatch, while preserving the accepted Three-Principal model and the interactive Resolve GUI/display boundary.

### 1.3 Strict Statement Classification Taxonomy
In adherence to Workorder Section 3, all statements throughout this specification are classified as:
- **[ACCEPTED-DESIGN]:** Formally accepted requirements in accepted architecture documents (e.g. Gate 5 Design R4, Gate 4 Design R10, DEP-01 R4 Amended V3, Phase 2 Boundary Design R8).
- **[IMPLEMENTED-CONTRACT]:** Cryptographic and schema behaviors verified in checked-in repository source code (e.g. `src/core/contracts/`).
- **[PACKAGING-DECLARATION]:** Declarations in host setup or verification scripts (e.g. `deploy/scripts/`, `deploy/systemd/`); facts about configuration code, not proof of host state.
- **[PROPOSAL]:** Architectural policies, mechanism models, and procedural designs proposed in this document.
- **[UNRESOLVED-PREREQUISITE]:** Governance, host-state, or implementation gaps that remain strictly open and require future authorized workorders.

---

## 2. Threat Model & Security Guarantee Definition

### 2.1 The Concrete Guarantee Sought (Entrypoint-Source Integrity Scope)
- **[PROPOSAL]:** The runtime process-binding architecture defines an **entrypoint-source integrity property** (BLOCKER-B-PROCESS-BINDING-R1-03):
  $$\mathbf{Invariant: \quad EntrypointSourceBound}(\mathcal{E}_{\text{entrypoint}}, \mathcal{D}_{\text{candidate}})$$
  Ensuring that the entrypoint script source provided to the Python interpreter process is byte-identical to `record.reviewed_candidate_sha256` without disk-level TOCTOU mutation:
  $$\text{SHA256}(\text{Bytes}(\mathcal{E}_{\text{entrypoint}})) \equiv \text{record.reviewed\_candidate\_sha256}$$
- **[UNRESOLVED-PREREQUISITE]:** *Explicit Scope Limitation:* Hashing and binding the entrypoint script (`run_gate6.py`) alone does **not** bind the complete execution closure. The Python interpreter binary (`/usr/bin/python3`), standard library, dynamically imported modules (`.venv/lib/python3.12/site-packages/`), native shared libraries (`fusionscript.so`), and in-flight post-load process memory remain an **unresolved dependency and residual risk** unless bound by a future hermetic container, sealed single-file package, or comprehensive manifest.

### 2.2 Threat Model & In-Scope Actor Capabilities
The threat model addresses adversaries operating under the unprivileged interactive user session (`interactive_user:1000`):
1. **Pre-Execution Disk Mutation (TOCTOU Race):** Modifying the candidate harness script on disk after hash verification but prior to interpreter read.
2. **Sibling Process Impersonation:** An unconfined process running under UID 1000 connecting to the Dispatcher AF_UNIX admission socket, presenting valid, pre-collected signed envelopes (`SignedApprovalEnvelope`, `SignedOperatorConsentEnvelope`, `SignedPreflightEvidenceEnvelope`, `SignedExecutionWorkorderRecord`), but executing unauthorized, arbitrary code.
3. **Environment & Dependency Tampering:** Manipulating environment variables (`PYTHONPATH`, `PYTHONHOME`, `LD_LIBRARY_PATH`, `RESOLVE_SCRIPT_API`) or modifying mutable virtual environment packages (`.venv/lib/python3.12/site-packages/`) to inject malicious code during interpreter startup while leaving the top-level candidate script file hash unchanged.
4. **Path & Symlink Redirection:** Manipulating filesystem symlinks or temporary paths (e.g. in `/tmp` or user workspace) to execute an unreviewed script.

### 2.3 Out-of-Scope Capabilities (Explicit Non-Guarantees)
The process-binding architecture does **not** defend against:
1. **Host Kernel Compromise:** An adversary possessing `root` UID 0 or kernel ring-0 privileges can bypass all DAC, cgroups, namespaces, and memory seals.
2. **Direct Resolve Studio Memory Modification:** An adversary with memory-write access to the running DaVinci Resolve binary itself (`/opt/resolve/bin/resolve`) can manipulate project state out-of-band.
3. **Hardware & Physical Attacks:** Physical memory probing or cold-boot attacks against workstation DRAM.

---

## 3. Comparison of Process-Binding Mechanism Families

To identify a viable architecture, four distinct mechanism families are evaluated against technical feasibility, direct code binding, and compatibility with the Three-Principal model:

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                      EVALUATION OF MECHANISM FAMILIES                                  │
├────────────────────────────────────────────────────────────────────────────────────────┤
│ (a) Supervised Launcher Daemon:                                                        │
│     - Direct code binding: UNRESOLVED / CONDITIONAL (Does not satisfy sibling-process  │
│       threat model under UID 1000 without independently enforceable launcher identity, │
│       protected attestation key/channel, and binding to exact connected child).        │
│     - Preserves 3-principal model: YES (Spawns child as UID 1000 with display creds). │
│     - Limitation: Intra-UID attestation to Dispatcher requires separate trust root,    │
│       key isolation, and wire protocol, all of which remain unresolved prerequisites.  │
├────────────────────────────────────────────────────────────────────────────────────────┤
│ (b) Descriptor-Pinned Execution (fexecve):                                            │
│     - Direct code binding: PARTIAL (Binds interpreter binary, NOT script bytecode).   │
│     - Script-to-process binding: Requires kernel-supported script execution or        │
│       custom launcher passing pinned file descriptor to Python via /dev/fd/N.          │
├────────────────────────────────────────────────────────────────────────────────────────┤
│ (c) Sealed Memory (memfd_create + F_SEAL_WRITE):                                       │
│     - Direct code binding: HIGH (Kernel-enforced write-sealed backing file descriptor)│
│     - Limitation: Seals backing file, not address space; native .so requires disk.     │
├────────────────────────────────────────────────────────────────────────────────────────┤
│ (d) Process & Cgroup Supervision (systemd scope / cgroup v2):                          │
│     - Direct code binding: NO (Provides accounting & lifecycle, NOT code attestation). │
│     - Classification: Supporting containment only; zero cryptographic code identity.   │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

### 3.1 Family (a): Supervised Launch / Exec by a Trusted Component
- **[PROPOSAL]:** In this model, an external unconfined process under UID 1000 is **not** permitted to directly initiate admission. Instead, an authorized Supervised Launcher component mediates execution:
  1. The launcher acquires an open file descriptor to the candidate script.
  2. The launcher computes `SHA256(fd)` and verifies equivalence with `reviewed_candidate_sha256`.
  3. The launcher spawns the execution process, maintaining supervisory attachment.
- **[UNRESOLVED-PREREQUISITE]:** *Direct Code Binding Evaluation:* **UNRESOLVED / CONDITIONAL (BLOCKER-B-PROCESS-BINDING-R2-01).**
  - While the launcher controls child process creation and passes the verified descriptor, executing the launcher under `interactive_user:1000` does **not** by itself establish direct code-to-process binding or provenance against a same-UID adversary.
  - Any sibling process under UID 1000 shares identical POSIX credentials (`SO_PEERCRED` reports `{pid, 1000, 1000}`) and can forge tokens or attempt admission.
  - The session-confined launcher does **not** satisfy the sibling-process threat model until an independently enforceable launcher identity, a protected attestation key or channel, and binding to the exact connected child are specified. Direct code binding remains an unresolved prerequisite requiring a separately authorized specification.

### 3.2 Family (b): Descriptor-Pinned Execution (`fexecve` / equivalent)
- **[PROPOSAL]:** The POSIX system call `fexecve(int fd, char *const argv[], char *const envp[])` executes the program referred to by an open file descriptor rather than a pathname, closing the pathname TOCTOU race.
- **[PROPOSAL]:** *Interpreter & Script Implications:*
  - In Linux, when `fexecve()` is invoked on a script with a `#!` shebang (e.g. `#!/usr/bin/python3`), the kernel opens the interpreter binary and passes `/dev/fd/<fd>` or the script pathname to the interpreter.
  - Standard CPython, upon receiving `/dev/fd/<fd>`, opens the file descriptor and reads script bytes. However, subsequent `import` statements dynamically search the filesystem (`sys.path`) and read modules by pathname from mutable directories.
  - *Finding:* Descriptor-pinning the entrypoint script closes the TOCTOU window for the entrypoint itself, but does **not** protect against dependency tampering in imported modules.

### 3.3 Family (c): Sealed Memory (`memfd_create` + `F_SEAL_WRITE`) (BLOCKER-B-PROCESS-BINDING-R1-04)
- **[PROPOSAL]:** The Linux system call `memfd_create(const char *name, unsigned int flags)` allocates anonymous in-memory files. Once populated with script bytes, applying seals:
  $$\text{fcntl}(fd, F\_ADD\_SEALS, F\_SEAL\_WRITE \mid F\_SEAL\_GROW \mid F\_SEAL\_SHRINK \mid F\_SEAL\_SEAL)$$
  creates a **kernel-enforced write-sealed backing file descriptor** preventing any subsequent modification of the in-memory file contents across all user processes.
- **[PROPOSAL]:** *Distinction Between Sealing and Cryptography:*
  - Memfd seals are an OS kernel-enforced write-prevention control on the backing descriptor. They are **not** a cryptographic verification mechanism, do not authenticate bytes, and do not attest to the process memory address space or dynamically imported modules.
  - The cryptographic digest comparison (`SHA256(fd) == reviewed_candidate_sha256`) is the distinct, independent integrity check evaluated prior to applying seals.
- **[PROPOSAL]:** *Python & Resolve Scripting Constraints:*
  - While Python can execute code from `/proc/self/fd/<sealed_fd>`, DaVinci Resolve scripting requires loading native shared objects (`fusionscript.so` via `import DaVinciResolveScript`).
  - Standard dynamic linkers (`ld.so`) historically require pathnames for `dlopen()`. While modern linkers support `/proc/self/fd/N`, standard Python package layouts assume on-disk site-packages directories.
  - *Finding:* Sealed memory provides robust entrypoint sealing, but must be paired with filesystem containment for native Resolve API libraries.

### 3.4 Family (d): Process & Cgroup Supervision (systemd scope / cgroup v2)
- **[PROPOSAL]:** Systemd transient scopes (`systemd-run --scope`) and cgroup v2 controllers group processes into unified hierarchical control groups.
- **[PROPOSAL]:** *Direct Code Binding Evaluation:* **NO direct code binding.**
  - Cgroups enforce resource limits (memory ceiling, CPU shares, I/O bandwidth, freezer).
  - Cgroup membership confirms that a process belongs to a monitored execution session, but provides **zero cryptographic attestation** regarding what code, bytecode, or binary is loaded inside that process.
  - Conflating cgroups with process-image binding is an architectural error. Cgroups serve as **supporting containment and lifecycle management only**.

---

## 4. Architectural Execution Boundary & Three-Principal Reconciliation

### 4.1 The Current Architectural Separation
The post-incident architecture (DEP-01 §2.1, Phase 2 Boundary Design R8) strictly enforces exactly three operating system execution principals:

| OS Principal | POSIX UID / GID | Authorized Custody & Execution Role | DaVinci Resolve Boundary Constraints |
| :--- | :--- | :--- | :--- |
| `_resolveflow_signer` | `901:901` | Keystore, Oracle private key, nonces. Headless systemd sandbox. | **Strictly forbidden** from accessing Resolve, display servers (`DISPLAY`), or user sessions. |
| `_resolveflow_dispatcher` | `902:902` | Execution journal, target ledgers, admission socket listener. Systemd sandbox: `ProtectSystem=strict`. | Connects to Oracle AF_UNIX socket. Evaluates Gate 5 signature. **Possesses zero DaVinci Resolve GUI session capability.** |
| `interactive_user` | `1000:1000` (`vanntruongg`) | Runs DaVinci Resolve Studio GUI process on `DISPLAY=:1`. Runs candidate runner harness. | **Sole owner of DaVinci Resolve GUI session.** Connects to Dispatcher admission socket. Holds zero release signing keys. |

### 4.2 The Resolve GUI / Display Ownership Boundary
- **[ACCEPTED-DESIGN]:** DaVinci Resolve Studio on Linux runs as an interactive X11/GUI application bound to `DISPLAY=:1`, owned by `interactive_user:1000`.
- **[ACCEPTED-DESIGN]:** The DaVinci Resolve Scripting API communicates with Resolve via an internal IPC socket located in `/tmp` or `/var/tmp/davinci_socket`, authenticated by local user ownership and X11 authentication cookies (`.Xauthority`).
- **[ACCEPTED-DESIGN]:** A daemon running under UID `902` cannot natively interact with the DaVinci Resolve GUI without acquiring the user's X11 session authority and running in the user's session environment. Moving Resolve execution to UID `902` would destroy least-privilege separation and violate DEP-01 §2.1.

### 4.3 Supervised Launcher Placement & Intra-UID Boundary Analysis (BLOCKER-B-PROCESS-BINDING-R1-01, BLOCKER-B-PROCESS-BINDING-R2-01)
To avoid violating the Three-Principal model or inventing an unapproved fourth principal, structural placement of the launcher must resolve the intra-UID trust problem:

1. **Rejection of Dispatcher-Mediated Fork/Exec (Model 1):**
   - The Dispatcher daemon runs strictly under unprivileged service user `_resolveflow_dispatcher:902` with `NoNewPrivileges=true` and possesses zero `CAP_SETUID` or root capability.
   - The Dispatcher cannot spawn child processes under UID `1000`. Granting the Dispatcher root privileges to perform `setuid(1000)` would violate the fundamental least-privilege boundary established after the incident.
2. **Analysis of Session-Confined Supervised Launcher (Model 2):**
   - Model 2 proposes a Supervised Launcher executing within the `interactive_user:1000` session domain to preserve the X11/display credentials needed for Resolve scripting.
   - **Critical Intra-UID Security Limitation (BLOCKER-B-PROCESS-BINDING-R1-01):**
     - Because the launcher runs under UID 1000, any other process running in the user session has identical numeric OS credentials (`SO_PEERCRED` reports `{pid, 1000, 1000}`).
     - A plain, unauthenticated "attestation token" passed over an AF_UNIX socket cannot prove supervisory provenance to the Dispatcher, because any sibling process under UID 1000 could forge, intercept, or replay such a token.
     - Furthermore, unless kernel-enforced ptrace restrictions (e.g. Yama `ptrace_scope >= 2`) or user-namespace isolation are active, concurrent sibling processes under UID 1000 can inspect or manipulate child memory (`/proc/<pid>/mem`), descriptors, or environment variables.
   - **Private Key Custody and Isolation Failure Against Same-UID Adversary (BLOCKER-B-PROCESS-BINDING-R2-01):**
     - Proposing a "launcher-held asymmetric signing keypair with a distinct Dispatcher trust anchor" does **not** establish trust against the stated same-UID adversary:
       - A private key stored in a user filesystem path or held in process memory by an ordinary unprivileged process under UID 1000 has zero DAC isolation from sibling processes executing under UID 1000. Any adversary process running under UID 1000 possesses identical DAC read permissions on user files and can inspect or manipulate memory via standard debugging interfaces (unless restricted). Merely having the Dispatcher trust the corresponding public key does not protect the private key from compromise or extraction by the same-UID adversary.
       - Alternative proposals—such as a dedicated helper service running under a distinct system UID or Linux user namespaces (`unshare`/user-ns container)—likewise require concrete peer-binding, credential sealing, and isolation guarantees that are not currently designed or implemented.
   - **Deficiency Against Sibling-Process Threat Model:**
     - Consequently, the session-confined launcher does **NOT** satisfy the sibling-process threat model until:
       1. An independently enforceable launcher identity is specified;
       2. A protected attestation key or channel immune to same-UID tampering is established;
       3. A concrete cryptographic and OS-level binding to the exact connected child process is formally defined.
   - **Status:** Model 2 remains an **explicitly hypothetical and unresolved operational proposal**. It does not satisfy the sibling-process threat model. Any additional authority, trust anchor, execution principal, or wire contract required to resolve this boundary must remain a separately authorized prerequisite (under future workorders), rather than assumed in this design.

---

## 5. End-to-End Trust Chain & TOCTOU Vulnerability Analysis

### 5.1 The Seven-Link Trust Chain
The end-to-end cryptographic and runtime verification path from Technical Lead signoff to DaVinci Resolve mutation spans seven distinct links:

```
[Link 1: Tech Lead Release Signoff]
  │  SignedExecutionWorkorderRecord commits to reviewed_candidate_sha256
  ▼
[Link 2: Supervised Input Acquisition]
  │  Launcher opens script descriptor with O_RDONLY | O_NOFOLLOW | O_CLOEXEC
  ▼
[Link 3: Artifact Integrity Verification]
  │  Launcher computes SHA-256(fd); confirms == reviewed_candidate_sha256
  ▼
[Link 4: Runtime Environment & Memory Sealing]
  │  Script copied to write-sealed memfd (F_SEAL_WRITE); environment sanitized
  ▼
[Link 5: Supervised Process Spawning]
  │  Child spawned executing sealed fd; parent captures PID and start time
  ▼
[Link 6: Admission IPC Verification]
  │  Dispatcher validates Gate 5 signature against /etc/resolveflow/tech_lead_public_key.pem
  │  Dispatcher validates SO_PEERCRED == {pid, 1000, 1000}
  │  (Intra-UID launcher provenance binding remains UNRESOLVED / HYPOTHETICAL;
  │   current request contract has no attestation field and no verified launcher identity)
  ▼
[Link 7: Authoritative Mutation Execution]
  │  Dispatcher journal commits pre-mutation intent -> Oracle clearance ->
  │  LiveResolveCanaryAdapter.execute_live_canary_action()
```

### 5.2 TOCTOU and Mutable Dependency Analysis
1. **Entrypoint Script TOCTOU (Closed via Descriptor Sealing):**
   - Opening via descriptor once, computing SHA-256 on that descriptor, and executing from that write-sealed descriptor eliminates the entrypoint file TOCTOU race.
2. **Mutable Python Virtual Environment (`.venv`) Gap:**
   - In Python, the entrypoint script imports modules from `.venv/lib/python3.12/site-packages/`. Even if `run_gate6.py` is sealed in memory, a local process under UID 1000 could mutate files in `.venv/` to execute arbitrary code during `import src.services.live_resolve_canary_adapter`.
   - *Requirement:* A production supervised launcher must package dependencies into an immutable, sealed container (e.g. read-only zipapp, PyInstaller sealed binary, or squashfs mount).
3. **Dynamic Shared Object (`fusionscript.so`) Gap:**
   - DaVinci Resolve's scripting library resides at `/opt/resolve/libs/Fusion/fusionscript.so` (owned by `root:root`). Because system directories under `/opt` are protected against user writes, the native library is secure against UID 1000 tampering, provided `LD_LIBRARY_PATH` is sanitized by the launcher.

---

## 6. Comprehensive Fail-Closed Behavior & Target Lockdown Specification

The process-binding architecture strictly adheres to fail-closed principles: **no target mutation occurs unless the adapter mutation boundary was already crossed; if a failure occurs before the mutation boundary, execution aborts with zero target mutation; once mutation may have begun, the system persists fail-closed in-doubt state (`IN_DOUBT_LOCKED` and `.in_doubt` marker),** consistently with accepted DEP-03 recovery semantics (BLOCKER-B-PROCESS-BINDING-R1-05).

| Failure Phase | Trigger Condition | Technical Detection | Architectural Action & Target Disposition |
| :--- | :--- | :--- | :--- |
| **Candidate Acquisition** | Script file missing, unreadable, symlink, or directory. | `open(..., O_NOFOLLOW)` returns `ENOENT`, `EACCES`, or `ELOOP`. | Launcher aborts immediately; exit status non-zero; zero admission attempt; zero mutation. |
| **Digest Mismatch** | `SHA256(fd) != record.reviewed_candidate_sha256`. | Inode hash comparison fails. | Launcher aborts; security tampering alert logged; zero admission attempt; zero mutation. |
| **Memory Sealing Failure** | Kernel fails `memfd_create` or `F_ADD_SEALS`. | `fcntl` returns `EINVAL` or `ENOSYS`. | Launcher aborts fail-closed; unsealed execution strictly prohibited; zero mutation. |
| **Environment Anomaly** | Unexpected `PYTHONPATH` or `LD_PRELOAD` detected. | Environment sanitization assertion fails. | Launcher sanitizes or aborts; untrusted environment rejected; zero mutation. |
| **Peer Credential Mismatch**| Connecting socket peer UID != 1000 or PID unexpected. | Dispatcher `SO_PEERCRED` check fails. | Dispatcher closes socket immediately (`ERR_GATE5_PEER_REJECTED`); zero clearance; zero mutation. |
| **Gate 5 Sig Invalid** | Tech Lead signature verification fails against anchor. | PureEd25519 verification returns `False`. | Dispatcher rejects admission (`ERR_GATE5_SIGNATURE_INVALID`); zero clearance; zero mutation. |
| **Client Abort Pre-Mutation** (BLOCKER-B-PROCESS-BINDING-R1-02) | Candidate runner terminates before Dispatcher mutation clearance. | Socket EOF / `POLLHUP` observed on admission connection. | Dispatcher aborts admission transaction; records `ABORTED` in journal and notifies Oracle; releases ephemeral exclusion lock (`.locks/.lock`), while target remains durably marked `EXHAUSTED` / terminal under accepted journal lifecycle rules (`is_target_locked_or_in_doubt()` rejects redispatch; zero automatic reservation clearing or redispatch); zero target mutation. |
| **Crash In Mutation Window** | Candidate runner terminates while adapter call in flight. | Connection dropped while `ACTION_PENDING` active in journal. | **CONSERVATIVE LOCKDOWN:** Journal forces target state to `IN_DOUBT_LOCKED`; `.in_doubt` marker persisted; zero automatic retry; zero unlock. |
| **Oracle Rejection** | Signer Oracle returns `REJECTED` or `FAILED`. | IPC response status != `ACCEPTED`. | Dispatcher fails closed; records failure; target lock retained pending audit; zero mutation. |

---

## 7. Contract Representation Assessment & Unresolved Prerequisites

### 7.1 Current Wire Contract Capabilities & Limitations
- **[IMPLEMENTED-CONTRACT]:** `src/core/contracts/dispatcher_admission_contracts.py` defines:
  ```python
  class DispatcherAdmissionRequest:
      schema_version: int
      request_id: str
      approval_envelope: SignedApprovalEnvelope
      operator_consent_envelope: SignedOperatorConsentEnvelope
      preflight_evidence_envelope: SignedPreflightEvidenceEnvelope
      execution_workorder_record: SignedExecutionWorkorderRecord
  ```
- **[IMPLEMENTED-CONTRACT]:** The accepted contract transfers strictly the four authorization envelopes. It contains:
  - **Zero fields** for runner process ID, process creation timestamp, or process start-time (`stat.st_ctime` or `/proc/<pid>/stat:starttime`).
  - **Zero fields** for a Supervised Launcher attestation token or signature.
  - **Zero fields** for candidate runtime packaging descriptors.

### 7.2 Strict Non-Modification Policy
- **[ACCEPTED-DESIGN]:** In adherence to Plan 07B7 Phase 2 invariants, no contract schemas or wire fields may be altered or extended under this design workorder.
- **[UNRESOLVED-PREREQUISITE]:** Any future protocol extension to carry process-attestation data must be formalized under a dedicated, separately reviewed contract workorder (`WO-GATE5-ADMISSION-CONTRACTS-R2`).

### 7.3 Enumeration of Unresolved Prerequisites
To transition Prerequisite B-2 towards operational implementation, the following sequential items remain unresolved:
1. **Prerequisite B-2.1: Supervised Launcher Daemon Specification & Intra-UID Attestation:**
   - Formal architecture for the session-confined launcher under `interactive_user:1000`. Must resolve the intra-UID attestation boundary, provide an enforceable identity and key custody mechanism against same-UID processes, and bind to the exact connected child process (BLOCKER-B-PROCESS-BINDING-R1-01, BLOCKER-B-PROCESS-BINDING-R2-01). Any additional authority, trust anchor, or execution principal must remain a separately authorized prerequisite.
2. **Prerequisite B-2.2: Admission IPC Contract Extension (or Process-Binding Protocol):**
   - Specification of how the Dispatcher verifies the connecting peer's supervisory provenance.
3. **Prerequisite B-2.3: Immutable Dependency Packaging (Sealed Zipapp / Standalone Binary):**
   - Packaging specification converting the candidate runner and its `.venv` dependencies into an immutable, single-file sealed executable to close the mutable site-packages dependency gap (BLOCKER-B-PROCESS-BINDING-R1-03).
4. **Prerequisite B-2.4: Candidate Harness Realignment (`run_gate6.py`):**
   - Formal realignment of `run_gate6.py` to reference the canonical trust anchor `/etc/resolveflow/tech_lead_public_key.pem` and interface with the supervised launcher.

---

## 8. Decision Matrix & Recommended Next Workorder

### 8.1 Comparative Mechanism Decision Matrix

| Evaluation Dimension | Family (a): Supervised Launcher | Family (b): Descriptor `fexecve` | Family (c): Sealed `memfd` | Family (d): Cgroup / Systemd Scope |
| :--- | :--- | :--- | :--- | :--- |
| **Direct Code-to-Process Binding** | **UNRESOLVED / CONDITIONAL (BLOCKER-B-PROCESS-BINDING-R2-01)**: Cannot satisfy same-UID adversary threat model without independently enforceable identity, protected attestation key/channel, and exact child binding. | **PARTIAL (Medium)**: Binds entrypoint binary; interpreter imports bypass. | **HIGH (Entrypoint only)**: Kernel-enforced write-sealed backing file descriptor (`F_SEAL_WRITE`). | **NO**: Cgroup tracks PID lifecycle only; zero code attestation. |
| **Dependency Protection** | **CONDITIONAL (if packaged)**: Requires sealed zipapp or bundle specification. | **NO**: Unsealed filesystem modules loaded dynamically. | **PARTIAL**: Entrypoint sealed; native `.so` libraries loaded from disk. | **NO**: Zero filesystem integrity enforcement. |
| **Three-Principal Model Alignment** | **PERFECT**: Operates within UID 1000; Dispatcher remains UID 902. | **HIGH**: Standard POSIX execution semantics. | **HIGH**: Standard Linux anonymous file primitive. | **HIGH**: Standard systemd process containment. |
| **DaVinci Resolve GUI Compatibility** | **PERFECT**: Preserves interactive X11 / display server ownership. | **HIGH**: Inherits user session environment. | **MEDIUM**: Must resolve native `fusionscript.so` dynamic linkage. | **PERFECT**: Manages session process group. |
| **Architectural Verdict** | **RECOMMENDED ARCHITECTURAL DIRECTION (HYPOTHETICAL / UNRESOLVED)** (Must be paired with Family c and requires resolving intra-UID attestation and child binding before implementation). | **SUPPORTING PRIMITIVE ONLY** (Insufficient alone for Python). | **RECOMMENDED INTEGRITY PRIMITIVE** (Inside Supervised Launcher). | **SUPPORTING CONTAINMENT ONLY** (Cannot satisfy B-2 alone). |

### 8.2 Architectural Synthesis & Strategic Recommendation
The conceptual architecture for Gate 5 Runtime Process Binding points towards a **Session-Confined Supervised Launcher (Family a) utilizing Sealed In-Memory Execution (Family c)**:
- The Supervised Launcher would execute within the `interactive_user:1000` domain to preserve Resolve GUI/display ownership.
- The launcher verifies `SHA256(script) == reviewed_candidate_sha256` descriptor-anchored, clones the script into an anonymous write-sealed `memfd` (`F_SEAL_WRITE`), and launches Python from the sealed descriptor.
- *Boundary Acknowledgment & Explicit Non-Satisfaction of Threat Model (BLOCKER-B-PROCESS-BINDING-R2-01):*
  - This architecture binds entrypoint source integrity without TOCTOU, but does **not** satisfy the sibling-process threat model under UID 1000 because intra-UID launcher attestation and private key custody against same-UID processes are unresolved.
  - Complete execution closure attestation, independently enforceable launcher identity, protected attestation channel, exact child binding, and wire contract fields remain unresolved prerequisites that require separately authorized future workorders.

### 8.3 Recommended Follow-On Workorders
1. **Next Immediate Sequence:** Prerequisite B-3 (Dispatcher Admission Server Daemon & Signature Engine Architecture Design).
2. **Follow-On Packaging Workorder:** Reconciling candidate harness path realignment in `run_gate6.py` and packaging specification under Blocker D.

---

## 9. DAG Alignment, Governance & Safety Invariants

- **Blocker Independence:** Blocker B is an independent architectural prerequisite under Plan 07B7 Phase 2. This process-binding design proposal does not depend on, resolve, or alter:
  - **Blocker A (Gate 4 Preflight Custody):** Remains strictly `NO_DECISION_RECORDED_PENDING_FULL_QUORUM` and `STRICTLY OPEN`.
  - **Blocker C (Orphan State Governance):** Prerequisite C-1 remains `STRICTLY OPEN`; C-2 remains non-operational implementation artifacts only.
  - **Blocker D (Host DAC Provisioning):** Prerequisite D-1 remains `OPEN / UNVERIFIED`.
- **Node WO5 Isolation:** Node `WO5 (Live Canary Execution Directive)` remains **COMPLETELY DISCONNECTED** in the execution DAG, with strictly zero incoming edges and strictly zero outgoing edges.
- **Safety Invariants Unconditionally Preserved:**
  - `ready_to_execute = False` is strictly maintained across all components.
  - Gate 6 strictly remains `NOT ACCEPTED` / incident-only.
  - Gate G07, Milestones M1, M2, and M3 strictly remain `OPEN`.
  - Target project `RF_TRIAL_AEC8F0A7A365D5F5` and timeline `RF_TRIAL_TL_AEC8F0A7A365D5F5` on `DISPLAY=:1` were outside the scope of this workorder and were not inspected or contacted.
  - Zero host modifications, zero key generation, zero credentials, zero network, zero socket IPC, zero launcher implementation, and zero Resolve API calls occurred.
