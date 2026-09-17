# -*- coding: utf-8 -*-
"""微信群聊总结助手 —— 图形界面。

原版项目：https://github.com/Vita0519/wechat_summary （作者 Vita0519）
本文件在保留原版界面结构与视觉风格的前提下，做了以下适配：

1. 移除对 ``import resources`` 的依赖。原版通过 Qt 资源文件（``.qrc`` 编译产物）
   加载 ``:main.ico`` / ``:/wechat.ico``，仓库里并未提供该模块，照抄会直接
   ``ModuleNotFoundError``。这里改为可选加载，缺失时不影响启动。
2. 配置文件、日志、总结输出一律以 **本文件所在目录** 为基准，
   避免双击 ``run.bat`` 时工作目录漂移导致配置"丢失"。
3. 修复原版 ``send_to_group`` / ``save_summary`` 在异常分支里引用未赋值变量
   ``original_text`` 会抛 ``UnboundLocalError`` 的问题。
4. 启动时若 wxauto4 缺失，弹出可读的提示窗口而不是抛堆栈。
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional

from PySide6.QtCore import QDateTime, QSettings, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QKeySequence, QPalette, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDateTimeEdit,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from loguru import logger

# 核心逻辑与路径基准
from wechat_summary import (
    BASE_DIR,
    CATEGORY_ORDER,
    CONFIG_DIR,
    DEFAULT_PROMPT_NAME,
    PROMPT_CATEGORY,
    PROMPT_DESCRIPTION,
    PROMPT_PRESETS,
    RETIRED_PROMPT_NAMES,
    CancelToken,
    JobCancelled,
    build_system_prompt,
    fetch_available_models,
    get_wechat_messages,
    save_summary,
    send_summary,
)
from wechat_text import analyze_markdown, clean_for_wechat, looks_like_markdown

# 可选的图标资源：原版用 Qt 资源系统，这里做成"有就用、没有就跳过"
try:  # pragma: no cover - 取决于运行环境
    import resources  # type: ignore  # noqa: F401
except Exception:  # pragma: no cover
    resources = None  # type: ignore

APP_ICON_PATH = BASE_DIR / "app.ico"


def _app_icon() -> QIcon:
    """拿到一个可用的窗口图标；没有就返回空图标。"""
    if APP_ICON_PATH.exists():
        return QIcon(str(APP_ICON_PATH))
    if resources is not None:
        return QIcon(":main.ico")
    return QIcon()


# --------------------------------------------------------------------------- #
# 视觉样式（沿用原版配色）
# --------------------------------------------------------------------------- #


class ModernStyle:
    BACKGROUND = "#ffffff"
    SECONDARY_BACKGROUND = "#f5f5f7"
    TEXT = "#1d1d1f"
    SECONDARY_TEXT = "#86868b"
    ACCENT = "#0066cc"
    BORDER = "#d2d2d7"

    FONT_FAMILY = "-apple-system, BlinkMacSystemFont, Microsoft YaHei, Segoe UI"

    @staticmethod
    def setup_widget(widget: QWidget) -> None:
        widget.setStyleSheet(
            f"""
            QMainWindow {{
                background-color: {ModernStyle.BACKGROUND};
            }}

            QWidget {{
                background-color: {ModernStyle.BACKGROUND};
                color: {ModernStyle.TEXT};
                font-family: {ModernStyle.FONT_FAMILY};
            }}

            QLabel {{
                color: {ModernStyle.TEXT};
                font-size: 12px;
                padding: 0;
                margin: 0;
            }}

            QLineEdit, QSpinBox, QComboBox {{
                border: 1px solid {ModernStyle.BORDER};
                border-radius: 3px;
                padding: 3px 8px;
                background: white;
                height: 24px;
                font-size: 12px;
            }}

            QPushButton {{
                background-color: {ModernStyle.ACCENT};
                color: white;
                border: none;
                border-radius: 3px;
                padding: 3px 12px;
                height: 24px;
                font-size: 12px;
                min-width: 80px;
            }}

            QPushButton:hover {{
                background-color: #0077ed;
            }}

            QPushButton:disabled {{
                background-color: #b9d4f0;
            }}

            QPushButton#mainButton {{
                height: 32px;
                font-size: 13px;
                font-weight: 500;
            }}

            QTextEdit {{
                border: 1px solid {ModernStyle.BORDER};
                border-radius: 3px;
                padding: 8px;
                background: white;
                font-size: 12px;
            }}

            QTabWidget::pane {{
                border: none;
                background-color: {ModernStyle.BACKGROUND};
            }}

            QTabBar::tab {{
                padding: 6px 16px;
                margin-right: 2px;
                color: {ModernStyle.TEXT};
                border: none;
                background: none;
                font-size: 12px;
            }}

            QTabBar::tab:selected {{
                color: {ModernStyle.ACCENT};
                border-bottom: 2px solid {ModernStyle.ACCENT};
            }}

            QScrollArea {{
                border: none;
            }}

            QFrame#configCard {{
                border: 1px solid {ModernStyle.BORDER};
                border-radius: 3px;
                background: white;
                padding: 12px;
                margin: 4px 0;
            }}
            """
        )


# --------------------------------------------------------------------------- #
# 后台线程：避免读取消息 / 调用大模型时界面卡死
# --------------------------------------------------------------------------- #


class SummaryWorker(QThread):
    """异步生成总结的工作线程。"""

    finished = Signal(str)   # 成功：总结文本
    error = Signal(str)      # 失败：错误信息
    progress = Signal(str)   # 进度：给状态栏用的一句话
    cancelled = Signal(str)  # 用户中止：中止原因

    def __init__(
        self,
        group_name: str,
        service_config: dict,
        prompt: str,
        start_time: Optional[dt.datetime] = None,
        hours: Optional[float] = None,
        end_time: Optional[dt.datetime] = None,
        cancel_token: Optional[CancelToken] = None,
    ) -> None:
        super().__init__()
        self.group_name = group_name
        self.service_config = service_config
        self.prompt = prompt
        self.start_time = start_time
        self.hours = hours
        self.end_time = end_time
        self.cancel_token = cancel_token or CancelToken()

    def _on_progress(self, info: dict) -> None:
        """把抓取进度翻译成一句人话。

        抓 24 小时的大群可能要翻几十屏、耗时几分钟，没有进度反馈用户会以为卡死，
        然后手动杀掉进程 —— 这本身就会导致「只拿到一部分消息」。
        """
        try:
            if info.get("done"):
                used = info.get("used", info.get("collected", 0))
                self.progress.emit(f"抓取完成（共 {used} 条有效记录），正在生成总结…")
                return
            step = int(info.get("step") or 0)
            if step <= 0:
                self.progress.emit("正在读取当前窗口的消息…")
                return
            self.progress.emit(
                f"向上翻页中：第 {step} 屏，已读取 {info.get('collected', 0)} 条记录…"
                "（按 Esc 或空格可中止）"
            )
        except Exception:  # noqa: BLE001 - 进度回调绝不影响主流程
            pass

    def run(self) -> None:
        try:
            summary = get_wechat_messages(
                self.group_name,
                self.hours,
                self.service_config,
                self.prompt,
                start_time=self.start_time,
                end_time=self.end_time,
                progress=self._on_progress,
                cancel=self.cancel_token,
            )
            if summary:
                self.finished.emit(summary)
            else:
                self.error.emit("未获取到消息，或尚未配置可用的 AI 服务")
        except JobCancelled as exc:
            # 用户主动中止：这是一次正常的操作，不是错误，别弹错误框
            logger.info(f"用户中止了本次抓取：{exc.reason}")
            self.cancelled.emit(exc.reason)
        except Exception as exc:  # noqa: BLE001 - 线程内必须兜底，否则静默死掉
            logger.exception("生成总结失败")
            self.error.emit(str(exc))


# --------------------------------------------------------------------------- #
# 配置
# --------------------------------------------------------------------------- #


class AIServiceConfig:
    """内置 AI 服务预设。

    ⚠️ **模型名是厂商最易变的东西，硬编码必然过期。** 本项目已经踩过两次：
    Kimi 的 ``moonshot-v1-8k/32k/128k`` 于 2026-08-31 全部下线，DeepSeek 的
    ``deepseek-chat`` / ``deepseek-reasoner`` 也已于 2026-07-24 被 V4 系列取代。

    所以这份清单只是「开箱可用」的起点，真正可靠的是另外两个手段：

    1. 模型下拉框**可编辑** —— 直接输入任何模型名；
    2. 卡片上的「刷新模型列表」—— 用你的 API Key 调 ``GET /models``，
       拉取该服务**当前**真实可用的模型。

    ⚠️ 但 ``GET /models`` **不是所有厂商都提供**（已逐一实测）：

    * 提供：DeepSeek / Kimi / DashScope / MiniMax / 硅基流动 / OpenRouter /
      OpenAI / 腾讯 TokenHub / 阶跃星辰 / Ollama；
    * **不提供**：智谱 GLM（OpenAPI 里没有这个路径）、火山方舟（模型列表走的是
      另一套 AK/SK 签名的管理接口）。这两家只能手工输模型名，GUI 会明确提示。

    刷新结果会写进 ``config/ai_config.json``，重启后依然有效。
    """

    #: 这份预设的核对时间（厂商随时可能调整，以「刷新模型列表」的结果为准）
    PRESETS_CHECKED_AT = "2026-09-17"

    #: 每个服务：
    #:   base_url             OpenAI 兼容端点
    #:   models               当前可用的对话模型（按「适合长文本总结」排序）
    #:   supports_model_list  是否实现了 GET /models（决定「刷新模型列表」能不能用）
    #:   notes                给用户看的注意事项（含已下线模型名，避免再被选中）
    SERVICES = {
        # ------------------------- 国内直连 -------------------------
        "DeepSeek": {
            "base_url": "https://api.deepseek.com",
            "models": ["deepseek-flash", "deepseek-v4-pro"],
            "supports_model_list": True,
            "notes": "deepseek-flash = V4.1-Flash（1M 上下文，带视觉）；"
                     "deepseek-v4-pro 为纯文本 1M。"
                     "deepseek-chat / deepseek-reasoner 已于 2026-07-24 下线。",
        },
        "Kimi 月之暗面": {
            "base_url": "https://api.moonshot.cn/v1",
            "models": ["kimi-k3", "kimi-k2.7-code", "kimi-k2.7-code-highspeed", "kimi-k2.6"],
            "supports_model_list": True,
            "notes": "kimi-k3 为旗舰（1M 上下文）。"
                     "moonshot-v1-* 全系已于 2026-08-31 下线，kimi-k2 / k2.5 也已下线。",
        },
        "通义千问（DashScope）": {
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "models": ["qwen3.8-max", "qwen3.8-flash", "qwen3.7-plus", "qwen3.7-flash", "qwen-long"],
            "supports_model_list": True,
            "notes": "qwen-long 支持 10M 上下文，超长聊天记录首选。"
                     "阿里云已在推工作空间专属域名，共享域名仍可用但被标记为 deprecated。",
        },
        "智谱 GLM": {
            "base_url": "https://open.bigmodel.cn/api/paas/v4",
            "models": ["glm-5.3", "glm-5.3-flash", "glm-5.2"],
            "supports_model_list": False,
            "notes": "智谱**没有** GET /models，请直接输模型名（glm-5 已是旧版）。",
        },
        "豆包（火山方舟）": {
            "base_url": "https://ark.cn-beijing.volces.com/api/v3",
            "models": ["doubao-seed-2-1-pro-260915", "doubao-seed-2-0-lite-260428"],
            "supports_model_list": False,
            "notes": "必须填**完整版本号**（如 doubao-seed-2-0-lite-260428）；"
                     "只写 doubao-seed-2-0-lite 会 404。"
                     "另外要在方舟控制台「开通管理」里开通该模型，否则同样 404。"
                     "官方没有 GET /models（模型列表走 AK/SK 签名的管理接口）。",
        },
        "MiniMax": {
            "base_url": "https://api.minimaxi.com/v1",
            "models": ["MiniMax-M3"],
            "supports_model_list": True,
            "notes": "国内站 api.minimaxi.com；国际站 api.minimax.io。"
                     "旧的 api.minimax.chat 已弃用。",
        },
        "腾讯 TokenHub（混元）": {
            "base_url": "https://tokenhub.tencentcloudmaas.com/v1",
            "models": ["hy3", "hy4-preview"],
            "supports_model_list": True,
            "notes": "混元的后继平台，hy3 是国产长文本里最便宜的一档（256K）。"
                     "旧域名 api.hunyuan.cloud.tencent.com 仍能应答，但不再上新模型；"
                     "旧的 hunyuan-* 模型名在 TokenHub 上不存在。",
        },
        "阶跃星辰 StepFun": {
            "base_url": "https://api.stepfun.com/v1",
            "models": ["step-3.7-flash", "step-3.5-flash"],
            "supports_model_list": True,
            "notes": "step-3 / step-2-* / step-1* 已于 2026-07-08 下线。",
        },
        # ------------------------- 聚合 / 国际 -------------------------
        "硅基流动 SiliconFlow": {
            "base_url": "https://api.siliconflow.cn/v1",
            "models": [
                "deepseek-ai/DeepSeek-V4-Flash",
                "Qwen/Qwen3.5-122B-A10B",
                "Qwen/Qwen3.5-35B-A3B",
                "meituan-longcat/LongCat-2.0",
            ],
            "supports_model_list": True,
            "notes": "海外站为 https://api.siliconflow.com/v1。"
                     "DeepSeek-V4-Flash 与 LongCat-2.0 都是 1M 上下文；"
                     "Qwen3.5-35B-A3B 是这里最便宜的长文本档。",
        },
        "OpenRouter（聚合）": {
            "base_url": "https://openrouter.ai/api/v1",
            "models": [
                "deepseek/deepseek-v4.1-flash",
                "openai/gpt-5.6-sol",
                "google/gemini-3.7-flash",
                "qwen/qwen3.8-2.4t-a95b",
                "x-ai/grok-4.6",
            ],
            "supports_model_list": True,
            "notes": "聚合站，模型 id 带厂商前缀；一个 Key 试遍各家。",
        },
        "OpenAI": {
            "base_url": "https://api.openai.com/v1",
            "models": ["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna", "gpt-6-astra"],
            "supports_model_list": True,
            "notes": "国内直连通常不可用，需要自备中转。",
        },
        # ------------------------- 本地 -------------------------
        "Ollama（本地）": {
            "base_url": "http://localhost:11434/v1",
            "models": ["qwen3:8b", "llama3.2", "deepseek-r1:7b"],
            "supports_model_list": True,
            "notes": "本机跑，数据不出内网；但小模型的总结质量明显弱于云端大模型。",
        },
    }

    @classmethod
    def base_url(cls, name: str) -> str:
        return str(cls.SERVICES.get(name, {}).get("base_url", ""))

    @classmethod
    def notes(cls, name: str) -> str:
        return str(cls.SERVICES.get(name, {}).get("notes", ""))

    @classmethod
    def supports_model_list(cls, name: str) -> bool:
        """该服务是否实现了 ``GET /models``（未知的服务默认当作支持）。"""
        return bool(cls.SERVICES.get(name, {}).get("supports_model_list", True))

    @classmethod
    def add_service(cls, name: str, base_url: str, models, supports_model_list: bool = True) -> None:
        cls.SERVICES[name] = {
            "base_url": base_url,
            "models": models if isinstance(models, list) else [models],
            "supports_model_list": bool(supports_model_list),
            "notes": "",
        }

    @classmethod
    def set_models(cls, name: str, models) -> None:
        """用刷新或手输结果替换某服务的模型清单（空列表则忽略）。"""
        if name not in cls.SERVICES:
            return
        cleaned = []
        for model in models or []:
            text = str(model).strip()
            if text and text not in cleaned:
                cleaned.append(text)
        if cleaned:
            cls.SERVICES[name]["models"] = cleaned


class AIConfig:
    """读写 ``config/ai_config.json``：AI 服务密钥 + 系统提示词模板。

    API Key 只保存在本地文件中，不会上传到除你所选 AI 服务之外的任何地方。

    提示词模板存在 ``prompts``（模板名 -> 提示词正文），``active_prompt``
    记录当前选中的模板名。内置模板来自 ``wechat_summary.PROMPT_PRESETS``，
    用户可以改正文、可以另存为新模板，但不能删除内置模板（只能还原原文）。
    """

    def __init__(self) -> None:
        self.config_dir = CONFIG_DIR
        self.config_file = "ai_config.json"
        self.config_path = self.config_dir / self.config_file

        os.makedirs(self.config_dir, exist_ok=True)

        self.configs: dict = {}
        self.last_service: str = ""

        # 系统提示词模板：内置预设打底，用户保存的同名项覆盖内置
        self.prompts: Dict[str, str] = dict(PROMPT_PRESETS)
        self.active_prompt: str = DEFAULT_PROMPT_NAME

        self.load_configs()

    # -- 读写 ----------------------------------------------------------------

    def load_configs(self) -> None:
        try:
            if self.config_path.exists():
                with open(self.config_path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)

                if "services" in data:
                    self.configs = data["services"]
                    for service_name, config in self.configs.items():
                        if service_name not in AIServiceConfig.SERVICES:
                            AIServiceConfig.SERVICES[service_name] = {
                                "base_url": config.get("base_url", ""),
                                "models": [config.get("model", "")],
                                "supports_model_list": config.get("supports_model_list", True),
                                "notes": "",
                            }
                        # 刷新/手输过的模型清单要留住，否则重启后又回到内置预设
                        AIServiceConfig.set_models(service_name, config.get("models"))
                # 提示词：新版存 prompts 字典；旧版只有一个 prompt 字段，
                # 这里迁移成自定义模板，避免用户已写好的提示词丢失。
                saved_prompts = data.get("prompts")
                if isinstance(saved_prompts, dict) and saved_prompts:
                    self.prompts.update({str(k): str(v) for k, v in saved_prompts.items()})
                elif data.get("prompt"):
                    self.prompts["我的自定义提示词（旧版迁移）"] = str(data["prompt"])

                active = data.get("active_prompt")
                if isinstance(active, str) and active in self.prompts:
                    self.active_prompt = active

                self.last_service = data.get("last_service", "")
                self._retire_legacy_prompts()
            else:
                self.configs = {
                    "DeepSeek": {
                        "api_key": "",
                        "base_url": "https://api.deepseek.com",
                        "model": "deepseek-flash",
                    },
                    "通义千问（DashScope）": {
                        "api_key": "",
                        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                        "model": "qwen3.8-flash",
                    },
                }
                self.save_configs()
        except Exception as exc:
            logger.error(f"加载配置失败: {exc}")

    def _retire_legacy_prompts(self) -> None:
        """摘掉已被新模板取代的旧内置模板。

        只删「名字在 RETIRED_PROMPT_NAMES 里、且正文与旧内置原文完全一致」的条目：
        用户自己改过的模板一定保留。动手之前先把整份配置备份一份，方便回退。
        """
        legacy = [name for name in RETIRED_PROMPT_NAMES if name in self.prompts]
        if not legacy:
            return

        backup = self.config_dir / f"ai_config.backup-{dt.datetime.now():%Y%m%d_%H%M%S}.json"
        try:
            if self.config_path.exists():
                backup.write_text(
                    self.config_path.read_text(encoding="utf-8"), encoding="utf-8"
                )
                logger.info(f"配置迁移前已备份到：{backup}")
        except Exception as exc:  # noqa: BLE001 - 备份失败不阻断迁移
            logger.warning(f"备份配置失败（继续迁移）：{exc}")

        for name in legacy:
            del self.prompts[name]
        if self.active_prompt in legacy:
            self.active_prompt = DEFAULT_PROMPT_NAME
        self.save_configs()
        logger.info(
            "已把 %d 个旧内置模板替换为新版（共 %d 套新模板）：%s",
            len(legacy),
            len(PROMPT_PRESETS),
            "、".join(legacy),
        )

    def save_configs(self) -> None:
        try:
            data = {
                "services": self.configs,
                "prompts": self.prompts,
                "active_prompt": self.active_prompt,
                "last_service": self.last_service,
            }
            with open(self.config_path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=4)
            logger.info(f"配置已保存到: {self.config_path}")
        except Exception as exc:
            logger.error(f"保存配置失败: {exc}")
            QMessageBox.warning(None, "警告", f"保存配置失败: {exc}")

    # -- 增删查 --------------------------------------------------------------

    def add_config(self, name: str, config: dict) -> None:
        self.configs[name] = config
        if name not in AIServiceConfig.SERVICES:
            AIServiceConfig.SERVICES[name] = {
                "base_url": config.get("base_url", ""),
                "models": [config.get("model", "")],
                "supports_model_list": bool(config.get("supports_model_list", True)),
                "notes": str(config.get("notes", "")),
            }
        self.save_configs()

    def remove_config(self, name: str) -> None:
        if name in self.configs:
            del self.configs[name]
            self.save_configs()

    def get_config(self, name: str) -> dict:
        return self.configs.get(name, {})

    def save_service_models(self, name: str, models) -> None:
        """把刷新（或手动整理）得到的模型清单落到 ``config/ai_config.json``。"""
        if name not in AIServiceConfig.SERVICES:
            return
        AIServiceConfig.set_models(name, models)
        entry = self.configs.setdefault(name, {})
        entry["models"] = list(AIServiceConfig.SERVICES[name]["models"])
        entry.setdefault("base_url", AIServiceConfig.base_url(name))
        entry.setdefault("model", "")
        entry.setdefault("api_key", "")
        self.save_configs()

    # -- 系统提示词模板 ------------------------------------------------------

    def prompt_names(self) -> List[str]:
        """按分类顺序返回模板名（内置分类在前，自定义的统一归到最后一类）。"""
        return [name for names in self.prompt_names_by_category().values() for name in names]

    def prompt_names_by_category(self) -> Dict[str, List[str]]:
        """``{分类: [模板名]}``，内置分类按固定顺序，自定义模板归入「我的模板」。

        为什么不让下拉框就是一个平铺列表：模板从 4 套涨到 18 套以后，平铺列表
        根本找不到想要的那一套。分类 + 每套一句话说明才能选得动。
        """
        grouped: Dict[str, List[str]] = {cat: [] for cat in CATEGORY_ORDER}
        grouped["我的模板"] = []
        for name in self.prompts:
            if name in PROMPT_PRESETS:
                grouped.setdefault(PROMPT_CATEGORY.get(name, "其他"), []).append(name)
            else:
                grouped["我的模板"].append(name)
        return {cat: names for cat, names in grouped.items() if names}

    def prompt_description(self, name: str) -> str:
        """模板的一句话说明：内置的来自预设表，自定义的给出长度提示。"""
        if name in PROMPT_DESCRIPTION:
            return PROMPT_DESCRIPTION[name]
        text = self.prompts.get(name, "")
        return f"自定义模板 · {len(text)} 字"

    def active_prompt_text(self) -> str:
        """当前选中模板的提示词正文。"""
        return self.prompts.get(self.active_prompt) or self.prompts[DEFAULT_PROMPT_NAME]

    def select_prompt(self, name: str) -> str:
        if name in self.prompts:
            self.active_prompt = name
        return self.active_prompt_text()

    def set_prompt_text(self, name: str, text: str) -> None:
        """保存模板正文（内置模板也允许改，随时可用 reset_prompt 还原）。"""
        if not name:
            return
        self.prompts[name] = text
        self.active_prompt = name
        self.save_configs()

    def add_prompt(self, name: str, text: str) -> bool:
        name = (name or "").strip()
        if not name:
            return False
        self.prompts[name] = text
        self.active_prompt = name
        self.save_configs()
        return True

    def remove_prompt(self, name: str) -> bool:
        """删除自定义模板；内置模板不允许删除。"""
        if name in PROMPT_PRESETS or name not in self.prompts:
            return False
        del self.prompts[name]
        if self.active_prompt == name:
            self.active_prompt = DEFAULT_PROMPT_NAME
        self.save_configs()
        return True

    @staticmethod
    def is_builtin_prompt(name: str) -> bool:
        return name in PROMPT_PRESETS

    def reset_prompt(self, name: str) -> Optional[str]:
        """把内置模板还原成内置原文；非内置模板返回 None。"""
        if name not in PROMPT_PRESETS:
            return None
        self.prompts[name] = PROMPT_PRESETS[name]
        self.save_configs()
        return self.prompts[name]

    def export_prompts(self, path: Path, names: Optional[List[str]] = None) -> int:
        """把模板导出成可分享的 JSON（模板名 -> 正文），返回导出条数。

        导出格式与 ``prompt_presets.py`` 里的 ``PROMPT_PRESETS`` 一致，
        所以导出的文件也能直接给别人导入。
        """
        selected = names if names is not None else list(self.prompts.keys())
        payload = {
            "version": 1,
            "exported_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "prompts": {name: self.prompts.get(name, "") for name in selected if name in self.prompts},
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return len(payload["prompts"])

    def import_prompts(self, path: Path, overwrite: bool = False) -> tuple:
        """从 JSON 文件导入模板，返回 ``(新增数, 跳过数)``。

        兼容两种格式：本项目导出的 ``{"prompts": {...}}``，以及裸的
        ``{"模板名": "正文"}`` 字典。
        """
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(raw, dict) and isinstance(raw.get("prompts"), dict):
            mapping = raw["prompts"]
        elif isinstance(raw, dict):
            mapping = raw
        else:
            raise ValueError("无法识别的模板文件格式（应为 JSON 对象）")

        added = skipped = 0
        for name, body in mapping.items():
            name = str(name).strip()
            body = str(body or "").strip()
            if not name or not body:
                skipped += 1
                continue
            if name in self.prompts and not overwrite:
                skipped += 1
                continue
            self.prompts[name] = body
            added += 1
        if added:
            self.save_configs()
        return added, skipped


# --------------------------------------------------------------------------- #
# 控件
# --------------------------------------------------------------------------- #


class ConfigCard(QFrame):
    """单个 AI 服务的配置卡片。"""

    def __init__(self, service_name: str, config: dict, parent=None) -> None:
        super().__init__(parent)
        self.service_name = service_name
        self.setObjectName("configCard")
        self.setFrameStyle(QFrame.StyledPanel)
        self.setup_ui(service_name, config)

    def setup_ui(self, service_name: str, config: dict) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        title = QLabel(service_name)
        title.setStyleSheet("font-size: 14px; font-weight: bold; margin-bottom: 6px;")
        layout.addWidget(title)

        key_layout = QHBoxLayout()
        key_layout.setSpacing(8)
        key_layout.addWidget(QLabel("API Key:"), 1)
        self.key_input = QLineEdit(config.get("api_key", ""))
        self.key_input.setEchoMode(QLineEdit.Password)
        key_layout.addWidget(self.key_input, 4)
        layout.addLayout(key_layout)

        model_layout = QHBoxLayout()
        model_layout.setSpacing(8)
        model_layout.addWidget(QLabel("Model:"), 1)

        self.model_combo = QComboBox()
        # 可编辑：模型名变化很快，必须允许直接手输
        self.model_combo.setEditable(True)
        self.model_combo.setInsertPolicy(QComboBox.NoInsert)
        self.model_combo.addItems(AIServiceConfig.SERVICES[service_name]["models"])
        current_model = config.get("model")
        if current_model:
            self.model_combo.setCurrentText(current_model)
        model_layout.addWidget(self.model_combo, 4)

        self.refresh_btn = QPushButton("刷新模型列表")
        self.refresh_btn.setFixedWidth(100)
        if AIServiceConfig.supports_model_list(service_name):
            self.refresh_btn.setToolTip(
                "用上面的 API Key 调该服务的 GET /models，拉取当前真实可用的模型。\n"
                "模型名变化很快（例如 Kimi 的 moonshot-v1-* 已于 2026-08-31 下线），\n"
                "建议填好 Key 后点一次；结果会保存到配置里。"
            )
        else:
            # 智谱 / 火山方舟这类没有 GET /models 的厂商，点了注定失败 —— 提前说清楚
            self.refresh_btn.setEnabled(False)
            self.refresh_btn.setText("不支持拉取")
            self.refresh_btn.setToolTip(
                "该服务没有提供 OpenAI 兼容的 GET /models 接口，无法自动拉取模型清单。\n"
                "请直接在 Model 框里手动输入模型名。"
            )
        self.refresh_btn.clicked.connect(self.refresh_models)
        model_layout.addWidget(self.refresh_btn)
        layout.addLayout(model_layout)

        notes = AIServiceConfig.notes(service_name)
        if notes:
            note_label = QLabel(notes)
            note_label.setWordWrap(True)
            note_label.setStyleSheet(f"color: {ModernStyle.SECONDARY_TEXT}; font-size: 11px;")
            layout.addWidget(note_label)

        save_btn = QPushButton("保存配置")
        save_btn.setFixedWidth(80)
        save_btn.clicked.connect(self.save_config)
        layout.addWidget(save_btn)

    # -- 模型列表刷新 --------------------------------------------------------

    def refresh_models(self) -> None:
        """按当前 API Key 拉取该服务的在线模型列表。"""
        if not AIServiceConfig.supports_model_list(self.service_name):
            QMessageBox.information(
                self,
                "该服务不支持",
                f"{self.service_name} 没有提供 OpenAI 兼容的 GET /models 接口，\n"
                "无法自动拉取模型清单。请直接在 Model 框里输入模型名。",
            )
            return

        api_key = self.key_input.text().strip()
        if not api_key:
            QMessageBox.warning(self, "警告", "请先填写该服务的 API Key，再刷新模型列表")
            return

        base_url = AIServiceConfig.base_url(self.service_name)
        self.refresh_btn.setEnabled(False)
        self.refresh_btn.setText("刷新中…")

        self._model_worker = ModelListWorker(api_key, base_url, self)
        self._model_worker.finished.connect(self.on_models_refreshed)
        self._model_worker.finished.connect(self._reset_refresh_button)
        self._model_worker.error.connect(self.on_models_error)
        self._model_worker.error.connect(self._reset_refresh_button)
        self._model_worker.start()

    def _reset_refresh_button(self, *_args) -> None:
        if not AIServiceConfig.supports_model_list(self.service_name):
            return
        self.refresh_btn.setEnabled(True)
        self.refresh_btn.setText("刷新模型列表")

    def on_models_refreshed(self, models: list) -> None:
        if not models:
            QMessageBox.information(self, "提示", "该服务返回的模型列表为空")
            return

        current = self.model_combo.currentText().strip()
        self.model_combo.clear()
        self.model_combo.addItems(models)
        if current:
            self.model_combo.setCurrentText(current)
        AIServiceConfig.set_models(self.service_name, models)

        main_window = self.window()
        if isinstance(main_window, MainWindow):
            main_window.save_service_models(self.service_name, models)

        QMessageBox.information(
            self,
            "成功",
            f"已获取 {len(models)} 个模型并保存。\n"
            "如果下拉框里没有你要的，也可以直接在 Model 框里手输。",
        )

    def on_models_error(self, message: str) -> None:
        QMessageBox.warning(
            self,
            "刷新失败",
            f"无法从该服务获取模型列表：\n{message}\n\n"
            "有些服务没有实现 GET /models，或需要额外中转配置。\n"
            "此时可以直接在 Model 框里手动输入模型名。",
        )

    def save_config(self) -> None:
        models = [self.model_combo.itemText(i) for i in range(self.model_combo.count())]
        config = {
            "api_key": self.key_input.text().strip(),
            "base_url": AIServiceConfig.base_url(self.service_name),
            "model": self.model_combo.currentText().strip(),
            "models": models,
            "supports_model_list": AIServiceConfig.supports_model_list(self.service_name),
        }
        main_window = self.window()
        if isinstance(main_window, MainWindow):
            main_window.save_service_config(self.service_name, config)


class AddServiceDialog(QDialog):
    """添加自定义（OpenAI 兼容）AI 服务。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setup_ui()

    def setup_ui(self) -> None:
        self.setWindowTitle("添加新服务")
        self.setMinimumWidth(400)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        name_layout = QVBoxLayout()
        name_layout.addWidget(QLabel("服务名称:"))
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("例如: OpenAI")
        name_layout.addWidget(self.name_input)
        layout.addLayout(name_layout)

        key_layout = QVBoxLayout()
        key_layout.addWidget(QLabel("API Key:"))
        self.key_input = QLineEdit()
        self.key_input.setEchoMode(QLineEdit.Password)
        self.key_input.setPlaceholderText("输入API密钥")
        key_layout.addWidget(self.key_input)
        layout.addLayout(key_layout)

        url_layout = QVBoxLayout()
        url_layout.addWidget(QLabel("API基础URL:"))
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("例如: https://api.openai.com/v1")
        url_layout.addWidget(self.url_input)
        layout.addLayout(url_layout)

        model_layout = QVBoxLayout()
        model_layout.addWidget(QLabel("模型名称:"))
        self.model_input = QLineEdit()
        self.model_input.setPlaceholderText("例如: gpt-4o-mini")
        model_layout.addWidget(self.model_input)
        layout.addLayout(model_layout)

        button_layout = QHBoxLayout()
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        save_btn = QPushButton("保存")
        save_btn.clicked.connect(self.accept)
        save_btn.setDefault(True)
        button_layout.addStretch()
        button_layout.addWidget(cancel_btn)
        button_layout.addWidget(save_btn)
        layout.addLayout(button_layout)

    def get_service_data(self) -> dict:
        return {
            "name": self.name_input.text().strip(),
            "api_key": self.key_input.text().strip(),
            "base_url": self.url_input.text().strip(),
            "model": self.model_input.text().strip(),
        }


class ModelListWorker(QThread):
    """后台拉取某服务的可用模型列表，避免网络请求卡住界面。"""

    finished = Signal(list)
    error = Signal(str)

    def __init__(self, api_key: str, base_url: str, parent=None) -> None:
        super().__init__(parent)
        self.api_key = api_key
        self.base_url = base_url

    def run(self) -> None:
        try:
            self.finished.emit(fetch_available_models(self.api_key, self.base_url))
        except Exception as exc:  # noqa: BLE001 - 线程内必须兜底，否则静默死掉
            logger.warning(f"刷新模型列表失败：{exc}")
            self.error.emit(str(exc))


# --------------------------------------------------------------------------- #
# 时间范围预设
# --------------------------------------------------------------------------- #
#
# 原版把小时数限制在 0..23，连「最近 24 小时」都选不了。这里改成两条通道：
#
#   * **相对时间**：最近 N 分钟 / 小时 / 天的快捷预设 + 自定义小时分钟数；
#   * **绝对时间**：直接给起止时刻（也可只用起始时刻，结束默认「到现在」），
#     用来取「昨天 08:00 到 18:00」这种跨天区间。
#
# 两种模式最终都归一到 ``(start_datetime, end_datetime|None)`` 交给抓取层。

#: 快捷预设：名称 -> 分钟数（相对）、或 "today"/"yesterday"/"week"（绝对起点）、
#: 或 None（自定义）
TIME_PRESETS: Dict[str, Optional[object]] = {
    "最近 30 分钟": 30,
    "最近 1 小时": 60,
    "最近 3 小时": 180,
    "最近 6 小时": 360,
    "最近 12 小时": 720,
    "最近 24 小时": 1440,
    "最近 2 天": 2880,
    "最近 3 天": 4320,
    "最近 7 天": 10080,
    "最近 14 天": 20160,
    "最近 30 天": 43200,
    "今天 00:00 起": "today",
    "昨天 00:00 起": "yesterday",
    "本周一 00:00 起": "week",
    "自定义（相对时间）": None,
    "自定义（起止时间）": "range",
}

DEFAULT_TIME_PRESET = "最近 1 小时"
MAX_TIME_HOURS = 720          # 相对模式的上限：30 天

#: 抓取阶段的墙钟预算（秒）。大群 + 7 天跨度可能真的要几分钟。
DEFAULT_COLLECT_BUDGET = 420.0


def _preset_start(name: str, now: dt.datetime) -> Optional[dt.datetime]:
    """把「今天 00:00 起」这类日历预设换算成具体时刻。"""
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if name == "today":
        return today
    if name == "yesterday":
        return today - dt.timedelta(days=1)
    if name == "week":
        return today - dt.timedelta(days=today.weekday())
    return None


# --------------------------------------------------------------------------- #
# 主窗口
# --------------------------------------------------------------------------- #


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.ai_config = AIConfig()
        self.worker: SummaryWorker | None = None
        #: 抓取作业的取消信号。GUI 线程置位，抓取线程在检查点抛 JobCancelled。
        self.cancel_token = CancelToken()
        #: 抓取期间要禁用的控件（窗口本身保持可用，否则快捷键收不到按键）
        self._busy_widgets: List[QWidget] = []
        self._abort_shortcuts: List[QShortcut] = []
        self.setup_ui()
        ModernStyle.setup_widget(self)
        self.setWindowIcon(_app_icon())
        self._setup_abort_shortcuts()
        self._set_busy(False)

    # -- 界面骨架 ------------------------------------------------------------

    def setup_ui(self) -> None:
        self.setWindowTitle("微信群聊总结工具（微信 4.x / wxauto4 版）")
        self.setMinimumSize(600, 500)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        tab_widget = QTabWidget()
        main_layout.addWidget(tab_widget)
        tab_widget.addTab(self.create_main_tab(), "群聊总结")
        tab_widget.addTab(self.create_ai_config_tab(), "AI服务配置")
        tab_widget.addTab(self.create_prompt_tab(), "提示词配置")

    def create_main_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        input_container = QWidget()
        input_layout = QHBoxLayout(input_container)
        input_layout.setContentsMargins(0, 0, 0, 0)
        input_layout.setSpacing(12)

        # 群聊名称
        group_widget = QWidget()
        group_layout = QVBoxLayout(group_widget)
        group_layout.setContentsMargins(0, 0, 0, 0)
        group_layout.setSpacing(5)
        group_layout.addWidget(QLabel("群聊名称"))
        self.group_name_input = QLineEdit()
        self.group_name_input.setPlaceholderText("例如: 文件传输助手")
        group_layout.addWidget(self.group_name_input)
        input_layout.addWidget(group_widget, 4)

        # 时间范围
        time_widget = QWidget()
        time_layout = QVBoxLayout(time_widget)
        time_layout.setContentsMargins(0, 0, 0, 0)
        time_layout.setSpacing(5)
        time_layout.addWidget(QLabel("获取时间范围"))

        time_input_widget = QWidget()
        time_input_layout = QHBoxLayout(time_input_widget)
        time_input_layout.setContentsMargins(0, 0, 0, 0)
        time_input_layout.setSpacing(5)

        # 快捷预设：解决「连 24 小时都选不了」的问题，并补上日历语义
        self.time_preset_combo = QComboBox()
        self.time_preset_combo.addItems(list(TIME_PRESETS.keys()))
        self.time_preset_combo.setCurrentText(DEFAULT_TIME_PRESET)
        self.time_preset_combo.setToolTip(
            "快捷选择时间跨度。\n"
            "「最近 N」是相对现在往前推；「今天/昨天/本周一 00:00 起」是日历语义；\n"
            "选「自定义（起止时间）」可以精确指定某天某时段。"
        )
        time_input_layout.addWidget(self.time_preset_combo, 1)

        # 相对模式：小时 + 分钟
        self.hours_spin = QSpinBox()
        self.hours_spin.setRange(0, MAX_TIME_HOURS)     # 最长 30 天（原版只能 0~23）
        self.hours_spin.setValue(1)
        self.hours_spin.setSuffix(" 小时")

        self.minutes_spin = QSpinBox()
        self.minutes_spin.setRange(0, 59)
        self.minutes_spin.setValue(0)
        self.minutes_spin.setSuffix(" 分钟")

        time_input_layout.addWidget(self.hours_spin)
        time_input_layout.addWidget(self.minutes_spin)
        time_layout.addWidget(time_input_widget)

        # 绝对模式：起始 / 结束时刻（结束留空表示「到现在」）
        self.range_widget = QWidget()
        range_layout = QHBoxLayout(self.range_widget)
        range_layout.setContentsMargins(0, 0, 0, 0)
        range_layout.setSpacing(5)

        now = dt.datetime.now()
        self.start_edit = QDateTimeEdit(QDateTime(now - dt.timedelta(days=1)))
        self.start_edit.setCalendarPopup(True)
        self.start_edit.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.end_edit = QDateTimeEdit(QDateTime(now))
        self.end_edit.setCalendarPopup(True)
        self.end_edit.setDisplayFormat("yyyy-MM-dd HH:mm")

        self.to_now_check = QCheckBox("结束到现在")
        self.to_now_check.setChecked(True)
        self.to_now_check.setToolTip("勾选表示结束时间就是「现在」；取消可以指定一个过去的结束时刻。")
        self.to_now_check.toggled.connect(lambda on: self.end_edit.setEnabled(not on))

        range_layout.addWidget(QLabel("从"))
        range_layout.addWidget(self.start_edit, 1)
        range_layout.addWidget(self.to_now_check)
        range_layout.addWidget(self.end_edit, 1)
        self.range_widget.setVisible(False)
        time_layout.addWidget(self.range_widget)

        input_layout.addWidget(time_widget, 4)

        # 双向联动：选预设 -> 填数字/切换模式；改数字 -> 切回「自定义」
        self._syncing_time = False
        self.time_preset_combo.currentTextChanged.connect(self.on_time_preset_changed)
        self.hours_spin.valueChanged.connect(self.on_time_spin_changed)
        self.minutes_spin.valueChanged.connect(self.on_time_spin_changed)
        self.on_time_preset_changed(DEFAULT_TIME_PRESET)

        # AI 服务
        service_widget = QWidget()
        service_layout = QVBoxLayout(service_widget)
        service_layout.setContentsMargins(0, 0, 0, 0)
        service_layout.setSpacing(5)
        service_layout.addWidget(QLabel("AI服务"))
        self.service_combo = QComboBox()
        self.update_service_combo()
        if self.ai_config.last_service:
            index = self.service_combo.findText(self.ai_config.last_service)
            if index >= 0:
                self.service_combo.setCurrentIndex(index)
        service_layout.addWidget(self.service_combo)
        input_layout.addWidget(service_widget, 2)

        layout.addWidget(input_container)

        # 总结模板（系统提示词）—— 不同群聊场景选不同模板
        prompt_row = QWidget()
        prompt_row_layout = QHBoxLayout(prompt_row)
        prompt_row_layout.setContentsMargins(0, 0, 0, 0)
        prompt_row_layout.setSpacing(8)
        prompt_row_layout.addWidget(QLabel("总结模板"))
        self.main_prompt_combo = QComboBox()
        self._fill_prompt_combo(self.main_prompt_combo, self.ai_config.active_prompt)
        prompt_row_layout.addWidget(self.main_prompt_combo, 1)
        layout.addWidget(prompt_row)
        # 连接放在 setCurrentText 之后，避免初始化时触发一次多余回调
        self.main_prompt_combo.currentTextChanged.connect(self.on_main_prompt_selected)

        get_msg_btn = QPushButton("获取群聊消息")
        get_msg_btn.setObjectName("mainButton")
        get_msg_btn.clicked.connect(self.get_messages)
        layout.addWidget(get_msg_btn)

        layout.addWidget(QLabel("消息总结预览"))

        self.summary_edit = QTextEdit()
        layout.addWidget(self.summary_edit)

        bottom_container = QWidget()
        bottom_layout = QHBoxLayout(bottom_container)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.setSpacing(8)

        self.status_label = QLabel()
        self.status_label.setStyleSheet(f"color: {ModernStyle.SECONDARY_TEXT};")
        self.status_label.setWordWrap(True)
        bottom_layout.addWidget(self.status_label, 1)

        save_btn = QPushButton("保存总结")
        save_btn.setFixedWidth(80)
        save_btn.clicked.connect(self.save_summary)

        send_btn = QPushButton("发送到群聊")
        send_btn.setFixedWidth(80)
        send_btn.clicked.connect(self.send_to_group)

        bottom_layout.addWidget(save_btn)
        bottom_layout.addWidget(send_btn)

        # 中止按钮：抓取可能持续十几分钟，必须有个显眼的停机开关。
        # 它和 Esc / 空格 走同一条路径（都调用 abort_job）。
        self.abort_btn = QPushButton("中止")
        self.abort_btn.setFixedWidth(80)
        self.abort_btn.setToolTip("中止当前抓取（等同于按 Esc 或空格）。\n抓取最多再等一次读取（约 10 秒）就会停下。")
        self.abort_btn.clicked.connect(self.abort_job)
        bottom_layout.addWidget(self.abort_btn)

        layout.addWidget(bottom_container)

        # 抓取期间要禁用的控件（窗口保持可用，否则快捷键收不到按键）
        self._busy_widgets = [
            input_container,
            prompt_row,
            get_msg_btn,
            save_btn,
            send_btn,
        ]

        # 输出格式：微信不渲染 Markdown，所以默认把发送内容降级成纯文本。
        # 预览区显示什么、发送什么，二者必须一致，否则用户会以为发出去的是带格式的。
        format_row = QWidget()
        format_layout = QHBoxLayout(format_row)
        format_layout.setContentsMargins(0, 0, 0, 0)
        format_layout.setSpacing(8)

        self.plain_text_check = QCheckBox("按微信纯文本排版（去掉 Markdown 符号）")
        self.plain_text_check.setChecked(True)
        self.plain_text_check.setToolTip(
            "微信聊天窗口不渲染 Markdown：直接发送会看到一堆井号、星号和竖线。\n"
            "勾选后会在显示/发送前做一次确定性转换（标题→【标题】、列表→·、表格→条目）。\n"
            "取消勾选则保留模型的原始 Markdown 输出。"
        )
        self.plain_text_check.toggled.connect(self.on_plain_text_toggled)
        format_layout.addWidget(self.plain_text_check)

        self.md_hint_label = QLabel()
        self.md_hint_label.setStyleSheet(f"color: {ModernStyle.SECONDARY_TEXT}; font-size: 11px;")
        format_layout.addWidget(self.md_hint_label, 1)
        layout.addWidget(format_row)

        about_label = QLabel(
            '<p><a href="https://github.com/Vita0519/wechat_summary">'
            "界面与思路源自 Vita0519/wechat_summary</a></p>",
            self,
        )
        about_label.setAlignment(Qt.AlignBottom | Qt.AlignLeft)
        about_label.setOpenExternalLinks(True)
        layout.addWidget(about_label)

        return tab

    def create_ai_config_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(16, 16, 16, 16)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)

        scroll_content = QWidget()
        self.scroll_layout = QVBoxLayout(scroll_content)
        self.scroll_layout.setSpacing(12)
        self.scroll_layout.setContentsMargins(0, 0, 0, 0)

        for service_name in AIServiceConfig.SERVICES:
            config = self.ai_config.get_config(service_name) or {}
            self.scroll_layout.addWidget(ConfigCard(service_name, config))

        self.scroll_layout.addStretch()
        scroll.setWidget(scroll_content)
        layout.addWidget(scroll)

        add_service_btn = QPushButton("添加新服务")
        add_service_btn.clicked.connect(self.add_custom_service)
        layout.addWidget(add_service_btn)

        return tab

    def create_prompt_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        help_label = QLabel(
            f"系统提示词模板（内置 {len(PROMPT_PRESETS)} 套，按场景分类）："
            "不同群聊场景选不同模板，效果差别很大。\n"
            "· 选中模板后可直接修改正文，点「保存修改」生效；\n"
            "· 「另存为新模板」把当前正文存成自己的模板；\n"
            "· 内置模板可用「恢复内置原文」还原，但不能删除；\n"
            "· 「导出 / 导入」可以把自己的模板存成 JSON 分享或换机迁移。"
        )
        help_label.setWordWrap(True)
        help_label.setStyleSheet(f"color: {ModernStyle.SECONDARY_TEXT};")
        layout.addWidget(help_label)

        select_row = QWidget()
        select_layout = QHBoxLayout(select_row)
        select_layout.setContentsMargins(0, 0, 0, 0)
        select_layout.setSpacing(8)
        select_layout.addWidget(QLabel("模板"))

        self.prompt_combo = QComboBox()
        self._fill_prompt_combo(self.prompt_combo, self.ai_config.active_prompt)

        self.prompt_kind_label = QLabel()
        self.prompt_kind_label.setStyleSheet(f"color: {ModernStyle.SECONDARY_TEXT};")

        select_layout.addWidget(self.prompt_combo, 1)
        select_layout.addWidget(self.prompt_kind_label)
        layout.addWidget(select_row)

        self.prompt_desc_label = QLabel()
        self.prompt_desc_label.setWordWrap(True)
        self.prompt_desc_label.setStyleSheet(
            f"color: {ModernStyle.SECONDARY_TEXT}; font-size: 11px;"
        )
        layout.addWidget(self.prompt_desc_label)

        self.prompt_edit = QTextEdit()
        self.prompt_edit.setPlaceholderText("在此编辑该模板的系统提示词...")
        self.prompt_edit.setText(self.ai_config.active_prompt_text())
        layout.addWidget(self.prompt_edit)

        button_container = QWidget()
        button_layout = QHBoxLayout(button_container)
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.setSpacing(8)

        reset_btn = QPushButton("恢复内置原文")
        reset_btn.clicked.connect(self.reset_prompt)

        new_btn = QPushButton("另存为新模板")
        new_btn.clicked.connect(self.save_prompt_as)

        delete_btn = QPushButton("删除模板")
        delete_btn.clicked.connect(self.delete_prompt)

        export_btn = QPushButton("导出模板")
        export_btn.clicked.connect(self.export_prompts)

        import_btn = QPushButton("导入模板")
        import_btn.clicked.connect(self.import_prompts)

        save_prompt_btn = QPushButton("保存修改")
        save_prompt_btn.clicked.connect(self.save_prompt)

        button_layout.addWidget(reset_btn)
        button_layout.addWidget(new_btn)
        button_layout.addWidget(delete_btn)
        button_layout.addWidget(export_btn)
        button_layout.addWidget(import_btn)
        button_layout.addStretch()
        button_layout.addWidget(save_prompt_btn)
        layout.addWidget(button_container)

        self._update_prompt_kind_label()
        # 连接放在初始化之后，避免构造期间触发回调
        self.prompt_combo.currentTextChanged.connect(self.on_prompt_selected)

        return tab

    def _fill_prompt_combo(self, combo: QComboBox, select: str) -> None:
        """按分类填充模板下拉框。

        用不可选的分类标题行做视觉分组 —— QComboBox 没有原生分组，
        加一个禁用的分隔项是最省事且不会误选的做法。
        """
        combo.blockSignals(True)
        combo.clear()
        for category, names in self.ai_config.prompt_names_by_category().items():
            combo.addItem(f"—— {category} ——")
            index = combo.count() - 1
            combo.model().item(index).setEnabled(False)
            for name in names:
                combo.addItem(name)
        if select:
            combo.setCurrentText(select)
        combo.blockSignals(False)

    def _update_prompt_kind_label(self) -> None:
        """标注当前模板是内置还是自定义。"""
        name = self.prompt_combo.currentText()
        if name.startswith("——"):
            return
        kind = "内置模板" if self.ai_config.is_builtin_prompt(name) else "自定义模板"
        self.prompt_kind_label.setText(kind)
        if hasattr(self, "prompt_desc_label"):
            self.prompt_desc_label.setText(self.ai_config.prompt_description(name))

    def _refresh_prompt_combos(self, select: str = "") -> None:
        """模板增删后重建两个下拉框，并让它们选中一致。"""
        target = select or self.ai_config.active_prompt
        names = self.ai_config.prompt_names()
        if target not in names:
            target = DEFAULT_PROMPT_NAME

        for combo in (getattr(self, "main_prompt_combo", None), getattr(self, "prompt_combo", None)):
            if combo is None:
                continue
            self._fill_prompt_combo(combo, target)

        self.ai_config.active_prompt = target
        self.prompt_edit.setText(self.ai_config.active_prompt_text())
        self._update_prompt_kind_label()

    def export_prompts(self) -> None:
        name = self.prompt_combo.currentText()
        if name.startswith("——"):
            return
        choice = QMessageBox.question(
            self,
            "导出模板",
            f"导出「{name}」这一个模板？\n\n"
            "选「No」则导出全部模板（含内置与自定义）。",
            QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
            QMessageBox.Yes,
        )
        if choice == QMessageBox.Cancel:
            return

        selected = [name] if choice == QMessageBox.Yes else None
        path, _ = QFileDialog.getSaveFileName(
            self, "导出到文件", f"wechat_prompts.json", "JSON 文件 (*.json)"
        )
        if not path:
            return
        try:
            count = self.ai_config.export_prompts(Path(path), selected)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "错误", f"导出失败：{exc}")
            return
        QMessageBox.information(self, "成功", f"已导出 {count} 个模板到：\n{path}")

    def import_prompts(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择模板文件", "", "JSON 文件 (*.json);;所有文件 (*)"
        )
        if not path:
            return
        overwrite = (
            QMessageBox.question(
                self,
                "同名模板",
                "遇到同名模板时是否覆盖？\n\n「Yes」覆盖，「No」保留原有模板并跳过。",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            == QMessageBox.Yes
        )
        try:
            added, skipped = self.ai_config.import_prompts(Path(path), overwrite=overwrite)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "错误", f"导入失败：{exc}")
            return
        self._refresh_prompt_combos()
        QMessageBox.information(
            self, "导入完成", f"新增/更新 {added} 个模板，跳过 {skipped} 个。"
        )

    # -- 中止机制 ------------------------------------------------------------

    def _setup_abort_shortcuts(self) -> None:
        """注册 Esc / 空格两个中止快捷键。

        两个关键取舍：

        1. **只在抓取期间启用**。空格是「中止」很反直觉，常驻的话会吃掉所有
           输入框里的空格；所以空闲时把它们关掉。
        2. **不做全局热键**。全局 ``RegisterHotKey`` 会让空格在整个系统里失效
           （任何程序都没法再打空格），Esc 也会抢别的程序。代价是：抓取时微信会被
           切到前台，用户需要先点回本窗口再按 Esc —— 界面上写明了这一点。
        """
        for key in (Qt.Key_Escape, Qt.Key_Space):
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setContext(Qt.WindowShortcut)
            shortcut.activated.connect(self.abort_job)
            shortcut.setEnabled(False)
            self._abort_shortcuts.append(shortcut)

    def _set_busy(self, busy: bool) -> None:
        """切换「正在抓取」状态。

        刻意**不**调用 ``self.setEnabled(False)``：窗口一旦禁用，Qt 就不会再把
        按键送给它的快捷键，Esc/空格 会彻底失效（这正是最容易踩的坑）。
        改成只禁用会引发冲突的控件。
        """
        self._busy = busy
        for widget in self._busy_widgets:
            widget.setEnabled(not busy)
        for shortcut in self._abort_shortcuts:
            shortcut.setEnabled(busy)
        if hasattr(self, "abort_btn"):
            self.abort_btn.setEnabled(busy)

    def abort_job(self) -> None:
        """中止当前抓取（Esc / 空格 / 「中止」按钮都走这里）。"""
        if not getattr(self, "_busy", False):
            return
        first = self.cancel_token.cancel("用户中止")
        if first:
            logger.info("用户请求中止本次抓取")
        self.status_label.setText("正在中止…（当前这一次读取结束后停止，最多约 10 秒）")
        if hasattr(self, "abort_btn"):
            self.abort_btn.setEnabled(False)

    def on_summary_cancelled(self, reason: str) -> None:
        """抓取被用户中止：恢复界面，不弹错误框。"""
        self._set_busy(False)
        self.status_label.setText(f"已中止（{reason}）。已抓到的内容不会保留。")
        QTimer.singleShot(4000, lambda: self.status_label.setText(""))

    # -- 交互逻辑 ------------------------------------------------------------

    def update_service_combo(self) -> None:
        self.service_combo.clear()
        self.service_combo.addItems(AIServiceConfig.SERVICES.keys())

    def save_service_models(self, service_name: str, models) -> None:
        """服务卡片刷新到新模型清单后，同步写回配置。"""
        self.ai_config.save_service_models(service_name, models)

    # -- 时间范围：预设与自定义双向联动 --------------------------------------

    def on_time_preset_changed(self, name: str) -> None:
        """选了快捷预设 -> 填数字 / 切换相对-绝对模式。"""
        value = TIME_PRESETS.get(name)

        if value == "range":
            self._set_time_mode("absolute")
            return

        start = _preset_start(value, dt.datetime.now()) if isinstance(value, str) else None
        if start is not None:
            self._set_time_mode("absolute")
            self._syncing_time = True
            try:
                self.start_edit.setDateTime(QDateTime(start))
                self.to_now_check.setChecked(True)
            finally:
                self._syncing_time = False
            return

        # 相对模式（含「自定义（相对时间）」：值为 None 时保留当前数值）
        self._set_time_mode("relative")
        if value is None:
            return
        minutes = int(value)  # type: ignore[arg-type]
        self._syncing_time = True
        try:
            self.hours_spin.setValue(minutes // 60)
            self.minutes_spin.setValue(minutes % 60)
        finally:
            self._syncing_time = False

    def _set_time_mode(self, mode: str) -> None:
        """切换「相对时间 / 起止时间」两种输入方式。"""
        self._time_mode = mode
        absolute = mode == "absolute"
        self.range_widget.setVisible(absolute)
        self.hours_spin.setVisible(not absolute)
        self.minutes_spin.setVisible(not absolute)

    def on_time_spin_changed(self, _value: int = 0) -> None:
        """手动改数字 -> 匹配回某个预设，匹配不上就切到「自定义（相对时间）」。"""
        if getattr(self, "_syncing_time", False):
            return
        total = self.hours_spin.value() * 60 + self.minutes_spin.value()
        matched = next(
            (k for k, v in TIME_PRESETS.items() if isinstance(v, int) and v == total),
            "自定义（相对时间）",
        )
        self.time_preset_combo.blockSignals(True)
        try:
            self.time_preset_combo.setCurrentText(matched)
        finally:
            self.time_preset_combo.blockSignals(False)

    def current_time_minutes(self) -> int:
        """当前选择的时间跨度（分钟），仅相对模式有意义。"""
        return self.hours_spin.value() * 60 + self.minutes_spin.value()

    def current_time_range(self) -> tuple:
        """把界面上的选择归一成 ``(start_datetime, end_datetime|None, hours|None)``。

        * 相对模式 -> ``(None, None, hours)``，交给底层按「现在往前推」计算；
        * 绝对模式 -> ``(start, end|None, None)``，end 为 None 表示「到现在」。
        """
        if getattr(self, "_time_mode", "relative") == "absolute":
            start = self.start_edit.dateTime().toPython()
            if isinstance(start, dt.date) and not isinstance(start, dt.datetime):
                start = dt.datetime.combine(start, dt.time())
            end = None if self.to_now_check.isChecked() else self.end_edit.dateTime().toPython()
            return start, end, None

        minutes = self.current_time_minutes()
        if minutes <= 0:
            minutes = 60
        return None, None, minutes / 60.0

    def time_range_summary(self) -> str:
        """给用户看的一句话时间范围说明。"""
        start, end, hours = self.current_time_range()
        if start is not None:
            head = f"{start:%Y-%m-%d %H:%M}"
            tail = f"{end:%Y-%m-%d %H:%M}" if end else "现在"
            return f"{head} ~ {tail}"
        return f"最近 {hours:g} 小时"

    def save_service_config(self, service_name: str, config: dict) -> None:
        self.ai_config.add_config(service_name, config)
        current_service = self.service_combo.currentText()
        self.update_service_combo()
        self.service_combo.setCurrentText(current_service)
        self._flash_status(f"{service_name} 配置已保存")

    def _flash_status(self, message: str, timeout_ms: int = 2000) -> None:
        """在状态栏显示一条会自动消失的提示。"""
        if hasattr(self, "status_label") and self.status_label:
            self.status_label.setText(message)
            QTimer.singleShot(timeout_ms, lambda: self.status_label.setText(""))

    def add_custom_service(self) -> None:
        dialog = AddServiceDialog(self)
        if not dialog.exec():
            return

        data = dialog.get_service_data()
        if not all([data["name"], data["api_key"], data["base_url"], data["model"]]):
            QMessageBox.warning(self, "警告", "所有字段都必须填写")
            return

        self.ai_config.add_config(
            data["name"],
            {
                "api_key": data["api_key"],
                "base_url": data["base_url"],
                "model": data["model"],
            },
        )
        self.update_service_combo()
        self.update_config_cards()

        index = self.service_combo.findText(data["name"])
        if index >= 0:
            self.service_combo.setCurrentIndex(index)
        self._flash_status(f"已添加新服务: {data['name']}")

    def update_config_cards(self) -> None:
        while self.scroll_layout.count():
            item = self.scroll_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        for service_name in AIServiceConfig.SERVICES:
            config = self.ai_config.get_config(service_name) or {}
            self.scroll_layout.addWidget(ConfigCard(service_name, config))
        self.scroll_layout.addStretch()

    # -- 系统提示词模板 ------------------------------------------------------

    def on_main_prompt_selected(self, name: str) -> None:
        """在主界面切换总结模板。"""
        if not name or name.startswith("——"):
            return
        self.ai_config.select_prompt(name)
        self.ai_config.save_configs()

        if hasattr(self, "prompt_combo"):
            self.prompt_combo.blockSignals(True)
            self.prompt_combo.setCurrentText(name)
            self.prompt_combo.blockSignals(False)
        if hasattr(self, "prompt_edit"):
            self.prompt_edit.setText(self.ai_config.active_prompt_text())
        if hasattr(self, "prompt_kind_label"):
            self._update_prompt_kind_label()
        self._flash_status(f"已切换模板：{name} — {self.ai_config.prompt_description(name)}")

    def on_prompt_selected(self, name: str) -> None:
        """在提示词配置页切换模板。"""
        if not name or name.startswith("——"):
            return
        self.ai_config.select_prompt(name)
        self.prompt_edit.setText(self.ai_config.active_prompt_text())
        self._update_prompt_kind_label()

        if hasattr(self, "main_prompt_combo"):
            self.main_prompt_combo.blockSignals(True)
            self.main_prompt_combo.setCurrentText(name)
            self.main_prompt_combo.blockSignals(False)
        self.ai_config.save_configs()

    def save_prompt(self) -> None:
        """保存当前模板的正文。"""
        name = self.prompt_combo.currentText()
        text = self.prompt_edit.toPlainText()
        if not name:
            return
        if not text.strip():
            QMessageBox.warning(self, "警告", "提示词内容不能为空")
            return

        self.ai_config.set_prompt_text(name, text)
        QMessageBox.information(self, "成功", f"模板「{name}」已保存")

    def save_prompt_as(self) -> None:
        """把编辑区内容另存为一个新模板。"""
        text = self.prompt_edit.toPlainText()
        if not text.strip():
            QMessageBox.warning(self, "警告", "提示词内容不能为空")
            return

        current = self.prompt_combo.currentText()
        name, ok = QInputDialog.getText(
            self, "另存为新模板", "模板名称：", text=f"{current} 副本"
        )
        if not ok:
            return
        name = (name or "").strip()
        if not name:
            QMessageBox.warning(self, "警告", "模板名称不能为空")
            return

        if name in self.ai_config.prompts:
            confirm = QMessageBox.question(
                self,
                "确认覆盖",
                f"模板「{name}」已存在，是否用当前正文覆盖它？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if confirm != QMessageBox.Yes:
                return

        self.ai_config.add_prompt(name, text)
        self._refresh_prompt_combos(name)
        self._flash_status(f"已保存模板：{name}")

    def delete_prompt(self) -> None:
        """删除自定义模板（内置模板不允许删除）。"""
        name = self.prompt_combo.currentText()
        if self.ai_config.is_builtin_prompt(name):
            QMessageBox.information(
                self,
                "提示",
                f"「{name}」是内置模板，不能删除。\n"
                "如需把它改回原始内容，请点「恢复内置原文」。",
            )
            return

        confirm = QMessageBox.question(
            self,
            "确认删除",
            f"确定删除自定义模板「{name}」？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return

        if self.ai_config.remove_prompt(name):
            self._refresh_prompt_combos(self.ai_config.active_prompt)
            self._flash_status(f"已删除模板：{name}")

    def reset_prompt(self) -> None:
        """把内置模板还原成内置原文。"""
        name = self.prompt_combo.currentText()
        text = self.ai_config.reset_prompt(name)
        if text is None:
            QMessageBox.information(
                self,
                "提示",
                f"「{name}」不是内置模板，没有“内置原文”可以恢复。\n"
                "如需删除它，请用「删除模板」。",
            )
            return
        self.prompt_edit.setText(text)
        self._flash_status(f"「{name}」已恢复内置原文")

    def get_messages(self) -> None:
        group_name = self.group_name_input.text().strip()
        service_name = self.service_combo.currentText()

        if not group_name:
            QMessageBox.warning(self, "警告", "请输入群聊名称")
            return

        start_time, end_time, hours = self.current_time_range()
        if start_time is not None and end_time is not None and end_time <= start_time:
            QMessageBox.warning(self, "警告", "结束时间必须晚于开始时间")
            return
        if start_time is None and not hours:
            QMessageBox.warning(
                self,
                "警告",
                "时间范围不能为 0。\n请用「获取时间范围」的快捷预设，或手动填小时/分钟。",
            )
            return

        service_config = self.ai_config.get_config(service_name)
        if not service_config or not service_config.get("api_key"):
            QMessageBox.warning(self, "警告", f"请先配置 {service_name} 的API密钥")
            return

        self.ai_config.last_service = service_name
        self.ai_config.save_configs()

        # 用当前选中的模板作为系统提示词；若提示词页有未保存的改动，则以编辑区为准
        prompt_name = self.ai_config.active_prompt
        prompt_text = self.ai_config.active_prompt_text()
        editor = getattr(self, "prompt_edit", None)
        combo = getattr(self, "prompt_combo", None)
        if (
            editor is not None
            and combo is not None
            and combo.currentText() == prompt_name
            and editor.toPlainText().strip()
        ):
            prompt_text = editor.toPlainText()

        self.status_label.setText(
            f"正在用模板「{prompt_name}」抓取 {self.time_range_summary()} 的消息…"
            "（按 Esc 或空格可中止）"
        )
        self.cancel_token.reset()       # 复用同一个 token，先清掉上一次的信号
        self._set_busy(True)

        self.worker = SummaryWorker(
            group_name,
            service_config,
            prompt_text,
            start_time=start_time,
            hours=hours,
            end_time=end_time,
            cancel_token=self.cancel_token,
        )
        self.worker.finished.connect(self.on_summary_finished)
        self.worker.error.connect(self.on_summary_error)
        self.worker.cancelled.connect(self.on_summary_cancelled)
        self.worker.progress.connect(self.on_summary_progress)
        self.worker.start()

    def on_summary_progress(self, message: str) -> None:
        """抓取进度：只更新状态栏，不打断用户。"""
        # 已经在中止流程里时，不要把「正在中止…」覆盖掉
        if getattr(self, "_busy", False) and self.cancel_token.cancelled:
            return
        self.status_label.setText(message)

    def on_summary_finished(self, summary: str) -> None:
        # 生成后先按当前输出格式过一遍，让「预览 = 将要发出去的内容」
        self._set_summary_text(summary)
        self.status_label.setText("")
        self._set_busy(False)

    def on_summary_error(self, error: str) -> None:
        self.status_label.setText("")
        self._set_busy(False)
        QMessageBox.critical(self, "错误", f"获取消息失败: {error}")

    # -- 输出格式：Markdown -> 微信纯文本 ------------------------------------

    def on_plain_text_toggled(self, checked: bool) -> None:
        """切换输出格式时，把预览区的内容同步转换，避免「看到的」和「发出去的」不一致。"""
        current = self.summary_edit.toPlainText()
        if not current.strip():
            self._update_md_hint(current)
            return
        self._set_summary_text(current)

    def _set_summary_text(self, text: str) -> None:
        if self.plain_text_check.isChecked():
            text = clean_for_wechat(text)
        self.summary_edit.setPlainText(text)
        self._update_md_hint(text)

    def _update_md_hint(self, text: str) -> None:
        """在格式行右侧提示是否还残留 Markdown 标记。"""
        if not text.strip():
            self.md_hint_label.setText("")
            return
        hits = analyze_markdown(text)
        if self.plain_text_check.isChecked():
            self.md_hint_label.setText(
                "已转为微信排版" if not hits else f"仍残留：{'、'.join(hits)}"
            )
        else:
            self.md_hint_label.setText(
                f"原始 Markdown（微信会原样显示 {'、'.join(hits)}）" if hits else "原始文本"
            )

    def send_to_group(self) -> None:
        group_name = self.group_name_input.text().strip()
        summary = self.summary_edit.toPlainText()

        if not group_name:
            QMessageBox.warning(self, "警告", "请输入群聊名称")
            return
        if not summary:
            QMessageBox.warning(self, "警告", "没有可发送的内容")
            return

        # 发送前再确认一次：微信不渲染 Markdown，用户很容易忽略这一点
        preview = summary if len(summary) <= 600 else summary[:600] + "\n…（已截断预览）"
        confirm = QMessageBox.question(
            self,
            "确认发送",
            f"确认把下面的内容发送到「{group_name}」？\n\n"
            f"———— 预览 ————\n{preview}\n———— 预览结束 ————\n\n"
            + ("内容已按微信纯文本排版。" if self.plain_text_check.isChecked()
               else "⚠️ 当前保留 Markdown，微信会原样显示井号/星号/竖线。"),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return

        button = self.sender()
        original_text = button.text() if isinstance(button, QPushButton) else ""
        try:
            if isinstance(button, QPushButton):
                button.setText("发送中...")
                button.setEnabled(False)
            self.status_label.setText("正在发送到群聊，请稍候...")
            QApplication.processEvents()

            # 预览区已经转换过了；这里按用户的选择再兜一次，保证幂等且不重复转换
            success = send_summary(
                group_name,
                summary,
                convert_markdown=self.plain_text_check.isChecked(),
            )

            if success:
                QMessageBox.information(self, "成功", "总结已发送到群聊")
            else:
                QMessageBox.warning(self, "警告", "发送失败，详见 logs/ 目录下的日志")
        except Exception as exc:
            QMessageBox.critical(self, "错误", f"发送失败: {exc}")
        finally:
            if isinstance(button, QPushButton):
                button.setText(original_text)
                button.setEnabled(True)
            self.status_label.setText("")

    def save_summary(self) -> None:
        summary = self.summary_edit.toPlainText()
        group_name = self.group_name_input.text().strip()

        if not summary:
            QMessageBox.warning(self, "警告", "没有可保存的内容")
            return
        if not group_name:
            QMessageBox.warning(self, "警告", "请输入群聊名称")
            return

        button = self.sender()
        original_text = button.text() if isinstance(button, QPushButton) else ""
        try:
            if isinstance(button, QPushButton):
                button.setText("保存中...")
                button.setEnabled(False)
            self.status_label.setText("正在保存文件，请稍候...")
            QApplication.processEvents()

            saved_file = save_summary(group_name, summary)

            if saved_file:
                QMessageBox.information(self, "成功", f"总结已保存到:\n{saved_file}")
            else:
                QMessageBox.warning(self, "警告", "保存失败，详见 logs/ 目录下的日志")
        except Exception as exc:
            QMessageBox.critical(self, "错误", f"保存失败: {exc}")
        finally:
            if isinstance(button, QPushButton):
                button.setText(original_text)
                button.setEnabled(True)
            self.status_label.setText("")


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("微信群聊总结助手")

    font = QFont(ModernStyle.FONT_FAMILY.split(",")[0].strip())
    app.setFont(font)

    icon = _app_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
