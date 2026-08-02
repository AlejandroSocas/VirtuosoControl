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
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QPushButton, QSlider, QLabel,
                             QGroupBox, QSystemTrayIcon, QMenu, QCheckBox,
                             QRadioButton, QButtonGroup, QColorDialog, QDialog, QComboBox)
from PyQt6.QtCore import Qt, QTimer, QSettings
from PyQt6.QtGui import QIcon, QAction, QColor, QPixmap, QPainter, QBrush, QPen
from virtuoso_control import VirtuosoController

TRANSLATIONS = {
    "es": {
        "Settings": "Configuración",
        "Start automatically with Linux": "Iniciar automáticamente con Linux",
        "Start minimized in system tray": "Iniciar minimizado en la bandeja",
        "Language (requires restart):": "Idioma (requiere reinicio):",
        "Save": "Guardar",
        "Cancel": "Cancelar",
        "Connecting...": "Conectando...",
        "Battery": "Batería",
        "Refresh": "Actualizar",
        "Microphone": "Micrófono",
        "Pick Color": "Elegir Color",
        "Color:": "Color:",
        "Brightness:": "Brillo:",
        "RGB Lighting": "Iluminación RGB",
        "Sidetone": "Sidetone",
        "Method:": "Método:",
        "Disable Sidetone": "Desactivar Sidetone",
        "Enable Sidetone": "Activar Sidetone",
        "Level:": "Nivel:",
        "Volume": "Volumen",
        "Profile 1": "Perfil 1",
        "Profile 2": "Perfil 2",
        "Profile 3": "Perfil 3",
        "Save 1": "Guardar 1",
        "Save 2": "Guardar 2",
        "Save 3": "Guardar 3",
        "🎨 RGB Profiles": "🎨 Perfiles RGB",
        "Load Profile 1": "Cargar Perfil 1",
        "Load Profile 2": "Cargar Perfil 2",
        "Load Profile 3": "Cargar Perfil 3",
        "Open": "Abrir",
        "Quit": "Salir",
        "Connected": "Conectado",
        "Disconnected": "Desconectado",
        "Searching for device...": "Buscando dispositivo...",
        "Wired": "Por cable",
        "Not connected": "No conectado",
        "Charging": "Cargando",
        "Discharging": "Descargando",
        "Handshake error": "Error de conexión",
        "Read error": "Error de lectura",
        "No response (try again)": "Sin respuesta",
        "Pick Logo Color": "Elegir Color del Logo",
        "Pick Microphone Color": "Elegir Color del Micrófono",
        "🔊 Sidetone: Enabled": "🔊 Sidetone: Activado",
        "🔇 Sidetone: Disabled": "🔇 Sidetone: Desactivado",
        "🎤 Mic: Active": "🎤 Micrófono: Activo",
        "🎤 Mic: Muted": "🎤 Micrófono: Silenciado",
        "Muted color:": "Color silenciado:",
        "Pick Mute Color": "Elegir Color Silenciado",
        "Mute feedback sound": "Sonido al silenciar",
        "Turn off when closing the app": "Apagar al cerrar la aplicación",
        "Tray icon:": "Icono de bandeja:",
        "Virtuoso icon": "Icono de Virtuoso",
        "Battery icon": "Icono de batería",
        "Both": "Ambos",
        "Profile Saved": "Perfil Guardado",
        "Error": "Error",
        "⚠️ Low Battery — Virtuoso SE": "⚠️ Batería Baja — Virtuoso SE",
    }
}

def _tr(text):
    s = QSettings("AlejandroSocas", "VirtuosoControl")
    lang = s.value("language", "en", type=str)
    if lang == "es" and text in TRANSLATIONS["es"]:
        return TRANSLATIONS["es"][text]
    return text

class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(_tr("Settings"))
        self.setFixedSize(300, 230)
        
        layout = QVBoxLayout(self)
        
        self.autostart_cb = QCheckBox(_tr("Start automatically with Linux"))
        self.minimized_cb = QCheckBox(_tr("Start minimized in system tray"))
        tray_layout = QHBoxLayout()
        tray_layout.addWidget(QLabel(_tr("Tray icon:")))
        self.tray_mode_combo = QComboBox()
        self.tray_mode_combo.addItem(_tr("Virtuoso icon"), "virtuoso")
        self.tray_mode_combo.addItem(_tr("Battery icon"), "battery")
        self.tray_mode_combo.addItem(_tr("Both"), "both")
        tray_layout.addWidget(self.tray_mode_combo)
        
        lang_layout = QHBoxLayout()
        lang_layout.addWidget(QLabel(_tr("Language (requires restart):")))
        self.lang_combo = QComboBox()
        self.lang_combo.addItem("English", "en")
        self.lang_combo.addItem("Español", "es")
        lang_layout.addWidget(self.lang_combo)
        
        layout.addWidget(self.autostart_cb)
        layout.addWidget(self.minimized_cb)
        layout.addLayout(tray_layout)
        layout.addLayout(lang_layout)
        layout.addStretch()
        
        btn_layout = QHBoxLayout()
        save_btn = QPushButton(_tr("Save"))
        save_btn.clicked.connect(self.save_and_close)
        cancel_btn = QPushButton(_tr("Cancel"))
        cancel_btn.clicked.connect(self.reject)
        
        btn_layout.addStretch()
        btn_layout.addWidget(cancel_btn)
        btn_layout.addWidget(save_btn)
        layout.addLayout(btn_layout)
        
        self.load_settings()

    def load_settings(self):
        s = QSettings("AlejandroSocas", "VirtuosoControl")
        self.minimized_cb.setChecked(s.value("start_minimized", False, type=bool))
        mode = s.value("tray_icon_mode", "virtuoso", type=str)
        idx = self.tray_mode_combo.findData(mode)
        if idx >= 0:
            self.tray_mode_combo.setCurrentIndex(idx)
        
        lang = s.value("language", "en", type=str)
        index = self.lang_combo.findData(lang)
        if index >= 0:
            self.lang_combo.setCurrentIndex(index)
            
        autostart_path = os.path.expanduser("~/.config/autostart/virtuoso-control.desktop")
        self.autostart_cb.setChecked(os.path.exists(autostart_path))

    def save_and_close(self):
        s = QSettings("AlejandroSocas", "VirtuosoControl")
        s.setValue("start_minimized", self.minimized_cb.isChecked())
        s.setValue("language", self.lang_combo.currentData())
        s.setValue("tray_icon_mode", self.tray_mode_combo.currentData())
        
        autostart_dir = os.path.expanduser("~/.config/autostart")
        autostart_path = os.path.join(autostart_dir, "virtuoso-control.desktop")
        
        if self.autostart_cb.isChecked():
            if not os.path.exists(autostart_dir):
                os.makedirs(autostart_dir)
            desktop_content = "[Desktop Entry]\nName=Virtuoso Control\nComment=Panel de control nativo para los auriculares Corsair Virtuoso SE\nExec=python3 /opt/virtuoso-control/virtuoso_gui.py\nIcon=/opt/virtuoso-control/virtuoso_icon.png\nTerminal=false\nType=Application\nCategories=Audio;Settings;HardwareSettings;\nStartupWMClass=virtuoso-control\n"
            with open(autostart_path, "w") as f:
                f.write(desktop_content)
        else:
            if os.path.exists(autostart_path):
                os.remove(autostart_path)
                
        self.accept()

class VirtuosoGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.ctrl = VirtuosoController()
        self._hid_connected = False
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
        self._load_settings()   # Load preferences (signals blocked)
        self.init_tray()        # Tray with quick actions

        # Timer: LED keep-alive (every 20s)
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
        dlg = SettingsDialog(self)
        if dlg.exec():
            self._setup_tray_icons()  # applies without a restart

    def init_ui(self):
        self.setWindowTitle("Virtuoso Control")
        self.setWindowIcon(QIcon(self.icon_path))
        self.setFixedSize(340, 700)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        # --- Status + Battery ---
        status_row = QHBoxLayout()
        self.status_label = QLabel(_tr("⏳ Connecting..."))
        self.status_label.setStyleSheet("font-weight: bold; padding: 4px;")
        
        self.refresh_conn_btn = QPushButton("↻")
        self.refresh_conn_btn.setFixedSize(30, 26)
        self.refresh_conn_btn.setToolTip(_tr("Refresh"))
        self.refresh_conn_btn.clicked.connect(self.force_reconnect)
        
        self.settings_btn = QPushButton("⚙")
        self.settings_btn.setFixedSize(30, 26)
        self.settings_btn.setToolTip(_tr("Settings"))
        self.settings_btn.clicked.connect(self.open_settings)
        
        status_row.addWidget(self.status_label)
        status_row.addStretch()
        status_row.addWidget(self.refresh_conn_btn)
        status_row.addWidget(self.settings_btn)
        layout.addLayout(status_row)

        batt_row = QHBoxLayout()
        self.batt_label = QLabel(_tr("🔋 Battery: --"))
        self.batt_btn = QPushButton(_tr("Refresh"))
        self.batt_btn.setFixedWidth(80)
        self.batt_btn.clicked.connect(self.check_battery)
        batt_row.addWidget(self.batt_label)
        batt_row.addStretch()
        batt_row.addWidget(self.batt_btn)
        layout.addLayout(batt_row)

        # --- Microphone ---
        mic_group = QGroupBox(_tr("Microphone"))
        mic_lay = QVBoxLayout()

        mic_color_row = QHBoxLayout()
        self.mic_color_btn = QPushButton(_tr("Pick Color"))
        self.mic_color_btn.clicked.connect(self.choose_mic_color)
        self.mic_color_preview = QLabel(" ")
        self.mic_color_preview.setFixedSize(30, 20)
        self.mic_color_preview.setStyleSheet("background-color: #ff0000; border: 1px solid black;")
        mic_color_row.addWidget(QLabel(_tr("Color:")))
        mic_color_row.addWidget(self.mic_color_preview)
        mic_color_row.addWidget(self.mic_color_btn)
        
        # Colour the mic LED takes while muted (red on Windows, but yours).
        mic_mute_color_row = QHBoxLayout()
        self.mic_mute_color_btn = QPushButton(_tr("Pick Mute Color"))
        self.mic_mute_color_btn.clicked.connect(self.choose_mic_mute_color)
        self.mic_mute_color_preview = QLabel(" ")
        self.mic_mute_color_preview.setFixedSize(30, 20)
        self.mic_mute_color_preview.setStyleSheet(
            "background-color: #ff0000; border: 1px solid black;")
        mic_mute_color_row.addWidget(QLabel(_tr("Muted color:")))
        mic_mute_color_row.addWidget(self.mic_mute_color_preview)
        mic_mute_color_row.addWidget(self.mic_mute_color_btn)
        mic_lay.addLayout(mic_mute_color_row)

        self.mic_tone_cb = QCheckBox(_tr("Mute feedback sound"))
        self.mic_tone_cb.setChecked(True)
        self.mic_tone_cb.toggled.connect(self._save_settings)
        mic_lay.addWidget(self.mic_tone_cb)

        mic_slider_row = QHBoxLayout()
        self.mic_slider = QSlider(Qt.Orientation.Horizontal)
        self.mic_slider.setRange(0, 100)
        self.mic_slider.setValue(100)
        self.mic_slider.valueChanged.connect(self.change_rgb)
        self.mic_label = QLabel("100%")
        self.mic_label.setFixedWidth(40)
        mic_slider_row.addWidget(self.mic_slider)
        mic_slider_row.addWidget(self.mic_label)
        
        mic_lay.addLayout(mic_color_row)
        mic_lay.addWidget(QLabel(_tr("Brightness:")))
        mic_lay.addLayout(mic_slider_row)
        mic_group.setLayout(mic_lay)
        layout.addWidget(mic_group)

        # --- RGB Lighting ---
        rgb_group = QGroupBox(_tr("RGB Lighting"))
        rgb_lay = QVBoxLayout()
        
        rgb_color_row = QHBoxLayout()
        self.rgb_color_btn = QPushButton(_tr("Pick Color"))
        self.rgb_color_btn.clicked.connect(self.choose_color)
        self.rgb_color_preview = QLabel(" ")
        self.rgb_color_preview.setFixedSize(30, 20)
        self.rgb_color_preview.setStyleSheet("background-color: #ff0000; border: 1px solid black;")
        rgb_color_row.addWidget(QLabel(_tr("Color:")))
        rgb_color_row.addWidget(self.rgb_color_preview)
        rgb_color_row.addWidget(self.rgb_color_btn)
        
        rgb_slider_row = QHBoxLayout()
        self.rgb_slider = QSlider(Qt.Orientation.Horizontal)
        self.rgb_slider.setRange(0, 100)
        self.rgb_slider.setValue(100)
        self.rgb_slider.valueChanged.connect(self.change_rgb)
        self.rgb_label = QLabel("100%")
        self.rgb_label.setFixedWidth(40)
        rgb_slider_row.addWidget(self.rgb_slider)
        rgb_slider_row.addWidget(self.rgb_label)
        
        rgb_lay.addLayout(rgb_color_row)
        rgb_lay.addWidget(QLabel(_tr("Brightness:")))
        rgb_lay.addLayout(rgb_slider_row)
        
        # Profiles
        profile_row = QHBoxLayout()
        self.profile_btn_1 = QPushButton(_tr("Profile 1"))
        self.profile_btn_2 = QPushButton(_tr("Profile 2"))
        self.profile_btn_3 = QPushButton(_tr("Profile 3"))
        self.profile_btn_1.clicked.connect(lambda: self.load_profile(1))
        self.profile_btn_2.clicked.connect(lambda: self.load_profile(2))
        self.profile_btn_3.clicked.connect(lambda: self.load_profile(3))
        profile_row.addWidget(self.profile_btn_1)
        profile_row.addWidget(self.profile_btn_2)
        profile_row.addWidget(self.profile_btn_3)
        
        save_profile_row = QHBoxLayout()
        self.save_p1 = QPushButton(_tr("Save 1"))
        self.save_p2 = QPushButton(_tr("Save 2"))
        self.save_p3 = QPushButton(_tr("Save 3"))
        self.save_p1.clicked.connect(lambda: self.save_profile(1))
        self.save_p2.clicked.connect(lambda: self.save_profile(2))
        self.save_p3.clicked.connect(lambda: self.save_profile(3))
        save_profile_row.addWidget(self.save_p1)
        save_profile_row.addWidget(self.save_p2)
        save_profile_row.addWidget(self.save_p3)

        rgb_lay.addLayout(profile_row)
        rgb_lay.addLayout(save_profile_row)

        rgb_group.setLayout(rgb_lay)
        layout.addWidget(rgb_group)

        # --- Sidetone ---
        side_group = QGroupBox(_tr("Sidetone"))
        side_lay = QVBoxLayout()

        method_row = QHBoxLayout()
        self.side_method_group = QButtonGroup()
        self.side_alsa_radio = QRadioButton("ALSA")
        self.side_v2w_radio = QRadioButton("V2W HID")
        self.side_alsa_radio.setChecked(True)
        self.side_method_group.addButton(self.side_alsa_radio)
        self.side_method_group.addButton(self.side_v2w_radio)
        self.side_alsa_radio.toggled.connect(self._save_settings)
        method_row.addWidget(QLabel(_tr("Method:")))
        method_row.addWidget(self.side_alsa_radio)
        method_row.addWidget(self.side_v2w_radio)
        method_row.addStretch()
        side_lay.addLayout(method_row)

        self.side_toggle = QPushButton(_tr("Disable Sidetone"))
        self.side_toggle.setCheckable(True)
        self.side_toggle.clicked.connect(self.toggle_sidetone)

        side_slider_row = QHBoxLayout()
        self.side_slider = QSlider(Qt.Orientation.Horizontal)
        self.side_slider.setRange(0, 100)
        self.side_slider.setValue(70)
        self.side_slider.valueChanged.connect(self.change_sidetone)
        self.side_label = QLabel("70%")
        self.side_label.setFixedWidth(40)
        side_slider_row.addWidget(self.side_slider)
        side_slider_row.addWidget(self.side_label)

        self.side_exit_cb = QCheckBox(_tr("Turn off when closing the app"))
        self.side_exit_cb.setChecked(True)
        self.side_exit_cb.toggled.connect(self._save_settings)
        side_lay.addWidget(self.side_exit_cb)

        side_lay.addWidget(self.side_toggle)
        side_lay.addWidget(QLabel(_tr("Level:")))
        side_lay.addLayout(side_slider_row)
        side_group.setLayout(side_lay)
        layout.addWidget(side_group)

        # --- Volume ---
        vol_group = QGroupBox(_tr("Volume"))
        vol_lay = QHBoxLayout()
        self.vol_slider = QSlider(Qt.Orientation.Horizontal)
        self.vol_slider.setRange(0, 100)
        self.vol_slider.setValue(70)
        self.vol_slider.valueChanged.connect(self.change_volume)
        self.vol_label = QLabel("70%")
        self.vol_label.setFixedWidth(40)
        vol_lay.addWidget(self.vol_slider)
        vol_lay.addWidget(self.vol_label)
        vol_group.setLayout(vol_lay)
        layout.addWidget(vol_group)

        layout.addStretch()

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
                  self.side_v2w_radio, self.vol_slider, self.rgb_slider, self.mic_slider):
            w.blockSignals(True)

        

        self.side_toggle.setChecked(s.value("sidetone_muted", False, type=bool))
        self.side_slider.setValue(s.value("sidetone_level", 70, type=int))
        self.vol_slider.setValue(s.value("volume", 70, type=int))

        if s.value("sidetone_v2w", False, type=bool):
            self.side_v2w_radio.setChecked(True)
        else:
            self.side_alsa_radio.setChecked(True)

        color_hex = s.value("rgb_color", "#ff0000", type=str)
        self._current_color = QColor(color_hex)

        mute_hex = s.value("mic_mute_color", "#ff0000", type=str)
        self._mic_mute_color = QColor(mute_hex)
        self.mic_mute_color_preview.setStyleSheet(
            f"background-color: {mute_hex}; border: 1px solid black;")
        self.mic_tone_cb.setChecked(s.value("mic_tone", True, type=bool))
        self.side_exit_cb.setChecked(s.value("sidetone_off_on_exit", True, type=bool))

        mic_hex = s.value("mic_color", "#ff0000", type=str)
        self._current_mic_color = QColor(mic_hex)
        self.mic_slider.setValue(s.value("mic_brightness", 100, type=int))
        self.mic_color_preview.setStyleSheet(f"background-color: {mic_hex}; border: 1px solid black;")
        self.mic_label.setText(f"{self.mic_slider.value()}%")
        self.rgb_slider.setValue(s.value("rgb_brightness", 100, type=int))
        self.rgb_color_preview.setStyleSheet(f"background-color: {color_hex}; border: 1px solid black;")
        self.rgb_label.setText(f"{self.rgb_slider.value()}%")

        # Actualizar labels

        self.side_label.setText(f"{self.side_slider.value()}%")
        self.vol_label.setText(f"{self.vol_slider.value()}%")
        if self.side_toggle.isChecked():
            self.side_toggle.setText(_tr("Enable Sidetone"))
        else:
            self.side_toggle.setText(_tr("Disable Sidetone"))

        # Desbloquear señales
        for w in (self.side_toggle, self.mic_tone_cb, self.side_exit_cb,
                  self.side_slider, self.side_alsa_radio,
                  self.side_v2w_radio, self.vol_slider, self.rgb_slider, self.mic_slider):
            w.blockSignals(False)

    def _save_settings(self):
        """Guarda las preferencias actuales."""
        s = QSettings("VirtuosoControl", "VirtuosoControl")
        s.setValue("mic_color", self._current_mic_color.name())
        s.setValue("mic_mute_color", self._mic_mute_color.name())
        s.setValue("mic_tone", self.mic_tone_cb.isChecked())
        s.setValue("sidetone_off_on_exit", self.side_exit_cb.isChecked())
        s.setValue("mic_brightness", self.mic_slider.value())

        s.setValue("sidetone_muted", self.side_toggle.isChecked())
        s.setValue("sidetone_level", self.side_slider.value())
        s.setValue("sidetone_v2w", self.side_v2w_radio.isChecked())
        s.setValue("volume", self.vol_slider.value())
        s.setValue("rgb_color", self._current_color.name())
        s.setValue("rgb_brightness", self.rgb_slider.value())

    def _reapply_all_settings(self):
        """Re-applies all saved settings to the hardware.
        Called on initial connect and after every successful reconnection."""
        # RGB
        self.apply_rgb()

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
        self.keep_alive_timer.start(20_000)
        self._sync_mic_from_pw()  # before RGB, so the LED starts correct
        self._reapply_all_settings()

    # ─── Conexión HID ────────────────────────────────────────────────

    def _try_initial_connect(self):
        """Conecta al HID sin handshake. El handshake se hace lazy."""
        if self.ctrl.connect():
            self._hid_connected = True
            self._update_status(True)
        else:
            self._hid_connected = False
            self._update_status(False)
            self.reconnect_timer.start(3000)

    def _update_status(self, connected):
        if connected:
            self.status_label.setText(f"🟢 {_tr('Connected')} — {_tr(self.ctrl.connection_mode)}")
            self.status_label.setStyleSheet(
                "font-weight: bold; padding: 4px; color: #2ecc71;")
                
            # Disable V2W controls if on wired mode
            is_wired = "Wired" in self.ctrl.connection_mode
            self.batt_btn.setDisabled(is_wired)
            self.mic_color_btn.setDisabled(is_wired)
            self.mic_slider.setDisabled(is_wired)
            self.rgb_color_btn.setDisabled(is_wired)
            self.rgb_slider.setDisabled(is_wired)
            
            if is_wired:
                self.batt_label.setText(_tr("🔋 Battery: N/A (Wired)"))
                self.tray_batt_action.setText(_tr("🔋 Battery: N/A (Wired)"))
                self._last_battery_percent = None
                self._refresh_battery_icon()

        else:
            self.status_label.setText(_tr("🔴 Disconnected — Searching for device..."))
            self.status_label.setStyleSheet(
                "font-weight: bold; padding: 4px; color: #e74c3c;")
            self.batt_btn.setDisabled(True)
            self.mic_color_btn.setDisabled(True)
            self.mic_slider.setDisabled(True)
            self.rgb_color_btn.setDisabled(True)
            self.rgb_slider.setDisabled(True)
            self._last_battery_percent = None
            self._refresh_battery_icon()

    def _on_connection_lost(self):
        self._hid_connected = False
        self._update_status(False)
        self.keep_alive_timer.stop()
        if not self.reconnect_timer.isActive():
            self.reconnect_timer.start(3000)

    def force_reconnect(self):
        self.status_label.setText(_tr("⏳ Connecting..."))
        self.status_label.setStyleSheet("font-weight: bold; padding: 4px; color: #f39c12;")
        # Use singleShot to allow UI to paint before blocking
        QTimer.singleShot(50, self.try_reconnect)

    def try_reconnect(self):
        if self.ctrl.reconnect() and self.ctrl.is_headset_alive():
            self._hid_connected = True
            self._update_status(True)
            self.reconnect_timer.stop()
            self.keep_alive_timer.start(20_000)
            self._reapply_all_settings()
            self._initial_battery_check()

    # ─── LED del micrófono ─────────────────────────────────────────



    def do_keep_alive(self):
        if not self._hid_connected:
            return
        # Check if the headset is actually responding (not just the dongle)
        if not self.ctrl.is_headset_alive():
            self._on_connection_lost()
            return
        # Headset is alive — send heartbeat and refresh RGB
        self.ctrl.send_heartbeat()
        self.apply_rgb()

    # ─── Iluminación RGB ─────────────────────────────────────────────

    def choose_color(self):
        color = QColorDialog.getColor(self._current_color, self, _tr("Pick Logo Color"))
        if color.isValid():
            self._current_color = color
            self.rgb_color_preview.setStyleSheet(f"background-color: {color.name()}; border: 1px solid black;")
            self.apply_rgb()
            self._save_settings()

    def change_rgb(self, value):
        self.rgb_label.setText(f"{self.rgb_slider.value()}%")
        self.mic_label.setText(f"{self.mic_slider.value()}%")
        self.apply_rgb()
        self._save_settings()

    def choose_mic_mute_color(self):
        color = QColorDialog.getColor(self._mic_mute_color, self,
                                      _tr("Pick Mute Color"))
        if color.isValid():
            self._mic_mute_color = color
            self.mic_mute_color_preview.setStyleSheet(
                f"background-color: {color.name()}; border: 1px solid black;")
            self.apply_rgb()  # visible immediately if currently muted
            self._save_settings()

    def choose_mic_color(self):
        color = QColorDialog.getColor(self._current_mic_color, self, _tr("Pick Microphone Color"))
        if color.isValid():
            self._current_mic_color = color
            self.mic_color_preview.setStyleSheet(f"background-color: {color.name()}; border: 1px solid black;")
            self.apply_rgb()
            self._save_settings()

    def apply_rgb(self):
        if self._hid_connected:
            # Battery LED state calculation
            batt_r, batt_g, batt_b = 0, 0, 0
            if self._last_battery_percent is not None:
                p = self._last_battery_percent
                if p >= 80:
                    batt_g = 255
                elif p >= 20:
                    batt_r, batt_g = 255, 255
                else:
                    batt_r = 255

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

            self.ctrl.set_all_rgb(
                (self._current_color.red(), self._current_color.green(), self._current_color.blue()),
                self.rgb_slider.value(),
                mic_rgb,
                mic_brightness,
                (batt_r, batt_g, batt_b),
                100
            )

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
        
        self.rgb_color_preview.setStyleSheet(f"background-color: {self._current_color.name()}; border: 1px solid black;")
        self.mic_color_preview.setStyleSheet(f"background-color: {self._current_mic_color.name()}; border: 1px solid black;")
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
            self.ctrl.play_mic_tone(muted)

    def _sync_mic_status(self):
        """Updates the read-only mic indicator in the tray."""
        if hasattr(self, "tray_mic_status"):
            self.tray_mic_status.setText(
                _tr("🎤 Mic: Muted") if self._mic_muted else _tr("🎤 Mic: Active"))

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
        else:
            self._apply_sidetone(self.side_slider.value())
            self.side_toggle.setText(_tr("Disable Sidetone"))

        self._sync_tray_sidetone()
        self._save_settings()

    def change_sidetone(self, value):
        self.side_label.setText(f"{value}%")
        if not self.side_toggle.isChecked():
            self._apply_sidetone(value)
        self._save_settings()

    # ─── Volume ──────────────────────────────────────────────────────

    def change_volume(self, value):
        self.vol_label.setText(f"{value}%")
        self.ctrl.set_volume(value)
        self._save_settings()

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
            if self._last_battery_percent is not None and self._last_battery_percent < 15:
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
            color = QColor(52, 152, 219) # Blue
        elif percent > 50:
            color = QColor(46, 204, 113) # Green
        elif percent > 20:
            color = QColor(241, 196, 15) # Yellow
        else:
            color = QColor(231, 76, 60) # Red
            
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
        self.batt_label.setText(f"🔋 {_tr('Battery')}: {battery_str}")
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
        self._refresh_battery_icon()

    def _check_low_battery(self, battery_str):
        """Shows desktop notification if battery < 15%."""
        try:
            percent = int(battery_str.split("%")[0])
            self._last_battery_percent = percent
            self.apply_rgb()  # Update LED battery state
        except (ValueError, IndexError):
            return

        if percent < 15 and not self._low_battery_notified:
            self.tray_icon.showMessage(
                _tr("⚠️ Low Battery — Virtuoso SE"),
                _tr("The headset is at {}% battery.").format(percent),
                QSystemTrayIcon.MessageIcon.Warning,
                10_000)
            self._low_battery_notified = True
        elif percent >= 20:
            # Reset flag when it goes up (e.g., charging)
            self._low_battery_notified = False

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

        # Sidetone is a mixer setting: it survives the app. Turn it off on the
        # way out so it cannot be left on with no UI around to switch it off.
        if self.side_exit_cb.isChecked() and not self.side_toggle.isChecked():
            self._apply_sidetone(0 if self.side_v2w_radio.isChecked() else "off")

        self.ctrl.disconnect()
        QApplication.instance().quit()


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setDesktopFileName("virtuoso-control")

    gui = VirtuosoGUI()
    
    s = QSettings("AlejandroSocas", "VirtuosoControl")
    if not s.value("start_minimized", False, type=bool):
        gui.show()

    app.aboutToQuit.connect(gui.quit_app)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
