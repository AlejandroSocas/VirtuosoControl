#!/usr/bin/env python3
"""
Virtuoso GUI — Interfaz gráfica para Corsair Virtuoso SE

Features:
- Conexión HID persistente (como iCUE)
- Control de LED del micrófono con keep-alive
- Sidetone (ALSA / V2W HID)
- Volumen (ALSA)
- Battery monitoring with notifications
- Quick actions from tray
- Persistence of preferences across sessions
- Automatic reconnection
"""
import sys
import os
import math
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QPushButton, QSlider, QLabel,
                             QSystemTrayIcon, QMenu, QCheckBox, QButtonGroup,
                             QColorDialog, QMessageBox, QToolButton)
from PyQt6.QtCore import (Qt, QTimer, QSettings, QSize, QThread,
                          QCoreApplication, QEvent)
from PyQt6.QtGui import QIcon, QAction, QColor, QPixmap, QPainter, QBrush, QPen
from PyQt6.QtNetwork import QLocalServer, QLocalSocket

from virtuoso_control import VirtuosoController, EQ_PRESETS, EQ_FREQUENCIES, BATTERY_THRESHOLDS
from virtuoso_i18n import _tr
from virtuoso_theme import (THEME, BATT_LEVEL, POWER_SAVING_BRIGHTNESS, c,
                            apply_theme, make_settings_icon, make_refresh_icon)
from virtuoso_widgets import (StatusDot, ColorSwatch, BatteryGauge,
                              ThemedComboBox, Card, SettingsDialog)


class VirtuosoGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.ctrl = VirtuosoController()
        self._hid_connected = False
        self._low_battery_notified_0 = False
        self._low_battery_notified = False
        self._mic_muted = False  # mirrors the headset's reported mute state
        self._quitting = False
        self._last_battery_percent = None
        self._last_charging = False
        self.batt_tray_icon = None  # optional standalone battery indicator
        self.batt_menu = None
        self._tray_mode = "virtuoso"

        # Absolute path to icon
        self.script_dir = os.path.dirname(os.path.realpath(__file__))
        self.icon_path = os.path.join(self.script_dir, "virtuoso_icon.png")

        self.init_ui()
        self._refresh_icons()
        self._load_settings()   # Load preferences (signals blocked)
        self._sync_mic_status()
        self.init_tray()        # Tray with quick actions

        # Height is locked to the assembled content rather than hardcoded, so
        # the window stays tight whichever language the labels are in.
        self.adjustSize()
        self.setFixedHeight(self.sizeHint().height())

        # Re-skin if the desktop switches between light and dark while running.
        try:
            QApplication.instance().styleHints().colorSchemeChanged.connect(
                self._on_color_scheme_changed)
        except AttributeError:
            pass

        # Timer: LED keep-alive (60s per official RequiredPingInterval)
        self.keep_alive_timer = QTimer()
        self.keep_alive_timer.timeout.connect(self.do_keep_alive)

        # Timer: automatic reconnection (every 5s)
        self.reconnect_timer = QTimer()
        self.reconnect_timer.timeout.connect(self.try_reconnect)

        # Timer: automatic battery check (every 5 min)
        self.battery_timer = QTimer()
        self.battery_timer.timeout.connect(self._auto_battery_check)
        self.battery_timer.start(300_000)

        # Timer: physical mic-mute button. Cheap — a non-blocking HID read
        # that returns immediately when nothing is pending.
        self.mic_button_timer = QTimer()
        self.mic_button_timer.timeout.connect(self._poll_mic_button)
        self.mic_button_timer.start(150)

        # Timer: low battery status LED blinking
        self._blink_state = False  # Alternate between True and False
        self.blink_timer = QTimer()
        self.blink_timer.timeout.connect(self._toggle_blink_led)

        # Timer: sync volume and mic gain from OS (every 1s)
        self.volume_sync_timer = QTimer()
        self.volume_sync_timer.timeout.connect(self._sync_volume_from_os)
        self.volume_sync_timer.start(1000)

        # Equalizer debounced apply timer (150ms)
        self.eq_apply_timer = QTimer(self)
        self.eq_apply_timer.setSingleShot(True)
        self.eq_apply_timer.timeout.connect(self._apply_current_eq)

        # Dynamic RGB effect timer (~15 FPS, active only for dynamic effects)
        self._effect_phase = 0.0
        self.rgb_effect_timer = QTimer(self)
        self.rgb_effect_timer.timeout.connect(self._tick_rgb_effect)

        # Voice prompt reminder timers (low battery & muted mic)
        self.low_batt_remind_timer = QTimer(self)
        self.low_batt_remind_timer.timeout.connect(self._on_low_batt_reminder)
        self.mic_mute_remind_timer = QTimer(self)
        self.mic_mute_remind_timer.timeout.connect(self._on_mic_mute_reminder)
        self._configure_reminder_timers()

        # Connect and apply saved preferences
        self._try_initial_connect()
        self._apply_saved_settings()

        # First battery read. The battery timer above only fires after 5
        # minutes, so without this the label sits at "--" until then or until
        # the user hits Refresh. Deferred so the window paints first — the
        # read blocks for up to ~1s.
        QTimer.singleShot(1200, self._initial_battery_check)

    # ─── Interface ───────────────────────────────────────────────────

    def open_settings(self):
        dlg = SettingsDialog(self, ctrl=self.ctrl)
        if dlg.exec():
            s = QSettings("AlejandroSocas", "VirtuosoControl")
            self._voice_prompts = s.value("voice_prompts", True, type=bool)
            self._configure_reminder_timers()
            self._setup_tray_icons()  # applies without a restart

    def _refresh_icons(self):
        """(Re)paints the header icons in the current theme's text colour."""
        refresh = make_refresh_icon(THEME["text"])
        settings = make_settings_icon(THEME["text"])
        for btn, icon in ((self.refresh_conn_btn, refresh),
                          (self.batt_btn, refresh),
                          (self.settings_btn, settings)):
            btn.setIcon(icon)
            btn.setIconSize(QSize(16, 16))

    def _on_color_scheme_changed(self, _scheme=None):
        """Re-skins when the desktop flips between light and dark.

        The stylesheet is rebuilt wholesale, but the handful of colours set
        inline (status text, mic state, battery percentage) live outside it and
        have to be recomputed from the new THEME by hand.
        """
        apply_theme(QApplication.instance())
        self._refresh_icons()
        self._update_status(self._hid_connected)
        self._sync_mic_status()
        self.batt_percent.setStyleSheet(
            f"color: {self.batt_gauge.level_color().name()};")
        for w in (self.status_dot, self.mic_state_dot, self.batt_gauge,
                  self.rgb_color_btn, self.mic_color_btn,
                  self.mic_mute_color_btn):
            w.update()

    # --- small builders shared by the cards -------------------------

    # Every card indents its controls past a label column of this width. It is
    # measured rather than hardcoded: at a fixed 54px the English labels fit
    # but "Micrófono" and "Silenciado" are cut off mid-word.
    ROW_LABELS = ("Logo", "Mic", "Effect", "Profiles", "Status", "Gain", "Muted", "Level", "Sleep", "Preset:")

    def _measure_row_label_width(self):
        fm = self.fontMetrics()
        return max(54, max(fm.horizontalAdvance(_tr(t)) for t in self.ROW_LABELS) + 8)

    def _row_label(self, text):
        lbl = QLabel(text)
        lbl.setObjectName("RowLabel")
        lbl.setFixedWidth(self._row_label_w)
        return lbl

    @staticmethod
    def _make_slider(value, on_change):
        sld = QSlider(Qt.Orientation.Horizontal)
        sld.setRange(0, 100)
        sld.setValue(value)
        sld.valueChanged.connect(on_change)
        return sld

    @staticmethod
    def _value_label(text):
        lbl = QLabel(text)
        lbl.setObjectName("Value")
        lbl.setFixedWidth(36)
        lbl.setAlignment(Qt.AlignmentFlag.AlignRight
                         | Qt.AlignmentFlag.AlignVCenter)
        return lbl

    def init_ui(self):
        self.setWindowTitle("Virtuoso Control")
        self.setWindowIcon(QIcon(self.icon_path))
        # Width is fixed so the cards keep their proportions; the height is
        # locked to the content in __init__ once every card has been built,
        # which keeps the window tight in both languages.
        self.setFixedWidth(740)
        self._row_label_w = self._measure_row_label_width()

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(14, 12, 14, 14)
        main_layout.setSpacing(10)

        main_layout.addLayout(self._build_header())

        # Two-column layout: Left (Battery, Lighting, Mic), Right (Sidetone, Volume, Equalizer)
        columns_layout = QHBoxLayout()
        columns_layout.setSpacing(12)
        columns_layout.setContentsMargins(0, 0, 0, 0)

        left_col = QVBoxLayout()
        left_col.setSpacing(10)
        left_col.addWidget(self._build_battery_card())
        left_col.addWidget(self._build_lighting_card())
        left_col.addWidget(self._build_mic_card())
        left_col.addStretch()

        right_col = QVBoxLayout()
        right_col.setSpacing(10)
        right_col.addWidget(self._build_sidetone_card())
        right_col.addWidget(self._build_volume_card())
        right_col.addWidget(self._build_equalizer_card())
        right_col.addStretch()

        columns_layout.addLayout(left_col, 1)
        columns_layout.addLayout(right_col, 1)

        main_layout.addLayout(columns_layout)

    def _build_header(self):
        row = QHBoxLayout()
        row.setSpacing(8)
        row.setContentsMargins(2, 0, 0, 2)

        self.status_dot = StatusDot()
        self.status_label = QLabel(_tr("Connecting..."))
        self.status_label.setObjectName("StatusText")

        self.refresh_conn_btn = QPushButton()
        self.refresh_conn_btn.setObjectName("IconBtn")
        self.refresh_conn_btn.setToolTip(_tr("Refresh"))
        self.refresh_conn_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.refresh_conn_btn.clicked.connect(self.force_reconnect)

        self.settings_btn = QPushButton()
        self.settings_btn.setObjectName("IconBtn")
        self.settings_btn.setToolTip(_tr("Settings"))
        self.settings_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.settings_btn.clicked.connect(self.open_settings)

        row.addWidget(self.status_dot)
        row.addWidget(self.status_label)
        row.addStretch()
        row.addWidget(self.refresh_conn_btn)
        row.addWidget(self.settings_btn)
        return row

    def _build_battery_card(self):
        card = Card(_tr("Battery"))

        self.batt_btn = QPushButton()
        self.batt_btn.setObjectName("IconBtn")
        self.batt_btn.setToolTip(_tr("Refresh"))
        self.batt_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.batt_btn.clicked.connect(self.check_battery)
        card.add_header_widget(self.batt_btn)

        self.batt_percent = QLabel("--")
        self.batt_percent.setObjectName("BattPercent")

        # Charging / discharging, i.e. the non-numeric half of the reading.
        # Also carries the whole string when there is no percentage to show
        # ("Not connected", "N/A (Wired)").
        self.batt_label = QLabel("")
        self.batt_label.setObjectName("Muted")
        self.batt_label.setContentsMargins(0, 0, 0, 4)

        read_row = QHBoxLayout()
        read_row.setSpacing(9)
        read_row.addWidget(self.batt_percent)
        read_row.addWidget(self.batt_label, 0, Qt.AlignmentFlag.AlignBottom)
        read_row.addStretch()

        self.batt_gauge = BatteryGauge()

        # Sleep timer row
        self.sleep_combo = ThemedComboBox()
        self.sleep_combo.addItem(_tr("Off"), 0)
        self.sleep_combo.addItem(f"1 min ({_tr('Debug')})", 1)
        self.sleep_combo.addItem("5 min", 5)
        self.sleep_combo.addItem(f"10 min ({_tr('Default')})", 10)
        self.sleep_combo.addItem("15 min", 15)
        self.sleep_combo.addItem("30 min", 30)
        self.sleep_combo.addItem("60 min", 60)
        self.sleep_combo.addItem("90 min", 90)
        self.sleep_combo.currentIndexChanged.connect(self._on_sleep_timer_changed)

        sleep_row = QHBoxLayout()
        sleep_row.setSpacing(10)
        sleep_row.addWidget(self._row_label(_tr("Sleep")))
        sleep_row.addWidget(self.sleep_combo, 1)

        card.body.setSpacing(7)
        card.body.addLayout(read_row)
        card.body.addWidget(self.batt_gauge)
        card.body.addSpacing(2)
        card.body.addLayout(sleep_row)
        return card

    def _build_lighting_card(self):
        card = Card(_tr("Lighting"))

        # Logo zone
        self.rgb_color_btn = ColorSwatch()
        self.rgb_color_btn.setToolTip(_tr("Pick Logo Color"))
        self.rgb_color_btn.clicked.connect(self.choose_color)
        self.rgb_slider = self._make_slider(100, self.change_rgb)
        self.rgb_label = self._value_label("100%")

        logo_row = QHBoxLayout()
        logo_row.setSpacing(10)
        logo_row.addWidget(self._row_label(_tr("Logo")))
        logo_row.addWidget(self.rgb_color_btn)
        logo_row.addWidget(self.rgb_slider, 1)
        logo_row.addWidget(self.rgb_label)

        # Mic zone
        self.mic_color_btn = ColorSwatch()
        self.mic_color_btn.setToolTip(_tr("Pick Microphone Color"))
        self.mic_color_btn.clicked.connect(self.choose_mic_color)
        self.mic_slider = self._make_slider(100, self.change_rgb)
        self.mic_label = self._value_label("100%")

        mic_row = QHBoxLayout()
        mic_row.setSpacing(10)
        mic_row.addWidget(self._row_label(_tr("Mic")))
        mic_row.addWidget(self.mic_color_btn)
        mic_row.addWidget(self.mic_slider, 1)
        mic_row.addWidget(self.mic_label)

        # Profiles: three load buttons plus one save menu, rather than the
        # six buttons this used to be.
        self.profile_btn_1 = QPushButton("1")
        self.profile_btn_2 = QPushButton("2")
        self.profile_btn_3 = QPushButton("3")
        profile_row = QHBoxLayout()
        profile_row.setSpacing(6)
        profile_row.addWidget(self._row_label(_tr("Profiles")))
        for i, btn in enumerate((self.profile_btn_1, self.profile_btn_2,
                                 self.profile_btn_3), start=1):
            btn.setObjectName("Profile")
            btn.setFixedWidth(42)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setToolTip(_tr(f"Load Profile {i}"))
            btn.clicked.connect(lambda _checked, n=i: self.load_profile(n))
            profile_row.addWidget(btn)
        profile_row.addStretch()

        self.save_profile_btn = QToolButton()
        self.save_profile_btn.setObjectName("SaveMenu")
        self.save_profile_btn.setText(f"{_tr('Save')}  ▾")
        self.save_profile_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.save_profile_btn.setToolTip(_tr("Save current lighting to a profile"))
        self.save_profile_btn.setPopupMode(
            QToolButton.ToolButtonPopupMode.InstantPopup)
        self.save_menu = QMenu(self)
        for i in (1, 2, 3):
            act = QAction(_tr(f"Save to Profile {i}"), self)
            act.triggered.connect(lambda _checked, n=i: self.save_profile(n))
            self.save_menu.addAction(act)
        self.save_profile_btn.setMenu(self.save_menu)
        profile_row.addWidget(self.save_profile_btn)

        # Quick colors row
        self.quick_lighting_combo = ThemedComboBox()
        self.quick_lighting_combo.addItem(_tr("Quick Colors"), None)
        self.quick_lighting_combo.addItem(f"🟡 {_tr('Corsair Yellow')}", "#FFCC00")
        self.quick_lighting_combo.addItem(f"⚪ {_tr('Pure White')}", "#FFFFFF")
        self.quick_lighting_combo.addItem(f"❄️ {_tr('Ice Blue')}", "#98F8FB")
        self.quick_lighting_combo.addItem(f"🔴 {_tr('Red Alert')}", "#FF0000")
        self.quick_lighting_combo.addItem(f"⚫ {_tr('Stealth (Off)')}", "#000000")
        self.quick_lighting_combo.currentIndexChanged.connect(self._on_quick_lighting_selected)

        quick_row = QHBoxLayout()
        quick_row.setSpacing(10)
        quick_row.addWidget(self._row_label(_tr("Presets")))
        quick_row.addWidget(self.quick_lighting_combo, 1)

        # Effect & Speed row
        self.rgb_effect_combo = ThemedComboBox()
        for label, key in ((_tr("Static"), "Static"),
                           (_tr("Rainbow"), "Rainbow"),
                           (_tr("Color Pulse"), "ColorPulse"),
                           (_tr("Color Shift"), "ColorShift"),
                           (_tr("Battery Reactive"), "Battery")):
            self.rgb_effect_combo.addItem(label, key)
        self.rgb_effect_combo.currentIndexChanged.connect(self._on_rgb_effect_changed)

        self.rgb_speed_combo = ThemedComboBox()
        for label, mult in ((_tr("Slow"), 0.5), (_tr("Medium"), 1.0), (_tr("Fast"), 2.0)):
            self.rgb_speed_combo.addItem(label, mult)
        self.rgb_speed_combo.setCurrentIndex(1)
        self.rgb_speed_combo.currentIndexChanged.connect(self._on_rgb_effect_changed)

        effect_row = QHBoxLayout()
        effect_row.setSpacing(8)
        effect_row.addWidget(self._row_label(_tr("Effect")))
        effect_row.addWidget(self.rgb_effect_combo, 2)
        effect_row.addWidget(self.rgb_speed_combo, 1)

        card.body.addLayout(logo_row)
        card.body.addLayout(mic_row)
        card.body.addLayout(effect_row)
        card.body.addLayout(quick_row)
        card.body.addLayout(profile_row)
        return card

    def _build_mic_card(self):
        card = Card(_tr("Microphone"))

        # Mute state was previously only visible in the tray menu; the physical
        # button is the only control, so the window should at least report it.
        self.mic_state_dot = StatusDot()
        self.mic_state_label = QLabel(_tr("Active"))
        state_row = QHBoxLayout()
        state_row.setSpacing(8)
        state_row.addWidget(self._row_label(_tr("Status")))
        state_row.addWidget(self.mic_state_dot)
        state_row.addWidget(self.mic_state_label)
        state_row.addStretch()

        # Mic gain/volume slider (PipeWire)
        self.mic_vol_slider = self._make_slider(100, self.change_mic_volume)
        self.mic_vol_label = self._value_label("100%")
        mic_vol_row = QHBoxLayout()
        mic_vol_row.setSpacing(10)
        mic_vol_row.addWidget(self._row_label(_tr("Gain")))
        mic_vol_row.addWidget(self.mic_vol_slider, 1)
        mic_vol_row.addWidget(self.mic_vol_label)

        # Colour the mic LED takes while muted (red on Windows, but yours).
        self.mic_mute_color_btn = ColorSwatch()
        self.mic_mute_color_btn.setToolTip(_tr("Pick Mute Color"))
        self.mic_mute_color_btn.clicked.connect(self.choose_mic_mute_color)
        mute_color_row = QHBoxLayout()
        mute_color_row.setSpacing(10)
        mute_color_row.addWidget(self._row_label(_tr("Muted")))
        mute_color_row.addWidget(self.mic_mute_color_btn)
        mute_color_row.addStretch()

        self.mic_tone_cb = QCheckBox(_tr("Mute feedback sound"))
        self.mic_tone_cb.setChecked(True)
        self.mic_tone_cb.toggled.connect(self._save_settings)

        card.body.addLayout(state_row)
        card.body.addLayout(mic_vol_row)
        card.body.addLayout(mute_color_row)
        card.body.addWidget(self.mic_tone_cb)
        return card

    def _build_sidetone_card(self):
        card = Card(_tr("Sidetone"))

        # Backend picker, drawn as a segmented control in the card header.
        # Checkable buttons rather than radios: same isChecked/setChecked/
        # toggled API, but stylable into a joined pair (a QRadioButton keeps
        # drawing its dot however hard QSS tries to collapse the indicator).
        self.side_method_group = QButtonGroup(self)
        self.side_method_group.setExclusive(True)
        self.side_alsa_radio = QPushButton("ALSA")
        self.side_alsa_radio.setObjectName("SegLeft")
        self.side_v2w_radio = QPushButton("V2W HID")
        self.side_v2w_radio.setObjectName("SegRight")
        for btn in (self.side_alsa_radio, self.side_v2w_radio):
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setToolTip(_tr("Method:"))
            self.side_method_group.addButton(btn)
        self.side_alsa_radio.setChecked(True)
        self.side_alsa_radio.toggled.connect(self._save_settings)

        seg = QWidget()
        seg_lay = QHBoxLayout(seg)
        seg_lay.setContentsMargins(0, 0, 0, 0)
        seg_lay.setSpacing(0)   # the two halves share a border
        seg_lay.addWidget(self.side_alsa_radio)
        seg_lay.addWidget(self.side_v2w_radio)
        card.add_header_widget(seg)

        self.side_toggle = QPushButton(_tr("Disable Sidetone"))
        self.side_toggle.setObjectName("Primary")
        self.side_toggle.setCheckable(True)
        self.side_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.side_toggle.clicked.connect(self.toggle_sidetone)

        self.side_slider = self._make_slider(70, self.change_sidetone)
        self.side_label = self._value_label("70%")
        level_row = QHBoxLayout()
        level_row.setSpacing(10)
        level_row.addWidget(self._row_label(_tr("Level")))
        level_row.addWidget(self.side_slider, 1)
        level_row.addWidget(self.side_label)

        self.side_exit_cb = QCheckBox(_tr("Turn off when closing the app"))
        self.side_exit_cb.setChecked(True)
        self.side_exit_cb.toggled.connect(self._save_settings)

        card.body.addWidget(self.side_toggle)
        card.body.addLayout(level_row)
        card.body.addWidget(self.side_exit_cb)
        return card

    def _build_volume_card(self):
        card = Card(_tr("Volume"))
        self.vol_slider = self._make_slider(70, self.change_volume)
        self.vol_label = self._value_label("70%")
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(self.vol_slider, 1)
        row.addWidget(self.vol_label)
        card.body.addLayout(row)
        return card

    def _build_equalizer_card(self):
        card = Card(_tr("Equalizer"))

        self.eq_enable_cb = QCheckBox(_tr("Enable Equalizer"))
        self.eq_enable_cb.setCursor(Qt.CursorShape.PointingHandCursor)
        self.eq_enable_cb.toggled.connect(self._on_eq_enabled_toggled)
        card.add_header_widget(self.eq_enable_cb)

        self.eq_export_btn = QPushButton("⤓")
        self.eq_export_btn.setObjectName("IconBtn")
        self.eq_export_btn.setToolTip(_tr("Export EasyEffects"))
        self.eq_export_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.eq_export_btn.setFixedSize(22, 22)
        self.eq_export_btn.clicked.connect(self._export_easyeffects)
        card.add_header_widget(self.eq_export_btn)

        self.eq_toggle_btn = QPushButton("▾")
        self.eq_toggle_btn.setObjectName("IconBtn")
        self.eq_toggle_btn.setFixedSize(22, 22)
        self.eq_toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.eq_toggle_btn.clicked.connect(self._toggle_eq_expanded)
        card.add_header_widget(self.eq_toggle_btn)

        # Preset selection row
        self.eq_preset_combo = ThemedComboBox()
        for name in EQ_PRESETS.keys():
            self.eq_preset_combo.addItem(_tr(name), name)
        self.eq_preset_combo.addItem(_tr("Custom"), "Custom")
        self.eq_preset_combo.currentIndexChanged.connect(self._on_eq_preset_changed)

        self.surround_cb = QCheckBox(_tr("7.1 Surround"))
        self.surround_cb.setCursor(Qt.CursorShape.PointingHandCursor)
        self.surround_cb.toggled.connect(self._on_surround_toggled)

        self.surround_test_btn = QPushButton(_tr("Test 7.1"))
        self.surround_test_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.surround_test_btn.setEnabled(False)
        self.surround_test_btn.clicked.connect(self.ctrl.play_surround_test)

        surround_row = QHBoxLayout()
        surround_row.setSpacing(8)
        surround_row.addWidget(self.surround_cb)
        surround_row.addStretch()
        surround_row.addWidget(self.surround_test_btn)

        preset_row = QHBoxLayout()
        preset_row.setSpacing(10)
        preset_row.addWidget(self._row_label(_tr("Preset:")))
        preset_row.addWidget(self.eq_preset_combo, 1)

        # Sliders container
        self.eq_sliders_container = QWidget()
        sliders_layout = QHBoxLayout(self.eq_sliders_container)
        sliders_layout.setContentsMargins(0, 4, 0, 4)
        sliders_layout.setSpacing(4)

        self.eq_sliders = []
        self.eq_labels = []

        freq_short = ["32", "64", "125", "250", "500", "1k", "2k", "4k", "8k", "16k"]
        for i, (freq, short_lbl) in enumerate(zip(EQ_FREQUENCIES, freq_short)):
            col = QVBoxLayout()
            col.setSpacing(2)
            col.setAlignment(Qt.AlignmentFlag.AlignCenter)

            f_lbl = QLabel(short_lbl)
            f_lbl.setObjectName("Muted")
            f_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            f_lbl.setStyleSheet("font-size: 10px;")

            slider = QSlider(Qt.Orientation.Vertical)
            slider.setRange(-120, 120)
            slider.setValue(0)
            slider.setFixedHeight(75)
            slider.setCursor(Qt.CursorShape.PointingHandCursor)
            slider.valueChanged.connect(lambda val, idx=i: self._on_eq_slider_moved(idx, val))

            val_lbl = QLabel("0.0")
            val_lbl.setObjectName("Muted")
            val_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            val_lbl.setStyleSheet("font-size: 9px;")

            col.addWidget(f_lbl)
            col.addWidget(slider, 0, Qt.AlignmentFlag.AlignCenter)
            col.addWidget(val_lbl)

            sliders_layout.addLayout(col)
            self.eq_sliders.append(slider)
            self.eq_labels.append(val_lbl)

        card.body.setSpacing(6)
        card.body.addLayout(preset_row)
        card.body.addLayout(surround_row)
        card.body.addWidget(self.eq_sliders_container)
        return card

    # ─── Tray with quick actions ───────────────────────────────────

    def init_tray(self):
        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(QIcon(self.icon_path))
        self.tray_icon.setToolTip("Virtuoso Control")

        menu = QMenu()



        # Mic state — informational only. The physical button is the control;
        # an in-app toggle would desync it.
        self.tray_mic_status = QAction(_tr("🎤 Mic: Active"), self)
        self.tray_mic_status.setEnabled(False)
        menu.addAction(self.tray_mic_status)

        # Toggle Sidetone (sincronizado con botón principal)
        self.tray_side_action = QAction(_tr("🔊 Sidetone: Enabled"), self)
        self.tray_side_action.setCheckable(True)
        self.tray_side_action.setChecked(self.side_toggle.isChecked())
        self.tray_side_action.triggered.connect(self._tray_toggle_sidetone)
        menu.addAction(self.tray_side_action)

        menu.addSeparator()
        
        # RGB Profiles Menu
        profiles_menu = menu.addMenu(_tr("🎨 RGB Profiles"))
        p1_action = QAction(_tr("Load Profile 1"), self)
        p1_action.triggered.connect(lambda: self.load_profile(1))
        p2_action = QAction(_tr("Load Profile 2"), self)
        p2_action.triggered.connect(lambda: self.load_profile(2))
        p3_action = QAction(_tr("Load Profile 3"), self)
        p3_action.triggered.connect(lambda: self.load_profile(3))
        profiles_menu.addAction(p1_action)
        profiles_menu.addAction(p2_action)
        profiles_menu.addAction(p3_action)

        menu.addSeparator()

        # Batería (click para actualizar)
        self.tray_batt_action = QAction(_tr("🔋 Battery: --"), self)
        self.tray_batt_action.triggered.connect(self.check_battery)
        menu.addAction(self.tray_batt_action)

        menu.addSeparator()

        open_action = QAction(_tr("Open"), self)
        open_action.triggered.connect(self.show)
        menu.addAction(open_action)

        exit_action = QAction(_tr("Quit"), self)
        exit_action.triggered.connect(self.quit_app)
        menu.addAction(exit_action)

        self.tray_menu = menu  # keep a reference alongside Qt's ownership
        self.tray_icon.setContextMenu(menu)
        self.tray_icon.activated.connect(self.tray_icon_activated)

        # Registers the battery icon before the main one so battery sits on
        # the left, and shows the main icon itself.
        self._setup_tray_icons()

        # The action label above is hardcoded to the enabled wording, so push
        # the state loaded from QSettings onto it now.
        self._sync_tray_sidetone()

    def _setup_tray_icons(self):
        """Applies the tray icon mode.

        virtuoso — one icon, the app logo, no battery gauge (default)
        battery  — one icon, the battery gauge drawn onto it
        both     — app logo plus a second, battery-only icon

        Ordering note: trays generally order icons by registration, so the
        battery icon is shown before the main one (see init_tray) to place it
        on the left. Switching to "both" at runtime cannot reorder an icon that
        is already registered — that needs a restart.
        """
        s = QSettings("AlejandroSocas", "VirtuosoControl")
        self._tray_mode = s.value("tray_icon_mode", "virtuoso", type=str)
        if self._tray_mode not in ("virtuoso", "battery", "both"):
            self._tray_mode = "virtuoso"

        if self._tray_mode == "both" and self.batt_tray_icon is None:
            self.batt_tray_icon = QSystemTrayIcon(self)
            self.batt_tray_icon.setIcon(QIcon(self.icon_path))

            # Its OWN menu on purpose: setContextMenu() transfers ownership in
            # PyQt, so handing it the main icon's menu destroys that menu along
            # with this icon and leaves the main icon pointing at freed memory.
            self.batt_menu = QMenu()
            act_refresh = QAction(_tr("Refresh"), self)
            act_refresh.triggered.connect(self.check_battery)
            act_open = QAction(_tr("Open"), self)
            act_open.triggered.connect(self.show)
            act_quit = QAction(_tr("Quit"), self)
            act_quit.triggered.connect(self.quit_app)
            for a in (act_refresh, act_open, act_quit):
                self.batt_menu.addAction(a)
            self.batt_tray_icon.setContextMenu(self.batt_menu)
            self.batt_tray_icon.activated.connect(self._batt_tray_activated)

        # Toggled by visibility rather than destroyed — recreating tray icons
        # is what caused the ownership crash above.
        if self._tray_mode == "both":
            # The tray assigns a position when an icon registers and never
            # reorders it, so showing the battery icon later always lands it on
            # the right. Re-register the main icon *behind* it instead.
            #
            # Battery is shown FIRST for a second reason: hiding the last
            # visible tray icon terminates the application, even with
            # setQuitOnLastWindowClosed(False). Keeping the battery icon up
            # means the main icon is never the last one when it is withdrawn.
            self.batt_tray_icon.setVisible(True)
            if self.tray_icon.isVisible():
                self.tray_icon.hide()
                # Deferred so the tray processes the removal before re-adding.
                QTimer.singleShot(120, self.tray_icon.show)
            else:
                self.tray_icon.show()
        else:
            if self.batt_tray_icon is not None:
                self.batt_tray_icon.setVisible(False)
            self.tray_icon.show()

        if self._tray_mode != "battery":
            self.tray_icon.setIcon(QIcon(self.icon_path))

        self._refresh_battery_icon()

    def _batt_tray_activated(self, reason):
        """Left-clicking the battery icon refreshes the reading."""
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.check_battery()

    def _refresh_battery_icon(self):
        """Draws the battery gauge onto whichever icon owns it in this mode."""
        if self._tray_mode == "virtuoso":
            self.tray_icon.setIcon(QIcon(self.icon_path))
            return

        if self._last_battery_percent is None:
            icon = QIcon(self.icon_path)
        else:
            icon = self._generate_battery_icon(self._last_battery_percent,
                                               self._last_charging)

        if self._tray_mode == "both" and self.batt_tray_icon is not None:
            self.batt_tray_icon.setIcon(icon)
        else:
            self.tray_icon.setIcon(icon)

    def tray_icon_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            if self.isVisible():
                self.hide()
            else:
                self.show()



    def _tray_toggle_sidetone(self, checked):
        """Toggle sidetone desde el menú del tray → sincroniza con botón."""
        self.side_toggle.setChecked(checked)
        self.toggle_sidetone()



    def _sync_tray_sidetone(self):
        """Sincroniza el texto del tray sidetone con el estado actual."""
        muted = self.side_toggle.isChecked()
        self.tray_side_action.setChecked(muted)
        self.tray_side_action.setText(
            _tr("🔇 Sidetone: Disabled") if muted else _tr("🔊 Sidetone: Enabled"))

    # ─── Persistencia de preferencias ────────────────────────────────

    def _load_settings(self):
        """Carga preferencias guardadas. Bloquea señales para evitar
        que se apliquen comandos antes de que la conexión esté lista."""
        s = QSettings("VirtuosoControl", "VirtuosoControl")

        # Bloquear señales durante la carga
        for w in (self.side_toggle, self.mic_tone_cb, self.side_exit_cb,
                  self.side_slider, self.side_alsa_radio,
                  self.side_v2w_radio, self.vol_slider, self.mic_vol_slider,
                  self.rgb_slider, self.mic_slider, self.rgb_effect_combo,
                  self.rgb_speed_combo, self.eq_enable_cb, self.surround_cb):
            w.blockSignals(True)

        self.side_toggle.setChecked(s.value("sidetone_muted", False, type=bool))
        self.side_slider.setValue(s.value("sidetone_level", 70, type=int))
        self.vol_slider.setValue(s.value("volume", 70, type=int))
        self.mic_vol_slider.setValue(s.value("mic_volume", 100, type=int))
        self.mic_vol_label.setText(f"{self.mic_vol_slider.value()}%")

        if s.value("sidetone_v2w", False, type=bool):
            self.side_v2w_radio.setChecked(True)
        else:
            self.side_alsa_radio.setChecked(True)

        color_hex = s.value("rgb_color", "#ff0000", type=str)
        self._current_color = QColor(color_hex)

        mute_hex = s.value("mic_mute_color", "#ff0000", type=str)
        self._mic_mute_color = QColor(mute_hex)
        self.mic_mute_color_btn.set_color(self._mic_mute_color)
        self.mic_tone_cb.setChecked(s.value("mic_tone", True, type=bool))
        self.side_exit_cb.setChecked(s.value("sidetone_off_on_exit", True, type=bool))

        mic_hex = s.value("mic_color", "#ff0000", type=str)
        self._current_mic_color = QColor(mic_hex)
        self.mic_slider.setValue(s.value("mic_brightness", 100, type=int))
        self.mic_color_btn.set_color(self._current_mic_color)
        self.mic_label.setText(f"{self.mic_slider.value()}%")
        self.rgb_slider.setValue(s.value("rgb_brightness", 100, type=int))
        self.rgb_color_btn.set_color(self._current_color)
        self.rgb_label.setText(f"{self.rgb_slider.value()}%")

        # Actualizar labels

        self.side_label.setText(f"{self.side_slider.value()}%")
        self.vol_label.setText(f"{self.vol_slider.value()}%")
        if self.side_toggle.isChecked():
            self.side_toggle.setText(_tr("Enable Sidetone"))
        else:
            self.side_toggle.setText(_tr("Disable Sidetone"))

        # Voice prompts
        s_user = QSettings("AlejandroSocas", "VirtuosoControl")
        self._voice_prompts = s_user.value("voice_prompts", True, type=bool)

        # Sleep timer
        sleep_mins = s.value("sleep_timer", 10, type=int)
        s_idx = self.sleep_combo.findData(sleep_mins)
        if s_idx >= 0:
            self.sleep_combo.blockSignals(True)
            self.sleep_combo.setCurrentIndex(s_idx)
            self.sleep_combo.blockSignals(False)

        # Equalizer preset
        eq_preset = s.value("eq_preset", "Pure Direct", type=str)
        eq_idx = self.eq_preset_combo.findData(eq_preset)
        if eq_idx >= 0:
            self.eq_preset_combo.blockSignals(True)
            self.eq_preset_combo.setCurrentIndex(eq_idx)
            self.eq_preset_combo.blockSignals(False)

        if eq_preset in EQ_PRESETS:
            gains = EQ_PRESETS[eq_preset]
            for i, gain in enumerate(gains):
                slider = self.eq_sliders[i]
                slider.blockSignals(True)
                slider.setValue(int(gain * 10))
                slider.blockSignals(False)
                sign = "+" if gain > 0 else ""
                self.eq_labels[i].setText(f"{sign}{gain:.1f}")
        elif eq_preset == "Custom":
            custom_gains = s.value("eq_custom_gains", [0.0]*10)
            if isinstance(custom_gains, list) and len(custom_gains) == 10:
                for i, g in enumerate(custom_gains):
                    try:
                        g_flt = float(g)
                        self.eq_sliders[i].blockSignals(True)
                        self.eq_sliders[i].setValue(int(g_flt * 10))
                        self.eq_sliders[i].blockSignals(False)
                        sign = "+" if g_flt > 0 else ""
                        self.eq_labels[i].setText(f"{sign}{g_flt:.1f}")
                    except (ValueError, TypeError):
                        pass

        eq_expanded = s.value("eq_expanded", True, type=bool)
        self.eq_sliders_container.setVisible(eq_expanded)
        self.eq_toggle_btn.setText("▾" if eq_expanded else "▸")

        # RGB Effect & Speed
        eff = s.value("rgb_effect", "Static", type=str)
        e_idx = self.rgb_effect_combo.findData(eff)
        if e_idx >= 0:
            self.rgb_effect_combo.setCurrentIndex(e_idx)
        spd = s.value("rgb_effect_speed", 1.0, type=float)
        sp_idx = self.rgb_speed_combo.findData(spd)
        if sp_idx >= 0:
            self.rgb_speed_combo.setCurrentIndex(sp_idx)
        self.rgb_speed_combo.setEnabled(eff in ("Rainbow", "ColorPulse", "ColorShift"))

        # Equalizer & Surround enabled
        eq_enabled = s.value("eq_enabled", False, type=bool)
        self.eq_enable_cb.setChecked(eq_enabled)
        surround_enabled = s.value("surround_enabled", False, type=bool)
        self.surround_cb.setChecked(surround_enabled)
        if hasattr(self, "surround_test_btn"):
            self.surround_test_btn.setEnabled(surround_enabled)

        # Desbloquear señales
        for w in (self.side_toggle, self.mic_tone_cb, self.side_exit_cb,
                  self.side_slider, self.side_alsa_radio,
                  self.side_v2w_radio, self.vol_slider, self.mic_vol_slider,
                  self.rgb_slider, self.mic_slider, self.rgb_effect_combo,
                  self.rgb_speed_combo, self.eq_enable_cb, self.surround_cb):
            w.blockSignals(False)

    def _save_settings(self):
        """Guarda las preferencias actuales."""
        s = QSettings("VirtuosoControl", "VirtuosoControl")
        s.setValue("mic_color", self._current_mic_color.name())
        s.setValue("mic_mute_color", self._mic_mute_color.name())
        s.setValue("mic_tone", self.mic_tone_cb.isChecked())
        s.setValue("sidetone_off_on_exit", self.side_exit_cb.isChecked())
        s.setValue("mic_brightness", self.mic_slider.value())
        s.setValue("mic_volume", self.mic_vol_slider.value())

        s.setValue("sidetone_muted", self.side_toggle.isChecked())
        s.setValue("sidetone_level", self.side_slider.value())
        s.setValue("sidetone_v2w", self.side_v2w_radio.isChecked())
        s.setValue("volume", self.vol_slider.value())
        s.setValue("rgb_color", self._current_color.name())
        s.setValue("rgb_brightness", self.rgb_slider.value())
        s.setValue("rgb_effect", self.rgb_effect_combo.currentData())
        s.setValue("rgb_effect_speed", self.rgb_speed_combo.currentData())
        s.setValue("sleep_timer", self.sleep_combo.currentData())
        s.setValue("eq_preset", self.eq_preset_combo.currentData())
        s.setValue("eq_expanded", self.eq_sliders_container.isVisible())
        s.setValue("eq_enabled", self.eq_enable_cb.isChecked())
        s.setValue("surround_enabled", self.surround_cb.isChecked())

    def _reapply_all_settings(self):
        """Re-applies all saved settings to the hardware.
        Called on initial connect and after every successful reconnection."""
        # Hardware sleep timer (SleepMode_StoreInDevice)
        mins = self.sleep_combo.currentData()
        if mins is not None and "Wired" not in self.ctrl.connection_mode:
            self.ctrl.set_sleep_timer(mins)

        # RGB & Dynamic effect timer
        self.apply_rgb()
        self._update_rgb_effect_timer()

        # Sidetone
        if self.side_toggle.isChecked():
            if self.side_v2w_radio.isChecked():
                self._apply_sidetone(0)
            else:
                self._apply_sidetone("off")
        else:
            self._apply_sidetone(self.side_slider.value())

        # Volume
        self.ctrl.set_volume(self.vol_slider.value())

    def _apply_saved_settings(self):
        """Aplica las preferencias cargadas al hardware.
        Se llama después de _try_initial_connect()."""
        self.keep_alive_timer.start(25_000)
        self._sync_mic_from_pw()  # before RGB, so the LED starts correct
        self._sync_volume_from_os()  # sync volume and mic gain from OS on start
        if self._hid_connected:
            self._reapply_all_settings()
        eq_on = hasattr(self, "eq_enable_cb") and self.eq_enable_cb.isChecked()
        surr_on = hasattr(self, "surround_cb") and self.surround_cb.isChecked()
        self.ctrl.eq.eq_enabled = eq_on
        self.ctrl.eq.surround_enabled = surr_on
        if eq_on or surr_on:
            gains = [s.value() / 10.0 for s in self.eq_sliders]
            self.ctrl.enable_eq(eq_on, gains)
            self._sync_volume_from_os()

    # ─── Conexión HID ────────────────────────────────────────────────

    def _try_initial_connect(self):
        """Conecta al HID y comprueba si los auriculares están activos."""
        if self.ctrl.connect():
            if self.ctrl.is_headset_alive():
                self._hid_connected = True
                self._update_status(True)
            else:
                self._hid_connected = False
                self._update_status(False)
                self.reconnect_timer.start(3000)
        else:
            self._hid_connected = False
            self._update_status(False)
            self.reconnect_timer.start(3000)

    def _set_status(self, text, color_key):
        """Header indicator: coloured dot plus plain text, no emoji."""
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"color: {THEME[color_key]};")
        self.status_dot.set_color(c(color_key))

    def _set_v2w_controls_enabled(self, enabled):
        """Enables/disables everything that needs a live V2W link."""
        for w in (self.batt_btn, self.mic_color_btn, self.mic_slider,
                  self.rgb_color_btn, self.rgb_slider, self.mic_mute_color_btn,
                  self.rgb_effect_combo, self.rgb_speed_combo,
                  self.profile_btn_1, self.profile_btn_2, self.profile_btn_3,
                  self.save_profile_btn, self.sleep_combo, self.quick_lighting_combo):
            try:
                w.setEnabled(enabled)
            except Exception:
                pass
        if enabled:
            eff = self.rgb_effect_combo.currentData()
            self.rgb_speed_combo.setEnabled(eff in ("Rainbow", "ColorPulse", "ColorShift"))

    def _update_status(self, connected):
        if connected:
            dev_name = self.ctrl.get_device_name()
            self._set_status(
                f"{dev_name} · {_tr(self.ctrl.connection_mode)}", "ok")

            # Disable V2W controls if on wired mode
            is_wired = "Wired" in self.ctrl.connection_mode
            self._set_v2w_controls_enabled(not is_wired)

            if is_wired:
                self._update_battery_display(_tr("N/A (Wired)"))
        else:
            self._set_status(
                f"{_tr('Disconnected')} · {_tr('Searching for device...')}",
                "danger")
            self._set_v2w_controls_enabled(False)
            self._update_battery_display(_tr("Not connected"))

    # ─── Hardware Sleep Timer & Quick Colors Handlers ────────────────

    def _on_sleep_timer_changed(self, index):
        mins = self.sleep_combo.currentData()
        if mins is not None:
            self.ctrl.set_sleep_timer(mins)
            s = QSettings("VirtuosoControl", "VirtuosoControl")
            s.setValue("sleep_timer", mins)

    def _on_quick_lighting_selected(self, index):
        color_hex = self.quick_lighting_combo.currentData()
        if not color_hex:
            return
        col = QColor(color_hex)
        self._current_color = col
        self.rgb_color_btn.set_color(col)
        # Switch back to Static when picking an explicit solid quick color
        static_idx = self.rgb_effect_combo.findData("Static")
        if static_idx >= 0:
            self.rgb_effect_combo.blockSignals(True)
            self.rgb_effect_combo.setCurrentIndex(static_idx)
            self.rgb_effect_combo.blockSignals(False)
            self.rgb_speed_combo.setEnabled(False)
        self._update_rgb_effect_timer()
        self.apply_rgb()
        self._save_settings()
        self.quick_lighting_combo.blockSignals(True)
        self.quick_lighting_combo.setCurrentIndex(0)
        self.quick_lighting_combo.blockSignals(False)

    # ─── Equalizer & Surround Handlers ───────────────────────────────

    def _run_dsp_transition(self, fn):
        """Runs a DSP toggle safely: cancels stale slider debounce and blocks re-entrant clicks."""
        if hasattr(self, "eq_apply_timer"):
            self.eq_apply_timer.stop()
        widgets = [w for w in (getattr(self, "eq_enable_cb", None),
                               getattr(self, "surround_cb", None)) if w is not None]
        for w in widgets:
            w.setEnabled(False)
        try:
            return fn()
        finally:
            for w in widgets:
                w.setEnabled(True)

    def _on_surround_toggled(self, checked):
        s = QSettings("VirtuosoControl", "VirtuosoControl")
        s.setValue("surround_enabled", checked)
        if hasattr(self, "surround_test_btn"):
            self.surround_test_btn.setEnabled(checked)
        self._run_dsp_transition(lambda: self.ctrl.enable_surround(checked))
        self._sync_volume_from_os()
        if getattr(self, "_voice_prompts", True):
            self.ctrl.play_sound("SurroundOn" if checked else "SurroundOff")

    def _on_eq_enabled_toggled(self, checked):
        s = QSettings("VirtuosoControl", "VirtuosoControl")
        s.setValue("eq_enabled", checked)
        gains = [sl.value() / 10.0 for sl in self.eq_sliders]
        self._run_dsp_transition(lambda: self.ctrl.enable_eq(checked, gains))
        self._sync_volume_from_os()

    def _apply_current_eq(self):
        gains = [sl.value() / 10.0 for sl in self.eq_sliders]
        s = QSettings("VirtuosoControl", "VirtuosoControl")
        s.setValue("eq_preset", "Custom")
        s.setValue("eq_custom_gains", gains)
        if hasattr(self, "eq_enable_cb") and self.eq_enable_cb.isChecked():
            self.ctrl.set_eq_gains(gains)

    def _on_eq_preset_changed(self, index):
        preset_name = self.eq_preset_combo.currentData()
        if not preset_name or preset_name == "Custom":
            return

        if preset_name in EQ_PRESETS:
            gains = EQ_PRESETS[preset_name]
            for i, gain in enumerate(gains):
                slider = self.eq_sliders[i]
                slider.blockSignals(True)
                slider.setValue(int(round(gain * 10)))
                slider.blockSignals(False)
                sign = "+" if gain > 0 else ""
                self.eq_labels[i].setText(f"{sign}{gain:.1f}")

            # Play official sound cue
            if getattr(self, "_voice_prompts", True):
                try:
                    preset_idx = list(EQ_PRESETS.keys()).index(preset_name) + 1
                    self.ctrl.play_sound(f"EqualizerPresetChanged{preset_idx}")
                except Exception:
                    pass

            if hasattr(self, "eq_apply_timer"):
                self.eq_apply_timer.stop()
            s = QSettings("VirtuosoControl", "VirtuosoControl")
            s.setValue("eq_preset", preset_name)
            if hasattr(self, "eq_enable_cb") and self.eq_enable_cb.isChecked():
                self.ctrl.set_eq_gains(gains)

    def _on_eq_slider_moved(self, idx, val):
        db = val / 10.0
        sign = "+" if db > 0 else ""
        self.eq_labels[idx].setText(f"{sign}{db:.1f}")

        # Set combo to "Custom" without retriggering preset handler
        self.eq_preset_combo.blockSignals(True)
        custom_idx = self.eq_preset_combo.findData("Custom")
        if custom_idx >= 0:
            self.eq_preset_combo.setCurrentIndex(custom_idx)
        self.eq_preset_combo.blockSignals(False)

        # Trigger debounced apply (150ms)
        self.eq_apply_timer.start(150)

    def _toggle_eq_expanded(self):
        is_visible = self.eq_sliders_container.isVisible()
        self.eq_sliders_container.setVisible(not is_visible)
        self.eq_toggle_btn.setText("▸" if is_visible else "▾")
        s = QSettings("VirtuosoControl", "VirtuosoControl")
        s.setValue("eq_expanded", not is_visible)
        self.adjustSize()
        self.setFixedHeight(self.sizeHint().height())

    def _export_easyeffects(self):
        preset_name = self.eq_preset_combo.currentData() or "Pure Direct"
        if preset_name == "Custom":
            gains = [s.value() / 10.0 for s in self.eq_sliders]
            EQ_PRESETS["Custom"] = gains

        path = self.ctrl.export_easyeffects_preset(preset_name)
        if path:
            QMessageBox.information(
                self,
                _tr("Export EasyEffects"),
                f"{_tr('Exported EasyEffects preset to:')}\n{path}"
            )
        else:
            QMessageBox.warning(self, _tr("Error"), "Failed to export preset.")

    def _on_connection_lost(self):
        self._hid_connected = False
        self.ctrl.reset_session_state()
        self._update_status(False)
        self.keep_alive_timer.stop()
        if hasattr(self, "rgb_effect_timer"):
            self.rgb_effect_timer.stop()
        if not self.reconnect_timer.isActive():
            self.reconnect_timer.start(3000)

    def force_reconnect(self):
        self._set_status(_tr("Connecting..."), "warn")
        self.ctrl.reconnect()
        QTimer.singleShot(50, self.try_reconnect)

    def try_reconnect(self):
        if not self.ctrl.is_connected:
            if not self.ctrl.connect():
                return

        if self.ctrl.is_headset_alive():
            self._hid_connected = True
            self._update_status(True)
            self.reconnect_timer.stop()
            self.keep_alive_timer.start(25_000)
            self._reapply_all_settings()
            self._initial_battery_check()

    def _recover_software_mode(self):
        """Re-enters Software Mode and restores all RGB/Brightness/Sleep settings after hardware reset or wake."""
        self.ctrl.reset_session_state()
        if self.ctrl._ensure_handshake():
            self._reapply_all_settings()
            return True
        return False

    # ─── LED del micrófono ─────────────────────────────────────────

    def do_keep_alive(self):
        if not self._hid_connected:
            return

        # In wired mode, V2W heartbeat and queries are unsupported
        if "Wired" in self.ctrl.connection_mode:
            return

        # Check current operating mode (1=Hardware, 2=Software, None=unresponsive)
        # Running every 25s keeps the headset safely under its 60s firmware watchdog,
        # and immediately recovers Software Mode if the headset woke up from hardware sleep.
        try:
            mode = self.ctrl.get_operating_mode()
            if mode == 1:
                if self._recover_software_mode():
                    return
            elif mode == 2:
                self.ctrl.send_heartbeat()
                self.apply_rgb()
                return

            # Mode query failed or returned None — verify liveness robustly
            QCoreApplication.processEvents()
            QThread.msleep(100)

            headset_alive = False
            for _ in range(3):
                if self.ctrl.is_headset_alive():
                    headset_alive = True
                    break
                QCoreApplication.processEvents()
                QThread.msleep(150)

            if not headset_alive:
                self._on_connection_lost()
                return

            # Alive again after transient miss: ensure software mode & LEDs are active
            if self.ctrl.get_operating_mode() == 1:
                self._recover_software_mode()

        except Exception:
            self._on_connection_lost()

    # ─── Iluminación RGB ─────────────────────────────────────────────

    def choose_color(self):
        color = QColorDialog.getColor(self._current_color, self, _tr("Pick Logo Color"))
        if color.isValid():
            self._current_color = color
            self.rgb_color_btn.set_color(color)
            self.apply_rgb()
            self._save_settings()

    def _on_rgb_effect_changed(self):
        eff = self.rgb_effect_combo.currentData()
        self.rgb_speed_combo.setEnabled(eff in ("Rainbow", "ColorPulse", "ColorShift"))
        self._update_rgb_effect_timer()
        self.apply_rgb()
        self._save_settings()

    def _update_rgb_effect_timer(self):
        eff = self.rgb_effect_combo.currentData() if hasattr(self, "rgb_effect_combo") else "Static"
        should_run = (
            self._hid_connected
            and "Wired" not in self.ctrl.connection_mode
            and self.rgb_slider.value() > 0
            and eff in ("Rainbow", "ColorPulse", "ColorShift")
        )
        if should_run:
            if not self.rgb_effect_timer.isActive():
                self.rgb_effect_timer.start(65)
        else:
            if self.rgb_effect_timer.isActive():
                self.rgb_effect_timer.stop()

    def _tick_rgb_effect(self):
        speed = float(self.rgb_speed_combo.currentData() or 1.0)
        self._effect_phase = (self._effect_phase + 0.025 * speed) % 1.0
        self.apply_rgb()

    def _compute_logo_rgb(self):
        eff = self.rgb_effect_combo.currentData() if hasattr(self, "rgb_effect_combo") else "Static"
        if eff == "Rainbow":
            col = QColor.fromHsvF(self._effect_phase, 1.0, 1.0)
            return (col.red(), col.green(), col.blue())
        if eff == "ColorPulse":
            pulse = 0.15 + 0.85 * (0.5 - 0.5 * math.cos(2 * math.pi * self._effect_phase))
            return (
                int(self._current_color.red() * pulse),
                int(self._current_color.green() * pulse),
                int(self._current_color.blue() * pulse),
            )
        if eff == "ColorShift":
            # Smoothly cycle between user's chosen color and its complementary/accent hue
            h0 = self._current_color.hsvHueF()
            if h0 < 0:
                h0 = 0.0
            h1 = (h0 + 0.5) % 1.0
            t = 0.5 - 0.5 * math.cos(2 * math.pi * self._effect_phase)
            c0 = QColor.fromHsvF(h0, max(0.7, self._current_color.hsvSaturationF()), 1.0)
            c1 = QColor.fromHsvF(h1, 1.0, 1.0)
            return (
                int(c0.red() * (1 - t) + c1.red() * t),
                int(c0.green() * (1 - t) + c1.green() * t),
                int(c0.blue() * (1 - t) + c1.blue() * t),
            )
        if eff == "Battery":
            p = self._last_battery_percent
            if p is None:
                return (self._current_color.red(), self._current_color.green(), self._current_color.blue())
            if self._last_charging:
                return (60, 150, 255)
            if p >= BATT_LEVEL["HIGH"]:
                return (0, 255, 80)
            if p >= BATT_LEVEL["MEDIUM"]:
                return (255, 220, 0)
            if p >= BATT_LEVEL["LOW"]:
                return (255, 110, 0)
            return (255, 0, 0)
        return (self._current_color.red(), self._current_color.green(), self._current_color.blue())

    def change_rgb(self, value):
        self.rgb_label.setText(f"{self.rgb_slider.value()}%")
        self.mic_label.setText(f"{self.mic_slider.value()}%")
        self._update_rgb_effect_timer()
        self.apply_rgb()
        self._save_settings()

    def choose_mic_mute_color(self):
        color = QColorDialog.getColor(self._mic_mute_color, self,
                                      _tr("Pick Mute Color"))
        if color.isValid():
            self._mic_mute_color = color
            self.mic_mute_color_btn.set_color(color)
            self.apply_rgb()  # visible immediately if currently muted
            self._save_settings()

    def choose_mic_color(self):
        color = QColorDialog.getColor(self._current_mic_color, self, _tr("Pick Microphone Color"))
        if color.isValid():
            self._current_mic_color = color
            self.mic_color_btn.set_color(color)
            self.apply_rgb()
            self._save_settings()

    def apply_rgb(self):
        if self._hid_connected:
            # Battery LED state calculation
            batt_r, batt_g, batt_b = 0, 0, 0
            if self._last_battery_percent is not None:
                p = self._last_battery_percent
                if p >= BATT_LEVEL["HIGH"]:
                    batt_g = 255                # Green (70%-100%)
                elif p >= BATT_LEVEL["MEDIUM"]:
                    batt_r, batt_g = 255, 255   # Yellow (50%-69%)
                elif p >= BATT_LEVEL["LOW"]:
                    batt_r, batt_g = 255, 128   # Orange (20%-49%)
                else:
                    # INTEGRATED BLINKING LOGIC.
                    # If the cable is connected, we ignore the blinking and force a solid red.
                    if self._last_charging:
                        batt_r = 255            # Red (<20%) fixed
                    # If it's disconnected, we allow normal blinking logic.
                    elif self.blink_timer.isActive():
                        if self._blink_state:
                            batt_r = 255        # Red
                        else:
                            batt_r = 0          # Off
                    else:
                        batt_r = 255            # Red (<20%) fixed

            # --- POWER SAVING MODE (BATTERY < 15%) ---
            # If the percentage is below the notification level, set the headset LEDs brightness to 0%.
            if self._last_battery_percent is not None and self._last_battery_percent < BATT_LEVEL["NOTIFY_LOW"]:

                # 1. Reducing the headset LEDs brightness at 0% of its corresponding value.
                if not getattr(self, '_battery_low_forced_zero', False):
                    self._brightness_snapshot_at_low = self.rgb_slider.value() # Saving slider value.
                    self._battery_low_forced_zero = True
                    self._user_interacted_with_slider = False # Flag to know if user changed the slider

                # Check if the user has moved the slider away from the snapshot value
                if not getattr(self, '_user_interacted_with_slider', False) and self.rgb_slider.value() != self._brightness_snapshot_at_low:
                    self._user_interacted_with_slider = True

                if not getattr(self, '_user_interacted_with_slider', False):
                    rgb_brightness = POWER_SAVING_BRIGHTNESS["RGB_fixed"] # 0% of static brightness.
                else:
                    rgb_brightness = int(self.rgb_slider.value() * POWER_SAVING_BRIGHTNESS["RGB_max"]) # 25% of the slider value (new 100%).

                # 2. Reducing the microphone brightness to 25% of its corresponding value.
                # Muted mic goes red, matching iCUE on Windows/macOS. The saved
                # colour is untouched and returns on unmute.
                if self._mic_muted:
                    mic_rgb = (self._mic_mute_color.red(),
                               self._mic_mute_color.green(),
                               self._mic_mute_color.blue())
                    mic_brightness = POWER_SAVING_BRIGHTNESS["MIC_fixed"] # 25% of static brightness.
                else:
                    mic_rgb = (self._current_mic_color.red(),
                               self._current_mic_color.green(),
                               self._current_mic_color.blue())
                    mic_brightness = int(self.mic_slider.value() * POWER_SAVING_BRIGHTNESS["MIC_max"]) # 50% of the slider value (new 100%).

            else:
                # Normal behavior if battery is >= 15%.
                # If the battery level rises again (e.g., while charging), clear the flag for the next time.
                if getattr(self, '_battery_low_forced_zero', False):
                    self._battery_low_forced_zero = False
                    self._user_interacted_with_slider = False  # Reset the interaction flag too.

                rgb_brightness = self.rgb_slider.value()

                # Muted mic goes red, matching iCUE on Windows/macOS. The saved
                # colour is untouched and returns on unmute.
                if self._mic_muted:
                    mic_rgb = (self._mic_mute_color.red(),
                               self._mic_mute_color.green(),
                               self._mic_mute_color.blue())
                    mic_brightness = 100
                else:
                    mic_rgb = (self._current_mic_color.red(),
                               self._current_mic_color.green(),
                               self._current_mic_color.blue())
                    mic_brightness = self.mic_slider.value()
            # -----------------------------------------------

            logo_rgb = self._compute_logo_rgb()
            self.ctrl.set_all_rgb(
                logo_rgb,
                rgb_brightness,
                mic_rgb,
                mic_brightness,
                (batt_r, batt_g, batt_b),
                100
            )

    def _toggle_blink_led(self):
        # Toggles the blinking state and updates the LEDs.
        self._blink_state = not self._blink_state
        self.apply_rgb()

    def save_profile(self, index):
        s = QSettings("VirtuosoControl", "VirtuosoControl")
        s.setValue(f"profile_{index}_rgb_color", self._current_color.name())
        s.setValue(f"profile_{index}_rgb_brightness", self.rgb_slider.value())
        s.setValue(f"profile_{index}_mic_color", self._current_mic_color.name())
        s.setValue(f"profile_{index}_mic_brightness", self.mic_slider.value())
        if self.tray_icon:
            self.tray_icon.showMessage(_tr("Profile Saved"), _tr("Profile {} saved successfully.").format(index), QSystemTrayIcon.MessageIcon.Information, 2000)

    def load_profile(self, index):
        s = QSettings("VirtuosoControl", "VirtuosoControl")
        
        rgb_color = s.value(f"profile_{index}_rgb_color", None, type=str)
        if not rgb_color:
            if self.tray_icon:
                self.tray_icon.showMessage(_tr("Error"), _tr("Profile {} is empty.").format(index), QSystemTrayIcon.MessageIcon.Warning, 2000)
            return
            
        self._current_color = QColor(rgb_color)
        self.rgb_slider.setValue(s.value(f"profile_{index}_rgb_brightness", 100, type=int))
        self._current_mic_color = QColor(s.value(f"profile_{index}_mic_color", "#ff0000", type=str))
        self.mic_slider.setValue(s.value(f"profile_{index}_mic_brightness", 100, type=int))
        
        self.rgb_color_btn.set_color(self._current_color)
        self.mic_color_btn.set_color(self._current_mic_color)
        self.rgb_label.setText(f"{self.rgb_slider.value()}%")
        self.mic_label.setText(f"{self.mic_slider.value()}%")
        
        self.apply_rgb()
        self._save_settings()

    # ─── Physical mic-mute button ────────────────────────────────────

    def _poll_mic_button(self):
        """Follows the headset's physical mic-mute button.

        In software mode the firmware forwards the button instead of acting on
        it, so the app has to do the muting and the LED. Deliberately one-way:
        the headset is the source of truth and there is no in-app toggle, which
        is what previously fought the button.
        """
        if not self._hid_connected:
            return
        presses = self.ctrl.poll_mic_button()
        if self.ctrl.consume_hw_reset_event():
            if self.ctrl.get_operating_mode() == 1:
                self._recover_software_mode()
        if presses % 2 == 0:
            return  # no presses, or an even number that cancels out
        self._apply_mic_mute(not self._mic_muted)

    def _apply_mic_mute(self, muted):
        """Applies a mute state coming from the headset button."""
        self._mic_muted = muted
        self.ctrl.set_mic_mute_pw(muted)
        self.apply_rgb()          # mute colour while muted, saved colour otherwise
        self._sync_mic_status()
        # The firmware beeps on a mute change in hardware mode; in software
        # mode it stays silent, so supply the cue ourselves.
        if self.mic_tone_cb.isChecked():
            if getattr(self, "_voice_prompts", True):
                self.ctrl.play_sound("MicOff" if muted else "MicOn")
            else:
                self.ctrl.play_mic_tone(muted)

    def _sync_mic_status(self):
        """Updates the read-only mic indicator in the window and the tray."""
        if hasattr(self, "tray_mic_status"):
            self.tray_mic_status.setText(
                _tr("🎤 Mic: Muted") if self._mic_muted else _tr("🎤 Mic: Active"))

        key = "danger" if self._mic_muted else "ok"
        self.mic_state_dot.set_color(c(key))
        self.mic_state_label.setText(
            _tr("Muted") if self._mic_muted else _tr("Active"))
        self.mic_state_label.setStyleSheet(f"color: {THEME[key]}; font-weight: 600;")

    def _sync_mic_from_pw(self):
        """Seeds our state from PipeWire so the LED matches at startup."""
        state = self.ctrl.get_mic_muted_pw()
        if state is not None:
            self._mic_muted = state
            self._sync_mic_status()

    # ─── Sidetone ────────────────────────────────────────────────────

    def _apply_sidetone(self, value):
        if self.side_v2w_radio.isChecked():
            if self._hid_connected:
                level = value if isinstance(value, int) else 0
                return self.ctrl.set_sidetone_v2w(level)
            return False
        else:
            return self.ctrl.set_sidetone(value)

    def toggle_sidetone(self):
        if self.side_toggle.isChecked():
            if self.side_v2w_radio.isChecked():
                self._apply_sidetone(0)
            else:
                self._apply_sidetone("off")
            self.side_toggle.setText(_tr("Enable Sidetone"))
            if getattr(self, "_voice_prompts", True):
                self.ctrl.play_sound("SidetoneOff")
        else:
            self._apply_sidetone(self.side_slider.value())
            self.side_toggle.setText(_tr("Disable Sidetone"))
            if getattr(self, "_voice_prompts", True):
                self.ctrl.play_sound("SidetoneOn")

        self._sync_tray_sidetone()
        self._save_settings()

    def change_sidetone(self, value):
        self.side_label.setText(f"{value}%")
        if not self.side_toggle.isChecked():
            self._apply_sidetone(value)
        self._save_settings()

    # ─── Volume & Mic Gain ───────────────────────────────────────────

    def change_mic_volume(self, value):
        self.mic_vol_label.setText(f"{value}%")
        self.ctrl.set_mic_volume_pw(value)
        self._save_settings()

    def change_volume(self, value):
        self.vol_label.setText(f"{value}%")
        self.ctrl.set_volume(value)
        self._save_settings()

    def _sync_volume_from_os(self):
        """Synchronizes output volume, mic gain sliders, and routes new app streams through DSP."""
        if self.ctrl.eq.is_enabled:
            self.ctrl.route_eq_streams()
        if not self.vol_slider.isSliderDown():
            vol = self.ctrl.get_volume()
            if vol is not None and vol != self.vol_slider.value():
                self.vol_slider.blockSignals(True)
                self.vol_slider.setValue(vol)
                self.vol_slider.blockSignals(False)
                self.vol_label.setText(f"{vol}%")
                self._save_settings()

        if hasattr(self, "mic_vol_slider") and not self.mic_vol_slider.isSliderDown():
            mvol = self.ctrl.get_mic_volume_pw()
            if mvol is not None and mvol != self.mic_vol_slider.value():
                self.mic_vol_slider.blockSignals(True)
                self.mic_vol_slider.setValue(mvol)
                self.mic_vol_slider.blockSignals(False)
                self.mic_vol_label.setText(f"{mvol}%")
                self._save_settings()

    # ─── Voice Prompt Reminders ──────────────────────────────────────

    def _configure_reminder_timers(self):
        s = QSettings("AlejandroSocas", "VirtuosoControl")
        lb_sec = s.value("low_battery_reminder_sec", 0, type=int)
        mm_sec = s.value("mic_mute_reminder_sec", 0, type=int)
        if lb_sec > 0:
            self.low_batt_remind_timer.start(lb_sec * 1000)
        else:
            self.low_batt_remind_timer.stop()
        if mm_sec > 0:
            self.mic_mute_remind_timer.start(mm_sec * 1000)
        else:
            self.mic_mute_remind_timer.stop()

    def _on_low_batt_reminder(self):
        if (self._hid_connected and getattr(self, "_voice_prompts", True)
                and not self._last_charging
                and self._last_battery_percent is not None
                and self._last_battery_percent < BATT_LEVEL["LOW"]):
            sound = "BatteryCritical" if self._last_battery_percent < BATT_LEVEL["NOTIFY_LOW"] else "BatteryLow"
            self.ctrl.play_sound(sound)

    def _on_mic_mute_reminder(self):
        if self._hid_connected and getattr(self, "_voice_prompts", True) and self._mic_muted:
            self.ctrl.play_sound("MicOff")

    # ─── Battery ─────────────────────────────────────────────────────

    def check_battery(self):
        """Queries battery and updates UI + tray."""
        if not self._hid_connected:
            self._update_battery_display(_tr("Not connected"))
            return
        battery_str = self.ctrl.get_battery()
        # Translate the status part of the battery string
        for status_key in ["Charging", "Discharging", "Not connected", "Handshake error", "Read error", "No response (try again)"]:
            if status_key in battery_str:
                battery_str = battery_str.replace(status_key, _tr(status_key))
        self._update_battery_display(battery_str)

        # Notificación de batería baja
        self._check_low_battery(battery_str)

    def _initial_battery_check(self):
        """Battery read right after startup or a reconnect.

        Skipped in wired mode, where _update_status() has already put
        "N/A (Wired)" in the label and the V2W battery query is unsupported.
        """
        if not self._hid_connected:
            return
        if "Wired" in self.ctrl.connection_mode:
            return
        self.check_battery()

    def _auto_battery_check(self):
        """Periodic auto-check. Only if handshake is done
        (to avoid triggering handshake and turning off the LED accidentally)."""
        if self._hid_connected and self.ctrl._handshake_done:
            # Smart Battery Polling: 
            # If battery is low (<15%) and not physically charging, skip polling to avoid beeps.
            if self._last_battery_percent is not None and self._last_battery_percent < BATT_LEVEL["NOTIFY_LOW"]:
                if not self.ctrl.is_usb_charging:
                    # Keep showing low battery, don't query headset
                    return
            
            battery_str = self.ctrl.get_battery()
            # Translate the status part of the battery string
            for status_key in ["Charging", "Discharging", "Not connected", "Handshake error", "Read error", "No response (try again)"]:
                if status_key in battery_str:
                    battery_str = battery_str.replace(status_key, _tr(status_key))
                    
            self._update_battery_display(battery_str)
            self._check_low_battery(battery_str)

    def _generate_battery_icon(self, percent, is_charging):
        if percent is None:
            return QIcon(self.icon_path)
            
        pixmap = QPixmap(32, 32)
        pixmap.fill(Qt.GlobalColor.transparent)
        
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # Draw battery outline
        pen = QPen(Qt.GlobalColor.white)
        pen.setWidth(2)
        painter.setPen(pen)
        painter.drawRoundedRect(4, 10, 22, 12, 2, 2)
        painter.drawRect(26, 13, 2, 6) # Tip
        
        # Fill color
        if is_charging:
            color = QColor(52, 52, 255) # Blue (Charging)
        elif percent >= BATT_LEVEL["HIGH"]:
            color = QColor(46, 204, 113) # Green (70%-100%)
        elif percent >= BATT_LEVEL["MEDIUM"]:
            color = QColor(241, 196, 15) # Yellow (50%-69%)
        elif percent >= BATT_LEVEL["LOW"]:
            color = QColor(230, 126, 34) # Orange (20%-49%)
        else:
            color = QColor(231, 76, 60)  # Red (<20%)
            
        painter.setBrush(QBrush(color))
        painter.setPen(Qt.PenStyle.NoPen)
        
        # Fill based on percent (width max = 18)
        fill_width = int(18 * (percent / 100.0))
        if fill_width > 0:
            painter.drawRoundedRect(6, 12, fill_width, 8, 1, 1)
            
        painter.end()
        return QIcon(pixmap)

    def _update_battery_display(self, battery_str):
        """Updates battery level in window, tray tooltip and menu."""
        self.tray_batt_action.setText(f"🔋 {_tr('Battery')}: {battery_str}")
        self.tray_icon.setToolTip(f"Virtuoso Control — {battery_str}")
        if self.batt_tray_icon is not None:
            self.batt_tray_icon.setToolTip(f"🔋 {_tr('Battery')}: {battery_str}")

        try:
            self._last_battery_percent = int(battery_str.split("%")[0])
            self._last_charging = (_tr("Charging") in battery_str
                                   or "Cargando" in battery_str
                                   or "Charging" in battery_str)
        except (ValueError, IndexError):
            self._last_battery_percent = None
            self._last_charging = False

        # The card splits the reading the tray shows as one string: the number
        # goes in the big label, whatever follows it ("[Discharging]") in the
        # small one. Non-numeric readings ("Not connected") have no number, so
        # the whole string becomes the small label.
        if self._last_battery_percent is None:
            self.batt_percent.setText("—")
            self.batt_label.setText(battery_str)
        else:
            self.batt_percent.setText(f"{self._last_battery_percent}%")
            tail = battery_str.split("%", 1)[1].strip().strip("[]").strip()
            self.batt_label.setText(tail)

        self.batt_gauge.set_state(self._last_battery_percent, self._last_charging)
        self.batt_percent.setStyleSheet(
            f"color: {self.batt_gauge.level_color().name()};")
        self._refresh_battery_icon()

    def _check_low_battery(self, battery_str):
        """Shows desktop notification if battery < 15% and controls blinking."""
        try:
            percent = int(battery_str.split("%")[0])
            self._last_battery_percent = percent
        except (ValueError, IndexError):
            return

        # --- Alert 1: Low Battery (< 30%) ---
        if not self._last_charging and BATT_LEVEL["NOTIFY_LOW"] <= percent < BATT_LEVEL["LOW"] and not self._low_battery_notified_0:
            if getattr(self, "_voice_prompts", True):
                self.ctrl.play_sound("BatteryLow")
            self.tray_icon.showMessage(
                _tr("⚠️ Low Battery — Virtuoso SE"),
                _tr("The headset is at {}% battery.").format(percent),
                QSystemTrayIcon.MessageIcon.Warning,
                10_000)
            self._low_battery_notified_0 = True

        # --- Blink Timer ---
        if not self._last_charging and percent < BATT_LEVEL["LOW"]:
            if not self.blink_timer.isActive():
                self.blink_timer.start(500) # Blinking 0.5s
        else:
            if self.blink_timer.isActive():
                self.blink_timer.stop() # Stopping Blinking

        # --- Alert 2: Very Low Battery (< 15%) ---
        if not self._last_charging and percent < BATT_LEVEL["NOTIFY_LOW"] and not self._low_battery_notified:
            if getattr(self, "_voice_prompts", True):
                self.ctrl.play_sound("BatteryCritical")
            self.tray_icon.showMessage(
                _tr("⚠️ Very Low Battery — Virtuoso SE"),
                _tr("The headset is at {}% battery. Power saving mode enabled.").format(percent),
                QSystemTrayIcon.MessageIcon.Warning,
                10_000)
            self._low_battery_notified = True

        # --- Resetting the flags ---
        if self._last_charging or percent >= BATT_LEVEL["LOW"]:
            # Reset flags when it goes up (e.g., charging)
            self._low_battery_notified_0 = False
            self._low_battery_notified = False

        self.apply_rgb() # Update LED battery state

    # ─── Lifecycle ───────────────────────────────────────────────────

    def closeEvent(self, event):
        event.ignore()
        self.hide()

    def quit_app(self):
        """Shuts down cleanly. Idempotent — see the guard below.

        __main__ wires this to app.aboutToQuit *and* the tray Quit action, and
        it calls quit() itself, so without the guard it re-enters via
        aboutToQuit until Python's recursion limit trips — roughly 14 seconds
        of teardown work repeated at every level before the app finally exits.
        """
        if self._quitting:
            return
        self._quitting = True

        self._save_settings()
        self.keep_alive_timer.stop()
        self.reconnect_timer.stop()
        self.battery_timer.stop()
        self.mic_button_timer.stop()
        if hasattr(self, "volume_sync_timer"):
            self.volume_sync_timer.stop()
        if hasattr(self, "blink_timer"):
            self.blink_timer.stop()
        if hasattr(self, "eq_apply_timer"):
            self.eq_apply_timer.stop()
        if hasattr(self, "rgb_effect_timer"):
            self.rgb_effect_timer.stop()
        if hasattr(self, "low_batt_remind_timer"):
            self.low_batt_remind_timer.stop()
        if hasattr(self, "mic_mute_remind_timer"):
            self.mic_mute_remind_timer.stop()

        # Restore direct hardware sink if EQ or Surround was running
        if ((hasattr(self, "eq_enable_cb") and self.eq_enable_cb.isChecked())
                or (hasattr(self, "surround_cb") and self.surround_cb.isChecked())):
            self.ctrl.eq.surround_enabled = False
            self.ctrl.enable_eq(False)

        # Sidetone is a mixer setting: it survives the app. Turn it off on the
        # way out so it cannot be left on with no UI around to switch it off.
        if self.side_exit_cb.isChecked() and not self.side_toggle.isChecked():
            self._apply_sidetone(0 if self.side_v2w_radio.isChecked() else "off")

        self.ctrl.disconnect()
        QApplication.instance().quit()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Virtuoso Control")

    # --- Control de Instancia Única ---
    socket_name = "VirtuosoControl_SingleInstance_Socket"
    socket = QLocalSocket()
    socket.connectToServer(socket_name)

    # Si se conecta con éxito, ya hay otra instancia ejecutándose
    if socket.waitForConnected(500):
        apply_theme(app)
        msg = QMessageBox()
        msg.setIcon(QMessageBox.Icon.Warning)
        msg.setWindowTitle("Virtuoso Control")
        msg.setText(_tr("The application is already running."))
        msg.setInformativeText(_tr("Virtuoso Control is already running in the background or in the system tray."))
        msg.setStandardButtons(QMessageBox.StandardButton.Ok)
        msg.exec()
        sys.exit(0)

    # Si no hay otra instancia, limpiar posible socket huérfano y escuchar
    QLocalServer.removeServer(socket_name)
    app.local_server = QLocalServer()
    app.local_server.listen(socket_name)
    # ----------------------------------

    app.setQuitOnLastWindowClosed(False)
    app.setDesktopFileName("virtuoso-control")
    apply_theme(app)   # follows the desktop's light/dark preference

    gui = VirtuosoGUI()
    
    s = QSettings("AlejandroSocas", "VirtuosoControl")
    if not s.value("start_minimized", False, type=bool):
        gui.show()

    app.aboutToQuit.connect(gui.quit_app)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
