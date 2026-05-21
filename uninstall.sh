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

echo "[*] Removing udev rules..."
rm -f "$UDEV_RULE"

# Reload udev
udevadm control --reload-rules
udevadm trigger

echo ""
echo "======================================"
echo " Uninstallation completed successfully! "
echo "======================================"
