#!/usr/bin/env bash
# ==============================================================================
# ResolveFlow-Assistant - Debian (.deb) Package Builder
# ==============================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

VERSION=${1:-"1.0.0"}
VERSION="${VERSION#v}" # Strip 'v' prefix if present (v1.0.0 -> 1.0.0)
VERSION="${VERSION:-"1.0.0"}"

APP_NAME="resolveflow-assistant"
DIST_DIR="${SCRIPT_DIR}/dist/ResolveFlow-Assistant"
PKG_DIR="${SCRIPT_DIR}/build/deb_pkg"
DEB_NAME="${APP_NAME}_${VERSION}_amd64.deb"
DEB_TARGET="${SCRIPT_DIR}/dist/${DEB_NAME}"

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
mkdir -p "${PKG_DIR}/usr/share/icons/hicolor/512x512/apps"
mkdir -p "${PKG_DIR}/usr/share/pixmaps"
mkdir -p "${PKG_DIR}/DEBIAN"
mkdir -p "${SCRIPT_DIR}/dist"

# 3. Sao chép nội dung ứng dụng vào /opt/resolveflow-assistant
echo "📁 Đang sao chép tệp chương trình vào cấu trúc gói..."
cp -r "${DIST_DIR}"/* "${PKG_DIR}/opt/resolveflow-assistant/"

# Sao chép icon ứng dụng chính thức của ChunDVC
if [ -f "${SCRIPT_DIR}/assets/branding/chundvc_512.png" ]; then
    cp "${SCRIPT_DIR}/assets/branding/chundvc_512.png" "${PKG_DIR}/usr/share/icons/hicolor/512x512/apps/chundvc.png"
    cp "${SCRIPT_DIR}/assets/branding/chundvc_512.png" "${PKG_DIR}/usr/share/pixmaps/chundvc.png"
    cp "${SCRIPT_DIR}/assets/branding/chundvc_512.png" "${PKG_DIR}/opt/resolveflow-assistant/chundvc.png"
fi

# Đảm bảo launcher có quyền thực thi
chmod +x "${PKG_DIR}/opt/resolveflow-assistant/launch_resolveflow.sh" || true
chmod +x "${PKG_DIR}/opt/resolveflow-assistant/ResolveFlow-Assistant" || true

# 4. Tạo wrapper script trong /usr/local/bin
cat << 'EOF' > "${PKG_DIR}/usr/local/bin/chundvc"
#!/usr/bin/env bash
exec /opt/resolveflow-assistant/launch_resolveflow.sh "$@"
EOF
chmod +x "${PKG_DIR}/usr/local/bin/chundvc"

cat << 'EOF' > "${PKG_DIR}/usr/local/bin/resolveflow-assistant"
#!/usr/bin/env bash
exec /opt/resolveflow-assistant/launch_resolveflow.sh "$@"
EOF
chmod +x "${PKG_DIR}/usr/local/bin/resolveflow-assistant"

# 5. Tạo Desktop Entry chính thức mang tên ChunDVC và icon sắc nét
cat << EOF > "${PKG_DIR}/usr/share/applications/chundvc.desktop"
[Desktop Entry]
Type=Application
Name=ChunDVC
GenericName=Trợ lý Dựng Phim DaVinci Resolve AI
Comment=ChunDVC - Trợ lý Dựng Phim AI & Kho Đạo Cụ cho DaVinci Resolve
Exec=/usr/local/bin/chundvc
Icon=chundvc
Terminal=false
Categories=AudioVideo;Video;AudioVideoEditing;
Keywords=chundvc;davinci;resolve;video;editing;ai;cut;roughcut;studio;
StartupWMClass=ChunDVC
EOF
chmod 644 "${PKG_DIR}/usr/share/applications/chundvc.desktop"
cp -f "${PKG_DIR}/usr/share/applications/chundvc.desktop" "${PKG_DIR}/usr/share/applications/resolveflow-assistant.desktop"

# 6. Tạo file điều khiển DEBIAN/control
cat << EOF > "${PKG_DIR}/DEBIAN/control"
Package: ${APP_NAME}
Version: ${VERSION}
Section: video
Priority: optional
Architecture: amd64
Depends: ffmpeg, libxcb-cursor0, zstd
Maintainer: Nguyen Van Truong <truong@chundvc.dev>
Description: AI-Powered Rough Cut & Asset Studio for DaVinci Resolve
 ResolveFlow Assistant (ChunDVC) is an AI-powered editing assistant that
 automates silence removal, speech cutouts, subtitles, beat drops, and
 imports timelines directly into DaVinci Resolve.
EOF

# 7. Đóng gói bằng dpkg-deb vào dist/
echo "📦 Đang nén và tạo tệp .deb qua dpkg-deb..."
dpkg-deb --build "$PKG_DIR" "${DEB_TARGET}"

# Sao chép ra thư mục gốc nếu cần tương thích ngược
cp -f "${DEB_TARGET}" "${SCRIPT_DIR}/${DEB_NAME}" 2>/dev/null || true

# 8. Tự động sinh dist/SHA256SUMS chứa SHA-256 checksum của resolveflow-assistant_${VERSION}_amd64.deb
echo "🔒 Đang tạo dist/SHA256SUMS..."
(cd "${SCRIPT_DIR}/dist" && sha256sum "${DEB_NAME}" > SHA256SUMS)

echo ""
echo "🎉 [THÀNH CÔNG] Đã tạo gói Debian:"
echo "   👉 Tệp cài đặt: ${DEB_TARGET}"
echo "   👉 Checksum: ${SCRIPT_DIR}/dist/SHA256SUMS"
echo ""
echo "💡 Cách cài đặt gói trên Ubuntu / Debian / Linux Mint:"
echo "   sudo dpkg -i dist/${DEB_NAME}"
echo "   sudo apt-get install -f   # (Nếu cần tự sửa thiếu dependencies)"
echo ""
echo "💡 Cách gỡ cài đặt sau này:"
echo "   sudo apt remove ${APP_NAME}"
echo "======================================================="
