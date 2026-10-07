# Task 1 - Fix Round 1

**Review Findings to Address:**
1. **Critical (Load-bearing):** `_generate_id` uses Python's built-in `hash()` function for string hashing. Since Python 3.3+, string hashes are randomized per-process by default. This means asset IDs will change every time the application is restarted. You MUST use a deterministic hash like `hashlib.md5(str(path).encode()).hexdigest()[:8]` for stable IDs.
2. **Minor:** The `rglob(f"*{ext}")` pattern is case-sensitive on non-Windows platforms. Fix this by scanning all files and checking if `path.suffix.lower() == ext`.

**Steps to Execute:**
1. Read `src/core/asset_indexer.py` and implement the fixes above.
2. Run the test suite `pytest tests/test_asset_indexer.py`.
3. Commit with message: `fix(core): ensure deterministic asset IDs and robust extension matching in AssetIndexer`.
4. Report back DONE.
