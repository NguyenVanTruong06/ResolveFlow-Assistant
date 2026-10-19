import os
import tempfile
import pytest
import numpy as np
from PIL import Image

from src.core.smart_cutout import SmartCutout


def test_heuristic_background_removal_white_bg():
    """Kiểm tra bóc tách phông nền trắng của sticker/meme thành transparent PNG."""
    # Tạo ảnh thử nghiệm 200x200: nền trắng (255,255,255) và hình tròn đỏ ở giữa (220,38,38)
    w, h = 200, 200
    img = Image.new("RGB", (w, h), (255, 255, 255))
    pixels = img.load()
    cx, cy, r = 100, 100, 40
    for y in range(h):
        for x in range(w):
            if (x - cx) ** 2 + (y - cy) ** 2 <= r ** 2:
                pixels[x, y] = (220, 38, 38)

    temp_dir = tempfile.gettempdir()
    input_file = os.path.join(temp_dir, "test_white_bg.png")
    output_file = os.path.join(temp_dir, "test_white_bg_cutout.png")
    img.save(input_file, "PNG")

    # Thực hiện bóc nền
    res_path = SmartCutout.remove_background(input_file, output_path=output_file)
    assert os.path.exists(res_path)

    res_img = Image.open(res_path)
    assert res_img.mode == "RGBA"
    arr = np.array(res_img)

    # 4 góc ban đầu là nền trắng -> Bây giờ phải trong suốt (alpha = 0)
    assert arr[0, 0, 3] == 0, f"Corner alpha should be 0, got {arr[0, 0, 3]}"
    assert arr[0, w-1, 3] == 0
    assert arr[h-1, 0, 3] == 0
    assert arr[h-1, w-1, 3] == 0

    # Trung tâm là hình tròn đỏ -> Phải giữ nguyên màu và đục (alpha = 255)
    assert arr[cy, cx, 3] == 255, f"Center alpha should be 255, got {arr[cy, cx, 3]}"
    assert arr[cy, cx, 0] == 220
    assert arr[cy, cx, 1] == 38
    assert arr[cy, cx, 2] == 38


def test_greenscreen_chroma_removal():
    """Kiểm tra bóc tách phông xanh lá (Chroma Key) cho meme / sticker."""
    w, h = 200, 200
    # Nền xanh lá chuẩn (0, 255, 0) và hình chữ nhật vàng (255, 230, 0)
    img = Image.new("RGB", (w, h), (0, 255, 0))
    pixels = img.load()
    for y in range(60, 140):
        for x in range(60, 140):
            pixels[x, y] = (255, 230, 0)

    temp_dir = tempfile.gettempdir()
    input_file = os.path.join(temp_dir, "test_greenscreen.png")
    output_file = os.path.join(temp_dir, "test_greenscreen_cutout.png")
    img.save(input_file, "PNG")

    res_path = SmartCutout.remove_greenscreen(input_file, output_path=output_file)
    assert os.path.exists(res_path)

    res_img = Image.open(res_path)
    assert res_img.mode == "RGBA"
    arr = np.array(res_img)

    # 4 góc xanh lá -> Alpha = 0
    assert arr[5, 5, 3] == 0
    assert arr[5, w-5, 3] == 0

    # Hình chữ nhật vàng -> Alpha = 255
    assert arr[100, 100, 3] == 255
    assert arr[100, 100, 0] == 255
    assert arr[100, 100, 1] == 230


def test_smart_cutout_auto_detect_mode():
    """Kiểm tra hàm auto_cutout tự nhận diện nền trắng hoặc phông xanh."""
    temp_dir = tempfile.gettempdir()

    # 1. Ảnh viền đen với khối vuông trắng 20x20 ở giữa
    img_black = Image.new("RGB", (100, 100), (0, 0, 0))
    for y in range(40, 60):
        for x in range(40, 60):
            img_black.putpixel((x, y), (255, 255, 255))
    p_black = os.path.join(temp_dir, "test_black_bg.png")
    img_black.save(p_black)

    out_black = SmartCutout.auto_cutout(p_black)
    res_b = Image.open(out_black)
    assert res_b.mode == "RGBA"
    arr_b = np.array(res_b)
    assert arr_b[0, 0, 3] == 0
    assert arr_b[50, 50, 3] == 255


def test_tab_assets_inspector_cutout_button(qapp):
    """Kiểm tra giao diện TabAssets có nút 'Tách nền AI' trong bảng Inspector khi chọn sticker/meme."""
    from src.ui.tabs.tab_assets import TabAssets
    tab = TabAssets()
    tab.show()

    assert hasattr(tab, "btn_smart_cutout"), "TabAssets must have btn_smart_cutout in inspector"
    assert tab.btn_smart_cutout.text() != ""

    # Chọn một icon/sticker
    icon_asset = tab.all_icons[0]
    tab._on_card_selected(icon_asset.id)
    assert tab.btn_smart_cutout.isVisible()
