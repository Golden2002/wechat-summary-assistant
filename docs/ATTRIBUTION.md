# 来源与致谢（Attribution）

本项目是**二次开发（derivative work）**。界面设计、交互结构、AI 总结提示词与函数命名
均沿用了原项目。特此声明并致谢。

---

## 1. 原始项目（本仓库的直接来源）

| 项 | 内容 |
| --- | --- |
| 名称 | 微信群聊总结助手（wechat_summary） |
| 作者 | **Vita0519** |
| 仓库 | <https://github.com/Vita0519/wechat_summary> |

### 本版沿用自原项目的部分

- **`wechat_summary_gui.py`**
  - `ModernStyle` 配色与 QSS 样式表；
  - 三个标签页（群聊总结 / AI服务配置 / 提示词配置）的布局结构；
  - `SummaryWorker` 多线程模式；
  - `AIConfig` / `AIServiceConfig` / `ConfigCard` / `AddServiceDialog` 的类划分。
- **`wechat_summary.py`**
  - 默认 AI 提示词的六节结构（重要提醒 / 今日热门话题 / 点评 / 待跟进事项 /
    其他讨论话题 / 结语）—— 现已成为 19 套模板中的「通用群聊总结（原版）」；
  - `get_wechat_messages` / `save_summary` / `send_summary` 三函数接口；
  - `summary/*.txt` 的输出格式。
- **README 中的免责声明段落**（见 [README](../README.md#许可与免责)）。

### 本版对原项目做的修改

见 [TECHNICAL 附录](TECHNICAL.md#附本项目对原项目的改动摘要)。
核心是把消息读取层从旧 `wxauto` 重写为 `wxauto4`，
并把「只拿得到当前屏幕几条」的抓取实现整个重写为滚动收集引擎。

### 许可情况

截至本版改写时，原仓库**未提供独立的 `LICENSE` 文件**
（目录内容已核对：`README.md`、`V2/`、`icons/`、`requirements.txt`、`resources.py`、
`resources.qrc`、`wechat_summary.py`、`wechat_summary_gui.py`、`使用截图.png`）。

因此本版**不主张任何独立授权**，并按原项目 README 中
「仅供学习和技术研究使用，不得用于任何商业或非法行为」的声明精神沿用。

> **如需商业使用或再分发，请先联系原作者取得授权。**

---

## 2. 微信 UI 自动化库

| 库 | 作者 | 说明 |
| --- | --- | --- |
| **wxauto4** | Cluic | 本版所依赖。PyPI <https://pypi.org/project/wxauto4/>，文档 <https://docs.wxauto.org/>，官网 <https://wxauto.org/> |
| `wxauto` | Cluic | 原版所依赖、**现已从 PyPI 下架**的老包，本版未使用 |

---

## 3. 依赖的开源项目

| 项目 | 用途 | 地址 |
| --- | --- | --- |
| OpenAI Python SDK | 调用 OpenAI 兼容接口 | <https://github.com/openai/openai-python> |
| Qt for Python (PySide6) | 图形界面 | <https://doc.qt.io/qtforpython-6/> |
| loguru | 日志 | <https://github.com/Delgan/loguru> |
| psutil | 进程与版本检测（由 wxauto4 引入） | <https://github.com/giampaolo/psutil> |

---

## 4. 参考资料

- wxauto 官方文档（安装与兼容性、3.9 → 4.x 适配机制变化）：<https://docs.wxauto.org/>
- wxauto 可接受使用政策：<https://docs.wxauto.org/legal/acceptable-use>
- 微信 4.0 版本归档（降级兜底用）：
  <https://github.com/SiverKing/wechat4.0-windows-versions/releases/tag/v4.1.8.107>

---

## 5. 无障碍热激活方案的来源（重要）

`wechat_uia_wake.py` 的机制**不是本项目原创**，来自以下公开的社区实测与文章，
特此声明并致谢：

| 来源 | 提供了什么 | 地址 |
| --- | --- | --- |
| `wechatauto-replica` 的 `wechatauto/uia_driver.py` | 完整实现思路：Qt accessibility 闸门字节、`4.1.13.65 → RVA 0x0AE2B0C8` 的实测值、DLL 特征扫描与"写入后校验、失败回滚" | <https://github.com/fanyuantaier/wechatauto-replica> |
| 《关于 Weixin 4.1 以上版本 Qt 框架问题的研究》 | 现象解释：Qt 渲染/无障碍差异导致顶层窗口时而是 `mmui::MainWindow`、时而是 `Qt51514QWindowIcon` | <https://www.vidibot.com/2115.html> |
| 《微信 4.1.5.X、UIAutomation、UI 树恢复》 | "按需暴露 UI 树"机制与自建 UIA 客户端的思路 | <https://blog.csdn.net/WWW7530471/article/details/156270059> |
| 《微信 4.0 的主窗口类名变化和对策研究》 | 主窗口类名从 `QMainWindow` 演变为 `Qt51514QWindowIcon` 的来龙去脉 | <https://www.vidibot.com/2129.html> |
| `wxrpa` | 上述 CSDN 文章提到的配套开源项目 | <https://github.com/wymliuming/wxrpa> |

本项目在此基础上做的是：裁成**最小必要动作**（写 1 个字节 + 校验 + 失败回滚）、
接进 `wechat_summary.py` 的失败重试路径、补上只读诊断入口，并写清风险与兜底方案。

---

## 6. 模型清单调研的来源

12 家服务的 base_url 与模型清单来自一轮带来源标注的公开文档调研，
结论与置信度分级见 [provider-api-research-2026-09-17.md](provider-api-research-2026-09-17.md)。
该调研明确遵守一条规则：**凡未能在官方页面读到的模型 ID 一律标注「未能验证」，
不编造**。
