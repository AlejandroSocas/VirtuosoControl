#!/usr/bin/env python3
"""
Virtuoso Control — Controlador HID para Corsair Virtuoso SE

Gestiona LED del micrófono, sidetone y batería mediante conexión HID
persistente (como iCUE), evitando las desconexiones USB que causaba
el patrón anterior de abrir/cerrar para cada comando.
"""
import hid
import time
import sys
import subprocess

VENDOR_ID = 0x1b1c
PRODUCT_ID = 0x0a42

# Endpoints V2W
EP_RECEIVER = 0x08  # Dongle/receptor USB
EP_HEADSET = 0x09   # Auriculares

# Tipos de comando V2W
CMD_GET = 0x01
CMD_SET = 0x02


class VirtuosoController:
    """Controlador persistente para Corsair Virtuoso SE.

    A diferencia del enfoque anterior (abrir/cerrar para cada comando),
    esta implementación mantiene una conexión HID abierta de forma
    persistente, igual que hace iCUE. Esto evita que los cascos se
    desconecten al enviar comandos.
    """

    def __init__(self):
        self.device = None
        self._connected = False
        self._handshake_done = False
        self._alsa_card = None  # Cache del nombre de tarjeta ALSA
        self._brightness = 100  # Brillo actual (0-100), por defecto máximo
        self._mic_led = True    # Estado actual del LED del micro

    # ─── Gestión de conexión ─────────────────────────────────────────

    def _find_path(self):
        """Busca el path HID del Virtuoso en la interfaz 4."""
        for d in hid.enumerate(VENDOR_ID, PRODUCT_ID):
            if d['interface_number'] == 4:
                return d['path']
        return None

    @property
    def is_connected(self):
        """Indica si hay una conexión HID activa."""
        return self._connected and self.device is not None

    def connect(self):
        """Abre el dispositivo HID y realiza el handshake V2W.

        Se llama UNA VEZ. La conexión se mantiene abierta hasta
        que se llame a disconnect() o la app se cierre.

        Returns:
            True si la conexión fue exitosa, False en caso contrario.
        """
        if self.is_connected:
            return True

        path = self._find_path()
        if not path:
            return False

        try:
            self.device = hid.device()
            self.device.open_path(path)
            self.device.set_nonblocking(True)
            self._connected = True
            # NO hacemos handshake aquí — se hará lazy cuando se necesite.
            # Así el LED no se apaga al iniciar la app.
            return True
        except Exception as e:
            print(f"Error al conectar: {e}", file=sys.stderr)
            self._connected = False
            self.device = None
            return False

    def _ensure_handshake(self):
        """Realiza el handshake V2W si no se ha hecho todavía.

        Se llama automáticamente antes de cualquier operación que lo
        necesite (LED, batería, sidetone V2W, heartbeat).
        """
        if self._handshake_done:
            return True
        if not self.is_connected:
            return False
        self._do_handshake()
        self._handshake_done = True
        return True

    def disconnect(self):
        """Cierra la conexión HID. Solo llamar al cerrar la app."""
        if self.device:
            try:
                self.device.close()
            except Exception:
                pass
            self.device = None
        self._connected = False
        self._handshake_done = False

    def reconnect(self):
        """Reconecta al dispositivo (ej: si se perdió la conexión)."""
        self.disconnect()
        time.sleep(0.5)  # Dar tiempo al subsistema USB
        return self.connect()

    # ─── Protocolo V2W ───────────────────────────────────────────────

    def _send_v2w(self, endpoint, cmd_type, payload):
        """Envía un comando V2W por la conexión existente.

        Formato: [0x00(report_id), 0x02(v2w_marker), endpoint, cmd_type, ...payload]
        Total: 65 bytes (1 report_id + 64 datos)
        """
        if not self.device:
            return False

        buf = [0x00, 0x02, endpoint, cmd_type] + payload
        buf += [0x00] * (65 - len(buf))

        try:
            self.device.write(buf)
            time.sleep(0.003)
            return True
        except Exception:
            self._connected = False
            return False

    def _flush_buffer(self):
        """Vacía el buffer HID de datos sobrantes."""
        if not self.device:
            return
        for _ in range(20):
            try:
                data = self.device.read(64)
                if not data:
                    break
            except Exception:
                break
            time.sleep(0.005)

    def _do_handshake(self):
        """Realiza el handshake V2W completo.

        Basado en la implementación de HeadsetControl (corsair_void_v2w.hpp):
        1. Solicitar firmware del receptor
        2. Activar modo software en el receptor
        3. Heartbeat al receptor
        4. Activar modo software en los auriculares
        5. Limpiar buffer
        6. Heartbeat a los auriculares
        """
        # 1. Firmware request al receptor
        self._send_v2w(EP_RECEIVER, CMD_SET, [0x13])

        # 2. Software mode al receptor
        self._send_v2w(EP_RECEIVER, CMD_GET, [0x03, 0x00, 0x02])

        # 3. Heartbeat al receptor
        self._send_v2w(EP_RECEIVER, CMD_SET, [0x12])

        # 4. Software mode a los auriculares
        self._send_v2w(EP_HEADSET, CMD_GET, [0x03, 0x00, 0x02])

        # 5. Limpiar buffer de respuestas acumuladas
        self._flush_buffer()

        # 6. Heartbeat a los auriculares
        self._send_v2w(EP_HEADSET, CMD_SET, [0x12])

        time.sleep(0.02)

    def _init_leds(self):
        """Inicializa el endpoint de los LEDs (necesario para el control RGB)."""
        return self._send_v2w(EP_HEADSET, 0x0d, [0x00, 0x01])

    def set_all_rgb(self, logo_rgb, logo_b, mic_rgb, mic_b, batt_rgb, batt_b):
        """Ajusta el color RGB de todas las zonas simultáneamente.
        
        El comando envía los colores para las zonas:
        [R1, R2, R3, G1, G2, G3, B1, B2, B3]
        0=Logo, 1=Batería, 2=Micrófono
        """
        if not self.is_connected:
            return False
        if not self._ensure_handshake():
            return False
            
        self._init_leds()
        
        data = [0] * 9
        
        # Helper para escalar y asignar
        def _apply_zone(idx, rgb_tuple, brightness):
            scale = brightness / 100.0
            data[idx] = int(rgb_tuple[0] * scale)
            data[idx + 3] = int(rgb_tuple[1] * scale)
            data[idx + 6] = int(rgb_tuple[2] * scale)
            
        _apply_zone(0, logo_rgb, logo_b)
        _apply_zone(1, batt_rgb, batt_b)
        _apply_zone(2, mic_rgb, mic_b)
        
        payload = [0x00, 0x09, 0x00, 0x00, 0x00] + data
        return self._send_v2w(EP_HEADSET, 0x06, payload)

    def set_rgb(self, r, g, b, brightness=100):
        """Método de retrocompatibilidad. Usa set_all_rgb en su lugar."""
        return self.set_all_rgb((r,g,b), brightness, (0,0,0), 0, (0,0,0), 0)

    def send_heartbeat(self):
        """Envía heartbeat V2W para mantener la sesión activa.

        Debe llamarse periódicamente (~cada 20s) para evitar que
        el firmware resetee el estado del LED.

        Returns:
            True si el heartbeat se envió correctamente.
        """
        if not self._ensure_handshake():
            return False
        return self._send_v2w(EP_HEADSET, CMD_SET, [0x12])

    # ─── Batería ─────────────────────────────────────────────────────

    def get_battery(self):
        """Lee el nivel de batería de los auriculares.

        Returns:
            String con el porcentaje y estado, o mensaje de error.
        """
        if not self.is_connected:
            return "No conectado"
        if not self._ensure_handshake():
            return "Error de handshake"

        self._flush_buffer()
        self._send_v2w(EP_HEADSET, CMD_SET, [0x0f])
        
        # Bucle para leer varios paquetes, ya que a veces llegan respuestas
        # de heartbeat o paquetes vacíos antes que la respuesta de batería real.
        for _ in range(5):
            time.sleep(0.1)
            try:
                res = self.device.read(64)
            except Exception:
                self._connected = False
                return "Error de lectura"

            if res and len(res) > 5:
                raw_val = (res[5] << 8) | res[4]
                # Si es 0 exacto, suele ser un paquete de otra cosa o un dummy
                if raw_val == 0:
                    continue
                    
                percent = min(raw_val // 10, 100)
                status_byte = res[3]
                status = "Cargando" if status_byte in [4, 5] else "Descargando"
                return f"{percent}% [{status}]"
                
        return "Sin respuesta (inténtalo de nuevo)"

    # ─── Sidetone (ALSA) ────────────────────────────────────────────

    def _find_alsa_card(self):
        """Encuentra dinámicamente el nombre de tarjeta ALSA del Corsair.

        Busca en /proc/asound/cards el dispositivo Corsair y devuelve
        su nombre corto (el que aparece entre corchetes).
        """
        if self._alsa_card:
            return self._alsa_card

        try:
            with open("/proc/asound/cards", "r") as f:
                for line in f:
                    # Formato: " 1 [Gaming         ]: USB-Audio - ..."
                    if "Corsair" in line or "VIRTUOSO" in line or "Gaming" in line:
                        if "[" in line and "]" in line:
                            start = line.index("[") + 1
                            end = line.index("]")
                            self._alsa_card = line[start:end].strip()
                            return self._alsa_card
        except Exception:
            pass

        # Fallback: buscar con aplay -l
        try:
            result = subprocess.run(
                ["aplay", "-l"], capture_output=True, text=True, timeout=5
            )
            for line in result.stdout.splitlines():
                if "Corsair" in line or "VIRTUOSO" in line:
                    if "card" in line:
                        parts = line.split(":")
                        card_num = parts[0].strip().split()[-1]
                        self._alsa_card = card_num
                        return self._alsa_card
        except Exception:
            pass

        return "Gamin"  # Último recurso

    def set_sidetone(self, value):
        """Controla el sidetone vía ALSA mixer.

        Args:
            value: 'on', 'off', o un número 0-100 (porcentaje).

        Returns:
            True si el comando se ejecutó correctamente.
        """
        card = self._find_alsa_card()

        try:
            if value == "on":
                cmd = ["amixer", "-c", card, "sset", "Sidetone", "unmute"]
            elif value == "off":
                cmd = ["amixer", "-c", card, "sset", "Sidetone", "mute"]
            else:
                val_str = f"{value}%" if "%" not in str(value) else str(value)
                # IMPORTANTE: incluir "unmute" para que al poner un valor
                # se desmutee automáticamente. Sin esto, si se había hecho
                # "mute" antes, el canal queda muteado aunque se ponga volumen.
                cmd = ["amixer", "-c", card, "sset", "Sidetone", val_str, "unmute"]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if result.returncode != 0:
                print(f"Error amixer: {result.stderr}", file=sys.stderr)
                return False
            return True
        except subprocess.TimeoutExpired:
            print("Timeout en amixer", file=sys.stderr)
            return False
        except Exception as e:
            print(f"Error sidetone ALSA: {e}", file=sys.stderr)
            return False

    # ─── Sidetone (V2W HID) ─────────────────────────────────────────

    def set_sidetone_v2w(self, level):
        """Controla el sidetone vía comandos V2W HID.

        Método alternativo basado en HeadsetControl (corsair_void_v2w.hpp).
        Puede funcionar mejor que ALSA en algunos sistemas.

        Secuencia:
        1. Toggle ANC (debe estar apagado para que sidetone funcione)
        2. Toggle Sidetone (encender/apagar)
        3. Establecer nivel (0-1000 internamente)

        Args:
            level: 0-100 (0 = desactivado).

        Returns:
            True si los comandos se enviaron correctamente.
        """
        if not self.is_connected:
            return False
        if not self._ensure_handshake():
            return False

        # Mapear 0-100 a 0-1000 (rango interno de Corsair)
        mapped = int(level * 10)
        mapped = (mapped // 10) * 10  # Redondear a múltiplos de 10
        low_byte = mapped & 0xFF
        high_byte = (mapped >> 8) & 0xFF

        if level == 0:
            # Apagar sidetone, encender ANC
            self._send_v2w(EP_HEADSET, CMD_GET, [0xd1, 0x00, 0x01])
            self._send_v2w(EP_HEADSET, CMD_GET, [0x46, 0x00, 0x01])
        else:
            # Apagar ANC (necesario para que sidetone funcione)
            self._send_v2w(EP_HEADSET, CMD_GET, [0xd1])
            # Encender sidetone
            self._send_v2w(EP_HEADSET, CMD_GET, [0x46])

        # Establecer nivel
        return self._send_v2w(EP_HEADSET, CMD_GET, [0x47, 0x00, low_byte, high_byte])

    # ─── Volumen (ALSA) ─────────────────────────────────────────────

    def set_volume(self, value):
        """Ajusta el volumen vía ALSA mixer."""
        card = self._find_alsa_card()
        try:
            val_str = f"{value}%" if "%" not in str(value) else str(value)
            result = subprocess.run(
                ["amixer", "-c", card, "sset", "Headset", val_str],
                capture_output=True, text=True, timeout=5
            )
            return result.returncode == 0
        except Exception:
            return False


# ─── CLI ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso:")
        print("  python3 virtuoso_control.py sidetone [on|off|0-100]")
        print("  python3 virtuoso_control.py sidetone-v2w [0-100]")
        print("  python3 virtuoso_control.py volume [0-100]")
        print("  python3 virtuoso_control.py battery")
        sys.exit(1)

    category = sys.argv[1].lower()
    ctrl = VirtuosoController()

    if category == "battery":
        if ctrl.connect():
            print(f"Batería: {ctrl.get_battery()}")
            ctrl.disconnect()
        else:
            print("Error: No se encontró el Virtuoso.")

    elif category == "rgb":
        r = int(sys.argv[2]) if len(sys.argv) > 2 else 255
        g = int(sys.argv[3]) if len(sys.argv) > 3 else 0
        b = int(sys.argv[4]) if len(sys.argv) > 4 else 0
        brightness = int(sys.argv[5]) if len(sys.argv) > 5 else 100
        if ctrl.connect():
            if ctrl.set_rgb(r, g, b, brightness):
                print(f"✓ RGB configurado: RGB({r},{g},{b}) Brillo: {brightness}%")
            else:
                print("✗ Error al ajustar RGB.")
            ctrl.disconnect()
        else:
            print("Error: No se encontró el Virtuoso.")

    elif category == "sidetone":
        val = sys.argv[2].lower() if len(sys.argv) > 2 else ""
        if ctrl.set_sidetone(val):
            print(f"✓ Sidetone (ALSA): {val}")
        else:
            print("✗ Error al ajustar sidetone.")

    elif category == "sidetone-v2w":
        val = int(sys.argv[2]) if len(sys.argv) > 2 else 0
        if ctrl.connect():
            if ctrl.set_sidetone_v2w(val):
                print(f"✓ Sidetone (V2W): {val}")
            else:
                print("✗ Error al ajustar sidetone V2W.")
            ctrl.disconnect()
        else:
            print("Error: No se encontró el Virtuoso.")

    elif category == "volume":
        val = sys.argv[2] if len(sys.argv) > 2 else ""
        if ctrl.set_volume(val):
            print(f"✓ Volumen: {val}%")
        else:
            print("✗ Error al ajustar volumen.")
