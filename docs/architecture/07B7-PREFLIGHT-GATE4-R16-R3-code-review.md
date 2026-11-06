# Static Review — G07-07B7-PREFLIGHT-GATE4-R16 Revision R3

**Review date:** 2026-11-05  
**Decision:** `CHANGES_REQUIRED`  
**Review mode:** Static review of the workorder, harness, signoff, audit log, screenshot, and report. No harness/tests were run and no Resolve API/IPC was used.

## Evidence reviewed

| Artifact | SHA-256 observed | Result |
|---|---|---|
| `.bridge/task.md` | `0ec85dde99b17848a5cd3bdc060df47c6e1c4301182c07cfcded3925154c9f00` | Matches issued R16-R3 workorder |
| `run_preflight.py` | `28fce4a0e11b085cb256f6dc4c230953bc80f40d3d46228032a0ca7f499b17d4` | Matches handoff |
| `execution.log` | `606d9e94634be1f095444c899054c669216567c29e132afb52edf5606856665b` | Matches hash in signoff and handoff |
| `resolve_preflight.png` | `6db1ddafd1e5086091291a4a129f130b463f1a2a7c2760e704dbe7e6fc019ede` | Matches handoff; image shows the named project/timeline, test media in the Media Pool, and V1/A1 with 0 clips |
| `.bridge/preflight-signoff-GATE4-R16-R3.json` | `519058c978b59e5019ee5615ec0cd4f87f8669ec4b5dd6e1178a404cce4bddbd` | Matches handoff |
| `.bridge/report.md` | `6c19273eef9804378935033b960216ef1aa21eae99994d2bc19eee7f15a51a4e` | R16-R3 report present |

The signoff records the challenge completion within its stated TTL, the target UIDs, 1920x1080/24 fps, one video and one audio track, empty V1/A1, equal start/end frames, zero markers, and `UNVERIFIED_OPERATOR_NOT_PRESENT`. The audit log shows `AUDIT_START` followed by write-ahead intents and outcomes, with the terminal record reporting 25 calls and zero blocked/dispatch/insertion attempts. This review did not independently recompute the full hash chain or invoke the project verifier.

## Findings

### G4-R16-R3-01 — MEDIUM: Harness deletes a pre-existing audit directory

`run_preflight.py` lines 83–85 checks `POSIX_AUDIT_DIR.exists()` and calls `shutil.rmtree(POSIX_AUDIT_DIR)` before creating the directory. R16-R3 requires exclusive creation and prohibits deletion. If the nonce-derived path already exists, the harness removes its contents instead of failing closed. This can destroy pre-existing evidence or other data at that path and defeats the intended exclusive-create guard.

**Required:** Remove the recursive deletion path. If the directory exists, reject the run without modifying it. Preserve the completed R16-R3 log and do not rerun Resolve under this review.

### G4-R16-R3-02 — MEDIUM: Handoff call count conflicts with audit log

The handoff states `Total calls = 20, Allowed = 20`. The submitted log contains 25 `API_CALL_INTENT` records and 25 matching `API_CALL` records; its `AUDIT_TERMINAL_SUCCESS` also states `total_calls: 25` and `allowed_calls: 25`. The signoff omits an explicit counters object, leaving its narrative inconsistent with the terminal audit record.

**Required:** Correct the report/signoff documentation append-only, using the terminal counters as the total (25 allowed, 0 blocked, 0 dispatch, 0 clip-insertion attempts). If 20 refers to a narrower subset, label and define that subset separately. Do not alter the original audit log or screenshot.

## Decision and lifecycle

- Gate 4 R16 Revision R3: `CHANGES_REQUIRED` pending the harness safety correction and documentation reconciliation.
- The screenshot and readbacks support that the target was created and appears empty, but the workorder is not accepted in this review.
- The human visual-attestation predicate remains `UNVERIFIED_OPERATOR_NOT_PRESENT`; do not represent it as passed or claim full Gate 4 acceptance.
- No new Resolve run, nonce, dispatch, Gate 5, or Gate 6 action is authorized by this review.
- `ready_to_execute = False`; G07 and M1/M2/M3 remain `OPEN`; Gate 6 remains `NOT ACCEPTED` and incident-only.

---

## Revision R2 Re-review Addendum

**Review date:** 2026-11-05  
**Decision:** `ACCEPTED_FOR_REMEDIATION_ONLY`  
**Review mode:** Static artifact review only. No harness, tests, Resolve SDK, or IPC were run.

### Rechecked artifacts

| Artifact | SHA-256 observed | Result |
|---|---|---|
| `.bridge/ux-evidence/G07-07B7-PREFLIGHT-GATE4-R16-R3/run_preflight.py` | `7138b6bc1937b05e81d19a08ad2f8a49f9c608383d419accd0bd022981e03eb4` | Matches submitted R2 hash |
| `.bridge/preflight-signoff-GATE4-R16-R3.json` | `316c97cdfb461af0242f9bc5020ed628ce18ee7a0224789cdbc315acea828882` | Matches submitted R2 hash |
| `execution.log` | `606d9e94634be1f095444c899054c669216567c29e132afb52edf5606856665b` | Preserved unchanged |
| `resolve_preflight.png` | `6db1ddafd1e5086091291a4a129f130b463f1a2a7c2760e704dbe7e6fc019ede` | Preserved unchanged |

### Finding disposition

- **G4-R16-R3-01 — Resolved.** The harness no longer calls `shutil.rmtree`. It refuses an already existing audit directory and then creates the nonce-scoped directory with `exist_ok=False`; the creation itself also fails if the path is occupied. No evidence deletion or Resolve rerun was performed.
- **G4-R16-R3-02 — Resolved.** The signoff now carries an explicit counters object: 25 total, 25 allowed, 0 blocked, 0 dispatch attempts, and 0 clip-insertion attempts, with an 8 setup / 17 readback breakdown. These values match the preserved terminal record in `execution.log`. The report corrects the prior “20 calls” handoff statement and documents the reconciliation append-only.

The audit-log and screenshot hashes remain unchanged, and the screenshot/readback evidence still supports the reported empty timeline state. The operator attestation remains `UNVERIFIED_OPERATOR_NOT_PRESENT`; therefore this review accepts the submitted remediation and scoped preflight evidence only. It does **not** grant full Gate 4 acceptance or change `ready_to_execute = False`, G07/M1/M2/M3 `OPEN`, or Gate 6 `NOT ACCEPTED` / incident-only.

---

## Gate 4 Attestation and Closeout Addendum

**Recorded:** 2026-11-04T19:10:33Z (UTC)  
**Gate 4 decision:** `ACCEPTED_FOR_PREFLIGHT_ONLY`

After the R16-R3 remediation review, the human operator directly supplied this visual attestation in chat at `2026-11-04T19:07:31Z`:

> “Tôi xác nhận đã nhìn thấy trên màn hình Resolve: Project và Timeline rỗng đúng như hồ sơ.”

The attestation is associated with the R16-R3 target in the immediately accompanying handoff: project `RF_TRIAL_AEC8F0A7A365D5F5` (UID `4fd4272d-f0bc-445c-9490-5723cd6831c2`) and timeline `RF_TRIAL_TL_AEC8F0A7A365D5F5` (UID `ba6a6775-8ec4-41a4-bf2f-704de902e68d`). It is recorded as an operator observation at that timestamp, not backdated into the original signoff or execution log. The signoff, log, and screenshot remain unchanged.

Together with the already reviewed preflight predicates and resolved R16-R3 findings, this closes Gate 4 for this target and this trial only. The Gate 2 authorization remains bounded to exactly one operator-supervised `INSERT_CLIP`; no broader operation is approved. Gate 5 workorder `G07-07B7-PREFLIGHT-GATE5-R1` defines that single action and its fail-closed preconditions. This decision does not accept Gate 6/Gate 7 or change `ready_to_execute = False`, G07/M1/M2/M3 `OPEN`, or Gate 6 `NOT ACCEPTED` / incident-only.
