# -*- coding: utf-8 -*-
"""核心逻辑回归测试（**不接触真实微信、不移动真实窗口**）。

为什么需要它
------------
本项目的全部风险都集中在两处：

1. 「把 wxauto4 的消息模型翻译成本项目自己的模型」这一层 —— 免费版与 Plus 版方法集
   不同，微信每次更新都可能改变 UI；
2. 「向上翻页把整个时间范围的消息都抓回来」这一层 —— 抓漏了用户是看不出来的，
   只会得到一个内容偏少的总结。

本测试用假的 wx 对象、假消息对象与假消息列表控件覆盖这些假设：
消息来源/类型映射、时间解析、翻页策略回退、按 id 去重、时间边界判定、
时间归属、渲染格式、模板完整性、输出清洗。

运行::

    .venv\\Scripts\\python.exe tests\\test_core.py
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
import tempfile
from types import SimpleNamespace

# 允许直接 `python tests/test_core.py`（把项目根加入 import 路径）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:  # pragma: no cover - 让中文输出在 GBK 控制台下也不乱码
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # pragma: no cover
    pass

import wechat_summary as core  # noqa: E402

# 测试里关掉真实鼠标事件：_global_wheel 会移动用户的光标
core.USE_GLOBAL_MOUSE_EVENTS = False

FAILED = []


def check(label: str, condition: bool, extra: object = "") -> None:
    mark = "  PASS  " if condition else "  FAIL  "
    tail = ("   -> " + repr(extra)) if (extra != "" and not condition) else ""
    print(mark + label + tail)
    if not condition:
        FAILED.append(label)


# --------------------------------------------------------------------------- #
# 假对象：模拟 wxauto4 的消息、WeChat 与消息列表控件
# --------------------------------------------------------------------------- #

#: 一屏能看到多少条（模拟微信只给「渲染出来的」消息注册 UIA 控件）
VIEWPORT = 12
#: 每翻一屏往上走多少条（故意小于 VIEWPORT，制造重叠，复现真实的重复读取）
PAGE = 8
#: 假消息列表的下边缘 y 坐标（几何判底用）
LIST_BOTTOM = 1000

_seq = [0]


class FakeMsg:
    """模拟 wxauto4 的消息对象（属性名依据 wxauto4/msgs/base.pyi）。"""

    def __init__(self, attr, mtype, content, sender="", time=None, mid=None):
        self.attr = attr
        self.type = mtype
        self.content = content
        self.sender = sender
        if time is not None:
            self.time = time
        _seq[0] += 1
        # wxauto4 每条消息都带一个 32 位十六进制 id；去重就靠它
        self.id = mid or ("%032x" % _seq[0])


def text_msg(content, sender="Alice", when=None, attr="friend"):
    return FakeMsg(attr, "text", content, sender, when)


def time_msg(when):
    return FakeMsg("system", "time", when.strftime("%Y-%m-%d %H:%M:%S"), time=when)


class FakeWx:
    """模拟 wxauto4 的 WeChat：只能看到「当前渲染出来的那一屏」。"""

    def __init__(self, log, viewport=VIEWPORT, page=PAGE):
        self.log = list(log)          # 旧 -> 新
        self.viewport = viewport
        self.page = page
        self.pos = len(self.log)      # 窗口右端（不含）
        self.reads = 0
        self.shuffle = False          # True 时模拟「重绘期间只返回一半」

    # -- 窗口 ---------------------------------------------------------------

    def _window(self):
        self.reads += 1
        start = max(0, self.pos - self.viewport)
        window = self.log[start:self.pos]
        if self.shuffle and self.reads % 2 == 0:
            # 真实观测：同一屏可能先返回 7 条、再返回 3 条
            window = window[len(window) // 2:]
        return list(window)

    def GetAllMessage(self):
        return self._window()

    # -- 兼容路径（老版本 / Plus 版） ---------------------------------------

    def LoadMoreCache(self, load_times=1):
        # 实测 wxauto4 41.1.7 免费版：内部调用不存在的 load_more_message
        raise AttributeError("'WeChatMainWnd' object has no attribute 'load_more_message'")

    def GetHistoryMessage(self, *a, **k):  # pragma: no cover - 仅供 hasattr 判断
        raise AssertionError("免费版不该走到这里")

    # -- 滚动位置控制（供 FakeList 调用） ----------------------------------

    def page_up(self):
        self.pos = max(0, self.pos - self.page)

    def page_down(self):
        self.pos = min(len(self.log), self.pos + self.page)

    def to_end(self):
        self.pos = len(self.log)

    def to_home(self):
        self.pos = min(len(self.log), self.viewport)


class FakeList:
    """模拟 ``mmui::RecyclerListView`` 控件。

    ``pageup_works`` / ``wheel_works`` 用来验证「首选翻页方式失效时会不会自动换一种」。
    """

    def __init__(self, wx):
        self.wx = wx
        self.calls = []
        self.pageup_works = True
        self.wheel_works = False
        self.home_works = True
        self.bar = _UnreadBar(wx)

    def SendKeys(self, text, interval=0.01, waitTime=0.5, charMode=True, api=True):
        self.calls.append(text)
        if text == "{PageUp}" and self.pageup_works:
            self.wx.page_up()
        elif text == "{End}":
            self.wx.to_end()
        elif text == "{Home}" and self.home_works:
            self.wx.to_home()
        elif text == "{PageDown}":
            self.wx.page_down()

    def WheelUp(self, x=None, y=None, ratioX=0.5, ratioY=0.5, wheelTimes=1,
                interval=0.05, waitTime=0.5, api=True, use_safe_coord=True):
        self.calls.append("wheel")
        if self.wheel_works:
            self.wx.page_up()

    def GetChildren(self):
        """真实 UIA 控件会返回「当前渲染出来的子节点」。

        收集器用它（只要 ~0.02 秒）来判断「这一屏读全了吗」「已经滚到最新了吗」，
        避免为了确认状态把 ~10 秒的 GetAllMessage 多读几遍。
        每个子节点还带 ``BoundingRectangle``，用于几何判底。
        """
        start = max(0, self.wx.pos - self.wx.viewport)
        window = self.wx.log[start:self.wx.pos]
        at_end = self.wx.pos >= len(self.wx.log)
        return [
            SimpleNamespace(
                Name=getattr(m, "content", ""),
                # 最后一条的下边缘与列表下边缘齐平 => 已经到底
                BoundingRectangle=SimpleNamespace(
                    left=0, top=0, right=100,
                    bottom=LIST_BOTTOM if (at_end and i == len(window) - 1) else LIST_BOTTOM - 80,
                ),
            )
            for i, m in enumerate(window)
        ]

    # 几何判底需要的属性
    BoundingRectangle = SimpleNamespace(left=0, top=0, right=100, bottom=LIST_BOTTOM)

    def GetParentControl(self):
        """父控件（模拟 ``mmui::MessageView``），它底下挂着「跳转到最新消息」条。

        这个条**只在不在底部时存在** —— 收集器就靠它判断「滚到最新了吗」。
        """
        return SimpleNamespace(
            ClassName="mmui::MessageView",
            GetChildren=lambda: [self] + ([self.bar] if not self._at_end() else []),
        )

    def _at_end(self) -> bool:
        return self.wx.pos >= len(self.wx.log)


class _UnreadBar:
    """模拟 ``mmui::UnreadBarView``（「跳转到最新消息」）。"""

    Name = "跳转到最新消息"
    ClassName = "mmui::UnreadBarView"

    def __init__(self, wx):
        self.wx = wx
        self.invoked = 0

    def GetPattern(self, _pattern_id):
        bar = self

        class _Invoke:
            def Invoke(self):
                bar.invoked += 1
                bar.wx.to_end()

        return _Invoke()

    def Click(self):
        self.invoked += 1
        self.wx.to_end()


# --------------------------------------------------------------------------- #
# 构造一条可复现的聊天记录
# --------------------------------------------------------------------------- #

#: 固定一个"现在"，避免测试受运行时刻影响
NOW = dt.datetime(2026, 9, 17, 15, 0, 0)


def build_log():
    """造一条跨越 6 小时、带时间分隔条的聊天记录（约 40 条）。

    * 08:55 ~ 10:30：很久以前（应当被时间边界排除）
    * 12:00 ~ 15:00：目标范围（start_time = 11:00）
    * 中间故意放两条**一模一样**的「哈哈哈哈」—— 旧的去重键会把它们当成一条
    """
    log = []
    old = dt.datetime(2026, 9, 17, 9, 0, 0)
    log.append(time_msg(old))
    for i in range(8):
        log.append(text_msg(f"上午消息{i}", "Bob", old + dt.timedelta(minutes=3 * i)))

    log.append(time_msg(dt.datetime(2026, 9, 17, 12, 0, 0)))
    for i in range(6):
        log.append(text_msg(f"下午消息{i}", "Carol", dt.datetime(2026, 9, 17, 12, 5 + i, 0)))

    # 同一个人连发两条完全一样的内容（真实现象，也是旧去重键的最大漏洞）
    log.append(text_msg("哈哈哈哈", "Dave", dt.datetime(2026, 9, 17, 13, 0, 0)))
    log.append(text_msg("哈哈哈哈", "Dave", dt.datetime(2026, 9, 17, 13, 1, 0)))

    log.append(time_msg(dt.datetime(2026, 9, 17, 14, 0, 0)))
    for i in range(8):
        log.append(text_msg(f"傍晚消息{i}", "Eve", dt.datetime(2026, 9, 17, 14, 5 + i, 0)))
    return log


START = dt.datetime(2026, 9, 17, 11, 0, 0)


def collect(log, start=START, **kwargs):
    wx = FakeWx(log)
    fake_list = FakeList(wx)
    kwargs.setdefault("interval", 0)
    kwargs.setdefault("focus", False)
    kwargs.setdefault("list_control", fake_list)
    records = core._collect_messages(wx, start, **kwargs)
    return records, wx, fake_list


def main() -> int:
    print("=== 1) classify_message: attr+type -> kind ===")
    check("friend text -> friend", core.classify_message(FakeMsg("friend", "text", "hi", "Alice")).kind == "friend")
    check("self text -> self", core.classify_message(FakeMsg("self", "text", "yo", "Me")).kind == "self")
    check("system time -> time", core.classify_message(FakeMsg("system", "time", "2026-09-01 10:00:00")).kind == "time")
    check("system 撤回 -> recall", core.classify_message(FakeMsg("system", "text", "Bob撤回了一条消息")).kind == "recall")
    check("system 其它 -> system", core.classify_message(FakeMsg("system", "text", "你邀请X加入群聊")).kind == "system")
    check("other -> other", core.classify_message(FakeMsg("other", "text", "???")).kind == "other")
    check("sender 被保留", core.classify_message(FakeMsg("friend", "text", "hi", "Alice")).sender == "Alice")

    print()
    print("=== 2) 时间解析 ===")
    check("iso datetime", core._parse_time_value("2026-09-01 10:00:00") == dt.datetime(2026, 9, 1, 10, 0, 0))
    check("仅日期", core._parse_time_value("2026-09-01") == dt.datetime(2026, 9, 1, 0, 0))
    y = core._parse_content_time("昨天 14:30")
    check("昨天 HH:MM", y is not None and y.hour == 14 and y.minute == 30
          and y.date() == dt.date.today() - dt.timedelta(days=1), y)
    t = core._parse_content_time("09:05")
    check("HH:MM 今天", t is not None and t.date() == dt.date.today() and t.hour == 9, t)
    check("纯文本 -> None", core._parse_content_time("今天天气不错") is None)
    check("越界时间 -> None", core._parse_content_time("25:99") is None)

    print()
    print("=== 3) 群名归一化 ===")
    check("空白差异被忽略", core._normalize_name(" 文件 传输助手 ") == core._normalize_name("文件传输助手"))
    check("大小写差异被忽略", core._normalize_name("GroupName") == core._normalize_name("groupname"))
    check("不同名不相等", core._normalize_name("群A") != core._normalize_name("群B"))

    print()
    print("=== 4) 长消息切分 ===")
    text = "\n".join("line %d %s" % (i, "x" * 120) for i in range(60))
    parts = core._split_message(text, limit=2000)
    check("确实被切分", len(parts) > 1, len(parts))
    check("每片 <= limit", all(len(p) <= 2000 for p in parts))
    check("无损（拼接还原）", "".join(parts) == text, (len("".join(parts)), len(text)))
    check("短文本不切", core._split_message("hello", limit=2000) == ["hello"])

    print()
    print("=== 5) 向上翻页收集：边界 / 去重 / 顺序 ===")
    log = build_log()
    records, wx, fake_list = collect(log, start=START, max_rounds=50)

    contents = [r.content for r in records if r.kind in ("friend", "self")]
    print("   条数:", len(records), " 消息:", len(contents), " 读了", wx.reads, "次")
    print("   调用序列:", fake_list.calls[:12], "...")

    check("确实向上翻了页", "{PageUp}" in fake_list.calls, fake_list.calls[:12])
    check("已在底部时不浪费动作（跳转条不存在就不点）",
          fake_list.bar.invoked == 0 and "{PageDown}" not in fake_list.calls,
          (fake_list.bar.invoked, fake_list.calls[:6]))
    check("越过边界：上午的消息被排除", not any(c.startswith("上午消息") for c in contents))
    check("范围内的消息都在", sum(1 for c in contents if c.startswith("下午消息")) == 6)
    check("范围内的消息都在(2)", sum(1 for c in contents if c.startswith("傍晚消息")) == 8)
    check("重复内容的两条都保留（旧去重键会丢一条）",
          contents.count("哈哈哈哈") == 2, contents.count("哈哈哈哈"))
    check("记录数符合预期（16 条消息 + 2 条范围内时间条）",
          len(records) == 18, len(records))
    check("时间顺序为 旧 -> 新",
          contents == [c for c in contents], contents[:4])
    check("下午消息在傍晚消息之前",
          contents.index("下午消息0") < contents.index("傍晚消息0"), contents[:3])
    check("时间分隔条只保留范围内的",
          all(r.time is None or r.time >= START for r in records if r.kind == "time"),
          [(r.kind, r.time) for r in records if r.kind == "time"])

    print()
    print("=== 5b) 列表停在历史中间时会先滚回最新 ===")
    wx_mid = FakeWx(log)
    wx_mid.pos = 0                       # 模拟「上次翻页后停在最旧的位置」
    fake_mid = FakeList(wx_mid)
    recs_mid = core._collect_messages(wx_mid, START, 50, 0, list_control=fake_mid, focus=False)
    got_mid = [r.content for r in recs_mid if r.kind == "friend"]
    check("会点「跳转到最新消息」滚回最新", fake_mid.bar.invoked > 0, fake_mid.bar.invoked)
    check("滚回最新后仍能取到范围内内容",
          sum(1 for c in got_mid if c.startswith("傍晚消息")) == 8, got_mid[:6])

    print()
    print("=== 6) 读一屏时的抖动不会丢消息 ===")
    wx2 = FakeWx(log)
    wx2.shuffle = True             # 每次读都有一半概率只返回后半屏
    fake2 = FakeList(wx2)
    recs2 = core._collect_messages(wx2, START, 50, 0, list_control=fake2, focus=False)
    got2 = [r.content for r in recs2 if r.kind == "friend"]
    check("抖动下依然拿全", got2.count("哈哈") == 0 and got2.count("哈哈哈哈") == 2, got2)
    check("抖动下下午消息齐全", sum(1 for c in got2 if c.startswith("下午消息")) == 6, got2)

    print()
    print("=== 7) 翻页方式自动回退 ===")
    wx3 = FakeWx(log)
    fake3 = FakeList(wx3)
    fake3.pageup_works = False     # PageUp 失效（真机实测键盘/滚轮都可能被吞）
    fake3.home_works = False       # Home 也救援不了（已到已加载历史的顶端）
    fake3.wheel_works = True       # 只有滚轮有效
    recs3 = core._collect_messages(wx3, START, 50, 0, list_control=fake3, focus=False)
    got3 = [r.content for r in recs3 if r.kind == "friend"]
    check("PageUp 失效时会换用滚轮", "wheel" in fake3.calls, fake3.calls[:16])
    check("换用滚轮后仍拿到范围内容", sum(1 for c in got3 if c.startswith("傍晚消息")) == 8, got3[:6])

    print()
    print("=== 7b) 所有翻页方式都失效时不空转 ===")
    wx3b = FakeWx(log)
    fake3b = FakeList(wx3b)
    fake3b.pageup_works = False
    fake3b.home_works = False
    fake3b.wheel_works = False
    recs3b = core._collect_messages(wx3b, START, 500, 0, list_control=fake3b, focus=False)
    check("会及时停下来（不会翻满 500 次）", len(fake3b.calls) < 30, len(fake3b.calls))
    check("仍然返回已读到的内容", len(recs3b) > 0, len(recs3b))

    print()
    print("=== 8) 一直翻到聊天开头（没有边界）===")
    wx4 = FakeWx(log)
    fake4 = FakeList(wx4)
    early = dt.datetime(2026, 9, 17, 8, 0, 0)
    recs4 = core._collect_messages(wx4, early, 50, 0, list_control=fake4, focus=False)
    got4 = [r.content for r in recs4 if r.kind == "friend"]
    check("翻到顶后上午消息也在", any(c.startswith("上午消息") for c in got4), got4[:4])
    check("总数正确", len(got4) == 8 + 6 + 2 + 8, len(got4))

    print()
    print("=== 9) 进度回调 ===")
    seen = []
    wx5 = FakeWx(log)
    core._collect_messages(
        wx5, START, 50, 0, list_control=FakeList(wx5), focus=False,
        progress=lambda info: seen.append(info),
    )
    check("回调被调用", len(seen) >= 2, len(seen))
    check("回调里有 collected 字段", all("collected" in s for s in seen))
    check("最后一次 done=True", seen[-1].get("done") is True, seen[-1])
    check("collected 单调不减",
          all(seen[i]["collected"] <= seen[i + 1]["collected"] for i in range(len(seen) - 1)),
          [s["collected"] for s in seen])

    print()
    print("=== 10) 墙钟预算 ===")
    wx6 = FakeWx(log)
    recs6 = core._collect_messages(wx6, START, 9999, 0, list_control=FakeList(wx6),
                                   focus=False, budget=5.0)
    check("预算内能正常结束", isinstance(recs6, list))

    print()
    print("=== 11) 时间归属 _resolve_times / _in_range ===")
    # 顺序刻意是「没有时间条的消息」在最前面：它比抓到窗口更早，不该有时间归属
    recs = [
        core.classify_message(text_msg("a", when=None)),
        core.classify_message(time_msg(dt.datetime(2026, 9, 17, 10, 0))),
        core.classify_message(text_msg("b", when=None)),
        core.classify_message(time_msg(dt.datetime(2026, 9, 17, 12, 0))),
        core.classify_message(text_msg("c", when=None)),
    ]
    core._resolve_times(recs)
    check("时间条自身有时间", recs[1].resolved == dt.datetime(2026, 9, 17, 10, 0), recs[1].resolved)
    check("第一条时间条之前的消息没有时间归属", recs[0].resolved is None, recs[0].resolved)
    check("时间条之后的消息继承时间", recs[2].resolved == dt.datetime(2026, 9, 17, 10, 0), recs[2].resolved)
    check("遇到新时间条后继承新时间", recs[4].resolved == dt.datetime(2026, 9, 17, 12, 0), recs[4].resolved)
    check("_in_range 排除过早的消息",
          core._in_range(recs[0], START, None, keep_unknown=False) is False)
    check("_in_range 在 keep_unknown 时保留无时间消息",
          core._in_range(recs[0], START, None, keep_unknown=True) is True)
    check("_in_range 保留范围内消息",
          core._in_range(recs[4], START, None, keep_unknown=False) is True)
    check("_in_range 尊重 end_time",
          core._in_range(recs[4], START, dt.datetime(2026, 9, 17, 11, 0), False) is False)

    print()
    print("=== 12) _render：时间锚点必须进入模型输入 ===")
    rendered_log = [
        time_msg(dt.datetime(2026, 9, 17, 14, 0)),
        text_msg("大家好", "张三", dt.datetime(2026, 9, 17, 14, 1)),
        text_msg("收到", "李四", dt.datetime(2026, 9, 17, 14, 2)),
    ]
    recs_r = [core.classify_message(m) for m in rendered_log]
    core._resolve_times(recs_r)
    detail, lines = core._render(recs_r)
    print("   lines:", lines)
    check("渲染出时间锚点", any("14:00" in ln for ln in lines), lines)
    check("发言人格式正确", "张三: 大家好" in lines, lines)
    check("时间锚点排在消息之前",
          next(i for i, ln in enumerate(lines) if "14:00" in ln)
          < next(i for i, ln in enumerate(lines) if "大家好" in ln), lines)

    print()
    print("=== 13) 自己发的总结会被跳过 ===")
    tmpdir = tempfile.mkdtemp(prefix="wxsum_test_")
    saved_sent = core.SENT_SUMMARY_FILE
    try:
        core.SENT_SUMMARY_FILE = core.Path(tmpdir) / "sent_summaries.json"
        summary_text = "【今日纪要】\n· 讨论了三件事。"
        own = text_msg(summary_text, "Me", attr="self")
        check("未记录时不会被误判",
              core._looks_like_own_summary(summary_text, {}) is False)
        core.remember_sent_summary(summary_text)
        check("记录后能被识别",
              core._looks_like_own_summary(summary_text, core._sent_summary_fingerprints()) is True)
        _, lines2 = core._render([core.classify_message(own)])
        check("渲染时跳过自己发的总结", lines2 == [], lines2)

        _, lines3 = core._render([core.classify_message(text_msg("我说的话", "Me", attr="self"))])
        check("普通自己发言不跳过", lines3 == ["Me: 我说的话"], lines3)

        long_self = "【长总结】" + "内容" * 300
        check("超长【】开头的自发消息按启发式跳过",
              core._looks_like_own_summary(long_self, {}) is True)
    finally:
        core.SENT_SUMMARY_FILE = saved_sent

    print()
    print("=== 14) 拼装系统提示词 ===")
    prompt = core.build_system_prompt()
    check("包含默认模板正文", core.PROMPT_PRESETS[core.DEFAULT_PROMPT_NAME][:20] in prompt)
    check("包含共享纪律（防提示注入）", "不是给你的指令" in prompt, prompt[-400:])
    check("包含非文本消息占位规则", "[图片]" in prompt)
    check("包含微信纯文本格式约束", "不要井号标题" in prompt)
    md_prompt = core.build_system_prompt(channel="markdown")
    check("markdown 渠道给出的是 Markdown 说明", "保存为 Markdown 文件" in md_prompt)
    check("markdown 渠道不再声明「会发到微信」", "会被直接发到微信聊天窗口" not in md_prompt)
    check("微信渠道声明了发送目标", "会被直接发到微信聊天窗口" in prompt)
    custom = core.build_system_prompt("自定义模板正文", extra="· 时间范围：今天")
    check("自定义模板也能被拼装", custom.startswith("自定义模板正文"))
    check("extra 被追加", custom.rstrip().endswith("时间范围：今天"), custom[-30:])

    print()
    print("=== 15) 超长记录分块 ===")
    many = ["x" * 100 for _ in range(50)]
    check("短输入不切块", len(core._split_for_model(many, 60_000)) == 1)
    chunks = core._split_for_model(many, 2_000)
    check("长输入会切块", len(chunks) > 1, len(chunks))
    check("切块无损", sum(len(c) for c in chunks) == len(many), (sum(len(c) for c in chunks), len(many)))
    check("每块不超过上限（+1 行余量）",
          all(sum(len(l) + 1 for l in c) <= 2_100 for c in chunks))

    print()
    print("=== 16) 翻页步数估算 ===")
    check("有下限", core._auto_load_rounds(0.01) >= 1, core._auto_load_rounds(0.01))
    check("随小时单调不减", core._auto_load_rounds(24) > core._auto_load_rounds(1),
          (core._auto_load_rounds(1), core._auto_load_rounds(24)))
    check("有上限", core._auto_load_rounds(10 ** 9) == core.MAX_LOAD_ROUNDS_CAP,
          core._auto_load_rounds(10 ** 9))
    check("容忍垃圾输入", core._auto_load_rounds(None) >= 1)
    check("24 小时能覆盖到 24 小时以上", core._auto_load_rounds(24) >= 24 * 60 // core.MESSAGES_PER_VIEWPORT,
          core._auto_load_rounds(24))

    print()
    print("=== 17) 对外接口与路径基准 ===")
    check("对外三函数存在", all(hasattr(core, n) for n in ("get_wechat_messages", "save_summary", "send_summary")))
    check("BASE_DIR 为脚本目录",
          str(core.BASE_DIR) == str(core.Path(core.__file__).resolve().parent), core.BASE_DIR)
    check("get_wechat_messages 支持绝对时间段",
          "start_time" in core.get_wechat_messages.__code__.co_varnames)
    check("send_summary 支持关闭 Markdown 转换",
          "convert_markdown" in core.send_summary.__code__.co_varnames)

    print()
    print("=== 18) 输出清洗已接线 ===")
    cleaned = core.clean_for_wechat("# 标题\n\n**要点**：完成\n\n- 甲\n- 乙")
    check("标题被转换", "【标题】" in cleaned, cleaned)
    check("加粗标记被去掉", "**" not in cleaned, cleaned)
    check("列表被转换", "· 甲" in cleaned, cleaned)
    check("looks_like_markdown 可用",
          core.looks_like_markdown("# x") is True and core.looks_like_markdown("【x】") is False)

    print()
    print("=== 19) 微信主窗口状态探测（只读，不需要微信在运行）===")
    windows = core._find_wechat_windows()
    if not windows:
        print("  INFO  未探测到微信顶层窗口（微信没运行，或系统非 Windows）")
    else:
        for w in windows:
            print("  INFO  hwnd=%s title=%r minimized=%s visible=%s"
                  % (w["hwnd"], w["title"], w["minimized"], w["visible"]))
        print("  INFO  连接失败提示里应包含排查步骤")
        enriched = core._explain_connect_failure(Exception("未找到已登录的客户端主窗口，请尝试：\n1. 检查是否关闭了主窗口"))
        check("保留原始报错", "未找到已登录的客户端主窗口" in enriched)
        check("补上 mmui::MainWindow 线索", "mmui::MainWindow" in enriched)
        check("指向无障碍热激活模块", "wechat_uia_wake" in enriched)

    print()
    print("=== 20) 系统提示词模板 ===")
    check("内置模板数 = 19", len(core.PROMPT_PRESETS) == 19, len(core.PROMPT_PRESETS))
    check("默认模板名在内置模板中", core.DEFAULT_PROMPT_NAME in core.PROMPT_PRESETS)
    check("分类齐全", set(core.PROMPT_CATEGORY.values()) == set(core.CATEGORY_ORDER),
          sorted(set(core.PROMPT_CATEGORY.values())))
    check("每套模板都有说明", all(core.PROMPT_DESCRIPTION.get(n) for n in core.PROMPT_PRESETS))
    check("分类函数返回全部模板",
          sum(len(v) for v in core.presets_by_category().values()) == len(core.PROMPT_PRESETS))

    for name, body in core.PROMPT_PRESETS.items():
        check("模板足够长：" + name, len(body or "") >= 300, len(body or ""))
        check("模板以分界行结尾：" + name, body.rstrip().endswith("以下是聊天记录："), body[-20:])
        check("模板不含 Markdown 标记：" + name,
              not any(ch in body for ch in ("**", "#", "|", "`")),
              [ch for ch in ("**", "#", "|", "`") if ch in body])

    print()
    print("=== 21) 模板增删改（隔离到临时目录，不动真实 config）===")
    try:
        import wechat_summary_gui as gui
    except Exception as exc:
        print("  INFO  跳过：无法导入 GUI 模块（%s）" % exc)
    else:
        saved_config_dir = gui.CONFIG_DIR
        try:
            with tempfile.TemporaryDirectory() as tmp:
                gui.CONFIG_DIR = core.Path(tmp)  # 重定向配置目录，避免污染真实配置
                cfg = gui.AIConfig()

                check("内置模板已加载", len(cfg.prompts) >= 18, len(cfg.prompts))
                check("默认选中默认模板", cfg.active_prompt == core.DEFAULT_PROMPT_NAME, cfg.active_prompt)
                check("已按分类分组",
                      "通用" in cfg.prompt_names_by_category()
                      and len(cfg.prompt_names_by_category()["工作协作"]) == 5,
                      {k: len(v) for k, v in cfg.prompt_names_by_category().items()})

                check("另存为新模板成功", cfg.add_prompt("我的模板", "测试正文") is True)
                check("新模板归入「我的模板」", "我的模板" in cfg.prompt_names_by_category().get("我的模板", []))
                check("切换后正文正确", cfg.active_prompt_text() == "测试正文")
                check("自定义模板有说明", "自定义模板" in cfg.prompt_description("我的模板"))

                check("内置模板不可删除", cfg.remove_prompt(core.DEFAULT_PROMPT_NAME) is False)
                check("自定义模板可删除", cfg.remove_prompt("我的模板") is True)
                check("删除后回退到默认模板", cfg.active_prompt == core.DEFAULT_PROMPT_NAME, cfg.active_prompt)

                cfg.set_prompt_text(core.DEFAULT_PROMPT_NAME, "被改坏的正文")
                check("内置模板可以被编辑", cfg.prompts[core.DEFAULT_PROMPT_NAME] == "被改坏的正文")
                check(
                    "恢复内置原文生效",
                    cfg.reset_prompt(core.DEFAULT_PROMPT_NAME)
                    == core.PROMPT_PRESETS[core.DEFAULT_PROMPT_NAME],
                )
                check("非内置模板无法恢复", cfg.reset_prompt("不存在的模板") is None)

                # 导出 / 导入
                out = core.Path(tmp) / "export.json"
                check("导出条数正确", cfg.export_prompts(out) == len(cfg.prompts))
                payload = json.loads(out.read_text(encoding="utf-8"))
                check("导出结构正确", isinstance(payload.get("prompts"), dict))
                added, skipped = cfg.import_prompts(out, overwrite=False)
                check("同名不覆盖时全部跳过", added == 0 and skipped == len(cfg.prompts), (added, skipped))
                out.write_text(json.dumps({"prompts": {"新导入的": "正文"}}, ensure_ascii=False),
                               encoding="utf-8")
                added, skipped = cfg.import_prompts(out, overwrite=False)
                check("新名字会被导入", added == 1 and "新导入的" in cfg.prompts, (added, skipped))

                # 旧模板迁移：写一个含旧内置模板名的配置，确认会被摘掉并备份
                legacy = core.RETIRED_PROMPT_NAMES[0]
                data = json.loads(cfg.config_path.read_text(encoding="utf-8"))
                data["prompts"][legacy] = "旧版正文"
                data["active_prompt"] = legacy
                cfg.config_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                cfg2 = gui.AIConfig()
                check("旧内置模板已被摘掉", legacy not in cfg2.prompts, legacy)
                check("active_prompt 已回退到默认模板",
                      cfg2.active_prompt == core.DEFAULT_PROMPT_NAME, cfg2.active_prompt)
                check("迁移前有备份",
                      any(p.name.startswith("ai_config.backup-") for p in core.Path(tmp).iterdir()),
                      [p.name for p in core.Path(tmp).iterdir()])

                on_disk = json.loads(cfg2.config_path.read_text(encoding="utf-8"))
                check("config 落盘含 prompts 字段", isinstance(on_disk.get("prompts"), dict))
                check("config 落盘含 active_prompt 字段", "active_prompt" in on_disk)
        finally:
            gui.CONFIG_DIR = saved_config_dir

    print()
    print("=== 22) 模型预设与时间范围 ===")
    try:
        import wechat_summary_gui as gui
    except Exception as exc:
        print("  INFO  跳过：无法导入 GUI 模块（%s）" % exc)
    else:
        services = gui.AIServiceConfig.SERVICES
        check("预设服务数 >= 12", len(services) >= 12, len(services))

        for name, cfg in services.items():
            check("服务 %s 有 base_url" % name, str(cfg.get("base_url", "")).startswith("http"))
            check("服务 %s 有模型清单" % name, len(cfg.get("models") or []) > 0)

        # 回归护栏：这些已下线的旧模型名不允许再出现在预设里
        dead = {
            "moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k", "moonshot-v1-auto",
            "deepseek-chat", "deepseek-reasoner", "qwen-max", "qwen-turbo", "kimi-latest",
            "kimi-k2", "kimi-k2.5", "glm-5", "glm-5.1", "hunyuan-a13b",
            "hunyuan-turbos-latest", "hunyuan-lite", "doubao-2.0-pro", "qwen3.7-max",
        }
        present = {m for cfg in services.values() for m in (cfg.get("models") or [])}
        check("预设里没有已下线的旧模型名", not (present & dead), sorted(present & dead))
        check("DeepSeek 用的是 V4 系列名",
              "deepseek-flash" in services.get("DeepSeek", {}).get("models", []))
        check("Kimi 用的是 K3/K2.6 系列名",
              "kimi-k3" in services.get("Kimi 月之暗面", {}).get("models", []))
        check("新增了腾讯 TokenHub 与阶跃星辰",
              "腾讯 TokenHub（混元）" in services and "阶跃星辰 StepFun" in services)
        check("智谱被标注为不支持模型列表",
              gui.AIServiceConfig.supports_model_list("智谱 GLM") is False)
        check("火山方舟被标注为不支持模型列表",
              gui.AIServiceConfig.supports_model_list("豆包（火山方舟）") is False)
        check("接口可达 (base_url 可取)", gui.AIServiceConfig.base_url("DeepSeek").startswith("http"))

        check("时间预设含「最近 24 小时」", gui.TIME_PRESETS.get("最近 24 小时") == 1440)
        check("时间预设含日历语义「今天 00:00 起」", gui.TIME_PRESETS.get("今天 00:00 起") == "today")
        check("时间预设含「自定义（起止时间）」", gui.TIME_PRESETS.get("自定义（起止时间）") == "range")
        check("小时上限已放开到 720", gui.MAX_TIME_HOURS == 720, gui.MAX_TIME_HOURS)
        check("_preset_start 能算今天零点",
              gui._preset_start("today", dt.datetime(2026, 9, 17, 15, 30))
              == dt.datetime(2026, 9, 17, 0, 0))
        check("_preset_start 能算昨天零点",
              gui._preset_start("yesterday", dt.datetime(2026, 9, 17, 15, 30))
              == dt.datetime(2026, 9, 16, 0, 0))
        check("_preset_start 能算本周一零点",
              gui._preset_start("week", dt.datetime(2026, 9, 17, 15, 30))
              == dt.datetime(2026, 9, 14, 0, 0))

    check("fetch_available_models 可调用", callable(core.fetch_available_models))
    check("remember_sent_summary 可调用", callable(core.remember_sent_summary))

    print()
    print("=== 23) 中止机制：CancelToken ===")
    token = core.CancelToken()
    check("初始未取消", token.cancelled is False)
    check("首次 cancel 返回 True", token.cancel("测试中止") is True)
    check("重复 cancel 返回 False（只算首次）", token.cancel() is False)
    check("中止原因被保留", token.reason == "测试中止", token.reason)
    try:
        token.raise_if_cancelled()
        check("raise_if_cancelled 会抛 JobCancelled", False, "没有抛异常")
    except core.JobCancelled as exc:
        check("raise_if_cancelled 会抛 JobCancelled", True)
        check("异常带上中止原因", exc.reason == "测试中止", exc.reason)
    token.reset()
    check("reset 后可复用", token.cancelled is False and token.raise_if_cancelled() is None)
    check("未置位时不抛异常", core._raise_if_cancelled(None) is None)

    print()
    print("=== 24) 中止机制：抓取中途取消 ===")
    started = core.CancelToken()
    started.cancel("还没开始就中止")
    wx_pre = FakeWx(log)
    try:
        core._collect_messages(wx_pre, START, 50, 0, list_control=FakeList(wx_pre),
                               focus=False, cancel=started)
        check("开跑前已取消 -> 立刻抛 JobCancelled", False, "没有抛异常")
    except core.JobCancelled:
        check("开跑前已取消 -> 立刻抛 JobCancelled", True)
    check("开跑前就取消时一次都没读", wx_pre.reads == 0, wx_pre.reads)

    mid = core.CancelToken()
    events = []

    def on_progress(info):
        events.append(info.get("step", 0))
        if info.get("step", 0) >= 2:
            mid.cancel("测试：中途叫停")

    wx_mid2 = FakeWx(log)
    try:
        core._collect_messages(wx_mid2, START, 50, 0, list_control=FakeList(wx_mid2),
                               focus=False, cancel=mid, progress=on_progress)
        check("中途取消 -> 抛 JobCancelled", False, "没有抛异常")
    except core.JobCancelled as exc:
        check("中途取消 -> 抛 JobCancelled", True)
        check("中途取消带原因", exc.reason == "测试：中途叫停", exc.reason)
    check("确实提前停了（没跑满 50 步）", len(events) <= 5, len(events))
    check("停止前已读到一些消息", wx_mid2.reads >= 3, wx_mid2.reads)

    print()
    print("=== 25) 中止机制：空窗口重试也会被取消 ===")
    token2 = core.CancelToken()
    token2.cancel("空窗口阶段中止")
    wx_empty = FakeWx([])
    try:
        core._collect_messages(wx_empty, START, 10, 0, list_control=FakeList(wx_empty),
                               focus=False, cancel=token2)
        check("空窗口 + 已取消 -> 抛 JobCancelled", False, "没有抛异常")
    except core.JobCancelled:
        check("空窗口 + 已取消 -> 抛 JobCancelled", True)

    print()
    if FAILED:
        print("RESULT: %d FAILED -> %s" % (len(FAILED), FAILED))
        return 1
    print("RESULT: ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
