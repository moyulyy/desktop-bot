# -*- coding: utf-8 -*-
"""定位 CER 文献中'自由能台阶图'所在页面与图注"""
import os, sys
import pymupdf
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PDF_DIR = os.path.join(BASE, "database", "pdfs")
KEYS = ["Ru 单原子", "钌氧化物簇", "氯消毒剂", "高熵合金", "单原子催化剂", "介稳态氧化钯"]
KW = ["free energy", "free-energy", "Gibbs free", "energy diagram", "energy profile",
      "step diagram", "台阶", "自由能"]

pdfs = [f for f in os.listdir(PDF_DIR) if f.lower().endswith(".pdf")]
for key in KEYS:
    f = [x for x in pdfs if key in x]
    if not f:
        print(f"== {key}: 未找到"); continue
    path = os.path.join(PDF_DIR, f[0])
    doc = pymupdf.open(path)
    print(f"\n===== {key} | {f[0]} | 共 {doc.page_count} 页 =====")
    for i, page in enumerate(doc):
        t = page.get_text()
        low = t.lower()
        hits = [k for k in KW if k.lower() in low]
        if not hits:
            continue
        # 找出图注行
        caps = []
        for line in t.splitlines():
            ll = line.lower()
            if ("fig." in ll or "figure" in ll) and ("free energy" in ll or "free-energy" in ll or "energy diagram" in ll or "energy profile" in ll):
                caps.append(line.strip()[:160])
        print(f"-- PDF第{i+1}页 命中{hits}")
        for c in caps[:4]:
            print("     图注:", c)
    doc.close()
