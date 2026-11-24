"""PipeWire DSP audio engine (10-band EQ & binaural 7.1 surround) and session helpers."""
import math
import os
import re
import shutil
import struct
import subprocess
import threading
import time
import wave

from virtuoso_protocol import EQ_FREQUENCIES, EQ_SINK_NAME


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
    """Manages a native 10-band PipeWire biquad filter chain equalizer and 7.1 surround DSP.

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
        self.hrir_path = os.path.join(self.config_dir, "virtuoso-hrir-7.1.wav")
        self._dsp_log_path = "/tmp/virtuoso-dsp.log"
        self._lock = threading.Lock()
        self.process = None
        self.is_enabled = False
        self.eq_enabled = False
        self.surround_enabled = False
        self.current_gains = [0.0] * 10

    @staticmethod
    def _get_default_sink():
        try:
            r = subprocess.run(["pactl", "get-default-sink"],
                               capture_output=True, text=True, timeout=2)
            if r.returncode == 0:
                return r.stdout.strip()
        except Exception:
            pass
        return None

    @staticmethod
    def _get_sink_info(sink_name):
        """Returns (sink_id, channels_str) e.g. ('72', '8ch') if sink exists, else (None, None)."""
        if not sink_name:
            return None, None
        try:
            env = dict(os.environ, LC_ALL="C")
            r = subprocess.run(["pactl", "list", "sinks", "short"],
                               capture_output=True, text=True, timeout=2, env=env)
            for line in r.stdout.splitlines():
                parts = line.strip().split("\t")
                if len(parts) >= 2 and parts[1] == sink_name:
                    m = re.search(r'\b(\d+ch)\b', line)
                    ch_str = m.group(1) if m else None
                    return parts[0], ch_str
        except Exception:
            pass
        return None, None

    @classmethod
    def _get_sink_id(cls, sink_name):
        sink_id, _ = cls._get_sink_info(sink_name)
        return sink_id

    def _wait_sink_gone(self, sink_name, timeout=1.5):
        """Waits until sink_name completely disappears from PipeWire."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            sink_id, _ = self._get_sink_info(sink_name)
            if sink_id is None:
                return True
            time.sleep(0.04)
        sink_id, _ = self._get_sink_info(sink_name)
        return sink_id is None

    def _wait_sink_ready(self, sink_name, expected_channels, timeout=2.0):
        """Waits until sink_name appears with the expected channel count and live process."""
        expected_str = f"{int(expected_channels)}ch"
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process is not None and self.process.poll() is not None:
                return False
            sink_id, ch_str = self._get_sink_info(sink_name)
            if sink_id is not None and (ch_str is None or ch_str == expected_str):
                return True
            time.sleep(0.04)
        return False

    def ensure_hrir_file(self):
        """Generates a 14-channel 48kHz binaural 7.1 HRIR WAV for PipeWire HeSuVi convolver."""
        os.makedirs(self.config_dir, exist_ok=True)
        if os.path.exists(self.hrir_path) and os.path.getsize(self.hrir_path) > 1024:
            return self.hrir_path

        rate = 48000
        n_frames = 256

        def _make_ir(delay_ms, gain, lp_alpha=0.0, rear_dip=False, refl_ms=None, refl_gain=0.0):
            buf = [0.0] * n_frames
            # Fractional delay linear interpolation
            d_samp = delay_ms * rate / 1000.0
            i0 = max(0, min(n_frames - 2, int(math.floor(d_samp))))
            frac = d_samp - i0
            buf[i0] += gain * (1.0 - frac)
            buf[i0 + 1] += gain * frac

            if rear_dip:
                # Pinna spectral notch (~7 kHz comb attenuation) + subtle diffuse tail for rear localization
                n_samp = (delay_ms + 0.075) * rate / 1000.0
                ni = max(0, min(n_frames - 2, int(math.floor(n_samp))))
                nfrac = n_samp - ni
                buf[ni] -= gain * 0.38 * (1.0 - nfrac)
                buf[ni + 1] -= gain * 0.38 * nfrac

            if refl_ms is not None and refl_gain != 0.0:
                r_samp = (delay_ms + refl_ms) * rate / 1000.0
                ri = max(0, min(n_frames - 2, int(math.floor(r_samp))))
                rfrac = r_samp - ri
                buf[ri] += refl_gain * (1.0 - rfrac)
                buf[ri + 1] += refl_gain * rfrac

            if lp_alpha > 0.0:
                y = 0.0
                for idx in range(n_frames):
                    y = (1.0 - lp_alpha) * buf[idx] + lp_alpha * y
                    buf[idx] = y
            return buf

        # HeSuVi 14-channel order (see /usr/share/pipewire/filter-chain/sink-virtual-surround-7.1-hesuvi.conf):
        # 0:FL->L, 1:FL->R, 2:SL->L, 3:SL->R, 4:RL->L, 5:RL->R, 6:FC->L,
        # 7:FR->R, 8:FR->L, 9:SR->R, 10:SR->L, 11:RR->R, 12:RR->L, 13:FC->R
        fl_l = _make_ir(0.05, 0.56, lp_alpha=0.0,  rear_dip=False, refl_ms=1.6, refl_gain=0.06)
        fl_r = _make_ir(0.32, 0.28, lp_alpha=0.45, rear_dip=False, refl_ms=1.9, refl_gain=0.05)
        sl_l = _make_ir(0.02, 0.62, lp_alpha=0.0,  rear_dip=False, refl_ms=2.1, refl_gain=0.07)
        sl_r = _make_ir(0.68, 0.14, lp_alpha=0.72, rear_dip=False, refl_ms=2.4, refl_gain=0.04)
        rl_l = _make_ir(0.10, 0.50, lp_alpha=0.22, rear_dip=True,  refl_ms=2.7, refl_gain=0.09)
        rl_r = _make_ir(0.46, 0.20, lp_alpha=0.62, rear_dip=True,  refl_ms=2.9, refl_gain=0.06)
        fc_l = _make_ir(0.08, 0.45, lp_alpha=0.10, rear_dip=False, refl_ms=1.4, refl_gain=0.04)
        fc_r = _make_ir(0.08, 0.45, lp_alpha=0.10, rear_dip=False, refl_ms=1.4, refl_gain=0.04)

        channels = [
            fl_l, fl_r,  # 0, 1: FL
            sl_l, sl_r,  # 2, 3: SL
            rl_l, rl_r,  # 4, 5: RL
            fc_l,        # 6: FC->L
            fl_l, fl_r,  # 7 (FR->R), 8 (FR->L)
            sl_l, sl_r,  # 9 (SR->R), 10 (SR->L)
            rl_l, rl_r,  # 11 (RR->R), 12 (RR->L)
            fc_r,        # 13: FC->R
        ]

        frames = bytearray()
        for t in range(n_frames):
            for ch in range(14):
                s = max(-32767, min(32767, int(round(channels[ch][t] * 32767.0))))
                frames += struct.pack("<h", s)

        with wave.open(self.hrir_path, "wb") as w:
            w.setnchannels(14)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(bytes(frames))
        return self.hrir_path

    def play_surround_test(self):
        """Generates and plays an 8-channel 7.1 surround speaker sweep (FL->FC->FR->SR->RR->RL->SL->LFE)."""
        test_path = "/tmp/virtuoso-71-test.wav"
        rate = 48000
        # 8ch order: [FL(0), FR(1), FC(2), LFE(3), RL(4), RR(5), SL(6), SR(7)]
        steps = [
            (0, 440.0),   # Front Left
            (2, 523.25),  # Front Center
            (1, 587.33),  # Front Right
            (7, 659.25),  # Side Right
            (5, 493.88),  # Rear Right
            (4, 392.00),  # Rear Left
            (6, 349.23),  # Side Left
            (3, 75.00),   # LFE / Subwoofer (+ harmonic for headphone clarity)
        ]
        tone_n = int(rate * 0.42)
        gap_n = int(rate * 0.08)
        fade_n = int(rate * 0.02)
        frames = bytearray()
        for ch_idx, freq in steps:
            for t in range(tone_n):
                env = min(1.0, t / fade_n, (tone_n - t) / fade_n)
                sig = math.sin(2.0 * math.pi * freq * t / rate)
                if ch_idx == 3:
                    # Enrich sub-bass LFE with 2nd harmonic so it punches clearly on headset drivers
                    sig = 0.72 * sig + 0.28 * math.sin(2.0 * math.pi * (freq * 2.0) * t / rate)
                sample = int(0.40 * env * 32767 * sig)
                row = [0] * 8
                row[ch_idx] = max(-32767, min(32767, sample))
                frames += struct.pack("<8h", *row)
            frames += b"\x00" * (gap_n * 8 * 2)

        try:
            with wave.open(test_path, "wb") as w:
                w.setnchannels(8)
                w.setsampwidth(2)
                w.setframerate(rate)
                w.writeframes(bytes(frames))

            eq_id, _ = self._get_sink_info(EQ_SINK_NAME)
            use_eq_sink = eq_id is not None

            if shutil.which("pw-play"):
                cmd = ["pw-play", "--channel-map=FL,FR,FC,LFE,RL,RR,SL,SR"]
                if use_eq_sink:
                    cmd.append(f"--target={EQ_SINK_NAME}")
                cmd.append(test_path)
            else:
                cmd = [
                    "paplay",
                    "--channel-map=front-left,front-right,front-center,lfe,rear-left,rear-right,side-left,side-right",
                    "--no-remix",
                ]
                if use_eq_sink:
                    cmd.append(f"--device={EQ_SINK_NAME}")
                cmd.append(test_path)

            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except Exception:
            return False

    @staticmethod
    def _get_sink_volume(sink_name):
        if not sink_name:
            return None
        try:
            r = subprocess.run(["pactl", "get-sink-volume", sink_name],
                               capture_output=True, text=True, timeout=2)
            if r.returncode == 0:
                m = re.search(r'(\d+)%', r.stdout)
                if m:
                    return max(0, min(100, int(m.group(1))))
        except Exception:
            pass
        return None

    @staticmethod
    def _set_sink_volume(sink_name, pct):
        if not sink_name or pct is None:
            return False
        try:
            r = subprocess.run(["pactl", "set-sink-volume", sink_name, f"{int(pct)}%"],
                               capture_output=True, text=True, timeout=2)
            return r.returncode == 0
        except Exception:
            return False

    def find_hw_sink(self):
        if self.hw_sink_name:
            if self._get_sink_id(self.hw_sink_name) is not None:
                return self.hw_sink_name
            self.hw_sink_name = None
        try:
            env = dict(os.environ, LC_ALL="C")
            res = subprocess.run(["pactl", "list", "sinks", "short"],
                                 capture_output=True, text=True, timeout=3, env=env)
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
        active_gains = gains if self.eq_enabled else [0.0] * len(EQ_FREQUENCIES)

        nodes_conf = []
        links_conf = []
        io_lines = ""

        if not self.surround_enabled:
            for i, (freq, gain) in enumerate(zip(EQ_FREQUENCIES, active_gains)):
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

            cap_desc = "Corsair Virtuoso Equalizer"
            cap_audio = """                audio.channels = 2
                audio.position = [ FL FR ]"""
            top_audio = """            audio.channels = 2
            audio.position = [ FL FR ]"""
        else:
            # Real 8-channel 7.1 Virtual Surround via PipeWire binaural HRIR convolver + 10-band EQ
            hrir = self.ensure_hrir_file()
            for spk in ("FL", "FR", "FC", "RL", "RR", "SL", "SR", "LFE"):
                nodes_conf.append(f'                    {{ type = builtin label = copy name = copy{spk} }}')

            convolver_map = [
                ("convFL_L", 0, "copyFL"),  ("convFL_R", 1, "copyFL"),
                ("convSL_L", 2, "copySL"),  ("convSL_R", 3, "copySL"),
                ("convRL_L", 4, "copyRL"),  ("convRL_R", 5, "copyRL"),
                ("convFC_L", 6, "copyFC"),  ("convFC_R", 13, "copyFC"),
                ("convFR_R", 7, "copyFR"),  ("convFR_L", 8, "copyFR"),
                ("convSR_R", 9, "copySR"),  ("convSR_L", 10, "copySR"),
                ("convRR_R", 11, "copyRR"), ("convRR_L", 12, "copyRR"),
                ("convLFE_L", 6, "copyLFE"), ("convLFE_R", 13, "copyLFE"),
            ]
            for cname, ch_idx, src_node in convolver_map:
                nodes_conf.append(f'                    {{ type = builtin label = convolver name = {cname} config = {{ filename = "{hrir}" channel = {ch_idx} }} }}')
                links_conf.append(f'                    {{ output = "{src_node}:Out" input = "{cname}:In" }}')

            nodes_conf.append('                    { type = builtin label = mixer name = mixL }')
            nodes_conf.append('                    { type = builtin label = mixer name = mixR }')
            mix_inputs = [
                ("convFL_L", "mixL", 1), ("convFL_R", "mixR", 1),
                ("convSL_L", "mixL", 2), ("convSL_R", "mixR", 2),
                ("convRL_L", "mixL", 3), ("convRL_R", "mixR", 3),
                ("convFC_L", "mixL", 4), ("convFC_R", "mixR", 4),
                ("convFR_R", "mixR", 5), ("convFR_L", "mixL", 5),
                ("convSR_R", "mixR", 6), ("convSR_L", "mixL", 6),
                ("convRR_R", "mixR", 7), ("convRR_L", "mixL", 7),
                ("convLFE_R", "mixR", 8), ("convLFE_L", "mixL", 8),
            ]
            for cname, mix_name, pin in mix_inputs:
                links_conf.append(f'                    {{ output = "{cname}:Out" input = "{mix_name}:In {pin}" }}')

            # Chain 10-band EQ after stereo binaural downmix
            for ch in ("L", "R"):
                for i, (freq, gain) in enumerate(zip(EQ_FREQUENCIES, active_gains)):
                    idx = i + 1
                    node_name = f"eq_band_{idx}_{ch}"
                    if i == 0:
                        label, q_val = "bq_lowshelf", 1.0
                    elif i == len(EQ_FREQUENCIES) - 1:
                        label, q_val = "bq_highshelf", 1.0
                    else:
                        label, q_val = "bq_peaking", 1.414
                    nodes_conf.append(f'                    {{ type = builtin name = {node_name} label = {label} control = {{ "Freq" = {float(freq):.1f} "Q" = {q_val:.3f} "Gain" = {float(gain):.1f} }} }}')
                    if i == 0:
                        links_conf.append(f'                    {{ output = "mix{ch}:Out" input = "{node_name}:In" }}')
                    else:
                        links_conf.append(f'                    {{ output = "eq_band_{i}_{ch}:Out" input = "{node_name}:In" }}')

            io_lines = """                inputs  = [ "copyFL:In" "copyFR:In" "copyFC:In" "copyLFE:In" "copyRL:In" "copyRR:In" "copySL:In" "copySR:In" ]
                outputs = [ "eq_band_10_L:Out" "eq_band_10_R:Out" ]"""
            cap_desc = "Corsair Virtuoso 7.1 Surround"
            cap_audio = """                audio.channels = 8
                audio.position = [ FL FR FC LFE RL RR SL SR ]"""
            top_audio = ""

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
            node.description = "Corsair Virtuoso Audio DSP"
            media.name       = "Corsair Virtuoso Audio DSP"
            filter.graph = {{
                nodes = [
{nodes_str}
                ]
                links = [
{links_str}
                ]
{io_lines}
            }}
{top_audio}
            capture.props = {{
                node.name   = "effect_input.virtuoso_eq"
                media.class = Audio/Sink
                node.description = "{cap_desc}"
                audio.rate = 48000
                node.pause-on-idle = true
{cap_audio}
            }}
            playback.props = {{
                node.name   = "effect_output.virtuoso_eq"
                node.passive = true
                node.pause-on-idle = true
                audio.channels = 2
                audio.position = [ FL FR ]
                audio.rate = 48000
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

    def _list_sink_inputs_detailed(self):
        """Returns list of dicts with id, sink, node_name, media_name from pactl."""
        entries = []
        try:
            env = dict(os.environ, LC_ALL="C")
            res = subprocess.run(["pactl", "list", "sink-inputs"],
                                 capture_output=True, text=True, timeout=2, env=env)
            cur = None
            for line in res.stdout.splitlines():
                if line.startswith("Sink Input #"):
                    if cur:
                        entries.append(cur)
                    cur = {"id": line.split("#", 1)[1].strip(), "sink": "", "node_name": "", "media_name": ""}
                elif cur:
                    s = line.strip()
                    if s.startswith("Sink:"):
                        cur["sink"] = s.split(":", 1)[1].strip()
                    elif s.startswith("node.name ="):
                        cur["node_name"] = s.split("=", 1)[1].strip().strip('"')
                    elif s.startswith("media.name ="):
                        cur["media_name"] = s.split("=", 1)[1].strip().strip('"')
            if cur:
                entries.append(cur)
        except Exception:
            pass
        return entries

    def _move_streams_safely(self, source_sink_id, target_sink):
        """Moves playback streams from source_sink_id (or any if None) to target_sink, never looping our DSP node."""
        if not target_sink:
            return
        try:
            for item in self._list_sink_inputs_detailed():
                if source_sink_id is not None and item["sink"] != source_sink_id:
                    continue
                if item["node_name"] == "effect_output.virtuoso_eq":
                    continue
                if "corsair virtuoso audio dsp" in item["media_name"].lower():
                    continue
                subprocess.run(["pactl", "move-sink-input", item["id"], target_sink],
                               capture_output=True, timeout=1)
        except Exception:
            pass

    def route_streams_to_eq(self):
        """Moves any application streams currently playing directly to hw_sink into EQ_SINK_NAME."""
        if not self.is_enabled:
            return
        if self._get_sink_id(EQ_SINK_NAME) is None:
            return
        hw_sink = self.find_hw_sink()
        hw_id = self._get_sink_id(hw_sink)
        if hw_id:
            self._move_streams_safely(hw_id, EQ_SINK_NAME)

    def _reconcile_dsp(self):
        """Idempotent, lock-protected DSP state machine covering all 4 states:
        (EQ=Off, 7.1=Off), (EQ=On, 7.1=Off), (EQ=Off, 7.1=On), (EQ=On, 7.1=On).
        """
        with self._lock:
            hw_sink = self.find_hw_sink()

            # Park active non-DSP streams safely on hw_sink before tearing down old DSP
            old_eq_id = self._get_sink_id(EQ_SINK_NAME)
            if old_eq_id and hw_sink:
                self._move_streams_safely(old_eq_id, hw_sink)

            if not self.eq_enabled and not self.surround_enabled:
                if hw_sink:
                    try:
                        subprocess.run(["pactl", "set-default-sink", hw_sink],
                                       capture_output=True, timeout=1)
                    except Exception:
                        pass
                self.stop_process_only()
                self.is_enabled = False
                return True

            # Synchronous teardown: never spawn new PipeWire DSP until old sink is gone
            self.stop_process_only()
            self.generate_config(self.current_gains)

            try:
                with open(self._dsp_log_path, "wb") as log_file:
                    self.process = subprocess.Popen(
                        ["pipewire", "-c", self.config_path],
                        stdout=subprocess.DEVNULL,
                        stderr=log_file,
                        start_new_session=True
                    )
            except Exception:
                self.process = None
                self.is_enabled = False
                return False

            expected_ch = 8 if self.surround_enabled else 2
            sink_ready = self._wait_sink_ready(EQ_SINK_NAME, expected_ch, timeout=2.0)

            # ZERO-HISS VOLUME RULE:
            # Always keep virtual DSP sink at 100% (0.00 dB unity gain) and NEVER boost
            # hw_sink to 100%. The USB DAC hardware volume remains under HW_VOLUME_CTRL.
            if sink_ready:
                self._set_sink_volume(EQ_SINK_NAME, 100)

                if hw_sink:
                    try:
                        subprocess.run(["pactl", "set-default-sink", hw_sink],
                                       capture_output=True, timeout=1)
                    except Exception:
                        pass
                    hw_id = self._get_sink_id(hw_sink)
                    if hw_id:
                        self._move_streams_safely(hw_id, EQ_SINK_NAME)

            self.is_enabled = bool(sink_ready)
            return self.is_enabled

    def start(self, gains=None):
        if gains is not None:
            self.current_gains = [float(g) for g in gains]
        return self._reconcile_dsp()

    def stop_process_only(self):
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=1.0)
            except Exception:
                try:
                    self.process.kill()
                    self.process.wait(timeout=0.5)
                except Exception:
                    pass
            self.process = None

        try:
            subprocess.run(["pkill", "-f", "virtuoso-filter-chain.conf"],
                           capture_output=True, timeout=1)
        except Exception:
            pass

        self._wait_sink_gone(EQ_SINK_NAME, timeout=1.5)

    def stop(self):
        self.eq_enabled = False
        self.surround_enabled = False
        return self._reconcile_dsp()

    def set_gains(self, gains):
        self.current_gains = [float(g) for g in gains]
        if self.eq_enabled:
            return self._reconcile_dsp()
        return True

    def set_surround(self, enabled):
        self.surround_enabled = bool(enabled)
        return self._reconcile_dsp()
