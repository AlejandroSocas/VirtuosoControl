#!/usr/bin/env python3
"""
Virtuoso GUI — Interfaz gráfica para Corsair Virtuoso SE

Features:
- Conexión HID persistente (como iCUE)
- Control de LED del micrófono con keep-alive
- Sidetone (ALSA / V2W HID)
- Volumen (ALSA)
- Monitorización de batería con notificaciones
- Acciones rápidas desde el tray
- Persistencia de preferencias entre sesiones
- Reconexión automática
"""
import sys
import os
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QPushButton, QSlider, QLabel,
                             QGroupBox, QSystemTrayIcon, QMenu, QCheckBox,
                             QRadioButton, QButtonGroup, QColorDialog)
from PyQt6.QtCore import Qt, QTimer, QSettings
from PyQt6.QtGui import QIcon, QAction, QColor
from virtuoso_control import VirtuosoController


class VirtuosoGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.ctrl = VirtuosoController()
        self._hid_connected = False
        self._low_battery_notified = False

        # Icono de la app
        icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "virtuoso_icon.png")
        if os.path.exists(icon_path):
            self.app_icon = QIcon(icon_path)
        else:
            self.app_icon = QIcon.fromTheme("audio-headset", QIcon.fromTheme("video-display"))
        self.setWindowIcon(self.app_icon)

        self.init_ui()
        self._load_settings()   # Cargar preferencias (señales bloqueadas)
        self.init_tray()        # Tray con acciones rápidas

        # Timer: keep-alive del LED (cada 20s)
        self.keep_alive_timer = QTimer()
        self.keep_alive_timer.timeout.connect(self.do_keep_alive)

        # Timer: reconexión automática (cada 5s)
        self.reconnect_timer = QTimer()
        self.reconnect_timer.timeout.connect(self.try_reconnect)

        # Timer: batería automática (cada 5 min)
        self.battery_timer = QTimer()
        self.battery_timer.timeout.connect(self._auto_battery_check)
        self.battery_timer.start(300_000)

        # Conectar y aplicar preferencias guardadas
        self._try_initial_connect()
        self._apply_saved_settings()

    # ─── Interfaz ────────────────────────────────────────────────────

    def init_ui(self):
        self.setWindowTitle("Virtuoso Control")
        self.setFixedSize(340, 700)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        # --- Estado + Batería ---
        status_row = QHBoxLayout()
        self.status_label = QLabel("⏳ Conectando...")
        self.status_label.setStyleSheet("font-weight: bold; padding: 4px;")
        
        self.refresh_conn_btn = QPushButton("↻")
        self.refresh_conn_btn.setFixedSize(30, 26)
        self.refresh_conn_btn.setToolTip("Forzar detección de auriculares")
        self.refresh_conn_btn.clicked.connect(self.force_reconnect)
        
        status_row.addWidget(self.status_label)
        status_row.addStretch()
        status_row.addWidget(self.refresh_conn_btn)
        layout.addLayout(status_row)

        batt_row = QHBoxLayout()
        self.batt_label = QLabel("🔋 Batería: --")
        self.batt_btn = QPushButton("Actualizar")
        self.batt_btn.setFixedWidth(80)
        self.batt_btn.clicked.connect(self.check_battery)
        batt_row.addWidget(self.batt_label)
        batt_row.addStretch()
        batt_row.addWidget(self.batt_btn)
        layout.addLayout(batt_row)

        # --- Micrófono ---
        mic_group = QGroupBox("Micrófono")
        mic_lay = QVBoxLayout()
        
        mic_color_row = QHBoxLayout()
        self.mic_color_btn = QPushButton("Elegir Color")
        self.mic_color_btn.clicked.connect(self.choose_mic_color)
        self.mic_color_preview = QLabel(" ")
        self.mic_color_preview.setFixedSize(30, 20)
        self.mic_color_preview.setStyleSheet("background-color: #ff0000; border: 1px solid black;")
        mic_color_row.addWidget(QLabel("Color:"))
        mic_color_row.addWidget(self.mic_color_preview)
        mic_color_row.addWidget(self.mic_color_btn)
        
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
        mic_lay.addWidget(QLabel("Brillo:"))
        mic_lay.addLayout(mic_slider_row)
        mic_group.setLayout(mic_lay)
        layout.addWidget(mic_group)

        # --- Iluminación RGB ---
        rgb_group = QGroupBox("Iluminación RGB")
        rgb_lay = QVBoxLayout()
        
        rgb_color_row = QHBoxLayout()
        self.rgb_color_btn = QPushButton("Elegir Color")
        self.rgb_color_btn.clicked.connect(self.choose_color)
        self.rgb_color_preview = QLabel(" ")
        self.rgb_color_preview.setFixedSize(30, 20)
        self.rgb_color_preview.setStyleSheet("background-color: #ff0000; border: 1px solid black;")
        rgb_color_row.addWidget(QLabel("Color:"))
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
        rgb_lay.addWidget(QLabel("Brillo:"))
        rgb_lay.addLayout(rgb_slider_row)
        rgb_group.setLayout(rgb_lay)
        layout.addWidget(rgb_group)

        # --- Sidetone ---
        side_group = QGroupBox("Sidetone (Retorno de voz)")
        side_lay = QVBoxLayout()

        method_row = QHBoxLayout()
        self.side_method_group = QButtonGroup()
        self.side_alsa_radio = QRadioButton("ALSA")
        self.side_v2w_radio = QRadioButton("V2W HID")
        self.side_alsa_radio.setChecked(True)
        self.side_method_group.addButton(self.side_alsa_radio)
        self.side_method_group.addButton(self.side_v2w_radio)
        self.side_alsa_radio.toggled.connect(self._save_settings)
        method_row.addWidget(QLabel("Método:"))
        method_row.addWidget(self.side_alsa_radio)
        method_row.addWidget(self.side_v2w_radio)
        method_row.addStretch()
        side_lay.addLayout(method_row)

        self.side_toggle = QPushButton("Desactivar Sidetone")
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

        side_lay.addWidget(self.side_toggle)
        side_lay.addWidget(QLabel("Nivel:"))
        side_lay.addLayout(side_slider_row)
        side_group.setLayout(side_lay)
        layout.addWidget(side_group)

        # --- Volumen ---
        vol_group = QGroupBox("Volumen")
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

    # ─── Tray con acciones rápidas ───────────────────────────────────

    def init_tray(self):
        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(self.app_icon)
        self.tray_icon.setToolTip("Virtuoso Control")

        menu = QMenu()



        # Toggle Sidetone (sincronizado con botón principal)
        self.tray_side_action = QAction("🔊 Sidetone: Activado", self)
        self.tray_side_action.setCheckable(True)
        self.tray_side_action.setChecked(self.side_toggle.isChecked())
        self.tray_side_action.triggered.connect(self._tray_toggle_sidetone)
        menu.addAction(self.tray_side_action)

        menu.addSeparator()

        # Batería (click para actualizar)
        self.tray_batt_action = QAction("🔋 Batería: --", self)
        self.tray_batt_action.triggered.connect(self.check_battery)
        menu.addAction(self.tray_batt_action)

        menu.addSeparator()

        open_action = QAction("Abrir", self)
        open_action.triggered.connect(self.show)
        menu.addAction(open_action)

        exit_action = QAction("Salir", self)
        exit_action.triggered.connect(self.quit_app)
        menu.addAction(exit_action)

        self.tray_icon.setContextMenu(menu)
        self.tray_icon.activated.connect(self.tray_icon_activated)
        self.tray_icon.show()

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
            "🔇 Sidetone: Desactivado" if muted else "🔊 Sidetone: Activado")

    # ─── Persistencia de preferencias ────────────────────────────────

    def _load_settings(self):
        """Carga preferencias guardadas. Bloquea señales para evitar
        que se apliquen comandos antes de que la conexión esté lista."""
        s = QSettings("VirtuosoControl", "VirtuosoControl")

        # Bloquear señales durante la carga
        for w in (self.side_toggle,
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
            self.side_toggle.setText("Activar Sidetone")
        else:
            self.side_toggle.setText("Desactivar Sidetone")

        # Desbloquear señales
        for w in (self.side_toggle,
                  self.side_slider, self.side_alsa_radio,
                  self.side_v2w_radio, self.vol_slider, self.rgb_slider, self.mic_slider):
            w.blockSignals(False)

    def _save_settings(self):
        """Guarda las preferencias actuales."""
        s = QSettings("VirtuosoControl", "VirtuosoControl")
        s.setValue("mic_color", self._current_mic_color.name())
        s.setValue("mic_brightness", self.mic_slider.value())

        s.setValue("sidetone_muted", self.side_toggle.isChecked())
        s.setValue("sidetone_level", self.side_slider.value())
        s.setValue("sidetone_v2w", self.side_v2w_radio.isChecked())
        s.setValue("volume", self.vol_slider.value())
        s.setValue("rgb_color", self._current_color.name())
        s.setValue("rgb_brightness", self.rgb_slider.value())

    def _apply_saved_settings(self):
        """Aplica las preferencias cargadas al hardware.
        Se llama después de _try_initial_connect()."""
        self.keep_alive_timer.start(20_000)



        # Sidetone: aplicar nivel o mute
        if self.side_toggle.isChecked():
            if self.side_v2w_radio.isChecked():
                self._apply_sidetone(0)
            else:
                self._apply_sidetone("off")
        else:
            self._apply_sidetone(self.side_slider.value())

        # Volumen
        self.ctrl.set_volume(self.vol_slider.value())
        
        # RGB
        self.apply_rgb()

    # ─── Conexión HID ────────────────────────────────────────────────

    def _try_initial_connect(self):
        """Conecta al HID sin handshake. El handshake se hace lazy."""
        if self.ctrl.connect():
            self._hid_connected = True
            self._update_status(True)
        else:
            self._hid_connected = False
            self._update_status(False)
            self.reconnect_timer.start(5000)

    def _update_status(self, connected):
        if connected:
            self.status_label.setText(f"🟢 Conectado — {self.ctrl.connection_mode}")
            self.status_label.setStyleSheet(
                "font-weight: bold; padding: 4px; color: #2ecc71;")
                
            # Desactivar controles V2W si estamos por cable
            is_wired = "Por Cable" in self.ctrl.connection_mode
            self.batt_btn.setDisabled(is_wired)
            self.mic_color_btn.setDisabled(is_wired)
            self.mic_slider.setDisabled(is_wired)
            self.rgb_color_btn.setDisabled(is_wired)
            self.rgb_slider.setDisabled(is_wired)
            
            if is_wired:
                self.batt_label.setText("🔋 Batería: N/A (Cable)")
                self.tray_batt_action.setText("🔋 Batería: N/A (Cable)")
                
        else:
            self.status_label.setText("🔴 Desconectado — Buscando dispositivo...")
            self.status_label.setStyleSheet(
                "font-weight: bold; padding: 4px; color: #e74c3c;")
            self.batt_btn.setDisabled(True)
            self.mic_color_btn.setDisabled(True)
            self.mic_slider.setDisabled(True)
            self.rgb_color_btn.setDisabled(True)
            self.rgb_slider.setDisabled(True)

    def _on_connection_lost(self):
        self._hid_connected = False
        self._update_status(False)
        self.keep_alive_timer.stop()
        if not self.reconnect_timer.isActive():
            self.reconnect_timer.start(5000)

    def force_reconnect(self):
        self.status_label.setText("⏳ Buscando dispositivo...")
        self.status_label.setStyleSheet("font-weight: bold; padding: 4px; color: #f39c12;")
        # Usamos singleShot para permitir que la interfaz se pinte antes del bloqueo
        QTimer.singleShot(50, self.try_reconnect)

    def try_reconnect(self):
        if self.ctrl.reconnect():
            self._hid_connected = True
            self._update_status(True)
            self.reconnect_timer.stop()
            self.keep_alive_timer.start(20_000)
            self.apply_rgb()

    # ─── LED del micrófono ─────────────────────────────────────────



    def do_keep_alive(self):
        if not self._hid_connected:
            return
        if not self.ctrl.send_heartbeat():
            self._on_connection_lost()
            return
        # Mantenemos viva la sesión RGB
        self.apply_rgb()

    # ─── Iluminación RGB ─────────────────────────────────────────────

    def choose_color(self):
        color = QColorDialog.getColor(self._current_color, self, "Elegir Color del Logo")
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

    def choose_mic_color(self):
        color = QColorDialog.getColor(self._current_mic_color, self, "Elegir Color del Micrófono")
        if color.isValid():
            self._current_mic_color = color
            self.mic_color_preview.setStyleSheet(f"background-color: {color.name()}; border: 1px solid black;")
            self.apply_rgb()
            self._save_settings()

    def apply_rgb(self):
        if self._hid_connected:
            # Bateria LED state calculation
            batt_r, batt_g, batt_b = 0, 0, 0
            if hasattr(self, '_last_battery_percent'):
                p = self._last_battery_percent
                if p >= 80:
                    batt_g = 255
                elif p >= 20:
                    batt_r, batt_g = 255, 255
                else:
                    batt_r = 255

            self.ctrl.set_all_rgb(
                (self._current_color.red(), self._current_color.green(), self._current_color.blue()), 
                self.rgb_slider.value(),
                (self._current_mic_color.red(), self._current_mic_color.green(), self._current_mic_color.blue()),
                self.mic_slider.value(),
                (batt_r, batt_g, batt_b),
                100
            )

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
            self.side_toggle.setText("Activar Sidetone")
        else:
            self._apply_sidetone(self.side_slider.value())
            self.side_toggle.setText("Desactivar Sidetone")

        self._sync_tray_sidetone()
        self._save_settings()

    def change_sidetone(self, value):
        self.side_label.setText(f"{value}%")
        if not self.side_toggle.isChecked():
            self._apply_sidetone(value)
        self._save_settings()

    # ─── Volumen ─────────────────────────────────────────────────────

    def change_volume(self, value):
        self.vol_label.setText(f"{value}%")
        self.ctrl.set_volume(value)
        self._save_settings()

    # ─── Batería ─────────────────────────────────────────────────────

    def check_battery(self):
        """Consulta la batería y actualiza UI + tray."""
        if not self._hid_connected:
            self._update_battery_display("No conectado")
            return
        battery_str = self.ctrl.get_battery()
        self._update_battery_display(battery_str)

        # Notificación de batería baja
        self._check_low_battery(battery_str)

    def _auto_battery_check(self):
        """Auto-check periódico. Solo si el handshake ya se hizo
        (para no disparar el handshake y apagar el LED sin querer)."""
        if self._hid_connected and self.ctrl._handshake_done:
            battery_str = self.ctrl.get_battery()
            self._update_battery_display(battery_str)
            self._check_low_battery(battery_str)

    def _update_battery_display(self, battery_str):
        """Actualiza el nivel de batería en ventana, tray tooltip y menú."""
        self.batt_label.setText(f"🔋 Batería: {battery_str}")
        self.tray_batt_action.setText(f"🔋 Batería: {battery_str}")
        self.tray_icon.setToolTip(f"Virtuoso Control — {battery_str}")

    def _check_low_battery(self, battery_str):
        """Muestra notificación de escritorio si la batería < 15%."""
        try:
            percent = int(battery_str.split("%")[0])
            self._last_battery_percent = percent
            self.apply_rgb()  # Update LED battery state
        except (ValueError, IndexError):
            return

        if percent < 15 and not self._low_battery_notified:
            self.tray_icon.showMessage(
                "⚠️ Batería baja — Virtuoso SE",
                f"Los auriculares tienen {percent}% de batería.",
                QSystemTrayIcon.MessageIcon.Warning,
                10_000)
            self._low_battery_notified = True
        elif percent >= 20:
            # Resetear flag cuando vuelve a subir (ej: cargando)
            self._low_battery_notified = False

    # ─── Ciclo de vida ───────────────────────────────────────────────

    def closeEvent(self, event):
        event.ignore()
        self.hide()

    def quit_app(self):
        self._save_settings()
        self.keep_alive_timer.stop()
        self.reconnect_timer.stop()
        self.battery_timer.stop()
        self.ctrl.disconnect()
        QApplication.instance().quit()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setDesktopFileName("virtuoso-control")

    gui = VirtuosoGUI()
    gui.show()

    app.aboutToQuit.connect(gui.quit_app)
    sys.exit(app.exec())
