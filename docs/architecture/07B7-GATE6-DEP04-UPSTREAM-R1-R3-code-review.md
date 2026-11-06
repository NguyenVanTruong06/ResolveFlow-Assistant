# Gate 6 DEP-04-UPSTREAM-R1 Revision R3 — Static Design Review

**Workorder:** G07-07B7-GATE6-DEP04-UPSTREAM-R1 — Trusted Provenance & Dispatcher Recovery Boundary Design  
**Disposition:** `CHANGES_REQUIRED`  
**Review mode:** Static document review only. No code, tests, harness, host, production path, key, socket, or Resolve runtime was accessed or executed.

## Artifact integrity

All submitted hashes match the workspace:

- `docs/architecture/07B7-GATE6-DEP04-UPSTREAM-R1-dependency-resolution-design.md`: `eb26485b09c04aaed4ee42667113fca6aac90c9ec2cb74ec80a346f22b43da11`
- `.bridge/report.md`: `7d03a07d9550df30c54a8f5f89d638e0a55331c551f066b0aba50a3ea5427568`
- `.bridge/task.md`: `199162d1096ce221cd170eb7a503d757abd55884d78772eda9f3dfae70da5b43`

## Prior finding disposition

The three R2 findings are substantively addressed:

- **R2-01:** Terminal states and response statuses now match accepted DEP-02C enums. Boundary 5B forbids resend after an ambiguous terminal notification. The administrative in-doubt procedure is explicitly out-of-band and not presented as an accepted Oracle protocol.
- **R2-02:** Boundary 1 no longer claims automatic Oracle-record expiry; the persistent orphan and missing discovery/reconciliation API are explicitly identified as unresolved dependencies.
- **R2-03:** The design states that live execution is unavailable until all four governance prerequisites are independently resolved and approved, and limits the listed follow-on workorders to static preparation.

## Finding

### DEP04-UPSTREAM-R1-R3-01 — DAG still draws a directed path to the prohibited live-execution workorder — MEDIUM

The R3 prose says all lines leading to live execution are severed and the DAG is terminally blocked. However, the Mermaid graph in §4.1 still contains a directed edge from `GovernanceBlockers` to `WO5` labelled “All Blockers Formally Accepted” (`docs/architecture/07B7-GATE6-DEP04-UPSTREAM-R1-dependency-resolution-design.md:380–384`). This visually represents the blockers as a condition that can lead to live execution, which contradicts the stated terminal block and the claim that the sequence has no path to WO5.

This does not itself authorize an execution, and the document correctly says no live execution workorder can be scheduled from this sequence. The graph nevertheless fails to communicate that boundary unambiguously.

**Required remediation:** Remove the directed edge to WO5. If useful, keep WO5 as a disconnected, explicitly out-of-scope placeholder and state that a future independent governance decision and separately issued workorder would be required. Do not imply that acceptance of the four blockers automatically opens or authorizes live execution.

## Decision and safety state

Revision R3 is **not yet accepted** as the final design artifact pending correction of the DAG. No implementation, tests, harness, imports, host operations, sockets, production key/state access, or Resolve operations were performed during review.

Maintain `ready_to_execute = False`; Gate 6 remains `NOT ACCEPTED` and incident-only; Gate G07 and Milestones M1/M2/M3 remain `OPEN`.
