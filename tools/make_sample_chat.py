# -*- coding: utf-8 -*-
"""生成 docs/sample_chat.png：给 tools/preview_ui.py 的调试预览当背景的合成聊天图。

只画假数据，不采集、不读取真实微信。图里的消息坐标和 tools/preview_ui.py 的 _BOXES 对齐，
这样调试预览能直接展示「消息区 + 各类识别框」的效果。重新生成：

    python -m tools.make_sample_chat
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "sample_chat.png"
W, H = 1303, 979
AREA = (433, 152, 1298, 767)
BOXES = [
    (83, 99, 156, 122, "name", "Asterlion"),
    (26, 131, 70, 146, "image", "借仲夏夜之梦"),
    (99, 136, 143, 162, "her", "难绷"),
    (83, 194, 156, 218, "name", "Asterlion"),
    (101, 233, 315, 256, "her", "怎么识别到仲夏夜之梦的（"),
    (612, 30, 700, 44, "gray", "链接卡片的灰字"),
    (612, 50, 690, 62, "tiny", "表情包里的小字"),
    (633, 298, 760, 328, "me", "好像识别头像了"),
    (685, 368, 762, 399, "me", "笑死我了"),
    (85, 432, 157, 454, "name", "Asterlion"),
    (27, 464, 70, 478, "image", "借仲夏夜之梦"),
    (99, 467, 159, 493, "her", "还真是"),
    (98, 563, 160, 592, "her", "哈哈哈"),
]


def _font(size):
    for name in ("msyh.ttc", "simhei.ttf", "simsun.ttc"):
        try:
            return ImageFont.truetype(str(Path("C:/Windows/Fonts") / name), size)
        except OSError:
            continue
    return ImageFont.load_default()


def main() -> int:
    img = Image.new("RGB", (W, H), "#f2f3f5")
    d = ImageDraw.Draw(img)
    # 左侧会话列表 + 右侧聊天面板
    d.rectangle((0, 0, 433, H), fill="#ededed")
    d.rectangle((433, 0, W, H), fill="#f7f8fa")
    d.rectangle((433, 0, W, 152), fill="#ffffff")
    d.line((433, 152, W, 152), fill="#e4e6e8")
    d.text((455, 48), "白金搬砖小分队", font=_font(28), fill="#1f2329")
    d.text((455, 92), "群聊 (3)", font=_font(16), fill="#8a9099")
    # 输入区
    d.rounded_rectangle((450, 830, 1260, 900), radius=10, fill="#ffffff", outline="#e2e5e8")
    d.text((470, 850), "在这里输入…", font=_font(18), fill="#a8aeb6")

    x0, y0, x1, y1 = AREA
    for bx0, by0, bx1, by1, kind, text in BOXES:
        x, y = x0 + bx0, y0 + by0
        xx, yy = x0 + bx1, y0 + by1
        if kind == "me":
            d.rounded_rectangle((x - 14, y - 8, xx + 16, yy + 8), radius=10,
                                fill="#95ec69", outline="#8ad95f")
        elif kind == "her":
            d.rounded_rectangle((x - 14, y - 8, xx + 16, yy + 8), radius=10,
                                fill="#ffffff", outline="#e2e5e8")
        elif kind == "image":
            d.ellipse((x - 4, y - 4, x + 44, y + 44), fill="#c7d2dc")
            d.text((x + 6, y + 12), "头像", font=_font(12), fill="#5d6b78")
            continue
        elif kind == "name":
            d.text((x, y - 18), text, font=_font(14), fill="#8a9099")
            continue
        color = "#7d858f" if kind in ("gray", "tiny") else "#1f2329"
        size = 13 if kind in ("gray", "tiny") else 17
        d.text((x, y), text, font=_font(size), fill=color)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    img.save(OUT, "PNG")
    print(f"已生成 {OUT}（合成数据，不含真实聊天）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
