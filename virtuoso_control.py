#!/usr/bin/env python3
"""
Virtuoso Control — HID Controller for Corsair Virtuoso SE

Manages microphone LED, sidetone and battery via HID connection
persistent (like iCUE), preventing USB disconnections caused by
the previous open/close pattern for each command.
"""
import hid
import json
import math
import os
import re
import struct
import subprocess
import sys
import time
import wave

VENDOR_ID = 0x1b1c
PRODUCT_IDS = {
    # Wireless Dongles
    0x0a3e: "Wireless",  # Virtuoso SE Wireless
    0x0a3f: "Wireless",  # Virtuoso RGB Wireless
    0x0a42: "Wireless",  # Virtuoso Black Wireless (Slipstream)
    0x0a46: "Wireless",  # Virtuoso White Wireless
    0x0a4a: "Wireless",  # Virtuoso SE v2 Wireless
    0x0a55: "Wireless",  # Virtuoso XT Wireless
    0x0a63: "Wireless",  # Virtuoso High-Fidelity
    0x0a64: "Wireless",  # Virtuoso Pearl Wireless
    
    # Wired Connections / Direct USB headsets
    0x0a3d: "Wired",     # Virtuoso SE
    0x0a41: "Wired",     # Virtuoso Black
    0x0a45: "Wired",     # Virtuoso White
    0x0a49: "Wired",     # Virtuoso SE v2
    0x0a54: "Wired",     # Virtuoso XT
    0x0a62: "Wired",     # Virtuoso Pearl
}

DEVICE_MODELS = {
    0x0a3d: "Corsair Virtuoso SE (Wired)",
    0x0a3e: "Corsair Virtuoso SE (Wireless)",
    0x0a3f: "Corsair Virtuoso RGB (Wireless)",
    0x0a41: "Corsair Virtuoso RGB (Wired)",
    0x0a42: "Corsair Virtuoso RGB (Wireless Dongle)",
    0x0a45: "Corsair Virtuoso White (Wired)",
    0x0a46: "Corsair Virtuoso White (Wireless)",
    0x0a49: "Corsair Virtuoso SE v2 (Wired)",
    0x0a4a: "Corsair Virtuoso SE v2 (Wireless Dongle)",
    0x0a54: "Corsair Virtuoso XT (Wired)",
    0x0a55: "Corsair Virtuoso XT (Wireless)",
    0x0a62: "Corsair Virtuoso Pearl (Wired)",
    0x0a63: "Corsair Virtuoso High-Fidelity (Wireless)",
    0x0a64: "Corsair Virtuoso Pearl (Wireless Dongle)",
}

# Official Corsair battery status thresholds from virtuoso_black_manifest
BATTERY_THRESHOLDS = {
    "CriticalLow": 15,
    "Low": 30,
    "Medium": 50,
    "High": 97,
    "Charged": 100,
}

# 10-Band Official Equalizer presets from iCUE 5 defaultUnlimitedEqualizerPresets
EQ_FREQUENCIES = [32, 64, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]  # Hz

EQ_PRESETS = {
    "Pure Direct": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    "Movie Theater": [-1.0, 2.0, 1.0, 1.0, 3.0, 4.0, 3.0, 1.0, 1.0, 3.0],
    "FPS Competition": [-2.0, -2.0, 1.0, 2.0, 1.0, 2.0, 4.0, 5.0, 2.0, 4.0],
    "Clear Chat": [-2.0, -1.0, 1.0, 4.0, 5.0, 4.0, 2.0, 2.0, 1.0, 1.0],
    "Bass Boost": [4.0, 6.0, 4.0, 0.0, -3.0, -3.0, -2.0, 1.0, 2.0, 2.0],
}

# V2W Endpoints
EP_RECEIVER = 0x08  # Wireless Dongle/Receiver
EP_HEADSET = 0x09   # Auriculares

# Tipos de comando V2W
CMD_GET = 0x01
CMD_SET = 0x02

# Bragi / V2W Property IDs (extracted from bragiprotocol::PropertyId)
OP_POLLING_RATE = 0x01             # uint8
OP_BRIGHTNESS = 0x02               # uint8 (0-100)
OP_OPERATING_MODE = 0x03           # 0x01: HW, 0x02: SW
OP_SLEEP_ENABLED = 0x0d            # bool (0/1) - AutomaticSleepEnabled (Bragi 0x0d)
OP_SLEEP_TIMEOUT_NORMAL = 0x0e     # uint32 LE ms - AutomaticSleepTimeoutNormal (Bragi 0x0e)
OP_BATTERY = 0x0f                  # uint8 (0-100%)
OP_CHARGE = 0x10                   # 1 = charging, 2 = discharging
OP_FIRMWARE_VERSION = 0x12         # firmware version
OP_BOOTLOADER_VERSION = 0x13       # bootloader version
OP_SLEEP_TIMEOUT_POWERSAVE = 0x37  # uint32 LE ms (on-device sleep timer)
OP_SIDETONE_MUTED = 0x46           # bool (0=unmuted, 1=muted)
OP_SIDETONE_LEVEL = 0x47           # uint16 LE (0-1000)
OP_MIC_MUTED = 0x8e                # bool (0=active, 1=muted)
OP_MIC_LEVEL = 0x8f                # uint8 (0-100)

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


def is_headset_audio_active(sink_name=None):
    """Checks if audio is currently playing to the Corsair Virtuoso sink or EQ sink.

    Reads 'pactl list sinks short' and checks if the state of our sink is RUNNING.
    Returns True if audio is playing, False otherwise.
    """
    try:
        res = subprocess.run(["pactl", "list", "sinks", "short"],
                             capture_output=True, text=True, timeout=2)
        for line in res.stdout.splitlines():
            low = line.lower()
            if "corsair" in low or "virtuoso" in low or "virtuoso_eq" in low:
                state = line.strip().split()[-1]
                if state == "RUNNING":
                    return True
        return False
    except Exception:
        return False


def is_screen_locked():
    """Checks if the user session/screensaver is currently locked.

    Uses standard D-Bus org.freedesktop.ScreenSaver.GetActive.
    Works reliably on KDE Plasma, GNOME, and XFCE.
    """
    try:
        r = subprocess.run(
            ["busctl", "--user", "call", "org.freedesktop.ScreenSaver",
             "/ScreenSaver", "org.freedesktop.ScreenSaver", "GetActive"],
            capture_output=True, text=True, timeout=2
        )
        if r.returncode == 0 and "b true" in r.stdout.strip():
            return True
    except Exception:
        pass
    return False


class PipeWireEqualizer:
    """Manages a native 10-band PipeWire biquad filter chain equalizer.

    Replicates the exact APO architecture found in Corsair iCUE's CorsairGamingAudioPO64.dll:
    - Band 1 (32 Hz): bq_lowshelf (Q=1.0)
    - Bands 2-9 (64, 125, 250, 500, 1000, 2000, 4000, 8000 Hz): bq_peaking (Q=1.414)
    - Band 10 (16000 Hz): bq_highshelf (Q=1.0)
    Creates virtual sink 'effect_input.virtuoso_eq' and connects directly to the
    Corsair Virtuoso hardware sink with bit-perfect fidelity.
    """

    def __init__(self, hw_sink_name=None):
        self.hw_sink_name = hw_sink_name
        self.config_dir = os.path.expanduser("~/.config/pipewire")
        self.config_path = os.path.join(self.config_dir, "virtuoso-filter-chain.conf")
        self.process = None
        self.is_enabled = False
        self.current_gains = [0.0] * 10

    def find_hw_sink(self):
        if self.hw_sink_name:
            return self.hw_sink_name
        try:
            res = subprocess.run(["pactl", "list", "sinks", "short"],
                                 capture_output=True, text=True, timeout=3)
            for line in res.stdout.splitlines():
                parts = line.strip().split("\t")
                if len(parts) >= 2:
                    name = parts[1]
                    if ("corsair" in name.lower() or "virtuoso" in name.lower()) and "virtuoso_eq" not in name:
                        self.hw_sink_name = name
                        return name
        except Exception:
            pass
        return None

    def generate_config(self, gains):
        hw_sink = self.find_hw_sink()
        target_line = f'target.object = "{hw_sink}"' if hw_sink else ""

        nodes_conf = []
        links_conf = []

        for i, (freq, gain) in enumerate(zip(EQ_FREQUENCIES, gains)):
            idx = i + 1
            node_name = f"eq_band_{idx}"
            if i == 0:
                label = "bq_lowshelf"
                q_val = 1.0
            elif i == len(EQ_FREQUENCIES) - 1:
                label = "bq_highshelf"
                q_val = 1.0
            else:
                label = "bq_peaking"
                q_val = 1.414

            nodes_conf.append(f"""                    {{
                        type  = builtin
                        name  = {node_name}
                        label = {label}
                        control = {{ "Freq" = {float(freq):.1f} "Q" = {q_val:.3f} "Gain" = {float(gain):.1f} }}
                    }}""")

            if i > 0:
                prev_node = f"eq_band_{i}"
                links_conf.append(f"""                    {{ output = "{prev_node}:Out" input = "{node_name}:In" }}""")

        nodes_str = "\n".join(nodes_conf)
        links_str = "\n".join(links_conf)

        content = f"""context.properties = {{
    log.level = 0
}}
context.spa-libs = {{
    audio.convert.* = audioconvert/libspa-audioconvert
    support.*       = support/libspa-support
}}
context.modules = [
    {{ name = libpipewire-module-rt flags = [ ifexists nofail ] }}
    {{ name = libpipewire-module-protocol-native }}
    {{ name = libpipewire-module-client-node }}
    {{ name = libpipewire-module-adapter }}
    {{ name = libpipewire-module-filter-chain
        args = {{
            node.description = "Corsair Virtuoso Equalizer"
            media.name       = "Corsair Virtuoso Equalizer"
            filter.graph = {{
                nodes = [
{nodes_str}
                ]
                links = [
{links_str}
                ]
            }}
            audio.channels = 2
            audio.position = [ FL FR ]
            capture.props = {{
                node.name   = "effect_input.virtuoso_eq"
                media.class = Audio/Sink
                node.description = "Corsair Virtuoso Equalizer"
            }}
            playback.props = {{
                node.name   = "effect_output.virtuoso_eq"
                node.passive = true
                {target_line}
            }}
        }}
    }}
]
"""
        os.makedirs(self.config_dir, exist_ok=True)
        with open(self.config_path, "w", encoding="utf-8") as f:
            f.write(content)
        return self.config_path

    def _get_active_sink_inputs(self):
        inputs = []
        try:
            res = subprocess.run(["pactl", "list", "sink-inputs", "short"],
                                 capture_output=True, text=True, timeout=2)
            for line in res.stdout.splitlines():
                parts = line.strip().split()
                if len(parts) >= 2:
                    inputs.append((parts[0], parts[1]))
        except Exception:
            pass
        return inputs

    def _move_streams_to_sink(self, target_sink):
        try:
            inputs = self._get_active_sink_inputs()
            for input_id, _ in inputs:
                subprocess.run(["pactl", "move-sink-input", input_id, target_sink],
                               capture_output=True, timeout=1)
        except Exception:
            pass

    def start(self, gains=None):
        if gains is not None:
            self.current_gains = [float(g) for g in gains]

        self.stop_process_only()
        self.generate_config(self.current_gains)

        self.process = subprocess.Popen(
            ["pipewire", "-c", self.config_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True
        )

        # Wait for sink to appear in PipeWire
        for _ in range(25):
            time.sleep(0.04)
            try:
                res = subprocess.run(["pactl", "list", "sinks", "short"],
                                     capture_output=True, text=True, timeout=1)
                if "effect_input.virtuoso_eq" in res.stdout:
                    break
            except Exception:
                pass

        # Switch default sink and move active streams
        try:
            subprocess.run(["pactl", "set-default-sink", "effect_input.virtuoso_eq"],
                           capture_output=True, timeout=1)
            self._move_streams_to_sink("effect_input.virtuoso_eq")
        except Exception:
            pass

        self.is_enabled = True
        return True

    def stop_process_only(self):
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=1.0)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass
            self.process = None

        try:
            subprocess.run(["pkill", "-f", "virtuoso-filter-chain.conf"],
                           capture_output=True, timeout=1)
        except Exception:
            pass

    def stop(self):
        hw_sink = self.find_hw_sink()
        if hw_sink:
            try:
                subprocess.run(["pactl", "set-default-sink", hw_sink],
                               capture_output=True, timeout=1)
                self._move_streams_to_sink(hw_sink)
            except Exception:
                pass

        self.stop_process_only()
        self.is_enabled = False
        return True

    def set_gains(self, gains):
        self.current_gains = [float(g) for g in gains]
        if self.is_enabled:
            return self.start(self.current_gains)
        return True


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
        self.product_id = None
        self._sleep_timer_minutes = 10
        self._sounds_dir = os.path.join(os.path.dirname(os.path.realpath(__file__)), "sounds")
        self.eq = PipeWireEqualizer()

    # ─── Connection Management ───────────────────────────────────────

    def _find_candidates(self):
        """Returns candidate (path, mode, pid) tuples, interface 4/3 for wireless, 0 for wired."""
        candidates = []
        for iface in [4, 3, 0]:
            for pid, mode in PRODUCT_IDS.items():
                for d in hid.enumerate(VENDOR_ID, pid):
                    if d['interface_number'] == iface:
                        candidates.append((d['path'], mode, pid))
        return candidates

    def _probe_interface(self, dev):
        """Sends a firmware-version request and checks for a response.

        Some dongles (e.g. Slipstream Multi-Device Receiver) expose both
        HID interfaces 3 and 4, but only one carries V2W traffic. The
        wrong interface accepts writes silently and never replies. This
        probe distinguishes the two in ~50 ms.
        """
        # Temporarily mount dev so _send_v2w can write to it.
        saved, self.device = self.device, dev
        try:
            if not self._send_v2w(EP_RECEIVER, CMD_SET, [0x13]):
                return False
            for _ in range(10):
                time.sleep(0.02)
                try:
                    data = dev.read(64)
                    if data:
                        return True
                except Exception:
                    return False
            return False
        finally:
            self.device = saved

    @property
    def is_connected(self):
        """Indicates if there is an active HID connection."""
        return self._connected and self.device is not None

    def get_device_name(self):
        """Returns the human-readable product name based on detected USB PID."""
        if self.product_id in DEVICE_MODELS:
            return DEVICE_MODELS[self.product_id]
        if self.connection_mode != "Unknown":
            return f"Corsair Virtuoso ({self.connection_mode})"
        return "Corsair Virtuoso"

    def connect(self):
        """Opens the HID device and performs the V2W handshake.

        Called ONCE. The connection is kept open until
        disconnect() is called or the app closes.

        Returns:
            True if the connection was successful, False otherwise.
        """
        if self.is_connected:
            return True

        for path, mode, pid in self._find_candidates():
            try:
                dev = hid.device()
                dev.open_path(path)
                dev.set_nonblocking(True)
            except Exception as e:
                print(f"Error opening {path}: {e}", file=sys.stderr)
                continue

            if mode == "Wired" or self._probe_interface(dev):
                self.device = dev
                self.connection_mode = mode
                self.product_id = pid
                self._connected = True
                return True
            dev.close()

        return False

    def _ensure_handshake(self):
        """Performs the V2W handshake if not done yet.

        Called automatically before any operation that
        needs it (LED, battery, V2W sidetone, heartbeat).

        The handshake is intentionally deferred from connect(): sending it
        immediately would put the headset into software mode, which turns
        the dongle LED off until disconnect.
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

        The receiver release below is now redundant -- _do_handshake() no longer
        puts the receiver into software mode -- but it is kept as a repair path:
        software mode survives closing the handle and even replugging the app,
        so this recovers a dongle left dark by an older build or a crashed run.
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
        self._leds_initialized = False

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
        2. (skipped -- see below) software mode on receiver
        3. Heartbeat to receiver
        4. Enable software mode on headset
        5. Clean buffer
        6. Heartbeat to headset

        HeadsetControl's step 2 puts the *receiver* into software mode as well,
        which switches the dongle's own status LED off for as long as the mode
        is held -- the same hand-over that kills the mic-mute button on the
        headset. Nothing here drives the dongle LED, so it just stays dark.

        Verified on a Virtuoso SE (PID 0x0a3e, freshly power-cycled): with this
        step omitted, battery queries and set_all_rgb() both still work and the
        dongle LED stays lit. Only the headset needs software mode. If a future
        model turns out to need the receiver in software mode for LED control,
        this is the line to restore.
        """
        # 1. Firmware request to receiver
        self._send_v2w(EP_RECEIVER, CMD_SET, [0x13])

        # 2. Software mode to receiver -- intentionally NOT sent, see docstring

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
        if getattr(self, '_leds_initialized', False):
            return True
        ok = self._send_v2w(EP_HEADSET, 0x0d, [0x00, 0x01])
        if ok:
            self._leds_initialized = True
        return ok

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
        
        # Helper to scale and assign using perceptual (quadratic) scaling
        def _apply_zone(idx, rgb_tuple, brightness):
            scale = (brightness / 100.0) ** 2.0
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
        for _ in range(8):
            time.sleep(0.04)
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
            wired_pids = [pid for pid, mode in PRODUCT_IDS.items() if mode == "Wired"]
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
        if self.connection_mode == "Wired":
            return "N/A (Wired)"
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
            return self.is_connected

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
                return self.is_connected
            return True
        except subprocess.TimeoutExpired:
            print("Timeout en amixer", file=sys.stderr)
            return self.is_connected
        except Exception as e:
            print(f"Error sidetone ALSA: {e}", file=sys.stderr)
            return self.is_connected

    # ─── Sidetone (V2W HID) ─────────────────────────────────────────

    def set_sidetone_v2w(self, level):
        """Controls the sidetone safely via ALSA mixer fallback.

        Note: Sending Void v2w ANC/Sidetone opcodes (0xd1, 0x46, 0x47 with 0-1000 scale)
        to Corsair Virtuoso causes the headset MCU firmware to freeze/lock up.
        Redirected to ALSA mixer to prevent hardware lockups.
        """
        return self.set_sidetone("off" if level == 0 else level)

    # ─── Volumen (PipeWire / ALSA) ──────────────────────────────────

    def set_volume(self, value):
        """Adjusts the headset volume via PipeWire (with ALSA mapped fallback)."""
        val_str = f"{value}%" if "%" not in str(value) else str(value)
        sink = self._find_pw_sink()
        if sink is not None:
            try:
                r = subprocess.run(
                    ["pactl", "set-sink-volume", sink, val_str],
                    capture_output=True, text=True, timeout=5
                )
                if r.returncode == 0:
                    return True
                # Sink name may have changed after reconnect; invalidate and retry once
                self._pw_sink = None
                sink = self._find_pw_sink()
                if sink is not None:
                    r = subprocess.run(
                        ["pactl", "set-sink-volume", sink, val_str],
                        capture_output=True, text=True, timeout=5
                    )
                    if r.returncode == 0:
                        return True
            except Exception:
                pass

        # Fallback: ALSA mixer with -M (perceptual mapped volume)
        card = self._find_alsa_card()
        if card is None:
            print("Error: Corsair audio device not found", file=sys.stderr)
            return False
        try:
            result = subprocess.run(
                ["amixer", "-M", "-c", card, "sset", "Headset", val_str],
                capture_output=True, text=True, timeout=5
            )
            return result.returncode == 0
        except Exception:
            return False

    def get_volume(self):
        """Reads the current headset volume percentage (0-100) from PipeWire (or ALSA)."""
        sink = self._find_pw_sink()
        if sink is not None:
            try:
                r = subprocess.run(
                    ["pactl", "get-sink-volume", sink],
                    capture_output=True, text=True, timeout=5
                )
                if r.returncode != 0:
                    self._pw_sink = None
                else:
                    m = re.search(r'(\d+)%', r.stdout)
                    if m:
                        return max(0, min(100, int(m.group(1))))
            except Exception:
                pass

        card = self._find_alsa_card()
        if card is None:
            return None
        try:
            r = subprocess.run(
                ["amixer", "-M", "-c", card, "sget", "Headset"],
                capture_output=True, text=True, timeout=5
            )
            if r.returncode == 0:
                m = re.search(r'\[(\d+)%\]', r.stdout)
                if m:
                    return max(0, min(100, int(m.group(1))))
        except Exception:
            pass
        return None

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

    # ─── Sleep Timer (Auto-Shutdown) ───────────────────────────────

    def set_sleep_timer(self, minutes):
        """Sets the configured sleep timeout in minutes.

        Range: 0 (disabled), or 1-99 minutes.
        Also synchronizes with Bragi hardware properties 0x0d and 0x0e when connected.
        """
        try:
            mins = int(minutes)
        except (ValueError, TypeError):
            return False

        mins = max(0, min(99, mins))
        self._sleep_timer_minutes = mins

        # If connected and handshake active, synchronize Bragi sleep properties with hardware
        if self.is_connected and self._handshake_done:
            try:
                # 0x0d: AutomaticSleepEnabled (1 if mins > 0 else 0)
                enabled = 1 if mins > 0 else 0
                self._send_v2w(EP_HEADSET, CMD_SET, [OP_SLEEP_ENABLED, 0x00, enabled])
                if mins > 0:
                    # 0x0e: AutomaticSleepTimeoutNormal (ms in uint32 LE)
                    ms = mins * 60 * 1000
                    payload = [OP_SLEEP_TIMEOUT_NORMAL, 0x00,
                               ms & 0xff, (ms >> 8) & 0xff, (ms >> 16) & 0xff, (ms >> 24) & 0xff]
                    self._send_v2w(EP_HEADSET, CMD_SET, payload)
            except Exception:
                pass

        return True

    def get_sleep_timer(self):
        """Returns the current configured sleep timer in minutes (default 10)."""
        return getattr(self, "_sleep_timer_minutes", 10)

    def get_hw_sleep_timer(self):
        """Queries the hardware sleep timeout from Bragi properties 0x0d and 0x0e.
        Returns a dict with 'enabled' and 'timeout_ms' or None if unqueried.
        """
        if not self.is_connected or not self._ensure_handshake():
            return None
        info = {}
        try:
            self._flush_buffer()
            if self._send_v2w(EP_HEADSET, CMD_SET, [OP_SLEEP_ENABLED]):
                for _ in range(5):
                    time.sleep(0.03)
                    res = self.device.read(64)
                    if res and len(res) > 4 and res[0] == 0x01:
                        info["enabled"] = bool(res[4])
                        break
            self._flush_buffer()
            if self._send_v2w(EP_HEADSET, CMD_SET, [OP_SLEEP_TIMEOUT_NORMAL]):
                for _ in range(5):
                    time.sleep(0.03)
                    res = self.device.read(64)
                    if res and len(res) > 7 and res[0] == 0x01:
                        ms = res[4] | (res[5] << 8) | (res[6] << 16) | (res[7] << 24)
                        info["timeout_ms"] = ms
                        info["timeout_min"] = ms // 60000
                        break
        except Exception:
            pass
        return info if info else None

    # ─── Notificaciones de Audio Oficiales (Voice Prompts) ───────────

    def play_sound(self, sound_name):
        """Plays an official Corsair sound prompt through the headset.

        Looks for sound_name (or sound_name.wav) in the sounds/ directory.
        Uses paplay with the headset sink if found, or default sink.
        Non-blocking.
        """
        if not sound_name:
            return False

        if not sound_name.lower().endswith(".wav"):
            sound_name = f"{sound_name}.wav"

        sound_path = os.path.join(self._sounds_dir, sound_name)
        if not os.path.exists(sound_path):
            return False

        sink = self._find_pw_sink()
        cmd = ["paplay"]
        if sink is not None:
            cmd.extend(["-d", sink])
        cmd.append(sound_path)

        try:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except Exception:
            try:
                subprocess.Popen(["aplay", "-q", sound_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return True
            except Exception:
                return False

    # ─── Batería y Umbrales Oficiales ────────────────────────────────

    def get_battery_threshold_state(self, level=None):
        """Returns the official Corsair threshold state: CriticalLow, Low, Medium, High, Charged."""
        if level is None:
            level = self._last_level
        if level is None:
            return "Unknown"
        if level <= BATTERY_THRESHOLDS["CriticalLow"]:
            return "CriticalLow"
        elif level <= BATTERY_THRESHOLDS["Low"]:
            return "Low"
        elif level <= BATTERY_THRESHOLDS["Medium"]:
            return "Medium"
        elif level <= BATTERY_THRESHOLDS["High"]:
            return "High"
        return "Charged"

    # ─── Ecualizador Oficial (10 Bandas) ────────────────────────────

    def get_eq_presets(self):
        """Returns the official 10-band equalizer presets extracted from Corsair iCUE."""
        return dict(EQ_PRESETS)

    def export_easyeffects_preset(self, preset_name, output_path=None):
        """Exports an official 10-band EQ preset to EasyEffects output preset JSON format."""
        if preset_name not in EQ_PRESETS:
            return None

        gains = EQ_PRESETS[preset_name]
        data = {
            "output": {
                "blocklist": [],
                "equalizer#0": {
                    "bypass": False,
                    "input-gain": 0.0,
                    "output-gain": 0.0,
                    "num-bands": len(EQ_FREQUENCIES),
                    "split-channels": False
                },
                "plugins_order": ["equalizer#0"]
            }
        }
        for i, (freq, gain) in enumerate(zip(EQ_FREQUENCIES, gains)):
            data["output"]["equalizer#0"][f"band{i}"] = {
                "frequency": float(freq),
                "gain": float(gain),
                "q": 1.414,
                "mode": "Bell",
                "mute": False,
                "solo": False
            }

        if output_path is None:
            config_dir = os.path.expanduser("~/.config/easyeffects/output")
            os.makedirs(config_dir, exist_ok=True)
            safe_name = preset_name.lower().replace(" ", "_")
            output_path = os.path.join(config_dir, f"virtuoso_{safe_name}.json")

        try:
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            return output_path
        except Exception as e:
            print(f"Error exporting EasyEffects preset: {e}", file=sys.stderr)
            return None

    def enable_eq(self, enabled, gains=None):
        """Enables or disables the real-time PipeWire 10-band equalizer."""
        if enabled:
            return self.eq.start(gains)
        else:
            return self.eq.stop()

    def set_eq_gains(self, gains):
        """Sets the 10-band equalizer gains in real-time."""
        return self.eq.set_gains(gains)

    def is_audio_active(self):
        """Checks if audio is currently playing to the headset."""
        return is_headset_audio_active(self._find_pw_sink())

    def is_screen_locked(self):
        """Checks if the user screen/session is locked."""
        return is_screen_locked()

    def enter_standby_sleep(self):
        """Prepares headset for firmware deep sleep:
        1. Turns off LEDs
        2. Mutes ALSA sidetone so mic monitor is cut immediately
        3. Hands control back to firmware (_leave_software_mode)
        4. Allows on-device sleep timer to power down the headset.
        """
        if not self.is_connected:
            return False
        try:
            self.set_all_rgb((0, 0, 0), 0, (0, 0, 0), 0, (0, 0, 0), 0)
            time.sleep(0.05)
            self.set_sidetone("off")
            self._leave_software_mode()
            self._handshake_done = False
            self._flush_buffer()
            return True
        except Exception:
            return False

    def check_passive_wakeup(self):
        """Checks for unsolicited packets pushed by the headset (e.g. power on, button press)
        WITHOUT sending any command or performing a handshake (which would wake the headset).
        Returns True if the headset pushed any packet, False otherwise.
        """
        if not self.is_connected or not self.device:
            return False
        try:
            data = self.device.read(64)
            if data and len(data) > 0:
                return True
        except Exception:
            pass
        return False

    def wake_from_standby_sleep(self):
        """Re-engages software mode and restores connection after sleep."""
        if not self.is_connected:
            return False
        try:
            self._handshake_done = False
            return self._ensure_handshake()
        except Exception:
            return False


# ─── CLI ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python3 virtuoso_control.py sidetone [on|off|0-100]")
        print("  python3 virtuoso_control.py sidetone-v2w [0-100]")
        print("  python3 virtuoso_control.py volume [0-100|status]")
        print("  python3 virtuoso_control.py mic [mute|unmute|status]   (PipeWire)")
        print("  python3 virtuoso_control.py battery")
        print("  python3 virtuoso_control.py sleep [minutes|off|status]")
        print("  python3 virtuoso_control.py eq [enable <preset>|disable|status|set <gains...>|list|export <preset>]")
        print("  python3 virtuoso_control.py sound [list|play <sound_name>]")
        print("  python3 virtuoso_control.py device-info")
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
        val = sys.argv[2].lower() if len(sys.argv) > 2 else "status"
        if val == "status":
            current_vol = ctrl.get_volume()
            if current_vol is not None:
                print(f"Volumen: {current_vol}%")
            else:
                print("✗ Could not read volume.")
        else:
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

    elif category == "sleep":
        val = sys.argv[2].lower() if len(sys.argv) > 2 else "status"
        if val == "status":
            print(f"Sleep Timer (Config): {ctrl.get_sleep_timer()} min")
            print(f"Audio Active: {ctrl.is_audio_active()}")
            print(f"Screen Locked: {ctrl.is_screen_locked()}")
            if ctrl.connect():
                hw = ctrl.get_hw_sleep_timer()
                if hw:
                    print(f"Hardware Sleep: {hw}")
                ctrl.disconnect()
        elif val == "off":
            ctrl.set_sleep_timer(0)
            print("✓ Sleep timer disabled (0 min).")
        else:
            try:
                mins = int(val)
                ctrl.set_sleep_timer(mins)
                print(f"✓ Sleep timer set to {mins} min.")
            except ValueError:
                print("Usage: sleep [minutes|off|status]")

    elif category == "sound":
        action = sys.argv[2].lower() if len(sys.argv) > 2 else "list"
        if action == "list":
            sounds_dir = os.path.join(os.path.dirname(os.path.realpath(__file__)), "sounds")
            if os.path.exists(sounds_dir):
                files = sorted([f.replace(".wav", "") for f in os.listdir(sounds_dir) if f.endswith(".wav")])
                print("Available sound prompts:")
                for s in files:
                    print(f"  - {s}")
            else:
                print("No sounds directory found.")
        elif action == "play":
            name = sys.argv[3] if len(sys.argv) > 3 else "MicOn"
            if ctrl.play_sound(name):
                print(f"✓ Playing prompt '{name}'...")
                time.sleep(0.8)
            else:
                print(f"✗ Prompt '{name}' not found or error playing.")
        else:
            print("Usage: sound [list|play <name>]")

    elif category == "eq":
        action = sys.argv[2].lower() if len(sys.argv) > 2 else "list"
        if action == "list":
            print("Official 10-Band EQ Presets (Corsair iCUE):")
            for name, gains in EQ_PRESETS.items():
                print(f"  [{name}]")
                bands_str = " ".join(f"{f}Hz:{g:+.1f}dB" for f, g in zip(EQ_FREQUENCIES, gains))
                print(f"    {bands_str}")
        elif action == "status":
            res = subprocess.run(["pactl", "list", "sinks", "short"], capture_output=True, text=True).stdout
            is_active = "effect_input.virtuoso_eq" in res
            def_sink = subprocess.run(["pactl", "get-default-sink"], capture_output=True, text=True).stdout.strip()
            print(f"EQ Active: {is_active}")
            print(f"Default Sink: {def_sink}")
        elif action == "enable":
            preset_name = sys.argv[3] if len(sys.argv) > 3 else "Pure Direct"
            gains = EQ_PRESETS.get(preset_name, [0.0] * 10)
            if ctrl.enable_eq(True, gains):
                print(f"✓ PipeWire EQ enabled with preset '{preset_name}'.")
            else:
                print("✗ Failed to enable PipeWire EQ.")
        elif action == "disable":
            if ctrl.enable_eq(False):
                print("✓ PipeWire EQ disabled, restored direct hardware sink.")
            else:
                print("✗ Failed to disable PipeWire EQ.")
        elif action == "set":
            if len(sys.argv) < 13:
                print("Usage: eq set <gain1> <gain2> ... <gain10> (10 dB values)")
            else:
                try:
                    gains = [float(sys.argv[i]) for i in range(3, 13)]
                    if ctrl.enable_eq(True, gains):
                        print("✓ Applied 10-band gains in real-time.")
                    else:
                        print("✗ Failed to apply gains.")
                except ValueError:
                    print("Invalid numeric values for gains.")
        elif action == "export":
            preset = sys.argv[3] if len(sys.argv) > 3 else "Pure Direct"
            path = ctrl.export_easyeffects_preset(preset)
            if path:
                print(f"✓ Exported EasyEffects preset to: {path}")
            else:
                print(f"✗ Failed to export preset '{preset}'. Available: {list(EQ_PRESETS.keys())}")
        else:
            print("Usage: eq [enable <preset>|disable|status|set <gains...>|list|export <preset>]")

    elif category == "device-info":
        if ctrl.connect():
            print(f"Device: {ctrl.get_device_name()}")
            print(f"Mode: {ctrl.connection_mode}")
            print(f"Product ID: {hex(ctrl.product_id) if ctrl.product_id else 'Unknown'}")
            print(f"Battery: {ctrl.get_battery()}")
            print(f"Sleep Timer: {ctrl.get_sleep_timer()} min")
            print(f"Audio Active: {ctrl.is_audio_active()}")
            ctrl.disconnect()
        else:
            print("Error: Virtuoso not found.")
