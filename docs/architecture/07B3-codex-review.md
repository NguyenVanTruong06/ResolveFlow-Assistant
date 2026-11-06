# G07-07B3-D — Codex review

Reviewed 2026-11-02T18:13:13+07:00: **CHANGES_REQUIRED** before approving the implementation slice. Report/design hashes match; the design appropriately separates serialized Gateway from coordinator and uses WAL/IN_DOUBT. Remaining safety contradictions need correction.

1. Do not claim **exactly-once** mutation. Advisory lock/idempotency prevents concurrent duplicate admission, but cannot make external Resolve IPC atomic with disk WAL. A crash after Resolve applies an action and before COMMITTED is durable remains IN_DOUBT and must never be called exactly-once.
2. FLT-08 says duplicate COMPLETED is `NO_OP` and returns prior success, while CP-03 says reject. Choose one consistent safe contract: return an explicit prior-result reference/status without dispatch and without reporting a new successful execution.
3. A 15-second Gateway timeout cannot imply the underlying Resolve call stopped. Do not release lock, issue a later mutation, or report terminal-safe while the RPC may still be running. Define bounded observation vs physical completion; unresolved calls stay busy/IN_DOUBT and retain ownership until Gateway confirms finished.
4. Cancellation after action K must not assume “finish action K safely” if request races with IPC. Define cancellation only at proven pre-dispatch checkpoints; once dispatched, await actual completion or IN_DOUBT. Do not set ABORTED if actions committed; use a truthful partial/interrupted state already supported or specify a schema change separately.
5. The design references `repair_torn_tail()` despite accepted 07A6/07A7 policy requiring preservation of original journal bytes. Recovery must stay non-destructive; never auto-truncate evidence.
6. Separate pure design scope from executor authorization. `ready_to_execute=False` remains until a distinct approved gate; 07B4 cannot enable mutation based only on this design.

Next task: `G07-07B3-D-R1`, design correction only.
