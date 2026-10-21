import json
import os
import re
import subprocess
import sys
import time
import uuid
from typing import Optional, Dict, Any, List, Tuple, Literal, Union
from pydantic import BaseModel, Field

class SubtitleConfig(BaseModel):
    """
    Lớp cấu hình phong cách chữ của phụ đề sử dụng Pydantic.
    """
    split_mode: Literal["characters", "words"] = Field(
        default="characters",
        description="Chế độ ngắt câu: 'characters' (theo số ký tự) hoặc 'words' (theo số từ)"
    )
    split_limit: int = Field(
        default=42, ge=1, le=200,
        description="Giới hạn tối đa (số ký tự hoặc số từ) trên một dòng trước khi ngắt câu"
    )
    max_chars_per_line: int = Field(
        default=42, ge=10, le=80,
        description="Số lượng ký tự tối đa trên một dòng (tương thích ngược)"
    )
    font_name: str = Field(
        default="Arial",
        description="Tên phông chữ hiển thị (ví dụ: Arial, Calibri...)"
    )
    font_size: int = Field(
        default=48, ge=10, le=200,
        description="Kích thước chữ"
    )
    color_hex: str = Field(
        default="#FFFFFF",
        description="Mã màu chữ dạng Hex (mặc định là Trắng)"
    )
    shadow_enabled: bool = Field(
        default=True,
        description="Bật hiệu ứng đổ bóng cho chữ để tăng độ tương phản đọc"
    )

class ResolveAutomation:
    """
    Lớp điều khiển tương tác tự động hóa với DaVinci Resolve thông qua cổng fusionscript API.
    """
    def __init__(self):
        self.resolve = None
        self.project_manager = None
        self.current_project = None

    last_connect_error: str = ""

    @staticmethod
    def _locate_fusionscript() -> Optional[str]:
        """
        Tìm fusionscript library: biến môi trường RESOLVE_SCRIPT_LIB, thư mục cài mặc định
        (Windows, Linux /opt/resolve, macOS), hoặc thư mục của tiến trình Resolve đang chạy.
        """
        from src.core.resolve_bridge import setup_resolve_environment
        _, lib_path = setup_resolve_environment()
        if lib_path and os.path.exists(lib_path):
            return lib_path

        candidates = []
        env = os.environ.get("RESOLVE_SCRIPT_LIB")
        if env:
            candidates.append(env)

        if sys.platform.startswith("linux"):
            candidates.extend([
                "/opt/resolve/libs/Fusion/fusionscript.so",
                "/opt/resolve/libs/Fusion/libfusionscript.so",
                "/opt/resolve/libs/fusionscript.so",
                "/opt/resolve/libs/libfusionscript.so",
            ])
        elif sys.platform.startswith("darwin"):
            candidates.append("/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.dylib")
        else:
            prog_files = os.environ.get("PROGRAMFILES", r"C:\Program Files")
            candidates.append(os.path.join(prog_files, "Blackmagic Design", "DaVinci Resolve", "fusionscript.dll"))

        try:
            import subprocess
            if sys.platform.startswith("win"):
                out = subprocess.run(
                    ["powershell.exe", "-NoProfile", "-Command",
                     "(Get-Process Resolve -ErrorAction SilentlyContinue | Select-Object -First 1).Path"],
                    capture_output=True, text=True, timeout=10
                ).stdout.strip()
                if out:
                    candidates.append(os.path.join(os.path.dirname(out), "fusionscript.dll"))
            elif sys.platform.startswith("linux"):
                out = subprocess.run(
                    ["pgrep", "-f", "/opt/resolve/bin/resolve"],
                    capture_output=True, text=True, timeout=5
                ).stdout.strip()
                if out:
                    candidates.append("/opt/resolve/libs/Fusion/fusionscript.so")
                    candidates.append("/opt/resolve/libs/Fusion/libfusionscript.so")
        except Exception:
            pass
        return next((c for c in candidates if c and os.path.exists(c)), None)

    def connect(self, timeout: float = 2.0) -> bool:
        """
        Kết nối tới ứng dụng DaVinci Resolve đang chạy.
        Tự động đăng ký đường dẫn Modules của Resolve vào sys.path trên từng hệ điều hành.
        Được bảo vệ bằng cơ chế kiểm tra tiến trình nhanh & timeout không chặn UI.

        Returns:
            bool: True nếu kết nối thành công.
        """
        import concurrent.futures
        from src.core.resolve_bridge import load_resolve_api
        self.last_connect_error = ""

        # Kiểm tra nhanh xem tiến trình DaVinci Resolve có đang tồn tại trên OS không
        if sys.platform.startswith("linux"):
            try:
                import subprocess
                p_check = subprocess.run(["pgrep", "-f", "resolve"], capture_output=True, text=True, timeout=0.5)
                if p_check.returncode != 0:
                    self.last_connect_error = "Tiến trình DaVinci Resolve chưa được bật trên hệ thống."
                    return False
            except Exception:
                pass

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(load_resolve_api)
                resolve_app, status_msg = future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            self.last_connect_error = f"Hết thời gian chờ kết nối DaVinci Resolve ({timeout}s). Ứng dụng có thể đang bận."
            return False
        except Exception as e:
            self.last_connect_error = f"Lỗi trong quá trình kết nối API Resolve: {e}"
            return False

        if resolve_app:
            self.resolve = resolve_app
            try:
                self.project_manager = self.resolve.GetProjectManager()
                self.current_project = self.project_manager.GetCurrentProject()
                return True
            except Exception as e:
                self.last_connect_error = f"Lỗi lấy ProjectManager từ Resolve: {e}"
                return False

        self.last_connect_error = status_msg
        return False

    def ensure_resolve_running(self, log_callback: Optional[Any] = None) -> bool:
        """
        Kiểm tra xem DaVinci Resolve có đang chạy không.
        Nếu không, tự động khởi chạy ứng dụng Resolve.exe từ đường dẫn mặc định trên Windows.

        Args:
            log_callback (callable, optional): Hàm ghi log.

        Returns:
            bool: True nếu kết nối thành công (sau khi đã khởi chạy hoặc nếu đã chạy sẵn).
        """
        def log(msg: str):
            if log_callback:
                log_callback(msg)
            else:
                print(msg)

        if self.connect():
            return True

        if sys.platform.startswith("linux"):
            candidates_exe = ["/opt/resolve/bin/resolve"]
            import shutil
            which_res = shutil.which("resolve")
            if which_res and which_res not in candidates_exe:
                candidates_exe.append(which_res)
        elif sys.platform.startswith("darwin"):
            candidates_exe = [
                "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/MacOS/Resolve"
            ]
        else:
            candidates_exe = [
                r"C:\Program Files\Blackmagic Design\DaVinci Resolve\Resolve.exe"
            ]

        resolve_exe = next((exe for exe in candidates_exe if os.path.exists(exe)), None)
        if resolve_exe:
            log(f" 🔍 Không tìm thấy DaVinci Resolve đang chạy. Đang tự động mở: {resolve_exe}...")
            import subprocess
            import time
            try:
                subprocess.Popen([resolve_exe])
                log(" 🚀 Đang khởi động DaVinci Resolve... Vui lòng chờ vài giây và mở một dự án (Project).")
                
                # Thử kết nối lại trong vòng 20 giây (khởi động cold start trên Linux/SSD có thể mất 10-15s)
                for i in range(20):
                    time.sleep(1)
                    if self.connect():
                        log(" ✔ Đã kết nối thành công tới DaVinci Resolve!")
                        return True
                log(" ⚠ DaVinci Resolve đang tải. Vui lòng đảm bảo bạn đã mở một Project cụ thể.")
            except Exception as e:
                log(f" ❌ Không thể khởi chạy DaVinci Resolve tự động: {str(e)}")
        else:
            log(" ❌ Không tìm thấy đường dẫn cài đặt DaVinci Resolve (kiểm tra /opt/resolve/bin/resolve hoặc C:\\Program Files\\...).")
        return False

    def is_connected(self) -> bool:
        """Kiểm tra xem kết nối tới DaVinci Resolve và Project hiện tại có hợp lệ hay không."""
        return bool(self.resolve and self.current_project)

    def get_media_pool_clips(self, folder=None) -> List[Any]:
        """Lấy toàn bộ các clips trong Media Pool (đệ quy qua các thư mục)."""
        if not self.is_connected():
            if not self.connect():
                return []
        
        media_pool = self.current_project.GetMediaPool()
        if not media_pool:
            return []

        target_folder = folder if folder else media_pool.GetRootFolder()
        if not target_folder:
            return []

        clips = []
        try:
            folder_clips = target_folder.GetClipList() or []
            clips.extend(folder_clips)
            subfolders = target_folder.GetSubFolderList() or []
            for sub in subfolders:
                clips.extend(self.get_media_pool_clips(folder=sub))
        except Exception:
            pass
        return clips

    def get_media_pool_file_paths(self) -> List[str]:
        """Lấy danh sách tất cả đường dẫn tệp file hoặc tên clip trong Media Pool."""
        clips = self.get_media_pool_clips()
        paths = []
        for clip in clips:
            try:
                fp = clip.GetClipProperty("File Path")
                if fp:
                    paths.append(os.path.normcase(os.path.abspath(fp)))
                else:
                    name = clip.GetName()
                    if name:
                        paths.append(name.lower())
            except Exception:
                pass
        return paths

    def check_clips_in_media_pool(self, paths: List[str]) -> Dict[str, bool]:
        """Kiểm tra từng tệp trong danh sách đã có trong Media Pool hay chưa."""
        pool_paths = self.get_media_pool_file_paths()
        result = {}
        for p in paths:
            norm_p = os.path.normcase(os.path.abspath(p))
            fname = os.path.basename(p).lower()
            exists = (norm_p in pool_paths) or any(fname == os.path.basename(pp) or fname in pp for pp in pool_paths)
            result[p] = exists
        return result

    def import_media_to_media_pool(self, paths: List[str], log_callback: Optional[Any] = None) -> bool:
        """Tự động nạp danh sách tệp video vào Media Pool của DaVinci Resolve."""
        def log(msg: str):
            if log_callback:
                log_callback(msg)
            else:
                print(msg)

        if not self.is_connected():
            if not self.connect():
                return False

        valid_paths = []
        for p in paths:
            if not p:
                continue
            abs_p = os.path.abspath(p)
            if os.path.exists(abs_p):
                valid_paths.append(abs_p)
                # Trên Linux: nếu tồn tại symlink Cac_O_Dia trong Videos, thêm phiên bản đường dẫn qua mount của DaVinci
                if sys.platform.startswith("linux") and abs_p.startswith("/media/vanntruongg/"):
                    sym_p = abs_p.replace("/media/vanntruongg/", "/home/vanntruongg/Videos/Cac_O_Dia/")
                    if os.path.exists(sym_p) and sym_p not in valid_paths:
                        valid_paths.append(sym_p)

        if not valid_paths:
            return False

        media_pool = self.current_project.GetMediaPool()
        media_storage = self.resolve.GetMediaStorage() if hasattr(self.resolve, "GetMediaStorage") else None

        imported = False
        if media_pool:
            try:
                res = media_pool.ImportMedia(valid_paths)
                if res:
                    imported = True
            except Exception as e:
                log(f" ℹ MediaPool ImportMedia: {str(e)}")

        if not imported and media_storage:
            try:
                res = media_storage.AddItemListToMediaPool(valid_paths)
                if res:
                    imported = True
            except Exception as e:
                log(f" ℹ MediaStorage AddItemList: {str(e)}")

        if imported:
            log(f" ✔ Đã tự động nạp {len(valid_paths)} tệp video nguồn vào Media Pool của Resolve!")
        return imported

    def auto_detect_video_paths(self) -> List[str]:
        """
        Tự động phát hiện danh sách đường dẫn các tệp video đang hoạt động trong DaVinci Resolve.
        Thử theo thứ tự ưu tiên:
        1. Lấy tất cả clip đang được chọn trong Media Pool (GetSelectedClips).
        2. Lấy toàn bộ clip trên track Video 1 của Timeline hiện tại.
        3. Lấy clip dưới Playhead trên Timeline hiện tại.

        Returns:
            List[str]: Danh sách các đường dẫn tệp video nguồn hợp lệ.
        """
        if not self.resolve or not self.current_project:
            if not self.connect():
                return []

        paths = []
        # 1. Thử lấy từ các clip đang chọn trong Media Pool
        try:
            media_pool = self.current_project.GetMediaPool()
            if media_pool:
                selected_clips = media_pool.GetSelectedClips()
                if selected_clips:
                    for clip in selected_clips:
                        file_path = clip.GetClipProperty("File Path")
                        if file_path and os.path.exists(file_path) and file_path not in paths:
                            paths.append(file_path)
                    if paths:
                        return paths
        except Exception:
            pass

        # 2. Thử lấy toàn bộ clip trên track Video 1 của Timeline hiện tại
        try:
            timeline = self.get_active_timeline()
            if timeline:
                video_items = timeline.GetItemListInTrack("video", 1)
                if video_items:
                    for item in video_items:
                        media_pool_item = item.GetMediaPoolItem()
                        if media_pool_item:
                            file_path = media_pool_item.GetClipProperty("File Path")
                            if file_path and os.path.exists(file_path) and file_path not in paths:
                                paths.append(file_path)
                    if paths:
                        return paths
        except Exception:
            pass

        # 3. Thử lấy từ clip dưới Playhead trên Timeline hiện tại
        try:
            timeline = self.get_active_timeline()
            if timeline:
                current_video_item = timeline.GetCurrentVideoItem()
                if current_video_item:
                    media_pool_item = current_video_item.GetMediaPoolItem()
                    if media_pool_item:
                        file_path = media_pool_item.GetClipProperty("File Path")
                        if file_path and os.path.exists(file_path) and file_path not in paths:
                            paths.append(file_path)
                    if paths:
                        return paths
        except Exception:
            pass

        return paths

    def auto_detect_video_path(self) -> Optional[str]:
        """
        Tự động phát hiện đường dẫn tệp video đơn lẻ đang hoạt động trong DaVinci Resolve.
        """
        paths = self.auto_detect_video_paths()
        return paths[0] if paths else None

    def get_active_timeline(self) -> Optional[Any]:
        """
        Lấy đối tượng Timeline đang mở và hoạt động trong DaVinci Resolve.

        Returns:
            object: Đối tượng Timeline của Resolve hoặc None nếu thất bại.
        """
        if not self.resolve or not self.current_project:
            if not self.connect():
                return None
        return self.current_project.GetCurrentTimeline()

    @staticmethod
    def generate_srt(subtitles: List[Dict[str, Any]], output_path: str) -> str:
        """
        Sinh tệp phụ đề định dạng SRT tiêu chuẩn từ mảng dữ liệu.
        Đây là giải pháp dự phòng và phân phối đa nền tảng tuyệt đối ổn định.

        Args:
            subtitles (List[Dict[str, Any]]): Mảng dữ liệu phụ đề chứa start, end, text.
            output_path (str): Đường dẫn lưu tệp SRT.

        Returns:
            str: Đường dẫn tệp SRT đã được tạo.
        """
        def format_time(seconds: float) -> str:
            seconds = max(0.0, seconds)
            hrs = int(seconds // 3600)
            mins = int((seconds % 3600) // 60)
            secs = int(seconds % 60)
            ms = int(round((seconds % 1) * 1000))
            if ms >= 1000:
                secs += 1
                ms = 0
            if secs >= 60:
                mins += 1
                secs = 0
            if mins >= 60:
                hrs += 1
                mins = 0
            return f"{hrs:02d}:{mins:02d}:{secs:02d},{ms:03d}"

        lines = []
        last_end = 0.0
        for idx, sub in enumerate(subtitles, 1):
            s = sub["start"]
            e = sub["end"]
            if e <= s:
                e = s + 0.3
            if s < last_end:
                s = last_end + 0.01
                if e <= s:
                    e = s + 0.3
            last_end = e
            start_str = format_time(s)
            end_str = format_time(e)
            text = sub["text"].strip()
            if not text:
                continue
            lines.append(f"{idx}")
            lines.append(f"{start_str} --> {end_str}")
            lines.append(f"{text}\n")

        parent_dir = os.path.dirname(output_path)
        if parent_dir and not os.path.exists(parent_dir):
            os.makedirs(parent_dir, exist_ok=True)

        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        return output_path

    def insert_subtitles_to_timeline(
        self,
        subtitles: List[Dict[str, Any]],
        config: SubtitleConfig,
        output_srt_path: Optional[str] = None,
        log_callback: Optional[Any] = None
    ) -> bool:
        """
        Thực hiện vẽ/chèn phụ đề trực tiếp lên Timeline DaVinci Resolve đang mở.

        Args:
            subtitles (List[Dict[str, Any]]): Mảng dữ liệu phụ đề cần vẽ.
            config (SubtitleConfig): Cấu hình kiểu dáng chữ.
            output_srt_path (str, optional): Đường dẫn tệp SRT đầu ra mong muốn.
            log_callback (callable, optional): Hàm để ghi log về GUI hoặc console.

        Returns:
            bool: True nếu chèn thành công hoặc xuất file SRT thành công.
        """
        def log(msg: str):
            if log_callback:
                log_callback(msg)
            else:
                print(msg)

        timeline = self.get_active_timeline()
        
        # Đường dẫn tệp SRT đầu ra
        if output_srt_path:
            srt_path = output_srt_path
        else:
            srt_path = os.path.join(os.environ.get("TEMP", "."), "temp_subtitles.srt")
            
        self.generate_srt(subtitles, srt_path)

        if not timeline:
            log(" ❌ Không tìm thấy Timeline DaVinci Resolve đang mở.")
            log(f" [INFO] Đã xuất file phụ đề SRT cục bộ thành công tại:\n 👉 {os.path.abspath(srt_path)}")
            return True

        try:
            # DaVinci Resolve 18+ cung cấp hàm ImportSubtitle trực tiếp trên đối tượng Timeline
            # Chèn tự động thành một Subtitle Track độc lập
            success = timeline.ImportSubtitle(srt_path)
            if success:
                log(" ✔ Tự động đồng bộ và chèn phụ đề thành công lên Timeline DaVinci Resolve!")
                return True
        except Exception as e:
            log(f" ⚠️ Lỗi khi import phụ đề vào Resolve: {str(e)}")

        log(" ➖ Kết nối thành công Resolve nhưng phiên bản API hiện tại yêu cầu thao tác kéo thả.")
        log(f" Đường dẫn file SRT:\n 👉 {os.path.abspath(srt_path)}")
        return True

    def import_edl_to_timeline(
        self,
        edl_path: str,
        video_path: Optional[Union[str, List[str]]] = None,
        timeline_name: str = "ResolveFlow Timeline",
        log_callback: Optional[Any] = None
    ) -> bool:
        """
        Import tệp EDL / FCPXML để tạo Timeline mới trong DaVinci Resolve và tự động liên kết Media.

        Args:
            edl_path (str): Đường dẫn tệp EDL (.edl) hoặc FCPXML (.fcpxml / .xml).
            video_path (str | List[str], optional): Đường dẫn tệp video nguồn.
            timeline_name (str): Tên Timeline mới muốn tạo.
            log_callback (callable, optional): Hàm ghi log.

        Returns:
            bool: True nếu import thành công.
        """
        def log(msg: str):
            if log_callback:
                log_callback(msg)
            else:
                print(msg)

        if not self.resolve or not self.current_project:
            if not self.connect():
                log(" ❌ Không thể kết nối tới ứng dụng DaVinci Resolve để import timeline.")
                if self.last_connect_error:
                    log(f" ℹ {self.last_connect_error}")
                return False

        if not self.current_project and self.project_manager:
            try:
                self.current_project = self.project_manager.LoadProject("ResolveFlow_AutoProject")
                if not self.current_project:
                    self.current_project = self.project_manager.CreateProject("ResolveFlow_AutoProject")
            except Exception:
                pass

        media_pool = self.current_project.GetMediaPool() if self.current_project else None
        if not media_pool:
            log(" ❌ Không tìm thấy Media Pool trong dự án DaVinci Resolve hiện tại.")
            return False

        # Chuẩn hóa danh sách video nguồn và nạp sẵn vào Media Pool của Resolve để chống lỗi Media Offline
        v_list = []
        if isinstance(video_path, list):
            v_list = [os.path.abspath(p) for p in video_path if os.path.exists(p)]
        elif isinstance(video_path, str) and video_path and os.path.exists(video_path):
            v_list = [os.path.abspath(video_path)]

        # Chuẩn bị danh sách tệp ứng viên: Ưu tiên tệp do caller chỉ định (nhất là FCP7 XML đa track), sau đó đến tệp thay thế
        candidate_paths = [os.path.abspath(edl_path)]
        dir_name = os.path.dirname(os.path.abspath(edl_path))
        base_stem = os.path.splitext(os.path.basename(edl_path))[0]

        if edl_path.lower().endswith(".xml"):
            alt_fcpxml = os.path.join(dir_name, f"{base_stem}.fcpxml")
            if os.path.exists(alt_fcpxml):
                candidate_paths.append(alt_fcpxml)
        elif edl_path.lower().endswith(".fcpxml"):
            alt_xml = os.path.join(dir_name, f"{base_stem}.xml")
            if os.path.exists(alt_xml):
                candidate_paths.append(alt_xml)

        # Trích xuất toàn bộ media từ XML/FCPXML để đảm bảo không bị thiếu video trên Timeline
        try:
            import xml.etree.ElementTree as ET
            from src.core.validator import DryRunValidator
            for cand in candidate_paths:
                if os.path.exists(cand):
                    try:
                        tree = ET.parse(cand)
                        root = tree.getroot()
                        for asset in root.findall(".//asset"):
                            src = asset.attrib.get("src")
                            if src:
                                p = DryRunValidator._uri_to_local_path(src)
                                if p and os.path.exists(p) and p not in v_list:
                                    v_list.append(os.path.abspath(p))
                        for pathurl in root.findall(".//pathurl"):
                            if pathurl.text:
                                p = DryRunValidator._uri_to_local_path(pathurl.text)
                                if p and os.path.exists(p) and p not in v_list:
                                    v_list.append(os.path.abspath(p))
                    except Exception:
                        pass
        except Exception:
            pass

        if v_list:
            self.import_media_to_media_pool(v_list, log_callback=log)

        first_dir = os.path.dirname(v_list[0]) if v_list else ""

        for target_file in candidate_paths:
            # Các biến thể tùy chọn import để tương thích tối đa với mọi bản DaVinci Resolve Studio (18, 19, 21)
            trial_options = []

            # Thử 1: Luôn ưu tiên tự động quét và nạp/liên kết clip nguồn (importSourceClips = True) để chống mất video
            opt1 = {"timelineName": timeline_name, "importSourceClips": True}
            if first_dir:
                opt1["sourceClipsPath"] = first_dir
            trial_options.append(opt1)

            # Thử 2: Đặt tên timeline và liên kết với Media Pool (importSourceClips = False)
            opt2 = {"timelineName": timeline_name, "importSourceClips": False}
            if first_dir:
                opt2["sourceClipsPath"] = first_dir
            trial_options.append(opt2)

            # Thử 3: Chỉ truyền timelineName
            trial_options.append({"timelineName": timeline_name})

            # Thử 4: Gọi trực tiếp không truyền dict tùy chọn
            trial_options.append(None)

            for opt in trial_options:
                try:
                    if opt is not None:
                        timeline = media_pool.ImportTimelineFromFile(target_file, opt)
                    else:
                        timeline = media_pool.ImportTimelineFromFile(target_file)

                    if timeline:
                        # Kiểm tra xem timeline có thực sự chứa video track 1 hay không
                        try:
                            v_items = timeline.GetItemListInTrack("video", 1) or []
                            if not v_items and opt == opt2:
                                # Nếu importSourceClips: False nạp timeline nhưng rỗng video, tiếp tục thử tùy chọn khác
                                continue
                        except Exception:
                            pass

                        log(f" 🎉 Đã tự động import Timeline '{timeline_name}' thành công từ {os.path.basename(target_file)}!")
                        return True
                except Exception as e:
                    log(f" ℹ Thử import {os.path.basename(target_file)}: {e}")

        log(" ➖ Cần import thủ công tệp Timeline (.fcpxml / .xml) vào DaVinci Resolve.")
        return False

    # Alias tương thích ngược cho các màn hình UI gọi import_fcpxml_timeline
    import_fcpxml_timeline = import_edl_to_timeline

    # ------------------------------------------------------------------
    # LUT / Color: phát hiện LUT đã gắn sẵn và áp dụng không ghi đè
    # ------------------------------------------------------------------
    @classmethod
    def get_resolve_lut_dir(cls) -> str:
        """Lấy thư mục lưu trữ LUT mặc định của DaVinci Resolve trên từng hệ điều hành."""
        from src.core.asset_manager import AssetManager
        return str(AssetManager.get_lut_install_dir())

    @property
    def RESOLVE_LUT_DIR(self) -> str:
        return self.get_resolve_lut_dir()

    @staticmethod
    def _graph_luts(graph: Any) -> List[str]:
        """Danh sách LUT (khác rỗng) đang gắn trên các node của một Graph; [] nếu không có hoặc API không hỗ trợ."""
        if graph is None:
            return []
        try:
            count = int(graph.GetNumNodes() or 0)
        except Exception:
            return []
        found = []
        for idx in range(1, count + 1):
            try:
                lut = graph.GetLUT(idx)
            except Exception:
                lut = ""
            if lut:
                found.append(str(lut))
        return found

    def _iter_video_items(self, timeline: Any):
        try:
            track_count = int(timeline.GetTrackCount("video") or 0)
        except Exception:
            track_count = 0
        for track in range(1, track_count + 1):
            try:
                for item in timeline.GetItemListInTrack("video", track) or []:
                    yield track, item
            except Exception:
                continue

    def scan_existing_luts(self) -> Dict[str, Any]:
        """
        Kiểm tra LUT người dùng đã gắn trong timeline hiện tại (cấp timeline và từng clip).
        Returns: {"timeline": [lut...], "clips": [(tên clip, [lut...]), ...], "total_clips": n}
        """
        timeline = self.get_active_timeline()
        result: Dict[str, Any] = {"timeline": [], "clips": [], "total_clips": 0}
        if not timeline:
            return result
        try:
            result["timeline"] = self._graph_luts(timeline.GetNodeGraph())
        except Exception:
            pass
        for _, item in self._iter_video_items(timeline):
            result["total_clips"] += 1
            try:
                luts = self._graph_luts(item.GetNodeGraph())
            except Exception:
                luts = []
            if luts:
                try:
                    name = item.GetName()
                except Exception:
                    name = "?"
                result["clips"].append((name, luts))
        return result

    def _set_lut(self, graph: Any, lut_path: str) -> bool:
        """Gắn LUT vào node 1 của Graph. Nếu Resolve chưa biết đường dẫn, chép vào thư mục LUT của Resolve rồi thử lại."""
        try:
            if graph.SetLUT(1, lut_path):
                return True
        except Exception:
            pass
        import shutil
        try:
            lut_base_dir = self.get_resolve_lut_dir()
            target_dir = os.path.join(lut_base_dir, "ResolveFlow")
            os.makedirs(target_dir, exist_ok=True)
            shutil.copy2(lut_path, os.path.join(target_dir, os.path.basename(lut_path)))
            if self.current_project:
                self.current_project.RefreshLUTList()
            return bool(graph.SetLUT(1, os.path.join("ResolveFlow", os.path.basename(lut_path))))
        except Exception:
            return False

    def apply_look_lut(
        self,
        lut_path: str,
        scope: str = "timeline",
        skip_existing: bool = True,
        log_callback: Optional[Any] = None
    ) -> Dict[str, int]:
        """
        Áp dụng một LUT (.cube) vào DaVinci Resolve mà KHÔNG phá màu đã chỉnh sẵn.
        - scope="timeline": gắn vào node của TIMELINE (nằm trên mọi grade của từng clip, không đụng vào grade clip).
        - scope="clips": gắn vào node 1 của từng clip.
        - skip_existing=True: bỏ qua timeline/clip đã có LUT (LUT của bạn được giữ nguyên, tránh chồng hai LUT).
        Returns: {"applied": n, "skipped": n, "failed": n}
        """
        def log(msg: str):
            if log_callback:
                log_callback(msg)
            else:
                print(msg)

        stats = {"applied": 0, "skipped": 0, "failed": 0}
        if not os.path.exists(lut_path):
            log(f" ❌ Không tìm thấy file LUT: {lut_path}")
            stats["failed"] += 1
            return stats
        timeline = self.get_active_timeline()
        if not timeline:
            log(" ❌ Không có Timeline nào đang mở trong DaVinci Resolve.")
            stats["failed"] += 1
            return stats
        lut_path = os.path.abspath(lut_path)
        try:
            if self.current_project:
                self.current_project.RefreshLUTList()
        except Exception:
            pass

        if scope == "timeline":
            try:
                graph = timeline.GetNodeGraph()
            except Exception:
                graph = None
            if graph is None:
                log(" ⚠ Phiên bản Resolve này không cho truy cập node của timeline. Hãy chọn phạm vi 'Từng clip'.")
                stats["failed"] += 1
                return stats
            existing = self._graph_luts(graph)
            if existing and skip_existing:
                log(f" ⏭ Timeline đã có LUT ({os.path.basename(existing[0])}). Giữ nguyên, không chồng thêm LUT.")
                stats["skipped"] += 1
            elif self._set_lut(graph, lut_path):
                log(f" ✔ Đã gắn '{os.path.basename(lut_path)}' lên node Timeline (không ảnh hưởng grade từng clip).")
                stats["applied"] += 1
            else:
                log(" ❌ Resolve không nhận LUT. Hãy chép file .cube vào thư mục LUT của Resolve rồi bấm lại.")
                stats["failed"] += 1
            return stats

        for _, item in self._iter_video_items(timeline):
            try:
                graph = item.GetNodeGraph()
            except Exception:
                graph = None
            if graph is None:
                stats["failed"] += 1
                continue
            if skip_existing and self._graph_luts(graph):
                stats["skipped"] += 1
            elif self._set_lut(graph, lut_path):
                stats["applied"] += 1
            else:
                stats["failed"] += 1
        log(f" ✔ LUT từng clip: gắn {stats['applied']}, bỏ qua {stats['skipped']} (đã có LUT), lỗi {stats['failed']}.")
        return stats

    def get_current_playhead_timecode(self) -> Optional[str]:
        """Lấy chuỗi timecode hiện tại của con trỏ (Playhead) trên Timeline đang mở."""
        timeline = self.get_active_timeline()
        if not timeline:
            return None
        try:
            return timeline.GetCurrentTimecode()
        except Exception:
            return None

    def get_current_playhead_seconds(self) -> float:
        """
        Lấy vị trí con trỏ (Playhead) hiện tại quy đổi ra giây (float) tương đối từ đầu timeline.
        Mặc định trả về 0.0 nếu chưa mở timeline hoặc không thể đọc.
        """
        timeline = self.get_active_timeline()
        if not timeline:
            return 0.0
        try:
            tc = timeline.GetCurrentTimecode()
            if not tc:
                return 0.0
            fps = 30.0
            try:
                fps_val = timeline.GetSetting("timelineFrameRate")
                if fps_val:
                    fps = float(fps_val)
            except Exception:
                pass

            start_tc = None
            try:
                start_tc = timeline.GetSetting("timelineStartTimecode")
            except Exception:
                pass

            parts = tc.replace(";", ":").split(":")
            if len(parts) == 4:
                hrs, mins, secs, frames = map(int, parts)
                current_total_seconds = hrs * 3600 + mins * 60 + secs + (frames / fps)
                if start_tc:
                    start_parts = start_tc.replace(";", ":").split(":")
                    if len(start_parts) == 4:
                        s_hrs, s_mins, s_secs, s_frames = map(int, start_parts)
                        start_total_seconds = s_hrs * 3600 + s_mins * 60 + s_secs + (s_frames / fps)
                        return max(0.0, current_total_seconds - start_total_seconds)

                if hrs >= 1:
                    current_total_seconds -= 3600.0
                return max(0.0, current_total_seconds)
        except Exception:
            pass
        return 0.0

    def insert_sfx_to_track(
        self,
        sfx_path: str,
        target_track: int = 2,
        time_pos: Optional[float] = None,
        volume_offset_db: float = -12.0,
        log_callback: Optional[Any] = None
    ) -> bool:
        """
        Nạp file âm thanh SFX vào Media Pool và chèn vào Audio Track chỉ định.
        """
        def log(msg: str):
            if log_callback:
                log_callback(msg)
            else:
                print(msg)

        if not os.path.exists(sfx_path):
            log(f" ❌ Không tìm thấy tệp SFX: {sfx_path}")
            return False

        if not self.is_connected():
            if not self.connect():
                log(" ❌ Không thể kết nối tới DaVinci Resolve.")
                return False

        timeline = self.get_active_timeline()
        if not timeline:
            log(" ❌ Không tìm thấy Timeline đang mở trong DaVinci Resolve.")
            return False

        # 1. Nạp tệp SFX vào Media Pool nếu chưa có
        self.import_media_to_media_pool([sfx_path], log_callback=log)
        
        # 2. Tìm MediaPoolItem tương ứng
        media_pool = self.current_project.GetMediaPool()
        clips = self.get_media_pool_clips()
        sfx_norm = os.path.normcase(os.path.abspath(sfx_path))
        sfx_item = None
        for c in clips:
            try:
                fp = c.GetClipProperty("File Path")
                if fp and os.path.normcase(os.path.abspath(fp)) == sfx_norm:
                    sfx_item = c
                    break
            except Exception:
                pass

        if not sfx_item and clips:
            fname = os.path.basename(sfx_path).lower()
            for c in clips:
                try:
                    if c.GetName() and c.GetName().lower() == fname:
                        sfx_item = c
                        break
                except Exception:
                    pass

        if not sfx_item:
            log(f" ⚠️ Đã nạp SFX vào Media Pool. Vui lòng kéo thả '{os.path.basename(sfx_path)}' vào Timeline Audio Track {target_track}.")
            return True

        # 3. Append to timeline at track
        try:
            appended = media_pool.AppendToTimeline([{
                "mediaPoolItem": sfx_item,
                "trackIndex": target_track,
                "mediaType": 2
            }])
            if appended:
                log(f" ✔ Đã chèn thành công hiệu ứng âm thanh '{os.path.basename(sfx_path)}' vào Audio Track {target_track}!")
                return True
        except Exception as e:
            log(f" ℹ Append to timeline: {str(e)}")

        log(f" ✔ SFX '{os.path.basename(sfx_path)}' đã sẵn sàng trong Media Pool.")
        return True

    def capture_playhead_preset(
        self,
        preset_name: str = "ChunDVC_Preset",
        log_callback: Optional[Any] = None
    ) -> Dict[str, Any]:
        """
        Hút kiểu chữ / Fusion Comp của clip tại vị trí Playhead thành Preset .setting vĩnh viễn.
        """
        from src.core.preset_capture import capture_playhead_text_preset
        return capture_playhead_text_preset(
            preset_name=preset_name,
            resolve_app=self.resolve,
            log_callback=log_callback
        )

    def insert_title_at_playhead(
        self,
        text: str,
        font_name: str = "Arial",
        font_size: int = 48,
        color_hex: str = "#FFFFFF",
        duration_sec: float = 3.0,
        preset_id: str = "karaoke_pop",
        log_callback: Optional[Any] = None,
        highlight_hex: Optional[str] = None,
        stroke_hex: Optional[str] = None,
        stroke_width: Optional[float] = None,
        box_glow_hex: Optional[str] = None,
        weight: str = "bold",
        animation: Optional[str] = None,
        timing_curve: Optional[str] = None,
        preferred_track: int = 2,
        **kwargs
    ) -> bool:
        """
        Chèn tiêu đề / Text+ preset tại vị trí Playhead lên Track V2 (hoặc track trống tiếp theo)
        sử dụng thuật toán NEW-NESTED-NO-RIPPLE-V2 (Zero Ripple, không cắt/đè timeline).
        """
        def log(msg: str):
            if log_callback:
                log_callback(msg)
            else:
                print(msg)

        if not text.strip():
            log(" ⚠️ Nội dung chữ (Title Text) không được để trống.")
            return False

        playhead_sec = self.get_current_playhead_seconds()
        log(f" 📝 Đang chèn Text+ Title '{text}' tại mốc {playhead_sec:.2f}s (Preset: {preset_id})...")

        # 1. Thử chèn qua thuật toán NEW-NESTED-NO-RIPPLE-V2 (cô lập tiến trình hoặc in-process an toàn)
        try:
            # Map preset_id sang tên Fusion template tương ứng
            template_name = kwargs.get("template_name", "")
            if not template_name:
                style_map = {
                    "kinetic_hormozi": "RF_SpringPop",
                    "bounce_word": "RF_SpringPop",
                    "highlighter_swipe": "RF_Highlighter",
                    "box_highlight": "RF_Highlighter",
                    "typewriter": "RF_Typewriter",
                    "vhs_retro": "RF_Typewriter",
                    "neon_pulse": "RF_NeonPulse",
                    "glow_neon": "RF_NeonPulse",
                    "rgb_glitch": "RF_RGBGlitch",
                    "paper_cutout": "RF_PaperCutout",
                    "clean_minimal": "RF_LowerThird",
                    "quote_box": "RF_QuoteBox",
                }
                template_name = style_map.get(preset_id, preset_id)

            res = run_title_insert_worker_isolated(
                title_name=template_name,
                display_name=text,
                preferred_track=preferred_track,
                duration_sec=duration_sec,
            )
            if res.get("ok"):
                log(f" ✔ Đã chèn thành công Title '{template_name}' lên Track V{res.get('track', preferred_track)} (Zero Ripple)!")
                return True
        except Exception as e:
            log(f" ℹ Không chèn qua Nested Importer V2 được ({e}), đang dùng phương thức Fusion Bridge...")

        # 2. Thử qua Fusion Bridge (cập nhật macro trực tiếp)
        try:
            from src.core.resolve_bridge import insert_title_at_playhead as bridge_insert_title
            style_map = {
                "kinetic_hormozi": "spring_pop",
                "bounce_word": "spring_pop",
                "highlighter_swipe": "highlighter",
                "box_highlight": "highlighter",
                "typewriter": "typewriter",
                "vhs_retro": "typewriter",
                "neon_pulse": "neon_pulse",
                "glow_neon": "neon_pulse",
                "rgb_glitch": "rgb_glitch",
                "paper_cutout": "paper_cutout",
                "clean_minimal": "lower_third",
                "quote_box": "quote_box",
            }
            anim_to_style = {
                "pop": "spring_pop",
                "bounce": "spring_pop",
                "typewriter": "typewriter",
                "glow": "neon_pulse",
                "box": "highlighter",
                "box_highlight": "highlighter",
                "glitch": "rgb_glitch",
                "paper": "paper_cutout",
                "slide": "lower_third",
                "static": "lower_third",
            }
            if animation and animation in anim_to_style:
                style = anim_to_style[animation]
            else:
                style = style_map.get(preset_id, "spring_pop")

            dur_frames = max(24, int(duration_sec * 30))
            fusion_size = round(max(0.04, min(0.25, font_size / 600.0)), 4)
            accent_col = highlight_hex or box_glow_hex or "#FFD400"
            font_style = "Black" if weight in ("black", "extra_bold") else ("Regular" if weight in ("regular", "normal") else "Bold")

            bridge_insert_title(
                style=style,
                params={
                    "text": text,
                    "font": font_name,
                    "style": font_style,
                    "size": fusion_size,
                    "color": color_hex,
                    "accent": accent_col,
                    "duration_frames": dur_frames,
                },
                track_index=preferred_track,
            )
            log(f" ✔ Đã chèn thành công Title hoạt họa '{style}' lên Track V{preferred_track} qua Fusion Bridge!")
            return True
        except Exception as e:
            log(f" ℹ Không chèn trực tiếp được qua Fusion Bridge ({e}), đang dùng phương thức FCPXML...")

        # 3. Phương thức dự phòng qua FCPXML
        try:
            import tempfile
            from src.core.fcpxml_generator import FCPXMLGenerator

            temp_title_fcpxml = os.path.join(tempfile.gettempdir(), f"rf_title_{uuid.uuid4().hex[:8]}.fcpxml")
            words_list = text.split()
            num_w = max(1, len(words_list))
            single_sub = [{
                "start": playhead_sec,
                "end": playhead_sec + duration_sec,
                "text": text,
                "words": [{"word": w, "start": playhead_sec + i * (duration_sec / num_w), "end": playhead_sec + (i + 1) * (duration_sec / num_w)} for i, w in enumerate(words_list)]
            }]

            FCPXMLGenerator.generate_karaoke_fcpxml(
                subtitles=single_sub,
                output_path=temp_title_fcpxml,
                font_name=font_name,
                font_size=font_size,
                aspect_ratio="16:9",
                preset=preset_id
            )
            log(f" ✔ Đã sinh tệp Title FCPXML Text+ tại:\n 👉 {temp_title_fcpxml}")
            return True
        except Exception as e:
            log(f" ❌ Lỗi khi tạo Title: {str(e)}")
            return False


    def set_render_preset_and_queue(
        self,
        preset_name: str = "tiktok_916",
        custom_name: Optional[str] = None,
        log_callback: Optional[Any] = None
    ) -> bool:
        """
        Cấu hình Render Settings và thêm vào Render Queue của DaVinci Resolve.
        """
        def log(msg: str):
            if log_callback:
                log_callback(msg)
            else:
                print(msg)

        if not self.is_connected():
            if not self.connect():
                log(" ❌ Không thể kết nối tới DaVinci Resolve.")
                return False

        project = self.current_project
        if not project:
            log(" ❌ Không tìm thấy Project đang mở.")
            return False

        preset_configs = {
            "tiktok_916": {
                "format": "mp4",
                "codec": "H264",
                "width": 1080,
                "height": 1920,
                "desc": "TikTok / Reels / Shorts 9:16 (1080x1920 60fps)"
            },
            "youtube_1080p": {
                "format": "mp4",
                "codec": "H264",
                "width": 1920,
                "height": 1080,
                "desc": "YouTube Standard 16:9 (1920x1080 Full HD)"
            },
            "youtube_4k": {
                "format": "mp4",
                "codec": "H265",
                "width": 3840,
                "height": 2160,
                "desc": "YouTube 4K Ultra HD (3840x2160 H.265)"
            },
            "podcast_audio": {
                "format": "wave",
                "codec": "LinearPCM",
                "desc": "Podcast Audio Master (WAV 48kHz 24-bit)"
            }
        }

        cfg = preset_configs.get(preset_name, preset_configs["tiktok_916"])
        log(f" 🎯 Đang thiết lập cấu hình Render: {cfg['desc']}...")

        try:
            if "format" in cfg and "codec" in cfg:
                try:
                    project.SetCurrentRenderFormatAndCodec(cfg["format"], cfg["codec"])
                except Exception:
                    pass
            if "width" in cfg and "height" in cfg:
                try:
                    project.SetRenderSettings({"CustomResolution": True, "TargetDir": os.path.expanduser("~")})
                except Exception:
                    pass

            job_id = project.AddRenderJob()
            if job_id:
                log(f" 🎉 Đã thêm công việc kết xuất thành công vào Render Queue của Resolve (Job ID: {job_id})!")
                return True
            else:
                log(" ℹ Đã cấu hình Render Settings. Bạn có thể bấm 'Render All' trên trang Deliver của Resolve.")
                return True
        except Exception as e:
            log(f" ⚠️ Lỗi khi thêm Render Job: {str(e)}")
            return False


def auto_import_timeline_to_resolve(
    xml_path: Union[str, os.PathLike],
    timeline_name: Optional[str] = None
) -> Tuple[bool, str]:
    """
    Quy trình True 1-Click tự động nạp Timeline vào DaVinci Resolve:
    1. Tự động kết nối Resolve qua load_resolve_api().
    2. Lấy current_project = project_manager.GetCurrentProject(). Nếu chưa mở project nào,
       tự động tạo/mở project dự phòng 'ResolveFlow_AutoProject'.
    3. Nạp timeline qua media_pool.ImportTimelineFromFile(..., {"timelineName": ..., "importSourceClips": True}).
    4. Nếu nạp .fcpxml bị từ chối, tự động fallback nạp file .xml (FCP7 XML) tương ứng ngay lập tức.
    5. Sau khi import thành công, gọi resolve.OpenPage("edit") để chuyển thẳng sang giao diện Edit Page.
    """
    xml_str = str(xml_path)
    if not os.path.exists(xml_str):
        base_stem, ext = os.path.splitext(xml_str)
        alt = base_stem + (".xml" if ext.lower() == ".fcpxml" else ".fcpxml")
        if os.path.exists(alt):
            xml_str = alt
        else:
            return False, f"Tệp Timeline không tồn tại: {xml_path}"

    from src.core.resolve_bridge import load_resolve_api
    resolve_app, status_msg = load_resolve_api()
    if not resolve_app:
        return False, f"Không thể kết nối DaVinci Resolve: {status_msg}"

    try:
        project_manager = resolve_app.GetProjectManager()
    except Exception as e:
        return False, f"Lỗi lấy ProjectManager từ DaVinci Resolve: {e}"

    if not project_manager:
        return False, "Không lấy được ProjectManager từ DaVinci Resolve."

    current_project = None
    try:
        current_project = project_manager.GetCurrentProject()
    except Exception:
        pass

    if not current_project:
        try:
            current_project = project_manager.LoadProject("ResolveFlow_AutoProject")
            if not current_project:
                current_project = project_manager.CreateProject("ResolveFlow_AutoProject")
        except Exception as e:
            return False, f"Lỗi khi mở/tạo project dự phòng trong DaVinci Resolve: {e}"

    if not current_project:
        return False, "Không thể mở hoặc tạo project trong DaVinci Resolve."

    try:
        media_pool = current_project.GetMediaPool()
    except Exception as e:
        return False, f"Lỗi lấy MediaPool từ project: {e}"

    if not media_pool:
        return False, "Không lấy được MediaPool từ project hiện tại."

    candidate_paths = [os.path.abspath(xml_str)]
    base_stem, ext = os.path.splitext(os.path.abspath(xml_str))
    if ext.lower() == ".fcpxml":
        alt_xml = base_stem + ".xml"
        if os.path.exists(alt_xml):
            candidate_paths.append(alt_xml)
    elif ext.lower() == ".xml":
        alt_fcpxml = base_stem + ".fcpxml"
        if os.path.exists(alt_fcpxml):
            candidate_paths.append(alt_fcpxml)

    opts = {"importSourceClips": True}
    if timeline_name:
        opts["timelineName"] = timeline_name

    imported_path = None
    for cand in candidate_paths:
        tl = None
        try:
            tl = media_pool.ImportTimelineFromFile(cand, opts)
        except Exception:
            pass
        if not tl:
            try:
                tl = media_pool.ImportTimelineFromFile(cand)
            except Exception:
                pass
        if tl:
            imported_path = cand
            break

    if imported_path:
        try:
            resolve_app.OpenPage("edit")
        except Exception:
            pass
        return True, f"Đã tự động import Timeline thành công từ {os.path.basename(imported_path)}!"

    return False, f"DaVinci Resolve từ chối nạp timeline từ {os.path.basename(xml_str)}."


ResolveAutomation.auto_import_timeline_to_resolve = staticmethod(auto_import_timeline_to_resolve)

# Bộ nhớ đệm metadata video (duration, is_vertical, width, height) tránh gọi subprocess ffprobe lặp lại
_VIDEO_META_CACHE: Dict[str, Dict[str, Any]] = {}

def get_video_metadata(video_path: str) -> Dict[str, Any]:
    """
    Truy xuất và lưu đệm siêu dữ liệu video (thời lượng, kích thước, chiều dọc/ngang).
    """
    if not video_path:
        return {"duration": 0.0, "is_vertical": False, "width": 1920, "height": 1080}
    norm_path = os.path.normpath(os.path.abspath(video_path))
    if norm_path in _VIDEO_META_CACHE:
        return _VIDEO_META_CACHE[norm_path]

    meta = {"duration": 0.0, "is_vertical": False, "width": 1920, "height": 1080}
    try:
        import ffmpeg
        probe = ffmpeg.probe(norm_path)
        video_stream = next((stream for stream in probe.get('streams', []) if stream.get('codec_type') == 'video'), None)
        if video_stream:
            width = int(video_stream.get('width', 1920))
            height = int(video_stream.get('height', 1080))
            meta["width"] = width
            meta["height"] = height
            meta["is_vertical"] = height > width
        format_info = probe.get("format", {})
        if "duration" in format_info:
            meta["duration"] = float(format_info["duration"])
    except Exception:
        pass

    _VIDEO_META_CACHE[norm_path] = meta
    return meta

def is_vertical_video(video_path: str) -> bool:
    """
    Kiểm tra xem video là dọc (portrait) hay ngang (landscape) với bộ nhớ đệm chống blocking.
    """
    if not video_path:
        return False
    norm_path = os.path.normpath(os.path.abspath(video_path))
    if norm_path in _VIDEO_META_CACHE:
        return _VIDEO_META_CACHE[norm_path].get("is_vertical", False)
    meta = get_video_metadata(video_path)
    return meta.get("is_vertical", False)

def split_subtitles(
    subtitles: List[Dict[str, Any]], 
    max_chars: Optional[int] = None,
    limit: Optional[int] = None,
    mode: Literal["characters", "words"] = "characters"
) -> List[Dict[str, Any]]:
    """
    Tách các phân đoạn phụ đề dựa trên:
    - Chế độ 'characters': Số ký tự tối đa trên một dòng (mặc định 42 cho 16:9, 22 cho 9:16).
    - Chế độ 'words': Số từ tối đa trên một dòng/card (ví dụ: 4-6 từ cho Shorts/Reels/TikTok).
    Sử dụng thông tin mốc thời gian từ đơn (word-level timestamps) để đảm bảo đồng bộ âm thanh chuẩn xác 100%.

    Args:
        subtitles (List[Dict[str, Any]]): Danh sách phân đoạn phụ đề thô từ Whisper.
        max_chars (int, optional): Tham số số ký tự tối đa (để tương thích ngược).
        limit (int, optional): Giới hạn tối đa (ký tự hoặc từ tùy theo mode).
        mode (str): 'characters' hoặc 'words'.

    Returns:
        List[Dict[str, Any]]: Danh sách các phân đoạn phụ đề đã được ngắt dòng tối ưu.
    """
    # Xác định giá trị giới hạn hiệu dụng
    if limit is not None:
        effective_limit = limit
    elif max_chars is not None:
        effective_limit = max_chars
    else:
        effective_limit = 42 if mode == "characters" else 6

    new_subtitles = []
    words = []
    
    for seg in subtitles:
        if seg.get("words"):
            words.extend(seg["words"])
        else:
            # Dự phòng nếu không có thông tin từ đơn
            text_words = seg["text"].split()
            if text_words:
                num_words = len(text_words)
                seg_dur = seg["end"] - seg["start"]
                word_dur = seg_dur / num_words if num_words > 0 else 0
                for i, w_text in enumerate(text_words):
                    words.append({
                        "word": w_text,
                        "start": seg["start"] + i * word_dur,
                        "end": seg["start"] + (i + 1) * word_dur
                    })
                    
    if not words:
        return subtitles

    current_segment_words = []
    current_len = 0
    
    for word_info in words:
        word = word_info["word"].strip()
        if not word:
            continue
            
        additional_len = len(word) + (1 if current_len > 0 else 0)
        
        # Kiểm tra khoảng cách im lặng giữa 2 từ để ngắt câu tự nhiên
        silence_gap = 0.0
        if current_segment_words:
            silence_gap = word_info["start"] - current_segment_words[-1]["end"]
            
        # Kiểm tra điều kiện ngắt dòng theo chế độ
        if mode == "words":
            should_split = (len(current_segment_words) >= effective_limit) or (silence_gap > 1.5)
        else:
            should_split = (current_len > 0 and current_len + additional_len > effective_limit) or (silence_gap > 1.5)

        if should_split and current_segment_words:
            new_subtitles.append({
                "start": current_segment_words[0]["start"],
                "end": current_segment_words[-1]["end"],
                "text": " ".join([w["word"].strip() for w in current_segment_words]),
                "words": list(current_segment_words)
            })
            current_segment_words = [word_info]
            current_len = len(word)
        else:
            current_segment_words.append(word_info)
            current_len += additional_len
            
    if current_segment_words:
        new_subtitles.append({
            "start": current_segment_words[0]["start"],
            "end": current_segment_words[-1]["end"],
            "text": " ".join([w["word"].strip() for w in current_segment_words]),
            "words": list(current_segment_words)
        })
        
    return new_subtitles

def map_time_to_timeline(t_src: float, keep_intervals: List[Any], base_rec_time: float = 0.0) -> float:
    """
    Ánh xạ mốc thời gian nguồn (source time) sang mốc thời gian trên timeline đã cắt (record time).
    Hỗ trợ cả List[CutSegment] và List[Tuple[float, float]].
    """
    from src.core.autocut import CutSegment
    if not keep_intervals:
        return base_rec_time + t_src

    # Chuẩn hóa về danh sách CutSegment
    if isinstance(keep_intervals[0], CutSegment):
        segments = keep_intervals
    else:
        raw_keep = sorted(keep_intervals, key=lambda x: x[0])
        segments = []
        current_time = 0.0
        max_val = max(t_src, max(x[1] for x in raw_keep) if raw_keep else t_src)
        for start, end in raw_keep:
            if start > current_time:
                segments.append(CutSegment(start=current_time, end=start, action="cut", speed=1.0))
            segments.append(CutSegment(start=start, end=end, action="keep", speed=1.0))
            current_time = end
        if current_time < max_val + 1.0:
            segments.append(CutSegment(start=current_time, end=max_val + 1.0, action="cut", speed=1.0))

    rec_time = base_rec_time
    for seg in segments:
        if t_src < seg.start:
            return rec_time
        elif seg.start <= t_src <= seg.end:
            if seg.action == "cut":
                return rec_time
            return rec_time + (t_src - seg.start) / seg.speed
        else:
            rec_time += seg.timeline_duration

    return rec_time

def map_time_with_speedup_segments(t_src: float, speedup_segments: List[Dict[str, Any]], base_rec_time: float = 0.0) -> float:
    """
    Ánh xạ mốc thời gian nguồn sang record time khi có áp dụng tua nhanh khoảng lặng (Speed-Ramp).
    """
    from src.core.autocut import CutSegment
    segs = []
    for s in speedup_segments:
        action = "keep" if s["type"] == "voice" else "speedup"
        segs.append(CutSegment(
            start=s["start"],
            end=s["end"],
            action=action,
            speed=s.get("speed", 1.0)
        ))
    return map_time_to_timeline(t_src, segs, base_rec_time)

def map_subtitles_with_speedup_segments(
    subtitles: List[Dict[str, Any]],
    speedup_segments: List[Dict[str, Any]],
    base_rec_time: float = 0.0
) -> List[Dict[str, Any]]:
    """
    Chuyển đổi mốc thời gian phụ đề sang record time khi có áp dụng tua nhanh (Speed-Ramp/Timelapse).
    """
    from src.core.autocut import CutSegment
    segs = []
    for s in speedup_segments:
        action = "keep" if s["type"] == "voice" else "speedup"
        segs.append(CutSegment(
            start=s["start"],
            end=s["end"],
            action=action,
            speed=s.get("speed", 1.0)
        ))
    return map_subtitles_to_timeline(subtitles, segs, base_rec_time)

def map_subtitles_to_timeline(
    subtitles: List[Dict[str, Any]], 
    keep_intervals: List[Any], 
    base_rec_time: float = 0.0
) -> List[Dict[str, Any]]:
    """
    Chuyển đổi toàn bộ mốc thời gian của phụ đề và các từ đơn sang mốc thời gian tương ứng trên timeline đã cắt khoảng lặng.
    Loại bỏ hoàn toàn bất kỳ phụ đề hoặc từ ngữ nào thuộc phân đoạn đã bị cắt bỏ (Silence Cut / Bad Takes).
    """
    from src.core.autocut import CutSegment
    if not keep_intervals:
        return subtitles

    # Chuẩn hóa keep_intervals thành List[CutSegment]
    if isinstance(keep_intervals[0], CutSegment):
        segments = keep_intervals
    else:
        raw_keep = sorted(keep_intervals, key=lambda x: x[0])
        segments = []
        current_time = 0.0
        max_val = 0.0
        if subtitles:
            max_val = max(sub["end"] for sub in subtitles)
        if raw_keep:
            max_val = max(max_val, max(x[1] for x in raw_keep))
            
        for start, end in raw_keep:
            if start > current_time:
                segments.append(CutSegment(start=current_time, end=start, action="cut", speed=1.0))
            segments.append(CutSegment(start=start, end=end, action="keep", speed=1.0))
            current_time = end
        if current_time < max_val + 1.0:
            segments.append(CutSegment(start=current_time, end=max_val + 1.0, action="cut", speed=1.0))

    mapped_subs = []
    for sub in subtitles:
        sub_start = sub["start"]
        sub_end = sub["end"]
        
        words = sub.get("words", [])
        if words:
            mapped_words = []
            for w in words:
                w_s = w.get("start", sub_start)
                w_e = w.get("end", sub_end)
                w_mid = (w_s + w_e) / 2.0
                
                # Tìm segment chứa w_mid (không dùng dung sai tùy tiện nữa)
                target_seg = None
                for seg in segments:
                    if seg.start <= w_mid <= seg.end:
                        target_seg = seg
                        break
                
                # Nếu từ này nằm trong đoạn bị cắt (cut), loại bỏ hoàn toàn!
                if target_seg is None or target_seg.action == "cut":
                    continue
                
                new_w_start = map_time_to_timeline(w_s, segments, base_rec_time)
                new_w_end = map_time_to_timeline(w_e, segments, base_rec_time)
                if new_w_end <= new_w_start:
                    new_w_end = new_w_start + max(0.05, w_e - w_s)
                mapped_words.append({
                    "word": w["word"],
                    "start": new_w_start,
                    "end": new_w_end
                })

            if not mapped_words:
                continue

            new_start = mapped_words[0]["start"]
            new_end = mapped_words[-1]["end"]
            if new_end <= new_start:
                new_end = new_start + 0.2

            mapped_subs.append({
                "start": new_start,
                "end": new_end,
                "text": " ".join(w["word"].strip() for w in mapped_words),
                "words": mapped_words
            })
        else:
            # Không có word timestamps: tính overlap với các keep/speedup segments
            total_overlap = 0.0
            for seg in segments:
                if seg.action != "cut":
                    ov_s = max(sub_start, seg.start)
                    ov_e = min(sub_end, seg.end)
                    if ov_e > ov_s:
                        total_overlap += (ov_e - ov_s)

            if total_overlap < 0.1:
                continue

            new_start = map_time_to_timeline(sub_start, segments, base_rec_time)
            new_end = map_time_to_timeline(sub_end, segments, base_rec_time)
            if new_end <= new_start:
                new_end = new_start + max(0.2, total_overlap)

            mapped_subs.append({
                "start": new_start,
                "end": new_end,
                "text": sub["text"],
                "words": []
            })

    return mapped_subs


# =============================================================================
# NEW-NESTED-NO-RIPPLE-V2 Engine (Akitt Pro Architecture Port)
# =============================================================================
INTERNAL_BIN_NAME = "_ChunDVC Preset Sources"
SOURCE_TIMELINE_PREFIX = "_CDVC_"


def _safe_name(text: str, limit: int = 36) -> str:
    cleaned = re.sub(r'[^0-9A-Za-zÀ-ỹ.-]+', '_', str(text or 'Preset'))
    cleaned = re.sub(r'_+', '_', cleaned).strip(' ._')
    return cleaned[:max(8, int(limit))] if cleaned else "Preset"



def _nominal_fps(project: Any) -> Tuple[int, float]:
    try:
        raw = project.GetSetting("timelineFrameRate")
        fps = float(raw) if raw not in (None, "") else 24.0
    except Exception:
        fps = 24.0

    if abs(fps - 23.976) < 0.02:
        return 24, fps
    if abs(fps - 29.97) < 0.02:
        return 30, fps
    if abs(fps - 59.94) < 0.02:
        return 60, fps
    return max(1, int(round(fps))), fps


def _timecode_to_frames(tc: str, nominal_fps: int) -> int:
    raw = str(tc or "00:00:00:00").strip()
    is_drop = ";" in raw
    parts = raw.replace(";", ":").split(":")
    if len(parts) != 4:
        return 0
    try:
        h, m, s, f = [int(x) for x in parts]
    except ValueError:
        return 0

    nominal = max(1, int(nominal_fps))
    total = (h * 3600 + m * 60 + s) * nominal + f
    if is_drop and nominal in (30, 60):
        drop = 2 if nominal == 30 else 4
        total_minutes = h * 60 + m
        total -= drop * (total_minutes - total_minutes // 10)
    return int(total)


def current_record_frame(timeline: Any, project: Any) -> Tuple[int, str]:
    nominal, _ = _nominal_fps(project)
    current_tc = str(timeline.GetCurrentTimecode() or "")
    start_tc = ""
    try:
        start_tc = str(timeline.GetStartTimecode() or "")
    except Exception:
        pass
    start_frame = 0
    try:
        start_frame = int(timeline.GetStartFrame() or 0)
    except Exception:
        pass

    cur_abs = _timecode_to_frames(current_tc, nominal)
    if start_tc:
        start_abs = _timecode_to_frames(start_tc, nominal)
        return int(start_frame + (cur_abs - start_abs)), current_tc
    return cur_abs, current_tc


def _track_locked(timeline: Any, track_idx: int) -> bool:
    try:
        return bool(timeline.GetIsTrackLocked("video", track_idx))
    except Exception:
        return False


def _track_enabled(timeline: Any, track_idx: int) -> bool:
    try:
        val = timeline.GetIsTrackEnabled("video", track_idx)
        return True if val is None else bool(val)
    except Exception:
        return True


def _track_is_free(timeline: Any, track_idx: int, start_frame: int, duration: int) -> bool:
    end_frame = start_frame + duration
    try:
        items = timeline.GetItemListInTrack("video", track_idx) or []
    except Exception:
        items = []

    for it in items:
        try:
            s = int(it.GetStart())
            e = int(it.GetEnd())
            if s < end_frame and e > start_frame:
                return False
        except Exception:
            continue
    return True


def find_or_create_free_video_track(
    timeline: Any, start_frame: int, duration: int, preferred_track: int = 2
) -> Tuple[int, bool]:
    total_tracks = int(timeline.GetTrackCount("video") or 1)

    # 1. Try preferred track first (e.g. V2)
    if preferred_track <= total_tracks:
        if not _track_locked(timeline, preferred_track) and _track_enabled(timeline, preferred_track):
            if _track_is_free(timeline, preferred_track, start_frame, duration):
                return preferred_track, False

    # 2. Try higher tracks from preferred_track + 1 .. total_tracks
    for t in range(preferred_track + 1, total_tracks + 1):
        if not _track_locked(timeline, t) and _track_enabled(timeline, t):
            if _track_is_free(timeline, t, start_frame, duration):
                return t, False

    # 3. Try lower tracks 1 .. preferred_track - 1 (except V1 if preferred > 1 unless empty)
    for t in range(1, preferred_track):
        if not _track_locked(timeline, t) and _track_enabled(timeline, t):
            if _track_is_free(timeline, t, start_frame, duration):
                return t, False

    # 4. If no free track exists, add a new video track
    added = timeline.AddTrack("video")
    new_count = int(timeline.GetTrackCount("video") or (total_tracks + 1))
    return new_count, True


def _find_or_create_internal_bin(media_pool: Any, bin_name: str = INTERNAL_BIN_NAME) -> Any:
    root = media_pool.GetRootFolder()
    subfolders = root.GetSubFolderList() or []
    for f in subfolders:
        try:
            if f.GetName() == bin_name:
                return f
        except Exception:
            continue
    try:
        return media_pool.AddSubFolder(root, bin_name)
    except Exception:
        return root


def snapshot_video_timeline(timeline: Any) -> Dict[str, Tuple[int, int]]:
    snap: Dict[str, Tuple[int, int]] = {}
    total_tracks = int(timeline.GetTrackCount("video") or 0)
    for t in range(1, total_tracks + 1):
        items = timeline.GetItemListInTrack("video", t) or []
        for it in items:
            try:
                uid = it.GetUniqueId()
                if not uid:
                    uid = f"{t}_{it.GetName()}_{it.GetStart()}"
                snap[str(uid)] = (int(it.GetStart()), int(it.GetEnd()))
            except Exception:
                continue
    return snap


def _existing_snapshot_unchanged(timeline: Any, snapshot: Dict[str, Tuple[int, int]]) -> bool:
    total_tracks = int(timeline.GetTrackCount("video") or 0)
    for t in range(1, total_tracks + 1):
        items = timeline.GetItemListInTrack("video", t) or []
        for it in items:
            try:
                uid = it.GetUniqueId()
                if not uid:
                    uid = f"{t}_{it.GetName()}_{it.GetStart()}"
                key = str(uid)
                if key in snapshot:
                    orig_start, orig_end = snapshot[key]
                    if int(it.GetStart()) != orig_start or int(it.GetEnd()) != orig_end:
                        return False
            except Exception:
                continue
    return True


def _create_source_timeline(
    media_pool: Any,
    project: Any,
    main_timeline: Any,
    title_name: str,
    display_name: str = "Preset",
    duration_sec: float = 3.0,
    log: Optional[Any] = None,
) -> Tuple[Any, Any, Any, int]:
    nominal, _ = _nominal_fps(project)
    token = uuid.uuid4().hex[:6]
    safe = _safe_name(display_name)
    source_name = f"{SOURCE_TIMELINE_PREFIX}{token}_{safe}"

    source_tl = media_pool.CreateEmptyTimeline(source_name)
    if not source_tl:
        raise RuntimeError(f"Could not create temporary source timeline '{source_name}'.")

    try:
        source_tl.SetStartTimecode("00:00:00:00")
    except Exception:
        pass

    project.SetCurrentTimeline(source_tl)
    source_item = source_tl.InsertFusionTitleIntoTimeline(str(title_name))
    project.SetCurrentTimeline(main_timeline)

    if not source_item:
        raise RuntimeError(f"InsertFusionTitleIntoTimeline returned None for title '{title_name}'.")

    duration_frames = 0
    try:
        duration_frames = int(source_item.GetDuration())
    except Exception:
        pass
    if duration_frames <= 0:
        try:
            duration_frames = int(source_item.GetEnd()) - int(source_item.GetStart())
        except Exception:
            pass
    if duration_frames <= 0:
        duration_frames = max(24, int(duration_sec * nominal))

    source_media = source_tl.GetMediaPoolItem()
    if not source_media:
        raise RuntimeError(f"Could not get MediaPoolItem from source timeline '{source_name}'.")

    internal_bin = _find_or_create_internal_bin(media_pool, INTERNAL_BIN_NAME)
    try:
        media_pool.MoveClips([source_media], internal_bin)
    except Exception:
        pass

    return source_tl, source_media, source_item, duration_frames


def import_edit_preset_v2(
    title_name: str,
    display_name: Optional[str] = None,
    preferred_track: int = 2,
    record_frame: Optional[int] = None,
    duration_sec: float = 3.0,
    resolve_app: Optional[Any] = None,
    log_callback: Optional[Any] = None,
) -> Dict[str, Any]:
    def log(msg: str):
        if log_callback:
            log_callback(msg)
        else:
            print(msg)

    if resolve_app is None:
        from src.core.resolve_bridge import load_resolve_api
        resolve_app, _ = load_resolve_api()

    if not resolve_app:
        return {"ok": False, "error": "resolve_not_found", "message": "DaVinci Resolve không phản hồi."}

    try:
        page = str(resolve_app.GetCurrentPage() or "").lower()
        if page != "edit":
            resolve_app.OpenPage("edit")
            time.sleep(0.12)
    except Exception:
        pass

    pm = resolve_app.GetProjectManager()
    project = pm.GetCurrentProject() if pm else None
    if not project:
        return {"ok": False, "error": "project_not_found", "message": "Không tìm thấy dự án đang mở."}

    main_timeline = project.GetCurrentTimeline()
    if not main_timeline:
        return {"ok": False, "error": "timeline_not_found", "message": "Không tìm thấy Timeline đang mở."}

    media_pool = project.GetMediaPool()
    if not media_pool:
        return {"ok": False, "error": "media_pool_not_found", "message": "Không truy cập được Media Pool."}

    disp = display_name or title_name

    # 1. Determine record frame
    if record_frame is not None and record_frame >= 0:
        rec_frame = int(record_frame)
    else:
        rec_frame, _ = current_record_frame(main_timeline, project)

    # 2. Snapshot timeline state before insertion for rollback safety
    snapshot = snapshot_video_timeline(main_timeline)

    # 3. Create private source timeline (zero collision risk)
    try:
        source_tl, source_media, source_item, duration = _create_source_timeline(
            media_pool=media_pool,
            project=project,
            main_timeline=main_timeline,
            title_name=title_name,
            display_name=disp,
            duration_sec=duration_sec,
            log=log,
        )
    except Exception as e:
        return {"ok": False, "error": "source_timeline_creation_failed", "detail": str(e), "message": f"Lỗi tạo Source Timeline: {e}"}

    # 4. Find or create free video track
    target_track, track_was_added = find_or_create_free_video_track(
        timeline=main_timeline,
        start_frame=rec_frame,
        duration=duration,
        preferred_track=preferred_track,
    )

    # 5. Place via AppendToTimeline with precise track and frame
    try:
        placed = media_pool.AppendToTimeline([{
            "mediaPoolItem": source_media,
            "startFrame": 0,
            "endFrame": int(duration),
            "recordFrame": int(rec_frame),
            "trackIndex": int(target_track),
            "mediaType": 1,  # Video
        }])
    except Exception as e:
        return {"ok": False, "error": "append_failed", "detail": str(e), "message": f"AppendToTimeline thất bại: {e}"}

    if not placed:
        return {"ok": False, "error": "append_returned_none", "message": "AppendToTimeline không tạo được TimelineItem."}

    placed_item = placed[0]

    # 6. Safety check: ensure existing clips did not ripple/shift
    if not _existing_snapshot_unchanged(main_timeline, snapshot):
        # Rollback immediately!
        try:
            main_timeline.DeleteClips([placed_item], False)
        except Exception:
            pass
        return {
            "ok": False,
            "error": "ripple_detected_rollback",
            "message": "Phát hiện hiện tượng dạt timeline (ripple), hệ thống đã tự động rollback về nguyên trạng.",
        }

    log(f" ✔ Đã chèn '{disp}' an toàn lên Track V{target_track} tại Frame {rec_frame} (Zero Ripple).")
    return {
        "ok": True,
        "track": target_track,
        "start": rec_frame,
        "end": rec_frame + duration,
        "duration": duration,
        "track_added": track_was_added,
        "item_name": placed_item.GetName() if hasattr(placed_item, "GetName") else disp,
    }


def run_title_insert_worker_isolated(
    title_name: str,
    display_name: Optional[str] = None,
    preferred_track: int = 2,
    duration_sec: float = 3.0,
    record_frame: Optional[int] = None,
    timeout: float = 15.0,
) -> Dict[str, Any]:
    """
    Spawns title_insert_worker.py in a short-lived subprocess to isolate
    fusionscript / COM state from the main GUI process.
    Falls back to in-process import_edit_preset_v2 if subprocess cannot be launched.
    """
    cmd = [
        sys.executable,
        "-m",
        "src.core.title_insert_worker",
        "--title",
        str(title_name),
        "--preferred-track",
        str(preferred_track),
        "--duration",
        str(duration_sec),
    ]
    if display_name:
        cmd.extend(["--display-name", str(display_name)])
    if record_frame is not None and record_frame >= 0:
        cmd.extend(["--record-frame", str(int(record_frame))])

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        for line in proc.stdout.splitlines():
            if line.startswith("CHUNDVC_RESULT:"):
                payload_str = line[len("CHUNDVC_RESULT:"):].strip()
                return json.loads(payload_str)

        if proc.returncode != 0:
            return {
                "ok": False,
                "error": "worker_failed",
                "detail": proc.stderr or proc.stdout,
            }
    except Exception:
        pass

    # Clean fallback: execute in-process if subprocess failed or timed out
    return import_edit_preset_v2(
        title_name=title_name,
        display_name=display_name,
        preferred_track=preferred_track,
        record_frame=record_frame,
        duration_sec=duration_sec,
    )

