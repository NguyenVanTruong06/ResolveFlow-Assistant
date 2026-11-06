# G04-04C6b-I — Codex review (2026-10-27)

CHANGES_REQUIRED. Codex chạy đúng 7 focused files:184passed,5warnings,3.26s,exit0. Đã đọc guard, normalization, hover entrypoints và tests. Test pass chưa bao phủ các boundary sau; probe tmpfiles/DI trực tiếp xác nhận:
1. bundled_root realpath_fn ném PermissionError -> allowed=True OK_BUILTIN_PREVIEW_VERIFIED với file ngoài root. Guard bỏ lỗi _safe_resolve_path ở dòng833 rồi chỉ containment nếu root truthy; fail-open.
2. linked external_path master không tồn tại nhưng companion same-stem tồn tại -> allowed=True OK_LINKED_PREVIEW_VERIFIED. Trái quyết định anchor linked phải verified, chưa có offline proxy mapping. Managed source missing + valid owned preview là policy riêng, không dùng để suy cho linked.
3. _normalize_preview_payload(MappingProxyType(managed_record), role hints) -> source_kind=None,origin_library_id=None. isinstance(dict) bỏ Mapping, metadata managed thành legacy qua hints; có thể bỏ identity check trong player consumer.
4. Managed preview_path ngoài active_root nhưng source master đúng kho có thật -> ERR_SYMLINK_ESCAPE thay vì fallback verified master đã được task cho phép. Candidate denial không đồng nhất identity/provider denial.

Giao 04C6b-R1 trên cùng 2 modules, tests bốn ca và faults. Chưa desktopverified; không mở Inspector/legacy hay sửa drag/insert. Không cần vòng thiết kế mới.

## Nghiệm thu R1 — 2026-10-27
ACCEPTED trong scope core preview + SharedHoverPreview. Codex đọc lại bốn branches và normalization, chạy exact7focusedfiles:188passed,5Qt deprecation warnings,3.17s,exit0. Có decoder stderr moov atom not found do fake media trong focused run, không có test failure/crash; không coi là bằng chứng codec/playback desktop. Bundled authority errors propagate, linked anchor regular-file gate trước companion, Mapping giữ provenance, managed unsafe candidate fallback chỉ về verified master. Prior insert/drag/lifecycle regression tests đạt trong scope.

Đóng code review này, không yêu cầu lặp lại suite. Các giới hạn: chưa desktopverified preview mới, Inspector/legacy drag/preview và Text/Fusion còn deferred; offline out-of-authority proxy mapping chưa implement, temp exports thành công giữ cho consumer đọc muộn. Tiếp theo G04-04C6-AUTO-UI checklist desktop hữu hạn trước chuyển residual tiếp.
