# Gate 3 I19 Closeout Review

**Review date:** 2026-11-04  
**Workorder:** `G07-AUTH-SIGNER-GATE3-I19 — Gate 3 Integration Harness & Closeout Submission`  
**Decision:** `CHANGES_REQUIRED` — Gate 3 is **not accepted / not closed**.  
**Scope:** Static review of the submitted integration harness, closeout dossier, test log, and appended handoff report. No tests were rerun in this review.

## Evidence reviewed

| Artifact | SHA-256 observed in workspace |
|---|---|
| `.bridge/task.md` | `5edf35aaa276f996e3c13a15c5693d6b294a04f128851514001c4d63e2c4c139` |
| `tests/integration/test_gate3_signer_dispatcher_integration.py` | `897442684ef4bbb3dedcabe0e765a81a20cc8d7cb1387546c6565d2f66aabdec` |
| `docs/architecture/07B7-AUTH-SIGNER-GATE3-CLOSEOUT.md` | `617411a0b0206ef45cf8426c4a99f014859441d3514a392db6f463462ce58d3e` |
| `.bridge/ux-evidence/G07-AUTH-SIGNER-G3-I19/pytest.log` | `c44196471d410f309df4d88e05f403ee55f0c3e8ecf9127cb0785a1196efaf45` |
| `.bridge/report.md` | `069444addd7d6eb1c26d8fd1a58ab25eabdf7019cd90bb769eccaadf0e8495b1` |

The evidence log records the authorized suite as **589 collected, 587 passed, 2 failed, 0 skipped, 0 xfailed, 2 warnings, exit code 1**. The new I19 integration module itself reports **9 passed**.

## Findings

### I19-01 — BLOCKER: Positive path bypasses durable target ledger and dispatch receipt integration

In the purported positive end-to-end test, the fixture explicitly sets `coordinator._durable_ledger = None` (`tests/integration/test_gate3_signer_dispatcher_integration.py:487-489`). The dispatcher is then constructed with `receipt_service=None` (`:731-736`). Instead of observing the receipt produced by that dispatch, the test constructs a different `ExecutionReceipt` using `proposal_revision=1` and stores it directly afterward (`:764-797`).

The dossier acknowledges why this was done: the signed 64-hex `proposal_revision` does not fit downstream integer conversions (`docs/architecture/07B7-AUTH-SIGNER-GATE3-CLOSEOUT.md:111-121`). This is useful dependency evidence, but it means the success test does not prove the requested chain through the durable target ledger and dispatcher-owned receipt persistence. It therefore cannot support the claim that the complete Gate 3 lifecycle has been integrated. Do not work around this by disabling those controls or substituting a separately-created receipt.

**Required:** Obtain a separately bounded workorder to reconcile the proposal revision contract across the affected accepted interfaces, then update the I19 harness so the durable ledger and receipt service remain enabled during the actual dispatch. Assert that the persisted receipt is the one emitted by that dispatch and carries the authorized proposal revision.

### I19-02 — BLOCKER: Authorized Gate 3 verification suite is failing

The submitted log shows two failures in the accepted I18 console tests:

- `test_missing_or_incorrect_visual_attestation_aborts`
- `test_missing_or_incorrect_confirmation_phrase_aborts`

Both fail because their fixed challenge expires at `2026-11-04T12:02:00Z`, while the default real clock is later (`pytest.log:17-23, 600-610`). The dossier correctly identifies the nondeterministic fixture and does not modify files outside the I19 allowlist. However, the Gate 3 audit closeout criterion requires a successful complete unit/contract/integration suite. A documented failure remains a failure and prevents Gate 3 acceptance.

**Required:** Issue a bounded follow-up workorder for the I18 test clock fixture (or an equivalent authorized correction), then rerun the authorized Gate 3 suite after the proposal-revision integration dependency is resolved. The closeout evidence must show zero failures, skipped tests, or xfails; classify warnings and leave no unexplained warning.

### I19-03 — HIGH: Closeout traceability table contains incorrect slice/review references

The matrix at `docs/architecture/07B7-AUTH-SIGNER-GATE3-CLOSEOUT.md:32-46` misattributes accepted work. For example, C-04 assigns the Authoritative Approval Store to I7 and a purported I7-R1 review; the store was implemented and accepted as I2. C-05 labels Director approval verification as I8, although that capability is I4. C-06 cites I5-R1 even though the accepted consent-verification revision is I5-R2. C-15 cites the unqualified I18 review rather than the accepted I18-R10 review. These references make the claimed Plan-to-implementation evidence chain unreliable.

**Required:** Reconcile every row against the actual accepted review artifacts and their recorded decisions. Include exact filenames/revisions and correct slice ownership; do not infer acceptance from a submitted report or test result.

### I19-04 — MEDIUM: Deliverable checksums in the closeout dossier are stale

The dossier’s §6 records the closeout file as `7a56fb8ded6e03bb0c4690d9656ee9f1dd13db8a92f0d3380f3c0c9171d93013` and the report as `6236e0afc731b3771769e2953886815b7f7a3dc5bba974eebb7bb1536be1b887`; the current workspace hashes are respectively `617411a0b0206ef45cf8426c4a99f014859441d3514a392db6f463462ce58d3e` and `069444addd7d6eb1c26d8fd1a58ab25eabdf7019cd90bb769eccaadf0e8495b1`. The dossier’s own checksum cannot be self-authenticated by embedding its final hash inside itself; record that digest in an external handoff/report entry after the dossier is finalized, and ensure the report hash is not presented as a pre-append digest.

**Required:** Correct the checksum evidence using a non-self-referential convention and verify hashes after final writes. The integration test and evidence-log hashes currently match the workspace.

## Decision and lifecycle state

The 9/9 I19 integration test result is positive evidence for the exercised local paths, and the dossier appropriately discloses the failures and interface limitation. Nevertheless, the positive path bypasses two required persistence/dispatch integrations, and the authorized broader suite exits nonzero. The traceability and checksum defects also prevent reliable closeout verification.

Therefore:

- I19 closeout: `CHANGES_REQUIRED`; not accepted.
- Gate 3: remains **OPEN / NOT ACCEPTED**.
- Gate G07 and Milestones M1/M2/M3: remain **OPEN**.
- Gate 6: remains **NOT ACCEPTED** and incident-only.
- `ready_to_execute = False`; Gate 4 and later work are not authorized by this review.
- The report’s identified `proposal_revision` contract mismatch requires a separate bounded workorder; no accepted source was changed in this review.
