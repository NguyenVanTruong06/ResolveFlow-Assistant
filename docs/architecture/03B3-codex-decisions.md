# Codex quyết định — 03B2-T / 03B3-D

03B2 ACCEPTED: Codex28UItestsPASS38.80s exit0sauprocesscompletion. Teststubtelemetry khôngchứngminhStudio production lifecyclefixed; deletedLibraryJobhandle làfailingteardownevidence, segfaultcausalchaincònUNVERIFIED.
03B3design ACCEPTED_WITH_BINDING_CORRECTIONS:
- DraftRAMonly, IDđãcó chọnexistingkhôngoverwrite, duplicate labelkhácIDcho phéphiểnthịID, noimplicitmanifestcreate.
- Reuse core/contracts/asset_dto.py:is_valid_custom_category_id, khôngcopyreserved/regexhai nơi. Corecommitrevalidateuntrustedjob['draft_taxonomy']; UIvalidationkhôngđủ.
- KhôngformalTwoPhaseCommit/ACIDclaim. Underlock: readlatesttaxonomy+registry ->validate/pre-serialize ->preparemanifest/prune ->renamepayload ->writemergedtaxonomy(ifnew) ->publishregistry ->best-effortsnapshot. Errortrướcpublish: best-efforttaxrestorevàfolderrollback độc lập; rollbackfailgiữgốc+diagnostics, khẳngđịnhchưa khôi phụchoàn toàn. Nojournal/powerlossguarantee.
- NếuregisteredCIDxuấthiện concurrentvớisamelabel thìreuse, differentlabelreject đểuserreview; khôngblind dictunionclobberbase.
- Restorechỉnếu currenttaxonomyvẫnbằngmergedwrittenbyjob vàoriginalidentitysafe, khôngoverwriteexternalchanges. Nếuroot/offline/rollbackfailthìkhôngforce cleanup hoặcretry.
- snapshotfail sau registrypublish vẫnsuccesswarning, khôngrollbacknewtaxonomy/pack. Returncontracthiệncópack_id/count/snapshot_synced/warnings; khôngthêm name/folderrồigọichúngexisting.
- Noemptydraftjob phảiđiđườnglegacy, khôngrewrite taxonomyhay thayerrorpolicy.
- Boundcrashwindow taxonomywritten/registrymissing đểextraunusedcategorycóthểcòn sauSIGKILL; deferredjournal/recovery, khônghứacancel/failedcommitluônzerochanges khirollbackfails.
