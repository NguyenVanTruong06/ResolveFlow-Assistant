"""tests/core/geometry/test_source_frame_evidence_parser.py
Bộ kiểm thử TDD chuẩn tắc cho VideoStreamHeader và parse_ffprobe_video_header (G06-06C2b-I).
"""
import ast
from collections.abc import Mapping
from fractions import Fraction
import inspect
import os
import socket
import time
from types import MappingProxyType
from typing import Any

import pytest

from src.core.contracts.time_contracts import RationalFPS
from src.core.geometry import (
    source_frame_evidence_contracts as contracts_mod,
    source_frame_evidence_parser as parser_mod,
)
from src.core.geometry.source_frame_evidence_contracts import (
    MAX_METADATA_STREAMS,
    MAX_METADATA_TEXT_BYTES,
    VideoStreamHeader,
)
from src.core.geometry.source_frame_evidence_parser import parse_ffprobe_video_header


# ==============================================================================
# 1. POSITIVE STANDARD HEADER TESTS
# ==============================================================================

def test_parse_standard_h264_header():
    """Kiểm tra parse header video H.264 tiêu chuẩn (30/1, 900 frames, 1/90000 time_base)."""
    payload = {
        "streams": [
            {
                "index": 0,
                "codec_name": "h264",
                "codec_type": "video",
                "r_frame_rate": "30/1",
                "avg_frame_rate": "30/1",
                "nb_frames": "900",
                "time_base": "1/90000",
            }
        ]
    }
    header = parse_ffprobe_video_header(payload)
    assert header.stream_index == 0
    assert header.codec_name == "h264"
    assert header.r_frame_rate == RationalFPS(30, 1)
    assert header.avg_frame_rate == RationalFPS(30, 1)
    assert header.reported_nb_frames == 900
    assert header.time_base == Fraction(1, 90000)


def test_parse_ntsc_fractional_rate_exact():
    """Kiểm tra tỷ lệ khung hình NTSC 30000/1001 và 24000/1001 được lưu chuẩn xác phân số hữu tỷ."""
    payload = {
        "streams": [
            {
                "index": 1,
                "codec_name": "hevc",
                "codec_type": "video",
                "r_frame_rate": "30000/1001",
                "avg_frame_rate": "24000/1001",
                "nb_frames": 1798,
                "time_base": "1/1000",
            }
        ]
    }
    header = parse_ffprobe_video_header(payload)
    assert header.stream_index == 1
    assert header.codec_name == "hevc"
    assert header.r_frame_rate == RationalFPS(30000, 1001)
    assert header.avg_frame_rate == RationalFPS(24000, 1001)
    assert header.reported_nb_frames == 1798
    assert header.time_base == Fraction(1, 1000)


def test_sentinels_mapped_to_none():
    """Kiểm tra các giá trị sentinel (N/A, 0/0, 0, missing) đều được ánh xạ về None."""
    payload = {
        "streams": [
            {
                "index": 0,
                "codec_type": "video",
                "codec_name": None,
                "r_frame_rate": "N/A",
                "avg_frame_rate": "0/0",
                "nb_frames": "0",
                "time_base": "0/1",
            }
        ]
    }
    header = parse_ffprobe_video_header(payload)
    assert header.stream_index == 0
    assert header.codec_name is None
    assert header.r_frame_rate is None
    assert header.avg_frame_rate is None
    assert header.reported_nb_frames is None
    assert header.time_base is None


def test_all_optional_fields_missing():
    """Kiểm tra video stream tối giản chỉ có index và codec_type hợp lệ."""
    payload = {
        "streams": [
            {
                "index": 0,
                "codec_type": "video",
            }
        ]
    }
    header = parse_ffprobe_video_header(payload)
    assert header.stream_index == 0
    assert header.codec_name is None
    assert header.r_frame_rate is None
    assert header.avg_frame_rate is None
    assert header.reported_nb_frames is None
    assert header.time_base is None


# ==============================================================================
# 2. RATES PRESERVED WITHOUT CFR/VFR INFERENCE & PACKETS IGNORED
# ==============================================================================

def test_equal_and_unequal_rates_preserved_without_cfr_inference():
    """Kiểm tra cả r_fps == avg_fps và r_fps != avg_fps đều được bảo tồn; không tự suy ra CFR/VFR."""
    # 1. Bằng nhau
    payload_equal = {
        "streams": [
            {"index": 0, "codec_type": "video", "r_frame_rate": "30/1", "avg_frame_rate": "30/1"}
        ]
    }
    h_eq = parse_ffprobe_video_header(payload_equal)
    assert h_eq.r_frame_rate == RationalFPS(30, 1)
    assert h_eq.avg_frame_rate == RationalFPS(30, 1)
    assert not hasattr(h_eq, "is_cfr"), "VideoStreamHeader không được chứa trường is_cfr"
    assert not hasattr(h_eq, "cfr_confidence_level"), "Không được chứa trường cfr_confidence_level"

    # 2. Khác nhau
    payload_diff = {
        "streams": [
            {"index": 0, "codec_type": "video", "r_frame_rate": "30/1", "avg_frame_rate": "28/1"}
        ]
    }
    h_diff = parse_ffprobe_video_header(payload_diff)
    assert h_diff.r_frame_rate == RationalFPS(30, 1)
    assert h_diff.avg_frame_rate == RationalFPS(28, 1)


def test_packet_arrays_and_unrelated_fields_ignored():
    """Kiểm tra các trường packets, duration, nb_read_packets không bao giờ được dùng thay cho reported_nb_frames."""
    payload = {
        "format": {"duration": "30.0", "unrelated": [1, 2, 3]},
        "streams": [
            {
                "index": 0,
                "codec_type": "video",
                "packets": [{"pts": 0}, {"pts": 3000}],
                "nb_read_packets": "500",
                "duration": "10.0",
                "nb_frames": "300",
            }
        ],
    }
    header = parse_ffprobe_video_header(payload)
    assert header.reported_nb_frames == 300


# ==============================================================================
# 3. STREAM FILTERING & SELECTION (AUDIO, COVER ART, MJPEG, SELECTOR)
# ==============================================================================

def test_multi_stream_filtering_and_lowest_index_default():
    """Kiểm tra payload đa stream: audio (0), cover art (1), video thật (2), video phụ (3)."""
    payload = {
        "streams": [
            {"index": 0, "codec_type": "audio", "codec_name": "aac"},
            {"index": 1, "codec_type": "video", "codec_name": "mjpeg", "disposition": {"attached_pic": 1}},
            {"index": 2, "codec_type": "video", "codec_name": "h264", "disposition": {"attached_pic": 0}},
            {"index": 3, "codec_type": "video", "codec_name": "prores"},
        ]
    }
    # Mặc định chọn video hợp lệ có index nhỏ nhất (stream 2)
    header = parse_ffprobe_video_header(payload)
    assert header.stream_index == 2
    assert header.codec_name == "h264"


def test_mjpeg_real_video_eligible():
    """Kiểm tra video MJPEG thật (không có attached_pic=1) vẫn là video stream hợp lệ."""
    payload = {
        "streams": [
            {"index": 0, "codec_type": "video", "codec_name": "mjpeg", "disposition": {"attached_pic": 0}}
        ]
    }
    header = parse_ffprobe_video_header(payload)
    assert header.stream_index == 0
    assert header.codec_name == "mjpeg"


def test_explicit_stream_selection():
    """Kiểm tra chỉ định stream rõ ràng qua selected_stream_index."""
    payload = {
        "streams": [
            {"index": 0, "codec_type": "video", "codec_name": "h264"},
            {"index": 2, "codec_type": "video", "codec_name": "prores"},
        ]
    }
    header = parse_ffprobe_video_header(payload, selected_stream_index=2)
    assert header.stream_index == 2
    assert header.codec_name == "prores"


@pytest.mark.parametrize("bad_selector", [True, False, -1, 1.5, "0", None.__class__])
def test_invalid_stream_selector_argument(bad_selector):
    """Kiểm tra tham số selected_stream_index sai kiểu hoặc số âm ném ERR_INVALID_STREAM_SELECTION."""
    payload = {"streams": [{"index": 0, "codec_type": "video"}]}
    with pytest.raises(ValueError, match="ERR_INVALID_STREAM_SELECTION"):
        parse_ffprobe_video_header(payload, selected_stream_index=bad_selector)  # type: ignore


def test_explicit_selection_audio_or_attached_pic_fails_no_video():
    """Kiểm tra chỉ định index của audio stream hoặc cover art ném ERR_NO_VIDEO_STREAM (không fallback)."""
    payload = {
        "streams": [
            {"index": 0, "codec_type": "audio"},
            {"index": 1, "codec_type": "video", "disposition": {"attached_pic": 1}},
            {"index": 2, "codec_type": "video"},
        ]
    }
    # Chỉ định stream audio
    with pytest.raises(ValueError, match="ERR_NO_VIDEO_STREAM"):
        parse_ffprobe_video_header(payload, selected_stream_index=0)

    # Chỉ định stream cover art
    with pytest.raises(ValueError, match="ERR_NO_VIDEO_STREAM"):
        parse_ffprobe_video_header(payload, selected_stream_index=1)

    # Chỉ định stream không tồn tại
    with pytest.raises(ValueError, match="ERR_NO_VIDEO_STREAM"):
        parse_ffprobe_video_header(payload, selected_stream_index=99)


def test_no_eligible_video_in_streams():
    """Kiểm tra payload chỉ có audio hoặc chỉ có attached pictures ném ERR_NO_VIDEO_STREAM."""
    payload = {
        "streams": [
            {"index": 0, "codec_type": "audio"},
            {"index": 1, "codec_type": "video", "disposition": {"attached_pic": 1}},
        ]
    }
    with pytest.raises(ValueError, match="ERR_NO_VIDEO_STREAM"):
        parse_ffprobe_video_header(payload)


# ==============================================================================
# 4. STRUCTURAL VALIDATION & METADATA BOUNDS (STREAMS LIMIT & MALFORMED ENTRIES)
# ==============================================================================

def test_payload_not_mapping_or_streams_not_list():
    """Kiểm tra payload không phải Mapping hoặc streams không phải list/tuple."""
    for bad_payload in (None, [1, 2], "not_a_map", 123):
        with pytest.raises(ValueError, match="ERR_INVALID_PROBE_PAYLOAD"):
            parse_ffprobe_video_header(bad_payload)  # type: ignore

    for bad_streams in (None, "streams_str", 123, {"a": 1}):
        with pytest.raises(ValueError, match="ERR_INVALID_PROBE_PAYLOAD"):
            parse_ffprobe_video_header({"streams": bad_streams})  # type: ignore


def test_streams_empty_list():
    """Kiểm tra danh sách streams rỗng ném ERR_NO_VIDEO_STREAM."""
    with pytest.raises(ValueError, match="ERR_NO_VIDEO_STREAM"):
        parse_ffprobe_video_header({"streams": []})


def test_streams_limit_overflow_rejected_before_iteration():
    """Kiểm tra danh sách streams > 256 phần tử bị chặn với ERR_PROBE_METADATA_LIMIT ngay lập tức."""
    class GuardedList(list):
        def __iter__(self):
            raise AssertionError("Không được duyệt qua danh sách khi đã vượt quá 256 phần tử")

    oversized = GuardedList([{"index": i, "codec_type": "video"} for i in range(MAX_METADATA_STREAMS + 1)])
    with pytest.raises(ValueError, match="ERR_PROBE_METADATA_LIMIT"):
        parse_ffprobe_video_header({"streams": oversized})


def test_stream_entries_structural_validation():
    """Kiểm tra cấu trúc stream entry: không phải Mapping, thiếu index, trùng index, index âm/bool."""
    # 1. Entry không phải mapping
    with pytest.raises(ValueError, match="ERR_INVALID_VIDEO_METADATA"):
        parse_ffprobe_video_header({"streams": ["not_a_mapping"]})

    # 2. Index thiếu hoặc sai kiểu
    for bad_idx in (None, -1, True, False, 1.0, "0"):
        with pytest.raises(ValueError, match="ERR_INVALID_VIDEO_METADATA"):
            parse_ffprobe_video_header({"streams": [{"index": bad_idx, "codec_type": "video"}]})

    # 3. Trùng lặp index
    with pytest.raises(ValueError, match="ERR_INVALID_VIDEO_METADATA"):
        parse_ffprobe_video_header({
            "streams": [
                {"index": 0, "codec_type": "video"},
                {"index": 0, "codec_type": "audio"},
            ]
        })

    # 4. Codec_type sai kiểu hoặc rỗng/whitespace
    for bad_ct in (None, 123, "", "   ", "video\x00extra"):
        with pytest.raises(ValueError, match="ERR_INVALID_VIDEO_METADATA"):
            parse_ffprobe_video_header({"streams": [{"index": 0, "codec_type": bad_ct}]})

    # 5. Disposition sai kiểu hoặc attached_pic không phải 0/1
    for bad_disp in ("not_a_map", 123):
        with pytest.raises(ValueError, match="ERR_INVALID_VIDEO_METADATA"):
            parse_ffprobe_video_header({"streams": [{"index": 0, "codec_type": "video", "disposition": bad_disp}]})

    for bad_att in (True, False, 2, -1, "1"):
        with pytest.raises(ValueError, match="ERR_INVALID_VIDEO_METADATA"):
            parse_ffprobe_video_header({
                "streams": [
                    {"index": 0, "codec_type": "video", "disposition": {"attached_pic": bad_att}}
                ]
            })


def test_unselected_stream_structural_errors_rejected_but_rate_errors_ignored():
    """Kiểm tra lỗi cấu trúc stream chưa chọn vẫn bị reject; nhưng lỗi rate/count của stream chưa chọn thì được bỏ qua."""
    # Lỗi cấu trúc ở stream 1 (trùng index) -> Bị reject
    with pytest.raises(ValueError, match="ERR_INVALID_VIDEO_METADATA"):
        parse_ffprobe_video_header({
            "streams": [
                {"index": 0, "codec_type": "video"},
                {"index": 0, "codec_type": "video"},
            ]
        })

    # Lỗi rate ở stream 1 (audio) -> Bị bỏ qua vì stream được chọn là stream 0
    header = parse_ffprobe_video_header({
        "streams": [
            {"index": 0, "codec_type": "video", "r_frame_rate": "30/1"},
            {"index": 1, "codec_type": "audio", "r_frame_rate": "30/0"},  # malformed rate trên audio
        ]
    })
    assert header.stream_index == 0
    assert header.r_frame_rate == RationalFPS(30, 1)


# ==============================================================================
# 5. METADATA FIELD VALIDATION (RATES, TIME_BASE, NB_FRAMES, CODEC_NAME)
# ==============================================================================

@pytest.mark.parametrize(
    "bad_rate",
    [
        "30/0",         # Mẫu số 0 với tử số dương
        "1/0",
        "29.97",        # Số thực không được chấp nhận
        "30",           # Thiếu mẫu số
        "+30/1",        # Ký tự dấu
        "-30/1",
        "30/1 ",        # Khoảng trắng
        " 30/1",
        "３０/１",      # Chữ số Unicode full-width
        "30/1/2",       # Quá nhiều dấu slash
        "",             # Chuỗi rỗng
        True,           # bool
        123,            # int
        ["30/1"],       # list
    ],
)
def test_malformed_r_frame_rate_rejected(bad_rate):
    """Kiểm tra r_frame_rate sai định dạng ném ERR_INVALID_VIDEO_METADATA."""
    payload = {"streams": [{"index": 0, "codec_type": "video", "r_frame_rate": bad_rate}]}
    with pytest.raises(ValueError, match="ERR_INVALID_VIDEO_METADATA"):
        parse_ffprobe_video_header(payload)


@pytest.mark.parametrize(
    "bad_rate",
    [
        "30/0", "24.0", "-24/1", "24/1 ", True, 123
    ],
)
def test_malformed_avg_frame_rate_rejected(bad_rate):
    """Kiểm tra avg_frame_rate sai định dạng ném ERR_INVALID_VIDEO_METADATA."""
    payload = {"streams": [{"index": 0, "codec_type": "video", "avg_frame_rate": bad_rate}]}
    with pytest.raises(ValueError, match="ERR_INVALID_VIDEO_METADATA"):
        parse_ffprobe_video_header(payload)


@pytest.mark.parametrize(
    "bad_tb",
    [
        "1/0", "90000/0", "0.001", "-1/1000", "+1/1000", True, 1000
    ],
)
def test_malformed_time_base_rejected(bad_tb):
    """Kiểm tra time_base sai định dạng ném ERR_INVALID_VIDEO_METADATA."""
    payload = {"streams": [{"index": 0, "codec_type": "video", "time_base": bad_tb}]}
    with pytest.raises(ValueError, match="ERR_INVALID_VIDEO_METADATA"):
        parse_ffprobe_video_header(payload)


@pytest.mark.parametrize(
    "bad_nb",
    [
        -1, -100, True, False, 100.5, "-100", "+100", "0123", " 100 ", "abc", [100]
    ],
)
def test_malformed_nb_frames_rejected(bad_nb):
    """Kiểm tra reported_nb_frames sai kiểu hoặc định dạng chuỗi ném ERR_INVALID_VIDEO_METADATA."""
    payload = {"streams": [{"index": 0, "codec_type": "video", "nb_frames": bad_nb}]}
    with pytest.raises(ValueError, match="ERR_INVALID_VIDEO_METADATA"):
        parse_ffprobe_video_header(payload)


@pytest.mark.parametrize(
    "bad_codec",
    [
        123, True, "", "   ", "h264\x00extra", [1]
    ],
)
def test_malformed_codec_name_rejected(bad_codec):
    """Kiểm tra codec_name sai kiểu, khoảng trắng, hoặc chứa NUL ném ERR_INVALID_VIDEO_METADATA."""
    payload = {"streams": [{"index": 0, "codec_type": "video", "codec_name": bad_codec}]}
    with pytest.raises(ValueError, match="ERR_INVALID_VIDEO_METADATA"):
        parse_ffprobe_video_header(payload)


def test_text_length_limit_exact_accepted_and_overflow_rejected():
    """Kiểm tra giới hạn ký tự MAX_METADATA_TEXT_BYTES (1024 bytes)."""
    # 1024 ký tự ASCII hợp lệ
    exact_text = "a" * MAX_METADATA_TEXT_BYTES
    payload_valid = {"streams": [{"index": 0, "codec_type": "video", "codec_name": exact_text}]}
    header = parse_ffprobe_video_header(payload_valid)
    assert header.codec_name == exact_text

    # 1025 ký tự -> Vượt trần
    overflow_text = "a" * (MAX_METADATA_TEXT_BYTES + 1)
    payload_overflow = {"streams": [{"index": 0, "codec_type": "video", "codec_name": overflow_text}]}
    with pytest.raises(ValueError, match="ERR_PROBE_METADATA_LIMIT"):
        parse_ffprobe_video_header(payload_overflow)


# ==============================================================================
# 6. DIRECT DTO CONSTRUCTOR VALIDATION & MUTATION ISOLATION
# ==============================================================================

def test_direct_dto_constructor_validation():
    """Kiểm tra constructor trực tiếp của VideoStreamHeader tự xác thực đầy đủ cả 6 trường."""
    # 1. Hợp lệ hoàn chỉnh
    dto = VideoStreamHeader(
        stream_index=0,
        codec_name="prores",
        r_frame_rate=RationalFPS(30, 1),
        avg_frame_rate=RationalFPS(30, 1),
        reported_nb_frames=900,
        time_base=Fraction(1, 1000),
    )
    assert dto.stream_index == 0

    # 2. stream_index âm hoặc bool hoặc sai kiểu
    with pytest.raises(ValueError, match="ERR_INVALID_VIDEO_METADATA"):
        VideoStreamHeader("0", None, None, None, None, None)  # type: ignore

    with pytest.raises(ValueError, match="ERR_INVALID_VIDEO_METADATA"):
        VideoStreamHeader(-1, None, None, None, None, None)

    with pytest.raises(ValueError, match="ERR_INVALID_VIDEO_METADATA"):
        VideoStreamHeader(True, None, None, None, None, None)  # type: ignore

    # 3. reported_nb_frames <= 0 hoặc bool
    with pytest.raises(ValueError, match="ERR_INVALID_VIDEO_METADATA"):
        VideoStreamHeader(0, None, None, None, 0, None)

    with pytest.raises(ValueError, match="ERR_INVALID_VIDEO_METADATA"):
        VideoStreamHeader(0, None, None, None, True, None)  # type: ignore

    # 4. time_base denominator <= 0 hoặc numerator <= 0
    with pytest.raises(ValueError, match="ERR_INVALID_VIDEO_METADATA"):
        bad_tb = Fraction(1, 1)
        object.__setattr__(bad_tb, "_denominator", 0)
        VideoStreamHeader(0, None, None, None, None, bad_tb)


def test_caller_mutation_isolation():
    """Kiểm tra thay đổi payload ngoài hoặc đối tượng tham số sau khi parse không làm đổi VideoStreamHeader."""
    stream_dict = {
        "index": 0,
        "codec_type": "video",
        "codec_name": "h264",
        "r_frame_rate": "30/1",
        "nb_frames": 100,
    }
    payload = {"streams": [stream_dict]}
    header = parse_ffprobe_video_header(payload)

    # Thay đổi dict ngoài
    stream_dict["codec_name"] = "hevc"
    stream_dict["nb_frames"] = 999
    payload["streams"] = []

    # Header bên trong vẫn giữ nguyên
    assert header.codec_name == "h264"
    assert header.reported_nb_frames == 100


def test_mapping_proxy_type_payload_supported():
    """Kiểm tra hỗ trợ tải trọng Mapping bất biến dạng MappingProxyType."""
    payload = MappingProxyType({
        "streams": (
            MappingProxyType({
                "index": 0,
                "codec_type": "video",
                "codec_name": "prores",
            }),
        )
    })
    header = parse_ffprobe_video_header(payload)
    assert header.stream_index == 0
    assert header.codec_name == "prores"


def test_positive_control_huge_exact_integer_no_float_coercion():
    """Kiểm tra số nguyên cực lớn trong reported_nb_frames (không tràn số, không qua float)."""
    huge_int = 10**18 + 12345
    payload = {
        "streams": [
            {
                "index": 0,
                "codec_type": "video",
                "nb_frames": str(huge_int),
            }
        ]
    }
    header = parse_ffprobe_video_header(payload)
    assert header.reported_nb_frames == huge_int


# ==============================================================================
# 7. PURITY SPIES & AST STATIC INSPECTION (ZERO IO, ZERO CLOCK, ZERO QT)
# ==============================================================================

def test_purity_zero_filesystem_network_and_clock(monkeypatch):
    """Xác nhận parse_ffprobe_video_header là pure function: zero IO, zero socket, zero clock."""
    io_called = []
    net_called = []
    clock_called = []

    real_open = open
    real_stat = os.stat
    real_socket = socket.socket
    real_time = time.time

    monkeypatch.setattr("builtins.open", lambda *a, **kw: (io_called.append("open"), real_open(*a, **kw))[1])
    monkeypatch.setattr("os.stat", lambda *a, **kw: (io_called.append("stat"), real_stat(*a, **kw))[1])
    monkeypatch.setattr("socket.socket", lambda *a, **kw: (net_called.append("socket"), real_socket(*a, **kw))[1])
    monkeypatch.setattr("time.time", lambda: (clock_called.append("time"), real_time())[1])

    payload = {
        "streams": [
            {
                "index": 0,
                "codec_type": "video",
                "codec_name": "h264",
                "r_frame_rate": "30/1",
                "nb_frames": 100,
            }
        ]
    }
    header = parse_ffprobe_video_header(payload)
    assert header.reported_nb_frames == 100

    assert io_called == [], f"Phát hiện filesystem I/O: {io_called}"
    assert net_called == [], f"Phát hiện network call: {net_called}"
    assert clock_called == [], f"Phát hiện clock call: {clock_called}"


def test_source_ast_purity_no_forbidden_imports():
    """Kiểm tra tĩnh cây cú pháp AST: Cả hai module pure parser/contracts không import module cấm."""
    forbidden = {"os", "sys", "subprocess", "datetime", "time", "socket", "PySide6", "PySide2", "PyQt5", "PyQt6"}

    for mod in (contracts_mod, parser_mod):
        source = inspect.getsource(mod)
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    assert root not in forbidden, f"{mod.__name__} import cấm: {alias.name}"
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    root = node.module.split(".")[0]
                    assert root not in forbidden, f"{mod.__name__} import from cấm: {node.module}"


# ==============================================================================
# 8. G06-06C2b-R1 BOUNDARY & SAFE DIAGNOSTICS REGRESSION SUITE (13 CODEX PROBES)
# ==============================================================================

class HostileNoRepr:
    """Đối tượng đối kháng: ném RuntimeError nếu __repr__ hoặc __str__ bị gọi."""
    def __init__(self) -> None:
        self.repr_evaluated = False
        self.str_evaluated = False

    def __repr__(self) -> str:
        self.repr_evaluated = True
        raise RuntimeError("repr must not be evaluated")

    def __str__(self) -> str:
        self.str_evaluated = True
        raise RuntimeError("str must not be evaluated")


def test_probes_group1_key_presence_vs_explicit_null():
    """Nhóm 1: Kiểm tra phân biệt giữa vắng mặt trường (key missing) và null tường minh (explicit null)."""
    # Probe 1: video_disposition_null
    with pytest.raises(ValueError, match="^ERR_INVALID_VIDEO_METADATA: disposition phải là Mapping"):
        parse_ffprobe_video_header({"streams": [{"index": 0, "codec_type": "video", "disposition": None}]})

    # Probe 2: video_attached_pic_null
    with pytest.raises(ValueError, match="^ERR_INVALID_VIDEO_METADATA: disposition.attached_pic phải là int 0 hoặc 1"):
        parse_ffprobe_video_header({
            "streams": [
                {"index": 0, "codec_type": "video", "disposition": {"attached_pic": None}}
            ]
        })


@pytest.mark.parametrize(
    "bad_attached_val",
    [None, True, False, "0", "1", 1.0, 0.0, [1], {"val": 1}, -1, 2, 99],
)
def test_probes_group1_attached_pic_types_parametrized(bad_attached_val):
    """Nhóm 1: disposition.attached_pic bắt buộc là strict int 0 hoặc 1, từ chối mọi kiểu khác."""
    with pytest.raises(ValueError, match="^ERR_INVALID_VIDEO_METADATA: disposition.attached_pic phải là int 0 hoặc 1"):
        parse_ffprobe_video_header({
            "streams": [
                {"index": 0, "codec_type": "video", "disposition": {"attached_pic": bad_attached_val}}
            ]
        })


def test_probes_group1_valid_controls():
    """Nhóm 1: Điều khiển dương tính cho missing disposition, missing attached_pic, và attached_pic == 0."""
    # 1. Missing disposition -> mặc định không phải cover art, stream hợp lệ
    h1 = parse_ffprobe_video_header({"streams": [{"index": 0, "codec_type": "video"}]})
    assert h1.stream_index == 0

    # 2. Missing attached_pic trong disposition -> mặc định 0, stream hợp lệ
    h2 = parse_ffprobe_video_header({"streams": [{"index": 0, "codec_type": "video", "disposition": {}}]})
    assert h2.stream_index == 0

    # 3. Explicit attached_pic == 0 -> stream hợp lệ
    h3 = parse_ffprobe_video_header({
        "streams": [{"index": 0, "codec_type": "video", "disposition": {"attached_pic": 0}}]
    })
    assert h3.stream_index == 0

    # 4. Explicit attached_pic == 1 -> bị loại trừ khỏi danh sách hợp lệ; nếu không còn stream nào -> ERR_NO_VIDEO_STREAM
    with pytest.raises(ValueError, match="^ERR_NO_VIDEO_STREAM:"):
        parse_ffprobe_video_header({
            "streams": [{"index": 0, "codec_type": "video", "disposition": {"attached_pic": 1}}]
        })


def test_probes_group2_consumption_scope_non_video_disposition_ignored():
    """Nhóm 2: Siêu dữ liệu disposition trên stream không phải video (audio, subtitle) được bỏ qua hoàn toàn."""
    # Probe 3: ignored_audio_disposition_invalid
    h = parse_ffprobe_video_header({
        "streams": [
            {"index": 0, "codec_type": "video"},
            {"index": 1, "codec_type": "audio", "disposition": "str_not_a_map"},
        ]
    })
    assert h.stream_index == 0

    # Nhiều stream phi video với disposition sai kiểu hoặc null
    h_multi = parse_ffprobe_video_header({
        "streams": [
            {"index": 0, "codec_type": "audio", "disposition": None},
            {"index": 1, "codec_type": "video", "codec_name": "h264"},
            {"index": 2, "codec_type": "subtitle", "disposition": 12345},
            {"index": 3, "codec_type": "data", "disposition": {"attached_pic": "invalid"}},
        ]
    })
    assert h_multi.stream_index == 1
    assert h_multi.codec_name == "h264"


def test_probes_group2_non_video_structural_checks_still_enforced():
    """Nhóm 2: Lỗi cấu trúc cốt lõi (index/codec_type) trên stream phi video vẫn BẮT BUỘC bị từ chối."""
    # 1. Audio stream thiếu index
    with pytest.raises(ValueError, match="^ERR_INVALID_VIDEO_METADATA:"):
        parse_ffprobe_video_header({
            "streams": [
                {"index": 0, "codec_type": "video"},
                {"codec_type": "audio"},
            ]
        })

    # 2. Audio stream trùng index với video stream
    with pytest.raises(ValueError, match="^ERR_INVALID_VIDEO_METADATA: phát hiện trùng lặp stream index"):
        parse_ffprobe_video_header({
            "streams": [
                {"index": 0, "codec_type": "video"},
                {"index": 0, "codec_type": "audio"},
            ]
        })

    # 3. Audio stream có index âm
    with pytest.raises(ValueError, match="^ERR_INVALID_VIDEO_METADATA: stream index phải là int >= 0"):
        parse_ffprobe_video_header({
            "streams": [
                {"index": 0, "codec_type": "video"},
                {"index": -1, "codec_type": "audio"},
            ]
        })

    # 4. Audio stream có codec_type rỗng hoặc sai kiểu
    with pytest.raises(ValueError, match="^ERR_INVALID_VIDEO_METADATA: codec_type"):
        parse_ffprobe_video_header({
            "streams": [
                {"index": 0, "codec_type": "video"},
                {"index": 1, "codec_type": ""},
            ]
        })


def test_probes_group3_safe_diagnostics_hostile_no_repr():
    """Nhóm 3: Chẩn đoán an toàn: không bao giờ gọi __repr__ hoặc __str__ của đối tượng đầu vào không hợp lệ."""
    # Probe 9: invalid_index_no_repr
    hostile1 = HostileNoRepr()
    with pytest.raises(ValueError, match="^ERR_INVALID_VIDEO_METADATA: stream index phải là int >= 0"):
        parse_ffprobe_video_header({"streams": [{"index": hostile1, "codec_type": "video"}]})
    assert hostile1.repr_evaluated is False
    assert hostile1.str_evaluated is False

    # Selector không hợp lệ chứa HostileNoRepr
    hostile2 = HostileNoRepr()
    with pytest.raises(ValueError, match="^ERR_INVALID_STREAM_SELECTION: selected_stream_index phải là int >= 0"):
        parse_ffprobe_video_header(
            {"streams": [{"index": 0, "codec_type": "video"}]},
            selected_stream_index=hostile2,  # type: ignore
        )
    assert hostile2.repr_evaluated is False
    assert hostile2.str_evaluated is False

    # DTO constructor chứa HostileNoRepr
    hostile3 = HostileNoRepr()
    with pytest.raises(ValueError, match="^ERR_INVALID_VIDEO_METADATA: stream_index phải là int >= 0"):
        VideoStreamHeader(hostile3, None, None, None, None, None)  # type: ignore
    assert hostile3.repr_evaluated is False
    assert hostile3.str_evaluated is False


def test_probes_group3_negative_huge_int_safe_diagnostics():
    """Nhóm 3: Số nguyên âm khổng lồ -(10**5000) không gây lỗi vượt giới hạn 4300 ký số của Python."""
    huge_neg = -(10**5000)

    # Probe 4: negative_huge_count_parser
    with pytest.raises(ValueError, match="^ERR_INVALID_VIDEO_METADATA: reported_nb_frames không được âm"):
        parse_ffprobe_video_header({"streams": [{"index": 0, "codec_type": "video", "nb_frames": huge_neg}]})

    # Probe 5: negative_huge_count_dto
    with pytest.raises(ValueError, match="^ERR_INVALID_VIDEO_METADATA: reported_nb_frames phải là int > 0"):
        VideoStreamHeader(0, None, None, None, huge_neg, None)

    # Probe 6: negative_huge_index_parser
    with pytest.raises(ValueError, match="^ERR_INVALID_VIDEO_METADATA: stream index phải là int >= 0"):
        parse_ffprobe_video_header({"streams": [{"index": huge_neg, "codec_type": "video"}]})

    # Probe 7: negative_huge_selector
    with pytest.raises(ValueError, match="^ERR_INVALID_STREAM_SELECTION: selected_stream_index phải là int >= 0"):
        parse_ffprobe_video_header(
            {"streams": [{"index": 0, "codec_type": "video"}]},
            selected_stream_index=huge_neg,
        )

    # Probe 10: dto_huge_negative_index
    with pytest.raises(ValueError, match="^ERR_INVALID_VIDEO_METADATA: stream_index phải là int >= 0"):
        VideoStreamHeader(huge_neg, None, None, None, None, None)

    # Probe 11: dto_huge_negative_nested_fps
    corrupt_fps = object.__new__(RationalFPS)
    object.__setattr__(corrupt_fps, "numerator", huge_neg)
    object.__setattr__(corrupt_fps, "denominator", 1)
    with pytest.raises(ValueError, match="^ERR_INVALID_VIDEO_METADATA: r_frame_rate numerator và denominator phải dương"):
        VideoStreamHeader(0, None, corrupt_fps, None, None, None)

    # Probe 12: dto_huge_negative_nested_timebase
    corrupt_tb = object.__new__(Fraction)
    object.__setattr__(corrupt_tb, "_numerator", huge_neg)
    object.__setattr__(corrupt_tb, "_denominator", 1)
    with pytest.raises(ValueError, match="^ERR_INVALID_VIDEO_METADATA: time_base numerator và denominator phải dương"):
        VideoStreamHeader(0, None, None, None, None, corrupt_tb)


def test_probes_group3_huge_positive_ints_exact_no_coercion():
    """Nhóm 3: Số nguyên dương khổng lồ 10**5000 bảo toàn tính chính xác tuyệt đối, không ép float/chuỗi."""
    huge_pos = 10**5000

    # Probe 8: huge_missing_selector
    with pytest.raises(ValueError, match="^ERR_NO_VIDEO_STREAM: không tìm thấy video stream hợp lệ với index đã chọn"):
        parse_ffprobe_video_header(
            {"streams": [{"index": 0, "codec_type": "video"}]},
            selected_stream_index=huge_pos,
        )

    # Probe 13: duplicate_huge_native_index
    with pytest.raises(ValueError, match="^ERR_INVALID_VIDEO_METADATA: phát hiện trùng lặp stream index"):
        parse_ffprobe_video_header({
            "streams": [
                {"index": huge_pos, "codec_type": "video"},
                {"index": huge_pos, "codec_type": "video"},
            ]
        })

    # Positive control: parser và DTO với index và nb_frames khổng lồ hợp lệ
    payload = {
        "streams": [
            {
                "index": huge_pos,
                "codec_type": "video",
                "codec_name": "rawvideo",
                "nb_frames": huge_pos + 1,
            }
        ]
    }
    header = parse_ffprobe_video_header(payload)
    # Xác nhận bảo toàn chính xác bằng toán tử so sánh số nguyên (không in chuỗi)
    assert header.stream_index == huge_pos
    assert header.reported_nb_frames == huge_pos + 1

    dto = VideoStreamHeader(
        stream_index=huge_pos,
        codec_name="rawvideo",
        r_frame_rate=None,
        avg_frame_rate=None,
        reported_nb_frames=huge_pos + 1,
        time_base=None,
    )
    assert dto.stream_index == huge_pos
    assert dto.reported_nb_frames == huge_pos + 1


def test_all_13_codex_counterexample_probes_manifest_exact():
    """Xác nhận bao phủ toàn bộ 13 counterexamples từ .bridge/ux-evidence/G06-06C2b-CODEX-REVIEW/probes.json."""
    huge_neg = -(10**5000)
    huge_pos = 10**5000

    # 1. video_disposition_null
    with pytest.raises(ValueError, match="ERR_INVALID_VIDEO_METADATA"):
        parse_ffprobe_video_header({"streams": [{"index": 0, "codec_type": "video", "disposition": None}]})

    # 2. video_attached_pic_null
    with pytest.raises(ValueError, match="ERR_INVALID_VIDEO_METADATA"):
        parse_ffprobe_video_header({
            "streams": [{"index": 0, "codec_type": "video", "disposition": {"attached_pic": None}}]
        })

    # 3. ignored_audio_disposition_invalid
    h3 = parse_ffprobe_video_header({
        "streams": [
            {"index": 0, "codec_type": "video"},
            {"index": 1, "codec_type": "audio", "disposition": "bad"},
        ]
    })
    assert h3.stream_index == 0

    # 4. negative_huge_count_parser
    with pytest.raises(ValueError, match="ERR_INVALID_VIDEO_METADATA"):
        parse_ffprobe_video_header({"streams": [{"index": 0, "codec_type": "video", "nb_frames": huge_neg}]})

    # 5. negative_huge_count_dto
    with pytest.raises(ValueError, match="ERR_INVALID_VIDEO_METADATA"):
        VideoStreamHeader(0, None, None, None, huge_neg, None)

    # 6. negative_huge_index_parser
    with pytest.raises(ValueError, match="ERR_INVALID_VIDEO_METADATA"):
        parse_ffprobe_video_header({"streams": [{"index": huge_neg, "codec_type": "video"}]})

    # 7. negative_huge_selector
    with pytest.raises(ValueError, match="ERR_INVALID_STREAM_SELECTION"):
        parse_ffprobe_video_header(
            {"streams": [{"index": 0, "codec_type": "video"}]},
            selected_stream_index=huge_neg,
        )

    # 8. huge_missing_selector
    with pytest.raises(ValueError, match="ERR_NO_VIDEO_STREAM"):
        parse_ffprobe_video_header(
            {"streams": [{"index": 0, "codec_type": "video"}]},
            selected_stream_index=huge_pos,
        )

    # 9. invalid_index_no_repr
    with pytest.raises(ValueError, match="ERR_INVALID_VIDEO_METADATA"):
        parse_ffprobe_video_header({"streams": [{"index": HostileNoRepr(), "codec_type": "video"}]})

    # 10. dto_huge_negative_index
    with pytest.raises(ValueError, match="ERR_INVALID_VIDEO_METADATA"):
        VideoStreamHeader(huge_neg, None, None, None, None, None)

    # 11. dto_huge_negative_nested_fps
    corrupt_fps = object.__new__(RationalFPS)
    object.__setattr__(corrupt_fps, "numerator", huge_neg)
    object.__setattr__(corrupt_fps, "denominator", 1)
    with pytest.raises(ValueError, match="ERR_INVALID_VIDEO_METADATA"):
        VideoStreamHeader(0, None, corrupt_fps, None, None, None)

    # 12. dto_huge_negative_nested_timebase
    corrupt_tb = object.__new__(Fraction)
    object.__setattr__(corrupt_tb, "_numerator", huge_neg)
    object.__setattr__(corrupt_tb, "_denominator", 1)
    with pytest.raises(ValueError, match="ERR_INVALID_VIDEO_METADATA"):
        VideoStreamHeader(0, None, None, None, None, corrupt_tb)

    # 13. duplicate_huge_native_index
    with pytest.raises(ValueError, match="ERR_INVALID_VIDEO_METADATA"):
        parse_ffprobe_video_header({
            "streams": [
                {"index": huge_pos, "codec_type": "video"},
                {"index": huge_pos, "codec_type": "video"},
            ]
        })


class BadEqObject:
    """Đối tượng đối kháng: ném lỗi nếu so sánh bằng (__eq__) hoặc format (__repr__/__str__)."""
    def __init__(self) -> None:
        self.eq_called = 0
        self.repr_called = False
        self.str_called = False

    def __eq__(self, other: object) -> bool:
        self.eq_called += 1
        raise RuntimeError("must type-gate before equality")

    def __repr__(self) -> str:
        self.repr_called = True
        raise RuntimeError("must not evaluate repr")

    def __str__(self) -> str:
        self.str_called = True
        raise RuntimeError("must not evaluate str")


class CustomStrSubclass(str):
    """Lớp con của str để kiểm tra chính sách strict str."""
    pass


def test_probes_nb_frames_sentinel_typegate_before_equality():
    """Kiểm tra type-gate nghiêm ngặt trước khi so sánh sentinel trong nb_frames."""
    # 1. Object với __eq__ độc hại bị từ chối với ERR_INVALID_VIDEO_METADATA và 0 lần gọi __eq__
    bad_obj = BadEqObject()
    with pytest.raises(ValueError, match="^ERR_INVALID_VIDEO_METADATA: nb_frames phải là int hoặc str hợp lệ"):
        parse_ffprobe_video_header({"streams": [{"index": 0, "codec_type": "video", "nb_frames": bad_obj}]})
    assert bad_obj.eq_called == 0
    assert bad_obj.repr_called is False
    assert bad_obj.str_called is False

    # 2. str subclass bị từ chối với strict str policy
    custom_str = CustomStrSubclass("100")
    with pytest.raises(ValueError, match="^ERR_INVALID_VIDEO_METADATA: nb_frames phải là int hoặc str hợp lệ"):
        parse_ffprobe_video_header({"streams": [{"index": 0, "codec_type": "video", "nb_frames": custom_str}]})

    # 3. Kiểu sai: Mapping, list, bool, float
    for bad_val in ({"a": 1}, [100], True, False, 123.45):
        with pytest.raises(ValueError, match="^ERR_INVALID_VIDEO_METADATA: nb_frames phải là int hoặc str hợp lệ"):
            parse_ffprobe_video_header({"streams": [{"index": 0, "codec_type": "video", "nb_frames": bad_val}]})

    # 4. Unknown controls và positive controls
    h_none = parse_ffprobe_video_header({"streams": [{"index": 0, "codec_type": "video", "nb_frames": None}]})
    assert h_none.reported_nb_frames is None

    h_na = parse_ffprobe_video_header({"streams": [{"index": 0, "codec_type": "video", "nb_frames": "N/A"}]})
    assert h_na.reported_nb_frames is None

    h_zero_int = parse_ffprobe_video_header({"streams": [{"index": 0, "codec_type": "video", "nb_frames": 0}]})
    assert h_zero_int.reported_nb_frames is None

    h_zero_str = parse_ffprobe_video_header({"streams": [{"index": 0, "codec_type": "video", "nb_frames": "0"}]})
    assert h_zero_str.reported_nb_frames is None

    h_exact_str = parse_ffprobe_video_header({"streams": [{"index": 0, "codec_type": "video", "nb_frames": "12345"}]})
    assert h_exact_str.reported_nb_frames == 12345

    huge_pos = 10**5000
    h_exact_int = parse_ffprobe_video_header({"streams": [{"index": 0, "codec_type": "video", "nb_frames": huge_pos}]})
    assert h_exact_int.reported_nb_frames == huge_pos
