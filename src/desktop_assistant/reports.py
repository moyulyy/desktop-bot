# -*- coding: utf-8 -*-
"""把复杂 Markdown 结果排版成独立 HTML，并在浏览器中打开。"""

from __future__ import annotations

import html
import os
import re
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .agent_framework import Tool, ToolResult


_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")


def _image_src(src: str) -> str:
    src = (src or "").strip()
    if re.match(r"^(?:https?|file):", src, re.I):
        return src
    expanded = os.path.abspath(os.path.expanduser(src))
    if os.path.exists(expanded) or re.match(r"^[A-Za-z]:[\/]", src) or os.path.isabs(src):
        return Path(expanded).as_uri()
    return src


def extract_references(text: str) -> list[tuple[str, str]]:
    found = []
    for match in _IMAGE_RE.finditer(text or ""):
        found.append((match.group(1) or "图片", match.group(2)))
    for match in _LINK_RE.finditer(text or ""):
        found.append((match.group(1) or match.group(2), match.group(2)))
    unique = []
    seen = set()
    for label, url in found:
        if url not in seen:
            seen.add(url)
            unique.append((label, url))
    return unique


def _inline(text: str) -> str:
    tokens: list[str] = []

    def stash(markup: str) -> str:
        tokens.append(markup)
        return f"\x00{len(tokens) - 1}\x00"

    def image(match):
        alt = html.escape(match.group(1) or "图片", quote=True)
        src = html.escape(_image_src(match.group(2)), quote=True)
        caption = html.escape(match.group(1) or "图片")
        return stash(f'<figure><img src="{src}" alt="{alt}" loading="lazy">'
                     f'<figcaption>{caption}</figcaption></figure>')

    def link(match):
        label = html.escape(match.group(1) or match.group(2))
        href = html.escape(match.group(2), quote=True)
        return stash(f'<a href="{href}" target="_blank" rel="noopener">{label}</a>')

    text = _IMAGE_RE.sub(image, text or "")
    text = _LINK_RE.sub(link, text)
    text = html.escape(text, quote=False)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    # 裸 URL 也变成可点击引用，避免报表只有纯文字地址。
    text = re.sub(
        r"(?<![\"'=])(https?://[^\s<]+)",
        lambda m: f'<a href="{html.escape(m.group(1), quote=True)}" '
                  f'target="_blank" rel="noopener">{m.group(1)}</a>',
        text)
    for index, markup in enumerate(tokens):
        text = text.replace(f"\x00{index}\x00", markup)
    return text


def _is_image_line(line: str) -> bool:
    return bool(re.fullmatch(r"\s*!\[[^\]]*\]\([^)]+\)\s*", line or ""))


def markdown_to_html(text: str) -> str:
    """轻量 Markdown 渲染；安装了 markdown 包时优先使用它。"""
    try:
        import markdown  # type: ignore
        return markdown.markdown(
            text, extensions=["fenced_code", "tables", "sane_lists", "nl2br"])
    except Exception:
        pass
    lines = (text or "").splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("```"):
            i += 1
            code = []
            while i < len(lines) and not lines[i].startswith("```"):
                code.append(lines[i])
                i += 1
            out.append("<pre><code>" + html.escape("\n".join(code)) + "</code></pre>")
            i += 1
            continue
        if line.startswith("#"):
            level = min(6, len(line) - len(line.lstrip("#")))
            out.append(f"<h{level}>{_inline(line[level:].strip())}</h{level}>")
            i += 1
            continue
        if "|" in line and i + 1 < len(lines) and re.match(
                r"^\s*\|?\s*:?-{3,}", lines[i + 1]):
            header = [cell.strip() for cell in line.strip().strip("|").split("|")]
            i += 2
            rows = []
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                rows.append([cell.strip() for cell in
                             lines[i].strip().strip("|").split("|")])
                i += 1
            out.append("<table><thead><tr>" + "".join(
                f"<th>{_inline(cell)}</th>" for cell in header
            ) + "</tr></thead><tbody>" + "".join(
                "<tr>" + "".join(f"<td>{_inline(cell)}</td>" for cell in row)
                + "</tr>" for row in rows) + "</tbody></table>")
            continue
        if re.match(r"^\s*[-*+]\s+", line):
            items = []
            while i < len(lines) and re.match(r"^\s*[-*+]\s+", lines[i]):
                items.append(re.sub(r"^\s*[-*+]\s+", "", lines[i]))
                i += 1
            out.append("<ul>" + "".join(f"<li>{_inline(item)}</li>"
                                        for item in items) + "</ul>")
            continue
        if not line.strip():
            i += 1
            continue
        if _is_image_line(line):
            out.append(_inline(line.strip()))
            i += 1
            continue
        para = []
        while i < len(lines) and lines[i].strip() and not lines[i].startswith(
                ("#", "```", "|")):
            para.append(lines[i])
            i += 1
        out.append("<p>" + _inline(" ".join(para)) + "</p>")
    return "\n".join(out)


STYLE = """
:root { color-scheme: dark; --bg:#0b0d12; --panel:#141822; --text:#e8edf7;
        --muted:#98a5ba; --accent:#66aaff; --line:#273044; }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--text);
       font:15px/1.7 Inter, "Segoe UI", "Microsoft YaHei", sans-serif; }
main { max-width: 1080px; margin: 0 auto; padding: 48px 28px 80px; }
header { border-bottom:1px solid var(--line); margin-bottom:32px; padding-bottom:20px; }
h1 { font-size:34px; line-height:1.2; margin:0 0 10px; letter-spacing:-.02em; }
.meta { color:var(--muted); font-size:13px; }
h2 { margin-top:38px; padding-bottom:8px; border-bottom:1px solid var(--line); }
h3 { margin-top:28px; color:#cfdcff; }
p, li { color:#d9e2f2; }
a { color:var(--accent); }
code { background:#202838; border:1px solid #303c52; border-radius:5px;
       padding:1px 5px; font:13px/1.5 "Cascadia Code", Consolas, monospace; }
pre { background:#101522; border:1px solid var(--line); border-radius:12px;
      padding:18px; overflow:auto; }
pre code { background:none; border:0; padding:0; color:#dce7ff; }
table { width:100%; border-collapse:collapse; margin:18px 0; }
th, td { border:1px solid var(--line); padding:9px 12px; text-align:left; }
th { background:#1b2231; color:#cdd9ee; }
tr:nth-child(even) td { background:#111722; }
blockquote { border-left:3px solid var(--accent); margin:16px 0;
             padding:2px 16px; color:var(--muted); }
img { max-width:100%; border-radius:10px; }
figure { margin:22px 0; padding:12px; background:#111722;
         border:1px solid var(--line); border-radius:12px; }
figcaption { color:var(--muted); font-size:13px; margin-top:8px; text-align:center; }
.references { margin-top:42px; padding:18px 22px; background:#111722;
              border:1px solid var(--line); border-radius:12px; }
.references h2 { margin-top:0; font-size:20px; }
.references li { margin:6px 0; overflow-wrap:anywhere; }
footer { margin-top:52px; color:var(--muted); font-size:12px; }
@media print { body { background:white; color:#111; } main { max-width:none; }
  a { color:#0645ad; } code { background:#f2f4f8; } }
"""


def build_report_html(title: str, content: str, subtitle: str = "") -> str:
    body = markdown_to_html(content)
    references = extract_references(content)
    if references:
        body += '<section class="references"><h2>参考链接与图片</h2><ul>'
        for label, url in references:
            safe_url = html.escape(url, quote=True)
            body += (f'<li><a href="{safe_url}" target="_blank" rel="noopener">'
                     f'{html.escape(label)}</a></li>')
        body += '</ul></section>'
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title><style>{STYLE}</style></head>
<body><main><header><h1>{html.escape(title)}</h1>
<div class="meta">{html.escape(subtitle or 'Desktop Bot 生成')} · {stamp}</div>
</header>{body}<footer>Generated by Desktop Bot · local report</footer>
</main></body></html>"""


def write_report(directory: str, title: str, content: str,
                 subtitle: str = "") -> str:
    os.makedirs(directory, exist_ok=True)
    safe = re.sub(r"[^\w\-.\u4e00-\u9fff]+", "-", title or "report").strip("-")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
    path = os.path.join(directory, f"{safe[:60] or 'report'}-{stamp}.html")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(build_report_html(title, content, subtitle))
    return path


def open_report(directory: str, title: str, content: str,
                subtitle: str = "") -> str:
    path = write_report(directory, title, content, subtitle)
    webbrowser.open(Path(path).resolve().as_uri())
    return path


def chat_markdown(messages: Iterable[dict]) -> str:
    lines = []
    for message in messages:
        role = message.get("role", "agent")
        text = message.get("text", "")
        if role == "user":
            lines.append(f"## 你\n\n{text}")
        elif role in ("agent", "error"):
            lines.append(f"## 助手\n\n{text}")
        elif role == "tool_group":
            lines.append("### 工具调用")
            for step in message.get("steps") or []:
                lines.append(f"- {step.get('label')} · {step.get('status')}")
            lines.append("")
        elif role == "choice":
            lines.append(f"## 选择：{text}")
        else:
            lines.append(text)
    return "\n\n".join(lines)


def build_report_tool(directory: str) -> Tool:
    def handler(args: dict) -> ToolResult:
        title = str(args.get("title") or "Desktop Bot 报告").strip()
        content = str(args.get("content") or "")
        if not content:
            return ToolResult("缺少 content。", is_error=True)
        try:
            path = open_report(directory, title, content,
                               "由智能体生成；可在浏览器中阅读、搜索或打印")
        except OSError as exc:
            return ToolResult(f"无法生成报告：{exc}", is_error=True)
        return ToolResult(f"报告已生成并在浏览器打开：{path}")
    return Tool(
        "open_report",
        "把长表格、复杂分析或大幅面结果排版成独立 HTML 并在浏览器中打开。"
        "适合内容较长、表格较宽、需要打印或并排阅读的场景。",
        {"type": "object", "properties": {
            "title": {"type": "string"},
            "content": {"type": "string", "description": "Markdown 内容"},
        }, "required": ["title", "content"]},
        handler,
    )
