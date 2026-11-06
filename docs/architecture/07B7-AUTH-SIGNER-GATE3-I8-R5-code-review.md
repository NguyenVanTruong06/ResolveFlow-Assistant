# G07-AUTH-SIGNER-GATE3-I8 Revision R5 — Static Code Review

**Decision: ACCEPTED**  
**Scope:** Digest-Bound Durable Revocation Epoch Anchor Core  
**Reviewed:** 2026-11-03

## Reviewed artifacts

- Workorder: `.bridge/task.md` — SHA-256 `dc8c23240fd50510a87bba2dd42e522a2a80447925ea15e5d793fb9cbd685f2d`.
- Implementation: `src/services/trial_authorization_signer_service.py` — SHA-256 `1b62c1aeaf825380f80ffb3ab721fcba3b4f7901b6f54a8e85aae8d27d3925c1`.
- Tests: `tests/services/test_trial_authorization_signer_service.py` — SHA-256 `dda5c1fadf77bd3cdff8388a99927df88057a5a1da258b3114732e0907abeeaa`.
- Submitted test log: `.bridge/ux-evidence/G07-AUTH-SIGNER-G3-I8/pytest.log` — SHA-256 `c85c76ee7b17742c93a466ef2d03fce8d756b92fafd195fee58a7ee663909193`.
- Completion report: `.bridge/report.md` — SHA-256 at submission `c2ab09e29384fc77915ddc3f4e6964c75ddc7744f54c35f21ce3be55bd897b76`.
- Prior review: `docs/architecture/07B7-AUTH-SIGNER-GATE3-I8-R4-code-review.md` — SHA-256 `41f3749623f24c35726fa015d3cca748a40c4393f113c492b11a8aeaff796268`.

Submitted hashes match the workspace. The submitted log reports 185 passed, 0 failed, exit code 0. Tests were not rerun during this static review.

## R4 finding disposition

`I8-R4-01` — **RESOLVED**. `_open_anchor_dir_under_temp_root()` now checks group/world-write bits on each opened component below the temporary root before descending, and still checks the final anchor directory. The added negative fixture makes an intermediate directory mode `0777`, expects `ERR_DIR_INSECURE_PERMS`, and verifies that neither lock nor anchor file was created.

The earlier cross-process queue issue is also resolved in this revision's test set: it reads exactly ten results with bounded `Queue.get(timeout=10.0)`, then checks every worker is no longer alive and has exit code zero.

## Review conclusion and safety boundary

`ACCEPTED` for the local, temporary-directory-only epoch-anchor primitive. Acceptance covers the verified-result provenance check, strict monotonic epoch/digest comparison, descriptor-relative traversal and file access, file/directory durability, and the submitted focused regression evidence as reported. It does not establish a production trust root, production path configuration, revocation enforcement wiring, signer service/daemon/RPC integration, or live Resolve readiness.

No tests were run during this static review. No Resolve, IPC, network, key, or production filesystem operations were performed. Preserve `ready_to_execute=False`; Gate G07 and Milestones M1/M2/M3 remain `OPEN`; Gate 3 is not complete until all its implementation slices and reviews are accepted; Gate 6 remains `NOT ACCEPTED` and incident-only. No authorization issuance or live operations are authorized by this review.
