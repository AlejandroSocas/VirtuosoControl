#!/bin/bash

# Comprobar si se ejecuta como root
if [ "$EUID" -ne 0 ]; then
  echo "Por favor, ejecuta este script como root (sudo ./install.sh)"
  exit 1
fi

echo "======================================"
echo " Instalando Corsair Virtuoso Control "
echo "======================================"

# Comprobar dependencias básicas
echo "[*] Comprobando dependencias..."
for cmd in python3 pip; do
  if ! command -v $cmd &> /dev/null; then
    echo "Falta dependencia: $cmd. Por favor instálalo primero."
    exit 1
  fi
done

# Crear directorio de instalación
INSTALL_DIR="/opt/virtuoso-control"
echo "[*] Creando directorio $INSTALL_DIR..."
mkdir -p "$INSTALL_DIR"

# Copiar archivos de código y recursos
echo "[*] Copiando archivos fuente..."
cp virtuoso_control.py "$INSTALL_DIR/"
cp virtuoso_gui.py "$INSTALL_DIR/"
cp virtuoso_icon.png "$INSTALL_DIR/"

# Dar permisos
chmod +x "$INSTALL_DIR/virtuoso_gui.py"
chmod +x "$INSTALL_DIR/virtuoso_control.py"
chmod 644 "$INSTALL_DIR/virtuoso_icon.png"

# Copiar regla udev
echo "[*] Instalando reglas udev..."
cp 99-corsair-virtuoso-hid.rules /etc/udev/rules.d/
chmod 644 /etc/udev/rules.d/99-corsair-virtuoso-hid.rules

# Recargar udev para aplicar permisos de inmediato
udevadm control --reload-rules
udevadm trigger

# Crear acceso directo .desktop global
echo "[*] Creando acceso directo en el sistema..."
DESKTOP_FILE="/usr/share/applications/virtuoso-control.desktop"
cat << EOF > "$DESKTOP_FILE"
[Desktop Entry]
Name=Virtuoso Control
Comment=Panel de control nativo para los auriculares Corsair Virtuoso SE
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
echo " ¡Instalación completada con éxito!   "
echo "======================================"
echo "Ahora puedes encontrar 'Virtuoso Control' en tu menú de aplicaciones."
echo "Para desinstalar en el futuro, ejecuta: sudo ./uninstall.sh"
