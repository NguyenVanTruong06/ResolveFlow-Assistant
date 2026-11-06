# G06-06C3-S2d — Codex review

2026-10-30. Verdict CHANGES_REQUIRED. Report SHA256 868bd3e01b00aa9135d3d670a385490862a13407e36cad56011afc2e4752c0e4.
Fresh prescribed6file suite:224 passed in77.09s/exit0. Source hashes match Worker report. Independent probes in .bridge/ux-evidence/G06-06C3-S2d-CODEX-REVIEW/probes.json reproduce3failures with valid positive terminal controls. Source/backend earlier gates remain accepted; S2d not accepted yet.

## A — User draft text lost on creative edit (P1)
Real ReviewPage.txt_batch_draft_input initially contains user JSON. Calling notify_creative_context_changed clears it to ''. Root cause: notify calls _invalidate_prepared_batches, whose txt_batch_draft_input.clear erases user input along with generated preview state. Report C6 only checks StylePage brief, not user's batch JSON. Preserve user draft text/history when invalidating creative-derived data. Generated prompts/previews may clear; stale actions must stay denied. Add positive controls through real StylePage signal as well as direct helper and getter/current-context mismatch paths.

## B — Missing pending proof accepted as legacy (P1)
With valid legacy current context, exact request/generation/intent/full signature, success terminal pending record lacking expected_creative_context_signature publishes prompt. A valid pending record with explicit None also publishes (correct positive control). Root cause pending_expected.get conflates missing member with explicit legacy None. Require field presence/type before success publication; missing proof is not authority for a legacy downgrade. Preserve well-formed explicitNone legacy behavior.

## C — Uppercase creative digest accepted (P2 contract regression)
Valid creative current context and pending lowercase digest: success result with uppercase digest still publishes prompt. Code accepts ABCDEF and uses lower() equality, contrary to canonical lowercase64hex backend contract. Validate BOTH expected and result strictly; no coercion/casefold/missing-value fallback. Current full UI signature/generation/request/intent and source/snapshot/artifact gates remain mandatory.

## D — Desktop evidence is functional positive evidence, cleanup NOT verified
Existing metrics record normal4case pass but cleanup={} in BOTH files; fault metrics verdict UNKNOWN. Fault returns2 before any real worker starts, so it doesn't exercise running-worker cleanup. Finally only closes+pumps0.2s then removes session unconditionally; no physical registry/join proof before deleting owned media/settings. Clipboard ownership set registers initial MIME only, not newly written prompts; unavailable/cleared current clipboard is not proof that content belongs to harness. Do not rerun unchanged harness to verify these claims.
Repair actual shared teardown, active-worker fault boundary, full MIME conditional ownership, complete env/session isolation and observable metrics. Keep standalone normal/fault runs bounded and no user apps/provider/Resolve. Screenshots currently use director.grab (WIDGET_RENDER), not screen-root capture; label accurately. Existing4positive cases can be inherited until targeted rerun, but cannot support a cleanup pass.

Next ONE grouped repair G06-06C3-S2d-R1: only review_page.py production expected; DirectorWindow/backend frozen. A/B/C regression tests, harness cleanup unit tests then safe targeted desktop run. No new design round/S3 work or scope expansion. If more dependency needed, show exact reproducer before editing.
