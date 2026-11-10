# -*- coding: utf-8 -*-
"""Qt 主线程接收采集状态、调度分析并核验填入。发送始终由用户操作。"""
import ctypes
import multiprocessing
import queue
import threading
import time
import traceback
from collections import deque
from dataclasses import dataclass

from app import settings, update, worker
from app.capture import find_chat_hwnd, window_identity
from app.fill import fill
from app.overlay import Overlay
from app.version import VERSION
from core.engine import ObsoleteAnalysis, analyze

_DEBOUNCE = 0.45


@dataclass(frozen=True)
class Version:
    session: int
    frame: int
    title: str
    messages: int
    target: int
    config: int


chats = {}
state = {"session": 0, "config": 0, "version": None, "layout": None,
         "hwnd": None, "identity": None, "chat": "", "wanted": None, "inflight": None}
results = queue.Queue()
fill_results = queue.Queue()
update_result = queue.Queue()


def chat_of(title):
    return chats.setdefault(title, {"history": deque(maxlen=60), "result": None, "rev": 0,
                                    "target_rev": 0, "target": None, "senders": [], "eligible": False})


def target_of(title):
    chat = chat_of(title)
    if chat["target"] in chat["senders"]:
        return chat["target"]
    if chat["history"]:
        return chat["history"][-1][2]  # 未识别到最新发言人时不沿用旧名字
    return None


def is_current(version):
    """采集线程已看见而 OCR 队列尚未处理的变化也使结果立即失效。"""
    return (version is not None and state["version"] == version
            and capture_on.is_set() and epoch.value == version.frame)


def can_fill(result):
    version = result.get("version") if result else None
    return bool(is_current(version) and ov.current_chat() == version.title
                and chat_of(version.title)["result"] is result
                and result.get("candidates") and result.get("status") not in ("boundary", "uncertain"))


def fill_reply(text, result, at_prefix=False):
    def valid():
        return can_fill(result)

    if not valid() or text not in result["candidates"]:
        raise RuntimeError("当前会话或消息已变化，请复制建议并自行检查")
    target = result.get("reply_to")
    if target and at_prefix:
        text = f"@{target} " + text
    fill(state["hwnd"], state["identity"], state["layout"], text, valid)


def request_fill(text):
    result = ov.displayed_result()
    if not can_fill(result) or state.get("filling"):
        raise RuntimeError("当前建议不可填入")
    at_prefix = ov.at_prefix_enabled()
    state["filling"] = True

    def run():
        try:
            fill_reply(text, result, at_prefix)
        except Exception:
            filled = False
        else:
            filled = True
        fill_results.put((result, filled))

    threading.Thread(target=run, daemon=True).start()
    return False  # Qt 后续从队列更新状态。


def spawn_worker():
    global q, epoch, child
    q = multiprocessing.Queue()
    epoch = multiprocessing.RawValue("L", 0)  # 32 位对齐的采集版本，无跨进程锁
    state["session"] += 1
    capture_on.set()
    child = multiprocessing.Process(
        target=worker.run,
        args=(q, state["hwnd"], capture_on, debug_on, state["app"], epoch, state["identity"]),
        daemon=True)
    child.start()


def clear_capture(reason=""):
    global child
    capture_on.clear()
    state.update(version=None, layout=None, chat="", wanted=None, hwnd=None, identity=None)
    ov.set_chat("")
    ov.invalidate_replies()
    ov.set_busy(False)
    ov.set_capture(False, reason)
    if child is not None:
        child.terminate()
        child.join(timeout=0.25)
        child = None
        q.close()


def set_debug(on):
    global dbg
    if not on:
        debug_on.clear()
        if dbg is not None:
            dbg.hide()
        return
    if dbg is None:
        from app.debugwin import DebugWindow

        dbg = DebugWindow(on_close=on_debug_closed)
    dbg.show()
    debug_on.set()


def on_debug_closed():
    debug_on.clear()
    ov.set_debug_switch(False)
    settings.save(debug_view_on=False)


def on_toggle_capture(on):
    if not on:
        clear_capture()
    elif child is None:
        try:
            hwnd, app = find_chat_hwnd()
            identity = window_identity(hwnd)
            if not identity:
                raise RuntimeError("未找到可确认的聊天窗口")
        except RuntimeError:
            ov.set_capture(False, "未找到聊天窗口，打开后再开启采集")
            return
        state.update(hwnd=hwnd, identity=identity, app=app.key)
        spawn_worker()


def analyze_bg(messages, version, config, target):
    try:
        result = analyze(
            messages, config.relationship, context=config.context, model=config.draft_model or None,
            provider=config.draft_provider, base_url=config.draft_base_url or None,
            reply_to=target, style=config.style, thinking=config.thinking,
            jev_provider=config.jev_provider, jev_model=config.jev_model or None,
            goal=config.goal, jev_key=config.jev_key, llm_key=config.llm_key,
            is_current=lambda: is_current(version))
        results.put(("ok", result, version))
    except ObsoleteAnalysis:
        results.put(("obsolete", None, version))
    except Exception:
        # 服务返回可能含请求正文和密钥；界面只提示操作原因。
        results.put(("error", None, version))


def check_update_bg():
    result = update.check_latest(VERSION)
    if result:
        update_result.put(result)


def update_version(stamp, title):
    chat = chat_of(title)
    state["version"] = Version(state["session"], stamp, title, chat["rev"],
                               chat["target_rev"], state["config"])


def schedule(title):
    if title == state["chat"] and state["version"] and chat_of(title)["eligible"]:
        state["wanted"] = (title, time.monotonic() + _DEBOUNCE)


def start_pending():
    wanted = state["wanted"]
    version = state["version"]
    if (not wanted or state["inflight"] or time.monotonic() < wanted[1]
            or not is_current(version) or wanted[0] != version.title):
        return
    state["wanted"] = None
    config = settings.analysis_settings(version.title)
    if not config.jev_key or not config.llm_key:
        ov.set_status("请先在设置中配置判断和起草模型", "warning")
        return
    chat = chat_of(version.title)
    target = target_of(version.title) if config.reply_target else None
    state["inflight"] = version
    ov.set_busy(True)
    threading.Thread(target=analyze_bg,
                     args=(tuple(chat["history"]), version, config, target), daemon=True).start()


def on_target_change(title, name):
    chat = chat_of(title)
    if name not in chat["senders"] or name == chat["target"]:
        return
    chat["target"] = name
    chat["target_rev"] += 1
    version = state["version"]
    if version and title == version.title:
        update_version(version.frame, title)
        ov.invalidate_replies()
        schedule(title)


def on_settings_saved():
    state["config"] += 1
    version = state["version"]
    if version:
        update_version(version.frame, version.title)
        schedule(version.title)
    ov.invalidate_replies()


def accept_frame(stamp, title, layout, transition, new, visible):
    previous = state["chat"]
    old_work = state["wanted"] or state["inflight"]
    state.update(version=None, layout=None, wanted=None, chat=title)
    ov.invalidate_replies()
    if title != previous:
        ov.set_chat(title)
    if not title:
        ov.set_status("会话标题或消息区无法确认，已暂停生成和填入", "warning")
        return

    chat = chat_of(title)
    if transition in ("baseline", "uncertain") or title != previous:
        chat["history"].clear()
        chat["senders"].clear()
        chat["eligible"] = False
        chat["rev"] += 1
        relevant = visible
    else:
        relevant = new
        if new:
            chat["rev"] += 1
            chat["eligible"] = new[-1][0] == "her"
    for who, name, text in relevant:
        chat["history"].append((who, text, name))
        if who == "her" and name:
            if name in chat["senders"]:
                chat["senders"].remove(name)
            chat["senders"].insert(0, name)
            del chat["senders"][60:]
    for who, name, text in new:
        ov.log_message(who, text, name, chat=title)
    ov.set_targets(title, chat["senders"], target_of(title))

    if transition == "uncertain":
        ov.set_status("消息顺序暂时无法确认，等待可确认的新消息", "warning")
        return
    if transition == "baseline":
        ov.set_status("已确认当前会话，等待新消息")
    state["layout"] = layout
    update_version(stamp, title)
    if new and not chat["eligible"]:
        ov.set_busy(False)
        ov.set_status("你已回复，等待对方的新消息")
    elif new or (old_work and title == previous):
        schedule(title)


def drain():
    while child is not None and capture_on.is_set():
        try:
            message = q.get_nowait()
        except queue.Empty:
            break
        kind, *data = message
        if kind == "frame":
            accept_frame(*data)
        elif kind == "debug" and dbg is not None:
            dbg.show_packet(data[0])
        elif kind == "resumed":
            ov.set_capture(True)
        elif kind == "dead":
            clear_capture(data[0])
            return
    # multiprocessing.Queue 的 feeder 可能尚未将对应帧送到 Qt。
    if state["version"] and not is_current(state["version"]):
        ov.invalidate_replies()
    if child is not None and not child.is_alive():
        clear_capture("采集已停止，请确认聊天窗口已打开")


def tick():
    try:
        drain()
        while not fill_results.empty():
            filled_result, filled = fill_results.get_nowait()
            state["filling"] = False
            if ov.displayed_result() is filled_result and capture_on.is_set():
                if filled:
                    ov.set_status("已填入，请确认内容后发送。", "success")
                else:
                    ov.set_status("未能确认当前输入框，请复制后自行粘贴。", "warning")
        while not update_result.empty():
            ov.set_update(*update_result.get_nowait())
        while not results.empty():
            kind, result, version = results.get_nowait()
            if state["inflight"] == version:
                state["inflight"] = None
                ov.set_busy(False)
            if not is_current(version):
                continue
            if kind == "ok":
                result["version"] = version
                chat_of(version.title)["result"] = result
                if ov.current_chat() == version.title:
                    ov.show(result)
            elif kind == "error":
                ov.set_status("生成失败，请检查网络和服务设置；收到新消息后会重试。", "error")
        start_pending()
    except Exception:
        traceback.print_exc()
    ov.after(50, tick)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    ctypes.windll.user32.SetProcessDPIAware()
    capture_on, debug_on = multiprocessing.Event(), multiprocessing.Event()
    child = dbg = None
    ov = Overlay(on_fill=request_fill, can_fill=can_fill,
                 on_toggle_capture=on_toggle_capture, on_target_change=on_target_change,
                 on_toggle_debug=set_debug, on_settings_saved=on_settings_saved,
                 result_of=lambda title: chats.get(title, {}).get("result"))
    on_toggle_capture(True)
    if settings.debug_view():
        set_debug(True)
    if not settings.has_jev_key():
        ov.set_status("请先在设置中配置模型", "warning")
        ov.after(0, ov.open_settings)
    if settings.check_update() and update.parse_version(VERSION):
        threading.Thread(target=check_update_bg, daemon=True).start()
    ov.after(50, tick)
    try:
        ov.run()
    finally:
        if child is not None:
            child.terminate()
