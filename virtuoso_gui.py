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
                             QFrame, QSystemTrayIcon, QMenu, QCheckBox,
                             QButtonGroup, QColorDialog, QDialog,
                             QComboBox, QAbstractButton, QToolButton, QSizePolicy)
from PyQt6.QtCore import (Qt, QTimer, QSettings, QRectF, QLineF, QSize,
                          QPointF)
from PyQt6.QtGui import (QIcon, QAction, QColor, QPixmap, QPainter, QBrush, QPen,
                         QFont, QPalette, QPolygonF)
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
        "Wireless": "Inalámbrico",
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
        "Lighting": "Iluminación",
        "Logo": "Logo",
        "Mic": "Micrófono",
        "Muted": "Silenciado",
        "Active": "Activo",
        "Profiles": "Perfiles",
        "Save to Profile 1": "Guardar en Perfil 1",
        "Save to Profile 2": "Guardar en Perfil 2",
        "Save to Profile 3": "Guardar en Perfil 3",
        "Save current lighting to a profile": "Guardar la iluminación actual en un perfil",
        "N/A (Wired)": "N/D (Cable)",
        "Level": "Nivel",
        "Brightness": "Brillo",
        "Method": "Método",
        "Status": "Estado",
    }
}

def _tr(text):
    s = QSettings("AlejandroSocas", "VirtuosoControl")
    lang = s.value("language", "en", type=str)
    if lang == "es" and text in TRANSLATIONS["es"]:
        return TRANSLATIONS["es"][text]
    return text

# ─── Theme ──────────────────────────────────────────────────────────
#
# The app follows the desktop's light/dark preference. Every colour used by
# the stylesheet *and* by the hand-painted widgets below comes from one of
# these two dicts, so switching schemes is a matter of swapping THEME and
# re-applying — see apply_theme().

PALETTES = {
    "dark": {
        "window":      "#14161b",
        "surface":     "#1c1f26",
        "surface_alt": "#232732",
        "border":      "#2e333f",
        "text":        "#e6e8ee",
        "text_muted":  "#8d94a5",
        "accent":      "#4c9aff",
        "accent_hi":   "#63a9ff",
        "accent_lo":   "#3d86e6",
        "on_accent":   "#0d1117",
        "track":       "#2b3040",
        "ok":          "#35d07f",
        "warn":        "#f0b429",
        "danger":      "#ff5c5c",
        "handle":      "#f2f4f8",
    },
    "light": {
        "window":      "#f2f4f7",
        "surface":     "#ffffff",
        "surface_alt": "#eef1f5",
        "border":      "#d7dce4",
        "text":        "#171a20",
        "text_muted":  "#626b7b",
        "accent":      "#2f6fd0",
        "accent_hi":   "#3c7de0",
        "accent_lo":   "#2860ba",
        "on_accent":   "#ffffff",
        "track":       "#dde2ea",
        "ok":          "#12925a",
        "warn":        "#b8770a",
        "danger":      "#d0342c",
        "handle":      "#ffffff",
    },
}

THEME = dict(PALETTES["dark"])


def c(key):
    """QColor for a theme key. Painted widgets read their colours through this."""
    return QColor(THEME[key])


def detect_scheme(app):
    """'dark' or 'light', from the desktop preference.

    Qt 6.5+ reports the platform's colour-scheme hint directly; the palette
    lightness check is a fallback for platforms that leave it Unknown.
    """
    try:
        scheme = app.styleHints().colorScheme()
        if scheme == Qt.ColorScheme.Dark:
            return "dark"
        if scheme == Qt.ColorScheme.Light:
            return "light"
    except AttributeError:
        pass
    return "dark" if app.palette().color(
        QPalette.ColorRole.Window).lightness() < 128 else "light"


def build_palette(p):
    """QPalette mirroring the theme.

    QSS cannot reach the primitives the style draws itself — the checkbox tick
    is the visible one: restyling `::indicator` in QSS removes the tick and
    there is no bundled image to put back. Those primitives follow the
    QPalette instead, so both have to be kept in step or checkboxes come out
    dark on the light skin.
    """
    pal = QPalette()
    window, surface = QColor(p["window"]), QColor(p["surface"])
    text, muted = QColor(p["text"]), QColor(p["text_muted"])
    accent = QColor(p["accent"])

    roles = {
        QPalette.ColorRole.Window: window,
        QPalette.ColorRole.WindowText: text,
        QPalette.ColorRole.Base: surface,
        QPalette.ColorRole.AlternateBase: QColor(p["surface_alt"]),
        QPalette.ColorRole.Text: text,
        QPalette.ColorRole.Button: QColor(p["surface_alt"]),
        QPalette.ColorRole.ButtonText: text,
        QPalette.ColorRole.ToolTipBase: QColor(p["surface_alt"]),
        QPalette.ColorRole.ToolTipText: text,
        QPalette.ColorRole.PlaceholderText: muted,
        QPalette.ColorRole.Highlight: accent,
        QPalette.ColorRole.HighlightedText: QColor(p["on_accent"]),
        QPalette.ColorRole.Link: accent,
    }
    for role, color in roles.items():
        pal.setColor(role, color)
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text,
                 QPalette.ColorRole.ButtonText):
        pal.setColor(QPalette.ColorGroup.Disabled, role, muted)
    return pal


def build_stylesheet(p):
    """The whole app skin, interpolated from a palette dict.

    Checkbox and radio indicators are deliberately left unstyled here — see
    build_palette() for why. The sidetone method picker is not a radio pair for
    the same reason: it is two checkable buttons in an exclusive group, which
    QSS *can* redraw as a segmented control.
    """
    return f"""
    QMainWindow, QDialog {{
        background: {p['window']};
    }}
    QToolTip {{
        background: {p['surface_alt']};
        color: {p['text']};
        border: 1px solid {p['border']};
        padding: 4px 6px;
        border-radius: 4px;
    }}

    /* Cards */
    QFrame#Card {{
        background: {p['surface']};
        border: 1px solid {p['border']};
        border-radius: 12px;
    }}
    QLabel#CardTitle {{
        color: {p['text_muted']};
        font-size: 11px;
        font-weight: 700;
    }}
    QLabel#Muted {{
        color: {p['text_muted']};
    }}
    QLabel#RowLabel {{
        color: {p['text_muted']};
        font-size: 12px;
    }}
    QLabel#Value {{
        color: {p['text']};
        font-size: 12px;
        font-weight: 600;
    }}
    QLabel#BattPercent {{
        font-size: 26px;
        font-weight: 700;
    }}
    QLabel#StatusText {{
        font-size: 13px;
        font-weight: 600;
    }}

    /* Buttons */
    QPushButton, QToolButton {{
        background: {p['surface_alt']};
        border: 1px solid {p['border']};
        border-radius: 8px;
        padding: 6px 12px;
        color: {p['text']};
    }}
    QPushButton:hover, QToolButton:hover {{
        background: {p['border']};
    }}
    QPushButton:pressed, QToolButton:pressed {{
        background: {p['accent_lo']};
        color: {p['on_accent']};
    }}
    QPushButton:disabled, QToolButton:disabled {{
        color: {p['text_muted']};
        background: {p['surface']};
        border-color: {p['surface_alt']};
    }}
    QPushButton#IconBtn {{
        padding: 0px;
        min-width: 28px;
        max-width: 28px;
        min-height: 28px;
        max-height: 28px;
        border-radius: 8px;
        font-size: 14px;
    }}
    QPushButton#Primary {{
        background: {p['accent']};
        border-color: {p['accent']};
        color: {p['on_accent']};
        font-weight: 600;
        padding: 8px 12px;
    }}
    QPushButton#Primary:hover {{
        background: {p['accent_hi']};
        border-color: {p['accent_hi']};
    }}
    QPushButton#Primary:checked {{
        background: {p['surface_alt']};
        border-color: {p['border']};
        color: {p['text_muted']};
    }}
    QPushButton#Primary:disabled {{
        background: {p['surface']};
        border-color: {p['surface_alt']};
        color: {p['text_muted']};
    }}
    QPushButton#Profile {{
        padding: 5px 0px;
        font-weight: 600;
    }}
    QToolButton#SaveMenu::menu-indicator {{
        image: none;
    }}

    /* Segmented control (sidetone method) */
    QPushButton#SegLeft, QPushButton#SegRight {{
        background: {p['surface_alt']};
        border: 1px solid {p['border']};
        border-radius: 0px;
        color: {p['text_muted']};
        padding: 3px 10px;
        font-size: 11px;
        font-weight: 600;
    }}
    QPushButton#SegLeft:hover, QPushButton#SegRight:hover {{
        color: {p['text']};
    }}
    QPushButton#SegLeft:checked, QPushButton#SegRight:checked {{
        background: {p['accent']};
        border-color: {p['accent']};
        color: {p['on_accent']};
    }}
    QPushButton#SegLeft:disabled, QPushButton#SegRight:disabled {{
        color: {p['text_muted']};
        background: {p['surface']};
        border-color: {p['surface_alt']};
    }}
    QPushButton#SegLeft {{
        border-top-left-radius: 7px;
        border-bottom-left-radius: 7px;
        border-right: none;
    }}
    QPushButton#SegRight {{
        border-top-right-radius: 7px;
        border-bottom-right-radius: 7px;
    }}

    /* Sliders */
    QSlider::groove:horizontal {{
        height: 5px;
        background: {p['track']};
        border-radius: 3px;
    }}
    QSlider::sub-page:horizontal {{
        background: {p['accent']};
        border-radius: 3px;
    }}
    QSlider::handle:horizontal {{
        background: {p['handle']};
        border: 2px solid {p['accent']};
        width: 12px;
        height: 12px;
        margin: -6px 0px;
        border-radius: 8px;
    }}
    QSlider::handle:horizontal:hover {{
        border-color: {p['accent_hi']};
    }}
    QSlider::groove:horizontal:disabled {{
        background: {p['surface_alt']};
    }}
    QSlider::sub-page:horizontal:disabled {{
        background: {p['border']};
    }}
    QSlider::handle:horizontal:disabled {{
        background: {p['surface_alt']};
        border-color: {p['border']};
    }}

    /* QCheckBox is intentionally absent: any QSS rule matching it hands
       indicator drawing to QStyleSheetStyle, which has no tick image to draw
       and renders a bare checkmark with no box. It is themed by palette only. */

    QComboBox {{
        background: {p['surface_alt']};
        border: 1px solid {p['border']};
        border-radius: 8px;
        padding: 5px 10px;
        color: {p['text']};
    }}
    QComboBox:hover {{
        border-color: {p['accent']};
    }}
    QComboBox QAbstractItemView {{
        background: {p['surface']};
        border: 1px solid {p['border']};
        selection-background-color: {p['accent']};
        selection-color: {p['on_accent']};
        outline: none;
    }}

    QMenu {{
        background: {p['surface']};
        border: 1px solid {p['border']};
        border-radius: 8px;
        padding: 4px;
    }}
    QMenu::item {{
        padding: 6px 18px 6px 12px;
        border-radius: 6px;
    }}
    QMenu::item:selected {{
        background: {p['accent']};
        color: {p['on_accent']};
    }}
    QMenu::item:disabled {{
        color: {p['text_muted']};
    }}
    QMenu::separator {{
        height: 1px;
        background: {p['border']};
        margin: 4px 8px;
    }}
    """


def apply_theme(app, scheme=None):
    """Swaps THEME to the given (or detected) scheme and re-skins the app.

    The platform style is deliberately left alone. Forcing Fusion was tried and
    reverted: Fusion derives the checkbox indicator's frame from palette shades
    that this palette flattens, so checked boxes came out as a bare tick with no
    box. The desktop's own style draws them correctly from the same palette.
    """
    if scheme is None:
        scheme = detect_scheme(app)
    THEME.clear()
    THEME.update(PALETTES[scheme])
    app.setPalette(build_palette(THEME))
    # Base size is set on the application font rather than in QSS, because a
    # QSS rule broad enough to cover every widget would also match QCheckBox.
    font = app.font()
    font.setPixelSize(13)
    app.setFont(font)
    app.setStyleSheet(build_stylesheet(THEME))
    return scheme


ICON_UNITS = 16      # logical drawing space for the painted icons
ICON_SCALE = 4       # device pixels per unit, so the icons stay crisp on hidpi


def _icon_painter(color, width=1.6):
    """Sets up a transparent pixmap and a stroked painter over ICON_UNITS².

    Both header icons are painted rather than typed: ⚙ and ↻ resolve to
    full-colour emoji glyphs on a typical Linux font stack, which ignore the
    palette and clash with everything else in the window.
    """
    pm = QPixmap(ICON_UNITS * ICON_SCALE, ICON_UNITS * ICON_SCALE)
    pm.setDevicePixelRatio(ICON_SCALE)
    pm.fill(Qt.GlobalColor.transparent)

    pt = QPainter(pm)
    pt.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color))
    pen.setWidthF(width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    pt.setPen(pen)
    return pm, pt


def make_settings_icon(color):
    """Rails with knobs — a settings/sliders glyph.

    Two rails, not three: at a 16px icon size a third rail closes the gaps up
    until the whole thing reads as a smudge.
    """
    pm, pt = _icon_painter(color, 1.7)

    rails = ((5.5, 10.2), (10.5, 6.0))
    knob_r, x0, x1 = 1.75, 1.9, 14.1
    gap = knob_r + 1.2
    for y, knob_x in rails:
        # Rail drawn either side of the knob so the knob never has to be
        # filled with the button's background colour to stay readable.
        pt.drawLine(QLineF(x0, y, knob_x - gap, y))
        pt.drawLine(QLineF(knob_x + gap, y, x1, y))
    pt.setBrush(QBrush(QColor(color)))
    pt.setPen(Qt.PenStyle.NoPen)
    for y, knob_x in rails:
        pt.drawEllipse(QRectF(knob_x - knob_r, y - knob_r, knob_r * 2, knob_r * 2))
    pt.end()
    return QIcon(pm)


def make_refresh_icon(color):
    """Open circular arrow — reconnect / re-read."""
    pm, pt = _icon_painter(color, 1.6)
    cx = cy = 8.0
    r = 5.0
    # The sweep stops well short of a full turn: the gap is what makes the
    # arrowhead legible as an arrowhead at 16px rather than a lump on a ring.
    start, span = 40, 250           # degrees, counter-clockwise
    pt.setBrush(Qt.BrushStyle.NoBrush)
    pt.drawArc(QRectF(cx - r, cy - r, r * 2, r * 2), start * 16, span * 16)

    # Arrowhead on the leading end of the sweep, aligned to the tangent there.
    end = math.radians(start + span)
    tip_x, tip_y = cx + r * math.cos(end), cy - r * math.sin(end)
    tangent = (-math.sin(end), -math.cos(end))
    normal = (math.cos(end), -math.sin(end))
    pt.setPen(Qt.PenStyle.NoPen)
    pt.setBrush(QBrush(QColor(color)))
    pt.drawPolygon(QPolygonF([
        QPointF(tip_x + tangent[0] * 3.6, tip_y + tangent[1] * 3.6),
        QPointF(tip_x + normal[0] * 2.4, tip_y + normal[1] * 2.4),
        QPointF(tip_x - normal[0] * 2.4, tip_y - normal[1] * 2.4),
    ]))
    pt.end()
    return QIcon(pm)


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
        if self._percent > 50:
            return c("ok")
        if self._percent > 20:
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
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(_tr("Settings"))
        self.setFixedWidth(340)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 14)
        layout.setSpacing(12)

        self.autostart_cb = QCheckBox(_tr("Start automatically with Linux"))
        self.minimized_cb = QCheckBox(_tr("Start minimized in system tray"))

        self.tray_mode_combo = QComboBox()
        self.tray_mode_combo.addItem(_tr("Virtuoso icon"), "virtuoso")
        self.tray_mode_combo.addItem(_tr("Battery icon"), "battery")
        self.tray_mode_combo.addItem(_tr("Both"), "both")

        self.lang_combo = QComboBox()
        self.lang_combo.addItem("English", "en")
        self.lang_combo.addItem("Español", "es")

        layout.addWidget(self.autostart_cb)
        layout.addWidget(self.minimized_cb)
        # Stacked rather than side by side: the Spanish label for the language
        # row is long enough to squeeze the combo box down to nothing.
        for text, combo in ((_tr("Tray icon:"), self.tray_mode_combo),
                            (_tr("Language (requires restart):"), self.lang_combo)):
            caption = QLabel(text)
            caption.setObjectName("RowLabel")
            layout.addWidget(caption)
            layout.addWidget(combo)

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
    ROW_LABELS = ("Logo", "Mic", "Profiles", "Status", "Muted", "Level")

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
        self.setFixedWidth(378)
        self._row_label_w = self._measure_row_label_width()

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(14, 12, 14, 14)
        layout.setSpacing(10)

        layout.addLayout(self._build_header())
        layout.addWidget(self._build_battery_card())
        layout.addWidget(self._build_lighting_card())
        layout.addWidget(self._build_mic_card())
        layout.addWidget(self._build_sidetone_card())
        layout.addWidget(self._build_volume_card())
        layout.addStretch()

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

        card.body.setSpacing(7)
        card.body.addLayout(read_row)
        card.body.addWidget(self.batt_gauge)
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

        card.body.addLayout(logo_row)
        card.body.addLayout(mic_row)
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

    def _set_status(self, text, color_key):
        """Header indicator: coloured dot plus plain text, no emoji."""
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"color: {THEME[color_key]};")
        self.status_dot.set_color(c(color_key))

    def _set_v2w_controls_enabled(self, enabled):
        """Enables/disables everything that needs a live V2W link."""
        for w in (self.batt_btn, self.mic_color_btn, self.mic_slider,
                  self.rgb_color_btn, self.rgb_slider, self.mic_mute_color_btn,
                  self.profile_btn_1, self.profile_btn_2, self.profile_btn_3,
                  self.save_profile_btn):
            w.setEnabled(enabled)

    def _update_status(self, connected):
        if connected:
            self._set_status(
                f"{_tr('Connected')} · {_tr(self.ctrl.connection_mode)}", "ok")

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

    def _on_connection_lost(self):
        self._hid_connected = False
        self._update_status(False)
        self.keep_alive_timer.stop()
        if not self.reconnect_timer.isActive():
            self.reconnect_timer.start(3000)

    def force_reconnect(self):
        self._set_status(_tr("Connecting..."), "warn")
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
            self.rgb_color_btn.set_color(color)
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
    apply_theme(app)   # follows the desktop's light/dark preference

    gui = VirtuosoGUI()
    
    s = QSettings("AlejandroSocas", "VirtuosoControl")
    if not s.value("start_minimized", False, type=bool):
        gui.show()

    app.aboutToQuit.connect(gui.quit_app)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
