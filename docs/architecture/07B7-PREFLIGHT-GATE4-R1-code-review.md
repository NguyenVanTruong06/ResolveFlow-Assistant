# Static Evidence Review — G07-07B7-PREFLIGHT-GATE4 Revision R1

**Decision: `CHANGES_REQUIRED`**  
**Review mode:** Static artifact and evidence review only. No Resolve API, UI, IPC, tests, or live dispatch was run.

## Reviewed artifacts

| Artifact | Submitted SHA-256 | Review result |
|---|---|---|
| `.bridge/preflight-signoff.json` | `cc6852814832ed58c5c1a71f93f58e63e67d3e94e142891208b28110c96df40d` | Hash matches |
| `.bridge/report.md` | `680dbdb04b06f65e029b4cfb88f86be58fe5983569708275ba70cbe7ae94afc2` | Hash matches |
| `.bridge/task.md` | `625e5e3288760844f821927cf7ab34259d3d8968dc3faed7116802b82dbefb13` | Hash matches |

## Evidence reviewed

- Challenge nonce `9A56C835B97208ED` is uppercase 16-hex; the project name matches the required regex and includes the nonce.
- The signoff records project creation at `12:48:18.769605+07:00`, 41.8 seconds after issue and before expiry at `12:52:37+07:00`.
- Project/timeline names and UIDs are present. All six empty-topology predicates are marked satisfied with their API query provenance. Start and end frames both read as 86400.
- An operator identity, attestation timestamp, and freshness statement are present in the signoff. I have not independently observed the Resolve desktop; the attestation remains the operator's statement.
- The report confirms no clip insertion or timeline-content edit. Its phrase “Zero ... Timeline Mutation” should be read as zero timeline-content edits; project and empty-timeline creation were the explicitly authorized setup mutations.

## Findings

### PREFLIGHT-GATE4-R1-01 — HIGH: Timeline creation time is absent from the challenge-TTL evidence

**Location:** `.bridge/preflight-signoff.json`, `challenge` and `target_provenance` objects.

The workorder requires creation of both the project and its single empty timeline before challenge expiry. The signoff records `created_at` for project creation but does not record when `CreateEmptyTimeline()` completed. The report's 41.8-second interval is tied to the project-creation timestamp; the artifact does not establish that timeline creation also completed before `12:52:37+07:00`.

**Required correction:** Add a contemporaneous timeline-created timestamp and evidence that it falls within the challenge window. If that evidence was not captured, do not infer it retrospectively; mark the TTL criterion unverified and follow a newly reviewed challenge/setup workorder.

### PREFLIGHT-GATE4-R1-02 — HIGH: 1080p24 profile values lack readback provenance and exact frame-rate representation

**Location:** `.bridge/preflight-signoff.json`, `target_provenance.profile`.

The signoff records width `1920`, height `1080`, and `fps: 24.0`, but supplies no API method/setting provenance for those profile values. The accepted plan requires 24/1 fps, while the evidence provides only a floating-point value. Workorder R1 requires provenance for each observed value.

**Required correction:** Record the exact read-only Resolve settings/API result used for project width, height, and frame rate, including exact 24/1 (or the source setting string that unambiguously represents 24 fps), and identify the query for each value. Do not change project settings while collecting this evidence.

## Decision and gate state

The challenge format, project identity, six empty-topology predicates, and reported operator attestation are present. The two evidence gaps above prevent independent verification of the full Gate 4 workorder, so Gate 4 Revision R1 is **not accepted**. Gate 5 workorder `G07-07B7-LIVE` is **not issued**.

`ready_to_execute=False`; Gate G07 and Milestones M1/M2/M3 remain **OPEN**. No Resolve interaction, timeline mutation, test, or dispatch was performed during this review.
