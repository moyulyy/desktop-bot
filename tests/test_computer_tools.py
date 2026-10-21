# -*- coding: utf-8 -*-
import os

import pytest

from desktop_assistant.computer_tools import (
    ComputerWorkspace,
    _vk_for,
    build_computer_tools,
)


def test_workspace_confines_paths(tmp_path):
    ws = ComputerWorkspace(str(tmp_path / "workspace"))
    ws.ensure()
    path = ws.safe_path("hello.py", "scripts")
    assert path.endswith(os.path.join("scripts", "hello.py"))
    with pytest.raises(ValueError):
        ws.safe_path("../../escape.py", "scripts")
    assert os.path.isdir(ws.screenshots_dir)
    assert os.path.isdir(ws.scripts_dir)


def test_computer_tools_and_workspace_script(tmp_path):
    tools = {tool.name: tool for tool in build_computer_tools(str(tmp_path / "ws"))}
    expected = {
        "screen_capture", "screen_info", "mouse_click", "keyboard_type",
        "keyboard_press", "focus_window", "workspace_write",
        "workspace_run_python", "workspace_list",
    }
    assert expected.issubset(tools)
    assert _vk_for("ctrl") == 0x11
    assert _vk_for("f12") == 0x7B

    written = tools["workspace_write"].call({
        "path": "hello.py",
        "content": "print('computer-workspace-ok')\n",
    })
    assert not written.is_error
    ran = tools["workspace_run_python"].call({"path": "hello.py"})
    assert not ran.is_error
    assert "computer-workspace-ok" in ran.content
    listed = tools["workspace_list"].call({"path": "scripts"})
    assert "hello.py" in listed.content
