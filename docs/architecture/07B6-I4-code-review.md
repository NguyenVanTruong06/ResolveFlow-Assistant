# G07-07B6-I4 Code Review — Isolated Mock-Only Trial Adapter & Integration

**Decision: CHANGES_REQUIRED**  
**Review mode:** Static source review; no tests, application, Resolve, IPC, FusionScript, or network commands were run.  
**Reviewed handoff:** `.bridge/report.md`, the I4 adapter and focused test, plus the accepted I1–I3 contracts and WAL consumer.

## Evidence and scope

The submitted SHA-256 values match the files currently on disk:

| Artifact | SHA-256 |
|---|---|
| `.bridge/report.md` | `7fd505b3888c37aee6ec5892fe0f3d69c17394884076b74cb2f305086c007b51` |
| `src/services/isolated_resolve_trial_adapter.py` | `adf2d5836efed5750eabe9b904c6db02e758f4fb2c09d6fd5bf3ef532b7997fc` |
| `tests/services/test_isolated_resolve_trial_adapter.py` | `6c10894506c2736f50ea9b3635c7db6f5afea5e8e0f9e458c89325ff0f6ad7e5` |
| `.bridge/ux-evidence/G07-07B6-I4/pytest_focused.log` | `28048ace648518bd272edb396410ba3f688934d68a5d86ec176d12aa2cfccd57` |
| `.bridge/ux-evidence/G07-07B6-I4/pytest_combined.log` | `848931e8179e11b1c0eb6b2c1cb8dfef6b53383148ecfde6e7207eeca3f45e39` |

The supplied logs end with 16 focused and 145 combined tests passing. They were inspected as handoff evidence, not rerun. This review found the following blockers despite those results.

## Findings

### I4-01 — BLOCKER: Mock admission trusts a caller-forgeable marker

**Location:** `src/services/isolated_resolve_trial_adapter.py:109-127, 141-156`

The constructor accepts `fake_gateway: Any`; admission checks only whether the object reports `_is_approved_mock_gateway is True` and lacks three named attributes. Any caller can provide another object with that public marker and an `execute_action()` implementation that performs arbitrary work. Such an object passes the tripwire without being `FakeResolveGateway`, so the adapter's mock-only guarantee depends on a self-asserted property.

**Required correction:** Bind admission to a non-forgeable adapter-owned gateway capability or enforce the exact approved fake implementation and its approved dispatch method. Add a negative test using a non-`FakeResolveGateway` object that sets the marker to `True` and implements `execute_action`; construction/admission must reject it before journal writes or calls.

### I4-02 — BLOCKER: START record violates the existing source WAL consumer schema

**Location:** `src/services/isolated_resolve_trial_adapter.py:210-227`; consumer: `src/services/resolve_execution_engine.py:1128-1150`

The adapter writes START `action_params` containing five identity fields plus `planned_actions_count`. The existing engine's START validator requires `total_actions` and then requires the exact key set `{"total_actions"}`. It will reject this START as missing `total_actions` and containing extra fields. The current I4 test checks only `ExecutionJournalService.replay_journal()`, which parses individual entries but does not exercise the engine's WAL state-machine validator; therefore its passing result does not establish consumer-compatible WAL.

**Contract conflict:** The I4 workorder asked for the five-field identity envelope in START, while the existing consumer explicitly forbids those fields there. These requirements cannot both hold. The source consumer's exact schema is the compatibility boundary; do not widen or alter the engine in this I4 slice to accommodate the incompatible START payload.

**Required correction:** Emit the source-consumer START schema exactly (`action_params={"total_actions": 1}` with the required control-record envelope). Keep the five-field identity envelope on action records where the stage contract permits it. Add an I4 test that validates the resulting journal with the existing source WAL consumer/state-machine validation, without modifying that consumer.

### I4-03 — BLOCKER: Existing journal history is not gated before another dispatch

**Location:** `src/services/isolated_resolve_trial_adapter.py:208-243`

When a journal already exists, the adapter takes `next_seq = len(replay.entries)` and appends a new ACTION_PENDING without checking `replay.is_clean`, the FSA terminal state, prior identity envelope, or whether a pending/committed/failed action already exists. Re-invoking the adapter with an execution ID whose journal already contains a pending or terminal action can therefore issue another gateway call; the append-only sequence check does not prevent duplicate dispatch or reject post-FINISH records.

**Required correction:** Fail closed before appending or dispatching when the execution journal already has prior history, unless a specifically authorized resume path validates the complete source WAL state machine and proves that dispatch is still permitted. For this one-action slice, the safe minimal rule is to accept only a genuinely new journal and reject any existing file/history. Cover pending, committed, failed, FINISH, torn-tail, and mismatched-identity histories in tests.

### I4-04 — BLOCKER: Failure to persist ACTION_COMMITTED leaves a dispatched action outside the in-doubt latch

**Location:** `src/services/isolated_resolve_trial_adapter.py:261-263, 316-330`

Only exceptions raised by `fake_gateway.execute_action()` enter the in-doubt handler. If the gateway returns after dispatch but appending/fsyncing ACTION_COMMITTED fails, the exception escapes from line 330 without `mark_timeline_in_doubt()`. The coordinator entry remains `LOCKED`, not `IN_DOUBT_LOCKED`, and the caller receives no structured uncertain-outcome result. A successful gateway return followed by an uncertain journal write is a post-dispatch durability failure and must not be treated as a clean outcome.

**Required correction:** Cover the entire post-pending interval—including gateway return, result validation, and ACTION_COMMITTED append—in a post-dispatch uncertainty boundary. If commit persistence cannot be confirmed, latch `IN_DOUBT_LOCKED`, retain the OS target lock, do not append a clean FINISH or retry, and propagate a structured in-doubt error/result. Add fault-injection tests for commit append/fsync failure after the fake gateway has been called.

### I4-05 — HIGH: Pending-record append errors are treated as clean rejection and release the target lock

**Location:** `src/services/isolated_resolve_trial_adapter.py:206-256`

The broad pre-dispatch `except` also catches failures from writing ACTION_PENDING. A write/fsync error can leave the journal with a complete pending record or a torn tail even though the gateway has not yet been called. The handler then releases the target lock solely because `has_target_lock()` is true. This conflates validation rejection with uncertain persistence and allows the next operation to proceed while the prior journal requires recovery.

**Required correction:** Separate validation failures from journal-persistence failures. Release only when the journal state is proven clean and no pending record was durably accepted; on ambiguous append/fsync outcome, preserve exclusion and require restart/reconciliation handling. Add fault-injection coverage for write failure before bytes, partial write, and fsync failure.

## Positive observations

- The adapter calls coordinator authorization binding and the canonical I1 assembler before dispatch, and compares the resulting action digest.
- The tested gateway-exception branch appends ACTION_FAILED with the pending payload and marks the target in doubt without releasing it.
- The implementation remains additive to the two I4 source/test files in the reviewed scope; this review did not modify source or tests.

## Decision and safety state

**CHANGES_REQUIRED.** Resolve I4-01 through I4-05 and submit a new revision for review. The passing supplied tests do not cover the identified trust-boundary, consumer-schema, replay, or post-dispatch durability cases.

This review does not accept I4 or authorize a subsequent slice, UI/production wiring, or live Resolve canary. Preserve `ready_to_execute=False`; Gate G07 and milestones M1/M2/M3 remain OPEN. No tests or Resolve/runtime calls were executed during this review.
