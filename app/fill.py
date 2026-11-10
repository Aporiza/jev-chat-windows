# -*- coding: utf-8 -*-
"""把选中的候选填进微信输入框：写剪贴板 → 点输入框 → Ctrl+V。绝不发回车、绝不点发送。"""
import ctypes
import ctypes.wintypes as w
import time

u32, k32 = ctypes.windll.user32, ctypes.windll.kernel32

# 64 位下 ctypes.windll 默认 restype 是 32 位 c_int，而 GlobalAlloc 返回 64 位 HGLOBAL——
# 不声明类型句柄会被截断成垃圾值，GlobalLock(垃圾) 返回 NULL，memmove(NULL,…) 就是
# "access violation writing 0x0"。所有带句柄/指针的函数必须显式声明。
k32.GlobalAlloc.restype = ctypes.c_void_p
k32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
k32.GlobalLock.restype = ctypes.c_void_p
k32.GlobalLock.argtypes = [ctypes.c_void_p]
k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
k32.GlobalFree.argtypes = [ctypes.c_void_p]
u32.SetClipboardData.restype = ctypes.c_void_p
u32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]


def set_clipboard(text):
    """写剪贴板。剪贴板可能被别的程序占着（剪贴板管理器、截图工具），重试几次。"""
    data = text.encode("utf-16-le") + b"\0\0"
    for attempt in range(10):
        if not u32.OpenClipboard(None):
            time.sleep(0.05)
            continue
        try:
            u32.EmptyClipboard()
            h = k32.GlobalAlloc(0x2, len(data))  # GMEM_MOVEABLE
            if not h:
                raise RuntimeError("GlobalAlloc 失败")
            p = k32.GlobalLock(h)
            if not p:
                k32.GlobalFree(h)
                raise RuntimeError("GlobalLock 失败")
            ctypes.memmove(p, data, len(data))
            k32.GlobalUnlock(h)
            if not u32.SetClipboardData(13, h):  # CF_UNICODETEXT；成功后句柄归系统，不能 Free
                k32.GlobalFree(h)
                raise RuntimeError(f"SetClipboardData 失败 (attempt {attempt})")
            return
        finally:
            u32.CloseClipboard()
    raise RuntimeError("OpenClipboard 连续失败，剪贴板被其他程序占用")


u32.GetForegroundWindow.restype = w.HWND
u32.SetForegroundWindow.argtypes = [w.HWND]
u32.GetWindowRect.argtypes = [w.HWND, ctypes.POINTER(w.RECT)]
u32.WindowFromPoint.argtypes = [w.POINT]
u32.WindowFromPoint.restype = w.HWND
u32.GetAncestor.argtypes = [w.HWND, w.UINT]
u32.GetAncestor.restype = w.HWND
u32.IsIconic.argtypes = [w.HWND]
u32.IsChild.argtypes = [w.HWND, w.HWND]


class GUIThreadInfo(ctypes.Structure):
    _fields_ = [("cbSize", w.DWORD), ("flags", w.DWORD), ("hwndActive", w.HWND),
                ("hwndFocus", w.HWND), ("hwndCapture", w.HWND), ("hwndMenuOwner", w.HWND),
                ("hwndMoveSize", w.HWND), ("hwndCaret", w.HWND), ("rcCaret", w.RECT)]


u32.GetGUIThreadInfo.argtypes = [w.DWORD, ctypes.POINTER(GUIThreadInfo)]
dwmapi = ctypes.windll.dwmapi
dwmapi.DwmGetWindowAttribute.argtypes = [w.HWND, w.DWORD, w.LPVOID, w.DWORD]


def _bounds(hwnd):
    r = w.RECT()
    if dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(r), ctypes.sizeof(r)) != 0:
        if not u32.GetWindowRect(hwnd, ctypes.byref(r)):
            raise RuntimeError("无法核验窗口坐标")
    return r.left, r.top, r.right, r.bottom


def _require(condition):
    if not condition:
        raise RuntimeError("当前聊天窗口、布局或输入焦点已变化，请复制后自行粘贴")


def fill(hwnd, identity, layout, text, still_valid):
    """核验实际窗口和像素，点击输入区，再确认焦点后粘贴。"""
    from app.capture import fresh_layout, window_identity

    def valid():
        return still_valid() and window_identity(hwnd) == identity and not u32.IsIconic(hwnd)

    def foreground():
        return valid() and u32.GetForegroundWindow() == hwnd

    _require(layout and identity and valid())
    if u32.GetForegroundWindow() != hwnd:
        # AttachThreadInput requires a message queue, including on a background thread.
        u32.PeekMessageW(ctypes.byref(w.MSG()), None, 0, 0, 0)
        fg = u32.GetForegroundWindow()
        fg_tid = u32.GetWindowThreadProcessId(fg, None)
        our_tid = k32.GetCurrentThreadId()
        attached = fg_tid and fg_tid != our_tid and u32.AttachThreadInput(our_tid, fg_tid, True)
        try:
            u32.SetForegroundWindow(hwnd)
        finally:
            if attached:
                u32.AttachThreadInput(our_tid, fg_tid, False)
        time.sleep(0.12)
    _require(foreground())

    bounds = _bounds(hwnd)
    _require(fresh_layout(hwnd) == layout and foreground() and _bounds(hwnd) == bounds)
    (height, width), (x0, _, x1, y1, _), _ = layout
    left, top, right, bottom = bounds
    _require(right - left == width and bottom - top == height and x1 - x0 >= 140 and height - y1 >= 60)
    point = w.POINT(left + x0 + 60, top + y1 + 40)
    set_clipboard(text)
    clipboard_version = u32.GetClipboardSequenceNumber()
    old = w.POINT()
    _require(u32.GetCursorPos(ctypes.byref(old)))
    try:
        _require(u32.SetCursorPos(point.x, point.y))
        _require(foreground() and _bounds(hwnd) == bounds
                 and u32.GetAncestor(u32.WindowFromPoint(point), 2) == hwnd)
        u32.mouse_event(0x2, 0, 0, 0, 0)
        u32.mouse_event(0x4, 0, 0, 0, 0)
        time.sleep(0.05)
    finally:
        u32.SetCursorPos(old.x, old.y)

    def focused():
        info = GUIThreadInfo(cbSize=ctypes.sizeof(GUIThreadInfo))
        return (foreground() and _bounds(hwnd) == bounds
                and u32.GetGUIThreadInfo(identity[1], ctypes.byref(info))
                and not (info.flags & 0x1E) and info.hwndActive == hwnd
                and info.hwndFocus and (info.hwndFocus == hwnd or u32.IsChild(hwnd, info.hwndFocus)))

    def chord(key):
        _require(focused())
        u32.keybd_event(0x11, 0, 0, 0)
        try:
            u32.keybd_event(key, 0, 0, 0)
            u32.keybd_event(key, 0, 2, 0)
        finally:
            u32.keybd_event(0x11, 0, 2, 0)

    chord(0x23)  # Ctrl+End
    _require(u32.GetClipboardSequenceNumber() == clipboard_version)
    chord(0x56)  # Ctrl+V
