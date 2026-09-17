# -*- coding: utf-8 -*-
"""检查 docs/diagrams 里每张插图在 A4 版心里是否「放得下」并且「看得清」。

插图的质量有两个独立的失败模式，必须在生成 PDF 之前拦住：

1. **放不下** —— 图太高，一页装不完，Chromium 会把它裁掉一截；
2. **看不清** —— 图太宽，缩进版心之后源字号被压到 6pt 以下，纸面上等于一片灰。

第二条是这个脚本存在的真正理由。Mermaid 只管把图画出来，它不知道版心有多宽；
一张 1100px 宽的横向流程图缩到 174mm 之后，14px 的标签只剩 6pt 左右。
与其等到打印出来才发现，不如在这里算一遍：印刷字号 = 源字号 × (版心宽 / 图画宽)。

用法::

    python tools/check_diagram_fit.py            # 检查，有不合格项时以非零码退出
    python tools/check_diagram_fit.py --list     # 只打印，不改变退出码
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import List, Tuple

# A4 (210×297mm) 减去 style.css 里 @page 的左右 18mm、上 20mm 下 22mm
CONTENT_WIDTH_MM = 210 - 18 - 18
CONTENT_HEIGHT_MM = 297 - 20 - 22

#: tools/pdf/mermaid-config.json 里的基础字号（px）
SOURCE_FONT_PX = 14.0
#: 印刷可读下限。技术文档的插图标签一般不小于 6pt（约等于脚注）
MIN_FONT_PT = 6.0
#: 留一点余量：图注也要占位置，图本身不该吃满整页
MAX_HEIGHT_RATIO = 0.86

MM_PER_INCH = 25.4

_VIEWBOX_RE = re.compile(r'viewBox="([\d.\-\s]+)"')


def _parse_viewbox(text: str) -> Tuple[float, float] | None:
    match = _VIEWBOX_RE.search(text)
    if not match:
        return None
    parts = [float(value) for value in match.group(1).split()]
    if len(parts) != 4 or parts[2] <= 0 or parts[3] <= 0:
        return None
    return parts[2], parts[3]


def check(diagram_dir: Path) -> List[Tuple[str, float, float, float, List[str]]]:
    """返回 ``[(图名, 缩放后高度mm, 占版心比例, 实际字号pt, 问题列表)]``。"""
    rows = []
    for path in sorted(diagram_dir.glob("*.svg")):
        size = _parse_viewbox(path.read_text(encoding="utf-8")[:4000])
        if size is None:
            rows.append((path.stem, 0.0, 0.0, 0.0, ["缺少可用的 viewBox"]))
            continue

        width, height = size
        # 图被等比缩放到版心宽度，这个比例同时决定高度与实际字号
        scale = CONTENT_WIDTH_MM / width          # mm / 用户单位
        height_mm = height * scale
        ratio = height_mm / CONTENT_HEIGHT_MM
        # 14px → ×scale 得到毫米 → 换成磅
        font_pt = SOURCE_FONT_PX * scale * (72.0 / MM_PER_INCH)

        problems = []
        if ratio > MAX_HEIGHT_RATIO:
            problems.append(f"占版心 {ratio * 100:.0f}%，会被裁切或顶掉图注")
        if font_pt < MIN_FONT_PT:
            problems.append(f"印刷字号只有 {font_pt:.1f}pt，低于 {MIN_FONT_PT}pt 下限")
        rows.append((path.stem, height_mm, ratio, font_pt, problems))
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="插图版心适配检查")
    parser.add_argument("--dir", default="docs/diagrams", help="SVG 所在目录")
    parser.add_argument("--list", action="store_true", help="只打印，始终返回 0")
    args = parser.parse_args()

    # Windows 控制台默认是 GBK，判定符号会直接抛 UnicodeEncodeError
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

    root = Path(__file__).resolve().parent.parent
    diagram_dir = root / args.dir if not Path(args.dir).is_absolute() else Path(args.dir)
    if not diagram_dir.is_dir():
        print(f"找不到插图目录：{diagram_dir}", file=sys.stderr)
        return 2

    rows = check(diagram_dir)
    if not rows:
        print(f"{diagram_dir} 下没有 SVG；请先运行 tools/render_diagrams.ps1", file=sys.stderr)
        return 2

    print(
        f"版心 {CONTENT_WIDTH_MM} × {CONTENT_HEIGHT_MM} mm；"
        f"源字号 {SOURCE_FONT_PX:.0f}px；高度上限 {MAX_HEIGHT_RATIO * 100:.0f}%；"
        f"字号下限 {MIN_FONT_PT}pt\n"
    )
    print(f"{'图':<24}{'高(mm)':>8}{'占版心':>9}{'印刷字号':>11}   结论")
    print("-" * 78)

    bad = 0
    for name, height_mm, ratio, font_pt, problems in rows:
        verdict = "；".join(problems) if problems else "合格"
        bad += 1 if problems else 0
        mark = "✗ " if problems else "✓ "
        print(f"{mark}{name:<22}{height_mm:>8.0f}{ratio * 100:>8.0f}%{font_pt:>10.1f}pt   {verdict}")

    print()
    if bad:
        print(f"{bad}/{len(rows)} 张图不合格。请在 tools/pdf/diagrams/*.mmd 里收窄布局后重新渲染。")
        return 0 if args.list else 1
    print(f"全部 {len(rows)} 张图合格。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
