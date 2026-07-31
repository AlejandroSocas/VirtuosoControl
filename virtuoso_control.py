#!/usr/bin/env python3
"""
Virtuoso Control — HID Controller for Corsair Virtuoso SE

Manages microphone LED, sidetone and battery via HID connection
persistent (like iCUE), preventing USB disconnections caused by
the previous open/close pattern for each command.
"""
import hid
import math
import os
import struct
import subprocess
import sys
import time
import wave

VENDOR_ID = 0x1b1c
PRODUCT_IDS = {
    # Wireless Dongles
    0x0a3e: "Wireless",
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

# Notification pushed by the headset when the physical mic-mute button is
# pressed while in software mode: [0x03, 0x01, 0x02, down].
# Captured from the control interface — the button reports key DOWN (1) and
# key UP (0) roughly 100-200ms apart, it does NOT report a mute state. Act on
# the down edge only; treating the value as a state mutes on press and unmutes
# on release.
MIC_BUTTON_REPORT = (0x03, 0x01, 0x02)

# Asynchronous status notification the headset pushes when a value changes:
#   [0x03, 0x01, 0x01, opcode, 0x00, lo, hi]
# Note the payload sits one byte later than in a query reply, which is
#   [0x01, 0x01, 0x02, 0x00, lo, hi]
NOTIFY_STATUS = (0x03, 0x01, 0x01)
OP_BATTERY = 0x0f
OP_CHARGE = 0x10   # 1 = charging, 2 = discharging


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
        self._pw_source = None  # PipeWire source name cache
        self._pw_sink = None    # PipeWire sink name cache
        self._pending_press = False  # button press seen while flushing
        self._last_level = None      # previous battery reading
        self._charging_inferred = False
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

    def _leave_software_mode(self):
        """Hands the headset back to firmware control.

        _do_handshake() puts the headset into *software* mode, where the
        firmware stops handling the physical mic-mute button and expects host
        software (iCUE on Windows) to do it instead. Nothing here handles that
        button, so while we hold the headset in software mode the button does
        nothing — and because closing the HID handle does not undo the mode
        switch, it stays dead after the app exits until the headset is
        power-cycled.

        Same opcode as the software-mode command in _do_handshake(), with 0x01
        in place of 0x02.
        """
        if not self.device:
            return False
        ok = self._send_v2w(EP_RECEIVER, CMD_GET, [0x03, 0x00, 0x01])
        time.sleep(0.05)
        ok = self._send_v2w(EP_HEADSET, CMD_GET, [0x03, 0x00, 0x01]) and ok
        time.sleep(0.05)
        return ok

    def disconnect(self):
        """Closes the HID connection. Only call when closing the app."""
        if self.device:
            # Give the mic-mute button back before dropping the handle.
            # Only meaningful if we actually entered software mode.
            if self._handshake_done:
                try:
                    self._leave_software_mode()
                except Exception:
                    pass
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
        self._alsa_card = None  # Invalidate ALSA card cache
        self._pw_source = None
        self._pw_sink = None
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
        """Flushes the HID buffer of leftover data.

        Mic-button presses are stashed rather than dropped: a battery read
        flushes first, and without this a press landing just before it would be
        silently lost.
        """
        if not self.device:
            return
        for _ in range(20):
            try:
                data = self.device.read(64)
                if not data:
                    break
                if len(data) > 3 and tuple(data[:3]) == MIC_BUTTON_REPORT and data[3]:
                    self._pending_press = True
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

    def is_headset_alive(self):
        """Checks if the headset is actually responding (not just the dongle).

        The dongle stays connected via USB even when the headset is off,
        so device.write() always succeeds. This method sends a battery
        query and checks if we get a response back from the headset.

        Returns:
            True if the headset responded, False if only the dongle is alive.
        """
        if not self.is_connected:
            return False
        if not self._ensure_handshake():
            return False

        self._flush_buffer()
        if not self._send_v2w(EP_HEADSET, CMD_SET, [0x0f]):
            return False

        # Wait for a response from the headset
        for _ in range(5):
            time.sleep(0.05)
            try:
                res = self.device.read(64)
                if res and len(res) > 3:
                    return True  # Got a response — headset is alive
            except Exception:
                self._connected = False
                return False

        return False  # No response — headset is off

    # ─── Physical mic-mute button ────────────────────────────────────

    def poll_mic_button(self):
        """Returns how many times the physical mic-mute button was pressed.

        Only works in software mode: that is the mode where the firmware stops
        acting on the button itself and forwards it to the host instead (see
        _do_handshake / _leave_software_mode).

        Counts key-down edges only. The button reports down (1) and up (0)
        ~100-200ms apart and carries no mute state of its own, so the caller
        owns the state and toggles per press.

        Safe to call from a timer; the handle is non-blocking.
        """
        if not self.is_connected or not self._handshake_done:
            return 0

        presses = 1 if self._pending_press else 0
        self._pending_press = False

        for _ in range(32):
            try:
                data = self.device.read(64)
            except Exception:
                self._connected = False
                return presses
            if not data:
                break
            if len(data) > 3 and tuple(data[:3]) == MIC_BUTTON_REPORT and data[3]:
                presses += 1  # key down; the matching key up is ignored
        return presses

    # ─── Mic mute (PipeWire) ─────────────────────────────────────────

    def _find_pw_source(self):
        """Finds the PipeWire/PulseAudio source name for the headset mic."""
        if self._pw_source:
            return self._pw_source
        try:
            out = subprocess.run(["pactl", "list", "sources", "short"],
                                 capture_output=True, text=True, timeout=5).stdout
            for line in out.splitlines():
                low = line.lower()
                if ("corsair" in low or "virtuoso" in low) and ".monitor" not in low:
                    parts = line.split("\t")
                    if len(parts) > 1:
                        self._pw_source = parts[1]
                        return self._pw_source
        except Exception:
            pass
        return None

    def set_mic_mute_pw(self, muted):
        """Mutes/unmutes the mic at the PipeWire layer.

        Deliberately NOT the ALSA capture switch: PipeWire is the layer the
        desktop itself mutes, so acting here stays in sync with it instead of
        hard-muting underneath it (which makes the mic look permanently dead).
        Targets the headset source by name — the default source is often a
        different microphone entirely.
        """
        src = self._find_pw_source()
        if src is None:
            print("Error: Corsair PipeWire source not found", file=sys.stderr)
            return False
        try:
            r = subprocess.run(
                ["pactl", "set-source-mute", src, "1" if muted else "0"],
                capture_output=True, text=True, timeout=5)
            return r.returncode == 0
        except Exception as e:
            print(f"Error setting PipeWire mute: {e}", file=sys.stderr)
            return False

    def get_mic_muted_pw(self):
        """Reads the current PipeWire mute state. None if unavailable."""
        src = self._find_pw_source()
        if src is None:
            return None
        try:
            r = subprocess.run(["pactl", "get-source-mute", src],
                               capture_output=True, text=True, timeout=5)
            if r.returncode != 0:
                return None
            return "yes" in r.stdout.lower()
        except Exception:
            return None

    # ─── Mute feedback tone ──────────────────────────────────────────

    def _find_pw_sink(self):
        """Finds the PipeWire sink name for the headset speakers."""
        if self._pw_sink:
            return self._pw_sink
        try:
            out = subprocess.run(["pactl", "list", "sinks", "short"],
                                 capture_output=True, text=True, timeout=5).stdout
            for line in out.splitlines():
                low = line.lower()
                if "corsair" in low or "virtuoso" in low:
                    parts = line.split("\t")
                    if len(parts) > 1:
                        self._pw_sink = parts[1]
                        return self._pw_sink
        except Exception:
            pass
        return None

    @staticmethod
    def _write_tone(path, freqs, ms=110, rate=44100, volume=0.30):
        """Renders a short stereo tone sequence to a WAV file."""
        n = int(rate * ms / 1000)
        fade = max(1, int(rate * 0.006))  # ramp, otherwise it clicks
        frames = bytearray()
        for freq in freqs:
            for i in range(n):
                env = min(1.0, i / fade, (n - i) / fade)
                s = int(volume * env * 32767 * math.sin(2 * math.pi * freq * i / rate))
                frames += struct.pack("<hh", s, s)
        with wave.open(path, "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(bytes(frames))

    def _tone_path(self, muted):
        """Returns the cached tone file, generating it on first use.

        Tones are synthesised rather than taken from a system theme so this
        works on any distro with no extra packages.
        """
        cache = os.path.expanduser("~/.cache/virtuoso-control")
        name = "mic-muted.wav" if muted else "mic-active.wav"
        path = os.path.join(cache, name)
        if os.path.exists(path):
            return path
        try:
            os.makedirs(cache, exist_ok=True)
            # Descending for mute, ascending for unmute — distinguishable
            # without looking at the headset.
            self._write_tone(path, [880, 494] if muted else [494, 880])
            return path
        except Exception as e:
            print(f"Error generating tone: {e}", file=sys.stderr)
            return None

    def play_mic_tone(self, muted):
        """Plays the mute/unmute feedback tone through the headset.

        In hardware mode the firmware beeps on a mute change by itself. Under
        software mode it does not — the host owns the button — so the app has
        to provide the cue. Fire-and-forget: never block the UI thread waiting
        on playback.
        """
        path = self._tone_path(muted)
        if path is None:
            return False
        sink = self._find_pw_sink()
        cmd = ["paplay"]
        if sink:
            cmd.append(f"--device={sink}")
        cmd.append(path)
        try:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
            return True
        except FileNotFoundError:
            try:
                subprocess.Popen(["pw-play", path], stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
                return True
            except Exception:
                return False
        except Exception:
            return False

    # ─── Batería ─────────────────────────────────────────────────────

    @property
    def is_usb_charging(self):
        """Checks if the headset is physically connected via USB."""
        try:
            wired_pids = [0x0a3d, 0x0a41, 0x0a49, 0x0a62]
            for d in hid.enumerate(VENDOR_ID):
                if d['product_id'] in wired_pids:
                    return True
        except Exception:
            pass
        return False

    def get_charging(self):
        """Queries the headset's charge state over V2W (opcode 0x10).

        Returns True if charging, False if discharging, None if unanswered.

        Found by capturing the notification the headset pushes when the cable
        is connected or removed; the same opcode answers a direct query.
        1 = charging, 2 = discharging.

        Unlike is_usb_charging this works with ANY power source — a wall
        charger or power bank never appears on this machine's USB bus, so
        enumeration cannot see it, but the headset knows and will say so.
        """
        if not self.is_connected or not self._handshake_done:
            return None

        for _ in range(3):
            self._flush_buffer()
            if not self._send_v2w(EP_HEADSET, CMD_SET, [OP_CHARGE]):
                return None
            for _ in range(8):
                time.sleep(0.05)
                try:
                    res = self.device.read(64)
                except Exception:
                    self._connected = False
                    return None
                if res and len(res) > 5 and res[0] == 0x01:
                    # Query reply: payload at [4]/[5]
                    val = (res[5] << 8) | res[4]
                    if val in (1, 2):
                        return val == 1
                    # Stale packet (the first read after idle is junk) — keep
                    # draining rather than abandoning this attempt.
        return None

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
        usb_charging = self.is_usb_charging

        valid_percents = []
        hw_charging = False

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
                    # 0 is an empty slot; anything over 1000 (=100%) is the
                    # stale first packet after idle. Capping it instead of
                    # rejecting it is what produced the phantom 100% readings.
                    if raw_val == 0 or raw_val > 1000:
                        continue

                    valid_percents.append(raw_val // 10)
                    if res[3] in (4, 5):
                        hw_charging = True
                    break

        if not valid_percents:
            return "No response (try again)"

        # Last reading is the most stable
        level = valid_percents[-1]

        # Trend is kept only as a last resort if the query goes unanswered.
        if self._last_level is not None:
            if level > self._last_level:
                self._charging_inferred = True
            elif level < self._last_level:
                self._charging_inferred = False
        self._last_level = level

        # Charge state, in order of reliability:
        #   1. the headset's own answer (opcode 0x10) — authoritative, and the
        #      only one that sees a wall charger or power bank
        #   2. a wired Corsair PID on the bus — only when charging from this PC
        #   3. res[3] in (4, 5) — never set by this firmware, kept for models
        #      that do report it
        #   4. the level trending upward
        charging = self.get_charging()
        if charging is None:
            charging = usb_charging or hw_charging or self._charging_inferred

        return f"{level}% [{'Charging' if charging else 'Discharging'}]"

    # ─── Sidetone (ALSA) ─────────────────────────────────────────────

    def _find_alsa_card(self):
        """Dynamically finds the ALSA card index for the Corsair headset.

        Matches ONLY on Corsair/Virtuoso identifiers. Generic words like
        "Gaming" or "Hea" also match unrelated USB audio devices — e.g. a
        "G560 Gaming Speaker" enumerating on a lower card index would win
        the scan and every amixer command would be sent to the speakers.

        Returns:
            The card index as a string, or None if the headset is not found.
            Callers must treat None as "do not run amixer" — guessing a card
            sends mixer commands to somebody else's hardware.
        """
        if self._alsa_card:
            return self._alsa_card

        try:
            with open("/proc/asound/cards", "r") as f:
                for line in f:
                    # Format: " 4 [Ga  ]: USB-Audio - CORSAIR VIRTUOSO SE Wireless Ga"
                    low = line.lower()
                    if "corsair" in low or "virtuoso" in low:
                        index = line.split()[0]
                        if index.isdigit():
                            self._alsa_card = index
                            return self._alsa_card
        except Exception:
            pass

        # Fallback: buscar con aplay -l
        try:
            result = subprocess.run(
                ["aplay", "-l"], capture_output=True, text=True, timeout=5
            )
            for line in result.stdout.splitlines():
                low = line.lower()
                if ("corsair" in low or "virtuoso" in low) and line.startswith("card "):
                    card_num = line.split(":")[0].strip().split()[-1]
                    if card_num.isdigit():
                        self._alsa_card = card_num
                        return self._alsa_card
        except Exception:
            pass

        return None

    def set_sidetone(self, value):
        """Controls the sidetone via ALSA mixer.

        Args:
            value: 'on', 'off', or a number 0-100 (percentage).

        Returns:
            True if the command was executed successfully.
        """
        card = self._find_alsa_card()
        if card is None:
            print("Error: Corsair ALSA card not found", file=sys.stderr)
            return False

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
        if card is None:
            print("Error: Corsair ALSA card not found", file=sys.stderr)
            return False
        try:
            val_str = f"{value}%" if "%" not in str(value) else str(value)
            result = subprocess.run(
                ["amixer", "-c", card, "sset", "Headset", val_str],
                capture_output=True, text=True, timeout=5
            )
            return result.returncode == 0
        except Exception:
            return False

    # ─── Micrófono (ALSA) ───────────────────────────────────────────

    def set_mic_mute(self, muted):
        """Mutes/unmutes the microphone via the ALSA capture switch.

        'Mic' is a capture-only control, so amixer needs cap/nocap here.
        mute/unmute are playback verbs — amixer accepts them, exits 0, and
        silently leaves the capture switch untouched.

        NOT used by the GUI, deliberately. The desktop already handles the
        headset's physical mute button by muting the PipeWire source, which
        sits above this switch. Muting here as well leaves the mic off at the
        ALSA layer, so the physical button appears to stop working — it toggles
        PipeWire over a mic that is already hard-muted underneath. Call this
        only if nothing else is managing mute.

        Args:
            muted: True to mute, False to unmute.

        Returns:
            True if the command was executed successfully.
        """
        card = self._find_alsa_card()
        if card is None:
            print("Error: Corsair ALSA card not found", file=sys.stderr)
            return False
        try:
            result = subprocess.run(
                ["amixer", "-c", card, "sset", "Mic", "nocap" if muted else "cap"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode != 0:
                print(f"Error amixer mic: {result.stderr}", file=sys.stderr)
                return False
            return True
        except Exception as e:
            print(f"Error mic mute: {e}", file=sys.stderr)
            return False

    def get_mic_muted(self):
        """Reads the current microphone mute state from ALSA.

        Lets the UI pick up changes made outside the app — including the
        headset's physical mic-mute button, if the firmware routes it
        through the capture switch.

        Returns:
            True if muted, False if active, None if it could not be read.
        """
        card = self._find_alsa_card()
        if card is None:
            return None
        try:
            result = subprocess.run(
                ["amixer", "-c", card, "sget", "Mic"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode != 0:
                return None
            for line in result.stdout.splitlines():
                # Only the channel line carries the switch state.
                if "[off]" in line:
                    return True
                if "[on]" in line:
                    return False
        except Exception:
            pass
        return None


# ─── CLI ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python3 virtuoso_control.py sidetone [on|off|0-100]")
        print("  python3 virtuoso_control.py sidetone-v2w [0-100]")
        print("  python3 virtuoso_control.py volume [0-100]")
        print("  python3 virtuoso_control.py mic [mute|unmute|status]   (PipeWire)")
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

    elif category == "mic":
        val = sys.argv[2].lower() if len(sys.argv) > 2 else "status"
        if val == "status":
            state = ctrl.get_mic_muted_pw()
            if state is None:
                print("✗ Could not read mic state.")
            else:
                print(f"Mic: {'Muted' if state else 'Active'}")
        elif val in ("mute", "unmute"):
            if ctrl.set_mic_mute_pw(val == "mute"):
                print(f"✓ Mic: {'Muted' if val == 'mute' else 'Active'}")
            else:
                print("✗ Error adjusting mic.")
        else:
            print("Usage: mic [mute|unmute|status]")
