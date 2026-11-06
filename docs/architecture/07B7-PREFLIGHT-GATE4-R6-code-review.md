# Static Evidence Review — G07-07B7-PREFLIGHT-GATE4-R6

**Review date:** 2026-11-04  
**Decision:** `CHANGES_REQUIRED` — Gate 4 R6 is not accepted pending finding `PREFLIGHT-GATE4-R6-01`.  
**Review mode:** Static artifact, hash, timestamp, and screenshot review. No Resolve API/UI/IPC calls and no tests were run during this review.

## Evidence reviewed

| Artifact | SHA-256 observed | Result |
|---|---|---|
| `.bridge/task.md` | `0d1ec9cba4b870d5dd1a774d932ee94c666f77ec2267d4e4727c96e5e9b0e643` | R6 workorder, nonce, target names, predicates, and artifact boundary match the submission |
| `.bridge/preflight-signoff-GATE4-R6.json` | `4d2933956e4b47a9d37ad4ee72b15a2d5dfb0d076991e911662830996f30cb08` | Submitted hash matches; contains per-query raw and normalized values and UTC observations |
| `.bridge/ux-evidence/G07-07B7-PREFLIGHT-GATE4-R6/resolve_preflight.png` | `672a3183c7c76417e21209e37f3601ce09cb92dd8b60dbe7cd5a0309c6f28038` | Submitted hash matches; image shows the R6 project and timeline names, V1/A1 lanes with 0 clips, and the source in the Media Pool |
| `.bridge/ux-evidence/canary_test_1080p24.mp4` | `7c83154437b2e05e01f6a795d6295e9827e04907c4479c7b0435e3a4ac18d916` | Matches the source digest in signoff |
| `.bridge/report.md` | `2919b46ce056414d0ef3dff0bcaa221a2b719813c9bd8fed5c0eecf2f57b9fda` | R6 completion record and full provenance table are present |

The challenge interval is `2026-11-04T15:23:24Z` through `15:28:24Z`. The signoff timestamp, each listed observation, and the operator attestation are at approximately `15:26:06.7Z`, within the interval. The project/timeline names match the challenge. Their UIDs differ from both blacklisted incident UIDs and the superseded R5 target UIDs. The screenshot supports the visible project/timeline identity and empty V1/A1 lanes. The direct `GetItemListInTrack` records for video and audio both contain empty lists.

## Finding

### PREFLIGHT-GATE4-R6-01 — MEDIUM: Predicate 10 mislabels a derived check as a Resolve API query

The signoff and report list predicate 10.1 as `object_type: Timeline`, `method_name: VerifyAllTracksEmpty`, with arguments `video_1` and `audio_1`, and a raw Boolean return. R6 §4.10 asks for the **evidence basis** for zero dispatch and zero placement; it does not define `VerifyAllTracksEmpty` as a Resolve API method. The dossier does not identify an API wrapper, module, or implementation for this method, so the record cannot be treated as a direct Resolve API observation. This weakens the otherwise detailed provenance by presenting an unattributed derived result as a raw API return.

**Required:** Preserve the submitted record as history and append a correction that identifies the actual source of predicate 10. If `VerifyAllTracksEmpty` is a runner-side/helper check, label it as derived evidence and identify the helper/source and its inputs; do not label it as a direct `Timeline` API call or raw Resolve return. Ground the predicate in the recorded `GetItemListInTrack` results, the no-dispatch execution record, and the operator's contemporaneous attestation. If a different direct API was actually invoked, provide its exact method, arguments, raw return type/value, and in-TTL timestamp only if captured contemporaneously; do not invent or backdate a query. Update the signoff/report by an authorized append-only correction under a new bounded workorder. No further Resolve query is authorized by this finding.

## Decision and lifecycle state

- Gate 4 R6: `CHANGES_REQUIRED`; do not mark the preflight accepted until `PREFLIGHT-GATE4-R6-01` is resolved.
- Gate 5 is not authorized by this review; a separate bounded workorder is still required.
- Gate G07 and Milestones M1/M2/M3 remain `OPEN`.
- Gate 6 remains `NOT ACCEPTED` and incident-only.
- `ready_to_execute = False`.

This is a static review of submitted artifacts. It does not independently establish the operator's physical presence or re-observe the Resolve session.
