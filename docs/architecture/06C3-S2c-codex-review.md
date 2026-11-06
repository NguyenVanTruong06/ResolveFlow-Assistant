# G06-06C3-S2c — Codex review

2026-10-30. Verdict: CHANGES_REQUIRED. UI rollout not authorized yet. Report SHA256 cbe0fbdc6229fdcd30d9cd7567b462c5bf00758a4135a7cadc705cc3d95cd4ab.
Fresh finite6file group: 119 passed in11.47s/exit0. Evidence .bridge/ux-evidence/G06-06C3-S2c-CODEX-REVIEW/verification.json and pytest_observed.log. Passing suite does not close the following independently reproduced binding defects.

## R1-A: lost typed context field silently drops requirements
A real ProjectOverviewContextInput with creative brief renders correctly. Delete only its instance creative_context member with object.__delattr__. render_project_overview_prompt still returns ok=True and removes creative requirements from prompt. getattr(context_input, 'creative_context', None) around line274 reads the dataclass class default None after deletion. hasattr also cannot distinguish this from a well-formed legacy instance. Expected ERR_OVERVIEW_INVALID_CONTEXT; observed successful legacy downgrade. Constructor-based None/default legacy instances still have a real stored field and must remain accepted.
The new pure test test_tc04_forged_typed_fields_and_deleted_members_caught does not actually delete any member; it only sets invalid nested values.

## R1-B: lost artifact proof silently downgrades transport
Create valid creative PreparedProjectOverviewArtifact and matching transport: helper accepts as positive control. Delete artifact's instance creative_context_signature, remove same key from transport. validate_project_overview_result_against_artifact returns True, treating class default None as legacy. Expected False. Artifact __post_init__ validation is insufficient after frozen-instance deletion. A valid legacy artifact constructed with default/explicit None must remain accepted with legacy transport; do not reject all None values.
This consistency guard is app-owned evidence, not protection against arbitrary malicious Python code; nevertheless its public typed revalidation contract explicitly requires rejecting malformed DTOs. No file exploit/provider action is claimed.

## Boundary propagation
Worker currently captures duplicated overview creative field via getattr(..., None) at constructor and later replaces it from captured request. Reject missing new field on an existing typed overview input before that replacement can conceal corruption; constructor should store a controlled error and run emit invalid_snapshot before source/cache/formatter IO. Preserve allowed omission of the whole optional overview context and valid input instances whose field actually stores None/empty.

## Test evidence corrections, grouped with same R1
- TC07 named held-barrier has NO barrier: it changes caller raw dict then starts worker. Add actual held point and nested/request/duplicate mutation controls.
- TC01 'frozen control' compares current function against default/None/empty variants; no independent fixed legacy oracle. Add a validated frozen legacy expected digest/bytes with traceable prior-source provenance, keep None/empty equivalence separately. Never calculate expected using same current formatter under test.
- New worker test file lacks HOME/XDG/visual isolation and strong owner retention on failed cooperative wait. Reuse the small local safe helper pattern already accepted in other creative tests. No new framework or deliberate real leaked thread.
- Report per-file counts do not correspond to current source/collection; rely on actual fresh totals/counts, not assumed case-group labels. Do not claim tested member deletion/held barrier when code does not execute them.

Fix A/B and propagation in same2production modules; tests in new core+worker creative files with existing overview regression group. No additional design task or UI edits. Preserve exact valid None/empty legacy bytes and all existing source/ledger/creative/UI proof checks. Accepted S1/S2a/S2b, pure geometry/planner/metadata remain frozen. Next workorder G06-06C3-S2c-R1 HIGH.
