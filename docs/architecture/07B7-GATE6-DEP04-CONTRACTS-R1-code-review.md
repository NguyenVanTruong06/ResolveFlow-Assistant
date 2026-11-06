# Gate 6 DEP-04-CONTRACTS-R1 — Static Contract Review

**Workorder:** G07-07B7-GATE6-DEP04-CONTRACTS-R1 — Cryptographic Provenance Contracts  
**Disposition:** `CHANGES_REQUIRED`  
**Review mode:** Static source/test review only. The authorized pytest command was not run because this session does not provide the OS-enforced sandbox required for target-controlled test execution.

## Artifact integrity

All submitted hashes match the workspace:

- `src/core/contracts/signed_preflight_contracts.py`: `4d2ccb3cbf373822951db38d5cbb5c1675317c9a952b930e1642abd9a588f65c`
- `src/core/contracts/signed_execution_workorder_contracts.py`: `4fd4fe36bf264f3683e1fbd095b12df0627842e21835ac2d2e71d3a41af4273f`
- `tests/core/contracts/test_signed_preflight_contracts.py`: `7ecd7ed845262f77acf70f747166876e096fa1d838ecbac53fea30b3910146a1`
- `tests/core/contracts/test_signed_execution_workorder_contracts.py`: `ea23ad98e9332b01f248243f4d48a6d941836579f5582252a7796e1f3363ede3`
- `.bridge/report.md`: `9a50a121253af3cceca047284fa50e599381e56db34e1ac36bf0d95bc4d37dcf`
- `.bridge/task.md`: `553b1e3e4524cd050ab085f93194af873896b94edd700a65b7041340c29514ac`

## Review summary

The implementation follows the requested two-contract scope and uses the accepted canonical JSON and UTC timestamp helpers. The schema versions, signature-field exclusion from signing payloads, digest/signature grammars, validity-window bounds, target pins, and unresolved authority warnings are present. The implementation does not contain key loading or signing/verification operations.

## Finding

### DEP04-CONTRACTS-R1-01 — Pinned target fields are not type-gated before equality checks — MEDIUM

In `SignedPreflightEvidenceEnvelope.__post_init__`, `target_project_uid`, `target_project_name`, `target_timeline_uid`, and `target_timeline_name` are compared to pinned constants without first requiring exact built-in `str` types (`src/core/contracts/signed_preflight_contracts.py:178–193`). In `SignedExecutionWorkorderRecord.__post_init__`, the same gap exists for `target_project_uid` and `target_timeline_uid` (`src/core/contracts/signed_execution_workorder_contracts.py:148–155`).

These are public dataclass constructors and `from_dict()` accepts any `Mapping`, not only values produced by a JSON decoder. A caller can therefore supply non-string Python values for fields whose schema requires strings; constructor validation is not a complete primitive-type gate. This conflicts with Workorder §3.1's strict schema requirement and §4's wrong-primitive-type test requirement. A value comparison is not a substitute for validating the field type.

**Required remediation:** Before comparing pinned values, require `type(value) is str` for every pinned target name/UID field in both contracts and raise the corresponding structured validation error on mismatch. Add regression cases for non-string values for each affected field (including a non-JSON custom object whose equality claims to match the pinned value). Confirm serialization cannot proceed for invalid instances. Re-run the single authorized pytest command in an appropriately isolated environment and report the actual result.

## Verification boundary and decision

The handoff reports 30 passing tests, but this review did not independently execute them. No code import, test execution, key access, socket/network activity, host operation, or Resolve interaction occurred during review.

Revision R1 is **not accepted** pending the finding above and fresh authorized test evidence. Maintain `ready_to_execute = False`; Gate 6 remains `NOT ACCEPTED` / incident-only; Gate G07 and Milestones M1/M2/M3 remain `OPEN`. The Gate 4 signing authority and Gate 5 trust-anchor dependencies remain unresolved.
