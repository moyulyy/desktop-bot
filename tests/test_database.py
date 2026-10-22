# -*- coding: utf-8 -*-
import os

import pytest

from desktop_assistant.database import KnowledgeBase, infer_category
from desktop_assistant.reports import build_report_html, markdown_to_html


def test_knowledge_base_add_search_and_catalog(tmp_path):
    kb = KnowledgeBase(str(tmp_path / "database"))
    note = tmp_path / "项目笔记.md"
    note.write_text("# 项目笔记\n\n这是关于预算和 Q3 复盘的内容。", encoding="utf-8")
    record = kb.add_file(str(note), description="Q3 预算复盘")
    assert record["category"] == "markdown"
    assert os.path.isfile(record["full_path"])
    assert kb.search("预算")[0]["title"] == "项目笔记"
    assert kb.search("Q3 复盘")
    url = kb.add_url("https://example.com/report", "Q3 报表",
                     "季度报表链接", "report,q3")
    assert url["category"] == "urls"
    assert kb.search("季度报表")[0]["path"].startswith("https://")
    assert str(tmp_path / "database" / "catalog.json") in kb.export_catalog()["catalog"]
    assert kb.stats()["total"] == 2
    assert infer_category(str(note)) == "markdown"


def test_reindex_and_text_extraction(tmp_path):
    kb = KnowledgeBase(str(tmp_path / "database"))
    script = kb.root and os.path.join(kb.root, "scripts", "hello.py")
    os.makedirs(os.path.dirname(script), exist_ok=True)
    with open(script, "w", encoding="utf-8") as handle:
        handle.write("print('hello database')\n")
    result = kb.reindex()
    assert result["stats"]["total"] >= 1
    assert kb.search("hello database")


def test_report_rendering():
    page = build_report_html("测试报告", "# 标题\n\n| 名称 | 值 |\n|---|---|\n| A | 1 |")
    assert "<h1>标题</h1>" in page
    assert "<table>" in page
    assert "测试报告" in page
    assert "strong" in markdown_to_html("**bold**")


@pytest.mark.skipif(os.name != "nt", reason="DPAPI is Windows-only")
def test_password_vault_round_trip(tmp_path):
    from desktop_assistant.password_vault import PasswordVault

    vault = PasswordVault(str(tmp_path / "database"))
    saved = vault.add("example.com", "alice", "s3cret", "https://example.com")
    assert saved["service"] == "example.com"
    entries = vault.list()
    assert entries and "password" not in entries[0]
    got = vault.get("example.com")
    assert got["password"] == "s3cret"
    assert vault.delete("example.com")
    assert vault.get("example.com") is None


def test_database_agent_tools(tmp_path):
    from desktop_assistant.database_tools import build_database_tools

    root = tmp_path / "db"
    tools = {tool.name: tool for tool in build_database_tools(
        str(root), base_dir=str(tmp_path), read_only=False,
        reports_dir=str(tmp_path / "reports"))}
    note = tmp_path / "note.md"
    note.write_text("知识库测试内容：火星计划。", encoding="utf-8")
    added = tools["database_add_file"].call({
        "path": "note.md", "description": "火星计划笔记", "tags": "mars"})
    assert not added.is_error
    found = tools["database_search"].call({"query": "火星计划"})
    assert not found.is_error
    assert "火星计划" in found.content
    url = tools["database_add_url"].call({
        "url": "https://example.com/mars", "title": "Mars URL"})
    assert not url.is_error
    assert "Mars URL" in tools["database_list"].call({"category": "urls"}).content
    assert "open_report" in tools
    assert "vault_add" in tools


def test_write_report_to_temp(tmp_path):
    from desktop_assistant.reports import write_report

    path = write_report(str(tmp_path / "reports"), "复杂结果", "# 标题\n\n内容")
    assert os.path.isfile(path)
    page = open(path, encoding="utf-8").read()
    assert "复杂结果" in page
    assert "<h1>标题</h1>" in page


def test_report_includes_images_links_code_and_references(tmp_path):
    image = tmp_path / "figure.png"
    image.write_bytes(b"not-a-real-png")
    content = (
        "# 报告\n\n"
        f"![预算图]({image.as_posix()})\n\n"
        "参考：[论文](https://example.com/paper)\n\n"
        "```python\nprint('hello')\n```\n\n"
        "裸链接：https://example.com/raw\n"
    )
    page = build_report_html("复杂结果", content, "测试")
    assert "<figure>" in page
    assert 'src="file:' in page
    assert 'href="https://example.com/paper"' in page
    assert 'href="https://example.com/raw"' in page
    assert "<pre><code>" in page
    assert "参考链接与图片" in page


def test_code_snippet_category_and_tool(tmp_path):
    from desktop_assistant.database_tools import build_database_tools

    kb = KnowledgeBase(str(tmp_path / "database"))
    record = kb.add_code(
        "读取 CSV", "import csv\nwith open('x.csv') as f:\n    print(list(csv.reader(f)))",
        language="python", description="常用 CSV 读取代码", tags="csv,python")
    assert record["category"] == "code"
    assert record["full_path"].endswith(".py")
    assert kb.search("CSV 读取")
    tools = {t.name: t for t in build_database_tools(
        str(tmp_path / "database"), base_dir=str(tmp_path), read_only=False)}
    added = tools["database_add_code"].call({
        "title": "SQL 查询", "content": "SELECT * FROM papers;",
        "language": "sql", "description": "查询论文", "tags": "sql"})
    assert not added.is_error
    assert "SQL 查询" in tools["database_list"].call({"category": "code"}).content
