# -*- coding: utf-8 -*-
"""GUI 冒烟测试：在 offscreen 模式下构造主窗口，检查各标签页能建起来。

不需要真实微信，也不需要真实配置（配置目录会被重定向到临时目录）。
"""
from __future__ import annotations

import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # pragma: no cover
    pass

FAILED = []


def check(label: str, condition: bool, extra: object = "") -> None:
    print(("  PASS  " if condition else "  FAIL  ") + label + ("" if condition else f"   -> {extra!r}"))
    if not condition:
        FAILED.append(label)


def main() -> int:
    import wechat_summary as core
    import wechat_summary_gui as gui
    from PySide6.QtWidgets import QApplication

    tmp = tempfile.mkdtemp(prefix="wxsum_gui_")
    gui.CONFIG_DIR = core.Path(tmp)

    app = QApplication.instance() or QApplication(sys.argv)
    window = gui.MainWindow()

    check("主窗口构造成功", window is not None)
    check("模板下拉框有分类标题", any(
        window.main_prompt_combo.itemText(i).startswith("——")
        for i in range(window.main_prompt_combo.count())
    ))
    check("模板下拉框含 18 套内置模板",
          sum(1 for n in core.PROMPT_PRESETS if window.main_prompt_combo.findText(n) >= 0) == len(core.PROMPT_PRESETS))

    # 时间范围：三条路径
    window.time_preset_combo.setCurrentText("最近 24 小时")
    start, end, hours = window.current_time_range()
    check("最近 24 小时 -> 相对模式 24h", start is None and hours == 24.0, (start, hours))

    window.time_preset_combo.setCurrentText("今天 00:00 起")
    start, end, hours = window.current_time_range()
    import datetime as dt
    check("今天 00:00 起 -> 绝对模式且为今天零点",
          start is not None and start.hour == 0 and start.minute == 0
          and start.date() == dt.date.today(), start)

    window.time_preset_combo.setCurrentText("自定义（起止时间）")
    start, end, hours = window.current_time_range()
    check("自定义起止 -> 绝对模式", start is not None and hours is None)

    window.time_preset_combo.setCurrentText("最近 3 小时")
    start, end, hours = window.current_time_range()
    check("回到相对模式", start is None and hours == 3.0, (start, hours))

    # 输出格式
    window.plain_text_check.setChecked(True)
    window._set_summary_text("# 标题\n\n**要点**：完成\n\n- 甲\n- 乙")
    preview = window.summary_edit.toPlainText()
    check("预览已去 Markdown", "#" not in preview and "**" not in preview, preview)
    check("预览保留内容", "要点" in preview and "· 甲" in preview, preview)

    window.plain_text_check.setChecked(False)
    window._set_summary_text("# 标题")
    check("关闭纯文本后保留原文", window.summary_edit.toPlainText() == "# 标题")

    # 中止机制
    window._set_busy(False)
    check("注册了 Esc / 空格 两个中止快捷键", len(window._abort_shortcuts) == 2,
          len(window._abort_shortcuts))
    check("空闲时快捷键禁用（不然会吃掉输入框里的空格）",
          all(not s.isEnabled() for s in window._abort_shortcuts))
    check("空闲时中止按钮禁用", window.abort_btn.isEnabled() is False)

    window._set_busy(True)
    check("抓取时快捷键启用", all(s.isEnabled() for s in window._abort_shortcuts))
    check("抓取时整个窗口仍可用（否则快捷键收不到按键）", window.isEnabled() is True)
    check("抓取时输入区被禁用", window.group_name_input.isEnabled() is False)
    check("抓取时中止按钮启用", window.abort_btn.isEnabled() is True)

    window.abort_job()
    check("abort_job 置位取消信号", window.cancel_token.cancelled is True)
    check("中止原因被记录", "中止" in window.cancel_token.reason, window.cancel_token.reason)
    check("状态栏提示已在中止", "中止" in window.status_label.text(), window.status_label.text())

    window.on_summary_cancelled("用户中止")
    check("中止后回到空闲状态", window.group_name_input.isEnabled() is True)
    check("中止后快捷键再次禁用",
          all(not s.isEnabled() for s in window._abort_shortcuts))
    check("中止不弹错误框（状态栏给出说明）", "已中止" in window.status_label.text(),
          window.status_label.text())

    # SummaryWorker 收到 JobCancelled 时应当发 cancelled 而不是 error
    import wechat_summary as _core
    seen_signals = {"cancelled": [], "error": [], "finished": []}
    original = gui.get_wechat_messages

    def _raise_job_cancelled(*_a, **_k):
        raise _core.JobCancelled("测试中止")

    try:
        gui.get_wechat_messages = _raise_job_cancelled
        worker = gui.SummaryWorker("测试群", {"api_key": "x"}, "模板")
        worker.cancelled.connect(lambda r: seen_signals["cancelled"].append(r))
        worker.error.connect(lambda r: seen_signals["error"].append(r))
        worker.finished.connect(lambda r: seen_signals["finished"].append(r))
        worker.run()          # 同步执行，不启线程
    finally:
        gui.get_wechat_messages = original

    check("worker 把 JobCancelled 转成 cancelled 信号",
          seen_signals["cancelled"] == ["测试中止"], seen_signals)
    check("worker 不再把它报成错误", seen_signals["error"] == [], seen_signals)

    # 服务预设
    check("服务预设 >= 12", len(gui.AIServiceConfig.SERVICES) >= 12, len(gui.AIServiceConfig.SERVICES))
    dead = {"deepseek-chat", "deepseek-reasoner", "moonshot-v1-8k", "moonshot-v1-32k",
            "moonshot-v1-128k", "qwen-max", "qwen-turbo", "kimi-k2", "glm-5", "hunyuan-a13b"}
    present = {m for cfg in gui.AIServiceConfig.SERVICES.values() for m in cfg.get("models", [])}
    check("预设不含已下线的旧模型名", not (present & dead), sorted(present & dead))
    check("智谱被标记为不支持模型列表",
          gui.AIServiceConfig.supports_model_list("智谱 GLM") is False)
    check("DeepSeek 支持模型列表",
          gui.AIServiceConfig.supports_model_list("DeepSeek") is True)
    check("每个服务都有 base_url 与 models",
          all(str(c.get("base_url", "")).startswith("http") and c.get("models")
              for c in gui.AIServiceConfig.SERVICES.values()))

    # 模板导入导出
    import json
    payload = gui.Path(tmp) / "out.json"
    count = window.ai_config.export_prompts(payload)
    check("导出模板成功", count >= len(core.PROMPT_PRESETS), count)
    check("导出文件可解析", isinstance(json.loads(payload.read_text(encoding="utf-8"))["prompts"], dict))
    added, skipped = window.ai_config.import_prompts(payload, overwrite=False)
    check("导入自己导出的文件全部跳过", added == 0 and skipped > 0, (added, skipped))

    print()
    if FAILED:
        print(f"RESULT: {len(FAILED)} FAILED -> {FAILED}")
        return 1
    print("RESULT: ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
