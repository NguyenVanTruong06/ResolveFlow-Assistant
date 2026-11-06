# G06-06C2c — Bounded acquisition approved; synchronous GUI integration rejected

2026-10-30. Design reviewed in report `a22410fac0a630cd7c477aa7b6883433d81a44901c700dc01230cec230381fb5`. DIRECTION_ACCEPTED_WITH_BINDING_CORRECTIONS.

## Scope and policy answers

- The design correctly locates GUI callers of ClipManifest.get_or_create (auto_window.py:4513/5113, review_page.py:5516). A synchronous probe with 3–5s timeout PER CLIP is still blocking GUI work; timeout is not an async architecture. **Do not wire the new subprocess helper into ClipManifest now.** First deliver one bounded synchronous IO helper for a worker/CLI caller; future integration must establish the async owner before repairing that legacy call path. Missing get_video_metadata import remains a recorded prerequisite, not fixed by this slice.
- No audio-only option in the initial video API; keep ERR_NO_VIDEO_STREAM explicit. No ClipEntry.probe_error or altered ClipEntry schema, no inference of freshness based on duration0/fps30 signatures. No silent manifest backfill or mutation of proposals/approval.
- No global/in-memory or disk cache in this slice. Proposed (path,size,mtime) cache key is too weak and introduces lifetime/unbounded-growth questions; defer caching until owner/policy/stream/source binding is designed. No unconditional full hash of source footage.
- SourceFreshnessStat includes ctime_ns as well as size/mtime/dev/ino. Matching snapshots detects changes visible to those observations; it does not prove no mutation occurred throughout the interval or guarantee the JSON is bound against every replacement race. No absolute TOCTOU/content/CFR proof claim.
- Cancellation callback is mandatory API capability; it was missing from the proposed function signature and finite matrix. Check before spawn, during child supervision, after read and before return; cancellation discards results and cleans up owned process/readers.
- Reader threads must be drained/closed/joined on success and failures; daemon status is not ownership cleanup. Child timeout, stream overflow and reader errors need bounded cleanup, wait/reap, preserve primary error by chaining if cleanup fails. Do not return success with outstanding ownership. Record OS-level stat/spawn blocking limits rather than promising a hard deadline for every filesystem syscall.
- Restrict protocols, not merely URL-shaped input strings: a local playlist may reference other protocols. Use documented `-protocol_whitelist file`, absolute local input with `-i`, separate argv, shell=False, stdin DEVNULL, no automatic installs/static_ffmpeg.add_paths or global PATH mutation. [Official ffprobe input option](https://www.ffmpeg.org/ffprobe.html), [official protocol whitelist](https://www.ffmpeg.org/ffmpeg-protocols.html#Protocol-Options).
- No guessed FPS bools or duplicate is_known fields in the result. Duration is optional exact Fraction of reported seconds, not verified stream duration. The six-field VideoStreamHeader stays unchanged and unverified.

## Implementation order

`G06-06C2b-FINAL-THEN-06C2c1-I`: Part A fixes only the count sentinel type-gate. Part B creates **one new** `local_metadata_acquisition.py` with two small owned frozen DTOs and acquire_local_video_header; no existing IO consumer is changed. Combined production scope exactly two files: existing parser + new IO helper. Focused mocked tests only, no user media/live probe/UI/Resolve in this task. Exact API, resource limits, error codes and complete upfront matrix in workorder. Live generated-fixture verification can follow a successful code review.
