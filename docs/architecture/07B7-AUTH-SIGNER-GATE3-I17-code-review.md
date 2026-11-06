# Gate 3-I17 Code Review

**Workorder:** G07-AUTH-SIGNER-GATE3-I17 — Local-Test Trusted Canary Dispatcher Service  
**Decision:** **CHANGES_REQUIRED**  
**Review type:** Static review and artifact/evidence hash comparison; tests not rerun.

## Artifacts reviewed

| Artifact | Submitted SHA-256 | Review status |
|---|---|---|
| `.bridge/task.md` | `cef7a860f03c9c910d2584cf393e21dde4970ad58e823d6d66bb7bdc2f3a88b4` | Matches workspace |
| `.bridge/report.md` | `5164175a10904e8722099d84cf0af7ca1523f586840a403dec936c2008791a5b` | Matches workspace |
| `src/services/trusted_canary_dispatch_service.py` | `9a5fddbc0c9390a098a64e5dd578b06ad189a436b38cb7c2dba900444b14d174` | Matches workspace |
| `tests/services/test_trusted_canary_dispatch_service.py` | `e2eb5f4ccb125bfcf2cabc82b9b5b91916c7d66f64bd99f66455a82baf692a97` | Matches workspace |
| `.bridge/ux-evidence/G07-AUTH-SIGNER-G3-I17/pytest.log` | `af6d662d782b16cf9cb45dd903c2a0ee74d4660b1f81c9229c06c9e546ab6b7d` | Matches workspace; reports 389 passed, 2 warnings, exit 0 |

## Findings

### I17-01 — HIGH: Plan binding is optional and is not recomputed

`dispatch_canary_action()` accepts `plan: Optional[Any] = None` (`src/services/trusted_canary_dispatch_service.py:781-786`) and passes it to `_enforce_scope_boundary()`. The plan/action/target/idempotency checks run only inside `if plan is not None` (`:625-660`). Therefore a caller can omit the plan entirely and still reach the oracle gate and mock dispatch. Even when a plan is supplied, the service checks selected attributes/action ID but does not recompute the canonical plan digest and compare it to `authorization.plan_digest_sha256`.

The workorder requires the action, plan, I16 authorization, consent digest, payload digest, and both target identities to match exactly. The current optional plan path bypasses that binding and the supplied plan path does not prove the signed plan digest corresponds to that plan.

**Required correction:** Require the exact accepted `TimelineExecutionPlan` type; require exactly one planned action; bind the dispatched action and target to its authoritative plan fields; recompute the canonical plan digest using the accepted binder serializer and compare it in constant time with the authorization's signed plan digest before requesting oracle verification. Add tests proving omission, wrong plan type, changed plan content, and mismatched planned action all cause zero oracle/adapter calls.

### I17-02 — HIGH: Receipt persists invented source and timeline metadata

When creating `ExecutionReceipt` (`src/services/trusted_canary_dispatch_service.py:903-943`), the implementation hardcodes `timeline_resolution=(1920, 1080)` and constructs `SourceFileDigestBinding.stat_identity=(0, 0, 1024, 0, 0)`. These values are not obtained from verified inputs or observations. The receipt is persisted through the accepted `ExecutionReceiptService`; its DTO therefore records fabricated resolution and filesystem identity as if they were receipt facts. Marking the state `INITIALIZED` and adding `MOCK_ONLY` error text does not make those fields truthful or safe for consumers that parse the receipt schema.

This violates the workorder instruction to persist a truthful mock-only receipt and to stop rather than synthesize production-shaped terminal/receipt data when the accepted contract cannot represent mock-only evidence truthfully.

**Required correction:** Do not write fabricated values into `ExecutionReceipt`. Either derive every persisted field from authoritative plan/source metadata and represent mock-only provenance explicitly through an approved, truthful schema/API, or stop and report the receipt-contract dependency for a separate bounded workorder. Add tests that inspect the persisted receipt and prove no placeholder or fabricated source/stat/resolution facts are emitted.

## Review notes

The implementation follows the mock-only adapter boundary and its submitted log reports **389 passed, 0 failed, 2 warnings**, exit code 0. The test suite was not rerun during this static review. The two findings prevent acceptance of I17. No later slice, socket/daemon work, production trust boundary, Resolve access, or live operation is authorized.

`ready_to_execute=False`; Gate G07 and Milestones M1/M2/M3 remain `OPEN`; Gate 6 remains `NOT ACCEPTED` and incident-only.
