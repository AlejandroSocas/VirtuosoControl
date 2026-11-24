"""Light/dark theme palettes, QSS stylesheet builder, and vector icon painters."""
import math
from PyQt6.QtCore import Qt, QRectF, QLineF, QPointF
from PyQt6.QtGui import QIcon, QColor, QPixmap, QPainter, QBrush, QPen, QPalette, QPolygonF

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
        "good":        "#ffff00",
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
        "good":        "#d4D400",
        "warn":        "#b8770a",
        "danger":      "#d0342c",
        "handle":      "#ffffff",
    },
}

THEME = dict(PALETTES["dark"])

# ─── Battery limit constants (aligned with official Corsair iCUE manifest) ────
BATT_LEVEL = {
    "HIGH":       50,
    "MEDIUM":     30,
    "LOW":        15,
    "NOTIFY_LOW": 15,  # For system notification (CriticalLow in manifest).
}

# ─── Brightness level limits for LEDs ───────────────────────────────
POWER_SAVING_BRIGHTNESS = {
    "RGB_fixed":    0,    # % Left and right LEDs.
    "MIC_fixed":   25,    # % Microphone LED.
    "RGB_max":   0.25,    # %/100
    "MIC_max":   0.50,    # %/100
}


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
        /* Right padding clears the strip ThemedComboBox paints into. */
        padding: 5px 26px 5px 10px;
        color: {p['text']};
    }}
    QComboBox:hover {{
        border-color: {p['accent']};
    }}
    QComboBox:disabled {{
        color: {p['text_muted']};
        background: {p['surface']};
        border-color: {p['surface_alt']};
    }}
    /* The default arrow is suppressed rather than restyled — there is no
       image to give it. ThemedComboBox.paintEvent draws the chevron. */
    QComboBox::drop-down {{
        border: none;
        background: transparent;
        width: 26px;
    }}
    QComboBox::down-arrow {{
        image: none;
        width: 0px;
        height: 0px;
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
