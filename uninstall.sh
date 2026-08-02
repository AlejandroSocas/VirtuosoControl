#!/bin/bash

# Check if running as root
if [ "$EUID" -ne 0 ]; then
  echo "Please run this script as root (sudo ./uninstall.sh)"
  exit 1
fi

echo "======================================"
echo " Uninstalling Corsair Virtuoso Control"
echo "======================================"

INSTALL_DIR="/opt/virtuoso-control"
DESKTOP_FILE="/usr/share/applications/virtuoso-control.desktop"
UDEV_RULE="/etc/udev/rules.d/99-corsair-virtuoso-hid.rules"

echo "[*] Removing source files from $INSTALL_DIR..."
rm -rf "$INSTALL_DIR"

echo "[*] Removing shortcut..."
rm -f "$DESKTOP_FILE"

echo "[*] Removing application icons..."
ICON_NAME="virtuoso-control"
for SZ in 16 22 24 32 48 64 128 256 512; do
  rm -f "/usr/share/icons/hicolor/${SZ}x${SZ}/apps/$ICON_NAME.png"
done
rm -f "/usr/share/pixmaps/$ICON_NAME.png"

echo "[*] Removing udev rules..."
rm -f "$UDEV_RULE"

# Reload udev
udevadm control --reload-rules
udevadm trigger

# Same cache refresh as the installer, so the entry disappears from the menu
# straight away instead of lingering until the next login.
echo "[*] Refreshing desktop caches..."
if command -v gtk-update-icon-cache &> /dev/null; then
  gtk-update-icon-cache -f -t /usr/share/icons/hicolor &> /dev/null || true
fi
if command -v update-desktop-database &> /dev/null; then
  update-desktop-database /usr/share/applications &> /dev/null || true
fi
if [ -n "$SUDO_USER" ] && command -v kbuildsycoca6 &> /dev/null; then
  sudo -u "$SUDO_USER" kbuildsycoca6 --noincremental &> /dev/null || true
fi

echo ""
echo "======================================"
echo " Uninstallation completed successfully! "
echo "======================================"
