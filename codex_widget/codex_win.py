"""查找可跟随的 Codex 窗口，并判断它是否正在被拖动或被挡住。

检测优先级（find_codex_rect）：
  1. codex.exe 自己的可见顶层窗口；
  2. 沿 codex.exe 父进程链找最近的有可见窗口的祖先（本机是 chatgpt.exe）；
  3. 父链上的经典控制台 conhost；
  4. 标题含 codex 的终端窗口。
编辑器/IDE（code、code-insiders、devenv）在宿主追溯里跳过：集成终端里的
codex 会话不能把浮窗拽到编辑器上。最小化窗口不跟随。
遮挡判定以目标 hwnd 为锚沿 z 序向上走，不依赖 EnumWindows 的全局顺序。
"""

import ctypes
import ctypes.wintypes

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
DRAG_HIDE_EXES = _TERMINAL_EXES | {"codex", "chatgpt"}


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_ulong),
                ("rcMonitor", ctypes.wintypes.RECT),
                ("rcWork", ctypes.wintypes.RECT),
                ("dwFlags", ctypes.c_ulong)]


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


def find_codex_rect():
    """返回可跟随的 Codex 窗口 (l, t, r, b, 顶栏高度, 工作区顶 y, hwnd)，找不到返回 None。

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


def clamp_to_work(hwnd, x, y, w, h):
    """把矩形左上角钳回 hwnd 所在显示器的工作区。查不到显示器则原样返回。"""
    user32 = ctypes.windll.user32
    mi = _MONITORINFO()
    mi.cbSize = ctypes.sizeof(mi)
    hmon = user32.MonitorFromWindow(hwnd, 2)
    if hmon and user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
        x = min(max(x, mi.rcWork.left), max(mi.rcWork.left, mi.rcWork.right - w))
        y = min(max(y, mi.rcWork.top), max(mi.rcWork.top, mi.rcWork.bottom - h))
    return x, y
