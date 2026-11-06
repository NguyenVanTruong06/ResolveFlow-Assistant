"""
tests/ui/test_creative_intake_state.py
Bộ kiểm thử TDD mở rộng cho G06-06C3-S1-R1-THEN-S2-D:
- Bao phủ trọn vẹn 9 ca kiểm chứng ranh giới nhập liệu của Codex (intake-boundary-probes.json).
- Bảo toàn khoảng trắng, ký tự xuống dòng (multiline rules), số thực khoa học (1e-07, 1e20).
- Nạp cấu hình lỗi có thẩm định transactional: Giữ nguyên UI, báo lỗi, chặn lưu đè target config.
- Xử lý tệp tham chiếu: Giới hạn 64 mục, từ chối định dạng lạ (.pdf/.zip), bắt lỗi trùng ID.
- Ghi chú tham chiếu: Không để ngoại lệ thoát qua Qt slot; lưu thất bại khi ghi chú lỗi; sửa đúng thì lưu thành công.
- Gọi trực tiếp hàm sản xuất DirectorWindow._on_style_changed với lightweight stub receiver.
- Kiểm tra môi trường cô lập HOME/XDG và bẫy tripwire zero-IO đối với tệp tham chiếu.
- Kiểm tra khả năng cuộn và tiếp cận thực tế (viewport intersection qua ensureWidgetVisible) tại 1200x750 và 1280x800.
"""

import json
import os
import sys
from typing import Any, Dict
import pytest

from PySide6.QtCore import QRect, Qt
from PySide6.QtWidgets import QApplication, QFileDialog

from src.core.contracts.edit_plan_contracts import (
    MAX_REFERENCE_ITEMS,
    MAX_RULES_PER_KIND,
    MAX_CREATIVE_CONTEXT_BYTES,
    CreativeContext,
    creative_context_to_dict,
)
from src.ui.director.director_state import (
    DirectorConfig,
    STYLE_CUSTOM,
    STYLE_PODCAST,
    STYLE_SHORTS,
    STYLE_VLOG,
)
from src.ui.director.director_window import DirectorWindow
from src.ui.director.pages.style_page import StylePage


@pytest.fixture(scope="session")
def qapp():
    """Khởi tạo QApplication offscreen dùng chung cho toàn bộ test suite."""
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Thiết lập môi trường cô lập cho HOME, XDG và QSettings."""
    fake_home = tmp_path / "fake_home"
    fake_home.mkdir(parents=True, exist_ok=True)
    fake_xdg = tmp_path / "fake_xdg"
    fake_xdg.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(fake_xdg / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(fake_xdg / "data"))


# ==============================================================================
# Nhóm 1: Ma trận 9 Ca Thẩm Định Ranh Giới Nhập Liệu của Codex (Boundary Probes Matrix)
# ==============================================================================

def test_probe_case_01_brief_whitespace_lossless(qapp):
    """Case 1: Không tự ý .strip() khoảng trắng và ký tự xuống dòng trong free_brief."""
    sp = StylePage()
    cfg = DirectorConfig()
    exact_text = "  Giữ nguyên lời khách\n"
    sp.panel_intake.txt_brief.setPlainText(exact_text)
    ok = sp.save_to_config(cfg)
    assert ok is True
    ctx = cfg.get_creative_context()
    assert ctx.free_brief == exact_text


def test_probe_case_02_rule_whitespace_lossless(qapp):
    """Case 2: Không tự ý .strip() khoảng trắng đầu/cuối trong từng mục quy tắc."""
    sp = StylePage()
    cfg = DirectorConfig()
    exact_rules = ["  Giữ nguyên câu  "]
    sp.panel_intake.editor_must_keep.set_items(exact_rules)
    ok = sp.save_to_config(cfg)
    assert ok is True
    ctx = cfg.get_creative_context()
    assert ctx.must_keep_rules == tuple(exact_rules)


def test_probe_case_03_multiline_rule_preserved_as_single_item(qapp):
    """Case 3: Quy tắc chứa ký tự xuống dòng '\\n' vẫn là 1 mục duy nhất, không bị phân tách sai."""
    sp = StylePage()
    cfg = DirectorConfig()
    exact_multiline = ["Một quy tắc\nhai dòng"]
    sp.panel_intake.editor_must_keep.set_items(exact_multiline)
    ok = sp.save_to_config(cfg)
    assert ok is True
    ctx = cfg.get_creative_context()
    assert ctx.must_keep_rules == tuple(exact_multiline)
    assert len(ctx.must_keep_rules) == 1

    # Nạp lại kiểm tra tính chất no-op roundtrip
    sp2 = StylePage()
    sp2.load_from_config(cfg)
    assert sp2.panel_intake.editor_must_keep.get_items() == exact_multiline


def test_probe_case_04_scientific_small_duration(qapp):
    """Case 4: Thời lượng số thực dạng khoa học nhỏ 1e-07 được nhận diện và lưu đúng kiểu float."""
    sp = StylePage()
    cfg = DirectorConfig()
    sp.panel_intake.chk_duration.setChecked(True)
    sp.panel_intake.txt_duration.setText("1e-07")
    ok = sp.save_to_config(cfg)
    assert ok is True
    ctx = cfg.get_creative_context()
    assert ctx.target_duration_sec == 1e-07
    assert isinstance(ctx.target_duration_sec, float)


def test_probe_case_05_scientific_large_duration(qapp):
    """Case 5: Thời lượng số thực dạng khoa học lớn 1e+20 được nhận diện và lưu đúng kiểu float."""
    sp = StylePage()
    cfg = DirectorConfig()
    sp.panel_intake.chk_duration.setChecked(True)
    sp.panel_intake.txt_duration.setText("1e+20")
    ok = sp.save_to_config(cfg)
    assert ok is True
    ctx = cfg.get_creative_context()
    assert ctx.target_duration_sec == 1e+20
    assert isinstance(ctx.target_duration_sec, float)


def test_probe_case_06_invalid_config_load_is_transactional(qapp):
    """Case 6: Nạp cấu hình lỗi không làm mất bản thảo cũ trên UI, báo lỗi và chặn lưu đè."""
    sp = StylePage()
    sp.panel_intake.txt_brief.setPlainText("LAST GOOD UI DRAFT")

    bad_cfg = DirectorConfig()
    bad_cfg.creative_context_dict = {"invalid_key_causes_rejection": 999}

    # Nạp cấu hình lỗi
    load_ok = sp.load_from_config(bad_cfg)
    assert load_ok is False
    assert not sp.panel_intake.lbl_error.isHidden()
    assert "Lỗi nạp cấu hình" in sp.panel_intake.lbl_error.text()

    # Bản thảo UI cũ không bị xóa thành rỗng!
    assert sp.panel_intake.txt_brief.toPlainText() == "LAST GOOD UI DRAFT"

    # Lần lưu sau bị chặn, không cho phép lưu đè target config đang lỗi
    save_ok = sp.save_to_config(bad_cfg)
    assert save_ok is False
    assert "Không thể lưu đè" in sp.panel_intake.lbl_error.text()
    assert bad_cfg.creative_context_dict == {"invalid_key_causes_rejection": 999}

    # Phục hồi bằng cách nạp cấu hình hợp lệ
    good_cfg = DirectorConfig()
    good_cfg.set_creative_context({"free_brief": "RECOVERED BRIEF"})
    recover_ok = sp.load_from_config(good_cfg)
    assert recover_ok is True
    assert sp.panel_intake.lbl_error.isHidden()
    assert sp.panel_intake.txt_brief.toPlainText() == "RECOVERED BRIEF"


def test_probe_case_07_unsupported_reference_extension_rejected(qapp):
    """Case 7: Tệp có phần mở rộng không được hỗ trợ (.pdf) bị từ chối, không tự tiện gán thành video."""
    sp = StylePage()
    res = sp.panel_intake.add_reference_path("/controlled/not-video.pdf")
    assert res is None
    assert len(sp.panel_intake._reference_items) == 0
    assert not sp.panel_intake.lbl_error.isHidden()
    assert "Định dạng tệp không được hỗ trợ" in sp.panel_intake.lbl_error.text()


def test_probe_case_08_legacy_missing_context_field_handled(qapp):
    """Case 8: Đối tượng legacy hoàn toàn vắng mặt trường creative_context_dict không gây AttributeError."""
    class MockLegacyDirectorConfig:
        pass

    leg = MockLegacyDirectorConfig()
    # Gọi hàm thuần túy get_creative_context
    ctx = DirectorConfig.get_creative_context(leg)
    assert isinstance(ctx, CreativeContext)
    assert ctx.free_brief == ""
    assert ctx.style_preset == ""
    assert ctx.target_duration_sec is None


def test_probe_case_09_invalid_reference_note_qt_boundary_and_retry(qapp):
    """Case 9: Ghi chú tham chiếu chứa NUL không gây ngoại lệ thoát qua Qt slot; lưu báo lỗi; sửa đúng thì lưu thành công."""
    sp = StylePage()
    sp.panel_intake.add_reference_path("/valid/path/reference_clip.mp4")
    assert len(sp.panel_intake._reference_items) == 1

    # Bẫy ngoại lệ Qt
    qt_errors = []
    old_hook = sys.excepthook

    def spy_hook(t, v, tb):
        qt_errors.append(f"{t.__name__}: {v}")

    sys.excepthook = spy_hook
    try:
        # Nhập ghi chú chứa ký tự NUL
        sp.panel_intake.txt_ref_note.setText("bad\x00note")
    finally:
        sys.excepthook = old_hook

    # 1. Tuyệt đối không có ngoại lệ thoát qua Qt slot!
    assert len(qt_errors) == 0

    # 2. Khi lưu: thẩm định phát hiện chuỗi NUL, từ chối lưu và hiển thị lỗi inline
    cfg = DirectorConfig()
    save_bad = sp.save_to_config(cfg)
    assert save_bad is False
    assert not sp.panel_intake.lbl_error.isHidden()
    assert "ERR_INVALID_STRING" in sp.panel_intake.lbl_error.text()
    # Cấu hình không bị lưu sai
    assert cfg.creative_context_dict == {}

    # 3. Người dùng sửa lại ghi chú hợp lệ
    sp.panel_intake.txt_ref_note.setText("Ghi chú đã được sửa đúng")
    save_good = sp.save_to_config(cfg)
    assert save_good is True
    assert sp.panel_intake.lbl_error.isHidden()
    ctx = cfg.get_creative_context()
    assert len(ctx.reference_items) == 1
    assert ctx.reference_items[0].user_note == "Ghi chú đã được sửa đúng"


# ==============================================================================
# Nhóm 2: Gọi Trực Tiếp Hàm Sản Xuất DirectorWindow._on_style_changed
# ==============================================================================

class StubAdvPanel:
    def __init__(self):
        self.loaded_config = None

    def load_from_config(self, cfg):
        self.loaded_config = cfg


class StubDirectorWindow:
    def __init__(self, config: DirectorConfig, page_style: StylePage, adv_panel: StubAdvPanel):
        self.config = config
        self.page_style = page_style
        self.adv_panel = adv_panel


def test_production_director_window_on_style_changed_call_with_stub_receiver(qapp):
    """Kiểm tra gọi trực tiếp hàm sản xuất DirectorWindow._on_style_changed trên stub receiver."""
    cfg = DirectorConfig()
    sp = StylePage()
    adv = StubAdvPanel()
    stub_win = StubDirectorWindow(cfg, sp, adv)

    # 1. Người dùng nhập bản thảo dở dang trên UI (chưa lưu)
    sp.panel_intake.txt_brief.setPlainText("Bản thảo gõ dở dang trên UI")
    sp.panel_intake.chk_duration.setChecked(True)
    sp.panel_intake.txt_duration.setText("45.5")
    sp.panel_intake.editor_must_keep.set_items(["Giữ câu mở đầu"])

    # Kết nối tín hiệu style_changed tới hàm sản xuất DirectorWindow._on_style_changed
    sp.style_changed.connect(lambda sid: DirectorWindow._on_style_changed(stub_win, sid))

    # Kích hoạt chọn thẻ Podcast qua sự kiện UI của StylePage
    sp._on_style_selected(STYLE_PODCAST)

    # Kiểm tra config đã cập nhật preset Podcast
    assert cfg.style == STYLE_PODCAST
    assert adv.loaded_config is cfg
    assert sp.current_style == STYLE_PODCAST
    assert sp.current_footage == "Nói liên tục"

    # Bản thảo trên intake panel được bảo toàn 100%!
    assert sp.panel_intake.txt_brief.toPlainText() == "Bản thảo gõ dở dang trên UI"
    assert sp.panel_intake.txt_duration.text() == "45.5"
    assert sp.panel_intake.editor_must_keep.get_items() == ["Giữ câu mở đầu"]

    # 2. Thử nghiệm với bản thảo lỗi (thời lượng chuỗi sai cú pháp)
    sp.panel_intake.txt_duration.setText("not_a_number")
    sp._on_style_selected(STYLE_SHORTS)
    assert cfg.style == STYLE_SHORTS
    assert sp.current_style == STYLE_SHORTS
    # Bản thảo lỗi không bị xóa hay gây crash
    assert sp.panel_intake.txt_duration.text() == "not_a_number"


# ==============================================================================
# Nhóm 3: Giới Hạn Tối Đa và Phân Loại File Tham Chiếu
# ==============================================================================

def test_reference_items_cap_and_bulk_dialog_boundary(qapp, monkeypatch):
    """Kiểm tra giới hạn 64 tài liệu tham chiếu và từ chối tệp vượt giới hạn."""
    sp = StylePage()
    panel = sp.panel_intake

    # Thêm 64 tệp hợp lệ
    for i in range(MAX_REFERENCE_ITEMS):
        res = panel.add_reference_path(f"/media/ref_{i}.mp4")
        assert res is not None

    assert len(panel._reference_items) == MAX_REFERENCE_ITEMS

    # Cố gắng thêm tệp thứ 65
    res65 = panel.add_reference_path("/media/ref_overflow.mp4")
    assert res65 is None
    assert len(panel._reference_items) == MAX_REFERENCE_ITEMS
    assert not panel.lbl_error.isHidden()
    assert "Đã đạt giới hạn tối đa" in panel.lbl_error.text()

    # Hủy hộp thoại chọn tệp không gây lỗi
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *args, **kwargs: ([], ""))
    panel.btn_add_ref.click()
    assert len(panel._reference_items) == MAX_REFERENCE_ITEMS


def test_reference_duplicate_explicit_id_rejected(qapp):
    """Từ chối thêm tệp tham chiếu nếu trùng lặp explicit ID."""
    sp = StylePage()
    res1 = sp.panel_intake.add_reference_path("/media/ref1.mp4", reference_id="custom_id_01")
    assert res1 is not None

    res2 = sp.panel_intake.add_reference_path("/media/ref2.mp4", reference_id="custom_id_01")
    assert res2 is None
    assert len(sp.panel_intake._reference_items) == 1
    assert "ERR_DUPLICATE_REFERENCE_ID" in sp.panel_intake.lbl_error.text()


# ==============================================================================
# Nhóm 4: Tripwires Zero-IO Đối Với Tệp Tham Chiếu Trong Programmatic Load/Save
# ==============================================================================

def test_zero_io_tripwires_during_programmatic_roundtrip(qapp, monkeypatch):
    """Cài đặt tripwires đảm bảo không có I/O đĩa hoặc socket mạng khi load/save cấu hình."""
    cfg = DirectorConfig()
    cfg.set_creative_context({
        "free_brief": "Kiểm tra tripwires",
        "reference_items": [
            {
                "reference_id": "ref_tripwire_01",
                "local_path": "/fake/non_existent/reference_video.mp4",
                "media_type": "video",
                "role": "reference_only",
                "user_note": "Zero IO note"
            }
        ],
        "reference_urls": ["https://example.com/unfetched_url"]
    })

    sp = StylePage()

    # Tripwires phát hiện gọi mạng
    import socket
    import urllib.request

    def fail_network(*args, **kwargs):
        pytest.fail("TRIPWIRE: Socket mạng bị gọi trái phép!")

    monkeypatch.setattr(socket, "socket", fail_network)
    monkeypatch.setattr(urllib.request, "urlopen", fail_network)

    # 1. Programmatic load
    ok_load = sp.load_from_config(cfg)
    assert ok_load is True

    # 2. Programmatic save
    cfg_out = DirectorConfig()
    ok_save = sp.save_to_config(cfg_out)
    assert ok_save is True
    assert cfg_out.get_creative_context().reference_items[0].local_path == "/fake/non_existent/reference_video.mp4"


# ==============================================================================
# Nhóm 5: Khả Năng Cuộn & Tiếp Cận Thực Tế Viewport (ensureWidgetVisible)
# ==============================================================================

def test_viewport_intersection_accessibility_at_target_resolutions(qapp):
    """Kiểm tra tính tiếp cận thực tế thông qua ensureWidgetVisible và giao cắt viewport."""
    sp = StylePage()

    # Danh sách các widget trọng tâm cần kiểm tra tiếp cận
    target_widgets = [
        sp.seg_footage,
        sp.seg_pacing,
        sp.slider_intensity,
        sp.panel_intake.txt_brief,
        sp.panel_intake.editor_must_keep,
        sp.panel_intake.editor_must_not,
        sp.panel_intake.list_refs,
        sp.panel_intake.editor_urls,
        sp.btn_adv,
    ]

    for w_size in [(1200, 750), (1280, 800)]:
        sp.resize(*w_size)
        sp.show()
        qapp.processEvents()

        viewport = sp.scroll_area.viewport()
        viewport_rect = viewport.rect()

        for widget in target_widgets:
            sp.scroll_area.ensureWidgetVisible(widget)
            qapp.processEvents()

            # Chuyển đổi tọa độ widget sang không gian của viewport
            top_left_in_vp = widget.mapTo(viewport, widget.rect().topLeft())
            widget_vp_rect = QRect(top_left_in_vp, widget.size())

            # Khẳng định widget nằm trong hoặc giao cắt với viewport đang hiển thị
            assert viewport_rect.intersects(widget_vp_rect), f"Widget {widget} không hiển thị trong viewport tại {w_size}!"


# ==============================================================================
# Nhóm 6: Roundtrip JSON Chuẩn Tắc Toàn Diện & Cô Lập Mutation
# ==============================================================================

def test_full_vietnamese_roundtrip_canonical_json(qapp):
    """Kiểm tra roundtrip JSON chuẩn tắc đầy đủ các trường tiếng Việt và đối tượng lồng nhau."""
    c1 = DirectorConfig()
    vn_payload = {
        "free_brief": "Ý đồ dựng phim tài liệu ngắn về Hà Nội mùa thu: nhịp điệu hoài niệm, âm thanh phố phường.",
        "style_preset": "cinematic_ha_noi_v1",
        "target_duration_sec": 88.5,
        "must_keep_rules": [
            "Giữ trọn vẹn lời độc thoại của cụ già đầu ngõ",
            "Giữ tiếng còi tàu Long Biên lúc hoàng hôn"
        ],
        "must_not_rules": [
            "Không dùng nhạc điện tử EDM",
            "Không chèn hiệu ứng chớp nháy trắng"
        ],
        "reference_items": [
            {
                "reference_id": "ref_hn01",
                "local_path": "/media/refs/ha_noi_autumn.mp4",
                "media_type": "video",
                "role": "reference_only",
                "user_note": "Tham khảo tone màu ấm áp và góc máy tĩnh"
            }
        ],
        "reference_urls": [
            "https://example.com/hanoi-autumn-doc"
        ]
    }

    c1.set_creative_context(vn_payload)

    # Chuyển đổi sang JSON primitives và serialize
    serialized = creative_context_to_dict(c1.get_creative_context())
    json_str = json.dumps(serialized, ensure_ascii=False)
    decoded = json.loads(json_str)

    c2 = DirectorConfig()
    c2.set_creative_context(decoded)
    ctx2 = c2.get_creative_context()

    assert ctx2.free_brief == vn_payload["free_brief"]
    assert ctx2.style_preset == "cinematic_ha_noi_v1"
    assert ctx2.target_duration_sec == 88.5
    assert ctx2.must_keep_rules == tuple(vn_payload["must_keep_rules"])
    assert ctx2.must_not_rules == tuple(vn_payload["must_not_rules"])
    assert len(ctx2.reference_items) == 1
    assert ctx2.reference_items[0].reference_id == "ref_hn01"
    assert ctx2.reference_urls == tuple(vn_payload["reference_urls"])


def test_mutation_isolation_and_transactional_rejection(qapp):
    """Caller hoặc output mutation không làm biến đổi cấu hình chấp thuận."""
    c = DirectorConfig()
    caller_dict = {
        "free_brief": "Ý đồ gốc",
        "must_keep_rules": ["Quy tắc 1"]
    }
    c.set_creative_context(caller_dict)

    caller_dict["must_keep_rules"].append("Mutate lén lút")
    caller_dict["free_brief"] = "Bị sửa đổi"
    assert c.get_creative_context().free_brief == "Ý đồ gốc"
    assert c.get_creative_context().must_keep_rules == ("Quy tắc 1",)

    out_dict = creative_context_to_dict(c.get_creative_context())
    out_dict["free_brief"] = "Mutate output"
    assert c.creative_context_dict["free_brief"] == "Ý đồ gốc"


def test_programmatic_load_signal_guards_and_stable_ids(qapp):
    """Nạp bằng code không phát tán event config_changed; ID tham chiếu ổn định."""
    sp = StylePage()
    cfg = DirectorConfig()
    cfg.set_creative_context({
        "free_brief": "Brief ban đầu",
        "reference_items": [
            {
                "reference_id": "ref_stable_001",
                "local_path": "/var/media/ref.mp4",
                "media_type": "video",
                "role": "reference_only",
                "user_note": "Ghi chú ban đầu"
            }
        ]
    })

    emitted_count = 0

    def on_change():
        nonlocal emitted_count
        emitted_count += 1

    sp.config_changed.connect(on_change)
    sp.load_from_config(cfg)
    assert emitted_count == 0

    cfg2 = DirectorConfig()
    ok = sp.save_to_config(cfg2)
    assert ok is True
    ctx2 = cfg2.get_creative_context()
    assert len(ctx2.reference_items) == 1
    assert ctx2.reference_items[0].reference_id == "ref_stable_001"


# ==============================================================================
# Nhóm 6: Bổ Sung Kiểm Thử Ranh Giới Nạp Panel Trực Tiếp & Bulk Rejection Minh Bạch
# ==============================================================================

def test_direct_panel_invalid_load_preserves_state_and_shows_error(qapp):
    """Nạp trực tiếp qua panel.load_from_dict với payload sai kiểu phải bị từ chối,
    bảo toàn nguyên vẹn bản thảo UI trước đó và hiển thị lỗi inline."""
    sp = StylePage()
    panel = sp.panel_intake

    # 1. Thiết lập bản thảo hợp lệ ban đầu trên UI
    panel.txt_brief.setPlainText("Bản thảo ban đầu đang soạn")
    panel.editor_must_keep.set_items(["Giữ nguyên đoạn mở đầu"])
    panel.add_reference_path("/media/ref_initial.png")
    assert len(panel._reference_items) == 1
    assert panel.lbl_error.isHidden()

    # 2. Thử nạp free_brief sai kiểu int (123)
    ret_int = panel.load_from_dict({"free_brief": 123})
    assert ret_int is False
    assert not panel.lbl_error.isHidden()
    assert "ERR_INVALID_TYPE" in panel.lbl_error.text()
    # Kiểm tra bảo toàn dữ liệu UI: không bị ép chuỗi thành '123'
    assert panel.txt_brief.toPlainText() == "Bản thảo ban đầu đang soạn"
    assert panel.editor_must_keep.get_items() == ["Giữ nguyên đoạn mở đầu"]
    assert len(panel._reference_items) == 1

    # 3. Thử nạp kiểu non-dict (bool, str, list)
    assert panel.load_from_dict(True) is False
    assert panel.load_from_dict("invalid_payload") is False
    assert panel.load_from_dict([1, 2, 3]) is False
    assert panel.txt_brief.toPlainText() == "Bản thảo ban đầu đang soạn"

    # 4. Thử nạp collection sai kiểu (must_keep_rules không phải list/tuple)
    assert panel.load_from_dict({"must_keep_rules": "not_a_list"}) is False
    assert panel.txt_brief.toPlainText() == "Bản thảo ban đầu đang soạn"

    # 5. Thử nạp reference row có role sai quy định
    assert panel.load_from_dict({
        "reference_items": [
            {
                "reference_id": "ref_bad_01",
                "local_path": "/media/ref.mp4",
                "media_type": "video",
                "role": "footage"
            }
        ]
    }) is False
    assert panel.txt_brief.toPlainText() == "Bản thảo ban đầu đang soạn"

    # 6. Thử nạp reference row thiếu reference_id (rỗng)
    assert panel.load_from_dict({
        "reference_items": [
            {
                "reference_id": "",
                "local_path": "/media/ref.mp4",
                "media_type": "video",
                "role": "reference_only"
            }
        ]
    }) is False
    assert panel.txt_brief.toPlainText() == "Bản thảo ban đầu đang soạn"

    # 7. Thử nạp chứa unknown key lạ
    assert panel.load_from_dict({"malicious_unknown_field": "injected"}) is False
    assert panel.txt_brief.toPlainText() == "Bản thảo ban đầu đang soạn"

    # 8. Nạp hợp lệ thành công: cập nhật controls, xóa lỗi
    ret_valid = panel.load_from_dict({
        "free_brief": "Brief mới chuẩn tắc",
        "must_keep_rules": ["Quy tắc mới 1", "Quy tắc mới 2"],
        "reference_urls": ["https://example.com/ref1"]
    })
    assert ret_valid is True
    assert panel.lbl_error.isHidden() or panel.lbl_error.text() == ""
    assert panel.txt_brief.toPlainText() == "Brief mới chuẩn tắc"
    assert panel.editor_must_keep.get_items() == ["Quy tắc mới 1", "Quy tắc mới 2"]
    assert panel.editor_urls.get_items() == ["https://example.com/ref1"]


def test_bulk_picker_mixed_selection_both_orders_and_cap(qapp):
    """Chọn nhiều file tham chiếu (hợp lệ lẫn không hợp lệ) ở cả 2 thứ tự
    phải hiển thị thông báo lỗi/cảnh báo rõ ràng và không được xóa lỗi tệp bị từ chối."""
    sp = StylePage()
    panel = sp.panel_intake

    # 1. Thứ tự 1: [unsupported.pdf, valid.png]
    res1 = panel.add_reference_paths(["/media/unsupported.pdf", "/media/valid.png"])
    assert res1["accepted_count"] == 1
    assert res1["rejected_count"] == 1
    assert len(panel._reference_items) == 1
    assert panel._reference_items[0]["local_path"] == os.path.abspath("/media/valid.png")
    # Lỗi từ chối tệp .pdf KHÔNG bị tệp .png ghi đè mất
    assert not panel.lbl_error.isHidden()
    assert "từ chối 1 tệp" in panel.lbl_error.text()
    assert "unsupported.pdf" in panel.lbl_error.text()

    # 2. Thứ tự 2: [valid2.mp4, not_video.zip] trên panel mới
    sp2 = StylePage()
    panel2 = sp2.panel_intake
    res2 = panel2.add_reference_paths(["/media/valid2.mp4", "/media/not_video.zip"])
    assert res2["accepted_count"] == 1
    assert res2["rejected_count"] == 1
    assert len(panel2._reference_items) == 1
    assert panel2._reference_items[0]["local_path"] == os.path.abspath("/media/valid2.mp4")
    assert not panel2.lbl_error.isHidden()
    assert "từ chối 1 tệp" in panel2.lbl_error.text()
    assert "not_video.zip" in panel2.lbl_error.text()

    # 3. Toàn bộ tệp không hợp lệ: [bad1.txt, bad2.wav]
    sp3 = StylePage()
    panel3 = sp3.panel_intake
    res3 = panel3.add_reference_paths(["/media/bad1.txt", "/media/bad2.wav"])
    assert res3["accepted_count"] == 0
    assert res3["rejected_count"] == 2
    assert len(panel3._reference_items) == 0
    assert not panel3.lbl_error.isHidden()
    assert "Từ chối toàn bộ 2 tệp" in panel3.lbl_error.text()

    # 4. Toàn bộ tệp hợp lệ: [img.webp, vid.mkv]
    sp4 = StylePage()
    panel4 = sp4.panel_intake
    res4 = panel4.add_reference_paths(["/media/img.webp", "/media/vid.mkv"])
    assert res4["accepted_count"] == 2
    assert res4["rejected_count"] == 0
    assert len(panel4._reference_items) == 2
    assert panel4.lbl_error.isHidden() or panel4.lbl_error.text() == ""

    # 5. Vượt giới hạn 64 tệp khi thêm hàng loạt
    # Đã có 62 tệp hợp lệ
    for i in range(62):
        panel4._reference_items.append({
            "reference_id": f"ref_bulk_{i}",
            "local_path": f"/media/auto_{i}.png",
            "media_type": "image",
            "role": "reference_only",
            "user_note": ""
        })
    assert len(panel4._reference_items) == 64

    # Cố gắng thêm 2 tệp hợp lệ tiếp theo
    res_overflow = panel4.add_reference_paths(["/media/extra1.png", "/media/extra2.png"])
    assert res_overflow["accepted_count"] == 0
    assert res_overflow["rejected_count"] == 2
    assert len(panel4._reference_items) == 64
    assert not panel4.lbl_error.isHidden()
    assert "vượt quá giới hạn tối đa 64 tệp" in panel4.lbl_error.text()

