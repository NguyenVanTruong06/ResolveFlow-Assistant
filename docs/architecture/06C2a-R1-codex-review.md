# G06-06C2a-R1 — FPS/range verified; Fraction residual

2026-10-30. SHA256 report verified: `fd49d87393df6920843d0fcb6e091803679035fd80aadbe00376016d73acfdc0`.

Fresh Codex command: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/core/geometry/test_frame_geometry_calculator.py tests/core/contracts/test_time_contracts.py -q` → **60 passed in 0.32s, exit 0**. Re-run original 14 probes: **14/14 controlled error codes PASS**. Owned FPS copies are used before arithmetic; range type checks precede bounds; purity claims corrected. A/B from prior review are closed, do not reopen them.

## One remaining validation family: Fraction members

`frame_geometry_calculator.py:_validate_and_convert_fraction` copies a nominal Fraction without checking its numerator/denominator. `frame_geometry_contracts.py:validate_speed_instance` checks member types but compares caller speed before requiring denominator >0. A malformed Fraction retains its nominal type after object.__setattr__, exactly like the FPS/range boundary cases covered by this contract.

Confirmed counterexamples in `.bridge/ux-evidence/G06-06C2a-R1-CODEX-REVIEW/probes.json`:
- `f=Fraction(1); object.__setattr__(f,'_denominator',0)`: calculator applied_speed and direct DTO raise **ZeroDivisionError** instead of controlled ValueError.
- `_numerator='1'`: calculator applied_speed raises **TypeError**; direct DTO already rejects with ERR_INVALID_SPEED.
- `_denominator=True`: calculator **accepts** malformed speed; direct DTO rejects correctly.
- `_denominator=-1`: calculator returns ERR_INVALID_SPEED; direct DTO reaches Formula B and reports ERR_INVALID_RECORD_DURATION, the wrong field.
- Same copying path is used for input/output seconds; additional probes cover those two arguments. Close this single family across all Fraction inputs in one patch, not per-field follow-up tasks.

This is validation of malformed nominal inputs, not a failure of Formula B on ordinary valid numbers. Do not change exact arithmetic, caps, public APIs, FPS/range policy or accepted time_contracts. No new arbitrary subclass/security threat model required. Strict raw Fraction components, canonical owned copy, then normal domain checks is sufficient.

## Verdict / next work

FPS/range repair **VERIFIED**; overall C2a **PENDING_FRACTION_BOUNDARY_FIX**. G06-06C2a-R2-THEN-06C2b-D combines the small residual with the next evidence-supplier design after finite tests pass. Part B is design only: source identity, exact metadata/CFR evidence levels, bounded cancellable acquisition and honest unknown state. No source probing or whole timeline implementation now.
