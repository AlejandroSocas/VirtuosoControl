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

echo "[*] Stopping running Virtuoso Control / PipeWire DSP processes..."
pkill -f "/opt/virtuoso-control/virtuoso_gui.py" 2>/dev/null || true
pkill -f "virtuoso-filter-chain.conf" 2>/dev/null || true

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

USER_HOME=""
if [ -n "$SUDO_USER" ]; then
  USER_HOME=$(getent passwd "$SUDO_USER" | cut -d: -f6)
fi
if [ -z "$USER_HOME" ]; then
  USER_HOME="$HOME"
fi

if [ -n "$USER_HOME" ]; then
  echo "[*] Removing autostart entry and generated PipeWire DSP files..."
  rm -f "$USER_HOME/.config/autostart/virtuoso-control.desktop"
  rm -f "$USER_HOME/.config/pipewire/virtuoso-filter-chain.conf"
  rm -f "$USER_HOME/.config/pipewire/virtuoso-hrir-7.1.wav"
fi

echo "[*] Removing temporary runtime files..."
rm -f /tmp/virtuoso-dsp.log /tmp/virtuoso-71-test.wav /tmp/virtuoso-mic-on.wav /tmp/virtuoso-mic-off.wav

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
echo "Note: User preferences (~/.config/VirtuosoControl/, ~/.config/AlejandroSocas/)"
echo "and any exported EasyEffects presets were preserved."
