#!/usr/bin/env python3
"""
Virtuoso Control — HID Controller for Corsair Virtuoso SE

Manages microphone LED, sidetone and battery via HID connection
persistent (like iCUE), preventing USB disconnections caused by
the previous open/close pattern for each command.
"""
import hid
import time
import sys
import subprocess

VENDOR_ID = 0x1b1c
PRODUCT_IDS = {
    # Wireless Dongles
    0x0a3f: "Wireless",
    0x0a42: "Wireless",
    0x0a4a: "Wireless",
    0x0a63: "Wireless",
    
    # Wired Connections / Direct USB headsets
    0x0a3d: "Wired",
    0x0a41: "Wired",
    0x0a49: "Wired",
    0x0a62: "Wired"
}

# V2W Endpoints
EP_RECEIVER = 0x08  # Wireless Dongle/Receiver
EP_HEADSET = 0x09   # Auriculares

# Tipos de comando V2W
CMD_GET = 0x01
CMD_SET = 0x02


class VirtuosoController:
    """Persistent controller for Corsair Virtuoso SE.

    Unlike the previous approach (open/close for each command),
    this implementation keeps a persistent HID connection open,
    just like iCUE does. This prevents the headset from
    disconnecting when sending commands.
    """

    def __init__(self):
        self.device = None
        self._connected = False
        self._handshake_done = False
        self._alsa_card = None  # ALSA card name cache
        self._brightness = 100  # Current brightness (0-100), max by default
        self._mic_led = True    # Current state of the Mic LED
        self.connection_mode = "Unknown"

    # ─── Connection Management ───────────────────────────────────────

    def _find_path(self):
        """Finds the HID path for the Virtuoso on interface 4."""
        for pid, mode in PRODUCT_IDS.items():
            for d in hid.enumerate(VENDOR_ID, pid):
                if d['interface_number'] == 4:
                    self.connection_mode = mode
                    return d['path']
        return None

    @property
    def is_connected(self):
        """Indicates if there is an active HID connection."""
        return self._connected and self.device is not None

    def connect(self):
        """Opens the HID device and performs the V2W handshake.

        Called ONCE. The connection is kept open until
        disconnect() is called or the app closes.

        Returns:
            True if the connection was successful, False otherwise.
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
            # We DO NOT handshake here — it will be done lazily when needed.
            # This prevents the LED from turning off when starting the app.
            return True
        except Exception as e:
            print(f"Error connecting: {e}", file=sys.stderr)
            self._connected = False
            self.device = None
            return False

    def _ensure_handshake(self):
        """Performs the V2W handshake if not done yet.

        Called automatically before any operation that
        needs it (LED, battery, V2W sidetone, heartbeat).
        """
        if self._handshake_done:
            return True
        if not self.is_connected:
            return False
        self._do_handshake()
        self._handshake_done = True
        return True

    def disconnect(self):
        """Closes the HID connection. Only call when closing the app."""
        if self.device:
            try:
                self.device.close()
            except Exception:
                pass
            self.device = None
        self._connected = False
        self._handshake_done = False

    def reconnect(self):
        """Reconnects to the device (e.g. if connection was lost)."""
        self.disconnect()
        time.sleep(0.5)  # Give USB subsystem time
        return self.connect()

    # ─── Protocolo V2W ───────────────────────────────────────────────

    def _send_v2w(self, endpoint, cmd_type, payload):
        """Sends a V2W command over the existing connection.

        Format: [0x00(report_id), 0x02(v2w_marker), endpoint, cmd_type, ...payload]
        Total: 65 bytes (1 report_id + 64 data)
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
        """Flushes the HID buffer of leftover data."""
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
        """Performs the complete V2W handshake.

        Based on HeadsetControl implementation (corsair_void_v2w.hpp):
        1. Request firmware from receiver
        2. Enable software mode on receiver
        3. Heartbeat to receiver
        4. Enable software mode on headset
        5. Clean buffer
        6. Heartbeat to headset
        """
        # 1. Firmware request to receiver
        self._send_v2w(EP_RECEIVER, CMD_SET, [0x13])

        # 2. Software mode to receiver
        self._send_v2w(EP_RECEIVER, CMD_GET, [0x03, 0x00, 0x02])

        # 3. Heartbeat to receiver
        self._send_v2w(EP_RECEIVER, CMD_SET, [0x12])

        # 4. Software mode to headset
        self._send_v2w(EP_HEADSET, CMD_GET, [0x03, 0x00, 0x02])

        # 5. Clean buffer de respuestas acumuladas
        self._flush_buffer()

        # 6. Heartbeat to headset
        self._send_v2w(EP_HEADSET, CMD_SET, [0x12])

        time.sleep(0.02)

    def _init_leds(self):
        """Initializes the LED endpoint (necessary for RGB control)."""
        return self._send_v2w(EP_HEADSET, 0x0d, [0x00, 0x01])

    def set_all_rgb(self, logo_rgb, logo_b, mic_rgb, mic_b, batt_rgb, batt_b):
        """Adjusts the RGB color of all zones simultaneously.
        
        The command sends colors for the zones:
        [R1, R2, R3, G1, G2, G3, B1, B2, B3]
        0=Logo, 1=Battery, 2=Microphone
        """
        if not self.is_connected:
            return False
        if not self._ensure_handshake():
            return False
            
        self._init_leds()
        
        data = [0] * 9
        
        # Helper to scale and assign
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
        """Backwards compatibility method. Use set_all_rgb instead."""
        return self.set_all_rgb((r,g,b), brightness, (0,0,0), 0, (0,0,0), 0)

    def send_heartbeat(self):
        """Sends V2W heartbeat to keep the session alive.

        Must be called periodically (~every 20s) to prevent
        the firmware from resetting the LED state.

        Returns:
            True if the heartbeat was sent successfully.
        """
        if not self._ensure_handshake():
            return False
        return self._send_v2w(EP_HEADSET, CMD_SET, [0x12])

    # ─── Batería ─────────────────────────────────────────────────────

    def get_battery(self):
        """Reads the battery level of the headset.

        Returns:
            String with percentage and status, or error message.
        """
        if not self.is_connected:
            return "Not connected"
        if not self._ensure_handshake():
            return "Handshake error"

        # Check if the headset is physically connected via USB (charging)
        is_usb_charging = False
        try:
            wired_pids = [0x0a3d, 0x0a41, 0x0a49, 0x0a62]
            for d in hid.enumerate(VENDOR_ID):
                if d['product_id'] in wired_pids:
                    is_usb_charging = True
                    break
        except Exception:
            pass

        valid_percents = []
        last_status = "Discharging"

        # We perform the check 3 consecutive times. 
        # Sometimes the first packet after a period of inactivity 
        # returns a false 100% or garbage data. We take the last value.
        for _ in range(3):
            self._flush_buffer()
            self._send_v2w(EP_HEADSET, CMD_SET, [0x0f])
            
            for _ in range(5):
                time.sleep(0.05)
                try:
                    res = self.device.read(64)
                except Exception:
                    self._connected = False
                    return "Read error"

                if res and len(res) > 5:
                    raw_val = (res[5] << 8) | res[4]
                    if raw_val == 0:
                        continue
                        
                    percent = min(raw_val // 10, 100)
                    status_byte = res[3]
                    
                    if is_usb_charging or status_byte in [4, 5]:
                        status = "Charging"
                    else:
                        status = "Discharging"
                    
                    valid_percents.append(percent)
                    last_status = status
                    break
        
        if valid_percents:
            # Return the last reading, which is the most stable
            return f"{valid_percents[-1]}% [{last_status}]"
                
        return "No response (try again)"

    # ─── Sidetone (ALSA) ─────────────────────────────────────────────

    def _find_alsa_card(self):
        """Dynamically finds the ALSA card name for the Corsair headset.

        Searches /proc/asound/cards for the Corsair device and returns
        its short name (the one in brackets).
        """
        if self._alsa_card:
            return self._alsa_card

        try:
            with open("/proc/asound/cards", "r") as f:
                for line in f:
                    # Format: " 1 [Gaming         ]: USB-Audio - ..."
                    if "Corsair" in line or "VIRTUOSO" in line or "Gaming" in line or "Hea" in line:
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

        return "Gamin"  # Last resort

    def set_sidetone(self, value):
        """Controls the sidetone via ALSA mixer.

        Args:
            value: 'on', 'off', or a number 0-100 (percentage).

        Returns:
            True if the command was executed successfully.
        """
        card = self._find_alsa_card()

        try:
            if value == "on":
                cmd = ["amixer", "-c", card, "sset", "Sidetone", "unmute"]
            elif value == "off":
                cmd = ["amixer", "-c", card, "sset", "Sidetone", "mute"]
            else:
                val_str = f"{value}%" if "%" not in str(value) else str(value)
                # IMPORTANT: include "unmute" so that when setting a value
                # it un-mutes automatically. Without this, if it was
                # "muted" before, the channel remains muted even if volume is set.
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
        """Controls the sidetone via V2W HID commands.

        Alternative method based on HeadsetControl (corsair_void_v2w.hpp).
        Might work better than ALSA on some systems.

        Sequence:
        1. Toggle ANC (must be off for sidetone to work)
        2. Toggle Sidetone (turn on/off)
        3. Set level (0-1000 internally)

        Args:
            level: 0-100 (0 = disabled).

        Returns:
            True if the commands were sent successfully.
        """
        if not self.is_connected:
            return False
        if not self._ensure_handshake():
            return False

        # Map 0-100 to 0-1000 (internal Corsair range)
        mapped = int(level * 10)
        mapped = (mapped // 10) * 10  # Round to multiples of 10
        low_byte = mapped & 0xFF
        high_byte = (mapped >> 8) & 0xFF

        if level == 0:
            # Turn off sidetone, turn on ANC
            self._send_v2w(EP_HEADSET, CMD_GET, [0xd1, 0x00, 0x01])
            self._send_v2w(EP_HEADSET, CMD_GET, [0x46, 0x00, 0x01])
        else:
            # Turn off ANC (necessary for sidetone to work)
            self._send_v2w(EP_HEADSET, CMD_GET, [0xd1])
            # Turn on sidetone
            self._send_v2w(EP_HEADSET, CMD_GET, [0x46])

        # Set level
        return self._send_v2w(EP_HEADSET, CMD_GET, [0x47, 0x00, low_byte, high_byte])

    # ─── Volumen (ALSA) ─────────────────────────────────────────────

    def set_volume(self, value):
        """Adjusts the volume via ALSA mixer."""
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
        print("Usage:")
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
            print("Error: Virtuoso not found.")

    elif category == "rgb":
        r = int(sys.argv[2]) if len(sys.argv) > 2 else 255
        g = int(sys.argv[3]) if len(sys.argv) > 3 else 0
        b = int(sys.argv[4]) if len(sys.argv) > 4 else 0
        brightness = int(sys.argv[5]) if len(sys.argv) > 5 else 100
        if ctrl.connect():
            if ctrl.set_rgb(r, g, b, brightness):
                print(f"✓ RGB configured: RGB({r},{g},{b}) Brillo: {brightness}%")
            else:
                print("✗ Error adjusting RGB.")
            ctrl.disconnect()
        else:
            print("Error: Virtuoso not found.")

    elif category == "sidetone":
        val = sys.argv[2].lower() if len(sys.argv) > 2 else ""
        if ctrl.set_sidetone(val):
            print(f"✓ Sidetone (ALSA): {val}")
        else:
            print("✗ Error adjusting sidetone.")

    elif category == "sidetone-v2w":
        val = int(sys.argv[2]) if len(sys.argv) > 2 else 0
        if ctrl.connect():
            if ctrl.set_sidetone_v2w(val):
                print(f"✓ Sidetone (V2W): {val}")
            else:
                print("✗ Error adjusting sidetone V2W.")
            ctrl.disconnect()
        else:
            print("Error: Virtuoso not found.")

    elif category == "volume":
        val = sys.argv[2] if len(sys.argv) > 2 else ""
        if ctrl.set_volume(val):
            print(f"✓ Volumen: {val}%")
        else:
            print("✗ Error adjusting volumen.")
