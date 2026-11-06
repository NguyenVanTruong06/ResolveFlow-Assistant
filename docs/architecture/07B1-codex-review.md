# G07-07B1-D — Codex design review

Reviewed 2026-11-02T17:52:57+07:00: **DESIGN_ACCEPTED_WITH_BINDING_CORRECTIONS**. Report/design hashes match. The design properly defines immutable offline plan binding, exact Formula B geometry, fail-closed occupancy, truthful approved omissions, and bounded canonical dry-run output.

Binding corrections for implementation: (1) Binder must revalidate the proposal/approval binding using the accepted verifier and must not trust a standalone approval-shaped DTO. (2) Source/catalog availability cannot be proven from an occupancy snapshot; accept explicit verified evidence/provider facts as inputs and never stat media or Resolve in the pure binder. Missing/stale source evidence blocks. (3) TargetSpec/occupancy snapshot must be exact project+timeline+FPS+track set bound; a scanned empty span means free only for that scanned track and snapshot revision. (4) Keep plan state descriptive only; no field or helper may imply `ready_to_execute=True`. (5) Canonical JSON count/byte limits must be prechecked safely, with deterministic UTF-8 encoding and equality-boundary tests.

Approve next slice `G07-07B2-I`: one pure production module plus tests, exact scope as proposed. Keep M2 offline and G07 open.
