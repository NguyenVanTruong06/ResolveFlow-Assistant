#!/usr/bin/env bash
# ==============================================================================
# ResolveFlow-Assistant - Debian (.deb) Package Builder
# ==============================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

RAW_VER="${1:-1.0.0}"
VERSION="${RAW_VER#v}" # Bỏ tiền tố 'v' nếu có (v1.0.0 -> 1.0.0)

APP_NAME="resolveflow-assistant"
DIST_DIR="${SCRIPT_DIR}/dist/ResolveFlow-Assistant"
PKG_DIR="${SCRIPT_DIR}/build/deb_pkg"
DEB_NAME="${APP_NAME}_${VERSION}_amd64.deb"

echo "======================================================="
echo "   Đóng gói Debian Package (.deb) cho ResolveFlow"
echo "   Phiên bản: ${VERSION}"
echo "======================================================="

# 1. Kiểm tra bản build standalone đã tồn tại chưa
if [ ! -d "$DIST_DIR" ]; then
    echo "ℹ Chưa tìm thấy '${DIST_DIR}', đang tiến hành build standalone..."
    python3 build_standalone.py
fi

if [ ! -d "$DIST_DIR" ]; then
    echo "❌ Lỗi: Thư mục '${DIST_DIR}' không tồn tại sau khi build!"
    exit 1
fi

# 2. Dọn dẹp và tạo cấu trúc thư mục .deb
rm -rf "$PKG_DIR"
mkdir -p "${PKG_DIR}/opt/resolveflow-assistant"
mkdir -p "${PKG_DIR}/usr/local/bin"
mkdir -p "${PKG_DIR}/usr/share/applications"
mkdir -p "${PKG_DIR}/DEBIAN"

# 3. Sao chép nội dung ứng dụng vào /opt/resolveflow-assistant
echo "📁 Đang sao chép tệp chương trình vào cấu trúc gói..."
cp -r "${DIST_DIR}"/* "${PKG_DIR}/opt/resolveflow-assistant/"

# Đảm bảo launcher có quyền thực thi
chmod +x "${PKG_DIR}/opt/resolveflow-assistant/launch_resolveflow.sh" || true
chmod +x "${PKG_DIR}/opt/resolveflow-assistant/ResolveFlow-Assistant" || true

# 4. Tạo wrapper script trong /usr/local/bin/resolveflow-assistant
cat << 'EOF' > "${PKG_DIR}/usr/local/bin/resolveflow-assistant"
#!/usr/bin/env bash
exec /opt/resolveflow-assistant/launch_resolveflow.sh "$@"
EOF
chmod +x "${PKG_DIR}/usr/local/bin/resolveflow-assistant"

# 5. Tạo Desktop Entry
cat << EOF > "${PKG_DIR}/usr/share/applications/resolveflow-assistant.desktop"
[Desktop Entry]
Type=Application
Name=ResolveFlow Assistant
GenericName=AI Video Rough Cut & Asset Studio
Comment=AI-Powered Rough Cut & Asset Studio for DaVinci Resolve
Exec=/usr/local/bin/resolveflow-assistant
Icon=video-x-generic
Terminal=false
Categories=AudioVideo;Video;AudioVideoEditing;
Keywords=davinci;resolve;video;editing;ai;cut;
EOF
chmod 644 "${PKG_DIR}/usr/share/applications/resolveflow-assistant.desktop"

# 6. Tạo file điều khiển DEBIAN/control
cat << EOF > "${PKG_DIR}/DEBIAN/control"
Package: ${APP_NAME}
Version: ${VERSION}
Section: video
Priority: optional
Architecture: amd64
Depends: ffmpeg, libxcb-cursor0
Maintainer: Nguyen Van Truong <truong@chundvc.dev>
Description: AI-Powered Rough Cut & Asset Studio for DaVinci Resolve
 ResolveFlow Assistant (ChunDVC) is an AI-powered editing assistant that
 automates silence removal, speech cutouts, subtitles, beat drops, and
 imports timelines directly into DaVinci Resolve.
EOF

# 7. Đóng gói bằng dpkg-deb
echo "📦 Đang nén và tạo tệp .deb qua dpkg-deb..."
dpkg-deb --build "$PKG_DIR" "${SCRIPT_DIR}/${DEB_NAME}"

echo ""
echo "🎉 [THÀNH CÔNG] Đã tạo gói Debian:"
echo "   👉 Tệp cài đặt: ${SCRIPT_DIR}/${DEB_NAME}"
echo ""
echo "💡 Cách cài đặt gói trên Ubuntu / Debian / Linux Mint:"
echo "   sudo dpkg -i ${DEB_NAME}"
echo "   sudo apt-get install -f   # (Nếu cần tự sửa thiếu dependencies)"
echo ""
echo "💡 Cách gỡ cài đặt sau này:"
echo "   sudo apt remove ${APP_NAME}"
echo "======================================================="
