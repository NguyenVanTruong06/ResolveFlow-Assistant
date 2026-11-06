# G06-06C2c1-I — REQUEST_CHANGES, one-module consolidated checklist

2026-10-30. Report SHA256 `88f08c1804052e0a7ac7c0e619356dde7dbffec8e497db909be7788db99f0cf4` verified. Fresh focused suite **143 passed in0.47s, exit0**. C2b sentinel guard verified and parser gate accepted separately. Mocked tests do not yet prove the IO lifecycle promised by the workorder.

## A. Ownership / terminal read evidence

- `local_metadata_acquisition.py:434–439`: readers are constructed/started and deadline initialized after Popen but BEFORE the guarded block. A reader start failure escapes without terminating/reaping child or closing pipes. Controlled fake probe: RuntimeError('cannot start reader'), terminate_calls=0, wait_calls=0, child still running. No real child was spawned in this review.
- `:451–454` checks reader.exception before poll; `:469–494` joins readers and checks overflow, but never rechecks exception. Deterministic late-read probe: valid JSON prefix, child poll releases a pipe OSError, join completes, function **returns success despite read failure**. This is a false-success defect, not only an unusual input validation issue.
- Ownership must start immediately after spawn, support partially initialized/unstarted readers, and require terminal EOF/no exceptions after drains. Check cancellation/deadline during drainage; don't replace supervision with long blocking joins. Audit actual cleanup branch with fakes, not only a test that mocks the whole cleanup function.

## B. Input/payload contracts

- `_parse_format_duration` delegates strings to generic Fraction(raw_dur). That accepts fractions, signs, whitespace, Unicode digits, underscores and scientific exponents instead of specified ASCII unsigned decimals. Tests even assert '30000/1001' as valid. Safe probe '1e10000' is accepted from 7 characters; generic exponent syntax allows computational expansion unrelated to header byte cap. Reject lexically BEFORE Fraction conversion; never test huge exponents by allocating them.
- Explicit `format:null` currently becomes absent/unknown instead of ERR_INVALID_PROBE_PAYLOAD; distinguish missing format from present wrong type.
- `_check_cancellation` uses truthiness; None/0/1/'cancel' callback RETURNS are accepted/treated as cancellation. Only callback result bool is allowed (cancelled_fn=None itself remains valid). Wrong result type -> ERR_PROBE_EXECUTION_FAILED without invoking custom truthiness.

## C. Owned result and strict stat snapshots

- LocalHeaderAcquisitionResult only isinstance-checks header/freshness and keeps aliases; later object.__setattr__ on caller header/stat changes the result. Malformed typed header is accepted; relative result path is accepted. Revalidate/copy nested values and require lexical absolute result path without IO.
- Source stat uses getattr(..., int(st_mtime*1e9)) fallback; the default expression is evaluated even when ns exists. Missing ns fields currently get fabricated from floats and return success, contradicting strict platform-evidence policy. Validate real ns/identity fields at both checkpoints and report ERR_SOURCE_STAT_FAILED when missing/malformed; no float-derived fallback.

Evidence `.bridge/ux-evidence/G06-06C2c1-CODEX-REVIEW/{lifecycle-probes.json,boundary-probes.json}`. Owned temp probe sessions were removed, 0 real children spawned, 0 probe reader threads remain. Fix all A/B/C in one R1, one production module; don't reopen accepted C2a/C2b or add GUI/manifest integration.
