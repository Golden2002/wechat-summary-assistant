# -*- coding: utf-8 -*-
"""GUI 冒烟测试（离屏运行，**不需要微信，也不会弹出窗口**）。

为什么需要它
------------
本项目唯一无法靠纯逻辑测试覆盖的就是 PySide6 界面代码 —— 而界面里的
「总结模板」下拉框、模板增删改、两个下拉框的联动，恰恰是容易改坏的地方。
Qt 的这类问题通常只在运行时才暴露，所以这里用 ``QT_QPA_PLATFORM=offscreen``
真正把窗口构造出来并操作一遍。

不会碰你的真实配置：``config`` 目录被重定向到临时目录。

运行::

    .venv\\Scripts\\python.exe tests\\test_gui_smoke.py

注：离屏模式下 Qt 可能打印一条 ``QFontDatabase: Cannot find font directory``
警告，那是离屏渲染没有字体目录导致的，与代码无关。
"""

from __future__ import annotations

import os
import sys
import tempfile

# 必须在导入 PySide6 之前设置，否则会尝试连真实显示器
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:  # pragma: no cover - 让中文输出在 GBK 控制台下也不乱码
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # pragma: no cover
    pass

from pathlib import Path  # noqa: E402

FAILED = []


def check(label: str, condition: bool, extra: object = "") -> None:
    mark = "  PASS  " if condition else "  FAIL  "
    tail = ("   -> " + repr(extra)) if (extra != "" and not condition) else ""
    print(mark + label + tail)
    if not condition:
        FAILED.append(label)


#: 版本演进说明：这里原来列的是三套旧模板名
#: （好友私聊 · 每日总结 / 医学生转行 · 医疗+AI 交流群日报 / 技术项目协作群 · 协作纪要）。
#: 它们已被 18 套新模板取代，并在加载配置时自动摘除（见 RETIRED_PROMPT_NAMES），
#: 所以这份「原版」冒烟测试同步换成新名字；新功能的覆盖见 test_gui_smoke_v2.py。
EXPECTED_TEMPLATES = [
    "通用群聊总结（原版）",
    "好友私聊 · 每日小结",
    "医疗与AI信息交流群 · 每日行业日报",
    "研发协作群 · 站会与协作纪要",
]


def main() -> int:
    try:
        from PySide6.QtWidgets import QApplication

        import wechat_summary as core
        import wechat_summary_gui as gui
    except Exception as exc:
        print("  SKIP  无法导入 PySide6 或本项目模块：%s" % exc)
        print("RESULT: SKIPPED（先运行 install.bat 装好依赖）")
        return 0

    try:
        app = QApplication.instance() or QApplication([])
    except Exception as exc:
        print("  SKIP  无法创建 QApplication（可能缺少离屏平台插件）：%s" % exc)
        print("RESULT: SKIPPED")
        return 0

    saved_config_dir = gui.CONFIG_DIR
    try:
        with tempfile.TemporaryDirectory() as tmp:
            gui.CONFIG_DIR = Path(tmp)  # 隔离：不碰真实 config/ai_config.json

            print("=== 1) 构造主窗口 ===")
            window = gui.MainWindow()
            check("MainWindow 构造成功", window is not None)

            print()
            print("=== 2) 两个模板下拉框都列出了内置模板 ===")
            main_names = [window.main_prompt_combo.itemText(i) for i in range(window.main_prompt_combo.count())]
            tab_names = [window.prompt_combo.itemText(i) for i in range(window.prompt_combo.count())]
            print("   主界面下拉框:", main_names)
            for name in EXPECTED_TEMPLATES:
                check("主界面下拉框含：" + name, name in main_names)
                check("提示词页下拉框含：" + name, name in tab_names)

            print()
            print("=== 3) 初始状态 ===")
            check("默认选中原版通用模板", window.ai_config.active_prompt == core.DEFAULT_PROMPT_NAME,
                  window.ai_config.active_prompt)
            check("初始标注为内置模板", window.prompt_kind_label.text() == "内置模板",
                  window.prompt_kind_label.text())
            check("编辑区载入了模板正文", len(window.prompt_edit.toPlainText()) > 100)

            print()
            print("=== 4) 主界面切模板 -> 提示词页联动 ===")
            target = "医疗与AI信息交流群 · 每日行业日报"
            window.main_prompt_combo.setCurrentText(target)
            app.processEvents()
            check("active_prompt 已更新", window.ai_config.active_prompt == target, window.ai_config.active_prompt)
            check("提示词页下拉框已同步", window.prompt_combo.currentText() == target,
                  window.prompt_combo.currentText())
            check("编辑区换成了该模板正文",
                  window.prompt_edit.toPlainText() == core.PROMPT_PRESETS[target])
            check("仍标注为内置模板", window.prompt_kind_label.text() == "内置模板")

            print()
            print("=== 5) 提示词页切模板 -> 主界面联动 ===")
            window.prompt_combo.setCurrentText("好友私聊 · 每日小结")
            app.processEvents()
            check("主界面下拉框已同步", window.main_prompt_combo.currentText() == "好友私聊 · 每日小结",
                  window.main_prompt_combo.currentText())
            check("active_prompt 已更新", window.ai_config.active_prompt == "好友私聊 · 每日小结")

            print()
            print("=== 6) 新增自定义模板 -> 两个下拉框都出现 ===")
            window.ai_config.add_prompt("冒烟测试模板", "测试正文")
            window._refresh_prompt_combos("冒烟测试模板")
            app.processEvents()
            check("主界面下拉框含新模板", window.main_prompt_combo.findText("冒烟测试模板") >= 0)
            check("提示词页下拉框含新模板", window.prompt_combo.findText("冒烟测试模板") >= 0)
            check("新模板标注为自定义", window.prompt_kind_label.text() == "自定义模板",
                  window.prompt_kind_label.text())

            print()
            print("=== 7) 删除自定义模板 -> 回退到默认模板 ===")
            window.ai_config.remove_prompt("冒烟测试模板")
            window._refresh_prompt_combos()
            app.processEvents()
            check("主界面下拉框已移除", window.main_prompt_combo.findText("冒烟测试模板") < 0)
            check("回退到默认模板", window.ai_config.active_prompt == core.DEFAULT_PROMPT_NAME,
                  window.ai_config.active_prompt)

            print()
            print("=== 8) 恢复内置原文 ===")
            window.prompt_combo.setCurrentText("好友私聊 · 每日小结")
            app.processEvents()
            window.ai_config.set_prompt_text("好友私聊 · 每日小结", "被我改坏了")
            restored = window.prompt_combo.currentText()
            text = window.ai_config.reset_prompt(restored)
            check("恢复内容等于内置原文", text == core.PROMPT_PRESETS["好友私聊 · 每日小结"])
            check("非内置模板无法恢复", window.ai_config.reset_prompt("不存在的模板") is None)

            print()
            print("=== 9) get_messages 的前置校验（用桩替换弹窗，避免离屏下阻塞）===")
            dialogs = []

            class FakeMessageBox:
                """替换掉 QMessageBox。

                ``QMessageBox.warning/information`` 是**模态阻塞**调用，离屏环境
                下没有人去点确定，会把测试直接卡死，所以这里必须用桩。
                """

                Yes, No = 1, 0

                @staticmethod
                def warning(*args, **kwargs):
                    dialogs.append("warning")

                @staticmethod
                def information(*args, **kwargs):
                    dialogs.append("information")

                @staticmethod
                def critical(*args, **kwargs):
                    dialogs.append("critical")

                @staticmethod
                def question(*args, **kwargs):
                    dialogs.append("question")
                    return FakeMessageBox.Yes

            original_message_box = gui.QMessageBox
            gui.QMessageBox = FakeMessageBox
            try:
                window.worker = None
                window.group_name_input.setText("")
                window.get_messages()
                check("空群名被拦截，未启动 worker", window.worker is None)
                check("弹出了一次警告", dialogs == ["warning"], dialogs)

                dialogs.clear()
                window.group_name_input.setText("某个群")
                window.service_combo.setCurrentText("DeepSeek")
                window.ai_config.configs["DeepSeek"] = {
                    "api_key": "",
                    "base_url": "https://api.deepseek.com",
                    "model": "deepseek-chat",
                }
                window.get_messages()
                check("未配置 API Key 被拦截，未启动 worker", window.worker is None)
                check("弹出了一次警告", dialogs == ["warning"], dialogs)
            finally:
                gui.QMessageBox = original_message_box

            print()
            print("=== 10) 时间范围：预设与自定义双向联动 ===")
            check("有时间预设下拉框", window.time_preset_combo.count() >= 5,
                  window.time_preset_combo.count())
            check("预设里含「最近 24 小时」",
                  window.time_preset_combo.findText("最近 24 小时") >= 0)

            window.time_preset_combo.setCurrentText("最近 24 小时")
            app.processEvents()
            check("选「最近 24 小时」= 1440 分钟", window.current_time_minutes() == 1440,
                  window.current_time_minutes())
            check("小时框跟着变成 24", window.hours_spin.value() == 24, window.hours_spin.value())

            window.time_preset_combo.setCurrentText("最近 7 天")
            app.processEvents()
            check("选「最近 7 天」= 10080 分钟", window.current_time_minutes() == 10080,
                  window.current_time_minutes())

            window.hours_spin.setValue(5)
            app.processEvents()
            check("手改小时 -> 自动切到「自定义」",
                  window.time_preset_combo.currentText() == "自定义（相对时间）",
                  window.time_preset_combo.currentText())

            window.time_preset_combo.setCurrentText("自定义（相对时间）")
            window.hours_spin.setValue(12)
            window.minutes_spin.setValue(30)
            app.processEvents()
            check("自定义 12h30m = 750 分钟", window.current_time_minutes() == 750,
                  window.current_time_minutes())

            window.hours_spin.setValue(24)
            window.minutes_spin.setValue(0)
            app.processEvents()
            check("改回 24 小时自动匹配预设",
                  window.time_preset_combo.currentText() == "最近 24 小时",
                  window.time_preset_combo.currentText())
            check("小时上限为 720（原版只有 23）", window.hours_spin.maximum() == 720,
                  window.hours_spin.maximum())

            print()
            print("=== 11) 模型下拉框可编辑 + 刷新按钮 ===")
            first_card = window.scroll_layout.itemAt(0).widget()
            check("第一张是 ConfigCard", isinstance(first_card, gui.ConfigCard))
            if isinstance(first_card, gui.ConfigCard):
                check("模型框可编辑（可手输任意模型名）", first_card.model_combo.isEditable())
                check("有「刷新模型列表」按钮",
                      first_card.refresh_btn.text().startswith("刷新"),
                      first_card.refresh_btn.text())
                items = [first_card.model_combo.itemText(i)
                         for i in range(first_card.model_combo.count())]
                check("模型清单非空", len(items) > 0, items)
    finally:
        gui.CONFIG_DIR = saved_config_dir

    print()
    if FAILED:
        print("RESULT: %d FAILED -> %s" % (len(FAILED), FAILED))
        return 1
    print("RESULT: ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
