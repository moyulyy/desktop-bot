# -*- coding: utf-8 -*-
import os

from desktop_assistant.library_service import ChatTempStore, LibraryOrganizer
from desktop_assistant.ocr import _extract_text
from desktop_assistant.rag import HashEmbedder


def test_chat_temp_store_and_promote(tmp_path):
    store = ChatTempStore(str(tmp_path / "temp"))
    source = tmp_path / "note.txt"
    source.write_text("临时消息附件", encoding="utf-8")
    saved = store.save_attachment(str(source))
    store.log_message("user", "查看附件", [saved])
    assert store.pending() == 1
    promoted = store.promote(str(tmp_path / "database"))
    assert promoted
    assert os.path.isfile(os.path.join(tmp_path, "database", "markdown",
                                       os.path.basename(promoted[-1])))
    store.clear()
    assert store.pending() == 0


def test_library_import_and_organize(tmp_path):
    db = tmp_path / "database"
    temp = tmp_path / "temp"
    source = tmp_path / "paper.md"
    source.write_text("# 火星计划\n\n这是论文文本。", encoding="utf-8")
    organizer = LibraryOrganizer(str(db), str(temp),
                                 embedding_model="__hash__")
    organizer.rag.embedder = HashEmbedder()
    result = organizer.import_files([str(source)])
    assert result["imported"]
    assert organizer.kb.search("火星计划")
    result = organizer.organize(include_temp=False)
    assert result["stats"]["documents"] >= 1


def test_umi_response_text_extraction():
    assert _extract_text({"code": 100, "data": "火星 OCR"}) == "火星 OCR"
    assert _extract_text({"data": [{"text": "第一行"}, {"text": "第二行"}]}) == "第一行\n第二行"
    assert _extract_text({"result": {"text": "nested"}}) == "nested"


def test_manual_organize_reads_and_clears_temp(tmp_path):
    store = ChatTempStore(str(tmp_path / "temp"))
    source = tmp_path / "chat.txt"
    source.write_text("自动任务应该读取这条临时消息", encoding="utf-8")
    saved = store.save_attachment(str(source))
    store.log_message("user", "临时消息", [saved])
    organizer = LibraryOrganizer(str(tmp_path / "database"), str(tmp_path / "temp"),
                                 embedding_model="__hash__")
    organizer.rag.embedder = HashEmbedder()
    organizer.organize(include_temp=True)
    assert store.pending() == 0
    assert not os.path.exists(os.path.join(tmp_path, "temp", "chat", "messages.jsonl"))
    assert organizer.kb.search("临时消息")
