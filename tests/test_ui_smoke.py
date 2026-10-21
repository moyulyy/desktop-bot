# -*- coding: utf-8 -*-
from PySide6.QtTest import QTest


def test_chat_card_streaming_and_roles(qapp):
    from desktop_assistant.app import ChatCard

    card = ChatCard()
    card.resize(440, 344)
    card.show()
    QTest.qWait(30)
    card.begin_stream()
    card.append_stream("你")
    card.append_stream("好")
    card.finish_stream("你好")
    assert card.messages[-1]["text"] == "你好"
    assert not card.messages[-1].get("streaming")

    card.add("tool", "read_file · 完成", meta={"ok": True})
    card.add("tool", "run_shell · 失败", meta={"ok": False})
    card.add("error", "模型调用失败：连接超时")
    QTest.qWait(60)
    card.grab()
    card.clear()
    assert card.messages == []
    card.close()


def test_floating_ball_smoke(qapp):
    from desktop_assistant.app import FloatingBall

    ball = FloatingBall()
    ball.show()
    QTest.qWait(80)
    ball.set_expanded(True)
    QTest.qWait(460)
    assert ball._expanded
    assert ball.chat._btns.get("settings") is not None

    ball._use_suggestion("现在几点了？")
    assert ball.input.text() == "现在几点了？"

    ball.chat.begin_stream()
    ball.chat.append_stream("流式")
    ball.chat.finish_stream("流式回复")
    ball.chat.add("tool", "read_file · 完成", meta={"ok": True})
    QTest.qWait(80)
    ball.grab()

    ball.set_chat_visible(False)
    QTest.qWait(280)
    ball.set_chat_visible(True)
    QTest.qWait(420)
    assert ball._chat_visible

    ball._apply_theme()
    ball.close()
    qapp.processEvents()


def test_settings_dialog_collects_values(qapp):
    from desktop_assistant.config_store import load_agent_config, load_asr_config
    from desktop_assistant.settings_dialog import SettingsDialog

    dlg = SettingsDialog(None, load_agent_config(apply_env=False),
                         load_asr_config(apply_env=False), {}, dark=True)
    dlg.show()
    QTest.qWait(60)
    dlg.api_key.setText("sk-test")
    dlg.base_url.setText("https://example.com/v1")
    dlg.model.setText("test-model")
    dlg.stream.setChecked(False)
    dlg.opacity.setValue(80)
    dlg._save()
    data = dlg.result_data
    assert data is not None
    assert data["agent"]["api_key"] == "sk-test"
    assert data["agent"]["model"] == "test-model"
    assert data["agent"]["stream"] is False
    assert data["prefs"]["opacity"] == 0.8
    assert data["agent"]["subagents"]["items"], "内置子智能体应出现在设置页"
    assert data["asr"]["region"] == "ap-guangzhou"
    dlg.deleteLater()


def test_permission_dialog_choices(qapp):
    from desktop_assistant.settings_dialog import PermissionDialog

    dlg = PermissionDialog(None, "write_file", {"path": "a.txt"}, "default", dark=True)
    dlg.show()
    QTest.qWait(40)
    dlg._finish("always")
    assert dlg.choice == "always"
    dlg.deleteLater()


def test_streaming_events_and_stop_button(qapp):
    from desktop_assistant.agent_framework import AgentEvent
    from desktop_assistant.app import FloatingBall

    ball = FloatingBall()
    ball.show()
    ball.set_expanded(True)
    QTest.qWait(440)

    ball._sending = True
    ball._update_send_state()
    assert ball.btn_send.kind == "stop"

    ball.chat.set_typing(True)
    ball._on_agent_event(AgentEvent("text_delta", text="你"))
    ball._on_agent_event(AgentEvent("text_delta", text="好"))
    ball._flush_stream()          # 不等定时器，直接验证缓冲刷新的行为
    assert ball.chat.stream_text() == "你好"

    ball._on_agent_event(AgentEvent("tool_use", tool_name="get_current_time"))
    assert not ball._stream_active
    ball._on_agent_event(AgentEvent("tool_result", tool_name="get_current_time"))
    ball._on_agent_event(AgentEvent("subagent_start", agent_name="researcher"))
    ball._on_agent_event(AgentEvent("subagent_end", agent_name="researcher"))
    ball._on_agent_reply("最终回答")
    assert ball.chat.messages[-1]["text"] == "最终回答"
    assert ball.btn_send.kind == "send"
    assert not ball._sending

    ball.close()
    qapp.processEvents()


def test_menu_and_clear_while_generating(qapp):
    from desktop_assistant.app import FloatingBall

    ball = FloatingBall()
    ball.show()
    ball.set_expanded(True)
    QTest.qWait(420)
    ball._populate_menu()
    texts = [a.text() for a in ball.menu.actions()]
    assert any("设置" in t for t in texts)

    ball._sending = True
    ball._clear_chat()
    assert ball._clear_after_reply
    ball._on_agent_reply("（已停止）")
    assert ball.chat.messages == []
    assert not ball._clear_after_reply
    assert not ball._sending

    ball.close()
    qapp.processEvents()


def test_open_settings_applies_result(qapp, monkeypatch):
    import desktop_assistant.app as fb
    from desktop_assistant.config_store import load_agent_config, load_asr_config

    class FakeDialog:
        def __init__(self, *args, **kwargs):
            self.result_data = {
                "agent": load_agent_config(apply_env=False),
                "asr": load_asr_config(apply_env=False),
                "prefs": {"appearance": "light", "opacity": 0.7,
                          "idle_dim": False, "notify_hidden": False,
                          "voice_enabled": False, "auto_show_chat": False},
                "reset_position": False,
            }

        def exec(self):
            return fb.QDialog.DialogCode.Accepted

    saved = {}
    monkeypatch.setattr(fb, "SettingsDialog", FakeDialog)
    monkeypatch.setattr(fb, "save_agent_config",
                        lambda cfg: saved.setdefault("agent", cfg) or cfg)
    monkeypatch.setattr(fb, "save_asr_config",
                        lambda cfg: saved.setdefault("asr", cfg) or cfg)

    ball = fb.FloatingBall()
    ball.show()
    QTest.qWait(60)
    ball._open_settings()
    assert ball._appearance == "light"
    assert ball._base_opacity == 0.7
    assert ball._voice_enabled is False
    assert ball._notify_hidden is False
    assert not ball.btn_mic.isEnabled()
    assert "agent" in saved and "asr" in saved
    ball.close()
    qapp.processEvents()


def test_dialogs_are_always_on_top(qapp):
    from PySide6.QtCore import Qt
    from desktop_assistant.config_store import load_agent_config, load_asr_config
    from desktop_assistant.settings_dialog import PermissionDialog, SettingsDialog

    perm = PermissionDialog(None, "open_url", {"url": "https://example.com"},
                            "default", dark=True)
    assert perm.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    assert perm.windowFlags() & Qt.WindowType.Tool
    perm.close()
    perm.deleteLater()

    dlg = SettingsDialog(None, load_agent_config(apply_env=False),
                         load_asr_config(apply_env=False), {}, dark=True)
    assert dlg.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    assert dlg.windowFlags() & Qt.WindowType.Tool
    assert dlg.workspace.text()
    dlg.close()
    dlg.deleteLater()


def test_tool_windows_do_not_use_taskbar(qapp):
    from PySide6.QtCore import Qt
    from desktop_assistant.app import FloatingBall

    ball = FloatingBall()
    assert ball.windowFlags() & Qt.WindowType.Tool
    assert ball.toast.windowFlags() & Qt.WindowType.Tool
    ball.close()
    ball.deleteLater()


def test_permission_dialog_exec_returns(qapp):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QDialog
    from desktop_assistant.settings_dialog import PermissionDialog

    dlg = PermissionDialog(None, "open_url", {"url": "https://example.com"},
                           "default", dark=True)
    QTimer.singleShot(60, dlg.reject)
    assert dlg.exec() == QDialog.DialogCode.Rejected
    dlg.deleteLater()


def test_markdown_local_image(qapp, tmp_path):
    from PySide6.QtGui import QColor, QImage
    from desktop_assistant.app import ChatCard

    path = tmp_path / "sample.png"
    img = QImage(400, 200, QImage.Format.Format_ARGB32)
    img.fill(QColor(240, 90, 120))
    assert img.save(str(path))

    card = ChatCard()
    card.resize(440, 344)
    card.show()
    QTest.qWait(30)
    card.add("agent", f"看这张图：\n\n![示例]({path.as_posix()})")
    QTest.qWait(60)
    m = card.messages[-1]
    assert m["th"] > 120
    assert path.as_posix() in card._images
    assert m["doc"].size().height() > 100
    card.grab()
    card.close()


def test_choice_card_click_and_cancel(qapp):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    from desktop_assistant.app import ChatCard

    card = ChatCard()
    card.resize(440, 344)
    card.show()
    QTest.qWait(30)
    card.add_choice("c1", "想让我先做哪一块？", ["交互", "设置", "架构"])
    QTest.qWait(60)
    m = card.messages[-1]
    assert m["role"] == "choice"
    assert len(m["option_rects"]) == 3

    selected = []
    card.choice_selected.connect(lambda cid, opt: selected.append((cid, opt)))
    rect = m["option_rects"][1]
    pos = QPoint(int(rect.center().x()),
                 int(rect.center().y() - card._scroll + 52))
    QTest.mouseClick(card, Qt.MouseButton.LeftButton, pos=pos)
    assert selected == [("c1", "设置")]
    assert m["selected"] == "设置"

    card.add_choice("c2", "继续吗？", ["继续", "算了"])
    QTest.qWait(40)
    card.cancel_choice("c2", "已取消")
    m2 = card.messages[-1]
    assert m2["cancelled"] == "已取消"
    card.close()


def test_stop_ui_is_immediate_and_ignores_late_reply(qapp):
    from desktop_assistant.agent_framework import AgentEvent
    from desktop_assistant.app import FloatingBall

    ball = FloatingBall()
    ball.show()
    ball.set_expanded(True)
    QTest.qWait(420)

    ball._sending = True
    ball.chat.set_typing(True)
    ball.chat.begin_stream()
    ball.chat.append_stream("一段还没结束的回复")
    ball._stop_generation()
    assert ball._stop_requested
    assert not ball.chat.typing
    assert not ball._stream_active
    stopped = [m for m in ball.chat.messages if m.get("role") == "tool_group"]
    assert stopped and any(step["label"] == "已停止生成"
                           for step in stopped[-1]["steps"])

    ball._on_agent_event(AgentEvent("text_delta", text="迟到的内容"))
    ball._on_agent_reply("迟到的完整回复")
    assert not ball._sending
    assert not ball._stop_requested
    assert all("迟到" not in m.get("text", "") for m in ball.chat.messages)
    ball.close()


def test_choice_request_integration_and_stop_cancel(qapp):
    from desktop_assistant.app import ChoiceRequest, FloatingBall

    ball = FloatingBall()
    ball.show()
    ball.set_expanded(True)
    QTest.qWait(420)

    req = ChoiceRequest("先做哪个？", ["交互", "设置"])
    ball._on_choice_asked(req)
    assert req.id in ball._pending_choices
    assert ball.chat.messages[-1]["role"] == "choice"

    ball._on_choice_selected(req.id, "设置")
    assert req.event.is_set()
    assert req.result == "设置"
    assert req.id not in ball._pending_choices
    assert ball.chat.messages[-1]["selected"] == "设置"

    req2 = ChoiceRequest("继续吗？", ["继续", "算了"])
    ball._on_choice_asked(req2)
    ball._sending = True
    ball._stop_generation()
    assert req2.event.is_set()
    assert "取消" in req2.result
    assert req2.id not in ball._pending_choices
    ball.close()


def test_bubble_toggle_removed_and_input_wider(qapp):
    from desktop_assistant.app import FloatingBall

    ball = FloatingBall()
    ball.show()
    ball.set_expanded(True)
    QTest.qWait(420)
    assert not hasattr(ball, "btn_chat")
    assert ball._geo.pill.width() > 340
    ball.close()


def test_message_panel_corner_resize_and_persist(qapp):
    from PySide6.QtCore import QPoint
    from desktop_assistant.app import (
        FloatingBall, MIN_CHAT_H, MIN_PANEL_W,
    )

    ball = FloatingBall()
    ball.show()
    ball.set_expanded(True)
    QTest.qWait(460)
    start_w, start_h = ball._chat_w, ball._chat_h
    corner = ball.chat.resize_corner
    assert corner in ("tl", "tr", "bl", "br")

    press = ball.chat.mapToGlobal(ball.chat._resize_grip_rect().center().toPoint())
    ball._on_chat_resize("press", press)
    dx = -70 if corner in ("tl", "bl") else 70
    dy = -50 if corner in ("tl", "tr") else 50
    ball._on_chat_resize("move", press + QPoint(dx, dy))
    assert ball._chat_w > start_w
    assert ball._chat_h > start_h
    assert int(ball.settings.value("chat_w")) == ball._chat_w
    ball._on_chat_resize("release", press + QPoint(dx, dy))

    wide, tall = ball._clamp_chat_size(10, 10000)
    assert wide >= MIN_PANEL_W
    assert tall >= MIN_CHAT_H
    ball._reset_chat_size()
    ball.close()


def test_chat_card_resize_grip_emits_events(qapp):
    from PySide6.QtCore import QPoint, Qt
    from desktop_assistant.app import ChatCard

    card = ChatCard()
    card.set_resize_corner("br")
    card.resize(460, 340)
    card.show()
    QTest.qWait(30)
    events = []
    card.resize_event.connect(lambda kind, pos: events.append(kind))
    rect = card._resize_grip_rect()
    center = rect.center().toPoint()
    QTest.mousePress(card, Qt.MouseButton.LeftButton, pos=center)
    QTest.mouseMove(card, center + QPoint(30, 24))
    QTest.mouseRelease(card, Qt.MouseButton.LeftButton,
                       pos=center + QPoint(30, 24))
    assert "press" in events
    assert "move" in events
    assert "release" in events
    card.close()


def test_tool_group_aggregates_and_collapses(qapp):
    from desktop_assistant.app import ChatCard

    card = ChatCard()
    card.resize(500, 420)
    card.show()
    QTest.qWait(30)
    card.tool_group_step("run_shell", "running")
    group = next(m for m in card.messages if m.get("role") == "tool_group")
    assert group["running"] is True
    assert card._anim_timer.isActive()
    card.tool_group_result("run_shell", ok=False)
    for index in range(3):
        card.tool_group_step("run_shell", "running")
        card.tool_group_result("run_shell", ok=index != 1)
    card.finish_tool_group()
    groups = [m for m in card.messages if m.get("role") == "tool_group"]
    assert len(groups) == 1
    assert len(groups[0]["steps"]) == 4
    assert "2 个失败" in groups[0]["summary"]
    assert not groups[0]["expanded"]
    card.toggle_tool_group(groups[0])
    assert groups[0]["expanded"]
    card.grab()
    card.close()


def test_agent_events_use_one_tool_group(qapp):
    from desktop_assistant.agent_framework import AgentEvent
    from desktop_assistant.app import FloatingBall

    ball = FloatingBall()
    ball.show()
    ball.set_expanded(True)
    QTest.qWait(420)
    for index in range(6):
        ball._on_agent_event(AgentEvent("tool_use", tool_name="run_shell"))
        ball._on_agent_event(AgentEvent("tool_result", tool_name="run_shell",
                                        is_error=index in (1, 4)))
    groups = [m for m in ball.chat.messages if m.get("role") == "tool_group"]
    tools = [m for m in ball.chat.messages if m.get("role") == "tool"]
    assert len(groups) == 1
    assert not tools
    assert len(groups[0]["steps"]) == 6
    assert "2 个失败" in groups[0]["summary"]
    ball._on_agent_reply("搜索完成")
    assert not groups[0]["running"]
    ball.close()
