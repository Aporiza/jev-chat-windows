# -*- coding: utf-8 -*-
"""WeChat window and bubble recognition settings."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np


def _wechat_me(bg: np.ndarray) -> bool:
    """WeChat's own bubble is green: G clearly above both R and B."""
    return bool(bg[1] > bg[0] + 40 and bg[1] > bg[2] + 40)


@dataclass(frozen=True)
class ChatApp:
    key: str
    label: str
    exes: tuple[str, ...]        # process names, lowercase
    main_title: str
    is_me: Callable[[np.ndarray], bool]


WECHAT = ChatApp("wechat", "微信", ("weixin.exe", "wechat.exe"), "微信", _wechat_me)

APPS = {WECHAT.key: WECHAT}
DEFAULT = WECHAT


def by_exe(exe: str) -> ChatApp | None:
    return next((a for a in APPS.values() if exe in a.exes), None)


def get(key: str | None) -> ChatApp:
    return APPS.get(key or "", DEFAULT)


if __name__ == "__main__":  # 自测：颜色规则用实测像素锁住，改错了当场炸
    assert by_exe("weixin.exe") is WECHAT and by_exe("wechat.exe") is WECHAT
    assert by_exe("chrome.exe") is None and get(None) is WECHAT
    assert WECHAT.is_me(np.array((149, 236, 105)))
    assert not WECHAT.is_me(np.array((255, 255, 255)))
    print("chatapps ok")
