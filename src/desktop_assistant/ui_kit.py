# -*- coding: utf-8 -*-
"""共享的视觉基础件：主题、颜色工具、矢量图标、玻璃阴影与 Siri 流光球。

主界面（``app.py``）和设置页（``settings_dialog.py``）都从这里取用，
保证两处视觉语言一致。所有图标都用 QPainterPath 现画，任何缩放比例下都清晰。
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QIcon,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
    QRadialGradient,
)
from PySide6.QtWidgets import QGraphicsBlurEffect, QGraphicsPixmapItem, QGraphicsScene, QMenu

AA = QPainter.RenderHint.Antialiasing
SMOOTH = QPainter.RenderHint.SmoothPixmapTransform
TEXT_AA = QPainter.RenderHint.TextAntialiasing
NO_PEN = Qt.PenStyle.NoPen
NO_BRUSH = Qt.BrushStyle.NoBrush
A_LEFT_V = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
A_CENTER = Qt.AlignmentFlag.AlignCenter

UI_FAMILIES = ["SF Pro Text", "Segoe UI Variable Text", "Segoe UI",
               "PingFang SC", "Microsoft YaHei UI"]

PAD = 30                 # 阴影留白
SH_BLUR = 20
SH_DY = 8

RED = QColor(255, 59, 48)
ORANGE = QColor(255, 149, 0)
GREEN = QColor(52, 199, 89)


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #
def clamp(v, lo=0.0, hi=1.0):
    return lo if v < lo else hi if v > hi else v


def lerp(a, b, t):
    return a + (b - a) * t


def ease_out(t):
    t = clamp(t)
    return 1.0 - (1.0 - t) ** 3


def mix(a: QColor, b: QColor, t: float) -> QColor:
    t = clamp(t)
    return QColor(round(lerp(a.red(), b.red(), t)), round(lerp(a.green(), b.green(), t)),
                  round(lerp(a.blue(), b.blue(), t)), round(lerp(a.alpha(), b.alpha(), t)))


def with_alpha(c: QColor, a) -> QColor:
    q = QColor(c)
    q.setAlpha(int(clamp(a, 0, 255)))
    return q


def css(c: QColor) -> str:
    return f"rgba({c.red()},{c.green()},{c.blue()},{c.alpha()})"


def ui_font(px: int, weight=QFont.Weight.Normal) -> QFont:
    f = QFont()
    f.setFamilies(UI_FAMILIES)
    f.setPixelSize(px)
    f.setWeight(weight)
    return f


# --------------------------------------------------------------------------- #
# 主题（iOS 深色 / 浅色）
# --------------------------------------------------------------------------- #
class Theme:
    def __init__(self, dark: bool):
        self.dark = dark
        self.accent = QColor(10, 132, 255) if dark else QColor(0, 122, 255)
        self.accent_soft = with_alpha(self.accent, 42)
        self.success = QColor(48, 209, 88) if dark else QColor(40, 170, 74)
        self.danger = RED
        self.warning = ORANGE
        if dark:
            self.card_top = QColor(52, 52, 56, 240)
            self.card_bot = QColor(34, 34, 37, 245)
            self.glass_top = QColor(58, 58, 62, 242)
            self.glass_bot = QColor(44, 44, 48, 246)
            self.hairline = QColor(255, 255, 255, 34)
            self.sep = QColor(255, 255, 255, 26)
            self.label = QColor(255, 255, 255)
            self.label2 = QColor(235, 235, 245, 160)
            self.label3 = QColor(235, 235, 245, 90)
            self.fill = QColor(120, 120, 128, 80)
            self.fill_hi = QColor(120, 120, 128, 120)
            self.bubble_in = QColor(59, 59, 63)
            self.bubble_in_text = QColor(255, 255, 255)
            self.shadow = 0.60
            self.menu_bg = QColor(44, 44, 47, 248)
            self.sidebar_bg = QColor(36, 36, 39, 238)
            self.input_bg = QColor(61, 61, 66)
            self.bubble_error = QColor(94, 40, 42, 235)
        else:
            self.card_top = QColor(255, 255, 255, 246)
            self.card_bot = QColor(246, 246, 249, 248)
            self.glass_top = QColor(255, 255, 255, 248)
            self.glass_bot = QColor(246, 246, 250, 248)
            self.hairline = QColor(0, 0, 0, 30)
            self.sep = QColor(60, 60, 67, 38)
            self.label = QColor(0, 0, 0)
            self.label2 = QColor(60, 60, 67, 153)
            self.label3 = QColor(60, 60, 67, 80)
            self.fill = QColor(120, 120, 128, 46)
            self.fill_hi = QColor(120, 120, 128, 72)
            self.bubble_in = QColor(233, 233, 235)
            self.bubble_in_text = QColor(0, 0, 0)
            self.shadow = 0.30
            self.menu_bg = QColor(250, 250, 252, 250)
            self.sidebar_bg = QColor(244, 244, 248, 240)
            self.input_bg = QColor(241, 241, 245)
            self.bubble_error = QColor(255, 232, 230, 245)


THEME_DARK = Theme(True)
THEME_LIGHT = Theme(False)


def style_menu(menu: QMenu, th: Theme, first_time: bool = False):
    if first_time:
        menu.setWindowFlags(menu.windowFlags() | Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.NoDropShadowWindowHint)
        menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        menu.setFont(ui_font(13))
    menu.setStyleSheet(f"""
        QMenu {{ background: {css(th.menu_bg)}; border: 1px solid {css(th.hairline)};
                 border-radius: 14px; padding: 6px; }}
        QMenu::item {{ color: {css(th.label)}; padding: 8px 28px 8px 16px;
                       border-radius: 8px; margin: 1px 0; }}
        QMenu::item:selected {{ background: {css(th.accent)}; color: #FFFFFF; }}
        QMenu::item:disabled {{ color: {css(th.label3)}; }}
        QMenu::separator {{ height: 1px; background: {css(th.sep)}; margin: 5px 10px; }}
        QMenu::icon {{ padding-left: 8px; }}
    """)


# --------------------------------------------------------------------------- #
# 绘图工具
# --------------------------------------------------------------------------- #
def rrect_path(r: QRectF, tl: float, tr: float, br: float, bl: float) -> QPainterPath:
    """四角半径可分别指定的圆角矩形（用于带「尾巴」的气泡）。"""
    p = QPainterPath()
    p.moveTo(r.left() + tl, r.top())
    p.lineTo(r.right() - tr, r.top())
    if tr:
        p.arcTo(r.right() - 2 * tr, r.top(), 2 * tr, 2 * tr, 90, -90)
    p.lineTo(r.right(), r.bottom() - br)
    if br:
        p.arcTo(r.right() - 2 * br, r.bottom() - 2 * br, 2 * br, 2 * br, 0, -90)
    p.lineTo(r.left() + bl, r.bottom())
    if bl:
        p.arcTo(r.left(), r.bottom() - 2 * bl, 2 * bl, 2 * bl, 270, -90)
    p.lineTo(r.left(), r.top() + tl)
    if tl:
        p.arcTo(r.left(), r.top(), 2 * tl, 2 * tl, 180, -90)
    p.closeSubpath()
    return p


_SHADOW_CACHE = {}


def soft_shadow(w: float, h: float, radius: float, dpr: float = 1.0) -> QPixmap:
    """元素「外侧」的柔和阴影（内部已镂空，所以半透明玻璃下面不会发脏）。
    返回的 pixmap 比元素每边大 PAD，绘制时放在 (x - PAD, y - PAD)。"""
    dpr = max(1.0, dpr)
    w, h = int(round(w)), int(round(h))
    key = (w, h, round(radius, 1), round(dpr, 2))
    pm = _SHADOW_CACHE.get(key)
    if pm is not None:
        return pm
    pw, ph = round((w + 2 * PAD) * dpr), round((h + 2 * PAD) * dpr)
    shape = QRectF(PAD * dpr, PAD * dpr, w * dpr, h * dpr)

    mask = QImage(pw, ph, QImage.Format.Format_ARGB32_Premultiplied)
    mask.fill(0)
    p = QPainter(mask)
    p.setRenderHint(AA)
    p.setPen(NO_PEN)
    p.setBrush(QColor(0, 0, 0))
    p.drawRoundedRect(shape.translated(0, SH_DY * dpr), radius * dpr, radius * dpr)
    p.end()

    scene = QGraphicsScene()
    scene.setSceneRect(0, 0, pw, ph)
    item = QGraphicsPixmapItem(QPixmap.fromImage(mask))
    eff = QGraphicsBlurEffect()
    eff.setBlurRadius(SH_BLUR * dpr)
    eff.setBlurHints(QGraphicsBlurEffect.BlurHint.QualityHint)
    item.setGraphicsEffect(eff)
    scene.addItem(item)

    out = QImage(pw, ph, QImage.Format.Format_ARGB32_Premultiplied)
    out.fill(0)
    p = QPainter(out)
    p.setRenderHint(AA)
    scene.render(p)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationOut)
    p.setPen(NO_PEN)
    p.setBrush(QColor(0, 0, 0))
    p.drawRoundedRect(shape, radius * dpr, radius * dpr)
    p.end()
    out.setDevicePixelRatio(dpr)
    pm = QPixmap.fromImage(out)
    _SHADOW_CACHE[key] = pm
    return pm


def _star_path(cx: float, cy: float, r: float, inner: float = 0.30) -> QPainterPath:
    pts = []
    for i in range(8):
        angle = -math.pi / 2 + i * math.pi / 4
        radius = r if i % 2 == 0 else r * inner
        pts.append(QPointF(cx + math.cos(angle) * radius, cy + math.sin(angle) * radius))
    path = QPainterPath()
    path.addPolygon(QPolygonF(pts))
    path.closeSubpath()
    return path


def draw_glyph(p: QPainter, kind: str, c: QPointF, size: float, color: QColor,
               filled: bool = False, lw: float = 0.0):
    """SF Symbols 风格的线性图标（全部用矢量路径绘制，缩放不糊）。"""
    u = float(size)
    lw = lw or max(1.6, u * 0.105)
    cx, cy = c.x(), c.y()
    p.save()
    p.setRenderHint(AA)
    pen = QPen(color, lw, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
               Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(NO_BRUSH)

    if kind == "send":                                   # arrow.up
        path = QPainterPath()
        path.moveTo(cx, cy + 0.32 * u)
        path.lineTo(cx, cy - 0.30 * u)
        path.moveTo(cx - 0.27 * u, cy - 0.03 * u)
        path.lineTo(cx, cy - 0.30 * u)
        path.lineTo(cx + 0.27 * u, cy - 0.03 * u)
        p.drawPath(path)

    elif kind == "stop":                                 # stop.fill
        p.setPen(NO_PEN)
        p.setBrush(color)
        s = 0.18 * u
        p.drawRoundedRect(QRectF(cx - s, cy - s, 2 * s, 2 * s), 0.08 * u, 0.08 * u)

    elif kind == "mic":                                  # mic
        p.setPen(NO_PEN)
        p.setBrush(color)
        cw = u * 0.26
        p.drawRoundedRect(QRectF(cx - cw / 2, cy - 0.38 * u, cw, 0.50 * u), cw / 2, cw / 2)
        p.setPen(pen)
        p.setBrush(NO_BRUSH)
        p.drawArc(QRectF(cx - 0.25 * u, cy - 0.20 * u, 0.50 * u, 0.46 * u), 180 * 16, 180 * 16)
        p.drawLine(QPointF(cx, cy + 0.26 * u), QPointF(cx, cy + 0.38 * u))
        p.drawLine(QPointF(cx - 0.10 * u, cy + 0.38 * u), QPointF(cx + 0.10 * u, cy + 0.38 * u))

    elif kind == "chat":                                 # message / message.fill
        rect = QRectF(cx - 0.42 * u, cy - 0.34 * u, 0.84 * u, 0.62 * u)
        path = QPainterPath()
        path.addRoundedRect(rect, 0.22 * u, 0.22 * u)
        tail = QPainterPath()
        tail.addPolygon(QPolygonF([QPointF(rect.left() + 0.15 * u, rect.bottom() - 0.04 * u),
                                   QPointF(rect.left() + 0.03 * u, rect.bottom() + 0.19 * u),
                                   QPointF(rect.left() + 0.32 * u, rect.bottom() - 0.04 * u)]))
        tail.closeSubpath()
        path = path.united(tail)
        if filled:
            p.setPen(NO_PEN)
            p.setBrush(color)
        else:
            p.setPen(pen)
            p.setBrush(NO_BRUSH)
        p.drawPath(path)

    elif kind == "trash":                                # trash
        path = QPainterPath()
        path.moveTo(cx - 0.31 * u, cy - 0.20 * u)
        path.lineTo(cx + 0.31 * u, cy - 0.20 * u)
        path.moveTo(cx - 0.10 * u, cy - 0.20 * u)
        path.lineTo(cx - 0.10 * u, cy - 0.32 * u)
        path.lineTo(cx + 0.10 * u, cy - 0.32 * u)
        path.lineTo(cx + 0.10 * u, cy - 0.20 * u)
        path.moveTo(cx - 0.22 * u, cy - 0.12 * u)
        path.lineTo(cx - 0.18 * u, cy + 0.30 * u)
        path.lineTo(cx + 0.18 * u, cy + 0.30 * u)
        path.lineTo(cx + 0.22 * u, cy - 0.12 * u)
        p.drawPath(path)

    elif kind == "chevron":                              # chevron.down
        path = QPainterPath()
        path.moveTo(cx - 0.27 * u, cy - 0.11 * u)
        path.lineTo(cx, cy + 0.15 * u)
        path.lineTo(cx + 0.27 * u, cy - 0.11 * u)
        p.setPen(QPen(color, lw * 1.1, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                      Qt.PenJoinStyle.RoundJoin))
        p.drawPath(path)

    elif kind == "chevron_right":
        path = QPainterPath()
        path.moveTo(cx - 0.12 * u, cy - 0.26 * u)
        path.lineTo(cx + 0.13 * u, cy)
        path.lineTo(cx - 0.12 * u, cy + 0.26 * u)
        p.drawPath(path)

    elif kind == "arrow_down":
        p.drawLine(QPointF(cx, cy - 0.30 * u), QPointF(cx, cy + 0.22 * u))
        path = QPainterPath()
        path.moveTo(cx - 0.22 * u, cy + 0.02 * u)
        path.lineTo(cx, cy + 0.25 * u)
        path.lineTo(cx + 0.22 * u, cy + 0.02 * u)
        p.drawPath(path)

    elif kind == "x":                                    # xmark
        p.drawLine(QPointF(cx - 0.24 * u, cy - 0.24 * u), QPointF(cx + 0.24 * u, cy + 0.24 * u))
        p.drawLine(QPointF(cx + 0.24 * u, cy - 0.24 * u), QPointF(cx - 0.24 * u, cy + 0.24 * u))

    elif kind == "check":
        path = QPainterPath()
        path.moveTo(cx - 0.30 * u, cy + 0.02 * u)
        path.lineTo(cx - 0.08 * u, cy + 0.24 * u)
        path.lineTo(cx + 0.32 * u, cy - 0.24 * u)
        p.setPen(QPen(color, lw * 1.25, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.drawPath(path)

    elif kind in ("gear", "settings"):
        p.setBrush(NO_BRUSH)
        p.drawEllipse(c, 0.27 * u, 0.27 * u)
        p.setPen(QPen(color, lw * 1.55, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap))
        for i in range(8):
            a = math.pi * i / 4
            p.drawLine(QPointF(cx + math.cos(a) * 0.28 * u, cy + math.sin(a) * 0.28 * u),
                       QPointF(cx + math.cos(a) * 0.37 * u, cy + math.sin(a) * 0.37 * u))
        p.drawEllipse(c, 0.10 * u, 0.10 * u)

    elif kind == "sun":
        p.drawEllipse(c, 0.17 * u, 0.17 * u)
        for i in range(8):
            a = math.pi * i / 4
            p.drawLine(QPointF(cx + math.cos(a) * 0.26 * u, cy + math.sin(a) * 0.26 * u),
                       QPointF(cx + math.cos(a) * 0.37 * u, cy + math.sin(a) * 0.37 * u))

    elif kind == "moon":
        outer = QPainterPath()
        outer.addEllipse(c, 0.32 * u, 0.32 * u)
        inner = QPainterPath()
        inner.addEllipse(c + QPointF(0.15 * u, -0.07 * u), 0.29 * u, 0.29 * u)
        p.setPen(NO_PEN)
        p.setBrush(color)
        p.drawPath(outer.subtracted(inner))

    elif kind in ("eye", "eye_off"):
        path = QPainterPath()
        path.moveTo(cx - 0.36 * u, cy)
        path.cubicTo(cx - 0.18 * u, cy - 0.28 * u, cx + 0.18 * u, cy - 0.28 * u,
                     cx + 0.36 * u, cy)
        path.cubicTo(cx + 0.18 * u, cy + 0.28 * u, cx - 0.18 * u, cy + 0.28 * u,
                     cx - 0.36 * u, cy)
        p.setBrush(NO_BRUSH)
        p.drawPath(path)
        p.setPen(NO_PEN)
        p.setBrush(color)
        p.drawEllipse(c, 0.09 * u, 0.09 * u)
        if kind == "eye_off":
            p.setPen(QPen(color, lw, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(QPointF(cx - 0.30 * u, cy + 0.30 * u),
                       QPointF(cx + 0.30 * u, cy - 0.30 * u))

    elif kind == "plus":
        p.drawLine(QPointF(cx - 0.24 * u, cy), QPointF(cx + 0.24 * u, cy))
        p.drawLine(QPointF(cx, cy - 0.24 * u), QPointF(cx, cy + 0.24 * u))

    elif kind == "robot":
        rect = QRectF(cx - 0.32 * u, cy - 0.18 * u, 0.64 * u, 0.46 * u)
        p.drawRoundedRect(rect, 0.14 * u, 0.14 * u)
        p.drawLine(QPointF(cx, cy - 0.18 * u), QPointF(cx, cy - 0.34 * u))
        p.setBrush(color)
        p.drawEllipse(QPointF(cx, cy - 0.37 * u), 0.045 * u, 0.045 * u)
        p.drawEllipse(QPointF(cx - 0.13 * u, cy + 0.02 * u), 0.045 * u, 0.045 * u)
        p.drawEllipse(QPointF(cx + 0.13 * u, cy + 0.02 * u), 0.045 * u, 0.045 * u)
        p.setBrush(NO_BRUSH)
        p.drawLine(QPointF(cx - 0.11 * u, cy + 0.15 * u), QPointF(cx + 0.11 * u, cy + 0.15 * u))

    elif kind == "sparkles":
        p.setPen(NO_PEN)
        p.setBrush(color)
        p.drawPath(_star_path(cx - 0.06 * u, cy + 0.05 * u, 0.28 * u))
        p.drawPath(_star_path(cx + 0.22 * u, cy - 0.20 * u, 0.14 * u))
        p.drawPath(_star_path(cx - 0.28 * u, cy - 0.22 * u, 0.09 * u))

    elif kind == "info":
        p.drawEllipse(c, 0.33 * u, 0.33 * u)
        p.drawLine(QPointF(cx, cy - 0.02 * u), QPointF(cx, cy + 0.17 * u))
        p.setBrush(color)
        p.drawEllipse(QPointF(cx, cy - 0.13 * u), 0.035 * u, 0.035 * u)

    elif kind == "key":
        p.drawEllipse(QPointF(cx - 0.16 * u, cy - 0.05 * u), 0.16 * u, 0.16 * u)
        path = QPainterPath()
        path.moveTo(cx - 0.02 * u, cy + 0.09 * u)
        path.lineTo(cx + 0.32 * u, cy + 0.38 * u)
        path.moveTo(cx + 0.20 * u, cy + 0.27 * u)
        path.lineTo(cx + 0.30 * u, cy + 0.17 * u)
        p.drawPath(path)

    elif kind == "copy":
        p.drawRoundedRect(QRectF(cx - 0.28 * u, cy - 0.28 * u, 0.40 * u, 0.40 * u),
                          0.07 * u, 0.07 * u)
        p.drawRoundedRect(QRectF(cx - 0.12 * u, cy - 0.12 * u, 0.40 * u, 0.40 * u),
                          0.07 * u, 0.07 * u)

    elif kind == "palette":
        p.drawEllipse(c, 0.33 * u, 0.33 * u)
        p.setPen(NO_PEN)
        p.setBrush(color)
        for dx, dy in ((-0.13, -0.10), (0.02, -0.16), (0.15, -0.02), (0.06, 0.15)):
            p.drawEllipse(QPointF(cx + dx * u, cy + dy * u), 0.045 * u, 0.045 * u)

    elif kind == "volume":
        path = QPainterPath()
        path.moveTo(cx - 0.30 * u, cy - 0.10 * u)
        path.lineTo(cx - 0.16 * u, cy - 0.10 * u)
        path.lineTo(cx + 0.02 * u, cy - 0.28 * u)
        path.lineTo(cx + 0.02 * u, cy + 0.28 * u)
        path.lineTo(cx - 0.16 * u, cy + 0.10 * u)
        path.lineTo(cx - 0.30 * u, cy + 0.10 * u)
        path.closeSubpath()
        p.setBrush(color)
        p.setPen(NO_PEN)
        p.drawPath(path)
        p.setBrush(NO_BRUSH)
        p.setPen(pen)
        p.drawArc(QRectF(cx - 0.08 * u, cy - 0.20 * u, 0.34 * u, 0.40 * u), -55 * 16, 110 * 16)

    elif kind == "bolt":
        path = QPainterPath()
        path.moveTo(cx + 0.05 * u, cy - 0.34 * u)
        path.lineTo(cx - 0.20 * u, cy + 0.06 * u)
        path.lineTo(cx - 0.02 * u, cy + 0.06 * u)
        path.lineTo(cx - 0.08 * u, cy + 0.34 * u)
        path.lineTo(cx + 0.22 * u, cy - 0.10 * u)
        path.lineTo(cx + 0.03 * u, cy - 0.10 * u)
        path.closeSubpath()
        p.setBrush(color)
        p.setPen(NO_PEN)
        p.drawPath(path)

    p.restore()


ORB_BLOBS = [  # (颜色, 相位偏移, 半径系数, 角速度系数)
    (QColor(70, 225, 255), 0.0, 0.90, 0.90),
    (QColor(182, 112, 255), 2.1, 0.85, -0.65),
    (QColor(255, 132, 200), 4.2, 0.75, 1.15),
]


def draw_orb(p: QPainter, c: QPointF, r: float, phase: float = 0.8, energy: float = 0.0,
             rec: float = 0.0, level: float = 0.0, glyph: bool = True):
    """类 Siri 的流光球：蓝色球体 + 三团缓慢游走的彩色光斑 + 高光 + 边缘光。
    energy：0 空闲 → 1 激活（光斑更亮更大幅度）；rec：0 → 1 渐变为录音红色并显示麦克风。"""
    p.save()
    p.setRenderHint(AA)

    if rec > 0.02:                                       # 聆听时向外扩散的声波环
        p.setBrush(NO_BRUSH)
        for i in range(2):
            t = (phase * 0.55 + i * 0.5) % 1.0
            rr = r * (1.05 + 0.50 * t) + level * r * 0.30
            p.setPen(QPen(QColor(255, 69, 58, int(150 * rec * (1.0 - t))), 1.6))
            p.drawEllipse(c, rr, rr)

    clip = QPainterPath()
    clip.addEllipse(c, r, r)
    p.setClipPath(clip)
    box = QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r)

    base = QRadialGradient(c + QPointF(-r * 0.30, -r * 0.50), r * 2.05)
    base.setColorAt(0.00, mix(QColor(150, 218, 255), QColor(255, 164, 138), rec))
    base.setColorAt(0.30, mix(QColor(48, 152, 255), QColor(255, 92, 76), rec))
    base.setColorAt(0.66, mix(QColor(10, 120, 248), QColor(230, 52, 64), rec))
    base.setColorAt(1.00, mix(QColor(18, 42, 176), QColor(132, 18, 54), rec))
    p.fillRect(box, QBrush(base))

    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Screen)
    amp = 0.40 + 0.10 * energy
    for col, off, size, spd in ORB_BLOBS:
        a = phase * spd + off
        pos = c + QPointF(math.cos(a) * r * amp, math.sin(a * 1.27 + off) * r * amp)
        col = mix(col, QColor(255, 160, 110), rec * 0.8)
        rg = QRadialGradient(pos, r * size)
        rg.setColorAt(0.0, with_alpha(col, 125 + 80 * energy))
        rg.setColorAt(1.0, with_alpha(col, 0))
        p.fillRect(box, QBrush(rg))
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

    depth = QRadialGradient(c + QPointF(r * 0.34, r * 0.48), r * 1.25)   # 右下内阴影
    depth.setColorAt(0.00, QColor(2, 8, 38, 0))
    depth.setColorAt(0.58, QColor(2, 8, 38, 0))
    depth.setColorAt(0.88, QColor(2, 8, 38, 58))
    depth.setColorAt(1.00, QColor(2, 8, 38, 86))
    p.fillRect(box, QBrush(depth))

    hc = c + QPointF(-r * 0.28, -r * 0.46)               # 左上高光
    hl = QRadialGradient(hc, r * 0.92)
    hl.setColorAt(0.0, QColor(255, 255, 255, 145))
    hl.setColorAt(0.42, QColor(255, 255, 255, 58))
    hl.setColorAt(1.0, QColor(255, 255, 255, 0))
    p.fillRect(box, QBrush(hl))

    spark = c + QPointF(-r * 0.40, -r * 0.47)            # 一点锐利高光
    sh = QRadialGradient(spark, r * 0.30)
    sh.setColorAt(0.0, QColor(255, 255, 255, 205))
    sh.setColorAt(1.0, QColor(255, 255, 255, 0))
    p.fillRect(box, QBrush(sh))

    bl = QLinearGradient(c + QPointF(0, r * 0.20), c + QPointF(0, r))   # 底部反光
    bl.setColorAt(0.0, QColor(255, 255, 255, 0))
    bl.setColorAt(1.0, QColor(255, 255, 255, 54))
    p.fillRect(box, QBrush(bl))
    p.setClipping(False)

    # 缓缓转动的玻璃边缘反光，让静止的球也有一点生命感
    start = int((phase * 42.0) % 360)
    p.setPen(QPen(QColor(255, 255, 255, 82), max(1.0, r * 0.045),
                  Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.setBrush(NO_BRUSH)
    p.drawArc(box.adjusted(0.8, 0.8, -0.8, -0.8), start * 16, 54 * 16)
    p.setPen(QPen(QColor(255, 255, 255, 34), 1.0))
    p.drawArc(box.adjusted(1.2, 1.2, -1.2, -1.2), (start + 188) * 16, 28 * 16)

    rim = QLinearGradient(c + QPointF(0, -r), c + QPointF(0, r))        # 边缘光
    rim.setColorAt(0.0, QColor(255, 255, 255, 178))
    rim.setColorAt(0.45, QColor(255, 255, 255, 40))
    rim.setColorAt(1.0, QColor(255, 255, 255, 92))
    p.setPen(QPen(QBrush(rim), max(1.0, r * 0.045)))
    p.setBrush(NO_BRUSH)
    p.drawEllipse(c, r - 0.5, r - 0.5)

    if glyph:
        sa = 1.0 - rec
        if sa > 0.01:
            draw_glyph(p, "chat", c + QPointF(0, -r * 0.02), r * 0.92,
                       QColor(255, 255, 255, int(232 * sa)), filled=True)
        if rec > 0.01:
            draw_glyph(p, "mic", c, r * 0.95, QColor(255, 255, 255, int(255 * rec)))
    p.restore()


def make_ball_pixmap(size: int = 64) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    draw_orb(p, QPointF(size / 2, size / 2), size / 2 - 3)
    p.end()
    return pm


def make_icon(kind: str, size: int = 20, color: QColor | None = None,
              filled: bool = False) -> QIcon:
    """把矢量图标包成 QIcon（2x 采样，菜单 / 按钮里也清晰）。"""
    color = QColor(color or QColor(0, 0, 0))
    dpr = 2.0
    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(AA)
    draw_glyph(p, kind, QPointF(size / 2, size / 2), size * 0.92, color, filled=filled)
    p.end()
    return QIcon(pm)
