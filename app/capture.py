# -*- coding: utf-8 -*-
"""找聊天窗口 + Windows Graphics Capture 盯着它 + 从帧里定位消息区。帧全程内存，绝不落盘。"""
import ctypes
import os
import threading
from hashlib import blake2b
import time
from ctypes import wintypes

import numpy as np

from app import chatapps

u32 = ctypes.windll.user32
u32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
u32.GetWindowThreadProcessId.restype = wintypes.DWORD
u32.IsWindow.argtypes = [wintypes.HWND]


def window_identity(hwnd):
    pid = wintypes.DWORD()
    thread = u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return (pid.value, thread) if thread and u32.IsWindow(hwnd) else None


def find_chat_hwnd(want=None):
    """枚举可见微信顶层窗口，优先按标题找到主窗口。"""
    k32 = ctypes.windll.kernel32
    found = []

    def exe_of(pid):
        h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return ""
        buf, size = ctypes.create_unicode_buffer(1024), ctypes.c_uint(1024)
        ok = k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size))
        k32.CloseHandle(h)
        return os.path.basename(buf.value).lower() if ok else ""

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def cb(hwnd, _):
        if not u32.IsWindowVisible(hwnd):
            return True
        pid = ctypes.c_ulong()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        app = chatapps.by_exe(exe_of(pid.value))
        if app and (want is None or app is want):
            title = ctypes.create_unicode_buffer(256)
            u32.GetWindowTextW(hwnd, title, 256)
            rect = wintypes.RECT()
            u32.GetWindowRect(hwnd, ctypes.byref(rect))
            area = (rect.right - rect.left) * (rect.bottom - rect.top)
            found.append((hwnd, title.value, area, app))
        return True

    u32.EnumWindows(cb, 0)
    if not found:
        raise RuntimeError("没找到聊天窗口，开着吗？")
    for app in chatapps.APPS.values():
        mine = [f for f in found if f[3] is app]
        if not mine:
            continue
        if app.main_title:
            hit = next((f for f in mine if f[1] == app.main_title), None)
            if hit:
                return hit[0], app
        best = max(mine, key=lambda f: f[2])
        return best[0], app
    raise RuntimeError("没找到聊天窗口，开着吗？")


def find_wechat_hwnd():
    """老名字：只返回 hwnd，给还没改过来的调用方用。"""
    return find_chat_hwnd()[0]


def unminimize(hwnd):
    """Windows 不渲染最小化的窗口，什么截图法都拿不到画面。发现被最小化就无激活还原，再压到所有窗口最底下——
    看着跟收起来一样，但 DWM 继续画。不抢焦点、不动大小位置。返回是否动了手。"""
    if not u32.IsIconic(hwnd):
        return False
    u32.ShowWindow(hwnd, 4)  # SW_SHOWNOACTIVATE
    u32.SetWindowPos(hwnd, 1, 0, 0, 0, 0, 0x13)  # HWND_BOTTOM, SWP_NOSIZE|SWP_NOMOVE|SWP_NOACTIVATE
    return True


def chat_area(full, header_h=60):
    """消息列表区 (x0, y_top, x1, y_in, 面板底色, y_pane)，全靠像素锚点，不写死坐标，深浅主题通用：
    - 面板底色 = 右半边最常见的颜色（抽样算，全量 np.unique 在 2560 宽的图上要半秒）
    - 面板左/右边界 = 第一/最后一根「底色占比 > 30%」的列（联系人列表是另一种底色，占比 0）
    - y_pane = 面板第一行；会话名就印在 y_pane~y_top 这条头部里（公告条也在里面）
    - 横向分隔线 = 整行单色且非底色；输入框顶 y_in = 面板 45% 高度以下第一根；
      公告条下面那根（有的话）= 消息区顶 y_top，没有就用 header_h
    认不出（窗口太小 / 拖到一半布局没铺好）返回 None。
    ponytail: 输入框拉高超过面板一半会认错；header_h 按 100% DPI 给的，缩放了按比例调。"""
    H, W = full.shape[:2]
    if H < 150 or W < 150 or not full.any():
        return None
    right = full[::8, W // 2::8].reshape(-1, 3)
    vals, cnt = np.unique(right, axis=0, return_counts=True)
    bg = vals[cnt.argmax()]
    isbg = np.abs(full.astype(int) - bg).sum(-1) <= 6
    col = isbg[H // 4: H * 3 // 4].mean(0)
    if not (col > 0.3).any():
        return None
    x0 = int(np.argmax(col > 0.3))
    x1 = W - int(np.argmax(col[::-1] > 0.3))
    if x1 - x0 < 140:
        return None
    row = isbg[:, x0:x1].mean(1)
    if not (row > 0.9).any():
        return None
    y0 = int(np.argmax(row > 0.9))
    y1 = H - int(np.argmax(row[::-1] > 0.9))
    band = full[y0:y1, x0:x1].astype(int)
    seps = y0 + np.where((band.std(axis=(1, 2)) < 4) & (row[y0:y1] < 0.1))[0]
    seps = [int(s) for i, s in enumerate(seps) if i == 0 or s - seps[i - 1] > 3]
    below = [s for s in seps if s > y0 + 0.45 * (y1 - y0)]
    if not below or y1 - below[0] < 60:
        return None
    y_in = below[0]
    above = [s for s in seps if y0 + header_h < s < y_in - 50]
    y_top = above[-1] if above else y0 + header_h
    if x1 - x0 < 100 or y_in - y_top < 40:
        return None
    return x0, y_top, x1, y_in, bg, y0


def layout_signature(full, area):
    """只在内存中比较当前窗口尺寸、标题区和消息区。"""
    x0, y0, x1, y1, _, pane_top = area
    relevant = np.ascontiguousarray(full[pane_top:y1 + 4, x0:x1])
    return (full.shape[:2], (x0, y0, x1, y1, pane_top), blake2b(relevant, digest_size=16).digest())


def fresh_layout(hwnd, timeout=1.0):
    """新建 WGC 会话，获取本次填入过程中才采到的窗口帧。"""
    try:
        cap = Capture(hwnd, settle=0.12, max_wait=timeout)
    except Exception:
        return None
    try:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and cap.alive():
            packet = cap.settled()
            if packet is not None:
                _, frame = packet
                area = chat_area(frame)
                return layout_signature(frame, area) if area is not None else None
            time.sleep(0.02)
        return None
    finally:
        cap.stop()


class Capture:
    """WGC 帧稳定后交给 OCR。原始画面变动时先使父进程中的旧结果失效。"""

    def __init__(self, hwnd, settle=0.25, max_wait=1.0, changed=None):
        from windows_capture import WindowsCapture

        self.settle, self.max_wait = settle, max_wait
        self.area = self.last = self.pending = None
        self.shape = self.latest = None
        self.t = self.t0 = 0.0
        self.changed = changed
        self.lock = threading.Lock()
        # None 保留旧版 Windows 支持。输入区和鼠标位于标题/消息区之外时不会影响比较。
        cap = WindowsCapture(cursor_capture=None, draw_border=None, window_hwnd=hwnd)
        cap.event(self.on_frame_arrived)
        cap.event(self.on_closed)
        self.ctl = cap.start_free_threaded()

    def on_frame_arrived(self, frame, control):
        full = np.ascontiguousarray(frame.frame_buffer[:, :, :3][:, :, ::-1])
        with self.lock:
            if full.shape != self.shape:
                self.shape = full.shape
                self.area = None
            area = self.area
            # 失去布局/黑屏也是状态变化，不能留下此前的已确认帧。
            view = layout_signature(full, area) if area is not None else (
                full.shape, blake2b(full, digest_size=16).digest())
            if view == self.last:
                return
            self.last = view
            stamp = self.changed() if self.changed else None
            now = time.monotonic()
            if self.pending is None:
                self.t0 = now
            self.latest = self.pending = (stamp, full)
            self.t = now

    def on_closed(self):
        if self.changed:
            self.changed()

    def settled(self):
        with self.lock:
            if self.pending is None:
                return None
            now = time.monotonic()
            if now - self.t < self.settle and now - self.t0 < self.max_wait:
                return None
            packet, self.pending = self.pending, None
            return packet

    def set_area(self, area, stamp):
        with self.lock:
            if self.latest is not None and self.latest[0] == stamp:
                self.area = area
                if area is not None:
                    self.last = layout_signature(self.latest[1], area)

    def alive(self):
        return not self.ctl.is_finished()

    def stop(self):
        self.ctl.stop()

    def wait(self):
        self.ctl.wait()
