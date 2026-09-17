# 排障与实测记录

本文档记录**在本机真实测到的现象与结论**，以及对应的解决办法。
换机器、升级微信或 `wxauto4` 之后，结论可能变化 —— 请以
`check_env.py` 与 `wechat_uia_wake.py status` 的实时结果为准。

面向使用者的快速上手在 [README](../README.md)；实现细节在 [TECHNICAL](TECHNICAL.md)。

实测环境：**微信 4.1.13.65 + wxauto4 41.1.7 + Python 3.12.10 + Windows 11**。

---

## 目录

- [1. 连接失败："未找到已登录的客户端主窗口"](#1-连接失败未找到已登录的客户端主窗口)
- [2. 消息读不全 / 只拿到当前页面几条](#2-消息读不全--只拿到当前页面几条)
- [3. 抓取很慢](#3-抓取很慢)
- [4. 未找到群聊或切换失败](#4-未找到群聊或切换失败)
- [5. 安装类问题](#5-安装类问题)
- [6. 兼容性矩阵](#6-兼容性矩阵)
- [7. 其他常见问题](#7-其他常见问题)

---

## 1. 连接失败："未找到已登录的客户端主窗口"

### 先破除一个常见误解

**最小化不是原因。** 实测确认：只要无障碍闸门已激活，微信主窗口**最小化时
`WeChat()` 依然能正常连接**，所以**不需要**为了运行本工具把微信窗口点开。
真正会出问题的是窗口被完全关闭或隐藏到托盘。

### 排查表

| 检查项 | 实测结果 |
| --- | --- |
| 微信客户端是否在运行 | 在运行，`D:\微信\Weixin\Weixin.exe`，版本 `4.1.13.65` |
| 是否存在主窗口 | 存在：`hwnd=67160`、Win32 窗口类 `Qt51514QWindowIcon`、标题 `微信` |
| 主窗口是否最小化 | 初始为最小化；还原后重试**仍然失败**（所以最小化不是根因） |
| wxauto4 期望的顶层窗口类 | `WeChatMainWnd._win_cls_name = 'Qt51514QWindowIcon'` → **与实际完全匹配** |
| wxauto4 期望的 UIA 类名 | `WeChatMainWnd._ui_cls_name = 'mmui::MainWindow'` |
| 实际窗口的 UIA 类名 | `Qt51514QWindowIcon` —— **Qt 空壳**，`mmui::*` 元素数为 0 |

### 根因：微信的「无障碍闸门」没被激活（**不是微信版本太新**）

微信 4.x 是自绘 UI，Qt **只在检测到无障碍客户端时才构建完整控件树**，微信把这个状态
缓存在 `Weixin.dll` 里的一个运行时字节。冷启动没有启用读屏时该字节为 0，
`WM_GETOBJECT` 只能返回 Qt 空壳，于是 wxauto4 的 `mmui::MainWindow` 校验失败。

同一个微信版本在不同机器上表现不同（有的好有的坏），正说明这是**运行时状态**问题，
而不是版本兼容问题 —— 所以**降级微信并不是必需的解**。

### 解决：热激活闸门，无需重启微信

`wechat_uia_wake.py` 把闸门字节置 1：

1. 置系统「存在屏幕阅读器」标志（`SystemParametersInfoW(SPI_SETSCREENREADER, 1, …)`）；
2. `OpenProcess` 打开加载了 `Weixin.dll` 的微信进程；
3. 在 `Weixin.dll` 基址 + 闸门 RVA 处写 1 字节（本项目实测：`4.1.13.65` → RVA `0x0AE2B0C8`）；
4. 用「`mmui::` 控件是否真的出现」校验，没出现就**回滚**并试下一个候选 RVA。

不注入代码、不重启微信、不改微信文件。

实测输出：

```text
$ .venv\Scripts\python.exe wechat_uia_wake.py wake
{"ok": true, "version": "4.1.13.65", "action": "hot_write_gate", "rva": 182628552,
 "detail": "热写闸门字节后 mmui 树已物化（无需重启微信）"}

$ .venv\Scripts\python.exe check_env.py --live
[ OK ] 实时连接: 成功读取当前窗口信息：{'chat_type': 'group',
       'chat_name': '学医学疯了我要提桶跑路', 'group_member_count': 223}
[ OK ] 消息读取: 当前窗口读到 6 条消息
[INFO] 消息样例: attr='friend' type='text' sender='野川🌳大五' content='...'
```

顺带这也验证了本项目的移植假设：真实消息对象的 `attr` / `type` / `sender` / `content`
与 `classify_message()` 的映射完全一致。

### 你通常什么都不用做

`wechat_summary.py` 的 `_create_wechat()` 已内置这个修复：`WeChat()` 一失败就自动热激活
并重试（只在真的失败时才动手，正常情况零开销）。GUI 与命令行都走这条路径。

想关闭这个自动行为：设环境变量 `WECHAT_SUMMARY_NO_WAKE=1`。

### 手动使用

```bat
REM 只读诊断：窗口、UIA 类名、闸门 RVA 候选（不写任何内存）
.venv\Scripts\python.exe wechat_uia_wake.py status

REM 执行热激活
.venv\Scripts\python.exe wechat_uia_wake.py wake

REM 清除系统读屏标志（可逆；闸门字节随微信重启自然复位）
.venv\Scripts\python.exe wechat_uia_wake.py restore
```

> 微信升级后闸门 RVA 可能漂移：版本表命中不了时会自动扫描 `Weixin.dll` 重新定位候选，
> 逐个写入并用「mmui 是否物化」校验，成功者会被记住。

### 兜底方案（热激活失败时）

1. **降级微信到 4.1.8.107 及以下**：wxauto 官方文档给出的版本归档
   <https://github.com/SiverKing/wechat4.0-windows-versions/releases/tag/v4.1.8.107>
2. **改用 `wxautox4`（Plus 版，付费）**：<https://wxauto.org/purchase>
3. **用软渲染启动微信**：给微信快捷方式加环境变量 `QT_OPENGL=software`、
   `QT_ANGLE_PLATFORM=software` 后再启动（需重启微信），部分机器上可让 Qt 直接暴露
   `mmui::MainWindow`。

### 偶发：`'ProfileWnd' object has no attribute 'topui'`

`WeChat()` 构造过程中 wxauto4 会去读个人资料窗口，微信 UI 状态不干净时这个调用会偶发失败。
本版对此做了**退避重试两次**（1.5s / 3s）—— 实测过几秒自己就好，不该把堆栈甩给用户。
如果两次都失败，检查是否有残留的个人名片/资料弹窗，手动关掉再试。

---

## 2. 消息读不全 / 只拿到当前页面几条

**这就是本项目要解决的核心问题。** 原版走 `wx.LoadMoreCache()` 翻页，
而免费版 `wxauto4` 的 `LoadMoreCache` 内部调用了一个**并不存在的方法**
（`WeChatMainWnd.load_more_message`），异常被吞掉后循环第一轮就 `break`：

```
WARNING | 加载更早的消息失败，停止翻页：
          'WeChatMainWnd' object has no attribute 'load_more_message'
```

用户看到的现象就是「只总结了当前页面那几条消息」。

本版把这一层整个重写成**滚动收集引擎**。实现细节与四个实测坑见
[TECHNICAL §4](TECHNICAL.md#4-消息抓取引擎)。如果你看到的仍然是旧行为，
**先确认你跑的是不是旧版代码**（`wechat_summary.py` 里应当能搜到 `_merge_window`）。

### 还是读不全怎么办

- **确认微信窗口没有被最小化**：虽然最小化不影响连接，但窗口高度直接决定一屏能装几条消息；
- **把微信窗口拉高**：高度上限被屏幕锁死，本机约 1032px ≈ 8~12 条，能拉高一点是一点；
- **缩小时间范围**：24 小时的大群可能要十几分钟，先试 1~3 小时；
- **看日志**：会写「向上翻页 N 次，累计 M 条消息」，以及**没覆盖到起始时间时的明确告警**；
- **跑探针**：`tools/collect_probe.py` 会打印每一步的明细，见
  [TECHNICAL §14](TECHNICAL.md#14-调试工具)。

### 偶发：一个子控件都枚举不到

微信切到后台时，Qt 会把消息列表的无障碍节点回收掉，`GetAllMessage()` 与子控件枚举会
**同时返回空**。现在的做法是**重试 + 点一次「跳到最新」强刷**，
而不是把空结果当成「群里没有消息」。

---

## 3. 抓取很慢

瓶颈在 wxauto4 免费版的 `GetAllMessage()`，两个实测数字：

| 操作 | 耗时 |
| --- | --- |
| 直接枚举消息列表的 UIA 子控件 | **~0.02 秒** |
| `GetAllMessage()`（拿发言人必需的接口） | **~10 秒**，且与消息条数几乎无关 |

一屏只能看到 8~12 条 → 吞吐上限 **0.5~1 条/秒**。
本版已经做了三处优化（一屏只读一次、不靠键盘定位最新、内容重叠对齐去重），
想再快只能**减少要抓的消息数**。

程序不会假装很快：状态栏实时显示「第 N 屏 / 已读取 M 条」，
超时或到限都会在日志里写明，并且会把「记录没有覆盖到时间范围的起点」告诉模型，
避免总结谎称覆盖了整个区间。

**中途想停**：按 `Esc` / 空格，或点「中止」按钮。

---

## 4. 未找到群聊或切换失败

- 群名要和微信里的**完全一致**（含 emoji、空格）；
- 确认微信已登录；
- 本工具在切换后会调用 `ChatInfo()` **校验当前窗口群名**是否匹配，不匹配就不发送 ——
  这是防止把总结发到错误会话的保护。

---

## 5. 安装类问题

### `pip install wxauto4` 报 `No matching distribution found`

先确认 Python 版本：

```bat
python --version
```

只要是 **3.14**，就是这个原因 —— `wxauto4` 元数据是 `Requires-Python: <3.14,>=3.9`，
只发布到 cp313。请改用 Python 3.12。

### 虚拟环境建好了，但 `.venv\Scripts\` 里没有 `pip.exe`

venv 路径里含**中文或特殊字符**时，`ensurepip` 可能失败。
默认路径 `D:\wechat_summary` 是纯 ASCII，正常情况下不会遇到；
若你把项目挪到了中文路径下，把 venv 建到纯英文路径即可。

### 环境自检说"未检测到正在运行的微信桌面客户端"

先启动微信并登录。`check_env.py` 是通过进程名（`Weixin.exe` / `WeChat.exe`）判断的。

---

## 6. 兼容性矩阵

| 项目 | 版本 | 状态 |
| --- | --- | --- |
| Windows | 10 / 11 | ✅ wxauto 基于 Windows UI Automation，**不支持** Linux / macOS |
| Python | 3.9 ~ 3.12 | ✅ 官方标注范围 |
| Python | 3.13 | ⚠️ 可用但不保证 |
| Python | **3.14+** | ❌ 无可用 wheel，必定安装失败 |
| 微信 | 3.x | ❌ 需用旧 `wxauto`，而该包已从 PyPI 下架 |
| 微信 | 4.1.8.107 及以下 | ✅ wxauto4（免费版）官方标注支持上限 |
| 微信 | 高于 4.1.8.107 | ⚠️ 可能部分方法失效；本版实测 **4.1.13.65 可用**（配合无障碍热激活）。兜底方案：`wxautox4`（Plus 版，付费） |
| 历史消息 | 免费版 `wxauto4` | ✅ 本版的**滚动收集引擎**（原版的 `LoadMoreCache` 走不通） |
| 历史消息 | Plus 版 `wxautox4` | 另有 `GetHistoryMessage(n=, callback=)`，更快；本版未适配该路径 |

`check_env.py` 会自动比对你机器上的微信版本并给出对应提示。

---

## 7. 其他常见问题

### Q: 发送很长的总结怎么办？

`_split_message()` 会按段落边界自动切分成多条发送，默认阈值 2000 字符。
拼接后能无损还原（有测试守）。

### Q: 总结里还有 `#` 和 `**`？

微信聊天窗口**不渲染 Markdown**，所以做了两层处理：提示词层要求纯文本，
代码层（`wechat_text.py`）在显示/发送前再做一次确定性转换。
界面上有「按微信纯文本排版」开关（默认开）——**预览区显示什么，发出去就是什么**。

如果仍看到残留，看预览区右侧的提示（会列出「仍残留：标题、表格…」），
那说明是转换器没覆盖到的新语法，欢迎提 issue 并附上原始输出。

### Q: 用这个会不会被封号？

wxauto 是第三方桌面自动化工具，**与腾讯/微信官方无任何隶属或授权关系**，
使用第三方自动化操作微信可能触发风控。请：

- 只操作**你本人有权控制**的设备、账号和会话；
- 低频使用，不要批量群发、不要自动加陌生好友、不要做多账号群控；
- 首次务必用「文件传输助手」测试。

参见 [wxauto 可接受使用政策](https://docs.wxauto.org/legal/acceptable-use)。

### Q: 微信升级后突然不能用了？

按顺序做三件事：

```bat
.venv\Scripts\python.exe check_env.py --live          REM 看连接与消息读取是否正常
.venv\Scripts\python.exe wechat_uia_wake.py status   REM 看闸门 RVA 是否能命中
.venv\Scripts\python.exe tools\collect_probe.py "群名" 1 60   REM 看抓取层是否正常
```

把这三条的输出附在 issue 里，基本能定位到是哪一层坏了。
