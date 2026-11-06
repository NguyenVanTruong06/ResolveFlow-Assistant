# Static Plan Review — G07-07B7-PLAN-D Revision R2

**Decision: CHANGES_REQUIRED**  
**Review scope:** Static review of `docs/architecture/07B7-live-canary-readiness-plan.md` Revision R2 against the accepted 07B5 design, accepted 07B6 implementation boundaries, and the R1 review findings. No tests, prototypes, Resolve, IPC, FusionScript, network, or AI were run.

## Summary

R2 closes the four R1 findings in substantial part: it distinguishes plan acceptance from explicit user authorization, specifies a separate live adapter while preserving the mock-only tripwire, aligns the action type and 120-frame geometry, and adds a pre-creation challenge plus the empty-timeline predicates. The plan remains design-only and does not itself authorize live execution.

Two safety-critical details still need to be made normative before this plan can be accepted: the live adapter must bind the *currently selected* project and timeline UIDs to the signed authorization at admission and immediately before dispatch; and the receipt/WAL/ledger terminalization order needs to match the accepted durability contract, with explicit crash repair for every boundary. There are also smaller consistency issues listed below.

## Findings

### B7-R2-01 — BLOCKER: Bind current project and timeline UIDs at both admission and dispatch

**Locations:** Plan §§3.2–3.6 and §5.2.1, especially lines 78–81, 105–113, 133–153, and 185–190.

The plan captures `project.GetUniqueId()` and `timeline.GetUniqueId()` into the signed authorization, but the dedicated live tripwire in §5.2 only requires a matching project name and challenge nonce. The final pre-IPC check in §4.2 checks only `GetStartFrame()`. Neither location explicitly requires a fresh `GetCurrentProject().GetUniqueId()` and `GetCurrentTimeline().GetUniqueId()` comparison against `auth.target_project_uid` and `auth.target_timeline_uid`.

A project name and nonce are not an immutable identity check; the active project or timeline can change after preflight, including to another object with a matching name. The adapter could then dispatch the signed action to a different target while passing the name and start-frame checks.

**Required correction:** Make exact project UID and timeline UID equality mandatory at initial admission and again at the last boundary immediately before `AppendToTimeline`. Any missing API value, query failure, or mismatch must abort before IPC. Keep the existing frame-drift check as a separate predicate. Add these identity checks to the acceptance matrix and to the future implementation/test requirements.

### B7-R2-02 — BLOCKER: Define crash-consistent ordering for receipt, FINISH, and ledger exhaustion

**Locations:** Plan §5.2.2, lines 191–195; §7.1, lines 241–247; and the Gate 3 requirements in §9, lines 309–311.

Section 5.2.2 orders completion as: persist and read back the terminal receipt, exhaust the target ledger, then append the clean `FINISH` WAL record. The accepted 07B5 completion contract instead places a durable WAL terminal record before receipt finalization and ledger exhaustion, with the target lock held until all required writes and read-backs succeed. R2 does not explain this changed ordering or define restart repair for crashes between these writes.

In particular, a crash after `EXHAUSTED` but before `FINISH` leaves a terminal ledger beside a nonterminal WAL; a crash after receipt publication but before ledger exhaustion leaves a receipt whose authoritative state is unclear. The plan does not specify how the new live adapter distinguishes and repairs these states without replaying Resolve or releasing the exclusion lock prematurely.

**Required correction:** Adopt the accepted 07B5 transaction order or explicitly propose a reviewed alternative that preserves its invariants. Add a crash-boundary table covering each durable write/fsync/read-back, the resulting WAL/receipt/ledger states, restart action, and lock disposition. State that no restart path redispatches the action and no lock is released until the complete terminal state is verified.

### B7-R2-03 — HIGH: Correct the project-name regex notation and nonce format contract

**Locations:** Plan §3.2, lines 71–77; §5.2.1, lines 187–190; §9 Gate 4, line 314.

The displayed regex at line 75 is written inside `\verb` with an escaped dollar anchor (`\$`), while the prose and later occurrences use `^RF_TRIAL_[0-9A-Z_]{8,32}$`. The nonce is specified as 16 hexadecimal characters, but the plan does not explicitly say whether it is canonical uppercase before embedding in a character class that only permits uppercase letters and digits. This leaves the human naming rule and the exact validator representation inconsistent.

**Required correction:** Show the regex as a literal code span `^RF_TRIAL_[0-9A-Z_]{8,32}$`, define the nonce’s canonical casing, and require exact nonce substring matching after that normalization (or define case-sensitive exact matching). Keep the sample name consistent with the 16-character nonce format.

### B7-R2-04 — MEDIUM: Remove the claim that the name/challenge prevents all wrong-project selection

**Location:** Plan §3.3, residual human-process risk paragraph, line 93.

The paragraph correctly acknowledges residual operator error, then says the regex and challenge nonce “will completely block” accidental selection of the wrong project. A name and nonce do not establish the identity or provenance of the selected Resolve project. This conflicts with the acknowledged human-process risk and overstates the protection provided by the mechanism.

**Required correction:** Describe the name/challenge as an admission filter only. State that immutable UID binding and explicit operator checks reduce selection risk, while residual human-process risk remains and is not eliminated by project naming.

### B7-R2-05 — LOW: Reconcile the gate count

**Location:** Plan §9 heading and introduction, lines 281–290, compared with the eight enumerated gates at lines 302–320.

The text calls this a “7-Gate Sequence,” while both the diagram and numbered list enumerate Gate 0 through Gate 7, which is eight gates.

**Required correction:** Call it an eight-gate sequence (or renumber the gates consistently).

## Safety-boundary confirmation

This review is limited to documentation. No source or test files were modified; no tests or prototypes were executed; no Resolve, IPC, FusionScript, network, or AI activity occurred. `ready_to_execute=False` and Gate G07/Milestones M1, M2, and M3 remain OPEN. This review does not authorize a live adapter implementation or live canary execution.
