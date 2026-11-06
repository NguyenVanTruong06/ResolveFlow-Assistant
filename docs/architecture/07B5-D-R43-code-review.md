# G07-07B5-D-R43 — Codex Static Design Review

**Decision: CHANGES_REQUIRED**  
**Scope:** Static review of Section 2.6 for R43. No production or test files were changed. No prototype validator, pytest, Resolve, IPC, FusionScript, application, AI, or network execution was performed.

## Reviewed inputs

Recomputed SHA-256 values match the submitted handoff:

- `.bridge/task.md`: `48b07689d170bd98e85b1b5f528832409c51ec4c096e8f667d8739200392bbb5`
- `docs/architecture/07B5-D-R42-code-review.md`: `17bbf91910715322909a1bb8eb67fee8e30bfe27649850e16dacfcf0f661f971`
- `docs/architecture/07B5-isolated-resolve-acceptance-gate-design.md`: `457e611da4fc3e64cd4fc3f197120b9474f7721acf9f47c659abaa867e79e9c3`
- `.bridge/report.md`: `57b7087d3a22f15402bdab35983e3d8a4235ae54af66b893d323770eb799fa53`
- `scratch/test_r43_validator.py`: `0454c7bb3471b20f46ca62c3a224cc573285391837f714e6b83d3e89a0a50d4d`

## Finding

### R43-01 — Ambiguous-lockout reconciliation trusts a caller-supplied boolean — HIGH

`reconcile_ambiguous_timeline(timeline_uid, confirmed_unlocked=False)` clears the `CLOSE_OUTCOME_UNKNOWN` entry and sets the timeline state to `RELEASED` whenever `confirmed_unlocked` is truthy (around lines 2530–2556). The method does not bind this assertion to an operator identity, an explicit confirmation record, or a one-time challenge for the affected timeline. A caller can pass `True` (or another truthy value such as the string `"false"`) and remove the fail-closed admission barrier; the next `acquire_target_lock()` is then permitted. The function's docstring and matrix call this operator confirmation, but the API only receives an unauthenticated Python value.

Because the lockout is the protection retained after an ambiguous OS descriptor close, clearing it is an authority-bearing operation. A bare truthy argument does not establish that an operator reviewed the affected timeline or intentionally authorized the reset.

**Required correction:** Replace the raw boolean with an explicit, coordinator-validated reconciliation authorization bound to the exact `timeline_uid`, current `CLOSE_OUTCOME_UNKNOWN` incident/state, and a fresh one-time challenge; or define an equally concrete operator-mediated gate and its trusted caller boundary. If a boolean remains as an additional field, require `type(confirmed_unlocked) is bool`, but do not treat that type check alone as proof of operator authorization. Add negative cases for truthy non-bools, wrong-timeline/stale/replayed confirmation, and an untrusted direct caller; assert the registry and pre-admission lockout remain unchanged.

## R42 finding reviewed

R43 adds the pre-admission gate before `os.open()`/`flock()`, preserves unresolved entries, and prevents a fresh acquire from overwriting `CLOSE_OUTCOME_UNKNOWN`. It also makes the old descriptor unreachable after an ambiguous close. The remaining concern is authority to clear that terminal state through `reconcile_ambiguous_timeline()`.

## Decision and safety boundary

R43 is **not accepted** until R43-01 is resolved. Keep `ready_to_execute=False`; keep Gate G07 and milestones M1/M2/M3 OPEN. This review grants no live Resolve authority and does not authorize implementation against a real Resolve instance.

The reported prototype validator result is a handoff claim only; it was not run or independently verified in this static review.
