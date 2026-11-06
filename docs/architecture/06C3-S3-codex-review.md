# G06-06C3-S3-I — Codex independent review

2026-10-30. Verdict CHANGES_REQUIRED. Report SHA256 e6d2d9a65ce80482e17d7fdd088c737ad82ca8faf93f3c140fafd90b65f3c25b.
Fresh prescribed suites:69regression tests/18.69s/exit0;13harness tests/0.91s/exit0. Evidence .bridge/ux-evidence/G06-06C3-S3-CODEX-REVIEW/probes.json. Ten independent current-input/authority/accounting probes fail despite the positive test matrix. S1/S2 remain accepted; S3 not accepted, no desktop/G07 authorization.

## A — Editorial approval is bound to cached proposal, not current inputs
- Build+approve valid proposal; change current creative context, call notify_creative_context_changed, then click stale widget approval again: approval is created. Widget stale branch leaves button enabled; _on_approve_clicked doesn't reject stale. Page _on_proposal_approved hashes only _current_proposal, never current plan/context/source/matching state.
- Change _video_plan_data in-place without signal then approve old event: old proposal approved. Change supplier video_paths to different source set: old proposal approved. Required current-binding checks cannot depend only on UI signals.
- _get_current_context returning{} still builds a new empty-creative proposal because .get(... ) or{} supplies defaults. Invalid/unavailable context must deny, not erase requirements.
- Inject verify_proposal_approval_binding=False: approval still stored, as return value is ignored.
- Rebuild error only sets widget error, leaving old page proposal/approval fields; stale approvals must not survive failed rebuilds.

## B — Builder fails closed-schema/accounting promises
- Main cut end10000 beyond loaded duration accepted; missing frame evidence then permits editorial approval of invalid primary bounds. Check finite seconds against known loaded metadata without pretending verified frames/freshness.
- broll_inserts=[123] is silently skipped, proposal accepted with0omissions. Existing core schema validates generic inserts structure only; builder must validate every consumed row/status.
- Raw plan has valid1B-roll demand, typed sidecar same revision+manifest but entries=(): proposal accepted with0assignments/0omissions. Bind complete demand/result bijection, not just claimed revision strings/type.
- manifest clip duration10**400 causes uncaught OverflowError in math.isfinite, expected controlledValueError. Raw path str(), bool duration->1, invalid duration skip, string.strip IDs and list materialization before bounds are additional source-reviewed contract issues to cover together.
- Default candidate selection finds first online before excluding reconnect/guidance/ref-only candidates; a good later candidate can be incorrectly omitted. Validate explicit choices and candidate provenance, not getattr fallback defaults. Sidecar requires expected revision+manifest proof and strict revalidation of mutable-bypass typed inputs.

## C — Explicit invalid frame evidence is downgraded into absence
Provide invalid _source_frame_evidence_table and valid FPS: assembler error is caught and geometry_result=None; approval remains enabled as ordinary unresolved. Distinguish genuinely absent evidence (editorial unresolved allowed) from invalid explicit evidence (block). Widget also trusts any object's status and can show foreign/forged VERIFIED geometry without proposal/revision binding. No frame evidence may be fabricated to satisfy tests.

## D — Surface completeness and test evidence
candidate_selected is declared/wired but never emitted by a real candidate-choice control. Current T02 manually emits candidate_omitted; doesn't prove choose/omit/restore interaction. Provide a compact real offered-candidate selector, scoped to active validated demand, and test QTest through UI. Current T04 uses MagicMock.status rather than bound geometry; T05 checks stale label but doesn't try clicking again. Use the reproduced negative controls and real positive producer flow.
Harness main skips sys.exit when retaining workers but then reaches EOF: interpreter still exits. Strong global refs alone don't survive process exit. Correct actual live-supervisor/app ownership with safe fake entrypoint test; no deliberately hung real worker. This is a small nonproduction correction grouped in same task, not another design round.

Next G06-06C3-S3-R1 same3production modules+existing harness/test. Close all A/B/C/D in one red-green matrix; no core schema/geometry/source/provider/Resolve expansion. No self acceptance or clean-worktree claims.
