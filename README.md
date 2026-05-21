# Corsair Virtuoso SE Control para Linux

Una aplicación de control nativa, ligera y persistente para los auriculares Corsair Virtuoso SE en Linux, basada en ingeniería inversa del protocolo V2W HID.

## Características Principales

*   **Iluminación RGB Completa:** Controla el color y el brillo del Logo lateral y del micrófono a través de selectores de color nativos (solo en modo inalámbrico).
*   **Monitor de Batería Fiable:** Lee de forma segura el estado de la batería, mostrando porcentajes estables y el estado de carga real. Si la batería baja del 15%, envía una notificación de escritorio. Además, ¡el LED de batería de los auriculares reflejará su nivel automáticamente (Verde, Amarillo o Rojo)!
*   **Soporte Dual (Cable / Dongle):** Detecta automáticamente si estás conectado mediante el dongle inalámbrico o directamente por cable USB, adaptando la interfaz y deshabilitando controles V2W no soportados en modo cable.
*   **Autoarranque Universal (FreeDesktop):** Configura la aplicación desde el panel de ajustes interno (`⚙`) para que se inicie automáticamente en segundo plano cada vez que enciendas tu PC (compatible con GNOME, KDE, XFCE y cualquier escritorio moderno).
*   **Inicio Minimizado:** Posibilidad de abrir la aplicación directamente en la bandeja del sistema de forma completamente silenciosa.
*   **Sidetone (Retorno de Voz):** Control independiente del sidetone mediante ALSA nativo o comandos V2W directos al hardware.
*   **Icono en el Área de Notificación (Tray):** Accede a todas las funciones rápidas y al panel de ajustes directamente desde la barra de tareas.
*   **Conexión Persistente:** Mantiene una sesión HID viva con los auriculares mediante *keep-alive*, previniendo desconexiones inesperadas del dispositivo (comportamiento idéntico al software original de Windows).

## Requisitos Previos

La aplicación requiere `python3` y algunas dependencias del sistema para funcionar correctamente:

```bash
# En sistemas basados en Fedora/RHEL:
sudo dnf install python3-pyqt6 python3-hidapi

# En sistemas basados en Debian/Ubuntu:
sudo apt install python3-pyqt6 python3-hidapi
```

*(Asegúrate de que tu usuario pertenece al grupo `audio` o equivalente si quieres usar la sincronización ALSA para el Sidetone y Volumen).*

## Instalación

La forma más sencilla de instalar la aplicación es clonando el repositorio y ejecutando el script de instalación automática, el cual configurará los permisos y creará un acceso directo en tu menú de aplicaciones.

### 1. Descargar el programa
Clona este repositorio en tu ordenador y entra en la carpeta:
```bash
git clone https://github.com/AlejandroSocas/VirtuosoControl.git
cd VirtuosoControl
```

### 2. Instalación Automática
Ejecuta el script de instalación con permisos de superusuario (necesario para aplicar las reglas udev y copiar los archivos a `/opt/`):
```bash
sudo ./install.sh
```
Una vez terminado, podrás abrir **"Virtuoso Control"** directamente desde el menú de aplicaciones de tu sistema operativo.

### (Opcional) Ejecución Manual Portable
Si prefieres no instalar la aplicación a nivel de sistema, puedes ejecutarla desde la carpeta descargada tras aplicar manualmente los permisos USB:
```bash
# Copiar y recargar las reglas UDEV (Solo necesario la primera vez)
sudo cp 99-corsair-virtuoso-hid.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger

# Arrancar la interfaz
python3 virtuoso_gui.py
```

## Desinstalación

Para eliminar completamente el programa y sus permisos de tu sistema, navega a la carpeta descargada y ejecuta:
```bash
sudo ./uninstall.sh
```

## Agradecimientos e Ingeniería Inversa
Este proyecto nace de la necesidad de controlar los Virtuoso SE en Linux sin que la batería drene rápidamente por culpa de los LEDs estáticos. A través de capturas extensas de Wireshark en Windows y el estudio del protocolo V2W, se han implementado métodos de inyección hexadecimal totalmente limpios para este dongle específico.
