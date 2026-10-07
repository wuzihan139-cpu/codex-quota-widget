"""进程 DPI 感知。不声明的话 Windows 会把整窗当位图拉伸，文字发虚。"""

import ctypes

from codex_widget.config import log


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
