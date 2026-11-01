# -*- coding: utf-8 -*-
"""UI text helpers for the Chinese interface."""


def T(text: str) -> str:
    return text


def bind(widget, source, setter="setText"):
    getattr(widget, setter)(source() if callable(source) else T(source))
    return widget
