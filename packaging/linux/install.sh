#!/bin/sh
# Install Para for this user: copies the app to ~/.local/opt/para and adds it to the applications menu.
# Run from the extracted folder:  ./install.sh      Remove with:  ./install.sh --uninstall
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/.local/opt/para"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
BIN="$HOME/.local/bin"

if [ "$1" = "--uninstall" ]; then
  rm -rf "$DEST" "$APPS/para.desktop" "$BIN/para"
  echo "Para removed. Your chats, settings and files are kept in ~/.local/share/para and ~/Para Files."
  exit 0
fi

mkdir -p "$DEST" "$APPS" "$BIN"
cp -a "$HERE/." "$DEST/"
ln -sf "$DEST/Para" "$BIN/para"
cat > "$APPS/para.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Para
Comment=Building regulations, cited
Exec=$DEST/Para
Icon=accessories-dictionary
Terminal=false
Categories=Office;Education;
EOF
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$APPS" >/dev/null 2>&1 || true
echo "Para installed. Open it from your applications menu, or run: para"
