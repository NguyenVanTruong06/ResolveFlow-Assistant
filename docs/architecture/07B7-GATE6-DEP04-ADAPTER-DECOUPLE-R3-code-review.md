# Gate 6 DEP04 Adapter Decoupling R3 — Static Code Review

**Workorder:** `G07-07B7-GATE6-DEP04-ADAPTER-DECOUPLE-R3`  
**Disposition:** `ACCEPTED_FOR_IMPLEMENTATION_ONLY`  
**Review mode:** Static source and test review only.

## Artifact integrity

Submitted hashes match the reviewed workspace:

- `src/services/live_resolve_canary_adapter.py`: `388e118f94aac73e2c41e2a81b2396c581b8ec3e77a1011367e5f78bdbd13203`
- `tests/services/test_live_resolve_canary_adapter_decoupled.py`: `171b6d0bd418038a4ced9ee2fd3bc5ef056291c0e8cb2adc8a9c75d3c4f91ec6`
- `.bridge/task.md`: `17468ce21410621bfebd7bed85a8ddf6f8025cbcb805e26068d608f994943b7a`
- `.bridge/report.md` before this review entry: `2e7bc52b3b2ada258ffbd769a5a1ac9da8213c83deeb83f8cee7cea2cba066bf`

## Review findings disposition

- **DEP04-ADAPTER-DECOUPLE-R2-01 — closed.** Per-call `now_utc` is no longer accepted as a declared parameter. The public verifier and dispatch entry points reject it if passed through `**kwargs`; freshness and expiry use the adapter's clock at invocation time. The clock is supplied at construction for deterministic composition/testing, with an internal UTC clock as the default. Regression tests cover both attempted per-call override and expiry under an injected clock.
- **Abort terminal-commit lock retention — addressed.** If committing `ABORTED` fails before mutation, the exception path calls `force_in_doubt_locked()` and does not release the target lock; the regression test asserts both the active lock and in-doubt latch. The successful pre-dispatch abort path releases only after its terminal commit.
- **Post-mutation terminal-commit failure — addressed.** If committing `COMPLETED` fails, the adapter attempts to latch in-doubt and returns a non-success terminal result with `terminalized=False`; it does not release the target lock or report completion.

The previously reviewed R1 findings remain addressed in the submitted source: durable pre-mutation ordering, request/nonce correlation and response freshness, mandatory nominal plan digest binding, tests that guard production-path access without probing those paths, and removal of import-time private-key generation.

## Verification boundary and decision

The handoff reports 29 passing tests (exit code 0). Artifact hashes match the workspace. I did not independently run pytest; this was a static review. No Resolve SDK, Resolve connection, Oracle socket, network, host provisioning, systemd, or production key/state operation was performed.

R3 is **accepted for the implementation/mock-test scope only**. This decision does not authorize Oracle socket use, host deployment, Gate 6 execution, a readiness transition, or live Resolve operations. Preserve `ready_to_execute = False`; Gate 6 remains `NOT ACCEPTED` / incident-only; Gate G07 and Milestones M1/M2/M3 remain `OPEN`.
