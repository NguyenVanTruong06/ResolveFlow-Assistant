"""
ResolveFlow Assistant - Connection & Environment Test Script
Tương thích 100% với DaVinci Resolve Free & Studio
"""
import sys

# Đảm bảo in các ký tự tiếng Việt không bị crash trên Console Windows
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

def main():
    print("=" * 45)
    print(" [ResolveFlow Assistant] - KIỂM TRA MÔI TRƯỜNG")
    print("=" * 45)

    # Thử lấy đối tượng 'resolve' được inject sẵn (khi chạy bên trong menu DaVinci Resolve)
    current_resolve = None
    try:
        current_resolve = resolve
    except NameError:
        pass

    # Nếu không tìm thấy (chạy từ bên ngoài), thử kết nối qua API FusionScript
    if not current_resolve:
        import os
        import sys
        resolve_script_path = r"C:\ProgramData\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting\Modules"
        if os.path.exists(resolve_script_path) and resolve_script_path not in sys.path:
            sys.path.append(resolve_script_path)
            
        try:
            import DaVinciResolveScript as dvr_script
            current_resolve = dvr_script.scriptapp("Resolve")
        except (ImportError, AttributeError):
            pass

    if not current_resolve:
        print("[LỖI] Không thể kết nối tới DaVinci Resolve.")
        print("  - Nếu chạy từ ngoài: Đảm bảo DaVinci Resolve đang mở và đã bật 'External scripting using: Local/Network' trong Cài đặt (Preferences).")
        print("  - Nếu chạy từ trong Resolve: Hãy chạy script từ menu 'Không gian làm việc > Tập lệnh'.")
        return

    # 1. Kiểm tra thông tin phiên bản Resolve
    version = current_resolve.GetVersionString()
    product = current_resolve.GetProductName()
    print(f"[OK] Ứng dụng: {product} (Version: {version})")

    # 2. Kiểm tra Project Manager & Dự án hiện tại
    pm = current_resolve.GetProjectManager()
    project = pm.GetCurrentProject()
    
    if not project:
        print("[CẢNH BÁO] Bạn chưa mở Project nào. Hãy tạo hoặc mở 1 Project trước khi chạy tool.")
        return

    project_name = project.GetName()
    print(f"[OK] Dự án đang mở: {project_name}")

    # 3. Kiểm tra Media Pool & Timeline
    media_pool = project.GetMediaPool()
    root_folder = media_pool.GetRootFolder()
    timeline = project.GetCurrentTimeline()
    
    print(f"[OK] Thư mục gốc Media Pool: {root_folder.GetName()}")
    if timeline:
        print(f"[OK] Timeline đang chọn: {timeline.GetName()} (FPS: {timeline.GetSetting('timelineFrameRate')})")
    else:
        print("[INFO] Hiện chưa có Timeline nào được mở trên Edit Page.")

    print("-" * 45)
    print("=> KẾT NỐI API THÀNH CÔNG! SẴN SÀNG PHÁT TRIỂN MODULE TIẾP THEO.")
    print("=" * 45)

if __name__ == "__main__":
    main()