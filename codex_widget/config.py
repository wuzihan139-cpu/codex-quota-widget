"""路径、轮询与外观常量，以及调试日志。

BASE_DIR 是入口脚本（或冻结 exe）所在目录，不是本包目录。
widget.log 要落在脚本旁边；写进包目录或 PyInstaller 临时解包目录都是错的。
"""

import os
import sys
import time

AUTH_PATH = os.path.expanduser("~/.codex/auth.json")
USAGE_URL = "https://chatgpt.com/backend-api/codex/usage"
REPO_URL = "https://github.com/wuzihan139-cpu/codex-quota-widget"
POLL_SECONDS = 60
FOLLOW_INTERVAL_MS = 1000
DRAG_INTERVAL_MS = 120
PROXY_DEFAULT = "http://127.0.0.1:7897"
# 右键「代理设置」写在这里。跟 exe 放一起会在把程序交给别人时带上你的地址。
PROXY_PATH = os.path.join(os.path.dirname(AUTH_PATH), "widget_proxy.txt")
ALPHA_DEFAULT = 0.96
ALPHA_MIN = 0.30
ALPHA_PATH = os.path.join(os.path.dirname(AUTH_PATH), "widget_alpha.txt")
DEBUG = "--debug" in sys.argv
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)  # 打包成 exe 后：exe 所在目录
else:
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_PATH = os.path.join(BASE_DIR, "widget.log")

BG, FG, DIM, TRACK = "#1e1e1e", "#e8eaed", "#9aa0a6", "#333333"
GREEN, ORANGE, RED = "#4caf50", "#ffb74d", "#ef5350"


def _proxy_text(value):
    value = (value or "").strip()
    if value.lower() == "direct":
        return "direct"
    return value


def saved_proxy():
    """本机保存的代理。没有文件或文件是空的时返回空字符串。"""
    try:
        with open(PROXY_PATH, encoding="utf-8") as f:
            return _proxy_text(f.read())
    except OSError:
        return ""


def get_proxy():
    """当前生效的代理。本机保存优先，其次环境变量，最后默认端口。"""
    saved = saved_proxy()
    if saved:
        return saved
    env = _proxy_text(os.environ.get("CODEX_WIDGET_PROXY", ""))
    return env or PROXY_DEFAULT


def set_saved_proxy(value):
    """写入本机代理。空字符串删掉文件，改回环境变量或默认端口。"""
    value = _proxy_text(value)
    if not value:
        try:
            os.remove(PROXY_PATH)
        except OSError:
            pass
        return get_proxy()
    os.makedirs(os.path.dirname(PROXY_PATH), exist_ok=True)
    with open(PROXY_PATH, "w", encoding="utf-8") as f:
        f.write(value)
    return value


def get_alpha():
    """当前透明度，0.30 到 1。没有记录或内容无效时用默认 0.96。"""
    try:
        with open(ALPHA_PATH, encoding="utf-8") as f:
            value = float(f.read().strip())
    except (OSError, ValueError):
        return ALPHA_DEFAULT
    return min(1.0, max(ALPHA_MIN, value))


def set_alpha(value):
    """记下透明度。传入 None 时删掉文件，回到默认 0.96。"""
    if value is None:
        try:
            os.remove(ALPHA_PATH)
        except OSError:
            pass
        return ALPHA_DEFAULT
    value = min(1.0, max(ALPHA_MIN, float(value)))
    os.makedirs(os.path.dirname(ALPHA_PATH), exist_ok=True)
    with open(ALPHA_PATH, "w", encoding="utf-8") as f:
        f.write("%.2f" % value)
    return value


def log(msg):
    if DEBUG:
        try:
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(time.strftime("[%Y-%m-%d %H:%M:%S] ") + msg + "\n")
        except OSError:
            pass
