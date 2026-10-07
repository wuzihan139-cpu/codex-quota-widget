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
交互: 左键拖动移动；双击折叠/展开；右键菜单（立即刷新/跟随/显示模式/折叠/退出）。
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
PROXY = os.environ.get("CODEX_WIDGET_PROXY", "http://127.0.0.1:7897")
DEBUG = "--debug" in sys.argv
LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "widget.log")

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

# 宿主追溯时要跳过的系统/外壳进程（避免跟到桌面或托盘上）
_HOST_SKIP_EXES = {"explorer", "system", "idle", "svchost", "services",
                   "csrss", "winlogon", "wininit", "smss", "lsass",
                   "pythonw", "python", "dllhost", "runtimebroker"}


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_ulong),
                ("rcMonitor", ctypes.wintypes.RECT),
                ("rcWork", ctypes.wintypes.RECT),
                ("dwFlags", ctypes.c_ulong)]


def find_codex_rect():
    """返回可跟随的 Codex 窗口 (l, t, r, b, 顶栏高度, 工作区顶 y)，找不到返回 None。

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
                      max(0, cpt.y - r.top), mi.rcWork.top)))
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
        self.remain_var = tk.BooleanVar(value=False)  # True=显示剩余额度
        self._c_err_shown = False
        self._rz = None
        self._follow_hidden = False
        self._follow_miss = 0
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
        menu.add_checkbutton(label="显示剩余额度",
                             variable=self.remain_var,
                             command=self.apply)
        menu.add_command(label="折叠 / 展开", command=self.toggle_collapse)
        menu.add_separator()
        menu.add_command(label="退出", command=root.destroy)

        root.bind_all("<ButtonPress-1>", self._press, add="+")
        root.bind_all("<B1-Motion>", self._move, add="+")
        root.bind_all("<Double-Button-1>", self._double, add="+")
        root.bind_all("<Button-3>", self._popup_menu(menu), add="+")

        root.update_idletasks()
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        w, h = root.winfo_reqwidth(), root.winfo_reqheight()
        root.geometry("+%d+%d" % (sw - w - 40, sh - h - 90))

        root.after(400, self._pump)
        root.after(30000, self._refresh_countdowns)
        root.after(FOLLOW_INTERVAL_MS, self._follow_tick)
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
        self.root.geometry("+%d+%d" % (round(cx - nw / 2),
                                       self.root.winfo_y()))

    # ---- 跟随模式 ----

    def _on_follow_toggle(self):
        self._set_layout(compact=self.follow_var.get())
        if self.follow_var.get():
            self._follow_tick()
        elif self._follow_hidden:
            self._follow_hidden = False
            self._follow_miss = 0
            self.root.deiconify()  # 关闭跟随时若已隐藏，恢复显示

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
        if not self.follow_var.get():
            return
        rect = find_codex_rect()
        if not rect:
            self._follow_miss += 1
            # 连续 2 拍找不到才隐藏：进程快照偶发失败不至于闪隐
            if self._follow_miss >= 2 and not self._follow_hidden:
                self._follow_hidden = True
                self.root.withdraw()  # Codex 无可见窗口：浮窗随之隐藏
            return
        self._follow_miss = 0
        l, t, r, b, nc_top, work_top = rect
        w = max(1, self.root.winfo_width())
        h = max(1, self.root.winfo_height())
        x = round((l + r) / 2 - w / 2)
        # 嵌入 Codex 顶栏：垂直居中于其非客户区顶部，随窗口 DPI 自适应
        y = t + max(0, (nc_top - h) // 2)
        if y < work_top:
            y = work_top  # 最大化/贴顶：不越过屏幕工作区顶边
        if abs(x - self.root.winfo_x()) > 1 or abs(y - self.root.winfo_y()) > 1:
            self.root.geometry("+%d+%d" % (x, y))
        if self._follow_hidden:
            self._follow_hidden = False
            self.root.deiconify()  # Codex 重新出现：先定位再显形并继续跟随

    def _press(self, e):
        if getattr(e.widget, "_is_resize", False):
            return
        self._dx = e.x_root - self.root.winfo_x()
        self._dy = e.y_root - self.root.winfo_y()

    def _move(self, e):
        if getattr(e.widget, "_is_resize", False):
            return
        self.root.geometry("+%d+%d" % (e.x_root - self._dx,
                                       e.y_root - self._dy))

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
