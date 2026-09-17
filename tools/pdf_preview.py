# -*- coding: utf-8 -*-
"""把 docs/TECHNICAL.pdf 的每一页导出成 PNG，用于人工核对版式。

同时打印每页的图片对象数与矢量路径数：插图是内联 SVG，
因此应当表现为「矢量路径很多、位图对象为零」。
"""
import pathlib
import sys

import fitz

sys.stdout.reconfigure(encoding="utf-8")

pdf = pathlib.Path("docs/TECHNICAL.pdf")
out = pathlib.Path("build/preview")
out.mkdir(parents=True, exist_ok=True)

doc = fitz.open(pdf)
print(f"{pdf} 共 {doc.page_count} 页")
for i in range(doc.page_count):
    page = doc[i]
    page.get_pixmap(dpi=110).save(str(out / f"p{i + 1:02d}.png"))
    print(f"p{i + 1:02d}  位图={len(page.get_images(full=True)):>2}  矢量路径={len(page.get_drawings()):>5}")
print(f"预览图已写入 {out}")
