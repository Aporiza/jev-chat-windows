# -*- coding: utf-8 -*-
"""子进程：截图 → 定位消息区 → OCR 头部会话名和消息 → 按会话去重，全在这边跑。
一次 OCR 250~800ms，放父进程的 Qt 主线程界面就僵了。
只往队列里丢纯 tuple/str（底色 bg 是 numpy，留在这边不过队列）。帧全程内存，绝不落盘。"""
import ctypes
import time

import numpy as np

from app import chatapps
from app.capture import Capture, chat_area, layout_signature, unminimize, window_identity
from app.ocr import Reader, read_title


def _packet(full, area, title, reader, lines):
    """调试视图的一帧：整帧缩到长边 ≤1100 再走队列（原帧 2560 宽裸传要 20MB），只在内存里传，不落盘。
    整数步长切片够用，不引新依赖；框和坐标照发原始值，画的那边按 scale 折算。"""
    k = max(1, -(-max(full.shape[:2]) // 1100))
    small = np.ascontiguousarray(full[::k, ::k])
    return {"w": small.shape[1], "h": small.shape[0], "rgb": small.tobytes(), "scale": k,
            "area": tuple(int(v) for v in area[:4]) if area else None,
            "pane_top": int(area[5]) if area else 0, "title": title,
            "boxes": reader.last_boxes if reader else [],
            "lines": [(w, n, t) for w, n, t, _ in lines],
            "ocr_ms": reader.last_ms if reader else 0, "ts": time.time()}


def run(q, hwnd, enabled, debug_on, app_key, epoch, identity):
    """只在标题和布局可确认时识别消息。每次恢复用新进程和新比较基线。"""
    ctypes.windll.user32.SetProcessDPIAware()
    app = chatapps.get(app_key)
    title, head, reader = "", None, None

    def changed():
        epoch.value += 1
        return epoch.value

    try:
        cap = Capture(hwnd, changed=changed)
    except Exception:
        q.put(("dead", "无法开始采集"))
        return
    try:
        q.put(("resumed",))
        while enabled.is_set() and cap.alive() and window_identity(hwnd) == identity:
            unminimize(hwnd)
            packet = cap.settled()
            if packet is None:
                time.sleep(0.05)
                continue
            stamp, full = packet
            try:
                area = chat_area(full)
                cap.set_area(area, stamp)
                if area is None:
                    title, head, reader = "", None, None
                    q.put(("frame", stamp, "", None, "unknown", [], []))
                    continue
                x0, y0, x1, y1, bg, y_pane = area
                crop = full[y_pane:y0, x0:x1]
                name = title if head is not None and np.array_equal(crop, head) else read_title(crop, app)
                if not name:
                    title, head, reader = "", None, None
                    q.put(("frame", stamp, "", None, "unknown", [], []))
                    continue
                if name != title:
                    reader = Reader(app)
                title, head = name, crop
                lines = reader.read(full[y0:y1, x0:x1], bg)
                new = reader.new_lines(lines)
                visible = [(who, sender, text) for who, sender, text, _ in lines]
                signature = layout_signature(full, area)
                q.put(("frame", stamp, title, signature, reader.transition, new, visible))
                if debug_on.is_set():
                    q.put(("debug", _packet(full, area, title, reader, lines)))
            except Exception:
                title, head, reader = "", None, None
                q.put(("frame", stamp, "", None, "unknown", [], []))
    finally:
        changed()
        cap.stop()
        q.put(("dead", "采集已停止，请确认聊天窗口已打开"))
