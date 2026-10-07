#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Codex 用量浮窗。

常驻桌面的置顶小窗，显示 Codex（ChatGPT 计划）的 5 小时额度 / 每周额度
用量百分比、进度条与重置倒计时。

数据来源: ~/.codex/auth.json 的登录令牌
        -> https://chatgpt.com/backend-api/codex/usage （每 60 秒刷新一次）
说明: chatgpt.com 的 Cloudflare 会拦截 Python 的 TLS 指纹（403），
     所以必须借助系统自带的 curl.exe（Schannel）发起请求。

用法: pythonw codex_quota_widget.pyw [--debug]
交互: 左键拖动移动；双击折叠/展开；右键菜单（立即刷新/跟随/固定跟随/显示模式/折叠/退出）。
托盘: 左键找回浮窗，右键菜单（找回/立即刷新/退出）。
环境变量: CODEX_WIDGET_PROXY 覆盖代理（默认 http://127.0.0.1:7897，
         设为 direct 则直连）。
"""

import ctypes
import ctypes.wintypes
import datetime
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont

AUTH_PATH = os.path.expanduser("~/.codex/auth.json")
USAGE_URL = "https://chatgpt.com/backend-api/codex/usage"
POLL_SECONDS = 60
FOLLOW_INTERVAL_MS = 1000
DRAG_INTERVAL_MS = 120
_PIN_DEFAULT_GAP = 105    # 固定跟随默认位：浮窗底边距窗口可见底边的像素（头像上方不远处）
_PIN_DEFAULT_LEFT = 8     # 固定跟随默认位：浮窗左边距窗口可见左边的像素
PROXY = os.environ.get("CODEX_WIDGET_PROXY", "http://127.0.0.1:7897")
DEBUG = "--debug" in sys.argv
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)  # 打包成 exe 后：exe 所在目录
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(BASE_DIR, "widget.log")

BG, FG, DIM, TRACK = "#1e1e1e", "#e8eaed", "#9aa0a6", "#333333"
GREEN, ORANGE, RED = "#4caf50", "#ffb74d", "#ef5350"

# Git 自带的 curl（Schannel 较新，能过 chatgpt.com 的 Cloudflare）；
# 系统自带的 curl.exe 版本旧，会被 403，仅作后备。
_GIT_CURL = r"C:\Program Files\Git\mingw64\bin\curl.exe"
_CURL = [_GIT_CURL, r"C:\Windows\System32\curl.exe", shutil.which("curl")]


def log(msg):
    if DEBUG:
        try:
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(time.strftime("[%Y-%m-%d %H:%M:%S] ") + msg + "\n")
        except OSError:
            pass


def curl_candidates():
    seen = []
    for c in _CURL:
        if c and os.path.exists(c) and c not in seen:
            seen.append(c)
    if not seen:
        raise RuntimeError("未找到 curl.exe")
    return seen


def load_auth():
    with open(AUTH_PATH, encoding="utf-8") as f:
        d = json.load(f)
    t = d.get("tokens") or {}
    return t.get("access_token"), t.get("account_id")


def run_curl(exe, proxy):
    access, account = load_auth()
    if not access:
        raise RuntimeError("未找到登录令牌，请先运行 codex 登录")
    cmd = [exe, "--silent", "--show-error",
           "--connect-timeout", "8", "--max-time", "20",
           "-w", "\n__HTTP__%{http_code}"]
    if proxy:
        cmd += ["-x", proxy]
    cmd += [USAGE_URL,
            "-H", "Authorization: Bearer " + access,
            "-H", "Accept: application/json",
            "-H", "User-Agent: codex_cli_rs/0.48.0"]
    if account:
        cmd += ["-H", "ChatGPT-Account-Id: " + account]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=30,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if "__HTTP__" not in p.stdout:
        raise RuntimeError("curl 无响应: " + (p.stderr or "").strip()[:120])
    body, _, status = p.stdout.rpartition("\n__HTTP__")
    status = status.strip()
    if status != "200":
        raise RuntimeError("HTTP " + status + " " + body.strip()[:100])
    return json.loads(body)


def fetch_usage():
    """返回 (usage 字典, 通道标签)。依次尝试各 curl（代理 -> 直连）。"""
    tries = [PROXY, None] if PROXY and PROXY != "direct" else [None]
    errors = []
    for exe in curl_candidates():
        for proxy in tries:
            label = proxy or "直连"
            try:
                return run_curl(exe, proxy), label
            except Exception as e:
                errors.append("%s(%s): %s" % (
                    os.path.basename(os.path.dirname(exe)), label, e))
                log("fetch fail %s via %s: %s" % (exe, label, e))
    raise RuntimeError("；".join(errors))


def parse_window(w):
    if not isinstance(w, dict):
        return None
    reset = None
    ra = w.get("reset_at")
    if isinstance(ra, (int, float)):
        reset = float(ra)
    elif isinstance(ra, str):
        try:
            dt = datetime.datetime.fromisoformat(ra.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.timezone.utc)
            reset = dt.timestamp()
        except ValueError:
            pass
    if reset is None and w.get("reset_after_seconds") is not None:
        try:
            reset = time.time() + float(w["reset_after_seconds"])
        except (TypeError, ValueError):
            pass
    try:
        used = float(w.get("used_percent"))
    except (TypeError, ValueError):
        used = None
    return {"used": used, "reset": reset}


def reset_text(epoch):
    if epoch is None:
        return ""
    s = epoch - time.time()
    if s <= 0:
        return "已重置"
    dt = datetime.datetime.fromtimestamp(epoch)
    hm = dt.strftime("%H:%M")
    total_min = int(s // 60)
    days, rem = divmod(total_min, 1440)
    hours, mins = divmod(rem, 60)
    if days:
        return "%d天%d小时后 (周%s %s)" % (days, hours, "一二三四五六日"[dt.weekday()], hm)
    if hours:
        return "%d小时%02d分后 (%s)" % (hours, mins, hm)
    return "%d分后 (%s)" % (mins, hm)


def reset_text_compact(epoch):
    """跟随模式的精简倒计时：不带目标时刻和"后"字。"""
    if epoch is None:
        return ""
    s = epoch - time.time()
    if s <= 0:
        return "已重置"
    total_min = int(s // 60)
    days, rem = divmod(total_min, 1440)
    hours, mins = divmod(rem, 60)
    if days:
        return "%d天%d时" % (days, hours)
    if hours:
        return "%d时%02d分" % (hours, mins)
    return "%d分" % mins


def _process_map():
    """进程快照 {pid: (ppid, exe)}，exe 为小写且不带 .exe 后缀。"""
    k32 = ctypes.windll.kernel32

    class PE(ctypes.Structure):
        _fields_ = [("dwSize", ctypes.c_ulong), ("cntUsage", ctypes.c_ulong),
                    ("th32ProcessID", ctypes.c_ulong),
                    ("th32DefaultHeapID", ctypes.c_size_t),
                    ("th32ModuleID", ctypes.c_ulong),
                    ("cntThreads", ctypes.c_ulong),
                    ("th32ParentProcessID", ctypes.c_ulong),
                    ("pcPriClassBase", ctypes.c_long),
                    ("dwFlags", ctypes.c_ulong),
                    ("szExeFile", ctypes.c_wchar * 260)]

    procs = {}
    snap = k32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    if snap == -1 or snap is None:
        return procs
    e = PE()
    e.dwSize = ctypes.sizeof(PE)
    ok = k32.Process32FirstW(snap, ctypes.byref(e))
    while ok:
        name = e.szExeFile.lower()
        if name.endswith(".exe"):
            name = name[:-4]
        procs[e.th32ProcessID] = (e.th32ParentProcessID, name)
        ok = k32.Process32NextW(snap, ctypes.byref(e))
    k32.CloseHandle(snap)
    return procs


# 可能承载 codex CLI 的终端程序（配合窗口标题含 codex 判定）
_TERMINAL_EXES = {"windowsterminal", "openconsole", "conhost", "cmd",
                  "powershell", "pwsh", "wezterm-gui", "alacritty",
                  "mintty", "hyper"}

# 宿主追溯时要跳过的系统/外壳进程（避免跟到桌面或托盘上）；
# 编辑器/IDE 也不算 Codex 宿主——其集成终端里跑的 codex 会话不应把
# 浮窗拽到编辑器窗口上（实测：VSCode 集成终端的 codex.exe 挂在
# code.exe 进程树下，ChatGPT App 最小化时浮窗曾因此跟走 VSCode）
_HOST_SKIP_EXES = {"explorer", "system", "idle", "svchost", "services",
                   "csrss", "winlogon", "wininit", "smss", "lsass",
                   "pythonw", "python", "dllhost", "runtimebroker",
                   "code", "code-insiders", "devenv"}

# 拖动这些进程的窗口时隐藏浮窗（Codex 自身/其宿主/可能承载 CLI 的终端）
_DRAG_HIDE_EXES = _TERMINAL_EXES | {"codex", "chatgpt"}


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_ulong),
                ("rcMonitor", ctypes.wintypes.RECT),
                ("rcWork", ctypes.wintypes.RECT),
                ("dwFlags", ctypes.c_ulong)]


def find_codex_rect():
    """返回可跟随的 Codex 窗口 (l, t, r, b, 顶栏高度, 工作区顶 y, hwnd)，找不到返回 None。

    优先级:
      1. Codex 桌面 App —— codex.exe 自己拥有的可见顶层窗口；
      2. CLI 宿主窗口 —— 沿 codex.exe 的父进程链向上找第一个拥有
         可见顶层窗口的进程（实测本机：在 ChatGPT 桌面 App 里跑
         Codex 时宿主是 chatgpt.exe；终端场景则是 WindowsTerminal）；
      3. 经典控制台 conhost（宿主在 codex 父链上）；
      4. 标题含 codex 的终端窗口（CLI 跑在激活标签页时标题会带 codex）。
    最小化窗口不参与跟随。Codex 仅以无头方式运行时返回 None。
    """
    user32 = ctypes.windll.user32
    procs = _process_map()

    wins = []  # (pid, exe, cls, title_lower, rect)

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND,
                        ctypes.wintypes.LPARAM)
    def cb(hwnd, _lp):
        if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
            return True
        pid = ctypes.wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        r = ctypes.wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        if r.right - r.left < 100 or r.bottom - r.top < 80:
            return True
        cpt = ctypes.wintypes.POINT(0, 0)
        user32.ClientToScreen(hwnd, ctypes.byref(cpt))
        hmon = user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
        mi = _MONITORINFO()
        mi.cbSize = ctypes.sizeof(mi)
        user32.GetMonitorInfoW(hmon, ctypes.byref(mi))
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, cls, 64)
        title = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, title, 256)
        info = procs.get(pid.value)
        wins.append((pid.value, info[1] if info else "", cls.value,
                     title.value.lower(),
                     (r.left, r.top, r.right, r.bottom,
                      max(0, cpt.y - r.top), mi.rcWork.top, hwnd)))
        return True

    user32.EnumWindows(cb, 0)

    def win_of(pred):
        for w in wins:
            if pred(w):
                return w[4]
        return None

    # 1) Codex 桌面 App 自身窗口
    r = win_of(lambda w: w[1] == "codex")
    if r:
        return r

    # 2) CLI 宿主：沿每条 codex.exe 父链找最近的有窗口祖先
    for ppid, name in procs.values():
        if name != "codex":
            continue
        cur, seen = ppid, set()
        while cur and cur not in seen and len(seen) < 8:
            seen.add(cur)
            info = procs.get(cur)
            if not info:
                break
            anc_ppid, anc_name = info
            if anc_name not in _HOST_SKIP_EXES:
                r = win_of(lambda w, p=cur: w[0] == p)
                if r:
                    return r
            cur = anc_ppid

    # 3) 经典控制台：可见的 ConsoleWindowClass 归 conhost，其宿主在链上
    chain = set()
    for ppid, name in procs.values():
        if name != "codex":
            continue
        cur = ppid
        while cur and cur not in chain and len(chain) < 64:
            chain.add(cur)
            cur = procs.get(cur, (0, ""))[0]
    r = win_of(lambda w: w[2] == "ConsoleWindowClass" and
               procs.get(w[0], (0, ""))[1] in ("conhost", "openconsole") and
               procs.get(w[0], (0, ""))[0] in chain)
    if r:
        return r

    # 4) 终端标题含 codex
    return win_of(lambda w: w[1] in _TERMINAL_EXES and "codex" in w[3])


class _GUITHREADINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("flags", ctypes.c_uint),
                ("hwndActive", ctypes.wintypes.HWND),
                ("hwndFocus", ctypes.wintypes.HWND),
                ("hwndCapture", ctypes.wintypes.HWND),
                ("hwndMenuOwner", ctypes.wintypes.HWND),
                ("hwndMoveSize", ctypes.wintypes.HWND),
                ("hwndCaret", ctypes.wintypes.HWND),
                ("rcCaret", ctypes.wintypes.RECT)]


def drag_target_exe():
    """正在被鼠标拖动的窗口所属进程名（小写、无 .exe）；不在拖动则 None。

    用前台线程 GUITHREADINFO 的 GUI_INMOVESIZE 标志判定，标题栏拖动与
    系统菜单移动都算；单次调用极廉价，适合高频轮询。
    """
    user32 = ctypes.windll.user32
    gti = _GUITHREADINFO()
    gti.cbSize = ctypes.sizeof(gti)
    if not user32.GetGUIThreadInfo(0, ctypes.byref(gti)):
        return None
    if not (gti.flags & 0x2) or not gti.hwndMoveSize:  # GUI_INMOVESIZE
        return None
    pid = ctypes.wintypes.DWORD()
    user32.GetWindowThreadProcessId(gti.hwndMoveSize, ctypes.byref(pid))
    info = _process_map().get(pid.value)
    return info[1] if info else None


try:
    _dwmapi = ctypes.windll.dwmapi
except OSError:
    _dwmapi = None


def occluder_over(rect, below_hwnd, skip_hwnd):
    """rect 区域上方是否存在可见的其他窗口（把 Codex 顶栏挡住）。

    以 below_hwnd（要保护的 Codex 窗口）为锚，用 GetWindow(GW_HWNDPREV)
    沿 z 序向上走，遇到与 rect 相交的可见窗口即视为遮挡。skip_hwnd 是
    浮窗自己；最小化与 DWM cloaked 的不可见幽灵窗口不参与判定。锚定
    遍历不依赖 EnumWindows 的全局顺序，Codex 之下的桌面层永远够不到。
    """
    user32 = ctypes.windll.user32
    if not user32.IsWindow(below_hwnd):
        return False
    l, t, r, b = rect
    hwnd = user32.GetWindow(below_hwnd, 3)  # GW_HWNDPREV：z 序中的上一个（更高）
    while hwnd:
        if hwnd != skip_hwnd and user32.IsWindowVisible(hwnd) \
                and not user32.IsIconic(hwnd):
            if _dwmapi is not None:
                v = ctypes.c_int(0)
                if _dwmapi.DwmGetWindowAttribute(
                        hwnd, 14, ctypes.byref(v), 4) == 0 and v.value:
                    hwnd = user32.GetWindow(hwnd, 3)
                    continue  # DWMWA_CLOAKED：屏上不可见
            rc = ctypes.wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rc))
            if rc.left < r and rc.right > l and rc.top < b and rc.bottom > t:
                return True
        hwnd = user32.GetWindow(hwnd, 3)
    return False


# ---- 托盘图标：独立线程自建隐藏窗口，纯 ctypes 无第三方依赖 ----

_WM_TRAY = 0x0400 + 7   # WM_APP+7：托盘回调消息
_NIM_ADD, _NIM_DELETE = 0, 2
_TIP = "Codex 额度浮窗"
_TRAY = {'hwnd': None, 'nid': None}


class _NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint),
                ("hWnd", ctypes.wintypes.HWND),
                ("uID", ctypes.c_uint),
                ("uFlags", ctypes.c_uint),
                ("uCallbackMessage", ctypes.c_uint),
                ("hIcon", ctypes.wintypes.HICON),
                ("szTip", ctypes.c_wchar * 128),
                ("dwState", ctypes.c_uint),
                ("dwStateMask", ctypes.c_uint),
                ("szInfo", ctypes.c_wchar * 256),
                ("uVersion", ctypes.c_uint),
                ("szInfoTitle", ctypes.c_wchar * 64),
                ("dwInfoFlags", ctypes.c_uint)]


def _tray_thread(q):
    """托盘线程：隐藏窗口收回调，左键找回浮窗，右键原生弹出菜单。"""
    try:
        _tray_run(q)
    except Exception:
        import traceback
        log("tray thread crash: %s" % traceback.format_exc())


def _tray_run(q):
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_longlong, ctypes.wintypes.HWND,
                                 ctypes.wintypes.UINT, ctypes.wintypes.WPARAM,
                                 ctypes.wintypes.LPARAM)

    class WNDCLASSW(ctypes.Structure):
        _fields_ = [("style", ctypes.c_uint), ("lpfnWndProc", WNDPROC),
                    ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                    ("hInstance", ctypes.wintypes.HINSTANCE),
                    ("hIcon", ctypes.wintypes.HICON), ("hCursor", ctypes.wintypes.HANDLE),
                    ("hbrBackground", ctypes.wintypes.HBRUSH),
                    ("lpszMenuName", ctypes.wintypes.LPCWSTR),
                    ("lpszClassName", ctypes.wintypes.LPCWSTR)]

    nid = _NOTIFYICONDATAW()
    nid.cbSize = ctypes.sizeof(nid)
    nid.uID = 1
    nid.uFlags = 0x1 | 0x2 | 0x4   # NIF_MESSAGE | NIF_ICON | NIF_TIP
    nid.uCallbackMessage = _WM_TRAY
    user32.LoadIconW.restype = ctypes.wintypes.HICON
    kernel32.GetModuleHandleW.restype = ctypes.wintypes.HMODULE
    user32.LoadIconW.argtypes = [ctypes.wintypes.HINSTANCE,
                                 ctypes.wintypes.LPVOID]
    # 打包成 exe 后优先用其内置图标；源码运行退回通用应用图标
    nid.hIcon = user32.LoadIconW(kernel32.GetModuleHandleW(None), 1) \
        or user32.LoadIconW(None, 32512)   # IDI_APPLICATION
    nid.szTip = _TIP
    taskbar_created = user32.RegisterWindowMessageW("TaskbarCreated")
    shell32 = ctypes.windll.shell32
    user32.CreateWindowExW.argtypes = [
        ctypes.wintypes.DWORD, ctypes.wintypes.LPCWSTR,
        ctypes.wintypes.LPCWSTR, ctypes.wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        ctypes.wintypes.HWND, ctypes.wintypes.HMENU,
        ctypes.wintypes.HINSTANCE, ctypes.wintypes.LPVOID]
    user32.CreateWindowExW.restype = ctypes.wintypes.HWND
    user32.DefWindowProcW.argtypes = [ctypes.wintypes.HWND,
                                      ctypes.wintypes.UINT,
                                      ctypes.wintypes.WPARAM,
                                      ctypes.wintypes.LPARAM]
    user32.DefWindowProcW.restype = ctypes.c_longlong

    menu = user32.CreatePopupMenu()
    user32.AppendMenuW(menu, 0, 1, "找回浮窗")
    user32.AppendMenuW(menu, 0, 2, "立即刷新")
    user32.AppendMenuW(menu, 0x00000800, 0, None)   # MF_SEPARATOR
    user32.AppendMenuW(menu, 0, 3, "退出")

    def wndproc(hwnd, msg, wparam, lparam):
        if msg == _WM_TRAY:
            try:
                if lparam == 0x0202:            # WM_LBUTTONUP：找回浮窗
                    q.put(("tray", "show", None, None))
                elif lparam == 0x0205:          # WM_RBUTTONUP：弹出菜单
                    user32.SetForegroundWindow(hwnd)
                    pt = ctypes.wintypes.POINT()
                    user32.GetCursorPos(ctypes.byref(pt))
                    cmd = user32.TrackPopupMenu(menu, 0x0100 | 0x2,
                                                pt.x, pt.y, 0, hwnd, None)
                    user32.PostMessageW(hwnd, 0, 0, 0)   # WM_NULL 消化前台标记
                    q.put(("tray", {1: "show", 2: "refresh",
                                    3: "quit"}.get(cmd), None, None))
            except Exception:
                pass
            return 0
        if msg == taskbar_created:               # explorer 重启后补挂图标
            shell32.Shell_NotifyIconW(_NIM_ADD, ctypes.byref(nid))
            return 0
        if msg == 0x0010:                        # WM_CLOSE
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    proc = WNDPROC(wndproc)
    wc = WNDCLASSW()
    wc.lpfnWndProc = proc
    wc.hInstance = kernel32.GetModuleHandleW(None)
    wc.lpszClassName = "CodexQuotaWidgetTray"
    user32.RegisterClassW(ctypes.byref(wc))
    hwnd = user32.CreateWindowExW(0, wc.lpszClassName, _TIP, 0,
                                  0, 0, 0, 0, None, None, wc.hInstance, None)
    nid.hWnd = hwnd
    _TRAY['hwnd'] = hwnd
    _TRAY['nid'] = nid
    _TRAY['added'] = bool(shell32.Shell_NotifyIconW(_NIM_ADD, ctypes.byref(nid)))
    if not _TRAY['added']:
        log("tray add failed err=%s" % kernel32.GetLastError())
    else:
        log("tray icon added hwnd=%s" % nid.hWnd)

    msg = ctypes.wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))


def enable_dpi_awareness():
    """按真实 DPI 原生渲染，避免 Windows 对整窗位图拉伸导致文字发虚。"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def apply_dpi_scaling(root):
    """按窗口所在显示器的真实 DPI 校正 tk 的点->像素换算。"""
    try:
        dpi = ctypes.windll.shcore.GetDpiForWindow(root.winfo_id())
    except Exception:
        try:
            dpi = ctypes.windll.user32.GetDpiForSystem()
        except Exception:
            dpi = 96
    root.tk.call("tk", "scaling", dpi / 72.0)
    log("dpi=%s tk-scaling=%.2f" % (dpi, dpi / 72.0))


class App:
    def __init__(self, root):
        self.root = root
        self.q = queue.Queue()
        self.data = None
        self.via = None
        self.error = None
        self.updated = None
        self.collapsed = False
        self.parsed = {}

        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.attributes("-alpha", 0.96)
        root.configure(bg=BG)

        self.base = {"title": 11, "norm": 10, "small": 9,
                     "bar_w": 95, "bar_h": 8, "cbar_w": 20, "cbar_h": 7}
        self.scale = 1.0
        self.follow_var = tk.BooleanVar(value=False)
        self.pin_var = tk.BooleanVar(value=False)  # 固定跟随：最大化回默认位，常规可拖调
        self._pin_custom = None    # 常规（非最大化）状态的钉住位 (x, 底边y)
        self._pin_max = None       # 最大化状态的钉住位（首次进默认位，之后记住调整）
        self._pin_abs = (0, 0)     # 当前绝对位置
        self._pin_was_zoomed = False
        self._last_rect = None
        self.remain_var = tk.BooleanVar(value=False)  # True=显示剩余额度
        self._c_err_shown = False
        self._rz = None
        self._follow_hidden = False
        self._follow_miss = 0
        self._dragging = False
        self.font_title = tkfont.Font(root=root, family="Microsoft YaHei UI",
                                      size=self.base["title"], weight="bold")
        self.font_norm = tkfont.Font(root=root, family="Microsoft YaHei UI",
                                     size=self.base["norm"])
        self.font_small = tkfont.Font(root=root, family="Microsoft YaHei UI",
                                      size=self.base["small"])
        self.f_title = tk.Label(root, text="Codex 获取中…",
                                font=self.font_title, bg=BG, fg=FG,
                                cursor="fleur", anchor="center")
        self.f_title.pack(fill="x", padx=10, pady=(6, 3))
        self.rows = {}
        for key, name in (("primary", "5h"), ("secondary", "周")):
            row = tk.Frame(root, bg=BG)
            tk.Label(row, text=name, font=self.font_norm, bg=BG, fg=DIM,
                     width=3, anchor="w").pack(side="left")
            bar = tk.Canvas(row, width=self.base["bar_w"],
                            height=self.base["bar_h"], bg=BG,
                            highlightthickness=0)
            bar.pack(side="left", padx=(0, 5))
            val = tk.Label(row, text="--", font=self.font_norm, bg=BG, fg=FG,
                           width=5, anchor="e")
            val.pack(side="left")
            reset = tk.Label(row, text="", font=self.font_small, bg=BG,
                             fg=DIM)
            reset.pack(side="left", padx=(7, 0))
            row.pack(fill="x", padx=10, pady=1)
            self.rows[key] = (bar, val, reset)
        self.f_status = tk.Label(root, text="获取中…", font=self.font_small,
                                 bg=BG, fg=DIM, anchor="w")
        self.f_status.pack(fill="x", padx=10, pady=(3, 6))

        # 跟随模式的紧凑单行布局（默认隐藏，开启跟随后排他显示）
        self.c_frame = tk.Frame(root, bg=BG)
        self.compact = {}
        for key, name in (("primary", "5h"), ("secondary", "周")):
            if key == "secondary":
                tk.Label(self.c_frame, text="│", font=self.font_norm, bg=BG,
                         fg="#454545").pack(side="left", padx=3)
            tk.Label(self.c_frame, text=name, font=self.font_norm, bg=BG,
                     fg=DIM).pack(side="left", padx=(0, 2))
            cbar = tk.Canvas(self.c_frame, width=self.base["cbar_w"],
                             height=self.base["cbar_h"], bg=BG,
                             highlightthickness=0)
            cbar.pack(side="left")
            cval = tk.Label(self.c_frame, text="--", font=self.font_norm,
                            bg=BG, fg=FG, width=5, anchor="w")
            cval.pack(side="left", padx=(2, 0))
            creset = tk.Label(self.c_frame, text="", font=self.font_small,
                              bg=BG, fg=DIM)
            creset.pack(side="left", padx=(4, 0))
            self.compact[key] = (cbar, cval, creset)
        self.c_err = tk.Label(root, text="", font=self.font_small, bg=BG,
                              fg=ORANGE, anchor="w")

        # 隐形缩放握把：覆盖在四周留白上，拖右缘/底缘/右下角可等比调整大小
        self.h_right = tk.Frame(root, bg=BG, cursor="sb_h_double_arrow")
        self.h_bottom = tk.Frame(root, bg=BG, cursor="sb_v_double_arrow")
        self.h_corner = tk.Frame(root, bg=BG, cursor="bottom_right_corner")
        self.h_right.place(relx=1.0, x=-6, y=0, width=6, relheight=1.0)
        self.h_bottom.place(x=0, rely=1.0, y=-6, relwidth=1.0, height=6)
        self.h_corner.place(relx=1.0, rely=1.0, x=-6, y=-6, width=6, height=6)
        for mode, wgt in (("right", self.h_right),
                          ("bottom", self.h_bottom),
                          ("corner", self.h_corner)):
            wgt._is_resize = True
            wgt.bind("<ButtonPress-1>",
                     lambda e, m=mode: self._resize_press(e, m))
            wgt.bind("<B1-Motion>", self._resize_move)

        menu = tk.Menu(root, tearoff=0)
        menu.add_command(label="立即刷新", command=self.refresh_async)
        menu.add_checkbutton(label="跟随 Codex 窗口",
                             variable=self.follow_var,
                             command=self._on_follow_toggle)
        menu.add_checkbutton(label="固定跟随",
                             variable=self.pin_var,
                             command=self._on_pin_toggle)
        menu.add_checkbutton(label="显示剩余额度",
                             variable=self.remain_var,
                             command=self.apply)
        menu.add_command(label="折叠 / 展开", command=self.toggle_collapse)
        menu.add_separator()
        menu.add_command(label="退出", command=self._quit)

        root.bind_all("<ButtonPress-1>", self._press, add="+")
        root.bind_all("<B1-Motion>", self._move, add="+")
        root.bind_all("<Double-Button-1>", self._double, add="+")
        root.bind_all("<Button-3>", self._popup_menu(menu), add="+")

        root.update_idletasks()
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        w, h = root.winfo_reqwidth(), root.winfo_reqheight()
        root.geometry("+%d+%d" % (sw - w - 40, sh - h - 90))
        self._self_hwnd = ctypes.windll.user32.GetAncestor(root.winfo_id(), 2)

        root.after(400, self._pump)
        root.after(30000, self._refresh_countdowns)
        root.after(FOLLOW_INTERVAL_MS, self._follow_tick)
        root.after(DRAG_INTERVAL_MS, self._drag_tick)
        threading.Thread(target=_tray_thread, args=(self.q,),
                         daemon=True).start()
        self.refresh_async()
        root.after(POLL_SECONDS * 1000, self._loop)

    # ---- 网络轮询（后台线程） ----

    def _loop(self):
        self.refresh_async()
        self.root.after(POLL_SECONDS * 1000, self._loop)

    def refresh_async(self):
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        try:
            data, via = fetch_usage()
            self.q.put(("ok", data, via, None))
        except Exception as e:
            self.q.put(("err", None, None, str(e)))

    def _pump(self):
        try:
            while True:
                kind, data, via, err = self.q.get_nowait()
                if kind == "tray":
                    self._on_tray(data)
                    continue
                if kind == "ok":
                    self.data, self.via, self.error = data, via, None
                    self.updated = time.strftime("%H:%M:%S")
                    log("ok via %s plan=%s" % (via, data.get("plan_type")))
                else:
                    self.error = err
                    log("failed: %s" % err)
                self.apply()
        except queue.Empty:
            pass
        self.root.after(400, self._pump)

    # ---- 界面 ----

    def apply(self):
        d = self.data or {}
        plan = (d.get("plan_type") or "").strip().capitalize() or "?"
        rl = d.get("rate_limit") or {}
        reached = bool(rl.get("limit_reached"))
        self.parsed = {}
        for key in ("primary", "secondary"):
            info = parse_window(rl.get(key + "_window"))
            self.parsed[key] = info
            used = info["used"] if info else None
            bar, val, reset = self.rows[key]
            cbar, cval, creset = self.compact[key]
            if used is not None:
                show = 100.0 - used if self.remain_var.get() else used
                pct = "%.0f%%" % show
                # 警告色阈值恒按已用比例算：剩余模式只换数字与填充量
                color = RED if reached else (
                    GREEN if used < 70 else ORANGE if used < 90 else RED)
                self.draw_bar(bar, show, color)
                self.draw_bar(cbar, show, color)
                val.config(text=pct)
                cval.config(text=pct, fg=color)
                reset.config(text=self.reset_text(info["reset"]))
                creset.config(text=reset_text_compact(info["reset"]))
            else:
                self.draw_bar(bar, None)
                self.draw_bar(cbar, None)
                val.config(text="--")
                cval.config(text="--", fg=FG)
                reset.config(text="无数据")
                creset.config(text="—")
        title = "Codex · " + plan + ("（已达上限）" if reached else "")
        self.f_title.config(text=title + ("  ⚠" if self.error else ""))
        if self.error:
            msg = self.error
            hint = " → 先运行一次 codex 刷新登录" if "HTTP 401" in msg else ""
            self.f_status.config(text="⚠ " + msg.split("；")[0][:44] + hint,
                                 fg=ORANGE)
        else:
            via_text = "直连" if self.via == "直连" else ("代理" if self.via else "")
            self.f_status.config(
                text="更新 %s · %s" % (self.updated or "--", via_text),
                fg=DIM)
        self._show_compact_error()

    def _show_compact_error(self):
        """跟随模式下没有状态行，错误临时替换紧凑单行的内容。"""
        want = bool(self.error) and self.follow_var.get()
        if want == self._c_err_shown:
            return
        self._c_err_shown = want
        pad = max(6, int(round(10 * self.scale)))
        if want:
            hint = " → 运行一次 codex 刷新登录" if "HTTP 401" in self.error else ""
            self.c_err.config(text="⚠ " + self.error.split("；")[0][:60] + hint)
            self.c_frame.pack_forget()
            self.c_err.pack(fill="x", padx=max(5, int(round(8 * self.scale))),
                            pady=(5, 5))
        elif self.follow_var.get():
            self.c_err.pack_forget()
            self.c_frame.pack(fill="x", padx=max(5, int(round(8 * self.scale))),
                              pady=(5, 5))

    @staticmethod
    def draw_bar(bar, fill, color=FG):
        bar.delete("all")
        w, h = int(bar["width"]), int(bar["height"])
        bar.create_rectangle(0, 0, w, h, fill=TRACK, outline="")
        if fill is not None:
            bar.create_rectangle(0, 0, max(2, int(w * min(fill, 100) / 100)),
                                 h, fill=color, outline="")

    @staticmethod
    def reset_text(epoch):
        return reset_text(epoch)

    def _refresh_countdowns(self):
        for key, info in self.parsed.items():
            if info:
                bar, val, reset = self.rows[key]
                reset.config(text=self.reset_text(info["reset"]))
                cbar, cval, creset = self.compact[key]
                creset.config(text=reset_text_compact(info["reset"]))
        self.root.after(30000, self._refresh_countdowns)

    # ---- 交互 ----

    def toggle_collapse(self):
        if self.follow_var.get():
            return  # 紧凑单行没有可折叠内容
        # 收回/展开保持水平中心不动：标题文字保持原位，宽度变化向两侧对称
        cx = self.root.winfo_x() + self.root.winfo_width() / 2
        self.collapsed = not self.collapsed
        pad = max(6, int(round(10 * self.scale)))
        for bar, val, reset in self.rows.values():
            if self.collapsed:
                bar.master.pack_forget()
            else:
                bar.master.pack(fill="x", padx=pad, pady=1)
        if self.collapsed:
            self.f_status.pack_forget()
        else:
            self.f_status.pack(fill="x", padx=pad,
                               pady=(int(3 * self.scale),
                                     int(6 * self.scale)))
        self.root.update_idletasks()
        nw = self.root.winfo_reqwidth()
        nx = round(cx - nw / 2)
        ny = self.root.winfo_y()
        self.root.geometry("+%d+%d" % (nx, ny))
        if self.pin_var.get():
            # 固定跟随：以"Codex·Plus"标题为锚原地收回/展开，并更新钉住记录
            self._pin_abs = (nx, ny)
            bottom = ny + max(1, self.root.winfo_reqheight())
            zoomed = self._last_rect is not None and bool(
                ctypes.windll.user32.IsZoomed(self._last_rect[6]))
            if zoomed:
                self._pin_max = (nx, bottom)
            else:
                self._pin_custom = (nx, bottom)

    # ---- 跟随模式 ----

    def _on_follow_toggle(self):
        if self.follow_var.get() and self.pin_var.get():
            self.pin_var.set(False)  # 两种跟随互斥
        self._set_layout(compact=self.follow_var.get())
        if self.follow_var.get():
            self._dragging = False
            self._follow_tick()
        elif self._follow_hidden:
            self._follow_hidden = False
            self._follow_miss = 0
            self.root.deiconify()  # 关闭跟随时若已隐藏，恢复显示

    def _on_pin_toggle(self):
        if self.pin_var.get():
            if self.follow_var.get():
                self.follow_var.set(False)
                self._on_follow_toggle()  # 还原常规布局并处理隐藏态
            self._pin_custom = None   # 启用即回默认位（左下角头像上方）
            self._pin_max = None      # 最大化槽位同样回默认位
            self._last_rect = None    # 首个同步不计算位移增量
            self._pin_was_zoomed = False
            self._dragging = False
            self._follow_miss = 0
            self._follow_tick()
        elif self._follow_hidden:
            self._follow_hidden = False
            self._follow_miss = 0
            self.root.deiconify()

    def _set_layout(self, compact):
        """在常规布局与跟随紧凑单行间切换，水平中心保持不动。"""
        cx = self.root.winfo_x() + self.root.winfo_width() / 2
        pad = max(6, int(round(10 * self.scale)))
        for bar, _, _ in self.rows.values():
            bar.master.pack_forget()
        self.f_title.pack_forget()
        self.f_status.pack_forget()
        self.c_frame.pack_forget()
        self.c_err.pack_forget()
        self._c_err_shown = False
        if compact:
            pad_c = max(5, int(round(8 * self.scale)))
            self.c_frame.pack(fill="x", padx=pad_c, pady=(5, 5))
        else:
            self.f_title.pack(fill="x", padx=pad,
                              pady=(int(6 * self.scale), int(3 * self.scale)))
            if not self.collapsed:
                for bar, _, _ in self.rows.values():
                    bar.master.pack(fill="x", padx=pad, pady=1)
                self.f_status.pack(fill="x", padx=pad,
                                   pady=(int(3 * self.scale),
                                         int(6 * self.scale)))
        self.root.update_idletasks()
        nw = self.root.winfo_reqwidth()
        self.root.geometry("+%d+%d" % (round(cx - nw / 2),
                                       self.root.winfo_y()))
        self._show_compact_error()

    def _follow_tick(self):
        self.root.after(FOLLOW_INTERVAL_MS, self._follow_tick)
        if not (self.follow_var.get() or self.pin_var.get()) or self._dragging:
            return  # 拖动期间由 _drag_tick 全权接管
        rect = find_codex_rect()
        if not rect:
            self._follow_miss += 1
            # 连续 2 拍找不到才隐藏：进程快照偶发失败不至于闪隐
            if self._follow_miss >= 2 and not self._follow_hidden:
                self._follow_hidden = True
                self.root.withdraw()  # Codex 无可见窗口：浮窗随之隐藏
            return
        self._follow_miss = 0
        self._sync_follow(rect)

    def _dock_rect(self, rect):
        """浮窗停靠在 Codex 顶栏时的目标矩形 (x1, y1, x2, y2)。"""
        l, t, r, b, nc_top, work_top, _hwnd = rect
        w = max(1, self.root.winfo_width())
        h = max(1, self.root.winfo_height())
        x = round((l + r) / 2 - w / 2)
        # 嵌入 Codex 顶栏：垂直居中于其非客户区顶部，随窗口 DPI 自适应
        y = t + max(0, (nc_top - h) // 2)
        if y < work_top:
            y = work_top  # 最大化/贴顶：不越过屏幕工作区顶边
        return (x, y, x + w, y + h)

    def _is_occluded(self, rect):
        """浮窗所在区域是否被其他窗口盖住（固定跟随看自身位置，否则看停靠区）。"""
        if self.pin_var.get():
            x, y = self.root.winfo_x(), self.root.winfo_y()
            area = (x, y, x + max(1, self.root.winfo_width()),
                    y + max(1, self.root.winfo_height()))
        else:
            area = self._dock_rect(rect)
        return occluder_over(area, rect[6], self._self_hwnd)

    def _pin_pos(self, rect):
        """固定跟随定位（两套位置各自记忆）：最大化用最大化槽位（首次进
        默认位=左下角头像上方，之后记住拖动调整）；常规用窗口化槽位
        （跟随窗口移动/缩放，可拖动调节）。底边固定——折叠不瞬移。"""
        user32 = ctypes.windll.user32
        h = max(1, self.root.winfo_height())
        zoomed = bool(user32.IsZoomed(rect[6]))
        if zoomed:
            if self._pin_max is None:
                x, y_top = self._default_pin_pos(rect)
                self._pin_max = (x, y_top + h)  # 默认位返回顶边，槽位存底边
            x, bottom = self._pin_max
            pos = self._clamp_pin(rect, x, bottom - h)
            self._pin_max = (pos[0], pos[1] + h)
            self._pin_abs = pos
            return pos
        was_zoomed = self._pin_was_zoomed
        self._pin_was_zoomed = False
        if self._pin_custom is None:
            x, y_top = self._default_pin_pos(rect)
            self._pin_custom = (x, y_top + max(1, self.root.winfo_height()))
        elif self._last_rect is not None and not was_zoomed:
            pl, pt, pr, pb = self._last_rect[:4]
            l, t, r, b = rect[:4]
            size_jump = (abs((r - l) - (pr - pl)) > 150
                         or abs((b - t) - (pb - pt)) > 150)
            if not size_jump:
                # 常规状态：窗口移动/小幅缩放时跟随位移（最大化/还原切换拍跳过）
                self._pin_custom = (self._pin_custom[0] + rect[0] - pl,
                                    self._pin_custom[1] + rect[1] - pt)
        x, bottom = self._pin_custom
        pos = self._clamp_pin(rect, x, bottom - h)
        self._pin_custom = (pos[0], pos[1] + h)
        self._pin_abs = pos
        return pos

    def _default_pin_pos(self, rect):
        """固定跟随默认位：左下角、头像上方（底边距可见底边 GAP，再上移 8px）。"""
        l, t, r, b = rect[:4]
        h = max(1, self.root.winfo_height())
        return self._clamp_pin(rect, l + _PIN_DEFAULT_LEFT,
                               b - 8 - _PIN_DEFAULT_GAP - h)

    def _clamp_pin(self, rect, x, y):
        """固定跟随位置钳回 Codex 所在显示器的工作区，防止最大化等场景溢出屏幕。"""
        user32 = ctypes.windll.user32
        mi = _MONITORINFO()
        mi.cbSize = ctypes.sizeof(mi)
        hmon = user32.MonitorFromWindow(rect[6], 2)
        if hmon and user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            w = max(1, self.root.winfo_width())
            h = max(1, self.root.winfo_height())
            x = min(max(x, mi.rcWork.left), max(mi.rcWork.left, mi.rcWork.right - w))
            y = min(max(y, mi.rcWork.top), max(mi.rcWork.top, mi.rcWork.bottom - h))
        return x, y

    def _sync_follow(self, rect):
        """按 Codex 当前矩形同步浮窗：被遮挡则暂时消失，否则定位显形。"""
        if self._is_occluded(rect):
            if not self._follow_hidden:
                self._follow_hidden = True
                self.root.withdraw()  # Codex 被盖住：浮窗跟着退场
            return
        pos = self._pin_pos(rect) if self.pin_var.get() else None
        self._place_follow(rect, pos)
        self._last_rect = rect   # 同步后更新：下一拍据此算窗口位移增量

    def _place_follow(self, rect, pos=None):
        """定位并显形；隐藏状态下走这里恢复。pos 给定则钉在该处（固定跟随）。"""
        if pos is None:
            pos = self._dock_rect(rect)[:2]
        x, y = pos
        if (self._follow_hidden or
                abs(x - self.root.winfo_x()) > 1 or
                abs(y - self.root.winfo_y()) > 1):
            self.root.geometry("+%d+%d" % (x, y))
        if self._follow_hidden:
            self._follow_hidden = False
            self.root.deiconify()  # 先定位再显形，避免闪现在旧位置

    def _drag_tick(self):
        """高频探针：拖动 Codex 时隐藏浮窗，松手立即贴到新位置顶端。"""
        self.root.after(DRAG_INTERVAL_MS, self._drag_tick)
        if not (self.follow_var.get() or self.pin_var.get()):
            self._dragging = False
            return
        exe = drag_target_exe()
        dragging = exe is not None and exe in _DRAG_HIDE_EXES
        if dragging and not self._dragging:
            self._dragging = True
            self._follow_miss = 0
            if not self._follow_hidden:
                self._follow_hidden = True
                self.root.withdraw()  # 拖动途中隐藏，消除 1s 节拍的跟随拖影
        elif not dragging and self._dragging:
            self._dragging = False
            rect = find_codex_rect()
            if rect:
                self._sync_follow(rect)  # 松手：贴到新顶端；被遮挡则保持隐藏
            # 找不到（如拖动中被关闭）：保持隐藏，交给 _follow_tick 处理

    def _on_tray(self, action):
        """托盘事件（经队列回到主线程执行）：找回/刷新/退出。"""
        if action == "show":
            self._dragging = False
            self._follow_miss = 0
            self._follow_hidden = False
            self.root.deiconify()  # 隐藏或卡住时从托盘找回
        elif action == "refresh":
            self.refresh_async()
        elif action == "quit":
            self._quit()

    def _quit(self):
        self._tray_remove()
        self.root.destroy()

    def _tray_remove(self):
        nid = _TRAY.get("nid")
        if nid:
            ctypes.windll.shell32.Shell_NotifyIconW(_NIM_DELETE,
                                                    ctypes.byref(nid))
            _TRAY["nid"] = None

    def _press(self, e):
        if getattr(e.widget, "_is_resize", False):
            return
        self._dx = e.x_root - self.root.winfo_x()
        self._dy = e.y_root - self.root.winfo_y()

    def _move(self, e):
        if getattr(e.widget, "_is_resize", False):
            return
        nx, ny = e.x_root - self._dx, e.y_root - self._dy
        self.root.geometry("+%d+%d" % (nx, ny))
        if self.pin_var.get():
            # 固定跟随时手动挪动 = 重新钉住当前状态（最大化/窗口化各自记忆）
            self._pin_abs = (nx, ny)
            bottom = ny + max(1, self.root.winfo_height())
            zoomed = self._last_rect is not None and bool(
                ctypes.windll.user32.IsZoomed(self._last_rect[6]))
            if zoomed:
                self._pin_max = (nx, bottom)
            else:
                self._pin_custom = (nx, bottom)

    def _double(self, e):
        if getattr(e.widget, "_is_resize", False):
            return
        if not self.follow_var.get():
            self.toggle_collapse()

    # ---- 边缘拖拽缩放 ----

    def _resize_press(self, e, mode):
        self._rz = {"mode": mode, "x": e.x_root, "y": e.y_root,
                    "w": max(1, self.root.winfo_width()),
                    "h": max(1, self.root.winfo_height()),
                    "scale": self.scale}

    def _resize_move(self, e):
        rz = self._rz
        if not rz:
            return
        fw = (rz["w"] + e.x_root - rz["x"]) / rz["w"]
        fh = (rz["h"] + e.y_root - rz["y"]) / rz["h"]
        factor = {"right": fw, "bottom": fh,
                  "corner": max(fw, fh)}[rz["mode"]]
        self.apply_scale(max(0.75, min(3.0, rz["scale"] * factor)))

    def apply_scale(self, s):
        if abs(s - self.scale) < 0.02:
            return
        self.scale = s
        b = self.base
        self.font_title.configure(size=max(8, round(b["title"] * s)))
        self.font_norm.configure(size=max(7, round(b["norm"] * s)))
        self.font_small.configure(size=max(6, round(b["small"] * s)))
        bw = int(round(b["bar_w"] * s))
        bh = max(5, int(round(b["bar_h"] * s)))
        pad = max(6, int(round(10 * s)))
        # pack_configure 会把已 pack_forget 的控件重新显示出来，
        # 所以只对当前真正处于 pack 管理下的控件调整边距
        for bar, val, reset in self.rows.values():
            bar.configure(width=bw, height=bh)
            if bar.master.winfo_manager() == "pack":
                bar.master.pack_configure(padx=pad, pady=1)
        cbw = max(12, int(round(b["cbar_w"] * s)))
        cbh = max(5, int(round(b["cbar_h"] * s)))
        for cbar, cval, creset in self.compact.values():
            cbar.configure(width=cbw, height=cbh)
        if self.f_title.winfo_manager() == "pack":
            self.f_title.pack_configure(padx=pad,
                                        pady=(int(6 * s), int(3 * s)))
        if self.f_status.winfo_manager() == "pack":
            self.f_status.pack_configure(padx=pad,
                                         pady=(int(3 * s), int(6 * s)))
        self.apply()

    @staticmethod
    def _popup_menu(menu):
        def handler(e):
            try:
                menu.tk_popup(e.x_root, e.y_root)
            finally:
                menu.grab_release()
        return handler


def main():
    log("start pid=%s debug=%s proxy=%r" % (os.getpid(), DEBUG, PROXY))
    enable_dpi_awareness()
    root = tk.Tk()
    apply_dpi_scaling(root)
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
