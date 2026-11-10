# -*- coding: utf-8 -*-
"""对话 → Jev 判断 → 起草候选 → Jev 排序。"""
from __future__ import annotations

from math import isfinite
from queue import Empty, Queue
from threading import BoundedSemaphore, Thread
from time import monotonic, time

try:
    from .draft import draft_candidates
    from .jev_client import JevError, ask
    from .questions import JUDGE_QUESTIONS, build_rank_question, build_state, guidance_text
except ImportError:
    from draft import draft_candidates
    from jev_client import JevError, ask
    from questions import JUDGE_QUESTIONS, build_rank_question, build_state, guidance_text

_NETWORK_SLOTS = BoundedSemaphore(4)
_REPLY_IDX = {"reply_a": 0, "reply_b": 1, "reply_c": 2}


class ObsoleteAnalysis(Exception):
    """请求已被新消息或界面状态替代。"""


class _Budget:
    def __init__(self, timeout, is_current):
        self.deadline = monotonic() + timeout
        self.is_current = is_current

    def check(self):
        if not self.is_current():
            raise ObsoleteAnalysis

    def remaining(self):
        self.check()
        remaining = self.deadline - monotonic()
        if remaining <= 0:
            raise JevError("已达到生成时限")
        return remaining

    def request(self, send):
        """总时限也覆盖 SDK 内部重试及可能迟迟不返回的读取。"""
        output = Queue(1)

        def run():
            try:
                value = send(remaining)
            except Exception as exc:
                output.put((False, exc))
            else:
                output.put((True, value))
            finally:
                _NETWORK_SLOTS.release()

        while not _NETWORK_SLOTS.acquire(timeout=min(0.05, self.remaining())):
            pass
        try:
            remaining = self.remaining()
            Thread(target=run, daemon=True).start()
        except BaseException:
            _NETWORK_SLOTS.release()
            raise
        while True:
            try:
                ok, value = output.get(timeout=min(0.05, self.remaining()))
            except Empty:
                continue
            self.remaining()
            if ok:
                return value
            raise value


def _add_usage(total, usage):
    if not isinstance(usage, dict):
        return
    for name, value in usage.items():
        if type(value) in (int, float) and isfinite(value):
            total[name] = total.get(name, 0) + value


def _choice(answers, question):
    answer = answers.get(question)
    return answer.get("choice") if isinstance(answer, dict) else None


def analyze(messages: list, relationship: str, model: str | None = None,
            timeout: float = 30, context: int = 10, provider: str = "deepseek",
            base_url: str | None = None, reply_to: str | None = None, style: str = "",
            thinking: bool = False, jev_provider: str = "openrouter",
            jev_model: str | None = None, *, goal: str = "", is_current=lambda: True,
            jev_key: str | None = None, llm_key: str | None = None) -> dict:
    """按总时限生成建议。best_index 为 None 表示没有可信排序。

    jev_key 显式传空字符串时进入降级模式：跳过 Jev 判断和排序，只用起草模型
    生成候选，status 为 "degraded"、ranking_status 为 "unavailable"。
    jev_key=None 仍按原逻辑从环境变量读取/调用，方便直接调 analyze 的调用方。
    """
    started = monotonic()
    budget = _Budget(timeout, is_current)
    state = build_state(messages, relationship, keep=context, reply_to=reply_to, goal=goal)
    answers, usage, stages = {}, {}, {}
    jev_enabled = jev_key is None or bool(str(jev_key).strip())

    def stage(name, operation, stage_usage=None):
        started_at = monotonic()
        metrics = stages[name] = {"status": "ok", "usage": stage_usage if stage_usage is not None else {}}
        try:
            return operation()
        except JevError:
            metrics["status"] = "failed"
            raise
        finally:
            metrics["elapsed_ms"] = round((monotonic() - started_at) * 1000)

    def skip(name):
        stages[name] = {"status": "skipped", "usage": {}, "elapsed_ms": 0}

    def judge(name, questions):
        def call():
            result = budget.request(lambda remaining: ask(
                state, questions, timeout=remaining, provider=jev_provider, model=jev_model,
                api_key=jev_key, bounded=True))
            if not isinstance(result, dict):
                raise JevError("判断返回格式不可用")
            _add_usage(stages[name]["usage"], result.get("usage"))
            _add_usage(usage, result.get("usage"))
            reported = result.get("answers")
            if not isinstance(reported, dict):
                raise JevError("判断答案格式不可用")
            return {key: value for key, value in reported.items() if isinstance(value, dict)}
        try:
            return stage(name, call)
        except JevError:
            return {}

    if jev_enabled:
        answers.update(judge("judge", dict(JUDGE_QUESTIONS)))
    else:
        skip("judge")
    boundary = (_choice(answers, "best_action") == "respect_boundary"
                or _choice(answers, "true_intent") == "set_boundary")
    candidates, best_index, scores = [], None, []
    ranking_status = "skipped"
    if not boundary:
        budget.remaining()
        draft_usage = {}
        candidates = stage("draft", lambda: draft_candidates(
            messages, relationship, provider=provider, model=model, base_url=base_url,
            timeout=timeout, keep=context, reply_to=reply_to, style=style, thinking=thinking,
            guidance=guidance_text(answers), goal=goal, api_key=llm_key, request=budget.request,
            usage_sink=lambda reported: _add_usage(draft_usage, reported)), draft_usage)
        if not candidates:
            raise JevError("起草结果没有可用候选回复")
        budget.check()
        if not jev_enabled:
            scores = [None] * len(candidates)
            ranking_status = "unavailable"
            skip("rank")
        elif len(candidates) >= 2:
            questions = build_rank_question(candidates)
            if not answers:
                questions.update(JUDGE_QUESTIONS)
            ranking = judge("rank", questions)
            answers.update(ranking)
            best_key = _choice(ranking, "best_reply")
            best_index = _REPLY_IDX.get(best_key) if isinstance(best_key, str) else None
            if best_index is not None and best_index < len(candidates):
                ranking_status = "ranked"
                probabilities = ranking["best_reply"].get("probabilities")
                probabilities = probabilities if isinstance(probabilities, dict) else {}
                scores = [None] * len(candidates)
                for key, idx in _REPLY_IDX.items():
                    value = probabilities.get(key)
                    if idx < len(scores) and type(value) in (int, float) and 0 <= value <= 1:
                        scores[idx] = value
            else:
                ranking_status, best_index = "failed", None
                stages["rank"]["status"] = "failed"
        else:
            ranking_status = "single_candidate"
        boundary = (_choice(answers, "best_action") == "respect_boundary"
                    or _choice(answers, "true_intent") == "set_boundary")
        if boundary:
            candidates, best_index, scores, ranking_status = [], None, [], "skipped"

    budget.check()
    uncertain = (_choice(answers, "best_action") == "clarify"
                 or _choice(answers, "true_intent") == "uncertain")
    status = ("boundary" if boundary else "uncertain" if uncertain
              else "degraded" if not jev_enabled
              else "partial"
              if len(candidates) < 3 or ranking_status != "ranked" or not _choice(answers, "best_action")
              else "complete")
    return {
        "candidates": candidates,
        "best_index": best_index,
        "best_reply": candidates[best_index] if best_index is not None else None,
        "scores": scores,
        "answers": answers,
        "usage": usage,
        "stages": stages,
        "elapsed_ms": round((monotonic() - started) * 1000),
        "ranking_status": ranking_status,
        "status": status,
        "generated_at": time(),
        "reply_to": reply_to,
    }


if __name__ == "__main__":
    from unittest.mock import patch

    with patch("__main__.ask", return_value={"answers": {}, "usage": {}}), \
         patch("__main__.draft_candidates", return_value=[]):
        try:
            analyze([("her", "hello")], "friends")
        except JevError as exc:
            assert "没有可用候选" in str(exc)
        else:
            raise SystemExit("应当抛错")
    print("engine ok")
