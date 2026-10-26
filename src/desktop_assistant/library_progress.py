# -*- coding: utf-8 -*-
"""知识库更新进度弹窗：紧凑的玻璃卡片，长文件名自动省略。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFontMetrics
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QSizePolicy,
    QVBoxLayout,
)

from .ui_kit import Theme, css, ui_font


class LibraryProgressDialog(QDialog):
    """轻量的无边框进度卡片，替代过宽的 QProgressDialog。"""

    def __init__(self, parent, theme: Theme):
        super().__init__(parent)
        self.theme = theme
        self._message = ""
        self._value = 0
        self.setWindowTitle("知识库更新")
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setModal(True)
        self.setFont(ui_font(13))
        self.setFixedWidth(480)
        self._build()
        self._apply_theme()

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 22, 22, 22)
        outer.setSpacing(0)

        self.panel = QFrame(self)
        self.panel.setObjectName("panel")
        shadow = QGraphicsDropShadowEffect(self.panel)
        shadow.setBlurRadius(46)
        shadow.setOffset(0, 14)
        shadow.setColor(QColor(0, 0, 0, 120))
        self.panel.setGraphicsEffect(shadow)
        outer.addWidget(self.panel)

        root = QVBoxLayout(self.panel)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(10)
        title = QLabel("知识库更新", self.panel)
        title.setObjectName("title")
        self.state = QLabel("本地索引", self.panel)
        self.state.setObjectName("state")
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.state)
        root.addLayout(header)

        self.detail = QLabel("正在准备…", self.panel)
        self.detail.setObjectName("detail")
        self.detail.setWordWrap(False)
        self.detail.setSizePolicy(QSizePolicy.Policy.Ignored,
                                  QSizePolicy.Policy.Preferred)
        self.detail.setMinimumWidth(0)
        root.addWidget(self.detail)

        self.bar = QProgressBar(self.panel)
        self.bar.setRange(0, 100)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(8)
        root.addWidget(self.bar)

        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        self.percent = QLabel("0%", self.panel)
        self.percent.setObjectName("percent")
        self.hint = QLabel("完成后自动关闭", self.panel)
        self.hint.setObjectName("hint")
        bottom.addWidget(self.percent)
        bottom.addStretch(1)
        bottom.addWidget(self.hint)
        root.addLayout(bottom)

    def _apply_theme(self):
        th = self.theme
        self.setStyleSheet(f"""
            QFrame#panel {{
                background: {css(th.card_top)};
                border: 1px solid {css(th.hairline)};
                border-radius: 18px;
            }}
            QLabel#title {{
                color: {css(th.label)};
                font-size: 15px;
                font-weight: 600;
            }}
            QLabel#state {{
                color: {css(th.label3)};
                font-size: 11px;
            }}
            QLabel#detail {{
                color: {css(th.label2)};
                font-size: 12px;
            }}
            QLabel#percent {{
                color: {css(th.accent)};
                font-size: 12px;
                font-weight: 600;
            }}
            QLabel#hint {{
                color: {css(th.label3)};
                font-size: 11px;
            }}
            QProgressBar {{
                background: {css(th.fill)};
                border: none;
                border-radius: 4px;
            }}
            QProgressBar::chunk {{
                background: {css(th.accent)};
                border-radius: 4px;
            }}
        """)

    def set_progress(self, value: int, message: str = ""):
        self._value = max(0, min(100, int(value or 0)))
        self.bar.setValue(self._value)
        self.percent.setText(f"{self._value}%")
        message = (message or "").strip()
        if message.startswith("正在解析 "):
            message = "正在解析 · " + message[len("正在解析 "):]
        elif message.startswith("正在更新索引 "):
            message = "正在更新索引 · " + message[len("正在更新索引 "):]
        self._message = message
        self._refresh_detail()

    def _refresh_detail(self):
        if not self._message:
            return
        width = self.detail.width()
        if width < 80:
            width = 400
        metrics = QFontMetrics(self.detail.font())
        self.detail.setText(
            metrics.elidedText(self._message, Qt.TextElideMode.ElideMiddle, width))

    def showEvent(self, event):
        super().showEvent(event)
        self.adjustSize()
        screen = self.parent().screen() if self.parent() else QApplication.primaryScreen()
        if screen is not None:
            geo = screen.availableGeometry()
            self.move(geo.center().x() - self.width() // 2,
                      geo.center().y() - self.height() // 2)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh_detail()
