# -*- coding: utf-8 -*-
"""论文/HTML 的本地 RAG：分块、embedding、混合检索与来源引用。

依赖是可选安装的（见 requirements-rag.txt）。没有安装 fastembed 时使用确定性的
HashEmbedder 保证离线可跑；生产环境请安装本地 embedding 模型。
"""

from __future__ import annotations

import hashlib
import html
import json
import math
import os
import re
import sqlite3
import struct
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import urlparse

from .database import KnowledgeBase, fts_query_variants

DEFAULT_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
DEFAULT_CHUNK_TOKENS = 700
DEFAULT_OVERLAP = 100


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def approx_tokens(text: str) -> int:
    """中英文混合的快速 token 估算（不是模型 tokenizer）。"""
    cjk = sum(1 for char in text if "\u4e00" <= char <= "\u9fff")
    words = len(re.findall(r"[A-Za-z0-9_]+", text))
    return cjk + words


def _norm(vector: list[float]) -> list[float]:
    length = math.sqrt(sum(x * x for x in vector)) or 1.0
    return [x / length for x in vector]


class HashEmbedder:
    """无模型依赖的确定性向量，仅用于离线测试/降级。"""

    dim = 384

    def embed(self, texts: Iterable[str]) -> list[list[float]]:
        out = []
        for text in texts:
            vec = [0.0] * self.dim
            for token in re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]", text.lower()):
                digest = hashlib.sha256(token.encode("utf-8")).digest()
                index = int.from_bytes(digest[:4], "little") % self.dim
                vec[index] += 1.0 if digest[4] & 1 else -1.0
            out.append(_norm(vec))
        return out


class FastEmbedder:
    """fastembed 的本地 ONNX embedding。"""

    def __init__(self, model_name: str = DEFAULT_MODEL,
                 cache_dir: Optional[str] = None):
        from fastembed import TextEmbedding
        kwargs = {"model_name": model_name}
        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)
            kwargs["cache_dir"] = cache_dir
        self.model = TextEmbedding(**kwargs)
        sample = list(self.model.embed(["dimension probe"]))
        self.dim = len(sample[0]) if sample else 384

    def embed(self, texts: Iterable[str]) -> list[list[float]]:
        return [_norm(list(map(float, vec))) for vec in self.model.embed(list(texts))]


def create_embedder(model_name: str = DEFAULT_MODEL,
                    cache_dir: Optional[str] = None):
    try:
        return FastEmbedder(model_name, cache_dir)
    except Exception as exc:                                      # noqa: BLE001
        print(f"[rag] fastembed 不可用，使用 HashEmbedder: {exc}")
        return HashEmbedder()


@dataclass
class Chunk:
    text: str
    page: Optional[int] = None
    section: str = ""
    kind: str = "text"
    image_path: str = ""


@dataclass
class ExtractedImage:
    page: int
    index: int
    data: bytes
    ext: str
    caption: str = ""
    ocr_text: str = ""
    path: str = ""


def chunk_text(text: str, chunk_tokens: int = DEFAULT_CHUNK_TOKENS,
               overlap: int = DEFAULT_OVERLAP, page: Optional[int] = None,
               section: str = "", kind: str = "text") -> list[Chunk]:
    text = re.sub(r"\r\n?", "\n", (text or "").strip())
    if not text:
        return []
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n+", text) if p.strip()]
    chunks: list[Chunk] = []
    current: list[str] = []
    count = 0
    for paragraph in paragraphs:
        p_tokens = max(1, approx_tokens(paragraph))
        if count and count + p_tokens > chunk_tokens:
            chunks.append(Chunk("\n\n".join(current), page, section, kind))
            carry = []
            carry_count = 0
            for old in reversed(current):
                old_tokens = approx_tokens(old)
                if carry_count + old_tokens > overlap:
                    break
                carry.insert(0, old)
                carry_count += old_tokens
            current, count = carry, carry_count
        current.append(paragraph)
        count += p_tokens
    if current:
        chunks.append(Chunk("\n\n".join(current), page, section, kind))
    return chunks


_TITLE_RE = re.compile(r"<\s*title[^>]*>(.*?)</\s*title>", re.I | re.S)
_CAPTION_RE = re.compile(
    r"((?:图|Figure|Fig\.|表|Table)\s*[0-9一二三四五六七八九十]+[^\n]{0,300})",
    re.IGNORECASE)


def extract_html(html_text: str, source_url: str = "") -> tuple[str, str]:
    """返回 (title, main_text)。优先 trafilatura，失败时退回标签清洗。"""
    title = ""
    match = _TITLE_RE.search(html_text)
    if match:
        title = html.unescape(re.sub(r"<[^>]+>", "", match.group(1))).strip()
    try:
        import trafilatura
        data = trafilatura.extract(html_text, output_format="markdown",
                                   include_tables=True, include_links=True,
                                   url=source_url or None)
        if data:
            return title, data
    except Exception:                                             # noqa: BLE001
        pass
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html_text, "html.parser")
        for node in soup(["script", "style", "nav", "footer", "header"]):
            node.decompose()
        main = soup.find("article") or soup.find("main") or soup.body or soup
        return title, main.get_text("\n", strip=True)
    except Exception:                                             # noqa: BLE001
        text = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", " ", html_text,
                      flags=re.I | re.S)
        text = html.unescape(re.sub(r"<[^>]+>", " ", text))
        return title, re.sub(r"[ \t]+", " ", text).strip()


def _normalize_image_bytes(data: bytes, ext: str = "png") -> tuple[bytes, str]:
    """把 PDF 内嵌图统一成白底 PNG，并裁掉巨大的纯色透明/黑边。"""
    try:
        from io import BytesIO
        from PIL import Image, ImageChops, ImageOps
        image = ImageOps.exif_transpose(Image.open(BytesIO(data)))
        if getattr(image, "is_animated", False):
            image.seek(0)
        if image.mode in ("RGBA", "LA") or "transparency" in image.info:
            rgba = image.convert("RGBA")
            background = Image.new("RGB", rgba.size, "white")
            background.paste(rgba, mask=rgba.getchannel("A"))
            image = background
        else:
            image = image.convert("RGB")
        width, height = image.size
        if width > 24 and height > 24:
            corner = image.getpixel((0, 0))
            corners = [image.getpixel((0, 0)), image.getpixel((width - 1, 0)),
                       image.getpixel((0, height - 1)),
                       image.getpixel((width - 1, height - 1))]
            if all(max(abs(c[i] - corner[i]) for i in range(3)) <= 12
                   for c in corners):
                background = Image.new("RGB", image.size, corner)
                mask = ImageChops.difference(image, background).convert("L")
                mask = mask.point(lambda value: 255 if value > 12 else 0)
                bbox = mask.getbbox()
                if bbox and (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]) < 0.90 * width * height:
                    pad = 8
                    image = image.crop((max(0, bbox[0] - pad), max(0, bbox[1] - pad),
                                        min(width, bbox[2] + pad), min(height, bbox[3] + pad)))
        output = BytesIO()
        image.save(output, "PNG", optimize=True)
        return output.getvalue(), "png"
    except Exception:
        return data, ext


def extract_pdf(path: str, ocr_images: bool = False) -> dict:
    """用 pdfplumber 提取版面/表格，pypdf 补充元数据与图片。

    这是可选依赖；没有安装时抛出清晰的安装提示。
    """
    pdfplumber = pypdf = None
    try:
        import pdfplumber as _pdfplumber
        pdfplumber = _pdfplumber
    except ImportError:
        pass
    try:
        from pypdf import PdfReader as _PdfReader
        pypdf = _PdfReader
    except ImportError:
        pass
    if pdfplumber is None and pypdf is None:
        raise RuntimeError("解析 PDF 需要安装 requirements-rag.txt 中的 pdfplumber/pypdf")

    pages: list[dict] = []
    metadata: dict = {}
    captions: list[str] = []
    images: list[ExtractedImage] = []
    if pdfplumber is not None:
        with pdfplumber.open(path) as pdf:
            metadata = dict(pdf.metadata or {})
            for page_no, page in enumerate(pdf.pages, 1):
                text = page.extract_text(x_tolerance=2, y_tolerance=3) or ""
                table_parts = []
                try:
                    for table in page.extract_tables() or []:
                        rows = []
                        for row in table:
                            rows.append("| " + " | ".join(
                                str(cell or "").replace("\n", " ") for cell in row) + " |")
                        if len(rows) >= 2:
                            table_parts.append("\n".join(rows))
                except Exception:                                 # noqa: BLE001
                    pass
                if table_parts:
                    text += "\n\n" + "\n\n".join(table_parts)
                captions.extend(_CAPTION_RE.findall(text))
                pages.append({"page": page_no, "text": text})
    if pypdf is not None:
        reader = pypdf(path)
        if not metadata:
            metadata = {k: v for k, v in (reader.metadata or {}).items()}
        for page_no, page in enumerate(reader.pages, 1):
            if pdfplumber is None:
                pages.append({"page": page_no,
                              "text": page.extract_text() or ""})
            try:
                for image_index, image in enumerate(page.images):
                    ext = (image.name.rsplit(".", 1)[-1]
                           if "." in getattr(image, "name", "") else "png")
                    caption = captions[len(images)] if len(images) < len(captions) else ""
                    images.append(ExtractedImage(
                        page=page_no, index=image_index,
                        data=image.data, ext=ext, caption=caption))
            except Exception:                                     # noqa: BLE001
                continue
    if ocr_images:
        for image in images:
            image.ocr_text = _ocr_bytes(image.data)
    return {"metadata": metadata, "pages": pages, "images": images,
            "captions": captions}


def _ocr_bytes(data: bytes) -> str:
    from .ocr import ocr_bytes
    return ocr_bytes(data)


class RagStore:
    def __init__(self, database_root: str):
        self.kb = KnowledgeBase(database_root)
        self.db_path = self.kb.db_path
        self._vec_available = None
        self._init()

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=20)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init(self):
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS rag_chunks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_id INTEGER NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    page INTEGER,
                    section TEXT NOT NULL DEFAULT '',
                    kind TEXT NOT NULL DEFAULT 'text',
                    text TEXT NOT NULL,
                    token_count INTEGER NOT NULL DEFAULT 0,
                    image_path TEXT NOT NULL DEFAULT '',
                    UNIQUE(document_id, chunk_index)
                );
                CREATE INDEX IF NOT EXISTS rag_chunks_doc ON rag_chunks(document_id);
                CREATE TABLE IF NOT EXISTS rag_embeddings (
                    chunk_id INTEGER PRIMARY KEY,
                    model TEXT NOT NULL,
                    dim INTEGER NOT NULL,
                    vector BLOB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS rag_images (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_id INTEGER NOT NULL,
                    page INTEGER,
                    image_index INTEGER NOT NULL DEFAULT 0,
                    path TEXT NOT NULL,
                    caption TEXT NOT NULL DEFAULT '',
                    ocr_text TEXT NOT NULL DEFAULT '',
                    description TEXT NOT NULL DEFAULT '',
                    UNIQUE(document_id, page, image_index, path)
                );
                CREATE TABLE IF NOT EXISTS rag_ingest_jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    status TEXT NOT NULL,
                    message TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                );
            """)
            try:
                conn.execute("""
                    CREATE VIRTUAL TABLE IF NOT EXISTS rag_chunks_fts USING fts5(
                        text, section, content='rag_chunks', content_rowid='id',
                        tokenize='trigram')
                """)
                conn.executescript("""
                    CREATE TRIGGER IF NOT EXISTS rag_chunks_ai AFTER INSERT ON rag_chunks BEGIN
                      INSERT INTO rag_chunks_fts(rowid,text,section)
                      VALUES(new.id,new.text,new.section); END;
                    CREATE TRIGGER IF NOT EXISTS rag_chunks_ad AFTER DELETE ON rag_chunks BEGIN
                      INSERT INTO rag_chunks_fts(rag_chunks_fts,rowid,text,section)
                      VALUES('delete',old.id,old.text,old.section); END;
                """)
            except sqlite3.OperationalError:
                pass
            conn.commit()

    def _vector_ready(self, conn, dim: int) -> bool:
        if self._vec_available is None:
            try:
                import sqlite_vec
                conn.enable_load_extension(True)
                sqlite_vec.load(conn)
                self._vec_available = True
            except Exception:                                     # noqa: BLE001
                self._vec_available = False
        if self._vec_available:
            try:
                conn.execute(
                    f"CREATE VIRTUAL TABLE IF NOT EXISTS rag_vec USING vec0(embedding float[{dim}])")
            except sqlite3.OperationalError:
                self._vec_available = False
        return bool(self._vec_available)

    @staticmethod
    def _pack(vector: list[float]) -> bytes:
        return struct.pack(f"<{len(vector)}f", *vector)

    @staticmethod
    def _unpack(blob: bytes) -> list[float]:
        return list(struct.unpack(f"<{len(blob) // 4}f", blob))

    def _delete_doc_chunks(self, conn, document_id: int):
        ids = [row[0] for row in conn.execute(
            "SELECT id FROM rag_chunks WHERE document_id=?", (document_id,))]
        if ids and self._vec_available:
            try:
                marks = ",".join("?" for _ in ids)
                conn.execute(f"DELETE FROM rag_vec WHERE rowid IN ({marks})", ids)
            except sqlite3.OperationalError:
                pass
        conn.execute("DELETE FROM rag_embeddings WHERE chunk_id IN "
                     "(SELECT id FROM rag_chunks WHERE document_id=?)", (document_id,))
        conn.execute("DELETE FROM rag_chunks WHERE document_id=?", (document_id,))

    def add_chunks(self, document_id: int, chunks: list[Chunk],
                   vectors: list[list[float]], model: str):
        if not chunks:
            return 0
        dim = len(vectors[0])
        with self._connect() as conn:
            self._delete_doc_chunks(conn, document_id)
            ids = []
            for index, (chunk, vector) in enumerate(zip(chunks, vectors)):
                cur = conn.execute("""
                    INSERT INTO rag_chunks(document_id,chunk_index,page,section,kind,text,
                        token_count,image_path) VALUES(?,?,?,?,?,?,?,?)
                """, (document_id, index, chunk.page, chunk.section, chunk.kind,
                      chunk.text, approx_tokens(chunk.text), chunk.image_path))
                ids.append(cur.lastrowid)
                conn.execute("INSERT OR REPLACE INTO rag_embeddings"
                             "(chunk_id,model,dim,vector) VALUES(?,?,?,?)",
                             (cur.lastrowid, model, dim, self._pack(vector)))
            if self._vector_ready(conn, dim):
                for chunk_id, vector in zip(ids, vectors):
                    try:
                        conn.execute("INSERT OR REPLACE INTO rag_vec(rowid,embedding) "
                                     "VALUES(?,?)", (chunk_id, self._pack(vector)))
                    except sqlite3.OperationalError:
                        pass
            conn.commit()
        return len(ids)

    def add_image(self, document_id: int, page: Optional[int], image_index: int,
                  path: str, caption: str = "", ocr_text: str = "",
                  description: str = "") -> int:
        with self._connect() as conn:
            cur = conn.execute("""
                INSERT INTO rag_images(document_id,page,image_index,path,caption,
                    ocr_text,description) VALUES(?,?,?,?,?,?,?)
            """, (document_id, page, image_index, path, caption, ocr_text, description))
            conn.commit()
            return cur.lastrowid

    def _chunks_by_ids(self, ids: list[int]) -> dict[int, dict]:
        if not ids:
            return {}
        marks = ",".join("?" for _ in ids)
        with self._connect() as conn:
            rows = conn.execute(f"""
                SELECT c.*, d.title, d.path, d.category, d.source
                FROM rag_chunks c JOIN documents d ON d.id=c.document_id
                WHERE c.id IN ({marks})
            """, ids).fetchall()
        return {row["id"]: dict(row) for row in rows}

    def search_fts(self, query: str, limit: int = 20) -> list[tuple[int, float]]:
        sql = """
            SELECT c.id AS id, bm25(rag_chunks_fts) AS score
            FROM rag_chunks_fts
            JOIN rag_chunks c ON c.id = rag_chunks_fts.rowid
            JOIN documents d ON d.id = c.document_id
            WHERE rag_chunks_fts MATCH ?
              AND d.path NOT LIKE 'markdown/INDEX%'
              AND d.path NOT LIKE 'markdown/chat-%'
              AND d.path NOT LIKE 'markdown/README%'
            ORDER BY bm25(rag_chunks_fts)
            LIMIT ?
        """
        with self._connect() as conn:
            for fts_query in fts_query_variants(query):
                try:
                    rows = conn.execute(sql, (fts_query, limit)).fetchall()
                except sqlite3.OperationalError:
                    rows = []
                if rows:
                    return [(row["id"], -float(row["score"])) for row in rows]
            like = f"%{query}%"
            rows = conn.execute("""
                SELECT c.id, 0.0 AS score FROM rag_chunks c
                JOIN documents d ON d.id=c.document_id
                WHERE (c.text LIKE ? OR c.section LIKE ?)
                  AND d.path NOT LIKE 'markdown/INDEX%'
                  AND d.path NOT LIKE 'markdown/chat-%'
                  AND d.path NOT LIKE 'markdown/README%'
                LIMIT ?
            """, (like, like, limit)).fetchall()
        return [(row["id"], 0.0) for row in rows]

    def search_vector(self, vector: list[float], limit: int = 20) -> list[tuple[int, float]]:
        dim = len(vector)
        with self._connect() as conn:
            if self._vector_ready(conn, dim):
                try:
                    rows = conn.execute("""
                        SELECT rowid, distance FROM rag_vec
                        WHERE embedding MATCH ? ORDER BY distance LIMIT ?
                    """, (self._pack(vector), limit)).fetchall()
                    if rows:
                        return [(row["rowid"], 1.0 / (1.0 + float(row["distance"])))
                                for row in rows]
                except sqlite3.OperationalError:
                    pass
            rows = conn.execute("""
                SELECT e.chunk_id, e.vector, e.dim
                FROM rag_embeddings e
                JOIN rag_chunks c ON c.id = e.chunk_id
                JOIN documents d ON d.id = c.document_id
                WHERE d.path NOT LIKE 'markdown/INDEX%'
                  AND d.path NOT LIKE 'markdown/chat-%'
                  AND d.path NOT LIKE 'markdown/README%'
            """).fetchall()
        scored = []
        for row in rows:
            if row["dim"] != dim:
                continue
            scored.append((row["chunk_id"], _cosine(vector, self._unpack(row["vector"]))))
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:limit]

    def search(self, query: str, vector: list[float], limit: int = 8) -> list[dict]:
        fts = self.search_fts(query, limit * 3)
        vec = self.search_vector(vector, limit * 3)
        scores: dict[int, float] = {}
        for rank, (chunk_id, _score) in enumerate(fts):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (60 + rank + 1)
        for rank, (chunk_id, _score) in enumerate(vec):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (60 + rank + 1)
        ordered = sorted(scores, key=scores.get, reverse=True)[:limit]
        records = self._chunks_by_ids(ordered)
        return [records[chunk_id] for chunk_id in ordered
                if chunk_id in records
                and not str(records[chunk_id].get("path") or "").startswith(
                    ("markdown/INDEX", "markdown/chat-", "markdown/README"))]

    def document_indexed(self, source: str, sha256: str = "") -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute("""
                SELECT d.id, d.path, d.title,
                       (SELECT COUNT(*) FROM rag_chunks c WHERE c.document_id=d.id) AS chunks
                FROM documents d WHERE d.source=?
                ORDER BY d.id DESC LIMIT 1
            """, (source,)).fetchone()
            if not row:
                return None
            if sha256 and row["chunks"]:
                doc_sha = conn.execute("SELECT sha256 FROM documents WHERE id=?",
                                       (row["id"],)).fetchone()
                if doc_sha and doc_sha[0] == sha256:
                    return dict(row)
            return None

    def list_images(self, document_id: Optional[int] = None, limit: int = 50) -> list[dict]:
        with self._connect() as conn:
            if document_id:
                rows = conn.execute(
                    "SELECT * FROM rag_images WHERE document_id=? LIMIT ?",
                    (document_id, max(1, min(200, limit)))).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM rag_images ORDER BY id DESC LIMIT ?",
                    (max(1, min(200, limit)),)).fetchall()
        return [dict(row) for row in rows]

    def images_for_document(self, document_id: int, limit: int = 12) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM rag_images WHERE document_id=? ORDER BY page,image_index LIMIT ?",
                (document_id, max(1, min(50, limit)))).fetchall()
        return [dict(row) for row in rows]

    def stats(self) -> dict:
        with self._connect() as conn:
            chunks = conn.execute("SELECT COUNT(*) FROM rag_chunks").fetchone()[0]
            docs = conn.execute("SELECT COUNT(DISTINCT document_id) FROM rag_chunks").fetchone()[0]
            images = conn.execute("SELECT COUNT(*) FROM rag_images").fetchone()[0]
        return {"documents": docs, "chunks": chunks, "images": images,
                "vector_backend": "sqlite-vec" if self._vec_available else "sqlite-bruteforce"}


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


class RagIngestor:
    def __init__(self, database_root: str, embedder=None,
                 embedding_model: str = DEFAULT_MODEL, cache_dir: Optional[str] = None):
        self.kb = KnowledgeBase(database_root)
        self.store = RagStore(database_root)
        self.embedder = embedder
        self.embedding_model = embedding_model
        self.cache_dir = cache_dir
        self.model = embedding_model

    def get_embedder(self):
        if self.embedder is None:
            self.embedder = create_embedder(self.embedding_model, self.cache_dir)
        return self.embedder

    @staticmethod
    def _file_sha(path: str) -> str:
        import hashlib
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    def ingest_pdf(self, path: str, title: str = "", description: str = "",
                   tags: str = "", ocr_images: bool = False,
                   force: bool = False) -> dict:
        path = os.path.abspath(os.path.expanduser(path))
        sha = self._file_sha(path)
        existing = self.store.document_indexed(path, sha)
        if existing and not force:
            return {"document": existing, "skipped": True, "chunks": existing["chunks"]}
        parsed = extract_pdf(path, ocr_images=ocr_images)
        meta = parsed["metadata"]
        title = title or str(meta.get("Title") or Path(path).stem)
        description = description or " ".join(
            filter(None, [str(meta.get("Author") or ""), str(meta.get("Subject") or "")]))
        doc = self.kb.add_file(path, "pdfs", title=title,
                               description=description, tags=tags)
        pages = parsed["pages"]
        chunks = []
        for page in pages:
            chunks.extend(chunk_text(page["text"], page=page["page"]))
        images = parsed["images"]
        image_dir = os.path.join(self.kb.root, "images", "papers", str(doc["id"]))
        os.makedirs(image_dir, exist_ok=True)
        for image in images:
            image_data, image_ext = _normalize_image_bytes(image.data, image.ext)
            name = f"page-{image.page}-img-{image.index}.{image_ext or 'png'}"
            image_path = os.path.join(image_dir, name)
            try:
                with open(image_path, "wb") as handle:
                    handle.write(image_data)
            except OSError:
                continue
            image.path = image_path
            self.store.add_image(doc["id"], image.page, image.index, image_path,
                                 image.caption, image.ocr_text)
            text = "\n".join(x for x in (image.caption, image.ocr_text) if x).strip()
            if text:
                chunks.append(Chunk(text, image.page, "图片/图表", "image", image_path))
        vectors = self.get_embedder().embed([chunk.text for chunk in chunks]) if chunks else []
        count = self.store.add_chunks(doc["id"], chunks, vectors, self.model)
        return {"document": doc, "chunks": count, "images": len(images),
                "pages": len(pages), "metadata": meta}

    def ingest_html(self, path: str, title: str = "", description: str = "",
                    tags: str = "", source_url: str = "") -> dict:
        path = os.path.abspath(os.path.expanduser(path))
        sha = self._file_sha(path)
        existing = self.store.document_indexed(path, sha)
        if existing:
            return {"document": existing, "skipped": True, "chunks": existing["chunks"]}
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            raw = handle.read()
        page_title, text = extract_html(raw, source_url)
        doc = self.kb.add_file(path, "html", title=title or page_title or Path(path).stem,
                               description=description, tags=tags)
        chunks = chunk_text(text)
        vectors = self.get_embedder().embed([chunk.text for chunk in chunks]) if chunks else []
        count = self.store.add_chunks(doc["id"], chunks, vectors, self.model)
        return {"document": doc, "chunks": count, "title": page_title}

    def ingest_url(self, url: str, title: str = "", description: str = "",
                   tags: str = "", html_text: Optional[str] = None) -> dict:
        existing = self.store.document_indexed(url)
        if existing:
            return {"document": existing, "skipped": True, "chunks": existing["chunks"]}
        if html_text is None:
            import requests
            response = requests.get(url, timeout=(10, 30),
                                    headers={"User-Agent": "desktop-bot-rag/1.0"})
            response.raise_for_status()
            html_text = response.text
        title_text, text = extract_html(html_text, url)
        doc = self.kb.add_url(url, title=title or title_text,
                              description=description, tags=tags)
        html_dir = os.path.join(self.kb.root, "html")
        os.makedirs(html_dir, exist_ok=True)
        safe = re.sub(r"[^\w.-]+", "-", urlparse(url).netloc + urlparse(url).path)[:100]
        html_path = os.path.join(html_dir, f"{safe or 'page'}.html")
        with open(html_path, "w", encoding="utf-8") as handle:
            handle.write(html_text)
        chunks = chunk_text(text)
        vectors = self.get_embedder().embed([chunk.text for chunk in chunks]) if chunks else []
        count = self.store.add_chunks(doc["id"], chunks, vectors, self.model)
        return {"document": doc, "chunks": count, "html": html_path}

    def ingest_text(self, path: str, title: str = "", description: str = "",
                    tags: str = "") -> dict:
        path = os.path.abspath(os.path.expanduser(path))
        sha = self._file_sha(path)
        existing = self.store.document_indexed(path, sha)
        if existing:
            return {"document": existing, "skipped": True, "chunks": existing["chunks"]}
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            text = handle.read()
        doc = self.kb.add_file(path, category="markdown", title=title or Path(path).stem,
                               description=description, tags=tags)
        chunks = chunk_text(text)
        vectors = self.get_embedder().embed([chunk.text for chunk in chunks]) if chunks else []
        return {"document": doc, "chunks": self.store.add_chunks(
            doc["id"], chunks, vectors, self.model), "skipped": False}

    def ingest_urls_file(self, path: str, limit: Optional[int] = None) -> dict:
        done = errors = 0
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if limit and done >= limit:
                    break
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                    self.ingest_url(data["url"], data.get("title", ""),
                                    data.get("description", ""),
                                    " ".join(data.get("tags") or []),
                                    data.get("html"))
                    done += 1
                except Exception:                                 # noqa: BLE001
                    errors += 1
        return {"urls": done, "errors": errors}

    def ingest_directory(self, folder: str, recursive: bool = True,
                         ocr_images: bool = False, force: bool = False) -> dict:
        folder = os.path.abspath(folder)
        count = errors = 0
        skip_files = {"catalog.json", "index.sqlite3",
                      "index.sqlite3-wal", "index.sqlite3-shm"}
        for dirpath, dirs, files in os.walk(folder):
            # 密码库和自动生成的元文件不应进入 RAG。
            dirs[:] = [name for name in dirs if name.lower() != "passwords"]
            if not recursive and os.path.abspath(dirpath) != folder:
                continue
            for name in files:
                lower = name.lower()
                if lower in skip_files:
                    continue
                if lower.endswith((".md", ".markdown")) and lower.startswith(
                        ("index", "readme")):
                    continue
                path = os.path.join(dirpath, name)
                try:
                    if lower.endswith(".pdf"):
                        self.ingest_pdf(path, ocr_images=ocr_images, force=force)
                        count += 1
                    elif lower.endswith((".html", ".htm")):
                        self.ingest_html(path)
                        count += 1
                    elif lower.endswith((".txt", ".md", ".markdown")):
                        self.ingest_text(path)
                        count += 1
                except Exception:                                 # noqa: BLE001
                    errors += 1
        return {"documents": count, "errors": errors,
                "stats": self.store.stats()}


def build_rag_tools(database_root: str, base_dir: str = ".", read_only: bool = False,
                    embedder=None, stop_event=None,
                    embedding_model: str = DEFAULT_MODEL,
                    embedding_cache: Optional[str] = None) -> list:
    """返回 RAG 工具，供主智能体或 rag/librarian 子智能体使用。"""
    from .agent_framework import Tool, ToolResult
    from .reports import build_report_tool, write_literature_report

    root = os.path.abspath(database_root)
    ingestor = RagIngestor(root, embedder=embedder,
                           embedding_model=embedding_model, cache_dir=embedding_cache)
    reports = build_report_tool(os.path.join(os.path.dirname(root), "temp", "reports"))

    def search(args):
        query = str(args.get("query") or "").strip()
        if not query:
            return ToolResult("缺少 query。", is_error=True)
        vector = ingestor.get_embedder().embed([query])[0]
        rows = ingestor.store.search(query, vector,
                                     int(args.get("top_k", 8) or 8))
        if not rows:
            return ToolResult("RAG 索引中没有找到相关内容。")
        lines = []
        for row in rows:
            lines.append(
                f"### {row.get('title') or row.get('path')}\n"
                f"- 来源：{row.get('source') or row.get('path')}"
                + (f"（第 {row['page']} 页）" if row.get("page") else "")
                + f"\n- 类型：{row.get('kind')}\n\n{row.get('text', '')[:1200]}")
        return ToolResult("\n\n".join(lines))

    def stats(_args):
        data = ingestor.store.stats()
        return ToolResult("RAG：" + "，".join(f"{k}={v}" for k, v in data.items()))

    def image_describe(args):
        path = str(args.get("path") or "").strip()
        images = ingestor.store.list_images()
        if path:
            images = [item for item in images if os.path.basename(item["path"]) ==
                      os.path.basename(path) or item["path"] == path]
        if not images:
            return ToolResult("没有找到图片资产；PDF 入库后会自动抽取内嵌图片。")
        lines = []
        for item in images[:8]:
            lines.append(
                f"- {item['path']}\n"
                f"  页码：{item.get('page') or '?'}\n"
                f"  图注：{item.get('caption') or '（未识别）'}\n"
                f"  OCR：{item.get('ocr_text') or '（无文字）'}\n"
                f"  描述：{item.get('description') or '（待补充）'}")
        return ToolResult("\n".join(lines))

    def literature_report(args):
        query = str(args.get("query") or "").strip()
        if not query:
            return ToolResult("缺少 query。", is_error=True)
        title = str(args.get("title") or "文献速查报告").strip()
        vector = ingestor.get_embedder().embed([query])[0]
        rows = ingestor.store.search(query, vector, int(args.get("top_k", 12) or 12))
        if not rows:
            return ToolResult("RAG 索引中没有找到相关文献。")
        grouped: dict[int, dict] = {}
        for row in rows:
            doc_id = row.get("document_id")
            if doc_id not in grouped:
                grouped[doc_id] = {"row": row, "texts": [], "images": []}
            grouped[doc_id]["texts"].append(row)
        items = []
        for doc_id, group in grouped.items():
            row = group["row"]
            source = os.path.abspath(str(row.get("source") or row.get("path") or ""))
            label = row.get("title") or Path(source).stem
            chunks = [(chunk.get("text") or "").strip() for chunk in group["texts"]]
            summary = "\n\n".join(text[:1200] for text in chunks if text)
            images = [
                {"path": image["path"],
                 "caption": image.get("caption") or image.get("description") or ""}
                for image in ingestor.store.images_for_document(doc_id)
            ]
            items.append({
                "title": label,
                "pdf": source,
                "page": f"第 {row['page']} 页" if row.get("page") else "",
                "meta": "RAG 混合检索结果",
                "summary": summary,
                "images": images,
            })
        path = write_literature_report(
            os.path.join(os.path.dirname(root), "temp", "reports"),
            title, items, f"RAG 文献报告 · 检索：{query}")
        return ToolResult(f"文献报告已生成，任务结束后会自动在浏览器打开：{path}")

    tools = [
        Tool("rag_search",
             "对论文/HTML 知识库做混合检索（FTS + 本地向量），返回带来源和页码的片段。",
             {"type": "object", "properties": {
                 "query": {"type": "string"},
                 "top_k": {"type": "integer"}},
              "required": ["query"]}, search),
        Tool("rag_stats", "查看 RAG 文档、分块、图片与向量后端统计。",
             {"type": "object", "properties": {}}, stats),
        Tool("rag_literature_report",
             "根据 RAG 检索结果生成文献 HTML 报告：包含 PDF 原文超链接、绝对路径、相关页码、文献图片和图片绝对路径。",
             {"type": "object", "properties": {
                 "query": {"type": "string"}, "title": {"type": "string"},
                 "top_k": {"type": "integer"}},
              "required": ["query"]}, literature_report),
        Tool("image_describe",
             "查看 PDF 内嵌图片的路径、页码、图注与 OCR 文本；可再交给多模态模型描述。",
             {"type": "object", "properties": {
                 "path": {"type": "string", "description": "可选，按图片路径筛选"}}},
             image_describe),
    ]
    if not read_only:
        tools.append(reports)
    if read_only:
        return tools
    tools.extend([
        Tool("rag_ingest_pdf",
             "解析 PDF 论文（正文、表格、内嵌图片、页码），分块并建立本地向量索引。",
             {"type": "object", "properties": {
                 "path": {"type": "string"}, "title": {"type": "string"},
                 "description": {"type": "string"}, "tags": {"type": "string"},
                 "ocr_images": {"type": "boolean"}},
              "required": ["path"]},
             lambda args: _wrap(ingestor.ingest_pdf(
                 _resolve(base_dir, args.get("path")), str(args.get("title") or ""),
                 str(args.get("description") or ""), str(args.get("tags") or ""),
                 bool(args.get("ocr_images", False))), stop_event)),
        Tool("rag_ingest_html",
             "解析本地 HTML 或 URL 并建立 RAG 索引；URL 只在导入时抓取一次。",
             {"type": "object", "properties": {
                 "path": {"type": "string"},
                 "url": {"type": "string"},
                 "title": {"type": "string"}, "description": {"type": "string"},
                 "tags": {"type": "string"}}},
             lambda args: _wrap(
                 ingestor.ingest_url(str(args["url"]), str(args.get("title") or ""),
                                     str(args.get("description") or ""),
                                     str(args.get("tags") or ""))
                 if args.get("url") else
                 ingestor.ingest_html(_resolve(base_dir, args.get("path")),
                                      str(args.get("title") or ""),
                                      str(args.get("description") or ""),
                                      str(args.get("tags") or "")), stop_event)),
        Tool("rag_ingest_text",
             "导入本地 txt/markdown 文本并分块建立检索索引。",
             {"type": "object", "properties": {
                 "path": {"type": "string"}, "title": {"type": "string"},
                 "description": {"type": "string"}, "tags": {"type": "string"}},
              "required": ["path"]},
             lambda args: _wrap(ingestor.ingest_text(
                 _resolve(base_dir, args.get("path")), str(args.get("title") or ""),
                 str(args.get("description") or ""), str(args.get("tags") or "")),
                 stop_event)),
        Tool("rag_ingest_urls",
             "批量导入 urls.jsonl 中的 URL；每条 URL 只在首次抓取一次。",
             {"type": "object", "properties": {
                 "path": {"type": "string"}, "limit": {"type": "integer"}},
              "required": ["path"]},
             lambda args: _wrap(ingestor.ingest_urls_file(
                 _resolve(base_dir, args.get("path")), args.get("limit")), stop_event)),
        Tool("rag_reindex",
             "重新扫描 knowledge database 的 PDF/HTML，并重建分块与向量索引；force=true 会重新解析已索引 PDF 以补全图片。",
             {"type": "object", "properties": {
                 "folder": {"type": "string"},
                 "force": {"type": "boolean"}},
              "required": []},
             lambda args: _wrap(ingestor.ingest_directory(
                 _resolve(base_dir, args.get("folder") or os.path.join(root, "papers")),
                 force=bool(args.get("force", False))), stop_event)),
    ])
    return tools


def _resolve(base_dir: str, path) -> str:
    path = os.path.expanduser(str(path or ""))
    return path if os.path.isabs(path) else os.path.join(base_dir, path)


def _wrap(result: dict, stop_event=None):
    if stop_event is not None and stop_event.is_set():
        from .agent_framework import ToolResult
        return ToolResult("已停止。", is_error=True)
    from .agent_framework import ToolResult
    return ToolResult(json.dumps(result, ensure_ascii=False, default=str)[:8000])
