#!/usr/bin/env bash
# ==============================================================================
# ResolveFlow Assistant - Linux Launch Script (Production Hardened)
# ==============================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "======================================================="
echo "   ResolveFlow-Assistant v1.0.0 (Linux Pro Edition)   "
echo "======================================================="

# 1. Qt6 Configuration (XCB Display Server & FFmpeg Multimedia Backend)
export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-xcb}"
export QT_MEDIA_BACKEND="${QT_MEDIA_BACKEND:-ffmpeg}"

# 2. DaVinci Resolve Studio Linux Environment
export RESOLVE_SCRIPT_API="${RESOLVE_SCRIPT_API:-/opt/resolve/Developer/Scripting}"

# Tìm kiếm ứng viên fusionscript.so / libfusionscript.so
if [ -z "$RESOLVE_SCRIPT_LIB" ]; then
    for cand in \
        "/opt/resolve/libs/Fusion/fusionscript.so" \
        "/opt/resolve/libs/Fusion/libfusionscript.so" \
        "/opt/resolve/libs/fusionscript.so" \
        "/opt/resolve/libs/libfusionscript.so"; do
        if [ -f "$cand" ]; then
            export RESOLVE_SCRIPT_LIB="$cand"
            break
        fi
    done
    export RESOLVE_SCRIPT_LIB="${RESOLVE_SCRIPT_LIB:-/opt/resolve/libs/Fusion/fusionscript.so}"
fi

export PYTHONPATH="${SCRIPT_DIR}:${RESOLVE_SCRIPT_API}/Modules:${PYTHONPATH}"

if [ -d "/opt/resolve/libs" ]; then
    export LD_LIBRARY_PATH="/opt/resolve/libs:${LD_LIBRARY_PATH}"
fi

# 3. Tự động liên kết thư viện CUDA / cuDNN trong .venv (nếu có)
for nv_lib in "${SCRIPT_DIR}"/.venv/lib/python*/site-packages/nvidia/*/lib; do
    if [ -d "$nv_lib" ]; then
        export LD_LIBRARY_PATH="${nv_lib}:${LD_LIBRARY_PATH}"
    fi
done

# 4. Virtual Environment Detection
if [ -x "${SCRIPT_DIR}/.venv/bin/python" ] && "${SCRIPT_DIR}/.venv/bin/python" -c "pass" 2>/dev/null; then
    PYTHON_EXEC="${SCRIPT_DIR}/.venv/bin/python"
    export PATH="${SCRIPT_DIR}/.venv/bin:${PATH}"
elif [ -d "${SCRIPT_DIR}/.venv/lib" ]; then
    # Hỗ trợ phân vùng NTFS mount nơi symlink .venv/bin/python bị lỗi reparse tag trên Linux
    for sp in "${SCRIPT_DIR}"/.venv/lib/python*/site-packages; do
        if [ -d "$sp" ]; then
            export PYTHONPATH="${sp}:${PYTHONPATH}"
        fi
    done
    PYTHON_EXEC="python3"
elif [ -x "${SCRIPT_DIR}/venv/bin/python" ] && "${SCRIPT_DIR}/venv/bin/python" -c "pass" 2>/dev/null; then
    PYTHON_EXEC="${SCRIPT_DIR}/venv/bin/python"
    export PATH="${SCRIPT_DIR}/venv/bin:${PATH}"
else
    PYTHON_EXEC="python3"
fi

# 5. Check FFmpeg (Hỗ trợ bundled static_ffmpeg trong .venv)
for static_ff in "${SCRIPT_DIR}"/.venv/lib/python*/site-packages/static_ffmpeg/bin/linux; do
    if [ -d "$static_ff" ]; then
        export PATH="${static_ff}:${PATH}"
        break
    fi
done

if ! command -v ffmpeg &> /dev/null; then
    echo "⚠️  CẢNH BÁO: Không tìm thấy 'ffmpeg' trong hệ thống."
    echo "   Vui lòng chạy: sudo apt install -y ffmpeg"
    echo "   (Tính năng cắt nhịp BGM và xử lý media có thể bị gián đoạn)"
    echo ""
else
    echo "🎬 Đã phát hiện FFmpeg: $(command -v ffmpeg)"
fi

# 6. Launch Application
echo "🚀 Đang khởi chạy với Python: $(${PYTHON_EXEC} --version) ..."
exec "${PYTHON_EXEC}" -m src.main "$@"
