import os
import sys
import subprocess

def build():
    print("🚀 Bắt đầu quá trình đóng gói ResolveFlow Assistant v4.1...")
    
    # Kiểm tra PyInstaller
    try:
        import PyInstaller
        print(f"✔ Tìm thấy PyInstaller phiên bản: {PyInstaller.__version__}")
    except ImportError:
        print("⚡ Đang cài đặt PyInstaller...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--clean",
        "--noconfirm",
        "resolveflow.spec"
    ]

    print("📦 Đang thực thi lệnh build:")
    print("   " + " ".join(cmd))
    res = subprocess.run(cmd)
    if res.returncode == 0:
        print("\n🎉 Đóng gói thành công!")
        print("👉 Thư mục kết quả: dist/ResolveFlow-Assistant/")
        print("👉 File chạy: dist/ResolveFlow-Assistant/ResolveFlow-Assistant.exe")
    else:
        print(f"\n❌ Lỗi trong quá trình build (Exit code: {res.returncode})")
        sys.exit(res.returncode)

if __name__ == "__main__":
    build()
