# G07-07A5-D — Codex design review

Reviewed 2026-11-02T13:17:32+07:00. **DESIGN_ACCEPTED_WITH_BINDING_CORRECTIONS**. Report SHA256 `f8f42267faf60645ea0284037d425accbaebf7e0709a895b34e96e15094afe4b`; design SHA256 `11f40ec27ba7063ab2c8f43bcc7825606a87fee92080ae71f085a09983c26d95`.

Review confirms useful separation of editorial approval, source proof, Resolve target observations and locked execution state. G07-07A5-I may proceed with the three proposed production modules, subject to the binding scope below.

## Binding corrections for implementation

1. **No contradiction with 07A4 C01/C03/C04.** Keep the ReviewPage-owned preapproval service as-is. Do not modify ReviewPage, PreflightWorker, SourceShutdownGuard or approval contracts; do not share or move cache ownership across Step3/Step4. Deep source verification may be opt-in only inside the existing readiness request path where authority is already present. Missing expected full SHA/stream evidence must produce an honest blocked/unverified result, not synthesize approval data.
2. **Timeline FPS has no fallback.** Implement the Resolve query as a read-only, explicit action-time observation. Require active Resolve, project and timeline, parse only supported exact representations to `RationalFPS`; reject missing, malformed, unsupported, bool, float-coercion and ambiguous values. Never use 24/30 defaults or source FPS as timeline FPS. Confirm the actual value format from Resolve API/runtime contracts; do not promise every listed decimal unless exact mapping is unambiguous.
3. **No target-track claims without API proof.** This slice may report only fields actually queried and validated. Do not claim collision/lock readiness if the current API path cannot prove it. Do not query/modify timeline content beyond the specified read-only authority calls.
4. **UI truth and failure safety.** BuildPage must distinguish editorial approval, deep source evidence, target observations and execution lock. Keep `ready_to_execute=False` in every result; no green “ready to build” state. Query errors, no Resolve, no active timeline and stale identity fail closed with clear diagnostics.
5. **Safe lifecycle.** Preserve current request/worker identity, busy/cancel/shutdown semantics and exact-one terminal behavior. No unbounded wait on the Resolve API; if calls can block, document and test bounded behavior without leaving QThread alive.
6. **Scope and tests.** Exact production allowlist: `src/ui/director/director_window.py`, `src/ui/director/pages/build_page.py`, `src/core/resolve_bridge.py`. Existing eight-file acceptance suite plus finite tests for the new pure/read-only query and UI wiring. No Resolve mutation, timeline insertion, receipt/journal implementation or `ready_to_execute=True`.

The design's mutation receipt/journal and rollback section is future architecture only; it is not authorized for this implementation slice. No desktop acceptance is implied by offscreen tests.
