# Corsair Virtuoso SE Control para Linux

Una aplicación de control nativa, ligera y persistente para los auriculares Corsair Virtuoso SE en Linux, basada en ingeniería inversa del protocolo V2W HID.

## Características Principales

*   **Iluminación RGB Completa:** Controla el color y el brillo del Logo lateral y del micrófono a través de selectores de color nativos.
*   **Monitor de Batería Fiable:** Lee de forma segura el estado de la batería, mostrando porcentajes estables y el estado de carga real. Si la batería baja del 15%, envía una notificación de escritorio. Además, ¡el LED de batería de los auriculares reflejará su nivel automáticamente (Verde, Amarillo o Rojo)!
*   **Sidetone (Retorno de Voz):** Control independiente del sidetone mediante ALSA nativo o comandos V2W directos al hardware.
*   **Icono en el Área de Notificación (Tray):** Accede a todas las funciones rápidas de forma silenciosa desde la barra de tareas.
*   **Conexión Persistente:** Mantiene una sesión HID viva con los auriculares mediante *keep-alive*, previniendo desconexiones inesperadas del dispositivo (comportamiento idéntico al software original de Windows).

## Requisitos Previos

La aplicación requiere `python3` y algunas dependencias del sistema para funcionar correctamente:

```bash
# En sistemas basados en Fedora/RHEL:
sudo dnf install python3-pyqt6 python3-hidapi
```

*(Asegúrate de que tu usuario pertenece al grupo `audio` o equivalente si quieres usar la sincronización ALSA para el Sidetone y Volumen).*

## Instalación

1.  **Regla UDEV (Fundamental):**
    Para que el programa pueda comunicarse con los auriculares sin necesitar permisos de root, debes copiar la regla udev incluida:
    ```bash
    sudo cp 99-corsair-virtuoso-hid.rules /etc/udev/rules.d/
    sudo udevadm control --reload-rules
    sudo udevadm trigger
    ```

2.  **Iniciando la aplicación:**
    Una vez aplicadas las reglas, puedes arrancar la interfaz gráfica ejecutando:
    ```bash
    python3 virtuoso_gui.py
    ```

3.  **Inicio Automático (Autostart):**
    Puedes copiar el archivo `.desktop` provisto a tu carpeta de inicio automático para que la aplicación cargue minimizada cada vez que enciendes el PC.
    ```bash
    cp virtuoso-control.desktop ~/.config/autostart/
    ```

## Agradecimientos e Ingeniería Inversa
Este proyecto nace de la necesidad de controlar los Virtuoso SE en Linux sin que la batería drene rápidamente por culpa de los LEDs estáticos. A través de capturas extensas de Wireshark en Windows y el estudio del protocolo V2W, se han implementado métodos de inyección hexadecimal totalmente limpios para este dongle específico.
