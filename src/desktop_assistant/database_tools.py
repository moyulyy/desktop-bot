# -*- coding: utf-8 -*-
"""把本地知识库、加密密码库与浏览器报告暴露成 Agent 工具。"""

from __future__ import annotations

import os
from typing import Optional

from .agent_framework import Tool, ToolResult
from .database import CATEGORIES, KnowledgeBase
from .password_vault import PasswordVault
from .reports import build_literature_tool, build_report_tool


def _fmt(record: dict) -> str:
    title = record.get("title") or record.get("path") or ""
    path = record.get("full_path") or record.get("path") or ""
    lines = [f"### {title}", f"- 类型：{record.get('category')} / {record.get('kind')}",
             f"- 路径：{path}"]
    if record.get("description"):
        lines.append(f"- 描述：{record['description']}")
    if record.get("tags"):
        lines.append(f"- 标签：{record['tags']}")
    if record.get("snippet"):
        lines.append(f"- 摘要：{record['snippet']}")
    return "\n".join(lines)


def build_database_tools(root: str, base_dir: str = ".",
                         read_only: bool = False, stop_event=None,
                         reports_dir: Optional[str] = None) -> list[Tool]:
    kb = KnowledgeBase(root)
    vault = PasswordVault(root)
    base_dir = os.path.abspath(base_dir or ".")
    reports_dir = reports_dir or os.path.join(os.path.dirname(kb.root),
                                              "temp", "reports")

    def resolve(path: str) -> str:
        path = os.path.expandvars(os.path.expanduser(str(path or "")))
        return path if os.path.isabs(path) else os.path.join(base_dir, path)

    def guarded(fn):
        def wrapper(args: dict) -> ToolResult:
            if stop_event is not None and stop_event.is_set():
                return ToolResult("已停止。", is_error=True)
            try:
                return fn(args)
            except (OSError, ValueError, RuntimeError) as exc:
                return ToolResult(str(exc), is_error=True)
        return wrapper

    @guarded
    def stats(_args):
        data = kb.stats()
        lines = [f"知识库：{data['root']}", f"索引：{data['db']}",
                 f"条目：{data['total']}（FTS={'on' if data['fts'] else 'off'}）"]
        for category, count in sorted(data["categories"].items()):
            lines.append(f"- {category}: {count}")
        return ToolResult("\n".join(lines))

    @guarded
    def search(args):
        query = str(args.get("query") or "").strip()
        if not query:
            return ToolResult("缺少 query。", is_error=True)
        results = kb.search(query, str(args.get("category") or ""),
                            int(args.get("limit", 10) or 10))
        if not results:
            return ToolResult(f"知识库中没有找到「{query}」。")
        return ToolResult("\n\n".join(_fmt(record) for record in results))

    @guarded
    def list_items(args):
        category = str(args.get("category") or "all")
        records = kb.list_category(category, int(args.get("limit", 50) or 50))
        if not records:
            return ToolResult("该分类还没有条目。")
        return ToolResult("\n\n".join(_fmt(record) for record in records))

    @guarded
    def inspect(args):
        identifier = str(args.get("identifier") or "").strip()
        record = kb.get(identifier)
        if not record:
            return ToolResult(f"没有找到：{identifier}", is_error=True)
        text = f"{_fmt(record)}\n- 大小：{record.get('size', 0)} B"
        excerpt = (record.get("text") or "").strip()
        if excerpt:
            text += "\n\n```text\n" + excerpt[:2000] + "\n```"
        return ToolResult(text)

    @guarded
    def add_file(args):
        path = resolve(str(args.get("path") or ""))
        record = kb.add_file(
            path,
            category=str(args.get("category") or ""),
            title=str(args.get("title") or ""),
            description=str(args.get("description") or ""),
            tags=str(args.get("tags") or ""),
        )
        return ToolResult("已整理入库：\n" + _fmt(record))

    @guarded
    def add_url(args):
        record = kb.add_url(str(args.get("url") or ""),
                            str(args.get("title") or ""),
                            str(args.get("description") or ""),
                            str(args.get("tags") or ""))
        return ToolResult("已记录 URL：\n" + _fmt(record))

    @guarded
    def add_code(args):
        record = kb.add_code(
            str(args.get("title") or "代码片段"),
            str(args.get("content") or ""),
            str(args.get("language") or "python"),
            str(args.get("description") or ""),
            str(args.get("tags") or ""))
        return ToolResult("已保存代码片段：\n" + _fmt(record))

    @guarded
    def add_note(args):
        record = kb.add_note(str(args.get("title") or "未命名笔记"),
                             str(args.get("content") or ""),
                             str(args.get("tags") or ""))
        return ToolResult("已写入 Markdown 笔记：\n" + _fmt(record))

    @guarded
    def reindex(_args):
        result = kb.reindex()
        return ToolResult(f"索引完成：{result['reindexed']} 个文件；"
                          f"当前 {result['stats']['total']} 条。")

    @guarded
    def vault_add(args):
        entry = vault.add(str(args.get("service") or ""),
                          str(args.get("username") or ""),
                          str(args.get("password") or ""),
                          str(args.get("url") or ""),
                          str(args.get("notes") or ""))
        return ToolResult(f"已加密保存密码条目：{entry['service']} / "
                          f"{entry.get('username', '')}（DPAPI）")

    @guarded
    def vault_list(_args):
        entries = vault.list()
        if not entries:
            return ToolResult("密码保险库为空。")
        return ToolResult("\n".join(
            f"- {item['service']} / {item.get('username', '')} "
            f"{item.get('url', '')}" for item in entries))

    @guarded
    def vault_get(args):
        entry = vault.get(str(args.get("identifier") or ""))
        if not entry:
            return ToolResult("没有找到密码条目。", is_error=True)
        return ToolResult(
            f"service: {entry['service']}\nusername: {entry.get('username', '')}\n"
            f"password: {entry.get('password', '')}\nurl: {entry.get('url', '')}\n"
            f"notes: {entry.get('notes', '')}\n\n"
            "密码已显示在当前会话中，请注意不要截图或分享。")

    tools = [
        Tool("database_stats", "查看本地知识库路径、条目数与分类统计。",
             {"type": "object", "properties": {}}, stats),
        Tool("database_search",
             "检索本地知识库中的文件、URL、Markdown 笔记和脚本，返回路径与摘要。",
             {"type": "object", "properties": {
                 "query": {"type": "string"},
                 "category": {"type": "string", "enum": ["all", *CATEGORIES]},
                 "limit": {"type": "integer"}},
              "required": ["query"]}, search),
        Tool("database_list", "列出知识库某个分类的条目；不传 category 列出全部。",
             {"type": "object", "properties": {
                 "category": {"type": "string", "enum": ["all", *CATEGORIES]},
                 "limit": {"type": "integer"}}}, list_items),
        Tool("database_inspect", "查看知识库条目的元数据和文本摘要。",
             {"type": "object", "properties": {
                 "identifier": {"type": "string", "description": "id、相对路径或标题"}},
              "required": ["identifier"]}, inspect),
        build_report_tool(reports_dir),
        build_literature_tool(reports_dir),
    ]
    if read_only:
        return tools
    tools.extend([
        Tool("database_add_file",
             "把文件复制到知识库并建立描述/标签索引；PDF、图片、Markdown、脚本会自动分类。",
             {"type": "object", "properties": {
                 "path": {"type": "string"},
                 "category": {"type": "string", "enum": list(CATEGORIES)},
                 "title": {"type": "string"}, "description": {"type": "string"},
                 "tags": {"type": "string"}},
              "required": ["path"]}, add_file),
        Tool("database_add_url", "把网页 URL 记录到知识库 urls 分类。",
             {"type": "object", "properties": {
                 "url": {"type": "string"}, "title": {"type": "string"},
                 "description": {"type": "string"}, "tags": {"type": "string"}},
              "required": ["url"]}, add_url),
        Tool("database_add_code", "把带语言、描述和标签的代码片段保存到知识库 code 分类，并建立检索索引。",
             {"type": "object", "properties": {
                 "title": {"type": "string"}, "content": {"type": "string"},
                 "language": {"type": "string", "description": "如 python / javascript / sql / powershell"},
                 "description": {"type": "string"}, "tags": {"type": "string"}},
              "required": ["title", "content"]}, add_code),
        Tool("database_note", "在知识库 markdown 分类创建一篇可检索笔记。",
             {"type": "object", "properties": {
                 "title": {"type": "string"}, "content": {"type": "string"},
                 "tags": {"type": "string"}},
              "required": ["title", "content"]}, add_note),
        Tool("database_reindex", "重新扫描知识库分类目录并更新 FTS 索引与 INDEX.md。",
             {"type": "object", "properties": {}}, reindex),
        Tool("vault_add", "用 Windows DPAPI 加密保存一条密码；明文不会写入磁盘。",
             {"type": "object", "properties": {
                 "service": {"type": "string"}, "username": {"type": "string"},
                 "password": {"type": "string"}, "url": {"type": "string"},
                 "notes": {"type": "string"}},
              "required": ["service", "password"]}, vault_add),
        Tool("vault_list", "列出密码条目的服务名、用户名和 URL，不返回密码。",
             {"type": "object", "properties": {}}, vault_list),
        Tool("vault_get", "读取一条密码；结果会显示在会话中，请谨慎使用。",
             {"type": "object", "properties": {
                 "identifier": {"type": "string", "description": "服务名或条目 id"}},
              "required": ["identifier"]}, vault_get),
    ])
    return tools
