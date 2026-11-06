"""
src/core/contracts/time_contracts.py
Hợp đồng thời gian thuần Python (G01-01B1-T-I).

Quy tắc kiến trúc:
1. Pure Python standard library: math, fractions, dataclasses, typing.
2. Không import Qt (PySide6), filesystem IO, network, hoặc Resolve native API.
3. Không fallback ngầm về 30.0; missing / invalid FPS phải ném ValueError rõ nghĩa.
4. Rounding half-even chính xác bằng số học phân số (rational arithmetic), không round float trung gian.
5. Bảo toàn 100% tỷ lệ của explicit Fraction/rational string; ánh xạ metadata float aliases theo policy minh bạch.
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import math
from typing import Any, Union


# ==============================================================================
# 1. RATIONAL FPS
# ==============================================================================

@dataclass(frozen=True)
class RationalFPS:
    """
    Biểu diễn tốc độ khung hình (FPS) dưới dạng phân số tối giản numerator / denominator.
    Đảm bảo tính chính xác tuyệt đối, loại bỏ sai số trôi frame của số thực.
    """
    numerator: int
    denominator: int

    def __post_init__(self) -> None:
        # 1. Kiểm tra kiểu nghiêm ngặt (cấm bool)
        if type(self.numerator) is not int or type(self.numerator) is bool:
            raise ValueError(f"numerator phải là số nguyên (int), nhận được: {type(self.numerator).__name__}")
        if type(self.denominator) is not int or type(self.denominator) is bool:
            raise ValueError(f"denominator phải là số nguyên (int), nhận được: {type(self.denominator).__name__}")

        # 2. Kiểm tra giá trị dương
        if self.numerator <= 0:
            raise ValueError(f"numerator phải dương (> 0), nhận được: {self.numerator}")
        if self.denominator <= 0:
            raise ValueError(f"denominator phải dương (> 0), nhận được: {self.denominator}")

        # 3. Tối giản phân số qua gcd
        g = math.gcd(self.numerator, self.denominator)
        if g > 1:
            object.__setattr__(self, "numerator", self.numerator // g)
            object.__setattr__(self, "denominator", self.denominator // g)

    def as_fraction(self) -> Fraction:
        """Chuyển đổi sang đối tượng fractions.Fraction."""
        return Fraction(self.numerator, self.denominator)

    def as_float(self) -> float:
        """Quy đổi sang float để hiển thị."""
        return self.numerator / self.denominator

    def __str__(self) -> str:
        if self.denominator == 1:
            return str(self.numerator)
        return f"{self.numerator}/{self.denominator}"

    def __repr__(self) -> str:
        return f"RationalFPS({self.numerator}/{self.denominator})"

    @classmethod
    def from_value(cls, value: Union[int, float, Fraction, str, RationalFPS]) -> RationalFPS:
        """
        Nhận diện và chuẩn hóa FPS từ nhiều định dạng:
        - RationalFPS: trả về chính nó.
        - int: chuyển thành value / 1 (yêu cầu > 0).
        - Fraction: bảo toàn đúng tỷ lệ của Fraction được truyền tường minh (không tự đổi 2997/100 sang NTSC).
        - str:
            + Dạng phân số "A/B": giữ nguyên tỷ lệ sau khi tối giản.
            + Dạng chuỗi số thập phân: ánh xạ qua policy alias float (23.976 -> 24000/1001, 29.97 -> 30000/1001...).
        - float:
            + Nguyên (24.0, 25.0, 30.0, 60.0): chuyển thành integer rate.
            + Metadata aliases chuẩn: 23.976, 23.98 -> 24000/1001; 29.97 -> 30000/1001; 59.94 -> 60000/1001.
            + Float gần NTSC thật với tolerance < 1e-4 -> NTSC tương ứng.
            + Các số thực hữu hạn khác: xấp xỉ qua limit_denominator(10010).
        - None, bool, số <= 0, NaN, Inf, chuỗi lỗi -> ném ValueError có ý nghĩa.
        """
        if isinstance(value, RationalFPS):
            return value

        if type(value) is bool:
            raise ValueError(f"FPS không thể là giá trị bool: {value!r}")

        if value is None:
            raise ValueError("FPS không thể là None. Phải cung cấp giá trị hợp lệ.")

        if isinstance(value, int):
            if value <= 0:
                raise ValueError(f"FPS nguyên phải là số dương (> 0), nhận được: {value}")
            return cls(value, 1)

        if isinstance(value, Fraction):
            if value <= 0:
                raise ValueError(f"Fraction FPS phải là số dương (> 0), nhận được: {value}")
            return cls(value.numerator, value.denominator)

        if isinstance(value, str):
            s = value.strip()
            if not s:
                raise ValueError("Chuỗi FPS không được rỗng.")

            # Giới hạn độ dài chuỗi để tránh DoS hoặc vượt quá int string limit
            if len(s) > 100:
                raise ValueError(f"Chuỗi FPS vượt quá độ dài cho phép ({len(s)} ký tự).")

            if "/" in s:
                parts = s.split("/")
                if len(parts) != 2:
                    raise ValueError(f"Chuỗi phân số FPS không đúng định dạng 'A/B': {s!r}")
                try:
                    num = int(parts[0].strip())
                    den = int(parts[1].strip())
                except ValueError as err:
                    raise ValueError(f"Chuỗi phân số FPS chứa giá trị không phải số nguyên: {s!r}") from err
                if den == 0:
                    raise ValueError(f"Mẫu số FPS không thể bằng 0: {s!r}")
                if num <= 0 or den <= 0:
                    raise ValueError(f"Tử số và mẫu số FPS phải là số dương (> 0): {s!r}")
                # Giữ nguyên tỷ lệ phân số tường minh
                frac = Fraction(num, den)
                return cls(frac.numerator, frac.denominator)

            # Chuỗi số thập phân hoặc số nguyên
            try:
                # Kiểm tra nếu là số nguyên dạng chuỗi ("24", "30")
                if s.isdigit() or (s.startswith("+") and s[1:].isdigit()):
                    int_val = int(s)
                    if int_val <= 0:
                        raise ValueError(f"FPS phải dương (> 0), nhận được: {int_val}")
                    return cls(int_val, 1)

                f_val = float(s)
            except ValueError as err:
                raise ValueError(f"Không thể chuyển đổi chuỗi sang FPS hợp lệ: {s!r}") from err

            return cls._from_float_with_policy(f_val)

        if isinstance(value, float):
            return cls._from_float_with_policy(value)

        raise ValueError(f"Kiểu dữ liệu FPS không được hỗ trợ: {type(value).__name__}")

    @classmethod
    def _from_float_with_policy(cls, value: float) -> RationalFPS:
        """Xử lý giá trị float theo chính sách alias và tolerance minh bạch."""
        if not math.isfinite(value):
            raise ValueError(f"FPS float phải là số hữu hạn (không chấp nhận NaN hoặc Infinity), nhận được: {value}")
        if value <= 0.0:
            raise ValueError(f"FPS float phải là số dương (> 0), nhận được: {value}")

        # 1. Số nguyên chính xác (24.0, 25.0, 30.0, 50.0, 60.0)
        rounded = round(value)
        if abs(value - rounded) < 1e-9:
            return cls(rounded, 1)

        # 2. Metadata aliases & float gần giá trị NTSC thực
        # 24000/1001 ~ 23.976023976023978
        if abs(value - 24000 / 1001) < 1e-4 or abs(value - 23.976) < 1e-5 or abs(value - 23.98) < 1e-5:
            return cls(24000, 1001)

        # 30000/1001 ~ 29.97002997002997
        if abs(value - 30000 / 1001) < 1e-4 or abs(value - 29.97) < 1e-5:
            return cls(30000, 1001)

        # 60000/1001 ~ 59.94005994005994
        if abs(value - 60000 / 1001) < 1e-4 or abs(value - 59.94) < 1e-5:
            return cls(60000, 1001)

        # 3. Các tỷ lệ hữu hạn khác: xấp xỉ phân số hợp lý với giới hạn mẫu số 10010
        try:
            frac = Fraction(str(value)).limit_denominator(10010)
            return cls(frac.numerator, frac.denominator)
        except Exception as err:
            raise ValueError(f"Không thể xấp xỉ FPS float sang phân số hợp lệ: {value}") from err


# ==============================================================================
# 2. TOÁN HỌC THỜI GIAN KHUNG HÌNH (PTS TO FRAME & FRAME TO PTS)
# ==============================================================================

def _round_half_to_even(num: int, den: int) -> int:
    """
    Làm tròn phân số num / den sang số nguyên gần nhất theo quy tắc round-half-to-even.
    Yêu cầu: den > 0.
    Hoạt động chính xác trên cả số dương và số âm (signed coordinate).
    """
    q, r = divmod(num, den)
    rem2 = 2 * r
    if rem2 < den:
        return q
    elif rem2 > den:
        return q + 1
    else:
        # Trường hợp chính giữa (exact tie): chọn số chẵn
        return q if q % 2 == 0 else q + 1


def pts_to_frame(pts_sec: Union[int, float, Fraction], fps: RationalFPS) -> int:
    """
    Quy đổi thời gian nguồn PTS (Presentation Time Stamp tính bằng giây)
    sang FRAME OFFSET nguyên dựa trên tốc độ khung hình RationalFPS.

    Nguyên tắc toán học:
    - Nhận int, float, hoặc Fraction (cấm bool, None, NaN, Inf).
    - Float được chuyển sang Fraction(str(value)) để đảm bảo hành vi tie dự đoán được.
    - Phép nhân phân số thuần túy: X = (pts_frac * fps.numerator) / fps.denominator.
    - Áp dụng round-half-to-even chính xác qua divmod.
    - Cho phép tọa độ âm (signed coordinate), không ngầm clamp về 0.
    """
    if type(pts_sec) is bool:
        raise ValueError(f"pts_sec không thể là bool: {pts_sec!r}")

    if pts_sec is None:
        raise ValueError("pts_sec không thể là None.")

    if not isinstance(fps, RationalFPS):
        raise TypeError(f"fps bắt buộc phải là đối tượng RationalFPS, nhận được: {type(fps).__name__}")

    if isinstance(pts_sec, int):
        pts_frac = Fraction(pts_sec, 1)
    elif isinstance(pts_sec, Fraction):
        pts_frac = pts_sec
    elif isinstance(pts_sec, float):
        if not math.isfinite(pts_sec):
            raise ValueError(f"pts_sec float phải hữu hạn (không NaN/Inf), nhận được: {pts_sec}")
        pts_frac = Fraction(str(pts_sec))
    else:
        raise TypeError(f"Kiểu dữ liệu pts_sec không hợp lệ: {type(pts_sec).__name__}")

    # X = (pts_frac.numerator * fps.numerator) / (pts_frac.denominator * fps.denominator)
    total_num = pts_frac.numerator * fps.numerator
    total_den = pts_frac.denominator * fps.denominator

    return _round_half_to_even(total_num, total_den)


def frame_to_pts(frame: int, fps: RationalFPS) -> Fraction:
    """
    Quy đổi chỉ số khung hình (frame offset) sang thời gian chính xác dạng Fraction (giây).

    Nguyên tắc:
    - Frame bắt buộc là int thực tế (cấm bool). Cho phép frame âm (signed coordinate).
    - Trả về Fraction chính xác để đảm bảo roundtrip hoàn hảo:
      pts_to_frame(frame_to_pts(frame, fps), fps) == frame.
    - Caller nếu muốn float có thể tự gọi float(res) tại ranh giới hiển thị.
    """
    if type(frame) is not int or type(frame) is bool:
        raise ValueError(f"frame bắt buộc phải là số nguyên (int, không phải bool), nhận được: {type(frame).__name__}")

    if not isinstance(fps, RationalFPS):
        raise TypeError(f"fps bắt buộc phải là đối tượng RationalFPS, nhận được: {type(fps).__name__}")

    # frame / (fps.numerator / fps.denominator) = (frame * fps.denominator) / fps.numerator
    return Fraction(frame * fps.denominator, fps.numerator)


# ==============================================================================
# 3. KHOẢNG THỜI GIAN FRAME NGUYÊN (TIME RANGE FRAMES)
# ==============================================================================

@dataclass(frozen=True)
class TimeRangeFrames:
    """
    Khoảng thời gian khung hình nguyên nửa mở [start_frame, end_frame).
    Đặc điểm:
    - Bất biến (Frozen), hai đầu là integer không bool.
    - Điều kiện: start_frame < end_frame (cho phép tọa độ âm).
    - duration_frames = end_frame - start_frame.
    - contains(frame): chứa start_frame, KHÔNG chứa end_frame.
    - overlaps(other): trả về False nếu hai khoảng liền kề (end == start).
    """
    start_frame: int
    end_frame: int

    def __post_init__(self) -> None:
        if type(self.start_frame) is not int or type(self.start_frame) is bool:
            raise ValueError(f"start_frame phải là int (không phải bool), nhận được: {type(self.start_frame).__name__}")
        if type(self.end_frame) is not int or type(self.end_frame) is bool:
            raise ValueError(f"end_frame phải là int (không phải bool), nhận được: {type(self.end_frame).__name__}")

        if self.start_frame >= self.end_frame:
            raise ValueError(
                f"Khoảng frame không hợp lệ: start_frame ({self.start_frame}) "
                f"phải nhỏ hơn end_frame ({self.end_frame})."
            )

    @property
    def duration_frames(self) -> int:
        """Độ dài khoảng tính bằng số khung hình nguyên."""
        return self.end_frame - self.start_frame

    def contains(self, frame: int) -> bool:
        """Kiểm tra frame có nằm trong khoảng nửa mở [start_frame, end_frame) hay không."""
        if type(frame) is not int or type(frame) is bool:
            raise ValueError(f"frame kiểm tra phải là int (không phải bool), nhận được: {type(frame).__name__}")
        return self.start_frame <= frame < self.end_frame

    def overlaps(self, other: TimeRangeFrames) -> bool:
        """
        Kiểm tra hai khoảng có giao nhau (chồng lấn) hay không.
        Hai khoảng nửa mở [s1, e1) và [s2, e2) giao nhau khi và chỉ khi:
        s1 < e2 và s2 < e1.
        Nếu liền kề nhau (e1 == s2 hoặc e2 == s1) -> trả về False.
        """
        if not isinstance(other, TimeRangeFrames):
            raise TypeError(f"Đối tượng so sánh phải là TimeRangeFrames, nhận được: {type(other).__name__}")
        return (self.start_frame < other.end_frame) and (other.start_frame < self.end_frame)
