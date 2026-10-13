#!/usr/bin/env bash
# ==============================================================================
# ResolveFlow Assistant - Linux Launch Script
# ==============================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "======================================================="
echo "   Khởi động ResolveFlow Assistant (Linux Studio Edition)"
echo "======================================================="

# 1. DaVinci Resolve Studio Linux Environment
export RESOLVE_SCRIPT_API="/opt/resolve/Developer/Scripting"
export RESOLVE_SCRIPT_LIB="/opt/resolve/libs/Fusion/fusionscript.so"
export PYTHONPATH="${SCRIPT_DIR}:${RESOLVE_SCRIPT_API}/Modules:${PYTHONPATH}"

if [ -d "/opt/resolve/libs" ]; then
    export LD_LIBRARY_PATH="/opt/resolve/libs:${LD_LIBRARY_PATH}"
fi

# 2. Virtual Environment Detection
if [ -f "${SCRIPT_DIR}/.venv/bin/python" ]; then
    PYTHON_EXEC="${SCRIPT_DIR}/.venv/bin/python"
elif [ -f "${SCRIPT_DIR}/venv/bin/python" ]; then
    PYTHON_EXEC="${SCRIPT_DIR}/venv/bin/python"
else
    PYTHON_EXEC="python3"
fi

# 3. Check FFmpeg
if ! command -v ffmpeg &> /dev/null; then
    echo "⚠️  CẢNH BÁO: Không tìm thấy 'ffmpeg' trong hệ thống."
    echo "   Vui lòng chạy: sudo apt install -y ffmpeg"
    echo "   (Tính năng cắt nhịp BGM và xử lý media có thể bị gián đoạn)"
    echo ""
fi

# 4. Launch Application
echo "🚀 Đang khởi chạy với Python: $(${PYTHON_EXEC} --version) ..."
exec "${PYTHON_EXEC}" -m src.main "$@"
