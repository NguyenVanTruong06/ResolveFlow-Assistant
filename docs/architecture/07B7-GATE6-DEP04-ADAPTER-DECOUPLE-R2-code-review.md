# Gate 6 DEP04 Adapter Decoupling R2 — Static Code Review

**Workorder:** `G07-07B7-GATE6-DEP04-ADAPTER-DECOUPLE-R2`  
**Disposition:** `CHANGES_REQUIRED`  
**Review mode:** Static source and test review only.

## Artifact integrity

Submitted hashes match the reviewed workspace:

- `src/services/live_resolve_canary_adapter.py`: `c1b46be1e5c961ae5970dd414cd96144f26c65be3dea6b5086d62398d9d2857f`
- `tests/services/test_live_resolve_canary_adapter_decoupled.py`: `5e38fe5d3da1372c366af6311e3387fb94a9fb774cb78afb4199654047035b05`
- `.bridge/task.md` at review time: `18d1546cb4996d2971299800ffe5b05ba7d24eb6595749643d90967c1c53f421`
- `.bridge/report.md` before this review entry: `5b99fee8025f2c1257172d4c3df26f536d917042c4d2095b21d8f3e967b74b75`

## Finding

### DEP04-ADAPTER-DECOUPLE-R2-01 — Caller-controlled clock bypasses Oracle response freshness and expiry — HIGH

`verify_oracle_verification_response()` accepts public `now_utc` at lines 863–875 and uses it as the authoritative current time at lines 957–970. `execute_live_canary_action()` exposes and forwards the same caller-supplied value at lines 1098–1148. A caller can therefore pass a timestamp within an old, otherwise valid response's `verified_at_iso`/`valid_until_iso` interval and make an expired or stale signed Oracle response satisfy both the expiry and age checks. The timestamp is not part of a trusted clock source and is not authenticated by the Oracle response.

This defeats the R2 remediation's stated guarantee that freshness and expiry are checked against current UTC time. In the live adapter path, the bypass can carry a replayed `VERIFIED_VALID` authorization across the mutation boundary.

**Required remediation:** Do not accept the verification time as a per-call caller-controlled argument on the authorization/dispatch API. Read the current UTC clock internally at verification time, or inject a trusted clock at construction behind a capability that callers cannot override per request. Keep deterministic tests by injecting/patching that clock at the test boundary. Add regression coverage proving a caller cannot make an expired or over-age Oracle response fresh by supplying an earlier timestamp, and that rejection happens before journal mutation or adapter invocation.

## Verification boundary and decision

The handoff reports 26 passing tests. I did not independently run pytest; this review was static. No Resolve SDK, Oracle socket, network, host provisioning, systemd, production key/state, or live operation was used during review.

R2 is **not accepted** until the caller-controlled freshness clock is removed or safely constrained and covered by regression tests. Preserve `ready_to_execute = False`; Gate 6 remains `NOT ACCEPTED` / incident-only; Gate G07 and Milestones M1/M2/M3 remain `OPEN`.
