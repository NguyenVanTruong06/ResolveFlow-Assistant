"""
Smart Cutout AI: Module Tách Nền Ảnh & Sticker Tự Động.
Học hỏi từ concat-vision (RMBG-1.4 / BiRefNet ONNX) & Hệ thống Heuristic Chroma/Alpha Edge-Aware.

Tính năng:
1. Tự động bóc nền trắng/đen/đơn sắc cho Memes, Logo, Stickers thành PNG trong suốt.
2. Bóc phông xanh Chroma Key (Green Screen) chuyên nghiệp với bộ khử viền xanh (Spill Suppression).
3. Hỗ trợ chạy mô hình học sâu RMBG-1.4 qua ONNX Runtime cục bộ khi có model file.
"""

import os
import tempfile
import numpy as np
from pathlib import Path
from typing import Optional, Union, Tuple
from PIL import Image, ImageFilter


class SmartCutout:
    """Bộ xử lý bóc tách phông nền tự động sang chuẩn Transparent RGBA PNG."""

    MODEL_DIR = Path("assets/models")
    DEFAULT_RMBG_NAME = "rmbg-1.4.onnx"

    @classmethod
    def get_model_path(cls) -> Path:
        cls.MODEL_DIR.mkdir(parents=True, exist_ok=True)
        return cls.MODEL_DIR / cls.DEFAULT_RMBG_NAME

    @classmethod
    def has_onnx_model(cls) -> bool:
        return cls.get_model_path().is_file()

    @staticmethod
    def _prepare_output_path(input_path: str, output_path: Optional[str] = None) -> str:
        if output_path:
            out_p = Path(output_path)
            out_p.parent.mkdir(parents=True, exist_ok=True)
            return str(out_p)
        stem = Path(input_path).stem
        temp_dir = tempfile.gettempdir()
        return os.path.join(temp_dir, f"{stem}_cutout.png")

    @classmethod
    def remove_background(
        cls,
        image_input: Union[str, Image.Image],
        output_path: Optional[str] = None,
        tolerance: float = 38.0,
        feather: int = 1
    ) -> str:
        """
        Bóc nền đơn sắc (trắng, xám, đen, v.v.) bằng thuật toán khoảng cách màu Euclide
        kèm bộ làm mượt viền biên antialiased feathering.
        """
        if isinstance(image_input, str):
            src_img = Image.open(image_input).convert("RGBA")
            out_file = cls._prepare_output_path(image_input, output_path)
        else:
            src_img = image_input.convert("RGBA")
            out_file = cls._prepare_output_path("image", output_path)

        w, h = src_img.size
        arr = np.array(src_img, dtype=np.float32)

        # 1. Lấy mẫu màu nền ở 4 góc và 4 cạnh biên
        sample_pts = [
            (0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1),
            (w // 2, 0), (w // 2, h - 1), (0, h // 2), (w - 1, h // 2)
        ]
        sampled_colors = [arr[pt[1], pt[0], :3] for pt in sample_pts]
        # Lấy trung vị màu để loại bỏ nhiễu biên
        bg_color = np.median(sampled_colors, axis=0)

        # 2. Tính khoảng cách màu (Euclidean Distance trong không gian RGB)
        rgb_diff = arr[:, :, :3] - bg_color
        dist = np.sqrt(np.sum(rgb_diff ** 2, axis=-1))

        # 3. Tạo mặt nạ Alpha chuyển tiếp mượt mà (Smoothstep Edge)
        t_low = max(4.0, tolerance * 0.70)
        t_high = max(t_low + 6.0, tolerance * 1.30)
        alpha = np.clip((dist - t_low) / (t_high - t_low), 0.0, 1.0) * 255.0

        # Gán kênh alpha
        arr[:, :, 3] = alpha

        # 4. Làm mịn viền bằng PIL nếu cần
        res_img = Image.fromarray(arr.astype(np.uint8), mode="RGBA")
        if feather > 0:
            # Lọc nhẹ kênh alpha để triệt tiêu răng cưa
            r, g, b, a = res_img.split()
            a_smooth = a.filter(ImageFilter.BoxBlur(feather))
            res_img = Image.merge("RGBA", (r, g, b, a_smooth))

        res_img.save(out_file, "PNG")
        return out_file

    @classmethod
    def remove_greenscreen(
        cls,
        image_input: Union[str, Image.Image],
        output_path: Optional[str] = None,
        green_threshold: float = 30.0
    ) -> str:
        """
        Bóc phông xanh Chroma Key với thuật toán Spill Suppression (Khử viền xanh bám đối tượng).
        """
        if isinstance(image_input, str):
            src_img = Image.open(image_input).convert("RGBA")
            out_file = cls._prepare_output_path(image_input, output_path)
        else:
            src_img = image_input.convert("RGBA")
            out_file = cls._prepare_output_path("greenscreen", output_path)

        arr = np.array(src_img, dtype=np.float32)
        r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]

        # Tiêu chí nhận diện pixel phông xanh: Kênh G trội hơn trung bình của R và B
        max_rb = np.maximum(r, b)
        green_diff = g - max_rb

        # Tính toán alpha: nếu green_diff > green_threshold thì trong suốt
        t_low = green_threshold * 0.5
        t_high = green_threshold * 1.2
        alpha = np.clip(1.0 - (green_diff - t_low) / (t_high - t_low), 0.0, 1.0) * 255.0
        arr[:, :, 3] = alpha

        # Khử lem màu xanh (Spill Suppression): hạ kênh Green ở viền xuống bằng max(R, B)
        spill_mask = (g > max_rb) & (alpha > 0)
        arr[:, :, 1][spill_mask] = max_rb[spill_mask]

        res_img = Image.fromarray(arr.astype(np.uint8), mode="RGBA")
        res_img.save(out_file, "PNG")
        return out_file

    @classmethod
    def auto_cutout(cls, image_input: Union[str, Image.Image], output_path: Optional[str] = None) -> str:
        """
        Tự động nhận diện loại phông nền (Green Screen hay Nền trắng/đen đơn sắc)
        để chọn giải thuật bóc tách tối ưu nhất.
        """
        if isinstance(image_input, str):
            img = Image.open(image_input).convert("RGB")
        else:
            img = image_input.convert("RGB")

        w, h = img.size
        arr = np.array(img, dtype=np.float32)

        # Kiểm tra màu ở các góc
        corners = [arr[0, 0], arr[0, w - 1], arr[h - 1, 0], arr[h - 1, w - 1]]
        avg_corner = np.mean(corners, axis=0)

        # Nếu góc có màu xanh lá trội rõ rệt -> Dùng remove_greenscreen
        if avg_corner[1] > avg_corner[0] + 35.0 and avg_corner[1] > avg_corner[2] + 35.0:
            return cls.remove_greenscreen(image_input, output_path=output_path)
        else:
            return cls.remove_background(image_input, output_path=output_path)

    @classmethod
    def run_onnx_rmbg(
        cls,
        image_input: Union[str, Image.Image],
        model_path: Optional[str] = None,
        output_path: Optional[str] = None
    ) -> str:
        """
        Chạy mô hình học sâu RMBG / BiRefNet thông qua ONNX Runtime cục bộ
        (nếu đã tải file model .onnx). Nếu chưa có file model, tự động fallback sang auto_cutout.
        """
        target_model = model_path or str(cls.get_model_path())
        if not os.path.isfile(target_model):
            return cls.auto_cutout(image_input, output_path=output_path)

        try:
            import onnxruntime as ort

            if isinstance(image_input, str):
                orig_img = Image.open(image_input).convert("RGB")
                out_file = cls._prepare_output_path(image_input, output_path)
            else:
                orig_img = image_input.convert("RGB")
                out_file = cls._prepare_output_path("rmbg", output_path)

            orig_w, orig_h = orig_img.size

            # Chuẩn bị input cho model 1024x1024
            target_size = (1024, 1024)
            resized = orig_img.resize(target_size, Image.BILINEAR)
            arr = np.array(resized, dtype=np.float32) / 255.0
            # Chuẩn hóa ImageNet mean/std
            mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
            std = np.array([0.229, 0.224, 0.225], dtype=np.float32)
            arr = (arr - mean) / std
            # Chuyển layout sang NCHW (1, 3, 1024, 1024)
            arr = np.transpose(arr, (2, 0, 1))
            tensor = np.expand_dims(arr, axis=0)

            # Chạy inference
            session = ort.InferenceSession(target_model, providers=["CPUExecutionProvider"])
            input_name = session.get_inputs()[0].name
            outputs = session.run(None, {input_name: tensor})
            mask = outputs[0][0, 0] # Output mask

            # Sigmoid nếu chưa được kích hoạt
            mask = 1.0 / (1.0 + np.exp(-mask))
            mask = np.clip(mask * 255.0, 0, 255).astype(np.uint8)

            # Resize mask về kích thước ảnh gốc
            mask_img = Image.fromarray(mask, mode="L").resize((orig_w, orig_h), Image.BILINEAR)

            # Ghép alpha vào ảnh gốc
            rgba_img = orig_img.convert("RGBA")
            rgba_img.putalpha(mask_img)
            rgba_img.save(out_file, "PNG")
            return out_file

        except Exception as e:
            print(f"ONNX RMBG inference fallback: {e}")
            return cls.auto_cutout(image_input, output_path=output_path)
