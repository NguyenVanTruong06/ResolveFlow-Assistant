# Gate 3-I15 Revision R3 Static Code Review — Local-Test Operator Consent Verification & Atomic Challenge Consumption Core

**Decision: CHANGES_REQUIRED**

## Scope and evidence

Reviewed the I15 workorder, R3 implementation, tests, completion report, and submitted focused test log. The submitted task, report, source, test, and evidence hashes match the workspace:

| Artifact | SHA-256 | Result |
|---|---|---|
| `.bridge/task.md` | `2048764a0d9e4f4eb13e6ebcf335644d4ad90c59af2d4cedcfebcf5d7df24e68` | Match |
| `.bridge/report.md` | `560c14bdbf11f795f19e83a0eacedc030e7cf9a1ccdb238e94be93848a821c4f` | Match |
| `src/services/trial_authorization_signer_service.py` | `5d4d43f72338fc4bf5650789403033905c23b6739f409487c6fefaecb6b983ae` | Match |
| `tests/services/test_trial_authorization_signer_service.py` | `f6210e8b71b141460a4a32191782a42e30aa80283d5011cba93e018eafe3e95c` | Match |
| `.bridge/ux-evidence/G07-AUTH-SIGNER-G3-I15/pytest.log` | `b383754953017b484c4c3142b9b9438ae91b4e953b8b56726fb101149c024b7e` | Match |

The supplied log reports **313 passed, 2 warnings in 3.12s**, exit code 0. Tests were not rerun; this is a static review. No Resolve, IPC, network, or live operation was performed.

## Prior findings

I15-R2-01 is resolved: `ChallengeConsumptionError` now exposes structured primary and cleanup exceptions, and the combined append-plus-close fault test verifies both. The earlier R1 findings for ledger close, monotonic expiry enforcement, and key normalization before I11 are also addressed in the reviewed code.

## Finding

### I15-R3-01 — HIGH: Descriptor traversal is only a preflight; ledger creation remains path-based and raceable

The new check opens the requested directory through `_open_anchor_dir_under_temp_root()` and stores the returned descriptor in `verified_ledger_dir_fd`, but the `finally` block closes that descriptor immediately (and suppresses any `os.close()` error). Later, under the registry lock, the implementation constructs `SignerConsumedNonceLedger(nonce_ledger_directory)` using the original path. The ledger constructor resolves and opens that path independently; its `O_NOFOLLOW` protects only the final component, not intermediate components.

Therefore the descriptor walk does not anchor the actual ledger operation. A concurrent rename/replacement of a traversed parent with a symlink after the walk and before the ledger constructor opens the path can redirect ledger creation or append outside the temporary root. The new static symlink test proves rejection when the symlink is already present, but it does not close this check/use race. This leaves the explicit local-temp-only persistence boundary unproven.

**Required disposition:** follow I15 workorder §6. The current accepted `SignerConsumedNonceLedger` API takes a path and does not accept/retain the verified directory descriptor for descriptor-relative file creation and access. Do not claim I15 complete or widen its allowlist to alter the accepted I6 API. Report this dependency and obtain a separate bounded workorder for the ledger's descriptor-based directory interface; after that interface is accepted, wire I15 through the retained descriptor and add a deterministic component-swap race test. The verified directory descriptor must remain authoritative through ledger open, append, fsync, and cleanup.

## Positive observations

- The R2 combined-failure diagnostic now keeps the append exception as `primary_error`/`__cause__` and the close exception in structured cleanup attributes.
- The lock-held consumption path applies both UTC expiry and monotonic elapsed-time checks, and operator public-key normalization occurs before I11 filesystem access.
- I11 remains read-only (`advance_anchor=False`); the implementation does not issue authorization or dispatch actions.

## Disposition

I15 remains **not accepted** pending resolution of I15-R3-01 through the separate ledger API dependency path required by the active workorder. Preserve `ready_to_execute=False`, Gate G07 and M1/M2/M3 `OPEN`, and Gate 6 `NOT ACCEPTED` and incident-only. I15 acceptance remains limited to local-test consent verification and durable one-time challenge consumption.
