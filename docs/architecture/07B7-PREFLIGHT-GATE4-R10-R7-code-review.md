# Static Code Review — G07-07B7-PREFLIGHT-GATE4-R10 Revision R7

**Review date:** 2026-11-04  
**Decision:** `CHANGES_REQUIRED`  
**Review mode:** Static review of submitted source, tests, workorder, report, and test log. Tests were not rerun. No Resolve SDK/UI/IPC was used.

## Evidence reviewed

| Artifact | SHA-256 observed | Result |
|---|---|---|
| `.bridge/task.md` | `3b84b7891b8eb298b4184f4fedf1b2121e3f19bbaeef2b14c2e811f2d165d10c` | R10 workorder verified |
| `src/tools/gate4_preflight_runner.py` | `2ae8e9c061628e5ec0b2baf07e02e940d107a129a49db63f61d55678b08e1908` | Matches submitted R7 hash |
| `tests/tools/test_gate4_preflight_runner.py` | `f3488501e222e19d4145d2fb8a5c4748548865f957f9059a031766e9425bee17` | Matches submitted R7 hash |
| `.bridge/ux-evidence/G07-07B7-PREFLIGHT-GATE4-R10/pytest.log` | `45c5e4e0995713ec1a5f19d1ae30f5dcc736d647d6defb7a1f287a4bdf4216b3` | Reports 13 passed, 0 failed, exit 0; not rerun |
| `.bridge/report.md` | `2be46f568e6667a05a55164995c8f7e37e719738be0e280f7907e5852aeb3f64` | R7 handoff and hashes present |

## Finding

### G4-R10-R7-01 — HIGH: Incident UID blacklist remains caller-mutable

The role allowlist mapping is now a `MappingProxyType` and `_setup_proxy_authority()` snapshots it into a closure, addressing G4-R10-R6-01. However, `BLACKLISTED_UIDS` remains a module-level mutable `set` (`src/tools/gate4_preflight_runner.py`, near the top of the module). `Gate4PreflightRunner.run()` consults that set while validating project and timeline UIDs. A caller can clear it or discard a known incident UID before the run, causing a blacklisted target to pass the current validation. The R7 test only protects `ALLOWED_METHODS_BY_ROLE`; it does not protect the blacklist.

**Required:** Store the incident UID denylist as an immutable `frozenset` captured by the runner's validation logic, and add a regression test proving external mutation/rebinding cannot cause a known blacklisted UID to pass.

### G4-R10-R7-02 — MEDIUM: `canonical_json_bytes()` is not RFC 8785 canonicalization

`canonical_json_bytes()` uses Python `json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False)` (`src/tools/gate4_preflight_runner.py`, around line 158). This is deterministic for many values but is not RFC 8785/JCS number serialization. For example, Python serializes the float `24.0` as `24.0`, while JCS uses the ECMAScript-compatible shortest number representation `24`. The Resolve-like profile values and audit return values include floats, so hashes produced here are not the claimed RFC 8785 canonical digests and cannot be independently reproduced by a conforming JCS verifier. Current tests verify chains using the same helper, so they do not expose the interoperability mismatch.

**Required:** Use a conforming RFC 8785 implementation already permitted by the workorder/dependency policy, or explicitly narrow the design and remove the RFC 8785 claim if the workorder permits. Add known-answer JCS vectors covering integral floats, exponent formatting, negative zero, and UTF-16 key ordering; verify both emitted bytes and audit-chain hashes against the vectors.

## Decision and lifecycle state

- Gate 4 R10 Revision R7: `CHANGES_REQUIRED`; do not use the runner for a live preflight until these findings are resolved and reviewed.
- The submitted 13/13 test result is acknowledged as reported; this review did not rerun tests.
- The prior R6 finding concerning mutable `ALLOWED_METHODS_BY_ROLE` is resolved by the closure snapshot; the remaining mutable blacklist is a separate policy input.
- No Resolve operation or Gate 5 action is authorized by this review.
- `ready_to_execute = False`; Gate G07 and Milestones M1/M2/M3 remain `OPEN`; Gate 6 remains `NOT ACCEPTED` and incident-only.
