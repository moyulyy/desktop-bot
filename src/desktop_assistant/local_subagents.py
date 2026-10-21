# -*- coding: utf-8 -*-
"""本地文件检索子智能体：用 Everything 1.5 在磁盘上查找文件。"""

from __future__ import annotations

import os

try:  # 包内导入
    from .agent_framework import SubagentSpec, default_subagents, default_tools
    from .everything_search import build_search_tool
    from .computer_tools import build_computer_tools
except ImportError:  # 兼容直接以脚本方式运行
    from agent_framework import SubagentSpec, default_subagents, default_tools
    from everything_search import build_search_tool
    from computer_tools import build_computer_tools


def available_subagents(cwd: str, workspace: str | None = None,
                        stop_event=None) -> list[SubagentSpec]:
    """内置角色 + finder（Everything）+ computer（电脑操作）。"""
    specs = list(default_subagents(cwd, stop_event=stop_event))
    base = {tool.name: tool for tool in default_tools(cwd, stop_event=stop_event)}
    search_tool = build_search_tool(cwd, stop_event=stop_event)
    tools = [search_tool]
    for name in ("read_file", "list_directory", "get_current_time"):
        tool = base.get(name)
        if tool is not None:
            tools.append(tool)
    workspace_path = os.path.abspath(os.path.expanduser(
        workspace or os.path.join(cwd, "temp")))
    cs_tools = build_computer_tools(workspace_path, stop_event=stop_event)
    for name in ("read_file", "list_directory", "get_current_time"):
        tool = base.get(name)
        if tool is not None:
            cs_tools.append(tool)
    specs.append(SubagentSpec(
        name="computer",
        description=(
            "操作电脑：截屏、鼠标点击/拖动/滚动、键盘输入、切换窗口、打开应用；"
            "可以在 temp 工作空间中生成临时 Python 脚本和截图。适合需要代替用户完成"
            "基础桌面操作的场景。"),
        system_prompt=(
            "你是电脑操作子智能体，负责在 Windows 桌面上完成用户要求的操作。"
            "先调用 screen_info 了解屏幕和当前窗口；需要观察界面时用 screen_capture，"
            "截图会保存到工作空间。可以使用 mouse_*、keyboard_*、focus_window、open_app；"
            "不确定坐标时可先截图并由用户确认。禁止删除个人文件、修改密码、付款、"
            "关机或关闭未保存的文档，除非用户明确要求并再次确认。每次操作后用简洁中文"
            "说明做了什么、结果如何。工作空间路径：" + workspace_path +
            "；临时 Python 脚本写入 temp/scripts/，截图在 temp/screenshots/。"
            "需要向用户展示截图时，在最终回答中包含 ![截图](截图绝对路径)。"),
        tools=cs_tools,
        max_turns=10,
    ))
    specs.append(SubagentSpec(
        name="finder",
        description=(
            "用 Everything 1.5 在本地磁盘查找文件和文件夹，支持文件名、扩展名、"
            "content:\"关键词\" 内容搜索；适合找项目文件、文档、图片和旧资料。"),
        system_prompt=(
            "你是本地文件检索子智能体。优先调用 search_files，使用 Everything 语法："
            "文件名、ext:pdf、content:\"关键词\" 等。需要时可读取文本文件确认内容。"
            "只做只读检索，不修改或删除任何文件。最终用简洁中文列出最相关的文件路径，"
            "并说明匹配原因；结果很多时按目录/类型归类。"),
        tools=tools,
        max_turns=6,
    ))
    return specs
