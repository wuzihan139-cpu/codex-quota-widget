"""托盘图标。独立线程自建隐藏窗口，纯 ctypes，无第三方依赖。

收句柄的 WinAPI（CreateWindowExW / LoadIconW / DefWindowProcW /
GetModuleHandleW）必须设 64 位 argtypes/restype。漏掉的话冻结 exe 里
托盘线程会 OverflowError 静默死亡；noconsole 下线程异常没有窗口，
所以入口用 try/except 把 traceback 写进日志。
"""

import ctypes
import ctypes.wintypes
import threading

from codex_widget.config import log

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


def start_tray(q):
    """后台线程挂托盘。回调经 q 以 ("tray", action, None, None) 回到主线程。"""
    threading.Thread(target=_tray_thread, args=(q,), daemon=True).start()


def remove_tray():
    nid = _TRAY.get("nid")
    if nid:
        ctypes.windll.shell32.Shell_NotifyIconW(_NIM_DELETE, ctypes.byref(nid))
        _TRAY["nid"] = None


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
