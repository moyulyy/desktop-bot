# -*- coding: utf-8 -*-
"""提取 CER 文献中自由能台阶图所在页面的图片（含整页渲染 + 内嵌图）"""
import os, json
import pymupdf

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PDF_DIR = os.path.join(BASE, "database", "pdfs")
OUT = os.path.join(BASE, "database", "images", "cer_steps")
os.makedirs(OUT, exist_ok=True)

# 文件名关键词 -> 关注的页码(1-based, 来自 RAG 分块/图注)
TARGETS = {
    "Ru 单原子": [9, 10, 11],
    "钌氧化物簇": [6, 7, 8],
    "氯消毒剂": [5, 6, 7],
    "高熵合金": [6, 7, 8],
    "单原子催化剂": [7, 8, 9],
    "介稳态氧化钯": [6, 7, 8],
}

pdfs = [f for f in os.listdir(PDF_DIR) if f.lower().endswith(".pdf")]
report = []
for key, pages in TARGETS.items():
    cand = [f for f in pdfs if key in f]
    if not cand:
        report.append({"key": key, "error": "no file"})
        continue
    path = os.path.join(PDF_DIR, cand[0])
    doc = pymupdf.open(path)
    n = doc.page_count
    for p in pages:
        if p > n:
            continue
        page = doc[p - 1]
        # 1) 整页渲染
        pix = page.get_pixmap(dpi=130)
        name = f"{key}_p{p}_page.png"
        pix.save(os.path.join(OUT, name))
        report.append({"key": key, "pdf": path, "page": p, "file": os.path.join(OUT, name), "type": "page"})
        # 2) 内嵌位图
        for i, img in enumerate(page.get_images(full=True)):
            xref = img[0]
            try:
                pix2 = pymupdf.Pixmap(doc, xref)
                if pix2.width < 300 or pix2.height < 200:
                    continue
                if pix2.n - pix2.alpha > 3:
                    pix2 = pymupdf.Pixmap(pymupdf.csRGB, pix2)
                name2 = f"{key}_p{p}_img{i}.png"
                pix2.save(os.path.join(OUT, name2))
                report.append({"key": key, "pdf": path, "page": p,
                               "file": os.path.join(OUT, name2), "type": "embedded",
                               "w": pix2.width, "h": pix2.height})
            except Exception as e:
                pass
    doc.close()

print(json.dumps(report, ensure_ascii=False, indent=1))
