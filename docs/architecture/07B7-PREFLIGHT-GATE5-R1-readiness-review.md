# Gate 5 R1 Readiness Review

**Review timestamp:** 2026-11-04T19:31:12Z (UTC)  
**Decision:** `CHANGES_REQUIRED — DO NOT EXECUTE`  
**Scope:** Static review of `.bridge/task.md`, the proposed `/tmp` harness approach, and the accepted Gate 3 dispatcher implementation. No harness or tests were run; no Resolve SDK, IPC, or UI was accessed.

## Evidence reviewed

| Artifact | SHA-256 observed | Review result |
|---|---|---|
| `.bridge/task.md` — Gate 5 R1 | `da9bdafb39d21c62a9c20ecc636c844ee00f28453e19d79ba02fe23c20a33f2f` | The order allows one precisely bound action but does not allow a runtime Python harness in `/tmp` |
| `src/services/trusted_canary_dispatch_service.py` | `ca3c28a1b7cf5d9ea37c13b4c38330f5bb0ed4e56fbff83638606132a340a658` | Module and service explicitly prohibit live Resolve dispatch |

## Findings

### G5-R1-01 — BLOCKER: Accepted dispatcher is mock-only and rejects the proposed live adapter

The module header declares `LOCAL_TEST_ONLY; MOCK_GATEWAY_ONLY; NO_LIVE_RESOLVE; NO_LIVE_DISPATCH` and describes dispatch through `FakeResolveGateway` (lines 7–15). `TrustedCanaryDispatchService` documents a mock adapter boundary (lines 652–658) and its constructor explicitly rejects `LiveResolveCanaryAdapter` (`ERR_LIVE_ADAPTER_PROHIBITED`, lines 674–676 and 746–748). The in-process `LocalTestOracleVerificationClient` is not an OS-separated signer/oracle service.

Therefore the proposed Signer/Oracle/`LiveResolveCanaryAdapter` path is not an accepted live execution chain. Gate 5 R1's instruction to use the accepted Gate 3 chain cannot safely be interpreted as permission to bypass that explicit prohibition. Do not mint a dispatch challenge, connect to Resolve, or attempt the insertion under the current implementation/workorder.

**Required:** A separately reviewed and accepted bounded workorder must establish a live-capable signer/dispatcher boundary compatible with Plan R7 (including the required authority separation and oracle boundary), or otherwise identify an already accepted live-capable path. Do not modify the current accepted local-test primitive ad hoc.

### G5-R1-02 — HIGH: Proposed `/tmp` Python harness is outside the exact artifact allowlist

Gate 5 R1 §4 permits the listed report/evidence artifacts and a runtime audit directory/log under `/tmp`; it does not permit creating or executing a new `run_gate6.py` (or other Python harness) from that directory. The proposal would add executable code outside the exact allowlist and without prior static review or an artifact hash.

**Required:** Do not create or run the proposed harness under R1. Any required runtime code must be explicitly allowlisted in a revised workorder, reviewed and hash-pinned before execution, or replaced by an already accepted entrypoint that performs no unreviewed code loading.

## Disposition

The operator's current statement satisfies the direct-presence/supervision precondition for the stated time, and the intended single action remains within the recorded Gate 2 action limit. Those facts do not remedy the missing live-capable Gate 3 boundary or the artifact-allowlist gap.

**Gate 5 R1 remains blocked.** No Resolve operation is authorized under this review. Preserve the target, do not issue a challenge, and do not run the proposed `/tmp` harness. `ready_to_execute = False`; G07 and M1/M2/M3 remain `OPEN`; Gate 6 remains `NOT ACCEPTED` and incident-only. The next step is a bounded upstream workorder addressing G5-R1-01 and explicitly resolving the harness allowlist before any execution.
