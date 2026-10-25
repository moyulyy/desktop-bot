# -*- coding: utf-8 -*-
"""把关键页渲染成 ASCII 文件名的 PNG，便于报告引用"""
import os, sys, json, shutil
import pymupdf
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PDF_DIR = os.path.join(BASE, "database", "pdfs")
OUT = os.path.join(BASE, "database", "images", "cer_steps", "en")
os.makedirs(OUT, exist_ok=True)

JOBS = [
    ("ru_sa_angew", "Ru 单原子", [9, 10], 200),
    ("ru4o8_small", "钌氧化物簇", [6, 7], 200),
    ("fe_ti4o7", "氯消毒剂", [6], 200),
    ("hea_nanowire", "高熵合金", [6, 7], 200),
    ("ru_o4_natcomm", "单原子催化剂", [7, 8], 200),
    ("pdo2_cej", "介稳态氧化钯", [7], 200),
]

pdfs = [f for f in os.listdir(PDF_DIR) if f.lower().endswith(".pdf")]
out = []
for slug, key, pages, dpi in JOBS:
    cand = [f for f in pdfs if key in f]
    if not cand:
        print("MISSING", slug); continue
    path = os.path.join(PDF_DIR, cand[0])
    doc = pymupdf.open(path)
    for p in pages:
        if p > doc.page_count:
            continue
        pix = doc[p - 1].get_pixmap(dpi=dpi)
        fn = os.path.join(OUT, f"{slug}_p{p}.png")
        pix.save(fn)
        out.append({"slug": slug, "pdf": path, "page": p, "img": fn,
                    "size": f"{pix.width}x{pix.height}"})
    doc.close()
print(json.dumps(out, ensure_ascii=False, indent=1))
