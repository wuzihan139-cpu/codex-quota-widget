"""顶栏跟随与固定跟随。

两种模式互斥。顶栏跟随把浮窗嵌进 Codex 窗口顶栏并使用紧凑单行
（布局切换由界面回调完成）。固定跟随保持常规布局，两套槽位互不影响：

- 最大化槽位 _pin_max：第一次进入最大化用默认位（左下、头像上方），
  之后记住该状态下的拖动。
- 窗口化槽位 _pin_custom：跟随窗口移动和小幅缩放。单拍尺寸跳变
  >150px 视为最大化/还原切换拍，跳过位移增量。

槽位存的是底边。_default_pin_pos 返回顶边，写入时加高度。
_last_rect 必须在 _sync_follow 完成定位之后更新。提前更新会使
位移增量恒为零，窗口化跟随不再跟着窗口走。拖动松手路径也依赖
这次更新。遮挡时直接返回，不更新 _last_rect。
"""

import ctypes

from codex_widget.codex_win import (
    DRAG_HIDE_EXES, clamp_to_work, drag_target_exe, find_codex_rect,
    occluder_over)
from codex_widget.config import DRAG_INTERVAL_MS, FOLLOW_INTERVAL_MS

_PIN_DEFAULT_GAP = 105    # 固定跟随默认位：浮窗底边距窗口可见底边的像素（头像上方不远处）
_PIN_DEFAULT_LEFT = 8     # 固定跟随默认位：浮窗左边距窗口可见左边的像素


class FollowController:
    def __init__(self, root, follow_var, pin_var, on_layout):
        self.root = root
        self.follow_var = follow_var
        self.pin_var = pin_var
        self.on_layout = on_layout
        self._pin_custom = None    # 常规（非最大化）状态的钉住位 (x, 底边y)
        self._pin_max = None       # 最大化状态的钉住位（首次进默认位，之后记住调整）
        self._pin_abs = (0, 0)     # 当前绝对位置
        self._pin_was_zoomed = False
        self._last_rect = None
        self._follow_hidden = False
        self._follow_miss = 0
        self._dragging = False
        self._self_hwnd = None

    def bind_hwnd(self, hwnd):
        self._self_hwnd = hwnd

    def start(self):
        self.root.after(FOLLOW_INTERVAL_MS, self._follow_tick)
        self.root.after(DRAG_INTERVAL_MS, self._drag_tick)

    def on_follow_toggle(self):
        if self.follow_var.get() and self.pin_var.get():
            self.pin_var.set(False)  # 两种跟随互斥
        self.on_layout(compact=self.follow_var.get())
        if self.follow_var.get():
            self._dragging = False
            self._follow_tick()
        elif self._follow_hidden:
            self._follow_hidden = False
            self._follow_miss = 0
            self.root.deiconify()  # 关闭跟随时若已隐藏，恢复显示

    def on_pin_toggle(self):
        if self.pin_var.get():
            if self.follow_var.get():
                self.follow_var.set(False)
                self.on_follow_toggle()  # 还原常规布局并处理隐藏态
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

    def remember_anchor(self, nx, ny):
        """折叠/展开以标题为锚后，把当前槽位改到新矩形。底边用 reqheight。"""
        self._pin_abs = (nx, ny)
        bottom = ny + max(1, self.root.winfo_reqheight())
        zoomed = self._last_rect is not None and bool(
            ctypes.windll.user32.IsZoomed(self._last_rect[6]))
        if zoomed:
            self._pin_max = (nx, bottom)
        else:
            self._pin_custom = (nx, bottom)

    def remember_drag(self, nx, ny):
        """手动挪动 = 重新钉住当前状态（最大化/窗口化各自记忆）。"""
        if not self.pin_var.get():
            return
        self._pin_abs = (nx, ny)
        bottom = ny + max(1, self.root.winfo_height())
        zoomed = self._last_rect is not None and bool(
            ctypes.windll.user32.IsZoomed(self._last_rect[6]))
        if zoomed:
            self._pin_max = (nx, bottom)
        else:
            self._pin_custom = (nx, bottom)

    def recover(self):
        """托盘找回：清掉隐藏/拖动态并显形。"""
        self._dragging = False
        self._follow_miss = 0
        self._follow_hidden = False
        self.root.deiconify()

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
        w = max(1, self.root.winfo_width())
        h = max(1, self.root.winfo_height())
        return clamp_to_work(rect[6], x, y, w, h)

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
        dragging = exe is not None and exe in DRAG_HIDE_EXES
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
