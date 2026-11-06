# G06-06C3-S1-I — Intake boundary/roundtrip repair required

2026-10-30. Current report SHA256 `994f9c634f41c6b8ead1d7541fabc60322ec44a3f21452762efc338b24a98bf8` verified. Codex fresh finite command `QT_QPA_PLATFORM=offscreen PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/ui/test_creative_intake_state.py tests/core/contracts/test_edit_plan_creative_context.py -q` → **37passed in1.08s, exit0**.

Config setter ownership and normal style transition draft protection are present; first-slice in-memory/no-prompt/no-autosave boundary is correctly reported. REQUEST_CHANGES for the following reproduced input issues, within original2file scope. Evidence `.bridge/ux-evidence/G06-06C3-S1-I-CODEX-REVIEW/intake-boundary-probes.json` (9cases, isolated offscreen widgets/temp environment, no file/model/network/Resolve work).

## A — Valid canonical data cannot roundtrip losslessly

- Brief '  Giữ nguyên lời khách\n' saves trimmed. A rule '  Giữ nguyên câu  ' is trimmed. One canonical rule containing a newline becomes two rules merely through load/save. Core normalization preserves these valid strings/items; UI must not silently rewrite user requirements. Same principle applies to reference_urls and note values.
- Valid loaded durations1e-7 and1e20 render using scientific notation, then fail saving because get_draft selects float parsing only when '.' exists. Support numeric representations of valid loaded int/float without arbitrary string coercion or loss; normalizer still rejects invalid/nonfinite values.

## B — Failed load is destructive and can overwrite the target

StylePage.load_from_config catches any exception and loads {}. With a malformed config, prior 'LAST GOOD UI DRAFT' becomes empty, no error is shown, later save=True overwrites the malformed target with empty valid-looking context. Validate candidate before mutating any visible fields; failed load must leave config and editor data unchanged, show error, report failure and prevent silent save-over of that failed target until deliberate valid reload/recovery. Do not silently substitute empty except genuine legacy empty/missing/None. Getter for a genuinely absent legacy field currently raises AttributeError; handle legacy absence deliberately.

## C — Reference picker/note boundaries

- Unknown extension defaults to video: /controlled/not-video.pdf is accepted and labeled video. Native picker even offers All Files. Infer only recognized image/video extensions, deny unsupported input visibly without mutation; explicit trusted media-type metadata can remain separate from inferred picker behavior. Enforce64item cap and stable identity before committing additions; no file inspection to guess type.
- Editing a reference note to 'bad\x00note' throws ValueError through Qt sys.excepthook; UI still shows invalid note, but save=True stores old empty note with no error. Preserve invalid draft visibly and reject save without config mutation, never report success for dropping that edit. Handle reference/validation errors at Qt action boundaries.

## D — Verification gaps to correct in same repair

Current tests copy the coordinator callback rather than invoking production DirectorWindow._on_style_changed with a lightweight receiver. The alleged no-I/O test checks missing-file existence without boundary spies; layout test only isVisible doesn't show viewport reachability. Add isolated HOME/XDG/settings, narrow actual receiver invocation, programmatic no-file/network/model tripwires and ensureWidgetVisible/viewport-intersection tests. No desktop claim from offscreen tests.

Next **G06-06C3-S1-R1-THEN-S2-D**, HIGH: grouped two-file intake fix + focused tests, then design-only actual creative request/prompt binding. No reopening accepted geometry/metadata or changing coordinator/ReviewPage/workers in this repair.
