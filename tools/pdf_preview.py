# -*- coding: utf-8 -*-
"""把 PDF 逐页转成 PNG，用于人工核对版面。

这是**开发期工具**，不属于运行时依赖，因此没有写进 requirements.txt。
它需要 PyMuPDF：

    .venv\\Scripts\\python.exe -m pip install pymupdf

用法::

    python tools/pdf_preview.py docs/TECHNICAL.pdf build/preview 96
    python tools/pdf_preview.py docs/TECHNICAL.pdf build/preview 120 --pages 1,2,6
"""

from __future__ import annotations

import argparse
import pathlib
import sys


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # pragma: no cover
        pass

    parser = argparse.ArgumentParser(description="PDF 转 PNG（版面核对用）")
    parser.add_argument("pdf", help="输入 PDF")
    parser.add_argument("outdir", help="输出目录")
    parser.add_argument("dpi", nargs="?", type=int, default=110, help="渲染分辨率，默认 110")
    parser.add_argument("--pages", default="", help="只渲染指定页，例如 1,2,6；缺省全部")
    args = parser.parse_args()

    try:
        import pymupdf  # type: ignore
    except ImportError:
        print("需要 PyMuPDF：.venv\\Scripts\\python.exe -m pip install pymupdf", file=sys.stderr)
        return 2

    pdf_path = pathlib.Path(args.pdf)
    if not pdf_path.exists():
        print(f"找不到文件：{pdf_path}", file=sys.stderr)
        return 2

    outdir = pathlib.Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    wanted = set()
    if args.pages:
        for part in args.pages.split(","):
            part = part.strip()
            if part:
                wanted.add(int(part))

    document = pymupdf.open(str(pdf_path))
    try:
        print(f"页数：{document.page_count}，页面尺寸：{document[0].rect}")
        produced = 0
        for index, page in enumerate(document, 1):
            if wanted and index not in wanted:
                continue
            pixmap = page.get_pixmap(dpi=args.dpi)
            target = outdir / f"p-{index:02d}.png"
            pixmap.save(str(target))
            produced += 1
            print(f"  {target}  {pixmap.width}x{pixmap.height}")
        print(f"已输出 {produced} 张到 {outdir}")
    finally:
        document.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
