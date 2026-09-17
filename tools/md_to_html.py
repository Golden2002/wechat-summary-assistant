# -*- coding: utf-8 -*-
"""把项目文档（Markdown / GFM）转换成用于排版的 HTML。

为什么自己写而不用第三方库
--------------------------
本机没有安装 ``markdown`` 之类的库，而本项目的文档只用到**很有限**的语法子集：
ATX 标题、围栏代码块、GFM 表格、有序/无序列表、引用块、水平线、行内代码/加粗/链接，
以及少量原样透传的 HTML 块。为一个受控子集引入依赖并不划算，而且自己实现可以
精确控制排版所需的三件事：

1. 给每个标题生成**稳定锚点**，以便生成目录；
2. 给围栏代码块补上**语言标签**（排版模板会在右上角显示它）；
3. 保留 ASCII 流程图里的空白（技术文档里有大量用制表符绘制的框图）。

用法::

    python tools/md_to_html.py --input docs/TECHNICAL.md --output build/technical.html \\
        --template tools/pdf/template.html --css tools/pdf/style.css \\
        --title "技术说明" --version v1.0.0
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import re
import shutil
import unicodedata
from pathlib import Path
from typing import List, Optional, Tuple

__all__ = ["convert_markdown", "render_document"]

# --------------------------------------------------------------------------- #
# 占位符（保护行内代码与自动链接，避免被后续的强调规则误伤）
# --------------------------------------------------------------------------- #

_PH_OPEN = "\ue000"
_PH_CLOSE = "\ue001"
_PH_RE = re.compile(_PH_OPEN + r"(\d+)" + _PH_CLOSE)

_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_AUTOLINK_RE = re.compile(r"<(https?://[^>\s]+)>")
_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)\)")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_BOLD_RE = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*")
_ITALIC_RE = re.compile(r"(?<![\w*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![\w*])")

_FENCE_RE = re.compile(r"^(\s*)(`{3,}|~{3,})\s*([^\s`]*)\s*$")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_HR_RE = re.compile(r"^\s{0,3}(?:-{3,}|\*{3,}|_{3,})\s*$")
_UL_RE = re.compile(r"^(\s*)([-*+])\s+(.*)$")
_OL_RE = re.compile(r"^(\s*)(\d{1,4})[.)、]\s*(.*)$")
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)*\|?\s*$")

#: 这些标签开头的行按「原样透传的 HTML 块」处理
_HTML_BLOCK_PREFIXES = (
    "<table", "</table", "<thead", "<tbody", "<tr", "<div", "<details", "<summary",
    "<p ", "<p>", "</p>", "<span", "<img", "<br", "<hr", "<b>", "</b>", "<code", "</code",
    "<nav", "</nav", "<figure", "</figure", "<figcaption", "<!--",
)


# --------------------------------------------------------------------------- #
# 行内处理
# --------------------------------------------------------------------------- #


def _inline(text: str) -> str:
    """把一行文本里的行内标记转换成 HTML。"""
    store: List[str] = []

    def protect(match: "re.Match[str]", raw: str) -> str:
        store.append(raw)
        return f"{_PH_OPEN}{len(store) - 1}{_PH_CLOSE}"

    # 1) 行内代码：先抽走，内容原样保留（只做转义）
    text = _INLINE_CODE_RE.sub(lambda m: protect(m, f"<code>{html.escape(m.group(1))}</code>"), text)
    # 2) 自动链接
    text = _AUTOLINK_RE.sub(
        lambda m: protect(m, f'<a href="{html.escape(m.group(1), quote=True)}">{html.escape(m.group(1))}</a>'),
        text,
    )
    # 3) 转义剩余的裸文本
    text = html.escape(text, quote=False)
    # 4) 图片、链接、强调
    text = _IMAGE_RE.sub(
        lambda m: f'<img src="{html.escape(m.group(2), quote=True)}" alt="{html.escape(m.group(1))}">', text
    )
    text = _LINK_RE.sub(
        lambda m: f'<a href="{html.escape(m.group(2), quote=True)}">{m.group(1)}</a>', text
    )
    text = _BOLD_RE.sub(r"<strong>\1</strong>", text)
    text = _ITALIC_RE.sub(r"<em>\1</em>", text)

    # 5) 还原占位符
    def restore(match: "re.Match[str]") -> str:
        index = int(match.group(1))
        return store[index] if 0 <= index < len(store) else match.group(0)

    return _PH_RE.sub(restore, text)


def _slug(text: str) -> str:
    """由标题文本生成锚点，规则与 GitHub 接近（便于沿用文档里的站内链接）。"""
    plain = re.sub(r"<[^>]+>", "", text)
    plain = plain.strip().lower()
    out = []
    for ch in plain:
        category = unicodedata.category(ch)
        if ch in " -_":
            out.append("-")
        elif category[0] in "LN" or "\u4e00" <= ch <= "\u9fff":
            out.append(ch)
    slug = "".join(out)
    return re.sub(r"-{2,}", "-", slug).strip("-") or "section"


# --------------------------------------------------------------------------- #
# 块级处理
# --------------------------------------------------------------------------- #


def _split_table_row(line: str) -> List[str]:
    row = line.strip()
    if row.startswith("|"):
        row = row[1:]
    if row.endswith("|"):
        row = row[:-1]
    return [cell.strip() for cell in row.split("|")]


def convert_markdown(text: str, *, drop_toc: bool = True) -> Tuple[str, str]:
    """把 Markdown 转换成 ``(正文 HTML, 目录 HTML)``。

    :param drop_toc: 是否丢掉文档里原有的「目录」小节（排版模板会生成一份带样式的目录）
    """
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: List[str] = []
    headings: List[Tuple[int, str, str]] = []

    index = 0
    total = len(lines)
    skipping_toc = False

    while index < total:
        line = lines[index]

        # ---- 丢掉原有的目录小节 ------------------------------------------
        if drop_toc and re.match(r"^##\s*目录\s*$", line):
            skipping_toc = True
            index += 1
            continue
        if skipping_toc:
            if _HR_RE.match(line) or re.match(r"^##\s", line):
                skipping_toc = False
                # 水平线本身也一起丢掉，避免目录后多出一条分隔线
                if _HR_RE.match(line):
                    index += 1
                continue
            index += 1
            continue

        # ---- 围栏代码块 ---------------------------------------------------
        fence = _FENCE_RE.match(line)
        if fence:
            indent, marker, lang = fence.group(1), fence.group(2), fence.group(3)
            body: List[str] = []
            index += 1
            closing = re.compile(r"^\s*" + re.escape(marker[0]) + r"{" + str(len(marker)) + r",}\s*$")
            while index < total and not closing.match(lines[index]):
                body.append(lines[index])
                index += 1
            index += 1  # 跳过收尾围栏
            code = html.escape("\n".join(body))
            label = f'<span class="code-lang">{html.escape(lang)}</span>' if lang else ""
            out.append(f'<div class="code-block">{label}<pre><code>{code}</code></pre></div>')
            continue

        # ---- 标题 ---------------------------------------------------------
        heading = _HEADING_RE.match(line)
        if heading:
            level = len(heading.group(1))
            raw = heading.group(2)
            inner = _inline(raw)
            slug = _slug(raw)
            # 处理重名锚点
            base, n = slug, 2
            existing = {h[2] for h in headings}
            while slug in existing:
                slug = f"{base}-{n}"
                n += 1
            headings.append((level, inner, slug))
            out.append(f'<h{level} id="{slug}">{inner}</h{level}>')
            index += 1
            continue

        # ---- 水平线 -------------------------------------------------------
        if _HR_RE.match(line):
            out.append("<hr>")
            index += 1
            continue

        # ---- 表格 ---------------------------------------------------------
        if "|" in line and index + 1 < total and _TABLE_SEP_RE.match(lines[index + 1]) and "|" in lines[index + 1]:
            header = _split_table_row(line)
            index += 2
            rows: List[List[str]] = []
            while index < total and lines[index].strip() and "|" in lines[index]:
                rows.append(_split_table_row(lines[index]))
                index += 1
            head_html = "".join(f"<th>{_inline(c)}</th>" for c in header)
            body_html = "".join(
                "<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in row) + "</tr>" for row in rows
            )
            out.append(
                '<div class="table-wrap"><table>'
                f"<thead><tr>{head_html}</tr></thead><tbody>{body_html}</tbody>"
                "</table></div>"
            )
            continue

        # ---- 原样透传的 HTML 块 -------------------------------------------
        stripped = line.strip()
        if stripped.startswith(_HTML_BLOCK_PREFIXES):
            block: List[str] = []
            while index < total and lines[index].strip():
                block.append(lines[index])
                index += 1
            out.append("\n".join(block))
            continue

        # ---- 引用块（含嵌套） ---------------------------------------------
        if re.match(r"^\s*>", line):
            block = []
            while index < total and re.match(r"^\s*>", lines[index]):
                block.append(re.sub(r"^\s*>\s?", "", lines[index]))
                index += 1
            inner_html: List[str] = []
            for sub in block:
                if not sub.strip():
                    continue
                inner_html.append(f"<p>{_inline(sub)}</p>")
            cls = "callout callout-warn" if re.search(r"警告|注意|⚠️|风险", "\n".join(block)) else "callout"
            out.append(f'<blockquote class="{cls}">{"".join(inner_html)}</blockquote>')
            continue

        # ---- 列表 ---------------------------------------------------------
        if _UL_RE.match(line) or _OL_RE.match(line):
            block = []
            while index < total and (
                _UL_RE.match(lines[index]) or _OL_RE.match(lines[index]) or
                (lines[index].startswith(("  ", "\t")) and lines[index].strip() and block)
            ):
                block.append(lines[index])
                index += 1
            out.append(_render_list(block))
            continue

        # ---- 空行 ---------------------------------------------------------
        if not stripped:
            index += 1
            continue

        # ---- 段落 ---------------------------------------------------------
        paragraph: List[str] = []
        while index < total:
            candidate = lines[index]
            if not candidate.strip():
                break
            if (
                _HEADING_RE.match(candidate) or _FENCE_RE.match(candidate) or _HR_RE.match(candidate)
                or _UL_RE.match(candidate) or _OL_RE.match(candidate)
                or re.match(r"^\s*>", candidate)
                or candidate.strip().startswith(_HTML_BLOCK_PREFIXES)
            ):
                break
            paragraph.append(candidate.strip())
            index += 1
        if paragraph:
            out.append("<p>" + _inline(" ".join(paragraph)) + "</p>")

    body_html = "\n".join(out)
    toc_html = _build_toc(headings)
    return body_html, toc_html


def _render_list(block: List[str]) -> str:
    """把一段列表源码渲染成嵌套的 ul/ol。"""
    html_parts: List[str] = []
    stack: List[Tuple[int, str]] = []   # (缩进, 标签)

    for raw in block:
        ordered = _OL_RE.match(raw)
        unordered = _UL_RE.match(raw)
        match = ordered or unordered
        if not match:
            # 续行：并入上一个 <li>
            if html_parts:
                html_parts[-1] = html_parts[-1].rstrip()
                html_parts.append(" " + _inline(raw.strip()))
            continue

        indent = len(match.group(1).expandtabs(4))
        content = match.group(3)
        tag = "ol" if ordered else "ul"

        if not stack:
            stack.append((indent, tag))
            html_parts.append(f"<{tag}><li>{_inline(content)}</li>")
            continue

        top_indent, top_tag = stack[-1]
        if indent > top_indent:
            stack.append((indent, tag))
            html_parts.append(f"<{tag}><li>{_inline(content)}</li>")
        else:
            while len(stack) > 1 and indent < stack[-1][0]:
                closed = stack.pop()[1]
                html_parts.append(f"</li></{closed}>")
            html_parts.append(f"</li><li>{_inline(content)}")
            stack[-1] = (stack[-1][0], tag)

    while stack:
        html_parts.append(f"</li></{stack.pop()[1]}>")
    return "".join(html_parts)


def _build_toc(headings: List[Tuple[int, str, str]]) -> str:
    """生成两级目录（只收 h2 与 h3）。"""
    items = [(lvl, inner, slug) for lvl, inner, slug in headings if lvl in (2, 3)]
    if not items:
        return ""
    parts = ['<nav class="toc">', '<h2 class="toc-title">目录</h2>', '<ol class="toc-list">']
    open_sub = False
    for level, inner, slug in items:
        if level == 2:
            if open_sub:
                parts.append("</ol></li>")
                open_sub = False
            parts.append(f'<li class="toc-l1"><a href="#{slug}">{inner}</a>')
        else:
            if not open_sub:
                parts.append('<ol class="toc-sub">')
                open_sub = True
            parts.append(f'<li class="toc-l2"><a href="#{slug}">{inner}</a></li>')
    if open_sub:
        parts.append("</ol></li>")
    parts.append("</ol></nav>")
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# 组装完整文档
# --------------------------------------------------------------------------- #


def render_document(
    markdown_text: str,
    template_path: Path,
    css_path: Path,
    output_path: Path,
    *,
    title: str,
    subtitle: str = "",
    project: str = "wechat-summary-assistant",
    version: str = "",
    date: Optional[str] = None,
    running_title: str = "",
    drop_toc: bool = True,
) -> Tuple[Path, int]:
    """把 Markdown 套进 HTML 模板并写盘；返回 ``(输出路径, 标题数)``。"""
    body, toc = convert_markdown(markdown_text, drop_toc=drop_toc)
    template = template_path.read_text(encoding="utf-8")

    replacements = {
        "{{LANG}}": "zh-CN",
        "{{TITLE}}": title or "技术说明",
        "{{SUBTITLE}}": subtitle,
        "{{PROJECT}}": project,
        "{{VERSION}}": version,
        "{{DATE}}": date or dt.date.today().isoformat(),
        "{{RUNNING_TITLE}}": running_title or f"{title} · {project}",
        "{{TOC}}": toc,
        "{{CONTENT}}": body,
    }
    # {{CONTENT}} 与 {{TOC}} 的值本身就是要插入的 HTML，**不能**转义；
    # 其余占位符是纯文本（标题、日期等），必须转义，否则标题里的 & < > 会破坏文档结构。
    raw_keys = {"{{CONTENT}}", "{{TOC}}"}
    for key, value in replacements.items():
        template = template.replace(key, value if key in raw_keys else html.escape(value))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(template, encoding="utf-8")

    # 模板用相对路径引用样式表，因此把 CSS 复制到输出目录。
    # CSS 也可以带占位符（例如页眉里的文档标题），所以同样做一次替换。
    css_target = output_path.parent / css_path.name
    css_text = css_path.read_text(encoding="utf-8")
    for key in ("{{RUNNING_TITLE}}", "{{TITLE}}", "{{PROJECT}}", "{{VERSION}}", "{{DATE}}"):
        if key in css_text:
            # CSS 字符串里不能出现裸引号，做一次最小转义
            css_text = css_text.replace(key, replacements[key].replace("\\", "\\\\").replace('"', '\\"'))
    if css_path.resolve() != css_target.resolve():
        css_target.write_text(css_text, encoding="utf-8")
    else:
        css_target.write_text(css_text, encoding="utf-8")

    heading_count = len(re.findall(r"<h[23] ", body))
    return output_path, heading_count


def main() -> int:
    parser = argparse.ArgumentParser(description="Markdown -> 排版用 HTML")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--template", default="tools/pdf/template.html")
    parser.add_argument("--css", default="tools/pdf/style.css")
    parser.add_argument("--title", default="技术说明")
    parser.add_argument("--subtitle", default="")
    parser.add_argument("--project", default="wechat-summary-assistant")
    parser.add_argument("--version", default="")
    parser.add_argument("--date", default="")
    parser.add_argument("--running-title", default="", help="页眉里显示的短标题")
    parser.add_argument("--keep-toc", action="store_true", help="保留 Markdown 里原有的目录小节")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent
    src = (root / args.input) if not Path(args.input).is_absolute() else Path(args.input)
    dst = (root / args.output) if not Path(args.output).is_absolute() else Path(args.output)
    tpl = (root / args.template) if not Path(args.template).is_absolute() else Path(args.template)
    css = (root / args.css) if not Path(args.css).is_absolute() else Path(args.css)

    if not src.exists():
        raise SystemExit(f"找不到输入文件：{src}")
    if not tpl.exists():
        raise SystemExit(f"找不到模板文件：{tpl}")
    if not css.exists():
        raise SystemExit(f"找不到样式文件：{css}")

    path, count = render_document(
        src.read_text(encoding="utf-8"),
        tpl,
        css,
        dst,
        title=args.title,
        subtitle=args.subtitle,
        project=args.project,
        version=args.version,
        date=args.date or None,
        running_title=args.running_title,
        drop_toc=not args.keep_toc,
    )
    size = path.stat().st_size
    print(f"已生成：{path}（{size / 1024:.1f} KB，含 {count} 个二/三级标题）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
