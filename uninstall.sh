#!/bin/bash

# Comprobar si se ejecuta como root
if [ "$EUID" -ne 0 ]; then
  echo "Por favor, ejecuta este script como root (sudo ./uninstall.sh)"
  exit 1
fi

echo "======================================"
echo " Desinstalando Corsair Virtuoso Control"
echo "======================================"

INSTALL_DIR="/opt/virtuoso-control"
DESKTOP_FILE="/usr/share/applications/virtuoso-control.desktop"
UDEV_RULE="/etc/udev/rules.d/99-corsair-virtuoso-hid.rules"

echo "[*] Eliminando archivos fuente de $INSTALL_DIR..."
rm -rf "$INSTALL_DIR"

echo "[*] Eliminando acceso directo..."
rm -f "$DESKTOP_FILE"

echo "[*] Eliminando reglas udev..."
rm -f "$UDEV_RULE"

# Recargar udev
udevadm control --reload-rules
udevadm trigger

echo ""
echo "======================================"
echo " ¡Desinstalación completada con éxito!  "
echo "======================================"
