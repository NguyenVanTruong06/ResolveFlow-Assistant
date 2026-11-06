# G07-07B7-PLAN-D Static Plan Review

**Decision: CHANGES_REQUIRED**  
**Review mode:** Static plan review only. No tests, Resolve application, IPC, FusionScript, network, or AI calls were run.  
**Scope:** `docs/architecture/07B7-live-canary-readiness-plan.md`, checked against the issued workorder, accepted 07B5 design, accepted 07B6 plan, and accepted I4 R4 implementation/review.

## Artifact integrity

The submitted hashes match the files on disk:

| Artifact | SHA-256 |
|---|---|
| `docs/architecture/07B7-live-canary-readiness-plan.md` | `bfb698006a545b5e6ffa836fe9a5ab09c6f0a461acff78cf5ab21f73e501119b` |
| `.bridge/report.md` | `3ad11867a3c1b4fabd02bb547f2921144e09a02f95529ee572865f2bc8a266ca` |
| `.bridge/task.md` | `7a7564f8710955ab77095281e63a12dccab5a5e236ac56502bce34ae0de354e8` |

The referenced accepted baselines also match their stated hashes: 07B5 design `e3dc16133e68648535f64dfc03592ed2f449ff6ae50438605e09d7f074df2132` and 07B6 plan V3 `628da41e58fc07c8b81ceadc1db42c8022fde5b1b315de6cf56fad0efec4f70a`.

## Findings

### B7-PLAN-01 — BLOCKER: The live sequence routes through an adapter that is deliberately fake-only

**Location:** readiness plan lines 124-140; accepted implementation `src/services/isolated_resolve_trial_adapter.py:141-175`; accepted 07B6 plan safety boundary.

The sequence calls `execute_isolated_action()` and then describes a live Resolve dispatch. The accepted I4 adapter's tripwire admits only an exact `FakeResolveGateway` instance with its authentic bound method and rejects live Resolve attributes. The accepted 07B6 plan also explicitly prohibits connecting Resolve or changing the mock-only tripwire. Therefore this sequence is not executable as written; making it work by weakening or bypassing the tripwire would violate the accepted safety boundary. The current I4 success path also leaves the ledger `ACTIVE` and does not persist a terminal receipt (`isolated_resolve_trial_adapter.py:493-509`), while the proposed diagram assumes receipt persistence and `EXHAUSTED` terminalization.

**Required correction:** Treat a separately designed and reviewed live execution path as a prerequisite before any live workorder. State how it will be isolated and admitted without altering or bypassing the existing `MOCK_ONLY` engine tripwire. Include the missing durable receipt/ledger terminalization capability as an implementation prerequisite. Do not claim the current I4 adapter can perform live dispatch.

### B7-PLAN-02 — BLOCKER: Action type, signed geometry, and record-frame origin conflict with the accepted contract

**Location:** readiness plan lines 95-103, 124-139, 144-170; accepted design Section 2.7 lines 3401-3408 and Section 3 lines 3414-3442.

The accepted design authorizes `action_type="INSERT_CLIP"`, timeline-relative start `0`, and source/timeline duration `120` frames. The readiness plan instead specifies `action_type="INSERT_A_ROLL_CLIP_V1"` and `100` frames. It also sets `record_in_frame=0` while allowing a timeline start frame such as `86400`; the accepted contract requires `authorized_record_frame == observed_timeline_start_frame + authorized_timeline_frame_start`, and Resolve must receive the signed `authorized_record_frame` directly. These values cannot produce a payload matching the accepted authorization contract and can target the wrong physical frame.

**Required correction:** Use one consistent action identifier/type and signed source/timeline geometry from the accepted design, or submit a separately reviewed design change before basing a live plan on new values. Express the timeline-relative start separately from the absolute `authorized_record_frame`; specify the latter as the captured timeline start plus the signed relative offset. Align the read-back predicates with that same geometry.

### B7-PLAN-03 — HIGH: Canary naming and freshness preflight omit the accepted pre-creation challenge protocol

**Location:** readiness plan lines 68-80; accepted design Section 2.7 lines 3403-3404.

The plan invents `CANARY_RESOLVEFLOW_ISOLATED_<timestamp>` and `CANARY_TL_V1_<timestamp>` names and checks that tracks containing clips are empty. The accepted design instead requires the app-generated pre-creation challenge to be bound into the disposable project identity, with project name matching `^RF_TRIAL_[0-9A-Z_]{8,32}$`; it also requires exactly one video track, at most one audio track, V1 empty, `GetStartFrame() == GetEndFrame()`, and zero markers, together with the human freshness attestation. The plan mentions challenge binding at a high level but its concrete creation and admission steps do not perform it or enforce the full occupancy predicate.

**Required correction:** Specify challenge generation before manual creation, how the operator incorporates it into the project name, and how admission compares the challenge and immutable project/timeline UIDs. Make every accepted emptiness/topology predicate explicit and fail closed when any is unavailable or mismatched. Preserve the design's stated residual human-process risk.

### B7-PLAN-04 — BLOCKER: Gate 2's quoted consent authorizes planning, not the live canary

**Location:** readiness plan lines 1-10 and 252-280, especially line 276.

The plan correctly says a live workorder requires explicit written user authorization, but the example Gate 2 phrase is: “Tôi xác nhận cho phép lập kế hoạch thi công thử nghiệm canary trực tiếp có điều hành viên giám sát.” This grants permission to plan the canary, not permission to conduct it. Treating this phrase as satisfying Gate 2 and proceeding to issue `G07-07B7-LIVE` would not meet the stated authorization requirement.

**Required correction:** Separate approval of this readiness plan from authorization to execute. Gate 2 must require a direct, unambiguous written statement authorizing the later single-action supervised live canary, with its scope and safety constraints stated. Do not pre-fill that authorization, infer it from approval of this plan, or advance the live gate until the user actually provides it. Require revalidation of the final target/action binding at the later workorder gate.

## Positive observations

- The plan repeatedly marks itself DESIGN ONLY and preserves `ready_to_execute=False`, Gate G07 OPEN, and M1/M2/M3 OPEN.
- It distinguishes mock evidence from unverified desktop/Resolve behavior and includes supervision, no-auto-retry, evidence capture, and stop-condition sections.
- It references the final accepted I1-R3, I2-R1, I3-R2, and I4-R4 review documents and correctly treats the 165-test result as mock-only evidence.

## Decision

**CHANGES_REQUIRED.** Resolve B7-PLAN-01 through B7-PLAN-04 before accepting the readiness plan. This review authorizes no implementation, Resolve launch or connection, UI wiring, IPC, FusionScript, or live canary. Keep `ready_to_execute=False`; Gate G07 and Milestones M1/M2/M3 remain OPEN. No tests or runtime actions were performed during this review.
