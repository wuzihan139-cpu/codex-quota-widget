"""浮窗界面：额度展示、轮询、拖动、缩放、折叠，以及右键菜单。

跟随定位在 follow.py，托盘在 tray.py。这里只负责把它们接到窗口上。
对已经 pack_forget 的控件调用 pack_configure 会把它重新显示出来，
apply_scale 里用 winfo_manager()=="pack" 守卫，改布局时保持这个守卫。
"""

import ctypes
import os
import queue
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import webbrowser

from codex_widget.config import (
    BG, DEBUG, DIM, FG, GREEN, ORANGE, POLL_SECONDS, PROXY, RED, REPO_URL,
    TRACK, log)
from codex_widget.dpi import apply_dpi_scaling, enable_dpi_awareness
from codex_widget.follow import FollowController
from codex_widget.tray import remove_tray, start_tray
from codex_widget.usage import (
    fetch_usage, parse_window, reset_text, reset_text_compact)

# 使用说明正文。仓库链接由 open_usage_help 插在最上方，不写进这段。
_USAGE_HELP = """\
这个浮窗显示 Codex（ChatGPT 计划）的 5 小时额度和每周额度：已用百分比、进度条、重置倒计时。颜色按已用比例：不到 70% 绿色，不到 90% 橙色，达到 90% 红色。

左键按住标题行拖动。拖右缘、底缘或右下角等比缩放（0.75 倍到 3 倍）。双击折叠成一行，再双击展开，水平中心不变。

立即刷新会马上再查一次额度，平时每 60 秒自动查一次。

跟随 Codex 窗口：浮窗贴在 Codex 顶栏，并收成一行。Codex 关闭或最小化后浮窗隐藏，窗口回来再继续跟随。拖动 Codex，或顶栏被别的窗口挡住时，浮窗先让开。

固定跟随与顶栏跟随不能同时开。浮窗保持完整布局，开启时落在 Codex 窗口左下角、头像上方。窗口化和最大化各记一个位置；把浮窗拖到别处，就重新钉住当前这一档。

显示剩余额度：数字和进度条改成剩余，颜色仍按已用比例计算。折叠 / 展开与双击相同。退出会关掉浮窗。

托盘图标在任务栏右下角。左键找回浮窗；右键可以找回、立即刷新或退出。浮窗藏起来时，从这里退出。

标题末尾出现警告标记，或底部状态行报错时，下一轮会自动重试。HTTP 401 时先运行一次 codex 重新登录。某一行显示 --，表示这一档还没有数据。
"""


def _link_click(x0, y0, x1, y1):
    """按下和松开几乎在同一点，才当成点击。拖选文字时不打开链接。"""
    return abs(x1 - x0) <= 3 and abs(y1 - y0) <= 3


def _token_end(text, j):
    """英文、数字、网址片段当作一个整体，避免折行时从中间断开。"""
    ch = text[j]
    if ch.isascii() and (ch.isalnum() or ch in "._:/%+-#%"):
        k = j + 1
        n = len(text)
        while k < n and text[k].isascii() and (
                text[k].isalnum() or text[k] in "._:/%+-#%"):
            k += 1
        return k
    return j + 1


def _wrap_to_pixels(font, text, max_px):
    """按像素把正文排满一行。中文逐字折，英文和数字整段换行。"""
    out = []
    for para in text.split("\n"):
        if para == "":
            out.append("")
            continue
        i = 0
        n = len(para)
        while i < n:
            if para[i] == " ":
                i += 1
                continue
            best = i
            j = i
            while j < n:
                k = _token_end(para, j)
                if font.measure(para[i:k]) <= max_px:
                    best = k
                    j = k
                else:
                    break
            if best == i:
                k = min(n, i + 1)
                while k < n and font.measure(para[i:k + 1]) <= max_px:
                    k += 1
                best = max(k, i + 1)
            out.append(para[i:best].rstrip())
            i = best
    return "\n".join(out)


def open_usage_help(master):
    """打开使用说明。最上方是仓库链接。已经打开时只把原窗口提到前面。"""
    prev = getattr(master, "_usage_help", None)
    if prev is not None:
        try:
            if prev.winfo_exists():
                prev.deiconify()
                prev.lift()
                prev.focus_force()
                return prev
        except tk.TclError:
            pass

    win = tk.Toplevel(master)
    master._usage_help = win
    win.withdraw()
    win.overrideredirect(True)
    win.attributes("-topmost", True)
    win.attributes("-alpha", 0)
    win.configure(bg=BG)

    title_font = tkfont.Font(root=win, family="Microsoft YaHei UI",
                             size=11, weight="bold")
    body_font = tkfont.Font(root=win, family="Microsoft YaHei UI", size=10)
    small_font = tkfont.Font(root=win, family="Microsoft YaHei UI", size=9)

    def _close():
        if getattr(master, "_usage_help", None) is win:
            master._usage_help = None
        win.destroy()

    shell = tk.Frame(win, bg=BG, highlightthickness=1,
                     highlightbackground="#3a3a3a")
    shell.pack(fill="both", expand=True)
    bar = tk.Frame(shell, bg=BG, cursor="fleur")
    bar.pack(fill="x", padx=14, pady=(10, 0))
    heading = tk.Label(bar, text="使用说明", font=title_font, bg=BG, fg=FG,
                       cursor="fleur")
    heading.pack(side="left")
    close = tk.Label(bar, text="关闭", font=small_font, bg=BG, fg=DIM,
                     cursor="hand2")
    close.pack(side="right")
    close.bind("<Button-1>", lambda e: _close())

    def _press_drag(e):
        win._dx = e.x_root - win.winfo_x()
        win._dy = e.y_root - win.winfo_y()

    def _drag(e):
        win.geometry("+%d+%d" % (e.x_root - win._dx, e.y_root - win._dy))

    for widget in (bar, heading):
        widget.bind("<ButtonPress-1>", _press_drag)
        widget.bind("<B1-Motion>", _drag)

    zero = max(1, body_font.measure("0"))
    # 比链接再宽一截，中文少折几行，窗口不用占满屏幕。
    width = max(36, body_font.measure(REPO_URL) // zero + 8)
    # 先按字符宽占住窗口。真正折行在映射后按像素重排，见下方。
    body = tk.Text(shell, width=width, height=8, font=body_font, bg=BG, fg=FG,
                   relief="flat", wrap="none", padx=14, pady=10,
                   highlightthickness=0, borderwidth=0, cursor="xterm",
                   insertbackground=FG, selectbackground="#3c4043",
                   selectforeground=FG)
    body.pack(fill="both", expand=True)
    body.tag_configure("link", foreground="#8ab4f8", underline=True)
    # 先只放链接。整段正文在 wrap=none 下会把窗口撑到一行那么宽。
    body.insert("1.0", REPO_URL, "link")
    win._body = body

    def _link_down(e):
        body._lx, body._ly = e.x, e.y

    def _link_up(e):
        if _link_click(body._lx, body._ly, e.x, e.y):
            webbrowser.open(REPO_URL)

    body.tag_bind("link", "<Enter>", lambda e: body.configure(cursor="hand2"))
    body.tag_bind("link", "<Leave>", lambda e: body.configure(cursor="xterm"))
    body.tag_bind("link", "<ButtonPress-1>", _link_down)
    body.tag_bind("link", "<ButtonRelease-1>", _link_up)

    def _on_key(e):
        # 焦点在正文上，Esc 到不了窗口本身的绑定，这里直接关。
        if e.keysym == "Escape":
            _close()
            return "break"
        if (e.state & 0x4) and e.keysym.lower() in ("c", "a"):
            return
        if e.keysym in ("Left", "Right", "Up", "Down", "Home", "End",
                        "Prior", "Next", "Shift_L", "Shift_R",
                        "Control_L", "Control_R"):
            return
        return "break"

    body.bind("<Key>", _on_key)
    body.bind("<<Paste>>", lambda e: "break")
    body.bind("<<Cut>>", lambda e: "break")
    win.bind("<Escape>", lambda e: _close())
    win.protocol("WM_DELETE_WINDOW", _close)

    # 按词折行会把一整句中文当成一个词，放不下就整段跳下去，右边空一截。
    # 映射后量到真实像素宽度，再逐字排满；英文和数字整段换到下一行。
    def _display_lines():
        shown = body.count("1.0", "end-1c", "displaylines")
        if not shown:
            return 12
        return int(shown[0] if isinstance(shown, tuple) else shown)

    win.deiconify()
    win.update_idletasks()
    # 先按请求宽度摆出来，高度给够，才能量到文字区的真实像素宽度。
    win.geometry("%dx%d+0+0" % (max(200, win.winfo_reqwidth()), 200))
    win.update_idletasks()
    # 文字区宽度 = 控件宽度减去两侧 padx。用 "0" 的字宽估算会偏窄，右边空一截。
    pad = int(body.cget("padx"))
    pixel_w = body.winfo_width()
    if pixel_w < 200:
        pixel_w = int(body.cget("width")) * max(1, body_font.measure("0")) + 2 * pad
    max_px = max(120, pixel_w - 2 * pad)
    wrapped = _wrap_to_pixels(body_font, _USAGE_HELP.strip("\n"), max_px)
    body.delete("1.0", "end")
    body.insert("1.0", REPO_URL, "link")
    body.insert("end", "\n\n" + wrapped)
    line_h = max(1, body_font.metrics("linespace"))
    max_lines = max(12, (win.winfo_screenheight() - 180) // line_h)
    lines = max(8, min(_display_lines(), max_lines))
    body.configure(height=lines)
    win.update_idletasks()
    w, h = win.winfo_reqwidth(), win.winfo_reqheight()
    sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
    x = max(0, (sw - w) // 2)
    y = max(0, (sh - h) // 2)
    # 必须带上宽高。只改位置的话，窗口会停在上面用来量宽度的 200px。
    win.geometry("%dx%d+%d+%d" % (w, h, x, y))
    win.update_idletasks()
    # pady 会吃掉最后一行。每次加一行并按新的请求高度重设窗口，直到最后一行露出来。
    guard = 0
    while body.yview()[1] < 0.995 and lines < max_lines and guard < 3:
        lines += 1
        guard += 1
        body.configure(height=lines)
        win.update_idletasks()
        w, h = win.winfo_reqwidth(), win.winfo_reqheight()
        win.geometry("%dx%d+%d+%d" % (
            w, h, max(0, (sw - w) // 2), max(0, (sh - h) // 2)))
        win.update_idletasks()
    win.attributes("-alpha", 0.98)
    win.lift()
    body.focus_set()
    return win


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
        self.follow = FollowController(root, self.follow_var, self.pin_var,
                                       self._set_layout)
        self.remain_var = tk.BooleanVar(value=False)  # True=显示剩余额度
        self._c_err_shown = False
        self._rz = None
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
                             command=self.follow.on_follow_toggle)
        menu.add_checkbutton(label="固定跟随",
                             variable=self.pin_var,
                             command=self.follow.on_pin_toggle)
        menu.add_checkbutton(label="显示剩余额度",
                             variable=self.remain_var,
                             command=self.apply)
        menu.add_command(label="折叠 / 展开", command=self.toggle_collapse)
        menu.add_separator()
        menu.add_command(label="使用说明", command=lambda: open_usage_help(root))
        menu.add_command(label="退出", command=self._quit)

        root.bind_all("<ButtonPress-1>", self._press, add="+")
        root.bind_all("<B1-Motion>", self._move, add="+")
        root.bind_all("<Double-Button-1>", self._double, add="+")
        root.bind_all("<Button-3>", self._popup_menu(menu), add="+")

        root.update_idletasks()
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        w, h = root.winfo_reqwidth(), root.winfo_reqheight()
        root.geometry("+%d+%d" % (sw - w - 40, sh - h - 90))
        self.follow.bind_hwnd(
            ctypes.windll.user32.GetAncestor(root.winfo_id(), 2))

        root.after(400, self._pump)
        root.after(30000, self._refresh_countdowns)
        self.follow.start()
        start_tray(self.q)
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
            self.follow.remember_anchor(nx, ny)

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

    def _on_tray(self, action):
        """托盘事件（经队列回到主线程执行）：找回/刷新/退出。"""
        if action == "show":
            self.follow.recover()
        elif action == "refresh":
            self.refresh_async()
        elif action == "quit":
            self._quit()

    def _quit(self):
        remove_tray()
        self.root.destroy()

    def _event_on_main(self, e):
        # bind_all 也会收到说明窗里的点击。不拦的话，拖说明或双击正文
        # 会把浮窗一起拖走、折叠。
        widget = getattr(e, "widget", None)
        if isinstance(widget, str):
            try:
                widget = self.root.nametowidget(widget)
            except KeyError:
                return False
        if widget is None:
            return False
        try:
            return widget.winfo_toplevel() is self.root
        except (tk.TclError, AttributeError):
            return False

    def _press(self, e):
        if not self._event_on_main(e):
            return
        if getattr(e.widget, "_is_resize", False):
            return
        self._dx = e.x_root - self.root.winfo_x()
        self._dy = e.y_root - self.root.winfo_y()

    def _move(self, e):
        if not self._event_on_main(e):
            return
        if getattr(e.widget, "_is_resize", False):
            return
        nx, ny = e.x_root - self._dx, e.y_root - self._dy
        self.root.geometry("+%d+%d" % (nx, ny))
        self.follow.remember_drag(nx, ny)

    def _double(self, e):
        if not self._event_on_main(e):
            return
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

    def _popup_menu(self, menu):
        def handler(e):
            if not self._event_on_main(e):
                return
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
