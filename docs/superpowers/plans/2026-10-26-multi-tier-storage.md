# Multi-tier Storage Implementation Plan

Goal: JSON external library with offline browsing, reviewed pack ingestion, verified migration.
Architecture: StorageManager owns root identity/config/registry; PackIngestService owns bounded IO and manifests; PySide6 wizard owns user review; existing Hub/Indexer provide integration.
Tech: Python3.12, standard library, PySide6 model/view + QThread.
Spec: docs/multi-tier-storage.md.

- [x] Write focused failing tests: root offline/no fallback, ingestion overrides/ZIP safety/space, linked paths, verified migration, UI review.
- [x] Implement src/core/storage_manager.py and src/core/pack_ingest.py.
- [x] Implement src/ui/widgets/pack_ingest_wizard.py with background jobs, table review and storage settings.
- [x] Route Asset Hub local/cloud imports through review; merge registry into Studio/Dock/search with taxonomy adapter.
- [x] Run focused storage/ingest/UI/integration regressions; inspect rendered UI, document limits and results.

Constraints: preserve dirty work, Vietnamese comments, existing QSettings IDs; no legacy card_widgets, no full pytest suite, no destructive migration on user data during development. No additional model/package downloads. Direct implementation authorized by user; no Worker delegation or commits.

Validation: focused102testspass25.01s; finalstorage/wizard23testspass4.69s after manifest validation. Screenshots inspected at .bridge/storage-evidence/. No actual external-drive migration, Drive download, or Resolve mutations.
