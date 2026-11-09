# -*- coding: utf-8 -*-
"""端到端冒烟：截图里那段真实对话跑一遍完整链，打印判断 + 排好序的候选。

链路是三段式：Jev 判断（7 道题） → 带着判断起草 3 条 → Jev 排序，两次 Jev 调用。

全程只要两把 key：判断一把 JEV_API_KEY（OpenRouter 或 TypeSafe 的），起草一把 LLM_API_KEY。

    set JEV_API_KEY=...   &  set LLM_API_KEY=...    (Windows)
    export JEV_API_KEY=... && export LLM_API_KEY=...(mac/Linux)
    python tools/demo.py

没有 Jev key 时，可以只模拟判断/排序、仍用真实起草模型：

    python tools/demo.py --mock-jev

默认：判断走 OpenRouter，起草走 DeepSeek 官网直连。换别家改下面两个常量
（可选的来源见 core/providers.py 的两张表）。
"""
from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path
from unittest.mock import patch

# 直接跑 `python tools/demo.py` 时，sys.path[0] 是 tools/，找不到 core/；把项目根补进去。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from core.engine import analyze
from core.jev_client import JevError
from core.questions import guidance_text

MESSAGES = [
    ("her", "你今天是不是又忘了我跟你说过什么？"),
    ("me", "记得，你先别提示我，让我自己说。"),
    ("her", "那你说。"),
    ("me", "等一下，我想说完整一点。"),
    ("her", "你最好是。"),
]
RELATIONSHIP = "romantic partners"
PROVIDER = "deepseek"        # 起草来源，见 core.providers.DRAFT_PROVIDERS
JEV_PROVIDER = "openrouter"  # 判断来源：openrouter 或 typesafe


def fmt(name: str, ans: dict) -> str:
    t = ans.get("type")
    if t == "noul":
        return f"{name}: {ans.get('noul'):.2f}"
    if t == "choice":
        return f"{name}: {ans.get('choice')} (conf {ans.get('confidence'):.2f})"
    if t == "score":
        return f"{name}: {ans.get('score'):.1f}/9 (conf {ans.get('confidence'):.2f})"
    return f"{name}: {ans}"


def _mock_ask(state, questions, **kwargs):
    """本地固定的 Jev 回答：让没有 Jev key 的成员也能跑通三段式编排。
    只用于 demo/测试，不代表真实判断质量。"""
    if "best_reply" in questions:
        return {
            "answers": {"best_reply": {
                "type": "choice", "choice": "reply_b", "confidence": 0.8,
                "probabilities": {"reply_a": 0.2, "reply_b": 0.6, "reply_c": 0.2}}},
            "usage": {"input_tokens": 50, "output_tokens": 5},
        }
    return {
        "answers": {
            "literal_question": {"type": "noul", "noul": 0.1},
            "true_intent": {"type": "choice", "choice": "confirm_you_care", "confidence": 0.8},
            "danger_level": {"type": "score", "score": 5.0, "confidence": 0.7},
            "should_reply_now": {"type": "noul", "noul": 0.2},
            "best_action": {"type": "choice", "choice": "check_history", "confidence": 0.7},
            "she_needs": {"type": "choice", "choice": "care", "confidence": 0.7},
            "tension_resolved": {"type": "noul", "noul": 0.1},
        },
        "usage": {"input_tokens": 200, "output_tokens": 20},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="端到端冒烟；--mock-jev 不调用 Jev")
    parser.add_argument("--mock-jev", action="store_true",
                        help="用本地固定判断结果模拟 Jev（仍调用起草模型）")
    args = parser.parse_args()

    print("对话:")
    for w, t in MESSAGES:
        print(f"  {w}: {t}")
    try:
        if args.mock_jev:
            print("\n[Jev] 本地 mock，不联网")
            with patch("core.engine.ask", _mock_ask):
                r = analyze(MESSAGES, RELATIONSHIP, provider=PROVIDER, jev_provider=JEV_PROVIDER)
        else:
            r = analyze(MESSAGES, RELATIONSHIP, provider=PROVIDER, jev_provider=JEV_PROVIDER)
    except JevError as e:
        print(f"\n失败: {e}")
        return 1

    print("\n判断:")
    for name in ("literal_question", "true_intent", "danger_level",
                 "should_reply_now", "best_action", "she_needs", "tension_resolved"):
        if name in r["answers"]:
            print("  " + fmt(name, r["answers"][name]))

    block = guidance_text(r["answers"])  # 起草时喂进去的那张小抄
    if block:
        print("\n" + block)

    print("\n候选（Jev 排序，★ = 推荐）:")
    scores = r.get("scores")
    for i, c in enumerate(r["candidates"]):
        pct = f"  {scores[i]:.0%}" if scores else ""
        print(f"  {'★' if i == r['best_index'] else ' '} {c}{pct}")

    u = r["usage"]
    if u:
        print(f"\nusage: in={u.get('input_tokens')} out={u.get('output_tokens')} "
              f"cost=${u.get('cost')}")
    print("\n期望核对: true_intent≈confirm_you_care, best_action≈check_history, danger_level 中高档")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
