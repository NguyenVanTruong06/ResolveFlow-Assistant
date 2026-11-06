# Codex quyết định — G03-D

DESIGN ACCEPTED_WITH_BINDING_CORRECTIONS.
1. Đồng ý side-by-side version, không ghi đè/xóa pack cũ. Warning trùng name+version là thiết kế đích, chưa thêm ở03A1; UUID mới vẫn independent copy, không gọi dedup đã có.
2. StarterPack button bypass thật: _on_starter_pack_clicked gọi StarterPackWorker ->seed_all ghi repo assets và preset/recipe. Cần slice riêng routing/staging producer, không lén coi PackIngestWizard(source=manifest) đã hỗ trợ custom producer. Chưa xóa/deprecate engine khi chưa consumer inventory.
3. CloudPackInstallWorker là dormant legacy: rg chỉ thấy class definition và tests instantiate; Hub _on_install_pack_requested/_on_custom_drive_download/_install_local_pack_file đã gọi _review_pack ->wizard. Import/typeannotation không chứng minh active runtime bypass. Không gắn P0 UI nếu chưa caller mới.
4. Không duyệt gộp streaming/ZIP/Starter thành2modules: routing cần UI và generator paths. Giao03A1 direct HTTP trong pack_ingest.py trước; rồi slice khác theo review. Starter bypass vẫn backlog ưu tiên có evidence, không coi đã sửa.
5. Không global10GiB cap cloud_downloader vì dùng source footage dài. Pack-only cap trước ở PackIngestService directHTTP. Drive shared downloader cần opt-in policy riêng sau.
6. HTTP urllib thường đã raise403/404; không gọi “luôn lưu403/404” là fact. Trọng tâm200 HTML/errorpage vàunknownLength; URL extension/MIME đơn lẻ không chứng minh valid.
7. Publish boundary là registry replace sau rename, không phải acquirelock. Taxonomy commit đã reject unknowncategory; thiếu ởinspection không chứng minh register category arbitrary.
8. Native transition .drfx/.setting không có nghĩa generic insertablevideo; giữ hướng dẫn Effects. Các ceiling10GiB/20GiB/100:1 là proposedpolicy, không benchmark;03A1 chỉapprove compressed HTTP10GiB default pack cap.
Design-only review không chạy pytest/app/network; chưa xác minh realDrive/auth/quota.
