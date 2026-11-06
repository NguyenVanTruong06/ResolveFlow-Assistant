# G07-07A8-D — Codex review

Reviewed 2026-11-02T16:56:53+07:00: **CHANGES_REQUIRED** before implementation. Report/design hashes match and DESIGN ONLY boundary is consistent. The read-only operator presentation and bounded sanitized diagnostics are useful, but three architecture points need correction.

1. **Resolve thread affinity:** Section 4.1 calls `query_active_timeline_authority()` directly from the proposed RecoveryAssessmentWorker QThread. This conflicts with the accepted single-thread ResolveGateway rule. Resolve authority must be obtained through the existing serialized gateway / approved owner thread, then passed as an immutable snapshot to the recovery worker; if unavailable, show target identity as unavailable and classify conservatively.
2. **Do not equate I/O failure with corruption:** EACCES/EIO/lock/read interruption do not prove artifact corruption. Keep distinct `UNREADABLE/ASSESSMENT_FAILED` diagnostics or an explicitly conservative unavailable state; never claim forensic corruption from permission/transient errors.
3. **UI actions must not imply forbidden execution:** “only Close” is fine for active lock, but refresh must remain read-only, must not acquire and retain a lock, and any future operator confirmation is informational only. Remove unsupported “normal execution” and “timeline nguyên vẹn 100%” claims; a clean receipt is app-recorded status, not independent timeline verification.

Next: G07-07A8-D-R1 design correction only. After correction, the proposed three-module UI slice may be reviewed again.
