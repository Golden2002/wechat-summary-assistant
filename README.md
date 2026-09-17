<div align="center">

# 微信群聊总结助手

**把刷不完的微信群聊，交给 AI 整理成一份能直接发回群里的总结。**

微信 4.x / wxauto4 适配版 · 图形界面 · 全程操作本机已登录的微信，不碰协议、不注入 DLL

[![Stars](https://img.shields.io/github/stars/Golden2002/wechat-summary-assistant?style=flat-square&color=0066cc)](https://github.com/Golden2002/wechat-summary-assistant/stargazers)
[![Last Commit](https://img.shields.io/github/last-commit/Golden2002/wechat-summary-assistant?style=flat-square)](https://github.com/Golden2002/wechat-summary-assistant/commits)
[![Platform](https://img.shields.io/badge/platform-Windows%2010%2F11-0078d4?style=flat-square)](#环境要求)
[![Python](https://img.shields.io/badge/python-3.9%20~%203.13-3776ab?style=flat-square)](#环境要求)
[![License](https://img.shields.io/badge/license-学习研究用途-lightgrey?style=flat-square)](#许可与免责)

</div>

---

## 这解决什么问题

微信群聊消息刷得太快，一天不看就几百条，错过重点；想回头翻又根本翻不完。

这个工具：

1. 从**你自己已登录的微信桌面客户端**里，读取指定会话在指定时间范围内的聊天记录；
2. 交给 DeepSeek / Kimi / 通义千问（或任何 OpenAI 兼容接口）生成结构化总结；
3. 结果可以直接发回群里 —— 并且**自动转成微信能正常显示的纯文本**。

全程走 Windows UI 自动化，**不涉及协议破解、不注入代码、不修改微信文件**。

---

## 特性

- 🔍 **真的能抓到整个时间范围** —— 原版因 `wxauto4` 免费版的内部方法缺失，只拿得到当前屏幕那几条；
  本版重写为滚动收集引擎，实测量级从 **7 条 → 95 条**
- 🧩 **18 套场景模板** —— 通用 / 私人社交 / 工作协作 / 学习与行业 / 社区与运营，
  每套都写了「只依据记录、不得编造、缺失写『记录中未提及』」的硬纪律
- 🧹 **自动去 Markdown** —— 微信不渲染 Markdown，输出会转成 `【标题】` / `· 条目` 的纯文本，
  预览即所发
- ⏱️ **灵活的时间范围** —— 相对（30 分钟 ~ 30 天）与绝对（今天/昨天/本周一 00:00 起、
  自定义起止时刻）双通道
- 🛑 **可随时中止** —— 抓大群要十几分钟，按 `Esc` / 空格 或点「中止」即可停下
- 🔌 **12 家 AI 服务开箱可用**，模型框可手输，支持一键拉取该服务**当前**真实可用的模型清单
- 🧠 **抗幻觉设计** —— `[图片]`/`[语音]` 占位不许猜内容、防提示注入、隐私脱敏、空小节自动省略
- 🧪 **4 套离线测试** —— 用一个「只能看到当前屏幕」的假微信覆盖抓取引擎的全部关键假设

---

## 快速开始

```bat
REM 1) 双击 install.bat 一键装好（建 venv + 装依赖 + 自检）
REM    或手动：py -3.12 -m venv .venv && .venv\Scripts\python.exe -m pip install -r requirements.txt

REM 2) 双击 run.bat 启动
```

然后：

1. 在「**AI服务配置**」页填一个服务的 API Key；
2. 回到「**群聊总结**」页，群名先填 **`文件传输助手`**（只属于你自己，最安全）；
3. 选时间范围 → 点「**获取群聊消息**」→ 生成后「保存总结」或「发送到群聊」。

> ⚠️ Python 用 **3.12**。`wxauto4` 要求 `<3.14`，**Python 3.14 装不上**。

---

## 效果示例

模型输出的是 Markdown，微信却只认纯文本 —— 这一层由工具自动处理：

<table>
<tr><th>模型原始输出</th><th>发送到微信的样子</th></tr>
<tr><td>

```markdown
# 今日纪要

**结论**：方案 A 通过。

## 待办
- [ ] 张三：提交 PR
- [x] 李四：更新文档

| 风险 | 等级 |
| --- | --- |
| 排期紧 | 高 |

> 周五前完成。
```

</td><td>

```text
【今日纪要】

结论：方案 A 通过。

【待办】
☐ 张三：提交 PR
☑ 李四：更新文档

· 排期紧：高

｜ 周五前完成。
```

</td></tr>
</table>

---

## 文档

| 文档 | 内容 |
| --- | --- |
| [docs/USAGE.md](docs/USAGE.md) | 安装、AI 服务与模型、时间范围、18 套模板、输出格式、中止、命令行 |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | 实测记录与排障：连接失败、消息读不全、抓取慢、兼容性矩阵 |
| [docs/TECHNICAL.md](docs/TECHNICAL.md) | 技术说明：架构、抓取引擎、中止机制、提示词系统、转换器、测试策略 |
| [docs/provider-api-research-2026-09-17.md](docs/provider-api-research-2026-09-17.md) | 12 家服务与模型清单的调研记录（含已下线名单与来源） |

---

## 项目结构

```
wechat_summary/
├─ wechat_summary_gui.py    图形界面入口（双击 run.bat 实际运行的就是它）
├─ wechat_summary.py        核心逻辑：抓消息 / 拼提示词 / 调模型 / 保存 / 发送
├─ prompt_presets.py        18 套内置提示词模板（纯数据，改文案不用动逻辑）
├─ wechat_text.py           Markdown → 微信纯文本转换器（带自测）
├─ wechat_uia_wake.py       微信 4.1+ 无障碍闸门热激活（自动修复，也可独立使用）
├─ check_env.py             环境自检（--live 可实连微信）
├─ tests/                   4 套离线测试（不需要微信运行）
├─ tools/collect_probe.py   真机抓取验证（不调用 AI、不发送任何消息）
└─ docs/                    使用 / 排障 / 技术 / 调研文档
```

---

## 环境要求

| 项目 | 要求 |
| --- | --- |
| 操作系统 | Windows 10 / 11（wxauto 基于 Windows UI Automation，**不支持** Linux / macOS） |
| Python | **3.9 ~ 3.13**，推荐 **3.12**；**3.14 不可用** |
| 微信 | 微信 4.x 桌面客户端，已在你本人账号下正常登录 |

完整兼容性矩阵（含微信版本、Plus 版差异）见
[docs/TROUBLESHOOTING.md §6](docs/TROUBLESHOOTING.md#6-兼容性矩阵)。

---

## 常见问题

<details>
<summary><b>提示「未找到已登录的客户端主窗口」？</b></summary>

**最小化不是原因**（已实测）。真正的原因几乎总是：微信 4.1+ 的「无障碍闸门」没被激活 ——
这不是微信版本问题，**不需要降级微信**。

工具会自动热激活并重试，通常你什么都不用做。手动诊断：

```bat
.venv\Scripts\python.exe wechat_uia_wake.py status
.venv\Scripts\python.exe wechat_uia_wake.py wake
```

详见 [docs/TROUBLESHOOTING.md §1](docs/TROUBLESHOOTING.md#1-连接失败未找到已登录的客户端主窗口)。

</details>

<details>
<summary><b>只总结出几条消息 / 消息读不全？</b></summary>

这是原实现的已知缺陷（`wxauto4` 免费版的 `LoadMoreCache` 内部方法缺失，翻页第一轮就中断），
本版已重写为滚动收集引擎。

若仍觉得读少了：确认微信窗口没被最小化、尽量拉高窗口、把时间范围调小。
抓取吞吐约 **0.5~1 条/秒**（瓶颈在微信侧，与模型无关），日志会写明是否覆盖到了起始时间。

详见 [docs/TROUBLESHOOTING.md §2](docs/TROUBLESHOOTING.md#2-消息读不全--只拿到当前页面几条)。

</details>

<details>
<summary><b>抓取太慢，能中途停吗？</b></summary>

按 **`Esc`** 或 **`空格`**，或点界面上的「**中止**」按钮（需先点回本工具窗口）。
中止是协作式的，最坏再等约 10 秒。

</details>

<details>
<summary><b>`pip install` 报 <code>No matching distribution found</code>？</b></summary>

多半是 Python 3.14。`wxauto4` 的元数据是 `Requires-Python: <3.14,>=3.9`，请改用 3.12。

</details>

<details>
<summary><b>总结里还是出现了 <code>#</code> 和 <code>**</code>？</b></summary>

看预览区右侧提示（会列出「仍残留：标题、表格…」），说明是转换器没覆盖到的新语法，
欢迎提 issue 并附上原始输出。

</details>

更多问题见 [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)。

---

## 为什么这个项目的注释特别多

这个项目的"依赖"是一门**会随时变化的 UI**：微信客户端每次更新都可能让自动化失效。
所以笔者的取舍是：

- 把**每一个实测结论**都写进注释与文档（为什么用 `{PageUp}` 而不是 `{End}`、
  为什么不能用 `msg.id` 去重），而不是只写"这样能跑"；
- 用**假微信**把抓取引擎的关键假设固化成测试，改代码时能立刻发现问题；
- 遇到厂商 API 变动（模型下线、`GET /models` 不通用）时，
  **把已下线的名字写进注释和提示里**，避免下一个人再踩。

细节见 [docs/TECHNICAL.md](docs/TECHNICAL.md)。

---

## 贡献

欢迎提 Issue / PR。改代码前建议先跑一遍离线测试：

```bat
.venv\Scripts\python.exe wechat_text.py
.venv\Scripts\python.exe tests\test_core.py
.venv\Scripts\python.exe tests\test_gui_smoke_v2.py
```

如果涉及抓取层，请附上 `tools/collect_probe.py` 的输出。

---

## 致谢

本项目是**二次开发版**，界面设计、交互结构与部分提示词沿用自
[Vita0519/wechat_summary](https://github.com/Vita0519/wechat_summary)（原始项目）。

- **[wxauto4](https://pypi.org/project/wxauto4/)**（作者 Cluic）—— 微信 UI 自动化库
- **[wechatauto-replica](https://github.com/fanyuantaier/wechatauto-replica)** 等社区实测文章 —— 无障碍闸门热激活方案的来源
- OpenAI Python SDK · Qt for Python (PySide6) · loguru

完整的来源清单、沿用范围与依赖致谢见 **[docs/ATTRIBUTION.md](docs/ATTRIBUTION.md)**。

---

## 许可与免责

- 本工具**仅供学习和技术研究使用**，不得用于任何商业或非法行为。
- 原项目**未提供独立的 LICENSE 文件**，因此本版**不主张任何独立授权**，
  并按原项目"仅供学习和技术研究使用"的声明精神沿用。**如需商业使用或再分发，请先联系原作者取得授权。**
- 本工具与腾讯/微信官方**无任何隶属或授权关系**，不是官方软件。
- 使用者应只操作**你本人有权控制**的设备、账号与会话，遵守微信服务协议，
  不得用于批量营销、骚扰或任何违法用途。使用第三方自动化操作微信**可能触发风控**，
  请低频使用，并先用「文件传输助手」测试。
- 作者不对本工具的安全性、完整性、可靠性、有效性、正确性或适用性做任何明示或暗示的保证，
  也不对使用或滥用造成的任何直接或间接损失承担责任。微信 4.x 客户端更新频繁，
  本版不对持续兼容性作任何承诺。

参见 [wxauto 可接受使用政策](https://docs.wxauto.org/legal/acceptable-use)。
