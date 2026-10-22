# -*- coding: utf-8 -*-
"""本地文件检索子智能体：用 Everything 1.5 在磁盘上查找文件。"""

from __future__ import annotations

import os

try:  # 包内导入
    from .agent_framework import SubagentSpec, default_subagents, default_tools
    from .everything_search import build_search_tool
    from .computer_tools import build_computer_tools
    from .database_tools import build_database_tools
    from .rag import build_rag_tools
except ImportError:  # 兼容直接以脚本方式运行
    from agent_framework import SubagentSpec, default_subagents, default_tools
    from everything_search import build_search_tool
    from computer_tools import build_computer_tools
    from database_tools import build_database_tools
    from rag import build_rag_tools


def available_subagents(cwd: str, workspace: str | None = None,
                        database: str | None = None,
                        stop_event=None, embedding_model: str | None = None,
                        embedding_cache: str | None = None) -> list[SubagentSpec]:
    """内置角色 + finder（Everything）+ computer（电脑操作）+ librarian（知识库整理）。"""
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
    db_path = os.path.abspath(os.path.expanduser(
        database or os.path.join(cwd, "database")))
    db_tools = build_database_tools(db_path, base_dir=cwd,
                                    stop_event=stop_event)
    for name in ("read_file", "list_directory", "get_current_time"):
        tool = base.get(name)
        if tool is not None:
            db_tools.append(tool)
    rag_tools = build_rag_tools(
        db_path, base_dir=cwd, read_only=False, stop_event=stop_event,
        embedding_model=embedding_model or "intfloat/multilingual-e5-small",
        embedding_cache=embedding_cache)
    for name in ("read_file", "list_directory", "get_current_time"):
        tool = base.get(name)
        if tool is not None:
            rag_tools.append(tool)
    specs.append(SubagentSpec(
        name="rag",
        description=(
            "论文/HTML 知识库 RAG：解析 PDF 正文、表格和内嵌图片，按页分块，"
            "建立本地 embedding 与混合检索，回答时给出文件/页码/URL 来源。"),
        system_prompt=(
            "你是论文与网页知识库检索助手。检索优先用 rag_search，不要凭记忆编造；"
            "回答必须给出标题、文件路径或 URL，PDF 还要给页码。中文和英文论文都要支持。"
            "没有足够证据时明确说“知识库中没有足够信息”。需要新增资料时使用 "
            "rag_ingest_pdf / rag_ingest_html / rag_ingest_urls；URL 只在导入时抓取一次，"
            "不要重复爬取。图表可先查 image_describe 的图注和 OCR，再结合上下文解释；"
            "文献报告必须使用 rag_literature_report/literature_report 的结构化字段："
            "每张图必须有 Figure/图号图注，不能把整页 PDF 当作 Fig；无法核验的图片不要放。"
            "证据不足时明确说明，不要补写推断值。"),
        tools=rag_tools,
        max_turns=12,
    ))
    specs.append(SubagentSpec(
        name="librarian",
        description=(
            "整理本地知识库：把文件、网页 URL、图片/截图、Markdown 和脚本"
            "分类入库，补全标题/描述/标签，并维护路径索引与全文检索。"),
        system_prompt=(
            "你是本地知识库整理员。工作目录是用户的 database 知识库："
            + db_path + "。用户给出文件或 URL 时，先判断类型，再调用 "
            "database_add_file / database_add_url 入库，并补全简洁标题、"
            "中文描述和 2-5 个标签；不确定分类时先 database_inspect 或读取内容，"
            "不要臆造。需要找资料时用 database_search；整理完用 database_reindex "
            "更新索引。密码只能用 vault_add 加密保存，绝不能写入 Markdown、"
            "描述或标签；不要在最终回答中回显密码。输出时列出类型、路径、"
            "描述和标签，复杂清单可用 open_report 在浏览器展示。"),
        tools=db_tools,
        max_turns=12,
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
