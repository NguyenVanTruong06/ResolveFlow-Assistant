# Dispatcher Admission IPC Contracts R1 — Static Review

- **Decision:** `CHANGES_REQUIRED`
- **Reviewed workorder:** `G07-07B7-GATE6-PHASE2-DISPATCHER-ADMISSION-CONTRACTS-R1`
- **Submitted hashes:** all five allowlisted file hashes matched the workspace.
- **Mode:** Static source/test/log review only. The authorized tests were not rerun; no sockets, services, host paths, keys, or Resolve operations were accessed.

## Finding

### DISPATCHER-ADMISSION-CONTRACTS-R1-01 — MEDIUM: Nested nominal contract checks accept subclasses

`DispatcherAdmissionRequest.__post_init__()` checks each nested signed envelope with `isinstance(...)` rather than exact type identity. `encode_admission_frame()` likewise accepts subclasses of the request/response DTOs. The workorder requires exact nominal DTO types. Because a subclass can override methods such as `to_dict()` or `to_json_bytes()`, it can pass the validation gates while changing the serialized wire claims, defeating the promised guarantee that the outer contract serializes the accepted nested DTO values without caller-defined behavior.

**Required correction:** Use exact type checks (`type(value) is ExpectedDTO`) for all four nested envelope fields, and exact supported DTO checks in `encode_admission_frame()`. Add regression tests using subclasses that override serialization methods and prove they are rejected before any frame bytes are emitted. Keep `from_dict()` reconstruction through the accepted concrete DTO parsers.

## Review notes

- Submitted SHA-256 values for the implementation, test file, pytest log, report, and unchanged task file matched the workspace.
- The submitted pytest log records `78 passed` and `EXIT_STATUS: 0` for the authorized command. No tests were rerun during review.
- The implementation otherwise provides strict field sets, duplicate-key rejection, RFC 8785 serialization, response status/error consistency, and bounded 4-byte length-prefix decoding in the reviewed paths.
- This contract work remains local and non-operational; no Dispatcher listener, socket operation, peer check, admission policy, or Gate 6 execution is accepted here.

## Safety disposition

`ready_to_execute = False`; Gate 6 remains `NOT ACCEPTED` / incident-only; G07 and M1/M2/M3 remain `OPEN`; WO5 remains disconnected. No runtime operations were performed.
