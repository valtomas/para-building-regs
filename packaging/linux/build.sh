#!/bin/sh
# Packages the PyInstaller build (dist/Para) for Linux, so people install it with a double-click:
#   dist/Para-linux.deb       Ubuntu, Debian, Mint, Pop!_OS: opens in the software centre, which installs it
#   dist/Para-linux.AppImage  other distributions: one file that runs where it is, nothing to install
# Run from the repository root after `pyinstaller packaging/app.spec`. Needs dpkg-deb and curl.
set -e
VERSION=$(python3 -c "import para; print(para.VERSION)")
APP=dist/Para
WORK=build/linux-packages
APPIMAGETOOL_URL=https://github.com/AppImage/appimagetool/releases/download/1.9.1/appimagetool-x86_64.AppImage
rm -rf "$WORK" && mkdir -p "$WORK"

desktop_entry() {  # $1: the command that starts Para
  cat <<EOF
[Desktop Entry]
Type=Application
Name=Para
GenericName=Building regulations
Comment=Answers about the building regulations in England, cited to the paragraph
Exec=$1
Icon=para
Terminal=false
Categories=Office;
Keywords=building;regulations;approved;documents;
StartupWMClass=Para
EOF
}

# --- .deb: the app in /opt/para, a menu entry, an icon, and `para` on the PATH ---
DEB="$WORK/deb"
mkdir -p "$DEB/DEBIAN" "$DEB/opt" "$DEB/usr/bin" "$DEB/usr/share/applications" \
         "$DEB/usr/share/icons/hicolor/512x512/apps"
cp -a "$APP" "$DEB/opt/para"
rm -f "$DEB/opt/para/install.sh"  # for the tarball only
ln -s /opt/para/Para "$DEB/usr/bin/para"
desktop_entry /opt/para/Para > "$DEB/usr/share/applications/para.desktop"
cp packaging/icon.png "$DEB/usr/share/icons/hicolor/512x512/apps/para.png"
# Qt and its web engine are bundled; these are the system libraries they open, present on any desktop install.
cat > "$DEB/DEBIAN/control" <<EOF
Package: para
Version: $VERSION
Architecture: amd64
Maintainer: Tomas Val <valtomas@gmail.com>
Section: misc
Priority: optional
Homepage: https://pararegs.co.uk
Installed-Size: $(du -sk "$DEB" | cut -f1)
Depends: libc6 (>= 2.35), libegl1, libgl1, libnss3, libxkbcommon0, libfontconfig1, libdbus-1-3
Description: Answers about the building regulations in England, cited to the paragraph
 Para searches a bundled library of the Approved Documents and building regulations with an AI model you
 choose, and cites the exact paragraph behind every answer.
EOF
chmod -R u+rwX,go+rX,go-w "$DEB"  # whatever the build machine's umask
# Chats, settings and the person's files live in their home folder, so removing the package leaves them alone.
dpkg-deb --root-owner-group -Zxz --build "$DEB" dist/Para-linux.deb

# --- AppImage: the same app with its menu entry and icon, in one file ---
AD="$WORK/Para.AppDir"
mkdir -p "$AD/usr/lib"
cp -a "$APP" "$AD/usr/lib/para"
rm -f "$AD/usr/lib/para/install.sh"
desktop_entry Para > "$AD/para.desktop"
cp packaging/icon.png "$AD/para.png"
cat > "$AD/AppRun" <<'EOF'
#!/bin/sh
exec "$(dirname "$(readlink -f "$0")")/usr/lib/para/Para" "$@"
EOF
chmod +x "$AD/AppRun"
TOOL="$WORK/appimagetool"
curl -fsSL -o "$TOOL" "$APPIMAGETOOL_URL" && chmod +x "$TOOL"
# Extract-and-run: build machines (CI containers) often have no FUSE to mount the tool's own AppImage.
APPIMAGE_EXTRACT_AND_RUN=1 ARCH=x86_64 "$TOOL" --no-appstream "$AD" dist/Para-linux.AppImage

ls -la dist/Para-linux.deb dist/Para-linux.AppImage
