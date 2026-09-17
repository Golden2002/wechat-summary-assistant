# -*- coding: utf-8 -*-
"""真机集成验证：用滚动收集引擎抓真实微信记录（**不调用 AI、不发送任何消息**）。

用途：换了微信版本 / 怀疑「消息读不全」时，先用它确认抓取层是否正常，
不必走完整的总结流程。

用法：
    .venv\\Scripts\\python.exe tools\\collect_probe.py "群名" [回溯小时数] [预算秒数]
"""
from __future__ import annotations

import datetime as dt
import os
import sys
import time

# 允许从任意工作目录运行：把项目根加入 import 路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wechat_summary import (
    CancelToken,
    JobCancelled,
    _auto_load_rounds,
    _collect_messages,
    _create_wechat,
    _log_detail,
    _render,
    _switch_to,
)


def main() -> int:
    group = sys.argv[1] if len(sys.argv) > 1 else None
    hours = float(sys.argv[2]) if len(sys.argv) > 2 else 24.0
    budget = float(sys.argv[3]) if len(sys.argv) > 3 else 240.0
    #: 第 4 个参数：在第 N 屏之后模拟「用户按 Esc 中止」，用来验证取消链路
    cancel_after_step = int(sys.argv[4]) if len(sys.argv) > 4 else 0

    start = dt.datetime.now() - dt.timedelta(hours=hours)
    print(f"目标：{group!r}  时间范围：{start:%Y-%m-%d %H:%M} ~ 现在  预算 {budget:.0f}s")
    if cancel_after_step:
        print(f"*** 将在第 {cancel_after_step} 屏之后请求中止（模拟按 Esc）***")

    t0 = time.monotonic()
    wx = _create_wechat()
    if group and not _switch_to(wx, group):
        print("切换失败")
        return 1
    print(f"已连接，当前会话：{wx.ChatInfo()}")

    cancel = CancelToken()
    events = []

    def progress(info):
        events.append(info)
        if info.get("done"):
            return
        print(
            f"  step={info.get('step'):>3} 已读={info.get('collected'):>4} "
            f"本步新增={info.get('gained', 0):>3} 方式={info.get('strategy', '-')}",
            flush=True,
        )
        # 模拟用户在这一屏之后按了 Esc
        if cancel_after_step and int(info.get("step") or 0) >= cancel_after_step:
            first = cancel.cancel("探针模拟按 Esc")
            if first:
                print(f"  >>> 已请求中止（t={time.monotonic() - t0:.1f}s）", flush=True)

    rounds = _auto_load_rounds(hours)
    print(f"翻页步数上限：{rounds}（与正式流程一致：按 {hours:g} 小时估算）")
    try:
        records = _collect_messages(
            wx, start, rounds, progress=progress, budget=budget, cancel=cancel
        )
    except JobCancelled as exc:
        elapsed = time.monotonic() - t0
        print("\n" + "=" * 60)
        print(f"已被中止（{exc.reason}），耗时 {elapsed:.1f}s")
        print(f"中止时进度回调 {len(events)} 次，最后一屏 step={events[-1].get('step') if events else '-'}")
        return 0
    elapsed = time.monotonic() - t0
    detail, lines = _render(records)

    print("\n" + "=" * 60)
    print(f"耗时 {elapsed:.1f}s，原始记录 {len(records)} 条，渲染出 {len(lines)} 行")
    anchors = [r.resolved or r.time for r in records if r.kind == "time" and (r.resolved or r.time)]
    if anchors:
        print(f"覆盖时间：{min(anchors):%Y-%m-%d %H:%M} ~ {max(anchors):%Y-%m-%d %H:%M}")
        print(f"时间分隔条 {len(anchors)} 个：{[a.strftime('%m-%d %H:%M') for a in anchors]}")
    else:
        print("没有解析到任何时间分隔条")

    speakers = {}
    for r in records:
        if r.kind in ("friend", "self"):
            speakers[r.sender] = speakers.get(r.sender, 0) + 1
    print(f"发言人分布（前 10）：{sorted(speakers.items(), key=lambda kv: -kv[1])[:10]}")

    print("\n--- 最早 12 行 ---")
    for line in lines[:12]:
        print("   ", line[:100])
    print("--- 最新 8 行 ---")
    for line in lines[-8:]:
        print("   ", line[:100])

    if events:
        print(f"\n进度回调 {len(events)} 次，最后一条：{events[-1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
