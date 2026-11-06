# G06-06C3 — Direction accepted; first slice intake/state only

2026-10-30. Design reviewed from report SHA256 `fbb93cdc607719c686600c240b5a90fba9c84c5e9d95859236342aabc79decc6`. Geometry accepted separately at06C2d-gate-acceptance.md. Proceed without another design-only round, with these binding corrections.

## Grounding corrections

- Canonical state field is `DirectorConfig.creative_context_dict` only; design alternates it with creative_context. Reuse actual CreativeContext/ReferenceItem and helpers, no parallel schema. Existing runtime pages are editors, config is accepted state; temporary invalid UI drafts may exist visibly without overwriting last-good config.
- Existing DirectorWindow._on_style_changed applies legacy preset then immediately StylePage.load_from_config. A naive panel load will erase unsaved brief/references. Preserve drafts across this actual callback sequence and test it, without editing coordinator in the first2file slice.
- Actual bounds from edit_plan_contracts.py are MAX_CREATIVE_CONTEXT_BYTES32768,128rules/kind,64references,64URLs. No512KiB CreativeContext budget and no silent4096character truncation; use existing canonical validation. ReferenceItem fields are reference_id/local_path/media_type/role/user_note; preserve stable IDs/notes/order.
- Design claims the core gives free_brief semantic precedence over other fields: no such semantic resolver exists in normalization. Preserve both as user-supplied intent, never claim contradiction/compliance solved by schema. UI sketch94% match is not calibrated confidence or authority.
- DirectorConfig is currently a runtime dataclass; the inspected coordinator syncs pages but has no demonstrated durable project save/load integration for the new field. ReviewPage QSettings does not persist the entire config. First slice provides owned config and explicit JSON-primitive roundtrip capability, not automatic restoration after restart. No new QSettings/global settings store or hidden file write.
- Two proposed production files cannot wire PromptPreparationRequest/worker/formatter/pending-generation or common review. Mark those paths PROPOSED_DEFERRED; do not claim creative input is already sent to any AI mode. Keep existing prompt/timeline route unchanged in this slice.
- UI save reports False/inline error on invalid draft without exceptions escaping Qt; pure config setter remains strict. Existing coordinator ignores save return values: navigation/AI validation gate is deferred with that consumer, not falsely claimed by adding a return flag.
- Approval factory accepts all omissions present in the normalized proposal; future partial checkbox choices must first produce a revised proposal/resolve unaccepted omissions, never pass an invented accepted_omission_ids factory argument. Approval/execution readiness remain separate; no factory change now.

## First implementation

**G06-06C3-S1-I — Creative intake and owned config roundtrip**, exactly director_state.py and pages/style_page.py plus focused tests. Visible Step2 editing for brief/rules/optional duration/reference metadata/URLs; transactional accepted state, no reference import into footage/assets, no file analysis/network. Valid config/state roundtrip and page/style transitions, not disk autosave or AI use.

After this slice: dedicated creative-context request/signature/prompt wiring with bounded async ownership; explicit project persistence ownership if desired; common proposal/omission review + app-owned approval; G07 independent preflight/execution. No reopening accepted metadata/geometry, no new product decision needed for D01–D03.
