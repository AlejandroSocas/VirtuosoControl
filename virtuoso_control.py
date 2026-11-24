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

from virtuoso_protocol import *  # noqa: F401,F403  (re-export protocol & EQ constants)
from virtuoso_audio import (  # noqa: F401
    PipeWireEqualizer,
    is_headset_audio_active,
    is_screen_locked,
)


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
        self._pending_hw_reset = False  # headset woke up / changed operating mode
        self._last_level = None      # previous battery reading
        self._charging_inferred = False
        self._brightness = 100  # Current brightness (0-100), max by default
        self._last_hw_brightness = None
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
            if not self._send_v2w(EP_RECEIVER, CMD_GET_PROP, [OP_FIRMWARE_VERSION]):
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
        ok = self._send_v2w(EP_RECEIVER, CMD_SET_PROP, [OP_OPERATING_MODE, 0x00, 0x01])
        time.sleep(0.05)
        ok = self._send_v2w(EP_HEADSET, CMD_SET_PROP, [OP_OPERATING_MODE, 0x00, 0x01]) and ok
        time.sleep(0.05)
        self._last_hw_brightness = None
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
                elif len(data) > 3 and tuple(data[:3]) == NOTIFY_STATUS and data[3] in (OP_OPERATING_MODE, OP_CONNECTED_SUBDEVICES):
                    self._pending_hw_reset = True
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
        self._send_v2w(EP_RECEIVER, CMD_GET_PROP, [OP_FIRMWARE_VERSION])

        # 2. Software mode to receiver -- intentionally NOT sent, see docstring

        # 3. Heartbeat to receiver
        self._send_v2w(EP_RECEIVER, CMD_GET_PROP, [0x12])

        # 4. Software mode to headset (consume its ACK so it never shifts subsequent reads)
        self._send_v2w(EP_HEADSET, CMD_SET_PROP, [OP_OPERATING_MODE, 0x00, 0x02])
        for _ in range(20):
            time.sleep(0.015)
            try:
                r = self.device.read(64)
                if r and len(r) > 3 and r[0] == 0x01 and r[1] == 1 and r[2] == CMD_SET_PROP:
                    break
            except Exception:
                break

        # 5. Clean buffer de respuestas acumuladas
        self._flush_buffer()

        # 6. Heartbeat to headset (consume its reply so the HID queue starts completely empty)
        self._send_v2w(EP_HEADSET, CMD_GET_PROP, [0x12])
        for _ in range(20):
            time.sleep(0.015)
            try:
                r = self.device.read(64)
                if r and len(r) > 3 and r[0] == 0x01 and r[1] == 1 and r[2] == CMD_GET_PROP:
                    break
            except Exception:
                break
        self._flush_buffer()

    def _init_leds(self, force=False):
        """Initializes the RealTimeRgbLightings buffer (UnbindBuffers + OpenFile 0x00)."""
        if not force and getattr(self, '_leds_initialized', False):
            return True
        self._send_v2w(EP_HEADSET, CMD_UNBIND_BUFFERS, [0x00])
        time.sleep(0.02)
        ok = self._send_v2w(EP_HEADSET, CMD_OPEN_FILE, [0x00, 0x01])
        time.sleep(0.04)
        self._flush_buffer()
        if ok:
            self._leds_initialized = True
        return ok

    def set_hw_brightness(self, percent):
        """Sets hardware LED brightness via Bragi Property 0x02 (scale 0..1000 uint16 LE).
        Directly controls non-protected LEDs such as the Logo LED (zone 0).
        """
        if not self.is_connected or not self._ensure_handshake():
            return False
        pct = max(0, min(100, int(percent)))
        val = int(round(pct * 10))
        ok = self._set_bragi_property(EP_HEADSET, OP_BRIGHTNESS, [val & 0xff, (val >> 8) & 0xff])
        if ok:
            self._last_hw_brightness = pct
        return ok

    def get_hw_brightness(self):
        """Queries hardware LED brightness from Bragi Property 0x02 (returns 0..100 or None)."""
        res = self._get_bragi_property(EP_HEADSET, OP_BRIGHTNESS)
        if res and len(res) > 5:
            raw = res[4] | (res[5] << 8)
            return max(0, min(100, raw // 10))
        return None

    def set_all_rgb(self, logo_rgb, logo_b, mic_rgb, mic_b, batt_rgb, batt_b):
        """Adjusts the RGB color of all zones simultaneously.
        
        The command sends colors for the zones:
        [R1, R2, R3, G1, G2, G3, B1, B2, B3]
        0=Logo (controlled by OP_BRIGHTNESS 0x02), 1=Battery (protected), 2=Microphone (protected)
        """
        if not self.is_connected:
            return False
        if not self._ensure_handshake():
            return False

        self._init_leds()

        # Update hardware brightness (OP_BRIGHTNESS 0x02) for Logo LED when changed
        logo_pct = max(0, min(100, int(logo_b)))
        if self._last_hw_brightness != logo_pct:
            self.set_hw_brightness(logo_pct)

        data = [0] * 9

        # Zone 0 (Logo): firmware applies OP_BRIGHTNESS hardware scaling (or off if 0%)
        if logo_pct > 0:
            data[0] = max(0, min(255, int(logo_rgb[0])))
            data[3] = max(0, min(255, int(logo_rgb[1])))
            data[6] = max(0, min(255, int(logo_rgb[2])))

        # Protected LEDs (Zone 1 Battery, Zone 2 Mic): ignore OP_BRIGHTNESS, scale per-zone
        def _apply_protected_zone(idx, rgb_tuple, brightness):
            scale = (max(0, min(100, brightness)) / 100.0) ** 2.0
            data[idx] = int(rgb_tuple[0] * scale)
            data[idx + 3] = int(rgb_tuple[1] * scale)
            data[idx + 6] = int(rgb_tuple[2] * scale)

        _apply_protected_zone(1, batt_rgb, batt_b)
        _apply_protected_zone(2, mic_rgb, mic_b)

        payload = [0x00, 0x09, 0x00, 0x00, 0x00] + data
        return self._send_v2w(EP_HEADSET, CMD_WRITE_BUF_BEGIN, payload)

    def set_rgb(self, r, g, b, brightness=100):
        """Backwards compatibility method. Use set_all_rgb instead."""
        return self.set_all_rgb((r, g, b), brightness, (0, 0, 0), 0, (0, 0, 0), 0)

    def send_heartbeat(self):
        """Sends V2W heartbeat to keep the session alive.

        Must be called periodically (~every 60s per RequiredPingInterval) to prevent
        the firmware from resetting the LED state.

        Returns:
            True if the heartbeat was sent successfully.
        """
        if not self._ensure_handshake():
            return False
        return self._send_v2w(EP_HEADSET, CMD_GET_PROP, [0x12])

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
        if not self._send_v2w(EP_HEADSET, CMD_GET_PROP, [OP_BATTERY]):
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

    def reset_session_state(self):
        """Invalidates cached handshake/LED state so the next operation re-applies SW mode."""
        self._handshake_done = False
        self._leds_initialized = False
        self._last_hw_brightness = None
        self._pending_hw_reset = False

    def get_operating_mode(self):
        """Reads headset OperatingMode (0x03): 1=Hardware, 2=Software, None if unreachable."""
        res = self._get_bragi_property(EP_HEADSET, OP_OPERATING_MODE, retries=1)
        if res and len(res) > 4:
            return res[4]
        return None

    def consume_hw_reset_event(self):
        """Returns True once if an unsolicited wake/mode-change notification was received."""
        val = self._pending_hw_reset
        self._pending_hw_reset = False
        return val

    # ─── Physical mic-mute button ────────────────────────────────────

    def poll_mic_button(self):
        """Returns how many times the physical mic-mute button was pressed.

        Only works in software mode: that is the mode where the firmware stops
        acting on the button itself and forwards it to the host instead (see
        _do_handshake / _leave_software_mode).

        Counts key-down edges only. The button reports key DOWN (1) and key UP (0)
        ~100-200ms apart and carries no mute state of its own, so the caller
        owns the state and toggles per press.

        Also captures unsolicited NOTIFY_STATUS mode/wake notifications so the GUI
        can restore Software Mode & RGB immediately after hardware sleep wake-up.

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
            elif len(data) > 3 and tuple(data[:3]) == NOTIFY_STATUS and data[3] in (OP_OPERATING_MODE, OP_CONNECTED_SUBDEVICES):
                self._pending_hw_reset = True
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

    def get_mic_volume_pw(self):
        """Reads the current microphone gain percentage (0-100) from PipeWire."""
        for attempt in range(2):
            src = self._find_pw_source()
            if src is None:
                return None
            try:
                r = subprocess.run(["pactl", "get-source-volume", src],
                                   capture_output=True, text=True, timeout=5)
                if r.returncode != 0:
                    self._pw_source = None
                    continue
                m = re.search(r'(\d+)%', r.stdout)
                if m:
                    return max(0, min(100, int(m.group(1))))
            except Exception:
                break
        return None

    def set_mic_volume_pw(self, value):
        """Sets the microphone gain percentage (0-100) at the PipeWire layer."""
        pct = max(0, min(100, int(value)))
        for attempt in range(2):
            src = self._find_pw_source()
            if src is None:
                return False
            try:
                r = subprocess.run(["pactl", "set-source-volume", src, f"{pct}%"],
                                   capture_output=True, text=True, timeout=5)
                if r.returncode == 0:
                    return True
                self._pw_source = None
            except Exception:
                break
        return False

    # ─── Mute feedback tone ──────────────────────────────────────────

    def _find_pw_sink(self, refresh=False):
        """Finds the Corsair Virtuoso hardware PipeWire sink for zero-hiss HW volume control."""
        if not refresh and self._pw_sink:
            return self._pw_sink
        hw_sink = self.eq.find_hw_sink()
        self._pw_sink = hw_sink or EQ_SINK_NAME
        return self._pw_sink

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
            if not self._send_v2w(EP_HEADSET, CMD_GET_PROP, [OP_CHARGE]):
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
            self._send_v2w(EP_HEADSET, CMD_GET_PROP, [OP_BATTERY])
            
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
                # Sink name may have changed after reconnect or EQ toggle; invalidate and retry once
                sink = self._find_pw_sink(refresh=True)
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
        sink = self._find_pw_sink(refresh=True)
        if sink is not None:
            try:
                r = subprocess.run(
                    ["pactl", "get-sink-volume", sink],
                    capture_output=True, text=True, timeout=5
                )
                if r.returncode != 0:
                    self._pw_sink = None
                    sink = self._find_pw_sink(refresh=True)
                    if sink is not None:
                        r = subprocess.run(
                            ["pactl", "get-sink-volume", sink],
                            capture_output=True, text=True, timeout=5
                        )
                if r.returncode == 0:
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

    # ─── Bragi Property Helpers ──────────────────────────────────────

    def _get_bragi_property(self, endpoint, prop_id, retries=2):
        """Reads a Bragi property via CMD_GET_PROP (0x02). Returns raw 64-byte response or None.

        Reply frame layout: [0x01, ep_idx (0=RX,1=HS), 0x02 (cmd echo), err (0=OK,5=unsupported), payload...]
        """
        if not self.is_connected or not self.device:
            return None
        expected_ep = 0 if endpoint == EP_RECEIVER else 1
        for _ in range(retries):
            self._flush_buffer()
            if not self._send_v2w(endpoint, CMD_GET_PROP, [prop_id]):
                return None
            for _ in range(16):
                time.sleep(0.02)
                try:
                    res = self.device.read(64)
                except Exception:
                    return None
                if res and len(res) > 4 and res[0] == 0x01 and res[1] == expected_ep and res[2] == CMD_GET_PROP:
                    if res[3] == 0:
                        return res
                    return None
        return None

    def _set_bragi_property(self, endpoint, prop_id, data_bytes):
        """Writes a Bragi property via CMD_SET_PROP (0x01) and drains its RF ACK."""
        if not self.is_connected or not self.device:
            return False
        if not self._send_v2w(endpoint, CMD_SET_PROP, [prop_id, 0x00] + list(data_bytes)):
            return False
        expected_ep = 0 if endpoint == EP_RECEIVER else 1
        for _ in range(6):
            time.sleep(0.015)
            try:
                res = self.device.read(64)
            except Exception:
                break
            if not res:
                continue
            if len(res) > 3 and tuple(res[:3]) == MIC_BUTTON_REPORT and res[3]:
                self._pending_press = True
                continue
            if len(res) > 3 and res[0] == 0x01 and res[1] == expected_ep and res[2] == CMD_SET_PROP:
                return res[3] == 0
        return True

    # ─── Sleep Timer (Hardware Auto-Shutdown) ───────────────────────

    def set_sleep_timer(self, minutes):
        """Sets the configured sleep timeout in minutes directly in headset hardware.

        Range: 0 (disabled), or 1-99 minutes.
        Synchronizes Bragi properties 0x0d (AutomaticSleepEnabled) and
        0x0e (AutomaticSleepTimeoutNormal) via CMD_SET_PROP (0x01).
        """
        try:
            mins = int(minutes)
        except (ValueError, TypeError):
            return False

        mins = max(0, min(99, mins))
        self._sleep_timer_minutes = mins

        if self.is_connected and self._ensure_handshake():
            try:
                enabled = 1 if mins > 0 else 0
                self._set_bragi_property(EP_HEADSET, OP_SLEEP_ENABLED, [enabled])
                if mins > 0:
                    ms = mins * 60 * 1000
                    self._set_bragi_property(
                        EP_HEADSET, OP_SLEEP_TIMEOUT_NORMAL,
                        list(ms.to_bytes(4, byteorder="little"))
                    )
            except Exception:
                pass

        return True

    def get_sleep_timer(self):
        """Returns the current configured sleep timer in minutes (default 10)."""
        return getattr(self, "_sleep_timer_minutes", 10)

    def get_hw_sleep_timer(self):
        """Queries the hardware sleep timeout from Bragi properties 0x0d and 0x0e.
        Returns a dict with 'enabled', 'timeout_ms', and 'timeout_min' or None if unqueried.
        """
        if not self.is_connected or not self._ensure_handshake():
            return None
        info = {}
        try:
            res_en = self._get_bragi_property(EP_HEADSET, OP_SLEEP_ENABLED)
            if res_en and len(res_en) > 4:
                info["enabled"] = bool(res_en[4])
            res_to = self._get_bragi_property(EP_HEADSET, OP_SLEEP_TIMEOUT_NORMAL)
            if res_to and len(res_to) > 7:
                ms = int.from_bytes(res_to[4:8], byteorder="little")
                info["timeout_ms"] = ms
                info["timeout_min"] = ms // 60000
        except Exception:
            pass
        return info if info else None

    # ─── Hardware Power Saving & Device Info ────────────────────────

    def set_power_saving_mode(self, enabled):
        """Enables or disables hardware Power Saving mode (Bragi property 0x0b)."""
        if not self.is_connected or not self._ensure_handshake():
            return False
        return self._set_bragi_property(EP_HEADSET, OP_POWER_SAVING, [1 if enabled else 0])

    def get_power_saving_mode(self):
        """Queries hardware Power Saving mode (Bragi property 0x0b)."""
        if not self.is_connected or not self._ensure_handshake():
            return None
        res = self._get_bragi_property(EP_HEADSET, OP_POWER_SAVING)
        if res and len(res) > 4:
            return bool(res[4])
        return None

    @staticmethod
    def _format_fw_version(res):
        if not res or len(res) < 8:
            return None
        major = res[4]
        minor = res[5]
        build = res[6] | (res[7] << 8)
        return f"v{major}.{minor}.{build}"

    def get_device_hw_info(self):
        """Queries full hardware telemetry via Bragi GET_PROPERTY (0x02)."""
        info = {
            "model": self.get_device_name(),
            "mode": self.connection_mode,
            "product_id": hex(self.product_id) if self.product_id else "Unknown",
        }
        if not self.is_connected:
            return info

        try:
            rx_fw = self._get_bragi_property(EP_RECEIVER, OP_FIRMWARE_VERSION)
            if rx_fw:
                info["dongle_fw"] = self._format_fw_version(rx_fw)
            rx_enc = self._get_bragi_property(EP_RECEIVER, OP_ENCRYPTION_STATUS)
            if rx_enc and len(rx_enc) > 4:
                info["rf_encryption"] = bool(rx_enc[4])
        except Exception:
            pass

        if "Wired" not in self.connection_mode and self._ensure_handshake():
            try:
                hs_fw = self._get_bragi_property(EP_HEADSET, OP_FIRMWARE_VERSION)
                if hs_fw:
                    info["headset_fw"] = self._format_fw_version(hs_fw)
                hs_bl = self._get_bragi_property(EP_HEADSET, OP_BOOTLOADER_VERSION)
                if hs_bl:
                    info["bootloader_fw"] = self._format_fw_version(hs_bl)
                poll = self._get_bragi_property(EP_HEADSET, OP_POLLING_RATE)
                if poll and len(poll) > 4 and poll[4] > 0:
                    hz_map = {1: 125, 2: 250, 3: 500, 4: 1000, 5: 2000}
                    info["polling_rate_hz"] = hz_map.get(poll[4], poll[4])
                ps = self._get_bragi_property(EP_HEADSET, OP_POWER_SAVING)
                if ps and len(ps) > 4:
                    info["power_saving"] = bool(ps[4])
                volt = self._get_bragi_property(EP_HEADSET, OP_BATTERY_VOLTAGE)
                if volt and len(volt) > 5:
                    mv = volt[4] | (volt[5] << 8)
                    if 2500 <= mv <= 5000:
                        info["battery_mv"] = mv
                hw_sleep = self.get_hw_sleep_timer()
                if hw_sleep:
                    info["hw_sleep"] = hw_sleep
            except Exception:
                pass
        return info

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
        self.eq.eq_enabled = bool(enabled)
        if gains is not None:
            self.eq.current_gains = [float(g) for g in gains]
        res = self.eq._reconcile_dsp()
        self._pw_sink = None
        return res

    def enable_surround(self, enabled):
        """Enables or disables PipeWire 7.1 Virtual Surround."""
        res = self.eq.set_surround(enabled)
        self._pw_sink = None
        return res

    def set_eq_gains(self, gains):
        """Sets the 10-band equalizer gains in real-time."""
        res = self.eq.set_gains(gains)
        self._pw_sink = None
        return res

    def route_eq_streams(self):
        """Routes active streams playing to the HW sink through the EQ sink."""
        self.eq.route_streams_to_eq()

    def play_surround_test(self):
        """Plays an 8-channel 7.1 surround test sweep."""
        return self.eq.play_surround_test()

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
        print("  python3 virtuoso_control.py rgb <r> <g> <b> [brightness]")
        print("  python3 virtuoso_control.py brightness [0-100|status]")
        print("  python3 virtuoso_control.py sidetone [on|off|0-100]")
        print("  python3 virtuoso_control.py sidetone-v2w [0-100]")
        print("  python3 virtuoso_control.py volume [0-100|status]")
        print("  python3 virtuoso_control.py mic [mute|unmute|status]   (PipeWire)")
        print("  python3 virtuoso_control.py mic-volume [0-100|status]   (PipeWire)")
        print("  python3 virtuoso_control.py battery")
        print("  python3 virtuoso_control.py sleep [minutes|off|status]")
        print("  python3 virtuoso_control.py power-save [on|off|status]")
        print("  python3 virtuoso_control.py eq [enable <preset>|disable|status|set <gains...>|list|export <preset>]")
        print("  python3 virtuoso_control.py surround [on|off|status|test]")
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

    elif category == "brightness":
        val = sys.argv[2].lower() if len(sys.argv) > 2 else "status"
        if ctrl.connect():
            if val == "status":
                b = ctrl.get_hw_brightness()
                print(f"Hardware Brightness: {b}%" if b is not None else "✗ Could not read hardware brightness.")
            else:
                if ctrl.set_hw_brightness(int(val)):
                    print(f"✓ Hardware Brightness: {val}%")
                else:
                    print("✗ Error adjusting hardware brightness.")
            ctrl.disconnect()
        else:
            print("Error: Virtuoso not found.")

    elif category == "mic-volume":
        val = sys.argv[2].lower() if len(sys.argv) > 2 else "status"
        if val == "status":
            v = ctrl.get_mic_volume_pw()
            print(f"Mic Volume: {v}%" if v is not None else "✗ Could not read mic volume.")
        else:
            if ctrl.set_mic_volume_pw(int(val)):
                print(f"✓ Mic Volume: {val}%")
            else:
                print("✗ Error adjusting mic volume.")

    elif category == "surround":
        val = sys.argv[2].lower() if len(sys.argv) > 2 else "status"
        if val == "status":
            res = subprocess.run(["pactl", "list", "sinks", "short"], capture_output=True, text=True).stdout
            is_active = EQ_SINK_NAME in res
            print(f"DSP Sink Active: {is_active}")
        elif val == "test":
            res = subprocess.run(["pactl", "list", "sinks", "short"], capture_output=True, text=True).stdout
            ctrl.eq.is_enabled = EQ_SINK_NAME in res
            if ctrl.play_surround_test():
                print("✓ Playing 7.1 8-channel surround test sweep (FL->FC->FR->SR->RR->RL->SL->LFE)...")
                time.sleep(4.2)
            else:
                print("✗ Failed to play 7.1 surround test.")
        elif val in ("on", "off"):
            if ctrl.enable_surround(val == "on"):
                print(f"✓ Virtual 7.1 Surround: {val.upper()}")
            else:
                print("✗ Failed to toggle Virtual 7.1 Surround.")
        else:
            print("Usage: surround [on|off|status|test]")

    elif category == "power-save":
        val = sys.argv[2].lower() if len(sys.argv) > 2 else "status"
        if ctrl.connect():
            if val == "status":
                ps = ctrl.get_power_saving_mode()
                print(f"Power Saving Mode: {ps}" if ps is not None else "✗ Could not read power saving mode.")
            elif val in ("on", "off"):
                if ctrl.set_power_saving_mode(val == "on"):
                    print(f"✓ Power Saving Mode: {val.upper()}")
                else:
                    print("✗ Error setting power saving mode.")
            ctrl.disconnect()
        else:
            print("Error: Virtuoso not found.")

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
            connected = ctrl.connect()
            ctrl.set_sleep_timer(0)
            if connected:
                ctrl.disconnect()
            print("✓ Hardware Sleep timer disabled (0 min).")
        else:
            try:
                mins = int(val)
                connected = ctrl.connect()
                ctrl.set_sleep_timer(mins)
                if connected:
                    ctrl.disconnect()
                print(f"✓ Hardware Sleep timer set to {mins} min.")
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
            hw = ctrl.get_device_hw_info()
            print(f"Device: {hw.get('model')}")
            print(f"Mode: {hw.get('mode')}")
            print(f"Product ID: {hw.get('product_id')}")
            if "headset_fw" in hw:
                print(f"Headset Firmware: {hw['headset_fw']}")
            if "dongle_fw" in hw:
                print(f"Dongle Firmware: {hw['dongle_fw']}")
            if "bootloader_fw" in hw:
                print(f"Bootloader: {hw['bootloader_fw']}")
            if "polling_rate_hz" in hw:
                print(f"Polling Rate: {hw['polling_rate_hz']} Hz")
            if "rf_encryption" in hw:
                print(f"RF Encryption: {'Enabled' if hw['rf_encryption'] else 'Disabled'}")
            if "power_saving" in hw:
                print(f"Power Saving Mode: {'Enabled' if hw['power_saving'] else 'Disabled'}")
            if "battery_mv" in hw:
                print(f"Battery Voltage: {hw['battery_mv']} mV")
            print(f"Battery: {ctrl.get_battery()}")
            if "hw_sleep" in hw:
                print(f"Hardware Sleep: {hw['hw_sleep']}")
            else:
                print(f"Sleep Timer: {ctrl.get_sleep_timer()} min")
            print(f"Audio Active: {ctrl.is_audio_active()}")
            ctrl.disconnect()
        else:
            print("Error: Virtuoso not found.")
