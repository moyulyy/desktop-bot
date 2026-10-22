# -*- coding: utf-8 -*-
"""设置页与工具授权对话框。

* :class:`SettingsDialog` —— 模型 / 语音 / 外观 / 子智能体 / 关于
* :class:`PermissionDialog` —— 工具调用前的授权确认

两者都沿用主界面的 iOS 风格：无边框、圆角玻璃卡片、跟随深色/浅色主题。
对话框只负责收集与校验配置，真正落盘由主程序调用 ``config_store`` 完成。
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Callable, Optional

from PySide6.QtCore import (
    QEvent,
    QObject,
    QPoint,
    QPointF,
    QRectF,
    QSize,
    Qt,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import QColor, QDesktopServices, QFont, QPainter
from PySide6.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
from .local_subagents import available_subagents
from .config_store import (
    AGENT_CONFIG_PATH,
    ASR_CONFIG_PATH,
    BASE_DIR,
    APP_NAME,
    APP_VERSION,
    DEFAULT_AGENT_CONFIG,
    ENGINES,
    PERMISSION_MODES,
    REGIONS,
    normalize_agent_config,
    normalize_asr_config,
    normalize_prefs,
)
from .ui_kit import (
    A_CENTER,
    AA,
    NO_PEN,
    RED,
    THEME_DARK,
    THEME_LIGHT,
    Theme,
    css,
    draw_glyph,
    make_icon,
    ui_font,
    with_alpha,
)


# --------------------------------------------------------------------------- #
# 通用样式与控件
# --------------------------------------------------------------------------- #
def dialog_qss(th: Theme) -> str:
    return f"""
        QDialog {{ background: transparent; }}
        QFrame#panel {{ background: {css(th.card_top)}; border: 1px solid {css(th.hairline)};
                        border-radius: 22px; }}
        QWidget#header, QWidget#footer, QWidget#sidebar, QWidget#pageBody {{ background: transparent; }}
        QWidget#sidebar {{ background: {css(th.sidebar_bg)};
                           border-top-left-radius: 22px; border-bottom-left-radius: 22px;
                           border-right: 1px solid {css(th.hairline)}; }}
        QLabel {{ color: {css(th.label)}; background: transparent; }}
        QLabel#title {{ font-size: 19px; font-weight: 600; }}
        QLabel#subtitle, QLabel#hint {{ color: {css(th.label2)}; font-size: 11px; }}
        QLabel#footerHint {{ color: {css(th.label3)}; font-size: 11px; }}
        QLabel#section {{ color: {css(th.label2)}; font-size: 12px; font-weight: 600; }}
        QLabel#error {{ color: {css(RED)}; font-size: 11px; }}
        QLabel#ok {{ color: {css(th.success)}; font-size: 11px; }}
        QFrame#card {{ background: {css(th.fill)}; border: 1px solid {css(th.hairline)};
                       border-radius: 14px; }}
        QFrame#divider {{ background: {css(th.sep)}; max-height: 1px; min-height: 1px; }}
        QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
        QListWidget#nav {{ background: transparent; border: none; outline: none;
                           font-size: 13px; }}
        QListWidget#nav::item {{ color: {css(th.label2)}; height: 42px; border-radius: 10px;
                                 padding-left: 12px; margin: 2px 10px; }}
        QListWidget#nav::item:hover {{ background: {css(th.fill)}; color: {css(th.label)}; }}
        QListWidget#nav::item:selected {{ background: {css(th.accent_soft)};
                                          color: {css(th.accent)}; font-weight: 600; }}
        QListWidget#subList {{ background: {css(th.input_bg)}; border: 1px solid {css(th.hairline)};
                               border-radius: 10px; outline: none; padding: 4px; }}
        QListWidget#subList::item {{ color: {css(th.label)}; padding: 7px 6px; border-radius: 6px;
                                     min-height: 22px; }}
        QListWidget#subList::item:selected {{ background: {css(th.accent_soft)};
                                              color: {css(th.accent)}; }}
        QListWidget#subList::indicator {{ width: 13px; height: 13px; border-radius: 4px;
            border: 1px solid {css(th.hairline)}; background: {css(th.input_bg)}; }}
        QListWidget#subList::indicator:checked {{ background: {css(th.accent)};
            border: 1px solid {css(th.accent)}; }}
        QLineEdit, QPlainTextEdit, QComboBox, QSpinBox {{
            background: {css(th.input_bg)}; border: 1px solid {css(th.hairline)};
            border-radius: 9px; padding: 7px 10px; color: {css(th.label)};
            selection-background-color: {css(th.accent)}; selection-color: #FFFFFF; }}
        QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus {{
            border: 1px solid {css(th.accent)}; }}
        QLineEdit:disabled, QPlainTextEdit:disabled, QComboBox:disabled, QSpinBox:disabled {{
            color: {css(th.label3)}; }}
        QPlainTextEdit {{ padding: 8px 10px; }}
        QPlainTextEdit#summary {{ font-family: Consolas, "Cascadia Mono", monospace;
                                  font-size: 11px; }}
        QComboBox::drop-down {{ border: none; width: 26px; }}
        QComboBox QAbstractItemView {{ background: {css(th.menu_bg)}; color: {css(th.label)};
            border: 1px solid {css(th.hairline)}; border-radius: 10px; padding: 4px;
            selection-background-color: {css(th.accent)}; selection-color: #FFFFFF;
            outline: none; }}
        QSpinBox::up-button, QSpinBox::down-button {{ width: 16px; border: none;
            background: transparent; }}
        QPushButton {{ background: {css(th.fill)}; color: {css(th.label)}; border: none;
                       border-radius: 9px; padding: 8px 16px; font-size: 12px; }}
        QPushButton:hover {{ background: {css(th.fill_hi)}; }}
        QPushButton:disabled {{ color: {css(th.label3)}; }}
        QPushButton#primary {{ background: {css(th.accent)}; color: #FFFFFF; font-weight: 600;
                               padding: 8px 20px; }}
        QPushButton#primary:hover {{ background: {css(th.accent.lighter(112))}; }}
        QPushButton#ghost {{ background: transparent; color: {css(th.label2)}; }}
        QPushButton#ghost:hover {{ background: {css(th.fill)}; color: {css(th.label)}; }}
        QPushButton#danger {{ background: transparent; color: {css(RED)}; }}
        QPushButton#danger:hover {{ background: {css(with_alpha(RED, 34))}; }}
        QPushButton#iconbutton {{ background: transparent; border-radius: 9px; padding: 0; }}
        QPushButton#iconbutton:hover {{ background: {css(th.fill)}; }}
        QPushButton#segment {{ background: transparent; border: 1px solid transparent;
                               color: {css(th.label2)}; padding: 7px 18px; border-radius: 8px; }}
        QPushButton#segment:hover {{ color: {css(th.label)}; }}
        QPushButton#segment:checked {{ background: {css(th.card_top)};
            border: 1px solid {css(th.hairline)}; color: {css(th.label)}; font-weight: 600; }}
        QSlider::groove:horizontal {{ height: 4px; background: {css(th.fill_hi)};
                                      border-radius: 2px; }}
        QSlider::sub-page:horizontal {{ background: {css(th.accent)}; border-radius: 2px; }}
        QSlider::handle:horizontal {{ width: 16px; height: 16px; margin: -7px 0;
            border-radius: 9px; background: #FFFFFF; border: 1px solid {css(th.hairline)}; }}
        QCheckBox {{ color: {css(th.label2)}; font-size: 12px; spacing: 8px; }}
        QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 5px;
            border: 1px solid {css(th.hairline)}; background: {css(th.input_bg)}; }}
        QCheckBox::indicator:checked {{ background: {css(th.accent)};
            border: 1px solid {css(th.accent)}; }}
        QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
        QScrollBar::handle:vertical {{ background: {css(th.fill_hi)}; border-radius: 4px;
                                       min-height: 28px; }}
        QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
        QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
    """


class ComboBox(QComboBox):
    """QComboBox 在自定义 QSS 下常常失去下拉箭头，这里补一个一致的。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.theme = THEME_DARK

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(AA)
        draw_glyph(p, "chevron", QPointF(self.width() - 17, self.height() / 2),
                   10, self.theme.label2)
        p.end()


class Switch(QAbstractButton):
    """iOS 风格的开关；带 180ms 的滑块动画。"""

    def __init__(self, checked: bool = False, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(46, 28)
        self.theme = THEME_DARK
        self._pos = 1.0 if checked else 0.0
        self._anim = QTimer(self)
        self._anim.setInterval(16)
        self._anim.timeout.connect(self._step)
        self.toggled.connect(self._animate)

    def _animate(self, on: bool):
        self._anim.start()

    def _step(self):
        target = 1.0 if self.isChecked() else 0.0
        self._pos += (target - self._pos) * 0.30
        if abs(target - self._pos) < 0.01:
            self._pos = target
            self._anim.stop()
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(46, 28)

    def paintEvent(self, _):
        th = self.theme
        p = QPainter(self)
        p.setRenderHint(AA)
        r = self.rect().adjusted(1, 1, -1, -1)
        on = self._pos
        track = QColor(120, 120, 128, 92) if th.dark else QColor(120, 120, 128, 66)
        if on > 0.01:
            track = QColor(
                round(track.red() + (th.accent.red() - track.red()) * on),
                round(track.green() + (th.accent.green() - track.green()) * on),
                round(track.blue() + (th.accent.blue() - track.blue()) * on),
                255)
        p.setPen(NO_PEN)
        p.setBrush(track)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        d = r.height() - 4
        x = r.left() + 2 + on * (r.width() - d - 4)
        p.setBrush(QColor(255, 255, 255))
        p.drawEllipse(QRectF(x, r.top() + 2, d, d))
        p.end()


class GlassDialog(QDialog):
    """无边框玻璃卡片对话框，带标题栏拖动。"""

    def __init__(self, parent, theme: Theme, width: int, height: int):
        super().__init__(parent)
        self.theme = theme
        self._centered = False
        self._drag_at: Optional[QPoint] = None
        self.setWindowTitle("设置")
        # Tool 让设置 / 授权窗口不出现在任务栏，也不会占用 Alt+Tab。
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.Tool
                            | Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setModal(True)
        self.resize(width, height)
        self.setFont(ui_font(13))
        self.setStyleSheet(dialog_qss(theme))

        outer = QVBoxLayout(self)
        outer.setContentsMargins(26, 26, 26, 26)
        self.panel = QFrame(self)
        self.panel.setObjectName("panel")
        shadow = QGraphicsDropShadowEffect(self.panel)
        shadow.setBlurRadius(46)
        shadow.setOffset(0, 14)
        shadow.setColor(QColor(0, 0, 0, 130))
        self.panel.setGraphicsEffect(shadow)
        outer.addWidget(self.panel)

        self.root = QVBoxLayout(self.panel)
        self.root.setContentsMargins(0, 0, 0, 0)
        self.root.setSpacing(0)

        self.header = QWidget(self.panel)
        self.header.setObjectName("header")
        self.header.setFixedHeight(68)
        self.header_layout = QHBoxLayout(self.header)
        self.header_layout.setContentsMargins(24, 10, 16, 8)
        self.header_layout.setSpacing(12)
        self.root.addWidget(self.header)

        self.body = QWidget(self.panel)
        self.body_layout = QHBoxLayout(self.body)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSpacing(0)
        self.root.addWidget(self.body, 1)

        self.footer = QWidget(self.panel)
        self.footer.setObjectName("footer")
        self.footer.setFixedHeight(66)
        self.footer_layout = QHBoxLayout(self.footer)
        self.footer_layout.setContentsMargins(24, 10, 20, 14)
        self.footer_layout.setSpacing(8)
        self.root.addWidget(self.footer)

        self._make_draggable(self.header)

    def _make_draggable(self, widget: QWidget):
        widget.installEventFilter(self)

    def eventFilter(self, obj, event):
        if obj is self.header:
            et = event.type()
            if et == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                self._drag_at = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                return True
            if et == QEvent.Type.MouseMove and self._drag_at is not None:
                self.move(event.globalPosition().toPoint() - self._drag_at)
                return True
            if et == QEvent.Type.MouseButtonRelease:
                self._drag_at = None
                return True
        return super().eventFilter(obj, event)

    def _fit_to_screen(self, available) -> None:
        w = min(self.width(), max(420, available.width() - 24))
        h = min(self.height(), max(360, available.height() - 24))
        if (w, h) != (self.width(), self.height()):
            self.resize(w, h)

    def showEvent(self, e):
        super().showEvent(e)
        if not self._centered:
            self._centered = True
            self._center()
        # 悬浮球自身是置顶的 Tool 窗口；对话框显示后必须主动抬到它上面，
        # 否则会出现「对话框被主面板盖住、只露出底部按钮」的重叠问题。
        self.raise_()
        self.activateWindow()

    def _center(self):
        screen = self.screen() or (self.parentWidget().screen()
                                   if self.parentWidget() else None)
        if screen is None:
            return
        av = screen.availableGeometry()
        self._fit_to_screen(av)
        parent = self.parentWidget()
        if parent is not None and parent.isVisible():
            geo = parent.window().frameGeometry()
            cx, cy = geo.center().x(), geo.center().y()
        else:
            cx, cy = av.center().x(), av.center().y()
        x = max(av.left() + 12, min(cx - self.width() // 2, av.right() - self.width() - 12))
        y = max(av.top() + 12, min(cy - self.height() // 2, av.bottom() - self.height() - 12))
        self.move(x, y)


def _label(text: str, obj: str = "", wrap: bool = False) -> QLabel:
    lab = QLabel(text)
    if obj:
        lab.setObjectName(obj)
    lab.setWordWrap(wrap)
    return lab


def _card() -> tuple[QFrame, QVBoxLayout]:
    card = QFrame()
    card.setObjectName("card")
    lay = QVBoxLayout(card)
    lay.setContentsMargins(16, 14, 16, 14)
    lay.setSpacing(11)
    return card, lay


def _field(title: str, widget: QWidget, hint: str = "") -> QWidget:
    box = QWidget()
    lay = QVBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(5)
    lay.addWidget(_label(title, "section"))
    lay.addWidget(widget)
    if hint:
        lay.addWidget(_label(hint, "hint", wrap=True))
    return box


def _switch_row(title: str, desc: str, switch: Switch) -> QWidget:
    box = QWidget()
    lay = QHBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(12)
    text = QVBoxLayout()
    text.setSpacing(2)
    text.addWidget(_label(title))
    if desc:
        text.addWidget(_label(desc, "hint", wrap=True))
    lay.addLayout(text, 1)
    lay.addWidget(switch, 0, Qt.AlignmentFlag.AlignVCenter)
    return box


def _secret_edit(text: str, th: Theme) -> tuple[QLineEdit, Callable[[], None]]:
    edit = QLineEdit(text)
    edit.setEchoMode(QLineEdit.EchoMode.Password)
    action = edit.addAction(make_icon("eye", 16, th.label2),
                            QLineEdit.ActionPosition.TrailingPosition)
    action.setToolTip("显示 / 隐藏")

    def toggle():
        hidden = edit.echoMode() == QLineEdit.EchoMode.Password
        edit.setEchoMode(QLineEdit.EchoMode.Normal if hidden
                         else QLineEdit.EchoMode.Password)
        action.setIcon(make_icon("eye_off" if hidden else "eye", 16, th.label2))

    action.triggered.connect(toggle)
    return edit, toggle


class ConnectionTester(QObject):
    """在后台线程里发一次最小请求，避免阻塞设置页。"""

    result_ready = Signal(bool, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._busy = False

    @property
    def busy(self) -> bool:
        return self._busy

    def start(self, cfg: dict):
        if self._busy:
            return
        self._busy = True
        threading.Thread(target=self._work, args=(dict(cfg),), daemon=True).start()

    def _emit_result(self, ok: bool, message: str):
        try:
            self.result_ready.emit(ok, message)
        except RuntimeError:
            # 对话框已经销毁（测试连接尚未返回），忽略即可。
            pass

    def _work(self, cfg: dict):
        import requests
        started = time.monotonic()
        try:
            base = (cfg.get("base_url") or "").rstrip("/")
            if not base or not cfg.get("model"):
                raise ValueError("请先填写 Base URL 与模型名")
            if not cfg.get("api_key"):
                raise ValueError("请先填写 API Key")
            resp = requests.post(
                f"{base}/chat/completions",
                headers={"Authorization": f"Bearer {cfg['api_key']}",
                         "Content-Type": "application/json"},
                json={"model": cfg["model"],
                      "messages": [{"role": "user", "content": "回复 OK"}],
                      "max_tokens": 8, "stream": False},
                timeout=20)
            elapsed = time.monotonic() - started
            try:
                data = resp.json()
            except ValueError:
                data = {}
            if resp.status_code >= 400:
                err = data.get("error") if isinstance(data, dict) else None
                if isinstance(err, dict):
                    detail = err.get("message") or err.get("code") or str(err)
                else:
                    detail = err or (resp.text or "")[:160]
                self._emit_result(False, f"连接失败（HTTP {resp.status_code}）：{detail}")
                return
            text = ""
            try:
                text = data["choices"][0]["message"].get("content") or ""
            except (KeyError, IndexError, TypeError):
                pass
            self._emit_result(True, f"连接成功 · {cfg['model']} · {elapsed:.1f}s"
                                    + (f" · {text.strip()[:20]}" if text else ""))
        except Exception as exc:                              # noqa: BLE001
            self._emit_result(False, f"连接失败：{type(exc).__name__}: {exc}")
        finally:
            self._busy = False


# --------------------------------------------------------------------------- #
# 设置页
# --------------------------------------------------------------------------- #
class SettingsDialog(GlassDialog):
    PRESETS = [
        ("DeepSeek", "https://api.deepseek.com", "deepseek-chat"),
        ("OpenAI", "https://api.openai.com/v1", "gpt-4o-mini"),
        ("通义千问 (DashScope)", "https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-plus"),
        ("自定义", "", ""),
    ]

    def __init__(self, parent, agent_cfg: dict, asr_cfg: dict, prefs: dict, dark: bool = True):
        self.agent_cfg = normalize_agent_config(agent_cfg)
        self.asr_cfg = normalize_asr_config(asr_cfg)
        self.prefs = normalize_prefs(prefs)
        self.result_data: Optional[dict] = None
        self._reset_position = False
        self._test_busy = False
        self._sub_loading = False
        super().__init__(parent, THEME_DARK if dark else THEME_LIGHT, 860, 700)
        self.setWindowTitle("设置")
        self._build_header()
        self._build_pages()
        self._build_footer()
        self._tester = ConnectionTester(self)
        self._tester.result_ready.connect(self._on_test_result)
        for switch in self.findChildren(Switch):
            switch.theme = self.theme
        for combo in self.findChildren(ComboBox):
            combo.theme = self.theme
        self._load_values()

    # ---- 骨架 ---------------------------------------------------------- #
    def _build_header(self):
        icon = QLabel()
        pm = make_icon("gear", 26, self.theme.label).pixmap(26, 26)
        icon.setPixmap(pm)
        icon.setFixedSize(34, 34)
        icon.setAlignment(A_CENTER)
        self.header_layout.addWidget(icon)
        titles = QVBoxLayout()
        titles.setSpacing(1)
        titles.addWidget(_label("设置", "title"))
        titles.addWidget(_label("模型、语音、外观与子智能体", "subtitle"))
        self.header_layout.addLayout(titles)
        self.header_layout.addStretch(1)
        close = QPushButton()
        close.setObjectName("iconbutton")
        close.setFixedSize(32, 32)
        close.setIcon(make_icon("x", 16, self.theme.label2))
        close.setIconSize(QSize(16, 16))
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setToolTip("关闭")
        close.clicked.connect(self.reject)
        self.header_layout.addWidget(close)

    def _build_pages(self):
        self.sidebar = QWidget()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setFixedWidth(196)
        side_lay = QVBoxLayout(self.sidebar)
        side_lay.setContentsMargins(0, 10, 0, 12)
        side_lay.setSpacing(6)
        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.setIconSize(QSize(18, 18))
        self.nav.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        side_lay.addWidget(self.nav, 1)
        version = _label(f"v{APP_VERSION}", "hint")
        version.setContentsMargins(24, 0, 0, 0)
        side_lay.addWidget(version)
        self.body_layout.addWidget(self.sidebar)

        self.stack = QStackedWidget()
        self.body_layout.addWidget(self.stack, 1)

        self.model_page = self._build_model_page()
        self.voice_page = self._build_voice_page()
        self.look_page = self._build_look_page()
        self.sub_page = self._build_subagent_page()
        self.about_page = self._build_about_page()

        for title, kind, page in (
            ("模型", "sparkles", self.model_page),
            ("语音", "mic", self.voice_page),
            ("外观", "palette", self.look_page),
            ("子智能体", "robot", self.sub_page),
            ("关于", "info", self.about_page),
        ):
            item = QListWidgetItem(make_icon(kind, 18, self.theme.label2), f"  {title}")
            item.setSizeHint(QSize(160, 44))
            self.nav.addItem(item)
            self.stack.addWidget(page)
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.nav.setCurrentRow(0)

    def _build_footer(self):
        self.footer_hint = _label("修改后点击「保存」生效", "footerHint")
        self.footer_layout.addWidget(self.footer_hint)
        self.footer_layout.addStretch(1)
        cancel = QPushButton("取消")
        cancel.setObjectName("ghost")
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.clicked.connect(self.reject)
        save = QPushButton("保存")
        save.setObjectName("primary")
        save.setCursor(Qt.CursorShape.PointingHandCursor)
        save.clicked.connect(self._save)
        self.footer_layout.addWidget(cancel)
        self.footer_layout.addWidget(save)

    def _page(self) -> tuple[QScrollArea, QVBoxLayout]:
        content = QWidget()
        content.setObjectName("pageBody")
        lay = QVBoxLayout(content)
        lay.setContentsMargins(26, 18, 26, 24)
        lay.setSpacing(14)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(content)
        return scroll, lay

    # ---- 模型页 -------------------------------------------------------- #
    def _build_model_page(self) -> QWidget:
        scroll, lay = self._page()

        card, c = _card()
        self.preset = ComboBox()
        for name, _, _ in self.PRESETS:
            self.preset.addItem(name)
        self.preset.activated.connect(self._apply_preset)
        c.addWidget(_field("服务商", self.preset, "选择后会自动填好接口地址与常用模型，可再手动修改。"))

        self.api_key, _ = _secret_edit(self.agent_cfg.get("api_key", ""), self.theme)
        self.api_key.setPlaceholderText("sk-…")
        c.addWidget(_field("API Key", self.api_key,
                           "仅保存在本机 config/agent_config.json；环境变量 OPENAI_API_KEY 可覆盖。"))

        self.base_url = QLineEdit(self.agent_cfg.get("base_url", ""))
        self.base_url.setPlaceholderText("https://api.openai.com/v1")
        c.addWidget(_field("Base URL", self.base_url,
                           "兼容 OpenAI /chat/completions 协议的地址。"))

        self.model = QLineEdit(self.agent_cfg.get("model", ""))
        self.model.setPlaceholderText("gpt-4o-mini")
        c.addWidget(_field("模型", self.model))

        test_row = QWidget()
        tl = QHBoxLayout(test_row)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(10)
        self.test_btn = QPushButton("测试连接")
        self.test_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.test_btn.clicked.connect(self._test_connection)
        self.test_state = QLabel("")
        self.test_state.setObjectName("hint")
        self.test_state.setWordWrap(True)
        tl.addWidget(self.test_btn)
        tl.addWidget(self.test_state, 1)
        c.addWidget(test_row)
        lay.addWidget(card)

        card, c = _card()
        temp_row = QWidget()
        tr = QHBoxLayout(temp_row)
        tr.setContentsMargins(0, 0, 0, 0)
        tr.setSpacing(12)
        self.temp = QSlider(Qt.Orientation.Horizontal)
        self.temp.setRange(0, 20)
        self.temp.setSingleStep(1)
        self.temp.setPageStep(2)
        self.temp_label = _label("0.7", "hint")
        self.temp_label.setFixedWidth(30)
        self.temp.valueChanged.connect(
            lambda v: self.temp_label.setText(f"{v / 10:.1f}"))
        tr.addWidget(self.temp, 1)
        tr.addWidget(self.temp_label)
        c.addWidget(_field("创造力（temperature）", temp_row,
                           "数值越低越稳定，越高越发散；0.7 适合日常对话。"))

        nums = QWidget()
        nl = QHBoxLayout(nums)
        nl.setContentsMargins(0, 0, 0, 0)
        nl.setSpacing(14)
        self.max_tokens = QSpinBox()
        self.max_tokens.setRange(256, 8192)
        self.max_tokens.setSingleStep(128)
        self.max_tokens.setSuffix(" tokens")
        self.max_turns = QSpinBox()
        self.max_turns.setRange(1, 60)
        self.max_turns.setSuffix(" 轮")
        nl.addWidget(_field("单次回复上限", self.max_tokens))
        nl.addWidget(_field("最多工具轮数", self.max_turns))
        c.addWidget(nums)

        self.stream = Switch(bool(self.agent_cfg.get("stream", True)))
        c.addWidget(_switch_row("流式输出", "回复逐字出现，响应更快、更自然。", self.stream))
        lay.addWidget(card)

        card, c = _card()
        self.permission = ComboBox()
        for key, text in PERMISSION_MODES.items():
            self.permission.addItem(text, key)
        self.permission.currentIndexChanged.connect(self._update_permission_hint)
        self.perm_hint = _label("", "hint", wrap=True)
        c.addWidget(_field("工具权限", self.permission))
        c.addWidget(self.perm_hint)
        self.system = QPlainTextEdit()
        self.system.setFixedHeight(106)
        self.system.setPlaceholderText("你是一个桌面助手…")
        c.addWidget(_field("系统提示词", self.system,
                           "描述助手的身份、语气与工作方式；子智能体会继承自己的提示词。"))
        lay.addWidget(card)

        note = _label(
            "内置工具：时间、读取 / 写入 / 编辑文件、列目录、执行命令、抓取网页、打开链接。"
            "写操作与命令默认会先征求你的同意。", "hint", wrap=True)
        lay.addWidget(note)
        lay.addStretch(1)
        return scroll

    def _apply_preset(self, index: int):
        if index < 0 or index >= len(self.PRESETS):
            return
        _, base, model = self.PRESETS[index]
        if not base:
            return
        if not self.base_url.text().strip() or self.base_url.text().strip() != base:
            self.base_url.setText(base)
        if not self.model.text().strip():
            self.model.setText(model)

    def _detect_preset(self):
        base = (self.agent_cfg.get("base_url") or "").lower()
        if "deepseek" in base:
            self.preset.setCurrentIndex(0)
        elif "openai.com" in base:
            self.preset.setCurrentIndex(1)
        elif "dashscope" in base:
            self.preset.setCurrentIndex(2)
        else:
            self.preset.setCurrentIndex(3)

    def _update_permission_hint(self):
        key = self.permission.currentData()
        extra = {
            "default": "只读工具（时间 / 读取 / 列目录 / 抓网页）直接放行；写入与命令会弹窗。",
            "acceptEdits": "文件编辑自动放行；执行命令与其它敏感操作仍会弹窗。",
            "bypassPermissions": "所有工具自动放行，请只在完全可信的环境使用。",
            "plan": "不执行任何工具，只做分析；适合先讨论方案。",
        }.get(key, "")
        self.perm_hint.setText(extra)

    # ---- 语音页 -------------------------------------------------------- #
    def _build_voice_page(self) -> QWidget:
        scroll, lay = self._page()
        card, c = _card()
        self.secret_id, _ = _secret_edit(self.asr_cfg.get("secret_id", ""), self.theme)
        self.secret_id.setPlaceholderText("AKID…")
        c.addWidget(_field("SecretId", self.secret_id))
        self.secret_key, _ = _secret_edit(self.asr_cfg.get("secret_key", ""), self.theme)
        self.secret_key.setPlaceholderText("腾讯云 SecretKey")
        c.addWidget(_field("SecretKey", self.secret_key,
                           "仅保存在本机 config/asr_config.json；也可用 TENCENT_SECRET_ID / "
                           "TENCENT_SECRET_KEY 环境变量覆盖。"))
        self.region = ComboBox()
        self.region.setEditable(True)
        self.region.addItems(REGIONS)
        c.addWidget(_field("地域", self.region))
        self.engine = ComboBox()
        self.engine.setEditable(True)
        self.engine.addItems(ENGINES)
        c.addWidget(_field("识别引擎", self.engine,
                           "16k_zh 适合普通话；方言 / 英文请选择对应引擎。"))
        lay.addWidget(card)

        card, c = _card()
        self.voice_enabled = Switch(bool(self.prefs.get("voice_enabled", True)))
        c.addWidget(_switch_row("启用语音输入", "输入栏显示麦克风按钮，点按后说话即可转文字。",
                                self.voice_enabled))
        lay.addWidget(card)

        lay.addWidget(_label(
            "语音识别使用腾讯云「一句话识别」。未填写密钥时，麦克风按钮会提示前往设置。"
            "录音只在本机短暂缓存，识别完成后立即丢弃。", "hint", wrap=True))
        lay.addStretch(1)
        return scroll

    # ---- 外观页 -------------------------------------------------------- #
    def _build_look_page(self) -> QWidget:
        scroll, lay = self._page()
        card, c = _card()
        seg = QWidget()
        sl = QHBoxLayout(seg)
        sl.setContentsMargins(0, 4, 0, 4)
        sl.setSpacing(4)
        self.theme_group = QButtonGroup(self)
        self.theme_group.setExclusive(True)
        self._theme_buttons = {}
        for key, text in (("auto", "跟随系统"), ("light", "浅色"), ("dark", "深色")):
            btn = QPushButton(text)
            btn.setObjectName("segment")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self.theme_group.addButton(btn)
            self._theme_buttons[key] = btn
            sl.addWidget(btn)
        sl.addStretch(1)
        c.addWidget(_field("主题", seg, "浅色 / 深色会立即应用到悬浮球与设置页。"))
        lay.addWidget(card)

        card, c = _card()
        op_row = QWidget()
        ol = QHBoxLayout(op_row)
        ol.setContentsMargins(0, 0, 0, 0)
        ol.setSpacing(12)
        self.opacity = QSlider(Qt.Orientation.Horizontal)
        self.opacity.setRange(35, 100)
        self.opacity_label = _label("100%", "hint")
        self.opacity_label.setFixedWidth(38)
        self.opacity.valueChanged.connect(lambda v: self.opacity_label.setText(f"{v}%"))
        ol.addWidget(self.opacity, 1)
        ol.addWidget(self.opacity_label)
        c.addWidget(_field("不透明度", op_row,
                           "也可以把鼠标悬停在悬浮球上滚动滚轮快速调节。"))
        self.idle_dim = Switch(bool(self.prefs.get("idle_dim", True)))
        c.addWidget(_switch_row("空闲时淡出", "鼠标离开 3 秒后轻微降低存在感，靠近即恢复。",
                                self.idle_dim))
        reset = QPushButton("把悬浮球放回屏幕右侧")
        reset.clicked.connect(self._mark_reset_position)
        c.addWidget(reset)
        lay.addWidget(card)

        card, c = _card()
        self.notify_hidden = Switch(bool(self.prefs.get("notify_hidden", True)))
        c.addWidget(_switch_row("气泡隐藏时弹出预览", "新回复会以横幅出现，点按横幅可展开对话。",
                                self.notify_hidden))
        self.auto_show_chat = Switch(bool(self.prefs.get("auto_show_chat", True)))
        c.addWidget(_switch_row("发送消息时显示气泡", "关闭后仍可在回复完成后手动展开。",
                                self.auto_show_chat))
        lay.addWidget(card)
        lay.addStretch(1)
        return scroll

    def _mark_reset_position(self):
        self._reset_position = True
        self._set_footer_hint("保存后悬浮球会回到屏幕右侧")

    def _choose_workspace(self):
        start = self.workspace.text().strip() or BASE_DIR
        path = QFileDialog.getExistingDirectory(self, "选择工作空间", start)
        if path:
            self.workspace.setText(path)

    def _choose_database(self):
        start = self.database_path.text().strip() or BASE_DIR
        path = QFileDialog.getExistingDirectory(self, "选择知识库目录", start)
        if path:
            self.database_path.setText(path)

    def _open_database(self):
        path = os.path.expanduser(self.database_path.text().strip()
                                  or os.path.join(BASE_DIR, "database"))
        try:
            os.makedirs(path, exist_ok=True)
        except OSError:
            pass
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _open_workspace(self):
        path = os.path.expanduser(self.workspace.text().strip()
                                  or os.path.join(BASE_DIR, "temp"))
        try:
            os.makedirs(path, exist_ok=True)
        except OSError:
            pass
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    # ---- 子智能体页 ---------------------------------------------------- #
    def _build_subagent_page(self) -> QWidget:
        scroll, lay = self._page()
        card, c = _card()
        self.sub_enabled = Switch(bool(self.agent_cfg["subagents"].get("enabled", True)))
        c.addWidget(_switch_row("启用子智能体", "主智能体可通过 task 工具把检索、编码、写作等任务委派出去。",
                                self.sub_enabled))
        self.sub_depth = QSpinBox()
        self.sub_depth.setRange(1, 4)
        self.sub_depth.setSuffix(" 层")
        c.addWidget(_field("最大委派深度", self.sub_depth,
                           "1 表示子智能体不能再委派；建议保持 2。"))
        ws_row = QWidget()
        wl = QHBoxLayout(ws_row)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.setSpacing(8)
        self.workspace = QLineEdit(str(self.agent_cfg.get("workspace") or ""))
        self.workspace.setPlaceholderText("电脑操作子智能体的工作空间")
        wl.addWidget(_field("工作空间", self.workspace,
                            "computer 子智能体在这里保存截图、临时 Python 脚本和笔记。"),
                     1)
        browse_ws = QPushButton("选择…")
        browse_ws.setCursor(Qt.CursorShape.PointingHandCursor)
        browse_ws.clicked.connect(self._choose_workspace)
        wl.addWidget(browse_ws, 0, Qt.AlignmentFlag.AlignBottom)
        open_ws = QPushButton("打开")
        open_ws.setCursor(Qt.CursorShape.PointingHandCursor)
        open_ws.clicked.connect(self._open_workspace)
        wl.addWidget(open_ws, 0, Qt.AlignmentFlag.AlignBottom)
        c.addWidget(ws_row)
        db_row = QWidget()
        dl = QHBoxLayout(db_row)
        dl.setContentsMargins(0, 0, 0, 0)
        dl.setSpacing(8)
        self.database_path = QLineEdit(str(self.agent_cfg.get("database") or ""))
        self.database_path.setPlaceholderText("本地知识库目录")
        dl.addWidget(_field("知识库目录", self.database_path,
                            "PDF、HTML/URL、图片/截图、Markdown、脚本和加密密码库。"),
                     1)
        browse_db = QPushButton("选择…")
        browse_db.setCursor(Qt.CursorShape.PointingHandCursor)
        browse_db.clicked.connect(self._choose_database)
        dl.addWidget(browse_db, 0, Qt.AlignmentFlag.AlignBottom)
        open_db = QPushButton("打开")
        open_db.setCursor(Qt.CursorShape.PointingHandCursor)
        open_db.clicked.connect(self._open_database)
        dl.addWidget(open_db, 0, Qt.AlignmentFlag.AlignBottom)
        c.addWidget(db_row)
        lay.addWidget(card)

        card, c = _card()
        split = QWidget()
        h = QHBoxLayout(split)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(12)

        left = QVBoxLayout()
        left.setSpacing(6)
        self.sub_list = QListWidget()
        self.sub_list.setObjectName("subList")
        self.sub_list.setFixedWidth(176)
        self.sub_list.currentRowChanged.connect(self._show_subagent)
        self.sub_list.itemChanged.connect(self._on_sub_item_changed)
        left.addWidget(self.sub_list, 1)
        btns = QHBoxLayout()
        btns.setSpacing(6)
        add = QPushButton("新增")
        add.setCursor(Qt.CursorShape.PointingHandCursor)
        add.clicked.connect(self._add_subagent)
        remove = QPushButton("删除")
        remove.setObjectName("danger")
        remove.setCursor(Qt.CursorShape.PointingHandCursor)
        remove.clicked.connect(self._remove_subagent)
        btns.addWidget(add)
        btns.addWidget(remove)
        left.addLayout(btns)
        h.addLayout(left)

        detail = QWidget()
        dl = QVBoxLayout(detail)
        dl.setContentsMargins(0, 0, 0, 0)
        dl.setSpacing(10)
        self.sub_name = QLineEdit()
        dl.addWidget(_field("名称", self.sub_name, "主模型通过这个名称调用子智能体。"))
        self.sub_desc = QLineEdit()
        self.sub_desc.setPlaceholderText("一句话说明它擅长什么")
        dl.addWidget(_field("用途描述", self.sub_desc))
        self.sub_system = QPlainTextEdit()
        self.sub_system.setFixedHeight(86)
        self.sub_system.setPlaceholderText("子智能体的系统提示词")
        dl.addWidget(_field("系统提示词", self.sub_system))
        self.sub_tools = QLineEdit()
        self.sub_tools.setPlaceholderText("如 web_fetch, get_current_time")
        dl.addWidget(_field("可用工具", self.sub_tools,
                            "逗号分隔，留空表示继承主智能体的全部工具。"))
        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(10)
        self.sub_model = QLineEdit()
        self.sub_model.setPlaceholderText("留空 = 使用主模型")
        self.sub_turns = QSpinBox()
        self.sub_turns.setRange(1, 30)
        self.sub_turns.setSuffix(" 轮")
        rl.addWidget(_field("模型覆盖", self.sub_model))
        rl.addWidget(_field("最大轮数", self.sub_turns))
        dl.addWidget(row)
        self.sub_builtin_hint = _label("", "hint", wrap=True)
        dl.addWidget(self.sub_builtin_hint)
        dl.addStretch(1)
        h.addWidget(detail, 1)
        c.addWidget(split)
        self._sub_detail = detail

        for widget in (self.sub_name, self.sub_desc, self.sub_tools, self.sub_model):
            widget.textEdited.connect(self._on_sub_text_edited)
        self.sub_system.textChanged.connect(self._on_sub_text_edited)
        self.sub_turns.valueChanged.connect(self._on_sub_value_edited)
        lay.addWidget(card)

        lay.addWidget(_label(
            "内置子智能体：researcher（联网检索）、coder（代码与命令）、writer（中文整理）、"
            "finder（Everything 本地检索）、computer（屏幕截图与电脑操作）。"
            "改动会写回 config/agent_config.json；也可以直接编辑该文件添加更多角色。",
            "hint", wrap=True))
        lay.addStretch(1)
        return scroll

    def _load_subagents(self) -> list[dict]:
        builtins = {s.name: s for s in available_subagents(
            BASE_DIR, workspace=self.agent_cfg.get("workspace"),
            database=self.agent_cfg.get("database"))}
        self._builtin_names = set(builtins)
        items = self.agent_cfg["subagents"].get("items") or []
        if not items:
            return [{
                "name": s.name,
                "description": s.description,
                "system": s.system_prompt,
                "tools": s.tool_names(),
                "model": s.model or "",
                "max_turns": s.max_turns,
                "enabled": s.enabled,
                "builtin": True,
            } for s in builtins.values()]
        out = []
        for raw in items:
            name = str(raw.get("name") or "").strip()
            if not name:
                continue
            base = builtins.get(name)
            out.append({
                "name": name,
                "description": str(raw.get("description") or (base.description if base else "")),
                "system": str(raw.get("system") or (base.system_prompt if base else "")),
                "tools": list(raw.get("tools") or (base.tool_names() if base else [])),
                "model": str(raw.get("model") or ""),
                "max_turns": int(raw.get("max_turns") or (base.max_turns if base else 6)),
                "enabled": bool(raw.get("enabled", True)),
                "builtin": base is not None,
            })
        return out

    def _refresh_sub_list(self, select: Optional[str] = None):
        self._sub_loading = True
        current = select
        if current is None and self.sub_list.currentRow() >= 0:
            current = self._subs[self.sub_list.currentRow()]["name"]
        self.sub_list.clear()
        row = 0
        for i, item in enumerate(self._subs):
            text = item["name"] + ("  · 内置" if item.get("builtin") else "")
            li = QListWidgetItem(text)
            li.setFlags(li.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            li.setCheckState(Qt.CheckState.Checked if item["enabled"] else Qt.CheckState.Unchecked)
            li.setToolTip(item["description"])
            self.sub_list.addItem(li)
            if item["name"] == current:
                row = i
        self._sub_loading = False
        if self._subs:
            self.sub_list.setCurrentRow(max(0, row))
        else:
            self._show_subagent(-1)

    def _show_subagent(self, row: int):
        if row < 0 or row >= len(self._subs):
            self._sub_detail.setEnabled(False)
            return
        self._sub_detail.setEnabled(True)
        item = self._subs[row]
        self._sub_loading = True
        self.sub_name.setText(item["name"])
        self.sub_name.setReadOnly(bool(item.get("builtin")))
        self.sub_desc.setText(item["description"])
        self.sub_system.setPlainText(item["system"])
        self.sub_tools.setText(", ".join(item["tools"]))
        self.sub_model.setText(item["model"])
        self.sub_turns.setValue(int(item["max_turns"]))
        self._sub_loading = False
        self.sub_builtin_hint.setText(
            "内置子智能体，名称不可修改；修改会覆盖它的默认提示词与工具。"
            if item.get("builtin") else
            "自定义子智能体；名称需唯一，留空的字段会使用通用默认值。")

    def _on_sub_item_changed(self, li: QListWidgetItem):
        if self._sub_loading:
            return
        row = self.sub_list.row(li)
        if 0 <= row < len(self._subs):
            self._subs[row]["enabled"] = li.checkState() == Qt.CheckState.Checked

    def _current_sub(self) -> Optional[dict]:
        row = self.sub_list.currentRow()
        if 0 <= row < len(self._subs):
            return self._subs[row]
        return None

    def _on_sub_text_edited(self, *_):
        if self._sub_loading:
            return
        item = self._current_sub()
        if item is None:
            return
        if not item.get("builtin"):
            old = item["name"]
            item["name"] = self.sub_name.text().strip()
            if item["name"] != old:
                row = self.sub_list.currentRow()
                li = self.sub_list.item(row)
                if li is not None:
                    li.setText(item["name"] or "（未命名）")
        item["description"] = self.sub_desc.text().strip()
        item["system"] = self.sub_system.toPlainText().strip()
        item["tools"] = [x.strip() for x in
                         self.sub_tools.text().replace("，", ",").split(",") if x.strip()]
        item["model"] = self.sub_model.text().strip()

    def _on_sub_value_edited(self, value: int):
        if self._sub_loading:
            return
        item = self._current_sub()
        if item is not None:
            item["max_turns"] = int(value)

    def _add_subagent(self):
        used = {x["name"] for x in self._subs}
        n = 1
        while f"custom{n}" in used:
            n += 1
        name = f"custom{n}"
        self._subs.append({
            "name": name,
            "description": "",
            "system": "",
            "tools": [],
            "model": "",
            "max_turns": 5,
            "enabled": True,
            "builtin": False,
        })
        self._refresh_sub_list(select=name)
        self.sub_name.setFocus()
        self.sub_name.selectAll()

    def _remove_subagent(self):
        item = self._current_sub()
        if item is None:
            return
        if item.get("builtin"):
            self._set_footer_hint("内置子智能体不能删除，可以取消勾选来停用")
            return
        self._subs.remove(item)
        self._refresh_sub_list()
        self._set_footer_hint("已移除，保存后生效")

    # ---- 关于页 -------------------------------------------------------- #
    def _build_about_page(self) -> QWidget:
        scroll, lay = self._page()
        hero = QWidget()
        hl = QVBoxLayout(hero)
        hl.setContentsMargins(0, 6, 0, 6)
        hl.setSpacing(6)
        orb = QLabel()
        orb.setPixmap(make_icon("sparkles", 40, self.theme.accent).pixmap(40, 40))
        orb.setAlignment(A_CENTER)
        hl.addWidget(orb)
        name = _label(APP_NAME, "title")
        name.setAlignment(A_CENTER)
        hl.addWidget(name)
        ver = _label(f"版本 {APP_VERSION} · PySide6 桌面悬浮球", "hint")
        ver.setAlignment(A_CENTER)
        hl.addWidget(ver)
        lay.addWidget(hero)

        card, c = _card()
        c.addWidget(_label("架构", "section"))
        c.addWidget(_label(
            "主智能体维护对话并调用工具；开启子智能体后，它可以通过 task 工具把"
            "「检索资料 / 修改代码 / 整理文案」交给独立上下文的子智能体，"
            "再把结论整合回主对话。子智能体拥有自己的工具集、模型覆盖与轮数上限，"
            "也可以在设置页新增角色。", "hint", wrap=True))
        lay.addWidget(card)

        card, c = _card()
        c.addWidget(_label("快捷键", "section"))
        for keys, desc in (
            ("Enter", "发送消息"),
            ("Esc", "停止生成 / 收起面板"),
            ("Ctrl+,", "打开设置"),
            ("Ctrl+Shift+H", "显示 / 隐藏消息气泡"),
            ("滚轮（悬停悬浮球）", "调节不透明度"),
        ):
            row = QWidget()
            rl = QHBoxLayout(row)
            rl.setContentsMargins(0, 0, 0, 0)
            k = _label(keys, "hint")
            k.setFixedWidth(150)
            rl.addWidget(k)
            rl.addWidget(_label(desc), 1)
            c.addWidget(row)
        lay.addWidget(card)

        card, c = _card()
        c.addWidget(_label("配置文件", "section"))
        for title, path in (("模型 / 子智能体", AGENT_CONFIG_PATH),
                            ("腾讯云语音", ASR_CONFIG_PATH)):
            row = QWidget()
            rl = QHBoxLayout(row)
            rl.setContentsMargins(0, 0, 0, 0)
            rl.setSpacing(10)
            rl.addWidget(_label(title, "hint"))
            val = _label(path, "hint")
            val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            rl.addWidget(val, 1)
            c.addWidget(row)
        open_btn = QPushButton("打开配置文件夹")
        open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        open_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(BASE_DIR)))
        c.addWidget(open_btn)
        lay.addWidget(card)

        lay.addWidget(_label(
            "依赖：PySide6、requests。所有密钥只保存在本机配置文件中，不会上传到第三方服务；"
            "对话内容会发送到你自己配置的模型接口。", "hint", wrap=True))
        lay.addStretch(1)
        return scroll

    # ---- 载入 / 保存 --------------------------------------------------- #
    def _load_values(self):
        self.temp.setValue(int(round(float(self.agent_cfg.get("temperature", 0.7)) * 10)))
        self.max_tokens.setValue(int(self.agent_cfg.get("max_tokens", 4096)))
        self.max_turns.setValue(int(self.agent_cfg.get("max_turns", 50)))
        self.temp_label.setText(f"{self.temp.value() / 10:.1f}")
        mode = self.agent_cfg.get("permission_mode", "default")
        for i in range(self.permission.count()):
            if self.permission.itemData(i) == mode:
                self.permission.setCurrentIndex(i)
                break
        self._update_permission_hint()
        self.system.setPlainText(self.agent_cfg.get("system", ""))
        self.workspace.setText(str(self.agent_cfg.get("workspace") or ""))
        self.database_path.setText(str(self.agent_cfg.get("database") or ""))
        self._detect_preset()
        self.secret_id.setText(self.asr_cfg.get("secret_id", ""))
        self.secret_key.setText(self.asr_cfg.get("secret_key", ""))
        self.region.setCurrentText(self.asr_cfg.get("region", "ap-guangzhou"))
        self.engine.setCurrentText(self.asr_cfg.get("engine", "16k_zh"))
        key = self.prefs.get("appearance", "auto")
        self._theme_buttons.get(key, self._theme_buttons["auto"]).setChecked(True)
        self.opacity.setValue(int(round(float(self.prefs.get("opacity", 1.0)) * 100)))
        self.opacity_label.setText(f"{self.opacity.value()}%")
        self._subs = self._load_subagents()
        self.sub_enabled.setChecked(bool(self.agent_cfg["subagents"].get("enabled", True)))
        self.sub_depth.setValue(int(self.agent_cfg["subagents"].get("max_depth", 2)))
        self._refresh_sub_list()

    def _test_connection(self):
        if self._test_busy or self._tester.busy:
            return
        self._test_busy = True
        self.test_btn.setEnabled(False)
        self.test_state.setObjectName("hint")
        self.test_state.setStyleSheet(f"color: {css(self.theme.label2)}; font-size: 11px;")
        self.test_state.setText("正在连接…")
        self._tester.start({
            "api_key": self.api_key.text().strip(),
            "base_url": self.base_url.text().strip(),
            "model": self.model.text().strip(),
        })

    def _on_test_result(self, ok: bool, message: str):
        self._test_busy = False
        self.test_btn.setEnabled(True)
        self.test_state.setText(message)
        self.test_state.setStyleSheet(
            f"color: {css(self.theme.success if ok else RED)}; font-size: 11px;")

    def _selected_theme(self) -> str:
        for key, btn in self._theme_buttons.items():
            if btn.isChecked():
                return key
        return "auto"

    def _save(self):
        # 子智能体校验
        seen = set()
        for item in self._subs:
            name = (item.get("name") or "").strip()
            if not name:
                self._fail("子智能体名称不能为空", 3)
                return
            if name in seen:
                self._fail(f"子智能体名称重复：{name}", 3)
                return
            seen.add(name)

        cfg = dict(self.agent_cfg)
        cfg.update({
            "api_key": self.api_key.text().strip(),
            "base_url": self.base_url.text().strip() or DEFAULT_AGENT_CONFIG["base_url"],
            "model": self.model.text().strip() or DEFAULT_AGENT_CONFIG["model"],
            "system": self.system.toPlainText().strip(),
            "temperature": round(self.temp.value() / 10, 1),
            "max_tokens": self.max_tokens.value(),
            "max_turns": self.max_turns.value(),
            "permission_mode": self.permission.currentData(),
            "stream": self.stream.isChecked(),
            "workspace": self.workspace.text().strip(),
            "database": self.database_path.text().strip(),
        })
        cfg["subagents"] = {
            "enabled": self.sub_enabled.isChecked(),
            "max_depth": self.sub_depth.value(),
            "items": [{
                "name": item["name"].strip(),
                "description": item.get("description", "").strip(),
                "system": item.get("system", "").strip(),
                "tools": list(item.get("tools") or []),
                "model": item.get("model", "").strip(),
                "max_turns": int(item.get("max_turns", 6)),
                "enabled": bool(item.get("enabled", True)),
            } for item in self._subs],
        }
        asr = {
            "secret_id": self.secret_id.text().strip(),
            "secret_key": self.secret_key.text().strip(),
            "region": self.region.currentText().strip() or "ap-guangzhou",
            "engine": self.engine.currentText().strip() or "16k_zh",
        }
        prefs = {
            "appearance": self._selected_theme(),
            "opacity": self.opacity.value() / 100.0,
            "idle_dim": self.idle_dim.isChecked(),
            "notify_hidden": self.notify_hidden.isChecked(),
            "voice_enabled": self.voice_enabled.isChecked(),
            "auto_show_chat": self.auto_show_chat.isChecked(),
        }
        self.result_data = {
            "agent": normalize_agent_config(cfg),
            "asr": normalize_asr_config(asr),
            "prefs": normalize_prefs(prefs),
            "reset_position": self._reset_position,
        }
        self.accept()

    def _set_footer_hint(self, message: str, error: bool = False):
        self.footer_hint.setText(message)
        self.footer_hint.setStyleSheet(
            f"color: {css(RED if error else self.theme.label3)}; font-size: 11px;")

    def _fail(self, message: str, page: int):
        self._set_footer_hint(message, error=True)
        self.nav.setCurrentRow(page)


# --------------------------------------------------------------------------- #
# 工具授权
# --------------------------------------------------------------------------- #
class PermissionDialog(GlassDialog):
    """工具授权确认。``choice`` 为 ``allow`` / ``always`` / ``deny``。"""

    def __init__(self, parent, tool_name: str, tool_input: dict, mode: str,
                 dark: bool = True):
        super().__init__(parent, THEME_DARK if dark else THEME_LIGHT, 520, 430)
        self.tool_name = tool_name
        self.choice = "deny"
        self.setWindowTitle("工具授权")
        self._build_header(tool_name)
        self._build_body(tool_input, mode)
        self._build_footer()

    def _build_header(self, tool_name: str):
        icon = QLabel()
        icon.setPixmap(make_icon("robot", 26, self.theme.accent).pixmap(26, 26))
        icon.setFixedSize(34, 34)
        icon.setAlignment(A_CENTER)
        self.header_layout.addWidget(icon)
        titles = QVBoxLayout()
        titles.setSpacing(1)
        titles.addWidget(_label("允许使用工具？", "title"))
        titles.addWidget(_label("智能体请求在你的电脑上执行一个操作", "subtitle"))
        self.header_layout.addLayout(titles)
        self.header_layout.addStretch(1)

    def _build_body(self, tool_input: dict, mode: str):
        wrap = QWidget()
        lay = QVBoxLayout(wrap)
        lay.setContentsMargins(24, 6, 24, 10)
        lay.setSpacing(10)

        name = QLabel(self.tool_name)
        name.setFont(ui_font(13, QFont.Weight.DemiBold))
        name.setStyleSheet(
            f"color: {css(self.theme.accent)}; background: {css(self.theme.accent_soft)};"
            f"border-radius: 8px; padding: 5px 10px;")
        name.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        lay.addWidget(name)

        try:
            pretty = json.dumps(tool_input, ensure_ascii=False, indent=2)
        except (TypeError, ValueError):
            pretty = str(tool_input)
        if len(pretty) > 1200:
            pretty = pretty[:1200] + "\n…（已截断）"
        summary = QPlainTextEdit()
        summary.setObjectName("summary")
        summary.setReadOnly(True)
        summary.setPlainText(pretty or "（无参数）")
        summary.setMinimumHeight(150)
        lay.addWidget(summary, 1)

        lay.addWidget(_label(
            f"当前权限模式：{PERMISSION_MODES.get(mode, mode)}。"
            "只有在你理解并信任该操作时才允许；允许一次只对本次调用生效。",
            "hint", wrap=True))
        self.body_layout.addWidget(wrap, 1)

    def _build_footer(self):
        hint = _label("事件将回传给智能体", "footerHint")
        self.footer_layout.addWidget(hint)
        self.footer_layout.addStretch(1)
        deny = QPushButton("拒绝")
        deny.setObjectName("ghost")
        deny.setCursor(Qt.CursorShape.PointingHandCursor)
        deny.clicked.connect(lambda: self._finish("deny"))
        always = QPushButton("总是允许")
        always.setCursor(Qt.CursorShape.PointingHandCursor)
        always.clicked.connect(lambda: self._finish("always"))
        allow = QPushButton("允许一次")
        allow.setObjectName("primary")
        allow.setCursor(Qt.CursorShape.PointingHandCursor)
        allow.clicked.connect(lambda: self._finish("allow"))
        self.footer_layout.addWidget(deny)
        self.footer_layout.addWidget(always)
        self.footer_layout.addWidget(allow)

    def _finish(self, choice: str):
        self.choice = choice
        self.accept()
