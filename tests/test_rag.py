# -*- coding: utf-8 -*-
from desktop_assistant.rag import HashEmbedder, RagIngestor, chunk_text


def test_chunking_preserves_pages_and_text():
    chunks = chunk_text("第一段 " * 300 + "\n\n第二段 火星计划", chunk_tokens=120, overlap=20, page=3)
    assert chunks
    assert all(chunk.page == 3 for chunk in chunks)
    assert "火星计划" in chunks[-1].text


def test_rag_ingests_html_and_hybrid_search(tmp_path):
    source = tmp_path / "paper.html"
    source.write_text(
        "<html><head><title>火星计划论文</title></head><body>"
        "<article><h1>Mars Mission</h1><p>这份论文讨论火星殖民与预算。</p>"
        "<p>Q3 budget and launch window.</p></article></body></html>",
        encoding="utf-8")
    ingestor = RagIngestor(str(tmp_path / "database"), embedder=HashEmbedder())
    result = ingestor.ingest_html(str(source), description="火星预算")
    assert result["chunks"] >= 1
    stats = ingestor.store.stats()
    assert stats["documents"] == 1
    query = "火星预算"
    rows = ingestor.store.search(query, ingestor.get_embedder().embed([query])[0])
    assert rows
    assert any("火星" in row["text"] for row in rows)
