# G07-07B6-I4 Revision R2 Code Review

**Decision: CHANGES_REQUIRED**  
**Review mode:** Static source review. No tests, Resolve application, IPC, FusionScript, or network calls were run.  
**Basis:** R1 review `docs/architecture/07B6-I4-code-review.md` and the submitted R2 source, tests, report, and logs.

## Artifact verification

The reported hashes match the files currently on disk:

| Artifact | SHA-256 |
|---|---|
| `.bridge/task.md` | `bde28fe409a9d30efc9f5a9c26c99fd7e6eb4f42be838a317f314362d92dd47a` |
| R1 review | `795d6dacf6f79899d431efb5295d9585eef4ac7c8fddb70b0e01c7459221a419` |
| `.bridge/report.md` | `a2a39c3b3becd1a56c603fedf6c22eccf477f1d158e390bdfae825b4da05574a` |
| `src/services/isolated_resolve_trial_adapter.py` | `619dc8ad199960ccaad2afb68f4ef2898c05f7bbad43a20322af3ab524cc7fc5` |
| `tests/services/test_isolated_resolve_trial_adapter.py` | `882f5bae438d0d18de6ddda6bb3af1861ac9d5870133e9eec20a10ca01600f47` |
| Focused log | `510e62aa4a98a2556e3b40c9677d5de12e43908f7c9fb0ff68a88238152525e5` |
| Combined log | `adb7263815645530d595176f8c4af5f1acfd6b1df73a0366e1e81eca7fa66a3a` |

The supplied logs end with 27 focused and 156 combined tests passing. I inspected the log tails but did not rerun them.

## R1 findings reassessed

- **I4-01:** Addressed for ordinary injected objects: the tripwire requires the exact `FakeResolveGateway` type and its authentic bound `execute_action` implementation, with negative tests.
- **I4-02:** Addressed: START uses `{"total_actions": 1}`, and the test calls the source control-envelope and WAL state-machine validators.
- **I4-03:** Dispatch against a pre-existing journal is now rejected. However, its rejection path still releases the target lock for every journal state, including unresolved or already-mutated histories; see R2-01 below.
- **I4-04:** Commit append errors now enter an in-doubt branch. That branch silently ignores failure to mark the coordinator in doubt; see R2-03.
- **I4-05:** START/PENDING append errors are separated from pure validation and do not immediately release the lock. Their safety remains process-local and is not preserved across restart; see R2-02.

## Findings

### I4-R2-01 — BLOCKER: Existing unresolved or terminal WAL is rejected, then its exclusion lock is released

**Location:** `src/services/isolated_resolve_trial_adapter.py:219-249`; test: `tests/services/test_isolated_resolve_trial_adapter.py:668-725`.

The journal-exists check raises inside the same `except` block used for clean pre-journal validation failures. That handler calls `release_target_lock()` whenever this coordinator reports a lock. The parameterized test explicitly asserts release for existing `ACTION_PENDING`, `ACTION_COMMITTED`, `ACTION_FAILED`, `FINISH`, and torn-tail journals.

Rejecting a replay is correct, but releasing exclusion is not. A prior PENDING may represent an action whose outcome is unknown; COMMITTED may represent a mutation already present; FAILED and torn-tail histories require reconciliation. Releasing the target lock lets the next execution proceed without validating or resolving that history. The source WAL consumer cannot convert a generic “journal already exists” error into proof that the timeline is clean.

**Required correction:** Separate clean validation rejection from prior journal history. Never release the target lock for an existing journal unless its complete source state and independent physical evidence prove a clean pre-dispatch outcome. Preserve the lock and route unresolved histories to the approved reconciliation path. Update tests so unresolved/terminal/torn histories assert that exclusion remains held and no new dispatch is possible.

### I4-R2-02 — BLOCKER: In-doubt and one-use target state are not durable across coordinator restart

**Location:** `src/services/isolated_resolve_trial_adapter.py:282-287, 327-390`; `src/services/durable_trial_target_ledger.py:920-924, 1020-1057, 1195-1206`.

`mark_timeline_in_doubt()` updates only `_target_locks` and `_lock_state`, both in-memory dictionaries. The adapter does not persist a corresponding `ACTIVE`/in-doubt record through the coordinator's `DurableTrialTargetLedgerService`. When the process exits, the OS releases `flock`; a fresh coordinator initializes empty lock registries and its pre-admission check has no durable in-doubt state to consult. The new adapter then encounters the old journal and, under the current R2-01 path, releases the newly acquired lock.

Consequently, same-process tests do not establish crash/restart exclusion. This is especially material after a gateway exception, a commit persistence failure, or an ambiguous WAL append.

**Required correction:** Bind admission, dispatch, and terminal/in-doubt outcomes to the durable target ledger using the accepted I3 API. Ensure restart repair consults durable target state and the complete WAL before any lock can be released or a new trial admitted. Add a restart test that creates an unresolved I4 history, constructs a fresh coordinator, and proves that admission remains blocked until authorized reconciliation.

### I4-R2-03 — HIGH: Commit-persistence handler suppresses failure to latch in-doubt state

**Location:** `src/services/isolated_resolve_trial_adapter.py:373-390`.

After ACTION_COMMITTED persistence fails, the adapter calls `mark_timeline_in_doubt()` but catches every exception and executes `pass`. It then raises an error whose attributes claim `target_lock_state="IN_DOUBT_LOCKED"` and `lock_retained=True` whether or not the coordinator changed state. If the coordinator call fails, the entry can remain `LOCKED`; callers receive a false state claim and later cleanup/re-admission logic can treat the target as ordinary.

**Required correction:** Do not suppress the coordinator transition failure or claim the transition succeeded. Preserve the OS descriptor/lock, surface both the commit and latch errors in a structured fail-closed error, and prevent subsequent adapter operations from treating the target as clean. Durable state from I4-R2-02 must remain authoritative if the in-memory latch cannot be written.

## Positive observations

- The exact fake-class and dispatch-method checks close the R1 caller-forgeable marker path.
- START serialization now matches the existing engine schema, and the success test exercises the source control-record and WAL state-machine checks.
- The adapter now rejects any pre-existing journal before dispatch and distinguishes validation from journal append failures.
- The gateway-exception and commit-append-failure branches retain the current process's target descriptor in the covered test paths.

## Decision and safety state

**CHANGES_REQUIRED.** Resolve I4-R2-01 through I4-R2-03 and submit the next revision for review. The submitted test counts and logs are acknowledged as handoff evidence; they do not cover the lock-release, fresh-coordinator restart, or failed-latch paths described above.

This review does not accept I4 or authorize UI/production wiring, a later implementation slice, or live Resolve canary. Preserve `ready_to_execute=False`; Gate G07 and milestones M1/M2/M3 remain OPEN. No source or test files were changed and no tests were run during this review.
