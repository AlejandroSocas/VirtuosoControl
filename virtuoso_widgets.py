"""Custom painted widgets, themed controls, and Settings dialog."""
import os
from PyQt6.QtWidgets import (QWidget, QAbstractButton, QComboBox, QFrame,
                             QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                             QCheckBox, QPushButton, QSizePolicy)
from PyQt6.QtCore import Qt, QSettings, QRectF, QPointF
from PyQt6.QtGui import QColor, QPainter, QBrush, QPen, QFont, QPolygonF

from virtuoso_i18n import _tr
from virtuoso_theme import THEME, BATT_LEVEL, c

# ─── Painted widgets ────────────────────────────────────────────────
#
# These draw themselves from THEME rather than from QSS, because QSS cannot
# express a rounded progress fill or a soft status glow.


class StatusDot(QWidget):
    """Small filled circle with a halo — the connection indicator."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(14, 14)
        self._color = c("text_muted")

    def set_color(self, color):
        self._color = QColor(color)
        self.update()

    def paintEvent(self, _event):
        pt = QPainter(self)
        pt.setRenderHint(QPainter.RenderHint.Antialiasing)
        halo = QColor(self._color)
        halo.setAlpha(60)
        pt.setPen(Qt.PenStyle.NoPen)
        pt.setBrush(QBrush(halo))
        pt.drawEllipse(QRectF(0, 0, 14, 14))
        pt.setBrush(QBrush(self._color))
        pt.drawEllipse(QRectF(3.5, 3.5, 7, 7))
        pt.end()


class ColorSwatch(QAbstractButton):
    """Clickable colour chip. Replaces the old preview-label + button pair."""

    def __init__(self, color="#ff0000", parent=None):
        super().__init__(parent)
        self._color = QColor(color)
        self.setFixedSize(38, 24)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def color(self):
        return QColor(self._color)

    def set_color(self, color):
        self._color = QColor(color)
        self.update()

    def paintEvent(self, _event):
        pt = QPainter(self)
        pt.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)

        fill = QColor(self._color)
        if not self.isEnabled():
            fill.setAlpha(70)
        pt.setBrush(QBrush(fill))

        edge = c("accent") if (self.underMouse() and self.isEnabled()) else c("border")
        pen = QPen(edge)
        pen.setWidthF(1.5)
        pt.setPen(pen)
        pt.drawRoundedRect(rect, 7, 7)
        pt.end()

    def enterEvent(self, event):
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.update()


class BatteryGauge(QWidget):
    """Rounded capacity bar. Colour tracks charge level / charging state."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(8)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._percent = None
        self._charging = False

    def set_state(self, percent, charging):
        self._percent = percent
        self._charging = charging
        self.update()

    def level_color(self):
        if self._percent is None:
            return c("text_muted")
        if self._charging:
            return c("accent")
        if self._percent >= BATT_LEVEL["HIGH"]:
            return c("ok")
        if self._percent >= BATT_LEVEL["MEDIUM"]:
            return c("good")
        if self._percent >= BATT_LEVEL["LOW"]:
            return c("warn")
        return c("danger")

    def paintEvent(self, _event):
        pt = QPainter(self)
        pt.setRenderHint(QPainter.RenderHint.Antialiasing)
        h = self.height()
        r = h / 2

        pt.setPen(Qt.PenStyle.NoPen)
        pt.setBrush(QBrush(c("track")))
        pt.drawRoundedRect(QRectF(0, 0, self.width(), h), r, r)

        if self._percent:
            # Never narrower than the capsule is tall, so 1–4% still reads as
            # a dot rather than a sliver clipped away by the rounded corners.
            w = max(h, self.width() * min(self._percent, 100) / 100.0)
            pt.setBrush(QBrush(self.level_color()))
            pt.drawRoundedRect(QRectF(0, 0, w, h), r, r)
        pt.end()


class ThemedComboBox(QComboBox):
    """Combo box that paints its own chevron.

    Same trap as QCheckBox (see build_stylesheet): styling the combo at all
    hands its subcontrols to QStyleSheetStyle, which has no image for
    ::down-arrow and falls back to a crude built-in triangle that ignores the
    palette. There is no bundled image to point it at, so the QSS suppresses
    the arrow entirely and it is drawn here instead.
    """

    ARROW_BOX = 26      # reserved strip on the right, matches QSS padding

    def paintEvent(self, event):
        super().paintEvent(event)

        pt = QPainter(self)
        pt.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(c("text") if self.isEnabled() else c("text_muted"))
        pen.setWidthF(1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        pt.setPen(pen)

        cx = self.width() - self.ARROW_BOX / 2
        cy = self.height() / 2
        w, h = 4.5, 2.6      # half-width / half-height of the chevron
        pt.drawPolyline(QPolygonF([
            QPointF(cx - w, cy - h / 2),
            QPointF(cx, cy + h),
            QPointF(cx + w, cy - h / 2),
        ]))
        pt.end()


class Card(QFrame):
    """Titled rounded container. `body` is the layout callers add rows to."""

    def __init__(self, title=None, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 13)
        outer.setSpacing(9)

        self.header = QHBoxLayout()
        self.header.setSpacing(8)
        if title:
            self.title_label = QLabel(title.upper())
            self.title_label.setObjectName("CardTitle")
            # QSS has no letter-spacing; the tracked small-caps look that makes
            # these read as section headings has to be set on the font.
            font = self.title_label.font()
            font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.1)
            self.title_label.setFont(font)
            self.header.addWidget(self.title_label)
        self.header.addStretch()
        outer.addLayout(self.header)

        self.body = QVBoxLayout()
        self.body.setSpacing(9)
        outer.addLayout(self.body)

    def add_header_widget(self, widget):
        self.header.addWidget(widget)


class SettingsDialog(QDialog):
    def __init__(self, parent=None, ctrl=None):
        super().__init__(parent)
        self.ctrl = ctrl
        self.setWindowTitle(_tr("Settings"))
        self.setFixedWidth(380)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 14)
        layout.setSpacing(10)

        self.autostart_cb = QCheckBox(_tr("Start automatically with Linux"))
        self.minimized_cb = QCheckBox(_tr("Start minimized in system tray"))
        self.voice_prompts_cb = QCheckBox(_tr("Enable official voice prompts"))
        self.hw_power_save_cb = QCheckBox(_tr("Hardware Power Saving"))

        self.tray_mode_combo = ThemedComboBox()
        self.tray_mode_combo.addItem(_tr("Virtuoso icon"), "virtuoso")
        self.tray_mode_combo.addItem(_tr("Battery icon"), "battery")
        self.tray_mode_combo.addItem(_tr("Both"), "both")

        self.lang_combo = ThemedComboBox()
        self.lang_combo.addItem("English", "en")
        self.lang_combo.addItem("Español", "es")

        self.low_batt_remind_combo = ThemedComboBox()
        for label, secs in ((_tr("Off"), 0), (_tr("30 sec"), 30), (_tr("60 sec"), 60),
                            (_tr("2 min"), 120), (_tr("5 min"), 300), (_tr("10 min"), 600)):
            self.low_batt_remind_combo.addItem(label, secs)

        self.mic_mute_remind_combo = ThemedComboBox()
        for label, secs in ((_tr("Off"), 0), (_tr("15 sec"), 15), (_tr("30 sec"), 30),
                            (_tr("60 sec"), 60), (_tr("2 min"), 120), (_tr("5 min"), 300)):
            self.mic_mute_remind_combo.addItem(label, secs)

        layout.addWidget(self.autostart_cb)
        layout.addWidget(self.minimized_cb)
        layout.addWidget(self.voice_prompts_cb)
        layout.addWidget(self.hw_power_save_cb)

        for text, combo in ((_tr("Tray icon:"), self.tray_mode_combo),
                            (_tr("Language (requires restart):"), self.lang_combo),
                            (_tr("Low battery reminder:"), self.low_batt_remind_combo),
                            (_tr("Mic muted reminder:"), self.mic_mute_remind_combo)):
            caption = QLabel(text)
            caption.setObjectName("RowLabel")
            layout.addWidget(caption)
            layout.addWidget(combo)

        # Hardware Info section
        hw_card = QFrame()
        hw_card.setObjectName("Card")
        hw_lay = QVBoxLayout(hw_card)
        hw_lay.setContentsMargins(10, 8, 10, 8)
        hw_lay.setSpacing(4)
        hw_title = QLabel(_tr("Hardware Info").upper())
        hw_title.setObjectName("CardTitle")
        hw_lay.addWidget(hw_title)
        self.hw_info_label = QLabel(_tr("Not connected"))
        self.hw_info_label.setObjectName("Muted")
        self.hw_info_label.setWordWrap(True)
        hw_lay.addWidget(self.hw_info_label)
        layout.addWidget(hw_card)

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)
        save_btn = QPushButton(_tr("Save"))
        save_btn.setObjectName("Primary")
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.clicked.connect(self.save_and_close)
        cancel_btn = QPushButton(_tr("Cancel"))
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.clicked.connect(self.reject)

        btn_layout.addStretch()
        btn_layout.addWidget(cancel_btn)
        btn_layout.addWidget(save_btn)
        layout.addSpacing(4)
        layout.addLayout(btn_layout)

        self.load_settings()
        self.adjustSize()
        self.setFixedHeight(self.sizeHint().height())

    def load_settings(self):
        s = QSettings("AlejandroSocas", "VirtuosoControl")
        self.minimized_cb.setChecked(s.value("start_minimized", False, type=bool))
        self.voice_prompts_cb.setChecked(s.value("voice_prompts", True, type=bool))
        self.hw_power_save_cb.setChecked(s.value("hw_power_saving", False, type=bool))
        mode = s.value("tray_icon_mode", "virtuoso", type=str)
        idx = self.tray_mode_combo.findData(mode)
        if idx >= 0:
            self.tray_mode_combo.setCurrentIndex(idx)

        lang = s.value("language", "en", type=str)
        index = self.lang_combo.findData(lang)
        if index >= 0:
            self.lang_combo.setCurrentIndex(index)

        lb_sec = s.value("low_battery_reminder_sec", 0, type=int)
        lb_idx = self.low_batt_remind_combo.findData(lb_sec)
        if lb_idx >= 0:
            self.low_batt_remind_combo.setCurrentIndex(lb_idx)

        mm_sec = s.value("mic_mute_reminder_sec", 0, type=int)
        mm_idx = self.mic_mute_remind_combo.findData(mm_sec)
        if mm_idx >= 0:
            self.mic_mute_remind_combo.setCurrentIndex(mm_idx)

        autostart_path = os.path.expanduser("~/.config/autostart/virtuoso-control.desktop")
        self.autostart_cb.setChecked(os.path.exists(autostart_path))

        if self.ctrl and self.ctrl.is_connected:
            hw = self.ctrl.get_device_hw_info()
            if hw.get("power_saving") is not None:
                self.hw_power_save_cb.setChecked(bool(hw["power_saving"]))
            lines = []
            if hw.get("headset_fw"):
                lines.append(f"{_tr('Headset FW:')} {hw['headset_fw']}")
            if hw.get("dongle_fw"):
                lines.append(f"{_tr('Dongle FW:')} {hw['dongle_fw']}")
            if hw.get("polling_rate_hz"):
                lines.append(f"{_tr('Polling:')} {hw['polling_rate_hz']} Hz")
            if hw.get("rf_encryption") is not None:
                enc = _tr("Enabled") if hw["rf_encryption"] else _tr("Disabled")
                lines.append(f"{_tr('Encryption:')} {enc}")
            if hw.get("battery_mv"):
                lines.append(f"{hw['battery_mv']} mV")
            if hw.get("hw_sleep"):
                sl = hw["hw_sleep"]
                sl_txt = f"{sl.get('timeout_min', '?')} min" if sl.get("enabled") else _tr("Off")
                lines.append(f"{_tr('HW Sleep:')} {sl_txt}")
            self.hw_info_label.setText("  ·  ".join(lines) if lines else hw.get("model", _tr("Connected")))
            if hw.get("power_saving") is None:
                self.hw_power_save_cb.setEnabled(False)
        else:
            self.hw_power_save_cb.setEnabled(False)
            self.hw_info_label.setText(_tr("Not connected"))

    def save_and_close(self):
        s = QSettings("AlejandroSocas", "VirtuosoControl")
        s.setValue("start_minimized", self.minimized_cb.isChecked())
        s.setValue("voice_prompts", self.voice_prompts_cb.isChecked())
        s.setValue("hw_power_saving", self.hw_power_save_cb.isChecked())
        s.setValue("language", self.lang_combo.currentData())
        s.setValue("tray_icon_mode", self.tray_mode_combo.currentData())
        s.setValue("low_battery_reminder_sec", self.low_batt_remind_combo.currentData())
        s.setValue("mic_mute_reminder_sec", self.mic_mute_remind_combo.currentData())

        if self.ctrl and self.ctrl.is_connected and "Wired" not in self.ctrl.connection_mode:
            self.ctrl.set_power_saving_mode(self.hw_power_save_cb.isChecked())

        autostart_dir = os.path.expanduser("~/.config/autostart")
        autostart_path = os.path.join(autostart_dir, "virtuoso-control.desktop")

        if self.autostart_cb.isChecked():
            if not os.path.exists(autostart_dir):
                os.makedirs(autostart_dir)
            # Icon is the themed NAME, matching install.sh. An absolute path
            # here gets cached by the shell and goes stale when the file is
            # replaced. Resolving it needs install.sh to have run — as does
            # the hardcoded Exec path just below it.
            desktop_content = (
                "[Desktop Entry]\n"
                "Name=Virtuoso Control\n"
                "Comment=Native control panel for Corsair Virtuoso SE headset\n"
                "Exec=python3 /opt/virtuoso-control/virtuoso_gui.py\n"
                "Icon=virtuoso-control\n"
                "Terminal=false\n"
                "Type=Application\n"
                # "Audio" alone is invalid without "AudioVideo" — the spec
                # validator flags it as an error that will become fatal.
                "Categories=AudioVideo;Audio;Settings;HardwareSettings;\n"
                "StartupWMClass=virtuoso-control\n"
            )
            with open(autostart_path, "w") as f:
                f.write(desktop_content)
        else:
            if os.path.exists(autostart_path):
                os.remove(autostart_path)

        self.accept()
