# -*- coding: utf-8 -*-
"""本地知识库：分类目录 + SQLite FTS5 索引 + 可读目录。

设计上借鉴 gbrain 的「有来源、可纠正、可检索」思路，但保持纯 Python、离线、
零额外服务依赖。数据库默认位于项目下的 ``database/``，不进入 Git。
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse

# 分类目录。urls/passwords 是逻辑分类，不保存普通文件。
CATEGORIES = {
    "pdfs": "PDF 文档",
    "html": "HTML 网页",
    "urls": "URL 链接",
    "images": "图片",
    "screenshots": "屏幕截图",
    "passwords": "密码保险库",
    "markdown": "Markdown 笔记",
    "code": "代码片段",
    "scripts": "脚本",
    "inbox": "待整理",
}

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}
SCRIPT_EXTS = {".py", ".js", ".ts", ".ps1", ".bat", ".cmd", ".sh",
               ".rb", ".go", ".rs", ".java", ".cs", ".sql", ".css", ".vue",
             ".jsx", ".tsx", ".c", ".h", ".cpp", ".hpp", ".php", ".swift",
             ".kt", ".scala", ".r"}
TEXT_EXTS = {".txt", ".md", ".markdown", ".html", ".htm", ".json",
             ".yaml", ".yml", ".toml", ".ini", ".cfg", ".log", ".csv",
             ".py", ".js", ".ts", ".ps1", ".bat", ".cmd", ".sh",
             ".rb", ".go", ".rs", ".java", ".cs", ".sql", ".css", ".vue",
             ".jsx", ".tsx", ".c", ".h", ".cpp", ".hpp", ".php", ".swift",
             ".kt", ".scala", ".r"}

_TAG_RE = re.compile(r"<script\b[^>]*>.*?</script>|<style\b[^>]*>.*?</style>",
                     re.IGNORECASE | re.DOTALL)
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError:
        return ""
    return digest.hexdigest()


def _slug(text: str, fallback: str = "note") -> str:
    text = re.sub(r"[^\w\-.\u4e00-\u9fff]+", "-", (text or "").strip(),
                  flags=re.UNICODE).strip("-._")
    return (text or fallback)[:80]


def infer_category(path: str, explicit: str = "") -> str:
    if explicit in CATEGORIES:
        return explicit
    ext = Path(path).suffix.lower()
    if ext == ".pdf":
        return "pdfs"
    if ext in (".html", ".htm"):
        return "html"
    if ext in IMAGE_EXTS:
        return "images"
    if ext in (".md", ".markdown"):
        return "markdown"
    if ext in SCRIPT_EXTS:
        return "scripts"
    return "inbox"


def fts_query_variants(query: str) -> list[str]:
    """FTS5/trigram 查询变体：短语 → 词项 AND → 原样。

    trigram tokenizer 对整句加引号要求连续匹配，像
    ``lattice oxygen mechanism`` 这类跨行或带断词的论文文本会查不到；
    退回到词项 AND 能显著提高召回。短于 3 字符的英文词项不会被
    trigram 索引，直接跳过。
    """
    query = (query or "").strip()
    if not query:
        return []
    escaped = query.replace('"', '""')
    variants = [f'"{escaped}"']
    terms = [term for term in re.split(r"\s+", query) if term]
    usable = []
    for term in terms:
        if len(term) >= 3 or re.search(r"[\u4e00-\u9fff]", term):
            usable.append(term.replace('"', '""'))
    if len(usable) >= 2:
        and_query = " AND ".join(f'"{term}"' for term in usable)
        if and_query not in variants:
            variants.append(and_query)
    if query not in variants:
        variants.append(query)
    return variants


class KnowledgeBase:
    def __init__(self, root: str):
        self.root = os.path.abspath(os.path.expanduser(str(root or "database")))
        self.db_path = os.path.join(self.root, "index.sqlite3")
        self.catalog_path = os.path.join(self.root, "catalog.json")
        self.index_md_path = os.path.join(self.root, "INDEX.md")
        self.has_fts = False
        self.ensure()

    # ------------------------------------------------------------------ #
    def ensure(self):
        for category in CATEGORIES:
            if category in ("urls", "passwords"):
                os.makedirs(os.path.join(self.root, category), exist_ok=True)
            else:
                os.makedirs(os.path.join(self.root, category), exist_ok=True)
        self._init_db()
        return self.root

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self):
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS documents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    path TEXT NOT NULL UNIQUE,
                    category TEXT NOT NULL,
                    kind TEXT NOT NULL DEFAULT 'file',
                    title TEXT NOT NULL DEFAULT '',
                    description TEXT NOT NULL DEFAULT '',
                    tags TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    mtime REAL NOT NULL DEFAULT 0,
                    size INTEGER NOT NULL DEFAULT 0,
                    sha256 TEXT NOT NULL DEFAULT '',
                    text TEXT NOT NULL DEFAULT ''
                )
            """)
            try:
                conn.execute("""
                    CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
                        title, description, tags, text,
                        content='documents', content_rowid='id',
                        tokenize='trigram'
                    )
                """)
                self.has_fts = True
            except sqlite3.OperationalError:
                self.has_fts = False
            if self.has_fts:
                for statement in (
                    """CREATE TRIGGER IF NOT EXISTS documents_ai AFTER INSERT ON documents BEGIN
                       INSERT INTO documents_fts(rowid,title,description,tags,text)
                       VALUES (new.id,new.title,new.description,new.tags,new.text); END""",
                    """CREATE TRIGGER IF NOT EXISTS documents_ad AFTER DELETE ON documents BEGIN
                       INSERT INTO documents_fts(documents_fts,rowid,title,description,tags,text)
                       VALUES ('delete',old.id,old.title,old.description,old.tags,old.text); END""",
                    """CREATE TRIGGER IF NOT EXISTS documents_au AFTER UPDATE ON documents BEGIN
                       INSERT INTO documents_fts(documents_fts,rowid,title,description,tags,text)
                       VALUES ('delete',old.id,old.title,old.description,old.tags,old.text);
                       INSERT INTO documents_fts(rowid,title,description,tags,text)
                       VALUES (new.id,new.title,new.description,new.tags,new.text); END""",
                ):
                    conn.execute(statement)
            conn.commit()

    # ------------------------------------------------------------------ #
    def _extract_text(self, path: str, category: str) -> str:
        ext = Path(path).suffix.lower()
        if category in ("images", "screenshots", "pdfs") or ext not in TEXT_EXTS:
            return ""
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                text = handle.read(120_000)
        except OSError:
            return ""
        if category == "html" or ext in (".html", ".htm"):
            text = _TAG_RE.sub(" ", text)
            text = _HTML_TAG_RE.sub(" ", text)
            text = html.unescape(text)
            text = re.sub(r"[ \t\r\f\v]+", " ", text)
            text = re.sub(r"\n\s*\n+", "\n\n", text)
        return text.strip()

    def _destination(self, source: str, category: str) -> str:
        source = os.path.abspath(source)
        root = os.path.abspath(self.root)
        if source == root or source.startswith(root + os.sep):
            try:
                rel = os.path.relpath(source, root)
                top = rel.split(os.sep)[0]
                # 已在知识库内：分类一致，或位于 cer_txt/txt/urls 等辅助目录时
                # 保持原位，避免每次整理都复制出 -1/-2 副本。
                if top == category or top not in CATEGORIES:
                    return source
            except ValueError:
                pass
        folder = os.path.join(root, category)
        os.makedirs(folder, exist_ok=True)
        base = _slug(Path(source).stem, "file")
        ext = Path(source).suffix
        candidate = os.path.join(folder, base + ext)
        index = 1
        while os.path.exists(candidate) and not (
                os.path.isfile(candidate) and os.path.samefile(source, candidate)):
            candidate = os.path.join(folder, f"{base}-{index}{ext}")
            index += 1
        if os.path.abspath(source) != os.path.abspath(candidate):
            shutil.copy2(source, candidate)
        return candidate

    def _record(self, path: str, category: str, kind: str, title: str,
                description: str, tags: str, source: str, text: str,
                created: Optional[str] = None) -> dict:
        full = (path if kind == "url" else
                path if os.path.isabs(path) else os.path.join(self.root, path))
        try:
            size = os.path.getsize(full) if kind != "url" else 0
            mtime = os.path.getmtime(full) if kind != "url" else 0
        except OSError:
            size, mtime = 0, 0
        return {
            "path": path,
            "category": category,
            "kind": kind,
            "title": title,
            "description": description,
            "tags": tags,
            "source": source,
            "created_at": created or _now(),
            "mtime": mtime,
            "size": size,
            "sha256": _sha256(full) if kind != "url" else "",
            "text": text,
        }

    def _upsert(self, record: dict) -> dict:
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT id FROM documents WHERE path = ?", (record["path"],)
            ).fetchone()
            if existing:
                conn.execute("""
                    UPDATE documents SET category=?, kind=?, title=?, description=?,
                        tags=?, source=?, mtime=?, size=?, sha256=?, text=?
                    WHERE path=?
                """, (record["category"], record["kind"], record["title"],
                      record["description"], record["tags"], record["source"],
                      record["mtime"], record["size"], record["sha256"],
                      record["text"], record["path"]))
                record["id"] = existing["id"]
            else:
                cur = conn.execute("""
                    INSERT INTO documents(path,category,kind,title,description,tags,
                        source,created_at,mtime,size,sha256,text)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                """, (record["path"], record["category"], record["kind"],
                      record["title"], record["description"], record["tags"],
                      record["source"], record["created_at"], record["mtime"],
                      record["size"], record["sha256"], record["text"]))
                record["id"] = cur.lastrowid
            conn.commit()
        record["full_path"] = self.full_path(record)
        return record

    def full_path(self, record: dict) -> str:
        if record.get("kind") == "url":
            return record["path"]
        if os.path.isabs(record["path"]):
            return record["path"]
        return os.path.join(self.root, record["path"])

    # ------------------------------------------------------------------ #
    def add_file(self, source: str, category: str = "", title: str = "",
                 description: str = "", tags: str = "", copy: bool = True,
                 _export: bool = True) -> dict:
        source = os.path.abspath(os.path.expanduser(source))
        if not os.path.isfile(source):
            raise FileNotFoundError(source)
        category = infer_category(source, category)
        if category in ("urls", "passwords"):
            raise ValueError("该分类不能通过 add_file 写入")
        destination = self._destination(source, category) if copy else source
        if not os.path.isfile(destination):
            raise FileNotFoundError(destination)
        rel = os.path.relpath(destination, self.root).replace(os.sep, "/")
        if not os.path.isabs(destination):
            rel = destination
        text = self._extract_text(destination, category)
        record = self._record(
            rel, category, Path(destination).suffix.lower().lstrip(".") or "file",
            title.strip() or Path(destination).stem,
            description.strip(), tags.strip(), source, text)
        record = self._upsert(record)
        if _export:
            self.export_catalog()
        return record

    def add_url(self, url: str, title: str = "", description: str = "",
                tags: str = "") -> dict:
        url = (url or "").strip()
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("URL 必须以 http:// 或 https:// 开头")
        text = " ".join(x for x in (url, title, description, tags) if x)
        record = self._record(url, "urls", "url", title.strip() or url,
                              description.strip(), tags.strip(), url, text)
        record = self._upsert(record)
        os.makedirs(os.path.join(self.root, "urls"), exist_ok=True)
        url_file = os.path.join(self.root, "urls", "urls.jsonl")
        lines = []
        if os.path.isfile(url_file):
            with open(url_file, "r", encoding="utf-8", errors="replace") as handle:
                lines = [line for line in handle if line.strip()]
        if not any(json.loads(line).get("url") == url for line in lines
                   if line.strip().startswith("{")):
            with open(url_file, "a", encoding="utf-8") as handle:
                handle.write(json.dumps({
                    "url": url, "title": record["title"],
                    "description": record["description"], "tags": record["tags"],
                    "created_at": record["created_at"],
                }, ensure_ascii=False) + "\n")
        self.export_catalog()
        return record

    def add_note(self, title: str, content: str, tags: str = "") -> dict:
        title = (title or "未命名笔记").strip()
        os.makedirs(os.path.join(self.root, "markdown"), exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        filename = f"{_slug(title)}-{stamp}.md"
        path = os.path.join(self.root, "markdown", filename)
        body = f"# {title}\n\n"
        if tags:
            body += f"tags: {tags}\n\n"
        body += content
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(body)
        return self.add_file(path, "markdown", title=title,
                             description=content[:300], tags=tags, copy=False)

    def add_code(self, title: str, content: str, language: str = "python",
                 description: str = "", tags: str = "") -> dict:
        """把常见代码片段保存到 database/code，并加入检索索引。"""
        title = (title or "代码片段").strip()
        language = (language or "text").strip().lower().lstrip(".")
        ext_map = {
            "python": "py", "py": "py", "javascript": "js", "js": "js",
            "typescript": "ts", "ts": "ts", "html": "html", "css": "css",
            "sql": "sql", "bash": "sh", "shell": "sh", "sh": "sh",
            "powershell": "ps1", "ps1": "ps1", "json": "json",
            "yaml": "yaml", "yml": "yml", "java": "java", "go": "go",
            "rust": "rs", "c": "c", "cpp": "cpp", "c++": "cpp",
            "csharp": "cs", "c#": "cs", "php": "php", "ruby": "rb",
        }
        ext = ext_map.get(language, "txt")
        os.makedirs(os.path.join(self.root, "code"), exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
        path = os.path.join(self.root, "code", f"{_slug(title, 'code')}-{stamp}.{ext}")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)
        tag_text = ", ".join(x for x in (tags.strip(), f"lang:{language}") if x)
        return self.add_file(
            path, category="code", title=title, description=description,
            tags=tag_text, copy=False)

    # ------------------------------------------------------------------ #
    def _row(self, row) -> dict:
        record = dict(row)
        record["full_path"] = self.full_path(record)
        return record

    def get(self, identifier: str) -> Optional[dict]:
        identifier = str(identifier or "").strip()
        if not identifier:
            return None
        with self._connect() as conn:
            row = None
            if identifier.isdigit():
                row = conn.execute("SELECT * FROM documents WHERE id=?",
                                   (int(identifier),)).fetchone()
            if row is None:
                row = conn.execute("SELECT * FROM documents WHERE path=?",
                                   (identifier,)).fetchone()
            if row is None:
                row = conn.execute(
                    "SELECT * FROM documents WHERE title LIKE ? LIMIT 1",
                    (f"%{identifier}%",)).fetchone()
        return self._row(row) if row else None

    def list_category(self, category: str = "", limit: int = 100) -> list[dict]:
        limit = max(1, min(500, int(limit or 100)))
        with self._connect() as conn:
            if category and category != "all":
                rows = conn.execute(
                    "SELECT * FROM documents WHERE category=? ORDER BY created_at DESC, id DESC LIMIT ?",
                    (category, limit)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM documents ORDER BY created_at DESC, id DESC LIMIT ?",
                    (limit,)).fetchall()
        return [self._row(row) for row in rows]

    def stats(self) -> dict:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT category, COUNT(*) AS n FROM documents GROUP BY category"
            ).fetchall()
        counts = {row["category"]: row["n"] for row in rows}
        return {"root": self.root, "db": self.db_path, "total": sum(counts.values()),
                "categories": counts, "fts": self.has_fts}

    def _snippet(self, row, query: str) -> str:
        text = " ".join(str(row.get(k) or "") for k in ("description", "text"))
        text = re.sub(r"\s+", " ", text).strip()
        index = text.lower().find((query or "").lower())
        if index < 0:
            return text[:240] + ("…" if len(text) > 240 else "")
        start = max(0, index - 80)
        end = min(len(text), index + 180)
        return ("…" if start else "") + text[start:end] + ("…" if end < len(text) else "")

    def search(self, query: str, category: str = "", limit: int = 10) -> list[dict]:
        query = (query or "").strip()
        if not query:
            return []
        limit = max(1, min(100, int(limit or 10)))
        rows = []
        if self.has_fts:
            seen: dict[int, object] = {}
            for fts_query in fts_query_variants(query):
                sql = """SELECT d.* FROM documents_fts f JOIN documents d ON d.id=f.rowid
                         WHERE documents_fts MATCH ?
                           AND d.path NOT LIKE 'markdown/INDEX%'"""
                params: list = [fts_query]
                if category and category != "all":
                    sql += " AND d.category=?"
                    params.append(category)
                sql += """ ORDER BY bm25(documents_fts) + CASE
                             WHEN d.path LIKE 'markdown/chat-%' THEN 8
                             WHEN d.path LIKE 'markdown/README%' THEN 8
                             ELSE 0 END
                           LIMIT ?"""
                params.append(limit)
                try:
                    with self._connect() as conn:
                        part = conn.execute(sql, params).fetchall()
                except sqlite3.OperationalError:
                    part = []
                for row in part:
                    seen.setdefault(row["id"], row)
                if len(seen) >= limit:
                    break
            rows = list(seen.values())[:limit]
        if not rows:
            like = f"%{query}%"
            sql = """SELECT d.* FROM documents d WHERE
                     (d.title LIKE ? OR d.description LIKE ? OR d.tags LIKE ?
                      OR d.text LIKE ? OR d.path LIKE ?)
                     AND d.path NOT LIKE 'markdown/INDEX%'"""
            params = [like, like, like, like, like]
            if category and category != "all":
                sql += " AND d.category=?"
                params.append(category)
            sql += " ORDER BY d.created_at DESC LIMIT ?"
            params.append(limit)
            with self._connect() as conn:
                rows = conn.execute(sql, params).fetchall()
        results = []
        for row in rows:
            record = self._row(row)
            record["snippet"] = self._snippet(record, query)
            results.append(record)
        return results

    def reindex(self,
                progress: Optional[Callable[[int, int, str], None]] = None) -> dict:
        touched = 0
        with self._connect() as conn:
            # 自动生成的目录/说明副本不应成为可检索的知识库内容。
            cur = conn.execute(
                "DELETE FROM documents WHERE path GLOB 'markdown/INDEX*' "
                "OR path GLOB 'markdown/README*'")
            touched += cur.rowcount
            rows = conn.execute("SELECT * FROM documents").fetchall()
            existing = {row["path"]: dict(row) for row in rows
                        if row["kind"] != "url"}
            for row in rows:
                if row["kind"] == "url":
                    continue
                full = self.full_path(dict(row))
                if not os.path.isfile(full):
                    conn.execute("DELETE FROM documents WHERE id=?", (row["id"],))
                    touched += 1
            conn.commit()

        files: list[tuple[str, str]] = []
        for category in CATEGORIES:
            if category in ("urls", "passwords"):
                continue
            folder = os.path.join(self.root, category)
            if not os.path.isdir(folder):
                continue
            for dirpath, _dirs, names in os.walk(folder):
                # RAG 抽取的论文图片由 rag_images 管理，避免重复进入知识库。
                if category == "images":
                    rel_dir = os.path.relpath(dirpath, folder).replace(os.sep, "/")
                    if rel_dir == "papers" or rel_dir.startswith("papers/"):
                        continue
                for name in names:
                    if (name.startswith(("INDEX", "README"))
                            and name.lower().endswith((".md", ".markdown"))):
                        continue
                    path = os.path.join(dirpath, name)
                    if os.path.isfile(path):
                        files.append((category, path))

        total = len(files)
        for index, (category, path) in enumerate(files, 1):
            name = os.path.basename(path)
            rel = os.path.relpath(path, self.root).replace(os.sep, "/")
            old = existing.get(rel)
            if old is not None:
                try:
                    stat = os.stat(path)
                except OSError:
                    stat = None
                if (stat is not None
                        and abs(float(old.get("mtime") or 0) - stat.st_mtime) < 0.5
                        and int(old.get("size") or 0) == stat.st_size):
                    if progress:
                        progress(index, total, name)
                    continue
            self.add_file(
                path, category, title=(old or {}).get("title", ""),
                description=(old or {}).get("description", ""),
                tags=(old or {}).get("tags", ""), copy=False, _export=False)
            touched += 1
            if progress:
                progress(index, total, name)
        self.export_catalog()
        return {"reindexed": touched, "stats": self.stats()}

    def export_catalog(self):
        records = []
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM documents ORDER BY category, path").fetchall()
            records = [self._row(row) for row in rows]
        payload = [{k: record.get(k) for k in (
            "id", "path", "category", "kind", "title", "description",
            "tags", "source", "created_at", "mtime", "size", "sha256")}
            for record in records]
        with open(self.catalog_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        groups: dict[str, list[dict]] = {}
        for record in records:
            groups.setdefault(record["category"], []).append(record)
        lines = ["# 本地知识库目录", "", f"生成时间：{_now()}", ""]
        for category in CATEGORIES:
            items = groups.get(category, [])
            lines.append(f"## {category} · {CATEGORIES[category]}（{len(items)}）")
            if not items:
                lines.append("")
                continue
            for item in items:
                title = item.get("title") or item["path"]
                desc = f" — {item['description']}" if item.get("description") else ""
                lines.append(f"- [{title}]({item['full_path']}) `{item['kind']}`{desc}")
            lines.append("")
        with open(self.index_md_path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
        return {"catalog": self.catalog_path, "index": self.index_md_path,
                "count": len(records)}
