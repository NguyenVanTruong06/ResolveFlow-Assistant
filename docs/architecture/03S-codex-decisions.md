# Codex quyết định — G03-03S-D

ACCEPTED_WITH_BINDING_CORRECTIONS; giao03S1-I.
1. Chấp thuận scopeSFX+LUT, BGM/Overlay/Meme chưa sinh/import trongslice. UI nói rõ phạmvi, không giữ tooltip75+hottrend như đã nhậpđủ. Legacy phantom memes giữ như dữliệulịch sử, không đưa vào job/registry hoặc đếm thành đãsinh; chưa xóa sửa bundledmanifest diệnrộng.
2. produce_to_staging(storage,cancel,progress) phảiđượcbindstoragecụthể bằngclosure khigiao prepare_factory(cancel,progress). Không truyền rawclassmethod thiếu storage vào wizard.
3. Auto_prepare guard và buttons hiện phụthuộcsource/url: cậpnhật factorypath (không cầnsourcegiả), giữ timer/cancel safeguards. Hủy trước timer không được sinhfile; factory background bởiLibraryJob, không tựStarterPackWorker.
4. BộđếmSFX/LUT lấy filethực + generatorregistry hiện hành, không hard66 hoặc max(count,16). <0.5s/25MiB làUNMEASURED. Generatorsbatch chưacooperative bêntrong: checkcancel trước/sau từngbatch, nói rõ cancellation latency cóthểtới batch kếtthúc. Khônghứa hardrealtime/perchunkreserve.
5. Preflightbudget thậntrọng cho finitebatch, ensure_space trước mỗi batch và actualOSErrorhandling; khôngchuyển cảgenerator/model hoặc importtools. Táchpresets/recipes khỏiproducer nhưng khôngđổibootstrapapp.
6. Preserve legacyclasses/signatures nếu chưa cần xóa; chặnUIcaller là acceptance, không gọi engine đãwrite-safe mọicallsite. Root/UUIDjob ownership kiểmtra trướccleanup/return, khôngfollow symlink hoặc xóa đích khác khivolume/configđổi.
7. Gridmodelview/nhãnViệt giữ. Designreview không chạy generator/test; actualcounts/performancechưađo.
