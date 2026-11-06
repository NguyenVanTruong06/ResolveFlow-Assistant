# Static Evidence Review — G07-07B7-PREFLIGHT-GATE4-R5

**Review date:** 2026-11-04  
**Decision:** `CHANGES_REQUIRED` — Gate 4 R5 is not accepted.  
**Review mode:** Static artifact, hash, and screenshot review. No Resolve API/UI/IPC calls or tests were run during this review.

## Evidence reviewed

| Artifact | SHA-256 observed | Result |
|---|---|---|
| `.bridge/task.md` | `a316af6bc208b9b5dd0f9686f8f92f0e8b3abdd5648c0eac59748d2ce336946d` | R5 workorder and 300-second challenge match submission |
| `.bridge/preflight-signoff-GATE4-R5.json` | `40039baaefaafcf346fa960c07fe9dfe7214b701341f244dfb6e9567cf812b02` | Hash matches; claims 10 predicates pass |
| `.bridge/ux-evidence/G07-07B7-PREFLIGHT-GATE4-R5/resolve_preflight.png` | `035ce946a1035d3d0b9eedfbfa340840e4b3fb2e285569271fe0ca04d6234cc5` | Hash matches; visible project/timeline names and empty V1/A1 lanes |
| `.bridge/ux-evidence/canary_test_1080p24.mp4` | `7c83154437b2e05e01f6a795d6295e9827e04907c4479c7b0435e3a4ac18d916` | Matches submitted source-media digest |
| `.bridge/report.md` | `374efe485889b9016409a93f24e9f6a5b86517d5151a3afabacd8fa023731f70` | Appended R5 report is present |

The reported finish time `2026-11-04T15:18:54Z` falls within the challenge interval `15:17:18Z`–`15:22:18Z`. Project and timeline names match the nonce; the reported UIDs differ from both blacklisted incident UIDs. The screenshot supports the visible target names and empty timeline. The report/signoff claim 1920x1080 at 24/1, one video track, at most one audio track, zero items, equal start/end frame, zero markers, and a contemporaneous operator attestation.

## Finding

### PREFLIGHT-GATE4-R5-01 — HIGH: API readback provenance is missing

Workorder R5 §4 requires direct API query provenance and observed values for all preflight predicates. The signoff records only summary fields such as `resolution: "1920x1080"`, `frame_rate: 24.0`, `video_tracks: 1`, and `markers_count: 0`; it does not identify the API method/query and returned value for these observations. The report's predicate table likewise labels values as “Observed API Value” without recording the corresponding query provenance. For example, there is no recorded `project.GetSetting(...)` / `timeline.GetSetting(...)` result establishing width, height, and frame rate. The screenshot does not show those profile settings.

**Required:** Provide contemporaneous evidence identifying the exact Resolve API query and raw returned value for each profile and topology predicate, including project and timeline width/height/frame rate, track counts, V1/A1 item lists, start/end frame, and markers. Add this provenance to the R5 signoff and append the correction to `.bridge/report.md` without rewriting prior history. Do not invent or retrospectively infer query results. If the missing readbacks are captured before the R5 challenge expiry, they may be appended under R5 and submitted for another review; after expiry, do not treat later queries as within the TTL and request a new bounded preflight workorder.

## Decision and lifecycle state

- Gate 4 R5: `CHANGES_REQUIRED`; preflight not accepted pending `PREFLIGHT-GATE4-R5-01`.
- Gate 5: not authorized.
- Gate G07 and Milestones M1/M2/M3 remain `OPEN`.
- Gate 6 remains `NOT ACCEPTED` and incident-only.
- `ready_to_execute = False`.

The operator attestation is recorded as the operator's statement; this review did not independently observe the setup session. No clip dispatch or timeline insertion is evidenced in the screenshot or reported artifacts.
