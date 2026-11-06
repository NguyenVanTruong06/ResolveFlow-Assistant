"""
ChunDVC / ResolveFlow Assistant - Auto-Update Service & Installer.
Kiểm tra bản cập nhật mới nhất từ GitHub Releases, tải và tự động cài đặt 1-click (tương tự cơ chế của Orca).
"""

import os
import sys
import re
import json
import shutil
import logging
import tarfile
import zipfile
import tempfile
import subprocess
import urllib.request
from typing import Optional, Dict, Any, Tuple
from PySide6.QtCore import QObject, QThread, Signal, QSettings

logger = logging.getLogger("ChunDVC.UpdateService")

CURRENT_VERSION = "1.0.10"
GITHUB_REPO = "NguyenVanTruong06/ResolveFlow-Assistant"
LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"


def parse_version_tuple(ver_str: str) -> Tuple[int, ...]:
    """Chuyển chuỗi version (vd 'v1.0.1' hoặc '1.0.2-beta1') thành tuple (1, 0, 2) để so sánh."""
    cleaned = re.sub(r'^[vV]', '', str(ver_str).strip())
    cleaned = cleaned.split('-')[0].split('+')[0]
    parts = cleaned.split('.')
    numeric = []
    for p in parts:
        digits = re.findall(r'\d+', p)
        if digits:
            numeric.append(int(digits[0]))
        else:
            break
    return tuple(numeric) if numeric else (0, 0, 0)


def check_for_updates_sync(timeout: int = 6) -> Optional[Dict[str, Any]]:
    """
    Kiểm tra đồng bộ xem có phiên bản mới hơn trên GitHub Releases hay không.
    Trả về dict thông tin nếu có bản mới, hoặc None nếu đã là bản mới nhất / mất mạng.
    """
    try:
        req = urllib.request.Request(
            LATEST_RELEASE_API,
            headers={
                "User-Agent": f"ChunDVC-ResolveFlow/{CURRENT_VERSION}",
                "Accept": "application/vnd.github.v3+json"
            }
        )
        with urllib.request.urlopen(req, timeout=timeout) as response:
            if response.status != 200:
                return None
            data = json.loads(response.read().decode("utf-8"))

        tag_name = data.get("tag_name", "")
        remote_ver = parse_version_tuple(tag_name)
        local_ver = parse_version_tuple(CURRENT_VERSION)

        has_update = remote_ver > local_ver

        assets_info = {}
        for asset in data.get("assets", []):
            name = asset.get("name", "").lower()
            url = asset.get("browser_download_url", "")
            size = asset.get("size", 0)
            if name.endswith(".deb"):
                assets_info["deb"] = url
                assets_info["deb_name"] = asset.get("name")
                assets_info["deb_size"] = size
            elif name.endswith(".tar.gz") and "linux" in name:
                assets_info["linux_tar"] = url
                assets_info["tar_name"] = asset.get("name")
                assets_info["tar_size"] = size
            elif name.endswith(".zip") and "windows" in name:
                assets_info["windows_zip"] = url
                assets_info["zip_name"] = asset.get("name")
                assets_info["zip_size"] = size

        return {
            "has_update": has_update,
            "current_version": CURRENT_VERSION,
            "latest_version": tag_name.lstrip("vV"),
            "tag_name": tag_name,
            "release_name": data.get("name") or tag_name,
            "html_url": data.get("html_url") or f"https://github.com/{GITHUB_REPO}/releases/tag/{tag_name}",
            "body": data.get("body", "").strip(),
            "published_at": data.get("published_at", ""),
            "assets": assets_info,
            "tarball_url": data.get("tarball_url"),
            "zipball_url": data.get("zipball_url")
        }
    except Exception as exc:
        logger.debug("Lỗi khi kiểm tra cập nhật từ GitHub: %s", exc)
        return None


class UpdateCheckWorker(QThread):
    """Worker chạy ngầm không làm đơ giao diện khi kiểm tra bản cập nhật mới."""
    update_found = Signal(dict)
    up_to_date = Signal(str)
    check_failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)

    def run(self):
        info = check_for_updates_sync(timeout=8)
        if info is None:
            self.check_failed.emit("Không thể kết nối tới máy chủ GitHub để kiểm tra cập nhật.")
            return

        if info.get("has_update"):
            self.update_found.emit(info)
        else:
            self.up_to_date.emit(f"Bạn đang dùng phiên bản mới nhất ({CURRENT_VERSION}).")


class UpdateDownloadAndInstallWorker(QThread):
    """
    Worker tải gói cập nhật từ GitHub và thực hiện cài đặt 1-Click:
    - Trên Linux: Tải .deb -> cài qua pkexec dpkg -i hoặc copy trực tiếp vào /opt/resolveflow-assistant
    - Trên Windows: Tải .zip -> giải nén và chuẩn bị script updater
    - Khi xong: phát tín hiệu install_finished(True, msg) để app sẵn sàng khởi động lại.
    """
    progress_changed = Signal(float, str)  # percent (0..100), label (e.g. "15.2 MB / 42.0 MB (36%)")
    status_changed = Signal(str)          # trạng thái (e.g. "Đang tải...", "Đang cài đặt...")
    install_finished = Signal(bool, str)  # success (True/False), message

    def __init__(self, update_info: dict, parent=None):
        super().__init__(parent)
        self.update_info = update_info
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        try:
            assets = self.update_info.get("assets", {})
            tag = self.update_info.get("tag_name", "")
            ver = self.update_info.get("latest_version", "")

            # Phân định hệ điều hành và tìm gói phù hợp
            if sys.platform.startswith("linux"):
                self._run_linux_update(assets, tag, ver)
            elif sys.platform.startswith("win"):
                self._run_windows_update(assets, tag, ver)
            else:
                self.install_finished.emit(False, "Hệ điều hành hiện tại chưa hỗ trợ 1-click update tự động.")
        except Exception as exc:
            logger.exception("Lỗi trong quá trình cập nhật tự động: %s", exc)
            self.install_finished.emit(False, f"Lỗi cập nhật: {exc}")

    def _download_file(self, url: str, dest_path: str, expected_size: int = 0) -> bool:
        """Tải file theo luồng chunk với báo cáo % tiến độ chính xác."""
        self.status_changed.emit("Đang kết nối tới máy chủ...")
        req = urllib.request.Request(
            url,
            headers={"User-Agent": f"ChunDVC-ResolveFlow/{CURRENT_VERSION}"}
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            total_bytes = int(resp.headers.get("content-length") or expected_size or 0)
            downloaded = 0
            chunk_size = 65536  # 64KB

            with open(dest_path, "wb") as f_out:
                while True:
                    if self._is_cancelled:
                        return False
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    f_out.write(chunk)
                    downloaded += len(chunk)

                    if total_bytes > 0:
                        percent = min(100.0, (downloaded / total_bytes) * 100.0)
                        down_mb = downloaded / (1024 * 1024)
                        tot_mb = total_bytes / (1024 * 1024)
                        msg = f"{down_mb:.1f} MB / {tot_mb:.1f} MB ({percent:.0f}%)"
                        self.progress_changed.emit(percent, msg)
                    else:
                        down_mb = downloaded / (1024 * 1024)
                        self.progress_changed.emit(50.0, f"{down_mb:.1f} MB đã tải")

        return True

    def _run_linux_update(self, assets: dict, tag: str, ver: str):
        deb_url = assets.get("deb")
        tar_url = assets.get("linux_tar")

        # 1. Ưu tiên tải gói .deb
        if deb_url:
            self.status_changed.emit("Đang tải gói cài đặt .deb...")
            tmp_deb = os.path.join(tempfile.gettempdir(), f"chundvc_{ver}_amd64.deb")
            if not self._download_file(deb_url, tmp_deb, assets.get("deb_size", 0)):
                self.install_finished.emit(False, "Đã hủy tải bản cập nhật.")
                return

            self.status_changed.emit("Đang cài đặt gói hệ thống...")
            self.progress_changed.emit(95.0, "Đang áp dụng bản cập nhật...")

            # Thử cài qua pkexec dpkg -i (chuẩn GUI prompt của Linux)
            installed_ok = False
            try:
                cmd = ["pkexec", "dpkg", "-i", tmp_deb]
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
                if proc.returncode == 0:
                    installed_ok = True
                else:
                    logger.debug("pkexec dpkg -i returned %s, stderr: %s", proc.returncode, proc.stderr)
            except Exception as e:
                logger.debug("pkexec dpkg -i không thực thi được: %s", e)

            # Xác thực kép: Nếu pkexec đã chạy và dpkg đã cài đặt gói lên hệ thống
            if not installed_ok:
                try:
                    chk = subprocess.run(
                        ["dpkg-query", "-W", "-f=${Version} ${Status}", "resolveflow-assistant"],
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
                    if chk.returncode == 0 and "install ok installed" in chk.stdout:
                        clean_ver = ver.lstrip("v")
                        if clean_ver in chk.stdout:
                            installed_ok = True
                except Exception as e:
                    logger.debug("dpkg-query check failed: %s", e)

            # Dự phòng: Nếu pkexec bị hủy hoặc không có quyền root, nhưng /opt/resolveflow-assistant ghi được
            opt_dir = "/opt/resolveflow-assistant"
            if not installed_ok and os.path.exists(opt_dir) and os.access(opt_dir, os.W_OK):
                try:
                    self.status_changed.emit("Đang giải nén trực tiếp vào thư mục ứng dụng...")
                    extract_tmp = os.path.join(tempfile.gettempdir(), "chundvc_deb_extracted")
                    shutil.rmtree(extract_tmp, ignore_errors=True)
                    os.makedirs(extract_tmp, exist_ok=True)
                    subprocess.run(["dpkg-deb", "-x", tmp_deb, extract_tmp], check=True)
                    extracted_opt = os.path.join(extract_tmp, "opt", "resolveflow-assistant")
                    if os.path.exists(extracted_opt):
                        for root_dir, dirs, files in os.walk(extracted_opt):
                            rel = os.path.relpath(root_dir, extracted_opt)
                            target_sub = os.path.join(opt_dir, rel) if rel != "." else opt_dir
                            os.makedirs(target_sub, exist_ok=True)
                            for f in files:
                                s = os.path.join(root_dir, f)
                                d = os.path.join(target_sub, f)
                                try:
                                    if os.path.exists(d):
                                        os.remove(d)
                                except Exception:
                                    pass
                                shutil.copy2(s, d)
                        installed_ok = True
                except Exception as e:
                    logger.warning("Giải nén trực tiếp .deb vào %s thất bại: %s", opt_dir, e)

            if installed_ok:
                self.progress_changed.emit(100.0, "Hoàn tất 100%")
                self.status_changed.emit(f"✔ Đã cập nhật thành công lên v{ver}!")
                self.install_finished.emit(True, f"Đã nâng cấp lên ChunDVC v{ver} thành công!")
            else:
                self.install_finished.emit(False, "Không thể ghi bản cài đặt vào hệ thống (Yêu cầu xác thực quyền quản trị).")
            return

        # 2. Dự phòng: Tải .tar.gz nếu chưa có .deb
        if tar_url:
            self.status_changed.emit("Đang tải gói lưu trữ Linux .tar.gz...")
            tmp_tar = os.path.join(tempfile.gettempdir(), f"chundvc_{ver}.tar.gz")
            if not self._download_file(tar_url, tmp_tar, assets.get("tar_size", 0)):
                self.install_finished.emit(False, "Đã hủy tải bản cập nhật.")
                return

            opt_dir = "/opt/resolveflow-assistant"
            if os.path.exists(opt_dir) and os.access(opt_dir, os.W_OK):
                self.status_changed.emit("Đang giải nén vào thư mục chương trình...")
                with tarfile.open(tmp_tar, "r:gz") as tar:
                    tar.extractall(path=tempfile.gettempdir())
                extracted_app = os.path.join(tempfile.gettempdir(), "ResolveFlow-Assistant")
                if os.path.exists(extracted_app):
                    for item in os.listdir(extracted_app):
                        s = os.path.join(extracted_app, item)
                        d = os.path.join(opt_dir, item)
                        if os.path.isdir(s):
                            shutil.copytree(s, d, dirs_exist_ok=True)
                        else:
                            shutil.copy2(s, d)
                    self.progress_changed.emit(100.0, "Hoàn tất 100%")
                    self.status_changed.emit(f"✔ Đã cập nhật thành công lên v{ver}!")
                    self.install_finished.emit(True, f"Đã cập nhật lên v{ver} thành công!")
                    return

            self.install_finished.emit(False, f"Gói cài đặt đã được tải về: {tmp_tar}")
            return

        # 3. Chế độ source clone dev (nếu là git repo)
        if not getattr(sys, "frozen", False) and os.path.isdir(os.path.join(os.getcwd(), ".git")):
            self.status_changed.emit("Đang đồng bộ mã nguồn Git (git pull)...")
            res = subprocess.run(["git", "pull", "origin", "main"], capture_output=True, text=True)
            if res.returncode == 0:
                self.progress_changed.emit(100.0, "Hoàn tất 100%")
                self.status_changed.emit(f"✔ Đã cập nhật mã nguồn lên v{ver}!")
                self.install_finished.emit(True, f"Đã đồng bộ git pull thành công lên v{ver}!")
                return
            else:
                self.install_finished.emit(False, f"Git pull lỗi: {res.stderr}")
                return

        self.install_finished.emit(False, "Bản phát hành này hiện chưa có gói cài đặt Linux tương thích.")

    def _run_windows_update(self, assets: dict, tag: str, ver: str):
        zip_url = assets.get("windows_zip")
        if not zip_url:
            self.install_finished.emit(False, "Chưa tìm thấy gói Windows .zip trong bản phát hành này.")
            return

        self.status_changed.emit("Đang tải bản cập nhật Windows...")
        tmp_zip = os.path.join(tempfile.gettempdir(), f"chundvc_{ver}_win.zip")
        if not self._download_file(zip_url, tmp_zip, assets.get("zip_size", 0)):
            self.install_finished.emit(False, "Đã hủy tải bản cập nhật.")
            return

        self.status_changed.emit("Đang giải nén gói cập nhật...")
        extract_dir = os.path.join(tempfile.gettempdir(), f"chundvc_{ver}_extracted")
        shutil.rmtree(extract_dir, ignore_errors=True)
        os.makedirs(extract_dir, exist_ok=True)

        with zipfile.ZipFile(tmp_zip, "r") as z:
            z.extractall(extract_dir)

        # Tạo script cập nhật Windows tự động khi thoát
        app_dir = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, 'frozen', False) else __file__))
        source_dir = os.path.join(extract_dir, "ResolveFlow-Assistant")
        if not os.path.exists(source_dir):
            source_dir = extract_dir

        updater_bat = os.path.join(tempfile.gettempdir(), "chundvc_apply_update.bat")
        bat_content = f"""@echo off
timeout /t 2 /nobreak > nul
robocopy "{source_dir}" "{app_dir}" /E /IS /IT /NP
start "" "{os.path.join(app_dir, 'ResolveFlow-Assistant.exe')}"
del "%~f0"
"""
        with open(updater_bat, "w", encoding="utf-8") as f:
            f.write(bat_content)

        self.progress_changed.emit(100.0, "Hoàn tất 100%")
        self.status_changed.emit(f"✔ Đã sẵn sàng cập nhật lên v{ver}!")
        self.install_finished.emit(True, f"Bản cập nhật v{ver} đã sẵn sàng. Khởi động lại để hoàn tất.")


def restart_app():
    """Khởi động lại ứng dụng một cách an toàn và dứt điểm."""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()

    try:
        if sys.platform.startswith("linux"):
            launcher = "/usr/local/bin/chundvc"
            if not os.path.exists(launcher):
                launcher = "/usr/local/bin/resolveflow-assistant"
            if not os.path.exists(launcher):
                launcher = "/opt/resolveflow-assistant/launch_resolveflow.sh"

            if os.path.exists(launcher):
                subprocess.Popen([launcher])
            elif getattr(sys, 'frozen', False):
                subprocess.Popen([sys.executable] + sys.argv[1:])
            else:
                subprocess.Popen([sys.executable] + sys.argv)
        elif sys.platform.startswith("win"):
            updater_bat = os.path.join(tempfile.gettempdir(), "chundvc_apply_update.bat")
            if os.path.exists(updater_bat):
                subprocess.Popen(["cmd.exe", "/c", updater_bat], shell=False)
            elif getattr(sys, 'frozen', False):
                subprocess.Popen([sys.executable] + sys.argv[1:])
            else:
                subprocess.Popen([sys.executable] + sys.argv)
        else:
            subprocess.Popen([sys.executable] + sys.argv)
    except Exception as exc:
        logger.error("Lỗi khi khởi động lại ứng dụng: %s", exc)

    if app:
        app.quit()
    sys.exit(0)


class AutoUpdateController(QObject):
    """
    Quản lý luồng kiểm tra cập nhật ngầm khi khởi động ứng dụng:
    - Chạy âm thầm không làm gián đoạn người dùng.
    - Nếu có bản mới, hiển thị Toast nổi ở góc dưới bên phải ("như Orca").
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self._checker: Optional[UpdateCheckWorker] = None
        self._toast = None

    def start_silent_check(self):
        """Khởi động kiểm tra ngầm."""
        self._checker = UpdateCheckWorker(self)
        self._checker.update_found.connect(self._on_update_found)
        self._checker.start()

    def _on_update_found(self, info: dict):
        latest = info.get("latest_version", "")
        settings = QSettings("ChunDVC", "ResolveFlow")
        ignored = settings.value("updates/ignored_version", "")
        if ignored == latest:
            logger.info("Phiên bản v%s đã được người dùng chọn bỏ qua.", latest)
            return

        try:
            from src.ui.widgets.update_toast import UpdateToastWidget
            self._toast = UpdateToastWidget(info)
            self._toast.show_animated()
        except Exception as exc:
            logger.warning("Không thể hiển thị Update Toast: %s", exc)
