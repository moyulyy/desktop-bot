# -*- coding: utf-8 -*-
"""
AI 桌面助手 · iOS 风格悬浮球
============================

布局 —— 形如「旦」字：上面一块消息气泡面板，下面一条输入栏，二者分离、中间留缝。

        ┌────────────────────────────────┐
        │ ◉  在线 · 模型名        ⚙  🗑  ⌄ │   消息气泡面板（可隐藏）
        │   ……气泡……                      │
        └────────────────────────────────┘
                      （间隙）
         ◉   💬   ┌──────────────────────┐
         球  气泡  │ 输入消息…   ✕  🎤  ↑ │   输入栏
                   └──────────────────────┘

交互
----
* 点按悬浮球               展开 / 收起；按住拖动可移动，松手后自动吸附屏幕边缘
* 右键悬浮球 / 系统托盘    菜单：展开、隐藏气泡、清空会话、设置、外观、退出
* 面板右上角 ⚙ / Ctrl+,    打开设置（模型、语音、外观、子智能体、关于）
* 💬 按钮 / 面板右上角 ⌄   隐藏 / 显示消息气泡；隐藏后新回复以横幅预览，按钮带未读角标
* Ctrl+Shift+H             隐藏 / 显示消息气泡
* 🎤 按钮                  腾讯云「一句话识别」语音转文字（球会变红并随音量脉动）
* Enter 发送 · Esc 停止/收起 · 滚轮悬停在球上调节不透明度
* 发送后按钮变为停止键；流式回复逐字出现，工具与子智能体进度用彩色胶囊提示
* 空状态提供示例问题，点一下即可填入输入框
* 消息支持 Markdown、拖选、Ctrl+C / Ctrl+A、右键复制；链接可在浏览器打开
* 支持跟随系统深色 / 浅色，也可在设置或菜单中手动指定

Agent 与子智能体
----------------
主智能体参考 Anthropic Claude Agent SDK 的循环设计（agent_framework.py）：
模型返回工具调用后执行工具并把结果回填，直到产出最终回答。
内置工具：时间、读取 / 写入 / 编辑文件、列目录、执行命令、抓取网页、打开链接。
危险工具由 permission_mode（default / acceptEdits / bypassPermissions / plan）
与授权弹窗控制，支持「允许一次 / 总是允许」。

启用子智能体后，主模型可以通过 ``task`` 工具把检索、编码、写作等任务交给
独立上下文的子智能体（researcher / coder / writer，可在设置页新增或停用）。
子智能体拥有自己的系统提示、工具集、模型覆盖与轮数上限，并可继续委派，
深度由 max_subagent_depth 限制。子智能体的工具调用仍逐个走权限校验。

配置
----
1) 环境变量：OPENAI_API_KEY / OPENAI_BASE_URL / OPENAI_MODEL / AGENT_SYSTEM
2) config/agent_config.json（见 config/agent_config.example.json；设置页会写入）
3) 语音：TENCENT_SECRET_ID / TENCENT_SECRET_KEY 或 config/asr_config.json

依赖: pip install -r requirements.txt
运行: python -m desktop_assistant（或双击 start.vbs）
测试: python -m pytest tests
"""

import base64
import hashlib
import hmac
import io
import json
import math
import os
import re
import sys
import tempfile
import threading
import time
import uuid
import wave
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Optional

from PySide6.QtCore import (
    QByteArray,
    QEasingCurve,
    QEvent,
    QLockFile,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    QSettings,
    Qt,
    QTimer,
    QUrl,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import (
    QAbstractTextDocumentLayout,
    QBrush,
    QColor,
    QDesktopServices,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QIcon,
    QImage,
    QKeySequence,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QRadialGradient,
    QShortcut,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QTextLayout,
    QTextOption,
)
from PySide6.QtMultimedia import QAudioFormat, QAudioSource, QMediaDevices
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QGraphicsOpacityEffect,
    QLineEdit,
    QMenu,
    QSystemTrayIcon,
    QWidget,
)

from .agent_framework import (
    SAFE_TOOLS,
    EDIT_TOOLS,
    Agent as AgentCore,
    AgentEvent,
    AgentOptions,
    PermissionResult,
    SubagentSpec,
    default_tools,
)
from .local_subagents import available_subagents
from .library_service import ChatTempStore, LibraryOrganizer, LibraryWorker
from .library_progress import LibraryProgressDialog
from .database_tools import build_database_tools
from .rag import build_rag_tools
from .reports import chat_markdown, open_pending_report, open_report
from .config_store import (
    DEFAULT_SYSTEM,
    DEFAULT_PREFS,
    PROJECT_ROOT,
    load_agent_config,
    load_asr_config,
    normalize_prefs,
    save_agent_config,
    save_asr_config,
)
from .settings_dialog import PermissionDialog, SettingsDialog
from .ui_kit import (
    AA,
    A_CENTER,
    A_LEFT_V,
    GREEN,
    NO_BRUSH,
    NO_PEN,
    ORANGE,
    PAD,
    RED,
    SMOOTH,
    TEXT_AA,
    THEME_DARK,
    THEME_LIGHT,
    clamp,
    css,
    draw_glyph,
    draw_orb,
    ease_out,
    make_ball_pixmap,
    make_icon,
    mix,
    rrect_path,
    soft_shadow,
    style_menu,
    ui_font,
    with_alpha,
)

# --------------------------------------------------------------------------- #
# 尺寸（逻辑像素）
# --------------------------------------------------------------------------- #
BALL = 56            # 悬浮球直径
ROW_H = 56           # 底部一行（球 + 输入栏）的高度
PILL_H = 44          # 输入栏高度
PANEL_W = 440        # 展开后消息面板默认宽度
CHAT_H = 344         # 消息面板默认高度
MIN_PANEL_W = 340    # 消息面板可缩放最小宽度
MAX_PANEL_W = 760    # 消息面板可缩放最大宽度
MIN_CHAT_H = 220     # 消息面板可缩放最小高度
MAX_CHAT_H = 760     # 消息面板可缩放最大高度
RESIZE_HANDLE = 22   # 外角缩放手柄尺寸
HEAD_H = 52          # 消息面板标题栏高度
GAP = 12             # 消息面板与输入行之间的缝（「旦」字中间那道缝）
ROW_GAP_A = 12       # 球 ↔ 输入栏
EDGE = 10            # 吸附屏幕边缘时的留白
SNAP = 28            # 吸附触发距离
R_CARD = 26          # 消息面板圆角

LEAD = 2


def layout_text(text: str, font: QFont, max_w: float, max_lines: int = 0):
    """用 QTextLayout 排版：返回 (layout, 实际最大行宽, 总高度)。
    比 boundingRect 更紧凑，气泡宽度能贴合文字；max_lines>0 时超出部分以「…」截断。"""
    src = (text or " ").replace("\r\n", "\n").replace("\r", "\n").replace("\n", " ")
    lay, wmax, y = None, 0.0, 0.0
    for _ in range(6):
        lay = QTextLayout(src, font)
        opt = QTextOption()
        opt.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        lay.setTextOption(opt)
        lay.beginLayout()
        y, wmax, n = 0.0, 0.0, 0
        while True:
            line = lay.createLine()
            if not line.isValid():
                break
            line.setLineWidth(max_w)
            line.setPosition(QPointF(0, y))
            y += line.height() + LEAD
            wmax = max(wmax, line.naturalTextWidth())
            n += 1
        lay.endLayout()
        if not max_lines or n <= max_lines:
            break
        last = lay.lineAt(max_lines - 1)
        cut = last.textStart() + last.textLength()
        src = src[:cut].rstrip("  ")[:-1].rstrip() + "…"
    return lay, int(math.ceil(wmax)), int(math.ceil(y - LEAD))


def fmt_time(ts: float) -> str:
    dt = datetime.fromtimestamp(ts)
    if dt.date() == datetime.now().date():
        return f"今天 {dt.strftime('%H:%M')}"
    return dt.strftime("%m-%d %H:%M")


_URL_RE = re.compile(
    r"(?<!\]\()(?<!<)(https?://[^\s<>()\[\]\u4e00-\u9fff]*"
    r"[^\s<>()\[\]\u4e00-\u9fff.,;:!?\u3002\uff0c\uff1b\uff1a\uff01\uff1f])")


def autolink(text: str) -> str:
    """把裸链接转成 Markdown 链接（已在 []() / <> 里的不重复，去掉尾随标点）。"""
    return _URL_RE.sub(lambda m: f"[{m.group(1)}]({m.group(1)})", text or "")


def build_markdown_doc(text: str, font: QFont, text_color: QColor, link_color: QColor,
                       code_bg: QColor, width: float, image_provider=None,
                       base_url=None) -> QTextDocument:
    """把一段 Markdown 解析为 QTextDocument，并统一下文字 / 链接 / 代码 / 图片。"""
    doc = QTextDocument()
    doc.setDocumentMargin(0)
    doc.setDefaultFont(font)
    if base_url is not None:
        doc.setBaseUrl(base_url)
    doc.setMarkdown(autolink(text))
    doc.setTextWidth(max(1.0, float(width)))

    cur = QTextCursor(doc)
    cur.select(QTextCursor.SelectionType.Document)
    base = QTextCharFormat()
    base.setForeground(text_color)
    cur.mergeCharFormat(base)

    blk = doc.begin()
    while blk.isValid():
        it = blk.begin()
        while not it.atEnd():
            frag = it.fragment()
            if frag.isValid():
                cf = frag.charFormat()
                pos, ln = frag.position(), frag.length()
                if cf.fontFixedPitch():
                    c = QTextCursor(doc)
                    c.setPosition(pos)
                    c.setPosition(pos + ln, QTextCursor.MoveMode.KeepAnchor)
                    f = QTextCharFormat()
                    f.setBackground(code_bg)
                    c.mergeCharFormat(f)
                if cf.isAnchor():
                    c = QTextCursor(doc)
                    c.setPosition(pos)
                    c.setPosition(pos + ln, QTextCursor.MoveMode.KeepAnchor)
                    f = QTextCharFormat()
                    f.setForeground(link_color)
                    f.setFontUnderline(True)
                    c.mergeCharFormat(f)
                if cf.isImageFormat():
                    fmt = cf.toImageFormat()
                    src = fmt.name()
                    img = image_provider(src) if image_provider is not None else None
                    if not isinstance(img, QImage) or img.isNull():
                        resource = doc.resource(
                            QTextDocument.ResourceType.ImageResource, QUrl(src))
                        img = resource if isinstance(resource, QImage) else None
                    if isinstance(img, QImage) and not img.isNull():
                        doc.addResource(QTextDocument.ResourceType.ImageResource,
                                        QUrl(src), img)
                        iw = max(1, img.width())
                        ih = max(1, img.height())
                        scale = min(1.0, max(40.0, width) / iw, 260.0 / ih)
                        fmt.setWidth(max(1, int(round(iw * scale))))
                        fmt.setHeight(max(1, int(round(ih * scale))))
                        c = QTextCursor(doc)
                        c.setPosition(pos)
                        c.setPosition(pos + ln, QTextCursor.MoveMode.KeepAnchor)
                        c.mergeCharFormat(fmt)
            it += 1
        blk = blk.next()
    return doc


# --------------------------------------------------------------------------- #
# 图标按钮
# --------------------------------------------------------------------------- #
class IconButton(QWidget):
    clicked = Signal()

    def __init__(self, kind: str, size: int, parent=None):
        super().__init__(parent)
        self.kind = kind
        self.theme = THEME_DARK
        self.active = False
        self.badge = 0
        self._hover = False
        self._down = False
        self._phase = 0.0
        self.setFixedSize(size, size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)

    def set_active(self, v: bool):
        self.active = bool(v)
        if self.kind == "mic":
            self._timer.start() if self.active else self._timer.stop()
        self.update()

    def set_kind(self, kind: str):
        if kind != self.kind:
            self.kind = kind
            self.update()

    def set_badge(self, n: int):
        self.badge = int(n)
        self.update()

    def _tick(self):
        self._phase += 0.033
        self.update()

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self._down = False
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            self._down = True
            self.update()
            e.accept()

    def mouseReleaseEvent(self, e):
        if self._down:
            self._down = False
            self.update()
            if self.rect().contains(e.position().toPoint()):
                self.clicked.emit()
            e.accept()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(AA)
        th = self.theme
        w = float(self.width())
        c = QPointF(w / 2, self.height() / 2)
        en = self.isEnabled()
        white = QColor(255, 255, 255)
        p.save()
        if not en:
            p.setOpacity(0.42)
        if self._down:
            p.translate(c)
            p.scale(0.92, 0.92)
            p.translate(-c.x(), -c.y())

        k = self.kind
        if k == "send":
            r = w / 2 - 1
            p.setPen(NO_PEN)
            if en:
                g = QLinearGradient(0, 0, 0, w)
                g.setColorAt(0, th.accent.lighter(118 if self._hover else 108))
                g.setColorAt(1, th.accent)
                p.setBrush(QBrush(g))
            else:
                p.setBrush(th.fill)
            p.drawEllipse(c, r, r)
            draw_glyph(p, "send", c, w * 0.50, white if en else with_alpha(white, 150))

        elif k == "stop":
            r = w / 2 - 1
            p.setPen(NO_PEN)
            g = QLinearGradient(0, 0, 0, w)
            g.setColorAt(0, th.accent.lighter(118 if self._hover else 108))
            g.setColorAt(1, th.accent)
            p.setBrush(QBrush(g))
            p.drawEllipse(c, r, r)
            draw_glyph(p, "stop", c, w * 0.50, white)

        elif k == "mic":
            if self.active:
                pulse = 0.5 + 0.5 * math.sin(self._phase * 5.0)
                p.setPen(NO_PEN)
                p.setBrush(with_alpha(RED, 70 * (1.0 - pulse)))
                hr = w / 2 - 4 + pulse * 4
                p.drawEllipse(c, hr, hr)
                p.setBrush(RED)
                p.drawEllipse(c, w / 2 - 4, w / 2 - 4)
                draw_glyph(p, "mic", c, w * 0.52, white)
            else:
                if self._hover:
                    p.setPen(NO_PEN)
                    p.setBrush(th.fill)
                    p.drawEllipse(c, w / 2 - 3, w / 2 - 3)
                draw_glyph(p, "mic", c, w * 0.52, th.label if self._hover else th.label2)

        elif k == "clear":
            p.setPen(NO_PEN)
            p.setBrush(QColor(142, 142, 147, 225 if self._hover else 170))
            p.drawEllipse(c, w * 0.42, w * 0.42)
            draw_glyph(p, "x", c, w * 0.42, white, lw=1.5)

        elif k == "chat":
            if self._hover or self._down:
                p.setPen(NO_PEN)
                p.setBrush(th.fill)
                p.drawEllipse(c, w / 2 - 1, w / 2 - 1)
            col = th.accent if self.active else (th.label if self._hover else th.label2)
            draw_glyph(p, "chat", c, w * 0.50, col, filled=self.active)
        p.restore()

        if self.badge > 0:                               # 未读角标
            txt = str(self.badge) if self.badge < 10 else "9+"
            bw = 17.0 if len(txt) == 1 else 24.0
            rect = QRectF(w - bw - 1, 1, bw, 17)
            p.setPen(NO_PEN)
            p.setBrush(RED)
            p.drawRoundedRect(rect, 8.5, 8.5)
            p.setPen(white)
            p.setFont(ui_font(10, QFont.Weight.Bold))
            p.drawText(rect, A_CENTER, txt)


# --------------------------------------------------------------------------- #
# 消息气泡面板（「旦」字上面那个「日」）
# --------------------------------------------------------------------------- #
class ChatCard(QWidget):
    hide_clicked = Signal()
    clear_clicked = Signal()
    settings_clicked = Signal()
    suggestion_clicked = Signal(str)
    choice_selected = Signal(str, str)
    database_add_files = Signal()
    drag_event = Signal(str, QPoint)
    resize_event = Signal(str, QPoint)
    copied = Signal(str)

    SUGGESTIONS = ("现在几点了？", "帮我查一下最新的 AI 新闻", "解释一下这段报错")
    SIDE = 14
    PX = 13
    PY = 8
    GAP_SAME = 3
    GAP_DIFF = 10
    TOP = 12
    BOTTOM = 14
    MAXF = 0.84

    def __init__(self, parent=None):
        super().__init__(parent)
        self.theme = THEME_DARK
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.messages = []          # dict(role, text, ts, t0, lay, tw, th)
        self.typing = False
        self.status = "在线"
        self.detail = ""
        self.presence = 1.0
        self.origin = QPointF()
        self._font = ui_font(14)
        self._items = []
        self._content_h = 0.0
        self._scroll = 0.0
        self._target = 0.0
        self._bar = 0.0
        self._hover = None
        self._press = None
        self._drag = False
        self._typing_t0 = 0.0
        self._btns = {}
        self._streaming_m = None
        self._chips = []
        self._chip_hover = -1
        self._choice_hover = None
        self._tool_group = None
        self._jump_rect = None
        self._jump_hover = False
        self.resize_corner = "tl"
        self._resizing = False
        self._resize_hover = False
        self._sel_m = None           # 当前选择的 message
        self._sel_rect = None        # 当前选择所在的气泡矩形
        self._sel_cursor = None      # 绑定在 message 文档上的 QTextCursor
        self._selecting = False
        self._press_link = None      # 按下时命中的链接
        self._images = {}
        self._image_pending = set()
        self._image_placeholders = {}
        self._image_base = PROJECT_ROOT + os.sep
        self._image_base_url = QUrl.fromLocalFile(self._image_base)
        self._nam = QNetworkAccessManager(self)

        self._scroll_anim = QVariantAnimation(self)
        self._scroll_anim.setDuration(240)
        self._scroll_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._scroll_anim.valueChanged.connect(self._on_scroll_value)
        self._bar_timer = QTimer(self)
        self._bar_timer.setSingleShot(True)
        self._bar_timer.timeout.connect(self._fade_bar)
        self._bar_fade = QTimer(self)
        self._bar_fade.setInterval(33)
        self._bar_fade.timeout.connect(self._fade_bar_step)
        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(33)
        self._anim_timer.timeout.connect(self._on_anim)
        self._layout_btns()

    # ------------------------------------------------------------------ #
    def add(self, role: str, text: str, animate: bool = True, meta: dict | None = None):
        was_bottom = self._target >= self._max_scroll() - 70
        m = {"role": role, "text": text, "ts": time.time(),
             "t0": time.monotonic() if animate else 0.0,
             "meta": dict(meta or {})}
        self._measure(m)
        self.messages.append(m)
        self._relayout()
        if role == "user" or was_bottom:
            self._scroll_to(self._max_scroll())
        self._kick()
        self.update()

    def add_choice(self, choice_id: str, question: str, options: list[str]):
        """在对话里插入一张带按钮的选择卡；同一 choice_id 只添加一次。"""
        if any(m.get("choice_id") == choice_id for m in self.messages):
            return
        m = {"role": "choice", "text": str(question or ""),
             "ts": time.time(), "t0": time.monotonic(),
             "choice_id": choice_id, "options": [str(x) for x in options],
             "selected": None, "cancelled": "", "meta": {}}
        self._measure(m)
        self.messages.append(m)
        self._relayout()
        self._scroll_to(self._max_scroll())
        self._kick()
        self.update()

    # ---- 工具调用聚合（折叠卡片）-------------------------------------- #
    def _ensure_tool_group(self):
        m = self._tool_group
        if m is not None and m in self.messages:
            return m
        was_bottom = self._target >= self._max_scroll() - 70
        m = {"role": "tool_group", "text": "", "steps": [], "running": False,
             "expanded": False, "summary": "工具调用", "ts": time.time(),
             "t0": time.monotonic(), "meta": {}}
        self.messages.append(m)
        self._tool_group = m
        self._relayout()
        if was_bottom:
            self._scroll_to(self._max_scroll(), animate=False)
        self._kick()
        self.update()
        return m

    @staticmethod
    def _tool_group_summary(m) -> str:
        steps = m.get("steps") or []
        fails = sum(1 for step in steps if step.get("status") == "fail")
        if m.get("running"):
            running = [step for step in steps if step.get("status") == "running"]
            if running:
                return f"{running[-1]['label']} 执行中…"
            return "工具执行中…"
        if not steps:
            return "工具调用"
        text = f"工具调用 · {len(steps)} 项"
        if fails:
            text += f" · {fails} 个失败"
        return text

    def _refresh_tool_group(self, m):
        m["running"] = any(step.get("status") == "running"
                           for step in m.get("steps") or [])
        m["summary"] = self._tool_group_summary(m)
        was_bottom = self._target >= self._max_scroll() - 70
        self._relayout()
        if was_bottom:
            self._scroll_to(self._max_scroll(), animate=False)
        self._kick()
        self.update()

    def tool_group_step(self, label: str, status: str = "running", detail: str = ""):
        """新增/更新一条工具步骤；running 时显示旋转等待动画。"""
        label = str(label or "").strip()
        if not label:
            return
        m = self._ensure_tool_group()
        step = None
        for item in reversed(m["steps"]):
            if item["label"] == label and item["status"] == "running":
                step = item
                break
        if step is None:
            step = {"label": label, "status": status, "detail": detail,
                    "t0": time.monotonic()}
            m["steps"].append(step)
        else:
            step["status"] = status
            if detail:
                step["detail"] = detail
        self._refresh_tool_group(m)

    def tool_group_result(self, label: str, ok: bool = True, detail: str = ""):
        """把最近的同名运行中步骤标记为完成/失败。"""
        label = str(label or "").strip()
        m = self._tool_group
        if not label or m is None or m not in self.messages:
            return
        target = None
        for item in reversed(m["steps"]):
            if item["label"] == label and item["status"] == "running":
                target = item
                break
        if target is None:
            target = next((item for item in reversed(m["steps"])
                           if item["label"] == label), None)
        if target is None:
            target = {"label": label, "status": "ok" if ok else "fail",
                      "detail": detail, "t0": time.monotonic()}
            m["steps"].append(target)
        else:
            target["status"] = "ok" if ok else "fail"
            if detail:
                target["detail"] = detail
        self._refresh_tool_group(m)

    def toggle_tool_group(self, m):
        m["expanded"] = not m.get("expanded", False)
        self._relayout()
        self.update()

    def finish_tool_group(self):
        """结束当前工具组并折叠；未收到结果的步骤标记为已结束。"""
        m = self._tool_group
        if m is None:
            return
        if m in self.messages:
            for step in m.get("steps") or []:
                if step.get("status") == "running":
                    step["status"] = "done"
            m["running"] = False
            m["expanded"] = False
            m["summary"] = self._tool_group_summary(m)
            self._relayout()
            self.update()
        self._tool_group = None

    def answer_choice(self, choice_id: str, option: str):
        for m in self.messages:
            if m.get("choice_id") == choice_id:
                m["selected"] = option
                m["cancelled"] = ""
                self._relayout()
                self.update()
                return

    def cancel_choice(self, choice_id: str, reason: str = "已取消"):
        for m in self.messages:
            if m.get("choice_id") == choice_id:
                m["selected"] = None
                m["cancelled"] = reason or "已取消"
                self._relayout()
                self.update()
                return

    def clear(self):
        self.messages.clear()
        self.typing = False
        self._streaming_m = None
        self._chips = []
        self._choice_hover = None
        self._tool_group = None
        self._jump_rect = None
        self._scroll_anim.stop()
        self._scroll = self._target = 0.0
        self._relayout()
        self.update()

    def set_typing(self, t: bool):
        t = bool(t)
        if t == self.typing:
            return
        was_bottom = self._target >= self._max_scroll() - 70
        self.typing = t
        self._typing_t0 = time.monotonic()
        self._relayout()
        if was_bottom:
            self._scroll_to(self._max_scroll())
        self._kick()
        self.update()

    def set_status(self, text: str, detail: str | None = None):
        changed = text != self.status or (detail is not None and detail != self.detail)
        self.status = text
        if detail is not None:
            self.detail = detail
        if changed:
            self.update(QRect(0, 0, self.width(), HEAD_H))

    # ---- 流式消息 ------------------------------------------------------ #
    def begin_stream(self):
        if self._streaming_m is not None:
            return
        self._clear_selection()
        was_bottom = self._target >= self._max_scroll() - 70
        m = {"role": "agent", "text": "", "ts": time.time(), "t0": 0.0,
             "streaming": True}
        self._measure(m)
        self.messages.append(m)
        self._streaming_m = m
        self._relayout()
        if was_bottom:
            self._scroll_to(self._max_scroll(), animate=False)
        self.update()

    def append_stream(self, delta: str):
        if not delta:
            return
        if self._streaming_m is None:
            self.begin_stream()
        self._clear_selection()
        was_bottom = self._target >= self._max_scroll() - 70
        self._streaming_m["text"] = self._streaming_m.get("text", "") + delta
        self._measure(self._streaming_m)
        self._relayout()
        if was_bottom:
            self._scroll_to(self._max_scroll(), animate=False)
        self.update()

    def finish_stream(self, text: str | None = None):
        m = self._streaming_m
        if m is None:
            return
        self._clear_selection()
        if text is not None:
            m["text"] = text
        m.pop("streaming", None)
        m["t0"] = 0.0
        was_bottom = self._target >= self._max_scroll() - 70
        self._streaming_m = None
        self._measure(m)
        self._relayout()
        if was_bottom:
            self._scroll_to(self._max_scroll(), animate=False)
        self.update()

    def stream_text(self) -> str:
        return self._streaming_m.get("text", "") if self._streaming_m else ""

    # ------------------------------------------------------------------ #
    def _inner_w(self) -> int:
        return self.width() - 2 * self.SIDE

    def _measure(self, m):
        if m.get("role") == "tool_group":
            m.pop("doc", None)
            m["tw"] = m["th"] = 0
            return
        max_bw = int(self._inner_w() * self.MAXF)
        content_w = max(40, max_bw - 2 * self.PX)
        th = self.theme
        font = self._font
        if m["role"] == "user":
            text_color = QColor(255, 255, 255)
            link_color = QColor(236, 244, 255)
            code_bg = QColor(255, 255, 255, 46)
        elif m["role"] == "tool":
            font = ui_font(12)
            text_color = th.label2
            ok = (m.get("meta") or {}).get("ok")
            if ok is True:
                text_color = th.success
            elif ok is False:
                text_color = th.danger
            link_color = th.accent
            code_bg = QColor(255, 255, 255, 26) if th.dark else QColor(0, 0, 0, 18)
        elif m["role"] == "error":
            text_color = th.label
            link_color = th.accent
            code_bg = QColor(255, 255, 255, 30) if th.dark else QColor(0, 0, 0, 22)
        elif m["role"] == "choice":
            text_color = th.bubble_in_text
            link_color = th.accent
            code_bg = QColor(255, 255, 255, 30) if th.dark else QColor(0, 0, 0, 22)
        else:
            text_color = th.bubble_in_text
            link_color = th.accent
            code_bg = QColor(255, 255, 255, 30) if th.dark else QColor(0, 0, 0, 22)
        doc = build_markdown_doc(m["text"], font, text_color, link_color, code_bg, content_w,
                                 image_provider=self._image_for,
                                 base_url=self._image_base_url)
        iw = doc.idealWidth()
        if m["role"] == "choice":
            opt_font = ui_font(12, QFont.Weight.DemiBold)
            fm = QFontMetrics(opt_font)
            opt_w = max((fm.horizontalAdvance(str(x)) for x in m.get("options", [])),
                        default=0) + 46
            tw = min(content_w, max(220, int(math.ceil(iw)) + 2, opt_w))
        else:
            tw = min(content_w, max(24, int(math.ceil(iw)) + 2))
        doc.setTextWidth(tw)
        m["doc"] = doc
        m["tw"] = int(math.ceil(doc.size().width()))
        m["th"] = int(math.ceil(doc.size().height()))
        m.pop("lay", None)

    def refresh_theme(self):
        """主题切换后重建 Markdown 文档，刷新文字 / 链接 / 代码配色。"""
        self._clear_selection()
        for m in self.messages:
            self._measure(m)
        self._relayout()
        self.update()

    def _relayout(self):
        items = []
        y = float(self.TOP)
        entries = list(self.messages)
        streaming_tail = bool(self.messages and self.messages[-1].get("streaming"))
        if self.typing and not streaming_tail:
            entries.append({"role": "agent", "typing": True, "ts": time.time(),
                            "t0": self._typing_t0})
        prev_role, prev_ts = None, None
        inner = self._inner_w()
        for i, m in enumerate(entries):
            ts = m["ts"]
            if prev_ts is None or ts - prev_ts > 300:
                if items:
                    y += 6
                items.append(("time", y, fmt_time(ts)))
                y += 16 + 8
                prev_role = None
            if prev_role is not None:
                y += self.GAP_SAME if prev_role == m["role"] else self.GAP_DIFF
            if m.get("typing"):
                bw, bh = 54, 34
            elif m["role"] == "tool":
                bw = m["tw"] + 2 * self.PX
                bh = max(22, m["th"] + 10)
            elif m["role"] == "tool_group":
                bw = max(220.0, min(inner * 0.92, 520.0))
                rows = len(m.get("steps") or []) if m.get("expanded") else 0
                bh = 36 + (rows * 24 + (8 if rows else 0))
            elif m["role"] == "choice":
                n = len(m.get("options", []))
                bw = m["tw"] + 2 * self.PX
                bh = m["th"] + 2 * self.PY + 10 + n * 32 + max(0, n - 1) * 6
                if m.get("cancelled"):
                    bh += 22
            else:
                bw = m["tw"] + 2 * self.PX
                bh = max(34, m["th"] + 2 * self.PY)
            nxt = entries[i + 1] if i + 1 < len(entries) else None
            tail = not (nxt and nxt["role"] == m["role"] and nxt["ts"] - ts <= 300)
            if m["role"] == "tool":
                x = self.SIDE + (inner - bw) / 2
                tail = False
            elif m["role"] == "tool_group":
                x = self.SIDE
                tail = False
            elif m["role"] == "user":
                x = self.SIDE + (inner - bw)
            else:
                x = self.SIDE
            items.append(("msg", QRectF(x, y, bw, bh), m, tail))
            if m["role"] == "tool_group":
                m["header_rect"] = QRectF(x, y, bw, 36)
            if m["role"] == "choice":
                rects = []
                oy = y + self.PY + m["th"] + 10
                for index in range(len(m.get("options", []))):
                    rects.append(QRectF(x + self.PX, oy, bw - 2 * self.PX, 32))
                    oy += 38
                m["option_rects"] = rects
                if m.get("cancelled"):
                    m["cancel_rect"] = QRectF(x + self.PX, oy, bw - 2 * self.PX, 18)
            y += bh
            prev_role, prev_ts = m["role"], ts
        self._items = items
        self._content_h = y + self.BOTTOM

    def _view_h(self) -> float:
        return max(0.0, self.height() - HEAD_H)

    def _max_scroll(self) -> float:
        return max(0.0, self._content_h - self._view_h())

    def _layout_btns(self):
        w = max(self.width(), 280)
        y = (HEAD_H - 28) / 2
        right = w - self.SIDE - 28
        self._btns = {
            "hide": QRectF(right, y, 28, 28),
            "clear": QRectF(right - 36, y, 28, 28),
            "settings": QRectF(right - 72, y, 28, 28),
        }

    def set_resize_corner(self, corner: str):
        if corner not in ("tl", "tr", "bl", "br") or corner == self.resize_corner:
            return
        self.resize_corner = corner
        self._layout_btns()
        self.update()

    def _resize_grip_rect(self) -> QRectF:
        w, h = self.width(), self.height()
        size = float(RESIZE_HANDLE)
        inset = 8.0
        top = HEAD_H + 6
        if self.resize_corner == "tl":
            return QRectF(inset, top, size, size)
        if self.resize_corner == "tr":
            return QRectF(w - inset - size, top, size, size)
        if self.resize_corner == "bl":
            return QRectF(inset, h - inset - size, size, size)
        return QRectF(w - inset - size, h - inset - size, size, size)

    def _resize_hit(self, pos: QPointF) -> bool:
        return self._resize_grip_rect().adjusted(-3, -3, 3, 3).contains(pos)

    def _resize_cursor(self):
        return (Qt.CursorShape.SizeFDiagCursor
                if self.resize_corner in ("tl", "br")
                else Qt.CursorShape.SizeBDiagCursor)

    def _paint_resize_grip(self, p: QPainter):
        if self.presence <= 0.35:
            return
        th = self.theme
        r = self._resize_grip_rect()
        active = self._resizing or self._resize_hover
        p.setPen(NO_PEN)
        p.setBrush(with_alpha(th.fill_hi, 220 if active else 95))
        p.drawRoundedRect(r, 7, 7)
        color = with_alpha(th.label2 if active else th.label3, 235 if active else 185)
        p.setPen(QPen(color, 1.35, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        for i in range(3):
            off = 5 + i * 4
            if self.resize_corner == "br":
                p.drawLine(QPointF(r.right() - off, r.bottom() - 2),
                           QPointF(r.right() - 2, r.bottom() - off))
            elif self.resize_corner == "bl":
                p.drawLine(QPointF(r.left() + off, r.bottom() - 2),
                           QPointF(r.left() + 2, r.bottom() - off))
            elif self.resize_corner == "tr":
                p.drawLine(QPointF(r.right() - off, r.top() + 2),
                           QPointF(r.right() - 2, r.top() + off))
            else:
                p.drawLine(QPointF(r.left() + off, r.top() + 2),
                           QPointF(r.left() + 2, r.top() + off))

    def resizeEvent(self, e):
        self._clear_selection()
        for m in self.messages:
            self._measure(m)
        self._relayout()
        self._scroll = self._target = clamp(self._scroll, 0.0, self._max_scroll())
        self._layout_btns()
        super().resizeEvent(e)

    # ---- 滚动 ---------------------------------------------------------- #
    def _scroll_to(self, target: float, animate: bool = True):
        self._target = clamp(target, 0.0, self._max_scroll())
        self._scroll_anim.stop()
        if animate and abs(self._target - self._scroll) > 1:
            self._scroll_anim.setStartValue(float(self._scroll))
            self._scroll_anim.setEndValue(float(self._target))
            self._scroll_anim.start()
        else:
            self._scroll = self._target
            self.update()
        self._show_bar()

    def _on_scroll_value(self, v):
        self._scroll = float(v)
        self.update()

    def _show_bar(self):
        self._bar_fade.stop()
        self._bar = 1.0
        self._bar_timer.start(900)

    def _fade_bar(self):
        self._bar_fade.start()

    def _fade_bar_step(self):
        self._bar = max(0.0, self._bar - 0.1)
        self.update()
        if self._bar <= 0.0:
            self._bar_fade.stop()

    def wheelEvent(self, e):
        px = e.pixelDelta().y()
        dy = px if px else e.angleDelta().y() * 0.55
        self._scroll_to(self._target - dy, animate=not px)
        e.accept()

    # ---- 动画 ---------------------------------------------------------- #
    def _kick(self):
        if not self._anim_timer.isActive():
            self._anim_timer.start()

    def _on_anim(self):
        now = time.monotonic()
        busy = (self.typing or self._streaming_m is not None
                or any(m.get("role") == "tool_group" and m.get("running")
                       for m in self.messages)
                or any(m.get("t0") and now - m.get("t0", 0) < 0.45
                       for m in self.messages[-3:]))
        self.update()
        if not busy:
            self._anim_timer.stop()

    # ---- 绘制 ---------------------------------------------------------- #
    def paintEvent(self, _):
        if self.presence <= 0.005:
            return
        th = self.theme
        w, h = self.width(), self.height()
        p = QPainter(self)
        p.setRenderHints(AA | SMOOTH | TEXT_AA)
        pres = self.presence
        p.setOpacity(clamp(pres * 1.35))
        s = 0.90 + 0.10 * pres                           # 从球的方向「弹」出来
        p.translate(self.origin)
        p.scale(s, s)
        p.translate(-self.origin.x(), -self.origin.y())

        path = QPainterPath()
        path.addRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), R_CARD, R_CARD)
        g = QLinearGradient(0, 0, 0, h)
        g.setColorAt(0, th.card_top)
        g.setColorAt(1, th.card_bot)
        p.fillPath(path, QBrush(g))

        self._paint_body(p, path)
        self._paint_header(p)

        p.setPen(QPen(th.hairline, 1.0))
        p.setBrush(NO_BRUSH)
        p.drawPath(path)
        self._paint_resize_grip(p)

    def _paint_header(self, p: QPainter):
        th = self.theme
        w = self.width()
        cy = HEAD_H / 2
        # 不透明标题底：滚动时消息不会从标题栏里透出来
        header_path = rrect_path(QRectF(0.5, 0.5, w - 1, HEAD_H),
                                 R_CARD, R_CARD, 0, 0)
        hg = QLinearGradient(0, 0, 0, HEAD_H)
        hg.setColorAt(0, th.card_top)
        hg.setColorAt(1, mix(th.card_top, th.card_bot, 0.35))
        p.fillPath(header_path, QBrush(hg))
        draw_orb(p, QPointF(self.SIDE + 15, cy), 14)
        x0 = self.SIDE + 38
        right = self._btns["settings"].left() - 10
        p.setPen(th.label)
        if self.detail:
            p.setFont(ui_font(13, QFont.Weight.DemiBold))
            p.drawText(QRectF(x0, cy - 17, max(20, right - x0), 18), A_LEFT_V, self.status)
            p.setPen(th.label2)
            p.setFont(ui_font(10))
            p.drawText(QRectF(x0, cy + 1, max(20, right - x0), 14), A_LEFT_V, self.detail)
        else:
            p.setFont(ui_font(13, QFont.Weight.DemiBold))
            p.drawText(QRectF(x0, cy - 10, max(20, right - x0), 20), A_LEFT_V, self.status)
        kinds = {"settings": "gear", "clear": "trash", "hide": "chevron"}
        for name, rect in self._btns.items():
            hov = self._hover == name
            p.setPen(NO_PEN)
            p.setBrush(th.fill_hi if hov else th.fill)
            p.drawEllipse(rect)
            draw_glyph(p, kinds[name], rect.center(), 15,
                       th.label if hov else th.label2)
        p.setPen(QPen(th.sep, 1.0))
        p.drawLine(QPointF(0, HEAD_H), QPointF(w, HEAD_H))

    def _paint_empty(self, p: QPainter, view: QRectF):
        th = self.theme
        c = QPointF(view.center().x(), view.center().y() - 88)
        draw_orb(p, c, 26)
        p.setPen(th.label)
        p.setFont(ui_font(15, QFont.Weight.DemiBold))
        p.drawText(QRectF(view.left(), c.y() + 38, view.width(), 22), A_CENTER, "有什么可以帮你？")
        p.setPen(th.label2)
        p.setFont(ui_font(12))
        p.drawText(QRectF(view.left(), c.y() + 64, view.width(), 18), A_CENTER,
                   "输入消息，或点一个例子开始")

        self._chips = []
        font = ui_font(12)
        fm = QFontMetrics(font)
        y = c.y() + 98
        for text in self.SUGGESTIONS:
            w = min(view.width() - 48, fm.horizontalAdvance(text) + 30)
            x = view.center().x() - w / 2
            rect = QRectF(x, y, w, 30)
            idx = len(self._chips)
            hov = self._chip_hover == idx
            p.setPen(NO_PEN)
            p.setBrush(th.fill_hi if hov else th.fill)
            p.drawRoundedRect(rect, 15, 15)
            p.setPen(th.label if hov else th.label2)
            p.setFont(font)
            p.drawText(rect, A_CENTER, text)
            self._chips.append((rect, text))
            y += 36

    def _paint_body(self, p: QPainter, card_path: QPainterPath):
        th = self.theme
        w, h = self.width(), self.height()
        view = QRectF(0, HEAD_H, w, h - HEAD_H)
        clip = QPainterPath()
        clip.addRect(view)
        p.save()
        p.setClipPath(clip.intersected(card_path))
        if not self._items:
            self._paint_empty(p, view)
            p.restore()
            self._jump_rect = None
            return
        self._chips = []
        now = time.monotonic()
        p.translate(0, HEAD_H - self._scroll)
        lo, hi = self._scroll - 70, self._scroll + view.height() + 70
        for it in self._items:
            if it[0] == "time":
                _, y, text = it
                if y < lo or y > hi:
                    continue
                p.setPen(th.label2)
                p.setFont(ui_font(11, QFont.Weight.DemiBold))
                p.drawText(QRectF(0, y, w, 16), A_CENTER, text)
            else:
                _, rect, m, tail = it
                if rect.bottom() < lo or rect.top() > hi:
                    continue
                if m.get("role") == "tool_group":
                    self._paint_tool_group(p, rect, m, now)
                else:
                    self._paint_bubble(p, rect, m, tail, now)

        # 跳到最新：离开底部时出现的悬浮小按钮
        self._jump_rect = None
        if (self._max_scroll() > 40
                and self._target < self._max_scroll() - 40):
            jr = QRectF(w - 42, HEAD_H + view.height() - 44, 30, 30)
            self._jump_rect = jr
            p.resetTransform()
            self._presence_xform(p)
            p.setClipping(False)
            p.setPen(NO_PEN)
            p.setBrush(th.fill_hi if self._jump_hover else th.fill)
            p.drawEllipse(jr)
            draw_glyph(p, "arrow_down", jr.center(), 14,
                       th.label if self._jump_hover else th.label2)

        if self._content_h > view.height() and self._bar > 0.01:     # overlay 滚动条
            p.resetTransform()
            self._presence_xform(p)
            p.setClipping(False)
            bar_h = max(28.0, view.height() ** 2 / self._content_h)
            top = HEAD_H + 6 + (self._scroll / max(1.0, self._max_scroll())) * (view.height() - 12 - bar_h)
            p.setPen(NO_PEN)
            p.setBrush(with_alpha(th.label3, th.label3.alpha() * self._bar))
            p.drawRoundedRect(QRectF(w - 6, top, 3, bar_h), 1.5, 1.5)
        p.restore()

    def _presence_xform(self, p: QPainter):
        p.translate(self.origin)
        s = 0.90 + 0.10 * self.presence
        p.scale(s, s)
        p.translate(-self.origin.x(), -self.origin.y())

    def _paint_bubble(self, p: QPainter, rect: QRectF, m, tail: bool, now: float):
        th = self.theme
        user = m["role"] == "user"
        k = ease_out((now - m["t0"]) / 0.32) if m.get("t0") else 1.0
        p.save()
        if k < 1.0:                                      # 新气泡：淡入 + 上浮 + 轻微放大
            org = QPointF(rect.right() if user else rect.left(), rect.bottom())
            p.setOpacity(p.opacity() * k)
            p.translate(org.x(), org.y() + (1.0 - k) * 10)
            s = 0.86 + 0.14 * k
            p.scale(s, s)
            p.translate(-org.x(), -org.y())

        R = min(18.0, rect.height() / 2)
        small = 5.0
        path = rrect_path(rect, R, R, small if (tail and user) else R,
                          small if (tail and not user) else R)
        if user:
            g = QLinearGradient(rect.topLeft(), rect.bottomLeft())
            g.setColorAt(0, th.accent.lighter(112))
            g.setColorAt(1, th.accent)
            p.fillPath(path, QBrush(g))
        elif m["role"] == "tool":
            ok = (m.get("meta") or {}).get("ok")
            if ok is True:
                p.fillPath(path, with_alpha(th.success, 36))
            elif ok is False:
                p.fillPath(path, with_alpha(RED, 36))
            else:
                p.fillPath(path, with_alpha(th.fill, 150))
        elif m["role"] == "error":
            p.fillPath(path, th.bubble_error)
            p.setPen(QPen(with_alpha(RED, 120), 1.0))
            p.setBrush(NO_BRUSH)
            p.drawPath(path)
        elif m["role"] == "choice":
            p.fillPath(path, th.bubble_in)
            p.setPen(QPen(with_alpha(th.accent, 95), 1.0))
            p.setBrush(NO_BRUSH)
            p.drawPath(path)
        else:
            p.fillPath(path, th.bubble_in)

        if m.get("typing"):                              # 输入中 · 三点波浪
            t = now * 7.0
            p.setPen(NO_PEN)
            for i in range(3):
                a = 0.35 + 0.65 * (0.5 + 0.5 * math.sin(t - i * 0.9))
                p.setBrush(with_alpha(th.label2, th.label2.alpha() * a / 0.63))
                p.drawEllipse(QPointF(rect.left() + 17 + i * 10.5, rect.center().y()), 3.1, 3.1)
        else:
            doc = m.get("doc")
            if doc is not None:
                tx = rect.x() + self.PX
                if m["role"] == "choice":
                    ty = rect.y() + self.PY
                else:
                    ty = rect.y() + (rect.height() - m["th"]) / 2
                p.save()
                p.setClipRect(rect.adjusted(1, 0, -1, 0))
                p.translate(tx, ty)
                ctx = QAbstractTextDocumentLayout.PaintContext()
                if (self._sel_cursor is not None and self._sel_m is m
                        and self._sel_cursor.hasSelection()):
                    sel = QAbstractTextDocumentLayout.Selection()
                    sel.cursor = self._sel_cursor
                    sf = QTextCharFormat()
                    sf.setBackground(QColor(255, 255, 255, 70) if user
                                     else with_alpha(th.accent, 96))
                    sel.format = sf
                    ctx.selections = [sel]
                ctx.clip = QRectF(0, 0, doc.size().width(), doc.size().height())
                doc.documentLayout().draw(p, ctx)
                if m.get("streaming") and m.get("text"):
                    t = now % 1.0                              # 光标轻微呼吸
                    alpha = 120 + int(110 * (0.5 + 0.5 * math.sin(t * 6.28)))
                    cx = rect.x() + self.PX + min(doc.size().width(),
                                                  rect.width() - 2 * self.PX) + 1
                    cy = rect.y() + (rect.height() - m["th"]) / 2 + m["th"] - 13
                    p.setPen(NO_PEN)
                    p.setBrush(with_alpha(th.accent, alpha if not user else 220))
                    p.drawRoundedRect(QRectF(cx, cy, 2, 13), 1, 1)
                p.restore()
        if m["role"] == "choice":
            self._paint_choices(p, m)
        p.restore()

    def _draw_spinner(self, p: QPainter, center: QPointF, now: float,
                      color: QColor, size: float = 7.0):
        r = QRectF(center.x() - size, center.y() - size, size * 2, size * 2)
        p.setBrush(NO_BRUSH)
        p.setPen(QPen(with_alpha(color, 48), 2.0))
        p.drawEllipse(r)
        p.setPen(QPen(color, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawArc(r, int((-now * 320) % 360 * 16), 100 * 16)

    def _paint_tool_group(self, p: QPainter, rect: QRectF, m, now: float):
        """折叠式工具活动卡片：运行时显示旋转等待环，点击标题展开步骤。"""
        th = self.theme
        steps = m.get("steps") or []
        running = bool(m.get("running"))
        failed = any(step.get("status") == "fail" for step in steps)
        path = rrect_path(rect, 14, 14, 14, 14)
        p.fillPath(path, with_alpha(th.fill, 165 if th.dark else 115))
        p.setPen(QPen(with_alpha(th.hairline, 220), 1.0))
        p.setBrush(NO_BRUSH)
        p.drawPath(path)

        header = m.get("header_rect") or QRectF(rect.left(), rect.top(), rect.width(), 36)
        cy = header.top() + 18
        if running:
            self._draw_spinner(p, QPointF(header.left() + 20, cy), now, th.accent, 7)
        else:
            color = th.danger if failed else (th.success if steps else th.label3)
            p.setPen(NO_PEN)
            p.setBrush(with_alpha(color, 94))
            p.drawEllipse(QPointF(header.left() + 20, cy), 5, 5)
            p.setPen(QPen(color, 1.2))
            p.setBrush(NO_BRUSH)
            p.drawEllipse(QPointF(header.left() + 20, cy), 7, 7)

        font = ui_font(12, QFont.Weight.DemiBold)
        p.setFont(font)
        fm = QFontMetrics(font)
        p.setPen(th.label if running else th.label2)
        text_rect = QRectF(header.left() + 34, header.top() + 8,
                           max(20.0, header.width() - 78), 20)
        summary = fm.elidedText(str(m.get("summary") or "工具调用"),
                                Qt.TextElideMode.ElideRight, int(text_rect.width()))
        p.drawText(text_rect, A_LEFT_V, summary)

        p.save()
        p.translate(rect.right() - 18, cy)
        if m.get("expanded"):
            p.rotate(180)
        draw_glyph(p, "chevron", QPointF(0, 0), 13, th.label3)
        p.restore()

        if not m.get("expanded"):
            return
        font = ui_font(11)
        p.setFont(font)
        fm = QFontMetrics(font)
        y = rect.top() + 40
        for step in steps:
            status = step.get("status")
            sx, sy = rect.left() + 20, y + 12
            if status == "running":
                self._draw_spinner(p, QPointF(sx, sy), now, th.accent, 5)
                color = th.label
            elif status == "fail":
                draw_glyph(p, "x", QPointF(sx, sy), 12, th.danger)
                color = th.danger
            elif status == "ok":
                draw_glyph(p, "check", QPointF(sx, sy), 12, th.success)
                color = th.label2
            else:
                p.setPen(NO_PEN)
                p.setBrush(with_alpha(th.label3, 130))
                p.drawEllipse(QPointF(sx, sy), 3, 3)
                color = th.label3
            p.setPen(color)
            label = fm.elidedText(str(step.get("label") or ""),
                                  Qt.TextElideMode.ElideRight,
                                  max(20, int(rect.width() - 72)))
            p.drawText(QRectF(rect.left() + 36, y, rect.width() - 52, 24),
                       A_LEFT_V, label)
            y += 24

    def _paint_choices(self, p: QPainter, m):
        th = self.theme
        options = m.get("options") or []
        rects = m.get("option_rects") or []
        if not options or len(rects) != len(options):
            return
        font = ui_font(12, QFont.Weight.DemiBold)
        fm = QFontMetrics(font)
        p.setFont(font)
        selected = m.get("selected")
        cancelled = bool(m.get("cancelled"))
        for index, (option, rect) in enumerate(zip(options, rects)):
            is_selected = option == selected
            answered = selected is not None or cancelled
            hover = (self._choice_hover == (id(m), index)) and not answered
            if is_selected:
                fill, border, color = th.accent, th.accent, QColor(255, 255, 255)
            elif answered:
                fill = with_alpha(th.fill, 80)
                border = th.hairline
                color = th.label3
            elif hover:
                fill, border, color = th.fill_hi, with_alpha(th.accent, 120), th.label
            else:
                fill = with_alpha(th.fill, 150)
                border = th.hairline
                color = th.label2
            p.setPen(QPen(border, 1.0))
            p.setBrush(fill)
            p.drawRoundedRect(rect, 10, 10)
            text_rect = rect.adjusted(12, 0, -34 if is_selected else -12, 0)
            p.setPen(color)
            label = fm.elidedText(str(option), Qt.TextElideMode.ElideRight,
                                  max(20, int(text_rect.width())))
            p.drawText(text_rect, A_LEFT_V, label)
            if is_selected:
                draw_glyph(p, "check", QPointF(rect.right() - 17, rect.center().y()),
                           12, QColor(255, 255, 255))
        if cancelled:
            cr = m.get("cancel_rect")
            if cr is not None:
                p.setPen(th.label3)
                p.setFont(ui_font(11))
                p.drawText(cr, A_LEFT_V, str(m.get("cancelled")))

    # ---- 悬停 / 选择 / 链接 -------------------------------------------- #
    def _copy_text(self, text: str):
        text = text or ""
        QGuiApplication.clipboard().setText(text)
        if text:
            self.copied.emit(text)

    # ---- Markdown 图片 -------------------------------------------------- #
    def _placeholder_image(self, text: str) -> QImage:
        key = (text, self.theme.dark)
        cached = self._image_placeholders.get(key)
        if cached is not None:
            return cached
        w, h = 300, 84
        img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        p.setRenderHint(AA)
        path = QPainterPath()
        path.addRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), 10, 10)
        p.fillPath(path, QBrush(self.theme.fill))
        p.setPen(self.theme.label2)
        p.setFont(ui_font(12))
        p.drawText(QRectF(0, 0, w, h), A_CENTER, text)
        p.end()
        self._image_placeholders[key] = img
        return img

    def _prepare_image(self, img: QImage) -> QImage:
        if img.isNull():
            return self._placeholder_image("图片无法显示")
        if img.width() > 1200 or img.height() > 1200:
            img = img.scaled(1200, 1200, Qt.AspectRatioMode.KeepAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
        w, h = img.width(), img.height()
        out = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
        out.fill(Qt.GlobalColor.transparent)
        p = QPainter(out)
        p.setRenderHint(AA)
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, w, h), 10, 10)
        p.setClipPath(path)
        p.drawImage(0, 0, img)
        p.end()
        return out

    def _image_for(self, src: str):
        src = str(src or "").strip()
        if not src:
            return None
        cached = self._images.get(src)
        if cached is not None:
            return cached
        lower = src.lower()
        if lower.startswith(("http://", "https://")):
            if src not in self._image_pending:
                self._image_pending.add(src)
                self._fetch_image(src)
            return self._placeholder_image("图片加载中…")

        path = src
        if lower.startswith("file:"):
            path = QUrl(src).toLocalFile()
        else:
            if "%" in path:
                path = QUrl.fromPercentEncoding(path.encode("utf-8"))
            if not os.path.isabs(path):
                path = os.path.join(self._image_base, path)
        img = QImage(path)
        if img.isNull():
            img = self._placeholder_image("图片无法显示")
        else:
            img = self._prepare_image(img)
        self._images[src] = img
        return img

    def _fetch_image(self, src: str):
        req = QNetworkRequest(QUrl(src))
        req.setRawHeader(b"User-Agent", b"Mozilla/5.0 (Windows NT 10.0; Win64; x64)")
        if hasattr(req, "setTransferTimeout"):
            req.setTransferTimeout(15000)
        reply = self._nam.get(req)
        reply.finished.connect(lambda r=reply, u=src: self._on_image_reply(r, u))

    def _on_image_reply(self, reply, src: str):
        self._image_pending.discard(src)
        img = None
        try:
            if reply.error() == QNetworkReply.NetworkError.NoError:
                data = bytes(reply.readAll())
                if 0 < len(data) <= 8 * 1024 * 1024:
                    candidate = QImage()
                    if candidate.loadFromData(data) and not candidate.isNull():
                        img = candidate
        finally:
            reply.deleteLater()
        if img is not None:
            self._images[src] = self._prepare_image(img)
        else:
            self._images[src] = self._placeholder_image("图片无法显示")
        self._reload_images(src)

    def _reload_images(self, src: str):
        self._clear_selection()
        was_bottom = self._target >= self._max_scroll() - 70
        for m in self.messages:
            self._measure(m)
        self._relayout()
        if was_bottom:
            self._scroll_to(self._max_scroll(), animate=False)
        self.update()

    def _content_pos(self, pos: QPointF) -> QPointF:
        return QPointF(pos.x(), pos.y() - HEAD_H + self._scroll)

    def _find_item(self, pos: QPointF):
        if pos.y() < HEAD_H:
            return None
        pt = self._content_pos(pos)
        for it in self._items:
            if (it[0] == "msg" and it[1].contains(pt)
                    and not it[2].get("typing")
                    and it[2].get("role") != "tool_group"):
                return it
        return None

    def _hit_btn(self, pos: QPointF):
        for name, rect in self._btns.items():
            if rect.adjusted(-3, -3, 3, 3).contains(pos):
                return name
        return None

    def _hit_chip(self, pos: QPointF) -> int:
        for i, (rect, _text) in enumerate(self._chips):
            if rect.contains(pos):
                return i
        return -1

    def _hit_jump(self, pos: QPointF) -> bool:
        return self._jump_rect is not None and self._jump_rect.adjusted(-2, -2, 2, 2).contains(pos)

    def _hit_choice(self, pos: QPointF):
        if pos.y() < HEAD_H:
            return None
        pt = self._content_pos(pos)
        for it in self._items:
            if it[0] != "msg":
                continue
            m = it[2]
            if (m.get("role") != "choice" or m.get("selected")
                    or m.get("cancelled")):
                continue
            for index, rect in enumerate(m.get("option_rects") or []):
                if rect.contains(pt):
                    return m, index
        return None

    def _doc_local(self, rect: QRectF, m, pos: QPointF) -> QPointF:
        pt = self._content_pos(pos)
        return QPointF(pt.x() - (rect.x() + self.PX),
                       pt.y() - (rect.y() + (rect.height() - m["th"]) / 2))

    def _doc_index(self, rect: QRectF, m, pos: QPointF) -> int:
        doc = m.get("doc")
        if doc is None:
            return -1
        local = self._doc_local(rect, m, pos)
        idx = doc.documentLayout().hitTest(local, Qt.HitTestAccuracy.FuzzyHit)
        if idx < 0:
            idx = 0 if local.y() <= 0 else doc.characterCount() - 1
        return max(0, min(idx, doc.characterCount() - 1))

    def _hit_tool_group(self, pos: QPointF):
        if pos.y() < HEAD_H:
            return None
        pt = self._content_pos(pos)
        for it in self._items:
            if it[0] != "msg":
                continue
            m = it[2]
            if m.get("role") == "tool_group":
                header = m.get("header_rect")
                if header is not None and header.contains(pt):
                    return m
        return None

    def _hit_link(self, pos: QPointF):
        it = self._find_item(pos)
        if not it:
            return None
        rect, m = it[1], it[2]
        doc = m.get("doc")
        if doc is None:
            return None
        idx = doc.documentLayout().hitTest(self._doc_local(rect, m, pos),
                                           Qt.HitTestAccuracy.ExactHit)
        if idx < 0:
            return None
        cur = QTextCursor(doc)
        cur.setPosition(idx)
        cf = cur.charFormat()
        if cf.isAnchor() and cf.anchorHref():
            return cf.anchorHref()
        return None

    def _selection_text(self) -> str:
        cur = self._sel_cursor
        if cur is not None and cur.hasSelection():
            return cur.selectedText().replace("\u2029", "\n")
        return ""

    def _selection_rect(self):
        if self._sel_rect is not None:
            return self._sel_rect
        return None

    def _select_all(self):
        m = self._sel_m
        if m is None:
            cands = [x for x in self.messages if not x.get("typing") and x.get("doc")]
            m = cands[-1] if cands else None
        if m is None or m.get("doc") is None:
            return
        rect = None
        for it in self._items:
            if it[0] == "msg" and it[2] is m:
                rect = it[1]
                break
        cur = QTextCursor(m["doc"])
        cur.select(QTextCursor.SelectionType.Document)
        self._sel_m, self._sel_rect, self._sel_cursor = m, rect, cur
        self.update()

    def _clear_selection(self):
        had = getattr(self, "_sel_cursor", None) is not None
        self._sel_cursor = None
        self._sel_m = None
        self._sel_rect = None
        if had:
            self.update()

    def _begin_selection(self, it, pos: QPointF):
        rect, m = it[1], it[2]
        cur = QTextCursor(m["doc"])
        cur.setPosition(self._doc_index(rect, m, pos))
        self._sel_m, self._sel_rect, self._sel_cursor = m, rect, cur
        self._selecting = True
        self.update()

    def keyPressEvent(self, e):
        if e.matches(QKeySequence.StandardKey.Copy):
            t = self._selection_text()
            if t:
                self._copy_text(t)
            e.accept()
        elif e.matches(QKeySequence.StandardKey.SelectAll):
            self._select_all()
            e.accept()
        else:
            super().keyPressEvent(e)

    def mousePressEvent(self, e):
        pos = e.position()
        if e.button() == Qt.MouseButton.LeftButton:
            if self._resize_hit(pos):
                self._resizing = True
                self._resize_hover = True
                self.setCursor(self._resize_cursor())
                self.resize_event.emit("press", e.globalPosition().toPoint())
                self.update()
                e.accept()
                return
            group = self._hit_tool_group(pos)
            if group is not None:
                self.toggle_tool_group(group)
                e.accept()
                return
            name = self._hit_btn(pos)
            idx = self._hit_chip(pos)
            if name:
                self._press = name
                self.update()
            elif idx >= 0:
                self.suggestion_clicked.emit(self._chips[idx][1])
            elif self._hit_jump(pos):
                self._scroll_to(self._max_scroll())
                self._jump_hover = False
            elif self._hit_choice(pos) is not None:
                m, index = self._hit_choice(pos)
                option = m["options"][index]
                m["selected"] = option
                m["cancelled"] = ""
                self._choice_hover = None
                self._relayout()
                self.update()
                self.choice_selected.emit(m["choice_id"], option)
            elif pos.y() < HEAD_H:                       # 拖动标题栏移动整个悬浮球
                self._drag = True
                self.drag_event.emit("press", e.globalPosition().toPoint())
            else:
                link = self._hit_link(pos)
                if link is not None:                     # 点链接：留给松开时打开
                    self._press_link = link
                else:
                    it = self._find_item(pos)
                    if it:
                        self.setFocus(Qt.FocusReason.MouseFocusReason)
                        self._begin_selection(it, pos)
                    else:
                        self._clear_selection()
            e.accept()
        elif e.button() == Qt.MouseButton.RightButton:
            it = self._find_item(pos)
            menu = QMenu(self)
            style_menu(menu, self.theme, first_time=True)
            sel = self._selection_text()
            if sel:
                menu.addAction("复制选中", lambda t=sel: self._copy_text(t))
            if it is not None:
                m = it[2]
                menu.addAction("复制这条消息", lambda t=m["text"]: self._copy_text(t))
            if len(self.messages) > 1:
                menu.addAction("复制全部", lambda: self._copy_text(
                    "\n\n".join(x["text"] for x in self.messages)))
            menu.addSeparator()
            menu.addAction("全选", self._select_all)
            menu.addSeparator()
            menu.addAction("添加文件到知识库…", self.database_add_files.emit)
            if not menu.isEmpty():
                menu.exec(e.globalPosition().toPoint())
            e.accept()

    def mouseMoveEvent(self, e):
        pos = e.position()
        if self._resizing:
            self.resize_event.emit("move", e.globalPosition().toPoint())
            e.accept()
            return
        resize_hover = self._resize_hit(pos)
        if resize_hover != self._resize_hover:
            self._resize_hover = resize_hover
            self.setCursor(self._resize_cursor() if resize_hover
                           else Qt.CursorShape.ArrowCursor)
            self.update()
        if self._drag:
            self.drag_event.emit("move", e.globalPosition().toPoint())
            e.accept()
            return
        if self._selecting and self._sel_cursor is not None:
            idx = self._doc_index(self._sel_rect, self._sel_m, pos)
            if idx >= 0:
                self._sel_cursor.setPosition(idx, QTextCursor.MoveMode.KeepAnchor)
                self.update()
            e.accept()
            return
        name = self._hit_btn(pos)
        if name != self._hover:
            self._hover = name
            self.update(QRect(0, 0, self.width(), HEAD_H))
        chip = self._hit_chip(pos)
        if chip != self._chip_hover:
            self._chip_hover = chip
            self.update()
        jump = self._hit_jump(pos)
        if jump != self._jump_hover:
            self._jump_hover = jump
            self.update()
        choice = self._hit_choice(pos)
        if choice != self._choice_hover:
            self._choice_hover = choice
            self.update()
        tool_group = self._hit_tool_group(pos)
        link = self._hit_link(pos)
        if resize_hover:
            self.setCursor(self._resize_cursor())
            self.setToolTip("拖动调整消息窗大小")
        elif name:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setToolTip({"settings": "设置", "clear": "清空会话",
                             "hide": "隐藏消息气泡"}[name])
        elif chip >= 0:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setToolTip("")
        elif jump:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setToolTip("回到最新消息")
        elif choice is not None:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setToolTip("")
        elif tool_group is not None:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setToolTip("展开 / 收起工具调用")
        elif link:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setToolTip(link)
        elif pos.y() < HEAD_H:
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            self.setToolTip("")
        elif self._find_item(pos):
            self.setCursor(Qt.CursorShape.IBeamCursor)     # 气泡内：文本选择光标
            self.setToolTip("")
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self.setToolTip("")

    def leaveEvent(self, e):
        changed = (bool(self._hover) or self._chip_hover >= 0 or self._jump_hover
                   or self._resize_hover)
        self._hover = None
        self._chip_hover = -1
        self._choice_hover = None
        self._jump_hover = False
        self._resize_hover = False
        if changed:
            self.update()
        self.setToolTip("")

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        if self._resizing:
            self._resizing = False
            self.resize_event.emit("release", e.globalPosition().toPoint())
            self._resize_hover = self._resize_hit(e.position())
            self.setCursor(self._resize_cursor() if self._resize_hover
                           else Qt.CursorShape.ArrowCursor)
            self.update()
            e.accept()
            return
        if self._drag:
            self._drag = False
            self.drag_event.emit("release", e.globalPosition().toPoint())
        elif self._selecting:
            self._selecting = False
            self.update()
        elif self._press:
            name, self._press = self._press, None
            if self._hit_btn(e.position()) == name:
                if name == "hide":
                    self.hide_clicked.emit()
                elif name == "settings":
                    self.settings_clicked.emit()
                else:
                    self.clear_clicked.emit()
            self.update()
        elif self._press_link is not None:
            href, self._press_link = self._press_link, None
            if self._hit_link(e.position()) == href:
                QDesktopServices.openUrl(QUrl(href))
        e.accept()


# --------------------------------------------------------------------------- #
# Agent（会话智能体）
# --------------------------------------------------------------------------- #
def build_subagent_specs(cfg: dict, base_tools: list, cwd: str,
                         stop_event=None) -> list[SubagentSpec]:
    """把配置里的子智能体条目翻译成框架的 SubagentSpec。

    items 为空时使用内置的 researcher / coder / writer；否则以配置为准，
    这样设置页可以停用、改名或新增角色。
    """
    sub = cfg.get("subagents") or {}
    if not sub.get("enabled", True):
        return []
    by_name = {t.name: t for t in base_tools}
    builtins = {s.name: s for s in available_subagents(
        cwd, workspace=cfg.get("workspace"), database=cfg.get("database"),
        stop_event=stop_event, embedding_model=cfg.get("embedding_model"),
        embedding_cache=cfg.get("embedding_cache"))}
    items = sub.get("items") or []
    if not items:
        return list(builtins.values())

    specs: list[SubagentSpec] = []
    for raw in items:
        if not isinstance(raw, dict) or not raw.get("enabled", True):
            continue
        name = str(raw.get("name") or "").strip()
        if not name:
            continue
        base = builtins.get(name)
        names = [str(x) for x in (raw.get("tools") or []) if str(x).strip()]
        if names:
            tools = [by_name[n] for n in names if n in by_name]
        elif base is not None:
            tools = list(base.tools or [])
        else:
            tools = None                      # 继承主智能体全部工具
        spec = SubagentSpec(
            name=name,
            description=str(raw.get("description") or (base.description if base else "")
                            ).strip() or f"子智能体 {name}",
            system_prompt=str(raw.get("system") or (base.system_prompt if base else "")),
            tools=tools,
            allowed_tools=(names or None) if tools is not None else None,
            model=str(raw.get("model") or "").strip() or None,
            max_turns=int(raw.get("max_turns") or (base.max_turns if base else 6)),
            permission_mode=raw.get("permission_mode") or None,
        )
        specs.append(spec)
    return specs


def local_reply(text: str) -> str:
    t = text.lower()
    now = datetime.now()
    if any(k in t for k in ("你好", "您好", "hi", "hello", "在吗", "hey")):
        return "你好呀！有什么可以帮你的吗？"
    if "时间" in t or "几点" in t or "time" in t:
        return f"现在是 {now.strftime('%H:%M:%S')}。"
    if "日期" in t or "几号" in t or "date" in t:
        return f"今天是 {now.strftime('%Y-%m-%d')}。"
    if "天气" in t or "weather" in t:
        return "天气需要接入在线 API 才能查询，可以帮我配置一下哦。"
    if "设置" in t or "配置" in t or "模型" in t:
        return ("在悬浮球右键菜单里选择「设置…」，可以配置模型接口、语音识别、"
                "外观，以及可委派的子智能体。")
    if "子智能体" in t or "agent" in t:
        return ("配置远程模型后，主智能体会在需要时通过 task 工具把检索、编码或写作"
                "交给独立上下文的子智能体；右键 → 设置 →「子智能体」即可管理角色。")
    if "帮助" in t or "help" in t or "怎么用" in t:
        return ("点按悬浮球展开对话；输入栏旁的按钮可以隐藏 / 显示消息；"
                "麦克风可以语音输入；右键悬浮球可以打开设置、切换外观或退出。")
    if "谢谢" in t or "thanks" in t or "thank" in t:
        return "不客气，随时找我～"
    return (f"我听到了：{text}\n\n"
            "当前是本地模式，只能做简单问答。"
            "在设置里配置模型接口后，我就能调用工具和子智能体了。")


class Agent:
    """桌面端智能体：封装 ``agent_framework.Agent``，补充配置热更新与工具策略。"""

    def __init__(self, permission_cb=None, on_event=None, ask_user_cb=None):
        self.cwd = PROJECT_ROOT
        self.permission_cb = permission_cb
        self.on_event = on_event
        self.ask_user_cb = ask_user_cb
        self.cfg: dict = {}
        self.core: AgentCore | None = None
        self.reload(preserve_history=False)

    def reload(self, preserve_history: bool = True):
        """重新读取配置并重建核心；默认保留对话上下文。"""
        history = list(self.core.history) if self.core is not None else []
        self.cfg = load_agent_config()
        stop_event = threading.Event()
        tools = default_tools(self.cwd, stop_event=stop_event)
        database = self.cfg.get("database") or os.path.join(self.cwd, "database")
        tools += build_database_tools(
            database, base_dir=self.cwd, read_only=True,
            stop_event=stop_event,
            reports_dir=os.path.join(self.cwd, "temp", "reports"))
        tools += build_rag_tools(
            database, base_dir=self.cwd, read_only=True,
            stop_event=stop_event,
            embedding_model=self.cfg.get("embedding_model"),
            embedding_cache=self.cfg.get("embedding_cache"))
        specs = build_subagent_specs(self.cfg, tools, self.cwd, stop_event=stop_event)
        sub = self.cfg.get("subagents") or {}
        system_prompt = self.cfg.get("system") or DEFAULT_SYSTEM
        if "ask_user" not in system_prompt:
            system_prompt += (
                "\n\n当用户意图不明确或存在多个合理方向时，"
                "调用 ask_user 工具给出 2-6 个简短选项让用户点击选择。"
                "回复支持 Markdown，也可以用 ![说明](图片URL或本地路径) 展示图片。"
                "当结果很长、表格很宽或需要打印时，调用 open_report 在浏览器中展示。"
                "论文/网页问题优先调用 rag_search，必须给出文件路径、URL 和页码来源。")
        self.core = AgentCore(AgentOptions(
            system_prompt=system_prompt,
            model=self.cfg.get("model", "gpt-4o-mini"),
            api_key=self.cfg.get("api_key", ""),
            base_url=self.cfg.get("base_url", "https://api.openai.com/v1"),
            temperature=float(self.cfg.get("temperature", 0.7) or 0.7),
            max_tokens=int(self.cfg.get("max_tokens", 4096) or 4096),
            max_turns=int(self.cfg.get("max_turns", 50) or 50),
            tools=tools,
            subagents=specs,
            max_subagent_depth=int(sub.get("max_depth", 2) or 2),
            permission_mode=str(self.cfg.get("permission_mode", "default")),
            can_use_tool=self._permission,
            cwd=self.cwd,
            stream=bool(self.cfg.get("stream", True)),
            on_event=self._emit_event,
            stop_event=stop_event,
            ask_user=self.ask_user_cb,
        ))
        if preserve_history and history:
            self.core.history = history[-80:]
        return self.core

    def _emit_event(self, event: AgentEvent):
        if self.on_event is not None:
            self.on_event(event)

    @property
    def remote(self) -> bool:
        return bool((self.cfg.get("api_key") or "").strip())

    @property
    def model_name(self) -> str:
        return str(self.cfg.get("model") or "")

    def reset(self):
        self.core.reset()

    def stop(self):
        self.core.stop()

    def clear_stop(self):
        """上一轮按过停止后，为新消息清掉停止标记。"""
        self.core.options.stop_event.clear()

    def tools(self) -> list:
        return self.core.registry.names()

    def run(self, text: str):
        """产出 agent_framework.AgentEvent；未配置远程模型时回退到本地应答。"""
        if self.remote:
            yield from self.core.run(text)
        else:
            time.sleep(0.35)                                   # 让「正在输入」自然一点
            reply = local_reply(text)
            event = AgentEvent("final", text=reply)
            self._emit_event(event)
            yield event

    def ask(self, text: str) -> str:
        """兼容旧接口：只返回最终文本。"""
        result = ""
        for ev in self.run(text):
            if ev.type in ("final", "error"):
                result = ev.text
        return result

    def _permission(self, name: str, tool_input: dict) -> PermissionResult:
        mode = str(self.cfg.get("permission_mode", "default"))
        if name == "ask_user":
            return PermissionResult(allow=True)
        if mode == "plan":
            return PermissionResult(allow=False, message="当前是只读规划模式，已拒绝工具调用。")
        if name in SAFE_TOOLS or mode == "bypassPermissions":
            return PermissionResult(allow=True)
        if mode == "acceptEdits" and name in EDIT_TOOLS:
            return PermissionResult(allow=True)
        if self.permission_cb is not None:
            return self.permission_cb(name, tool_input)
        return PermissionResult(allow=False, message=f"工具「{name}」未获授权。")


# --------------------------------------------------------------------------- #
# 横幅提示（状态提示 / 气泡隐藏时的新消息预览），iOS 通知横幅风格
# --------------------------------------------------------------------------- #
class Toast(QWidget):
    activated = Signal()
    PADX = 14
    PADY = 9
    ICON = 28
    ICON_GAP = 10

    def __init__(self):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.theme = THEME_DARK
        self.kind = "info"
        self.title = None
        self._lay = None
        self._tw = self._th = 0
        self._cw = self._ch = 0
        self._radius = 22.0
        self._base = QPoint()
        self._dir = 1
        self._closing = False
        self._phase = 0.0
        self._fade = QVariantAnimation(self)
        self._fade.setDuration(200)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade.valueChanged.connect(self._on_fade)
        self._fade.finished.connect(self._on_fade_done)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.dismiss)
        self._pulse = QTimer(self)
        self._pulse.setInterval(33)
        self._pulse.timeout.connect(self._tick)

    def show_message(self, text: str, anchor: QRect, kind: str = "info", title: str = None,
                     ttl: int = 3200):
        self.kind, self.title = kind, title
        self.setCursor(Qt.CursorShape.PointingHandCursor if kind == "reply"
                       else Qt.CursorShape.ArrowCursor)
        text_w = 236
        self._lay, self._tw, self._th = layout_text(text, ui_font(13), text_w,
                                                    max_lines=3 if kind == "reply" else 0)
        title_h = 16 if title else 0
        body_h = title_h + (2 if title else 0) + self._th
        self._cw = self.PADX * 2 + self.ICON + self.ICON_GAP + max(self._tw, 96 if title else 0)
        self._ch = self.PADY * 2 + max(self.ICON, body_h)
        self._radius = min(22.0, self._ch / 2)
        w, h = self._cw + 2 * PAD, self._ch + 2 * PAD

        screen = QGuiApplication.screenAt(anchor.center()) or QGuiApplication.primaryScreen()
        av = screen.availableGeometry()
        cx = clamp(anchor.center().x() - self._cw / 2, av.left() + 8, av.right() - self._cw - 8)
        cy = anchor.top() - 10 - self._ch
        self._dir = 1
        if cy < av.top() + 8:
            cy = anchor.bottom() + 10
            self._dir = -1
        self._base = QPoint(round(cx) - PAD, round(cy) - PAD)
        live = self.isVisible() and not self._closing          # 已在显示：原地更新，不闪烁
        start = float(self.windowOpacity()) if live else 0.0
        self.setGeometry(self._base.x(), self._base.y() + round((1.0 - start) * 8 * self._dir), w, h)
        self.setWindowOpacity(start)
        self.show()
        self.raise_()
        self._closing = False
        self._fade.stop()
        self._fade.setStartValue(start)
        self._fade.setEndValue(1.0)
        self._fade.start()
        if kind == "rec":
            self._pulse.start()
        else:
            self._pulse.stop()
        if ttl > 0:
            self._timer.start(ttl)
        else:
            self._timer.stop()
        self.update()

    def hide_now(self):
        """立即隐藏（打开模态对话框前使用，避免它压在对话框上面）。"""
        self._timer.stop()
        self._pulse.stop()
        self._fade.stop()
        self._closing = False
        self.hide()

    def dismiss(self):
        self._timer.stop()
        if not self.isVisible() or self._closing:
            return
        self._closing = True
        self._fade.stop()
        self._fade.setStartValue(float(self.windowOpacity()))
        self._fade.setEndValue(0.0)
        self._fade.start()

    def _on_fade(self, v):
        v = float(v)
        self.setWindowOpacity(v)
        self.move(self._base.x(), self._base.y() + round((1.0 - v) * 8 * self._dir))

    def _on_fade_done(self):
        if self._closing and self.windowOpacity() < 0.02:
            self._pulse.stop()
            self.hide()
            self._closing = False

    def _tick(self):
        self._phase += 0.033
        self.update()

    def paintEvent(self, _):
        if self._lay is None:
            return
        th = self.theme
        p = QPainter(self)
        p.setRenderHints(AA | SMOOTH | TEXT_AA)
        rect = QRectF(PAD, PAD, self._cw, self._ch)
        p.setOpacity(th.shadow)
        p.drawPixmap(QPointF(PAD - PAD, PAD - PAD),
                     soft_shadow(self._cw, self._ch, self._radius, self.devicePixelRatioF()))
        p.setOpacity(1.0)
        path = QPainterPath()
        path.addRoundedRect(rect, self._radius, self._radius)
        g = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        g.setColorAt(0, th.glass_top)
        g.setColorAt(1, th.glass_bot)
        p.fillPath(path, QBrush(g))
        p.setPen(QPen(th.hairline, 1.0))
        p.setBrush(NO_BRUSH)
        p.drawPath(path)

        ic = QPointF(rect.left() + self.PADX + self.ICON / 2, rect.top() + self.PADY + self.ICON / 2)
        if self.kind == "rec":
            pulse = 0.5 + 0.5 * math.sin(self._phase * 5)
            p.setPen(NO_PEN)
            p.setBrush(with_alpha(RED, 60 * (1 - pulse)))
            p.drawEllipse(ic, self.ICON / 2 + pulse * 2, self.ICON / 2 + pulse * 2)
            p.setBrush(RED)
            p.drawEllipse(ic, self.ICON / 2 - 2, self.ICON / 2 - 2)
            draw_glyph(p, "mic", ic, 15, QColor(255, 255, 255))
        elif self.kind == "error":
            p.setPen(NO_PEN)
            p.setBrush(ORANGE)
            p.drawEllipse(ic, self.ICON / 2 - 1, self.ICON / 2 - 1)
            p.setPen(QColor(255, 255, 255))
            p.setFont(ui_font(16, QFont.Weight.Bold))
            p.drawText(QRectF(ic.x() - 10, ic.y() - 10, 20, 20), A_CENTER, "!")
        else:
            draw_orb(p, ic, self.ICON / 2 - 1)

        tx = rect.left() + self.PADX + self.ICON + self.ICON_GAP
        body_h = (18 if self.title else 0) + self._th
        ty = rect.top() + (self._ch - body_h) / 2
        if self.title:
            p.setPen(th.label2)
            p.setFont(ui_font(12, QFont.Weight.DemiBold))
            p.drawText(QRectF(tx, ty, self._tw + 40, 16), A_LEFT_V, self.title)
            ty += 18
        p.setPen(th.label)
        self._lay.draw(p, QPointF(tx, ty))

    def mousePressEvent(self, e):
        if self.kind == "reply":
            self.activated.emit()
        self.dismiss()


# --------------------------------------------------------------------------- #
# 腾讯云一句话识别
# --------------------------------------------------------------------------- #
def _hmac256(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def tencent_sentence_recognition(wav_bytes: bytes, secret_id: str, secret_key: str,
                                 region: str = "ap-guangzhou", engine: str = "16k_zh",
                                 timeout: int = 20) -> str:
    import requests
    service, host = "asr", "asr.tencentcloudapi.com"
    action, version = "SentenceRecognition", "2019-06-14"
    payload = {"ProjectId": 0, "SubServiceType": 2, "EngSerViceType": engine,
               "SourceType": 1, "VoiceFormat": "wav",
               "Data": base64.b64encode(wav_bytes).decode(),
               "DataLen": len(wav_bytes), "WordInfo": 0}
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    ts = int(time.time())
    date = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")
    ct = "application/json; charset=utf-8"
    canonical_headers = f"content-type:{ct}\nhost:{host}\nx-tc-action:{action.lower()}\n"
    signed_headers = "content-type;host;x-tc-action"
    hashed_payload = hashlib.sha256(body.encode("utf-8")).hexdigest()
    canonical_request = f"POST\n/\n\n{canonical_headers}\n{signed_headers}\n{hashed_payload}"
    scope = f"{date}/{service}/tc3_request"
    sts = (f"TC3-HMAC-SHA256\n{ts}\n{scope}\n"
           + hashlib.sha256(canonical_request.encode("utf-8")).hexdigest())
    k_date = _hmac256(("TC3" + secret_key).encode("utf-8"), date)
    k_service = _hmac256(k_date, service)
    k_signing = _hmac256(k_service, "tc3_request")
    signature = hmac.new(k_signing, sts.encode("utf-8"), hashlib.sha256).hexdigest()
    auth = (f"TC3-HMAC-SHA256 Credential={secret_id}/{scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}")
    headers = {"Authorization": auth, "Content-Type": ct, "Host": host,
               "X-TC-Action": action, "X-TC-Timestamp": str(ts),
               "X-TC-Version": version, "X-TC-Region": region}
    data = requests.post(f"https://{host}", headers=headers, data=body.encode("utf-8"),
                         timeout=timeout).json().get("Response", {})
    if "Error" in data:
        raise RuntimeError(f"{data['Error'].get('Code')}: {data['Error'].get('Message')}")
    return data.get("Result", "")


def pcm_to_wav(pcm: bytes, rate: int, channels: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def pcm_level(pcm: bytes) -> float:
    """16-bit 小端 PCM 的粗略音量（0~1），用于驱动球的声波环。"""
    n = len(pcm) // 2
    if n <= 0:
        return 0.0
    samples = memoryview(pcm[:n * 2]).cast("h")[::4]
    if not len(samples):
        return 0.0
    rms = math.sqrt(sum(s * s for s in samples) / len(samples)) / 32768.0
    return clamp(rms * 7.0)


# --------------------------------------------------------------------------- #
# 主悬浮球
# --------------------------------------------------------------------------- #
class PermissionRequest:
    """跨线程的工具授权请求：worker 线程等待，主线程弹窗后回填。"""

    def __init__(self, name: str, tool_input: dict, mode: str):
        self.name = name
        self.input = tool_input
        self.mode = mode
        self.event = threading.Event()
        self.result = PermissionResult(allow=False, message="已拒绝。")

    def summary(self) -> str:
        try:
            return json.dumps(self.input, ensure_ascii=False)[:300]
        except (TypeError, ValueError):
            return str(self.input)[:300]


class ChoiceRequest:
    """跨线程的选择请求：worker 等待，主线程渲染按钮气泡后回填。"""

    def __init__(self, question: str, options: list[str]):
        self.id = uuid.uuid4().hex[:10]
        self.question = question
        self.options = list(options)
        self.event = threading.Event()
        self.result = ""


class FloatingBall(QWidget):
    asr_result = Signal(bool, str)
    agent_reply = Signal(str)
    agent_event = Signal(object)
    permission_asked = Signal(object)
    choice_asked = Signal(object)

    def __init__(self):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow)
        self.setWindowTitle("AI 助手")
        self.setMouseTracking(True)
        self.settings = QSettings()

        # —— 界面偏好 ——
        self._prefs = self._load_prefs()
        self._allowed_tools = self._load_allowed_tools()
        self._chat_visible = self.settings.value("chat_visible", True, type=bool)
        self._chat_w = self._read_size_setting("chat_w", PANEL_W, MIN_PANEL_W, MAX_PANEL_W)
        self._chat_h = self._read_size_setting("chat_h", CHAT_H, MIN_CHAT_H, MAX_CHAT_H)
        self._appearance = self._prefs["appearance"]
        self._idle_dim_enabled = self._prefs["idle_dim"]
        self._notify_hidden = self._prefs["notify_hidden"]
        self._voice_enabled = self._prefs["voice_enabled"]
        self._auto_show_chat = self._prefs["auto_show_chat"]
        self._temp_store = ChatTempStore(os.path.join(PROJECT_ROOT, "temp"))
        self._attachments: list[str] = []
        self._library_worker = None
        self._library_progress = None

        # —— 状态 ——
        self._expanded = False
        self._left = True               # 展开方向：球在右侧 → 向左展开
        self._up = True                 # 消息面板在输入行的上方
        self._mode = "collapsed"        # collapsed | row | full
        self._exp_t = 0.0               # 展开进度 0~1
        self._chat_t = 1.0 if self._chat_visible else 0.0
        self._sending = False
        self._recording = False
        self._asr_busy = False
        self._audio_source = None
        self._audio_io = None
        self._audio_buf = None
        self._rec_fmt = None
        self._asr_cfg = load_asr_config()
        self.agent = Agent(permission_cb=self._request_permission,
                           on_event=self._on_agent_event_cb,
                           ask_user_cb=self._request_choice)
        self._stream_buf = ""
        self._stream_active = False
        self._had_error = False
        self._clear_after_reply = False
        self._stop_requested = False
        self._settings_open = False
        self._permission_open = False
        self._pending_choices: dict[str, ChoiceRequest] = {}

        # —— 球的动效参数 ——
        self._phase = 0.8
        self._energy = 0.0
        self._rec_k = 0.0
        self._level = 0.0
        self._hover_ball = False
        self._hover_k = 0.0
        self._press_k = 0.0
        self._pressed = False
        self._focus_k = 0.0
        self._dim = 0.0
        self._idle_since = time.monotonic()
        self._last_tick = time.monotonic()
        self._base_opacity = clamp(float(self._prefs.get("opacity", 1.0)), 0.35, 1.0)
        self._applied_opacity = -1.0

        # —— 拖动 ——
        self._press_active = False
        self._press_from_ball = False
        self._dragging = False
        self._press_global = QPoint()
        self._press_win = QPoint()
        self._last_toggle = 0.0
        self._resizing_chat = False
        self._resize_press = QPoint()
        self._resize_w0 = self._chat_w
        self._resize_h0 = self._chat_h
        self._resize_left = self._left
        self._resize_up = self._up

        self.theme = THEME_DARK
        self.toast = Toast()
        self.toast.activated.connect(self.reveal_chat)

        self._build_widgets()
        self._build_animations()
        self._restore_position()
        self._chat_w, self._chat_h = self._clamp_chat_size(self._chat_w, self._chat_h)
        self._geo = self._compute_geo("collapsed")
        self._set_window_mode("collapsed")
        self._build_menu_and_tray()
        self._apply_theme()
        self._update_mic_state()
        self._update_send_state()

        self.asr_result.connect(self._on_asr_result)
        self.agent_reply.connect(self._on_agent_reply)
        self.agent_event.connect(self._on_agent_event)
        self.permission_asked.connect(self._on_permission_asked)
        self.choice_asked.connect(self._on_choice_asked)
        self._rec_timer = QTimer(self)
        self._rec_timer.setSingleShot(True)
        self._rec_timer.timeout.connect(self._stop_record)
        self._stream_timer = QTimer(self)
        self._stream_timer.setInterval(50)
        self._stream_timer.timeout.connect(self._flush_stream)
        sc = QShortcut(QKeySequence("Ctrl+Shift+H"), self)
        sc.setContext(Qt.ShortcutContext.ApplicationShortcut)
        sc.activated.connect(self.toggle_chat)
        sc2 = QShortcut(QKeySequence("Ctrl+,"), self)
        sc2.setContext(Qt.ShortcutContext.ApplicationShortcut)
        sc2.activated.connect(self._open_settings)
        try:
            QGuiApplication.styleHints().colorSchemeChanged.connect(lambda *_: self._apply_theme())
        except Exception:                                              # noqa: BLE001
            pass
        QApplication.instance().aboutToQuit.connect(self._save_state)

        self._refresh_status()

        self._tick = QTimer(self)
        self._tick.setInterval(33)
        self._tick.timeout.connect(self._on_tick)
        self._tick.start()

        self._daily_timer = QTimer(self)
        self._daily_timer.setInterval(60_000)
        self._daily_timer.timeout.connect(self._check_daily_library)
        self._daily_timer.start()

    # ------------------------------------------------------------------ #
    # 构建
    # ------------------------------------------------------------------ #
    def _read_size_setting(self, key: str, default: int, low: int, high: int) -> int:
        try:
            value = int(float(self.settings.value(key, default)))
        except (TypeError, ValueError):
            value = default
        return max(low, min(high, value))

    def _load_prefs(self) -> dict:
        raw = {}
        for key, default in DEFAULT_PREFS.items():
            value = self.settings.value(key, default)
            if isinstance(value, str) and key != "appearance":
                value = value.strip()
            raw[key] = value
        return normalize_prefs(raw)

    def _save_prefs(self):
        for key, value in self._prefs.items():
            self.settings.setValue(key, value)

    def _load_allowed_tools(self) -> set[str]:
        raw = self.settings.value("allowed_tools", [])
        if isinstance(raw, str):
            raw = [x for x in raw.replace("，", ",").split(",") if x.strip()]
        elif raw is None:
            raw = []
        return {str(x) for x in raw}

    def _save_allowed_tools(self):
        self.settings.setValue("allowed_tools", sorted(self._allowed_tools))

    def _build_widgets(self):
        self.chat = ChatCard(self)
        self.chat.hide()
        self.chat.hide_clicked.connect(lambda: self.set_chat_visible(False))
        self.chat.clear_clicked.connect(self._clear_chat)
        self.chat.drag_event.connect(self._on_card_drag)
        self.chat.resize_event.connect(self._on_chat_resize)
        self.chat.copied.connect(self._on_copied)
        self.chat.settings_clicked.connect(self._open_settings)
        self.chat.suggestion_clicked.connect(self._use_suggestion)
        self.chat.choice_selected.connect(self._on_choice_selected)
        self.chat.database_add_files.connect(self._choose_library_files)

        self.row = QWidget(self)        # 装输入栏里所有控件，便于整体淡入淡出
        self.row.hide()
        self._row_fx = QGraphicsOpacityEffect(self.row)
        self._row_fx.setOpacity(0.0)
        self.row.setGraphicsEffect(self._row_fx)

        self.input = QLineEdit(self.row)
        self.input.setFrame(False)
        self.input.setFont(ui_font(14))
        self.input.setPlaceholderText("输入消息…")
        self.input.returnPressed.connect(self._on_send)
        self.input.textChanged.connect(self._update_send_state)
        self.input.installEventFilter(self)
        self.btn_clear = IconButton("clear", 20, self.row)
        self.btn_clear.clicked.connect(self._clear_composer)
        self.btn_mic = IconButton("mic", 32, self.row)
        self.btn_mic.setToolTip("语音输入（腾讯云一句话识别）")
        self.btn_mic.clicked.connect(self._toggle_record)
        self.btn_attach = IconButton("plus", 26, self.row)
        self.btn_attach.setToolTip("添加附件 / 截图 / 粘贴图片")
        self.btn_attach.clicked.connect(self._show_attach_menu)
        self.btn_send = IconButton("send", 34, self.row)
        self.btn_send.setToolTip("发送  (Enter)")
        self.btn_send.clicked.connect(self._on_send)
        self.btn_library = IconButton("sparkles", 26, self)
        self.btn_library.setToolTip("添加文件到知识库")
        self.btn_library.clicked.connect(self._choose_library_files)
        self._themed = [self.btn_clear, self.btn_mic, self.btn_attach, self.btn_send,
                        self.btn_library, self.chat, self.toast]

    def _build_animations(self):
        self._exp_anim = QVariantAnimation(self)
        self._exp_anim.valueChanged.connect(self._on_exp)
        self._exp_anim.finished.connect(self._on_exp_done)
        self._chat_anim = QVariantAnimation(self)
        self._chat_anim.valueChanged.connect(self._on_chat)
        self._chat_anim.finished.connect(self._on_chat_done)
        self._snap_anim = QVariantAnimation(self)
        self._snap_anim.setDuration(200)
        self._snap_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._snap_anim.valueChanged.connect(self._on_snap_value)
        self._snap_anim.finished.connect(self._save_state)

    def _build_menu_and_tray(self):
        self.menu = QMenu()
        style_menu(self.menu, THEME_DARK, first_time=True)
        self._appearance_menu = QMenu("外观")
        style_menu(self._appearance_menu, THEME_DARK, first_time=True)
        self.menu.aboutToShow.connect(self._populate_menu)

        self.tray = QSystemTrayIcon(QIcon(make_ball_pixmap(64)), self)
        self.tray.setToolTip("AI 助手")
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._on_tray_activated)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()

    def _populate_menu(self):
        m = self.menu
        th = self.theme
        m.clear()
        m.addAction("收起悬浮球" if self._expanded else "展开对话", self.toggle_panel)
        m.addAction("隐藏消息气泡" if self._chat_visible else "显示消息气泡", self.toggle_chat)
        m.addAction("在浏览器打开当前对话", self._open_chat_in_browser)
        m.addAction("添加文件到知识库…", self._choose_library_files)
        m.addAction("立即更新 RAG 与知识库", self._manual_library_update)
        m.addAction("清空会话", self._clear_chat)
        m.addSeparator()
        m.addAction(make_icon("gear", 16, th.label2), "设置…", self._open_settings)
        sub = self._appearance_menu
        sub.clear()
        for key, label in (("auto", "跟随系统"), ("light", "浅色"), ("dark", "深色")):
            mark = "●  " if self._appearance == key else "     "
            sub.addAction(mark + label, lambda k=key: self._set_appearance(k))
        m.addMenu(sub)
        m.addAction("恢复消息窗大小", self._reset_chat_size)
        if self._allowed_tools:
            m.addAction("清除已记住的工具授权", self._forget_tools)
        m.addSeparator()
        m.addAction("退出", QApplication.quit)

    # ------------------------------------------------------------------ #
    # 主题
    # ------------------------------------------------------------------ #
    def _resolve_dark(self) -> bool:
        if self._appearance == "dark":
            return True
        if self._appearance == "light":
            return False
        try:
            cs = QGuiApplication.styleHints().colorScheme()
            return cs != Qt.ColorScheme.Light
        except Exception:                                              # noqa: BLE001
            return True

    def _set_appearance(self, key: str):
        self._appearance = key
        self._prefs["appearance"] = key
        self.settings.setValue("appearance", key)
        self._apply_theme()

    # ------------------------------------------------------------------ #
    # 设置
    # ------------------------------------------------------------------ #
    def _open_settings(self):
        if self._settings_open or self._permission_open:
            return
        if self._sending:
            self._toast("请先停止当前回复，再修改设置。", self._content_global(),
                                    kind="info", ttl=2200)
            return
        self.toast.hide_now()
        if not self.isVisible():
            # Qt.Tool 子窗口在父窗口隐藏时也不会显示，从托盘打开设置时先把球叫回来。
            self.show()
            self.raise_()
        dlg = SettingsDialog(self, self.agent.cfg, self._asr_cfg, self._prefs,
                             dark=self.theme.dark)
        accepted = False
        self._settings_open = True
        try:
            accepted = dlg.exec() == QDialog.DialogCode.Accepted and bool(dlg.result_data)
        finally:
            self._settings_open = False
        if not accepted:
            return
        data = dlg.result_data
        try:
            save_agent_config(data["agent"])
            save_asr_config(data["asr"])
        except OSError as exc:
            self._toast(f"保存失败：{exc}", self._content_global(),
                                    kind="error", ttl=5000)
            return

        self._prefs = normalize_prefs(data["prefs"])
        self._save_prefs()
        self._asr_cfg = load_asr_config()
        self._appearance = self._prefs["appearance"]
        self._base_opacity = clamp(float(self._prefs["opacity"]), 0.35, 1.0)
        self._idle_dim_enabled = bool(self._prefs["idle_dim"])
        self._notify_hidden = bool(self._prefs["notify_hidden"])
        self._voice_enabled = bool(self._prefs["voice_enabled"])
        self._auto_show_chat = bool(self._prefs["auto_show_chat"])

        self.agent.reload(preserve_history=True)
        self._apply_theme()
        self._update_mic_state()
        if data.get("reset_position"):
            self._reset_ball_position()
        self._refresh_status()
        self._update_send_state()
        self._toast("设置已保存", self._content_global(), kind="info", ttl=1800)

    def _forget_tools(self):
        self._allowed_tools.clear()
        self._save_allowed_tools()
        self._toast("已清除记住的工具授权", self._content_global(),
                                kind="info", ttl=2000)

    def _reset_ball_position(self):
        self._exp_anim.stop()
        self._snap_anim.stop()
        self._expanded = False
        self._exp_t = 0.0
        self._chat_t = 1.0 if self._chat_visible else 0.0
        avail = QGuiApplication.primaryScreen().availableGeometry()
        self.ball_center = QPointF(avail.right() - EDGE - BALL / 2, avail.center().y())
        self._set_window_mode("collapsed")
        self._save_state()
        self.show()
        self.raise_()

    def _update_mic_state(self):
        if not hasattr(self, "btn_mic"):
            return
        self.btn_mic.setEnabled(self._voice_enabled)
        self.btn_mic.setToolTip("语音输入（腾讯云一句话识别）" if self._voice_enabled
                                else "语音输入已在设置中关闭")

    def _apply_theme(self):
        self.theme = th = THEME_DARK if self._resolve_dark() else THEME_LIGHT
        self.input.setStyleSheet(
            f"QLineEdit {{ background: transparent; border: none; color: {css(th.label)};"
            f" selection-background-color: {css(th.accent)}; selection-color: #FFFFFF; }}")
        pal = self.input.palette()
        pal.setColor(QPalette.ColorRole.PlaceholderText, th.label3)
        self.input.setPalette(pal)
        style_menu(self.menu, th)
        style_menu(self._appearance_menu, th)
        for w in self._themed:
            w.theme = th
            w.update()
        self.chat.refresh_theme()
        self.tray.setToolTip("AI 助手 · " + self._idle_detail())
        self.update()

    # ------------------------------------------------------------------ #
    # 几何：窗口 = 球 / 球+输入行 / 球+输入行+消息面板，四周各留 PAD 透明边
    # ------------------------------------------------------------------ #
    def _compute_geo(self, mode: str):
        expanded = mode != "collapsed"
        chat = mode == "full"
        left, up = self._left, self._up
        panel_w, panel_h = int(self._chat_w), int(self._chat_h)
        wc = panel_w if expanded else BALL
        hc = ROW_H + ((GAP + panel_h) if chat else 0)
        row_y = (PAD + hc - ROW_H) if up else PAD
        ball_x = PAD + (wc - BALL if (left and expanded) else 0)
        g = SimpleNamespace(mode=mode, expanded=expanded, left=left, up=up, row_y=row_y,
                            win_w=wc + 2 * PAD, win_h=hc + 2 * PAD,
                            ball=QRectF(ball_x, row_y, BALL, BALL),
                            pill=None, chat=None)
        if expanded:
            py = row_y + (ROW_H - PILL_H) / 2
            if left:
                px = PAD
                pw = ball_x - ROW_GAP_A - PAD
            else:
                px = ball_x + BALL + ROW_GAP_A
                pw = PAD + wc - px
            g.pill = QRectF(px, py, pw, PILL_H)
        if chat:
            cy = PAD if up else row_y + ROW_H + GAP
            g.chat = QRectF(PAD, cy, panel_w, panel_h)
        return g

    def _set_window_mode(self, mode: str):
        self._mode = mode
        self._geo = g = self._compute_geo(mode)
        pos = self.ball_center - g.ball.center()
        self.setGeometry(round(pos.x()), round(pos.y()), g.win_w, g.win_h)
        self._layout_children()

    def _layout_children(self):
        g = self._geo
        if g.expanded:
            cr = QRect(g.pill.toRect())
            cr.setTop(int(g.row_y))
            cr.setHeight(ROW_H)
            self.row.setGeometry(cr)
            ox, oy = cr.x(), cr.y()
            pl = g.pill.x() - ox
            pt = g.pill.y() - oy
            pw, ph = g.pill.width(), g.pill.height()
            sx = pl + pw - 5 - 34
            self.btn_send.move(round(sx), round(pt + (ph - 34) / 2))
            ax = sx - 2 - 26
            self.btn_attach.move(round(ax), round(pt + (ph - 26) / 2))
            mx = ax - 2 - 32
            self.btn_mic.move(round(mx), round(pt + (ph - 32) / 2))
            cx = mx - 2 - 20
            self.btn_clear.move(round(cx), round(pt + (ph - 20) / 2))
            ix = pl + 16
            self.input.setGeometry(round(ix), round(pt + (ph - 30) / 2),
                                   max(20, round(cx - 4 - ix)), 30)
        if g.chat is not None:
            corner = ("t" if g.up else "b") + ("l" if g.left else "r")
            self.chat.set_resize_corner(corner)
            self.chat.setGeometry(g.chat.toRect())
        ball = g.ball
        library_x = ball.right() + 2 if g.left else ball.left() - 28
        self.btn_library.move(round(library_x), round(ball.bottom() - 28))
        self._sync_children()

    def _chat_pres(self) -> float:
        return clamp(self._chat_t) * clamp((self._exp_t - 0.12) / 0.88)

    def _card_xform(self, pres: float):
        g = self._geo
        s = 0.90 + 0.10 * pres
        cx = clamp(g.ball.center().x(), g.chat.left() + 40, g.chat.right() - 40)
        oy = g.chat.bottom() if g.up else g.chat.top()
        return s, QPointF(cx, oy)

    def _sync_children(self):
        """依据当前动画进度同步子控件的可见性 / 透明度。"""
        g = self._geo
        op = clamp((self._exp_t - 0.80) / 0.20)       # 输入栏背景长满之后，里面的控件才淡入
        show_row = g.expanded and op > 0.01
        self.row.setVisible(show_row)
        self._row_fx.setOpacity(op)
        self._row_fx.setEnabled(op < 0.999)
        pres = self._chat_pres()
        if g.chat is not None and pres > 0.01:
            s, org = self._card_xform(pres)
            self.chat.origin = QPointF(org.x() - g.chat.x(), org.y() - g.chat.y())
            self.chat.presence = pres
            self.chat.show()
            self.chat.update()
        else:
            self.chat.hide()

    def _content_global(self) -> QRect:
        g = self._geo
        r = QRectF(g.ball)
        if g.expanded:
            r = r.united(g.pill)
        if g.chat is not None:
            r = r.united(g.chat)
        return r.translated(QPointF(self.pos())).toRect()

    def _sync_anchor(self):
        self.ball_center = QPointF(self.pos()) + self._geo.ball.center()

    def _screen_avail(self, pt: QPoint):
        screen = QGuiApplication.screenAt(pt) or QGuiApplication.primaryScreen()
        return screen.availableGeometry()

    def _restore_position(self):
        avail = QGuiApplication.primaryScreen().availableGeometry()
        default = QPointF(avail.right() - EDGE - BALL / 2, avail.center().y())
        try:
            x = float(self.settings.value("ball_x"))
            y = float(self.settings.value("ball_y"))
            self.ball_center = QPointF(x, y)
        except (TypeError, ValueError):
            self.ball_center = default
        if QGuiApplication.screenAt(self.ball_center.toPoint()) is None:
            self.ball_center = default

    def _save_state(self):
        self.settings.setValue("ball_x", self.ball_center.x())
        self.settings.setValue("ball_y", self.ball_center.y())
        self.settings.setValue("chat_w", int(self._chat_w))
        self.settings.setValue("chat_h", int(self._chat_h))
        self._prefs["opacity"] = self._base_opacity
        self._save_prefs()
        self._save_allowed_tools()

    def _clamp_chat_size(self, width: float, height: float) -> tuple[int, int]:
        """限制消息面板尺寸，避免缩放后超出当前屏幕。"""
        avail = self._screen_avail(self.ball_center.toPoint())
        margin = 12
        room_w = (self.ball_center.x() - avail.left() - BALL / 2 - margin
                  if self._left else
                  avail.right() - self.ball_center.x() + BALL / 2 - margin)
        room_h = (self.ball_center.y() - avail.top() - GAP - BALL / 2 - margin
                  if self._up else
                  avail.bottom() - self.ball_center.y() - GAP - BALL / 2 - margin)
        max_w = max(MIN_PANEL_W, min(MAX_PANEL_W,
                                     avail.width() - 2 * PAD - margin,
                                     int(room_w)))
        max_h = max(MIN_CHAT_H, min(MAX_CHAT_H,
                                     avail.height() - 2 * PAD - ROW_H - GAP - margin,
                                     int(room_h)))
        return (max(MIN_PANEL_W, min(max_w, int(round(width)))),
                max(MIN_CHAT_H, min(max_h, int(round(height)))))

    def _on_chat_resize(self, action: str, global_pos: QPoint):
        """ChatCard 外角手柄的拖动：保持球位置不变，扩展消息面板。"""
        if action == "press":
            self._resizing_chat = True
            self._resize_press = QPoint(global_pos)
            self._resize_w0 = int(self._chat_w)
            self._resize_h0 = int(self._chat_h)
            self._resize_left = self._left
            self._resize_up = self._up
            self._exp_anim.stop()
            self._snap_anim.stop()
            return
        if action == "release":
            if self._resizing_chat:
                self._resizing_chat = False
                self.settings.setValue("chat_w", int(self._chat_w))
                self.settings.setValue("chat_h", int(self._chat_h))
            return
        if not self._resizing_chat:
            return
        dx = global_pos.x() - self._resize_press.x()
        dy = global_pos.y() - self._resize_press.y()
        width = self._resize_w0 + (-dx if self._resize_left else dx)
        height = self._resize_h0 + (-dy if self._resize_up else dy)
        width, height = self._clamp_chat_size(width, height)
        if width == int(self._chat_w) and height == int(self._chat_h):
            return
        self._chat_w, self._chat_h = width, height
        if self._mode == "full":
            self._set_window_mode("full")
        self.settings.setValue("chat_w", width)
        self.settings.setValue("chat_h", height)

    def _reset_chat_size(self):
        self._chat_w, self._chat_h = PANEL_W, CHAT_H
        self.settings.setValue("chat_w", PANEL_W)
        self.settings.setValue("chat_h", CHAT_H)
        if self._mode == "full":
            self._set_window_mode("full")

    def _decide_orientation(self):
        avail = self._screen_avail(self.ball_center.toPoint())
        self._left = self.ball_center.x() > avail.center().x()
        need = self._chat_h + GAP + BALL / 2 + EDGE
        above = self.ball_center.y() - avail.top()
        below = avail.bottom() - self.ball_center.y()
        if above >= need:
            self._up = True
        elif below >= need:
            self._up = False
        else:
            self._up = above >= below

    def _clamp_into_screen(self, margin: int = 8):
        vis = self._content_global()
        av = self._screen_avail(vis.center())
        dx = dy = 0
        if vis.left() < av.left() + margin:
            dx = av.left() + margin - vis.left()
        elif vis.right() > av.right() - margin:
            dx = av.right() - margin - vis.right()
        if vis.top() < av.top() + margin:
            dy = av.top() + margin - vis.top()
        elif vis.bottom() > av.bottom() - margin:
            dy = av.bottom() - margin - vis.bottom()
        if dx or dy:
            self.move(self.x() + dx, self.y() + dy)
            self._sync_anchor()

    # ------------------------------------------------------------------ #
    # 绘制
    # ------------------------------------------------------------------ #
    def _glass(self, p: QPainter, rect: QRectF, radius: float, focus: float = 0.0):
        th = self.theme
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        g = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        g.setColorAt(0, th.glass_top)
        g.setColorAt(1, th.glass_bot)
        p.fillPath(path, QBrush(g))
        edge = mix(th.hairline, with_alpha(th.accent, 200), focus)
        p.setPen(QPen(edge, 1.0 + 0.4 * focus))
        p.setBrush(NO_BRUSH)
        p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHints(AA | SMOOTH | TEXT_AA)
        g, th = self._geo, self.theme
        dpr = self.devicePixelRatioF()
        et = self._exp_t

        if g.expanded and et > 0.003:
            pres = self._chat_pres()
            if g.chat is not None and pres > 0.003:           # 消息面板的阴影（面板本体由 ChatCard 绘制）
                s, org = self._card_xform(pres)
                p.save()
                p.setOpacity(th.shadow * clamp(pres * 1.4))
                p.translate(org)
                p.scale(s, s)
                p.translate(-org.x(), -org.y())
                p.drawPixmap(QPointF(g.chat.x() - PAD, g.chat.y() - PAD),
                             soft_shadow(int(self._chat_w), int(self._chat_h), R_CARD, dpr))
                p.restore()

            a = clamp(et / 0.7)                               # 输入栏：从球的方向「长」出来
            full = g.pill
            frac = 0.30 + 0.70 * clamp(et / 0.85)
            ax = full.right() if g.left else full.x()
            p.save()
            p.setOpacity(th.shadow * a)
            p.translate(ax, 0)
            p.scale(frac, 1.0)
            p.translate(-ax, 0)
            p.drawPixmap(QPointF(full.x() - PAD, full.y() - PAD),
                         soft_shadow(full.width(), full.height(), PILL_H / 2, dpr))
            p.restore()
            rect = QRectF(full.right() - full.width() * frac, full.y(), full.width() * frac,
                          full.height()) if g.left else \
                QRectF(full.x(), full.y(), full.width() * frac, full.height())
            p.setOpacity(a)
            self._glass(p, rect, PILL_H / 2, self._focus_k)
            p.setOpacity(1.0)

        # —— 悬浮球：呼吸 + 外发光 + 玻璃环 + 在线状态点 ——
        c = g.ball.center()
        calm = 1.0 - max(self._energy, self._rec_k)
        breath = 1.0 + 0.008 * math.sin(self._phase * 1.15) * calm
        r = BALL / 2 * (breath + 0.05 * self._hover_k - 0.08 * self._press_k)

        p.setOpacity(th.shadow * 0.9)
        p.drawPixmap(QPointF(g.ball.x() - PAD, g.ball.y() - PAD),
                     soft_shadow(BALL, BALL, BALL / 2, dpr))
        p.setOpacity(1.0)

        glow = max(self._energy, self._hover_k * 0.45, self._rec_k)
        accent = mix(th.accent, RED, self._rec_k)
        glow_r = r + 8.0 + 5.0 * glow
        aura = QRadialGradient(c, glow_r)
        aura.setColorAt(0.50, with_alpha(accent, 10 + 34 * glow))
        aura.setColorAt(0.74, with_alpha(accent, 4 + 20 * glow))
        aura.setColorAt(1.00, with_alpha(accent, 0))
        p.setPen(NO_PEN)
        p.setBrush(QBrush(aura))
        p.drawEllipse(c, glow_r, glow_r)

        p.setBrush(NO_BRUSH)
        ring = QLinearGradient(c.x(), c.y() - r, c.x(), c.y() + r)
        ring.setColorAt(0.0, with_alpha(QColor(255, 255, 255), 165))
        ring.setColorAt(0.48, with_alpha(accent, 78))
        ring.setColorAt(1.0, with_alpha(accent, 138))
        p.setPen(QPen(QBrush(ring), 1.15))
        p.drawEllipse(c, r + 3.2, r + 3.2)

        draw_orb(p, c, r, self._phase, self._energy, self._rec_k, self._level)

        if self._recording:
            dot, busy = RED, True
        elif self._sending:
            dot, busy = ORANGE, True
        elif self.agent.remote:
            dot, busy = GREEN, False
        else:
            dot, busy = th.label3, False
        dot_c = c + QPointF(r * 0.68, r * 0.68)
        if busy:
            pulse = 0.5 + 0.5 * math.sin(self._phase * 5.0)
            p.setPen(NO_PEN)
            p.setBrush(with_alpha(dot, 26 + 30 * (1.0 - pulse)))
            p.drawEllipse(dot_c, 7.5 + 2.5 * pulse, 7.5 + 2.5 * pulse)
        p.setPen(QPen(with_alpha(th.card_bot, 236), 2.0))
        p.setBrush(dot)
        p.drawEllipse(dot_c, 4.4, 4.4)
        p.setPen(NO_PEN)
        p.setBrush(QColor(255, 255, 255, 135))
        p.drawEllipse(dot_c + QPointF(-1.1, -1.2), 1.35, 1.35)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if hasattr(self, "_geo"):
            self._layout_children()

    # ------------------------------------------------------------------ #
    # 每帧：球的呼吸 / 悬停 / 按压 / 聚焦 / 空闲淡出
    # ------------------------------------------------------------------ #
    def _touch(self):
        self._idle_since = time.monotonic()

    def _on_tick(self):
        if not self.isVisible():
            return
        now = time.monotonic()
        dt = clamp(now - self._last_tick, 0.0, 0.1)
        self._last_tick = now
        tgt = 1.0 if self._recording else (0.6 if self._sending else 0.0)
        self._energy += (tgt - self._energy) * min(1.0, dt * 5)
        self._rec_k += ((1.0 if self._recording else 0.0) - self._rec_k) * min(1.0, dt * 9)
        self._phase += dt * (0.30 + 1.6 * self._energy)
        self._level = max(0.0, self._level - dt * 2.5)
        self._hover_k += ((1.0 if self._hover_ball else 0.0) - self._hover_k) * min(1.0, dt * 14)
        self._press_k += ((1.0 if self._pressed else 0.0) - self._press_k) * min(1.0, dt * 22)

        old_focus = self._focus_k
        want_focus = 1.0 if (self._expanded and self.input.hasFocus()) else 0.0
        self._focus_k += (want_focus - self._focus_k) * min(1.0, dt * 12)

        idle = (self._idle_dim_enabled and not self._expanded and not self._hover_ball
                and not self._recording and not self._sending
                and now - self._idle_since > 3.0)
        self._dim += ((1.0 if idle else 0.0) - self._dim) * min(1.0, dt * 3)
        op = self._base_opacity * (1.0 - 0.45 * self._dim)
        if abs(op - self._applied_opacity) > 0.01:
            self._applied_opacity = op
            self.setWindowOpacity(op)

        g = self._geo
        self.update(g.ball.adjusted(-24, -24, 24, 24).toRect())
        if g.pill is not None and abs(self._focus_k - old_focus) > 0.004:
            self.update(g.pill.adjusted(-3, -3, 3, 3).toRect())

    # ------------------------------------------------------------------ #
    # 鼠标：球
    # ------------------------------------------------------------------ #
    def _hit_ball(self, pos) -> bool:
        c = self._geo.ball.center()
        return math.hypot(pos.x() - c.x(), pos.y() - c.y()) <= BALL / 2 + 4

    def mousePressEvent(self, e):
        pos = e.position()
        self._touch()
        if e.button() == Qt.MouseButton.LeftButton and self._hit_ball(pos):
            self._drag_begin(e.globalPosition().toPoint(), from_ball=True)
            e.accept()
        elif e.button() == Qt.MouseButton.RightButton and self._hit_ball(pos):
            self.menu.popup(e.globalPosition().toPoint())
            e.accept()
        elif self._expanded and self._geo.pill is not None and self._geo.pill.contains(pos):
            self.input.setFocus()
            e.accept()
        else:
            super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        self._touch()
        if self._press_active:
            self._drag_move(e.globalPosition().toPoint())
            e.accept()
            return
        hit = self._hit_ball(e.position())
        self._hover_ball = hit
        self.setCursor(Qt.CursorShape.OpenHandCursor if hit else Qt.CursorShape.ArrowCursor)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._press_active and e.button() == Qt.MouseButton.LeftButton:
            self._drag_end()
            self.setCursor(Qt.CursorShape.OpenHandCursor if self._hit_ball(e.position())
                           else Qt.CursorShape.ArrowCursor)
            e.accept()
            return
        super().mouseReleaseEvent(e)

    def enterEvent(self, e):
        self._touch()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover_ball = False
        super().leaveEvent(e)

    def wheelEvent(self, e):
        if not self._hit_ball(e.position()):
            return
        self._touch()
        step = 0.05 * (1 if e.angleDelta().y() > 0 else -1)
        self._base_opacity = clamp(self._base_opacity + step, 0.35, 1.0)
        self._prefs["opacity"] = self._base_opacity
        self.settings.setValue("opacity", self._base_opacity)
        e.accept()

    # ---- 拖动（球和消息面板标题栏共用）---------------------------------- #
    def _on_card_drag(self, kind: str, gpos: QPoint):
        if kind == "press":
            self._drag_begin(gpos, from_ball=False)
        elif kind == "move":
            self._drag_move(gpos)
        else:
            self._drag_end()

    def _drag_begin(self, gpos: QPoint, from_ball: bool):
        self._snap_anim.stop()
        self._press_active = True
        self._press_from_ball = from_ball
        self._pressed = from_ball
        self._dragging = False
        self._press_global = gpos
        self._press_win = self.pos()
        if from_ball:
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def _drag_move(self, gpos: QPoint):
        delta = gpos - self._press_global
        if not self._dragging and delta.manhattanLength() > 5:
            self._dragging = True
        if self._dragging:
            self.move(self._press_win + delta)
            self._sync_anchor()

    def _drag_end(self):
        self._press_active = False
        self._pressed = False
        if self._dragging:
            self._dragging = False
            self._finish_drag()
        elif self._press_from_ball:
            now = time.monotonic()
            if now - self._last_toggle >= 0.22:
                self._last_toggle = now
                self.toggle_panel()

    def _finish_drag(self):
        vis = self._content_global()
        av = self._screen_avail(vis.center())
        m = EDGE
        dx = dy = 0
        if vis.left() < av.left() + m + SNAP:
            dx = av.left() + m - vis.left()
        elif vis.right() > av.right() - m - SNAP:
            dx = av.right() - m - vis.right()
        if vis.top() < av.top() + m + SNAP:
            dy = av.top() + m - vis.top()
        elif vis.bottom() > av.bottom() - m - SNAP:
            dy = av.bottom() - m - vis.bottom()
        self._snap_anim.stop()
        self._snap_anim.setStartValue(self.pos())
        self._snap_anim.setEndValue(QPoint(self.x() + dx, self.y() + dy))
        self._snap_anim.start()
        self._save_state()

    def _on_snap_value(self, v):
        self.move(v)
        self._sync_anchor()

    # ------------------------------------------------------------------ #
    # 展开 / 收起 / 隐藏气泡
    # ------------------------------------------------------------------ #
    @staticmethod
    def _run(anim: QVariantAnimation, a: float, b: float, ms: int, curve):
        anim.stop()
        anim.setStartValue(float(a))
        anim.setEndValue(float(b))
        anim.setDuration(ms)
        anim.setEasingCurve(curve)
        anim.start()

    def toggle_panel(self):
        self.set_expanded(not self._expanded)

    def set_expanded(self, on: bool, focus: bool = True):
        if on == self._expanded:
            return
        self._expanded = on
        self._touch()
        if on:
            self.toast.dismiss()
            self._decide_orientation()
            self._chat_w, self._chat_h = self._clamp_chat_size(self._chat_w, self._chat_h)
            self._chat_anim.stop()
            self._chat_t = 1.0 if self._chat_visible else 0.0
            self._set_window_mode("full" if self._chat_visible else "row")
            self._clamp_into_screen()
            self._run(self._exp_anim, self._exp_t, 1.0, 340, QEasingCurve.Type.OutCubic)
            self.show()
            self.raise_()
            if focus:
                self.activateWindow()
                self.input.setFocus()
        else:
            self.input.clearFocus()
            self._run(self._exp_anim, self._exp_t, 0.0, 240, QEasingCurve.Type.InOutCubic)

    def _on_exp(self, v):
        self._exp_t = float(v)
        self._sync_children()
        self.update()

    def _on_exp_done(self):
        if not self._expanded and self._exp_t <= 0.001:
            self._set_window_mode("collapsed")
            self._save_state()
            self.update()

    def toggle_chat(self):
        self.set_chat_visible(not self._chat_visible)

    def reveal_chat(self):
        """点击横幅 / 托盘：展开并显示消息气泡。"""
        if not self._expanded:
            self.set_expanded(True)
        if not self._chat_visible:
            self.set_chat_visible(True)

    def set_chat_visible(self, vis: bool):
        self._chat_visible = bool(vis)
        self.settings.setValue("chat_visible", self._chat_visible)
        if not self._expanded:
            self._chat_t = 1.0 if vis else 0.0
            return
        if vis:
            self.toast.dismiss()
            self._set_window_mode("full")
            self._clamp_into_screen()
            self._run(self._chat_anim, self._chat_t, 1.0, 380, QEasingCurve.Type.OutCubic)
        else:
            self._run(self._chat_anim, self._chat_t, 0.0, 240, QEasingCurve.Type.InOutCubic)
        self.input.setFocus()

    def _on_chat(self, v):
        self._chat_t = float(v)
        self._sync_children()
        self.update()

    def _on_chat_done(self):
        if self._expanded and not self._chat_visible and self._chat_t <= 0.001:
            self._set_window_mode("row")        # 气泡彻底隐藏后，窗口缩回只剩输入行
            self.update()

    # ------------------------------------------------------------------ #
    # 发送 / Agent
    # ------------------------------------------------------------------ #
    def _idle_status(self) -> str:
        return "在线" if self.agent.remote else "本地模式"

    def _idle_detail(self) -> str:
        if self.agent.remote:
            subs = self.agent.core.subagents.enabled() if self.agent.core else []
            return self.agent.model_name + ("  ·  子智能体已开启" if subs else "")
        return "右键 → 设置，配置模型接口"

    def _refresh_status(self):
        if not self._sending and not self._recording:
            self.chat.set_status(self._idle_status(), self._idle_detail())

    def _update_send_state(self):
        text = self.input.text()
        if self._sending:
            if self._stop_requested:
                self.btn_send.set_kind("send")
                self.btn_send.setEnabled(False)
                self.btn_send.setToolTip("正在停止…")
            else:
                self.btn_send.set_kind("stop")
                self.btn_send.setEnabled(True)
                self.btn_send.setToolTip("停止生成  (Esc)")
        else:
            self.btn_send.set_kind("send")
            self.btn_send.setEnabled(bool(text.strip()) or bool(self._attachments))
            self.btn_send.setToolTip("发送  (Enter)")
        self.btn_clear.setVisible((bool(text) or bool(self._attachments))
                                  and not self._sending)

    def _open_chat_in_browser(self):
        if not self.chat.messages:
            self._toast("当前没有可导出的对话。", self._content_global(),
                        kind="info", ttl=1800)
            return
        try:
            path = open_report(
                os.path.join(PROJECT_ROOT, "temp", "reports"),
                "Desktop Bot 对话记录",
                chat_markdown(self.chat.messages),
                "从悬浮球导出的当前会话")
        except OSError as exc:
            self._toast(f"无法打开浏览器：{exc}", self._content_global(),
                        kind="error", ttl=2600)
            return
        self._toast(f"已在浏览器打开：{os.path.basename(path)}",
                    self._content_global(), kind="info", ttl=2200)

    def _clear_chat(self):
        if self._sending:
            # 等当前这一轮真正停下来再清空，避免 worker 把旧内容写回新会话
            self._clear_after_reply = True
            self._cancel_pending_choices()
            self.agent.stop()
            self.chat.set_status("正在停止并清空…", self._idle_detail())
            return
        self._do_clear_chat()

    def _do_clear_chat(self):
        self._cancel_pending_choices()
        self._sending = False
        self._stream_buf = ""
        self._stream_active = False
        self._had_error = False
        self._clear_after_reply = False
        self._stop_requested = False
        if self._stream_timer.isActive():
            self._stream_timer.stop()
        self.chat.clear()
        self.agent.reset()
        self._refresh_status()
        self._update_send_state()

    def _toast(self, text: str, anchor=None, kind: str = "info",
               title: str | None = None, ttl: int = 3200):
        """统一的横幅入口：模态对话框打开时不再弹出第三层窗口。"""
        if self._settings_open or self._permission_open:
            return
        self.toast.show_message(text, anchor or self._content_global(),
                                kind=kind, title=title, ttl=ttl)

    def _on_copied(self, _text: str):
        self._toast("已复制到剪贴板", self._content_global(), kind="info", ttl=1600)

    def _use_suggestion(self, text: str):
        self.input.setText(text)
        if not self._expanded:
            self.set_expanded(True)
        self.input.setFocus()

    def _on_send(self):
        if self._sending:
            self._stop_generation()
            return
        if self._recording or self._asr_busy:
            self._toast("请先结束语音输入，再发送消息。", self._content_global(),
                        kind="info", ttl=2200)
            return
        text = self.input.text().strip()
        attachments = list(self._attachments)
        if not text and not attachments:
            return
        self.chat.finish_tool_group()
        display = text or "（已附加文件）"
        if attachments:
            display += "\n\n" + "\n".join(
                f"📎 {os.path.basename(path)}" for path in attachments)
            text_for_agent = (text + "\n\n[用户附件]\n" + "\n".join(attachments)).strip()
        else:
            text_for_agent = text
        self._temp_store.log_message("user", text, attachments)
        self._attachments = []
        self._update_attach_hint()
        self.input.clear()
        if not self._expanded:
            self.set_expanded(True)
        if not self._chat_visible and self._auto_show_chat:
            self.set_chat_visible(True)
        self.chat.add("user", display)
        self.chat.set_typing(True)
        self.agent.clear_stop()
        self.chat.set_status("正在思考…", self._idle_detail())
        self._sending = True
        self._stream_buf = ""
        self._stream_active = False
        self._had_error = False
        self._clear_after_reply = False
        self._stop_requested = False
        self._update_send_state()
        threading.Thread(target=self._agent_worker, args=(text_for_agent,), daemon=True).start()

    # ------------------------------------------------------------------ #
    # 知识库 / 附件
    # ------------------------------------------------------------------ #
    def _update_attach_hint(self):
        if not hasattr(self, "input"):
            return
        count = len(self._attachments)
        self.input.setPlaceholderText(
            f"已附加 {count} 个文件，可继续输入…" if count else "输入消息…")
        self._update_send_state()

    def _clear_composer(self):
        for path in self._attachments:
            try:
                if os.path.abspath(path).startswith(
                        os.path.abspath(self._temp_store.root) + os.sep):
                    os.remove(path)
            except OSError:
                pass
        self._attachments = []
        self.input.clear()
        self._update_attach_hint()

    def _show_attach_menu(self):
        menu = QMenu(self)
        menu.addAction("添加文件 / 图片…", self._choose_attachments)
        menu.addAction("截取屏幕", self._capture_attachment)
        menu.addAction("粘贴剪贴板", lambda: self._paste_attachment())
        menu.exec(self.input.mapToGlobal(QPoint(0, self.input.height())))

    def _choose_attachments(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "添加附件", PROJECT_ROOT, "所有文件 (*.*)")
        self._add_attachment_paths(paths)

    def _add_attachment_paths(self, paths):
        added = 0
        for path in paths:
            try:
                self._attachments.append(self._temp_store.save_attachment(path))
                added += 1
            except OSError as exc:
                self._toast(f"附件读取失败：{exc}", self._content_global(),
                            kind="error", ttl=2400)
        if added:
            self._update_attach_hint()
            self._toast(f"已附加 {added} 个文件", self._content_global(),
                        kind="info", ttl=1600)

    def _capture_attachment(self):
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        pixmap = screen.grabWindow(0)
        try:
            path = self._temp_store.save_image(pixmap.toImage())
            self._attachments.append(path)
            self._update_attach_hint()
            self._toast("截图已附加", self._content_global(), kind="info", ttl=1600)
        except OSError as exc:
            self._toast(f"截图失败：{exc}", self._content_global(), kind="error", ttl=2400)

    def _paste_attachment(self) -> bool:
        mime = QGuiApplication.clipboard().mimeData()
        if mime is None:
            return False
        if mime.hasImage():
            image = QGuiApplication.clipboard().image()
            if not image.isNull():
                try:
                    self._attachments.append(self._temp_store.save_image(image))
                    self._update_attach_hint()
                    return True
                except OSError:
                    return False
        if mime.hasUrls():
            paths = [url.toLocalFile() for url in mime.urls() if url.isLocalFile()]
            if paths:
                self._add_attachment_paths(paths)
                return True
        return False

    def _choose_library_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "添加文件到知识库", PROJECT_ROOT, "所有文件 (*.*)")
        if paths:
            self._start_library_update(paths=paths, include_temp=False)

    def _manual_library_update(self):
        self._start_library_update(include_temp=True)

    def _check_daily_library(self):
        now = time.localtime()
        today = time.strftime("%Y-%m-%d", now)
        if now.tm_hour == 2 and now.tm_min < 5:
            if self.settings.value("last_daily_library", "") != today:
                self.settings.setValue("last_daily_library", today)
                if self._library_worker is None:
                    self._start_library_update(include_temp=True, silent=True)

    def _start_library_update(self, paths=None, include_temp=False, silent=False):
        if self._library_worker is not None:
            self._toast("知识库正在整理，请稍候。", self._content_global(),
                        kind="info", ttl=1800)
            return
        cfg = self.agent.cfg
        database = cfg.get("database") or os.path.join(PROJECT_ROOT, "database")
        organizer = LibraryOrganizer(
            database, os.path.join(PROJECT_ROOT, "temp"),
            embedding_model=cfg.get("embedding_model"),
            embedding_cache=cfg.get("embedding_cache"))
        worker = LibraryWorker(organizer, paths=paths, include_temp=include_temp)
        self._library_worker = worker
        if not silent:
            progress = LibraryProgressDialog(self, self.theme)
            progress.setWindowModality(Qt.WindowModality.ApplicationModal)
            progress.show()
            self._library_progress = progress
            worker.progress.connect(progress.set_progress)
        worker.finished.connect(self._on_library_done)
        worker.failed.connect(self._on_library_failed)
        worker.start()

    def _on_library_done(self, result):
        if self._library_progress is not None:
            progress = self._library_progress
            progress.set_progress(100, "知识库已更新")
            QTimer.singleShot(420, progress.close)
            self._library_progress = None
        worker, self._library_worker = self._library_worker, None
        if worker is not None:
            worker.deleteLater()
        self._toast("知识库已更新，可以直接检索新资料。", self._content_global(),
                    kind="info", ttl=2600)
        self._update_attach_hint()

    def _on_library_failed(self, message):
        if self._library_progress is not None:
            self._library_progress.close()
            self._library_progress = None
        worker, self._library_worker = self._library_worker, None
        if worker is not None:
            worker.deleteLater()
        self._toast(f"知识库更新失败：{message}", self._content_global(),
                    kind="error", ttl=5000)

    def _stop_generation(self):
        if not self._sending:
            return
        self._stop_requested = True
        self._cancel_pending_choices()
        self.agent.stop()
        self._finish_stream_inline()
        self.chat.set_typing(False)
        self.chat.tool_group_step("已停止生成", "fail")
        self.chat.set_status("已停止", self._idle_detail())
        self.btn_send.set_kind("send")
        self.btn_send.setEnabled(False)
        self.btn_send.setToolTip("正在停止…")

    def _agent_worker(self, text: str):
        reply = ""
        try:
            for ev in self.agent.run(text):
                if ev.type == "final":
                    reply = ev.text
        except Exception as exc:                                       # noqa: BLE001
            reply = f"出错了：{exc}"
            self.agent_event.emit(AgentEvent("error", text=reply))
        self.agent_reply.emit(reply or "（没有返回内容）")

    def _on_agent_event_cb(self, ev: AgentEvent):
        """worker 线程回调：只做跨线程投递，不直接碰界面。"""
        self.agent_event.emit(ev)

    def _flush_stream(self):
        if not self._stream_buf:
            self._stream_timer.stop()
            return
        if not self._stream_active:
            self.chat.set_typing(False)
            self.chat.begin_stream()
            self._stream_active = True
        buf, self._stream_buf = self._stream_buf, ""
        self.chat.append_stream(buf)

    def _finish_stream_inline(self, text: Optional[str] = None) -> bool:
        """把尚未落盘的流式增量刷进气泡，并结束当前流式消息。"""
        if self._stream_timer.isActive():
            self._stream_timer.stop()
        if self._stream_buf:
            if not self._stream_active:
                self.chat.set_typing(False)
                self.chat.begin_stream()
                self._stream_active = True
            buf, self._stream_buf = self._stream_buf, ""
            self.chat.append_stream(buf)
        if self._stream_active:
            self.chat.finish_stream(text)
            self._stream_active = False
            return True
        return False

    def _on_agent_event(self, ev: AgentEvent):
        """主线程处理智能体的中间事件（流式文本 / 工具 / 子智能体进度）。"""
        if self._clear_after_reply or self._stop_requested:
            return
        if ev.type == "text_delta":
            if not self._stream_buf and not self._stream_active:
                self.chat.finish_tool_group()
            self._stream_buf += ev.text
            if not self._stream_timer.isActive():
                self._stream_timer.start()
        elif ev.type == "text":
            if ev.text:
                self.chat.finish_tool_group()
            return
        elif ev.type == "tool_use":
            self._finish_stream_inline()
            if ev.tool_name not in ("task", "ask_user"):
                self.chat.tool_group_step(ev.tool_name, "running")
            self.chat.set_status(f"正在调用工具：{ev.tool_name}…", self._idle_detail())
        elif ev.type == "tool_result":
            if ev.tool_name not in ("task", "ask_user"):
                self.chat.tool_group_result(ev.tool_name, not ev.is_error)
            self.chat.set_status("正在整理结果…", self._idle_detail())
        elif ev.type == "subagent_start":
            self._finish_stream_inline()
            self.chat.tool_group_step(f"子任务 · {ev.agent_name}", "running")
            self.chat.set_status(f"{ev.agent_name} 正在处理…", self._idle_detail())
        elif ev.type == "subagent_event":
            inner = ev.subagent_event
            label = (f"{ev.agent_name} · {ev.tool_name}" if ev.tool_name
                     else f"子任务 · {ev.agent_name}")
            if inner == "tool_use":
                if ev.tool_name not in ("task", "ask_user"):
                    self.chat.tool_group_step(label, "running")
                self.chat.set_status(f"{ev.agent_name} · {ev.tool_name}…", self._idle_detail())
            elif inner == "tool_result":
                if ev.tool_name not in ("task", "ask_user"):
                    self.chat.tool_group_result(label, not ev.is_error)
            elif inner == "error":
                self.chat.tool_group_result(f"子任务 · {ev.agent_name}", False)
                self.chat.set_status(f"{ev.agent_name} 遇到问题", self._idle_detail())
        elif ev.type == "subagent_end":
            self.chat.tool_group_result(f"子任务 · {ev.agent_name}", not ev.is_error)
            self.chat.set_status("正在整理结果…", self._idle_detail())
        elif ev.type == "error":
            self._had_error = True
            self._finish_stream_inline()
            self.chat.tool_group_step("运行出错", "fail")
            self.chat.set_status("出错了", self._idle_detail())

    def _request_permission(self, name: str, tool_input: dict) -> PermissionResult:
        """在 worker 线程中调用：向主线程请求工具授权并等待结果。"""
        if name in self._allowed_tools:
            return PermissionResult(allow=True)
        req = PermissionRequest(name, tool_input,
                                str(self.agent.cfg.get("permission_mode", "default")))
        self.permission_asked.emit(req)
        if not req.event.wait(timeout=180):
            return PermissionResult(allow=False, message="等待授权超时，已取消该操作。")
        return req.result

    def _request_choice(self, question: str, options: list[str]) -> str:
        """worker 线程调用：把选项气泡交给主线程，等待用户点击。"""
        req = ChoiceRequest(question, options)
        self.choice_asked.emit(req)
        if not req.event.wait(timeout=300):
            return ""
        return req.result

    def _on_choice_asked(self, req: ChoiceRequest):
        if self._stop_requested or self._clear_after_reply:
            req.result = "（已取消）"
            req.event.set()
            return
        self._pending_choices[req.id] = req
        self.chat.set_typing(False)
        self.reveal_chat()
        self.chat.add_choice(req.id, req.question, req.options)
        self.chat.set_status("等待你选择…", self._idle_detail())
        self.raise_()

    def _on_choice_selected(self, choice_id: str, option: str):
        req = self._pending_choices.pop(choice_id, None)
        if req is None:
            self.chat.cancel_choice(choice_id, "该选择已失效")
            return
        req.result = option
        req.event.set()
        self.chat.answer_choice(choice_id, option)
        self.chat.set_status(f"已选择「{option}」，继续处理…", self._idle_detail())

    def _cancel_pending_choices(self, reason: str = "已取消"):
        if not self._pending_choices:
            return
        for req in list(self._pending_choices.values()):
            req.result = f"（{reason}）"
            req.event.set()
            self.chat.cancel_choice(req.id, reason)
        self._pending_choices.clear()

    def _on_permission_asked(self, req: PermissionRequest):
        """主线程弹窗询问工具授权；支持「总是允许」。"""
        if req.event.is_set():
            return
        if self._stop_requested or self._clear_after_reply:
            req.result = PermissionResult(allow=False, message="已停止，未执行该操作。")
            req.event.set()
            return
        if self._permission_open:
            req.result = PermissionResult(allow=False, message="已有授权请求正在处理。")
            req.event.set()
            return
        self.toast.hide_now()
        if not self.isVisible():
            self.show()
            self.raise_()
        self._permission_open = True
        try:
            dlg = PermissionDialog(self, req.name, req.input, req.mode,
                                   dark=self.theme.dark)
            dlg.exec()
            choice = dlg.choice
            allow = choice in ("allow", "always")
            if choice == "always":
                self._allowed_tools.add(req.name)
                self._save_allowed_tools()
            req.result = PermissionResult(
                allow=allow,
                message="" if allow else "用户拒绝了该工具调用。")
        except Exception:                                      # noqa: BLE001
            req.result = PermissionResult(allow=False, message="授权界面出错了。")
        finally:
            self._permission_open = False
            req.event.set()

    def _on_agent_reply(self, reply: str):
        self._sending = False
        self.chat.set_typing(False)
        if self._clear_after_reply:
            self._do_clear_chat()
            self.input.setFocus()
            return
        if self._stop_requested:
            self._stop_requested = False
            self._finish_stream_inline()
            self.chat.finish_tool_group()
            self._refresh_status()
            self._update_send_state()
            self.input.setFocus()
            return
        if self._stream_active:
            self._finish_stream_inline(reply or None)
        elif reply:
            role = "error" if self._had_error else "agent"
            self.chat.add(role, reply)
        self.chat.finish_tool_group()
        if reply:
            self._temp_store.log_message("assistant", reply, [])
        self._had_error = False
        self._refresh_status()
        self._update_send_state()
        # 报告工具只生成文件；等本轮回答结束后再打开，避免生成过程弹浏览器。
        open_pending_report()
        if self._expanded and self._chat_visible:
            self.input.setFocus()
        else:                                    # 气泡被隐藏：横幅预览
            if self._notify_hidden:
                self._toast(reply, self._content_global(), kind="reply",
                            title="新消息", ttl=6000)

    # ------------------------------------------------------------------ #
    # 语音识别
    # ------------------------------------------------------------------ #
    def _toggle_record(self):
        if self._sending:
            self._toast("等这次回复结束再说话哦。", self._content_global(),
                                    kind="info", ttl=2200)
            return
        if self._recording:
            self._stop_record()
        else:
            self._start_record()

    def _start_record(self):
        if not self._voice_enabled:
            self._toast("语音输入已关闭，可在设置 → 语音中开启。",
                                    self._content_global(), kind="info", ttl=3200)
            return
        if self._asr_busy:
            return
        cfg = self._asr_cfg
        if not (cfg.get("secret_id") and cfg.get("secret_key")):
            self._toast(
                "未配置腾讯云语音识别。请在设置 → 语音中填写 SecretId / SecretKey。",
                self._content_global(), kind="error", ttl=5000)
            return
        dev = QMediaDevices.defaultAudioInput()
        fmt = QAudioFormat()
        fmt.setSampleRate(16000)
        fmt.setChannelCount(1)
        fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        if not dev.isFormatSupported(fmt):
            self._toast("当前麦克风不支持 16kHz 单声道录音。", self._content_global(),
                                    kind="error")
            return
        self._rec_fmt = fmt
        self._audio_source = QAudioSource(dev, fmt, self)
        self._audio_buf = QByteArray()
        self._audio_io = self._audio_source.start()
        if self._audio_io is None:
            self._audio_source = None
            self._toast("无法打开麦克风。", self._content_global(), kind="error")
            return
        self._audio_io.readyRead.connect(self._on_audio_ready)
        self._recording = True
        self.btn_mic.set_active(True)
        self.chat.set_status("正在聆听…")
        self._rec_timer.start(60000)
        self._toast("正在聆听… 再次点按麦克风结束", self._content_global(), kind="rec", ttl=0)

    def _on_audio_ready(self):
        if self._audio_io is not None and self._audio_buf is not None:
            data = bytes(self._audio_io.readAll())
            self._audio_buf.append(data)
            self._level = max(self._level, pcm_level(data))

    def _stop_record(self):
        if not self._recording:
            return
        self._rec_timer.stop()
        self._recording = False
        self.btn_mic.set_active(False)
        if self._audio_source is not None:
            self._audio_source.stop()
        pcm = bytes(self._audio_buf) if self._audio_buf is not None else b""
        self._audio_io = None
        self._audio_source = None
        self._refresh_status()
        if len(pcm) < 3200:
            self._toast("录音太短，请重试。", self._content_global(), kind="error")
            return
        wav = pcm_to_wav(pcm, self._rec_fmt.sampleRate(), self._rec_fmt.channelCount())
        self._asr_busy = True
        self.chat.set_status("识别中…")
        self._toast("识别中…", self._content_global(), kind="info", ttl=0)
        threading.Thread(target=self._do_asr, args=(wav,), daemon=True).start()

    def _do_asr(self, wav: bytes):
        cfg = self._asr_cfg
        try:
            text = tencent_sentence_recognition(
                wav, cfg["secret_id"], cfg["secret_key"],
                cfg.get("region", "ap-guangzhou"), cfg.get("engine", "16k_zh"))
            self.asr_result.emit(True, text)
        except Exception as exc:                                       # noqa: BLE001
            self.asr_result.emit(False, str(exc))

    def _on_asr_result(self, ok: bool, payload: str):
        self._asr_busy = False
        self._refresh_status()
        self.toast.dismiss()
        if not ok:
            self._toast(f"识别失败：{payload}", self._content_global(), kind="error", ttl=5000)
            return
        text = (payload or "").strip()
        if not text:
            self._toast("没有听清，请重试。", self._content_global(), kind="error")
            return
        cur = self.input.text().strip()
        self.input.setText((cur + " " + text).strip() if cur else text)
        self.input.setFocus()

    # ------------------------------------------------------------------ #
    # 键盘 / 托盘 / 关闭
    # ------------------------------------------------------------------ #
    def _escape(self):
        if self._sending:
            self._stop_generation()
        elif self._expanded:
            self.set_expanded(False)

    def eventFilter(self, obj, event):
        if obj is self.input:
            t = event.type()
            if t == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
                self._escape()
                return True
            if (t == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_V
                    and event.modifiers() & Qt.KeyboardModifier.ControlModifier):
                if self._paste_attachment():
                    return True
        return super().eventFilter(obj, event)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self._escape()
            e.accept()
            return
        super().keyPressEvent(e)

    def _on_tray_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                      QSystemTrayIcon.ActivationReason.DoubleClick):
            self.show()
            self.raise_()
            self.reveal_chat()

    def closeEvent(self, e):
        self._cancel_pending_choices()
        if self._sending:
            self.agent.stop()
        if self._recording:
            self._stop_record()
        self._save_state()
        if self._audio_source is not None:
            self._audio_source.stop()
        super().closeEvent(e)


# --------------------------------------------------------------------------- #
def _install_error_log():
    """pythonw 启动时没有 stderr；把未捕获异常写到同目录，方便排查。"""
    import traceback

    temp_dir = os.path.join(PROJECT_ROOT, "temp")
    os.makedirs(temp_dir, exist_ok=True)
    path = os.path.join(temp_dir, "app_error.log")

    def hook(exc_type, exc, tb):
        try:
            with open(path, "a", encoding="utf-8") as f:
                stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                f.write("\n[" + stamp + "]\n")
                f.write("".join(traceback.format_exception(exc_type, exc, tb)))
        except OSError:
            pass
        try:
            sys.__excepthook__(exc_type, exc, tb)
        except Exception:                                      # noqa: BLE001
            pass

    sys.excepthook = hook


def main():
    _install_error_log()
    QApplication.setOrganizationName("FloatingBall")
    QApplication.setApplicationName("FloatingBall")

    # 单实例：重复双击启动器时保持安静，不再出现第二个球/托盘图标。
    lock = QLockFile(os.path.join(tempfile.gettempdir(), "floating_ball.lock"))
    lock.setStaleLockTime(15000)
    if not lock.tryLock(120):
        return

    app = QApplication(sys.argv)
    app._instance_lock = lock          # 保持引用，进程存活期间持有锁
    app.setStyle("Fusion")
    app.setQuitOnLastWindowClosed(False)
    app.setFont(ui_font(14))
    app.setWindowIcon(QIcon(make_ball_pixmap(64)))

    ball = FloatingBall()
    ball.show()
    ball.raise_()

    if os.environ.get("FB_TEST") == "1":
        QTimer.singleShot(1500, app.quit)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
