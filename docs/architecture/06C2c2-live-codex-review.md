# G06-06C2c2 live review — positive handoff retained, harness repair required

2026-10-30. Report SHA256 `28d7d3d61a65e089f7484767c80c1a1da56ebec3e1eae7a010749925bbf5a307` verified. Prior production code acceptance remains unchanged.

Fresh focused feature command: `QT_QPA_PLATFORM=offscreen PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/ui/director/test_prompt_preparation_metadata_slice.py -q` → **16 passed in5.92s, exit0**. Both chronology aliases are now controlled. Positive locator proof deliberately calls the original chronology helper with a separate locator fake; do not describe this as literally zero locator calls across every assertion. No actual binary is needed by those feature tests.

Reviewed Worker normal/fault metrics and harness source: actual child tables show generated fixture and real ffprobe processes, successful copy_video/batch handoff and corrupt-file denial. These positive observations are retained, not repeated in this review. They do not yet support the claimed robust cleanup envelope.

## Confirmed harness defects

Codex controlled main() boundary probes (zero actual child processes/QThreads) are recorded at `.bridge/ux-evidence/G06-06C2c2-FINAL-CODEX-REVIEW/harness-boundary-probes.json`:

1. Inject `OSError(CONTROLLED_SETUP_FAILURE)` during visual-cache mkdir after SessionOwner allocation: exception escapes, owner.cleanup is never called, session remains. QApplication, owner allocation, HOME/cache setup and instrumentation occur before the main protected try. SessionOwner constructor also allocates/hooks before caller can reliably own failure. Top-level application imports happen before per-session environment isolation; HOME is changed without restoration.
2. Inject controlled cleanup verdict CLEANUP_FAILED with session_deleted=False on an otherwise positive normal path: **main returns0**. The normal exit path is unconditional. This is a false success gate, independent of whether previous actual successful cleanup happened.

Additional closure requirements from the same existing workorder: unexpected-error branch returns before metrics persistence and calls cleanup twice; preserve evidence on every failure. Cancellation/fault barrier must prove worker.isRunning, cancel before releasing it, capture the terminal, and restore acquisition/Popen hooks after physical ownership resolves. Cleanup may stop children after initial worker wait but does not rejoin the worker afterward. Retain strong ownership when cleanup cannot finish.

## Missing measured details (same task, not new product scope)

Case1/2 currently check tolerances against hard-coded floats; they do not observe actual acquisition timeout/callback/thread/header results. Batch case only checks a nonempty source_ranges list, not equality with the manifest. Source invariant compares hash and size, not all five freshness fields; metrics omit the before/after values. Add these existing requested assertions in the same harness repair. Phrase header evidence as HEADER_REPORTED; seeded cache is not inference. Avoid blanket mapping of invalid metadata to0.0: malformed results are denied; only successful optional missing/unrepresentable values map unknown.

Next **G06-06C2c2-EVIDENCE-R1-THEN-06C2d-D**, HIGH: one grouped harness-only repair + bounded normal/fault verification, then design-only whole-plan geometry/evidence boundary. No src changes, no repeat of accepted production tests. Full consumer live acceptance remains pending; production code gate is not reopened.
