"""
Cloud Downloader & Google Drive Integration Engine (Ultra-Resilient Edition).
Hỗ trợ phân tích đường dẫn, xác thực và tải ngầm video/thư mục từ Google Drive.
Tự động bypass trang cảnh báo virus cho file lớn (>100MB), chống lỗi ghi nhầm HTML vào MP4,
tự động retry chống 429 Quota Exceeded, bảo toàn cấu trúc thư mục phân tầng và nạp trực tiếp vào ResolveFlow.
"""

import os
import re
import time
import shutil
import urllib.request
import urllib.parse
from http.cookiejar import CookieJar
from typing import Optional, Callable, Dict, Any, Tuple, List
from pydantic import BaseModel, Field


class CloudResourceInfo(BaseModel):
    """Thông tin trích xuất từ đường dẫn Cloud."""
    url: str
    resource_id: str
    resource_type: str  # "file" hoặc "folder"
    service: str = "google_drive"


class DownloadProgress(BaseModel):
    """Tiến trình tải tệp."""
    filename: str
    downloaded_bytes: int
    total_bytes: int
    percent: float
    speed_mb_s: float
    status: str = "downloading"  # downloading, completed, error


def parse_google_drive_url(url: str) -> Optional[CloudResourceInfo]:
    """
    Phân tích và trích xuất Resource ID & Loại (File/Folder) từ URL Google Drive.
    Hỗ trợ các định dạng:
    - https://drive.google.com/file/d/<ID>/view?usp=sharing
    - https://drive.google.com/drive/folders/<ID>?usp=sharing
    - https://drive.google.com/drive/u/0/folders/<ID>
    - https://drive.google.com/open?id=<ID>
    - https://drive.google.com/uc?id=<ID>
    - ID trực tiếp
    """
    if not url or not isinstance(url, str):
        return None

    clean_url = url.strip()

    # 1. Folder URL: /folders/<ID>
    folder_match = re.search(r"/folders/([a-zA-Z0-9_-]+)", clean_url)
    if folder_match:
        return CloudResourceInfo(
            url=clean_url,
            resource_id=folder_match.group(1),
            resource_type="folder",
            service="google_drive"
        )

    # 2. File URL: /file/d/<ID>
    file_match = re.search(r"/file/d/([a-zA-Z0-9_-]+)", clean_url)
    if file_match:
        return CloudResourceInfo(
            url=clean_url,
            resource_id=file_match.group(1),
            resource_type="file",
            service="google_drive"
        )

    # 3. Parameter id: id=<ID>
    id_match = re.search(r"[?&]id=([a-zA-Z0-9_-]+)", clean_url)
    if id_match:
        res_id = id_match.group(1)
        res_type = "folder" if "folder" in clean_url.lower() else "file"
        return CloudResourceInfo(
            url=clean_url,
            resource_id=res_id,
            resource_type=res_type,
            service="google_drive"
        )

    # 4. Nếu người dùng dán trực tiếp ID thuần (chuỗi alphanumeric dài >= 25)
    if re.match(r"^[a-zA-Z0-9_-]{25,}$", clean_url):
        return CloudResourceInfo(
            url=clean_url,
            resource_id=clean_url,
            resource_type="file",
            service="google_drive"
        )

    return None


class GoogleDriveDownloader:
    """
    Bộ động cơ tải tệp/thư mục từ Google Drive sử dụng streaming chunk của Python Standard Library
    kết hợp đa cơ chế Fallback (gdown, direct streaming bypass, token extraction) và kiểm tra toàn vẹn file.
    """

    USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

    @classmethod
    def _parse_form_inputs(cls, html_text: str) -> Tuple[Dict[str, str], Optional[str]]:
        """Bóc tách toàn bộ input fields và action URL từ form HTML (kể cả virus warning page)."""
        inputs = re.findall(r'<input[^>]*name=["\']([^"\']+)["\'][^>]*value=["\']([^"\']*)["\']', html_text, re.IGNORECASE)
        inputs += re.findall(r'<input[^>]*value=["\']([^"\']*)["\'][^>]*name=["\']([^"\']+)["\']', html_text, re.IGNORECASE)
        
        params: Dict[str, str] = {}
        for item in inputs:
            k = item[0] if not item[0].startswith("http") else item[1]
            v = item[1] if not item[0].startswith("http") else item[0]
            params[k] = v

        action_match = re.search(r'<form[^>]*action=["\']([^"\']+)["\']', html_text, re.IGNORECASE)
        action_url = action_match.group(1) if action_match else None
        return params, action_url

    @classmethod
    def _extract_download_link_from_html(cls, html_text: str, file_id: str) -> Optional[str]:
        """
        Trích xuất link tải trực tiếp từ các biến thể trang cảnh báo virus của Google Drive
        (hỗ trợ thẻ <a>, href uc-download-link, download-button, window.location, v.v.).
        """
        # 1. Thẻ <a> với href chứa download / confirm
        a_matches = re.findall(r'<a[^>]*href=["\']([^"\']+)["\']', html_text, re.IGNORECASE)
        for link in a_matches:
            unescaped = link.replace("&amp;", "&")
            if "drive.usercontent.google.com/download" in unescaped or ("confirm=" in unescaped and file_id in unescaped):
                if unescaped.startswith("/"):
                    return f"https://drive.google.com{unescaped}"
                return unescaped

        # 2. Link JavaScript hoặc window.location
        js_match = re.search(r'["\'](https://drive\.usercontent\.google\.com/download\?[^"\']+)["\']', html_text)
        if js_match:
            return js_match.group(1).replace("&amp;", "&")

        # 3. Form action kết hợp params
        params, action_url = cls._parse_form_inputs(html_text)
        if action_url or params:
            base = action_url or "https://drive.usercontent.google.com/download"
            if "id" not in params:
                params["id"] = file_id
            if "export" not in params:
                params["export"] = "download"
            if "confirm" not in params:
                params["confirm"] = "t"
            query = urllib.parse.urlencode(params)
            return f"{base}?{query}" if "?" not in base else f"{base}&{query}"

        return None

    @classmethod
    def _extract_filename_from_headers(cls, headers: Any, default_id: str) -> str:
        """Trích xuất tên file thực từ Content-Disposition header."""
        cd = headers.get("Content-Disposition", "")
        if cd:
            fn_utf8 = re.search(r"filename\*=UTF-8''([^;]+)", cd, re.IGNORECASE)
            if fn_utf8:
                return urllib.parse.unquote(fn_utf8.group(1))
            fn_match = re.search(r'filename="?([^";]+)"?', cd)
            if fn_match:
                return fn_match.group(1).strip()
        return f"GoogleDrive_{default_id}.mp4"

    @classmethod
    def _is_valid_binary_file(cls, filepath: str) -> bool:
        """Kiểm tra xem file tải về có phải là file nhị phân hợp lệ không (tránh file lỗi HTML)."""
        if not os.path.exists(filepath) or os.path.getsize(filepath) == 0:
            return False
        # Nếu file nhỏ hơn 20KB và bắt đầu bằng HTML tag -> file lỗi
        size = os.path.getsize(filepath)
        if size < 30 * 1024:
            try:
                with open(filepath, "rb") as f:
                    header = f.read(512)
                    header_str = header.decode("utf-8", errors="ignore").lower()
                    if "<!doctype html" in header_str or "<html" in header_str or "quota exceeded" in header_str:
                        return False
            except Exception:
                pass
        return True

    @classmethod
    def download_file(
        cls,
        file_id: str,
        output_dir: str,
        custom_filename: Optional[str] = None,
        progress_callback: Optional[Callable[[DownloadProgress], None]] = None,
        cancel_checker: Optional[Callable[[], bool]] = None,
        max_retries: int = 3
    ) -> str:
        """
        Tải một tệp từ Google Drive về output_dir với streaming chunk, tự động vượt qua trang cảnh báo virus,
        chống ghi nhầm HTML vào file MP4, tự động retry khi gặp lỗi tạm thời.
        """
        os.makedirs(output_dir, exist_ok=True)

        for attempt in range(1, max_retries + 1):
            if cancel_checker and cancel_checker():
                raise InterruptedError("Tiến trình tải Google Drive đã bị hủy bởi người dùng.")

            try:
                cookie_jar = CookieJar()
                opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))

                # Thử endpoint chuẩn
                base_url = f"https://drive.google.com/uc?export=download&id={file_id}"
                req = urllib.request.Request(base_url, headers={"User-Agent": cls.USER_AGENT})

                resp = opener.open(req, timeout=35)
                content_type = resp.headers.get("Content-Type", "")

                # Nếu gặp trang HTML cảnh báo virus cho file lớn
                if "text/html" in content_type:
                    html_text = resp.read().decode("utf-8", errors="ignore")
                    resp.close()

                    # Kiểm tra các thông báo lỗi nghiêm trọng
                    if "Quota exceeded" in html_text or "too many users" in html_text.lower():
                        raise ValueError("Google Drive báo lỗi: Vượt quá hạn ngạch tải (Quota exceeded). File này đang có quá nhiều lượt tải.")
                    if "Access denied" in html_text or "You need access" in html_text:
                        raise ValueError("Không có quyền truy cập file này. Vui lòng kiểm tra quyền chia sẻ công khai.")

                    # Trích xuất link xác nhận tải
                    direct_url = cls._extract_download_link_from_html(html_text, file_id)
                    if not direct_url:
                        direct_url = f"https://drive.usercontent.google.com/download?id={file_id}&export=download&confirm=t"

                    confirm_req = urllib.request.Request(direct_url, headers={"User-Agent": cls.USER_AGENT})
                    resp = opener.open(confirm_req, timeout=45)
                    content_type_2 = resp.headers.get("Content-Type", "")

                    # Nếu sau khi xác nhận vẫn trả về HTML lỗi -> báo lỗi rõ ràng
                    if "text/html" in content_type_2:
                        html_text_2 = resp.read().decode("utf-8", errors="ignore")
                        resp.close()
                        if "Quota exceeded" in html_text_2 or "too many users" in html_text_2.lower():
                            raise ValueError("Google Drive báo lỗi vượt hạn ngạch tải ẩn danh (Quota exceeded).")
                        # Thử Fallback qua gdown nếu có
                        raise RuntimeError(f"Google Drive yêu cầu xác thực web hoặc giới hạn tải: {html_text_2[:200]}")

                # Xác định tên file
                target_filename = custom_filename or cls._extract_filename_from_headers(resp.headers, file_id)
                target_filename = re.sub(r'[\\/*?:"<>|]', "_", target_filename)
                target_path = os.path.join(output_dir, target_filename)
                part_path = target_path + ".part"

                total_length_header = resp.headers.get("Content-Length")
                total_bytes = int(total_length_header) if total_length_header and total_length_header.isdigit() else 0

                downloaded_bytes = 0
                chunk_size = 1024 * 1024  # 1MB chunk
                start_time = time.time()
                last_report_time = start_time

                try:
                    with open(part_path, "wb") as out_file:
                        while True:
                            if cancel_checker and cancel_checker():
                                raise InterruptedError("Tiến trình tải Google Drive đã bị hủy bởi người dùng.")

                            chunk = resp.read(chunk_size)
                            if not chunk:
                                break

                            out_file.write(chunk)
                            downloaded_bytes += len(chunk)

                            now = time.time()
                            if progress_callback and (now - last_report_time >= 0.25 or downloaded_bytes == total_bytes):
                                elapsed = max(now - start_time, 0.001)
                                speed_mb = (downloaded_bytes / (1024 * 1024)) / elapsed
                                pct = (downloaded_bytes / total_bytes * 100.0) if total_bytes > 0 else 0.0
                                progress_callback(DownloadProgress(
                                    filename=target_filename,
                                    downloaded_bytes=downloaded_bytes,
                                    total_bytes=total_bytes,
                                    percent=pct,
                                    speed_mb_s=speed_mb,
                                    status="downloading"
                                ))
                                last_report_time = now
                finally:
                    resp.close()

                # Kiểm tra tính toàn vẹn của tệp .part trước khi đổi tên
                if not cls._is_valid_binary_file(part_path):
                    if os.path.exists(part_path):
                        os.remove(part_path)
                    raise ValueError("Tệp tải về không hợp lệ hoặc bị lỗi máy chủ Google Drive.")

                if os.path.exists(target_path):
                    try:
                        os.remove(target_path)
                    except Exception:
                        pass
                os.rename(part_path, target_path)

                if progress_callback:
                    progress_callback(DownloadProgress(
                        filename=target_filename,
                        downloaded_bytes=downloaded_bytes,
                        total_bytes=downloaded_bytes if total_bytes <= 0 else total_bytes,
                        percent=100.0,
                        speed_mb_s=0.0,
                        status="completed"
                    ))

                return target_path

            except InterruptedError:
                raise
            except Exception as e:
                # Dọn dẹp file part nếu có
                part_path = os.path.join(output_dir, (custom_filename or f"GoogleDrive_{file_id}.mp4") + ".part")
                if os.path.exists(part_path):
                    try:
                        os.remove(part_path)
                    except Exception:
                        pass

                if attempt < max_retries:
                    time.sleep(attempt * 1.5)  # Backoff retry
                    continue

                # Fallback cuối cùng: Thử gdown nếu urllib thất bại
                try:
                    import gdown
                    fallback_fn = custom_filename or f"GoogleDrive_{file_id}.mp4"
                    fallback_target = os.path.join(output_dir, fallback_fn)
                    dl_res = gdown.download(id=file_id, output=fallback_target, quiet=True, fuzzy=True)
                    if dl_res and cls._is_valid_binary_file(fallback_target):
                        if progress_callback:
                            progress_callback(DownloadProgress(
                                filename=fallback_fn,
                                downloaded_bytes=os.path.getsize(fallback_target),
                                total_bytes=os.path.getsize(fallback_target),
                                percent=100.0,
                                speed_mb_s=0.0,
                                status="completed"
                            ))
                        return fallback_target
                except Exception:
                    pass

                raise ValueError(f"Không thể tải tệp Google Drive (ID: {file_id}): {str(e)}")

        raise ValueError(f"Tải tệp thất bại sau {max_retries} lần thử.")

    @classmethod
    def download_folder(
        cls,
        folder_id: str,
        output_dir: str,
        progress_callback: Optional[Callable[[DownloadProgress], None]] = None,
        cancel_checker: Optional[Callable[[], bool]] = None
    ) -> str:
        """
        Tải toàn bộ thư mục từ Google Drive về output_dir.
        Tự động bóc tách cây thư mục qua gdown (skip_download=True), lọc tệp trùng,
        và tải từng file bằng bộ động cơ streaming bypass virus warning trực tiếp.
        """
        os.makedirs(output_dir, exist_ok=True)
        try:
            import gdown
            folder_url = f"https://drive.google.com/drive/folders/{folder_id}"
            
            if progress_callback:
                progress_callback(DownloadProgress(
                    filename=f"Đang quét cấu trúc thư mục Google Drive...",
                    downloaded_bytes=0,
                    total_bytes=0,
                    percent=5.0,
                    speed_mb_s=0.0,
                    status="downloading"
                ))

            # Lấy danh sách toàn bộ file trong thư mục
            file_list = []
            try:
                file_list = gdown.download_folder(
                    id=folder_id,
                    output=output_dir,
                    quiet=True,
                    use_cookies=False,
                    skip_download=True
                )
            except Exception:
                try:
                    file_list = gdown.download_folder(
                        url=folder_url,
                        output=output_dir,
                        quiet=True,
                        use_cookies=False,
                        skip_download=True
                    )
                except Exception:
                    file_list = []

            # Nếu lấy được danh sách file từ cây thư mục
            if file_list and isinstance(file_list, list) and len(file_list) > 0 and hasattr(file_list[0], "id"):
                total_files = len(file_list)
                failed_items: List[Tuple[str, str]] = []

                for idx, file_item in enumerate(file_list):
                    if cancel_checker and cancel_checker():
                        raise InterruptedError("Tiến trình tải Google Drive đã bị hủy bởi người dùng.")

                    target_file_path = file_item.local_path
                    target_parent_dir = os.path.dirname(target_file_path)
                    os.makedirs(target_parent_dir, exist_ok=True)
                    target_name = os.path.basename(target_file_path)

                    # Bỏ qua nếu file đã tồn tại và có kích thước hợp lệ
                    if cls._is_valid_binary_file(target_file_path):
                        if progress_callback:
                            progress_callback(DownloadProgress(
                                filename=f"[{idx+1}/{total_files}] Đã có sẵn: {target_name}",
                                downloaded_bytes=idx + 1,
                                total_bytes=total_files,
                                percent=round(((idx + 1) / total_files) * 100.0, 1),
                                speed_mb_s=0.0,
                                status="downloading"
                            ))
                        continue

                    # Tạo callback cho từng file lồng vào tiến trình folder
                    def make_file_cb(file_idx: int, filename_curr: str):
                        def _cb(p: DownloadProgress):
                            if progress_callback:
                                overall_pct = round(((file_idx + (p.percent / 100.0)) / total_files) * 100.0, 1)
                                progress_callback(DownloadProgress(
                                    filename=f"[{file_idx+1}/{total_files}] {filename_curr}",
                                    downloaded_bytes=p.downloaded_bytes,
                                    total_bytes=p.total_bytes,
                                    percent=overall_pct,
                                    speed_mb_s=p.speed_mb_s,
                                    status="downloading"
                                ))
                        return _cb

                    try:
                        cls.download_file(
                            file_id=file_item.id,
                            output_dir=target_parent_dir,
                            custom_filename=target_name,
                            progress_callback=make_file_cb(idx, target_name),
                            cancel_checker=cancel_checker
                        )
                    except Exception as fe:
                        err_msg = str(fe)
                        if "InterruptedError" in type(fe).__name__:
                            raise
                        failed_items.append((target_name, err_msg))
                        print(f"Lỗi tải file {target_name} ({file_item.id}): {err_msg}")

                if progress_callback:
                    status_text = f"Hoàn tất tải {total_files - len(failed_items)}/{total_files} tệp từ Google Drive!"
                    if failed_items:
                        status_text += f" ({len(failed_items)} tệp bị lỗi hạn ngạch)"
                    progress_callback(DownloadProgress(
                        filename=status_text,
                        downloaded_bytes=total_files - len(failed_items),
                        total_bytes=total_files,
                        percent=100.0,
                        speed_mb_s=0.0,
                        status="completed"
                    ))
                return output_dir

            # Fallback nếu gdown không trả về list qua skip_download
            downloaded = gdown.download_folder(
                id=folder_id,
                output=output_dir,
                quiet=False,
                use_cookies=False
            )

            if not downloaded:
                downloaded = gdown.download_folder(
                    url=folder_url,
                    output=output_dir,
                    quiet=False,
                    use_cookies=False
                )

            if not downloaded:
                raise ValueError(
                    "Không tải được tệp nào từ thư mục Google Drive này.\n"
                    "👉 Hãy đảm bảo bạn đã bật quyền chia sẻ: 'Bất kỳ ai có đường liên kết đều có thể xem'."
                )

            if progress_callback:
                progress_callback(DownloadProgress(
                    filename=f"GoogleDrive_Folder_{folder_id}",
                    downloaded_bytes=100,
                    total_bytes=100,
                    percent=100.0,
                    speed_mb_s=0.0,
                    status="completed"
                ))

            return output_dir

        except Exception as e:
            try:
                from src.core.folder_scanner import scan_project_folder
                proj = scan_project_folder(output_dir)
                if proj and proj.all_video_paths:
                    if progress_callback:
                        progress_callback(DownloadProgress(
                            filename=f"GoogleDrive_Folder_{folder_id}",
                            downloaded_bytes=len(proj.all_video_paths),
                            total_bytes=len(proj.all_video_paths),
                            percent=100.0,
                            speed_mb_s=0.0,
                            status="completed"
                        ))
                    return output_dir
            except Exception:
                pass

            err_str = str(e)
            if "Permission denied" in err_str or "Cannot retrieve" in err_str or "500" in err_str or "many accesses" in err_str:
                raise ValueError(
                    f"Google Drive tạm thời giới hạn tải ({err_str}).\n"
                    "👉 Nguyên nhân: Google Drive khóa hạn ngạch tải ẩn danh khi tải quá nhiều GB liên tục.\n"
                    "👉 Giải pháp: Các video đã tải trước đó vẫn an toàn tại thư mục đích. Bạn có thể mở thư mục đó để dựng ngay, hoặc tạo bản sao trên Drive để tải nốt các clip còn lại."
                )
            raise
