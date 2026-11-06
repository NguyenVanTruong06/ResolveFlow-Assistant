"""Unit tests for CreativeContext and ReferenceItem contracts (G06-06C1a-R1).

Validates:
- Pure stdlib dataclasses (frozen, immutable, owned, zero runtime aliases)
- Strict intake rules for creative brief, style preset, rules, reference-only items, and stored URLs
- Deterministic 64-hex SHA-256 signature
- Bounds enforcement: MAX_REFERENCE_ITEMS=64, MAX_REFERENCE_URLS=64, MAX_RULES_PER_KIND=128
- Pre-iteration count checks (CountedList overflow has 0 iterations)
- Byte budget: MAX_CREATIVE_CONTEXT_BYTES=32768 (multi-byte UTF-8 exact boundary)
- Lexical absolute path validation (POSIX, Windows drive, valid UNC share; rejects \\server without share and C:relative)
- Bigint overflow handling (controlled ValueError on 10**400 and -10**400, no OverflowError; 10**20 positive control accepted)
- Strict sequences (list/tuple only, rejects range/set/dict/str/bytes)
- Mixed unknown keys handling (no TypeError on mixed types)
- Strict typed boundary revalidation (mutated typed DTO cannot bypass normalize, to_dict, or signature)
- Zero filesystem IO and zero network calls (spy purity check)
"""

from dataclasses import FrozenInstanceError
import json
import math
import os
import socket
import pytest

from src.core.contracts.edit_plan_contracts import (
    ReferenceItem,
    CreativeContext,
    normalize_creative_context,
    creative_context_to_dict,
    compute_creative_context_signature,
    MAX_REFERENCE_ITEMS,
    MAX_REFERENCE_URLS,
    MAX_RULES_PER_KIND,
    MAX_CREATIVE_CONTEXT_BYTES,
)


class CountedList(list):
    """Test helper list tracking iteration attempts."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.iterations = 0

    def __iter__(self):
        self.iterations += 1
        return super().__iter__()


class TestReferenceItemContract:
    """Tests for ReferenceItem frozen dataclass."""

    def test_valid_reference_item_creation(self):
        ref = ReferenceItem(
            reference_id="ref_01",
            local_path="/media/footage/video_mau.mp4",
            media_type="video",
            role="reference_only",
            user_note="Tham khảo nhịp cắt vui tươi",
        )
        assert ref.reference_id == "ref_01"
        assert ref.local_path == "/media/footage/video_mau.mp4"
        assert ref.media_type == "video"
        assert ref.role == "reference_only"
        assert ref.user_note == "Tham khảo nhịp cắt vui tươi"

    def test_default_role_and_user_note(self):
        ref = ReferenceItem(
            reference_id="ref_02",
            local_path="/tmp/image.png",
            media_type="image",
        )
        assert ref.role == "reference_only"
        assert ref.user_note == ""

    def test_immutability(self):
        ref = ReferenceItem(
            reference_id="ref_01",
            local_path="/media/ref.mp4",
            media_type="video",
        )
        with pytest.raises(FrozenInstanceError):
            ref.reference_id = "other_id"  # type: ignore

    def test_invalid_role_rejected(self):
        for bad_role in ("footage", "broll", "sfx", "REFERENCE_ONLY", "", True, 123, ["reference_only"]):
            with pytest.raises(ValueError, match="role"):
                ReferenceItem(
                    reference_id="ref_01",
                    local_path="/media/ref.mp4",
                    media_type="video",
                    role=bad_role,  # type: ignore
                )

    def test_invalid_media_type_rejected(self):
        for bad_type in ("audio", "text", "3d", "", "IMAGE", "VIDEO", True, 42, ["video"]):
            with pytest.raises(ValueError, match="media_type"):
                ReferenceItem(
                    reference_id="ref_01",
                    local_path="/media/ref.mp4",
                    media_type=bad_type,  # type: ignore
                )

    def test_blank_reference_id_rejected(self):
        for bad_id in ("", "   ", "\t\n", True, 123):
            with pytest.raises(ValueError, match="reference_id"):
                ReferenceItem(
                    reference_id=bad_id,  # type: ignore
                    local_path="/media/ref.mp4",
                    media_type="video",
                )

    def test_relative_or_invalid_local_path_rejected(self):
        bad_paths = [
            "",
            "   ",
            "relative/path.mp4",
            "./clip.mp4",
            "../clip.mp4",
            "C:relative_file.mp4",  # Windows drive relative without root
            r"\\server",           # UNC without share component
            r"\\server\\",
            "//server",
        ]
        for bad_path in bad_paths:
            with pytest.raises(ValueError, match="local_path"):
                ReferenceItem(
                    reference_id="ref_01",
                    local_path=bad_path,
                    media_type="video",
                )

    def test_valid_posix_windows_unc_paths_accepted_without_io(self):
        # Lexical checks only, nonexistent paths accepted
        valid_paths = [
            "/home/user/video.mp4",
            "/var/tmp/image.jpg",
            "C:\\Users\\Video\\clip.mp4",
            "C:/Users/Video/clip.mp4",
            "D:/Project/sample.png",
            r"\\server\share\ref.mp4",
            r"\\server\share",
            r"\\?\C:\win\file.mp4",
        ]
        for idx, p in enumerate(valid_paths):
            ref = ReferenceItem(
                reference_id=f"ref_{idx}",
                local_path=p,
                media_type="image" if p.endswith(".png") or p.endswith(".jpg") else "video",
            )
            assert ref.local_path == p

    def test_surrogate_and_nul_rejected_in_fields(self):
        with pytest.raises(ValueError, match="NUL"):
            ReferenceItem(
                reference_id="ref_\0_bad",
                local_path="/media/ref.mp4",
                media_type="video",
            )
        with pytest.raises(ValueError, match="NUL"):
            ReferenceItem(
                reference_id="ref_01",
                local_path="/media/\0ref.mp4",
                media_type="video",
            )
        with pytest.raises(ValueError, match="UTF-8"):
            ReferenceItem(
                reference_id="ref_01",
                local_path="/media/ref.mp4",
                media_type="video",
                user_note="Lone surrogate: \ud800",
            )


class TestCreativeContextContract:
    """Tests for CreativeContext dataclass and helpers."""

    def test_defaults_unset(self):
        ctx = CreativeContext()
        assert ctx.free_brief == ""
        assert ctx.style_preset == ""
        assert ctx.target_duration_sec is None
        assert ctx.must_keep_rules == ()
        assert ctx.must_not_rules == ()
        assert ctx.reference_items == ()
        assert ctx.reference_urls == ()

    def test_vietnamese_roundtrip_preservation(self):
        brief_vn = "Chào mừng đến với tiệm bánh X!\n\"Bánh kem\" & 'bánh ngọt' \\ giòn tan 🎂."
        note_vn = "Học theo nhịp điệu dễ thương, không lấy âm thanh lạ ✨."
        ref = ReferenceItem(
            reference_id="ref_vn",
            local_path="/media/tiem_banh/mau.mp4",
            media_type="video",
            user_note=note_vn,
        )
        ctx = CreativeContext(
            free_brief=brief_vn,
            style_preset="cute_vlog",
            target_duration_sec=60.0,
            must_keep_rules=("Câu chào thương hiệu mở đầu: Tiệm Bánh X",),
            must_not_rules=("Cấm meme giật gân", "Không chèn tiếng thét"),
            reference_items=(ref,),
            reference_urls=("https://tiktok.com/@tiembanhx/video/123",),
        )

        assert ctx.free_brief == brief_vn
        assert ctx.reference_items[0].user_note == note_vn
        d = creative_context_to_dict(ctx)
        assert d["free_brief"] == brief_vn
        assert d["reference_items"][0]["user_note"] == note_vn

        # Roundtrip normalization
        ctx_roundtrip = normalize_creative_context(d)
        assert ctx_roundtrip == ctx

    def test_direct_vs_raw_mapping_equivalent(self):
        raw = {
            "free_brief": "Video du lịch 60s",
            "style_preset": "travel_vlog",
            "target_duration_sec": 60,
            "must_keep_rules": ["Flycam Cầu Rồng", "Phỏng vấn người dân"],
            "must_not_rules": ["meme_troll"],
            "reference_items": [
                {
                    "reference_id": "r1",
                    "local_path": "/data/ref.mp4",
                    "media_type": "video",
                    "user_note": "Góc quay flycam rộng",
                }
            ],
            "reference_urls": ["https://youtube.com/watch?v=sample"],
        }
        ctx_from_raw = normalize_creative_context(raw)

        ref_direct = ReferenceItem(
            reference_id="r1",
            local_path="/data/ref.mp4",
            media_type="video",
            user_note="Góc quay flycam rộng",
        )
        ctx_direct = CreativeContext(
            free_brief="Video du lịch 60s",
            style_preset="travel_vlog",
            target_duration_sec=60,
            must_keep_rules=("Flycam Cầu Rồng", "Phỏng vấn người dân"),
            must_not_rules=("meme_troll",),
            reference_items=(ref_direct,),
            reference_urls=("https://youtube.com/watch?v=sample",),
        )

        assert ctx_from_raw == ctx_direct
        assert compute_creative_context_signature(ctx_from_raw) == compute_creative_context_signature(ctx_direct)

    def test_ownership_and_immutability(self):
        input_rules = ["Rule 1", "Rule 2"]
        raw = {
            "free_brief": "test",
            "must_keep_rules": input_rules,
        }
        ctx = normalize_creative_context(raw)

        # Mutating input after normalization does not affect context
        input_rules.append("Rule 3")
        assert ctx.must_keep_rules == ("Rule 1", "Rule 2")

        # Mutating dictionary output does not affect context
        d = creative_context_to_dict(ctx)
        d["must_keep_rules"].append("Rule 4")
        assert ctx.must_keep_rules == ("Rule 1", "Rule 2")

        # Frozen instance
        with pytest.raises(FrozenInstanceError):
            ctx.free_brief = "modified"  # type: ignore

    def test_signature_deterministic_and_all_fields_sensitive(self):
        base = CreativeContext(
            free_brief="brief",
            style_preset="preset",
            target_duration_sec=30.0,
            must_keep_rules=("k1", "k2"),
            must_not_rules=("n1",),
            reference_items=(
                ReferenceItem(reference_id="r1", local_path="/ref1.mp4", media_type="video"),
            ),
            reference_urls=("https://example.com/1",),
        )
        sig_base = compute_creative_context_signature(base)
        assert len(sig_base) == 64
        assert sig_base.islower()
        # Same object produces same signature
        assert compute_creative_context_signature(base) == sig_base

        # Ordering of rules changes signature
        ctx_reordered_rules = CreativeContext(
            free_brief="brief",
            style_preset="preset",
            target_duration_sec=30.0,
            must_keep_rules=("k2", "k1"),
            must_not_rules=("n1",),
            reference_items=(
                ReferenceItem(reference_id="r1", local_path="/ref1.mp4", media_type="video"),
            ),
            reference_urls=("https://example.com/1",),
        )
        assert compute_creative_context_signature(ctx_reordered_rules) != sig_base

        # Modification of any field changes signature
        assert compute_creative_context_signature(CreativeContext(free_brief="brief_changed")) != compute_creative_context_signature(CreativeContext(free_brief="brief"))
        assert compute_creative_context_signature(CreativeContext(style_preset="preset_a")) != compute_creative_context_signature(CreativeContext(style_preset="preset_b"))
        assert compute_creative_context_signature(CreativeContext(target_duration_sec=10)) != compute_creative_context_signature(CreativeContext(target_duration_sec=20))

    def test_duplicate_reference_id_rejected(self):
        ref1 = ReferenceItem(reference_id="ref_dup", local_path="/p1.mp4", media_type="video")
        ref2 = ReferenceItem(reference_id="ref_dup", local_path="/p2.mp4", media_type="video")

        with pytest.raises(ValueError, match="duplicate.*reference_id"):
            CreativeContext(reference_items=(ref1, ref2))

        with pytest.raises(ValueError, match="duplicate.*reference_id"):
            normalize_creative_context({
                "reference_items": [
                    {"reference_id": "r_dup", "local_path": "/p1.mp4", "media_type": "video"},
                    {"reference_id": "r_dup", "local_path": "/p2.mp4", "media_type": "video"},
                ]
            })

    def test_unknown_keys_rejected_and_mixed_type_safe(self):
        # Context unknown keys with mixed types (int and str)
        with pytest.raises(ValueError, match="unknown"):
            normalize_creative_context({1: "val", "unknown_str": "val"})

        with pytest.raises(ValueError, match="unknown"):
            normalize_creative_context({"user_approved": True})

        with pytest.raises(ValueError, match="unknown"):
            normalize_creative_context({"asset_file": "broll.mp4"})

        # Reference unknown keys with mixed types
        with pytest.raises(ValueError, match="unknown"):
            normalize_creative_context({
                "reference_items": [
                    {
                        "reference_id": "r1",
                        "local_path": "/p.mp4",
                        "media_type": "video",
                        2: "extra_int_key",
                        "extra_str_key": "not_allowed",
                    }
                ]
            })

    def test_duration_validation(self):
        # Valid positive finite durations
        assert CreativeContext(target_duration_sec=60).target_duration_sec == 60
        assert CreativeContext(target_duration_sec=45.5).target_duration_sec == 45.5
        assert CreativeContext(target_duration_sec=None).target_duration_sec is None

        # Positive control: finite positive large bigint within byte budget is accepted
        assert CreativeContext(target_duration_sec=10**20).target_duration_sec == 10**20

        # Bigint overflow: 10**400 and -10**400 raise controlled ValueError (no OverflowError)
        with pytest.raises(ValueError, match="target_duration_sec"):
            CreativeContext(target_duration_sec=10**400)

        with pytest.raises(ValueError, match="target_duration_sec"):
            CreativeContext(target_duration_sec=-10**400)

        # Rejects bool
        with pytest.raises(ValueError, match="target_duration_sec"):
            CreativeContext(target_duration_sec=True)  # type: ignore

        # Rejects string, zero, negative, NaN, Inf
        for bad_dur in ("60", "30.5", 0, 0.0, -5, -0.1, math.nan, math.inf, -math.inf):
            with pytest.raises(ValueError, match="target_duration_sec"):
                CreativeContext(target_duration_sec=bad_dur)  # type: ignore

    def test_strict_sequence_types_rejected(self):
        # Only list or tuple are accepted; range, set, dict, str, bytes are rejected
        with pytest.raises(ValueError, match="ERR_INVALID_SEQUENCE"):
            CreativeContext(must_keep_rules=range(0))  # type: ignore

        with pytest.raises(ValueError, match="ERR_INVALID_SEQUENCE"):
            CreativeContext(must_not_rules=range(5))  # type: ignore

        with pytest.raises(ValueError, match="ERR_INVALID_SEQUENCE"):
            CreativeContext(reference_items={"r1": ReferenceItem("r1", "/p.mp4", "video")})  # type: ignore

        with pytest.raises(ValueError, match="ERR_INVALID_SEQUENCE"):
            normalize_creative_context({"reference_urls": set(["https://ok.com"])})

    def test_count_precheck_before_iteration(self):
        # CountedList with 129 rules must raise ValueError with ZERO iterations
        counted_rules = CountedList([f"Rule {i}" for i in range(MAX_RULES_PER_KIND + 1)])
        with pytest.raises(ValueError, match="ERR_COUNT_OVERFLOW"):
            normalize_creative_context({"must_keep_rules": counted_rules})
        assert counted_rules.iterations == 0, "Pre-check must trigger before any iteration or copy!"

    def test_blank_items_in_rules_or_urls_rejected(self):
        with pytest.raises(ValueError, match="must_keep_rules"):
            CreativeContext(must_keep_rules=("valid", "", "valid2"))

        with pytest.raises(ValueError, match="must_not_rules"):
            CreativeContext(must_not_rules=("   ",))

        with pytest.raises(ValueError, match="reference_urls"):
            CreativeContext(reference_urls=("https://ok.com", "\t\n"))

    def test_max_count_limits_equality_accepted_overflow_rejected(self):
        # MAX_REFERENCE_ITEMS = 64
        refs_exact = tuple(
            ReferenceItem(reference_id=f"r_{i}", local_path=f"/path_{i}.mp4", media_type="video")
            for i in range(MAX_REFERENCE_ITEMS)
        )
        ctx_exact_refs = CreativeContext(reference_items=refs_exact)
        assert len(ctx_exact_refs.reference_items) == 64

        refs_overflow = refs_exact + (
            ReferenceItem(reference_id="r_overflow", local_path="/overflow.mp4", media_type="video"),
        )
        with pytest.raises(ValueError, match=f"reference_items.*exceeds maximum {MAX_REFERENCE_ITEMS}"):
            CreativeContext(reference_items=refs_overflow)

        # MAX_REFERENCE_URLS = 64
        urls_exact = tuple(f"https://example.com/{i}" for i in range(MAX_REFERENCE_URLS))
        ctx_exact_urls = CreativeContext(reference_urls=urls_exact)
        assert len(ctx_exact_urls.reference_urls) == 64

        urls_overflow = urls_exact + ("https://example.com/overflow",)
        with pytest.raises(ValueError, match=f"reference_urls.*exceeds maximum {MAX_REFERENCE_URLS}"):
            CreativeContext(reference_urls=urls_overflow)

        # MAX_RULES_PER_KIND = 128
        rules_exact = tuple(f"Rule {i}" for i in range(MAX_RULES_PER_KIND))
        ctx_exact_rules = CreativeContext(must_keep_rules=rules_exact)
        assert len(ctx_exact_rules.must_keep_rules) == 128

        rules_overflow = rules_exact + ("Rule overflow",)
        with pytest.raises(ValueError, match=f"must_keep_rules.*exceeds maximum {MAX_RULES_PER_KIND}"):
            CreativeContext(must_keep_rules=rules_overflow)

    def test_byte_budget_enforcement_with_multibyte_utf8(self):
        # Base dict overhead
        base_dict = {
            "free_brief": "",
            "style_preset": "test_preset",
            "target_duration_sec": 60,
            "must_keep_rules": [],
            "must_not_rules": [],
            "reference_items": [],
            "reference_urls": [],
        }
        overhead = len(json.dumps(base_dict, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8'))
        remaining = MAX_CREATIVE_CONTEXT_BYTES - overhead

        # Use Vietnamese 3-byte characters (e.g., 'ế' is 3 bytes in UTF-8)
        char = "ế"
        char_bytes = len(char.encode("utf-8"))
        num_chars = remaining // char_bytes
        pad_bytes = remaining % char_bytes
        exact_brief = (char * num_chars) + ("a" * pad_bytes)

        ctx_exact = normalize_creative_context({"free_brief": exact_brief, "style_preset": "test_preset", "target_duration_sec": 60})
        raw_bytes = json.dumps(creative_context_to_dict(ctx_exact), ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
        assert len(raw_bytes) == MAX_CREATIVE_CONTEXT_BYTES

        # +1 byte overflows
        overflow_brief = exact_brief + "b"
        with pytest.raises(ValueError, match=f"MAX_CREATIVE_CONTEXT_BYTES.*{MAX_CREATIVE_CONTEXT_BYTES}"):
            normalize_creative_context({"free_brief": overflow_brief, "style_preset": "test_preset", "target_duration_sec": 60})

    def test_typed_boundary_revalidation_and_no_bypass(self):
        # 1. Mutated ReferenceItem role rejected when passed to CreativeContext
        ref = ReferenceItem(reference_id="r1", local_path="/valid.mp4", media_type="video")
        object.__setattr__(ref, "role", "footage")  # Simulate unauthorized mutation
        with pytest.raises(ValueError, match="role"):
            CreativeContext(reference_items=(ref,))

        # 2. Mutated CreativeContext duration rejected by normalize, to_dict, signature
        ctx = CreativeContext(free_brief="ok", target_duration_sec=30)
        object.__setattr__(ctx, "target_duration_sec", -1)  # Simulate unauthorized mutation

        with pytest.raises(ValueError, match="target_duration_sec"):
            normalize_creative_context(ctx)

        with pytest.raises(ValueError, match="target_duration_sec"):
            creative_context_to_dict(ctx)

        with pytest.raises(ValueError, match="target_duration_sec"):
            compute_creative_context_signature(ctx)

        # 3. Oversized brief in typed context rejected by to_dict and signature
        ctx_large = CreativeContext(free_brief="ok")
        object.__setattr__(ctx_large, "free_brief", "a" * (MAX_CREATIVE_CONTEXT_BYTES + 100))

        with pytest.raises(ValueError, match="MAX_CREATIVE_CONTEXT_BYTES"):
            creative_context_to_dict(ctx_large)

        with pytest.raises(ValueError, match="MAX_CREATIVE_CONTEXT_BYTES"):
            compute_creative_context_signature(ctx_large)

    def test_purity_spy_no_filesystem_no_network(self, monkeypatch):
        # Spy on open, os.stat, socket.socket to verify 0 IO and 0 network
        io_called = []
        net_called = []

        def fake_open(*args, **kwargs):
            io_called.append("open")
            raise RuntimeError("Filesystem open prohibited!")

        def fake_stat(*args, **kwargs):
            io_called.append("stat")
            raise RuntimeError("Filesystem stat prohibited!")

        def fake_socket(*args, **kwargs):
            net_called.append("socket")
            raise RuntimeError("Network calls prohibited!")

        monkeypatch.setattr("builtins.open", fake_open)
        monkeypatch.setattr(os, "stat", fake_stat)
        monkeypatch.setattr(socket, "socket", fake_socket)

        # Run full pipeline with valid paths and URLs
        ref = ReferenceItem(
            reference_id="ref_pure",
            local_path="/media/footage/clip.mp4",
            media_type="video",
            user_note="Purity check",
        )
        ctx = CreativeContext(
            free_brief="Zero IO test",
            style_preset="minimalist",
            target_duration_sec=60,
            must_keep_rules=("Rule 1",),
            reference_items=(ref,),
            reference_urls=("https://example.com/ref",),
        )

        d = creative_context_to_dict(ctx)
        ctx_norm = normalize_creative_context(d)
        sig = compute_creative_context_signature(ctx_norm)

        assert len(sig) == 64
        assert len(io_called) == 0, f"Detected prohibited filesystem IO: {io_called}"
        assert len(net_called) == 0, f"Detected prohibited network calls: {net_called}"

    def test_lone_surrogates_and_nul_in_creative_context(self):
        with pytest.raises(ValueError, match="NUL"):
            CreativeContext(free_brief="Brief with \0 character")

        with pytest.raises(ValueError, match="NUL"):
            CreativeContext(style_preset="preset_\0_bad")

        with pytest.raises(ValueError, match="UTF-8"):
            CreativeContext(free_brief="Surrogate \ud800 test")
