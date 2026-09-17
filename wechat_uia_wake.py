# -*- coding: utf-8 -*-
"""让微信 4.1+ 的 UI Automation 树“物化”，使 wxauto4 能找到 ``mmui::MainWindow``。

问题现象
--------
微信 4.1.x 里，wxauto4 会报 ``未找到已登录的客户端主窗口``。原因是：

* wxauto4 用 Win32 窗口类 ``Qt51514QWindowIcon`` 找到微信主窗口（这一步是成功的）；
* 然后要求该窗口在 UI Automation 里的 ``ClassName == 'mmui::MainWindow'``
  （见 ``wxauto4/ui/main.py`` 的 ``WeChatMainWnd._ui_cls_name``）；
* 而微信 4.x 是自绘 UI，Qt **只在检测到无障碍客户端时才暴露完整控件树**。
  微信把这个状态缓存在 ``Weixin.dll`` 里的一个运行时字节（下称“无障碍闸门字节”）。
  冷启动没启用读屏时该字节为 0，``WM_GETOBJECT`` 只能拿到 Qt 空壳
  （类名就是 ``Qt51514QWindowIcon``，子节点几乎为空）→ 校验失败 → 报错。

为什么不是“微信版本太新”
------------------------
同一个微信版本，不同机器上可能一个好一个坏——取决于启动时无障碍是否被激活。
因此**降级微信并不是唯一解**，甚至不是最优解。

解决办法
--------
把闸门字节置 1，让当前进程**立即**返回 mmui provider：

1. 置系统“存在屏幕阅读器”标志（``SystemParametersInfoW(SPI_SETSCREENREADER, 1, ...)``）；
2. ``OpenProcess`` 拿到加载了 ``Weixin.dll`` 的那个 PID；
3. 在 ``Weixin.dll`` 基址 + 闸门 RVA 处写入 1 字节；
4. **校验** ``mmui::`` 控件是否真的出现；没出现就回滚该字节并试下一个候选 RVA。

不注入代码、不重启微信、不改微信文件。写入的这 1 个字节本来就是系统读屏器
会让 Qt 去写的状态位；微信重启后该字节自然回到 0。

闸门 RVA 的取得
---------------
优先用下面这张**实测版本表**（含本项目实测环境 4.1.13.65）；命中不了就扫描
``Weixin.dll``：找 ``48 85 C9 0F 84 ?? ?? ?? ?? 80 3D <disp32> 00 0F 84``
形态的 RIP 相对比较指令，反解出被比较的写权限数据地址，并按“是否靠近
``qt.accessibility.core`` 字符串的引用”排序。每次写入都会用“mmui 树是否
物化”来校验，所以错的候选项会被自动回滚，不会留下副作用。

来源与致谢
----------
本模块的机制来自公开的社区实测，非本项目原创：

* ``wechatauto-replica`` 的 ``wechatauto/uia_driver.py``
  —— <https://github.com/fanyuantaier/wechatauto-replica>
  （其中 ``QACCESSIBLE_ACTIVE_RVA_BY_VERSION`` 明确记录了 ``4.1.13.65``）
* 《关于 Weixin 4.1 以上版本 Qt 框架问题的研究》
  —— <https://www.vidibot.com/2115.html>
* 《微信 4.1.5.X、UIAutomation、UI 树恢复》
  —— <https://blog.csdn.net/WWW7530471/article/details/156270059>

命令行用法::

    .venv\\Scripts\\python.exe wechat_uia_wake.py status    # 只检查，不写任何东西
    .venv\\Scripts\\python.exe wechat_uia_wake.py wake      # 执行热激活
    .venv\\Scripts\\python.exe wechat_uia_wake.py restore   # 关闭读屏标志（可逆）

风险提示
--------
* 这是**向另一个进程写入 1 个字节**。虽然写入的是 Qt 自己也会写的布尔状态位，
  但某些杀毒/EDR 可能对 ``WriteProcessMemory`` 行为告警；
* 校验失败会自动回滚；成功时可用 ``restore`` 关掉系统读屏标志
  （闸门字节随微信重启自然复位）；
* 若你不接受该做法，替代方案是把微信降级到 wxauto4 官方标注支持的 4.1.8.107
  及以下，或用 ``QT_OPENGL=software`` 启动微信（见项目 README）。
"""

from __future__ import annotations

import ctypes
import os
import re
import struct
import time
from ctypes import wintypes
from typing import Dict, List, Optional, Tuple

# --------------------------------------------------------------------------- #
# 常量
# --------------------------------------------------------------------------- #

SPI_GETSCREENREADER = 0x0046
SPI_SETSCREENREADER = 0x0047
SPIF_SENDCHANGE = 0x02

#: 实测过的无障碍闸门 RVA（Weixin.dll 相对偏移）。键是版本目录名。
QACCESSIBLE_ACTIVE_RVA_BY_VERSION: Dict[str, int] = {
    "4.1.11.22": 0x0A1E7DB8,
    "4.1.13.65": 0x0AE2B0C8,   # 本项目实测环境即为该版本
}

QACCESSIBLE_CORE_STRING = b"qt.accessibility.core"
QACCESSIBLE_GATE_PATTERN = re.compile(
    rb"\x48\x85\xc9\x0f\x84....\x80\x3d(?P<disp>.{4})\x00\x0f\x84",
    re.DOTALL,
)

IMAGE_SCN_MEM_EXECUTE = 0x20000000
IMAGE_SCN_MEM_WRITE = 0x80000000

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_OPERATION = 0x0008
PROCESS_VM_READ = 0x0010
PROCESS_VM_WRITE = 0x0020

TH32CS_SNAPMODULE = 0x00000008
TH32CS_SNAPMODULE32 = 0x00000010
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
MAX_MODULE_NAME32 = 255
MAX_PATH = 260

WECHAT_EXE_NAMES = ("weixin.exe", "wechat.exe")
WECHAT_WINDOW_TITLES = ("微信", "Weixin", "WeChat")


class MODULEENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", ctypes.c_ulong),
        ("th32ModuleID", ctypes.c_ulong),
        ("th32ProcessID", ctypes.c_ulong),
        ("GlblcntUsage", ctypes.c_ulong),
        ("ProccntUsage", ctypes.c_ulong),
        ("modBaseAddr", ctypes.POINTER(ctypes.c_ubyte)),
        ("modBaseSize", ctypes.c_ulong),
        ("hModule", ctypes.c_void_p),
        ("szModule", ctypes.c_wchar * (MAX_MODULE_NAME32 + 1)),
        ("szExePath", ctypes.c_wchar * MAX_PATH),
    ]


# --------------------------------------------------------------------------- #
# 系统“屏幕阅读器”标志（可逆）
# --------------------------------------------------------------------------- #


def set_screen_reader_flag(enable: bool) -> bool:
    """置/清系统“存在屏幕阅读器”标志。成功返回 True。"""
    try:
        ok = ctypes.windll.user32.SystemParametersInfoW(
            SPI_SETSCREENREADER, 1 if enable else 0, 0, SPIF_SENDCHANGE
        )
        return bool(ok)
    except Exception:
        return False


def get_screen_reader_flag() -> Optional[bool]:
    """读当前系统“存在屏幕阅读器”标志；失败返回 None。"""
    try:
        value = wintypes.BOOL()
        ok = ctypes.windll.user32.SystemParametersInfoW(
            SPI_GETSCREENREADER, 0, ctypes.byref(value), 0
        )
        return bool(value.value) if ok else None
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# 进程 / 模块 / 窗口
# --------------------------------------------------------------------------- #


def _module_entries(pid: int):
    """枚举指定 PID 的模块，产出 (基址, 大小, 模块名, 路径)。"""
    kernel32 = ctypes.windll.kernel32
    snapshot = kernel32.CreateToolhelp32Snapshot(
        TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pid
    )
    if snapshot == INVALID_HANDLE_VALUE:
        return
    try:
        entry = MODULEENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        if not kernel32.Module32FirstW(snapshot, ctypes.byref(entry)):
            return
        while True:
            base = ctypes.cast(entry.modBaseAddr, ctypes.c_void_p).value or 0
            yield base, int(entry.modBaseSize), entry.szModule, entry.szExePath
            if not kernel32.Module32NextW(snapshot, ctypes.byref(entry)):
                break
    finally:
        kernel32.CloseHandle(snapshot)


def weixin_dll_module(pid: int) -> Optional[Tuple[int, int, str]]:
    """返回该 PID 里 Weixin.dll 的 (基址, 大小, 磁盘路径)。"""
    for base, size, name, path in _module_entries(pid) or ():
        if (name or "").lower() == "weixin.dll":
            return base, size, path
    return None


def wechat_window_handles() -> List[Dict[str, object]]:
    """枚举当前可见的微信顶层窗口。

    只保留**加载了 Weixin.dll** 的进程窗口：微信有若干辅助进程也带
    ``Weixin.exe`` 名字但没有该 DLL，对它们热激活必然失败，只会产生噪音。
    """
    user32 = ctypes.windll.user32
    found: List[Dict[str, object]] = []
    seen = set()

    def _callback(hwnd, _lparam):
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value
            if title not in WECHAT_WINDOW_TITLES:
                return True

            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            pid = int(pid.value)
            if pid in seen:
                return True
            module = weixin_dll_module(pid)
            if not module:
                return True
            seen.add(pid)
            found.append(
                {
                    "hwnd": hwnd,
                    "pid": pid,
                    "title": title,
                    "minimized": bool(user32.IsIconic(hwnd)),
                    "dll_base": module[0],
                    "dll_path": module[2],
                }
            )
        except Exception:
            pass
        return True

    try:
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        user32.EnumWindows(callback_type(_callback), 0)
    except Exception:
        return []
    return found


# --------------------------------------------------------------------------- #
# UIA 树是否物化
# --------------------------------------------------------------------------- #


def _uia():
    """拿到 uiautomation 模块（优先用 wxauto4 自带的那份）。"""
    try:
        from wxauto4.uia import uiautomation as auto  # type: ignore

        return auto
    except Exception:
        pass
    try:
        import uiautomation as auto  # type: ignore

        return auto
    except Exception:
        return None


def mmui_present(hwnd: int, timeout: float = 1.0) -> bool:
    """该窗口是否已物化出 ``mmui::`` 控件（仍是 Qt 空壳时为 False）。"""
    auto = _uia()
    if auto is None:
        return False

    deadline = time.time() + max(0.1, timeout)
    while True:
        control = None
        try:
            control = auto.ControlFromHandle(hwnd)
        except Exception:
            control = None
        if control is not None:
            try:
                if (control.ClassName or "").startswith("mmui::"):
                    return True
            except Exception:
                pass
            try:
                children = control.GetChildren()
            except Exception:
                children = []
            for child in children:
                try:
                    if (child.ClassName or "").startswith("mmui::"):
                        return True
                except Exception:
                    continue
        if time.time() >= deadline:
            return False
        time.sleep(0.2)


def describe_uia(hwnd: int) -> Dict[str, object]:
    """读取该窗口的 UIA 顶层类名与子节点情况，用于诊断。"""
    auto = _uia()
    if auto is None:
        return {"error": "uiautomation 不可用"}
    try:
        control = auto.ControlFromHandle(hwnd)
    except Exception as exc:
        return {"error": repr(exc)}
    if control is None:
        return {"error": "ControlFromHandle 返回 None"}
    try:
        class_name = control.ClassName or ""
        name = control.Name or ""
    except Exception as exc:
        return {"error": repr(exc)}
    classes: List[str] = []
    try:
        for child in control.GetChildren():
            classes.append(child.ClassName or "")
    except Exception:
        pass
    return {
        "class_name": class_name,
        "name": name,
        "children": classes,
        "mmui": class_name.startswith("mmui::"),
    }


# --------------------------------------------------------------------------- #
# Weixin.dll 扫描：找出闸门候选项
# --------------------------------------------------------------------------- #


def _pe_sections(data: bytes) -> List[dict]:
    try:
        pe_offset = struct.unpack_from("<I", data, 0x3C)[0]
        if data[pe_offset:pe_offset + 4] != b"PE\0\0":
            return []
        coff = pe_offset + 4
        count = struct.unpack_from("<H", data, coff + 2)[0]
        optional_size = struct.unpack_from("<H", data, coff + 16)[0]
        section_offset = coff + 20 + optional_size
        sections = []
        for index in range(count):
            offset = section_offset + index * 40
            name = data[offset:offset + 8].split(b"\0", 1)[0].decode("ascii", "ignore")
            virtual_size, virtual_address, raw_size, raw_ptr = struct.unpack_from(
                "<IIII", data, offset + 8
            )
            characteristics = struct.unpack_from("<I", data, offset + 36)[0]
            sections.append(
                {
                    "name": name,
                    "rva": virtual_address,
                    "vsize": virtual_size,
                    "raw_size": raw_size,
                    "raw_ptr": raw_ptr,
                    "chars": characteristics,
                }
            )
        return sections
    except Exception:
        return []


def _section_for_rva(sections: List[dict], rva: int) -> Optional[dict]:
    for section in sections:
        size = max(section["vsize"], section["raw_size"])
        if section["rva"] <= rva < section["rva"] + size:
            return section
    return None


def _offset_to_rva(sections: List[dict], offset: int) -> Optional[int]:
    for section in sections:
        if section["raw_ptr"] <= offset < section["raw_ptr"] + section["raw_size"]:
            return section["rva"] + offset - section["raw_ptr"]
    return None


def _rip_xrefs_to_rva(data: bytes, sections: List[dict], target_rva: int) -> List[int]:
    """找出所有 RIP 相对寻址到 target_rva 的指令 RVA（用于按可信度排序）。"""
    xrefs: List[int] = []
    for section in sections:
        if not (section["chars"] & IMAGE_SCN_MEM_EXECUTE):
            continue
        start = section["raw_ptr"]
        end = min(len(data), start + section["raw_size"])
        raw = data[start:end]
        for index in range(0, max(0, len(raw) - 7)):
            # lea reg, [rip+disp32] -> 48/4C 8D 05|0D|15|1D...
            if 0x48 <= raw[index] <= 0x4F and raw[index + 1] == 0x8D and (raw[index + 2] & 0xC7) == 0x05:
                disp = struct.unpack_from("<i", raw, index + 3)[0]
                insn_rva = section["rva"] + index
                if insn_rva + 7 + disp == target_rva:
                    xrefs.append(insn_rva)
            # lea reg32, [rip+disp32] -> 8D 05|0D|15|1D...
            if raw[index] == 0x8D and (raw[index + 1] & 0xC7) == 0x05:
                disp = struct.unpack_from("<i", raw, index + 2)[0]
                insn_rva = section["rva"] + index
                if insn_rva + 6 + disp == target_rva:
                    xrefs.append(insn_rva)
    return xrefs


def _scan_gate_candidates(dll_path: str) -> Tuple[int, ...]:
    """扫描 Weixin.dll，按可信度返回闸门 RVA 候选。"""
    try:
        with open(dll_path, "rb") as handle:
            data = handle.read()
    except OSError:
        return ()

    sections = _pe_sections(data)
    if not sections:
        return ()

    core_offset = data.find(QACCESSIBLE_CORE_STRING)
    core_rva = _offset_to_rva(sections, core_offset) if core_offset >= 0 else None
    core_xrefs = _rip_xrefs_to_rva(data, sections, core_rva) if core_rva is not None else []

    candidates: List[Tuple[int, int]] = []
    for match in QACCESSIBLE_GATE_PATTERN.finditer(data):
        match_rva = _offset_to_rva(sections, match.start())
        disp_rva = _offset_to_rva(sections, match.start("disp"))
        if match_rva is None or disp_rva is None:
            continue
        match_section = _section_for_rva(sections, match_rva)
        if not match_section or not (match_section["chars"] & IMAGE_SCN_MEM_EXECUTE):
            continue

        cmp_rva = disp_rva - 2  # 80 3D <disp32> 00
        disp = struct.unpack("<i", match.group("disp"))[0]
        target_rva = cmp_rva + 7 + disp
        target_section = _section_for_rva(sections, target_rva)
        if not target_section or not (target_section["chars"] & IMAGE_SCN_MEM_WRITE):
            continue

        if core_xrefs:
            distance = min(abs(match_rva - xref) for xref in core_xrefs)
        else:
            distance = 0x7FFFFFFF
        candidates.append((distance, target_rva))

    if not candidates:
        return ()
    candidates.sort(key=lambda item: item[0])
    near = [rva for distance, rva in candidates if distance <= 0x20000]
    ordered = near or [rva for _distance, rva in candidates]
    return tuple(dict.fromkeys(ordered))


def version_of_dll_path(dll_path: str) -> str:
    """从 ``...\\<版本号>\\Weixin.dll`` 取出版本号字符串。"""
    return os.path.basename(os.path.dirname(dll_path))


def candidate_gate_rvas(dll_path: str) -> List[int]:
    """闸门 RVA 候选序列：实测版本表（最快）→ DLL 扫描结果。"""
    ordered: List[int] = []

    version = version_of_dll_path(dll_path)
    known = QACCESSIBLE_ACTIVE_RVA_BY_VERSION.get(version)
    if known is not None:
        ordered.append(int(known))

    for rva in _scan_gate_candidates(dll_path):
        if int(rva) not in ordered:
            ordered.append(int(rva))
    return ordered


# --------------------------------------------------------------------------- #
# 进程内存读写
# --------------------------------------------------------------------------- #


def _open_process(pid: int):
    access = (
        PROCESS_QUERY_INFORMATION | PROCESS_VM_READ | PROCESS_VM_WRITE | PROCESS_VM_OPERATION
    )
    kernel32 = ctypes.windll.kernel32
    return kernel32.OpenProcess(access, False, pid)


def _read_process_byte(handle, address: int) -> Optional[int]:
    buf = (ctypes.c_ubyte * 1)()
    read = ctypes.c_size_t(0)
    ok = ctypes.windll.kernel32.ReadProcessMemory(
        handle, ctypes.c_void_p(address), buf, 1, ctypes.byref(read)
    )
    return int(buf[0]) if ok and read.value == 1 else None


def _write_process_byte(handle, address: int, value: int) -> bool:
    buf = (ctypes.c_ubyte * 1)(value & 0xFF)
    written = ctypes.c_size_t(0)
    ok = ctypes.windll.kernel32.WriteProcessMemory(
        handle, ctypes.c_void_p(address), buf, 1, ctypes.byref(written)
    )
    return bool(ok and written.value == 1)


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #


def wake(hwnd: Optional[int] = None, verify: bool = True, log=print) -> Dict[str, object]:
    """对微信进程做无障碍热激活，返回结构化结果。

    :param hwnd: 指定窗口句柄；不传则自动挑一个可见的微信主窗口
    :param verify: 写入后是否用“mmui 树是否物化”校验（建议开启，失败会自动回滚）
    :return: ``{ok, hwnd, pid, rva, version, action, detail}``
    """
    result: Dict[str, object] = {
        "ok": False,
        "hwnd": hwnd,
        "pid": None,
        "rva": None,
        "version": None,
        "action": None,
        "detail": "",
    }

    windows = wechat_window_handles()
    if not windows:
        result["detail"] = "未找到加载了 Weixin.dll 的可见微信窗口（微信是否已启动并登录？）"
        return result

    if hwnd is not None:
        target = next((w for w in windows if w["hwnd"] == hwnd), None)
    else:
        # 优先取未最小化、面积最大的那个
        target = next((w for w in windows if not w["minimized"]), windows[0])
    if target is None:
        result["detail"] = f"句柄 {hwnd} 不属于任何微信主窗口"
        return result

    result["hwnd"] = target["hwnd"]
    result["pid"] = target["pid"]

    pid = int(target["pid"])
    dll_path = str(target["dll_path"])
    dll_base = int(target["dll_base"])
    result["version"] = version_of_dll_path(dll_path)

    # 已经物化就不用动内存
    if mmui_present(int(target["hwnd"]), timeout=0.5):
        result.update(ok=True, action="already", detail="mmui 控件树已存在，无需热激活")
        return result

    # 1) 置系统读屏标志（本来就是读屏器会做的事）
    flag_ok = set_screen_reader_flag(True)
    result["action"] = "screen_reader_flag" if flag_ok else None

    # 有些机器仅凭该标志即可让树物化，先给它一点时间
    if mmui_present(int(target["hwnd"]), timeout=1.5):
        result.update(ok=True, action="screen_reader_flag",
                      detail="仅置系统读屏标志后 mmui 树即已物化")
        return result

    # 2) 热写闸门字节
    candidates = candidate_gate_rvas(dll_path)
    if not candidates:
        result["detail"] = (
            "无法确定无障碍闸门 RVA：版本表未收录 %s，且 DLL 扫描未命中候选。" % result["version"]
        )
        return result

    handle = _open_process(pid)
    if not handle:
        result["detail"] = (
            "OpenProcess 失败（PID=%s）。可能需要以相同权限运行本程序（微信若以管理员"
            "身份运行，本程序也需要）。" % pid
        )
        return result

    tried: List[str] = []
    try:
        for rva in candidates:
            address = dll_base + int(rva)
            current = _read_process_byte(handle, address)
            if current is None:
                tried.append("0x%X(读失败)" % rva)
                continue

            wrote = False
            if current != 1:
                if not _write_process_byte(handle, address, 1):
                    tried.append("0x%X(写失败)" % rva)
                    continue
                wrote = True
            tried.append("0x%X(%s->1)" % (rva, current))

            if not verify:
                result.update(ok=True, rva=int(rva), detail="已写入（未校验）")
                return result

            if mmui_present(int(target["hwnd"]), timeout=2.5):
                result.update(
                    ok=True,
                    rva=int(rva),
                    action="hot_write_gate",
                    detail="热写闸门字节后 mmui 树已物化（无需重启微信）",
                )
                return result

            if wrote:  # 候选不对，回滚，避免留下副作用
                _write_process_byte(handle, address, current)
                tried[-1] = "0x%X(写入后未生效，已回滚)" % rva
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)

    result["detail"] = "已尝试候选但 mmui 树仍未物化：" + "，".join(tried)
    return result


def status() -> Dict[str, object]:
    """只读诊断：窗口、DLL、UIA 类名、候选 RVA。不写任何内存。"""
    report: Dict[str, object] = {
        "screen_reader_flag": get_screen_reader_flag(),
        "has_uia": _uia() is not None,
        "windows": [],
    }
    for window in wechat_window_handles():
        info: Dict[str, object] = dict(window)
        info.pop("dll_base", None)
        info["uia"] = describe_uia(int(window["hwnd"]))
        candidates = candidate_gate_rvas(str(window["dll_path"]))
        info["gate_rva_candidates"] = ["0x%X" % rva for rva in candidates[:6]]
        report["windows"].append(info)
    return report


# --------------------------------------------------------------------------- #
# 命令行
# --------------------------------------------------------------------------- #


def _print_report(data: Dict[str, object]) -> None:
    import json

    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(argv if argv is not None else __import__("sys").argv[1:])
    action = argv[0] if argv else "status"

    try:  # pragma: no cover - 终端编码
        __import__("sys").stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    if action == "status":
        _print_report(status())
        return 0

    if action == "wake":
        outcome = wake()
        _print_report(outcome)
        return 0 if outcome.get("ok") else 1

    if action == "restore":
        ok = set_screen_reader_flag(False)
        print("已清除系统读屏标志：%s" % ok)
        print("说明：闸门字节随微信重启自然复位；若仍想在当前会话恢复，重启微信即可。")
        return 0 if ok else 1

    print("用法：python wechat_uia_wake.py [status|wake|restore]")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
