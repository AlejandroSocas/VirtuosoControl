#!/bin/bash

# Check if running as root
if [ "$EUID" -ne 0 ]; then
  echo "Please run this script as root (sudo ./install.sh)"
  exit 1
fi

echo "======================================"
echo " Installing Corsair Virtuoso Control  "
echo "======================================"

# Check basic dependencies
echo "[*] Checking dependencies..."
for cmd in python3 pip; do
  if ! command -v $cmd &> /dev/null; then
    echo "Missing dependency: $cmd. Please install it first."
    exit 1
  fi
done

# Create installation directory
INSTALL_DIR="/opt/virtuoso-control"
echo "[*] Creating directory $INSTALL_DIR..."
mkdir -p "$INSTALL_DIR"

# Copy source files and assets. The app loads its own window/tray icon from
# beside the script, so this copy is still needed alongside the themed one.
echo "[*] Copying source files..."
cp virtuoso_control.py "$INSTALL_DIR/"
cp virtuoso_gui.py "$INSTALL_DIR/"
cp virtuoso_icon.png "$INSTALL_DIR/"

# Set permissions
chmod +x "$INSTALL_DIR/virtuoso_gui.py"
chmod +x "$INSTALL_DIR/virtuoso_control.py"
chmod 644 "$INSTALL_DIR/virtuoso_icon.png"

# Install the icon into the icon theme, under a NAME rather than a path.
#
# This used to be an absolute Icon=/opt/... path. Desktop shells cache the
# pixmap they loaded for a given path, so replacing the file in place left the
# old icon on screen until the shell was restarted — the icon looked like it
# had not been updated even though the file on disk had.
ICON_NAME="virtuoso-control"
echo "[*] Installing application icon..."
if python3 - "virtuoso_icon.png" "$ICON_NAME" <<'PY'
import os, sys
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage

src, name = sys.argv[1], sys.argv[2]
img = QImage(src)
if img.isNull():
    raise SystemExit(1)
for sz in (16, 22, 24, 32, 48, 64, 128, 256, 512):
    d = "/usr/share/icons/hicolor/%dx%d/apps" % (sz, sz)
    os.makedirs(d, exist_ok=True)
    img.scaled(sz, sz, Qt.AspectRatioMode.KeepAspectRatio,
               Qt.TransformationMode.SmoothTransformation
               ).save(os.path.join(d, name + ".png"))
PY
then
  echo "    themed icon sizes installed"
else
  echo "    (could not scale via PyQt6 — installing full size only)"
  install -Dm644 virtuoso_icon.png \
    "/usr/share/icons/hicolor/512x512/apps/$ICON_NAME.png"
fi

# Legacy path, still searched by every desktop when resolving a named icon.
install -Dm644 virtuoso_icon.png "/usr/share/pixmaps/$ICON_NAME.png"

# Copy udev rules
echo "[*] Installing udev rules..."
cp 99-corsair-virtuoso-hid.rules /etc/udev/rules.d/
chmod 644 /etc/udev/rules.d/99-corsair-virtuoso-hid.rules

# Reload udev to apply permissions immediately
udevadm control --reload-rules
udevadm trigger

# Create global .desktop shortcut
echo "[*] Creating system shortcut..."
DESKTOP_FILE="/usr/share/applications/virtuoso-control.desktop"
cat << EOF > "$DESKTOP_FILE"
[Desktop Entry]
Name=Virtuoso Control
Comment=Native control panel for Corsair Virtuoso SE headset
Exec=python3 $INSTALL_DIR/virtuoso_gui.py
Icon=$ICON_NAME
Terminal=false
Type=Application
Categories=AudioVideo;Audio;Settings;HardwareSettings;
StartupWMClass=virtuoso-control
EOF

chmod 644 "$DESKTOP_FILE"

# Invalidate the desktop caches, otherwise the menu keeps serving the icon and
# entry it cached the last time round.
echo "[*] Refreshing desktop caches..."
if command -v gtk-update-icon-cache &> /dev/null; then
  gtk-update-icon-cache -f -t /usr/share/icons/hicolor &> /dev/null || true
fi
if command -v update-desktop-database &> /dev/null; then
  update-desktop-database /usr/share/applications &> /dev/null || true
fi
# KDE's cache is per-user, so it must be rebuilt as the invoking user rather
# than as root — running it under sudo would only refresh root's cache.
if [ -n "$SUDO_USER" ] && command -v kbuildsycoca6 &> /dev/null; then
  sudo -u "$SUDO_USER" kbuildsycoca6 --noincremental &> /dev/null || true
fi

echo ""
echo "======================================"
echo " Installation completed successfully! "
echo "======================================"
echo "You can now find 'Virtuoso Control' in your application menu."
echo "To uninstall in the future, run: sudo ./uninstall.sh"
