import os
import sys
import subprocess

# Đảm bảo mã hóa UTF-8 trên Windows console
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

def build():
    print("[INFO] Bat dau qua trinh dong goi ResolveFlow Assistant v4.1...")
    
    # Kiểm tra PyInstaller
    try:
        import PyInstaller
        print(f"[OK] Tim thay PyInstaller phien ban: {PyInstaller.__version__}")
    except ImportError:
        print("[INFO] Dang cai dat PyInstaller...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--clean",
        "--noconfirm",
        "resolveflow.spec"
    ]

    print("[BUILD] Dang thuc thi lenh build:")
    print("   " + " ".join(cmd))
    res = subprocess.run(cmd)
    if res.returncode == 0:
        print("\n[SUCCESS] Dong goi thanh cong!")
        print("  -> Thu muc ket qua: dist/ResolveFlow-Assistant/")
        print("  -> File chay: dist/ResolveFlow-Assistant/ResolveFlow-Assistant.exe")
    else:
        print(f"\n[ERROR] Loi trong qua trinh build (Exit code: {res.returncode})")
        sys.exit(res.returncode)

if __name__ == "__main__":
    build()
