# G07-07A5-I — Codex review

Reviewed 2026-11-02T13:42:46+07:00. **CHANGES_REQUIRED**. Report hash verified: `873bcd5b7d586fdcc9e4e9456fa99c2843c6f371cebf816d412557318afbb007`. Reported suite is substantial, but a binding authority defect remains in `query_active_timeline_authority`: `src/core/resolve_bridge.py` lines 181–189 falls back from `timeline.GetSetting("timelineFrameRate")` to `project.GetSetting("timelineFrameRate")`. The accepted design requires FPS from the active timeline only and forbids fallback. A project setting is not proof of the current timeline's effective FPS. Missing/empty/error timeline FPS must fail closed. Add a negative probe proving project FPS cannot rescue missing timeline FPS, plus positive exact timeline FPS controls.

Also verify ReadinessRequest and BuildPage behavior against the accepted G07-07A5-D review: deep opt-in only with complete approved source binding and valid timeline authority; missing authority preserves metadata-only path; every result keeps `ready_to_execute is False`. Do not expand scope or claim desktop acceptance.

Independent acceptance was not completed in this review; the worker's 154 count remains reported evidence only. Exact next workorder is in `.bridge/task.md`.
