# -*- coding: utf-8 -*-
"""论文/HTML/文本批量导入与检索 CLI。

用法（在项目根目录）：
  conda run -n desktop-rag env PYTHONPATH=src python -m desktop_assistant.rag_cli ingest-pdf paper.pdf
  conda run -n desktop-rag env PYTHONPATH=src python -m desktop_assistant.rag_cli ingest-dir database/papers
  conda run -n desktop-rag env PYTHONPATH=src python -m desktop_assistant.rag_cli ingest-text notes.md
  conda run -n desktop-rag env PYTHONPATH=src python -m desktop_assistant.rag_cli ingest-urls database/urls/urls.jsonl
  conda run -n desktop-rag env PYTHONPATH=src python -m desktop_assistant.rag_cli search "火星任务预算"
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from .config_store import PROJECT_ROOT, load_agent_config
from .rag import RagIngestor


def _ingestor(args):
    cfg = load_agent_config(apply_env=False)
    return RagIngestor(
        cfg.get("database") or os.path.join(PROJECT_ROOT, "database"),
        embedding_model=cfg.get("embedding_model"),
        cache_dir=cfg.get("embedding_cache"),
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description="Desktop Bot 本地 RAG 导入工具")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("ingest-pdf", "ingest-text", "ingest-urls"):
        cmd = sub.add_parser(name)
        cmd.add_argument("path")
    cmd = sub.add_parser("ingest-dir")
    cmd.add_argument("folder")
    cmd.add_argument("--ocr-images", action="store_true")
    cmd = sub.add_parser("search")
    cmd.add_argument("query")
    cmd.add_argument("--top-k", type=int, default=8)
    sub.add_parser("stats")
    args = parser.parse_args(argv)

    ing = _ingestor(args)
    if args.command == "ingest-pdf":
        result = ing.ingest_pdf(args.path)
    elif args.command == "ingest-text":
        result = ing.ingest_text(args.path)
    elif args.command == "ingest-urls":
        result = ing.ingest_urls_file(args.path)
    elif args.command == "ingest-dir":
        result = ing.ingest_directory(args.folder, ocr_images=args.ocr_images)
    elif args.command == "search":
        vector = ing.get_embedder().embed([args.query])[0]
        result = ing.store.search(args.query, vector, args.top_k)
    else:
        result = ing.store.stats()
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
