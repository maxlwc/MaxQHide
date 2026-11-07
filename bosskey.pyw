# -*- coding: utf-8 -*-
"""
MaxQHide —— Windows 11 老板键工具

功能：
  * 全局热键一键隐藏 / 唤醒配置好的游戏或任意程序
  * 隐藏后程序继续运行，任务栏不再显示图标
  * 隐藏期间程序新弹出的窗口会被自动隐藏
  * 托盘图标(眼睛开/合表示状态) + 设置界面，程序列表、热键均可自定义
  * 支持命令行 --list / --hide / --show

仅使用 Python 标准库(ctypes + tkinter)，无需安装第三方依赖。
"""
import ctypes
import json
import os
import queue
import sys
import threading
import time
from ctypes import wintypes as wt
from dataclasses import dataclass

import tkinter as tk
from tkinter import ttk, messagebox

APP_NAME = "MaxQHide"
APP_VERSION = "1.0.0"
APPQ = queue.Queue()          # 线程 -> GUI 线程 的命令队列

DEBUG_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "maxqhide_debug.log")


def dbg(msg):
    with open(DEBUG_LOG, "a", encoding="utf-8") as f:
        f.write("%.3f %s\n" % (time.time(), msg))

# ---------------------------------------------------------------- win32 基础

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)

WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_COMMAND = 0x0111
WM_HOTKEY = 0x0312
WM_APP = 0x8000
WM_TRAYICON = WM_APP + 1        # 托盘回调消息
TM_UPDATE_ICON = WM_APP + 6     # 托盘图标状态更新(内部)
TM_SHOW_SETTINGS = WM_APP + 7   # 第二个实例请求打开设置
HKM_RECONFIG = WM_APP + 2       # 热键线程: 重新注册
HKM_QUIT = WM_APP + 3           # 热键线程: 退出
HKM_SUSPEND = WM_APP + 4        # 热键线程: 暂停注册(捕获新热键期间)
HKM_RESUME = WM_APP + 5         # 热键线程: 恢复注册

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN = 0x1, 0x2, 0x4, 0x8
MOD_NOREPEAT = 0x4000
SW_HIDE, SW_SHOW = 0, 5
GW_OWNER = 4
DWMWA_CLOAKED = 14
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
HWND_MESSAGE = wt.HWND(-3)
ERROR_ALREADY_EXISTS = 183
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP = 1, 2, 4
TPM_RIGHTBUTTON, TPM_RETURNCMD, TPM_NONOTIFY = 0x2, 0x100, 0x80
MF_STRING, MF_SEPARATOR = 0x0, 0x800
WM_RBUTTONUP, WM_LBUTTONUP, WM_LBUTTONDBLCLK = 0x0205, 0x0204, 0x0203

WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)

user32.SetProcessDPIAware()
user32.EnumWindows.argtypes = (WNDENUMPROC, wt.LPARAM)
user32.GetWindowTextLengthW.argtypes = (wt.HWND,)
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = (wt.HWND, wt.LPWSTR, ctypes.c_int)
user32.GetClassNameW.argtypes = (wt.HWND, wt.LPWSTR, ctypes.c_int)
user32.GetWindowThreadProcessId.argtypes = (wt.HWND, ctypes.POINTER(wt.DWORD))
user32.GetWindowThreadProcessId.restype = wt.DWORD
user32.IsWindowVisible.argtypes = (wt.HWND,)
user32.IsWindow.argtypes = (wt.HWND,)
user32.ShowWindow.argtypes = (wt.HWND, ctypes.c_int)
user32.SetForegroundWindow.argtypes = (wt.HWND,)
user32.GetWindow.argtypes = (wt.HWND, wt.UINT)
user32.GetWindow.restype = wt.HWND
user32.FindWindowW.argtypes = (wt.LPCWSTR, wt.LPCWSTR)
user32.FindWindowW.restype = wt.HWND
user32.RegisterHotKey.argtypes = (wt.HWND, ctypes.c_int, wt.UINT, wt.UINT)
user32.UnregisterHotKey.argtypes = (wt.HWND, ctypes.c_int)
user32.GetMessageW.argtypes = (ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT)
user32.GetMessageW.restype = ctypes.c_int
user32.DispatchMessageW.argtypes = (ctypes.POINTER(wt.MSG),)
user32.DispatchMessageW.restype = LRESULT
user32.PostThreadMessageW.argtypes = (wt.DWORD, wt.UINT, wt.WPARAM, wt.LPARAM)
user32.PostMessageW.argtypes = (wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
user32.DefWindowProcW.argtypes = (wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
user32.DefWindowProcW.restype = LRESULT
user32.CreateWindowExW.argtypes = (
    wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID)
user32.CreateWindowExW.restype = wt.HWND
user32.DestroyWindow.argtypes = (wt.HWND,)
user32.RegisterClassW.argtypes = (ctypes.c_void_p,)
user32.RegisterClassW.restype = wt.WORD
user32.GetCursorPos.argtypes = (ctypes.POINTER(wt.POINT),)
user32.CreatePopupMenu.restype = wt.HMENU
user32.AppendMenuW.argtypes = (wt.HMENU, wt.UINT, ctypes.c_size_t, wt.LPCWSTR)
user32.TrackPopupMenu.argtypes = (
    wt.HMENU, wt.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.HWND, ctypes.c_void_p)
user32.TrackPopupMenu.restype = ctypes.c_int
user32.DestroyMenu.argtypes = (wt.HMENU,)
user32.MessageBoxW.argtypes = (wt.HWND, wt.LPCWSTR, wt.LPCWSTR, wt.UINT)
user32.GetDC.restype = wt.HDC
user32.ReleaseDC.argtypes = (wt.HWND, wt.HDC)
user32.CreateIconIndirect.argtypes = (ctypes.c_void_p,)
user32.CreateIconIndirect.restype = wt.HICON
user32.GetAsyncKeyState.argtypes = (ctypes.c_int,)
user32.GetAsyncKeyState.restype = ctypes.c_short

kernel32.OpenProcess.argtypes = (wt.DWORD, wt.BOOL, wt.DWORD)
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = (
    wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD))
kernel32.QueryFullProcessImageNameW.restype = wt.BOOL
kernel32.CloseHandle.argtypes = (wt.HANDLE,)
kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, wt.BOOL, wt.LPCWSTR)
kernel32.CreateMutexW.restype = wt.HANDLE
kernel32.GetModuleHandleW.argtypes = (wt.LPCWSTR,)
kernel32.GetModuleHandleW.restype = wt.HINSTANCE
kernel32.GetCurrentThreadId.restype = wt.DWORD

dwmapi.DwmGetWindowAttribute.argtypes = (wt.HWND, wt.DWORD, ctypes.c_void_p, wt.DWORD)
dwmapi.DwmGetWindowAttribute.restype = ctypes.HRESULT

gdi32.CreateBitmap.restype = wt.HBITMAP
gdi32.CreateDIBSection.argtypes = (
    wt.HDC, ctypes.c_void_p, wt.UINT,
    ctypes.POINTER(ctypes.c_void_p), wt.HANDLE, wt.DWORD)
gdi32.CreateDIBSection.restype = wt.HBITMAP
gdi32.DeleteObject.argtypes = (wt.HANDLE,)

shell32.Shell_NotifyIconW.argtypes = (wt.UINT, ctypes.c_void_p)
shell32.Shell_NotifyIconW.restype = wt.BOOL
shell32.SHGetFolderPathW.argtypes = (wt.HWND, ctypes.c_int, wt.HANDLE, wt.UINT, wt.LPWSTR)
shell32.SHGetFolderPathW.restype = ctypes.HRESULT


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.UINT), ("hWnd", wt.HWND), ("uID", wt.UINT),
        ("uFlags", wt.UINT), ("uCallbackMessage", wt.UINT), ("hIcon", wt.HICON),
        ("szTip", wt.WCHAR * 128), ("dwState", wt.DWORD), ("dwStateMask", wt.DWORD),
        ("szInfo", wt.WCHAR * 256), ("uVersion", wt.UINT),
        ("szInfoTitle", wt.WCHAR * 64), ("dwInfoFlags", wt.DWORD),
        ("guidItem", ctypes.c_char * 16), ("hBalloonIcon", wt.HICON),
    ]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wt.UINT), ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
        ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON), ("hCursor", wt.HANDLE),
        ("hbrBackground", wt.HBRUSH), ("lpszMenuName", wt.LPCWSTR),
        ("lpszClassName", wt.LPCWSTR),
    ]


class ICONINFO(ctypes.Structure):
    _fields_ = [
        ("fIcon", wt.BOOL), ("xHotspot", wt.DWORD), ("yHotspot", wt.DWORD),
        ("hbmMask", wt.HBITMAP), ("hbmColor", wt.HBITMAP),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wt.UINT), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
        ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
        ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG),
        ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
        ("biClrImportant", wt.DWORD),
    ]


# ---------------------------------------------------------------- 窗口枚举 / 进程

@dataclass
class WinInfo:
    hwnd: int
    pid: int
    title: str
    cls: str
    exe: str


def get_process_name(pid):
    """返回进程可执行文件名，如 notepad.exe；失败返回空串。"""
    if not pid:
        return ""
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wt.DWORD(1024)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value)
        return ""
    finally:
        kernel32.CloseHandle(h)


def is_cloaked(hwnd):
    """UWP/虚拟桌面上的"隐形"窗口(cloaked)，隐藏/查找时应忽略。"""
    val = wt.DWORD(0)
    if dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(val), 4) == 0:
        return val.value != 0
    return False


def enum_windows():
    res = []
    own_pid = os.getpid()

    def cb(hwnd, _lparam):
        try:
            pid = wt.DWORD(0)
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == own_pid:
                # 本进程窗口必须跳过：对同进程窗口的文本查询是同步 SendMessage，
                # 若 GUI 线程正阻塞等待 Core.lock 会形成互等死锁
                return True
            buf = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(hwnd, buf, 512)   # 跨进程读缓存标题，不会阻塞
            cbuf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cbuf, 256)
            res.append(WinInfo(hwnd, pid.value, buf.value, cbuf.value,
                               get_process_name(pid.value)))
        except Exception:
            pass
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return res


# ---------------------------------------------------------------- 配置

DEFAULT_CONFIG = {
    "version": 1,
    "hide_hotkey": "ctrl+alt+h",
    "show_hotkey": "ctrl+alt+s",
    "hide_gui": True,              # 按老板键时同时隐藏本设置窗口
    "monitor_new_windows": True,   # 隐藏期间自动隐藏新弹出的匹配窗口
    "autostart": False,
    "programs": [
        {"id": 1, "name": "示例：记事本", "match_type": "process",
         "match_value": "notepad.exe", "enabled": True},
    ],
}


def config_paths():
    base = os.path.dirname(os.path.abspath(__file__))
    return (os.path.join(base, "config.json"),
            os.path.join(os.environ.get("APPDATA", base), "MaxQHide", "config.json"))


def load_cfg():
    primary, fallback = config_paths()
    for path in (primary, fallback):
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                merged = dict(DEFAULT_CONFIG)
                merged.update(cfg)
                merged.setdefault("programs", [])
                return merged, path
            except Exception:
                pass
    cfg = dict(DEFAULT_CONFIG)
    cfg["programs"] = [dict(p) for p in DEFAULT_CONFIG["programs"]]
    path = primary
    try:
        os.makedirs(os.path.dirname(primary), exist_ok=True)
        save_cfg(cfg, primary)
    except OSError:
        path = fallback
        os.makedirs(os.path.dirname(fallback), exist_ok=True)
        save_cfg(cfg, fallback)
    return cfg, path


def save_cfg(cfg, path):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def get_startup_bat_path():
    buf = ctypes.create_unicode_buffer(260)
    if shell32.SHGetFolderPathW(None, 2, None, 0, buf) != 0:
        return None
    return os.path.join(buf.value, "MaxQHide.bat")


def set_autostart(on):
    """开机自启：向 shell:startup 写入/删除一个启动 bat。"""
    bat = get_startup_bat_path()
    if not bat:
        return False
    try:
        if on:
            exe = sys.executable
            if os.path.basename(exe).lower() == "python.exe":
                exe = os.path.join(os.path.dirname(exe), "pythonw.exe")
            script = os.path.abspath(__file__)
            with open(bat, "w", encoding="utf-8") as f:
                f.write('@echo off\r\nstart "" "{}" "{}"\r\n'.format(exe, script))
        elif os.path.isfile(bat):
            os.remove(bat)
        return True
    except OSError:
        return False


# ---------------------------------------------------------------- 热键解析

MOD_NAMES = {"ctrl": MOD_CONTROL, "control": MOD_CONTROL, "alt": MOD_ALT,
             "shift": MOD_SHIFT, "win": MOD_WIN}
MOD_ORDER = [("ctrl", MOD_CONTROL), ("alt", MOD_ALT),
             ("shift", MOD_SHIFT), ("win", MOD_WIN)]
VK_SPECIAL = {
    "space": 0x20, "tab": 0x09, "enter": 0x0D, "return": 0x0D, "esc": 0x1B,
    "escape": 0x1B, "backspace": 0x08, "delete": 0x2E, "del": 0x2E,
    "insert": 0x2D, "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28, "capslock": 0x14,
    "printscreen": 0x2C, "minus": 0xBD, "plus": 0xBB, ",": 0xBC, ".": 0xBE,
    "`": 0xC0, "[": 0xDB, "]": 0xDD, ";": 0xBA, "'": 0xDE, "/": 0xBF, "\\": 0xDC,
}
VK_SPECIAL_NAMES = {
    0x20: "space", 0x09: "tab", 0x0D: "enter", 0x1B: "esc", 0x08: "backspace",
    0x2E: "delete", 0x2D: "insert", 0x24: "home", 0x23: "end",
    0x21: "pageup", 0x22: "pagedown", 0x25: "left", 0x26: "up",
    0x27: "right", 0x28: "down", 0x14: "capslock", 0x2C: "printscreen",
    0xBD: "minus", 0xBB: "plus", 0xBC: ",", 0xBE: ".", 0xC0: "`",
    0xDB: "[", 0xDD: "]", 0xBA: ";", 0xDE: "'", 0xBF: "/", 0xDC: "\\",
}


def parse_hotkey(s):
    """'ctrl+alt+h' -> (mods, vk)；无效返回 None。必须至少含一个修饰键。"""
    if not s:
        return None
    parts = [p.strip().lower() for p in s.split("+") if p.strip()]
    mods, vk = 0, None
    for p in parts:
        if p in MOD_NAMES:
            mods |= MOD_NAMES[p]
        elif len(p) == 1 and p.isalpha():
            vk = ord(p.upper())
        elif len(p) == 1 and p.isdigit():
            vk = ord(p)
        elif p in VK_SPECIAL:
            vk = VK_SPECIAL[p]
        elif p[0] == "f" and p[1:].isdigit() and 1 <= int(p[1:]) <= 24:
            vk = 0x6F + int(p[1:])
        else:
            return None
    if vk is None or mods == 0:
        return None
    return mods, vk


def vk_name(vk):
    if 0x30 <= vk <= 0x39:
        return chr(vk)
    if 0x41 <= vk <= 0x5A:
        return chr(vk).lower()
    if 0x70 <= vk <= 0x87:
        return "F%d" % (vk - 0x6F)
    return VK_SPECIAL_NAMES.get(vk, "0x%02X" % vk)


def hotkey_display(s):
    parsed = parse_hotkey(s)
    if not parsed:
        return "%s (无效)" % s
    mods, vk = parsed
    names = [m for m, bit in MOD_ORDER if mods & bit]
    return "+".join([n.capitalize() for n in names] + [vk_name(vk)])


# ---------------------------------------------------------------- 核心逻辑：匹配 / 隐藏 / 唤醒

def prog_match(win, prog):
    v = (prog.get("match_value") or "").strip().lower()
    if not v:
        return False
    if prog.get("match_type") == "title":
        return v in win.title.lower()
    exe = win.exe.lower()
    if not exe:
        return False
    if exe == v:
        return True
    # 未写扩展名时自动兼容 xxx -> xxx.exe
    return not v.endswith(".exe") and exe == v + ".exe"


AUX_CLASSES = {"Default IME", "MSCTFIME UI", "IME", "GDI+ Window",
               "GDI+ Hook Window Class", "OleMainThreadWndClass",
               "ClipboardChainListener"}


class Core:
    def __init__(self, cfg_ref):
        self._cfg = cfg_ref            # callable -> cfg dict
        self.lock = threading.RLock()
        self.hidden = {}               # hwnd -> WinInfo（被我们隐藏的窗口）

    def programs(self):
        return [p for p in self._cfg().get("programs", [])
                if p.get("enabled", True)]

    def find(self, visible_only=True, progs=None):
        own = os.getpid()
        progs = self.programs() if progs is None else progs
        out = []
        for w in enum_windows():
            if w.pid == own or w.cls == "MaxQHideTrayWnd" or w.cls in AUX_CLASSES:
                continue
            if visible_only:
                if not user32.IsWindowVisible(w.hwnd):
                    continue
                if is_cloaked(w.hwnd):
                    continue
            if any(prog_match(w, p) for p in progs):
                out.append(w)
        return out

    def find_for(self, prog, visible_only=True):
        return self.find(visible_only=visible_only, progs=[prog])

    def _purge(self):
        for h in [h for h in self.hidden if not user32.IsWindow(h)]:
            self.hidden.pop(h, None)

    def hide_now(self):
        """隐藏所有匹配且可见的窗口，返回本次新隐藏的数量。"""
        targets = self.find(visible_only=True)      # 枚举在锁外
        with self.lock:
            n = 0
            for w in targets:
                if w.hwnd in self.hidden:
                    continue
                user32.ShowWindow(w.hwnd, SW_HIDE)
                self.hidden[w.hwnd] = w
                n += 1
            self._purge()
            return n

    def toggle_prog(self, prog):
        """独立热键：只隐藏/唤醒该规则的窗口。返回 ("hide"/"show", 数量)。"""
        visible = self.find_for(prog, visible_only=True)
        if visible:
            with self.lock:
                n = 0
                for w in visible:
                    user32.ShowWindow(w.hwnd, SW_HIDE)
                    self.hidden[w.hwnd] = w
                    n += 1
                return "hide", n
        candidates = self.find_for(prog, visible_only=False)
        with self.lock:
            self._purge()
            # 无标题的辅助窗口即使曾被记录也保持隐藏（且不再计入）
            for h in [h for h, w in self.hidden.items()
                      if prog_match(w, prog) and not w.title]:
                self.hidden.pop(h, None)
            targets = [h for h, w in self.hidden.items()
                       if prog_match(w, prog) and user32.IsWindow(h)
                       and w.title]
            if not targets:
                # 兜底只挑有标题的窗口：无标题的多为应用内部辅助窗口
                # (如 Electron 骨架窗)，显示出来只会是黑框
                targets = [w.hwnd for w in candidates
                           if w.title and not user32.IsWindowVisible(w.hwnd)]
            first = None
            for h in targets:
                user32.ShowWindow(h, SW_SHOW)
                self.hidden.pop(h, None)
                if first is None:
                    first = h
            if first is not None:
                user32.SetForegroundWindow(first)
            return "show", len(targets)

    def sweep(self):
        """隐藏期间轮询：把处于隐藏状态的那些程序新出现的窗口也藏起来。"""
        with self.lock:
            if not self.hidden:
                return
            snapshot = dict(self.hidden)
            active = [p for p in self.programs()
                      if any(prog_match(w, p) for w in snapshot.values())]
        if not active:
            return
        wins = self.find(visible_only=True, progs=active)   # 枚举在锁外
        with self.lock:
            for w in wins:
                if w.hwnd not in self.hidden:
                    user32.ShowWindow(w.hwnd, SW_HIDE)
                    self.hidden[w.hwnd] = w
            self._purge()

    def show_now(self):
        """唤醒全部被隐藏的窗口。优先使用记录；无记录时回退为按规则枚举。
        只恢复有标题的窗口——无标题的多为应用内部辅助窗口，显示出来是黑框。"""
        with self.lock:
            self._purge()
            titled = [h for h in self.hidden
                      if self.hidden[h].title and user32.IsWindow(h)]
            self.hidden.clear()
        targets = titled
        if not targets:
            wins = self.find(visible_only=False)
            targets = [w.hwnd for w in wins
                       if w.title and not user32.IsWindowVisible(w.hwnd)]
        first = None
        for h in targets:
            user32.ShowWindow(h, SW_SHOW)
            if first is None:
                first = h
        if first is not None:
            user32.SetForegroundWindow(first)
        return len(targets)

    def snapshot_hidden(self):
        with self.lock:
            self._purge()
            return dict(self.hidden)


# ---------------------------------------------------------------- 图标绘制

def make_icon_pixels(closed=False, S=32):
    px = [[(0, 0, 0, 0)] * S for _ in range(S)]
    R = 8
    border_c, bg_c = (99, 102, 241, 255), (37, 41, 61, 255)
    eye_c, pupil_c = (240, 245, 255, 255), (59, 130, 246, 255)
    for y in range(S):
        for x in range(S):
            dx = max(R - x, x - (S - 1 - R), 0)
            dy = max(R - y, y - (S - 1 - R), 0)
            d2 = dx * dx + dy * dy
            if d2 <= R * R:
                px[y][x] = border_c if d2 > (R - 1.5) ** 2 else bg_c
    c = S // 2
    if closed:
        for y in (c - 1, c):
            for x in range(8, S - 8):
                px[y][x] = eye_c
    else:
        a, b = 10.0, 6.5
        for y in range(S):
            for x in range(S):
                ex = (x - c + 0.5) / a
                ey = (y - c - 0.5) / b
                if ex * ex + ey * ey <= 1.0:
                    px[y][x] = eye_c
        pr2 = 3.6 * 3.6
        for y in range(S):
            for x in range(S):
                ddx, ddy = x - c + 0.5, y - c - 0.5
                if ddx * ddx + ddy * ddy <= pr2:
                    px[y][x] = pupil_c
        px[c - 2][c - 2] = (255, 255, 255, 255)
    return px


def create_hicon(pixels):
    h = len(pixels)
    w = len(pixels[0])
    header = BITMAPINFOHEADER()
    header.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    header.biWidth = w
    header.biHeight = -h          # 负值 = 自上而下
    header.biPlanes = 1
    header.biBitCount = 32
    header.biCompression = 0      # BI_RGB

    data = bytearray(w * h * 4)
    i = 0
    for row in pixels:            # BGRA
        for r, g, b, a in row:
            data[i] = b
            data[i + 1] = g
            data[i + 2] = r
            data[i + 3] = a
            i += 4

    hdc = user32.GetDC(None)
    try:
        pp = ctypes.c_void_p()
        hcolor = gdi32.CreateDIBSection(hdc, ctypes.byref(header), 0,
                                        ctypes.byref(pp), None, 0)
        if not hcolor or not pp.value:
            return None
        ctypes.memmove(pp.value, bytes(data), len(data))
    finally:
        user32.ReleaseDC(None, hdc)
    hmask = gdi32.CreateBitmap(w, h, 1, 1, None)
    ii = ICONINFO()
    ii.fIcon = True
    ii.hbmMask = hmask
    ii.hbmColor = hcolor
    hicon = user32.CreateIconIndirect(ctypes.byref(ii))
    gdi32.DeleteObject(hmask)
    gdi32.DeleteObject(hcolor)
    return hicon


# ---------------------------------------------------------------- 热键线程

HOTKEY_CN = {"hide_hotkey": "隐藏", "show_hotkey": "唤醒"}


class HotkeyThread(threading.Thread):
    """注册全局热键：id 1/2 为全局隐藏/唤醒，1000+程序id 为各程序独立热键。"""

    def __init__(self, cfg_ref):
        super().__init__(daemon=True)
        self._cfg = cfg_ref
        self.tid = 0
        self._started = threading.Event()
        self._reg_map = {}          # RegisterHotKey id -> 程序规则 id

    def wait_ready(self, timeout=3.0):
        self._started.wait(timeout)

    def run(self):
        self.tid = kernel32.GetCurrentThreadId()
        self._started.set()
        self._apply()
        msg = wt.MSG()
        while True:
            r = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if r in (0, -1):
                break
            if msg.message == WM_HOTKEY:
                w = msg.wParam
                dbg("hotkey id=%s reg_map_keys=%s" % (w, list(self._reg_map)))
                if w == 1:
                    APPQ.put(("hide",))
                elif w == 2:
                    APPQ.put(("show",))
                elif w in self._reg_map:
                    APPQ.put(("toggle", self._reg_map[w]))
                else:
                    dbg("hotkey id=%s NOT IN reg_map, ignored" % w)
            elif msg.message == HKM_RECONFIG:
                self._apply(report=True)
            elif msg.message == HKM_SUSPEND:
                self._unregister_all()
            elif msg.message == HKM_RESUME:
                self._apply(report=True)
            elif msg.message == HKM_QUIT:
                break
        self._unregister_all()

    def _unregister_all(self):
        for i in (1, 2):
            user32.UnregisterHotKey(None, i)
        for rid in list(self._reg_map):
            user32.UnregisterHotKey(None, rid)
        self._reg_map.clear()

    def _apply(self, report=False):
        errs = []
        self._unregister_all()
        taken = {}   # (mods, vk) -> 名称，用于冲突检测
        for idx, key in ((1, "hide_hotkey"), (2, "show_hotkey")):
            raw = self._cfg().get(key) or ""
            parsed = parse_hotkey(raw)
            if not parsed:
                errs.append("%s热键配置无效: %r" % (HOTKEY_CN[key], raw))
                continue
            if parsed in taken:
                errs.append("%s热键与%s热键重复" % (HOTKEY_CN[key], taken[parsed]))
                continue
            if not user32.RegisterHotKey(None, idx, parsed[0] | MOD_NOREPEAT, parsed[1]):
                errs.append("%s热键 %s 注册失败(可能被其他程序占用)"
                            % (HOTKEY_CN[key], hotkey_display(raw)))
                continue
            taken[parsed] = HOTKEY_CN[key]
        for p in self._cfg().get("programs", []):
            raw = p.get("hotkey") or ""
            if not raw or not p.get("id") or not p.get("enabled", True):
                continue
            parsed = parse_hotkey(raw)
            name = p.get("name", raw)
            if not parsed:
                errs.append("「%s」的独立热键无效: %r" % (name, raw))
                continue
            if parsed in taken:
                errs.append("「%s」的独立热键与%s重复" % (name, taken[parsed]))
                continue
            rid = 1000 + p["id"]
            if not user32.RegisterHotKey(None, rid, parsed[0] | MOD_NOREPEAT, parsed[1]):
                errs.append("「%s」的独立热键 %s 注册失败(可能被其他程序占用)"
                            % (name, hotkey_display(raw)))
                continue
            taken[parsed] = "「%s」" % name
            self._reg_map[rid] = p["id"]
        if report and errs:
            APPQ.put(("warn", "\n".join(errs)))
        return errs

    def reconfigure(self):
        if self.tid:
            user32.PostThreadMessageW(self.tid, HKM_RECONFIG, 0, 0)

    def suspend(self):
        if self.tid:
            user32.PostThreadMessageW(self.tid, HKM_SUSPEND, 0, 0)

    def resume(self):
        if self.tid:
            user32.PostThreadMessageW(self.tid, HKM_RESUME, 0, 0)

    def quit(self):
        if self.tid:
            user32.PostThreadMessageW(self.tid, HKM_QUIT, 0, 0)


# ---------------------------------------------------------------- 托盘线程

class TrayThread(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.hwnd = None
        self.ok = False
        self._started = threading.Event()
        self._pending = (False, 0)   # (closed, count)
        self._applied = (None, None)
        self._icon_open = None
        self._icon_closed = None

    def wait_ready(self, timeout=3.0):
        self._started.wait(timeout)

    def run(self):
        self.tid = kernel32.GetCurrentThreadId()
        try:
            self._icon_open = create_hicon(make_icon_pixels(False))
            self._icon_closed = create_hicon(make_icon_pixels(True))
            self._wndproc_ref = WNDPROC(self._wndproc)
            wc = WNDCLASSW()
            wc.lpfnWndProc = self._wndproc_ref
            wc.hInstance = kernel32.GetModuleHandleW(None)
            wc.lpszClassName = "MaxQHideTrayWnd"
            if not user32.RegisterClassW(ctypes.byref(wc)):
                self._started.set()
                return
            # message-only 窗口：不显示、只接收托盘消息
            self.hwnd = user32.CreateWindowExW(
                0, "MaxQHideTrayWnd", "", 0, 0, 0, 0, 0,
                HWND_MESSAGE, None, wc.hInstance, None)
            if not self.hwnd:
                self._started.set()
                return
            nid = NOTIFYICONDATAW()
            nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
            nid.hWnd = self.hwnd
            nid.uID = 1
            nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
            nid.uCallbackMessage = WM_TRAYICON
            nid.hIcon = self._icon_open
            nid.szTip = "%s 老板键" % APP_NAME
            self.ok = bool(shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)))
        finally:
            self._started.set()
        if not self.ok:
            return
        msg = wt.MSG()
        while True:
            r = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if r in (0, -1):
                break
            user32.DispatchMessageW(ctypes.byref(msg))

    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == WM_TRAYICON:
            if lparam in (WM_RBUTTONUP, 0x0206):  # 右键: 菜单
                self._popup_menu(hwnd)
            elif lparam in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                APPQ.put(("open_settings",))
            return 0
        if msg == TM_UPDATE_ICON:
            self._apply_icon()
            return 0
        if msg == TM_SHOW_SETTINGS:
            APPQ.put(("open_settings",))
            return 0
        if msg == WM_CLOSE:
            user32.DestroyWindow(hwnd)
            return 0
        if msg == WM_DESTROY:
            nid = NOTIFYICONDATAW()
            nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
            nid.hWnd = hwnd
            nid.uID = 1
            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _popup_menu(self, hwnd):
        hmenu = user32.CreatePopupMenu()
        user32.AppendMenuW(hmenu, MF_STRING, 1, "打开设置")
        user32.AppendMenuW(hmenu, MF_STRING, 2, "立即隐藏")
        user32.AppendMenuW(hmenu, MF_STRING, 3, "立即唤醒")
        user32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(hmenu, MF_STRING, 4, "退出")
        pt = wt.POINT(0, 0)
        user32.GetCursorPos(ctypes.byref(pt))
        # 弹出菜单前必须前置自己的窗口，否则菜单无法自动消失
        user32.SetForegroundWindow(hwnd)
        cmd = user32.TrackPopupMenu(
            hmenu, TPM_RETURNCMD | TPM_RIGHTBUTTON | TPM_NONOTIFY,
            pt.x, pt.y, 0, hwnd, None)
        user32.PostMessageW(hwnd, 0x0000, 0, 0)  # WM_NULL
        user32.DestroyMenu(hmenu)
        if cmd == 1:
            APPQ.put(("open_settings",))
        elif cmd == 2:
            APPQ.put(("hide",))
        elif cmd == 3:
            APPQ.put(("show",))
        elif cmd == 4:
            APPQ.put(("exit",))

    def _apply_icon(self):
        closed, count = self._pending
        if not self.hwnd or self._applied == self._pending:
            return
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self.hwnd
        nid.uID = 1
        nid.uFlags = NIF_ICON | NIF_TIP
        nid.hIcon = self._icon_closed if closed else self._icon_open
        nid.szTip = "%s 老板键 - 已隐藏 %d 个窗口" % (APP_NAME, count) if closed \
            else "%s 老板键" % APP_NAME
        if shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid)):
            self._applied = self._pending

    def set_state(self, closed, count):
        self._pending = (closed, count)
        if self.hwnd:
            user32.PostMessageW(self.hwnd, TM_UPDATE_ICON, 0, 0)

    def quit(self):
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)


# ---------------------------------------------------------------- 热键捕获对话框

KEYSYM_VK = {
    "space": 0x20, "Tab": 0x09, "Return": 0x0D, "Escape": 0x1B,
    "BackSpace": 0x08, "Delete": 0x2E, "Insert": 0x2D, "Home": 0x24,
    "End": 0x23, "Prior": 0x21, "Next": 0x22, "Left": 0x25, "Up": 0x26,
    "Right": 0x27, "Down": 0x28, "Caps_Lock": 0x14, "Print": 0x2C,
    "minus": 0xBD, "plus": 0xBB, "comma": 0xBC, "period": 0xBE,
    "grave": 0xC0, "bracketleft": 0xDB, "bracketright": 0xDD,
    "semicolon": 0xBA, "apostrophe": 0xDE, "slash": 0xBF, "backslash": 0xDC,
}
MODIFIER_KEYSYMS = {
    "Control_L", "Control_R", "Alt_L", "Alt_R", "Shift_L", "Shift_R",
    "Win_L", "Win_R", "Meta_L", "Meta_R", "Super_L", "Super_R",
    "Caps_Lock", "Num_Lock", "Scroll_Lock", "ISO_Level3_Shift", "Mode_switch",
}


def win_key_down():
    """Tk 对 Win 修饰键的状态位报告不可靠，用系统异步键状态兜底。"""
    return (user32.GetAsyncKeyState(0x5B) | user32.GetAsyncKeyState(0x5C)) & 0x8000


def physical_mods():
    """读取 Ctrl/Alt/Shift/Win 的物理按下状态。

    不能依赖 Tk 事件 state 位：部分输入法/键盘环境下 0x8 位恒为 1，
    会导致捕获结果凭空多出 Alt（如 Ctrl+F1 变成 Ctrl+Alt+F1）。
    """
    mods = []
    if user32.GetAsyncKeyState(0x11) & 0x8000:   # VK_CONTROL
        mods.append("ctrl")
    if user32.GetAsyncKeyState(0x12) & 0x8000:   # VK_MENU
        mods.append("alt")
    if user32.GetAsyncKeyState(0x10) & 0x8000:   # VK_SHIFT
        mods.append("shift")
    if win_key_down():
        mods.append("win")
    return mods


def center_and_grab(win, parent, focus_widget=None):
    """居中于父窗口；窗口映射后延迟接管 grab/焦点。

    不用 wait_visibility：窗口在等待期间被销毁会永久挂起。
    """
    win.update_idletasks()
    px = parent.winfo_rootx() + (parent.winfo_width() - win.winfo_width()) // 2
    py = parent.winfo_rooty() + (parent.winfo_height() - win.winfo_height()) // 2
    win.geometry("+%d+%d" % (px, py))
    (focus_widget or win).focus_set()

    def _grab():
        try:
            if win.winfo_exists():
                win.grab_set()
        except Exception:
            pass

    win.after(80, _grab)


class HotkeyDialog(tk.Toplevel):
    """全屏捕获组合键的小对话框，返回规范化的热键字符串。"""
    result = None

    def __init__(self, parent, title):
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.configure(padx=24, pady=20)
        ttk.Label(self, text="请按下新的组合键",
                  font=("Microsoft YaHei UI", 12, "bold")).pack()
        self.hint = ttk.Label(self, text="两键即可，如 Ctrl+H\n需含 Ctrl / Alt / Shift / Win 中至少一个\n按 Esc 取消",
                              justify="center", foreground="#666")
        self.hint.pack(pady=(6, 0))
        self.transient(parent)
        self.bind("<KeyPress>", self._on_key)
        center_and_grab(self, parent)

    def _on_key(self, e):
        if e.keysym == "Escape":
            self.destroy()
            return
        if e.keysym in MODIFIER_KEYSYMS:
            return
        mods = physical_mods()
        if not mods:
            # 兜底：事件延迟到松开修饰键后才处理时，物理状态已读不到
            if e.state & 0x04:
                mods.append("ctrl")
            if e.state & 0x01:
                mods.append("shift")
            if e.state & 0x40:
                mods.append("win")
        ks = e.keysym
        if len(ks) == 1 and ks.isalpha():
            vk = ord(ks.upper())
        elif len(ks) == 1 and ks.isdigit():
            vk = ord(ks)
        elif ks in KEYSYM_VK:
            vk = KEYSYM_VK[ks]
        elif len(ks) == 2 and ks[0] == "F" and ks[1:].isdigit() and 1 <= int(ks[1:]) <= 24:
            vk = 0x6F + int(ks[1:])
        else:
            return
        if not mods:
            self.hint.configure(text="单按此键会拦截正常打字，请加修饰键\n例如 Ctrl / Alt / Shift / Win + 该键")
            return
        mods = [m for m in ("ctrl", "alt", "shift", "win") if m in mods]
        self.result = "+".join(mods + [vk_name(vk)])
        self.destroy()


# ---------------------------------------------------------------- 主界面

TYPE_CN = {"process": "进程名", "title": "标题包含"}
TYPE_CN_TO_KEY = {v: k for k, v in TYPE_CN.items()}


class ProgramDialog(tk.Toplevel):
    """手动添加 / 编辑一条程序规则。"""
    result = None  # dict(name, match_type, match_value, hotkey)

    def __init__(self, parent, init=None, hotkey_mgr=None):
        super().__init__(parent)
        self.hotkey_mgr = hotkey_mgr
        self.title("编辑规则" if init else "手动添加规则")
        self.resizable(False, False)
        self.configure(padx=16, pady=14)
        frm = ttk.Frame(self)
        frm.pack()
        ttk.Label(frm, text="备注名称").grid(row=0, column=0, sticky="w", pady=4)
        self.name = ttk.Entry(frm, width=36)
        self.name.grid(row=0, column=1, padx=(10, 0), pady=4)
        ttk.Label(frm, text="匹配方式").grid(row=1, column=0, sticky="w", pady=4)
        self.type_cb = ttk.Combobox(frm, state="readonly", width=33,
                                    values=list(TYPE_CN.values()))
        self.type_cb.grid(row=1, column=1, padx=(10, 0), pady=4)
        ttk.Label(frm, text="匹配值").grid(row=2, column=0, sticky="w", pady=4)
        self.value = ttk.Entry(frm, width=36)
        self.value.grid(row=2, column=1, padx=(10, 0), pady=4)
        ttk.Label(frm, text="独立热键").grid(row=3, column=0, sticky="w", pady=4)
        hkbox = ttk.Frame(frm)
        hkbox.grid(row=3, column=1, padx=(10, 0), pady=4, sticky="w")
        self.hk_btn = ttk.Button(hkbox, width=22, command=self._capture_hotkey)
        self.hk_btn.pack(side="left")
        ttk.Button(hkbox, text="清除", width=6,
                   command=self._clear_hotkey).pack(side="left", padx=6)
        ttk.Label(frm, foreground="#888",
                  text="独立热键：按下后只隐藏/唤醒该程序（再按一次切换）。\n"
                       "进程名如 yuanshen.exe（可不带 .exe）；标题包含适合 UWP 游戏").grid(
            row=4, column=0, columnspan=2, sticky="w", pady=(8, 0))
        btns = ttk.Frame(self)
        btns.pack(pady=(14, 0))
        ttk.Button(btns, text="确定", command=self._ok).pack(side="left", padx=6)
        ttk.Button(btns, text="取消", command=self.destroy).pack(side="left", padx=6)

        self.hotkey_value = init.get("hotkey") if init else None
        self._update_hk_btn()
        self.type_cb.current(0)
        if init:
            self.name.insert(0, init.get("name", ""))
            self.type_cb.current(0 if init.get("match_type") == "process" else 1)
            self.value.insert(0, init.get("match_value", ""))
        self.bind("<Return>", lambda e: self._ok())
        self.bind("<Escape>", lambda e: self.destroy())
        self.transient(parent)
        center_and_grab(self, parent, self.value)

    def _capture_hotkey(self):
        if self.hotkey_mgr:
            self.hotkey_mgr.suspend()
        try:
            d = HotkeyDialog(self, "设置独立热键")
            self.wait_window(d)
        finally:
            if self.hotkey_mgr:
                self.hotkey_mgr.resume()
        try:  # 捕获对话框的 grab 结束后恢复本对话框的 grab
            self.grab_set()
        except Exception:
            pass
        if d.result:
            self.hotkey_value = d.result
            self._update_hk_btn()

    def _clear_hotkey(self):
        self.hotkey_value = None
        self._update_hk_btn()

    def _update_hk_btn(self):
        self.hk_btn.configure(
            text=hotkey_display(self.hotkey_value) if self.hotkey_value else "无（点击设置）")

    def _ok(self):
        name = self.name.get().strip()
        value = self.value.get().strip()
        if not value:
            messagebox.showwarning(APP_NAME, "请填写匹配值", parent=self)
            return
        if not name:
            name = value
        self.result = {
            "name": name,
            "match_type": TYPE_CN_TO_KEY[self.type_cb.get()],
            "match_value": value,
            "hotkey": self.hotkey_value,
        }
        self.destroy()


class RunningAppsDialog(tk.Toplevel):
    """从当前运行的窗口中选择要添加的程序。"""

    def __init__(self, parent, on_add):
        super().__init__(parent)
        self.title("从运行中的程序添加")
        self.on_add = on_add
        self.configure(padx=12, pady=10)
        top = ttk.Frame(self)
        top.pack(fill="x")
        ttk.Label(top, text="勾选要隐藏的程序窗口（可多选）").pack(side="left")
        ttk.Button(top, text="刷新", width=8, command=self.refresh).pack(side="right")
        cols = ("title", "exe")
        self.tree = ttk.Treeview(self, columns=cols, show="headings",
                                 selectmode="extended", height=12)
        self.tree.heading("title", text="窗口标题")
        self.tree.heading("exe", text="进程")
        self.tree.column("title", width=380)
        self.tree.column("exe", width=170)
        self.tree.pack(pady=(8, 0))
        btns = ttk.Frame(self)
        btns.pack(pady=(10, 0))
        ttk.Button(btns, text="添加所选", command=self._ok).pack(side="left", padx=6)
        ttk.Button(btns, text="取消", command=self.destroy).pack(side="left", padx=6)
        self.transient(parent)
        self.refresh()
        center_and_grab(self, parent, self.tree)

    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        own = os.getpid()
        for w in enum_windows():
            if w.pid == own or not w.title:
                continue
            if not user32.IsWindowVisible(w.hwnd) or is_cloaked(w.hwnd):
                continue
            if user32.GetWindow(w.hwnd, GW_OWNER):
                continue
            self.tree.insert("", "end", iid=str(w.hwnd),
                             values=(w.title[:60], w.exe or "(未知进程)"))

    def _ok(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo(APP_NAME, "请先选择至少一个窗口", parent=self)
            return
        rules = []
        for iid in sel:
            w = self.tree.item(iid, "values")
            title, exe = w[0], (w[1] if len(w) > 1 else "")
            if exe.lower() in ("applicationframehost.exe", "(未知进程)", ""):
                # UWP 应用按进程名匹配会误伤所有 UWP，改用标题匹配
                rules.append({"name": title[:24],
                              "match_type": "title",
                              "match_value": title.split("-")[-1].strip() or title})
            else:
                rules.append({"name": os.path.splitext(exe)[0],
                              "match_type": "process",
                              "match_value": exe})
        self.on_add(rules)
        self.destroy()


class App:
    def __init__(self, root, cfg, cfg_path, core, hotkeys, tray):
        self.root = root
        self.cfg = cfg
        self.cfg_path = cfg_path
        self.core = core
        self.hotkeys = hotkeys
        self.tray = tray
        self.boss_hid_gui = False
        self._next_id()

        root.title("%s 老板键 v%s" % (APP_NAME, APP_VERSION))
        root.geometry("760x600")
        root.minsize(680, 520)
        root.option_add("*Font", ("Microsoft YaHei UI", 10))
        try:
            ttk.Style().theme_use("vista")
        except Exception:
            pass
        style = ttk.Style()
        style.configure("Treeview", rowheight=28,
                        font=("Microsoft YaHei UI", 10))
        style.configure("Hide.TButton", foreground="#b91c1c")
        style.configure("Show.TButton", foreground="#15803d")

        self._icon_img = self._build_icon_photo()
        if self._icon_img:
            root.iconphoto(True, self._icon_img)
        root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_ui()
        self._load_list()
        root.after(100, self._poll_queue)
        root.after(2000, self._refresh_status)

    # ---------- UI 构建

    def _build_icon_photo(self):
        try:
            img = tk.PhotoImage(width=32, height=32)
            rows = tuple(tuple("#%02x%02x%02x" % (r, g, b) for (r, g, b, a) in row)
                         for row in make_icon_pixels(False))
            img.put(rows)
            return img
        except Exception:
            return None

    def _build_ui(self):
        pad = {"padx": 14, "pady": (8, 4)}
        head = ttk.Frame(self.root)
        head.pack(fill="x", **pad)
        ttk.Label(head, text="%s · 老板键" % APP_NAME,
                  font=("Microsoft YaHei UI", 14, "bold")).pack(side="left")
        self.hotkey_tip = ttk.Label(head, foreground="#666")
        self.hotkey_tip.pack(side="right")

        listframe = ttk.LabelFrame(self.root, text=" 隐藏列表（隐藏与唤醒共用此列表）")
        listframe.pack(fill="both", expand=True, **pad)
        cols = ("name", "type", "value", "hotkey", "status")
        self.tree = ttk.Treeview(listframe, columns=cols, show="headings")
        for cid, text, width, anchor in (
                ("name", "名称", 150, "w"), ("type", "匹配方式", 80, "center"),
                ("value", "匹配值", 180, "w"), ("hotkey", "独立热键", 100, "center"),
                ("status", "状态", 80, "center")):
            self.tree.heading(cid, text=text)
            self.tree.column(cid, width=width, anchor=anchor)
        sb = ttk.Scrollbar(listframe, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=8)
        sb.pack(side="right", fill="y", pady=8, padx=(0, 8))
        self.tree.tag_configure("hidden", foreground="#b45309")
        self.tree.tag_configure("running", foreground="#15803d")
        self.tree.tag_configure("off", foreground="#999")
        self.tree.bind("<Double-1>", lambda e: self.edit_program())
        self.tree.bind("<Button-3>", self._popup_prog_menu)

        row1 = ttk.Frame(self.root)
        row1.pack(fill="x", **pad)
        ttk.Button(row1, text="从运行中的程序添加…", command=self.add_from_running).pack(side="left")
        ttk.Button(row1, text="手动添加", command=self.add_manual).pack(side="left", padx=6)
        ttk.Button(row1, text="编辑", command=self.edit_program).pack(side="left")
        ttk.Button(row1, text="删除", command=self.del_program).pack(side="left", padx=6)

        hkframe = ttk.LabelFrame(self.root, text=" 热键 ")
        hkframe.pack(fill="x", **pad)
        ttk.Label(hkframe, text="隐藏热键").grid(row=0, column=0, padx=10, pady=6)
        self.hide_hk_btn = ttk.Button(hkframe, command=lambda: self.set_hotkey("hide_hotkey"))
        self.hide_hk_btn.grid(row=0, column=1, padx=(0, 24))
        ttk.Label(hkframe, text="唤醒热键").grid(row=0, column=2)
        self.show_hk_btn = ttk.Button(hkframe, command=lambda: self.set_hotkey("show_hotkey"))
        self.show_hk_btn.grid(row=0, column=3)
        self._update_hotkey_buttons()

        opt = ttk.Frame(self.root)
        opt.pack(fill="x", **pad)
        self.var_hide_gui = tk.BooleanVar(value=bool(self.cfg.get("hide_gui", True)))
        ttk.Checkbutton(opt, text="按隐藏键时同时隐藏本设置窗口",
                        variable=self.var_hide_gui,
                        command=self._save_options).pack(side="left")
        self.var_monitor = tk.BooleanVar(value=bool(self.cfg.get("monitor_new_windows", True)))
        ttk.Checkbutton(opt, text="隐藏期间自动隐藏新弹出的窗口",
                        variable=self.var_monitor,
                        command=self._save_options).pack(side="left", padx=12)
        self.var_autostart = tk.BooleanVar(value=bool(self.cfg.get("autostart", False)))
        ttk.Checkbutton(opt, text="开机自启", variable=self.var_autostart,
                        command=self._save_options).pack(side="left")

        act = ttk.Frame(self.root)
        act.pack(fill="x", pady=(10, 0), padx=14)
        self.btn_hide = ttk.Button(act, text="立即隐藏", style="Hide.TButton",
                                   command=self.do_hide)
        self.btn_hide.pack(side="left", ipadx=16, ipady=3)
        self.btn_show = ttk.Button(act, text="立即唤醒", style="Show.TButton",
                                   command=self.do_show)
        self.btn_show.pack(side="left", padx=10, ipadx=16, ipady=3)
        self.hidden_label = ttk.Label(act, foreground="#666")
        self.hidden_label.pack(side="right")

        self.status = ttk.Label(self.root, relief="groove", anchor="w",
                                padding=(8, 4))
        self.status.pack(fill="x", side="bottom", padx=14, pady=(4, 10))

    # ---------- 列表操作

    def _next_id(self):
        """为手工编辑配置导致缺少 id 的规则补齐 id。"""
        progs = self.cfg.setdefault("programs", [])
        ids = [p.get("id", 0) for p in progs]
        counter = max(ids) + 1 if ids else 1
        dirty = False
        for p in progs:
            if not p.get("id"):
                p["id"] = counter
                counter += 1
                dirty = True
        self._id_counter = counter
        if dirty:
            self._save_cfg()

    def _load_list(self):
        self.tree.delete(*self.tree.get_children())
        for p in self.cfg.get("programs", []):
            hk = p.get("hotkey")
            self.tree.insert("", "end", iid=str(p.get("id")),
                             values=(p.get("name", ""),
                                     TYPE_CN.get(p.get("match_type"), p.get("match_type")),
                                     p.get("match_value", ""),
                                     hotkey_display(hk) if hk else "—", ""))

    def _selected_program(self):
        sel = self.tree.selection()
        if not sel:
            return None
        pid = int(sel[0])
        for p in self.cfg.get("programs", []):
            if p.get("id") == pid:
                return p
        return None

    def _popup_prog_menu(self, event):
        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        self.tree.selection_set(iid)
        m = tk.Menu(self.root, tearoff=0)
        m.add_command(label="编辑", command=self.edit_program)
        m.add_command(label="删除", command=self.del_program)
        m.tk_popup(event.x_root, event.y_root)

    def _hotkey_conflict(self, rule, editing_id=None):
        """返回冲突描述；无冲突返回 None。"""
        parsed = parse_hotkey(rule.get("hotkey") or "")
        if not parsed:
            return None
        for key, label in (("hide_hotkey", "全局隐藏热键"),
                           ("show_hotkey", "全局唤醒热键")):
            if parse_hotkey(self.cfg.get(key) or "") == parsed:
                return label
        for p in self.cfg.get("programs", []):
            if p.get("id") == editing_id:
                continue
            if p.get("hotkey") and parse_hotkey(p["hotkey"]) == parsed:
                return "「%s」的独立热键" % p.get("name", "")
        return None

    def _add_rules(self, rules):
        exist = {(p.get("match_type"), (p.get("match_value") or "").lower())
                 for p in self.cfg.get("programs", [])}
        added, skipped = 0, 0
        cleared = []
        for r in rules:
            key = (r["match_type"], r["match_value"].lower())
            if key in exist:
                skipped += 1
                continue
            exist.add(key)
            conflict = self._hotkey_conflict(r)
            if conflict:
                cleared.append("「%s」%s 与%s重复，已清除"
                               % (r.get("name", ""), hotkey_display(r["hotkey"]), conflict))
                r["hotkey"] = None
            r["id"] = self._id_counter
            self._id_counter += 1
            r["enabled"] = True
            self.cfg.setdefault("programs", []).append(r)
            added += 1
        self._save_cfg()
        self._load_list()
        self.hotkeys.reconfigure()
        if cleared:
            messagebox.showwarning(APP_NAME, "\n".join(cleared), parent=self.root)
        msg = "已添加 %d 条规则" % added
        if skipped:
            msg += "（%d 条已存在，自动跳过）" % skipped
        self.set_status(msg)

    def add_from_running(self):
        RunningAppsDialog(self.root, self._add_rules)

    def add_manual(self):
        d = ProgramDialog(self.root, hotkey_mgr=self.hotkeys)
        self.root.wait_window(d)
        if d.result:
            self._add_rules([d.result])

    def edit_program(self):
        p = self._selected_program()
        if not p:
            messagebox.showinfo(APP_NAME, "请先选择一条规则", parent=self.root)
            return
        d = ProgramDialog(self.root, init=p, hotkey_mgr=self.hotkeys)
        self.root.wait_window(d)
        if d.result:
            conflict = self._hotkey_conflict(d.result, editing_id=p.get("id"))
            if conflict:
                d.result["hotkey"] = None
                messagebox.showwarning(
                    APP_NAME,
                    "该热键与%s重复，独立热键未保存" % conflict,
                    parent=self.root)
            p.update(d.result)
            self._save_cfg()
            self._load_list()
            self.hotkeys.reconfigure()
            self.set_status("规则已更新")

    def del_program(self):
        p = self._selected_program()
        if not p:
            messagebox.showinfo(APP_NAME, "请先选择一条规则", parent=self.root)
            return
        if messagebox.askyesno(APP_NAME, "确定删除规则「%s」？" % p.get("name"), parent=self.root):
            self.cfg["programs"] = [x for x in self.cfg.get("programs", [])
                                    if x.get("id") != p.get("id")]
            self._save_cfg()
            self._load_list()
            self.hotkeys.reconfigure()
            self.set_status("已删除")

    # ---------- 热键 / 选项

    def set_hotkey(self, key):
        other = "show_hotkey" if key == "hide_hotkey" else "hide_hotkey"
        # 捕获期间暂停现有热键，否则按下当前组合键会被自己注册的热键吞掉
        self.hotkeys.suspend()
        try:
            d = HotkeyDialog(self.root, "设置%s热键" % HOTKEY_CN[key])
            self.root.wait_window(d)
            if not d.result:
                return
            if d.result == self.cfg.get(other, "").lower():
                messagebox.showwarning(APP_NAME, "与%s热键相同，请换一个" % HOTKEY_CN[other],
                                       parent=self.root)
                return
            self.cfg[key] = d.result
            self._save_cfg()
            self._update_hotkey_buttons()
            self.set_status("%s热键已设置为 %s" % (HOTKEY_CN[key], hotkey_display(d.result)))
        finally:
            self.hotkeys.resume()

    def _update_hotkey_buttons(self):
        self.hide_hk_btn.configure(text=hotkey_display(self.cfg.get("hide_hotkey")) + "   ✎")
        self.show_hk_btn.configure(text=hotkey_display(self.cfg.get("show_hotkey")) + "   ✎")
        self.hotkey_tip.configure(
            text="按 %s 隐藏 / 按 %s 唤醒" % (hotkey_display(self.cfg.get("hide_hotkey")),
                                        hotkey_display(self.cfg.get("show_hotkey"))))

    def _save_options(self):
        self.cfg["hide_gui"] = bool(self.var_hide_gui.get())
        self.cfg["monitor_new_windows"] = bool(self.var_monitor.get())
        want_autostart = bool(self.var_autostart.get())
        if want_autostart != bool(self.cfg.get("autostart")):
            if not set_autostart(want_autostart):
                messagebox.showwarning(APP_NAME, "开机自启设置失败", parent=self.root)
                self.var_autostart.set(self.cfg.get("autostart", False))
                return
            self.cfg["autostart"] = want_autostart
        self._save_cfg()

    def _save_cfg(self):
        try:
            save_cfg(self.cfg, self.cfg_path)
        except OSError as e:
            self.set_status("配置保存失败：%s" % e)

    # ---------- 隐藏 / 唤醒

    def do_hide(self):
        n = self.core.hide_now()
        total = len(self.core.snapshot_hidden())
        self.tray.set_state(True, total)
        if self.cfg.get("hide_gui", True) and self.root.state() == "normal":
            self.root.withdraw()
            self.boss_hid_gui = True
        self.set_status("已隐藏 %d 个窗口（合计 %d 个）— 程序仍在后台运行" % (n, total)
                        if n or total else "没有找到可隐藏的匹配窗口")
        self._refresh_status()

    def do_show(self):
        n = self.core.show_now()
        self.tray.set_state(False, 0)
        if self.boss_hid_gui:
            self.root.deiconify()
            self.boss_hid_gui = False
        self.set_status("已唤醒 %d 个窗口" % n if n else "当前没有处于隐藏状态的匹配窗口")
        self._refresh_status()

    def do_toggle(self, prog_id):
        prog = next((p for p in self.cfg.get("programs", [])
                     if p.get("id") == prog_id), None)
        if not prog:
            dbg("toggle prog_id=%s NOT FOUND in cfg" % prog_id)
            return
        mode, n = self.core.toggle_prog(prog)
        dbg("toggle prog=%r -> %s %d" % (prog.get("name"), mode, n))
        name = prog.get("name", "")
        if mode == "hide":
            self.set_status("已隐藏「%s」%d 个窗口（该程序独立热键）" % (name, n) if n
                            else "「%s」当前没有可见的匹配窗口" % name)
        else:
            self.set_status("已唤醒「%s」%d 个窗口" % (name, n) if n
                            else "「%s」当前没有隐藏中的窗口" % name)
        self._refresh_status()

    # ---------- 队列与状态刷新

    def _poll_queue(self):
        try:
            while True:
                cmd = APPQ.get_nowait()
                dbg("poll cmd=%r" % (cmd,))
                kind = cmd[0]
                if kind == "hide":
                    self.do_hide()
                elif kind == "show":
                    self.do_show()
                elif kind == "toggle":
                    self.do_toggle(cmd[1])
                elif kind == "open_settings":
                    self.root.deiconify()
                    self.root.lift()
                    self.root.focus_force()
                elif kind == "warn":
                    self.set_status(cmd[1], error=True)
                    if self.root.state() == "normal":
                        messagebox.showwarning(APP_NAME, cmd[1], parent=self.root)
                elif kind == "exit":
                    self.exit_app()
        except queue.Empty:
            pass
        except Exception:
            pass  # 单条命令异常不能中断轮询循环
        finally:
            self.root.after(100, self._poll_queue)

    def _heal_grabs(self):
        """自动解除"已隐藏的窗口仍持有 grab"造成的界面点击无响应。"""
        try:
            w = self.root.grab_current()
            if w is not None and not w.winfo_ismapped():
                w.grab_release()
        except Exception:
            pass

    def _refresh_status(self):
        try:
            self._heal_grabs()
            progs = self.core.programs()
            visible = self.core.find(visible_only=True)
            hidden = self.core.snapshot_hidden()
            for p in progs:
                status, tag = "未运行", "off"
                if any(prog_match(w, p) for w in hidden.values()):
                    status, tag = "已隐藏", "hidden"
                elif any(prog_match(w, p) for w in visible):
                    status, tag = "运行中", "running"
                iid = str(p.get("id"))
                if self.tree.exists(iid):
                    self.tree.set(iid, "status", status)
                    self.tree.item(iid, tags=(tag,))
            self.hidden_label.configure(
                text="隐藏中：%d 个窗口" % len(hidden) if hidden else "当前无隐藏窗口")
            self.tray.set_state(bool(hidden), len(hidden))
        finally:
            self.root.after(2000, self._refresh_status)

    def set_status(self, text, error=False):
        self.status.configure(text=text,
                              foreground="#b91c1c" if error else "#333")

    # ---------- 退出

    def _on_close(self):
        # 有弹窗打开时不隐藏主窗口：transient 弹窗会跟着一起被藏进后台，
        # 其 grab 会让整个界面点击失效
        for w in self.root.winfo_children():
            if isinstance(w, (ProgramDialog, RunningAppsDialog, HotkeyDialog)):
                return
        if self.tray.ok:
            self.root.withdraw()
            self.set_status("已最小化到托盘，右键托盘图标可退出")
        else:
            self.root.iconify()

    def exit_app(self):
        hidden = len(self.core.snapshot_hidden())
        if hidden:
            r = messagebox.askyesnocancel(
                APP_NAME,
                "仍有 %d 个窗口处于隐藏状态。\n\n退出前是否唤醒它们？\n"
                "选「否」将保持隐藏，之后可用命令行  python bosskey.pyw --show  唤醒。" % hidden,
                parent=self.root)
            if r is None:
                return
            if r:
                self.core.show_now()
        self._save_cfg()
        self.hotkeys.quit()
        self.tray.quit()
        time.sleep(0.25)  # 给线程一点时间注销热键、移除托盘图标
        self.root.destroy()


# ---------------------------------------------------------------- 命令行模式

def run_cli():
    import argparse
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        kernel32.SetConsoleOutputCP(65001)
    except Exception:
        pass
    ap = argparse.ArgumentParser(
        prog="bosskey.pyw",
        description="%s 老板键 - 命令行模式(与托盘程序互相独立使用)" % APP_NAME)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--hide", action="store_true", help="立即隐藏所有匹配窗口")
    g.add_argument("--show", action="store_true", help="唤醒所有匹配的隐藏窗口")
    g.add_argument("--list", action="store_true", help="列出匹配的窗口及状态")
    args = ap.parse_args()

    cfg, path = load_cfg()
    core = Core(lambda: cfg)
    if args.hide:
        n = core.hide_now()
        print("已隐藏 %d 个窗口" % n)
        return
    if args.show:
        n = core.show_now()
        print("已唤醒 %d 个窗口" % n)
        return
    wins = core.find(visible_only=False)
    if not wins:
        print("没有匹配的窗口。当前配置文件: %s" % path)
        print("可在其中编辑 programs 列表，或运行图形界面添加。")
        return
    print("%-6s %-8s %-24s %s" % ("状态", "PID", "进程", "窗口标题"))
    for w in wins:
        state = "可见" if user32.IsWindowVisible(w.hwnd) else "已隐藏"
        print("%-6s %-8d %-24s %s" % (state, w.pid, w.exe or "?", w.title[:50]))


# ---------------------------------------------------------------- 入口

def main():
    if sys.platform != "win32":
        sys.exit("仅支持 Windows")
    if len(sys.argv) > 1:
        run_cli()
        return

    mutex = kernel32.CreateMutexW(None, False, "Local\\MaxQHide_SingleInstance")
    if mutex and ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        hwnd = user32.FindWindowW("MaxQHideTrayWnd", None)
        if hwnd:
            user32.PostMessageW(hwnd, TM_SHOW_SETTINGS, 0, 0)
            return
        user32.MessageBoxW(None, "%s 已在运行中（请查看托盘图标）。" % APP_NAME,
                           APP_NAME, 0x40)
        return

    cfg, cfg_path = load_cfg()
    core = Core(lambda: cfg)
    hotkeys = HotkeyThread(lambda: cfg)
    hotkeys.start()
    hotkeys.wait_ready()
    tray = TrayThread()
    tray.start()
    tray.wait_ready()

    def monitor_loop():
        while True:
            time.sleep(0.7)
            if cfg.get("monitor_new_windows", True):
                core.sweep()

    threading.Thread(target=monitor_loop, daemon=True).start()

    root = tk.Tk()
    App(root, cfg, cfg_path, core, hotkeys, tray)
    root.mainloop()


if __name__ == "__main__":
    main()
