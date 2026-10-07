"""路径、轮询与外观常量，以及调试日志。

BASE_DIR 是入口脚本（或冻结 exe）所在目录，不是本包目录。
widget.log 要落在脚本旁边；写进包目录或 PyInstaller 临时解包目录都是错的。
"""

import os
import sys
import time

AUTH_PATH = os.path.expanduser("~/.codex/auth.json")
USAGE_URL = "https://chatgpt.com/backend-api/codex/usage"
POLL_SECONDS = 60
FOLLOW_INTERVAL_MS = 1000
DRAG_INTERVAL_MS = 120
PROXY = os.environ.get("CODEX_WIDGET_PROXY", "http://127.0.0.1:7897")
DEBUG = "--debug" in sys.argv
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)  # 打包成 exe 后：exe 所在目录
else:
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_PATH = os.path.join(BASE_DIR, "widget.log")

BG, FG, DIM, TRACK = "#1e1e1e", "#e8eaed", "#9aa0a6", "#333333"
GREEN, ORANGE, RED = "#4caf50", "#ffb74d", "#ef5350"


def log(msg):
    if DEBUG:
        try:
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(time.strftime("[%Y-%m-%d %H:%M:%S] ") + msg + "\n")
        except OSError:
            pass
