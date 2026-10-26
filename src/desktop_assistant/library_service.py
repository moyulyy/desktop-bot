# -*- coding: utf-8 -*-
"""知识库整理、消息附件临时区与后台更新任务。"""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime
from typing import Callable, Optional

from PySide6.QtCore import QThread, Signal

from .database import KnowledgeBase, infer_category
from .rag import RagIngestor


def _inside(root: str, path: str) -> bool:
    root = os.path.normcase(os.path.abspath(root))
    path = os.path.normcase(os.path.abspath(path))
    try:
        return os.path.commonpath([root, path]) == root
    except ValueError:
        return False


class ChatTempStore:
    """输入框附件和消息的临时区；更新知识库后清空。"""

    def __init__(self, temp_root: str):
        self.root = os.path.abspath(temp_root)
        self.chat_dir = os.path.join(self.root, "chat")
        self.files_dir = os.path.join(self.chat_dir, "files")
        self.log_path = os.path.join(self.chat_dir, "messages.jsonl")
        os.makedirs(self.files_dir, exist_ok=True)

    def _name(self, source: str) -> str:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
        base = os.path.basename(source)
        return os.path.join(self.files_dir, f"{stamp}-{base}")

    def save_attachment(self, source: str) -> str:
        source = os.path.abspath(os.path.expanduser(source))
        if not os.path.isfile(source):
            raise FileNotFoundError(source)
        destination = self._name(source)
        shutil.copy2(source, destination)
        return destination

    def save_image(self, image) -> str:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
        path = os.path.join(self.files_dir, f"{stamp}-paste.png")
        if not image.save(path, "PNG"):
            raise OSError("图片保存失败")
        return path

    def log_message(self, role: str, text: str, attachments: list[str]):
        os.makedirs(self.chat_dir, exist_ok=True)
        with open(self.log_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "ts": datetime.now().isoformat(timespec="seconds"),
                "role": role, "text": text, "attachments": attachments,
            }, ensure_ascii=False) + "\n")

    def promote(self, database_root: str) -> list[str]:
        """把临时附件放进 database/inbox，并把消息记录写成 Markdown。"""
        if not _inside(self.root, self.files_dir):
            raise PermissionError("临时目录越界")
        kb = KnowledgeBase(database_root)
        inbox = os.path.join(kb.root, "inbox")
        os.makedirs(inbox, exist_ok=True)
        paths = []
        if os.path.isdir(self.files_dir):
            for name in os.listdir(self.files_dir):
                source = os.path.join(self.files_dir, name)
                if os.path.isfile(source):
                    destination = os.path.join(inbox, name)
                    shutil.copy2(source, destination)
                    paths.append(destination)
        if os.path.isfile(self.log_path):
            messages = []
            with open(self.log_path, "r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    try:
                        messages.append(json.loads(line))
                    except ValueError:
                        pass
            if messages:
                stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                transcript = os.path.join(kb.root, "markdown", f"chat-{stamp}.md")
                os.makedirs(os.path.dirname(transcript), exist_ok=True)
                with open(transcript, "w", encoding="utf-8") as handle:
                    handle.write(f"# 对话记录 {stamp}\n\n")
                    for message in messages:
                        handle.write(f"## {message.get('role')} · {message.get('ts')}\n\n")
                        handle.write(str(message.get("text") or "") + "\n\n")
                        for path in message.get("attachments") or []:
                            handle.write(f"- 附件：{path}\n")
                        handle.write("\n")
                paths.append(transcript)
        return paths

    def clear(self):
        if _inside(self.root, self.chat_dir):
            shutil.rmtree(self.chat_dir, ignore_errors=True)
        os.makedirs(self.files_dir, exist_ok=True)

    def pending(self) -> int:
        if not os.path.isdir(self.files_dir):
            return 0
        return sum(1 for name in os.listdir(self.files_dir)
                   if os.path.isfile(os.path.join(self.files_dir, name)))


class LibraryOrganizer:
    """写入仅限 database；自动/手动整理都会读取 temp 并写回 database。"""

    def __init__(self, database_root: str, temp_root: str,
                 embedding_model: Optional[str] = None,
                 embedding_cache: Optional[str] = None):
        self.root = os.path.abspath(database_root)
        self.temp = ChatTempStore(temp_root)
        self.kb = KnowledgeBase(self.root)
        self.rag = RagIngestor(self.root, embedding_model=embedding_model,
                               cache_dir=embedding_cache)

    def import_files(self, paths: list[str],
                     progress: Optional[Callable[[int, str], None]] = None) -> dict:
        total = max(1, len(paths))
        imported = []
        for index, source in enumerate(paths):
            source = os.path.abspath(os.path.expanduser(source))
            if not os.path.isfile(source):
                continue
            category = infer_category(source)
            if "screenshot" in os.path.basename(source).lower() and category == "images":
                category = "screenshots"
            if progress:
                progress(int(index / total * 100), f"正在整理 {os.path.basename(source)}")
            doc = self.kb.add_file(source, category, description="通过悬浮球添加")
            lower = source.lower()
            if lower.endswith(".pdf"):
                self.rag.ingest_pdf(doc["full_path"])
            elif lower.endswith((".html", ".htm")):
                self.rag.ingest_html(doc["full_path"])
            elif lower.endswith((".txt", ".md", ".markdown")):
                self.rag.ingest_text(doc["full_path"])
            imported.append(doc["path"])
        if progress:
            progress(100, "文件已加入知识库")
        return {"imported": imported, "stats": self.rag.store.stats()}

    def organize(self, include_temp: bool = False,
                 progress: Optional[Callable[[int, str], None]] = None) -> dict:
        temp_paths = []
        if include_temp:
            if progress:
                progress(2, "正在接收消息附件")
            temp_paths = self.temp.promote(self.root)
        if progress:
            progress(8, "正在重建知识库索引")
        self.kb.reindex(
            progress=(lambda done, total, name: progress(
                8 + int(done / max(1, total) * 10), f"正在更新索引 {name}"))
            if progress else None)
        if progress:
            progress(18, "正在解析 PDF / HTML / 文本")
        result = self.rag.ingest_directory(
            self.root,
            progress=(lambda done, total, name: progress(
                18 + int(done / max(1, total) * 76), f"正在解析 {name}"))
            if progress else None)
        if progress:
            progress(96, "正在更新向量索引与目录")
        pruned = self.rag.store.prune_images(30)
        self.kb.reindex()
        if include_temp:
            self.temp.clear()
        if progress:
            progress(100, "知识库已更新")
        return {"result": result, "temp_files": len(temp_paths),
                "pruned_images": pruned,
                "stats": self.rag.store.stats()}


class LibraryWorker(QThread):
    progress = Signal(int, str)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, organizer: LibraryOrganizer, paths=None, include_temp=False):
        super().__init__()
        self.organizer = organizer
        self.paths = paths
        self.include_temp = include_temp

    def run(self):
        try:
            if self.paths:
                result = self.organizer.import_files(
                    self.paths, lambda p, m: self.progress.emit(p, m))
            else:
                result = self.organizer.organize(
                    self.include_temp, lambda p, m: self.progress.emit(p, m))
            self.finished.emit(result)
        except Exception as exc:                                  # noqa: BLE001
            self.failed.emit(str(exc))
