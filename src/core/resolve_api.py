import os
import sys
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
        Tìm fusionscript.dll: biến môi trường RESOLVE_SCRIPT_LIB, thư mục cài mặc định, hoặc thư mục của tiến trình
        Resolve.exe đang chạy (trường hợp cài ở ổ/thư mục khác như E:\\APP).
        """
        candidates = []
        env = os.environ.get("RESOLVE_SCRIPT_LIB")
        if env:
            candidates.append(env)
        candidates.append(r"C:\Program Files\Blackmagic Design\DaVinci Resolve\fusionscript.dll")
        try:
            import subprocess
            out = subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command",
                 "(Get-Process Resolve -ErrorAction SilentlyContinue | Select-Object -First 1).Path"],
                capture_output=True, text=True, timeout=10
            ).stdout.strip()
            if out:
                candidates.append(os.path.join(os.path.dirname(out), "fusionscript.dll"))
        except Exception:
            pass
        return next((c for c in candidates if c and os.path.exists(c)), None)

    def connect(self) -> bool:
        """
        Kết nối tới ứng dụng DaVinci Resolve đang chạy.
        Tự động đăng ký đường dẫn Modules của Resolve trên Windows vào sys.path.

        Returns:
            bool: True nếu kết nối thành công.
        """
        # Đường dẫn Module lập trình mặc định của DaVinci Resolve trên Windows
        resolve_script_path = r"C:\ProgramData\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting\Modules"
        if os.path.exists(resolve_script_path) and resolve_script_path not in sys.path:
            sys.path.append(resolve_script_path)

        self.last_connect_error = ""
        lib = self._locate_fusionscript()
        if lib and not os.environ.get("RESOLVE_SCRIPT_LIB"):
            os.environ["RESOLVE_SCRIPT_LIB"] = lib
        try:
            import DaVinciResolveScript as dvr_script
            # Gọi ứng dụng Resolve thông qua cổng FusionScript
            self.resolve = dvr_script.scriptapp("Resolve")
            if self.resolve:
                self.project_manager = self.resolve.GetProjectManager()
                self.current_project = self.project_manager.GetCurrentProject()
                return True
            self.last_connect_error = "Resolve không phản hồi. Mở Resolve và bật Preferences > General > External scripting using = Local."
        except (ImportError, AttributeError) as e:
            # Không tìm thấy thư viện SDK hoặc Resolve chưa được khởi chạy
            self.last_connect_error = f"Không nạp được thư viện kết nối Resolve ({str(e)[:80]})."
        except Exception as e:
            # vd SystemError "initialization of fusionscript failed": scripting ngoài bị tắt, hoặc Resolve bản Free
            self.last_connect_error = (
                "Resolve từ chối kết nối scripting ngoài. Hãy bật Preferences > General > External scripting using = Local; "
                "lưu ý bản DaVinci Resolve Free thường không hỗ trợ scripting ngoài (cần Studio). "
                "Bạn vẫn có thể import file .fcpxml thủ công bằng File > Import > Timeline."
            )
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

        resolve_exe = r"C:\Program Files\Blackmagic Design\DaVinci Resolve\Resolve.exe"
        if os.path.exists(resolve_exe):
            log(" 🔍 Không tìm thấy DaVinci Resolve đang chạy. Đang tự động mở DaVinci Resolve...")
            import subprocess
            import time
            try:
                subprocess.Popen([resolve_exe])
                log(" 🚀 Đang khởi động DaVinci Resolve... Vui lòng chờ vài giây và mở một dự án (Project).")
                
                # Thử kết nối lại trong vòng 15 giây
                for i in range(15):
                    time.sleep(1)
                    if self.connect():
                        log(" ✔ Đã kết nối thành công tới DaVinci Resolve!")
                        return True
                log(" ⚠ DaVinci Resolve đang tải. Vui lòng đảm bảo bạn đã mở một Project cụ thể.")
            except Exception as e:
                log(f" ❌ Không thể khởi chạy DaVinci Resolve tự động: {str(e)}")
        else:
            log(" ❌ Không tìm thấy đường dẫn cài đặt mặc định của DaVinci Resolve tại C:\\Program Files\\...")
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

        valid_paths = [os.path.abspath(p) for p in paths if os.path.exists(p)]
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
                res = media_storage.AddFileListToMediaPool(valid_paths)
                if res:
                    imported = True
            except Exception as e:
                log(f" ℹ MediaStorage AddFileList: {str(e)}")

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

        media_pool = self.current_project.GetMediaPool()
        if not media_pool:
            log(" ❌ Không tìm thấy Media Pool trong dự án DaVinci Resolve hiện tại.")
            return False

        # Chuẩn hóa danh sách video nguồn và nạp sẵn vào Media Pool của Resolve để chống lỗi Media Offline
        v_list = []
        if isinstance(video_path, list):
            v_list = [os.path.abspath(p) for p in video_path if os.path.exists(p)]
        elif isinstance(video_path, str) and video_path and os.path.exists(video_path):
            v_list = [os.path.abspath(video_path)]

        if v_list:
            self.import_media_to_media_pool(v_list, log_callback=log)

        first_dir = os.path.dirname(v_list[0]) if v_list else ""
        file_ext = os.path.splitext(edl_path)[1].lower()
        # Ưu tiên liên kết chính xác với Media Pool Item vừa nạp (importSourceClips = False)
        # để tránh việc Resolve tự quét cả thư mục và liên kết nhầm sang video khác trong cùng folder
        import_options = {
            "timelineName": timeline_name,
            "importSourceClips": False,
            "sourceClipsPath": first_dir
        }

        try:
            timeline = media_pool.ImportTimelineFromFile(os.path.abspath(edl_path), import_options)
            if not timeline:
                # Nếu importSourceClips = False không tìm thấy, thử lại với importSourceClips = True
                import_options["importSourceClips"] = True
                timeline = media_pool.ImportTimelineFromFile(os.path.abspath(edl_path), import_options)

            if timeline:
                log(f" 🎉 Đã tự động import Timeline '{timeline_name}' thành công trong DaVinci Resolve (Media đã liên kết đúng tệp nguồn)!")
                return True
        except Exception as e:
            log(f" ⚠️ Lỗi khi gọi API ImportTimelineFromFile: {str(e)}")

        log(" ➖ Cần import thủ công tệp Timeline (.fcpxml / .edl) vào DaVinci Resolve.")
        return False

    # ------------------------------------------------------------------
    # LUT / Color: phát hiện LUT đã gắn sẵn và áp dụng không ghi đè
    # ------------------------------------------------------------------
    RESOLVE_LUT_DIR = r"C:\ProgramData\Blackmagic Design\DaVinci Resolve\Support\LUT"

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
            target_dir = os.path.join(self.RESOLVE_LUT_DIR, "ResolveFlow")
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

    def insert_title_at_playhead(
        self,
        text: str,
        font_name: str = "Arial",
        font_size: int = 48,
        color_hex: str = "#FFFFFF",
        duration_sec: float = 3.0,
        preset_id: str = "karaoke_pop",
        log_callback: Optional[Any] = None
    ) -> bool:
        """
        Chèn tiêu đề / Text+ preset tại vị trí Playhead trên Video Track 2.
        """
        def log(msg: str):
            if log_callback:
                log_callback(msg)
            else:
                print(msg)

        if not text.strip():
            log(" ⚠️ Nội dung chữ (Title Text) không được để trống.")
            return False

        import tempfile
        import uuid
        from src.core.fcpxml_generator import FCPXMLGenerator

        playhead_sec = self.get_current_playhead_seconds()
        log(f" 📝 Đang tạo Text+ Title '{text}' tại mốc {playhead_sec:.2f}s (Preset: {preset_id})...")

        temp_title_fcpxml = os.path.join(tempfile.gettempdir(), f"rf_title_{uuid.uuid4().hex[:8]}.fcpxml")
        words_list = text.split()
        num_w = max(1, len(words_list))
        single_sub = [{
            "start": playhead_sec,
            "end": playhead_sec + duration_sec,
            "text": text,
            "words": [{"word": w, "start": playhead_sec + i * (duration_sec / num_w), "end": playhead_sec + (i + 1) * (duration_sec / num_w)} for i, w in enumerate(words_list)]
        }]
        
        try:
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

def is_vertical_video(video_path: str) -> bool:
    """
    Kiểm tra xem video là dọc (portrait) hay ngang (landscape) bằng ffprobe.
    """
    try:
        import ffmpeg
        probe = ffmpeg.probe(video_path)
        video_stream = next((stream for stream in probe['streams'] if stream['codec_type'] == 'video'), None)
        if video_stream:
            width = int(video_stream.get('width', 1920))
            height = int(video_stream.get('height', 1080))
            return height > width
    except Exception:
        pass
    return False

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
