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
交互: 左键拖动移动；双击折叠/展开；右键菜单（立即刷新/折叠/退出）。
环境变量: CODEX_WIDGET_PROXY 覆盖代理（默认 http://127.0.0.1:7897，
         设为 direct 则直连）。
"""

import ctypes
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
PROXY = os.environ.get("CODEX_WIDGET_PROXY", "http://127.0.0.1:7897")
DEBUG = "--debug" in sys.argv
LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "widget.log")

BG, FG, DIM, TRACK = "#16181d", "#e8eaed", "#9aa0a6", "#2a2e34"
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
                     "bar_w": 95, "bar_h": 8}
        self.scale = 1.0
        self._rz = None
        self.font_title = tkfont.Font(root=root, family="Microsoft YaHei UI",
                                      size=self.base["title"], weight="bold")
        self.font_norm = tkfont.Font(root=root, family="Microsoft YaHei UI",
                                     size=self.base["norm"])
        self.font_small = tkfont.Font(root=root, family="Microsoft YaHei UI",
                                      size=self.base["small"])
        self.f_title = tk.Label(root, text="Codex 获取中…",
                                font=self.font_title, bg=BG, fg=FG,
                                cursor="fleur", anchor="w")
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
            bar, val, reset = self.rows[key]
            if info and info["used"] is not None:
                self.draw_bar(bar, info["used"], force_red=reached)
                val.config(text="%.0f%%" % info["used"])
                reset.config(text=self.reset_text(info["reset"]))
            else:
                self.draw_bar(bar, None)
                val.config(text="--")
                reset.config(text="无数据")
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

    @staticmethod
    def draw_bar(bar, used, force_red=False):
        bar.delete("all")
        w, h = int(bar["width"]), int(bar["height"])
        bar.create_rectangle(0, 0, w, h, fill=TRACK, outline="")
        if used is not None:
            color = RED if force_red else (
                GREEN if used < 70 else ORANGE if used < 90 else RED)
            bar.create_rectangle(0, 0, max(2, int(w * min(used, 100) / 100)),
                                 h, fill=color, outline="")

    @staticmethod
    def reset_text(epoch):
        return reset_text(epoch)

    def _refresh_countdowns(self):
        for key, info in self.parsed.items():
            if info:
                bar, val, reset = self.rows[key]
                reset.config(text=self.reset_text(info["reset"]))
        self.root.after(30000, self._refresh_countdowns)

    # ---- 交互 ----

    def toggle_collapse(self):
        self.collapsed = not self.collapsed
        for bar, val, reset in self.rows.values():
            if self.collapsed:
                bar.master.pack_forget()
            else:
                bar.master.pack(fill="x", padx=10, pady=1)
        if self.collapsed:
            self.f_status.pack_forget()
        else:
            self.f_status.pack(fill="x", padx=10, pady=(3, 6))

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
        if not getattr(e.widget, "_is_resize", False):
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
        for bar, val, reset in self.rows.values():
            bar.configure(width=bw, height=bh)
            bar.master.pack_configure(padx=pad, pady=1)
        self.f_title.pack_configure(padx=pad,
                                    pady=(int(6 * s), int(3 * s)))
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
