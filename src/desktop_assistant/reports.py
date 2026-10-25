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
from urllib.parse import unquote, urlparse

from .agent_framework import Tool, ToolResult


_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_PDF_RE = re.compile(r"((?:[A-Za-z]:[\/]|database[\/]pdfs[\/])[^<>|\n]*?\.pdf)", re.I)


def _local_path(value: str) -> str:
    """把本地路径或（可能被重复编码的）file URI 还原成绝对路径。"""
    raw = (value or "").strip()
    if raw.lower().startswith("file:"):
        parsed = urlparse(raw)
        path = unquote(parsed.path)
        # 兼容 file:///e:/C%3A/... 这类历史错误 URI
        match = re.match(r"^/[A-Za-z]:[\/]([A-Za-z]:[\/].*)$", path)
        if match:
            path = match.group(1)
        elif os.name == "nt" and re.match(r"^/[A-Za-z]:", path):
            path = path[1:]
        return os.path.abspath(os.path.expanduser(path))
    return os.path.abspath(os.path.expanduser(raw))


def _safe_href(value: str) -> str:
    value = (value or "").strip()
    if value.lower().startswith("file:"):
        return Path(_local_path(value)).as_uri()
    return value


def _image_src(src: str) -> str:
    src = (src or "").strip()
    if re.match(r"^https?:", src, re.I):
        return src
    if src.lower().startswith("file:"):
        return _safe_href(src)
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


def review_literature_items(items: list[dict]) -> tuple[list[dict], list[str]]:
    """报告生成前的硬性审核：路径、图注、图号缺失的图片不允许进入成品。"""
    clean, warnings = [], []
    for index, item in enumerate(items or [], 1):
        if not isinstance(item, dict):
            warnings.append(f"第 {index} 条不是有效对象，已跳过。")
            continue
        title = str(item.get("title") or "").strip()
        pdf = _local_path(str(item.get("pdf") or ""))
        if not title:
            warnings.append(f"第 {index} 条缺少文献标题，已跳过。")
            continue
        if not pdf or not os.path.isfile(pdf):
            warnings.append(f"「{title}」找不到 PDF 文件：{pdf or '未填写'}。")
        images, kept = [], []
        for image in item.get("images") or []:
            if isinstance(image, str):
                image = {"path": image}
            if not isinstance(image, dict):
                continue
            path = _local_path(str(image.get("path") or ""))
            caption = str(image.get("caption") or image.get("alt") or "").strip()
            if not path or not os.path.isfile(path):
                warnings.append(f"「{title}」图片不存在，已从报告移除。")
                continue
            if not re.search(r"(fig(?:ure)?|scheme|图\s*\d|图[A-Za-z0-9])",
                             caption, re.I):
                warnings.append(f"「{title}」图片缺少 Figure/图号，已从报告移除：{os.path.basename(path)}")
                continue
            if re.search(r"(?:^|[\/])(?:full|page[-_]?\d+|whole[-_]?page)",
                         path, re.I) and not re.search(r"crop|fig_", path, re.I):
                warnings.append(f"「{title}」疑似整页截图而非 Fig，已从报告移除。")
                continue
            kept.append({**image, "path": path, "caption": caption})
            images = kept
        if not images:
            warnings.append(f"「{title}」没有可核验的 Fig 图片，卡片将只保留文字与 PDF 链接。")
        cleaned = dict(item)
        cleaned["pdf"] = pdf
        cleaned["images"] = images
        clean.append(cleaned)
    return clean, warnings


def extract_pdf_paths(text: str) -> list[tuple[str, str, str]]:
    found, seen = [], set()
    for match in _PDF_RE.finditer(text or ""):
        raw = match.group(1).strip().strip("`")
        if raw.lower() in seen:
            continue
        seen.add(raw.lower())
        path = _local_path(raw)
        title = Path(path).stem
        found.append((title, Path(path).as_uri(), path))
    return found


def _pdf_section(text: str) -> str:
    refs = extract_pdf_paths(text)
    if not refs:
        return ""
    html_parts = ['<section class="pdf-list"><h2>PDF 文献</h2>']
    for title, url, path in refs:
        html_parts.append(
            f'<details open><summary>{html.escape(title)}</summary>'
            f'<p><a href="{html.escape(url, quote=True)}" target="_blank" '
            f'rel="noopener">打开 PDF 原文</a></p>'
            f'<p class="pdf-path">绝对路径：<code>{html.escape(path)}</code></p>'
            f'</details>')
    html_parts.append("</section>")
    return "".join(html_parts)


def _inline(text: str) -> str:
    tokens: list[str] = []

    def stash(markup: str) -> str:
        tokens.append(markup)
        return f"\x00{len(tokens) - 1}\x00"

    def image(match):
        alt = html.escape(match.group(1) or "图片", quote=True)
        src = html.escape(_image_src(match.group(2)), quote=True)
        caption = html.escape(match.group(1) or "图片")
        return stash(f'<figure><img class="zoomable" src="{src}" alt="{alt}" '
                     f'loading="lazy" title="点击全屏查看" '
                     f'onclick="openLightbox(this.src,this.alt)">'
                     f'<figcaption>{caption}</figcaption></figure>')

    def link(match):
        label = html.escape(match.group(1) or match.group(2))
        href = html.escape(_safe_href(match.group(2)), quote=True)
        return stash(f'<a href="{href}" target="_blank" rel="noopener">{label}</a>')

    def pdf_link(match):
        raw = match.group(1)
        path = _local_path(raw)
        href = html.escape(Path(path).as_uri(), quote=True)
        label = html.escape(Path(path).name)
        return stash(f'<a href="{href}" target="_blank" rel="noopener">{label}</a>')

    # 先保护 Markdown 图片/链接，避免 PDF 路径正则匹配到链接 URL 内部。
    text = _IMAGE_RE.sub(image, text or "")
    text = _LINK_RE.sub(link, text)
    text = _PDF_RE.sub(pdf_link, text)
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
:root { color-scheme: light; --bg:#f4f6fa; --panel:#ffffff; --text:#1a2233;
        --muted:#64748b; --accent:#2563eb; --line:#dbe3ef; }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--text);
       font:15px/1.7 Inter, "Segoe UI", "Microsoft YaHei", sans-serif; }
main { max-width: 1080px; margin: 0 auto; padding: 48px 28px 80px; }
header { border-bottom:1px solid var(--line); margin-bottom:32px; padding-bottom:20px; }
h1 { font-size:34px; line-height:1.2; margin:0 0 10px; letter-spacing:-.02em; }
.meta { color:var(--muted); font-size:13px; }
h2 { margin-top:38px; padding-bottom:8px; border-bottom:1px solid var(--line); }
h3 { margin-top:28px; color:#1e3a5f; }
p, li { color:#334155; }
a { color:var(--accent); }
code { background:#eef3fb; border:1px solid #d6e0f0; border-radius:5px;
       padding:1px 5px; font:13px/1.5 "Cascadia Code", Consolas, monospace; }
pre { background:#f6f8fc; border:1px solid var(--line); border-radius:12px;
      padding:18px; overflow:auto; }
pre code { background:none; border:0; padding:0; color:#25324a; }
table { width:100%; border-collapse:collapse; margin:18px 0; }
th, td { border:1px solid var(--line); padding:9px 12px; text-align:left; }
th { background:#eef3fb; color:#25324a; }
tr:nth-child(even) td { background:#f8faff; }
blockquote { border-left:3px solid var(--accent); margin:16px 0;
             padding:2px 16px; color:var(--muted); }
img { display:block; width:auto; height:auto; max-width:100%; max-height:70vh;
      object-fit:contain; margin:0 auto; border-radius:10px; background:#fff; }
figure { margin:22px 0; padding:12px; background:#ffffff;
         border:1px solid var(--line); border-radius:12px; color:#1a2233; }
figcaption { color:#46536a; font-size:13px; margin-top:8px; text-align:center; }
.pdf-list { margin:24px 0 34px; padding:16px 20px; background:#ffffff;
            border:1px solid var(--line); border-radius:12px; }
.pdf-list h2 { margin:0 0 10px; border:0; font-size:20px; }
.pdf-list details { border-top:1px solid var(--line); padding:9px 0; }
.pdf-list summary { cursor:pointer; color:#25324a; font-weight:600; }
.pdf-list p { margin:8px 0 0; }
.references { margin-top:42px; padding:18px 22px; background:#ffffff;
              border:1px solid var(--line); border-radius:12px; }
.references h2 { margin-top:0; font-size:20px; }
.references li { margin:6px 0; overflow-wrap:anywhere; }
footer { margin-top:52px; color:var(--muted); font-size:12px; }
#lightbox { position:fixed; inset:0; z-index:9999; display:none; align-items:center;
            justify-content:center; background:rgba(10,18,32,.88); padding:24px; }
#lightbox.open { display:flex; }
#lightbox img { max-width:96vw; max-height:92vh; width:auto; height:auto;
                object-fit:contain; background:#fff; border-radius:8px; cursor:grab;
                transform-origin:center center; touch-action:none; }
#lightbox img.dragging { cursor:grabbing; }
#lightbox .close { position:absolute; top:18px; right:22px; color:#fff;
                   font-size:32px; line-height:1; cursor:pointer; border:0;
                   background:transparent; }
@media print { body { background:white; color:#111; } main { max-width:none; }
  a { color:#0645ad; } code { background:#f2f4f8; } }
"""


LIGHTBOX_HTML = r'''<div id="lightbox" onclick="closeLightbox(event)"><button class="close" onclick="closeLightbox(event)">&times;</button><img id="lightbox-img" alt=""></div>
<script>
(function () {
  const box = document.getElementById('lightbox');
  const img = document.getElementById('lightbox-img');
  let scale = 1, tx = 0, ty = 0, dragging = false, sx = 0, sy = 0, ox = 0, oy = 0;
  function apply() { img.style.transform = `translate(${tx}px, ${ty}px) scale(${scale})`; }
  window.openLightbox = function (src, alt) {
    img.src = src; img.alt = alt || ''; scale = 1; tx = 0; ty = 0; apply();
    box.classList.add('open');
  };
  window.closeLightbox = function (e) {
    if (e) e.stopPropagation();
    box.classList.remove('open'); scale = 1; tx = 0; ty = 0; apply();
  };
  img.addEventListener('click', e => e.stopPropagation());
  img.addEventListener('wheel', e => {
    e.preventDefault();
    scale = Math.max(0.5, Math.min(8, scale * (e.deltaY < 0 ? 1.15 : 1 / 1.15)));
    apply();
  }, { passive: false });
  img.addEventListener('pointerdown', e => {
    e.preventDefault(); dragging = true; sx = e.clientX; sy = e.clientY;
    ox = tx; oy = ty; img.classList.add('dragging'); img.setPointerCapture(e.pointerId);
  });
  img.addEventListener('pointermove', e => {
    if (!dragging) return;
    tx = ox + e.clientX - sx; ty = oy + e.clientY - sy; apply();
  });
  img.addEventListener('pointerup', () => { dragging = false; img.classList.remove('dragging'); });
  img.addEventListener('dblclick', () => { scale = 1; tx = 0; ty = 0; apply(); });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') closeLightbox();
    if (e.key === '+' || e.key === '=') { scale = Math.min(8, scale * 1.2); apply(); }
    if (e.key === '-') { scale = Math.max(0.5, scale / 1.2); apply(); }
  });
})();
</script>'''

def build_report_html(title: str, content: str, subtitle: str = "") -> str:
    body = markdown_to_html(content)
    pdf_section = _pdf_section(content)
    body = pdf_section + body
    references = extract_references(content)
    if references:
        body += '<section class="references"><h2>参考链接与图片</h2><ul>'
        for label, url in references:
            safe_url = html.escape(_safe_href(url), quote=True)
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
</header>{body}<footer>由 Desktop Bot 生成 · 本地报告</footer>
{LIGHTBOX_HTML}
</main></body></html>"""


_pending_report = None


def write_report(directory: str, title: str, content: str,
                 subtitle: str = "") -> str:
    global _pending_report
    os.makedirs(directory, exist_ok=True)
    safe = re.sub(r"[^\w\-.\u4e00-\u9fff]+", "-", title or "report").strip("-")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
    path = os.path.join(directory, f"{safe[:60] or 'report'}-{stamp}.html")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(build_report_html(title, content, subtitle))
    _pending_report = path
    return path


def consume_pending_report() -> str:
    global _pending_report
    path, _pending_report = _pending_report, None
    return path or ""


def open_pending_report() -> str:
    path = consume_pending_report()
    if path and os.path.exists(path):
        webbrowser.open(Path(path).resolve().as_uri())
    return path


def open_report(directory: str, title: str, content: str,
                subtitle: str = "") -> str:
    global _pending_report
    path = write_report(directory, title, content, subtitle)
    _pending_report = None
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
            path = write_report(directory, title, content,
                                "由智能体生成；任务完成后在浏览器打开")
        except OSError as exc:
            return ToolResult(f"无法生成报告：{exc}", is_error=True)
        return ToolResult(f"报告已生成，任务完成后会自动在浏览器打开：{path}")
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


def build_literature_html(title: str, items: list[dict], subtitle: str = "") -> str:
    """结构化文献报告：PDF 链接、绝对路径和图片由字段直接生成，不依赖模型拼 Markdown。"""
    items, warnings = review_literature_items(items)
    cards = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        item_title = html.escape(str(item.get("title") or "未命名文献"))
        pdf = _local_path(str(item.get("pdf") or ""))
        pdf_uri = Path(pdf).as_uri() if item.get("pdf") else ""
        page = html.escape(str(item.get("page") or ""))
        summary = html.escape(str(item.get("summary") or item.get("notes") or ""))
        meta = html.escape(str(item.get("meta") or ""))
        links = []
        if pdf_uri:
            links.append(
                f'<a class="pdf-link" href="{html.escape(pdf_uri, quote=True)}" '
                f'target="_blank" rel="noopener">打开 PDF 原文</a>')
            links.append(f'<span class="path">PDF 绝对路径：<code>{html.escape(pdf)}</code></span>')
        images = []
        for image in item.get("images") or []:
            if isinstance(image, str):
                image = {"path": image}
            if not isinstance(image, dict):
                continue
            path = str(image.get("path") or "").strip()
            if not path:
                continue
            src = _image_src(path)
            absolute = _local_path(path)
            caption = html.escape(str(image.get("caption") or image.get("alt") or "文献图片"))
            images.append(
                f'<figure><img class="zoomable" src="{html.escape(src, quote=True)}" '
                f'alt="{caption}" title="点击全屏查看" loading="lazy" '
                f'onclick="openLightbox(this.src,this.alt)">'
                f'<figcaption>{caption}　<span class="img-path">图片绝对路径：'
                f'<code>{html.escape(absolute)}</code></span></figcaption></figure>')
        page_html = f"<span class='page'>{page}</span>" if page else ""
        meta_html = f"<p class='meta'>{meta}</p>" if meta else ""
        links_html = f"<p class='links'>{chr(32).join(links)}</p>" if links else ""
        summary_html = f"<p>{summary}</p>" if summary else ""
        cards.append(
            f'<article class="lit-card"><h2>{item_title}{page_html}</h2>'
            f'{meta_html}{links_html}{summary_html}'
            f'{"".join(images)}</article>')
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    body = "".join(cards) or "<p>没有可展示的文献。</p>"
    if warnings:
        body = ('<section class="review"><h2>审核提示</h2><ul>'
                + "".join(f"<li>{html.escape(w)}</li>" for w in warnings)
                + "</ul><p>未通过核验的图片不会出现在卡片中。</p></section>" + body)
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title><style>{STYLE}
.lit-card {{ max-width:1080px; margin:0 auto 26px; background:#fff; border:1px solid var(--line);
            border-radius:14px; padding:20px 24px 18px; box-shadow:0 2px 10px rgba(30,50,90,.06); }}
.lit-card h2 {{ margin:0 0 8px; display:flex; justify-content:space-between; gap:16px; }}
.lit-card .page {{ color:var(--accent); font-size:14px; font-weight:600; white-space:nowrap; }}
.lit-card .meta {{ color:var(--muted); font-size:13px; margin:0 0 10px; }}
.lit-card .links {{ background:#f7f9fd; border-radius:8px; padding:9px 11px; font-size:12px;
                    line-height:1.8; overflow-wrap:anywhere; }}
.lit-card .path, .lit-card .img-path {{ color:var(--muted); margin-left:10px; }}
.lit-card .img-path {{ display:block; font-size:11px; margin-top:5px; }}
.lit-card .zoomable {{ cursor:zoom-in; }}
.review {{ max-width:1080px; margin:0 auto 20px; padding:14px 18px; background:#fff7ed;
           border:1px solid #fed7aa; border-radius:12px; color:#7c2d12; }}
.review h2 {{ margin:0 0 8px; font-size:17px; border:0; }}
.review li {{ color:#7c2d12; }}
</style></head><body><main><header><h1>{html.escape(title)}</h1>
<div class="meta">{html.escape(subtitle or '本地文献报告')} · {stamp}</div></header>
{body}<footer>由 Desktop Bot 生成 · 本地文献报告</footer>
{LIGHTBOX_HTML}
</main></body></html>"""


def write_literature_report(directory: str, title: str, items: list[dict],
                            subtitle: str = "") -> str:
    global _pending_report
    os.makedirs(directory, exist_ok=True)
    safe = re.sub(r"[^\w\-.\u4e00-\u9fff]+", "-", title or "literature").strip("-")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
    path = os.path.join(directory, f"{safe[:60] or 'literature'}-{stamp}.html")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(build_literature_html(title, items, subtitle))
    _pending_report = path
    return path


def build_literature_tool(directory: str) -> Tool:
    def handler(args: dict) -> ToolResult:
        title = str(args.get("title") or "文献检索报告").strip()
        items = args.get("items")
        if not isinstance(items, list) or not items:
            return ToolResult("items 不能为空。", is_error=True)
        try:
            path = write_literature_report(
                directory, title, items,
                "结构化文献检索报告；任务结束后在浏览器打开")
        except OSError as exc:
            return ToolResult(f"无法生成文献报告：{exc}", is_error=True)
        return ToolResult(f"文献报告已生成，任务完成后会自动在浏览器打开：{path}")

    schema = {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "pdf": {"type": "string", "description": "PDF 绝对路径"},
                        "page": {"type": "string"},
                        "meta": {"type": "string"},
                        "summary": {"type": "string"},
                        "images": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "path": {"type": "string", "description": "图片绝对路径"},
                                    "caption": {"type": "string"},
                                },
                                "required": ["path"],
                            },
                        },
                    },
                    "required": ["title", "pdf"],
                },
            },
        },
        "required": ["title", "items"],
    }
    return Tool(
        "literature_report",
        "用结构化字段生成文献检索 HTML 报告。每篇传 title、pdf（绝对路径）、page、summary、"
        "images（path/caption 数组）。工具会自动生成可点击的 PDF 超链接、绝对路径、"
        "浅色报告页和可点击全屏的文献图片，避免模型手写 Markdown 链接出错。",
        schema,
        handler,
    )
