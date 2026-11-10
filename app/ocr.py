# -*- coding: utf-8 -*-
"""消息区截图 → 谁说了什么。RapidOCR 吃 numpy，不落盘。"""
import re
import time

import numpy as np
from rapidocr_onnxruntime import RapidOCR

from app.chatapps import DEFAULT, ChatApp

_ENGINE = None


def _engine(app: ChatApp = DEFAULT):
    """OCR 引擎全进程共用：一个实例 ~40MB，每个会话一个 Reader，不能各带一个。
    det_limit_type 默认 'min' 会把小图放大到短边 736，裁小反而更慢；必须 'max'。"""
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = RapidOCR(intra_op_num_threads=4, det_limit_type="max",
                           det_limit_side_len=4000)
    return _ENGINE


def read_title(header, app: ChatApp = DEFAULT):
    """面板头部那一条截图 → 会话名（numpy RGB）。取最靠上的一行，同一行里取最左的
    （右边是图标按钮，OCR 不出字；下面那行是公告）。保留精确显示的标题。
    认不出返回 ""。一次约 60ms，所以调用方只在头部像素变了时才问。"""
    res, _ = _engine(app)(header, use_cls=False)
    if not res:
        return ""
    first = min(res, key=lambda r: r[0][0][1])
    row = first[0][0][1] + (first[0][2][1] - first[0][0][1])  # 框底：顶在这之上的算同一行
    first = min((r for r in res if r[0][0][1] <= row), key=lambda r: r[0][0][0])
    return first[1].strip() if first[2] >= 0.75 else ""


def who_said(chat, box, app: ChatApp = DEFAULT):
    """按 OCR 框里的颜色分类，不看 x 坐标。返回 (谁, 底色, 墨高)：
    先看底色平不平：框里众数颜色占比 <45% 就是图片（头像/照片/表情包）里的字 → None 丢掉。
    绿底 → me；非绿且文字对底色对比度 ≥150 → her；其余（引用块、群里的发言人名、时间戳、系统提示、
    链接卡片描述——都是灰字，对比度 80~95）→ "gray"。
    实测：气泡正文对比度 178~208，me 绿泡 142~150，灰字 ≤ 93。深浅主题都靠这套。
    微信绿色气泡由 app.is_me 判为 "me"。
    墨高 = 框里最长一段连续有字的行数（OCR 框对小字有固定 padding、还会蹭到上下行，不能拿框高比大小）。"""
    xs, ys = [p[0] for p in box], [p[1] for p in box]
    reg = chat[int(min(ys)):int(max(ys)), int(min(xs)):int(max(xs))].astype(int)
    if reg.size == 0:
        return None, None, 0
    vals, cnt = np.unique(reg.reshape(-1, 3), axis=0, return_counts=True)
    bg = vals[cnt.argmax()]
    if cnt.max() / reg.shape[0] / reg.shape[1] < 0.45:
        # 文字必须落在平底色上：WGC 帧是精确像素，气泡/面板里众数颜色占 0.56~0.82，
        # 头像/照片/表情包里只有 0.1~0.3——那是图片里的字（头像上的「借仲夏夜之梦」之类），不是消息。
        # ponytail: 只对精确像素的帧成立；缩放/压缩过的截图（比如拿预览窗再截一次的图）底色会糊成几百种颜色，全会被当图片。
        return None, bg, 0
    diff = np.abs(reg @ [0.299, 0.587, 0.114] - bg @ [0.299, 0.587, 0.114])
    ink_h = best = 0
    for r in (diff > 60).any(axis=1):
        best = best + 1 if r else 0
        ink_h = max(ink_h, best)
    if app.is_me(bg):
        return "me", bg, ink_h
    return ("her" if diff.max() >= 150 else "gray"), bg, ink_h


class Reader:
    """识别当前会话的消息，比较相邻帧中的有序气泡。"""

    def __init__(self, app: ChatApp = DEFAULT):
        self.app = app
        self.ocr = _engine(app)
        self.lh = None  # 正常气泡字高，头一帧定
        self.previous = None
        self.frontier = ()
        self.frontier_y = ()
        self.resync = False
        self.transition = "baseline"
        self.last_boxes = []  # 调试视图用：[(x0,y0,x1,y1,kind,text)]，消息区裁剪坐标
        self.last_ms = 0  # 上一帧 OCR 耗时

    def read(self, chat, pane_bg):
        """→ [(who, name, text, y)]，同一气泡的多行已合并。who ∈ me/her；name 群聊里是发言人，单聊 None。
        顺带把每个框的分类记进 self.last_boxes（调试视图画框用，几十个 tuple，不开也不亏）。"""
        t0 = time.perf_counter()
        res, _ = self.ocr(chat, use_cls=False)
        self.last_ms = int((time.perf_counter() - t0) * 1000)
        self.last_boxes = []
        W = chat.shape[1]
        # 群聊：每条 her 气泡上方一行灰色发言人名（靠左、短、不带冒号、印在面板底色上），从上往下扫，名字带给后面的气泡。
        # 引用块/时间戳/公告带冒号，链接卡片灰字印在气泡底色上，都不会被当成名字。
        name, name_bottom, raw = None, 0, []
        for box, text, _ in sorted(res or [], key=lambda r: r[0][0][1]):
            kind, bg, h = who_said(chat, box, self.app)
            xs, ys = [p[0] for p in box], [p[1] for p in box]
            rect = (int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys)))
            # 印在面板底色上的字都不是消息，群聊发言人名可能在这里。
            on_pane = bg is not None and np.abs(bg - pane_bg).sum() <= 6
            if kind == "gray" or (on_pane and kind == "her"):
                taken = bool(on_pane and box[0][0] < 0.25 * W and len(text) <= 16
                             and not re.search("[:：]", text))
                if taken:
                    name, name_bottom = text, rect[3]
                self.last_boxes.append(rect + ("name" if taken else "gray", text))
                continue
            if kind is None or (self.lh and h < 0.6 * self.lh):
                # 字比正常气泡小得多 = 图片消息（截图/表情包）里的字，不是气泡
                self.last_boxes.append(rect + ("image" if kind is None else "tiny", text))
                continue
            self.last_boxes.append(rect + (kind, text))
            near_name = name if kind == "her" and 0 <= rect[1] - name_bottom <= max(8, 1.5 * h) else None
            raw.append((kind, near_name, text, rect[1], rect[3], h))
            name = None
        if not self.lh and len(raw) >= 3:
            self.lh = float(np.median([r[5] for r in raw]))
        # 同一气泡的多行合并：同人、上一行底到这一行顶的间距不到半个字高（不同气泡之间至少隔一个字高）
        lines = []
        for who, nm, text, top, bottom, h in raw:
            if lines and lines[-1][0] == who and nm is None and top - lines[-1][4] < 0.6 * (self.lh or h):
                lines[-1][2] += text
                lines[-1][4] = bottom
            else:
                lines.append([who, nm, text, top, bottom])
        return [(w, n, t, y) for w, n, t, y, _ in lines]

    def new_lines(self, lines):
        """仅把前沿后面有连续重叠证据的气泡当成新消息。"""
        current = tuple((who, name, text) for who, name, text, _ in lines)
        positions = tuple(y for _, _, _, y in lines)
        previous, self.previous = self.previous, current
        if previous is None:
            self.frontier, self.frontier_y = current, positions
            self.transition = "baseline"
            return []
        if previous == current:
            self.transition = "stable" if current == self.frontier and not self.resync else "uncertain"
            self.frontier_y = positions if current == self.frontier else self.frontier_y
            return []
        # 保留前沿，往上滚动再回到原处时不重报。
        if current and any(self.frontier[i:i + len(current)] == current
                           for i in range(len(self.frontier))):
            self.transition = "baseline" if current == self.frontier and not self.resync else "uncertain"
            if current == self.frontier:
                self.frontier_y = positions
            return []
        for n in range(min(len(self.frontier), len(current)), 0, -1):
            if self.frontier[-n:] == current[:n]:
                # 失去前沿后，随滚动上移的重叠内容不能用于恢复生成。
                certain = previous == self.frontier and (
                    not self.resync or self.frontier_y[-n:] == positions[:n])
                self.frontier, self.frontier_y = current, positions
                self.resync = not certain
                self.transition = "append" if certain else "uncertain"
                return list(current[n:]) if certain else []
        self.frontier, self.frontier_y = current, positions
        self.resync = True
        self.transition = "uncertain"
        return []
