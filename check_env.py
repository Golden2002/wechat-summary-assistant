# -*- coding: utf-8 -*-
"""环境自检脚本。

用法::

    .venv\\Scripts\\python.exe check_env.py            # 只做只读检查
    .venv\\Scripts\\python.exe check_env.py --live     # 额外真实连接一次微信窗口

它会检查：
  1. Python 版本是否在 wxauto4 支持范围内（3.9 ~ 3.13，**不支持 3.14**）；
  2. 关键依赖是否装好、版本号多少；
  3. 本机微信桌面客户端是否在运行、路径与版本号；
  4. 该微信版本是否超出 wxauto4 免费版的兼容范围。

注意：``--live`` 会真实读取当前微信窗口信息，请先手动打开微信、
确保已登录，并优先在"文件传输助手"上测试。
"""

from __future__ import annotations

import argparse
import re
import sys
from typing import List, Optional, Tuple

# 让中文输出在 GBK 代码页的控制台下也不乱码
try:  # pragma: no cover - 取决于终端
    if sys.stdout is not None:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    if sys.stderr is not None:
        sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
except Exception:  # pragma: no cover
    pass

# ---------------------------------------------------------------- 期望值

SUPPORTED_MIN = (3, 9)
SUPPORTED_MAX = (3, 13)          # 含 3.13
REQUIRED_PY = ">=3.9, <3.14"

#: wxauto4 免费版官方标注支持的微信客户端上限（见 https://docs.wxauto.org/docs/install）
WECHAT_TESTED_MAX = (4, 1, 8, 107)

#: 已知的微信客户端进程名 -> 说明
WECHAT_PROCESS_NAMES = {
    "weixin": "微信 4.x（新版客户端）",
    "wechat": "微信 3.x（旧版客户端）",
    "wechatappex": "微信小程序容器（附属进程）",
}

results: List[Tuple[str, str, str]] = []   # (level, 项目, 说明)


def record(level: str, item: str, detail: str) -> None:
    results.append((level, item, detail))
    icon = {"OK": "[ OK ]", "WARN": "[WARN]", "FAIL": "[FAIL]", "INFO": "[INFO]"}.get(level, "[ ?? ]")
    print(f"{icon} {item}: {detail}")


# ---------------------------------------------------------------- 1. Python


def check_python() -> None:
    version = sys.version_info
    current = f"{version.major}.{version.minor}.{version.micro}"
    record("INFO", "Python 版本", f"{current}  ({sys.executable})")

    if (version.major, version.minor) < SUPPORTED_MIN:
        record("FAIL", "Python 兼容性", f"需要 {REQUIRED_PY}，当前过低：{current}")
    elif (version.major, version.minor) > SUPPORTED_MAX:
        record(
            "FAIL",
            "Python 兼容性",
            f"{current} 不被 wxauto4 支持（要求 {REQUIRED_PY}）；"
            "PyPI 上 wxauto4 只发布了 cp39~cp313 的 wheel，没有 cp314，安装必然失败",
        )
    else:
        record("OK", "Python 兼容性", f"{current} 在 {REQUIRED_PY} 范围内")


# ---------------------------------------------------------------- 2. 依赖


def check_packages() -> None:
    packages = [
        ("wxauto4", "wxauto4", "微信 4.x UI 自动化核心"),
        ("openai", "openai", "OpenAI 兼容接口客户端"),
        ("loguru", "loguru", "日志"),
        ("PySide6", "PySide6", "图形界面"),
        ("psutil", "psutil", "进程检测"),
    ]
    for import_name, dist_name, purpose in packages:
        try:
            module = __import__(import_name)
            version = getattr(module, "__version__", None)
            if version is None:
                try:
                    from importlib.metadata import version as _v

                    version = _v(dist_name)
                except Exception:
                    version = "未知版本"
            record("OK", f"依赖 {dist_name}", f"{version} —— {purpose}")
        except Exception as exc:
            record("FAIL", f"依赖 {dist_name}", f"导入失败：{exc}（{purpose}）")


# ---------------------------------------------------------------- 3. wxauto4 API


def check_wxauto_api() -> None:
    """检查本版代码依赖的 wxauto4 API 是否齐全。

    这一步很有必要：wxauto4 免费版与 Plus 版的方法集**不一样**。例如官方文档里
    标 "✨" 的 ``GetHistoryMessage`` 就是 Plus 专有方法。
    """
    try:
        from wxauto4 import WeChat
    except Exception as exc:
        record("FAIL", "API 检查", f"无法导入 wxauto4：{exc}")
        return

    required = {
        "ChatWith": "切换会话",
        "ChatInfo": "读取窗口信息",
        "GetAllMessage": "读取消息",
        "SendMsg": "发送消息",
    }
    for name, purpose in required.items():
        if hasattr(WeChat, name):
            record("OK", f"API {name}", purpose)
        else:
            record("FAIL", f"API {name}", f"缺失（{purpose}）—— 本版代码依赖它")

    if hasattr(WeChat, "LoadMoreCache"):
        record(
            "OK",
            "历史消息翻页",
            "LoadMoreCache 可用 —— 免费版 wxauto4 的翻页方式，与原版 LoadMoreMessage 等价",
        )
    elif hasattr(WeChat, "GetHistoryMessage"):
        record("OK", "历史消息翻页", "GetHistoryMessage 可用 —— Plus 版 wxautox4")
    else:
        record(
            "WARN",
            "历史消息翻页",
            "既无 LoadMoreCache 也无 GetHistoryMessage，只能读取当前窗口可见消息",
        )


# ---------------------------------------------------------------- 无障碍闸门


def check_accessibility_gate() -> None:
    """检查微信窗口的 UIA 树是否已物化出 mmui 控件。

    这是 wxauto4 能否连上的**真正前提**：微信 4.1+ 冷启动时只暴露 Qt 空壳
    （UIA 类名 Qt51514QWindowIcon），wxauto4 要找的是 mmui::MainWindow。
    """
    try:
        import wechat_uia_wake as gate
    except Exception as exc:
        record("WARN", "无障碍闸门", f"无法导入 wechat_uia_wake：{exc}")
        return

    windows = gate.wechat_window_handles()
    if not windows:
        record("WARN", "无障碍闸门", "未找到加载了 Weixin.dll 的可见微信窗口，无法判断")
        return

    window = windows[0]
    info = gate.describe_uia(int(window["hwnd"]))
    class_name = info.get("class_name")
    version = str(window.get("dll_path", "")).replace("\\", "/").split("/")[-2:-1]
    version_text = version[0] if version else "未知"

    if info.get("mmui"):
        record(
            "OK",
            "无障碍闸门",
            f"UIA 类名 = {class_name}（mmui 树已物化，wxauto4 可以连接）",
        )
    else:
        record(
            "WARN",
            "无障碍闸门",
            f"UIA 类名 = {class_name!r}，仍是 Qt 空壳 —— wxauto4 会报"
            "“未找到已登录的客户端主窗口”。"
            "这不是微信版本问题：微信 4.1+ 冷启动会隐藏控件树，需要激活无障碍闸门。"
            "执行 wechat_uia_wake.py wake 可热激活（wechat_summary.py / GUI 也会自动做）",
        )

    flag = gate.get_screen_reader_flag()
    record(
        "INFO",
        "系统读屏标志",
        f"{flag}（微信版本目录：{version_text}；"
        "闸门未激活时本工具会自动热写 1 个状态字节，不重启微信）",
    )


# ---------------------------------------------------------------- 4. 微信客户端


def _find_wechat_processes() -> List[Tuple[str, Optional[str], Optional[str]]]:
    """返回 [(进程名, 可执行文件路径, 文件版本)]，已去重。"""
    found: List[Tuple[str, Optional[str], Optional[str]]] = []
    seen = set()

    try:
        import psutil  # type: ignore

        for proc in psutil.process_iter(["name", "exe"]):
            try:
                name = (proc.info.get("name") or "").lower()
                base = name[:-4] if name.endswith(".exe") else name
                if base not in WECHAT_PROCESS_NAMES or base == "wechatappex":
                    continue
                exe = proc.info.get("exe")
                key = (base, exe)
                if key in seen:
                    continue
                seen.add(key)
                found.append((base, exe, _file_version(exe)))
            except Exception:
                continue
    except ImportError:
        record("WARN", "进程检测", "psutil 不可用，跳过微信进程检测")

    return found


def _file_version(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    try:
        import ctypes
        from ctypes import wintypes

        size = ctypes.windll.version.GetFileVersionInfoSizeW(path, None)
        if not size:
            return None
        buffer = ctypes.create_string_buffer(size)
        if not ctypes.windll.version.GetFileVersionInfoW(path, 0, size, buffer):
            return None
        pointer = ctypes.c_void_p()
        length = wintypes.UINT()
        if not ctypes.windll.version.VerQueryValueW(
            buffer, "\\", ctypes.byref(pointer), ctypes.byref(length)
        ):
            return None

        class VS_FIXEDFILEINFO(ctypes.Structure):
            _fields_ = [
                ("dwSignature", wintypes.DWORD),
                ("dwStrucVersion", wintypes.DWORD),
                ("dwFileVersionMS", wintypes.DWORD),
                ("dwFileVersionLS", wintypes.DWORD),
                ("dwProductVersionMS", wintypes.DWORD),
                ("dwProductVersionLS", wintypes.DWORD),
                ("dwFileFlagsMask", wintypes.DWORD),
                ("dwFileFlags", wintypes.DWORD),
                ("dwFileOS", wintypes.DWORD),
                ("dwFileType", wintypes.DWORD),
                ("dwFileSubtype", wintypes.DWORD),
                ("dwFileDateMS", wintypes.DWORD),
                ("dwFileDateLS", wintypes.DWORD),
            ]

        info = ctypes.cast(pointer, ctypes.POINTER(VS_FIXEDFILEINFO)).contents
        ms, ls = info.dwFileVersionMS, info.dwFileVersionLS
        return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
    except Exception:
        return None


def _wechat_window_states() -> List[dict]:
    """枚举标题为微信的顶层窗口，返回句柄 + 最小化/可见状态。

    用途是**告知**窗口当前状态，而不是判定故障：本项目实测确认，
    只要无障碍闸门已激活，主窗口最小化时 ``WeChat()`` 依然能正常连接，
    所以"最小化"不是 "未找到已登录的客户端主窗口" 的原因。
    """
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        return []

    user32 = ctypes.windll.user32
    found: List[dict] = []
    titles = ("微信", "Weixin", "WeChat")

    def _callback(hwnd, _lparam):
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        if buffer.value not in titles:
            return True
        # 微信有若干隐藏的 Qt 工具窗口（标题同样是 Weixin），不是主窗口，需排除
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        found.append(
            {
                "hwnd": hwnd,
                "title": buffer.value,
                "pid": pid.value,
                "minimized": bool(user32.IsIconic(hwnd)),
                "visible": bool(user32.IsWindowVisible(hwnd)),
            }
        )
        return True

    try:
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        user32.EnumWindows(callback_type(_callback), 0)
    except Exception:
        return []
    return found


def _version_tuple(text: str) -> Tuple[int, ...]:
    numbers = re.findall(r"\d+", text or "")
    return tuple(int(n) for n in numbers[:4])


def check_wechat() -> None:
    processes = _find_wechat_processes()
    if not processes:
        record(
            "WARN",
            "微信客户端",
            "未检测到正在运行的微信桌面客户端；请先启动并登录微信再运行本工具",
        )
        return

    for name, exe, version in processes:
        description = WECHAT_PROCESS_NAMES.get(name, name)
        location = exe or "（未知路径，可能被权限限制）"
        record("OK", "微信客户端", f"{description}  版本={version or '未知'}  路径={location}")

        if name == "wechat":
            record(
                "WARN",
                "客户端代际",
                "检测到微信 3.x。本适配版基于 wxauto4，面向微信 4.x；"
                "3.x 请使用原版项目依赖的旧 wxauto（但该包已下架）",
            )
            continue

        if not version:
            continue

        current = _version_tuple(version)
        if len(current) >= 3 and current[:3] > WECHAT_TESTED_MAX[:3]:
            record(
                "WARN",
                "客户端代际",
                f"你的微信 {version} 高于 wxauto4 免费版官方标注的支持上限 "
                f"{'.'.join(map(str, WECHAT_TESTED_MAX))}；"
                "UI 元素可能已变化导致部分方法失效，若遇到异常可考虑升级到 wxautox4（Plus 版）",
            )
        else:
            record("OK", "客户端代际", f"微信 {version} 在 wxauto4 免费版支持范围内")

    # 主窗口状态：只做告知，不作为故障判据（最小化不影响连接，已实测）
    windows = _wechat_window_states()
    if windows:
        opened = [w for w in windows if not w["minimized"]]
        if opened:
            record("OK", "主窗口状态", f"微信主窗口已打开（hwnd={opened[0]['hwnd']}）")
        else:
            record(
                "INFO",
                "主窗口状态",
                "微信主窗口当前最小化。实测确认这**不影响**连接"
                "（只要无障碍闸门已激活），无需为了运行本工具把窗口点开",
            )


# ---------------------------------------------------------------- 5. 实时连通性


def check_live() -> None:
    print()
    print("---- --live：真实连接微信窗口 ----")
    try:
        from wxauto4 import WeChat
    except Exception as exc:
        record("FAIL", "实时连接", f"无法导入 wxauto4：{exc}")
        return

    try:
        wx = WeChat()
    except Exception as exc:
        record("FAIL", "实时连接", f"初始化 WeChat() 失败：{exc}")
        if "主窗口" in str(exc):
            record(
                "INFO",
                "排查方向",
                "微信 4.1+ 冷启动时 UIAutomation 只能拿到 Qt 空壳（Qt51514QWindowIcon），"
                "而 wxauto4 要找的是 mmui::MainWindow —— 这是微信的**无障碍闸门**没有被激活，"
                "不是版本不兼容。执行 wechat_uia_wake.py wake 可热激活（无需重启微信）；"
                "直接跑 wechat_summary.py 或 run.bat 时本工具会自动热激活并重试",
            )
        return

    try:
        info = wx.ChatInfo()
        record("OK", "实时连接", f"成功读取当前窗口信息：{info}")
    except Exception as exc:
        record("WARN", "实时连接", f"窗口已建立但读取信息失败：{exc}")

    try:
        messages = wx.GetAllMessage() or []
        record("OK", "消息读取", f"当前窗口读到 {len(messages)} 条消息")
        for msg in messages[-3:]:
            record(
                "INFO",
                "消息样例",
                f"attr={getattr(msg, 'attr', None)!r} "
                f"type={getattr(msg, 'type', None)!r} "
                f"sender={getattr(msg, 'sender', None)!r} "
                f"content={str(getattr(msg, 'content', ''))[:40]!r}",
            )

        # 用真实消息校验本项目的分类/渲染假设（只读，不发送任何东西）
        try:
            import wechat_summary as core

            records = [core.classify_message(m) for m in messages]
            counts = {}
            for rec in records:
                counts[rec.kind] = counts.get(rec.kind, 0) + 1
            record("OK", "消息分类", f"kind 统计 = {counts}")
            _, lines = core._render(records)
            record("INFO", "渲染样例", str(lines[:3]))
        except Exception as exc:
            record("WARN", "消息分类", f"无法校验：{exc}")
    except Exception as exc:
        record("WARN", "消息读取", f"GetAllMessage() 失败：{exc}")


# ---------------------------------------------------------------- main


def main() -> int:
    parser = argparse.ArgumentParser(description="微信群聊总结助手 —— 环境自检")
    parser.add_argument(
        "--live",
        action="store_true",
        help="额外真实连接一次微信窗口（需微信已启动并登录）",
    )
    args = parser.parse_args()

    print("=" * 68)
    print("微信群聊总结助手 —— 环境自检（wxauto4 / 微信 4.x 适配版）")
    print("=" * 68)

    check_python()
    check_packages()
    check_wxauto_api()
    check_accessibility_gate()
    check_wechat()

    if args.live:
        check_live()

    print()
    print("=" * 68)
    failed = [r for r in results if r[0] == "FAIL"]
    warned = [r for r in results if r[0] == "WARN"]

    if failed:
        print(f"结论：发现 {len(failed)} 个阻断问题，请先按上面的 [FAIL] 修复。")
        for _, item, detail in failed:
            print(f"  - {item}: {detail}")
    elif warned:
        print(f"结论：环境可用，但有 {len(warned)} 条提醒（[WARN]）。")
    else:
        print("结论：环境检查全部通过，可以运行 run.bat 启动界面。")

    print("=" * 68)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
