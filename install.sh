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

# Copy source files and assets
echo "[*] Copying source files..."
cp virtuoso_control.py "$INSTALL_DIR/"
cp virtuoso_gui.py "$INSTALL_DIR/"
cp virtuoso_icon.png "$INSTALL_DIR/"

# Set permissions
chmod +x "$INSTALL_DIR/virtuoso_gui.py"
chmod +x "$INSTALL_DIR/virtuoso_control.py"
chmod 644 "$INSTALL_DIR/virtuoso_icon.png"

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
Icon=$INSTALL_DIR/virtuoso_icon.png
Terminal=false
Type=Application
Categories=Audio;Settings;HardwareSettings;
StartupWMClass=virtuoso-control
EOF

chmod 644 "$DESKTOP_FILE"

echo ""
echo "======================================"
echo " Installation completed successfully! "
echo "======================================"
echo "You can now find 'Virtuoso Control' in your application menu."
echo "To uninstall in the future, run: sudo ./uninstall.sh"
