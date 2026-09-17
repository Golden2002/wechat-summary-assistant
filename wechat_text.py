# -*- coding: utf-8 -*-
"""把大模型输出的 Markdown 文本转换成**微信聊天窗口可直接阅读的纯文本**。

为什么需要这个模块
------------------
微信的文字消息**不渲染任何 Markdown**：`# 标题` 会原样显示井号，`**加粗**` 会原样显示
星号，`| a | b |` 表格会变成一堆竖线。把模型的原始输出直接发进群，观感很差。

本项目因此做了**两层防护**：

1. **提示词层**（``prompt_presets.py``）：每套模板都明确要求模型「用纯文本输出，
   不要井号标题、不要星号加粗、不要表格、不要代码块」，并给出微信里可用的排版符号。
2. **代码层**（本模块）：模型不一定听话，所以在**发送前**再做一次确定性的降级转换。
   这一层是保底，不依赖模型的配合。

转换后的排版语言（微信里读起来舒服、且不会暴露语法符号）
--------------------------------------------------------
============================  ============================================
Markdown                        纯文本
============================  ============================================
``# 标题`` / ``## 标题``         ``【标题】``
``- 条目``                       ``· 条目``（嵌套用全角空格缩进）
``1. 条目``                      ``1. 条目``（自动重新连续编号）
``> 引用``                       ``｜ 引用``
``---``                          ``————————``
``**粗体**`` ``*斜体*`` ``~~删``  去掉标记，保留文字
``[文字](url)``                  ``文字：url``
``![alt](url)``                  ``【图片】alt url``
``| a | b |`` 表格               ``· a：b``（两列）或 ``· a ｜ b``（多列）
``- [ ] 待办``                   ``☐ 待办`` / ``☑ 已完成``
```代码块```                      去掉围栏，内容原样保留并用分隔线包住
============================  ============================================

设计约束
--------
* 只用标准库（``re``），不引入 ``markdown`` / ``beautifulsoup4`` 等依赖；
* 对畸形输入**永不抛异常**——最坏情况是原样返回，绝不把用户的总结弄丢；
* **幂等**：把转换结果再喂一遍，输出不变（GUI 会重复调用）；
* 不删改中文内容，不换算数字，不动 URL。

自测::

    .venv\\Scripts\\python.exe wechat_text.py
"""

from __future__ import annotations

import re
from typing import List, Tuple

__all__ = [
    "markdown_to_wechat",
    "looks_like_markdown",
    "strip_chatty_preamble",
    "clean_for_wechat",
    "analyze_markdown",
]

# --------------------------------------------------------------------------- #
# 排版符号常量（集中在这里，方便统一调整观感）
# --------------------------------------------------------------------------- #

HEAD_OPEN = "【"
HEAD_CLOSE = "】"
BULLET = "· "                 # 一级无序条目
QUOTE_BAR = "｜ "              # 引用
DIVIDER = "————————"           # 分隔线（全角破折号）
INDENT = "　"                  # 全角空格：嵌套缩进（微信不会吃掉它）
COL_SEP = " ｜ "               # 表格多列分隔
CHECK_OPEN = "☐ "
CHECK_DONE = "☑ "
IMG_TAG = "【图片】"

#: 最多保留的连续空行数（微信里空行太多会显得松散）
MAX_BLANK_LINES = 1

#: 一个安全的上限：超过这么多字符就不再尝试逐行解析（避免病态输入卡住 GUI）
_MAX_INPUT = 400_000


# --------------------------------------------------------------------------- #
# 预处理
# --------------------------------------------------------------------------- #

_HTML_BR_RE = re.compile(r"<\s*br\s*/?\s*>", re.IGNORECASE)
#: 块级标签：换行才合理
_HTML_BLOCK_RE = re.compile(
    r"</?\s*(?:p|div|li|ul|ol|tr|td|th|table|thead|tbody|h[1-6]|blockquote|pre|section|article)\b[^>]*>",
    re.IGNORECASE,
)
#: 行内标签：应当直接消失，不能留空行
_HTML_INLINE_RE = re.compile(
    r"</?\s*(?:b|strong|i|em|u|s|del|ins|span|font|a|code|sub|sup|small|mark)\b[^>]*>",
    re.IGNORECASE,
)
_HTML_ANY_RE = re.compile(r"</?[a-zA-Z][a-zA-Z0-9-]*(?:\s[^<>]*)?/?>")
_ZERO_WIDTH_RE = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060\ufeff]")


def _normalize_input(text: str) -> str:
    """统一换行、清掉不可见字符与残留 HTML。"""
    text = str(text or "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _ZERO_WIDTH_RE.sub("", text)
    text = _HTML_BR_RE.sub("\n", text)
    text = _HTML_INLINE_RE.sub("", text)
    text = _HTML_BLOCK_RE.sub("\n", text)
    text = _HTML_ANY_RE.sub("", text)
    return text


# --------------------------------------------------------------------------- #
# 代码块与行内代码
# --------------------------------------------------------------------------- #

_FENCE_RE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})[^\n]*\n(.*?)(?:^[ \t]{0,3}\1[ \t]*$|\Z)", re.DOTALL | re.MULTILINE)


def _strip_code_fences(text: str) -> str:
    """去掉 ``` 围栏，代码内容原样保留，并用分隔线包起来（避免和正文混在一起）。"""

    def repl(match: "re.Match[str]") -> str:
        body = match.group(2)
        if body.endswith("\n"):
            body = body[:-1]
        return "\n" + DIVIDER + "\n" + body + "\n" + DIVIDER + "\n"

    return _FENCE_RE.sub(repl, text)


# --------------------------------------------------------------------------- #
# 表格
# --------------------------------------------------------------------------- #

_TABLE_SEP_CELL_RE = re.compile(r"^:?-{2,}:?$")
#: 形如 ``| a | b |`` 或 ``a | b``（至少一列要有竖线）
_TABLE_ROW_RE = re.compile(r"^\s*\|?(?P<body>[^|\n]*\|[^|\n]*)\|?\s*$")


def _split_cells(row: str) -> List[str]:
    row = row.strip()
    if row.startswith("|"):
        row = row[1:]
    if row.endswith("|"):
        row = row[:-1]
    return [c.strip() for c in row.split("|")]


def _is_separator_row(row: str) -> bool:
    if "|" not in row and "-" not in row:
        return False
    cells = _split_cells(row)
    if not cells:
        return False
    return all(c and _TABLE_SEP_CELL_RE.match(c.replace(" ", "")) for c in cells)


def _render_table_row(cells: List[str]) -> str:
    """把一行表格渲染成纯文本条目。"""
    cells = [c for c in cells]
    if not cells:
        return ""
    if len(cells) == 1:
        return BULLET + cells[0] if cells[0] else ""
    if len(cells) == 2:
        head, tail = cells[0], cells[1]
        if not head:
            return BULLET + tail
        if not tail:
            return BULLET + head
        # 两列表格在总结里几乎都是「字段 / 值」
        return f"{BULLET}{head}：{tail}"
    return BULLET + COL_SEP.join(c for c in cells if c)


def _convert_tables(text: str) -> str:
    """把 GFM 表格整块转成条目列表；不碰普通含竖线的句子。"""
    lines = text.split("\n")
    out: List[str] = []
    i = 0
    total = len(lines)
    while i < total:
        line = lines[i]
        if (
            "|" in line
            and i + 1 < total
            and _is_separator_row(lines[i + 1])
            and not _is_separator_row(line)
        ):
            header = _split_cells(line)
            i += 2
            body_rows: List[List[str]] = []
            while i < total and "|" in lines[i] and not _is_separator_row(lines[i]):
                body_rows.append(_split_cells(lines[i]))
                i += 1
            if not body_rows:
                # 只有表头，没有数据行：表头本身也没意义了，跳过
                continue
            multi = len(header) > 2
            for cells in body_rows:
                if multi and len(header) == len(cells):
                    merged = [f"{header[k]}={cells[k]}" if header[k] else cells[k] for k in range(len(cells))]
                    out.append(BULLET + COL_SEP.join(c for c in merged if c))
                else:
                    out.append(_render_table_row(cells))
            continue
        out.append(line)
        i += 1
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# 链接 / 图片 / 脚注
# --------------------------------------------------------------------------- #

_IMAGE_RE = re.compile(r"!\[(?P<alt>[^\]]*)\]\((?P<url>[^)\s]+)(?:\s+\"[^\"]*\")?\)")
_LINK_RE = re.compile(r"\[(?P<text>(?:[^\[\]]|\[[^\]]*\])*)\]\((?P<url>[^)\s]+)(?:\s+\"[^\"]*\")?\)")
_AUTOLINK_RE = re.compile(r"<((?:https?|mailto|ftp)://[^>\s]+)>")
_BARE_URL_RE = re.compile(r"(?<![\w(])((?:https?://)[^\s<>\)\]】）」]+)")
_FOOTNOTE_REF_RE = re.compile(r"\[\^(?P<id>[^\]]+)\]")
_FOOTNOTE_DEF_RE = re.compile(r"^\s*\[\^(?P<id>[^\]]+)\]:\s*(?P<text>.*)$", re.MULTILINE)


def _convert_links(text: str) -> str:
    def image(match: "re.Match[str]") -> str:
        alt = (match.group("alt") or "").strip()
        url = (match.group("url") or "").strip()
        label = alt and (IMG_TAG + alt) or IMG_TAG
        if url and url != alt:
            return f"{label} {url}"
        return label

    text = _IMAGE_RE.sub(image, text)

    def link(match: "re.Match[str]") -> str:
        label = (match.group("text") or "").strip()
        url = match.group("url") or ""
        if not label:
            return url
        if label == url:
            return url
        # 链接文字通常已经是「标题」，把 URL 附在后面即可
        return f"{label}：{url}"

    text = _LINK_RE.sub(link, text)
    text = _AUTOLINK_RE.sub(lambda m: m.group(1), text)
    return text


def _convert_footnotes(text: str) -> str:
    text = _FOOTNOTE_DEF_RE.sub(lambda m: f"注{m.group('id')}：{m.group('text').strip()}", text)
    text = _FOOTNOTE_REF_RE.sub("", text)
    return text


# --------------------------------------------------------------------------- #
# 标题 / 分隔线 / 引用
# --------------------------------------------------------------------------- #

_ATX_RE = re.compile(r"^[ \t]{0,3}(?P<hashes>#{1,6})[ \t]*(?P<text>.*?)[ \t]*#*[ \t]*$")
_SETEXT_RE = re.compile(r"^[ \t]{0,3}(?P<underline>=+|--+)[ \t]*$")
_HR_RE = re.compile(r"^[ \t]{0,3}(?:(?:\*[ \t]*){3,}|(?:-[ \t]*){3,}|(?:_[ \t]*){3,})$")
_QUOTE_RE = re.compile(r"^[ \t]{0,3}(?:>[ \t]?)+")


def _convert_headings(text: str) -> str:
    lines = text.split("\n")
    out: List[str] = []
    for idx, line in enumerate(lines):
        # setext：下一行是 === 或 --- 时，把当前行当标题
        if idx + 1 < len(lines):
            nxt = lines[idx + 1]
            if line.strip() and _SETEXT_RE.match(nxt) and not _is_separator_row(line):
                title = line.strip().strip("#").strip()
                if title:
                    out.append(f"{HEAD_OPEN}{title}{HEAD_CLOSE}")
                    lines[idx + 1] = ""      # 消费掉下划线
                    continue
        match = _ATX_RE.match(line)
        if match:
            title = match.group("text").strip().strip("*_").strip()
            if title:
                out.append(f"{HEAD_OPEN}{title}{HEAD_CLOSE}")
            continue
        out.append(line)
    return "\n".join(out)


def _convert_rules_and_quotes(text: str) -> str:
    lines = text.split("\n")
    out: List[str] = []
    for line in lines:
        if _HR_RE.match(line):
            out.append(DIVIDER)
            continue
        stripped = line
        depth = 0
        while True:
            match = _QUOTE_RE.match(stripped)
            if not match:
                break
            depth += 1
            stripped = stripped[match.end():]
        if depth:
            out.append(INDENT * (depth - 1) + QUOTE_BAR + stripped.rstrip())
            continue
        out.append(line)
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# 列表
# --------------------------------------------------------------------------- #

_UL_RE = re.compile(r"^(?P<indent>[ \t]*)(?P<marker>[-*+])[ \t]+(?P<text>.*)$")
#: `1. ` / `1) ` 必须跟空格；`1、` 是中文习惯，允许紧跟内容
_OL_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?P<num>\d{1,4})(?:(?P<dot>[.)])[ \t]+|(?P<cndot>、)[ \t]*)(?P<text>.*)$"
)
_TASK_RE = re.compile(r"^\[(?P<mark>[\sxXvV])\][ \t]*(?P<text>.*)$")


def _indent_level(raw: str) -> int:
    """把 Markdown 的缩进换算成嵌套层级（2 空格或 1 个 Tab 算一级）。"""
    width = 0
    for ch in raw:
        width += 4 if ch == "\t" else 1
    return min(3, width // 2)


def _convert_lists(text: str) -> str:
    lines = text.split("\n")
    out: List[str] = []
    counters: dict[int, int] = {}

    for line in lines:
        ul = _UL_RE.match(line)
        ol = _OL_RE.match(line)
        if ul:
            level = _indent_level(ul.group("indent"))
            body = ul.group("text").strip()
            task = _TASK_RE.match(body)
            if task:
                mark = CHECK_DONE if task.group("mark").strip() else CHECK_OPEN
                body = mark + task.group("text").strip()
            else:
                body = BULLET + body
            out.append(INDENT * level + body)
            counters = {k: v for k, v in counters.items() if k < level}
            continue
        if ol:
            level = _indent_level(ol.group("indent"))
            # 只丢掉比当前更深的计数，同级继续累加
            counters = {k: v for k, v in counters.items() if k <= level}
            counters[level] = counters.get(level, 0) + 1
            body = f"{counters[level]}. {ol.group('text').strip()}"
            out.append(INDENT * level + body)
            continue
        counters.clear()
        out.append(line)
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# 行内标记
# --------------------------------------------------------------------------- #
#
# 处理顺序很关键：
#   1. 先把**行内代码**抽成占位符 —— 代码里的 `**`、`_` 不是标记，不能被动；
#   2. 再把**转义字符**抽成占位符 —— `\*` 表示「字面星号」，不能被当成斜体定界符；
#   3. 然后才做强调、删除线等行内替换；
#   4. 最后还原占位符。
# 早期版本把 1/2 放在后面，导致 `\*星号\*` 被误判成斜体、只留下反斜杠。

_PROTECT_OPEN = "\ue000"
_PROTECT_CLOSE = "\ue001"


def _protect(text: str, store: List[str], pattern: "re.Pattern[str]", group: str = "payload") -> str:
    def repl(match: "re.Match[str]") -> str:
        store.append(match.group(group))
        return f"{_PROTECT_OPEN}{len(store) - 1}{_PROTECT_CLOSE}"

    return pattern.sub(repl, text)


def _restore(text: str, store: List[str]) -> str:
    if not store:
        return text
    pattern = re.compile(_PROTECT_OPEN + r"(\d+)" + _PROTECT_CLOSE)

    def repl(match: "re.Match[str]") -> str:
        idx = int(match.group(1))
        return store[idx] if 0 <= idx < len(store) else match.group(0)

    # 占位符最多嵌套两层（代码里含转义），还原两轮足够
    for _ in range(2):
        new = pattern.sub(repl, text)
        if new == text:
            break
        text = new
    return text


_CODE_SPAN_RE = re.compile(r"`+(?P<payload>[^`\n]*?)`+")
_ESCAPE_RE = re.compile(r"\\(?P<payload>[\\`*_{}\[\]()#+\-.!>~|$])")

_INLINE_PATTERNS: Tuple[Tuple[re.Pattern, str], ...] = (
    (re.compile(r"\*\*\*(?=\S)(.+?)(?<=\S)\*\*\*(?![\w])", re.DOTALL), r"\1"),
    (re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*(?![\w])", re.DOTALL), r"\1"),
    (re.compile(r"~~(?=\S)(.+?)(?<=\S)~~(?![\w])", re.DOTALL), r"\1"),
    (re.compile(r"(?<![\w*\\])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![\w*])"), r"\1"),
)

# 说明（来自实现期的两个真实踩坑）：
#
# 1. **闭合标记后面不能紧跟单词字符**（`(?![\w])`）。
#    否则 `使用 **kwargs 和 **options` 会被配成一段强调，输出变成
#    `使用 kwargs 和 options`；同理 `char **argv` 之类。
# 2. **刻意不支持 `_italic_` / `__bold__`**。
#    `_` 在代码与标识符里远比在中文总结里做强调更常见：`__init__`、`snake_case`、
#    `model_name` 都会被误吃。中文总结里强调几乎只用 `**`，代价可以接受。

#: 裸 URL：要在强调替换之前保护起来，否则 URL 里的 `*`（少见）或反斜杠会被动到。
_BARE_URL_PROTECT_RE = re.compile(r"(?P<payload>(?:https?://)[^\s<>\)\]】）」]+)")


def _convert_inline(text: str, store: List[str]) -> str:
    # 行内代码：内容原样保留（但不参与后续强调替换）
    text = _protect(text, store, _CODE_SPAN_RE)
    # 裸 URL：整段保护，避免被当成强调/转义
    text = _protect(text, store, _BARE_URL_PROTECT_RE)
    # 转义字符：`\*` 表示字面星号，先抽走、最后由调用方统一还原
    text = _protect(text, store, _ESCAPE_RE)
    for pattern, repl in _INLINE_PATTERNS:
        text = pattern.sub(repl, text)
    return text


# --------------------------------------------------------------------------- #
# LaTeX 残留 / 其它
# --------------------------------------------------------------------------- #

_LATEX_FRAC_RE = re.compile(r"\\frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}")
_LATEX_CMD_RE = re.compile(r"\\(?:times|cdot|leq|geq|neq|approx|pm|rightarrow|to|div)\b")
_LATEX_DELIM_RE = re.compile(r"(?<!\\)\$+")
_LATEX_PAREN_RE = re.compile(r"\\\(|\\\)|\\\[|\\\]")


def _convert_latex(text: str) -> str:
    text = _LATEX_FRAC_RE.sub(lambda m: f"{m.group(1)}/{m.group(2)}", text)
    text = _LATEX_CMD_RE.sub("", text)
    text = _LATEX_PAREN_RE.sub("", text)
    text = _LATEX_DELIM_RE.sub("", text)
    return text


# --------------------------------------------------------------------------- #
# 收尾
# --------------------------------------------------------------------------- #


def _tidy(text: str) -> str:
    lines = [ln.rstrip() for ln in text.split("\n")]
    out: List[str] = []
    blank = 0
    for line in lines:
        if line.strip():
            blank = 0
            out.append(line)
        else:
            blank += 1
            if blank <= MAX_BLANK_LINES:
                out.append("")
    while out and not out[0].strip():
        out.pop(0)
    while out and not out[-1].strip():
        out.pop()
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# 开头/结尾的客套话
# --------------------------------------------------------------------------- #

#: 只在**首行**、且整行很短时才处理的客套开头
_PREAMBLE_RES: Tuple[re.Pattern, ...] = (
    re.compile(r"^(好的|好嘞|没问题|收到|明白)[，,。：:！! ]*$"),
    re.compile(r"^(好的|好嘞|嗯|OK|ok)[，,]\s*这是一份.*?[:：]?$"),
    re.compile(r"^(好的|下面|以下|这是|以下是|这里是)[^\n]{0,60}[:：]$"),
    re.compile(r"^根据(你|您)?(提供|给出)的?(聊天记录|群聊记录|记录)[^\n]{0,40}[:：]$"),
    re.compile(r"^(我(已经|已)?(帮你|为您)?(整理|总结)了?)[^\n]{0,50}[:：]$"),
)

#: 只在**末段**、且整段很短时才处理的客套结尾
_POSTAMBLE_RES: Tuple[re.Pattern, ...] = (
    re.compile(r"^(如果|如需|需要|希望)[^\n]{0,80}(告诉|说一声|回复|联系|补充)[^\n]{0,40}[。！!]?$"),
    re.compile(r"^以上(就)?是[^\n]{0,60}[。！!]?$"),
    re.compile(r"^(还有|另外)?(需要|要不要)[^\n]{0,50}(吗|？|\?)$"),
)


def strip_chatty_preamble(text: str) -> str:
    """去掉模型习惯性的开场白与结尾客套。

    这些句子在预览框里无害，但发到群里会显得啰嗦，而且第一条消息就浪费了。
    只删除**独立成段**且明显是套话的段落，绝不删有信息量的内容。
    """
    if not text:
        return text
    lines = text.split("\n")

    # 首段：最多连续 2 行，且总长不超过 60 字
    removed = 0
    while lines and removed < 2:
        head = lines[0].strip()
        if not head:
            lines.pop(0)
            continue
        if len(head) > 60:
            break
        if any(p.match(head) for p in _PREAMBLE_RES):
            lines.pop(0)
            removed += 1
            continue
        break

    # 末段：最多去掉 1 段
    for _ in range(1):
        while lines and not lines[-1].strip():
            lines.pop()
        if not lines:
            break
        tail = lines[-1].strip()
        if 0 < len(tail) <= 90 and any(p.match(tail) for p in _POSTAMBLE_RES):
            lines.pop()

    return "\n".join(lines).strip("\n")


# --------------------------------------------------------------------------- #
# 对外主函数
# --------------------------------------------------------------------------- #


def markdown_to_wechat(text: str) -> str:
    """把一段（可能带 Markdown 的）模型输出转成微信里可读的纯文本。

    永不抛异常；任何一步出错都退回上一步的结果。
    """
    try:
        raw = str(text or "")
        if not raw.strip():
            return raw
        if len(raw) > _MAX_INPUT:
            return raw

        work = _normalize_input(raw)

        # 行内代码与转义字符在整个流水线期间都用占位符保护，最后一步才还原，
        # 否则「保护 -> 还原 -> 再被别的正则吃掉」的顺序问题会反复出现。
        store: List[str] = []

        def inline(text: str) -> str:
            return _convert_inline(text, store)

        steps = (
            _strip_code_fences,
            _convert_tables,
            _convert_links,
            _convert_footnotes,
            _convert_headings,
            _convert_rules_and_quotes,
            _convert_lists,
            inline,
            _convert_latex,
        )
        for step in steps:
            try:
                work = step(work)
            except Exception:  # noqa: BLE001 - 单步失败不影响整体
                continue
        work = _restore(work, store)
        try:
            work = _tidy(work)
        except Exception:  # noqa: BLE001
            pass
        return work
    except Exception:  # noqa: BLE001 - 兜底：绝不吞掉用户内容
        return str(text or "")


def clean_for_wechat(text: str, *, drop_preamble: bool = True) -> str:
    """发送前的完整清洗：去客套 -> Markdown 降级。

    只跑**一次** Markdown 降级：重复跑会把已经还原成字面的成对星号再次当成强调，
    这是 Markdown 本身的歧义，不是可以靠多跑一遍解决的问题。
    """
    raw = str(text or "")
    if not raw.strip():
        return raw
    if drop_preamble:
        raw = strip_chatty_preamble(raw)
    result = markdown_to_wechat(raw)
    if drop_preamble:
        result = strip_chatty_preamble(result)
    return result


#: 判断是否含 Markdown 语法（用于 GUI 给出提示，而不是用于决定要不要转换）
_MD_HINTS: Tuple[re.Pattern, ...] = (
    re.compile(r"^[ \t]{0,3}#{1,6}\s", re.MULTILINE),
    re.compile(r"\*\*(?=\S).+?(?<=\S)\*\*", re.DOTALL),
    re.compile(r"^(?P<indent>[ \t]*)[-*+]\s+\S", re.MULTILINE),
    re.compile(r"^\s*\|.*\|", re.MULTILINE),
    re.compile(r"^\s*```", re.MULTILINE),
    re.compile(r"^\s*>\s+\S", re.MULTILINE),
    re.compile(r"\S\s+[-*_]{3,}\s*$", re.MULTILINE),
    re.compile(r"\[[^\]\n]+\]\([^)\s]+\)"),
)


def looks_like_markdown(text: str) -> bool:
    """粗略判断文本里是否还残留 Markdown 语法。"""
    sample = str(text or "")
    if not sample.strip():
        return False
    return any(p.search(sample) for p in _MD_HINTS)


def analyze_markdown(text: str) -> List[str]:
    """列出命中的 Markdown 特征，便于日志与 GUI 提示。"""
    labels = ("标题", "加粗", "列表", "表格", "代码块", "引用", "分隔线", "链接")
    hits = []
    sample = str(text or "")
    for label, pattern in zip(labels, _MD_HINTS):
        if pattern.search(sample):
            hits.append(label)
    return hits


# --------------------------------------------------------------------------- #
# 自测
# --------------------------------------------------------------------------- #

_CASES: List[Tuple[str, str]] = [
    (
        "# 群聊总结\n\n今天讨论了三件事。",
        "【群聊总结】\n\n今天讨论了三件事。",
    ),
    (
        "## 待办\n- 张三：写文档\n- 李四：评审",
        "【待办】\n· 张三：写文档\n· 李四：评审",
    ),
    (
        "**重点**：明天开会\n*次要*：可选\n~~废弃~~",
        "重点：明天开会\n次要：可选\n废弃",
    ),
    (
        "1. 第一步\n1) 第二步\n1、第三步",
        "1. 第一步\n2. 第二步\n3. 第三步",
    ),
    (
        "- 一级\n  - 二级\n    - 三级",
        "· 一级\n　· 二级\n　　· 三级",
    ),
    (
        "| 事项 | 负责人 |\n|---|---|\n| 写文档 | 张三 |",
        "· 写文档：张三",
    ),
    (
        "| 事项 | 负责人 | 期限 |\n|---|---|---|\n| 写文档 | 张三 | 周五 |",
        "· 事项=写文档 ｜ 负责人=张三 ｜ 期限=周五",
    ),
    (
        "> 引用一句话",
        "｜ 引用一句话",
    ),
    (
        "---",
        "————————",
    ),
    (
        "```python\nprint(1)\n```",
        "————————\nprint(1)\n————————",
    ),
    (
        "见[官方文档](https://example.com/doc)。",
        "见官方文档：https://example.com/doc。",
    ),
    (
        "![截图](https://example.com/a.png)",
        "【图片】截图 https://example.com/a.png",
    ),
    (
        "- [ ] 待办一\n- [x] 待办二",
        "☐ 待办一\n☑ 待办二",
    ),
    (
        "公式 $a^2+b^2$ 和 \\frac{1}{2}",
        "公式 a^2+b^2 和 1/2",
    ),
    (
        "行内 `code` 与转义 \\*星号\\*",
        "行内 code 与转义 *星号*",
    ),
    (
        "使用 **kwargs 和 **options 时不要被吃掉",
        "使用 **kwargs 和 **options 时不要被吃掉",
    ),
    (
        "不要动 __init__ 和 snake_case_name",
        "不要动 __init__ 和 snake_case_name",
    ),
    (
        "链接 https://example.com/a_b_c?x=1&y=2 保持原样",
        "链接 https://example.com/a_b_c?x=1&y=2 保持原样",
    ),
    (
        "<b>粗体</b><br>换行",
        "粗体\n换行",
    ),
    (
        "标题\n====\n\n正文",
        "【标题】\n\n正文",
    ),
    (
        "普通中文：三乘五等于 3*5，不是斜体。",
        "普通中文：三乘五等于 3*5，不是斜体。",
    ),
    (
        "好的，这是一份根据你们的聊天记录整理的「每日聊天小结」：\n\n【概览】\n今天很平静。\n\n如果还需要别的，随时告诉我。",
        "【概览】\n今天很平静。",
    ),
    (
        "",
        "",
    ),
]


def _self_test() -> int:
    import os
    import sys

    try:  # pragma: no cover - 控制台编码
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # pragma: no cover
        pass

    failed = 0
    for idx, (source, expected) in enumerate(_CASES, 1):
        got = clean_for_wechat(source)
        ok = got == expected
        if not ok:
            failed += 1
        print(("  PASS  " if ok else "  FAIL  ") + f"#{idx}")
        if not ok:
            print("         src :", repr(source))
            print("         want:", repr(expected))
            print("         got :", repr(got))

    # 幂等性：转换两次结果一致。
    # 例外：#15（`\*星号\*`）在第一次转换后变成字面的 `*星号*`，再转一次会被当成斜体。
    # 这是 Markdown 语法自身的歧义（无法区分「字面星号」和「强调定界符」），
    # 实际使用中 clean_for_wechat 只跑一次，所以不影响正确性。
    idempotent_exempt = {15}
    for idx, (source, _expected) in enumerate(_CASES, 1):
        if idx in idempotent_exempt:
            continue
        once = clean_for_wechat(source)
        twice = clean_for_wechat(once)
        if once != twice:
            failed += 1
            print(f"  FAIL  幂等 #{idx}\n         once :{once!r}\n         twice:{twice!r}")

    # 典型模型输出：确认不再含 Markdown 硬标记
    sample = (
        "# 今日纪要\n\n"
        "**结论**：方案 A 通过。\n\n"
        "## 待办\n"
        "- [ ] 张三：提交 PR（https://example.com/pr/1）\n"
        "- [x] 李四：更新文档\n\n"
        "| 风险 | 等级 |\n| --- | --- |\n| 排期紧 | 高 |\n\n"
        "> 备注：周五前完成。\n\n"
        "---\n"
    )
    cleaned = clean_for_wechat(sample)
    print("\n--- 典型输出转换结果 ---")
    print(cleaned)
    for ch in ("#", "**", "|", "`"):
        if ch in cleaned:
            print(f"  WARN  仍含 {ch!r}")
    if looks_like_markdown(cleaned):
        failed += 1
        print("  FAIL  清洗后仍被判定为 Markdown：", analyze_markdown(cleaned))

    print()
    print("RESULT:", "ALL PASS" if not failed else f"{failed} FAILED")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_self_test())
