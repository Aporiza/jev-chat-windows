import ctypes
import importlib
import queue
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app import settings
from core import engine
from core.jev_client import JevError


def load_with_stubs(module, **stubs):
    with patch.dict(sys.modules, {name: types.SimpleNamespace(**attrs)
                                 for name, attrs in stubs.items()}):
        return importlib.import_module(module)


def reader_type():
    return load_with_stubs("app.ocr", numpy={},
                           rapidocr_onnxruntime={"RapidOCR": Mock()}).Reader


class MessageTests(unittest.TestCase):
    def appended(self, previous, current):
        reader = reader_type()()
        reader.new_lines([(*row, y) for y, row in enumerate(previous)])
        return reader.new_lines([(*row, y) for y, row in enumerate(current)])

    def test_negation_is_a_new_message(self):
        yes = ("her", "A", "我今天有时间见面")
        no = ("her", "A", "我今天没有时间见面")
        self.assertEqual(self.appended([yes], [yes, no]), [no])

    def test_repeated_message_preserves_multiplicity(self):
        message = ("her", "A", "好")
        self.assertEqual(self.appended([message], [message, message]), [message])

    def test_same_text_from_different_people_is_new(self):
        a, b = ("her", "A", "好"), ("her", "B", "好")
        self.assertEqual(self.appended([a], [a, b]), [b])

    def test_non_overlapping_scroll_is_not_a_new_message(self):
        self.assertEqual(self.appended([("me", None, "今天见")],
                                       [("her", None, "昨天见")]), [])

    def test_scrolling_between_overlapping_old_viewports_does_not_rearm_generation(self):
        reader = reader_type()()
        reader.new_lines([("her", "A", "当前", 100)])
        reader.new_lines([("her", "A", "旧一", 0), ("her", "A", "旧二", 30)])
        self.assertEqual(reader.new_lines([("her", "A", "旧二", 0), ("her", "A", "旧三", 30)]), [])
        self.assertEqual(reader.transition, "uncertain")


class AnalysisTests(unittest.TestCase):
    @patch.object(engine, "draft_candidates", return_value=["甲", "乙", "丙"])
    @patch.object(engine, "ask", side_effect=[{"answers": {}}, JevError("timeout")])
    def test_ranking_failure_has_no_recommendation(self, ask, draft):
        result = engine.analyze([("her", "你好")], "friends")
        self.assertEqual(result["candidates"], ["甲", "乙", "丙"])
        self.assertIsNone(result["best_index"])
        self.assertIsNone(result["best_reply"])

    def test_chat_preferences_have_global_defaults_without_cross_chat_leak(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(settings, "_CONFIG", str(Path(tmp, "config.json"))):
            settings.save(relationship_text="colleagues", style_text="简短")
            settings.save_preferences("A", "friends", "随意", "结束对话")
            self.assertEqual(settings.preferences("A"), ("friends", "随意", "结束对话"))
            self.assertEqual(settings.preferences("B"), ("colleagues", "简短", ""))

    def test_new_install_does_not_assume_romance(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(settings, "_CONFIG", str(Path(tmp, "config.json"))):
            self.assertEqual(settings.relationship(), "unspecified")

    def test_explicit_no_contact_judgment_skips_drafting(self):
        response = {"answers": {"best_action": {"choice": "respect_boundary"}}, "usage": {}}
        with patch.object(engine, "ask", return_value=response), patch.object(engine, "draft_candidates") as draft:
            result = engine.analyze([("her", "请不要再联系我")], "friends")
        self.assertEqual(result["status"], "boundary")
        self.assertEqual(result["candidates"], [])
        draft.assert_not_called()

    def test_obsolete_inflight_judge_cannot_start_drafting(self):
        obsolete, release = threading.Event(), threading.Event()

        def ask(*args, **kwargs):
            obsolete.set()
            release.wait(1)
            return {"answers": {}}

        try:
            with patch.object(engine, "ask", side_effect=ask), patch.object(engine, "draft_candidates") as draft:
                with self.assertRaises(engine.ObsoleteAnalysis):
                    engine.analyze([("her", "你好")], "unspecified", is_current=lambda: not obsolete.is_set())
                draft.assert_not_called()
        finally:
            release.set()

    def test_total_deadline_rejects_a_blocked_request_before_sdk_returns(self):
        release = threading.Event()
        try:
            with patch.object(engine, "ask", side_effect=lambda *a, **k: release.wait(1)), \
                    patch.object(engine, "draft_candidates") as draft:
                started = time.monotonic()
                with self.assertRaisesRegex(JevError, "时限"):
                    engine.analyze([("her", "你好")], "unspecified", timeout=0.02)
                self.assertLess(time.monotonic() - started, 0.7)
                draft.assert_not_called()
        finally:
            release.set()

    def test_goal_usage_and_partial_scores_are_reported(self):
        from core import draft

        def ask(state, questions, **kwargs):
            self.assertEqual(state["chat"]["communication_goal"], "确认时间")
            if "best_reply" in questions:
                return {"answers": {"best_reply": {"choice": "reply_b", "probabilities": {"reply_b": 0.8}}},
                        "usage": {"input_tokens": 30}}
            return {"answers": {"best_action": {"choice": "make_plan"}}, "usage": {"input_tokens": 20}}

        def chat(protocol, base, key, model, system, turns, **kwargs):
            self.assertIn("确认时间", turns[0])
            kwargs["usage_sink"]({"input_tokens": 10, "output_tokens": 15})
            return '["周五可以", "周六见", "下周三怎么样"]'

        with patch.object(engine, "ask", side_effect=ask), patch.object(draft, "chat", side_effect=chat):
            result = engine.analyze([("her", "哪天有空")], "friends", goal="确认时间", llm_key="demo")
        self.assertEqual(result["best_index"], 1)
        self.assertEqual(result["scores"], [None, 0.8, None])
        self.assertEqual(result["usage"], {"input_tokens": 50})
        self.assertEqual(result["stages"]["draft"]["usage"], {"input_tokens": 10, "output_tokens": 15})
        self.assertGreater(result["generated_at"], 0)
        self.assertEqual(set(result["stages"]), {"judge", "draft", "rank"})


class ApplicationTests(unittest.TestCase):
    def setUp(self):
        self.app = load_with_stubs("main",
                                  **{"app.capture": {"find_chat_hwnd": Mock(), "window_identity": Mock()},
                                     "app.fill": {"fill": Mock()}, "app.worker": {},
                                     "app.overlay": {"Overlay": Mock()}})
        app = self.app
        app.chats.clear()
        app.state.clear()
        app.state.update(session=1, config=0, hwnd=1, identity=(1, 2), version=None,
                         layout=None, chat="", wanted=None, inflight=None)
        app.q = queue.Queue()
        app.results = queue.Queue()
        app.update_result = queue.Queue()
        app.child, app.dbg = Mock(), None
        app.epoch = types.SimpleNamespace(value=0)
        app.capture_on = threading.Event()
        app.capture_on.set()
        app.ov = Mock()
        app.ov.current_chat.return_value = "A"
        app.fill.reset_mock()

    def frame(self, title, transition, new=(), visible=()):
        app = self.app
        app.epoch.value += 1
        layout = ((800, 1000), (400, 40, 990, 600, 0), b"signature") if title else None
        app.q.put(("frame", app.epoch.value, title, layout, transition, new, visible))
        app.drain()

    def generate(self):
        self.frame("A", "baseline", visible=[("me", None, "你好")])
        self.frame("A", "append", [("her", "甲", "在吗")])
        app = self.app
        app.state["wanted"] = None
        result = {"candidates": ["旧建议"]}
        app.results.put(("ok", result, app.state["version"]))
        app.tick()
        self.assertTrue(app.can_fill(result))
        return result

    def assert_no_fill(self, result):
        with self.assertRaises(RuntimeError):
            self.app.fill_reply(result["candidates"][0], result)
        self.app.fill.assert_not_called()

    def test_own_reply_prevents_filling_cached_reply(self):
        result = self.generate()
        self.frame("A", "append", [("me", None, "已回复")])
        self.app.ov.current_chat.return_value = "B"
        self.app.ov.current_chat.return_value = "A"
        self.assert_no_fill(result)
        self.assertIs(self.app.chat_of("A")["result"], result)

    def test_late_result_for_chat_being_browsed_does_not_enable_fill(self):
        app = self.app
        cached = self.generate()
        version = app.state["version"]
        self.frame("B", "baseline", visible=[("me", None, "B 的聊天")])
        late = {"candidates": ["晚到的 A 建议"]}
        app.ov.show.reset_mock()
        app.results.put(("ok", late, version))
        app.tick()
        app.ov.show.assert_not_called()
        self.assertIs(app.chat_of("A")["result"], cached)
        self.assert_no_fill(cached)

    def test_result_for_live_chat_is_cached_while_viewing_another(self):
        result = self.generate()
        app = self.app
        app.ov.current_chat.return_value = "B"
        app.ov.show.reset_mock()
        replacement = {"candidates": ["A 的建议"]}
        app.results.put(("ok", replacement, app.state["version"]))
        app.tick()
        app.ov.show.assert_not_called()
        self.assertIs(app.chat_of("A")["result"], replacement)
        self.assert_no_fill(result)
        self.assert_no_fill(replacement)

    def test_title_loss_does_not_leave_fill_available(self):
        result = self.generate()
        self.frame("", "unknown")
        self.assert_no_fill(result)
        self.assertEqual(self.app.state["chat"], "")

    def test_unprocessed_frame_immediately_blocks_fill(self):
        result = self.generate()
        self.app.epoch.value += 1
        self.assert_no_fill(result)

    def test_pause_and_reopen_never_reactivates_cached_reply(self):
        result = self.generate()
        self.app.q.close = Mock()
        self.app.on_toggle_capture(False)
        self.assert_no_fill(result)
        self.app.state["session"] += 1
        self.app.capture_on.set()
        self.app.child = Mock()
        self.frame("A", "baseline", visible=[("her", "甲", "在吗")])
        self.assert_no_fill(result)

    def test_settings_save_and_target_change_each_invalidate_results(self):
        app = self.app
        result = self.generate()
        app.on_target_change("A", "甲")
        self.assert_no_fill(result)
        app.state["wanted"] = None
        replacement = {"candidates": ["新建议"]}
        app.results.put(("ok", replacement, app.state["version"]))
        app.tick()
        self.assertTrue(app.can_fill(replacement))
        app.on_settings_saved()
        self.assert_no_fill(replacement)

    def test_fill_uses_the_snapshot_and_requires_final_validity(self):
        app = self.app
        result = self.generate()
        valid_layout = app.state["layout"]
        app.fill_reply("旧建议", result)
        hwnd, identity, layout, text, check = app.fill.call_args.args
        self.assertEqual((hwnd, identity, layout, text), (1, (1, 2), valid_layout, "旧建议"))
        self.assertTrue(check())
        app.epoch.value += 1
        self.assertFalse(check())

    def test_burst_is_debounced_and_captures_latest_messages_and_settings(self):
        app = self.app
        clock = types.SimpleNamespace(now=10)
        self.frame("A", "baseline", visible=[("me", None, "问候")])
        config = types.SimpleNamespace(jev_key="j", llm_key="l", reply_target=True)
        with patch.object(app.time, "monotonic", side_effect=lambda: clock.now), \
                patch.object(app.settings, "analysis_settings", return_value=config), \
                patch.object(app.threading, "Thread") as thread:
            self.frame("A", "append", [("her", "甲", "第一条")])
            clock.now += 0.25
            self.frame("A", "append", [("her", "乙", "第二条")])
            clock.now += 0.25
            app.tick()
            thread.assert_not_called()
            clock.now += 0.25
            app.tick()
            _, version, snapshot, target = thread.call_args.kwargs["args"]
            self.assertEqual(thread.call_args.kwargs["args"][0][-2:],
                             (("her", "第一条", "甲"), ("her", "第二条", "乙")))
            self.assertEqual(target, "乙")
            self.assertIs(snapshot, config)
            self.assertTrue(app.is_current(version))

    def test_llm_key_alone_starts_degraded_analysis(self):
        """没有 Jev key 时仍应开始起草，只是提示降级。"""
        app = self.app
        clock = types.SimpleNamespace(now=10)
        self.frame("A", "baseline", visible=[("me", None, "问候")])
        config = types.SimpleNamespace(jev_key="", llm_key="l", reply_target=False)
        with patch.object(app.time, "monotonic", side_effect=lambda: clock.now), \
                patch.object(app.settings, "analysis_settings", return_value=config), \
                patch.object(app.threading, "Thread") as thread:
            self.frame("A", "append", [("her", "甲", "在吗")])
            clock.now += 1
            app.tick()
            thread.assert_called_once()
            app.ov.set_status.assert_any_call("未配置 Jev：只起草候选，跳过判断与排序", "warning")


class TargetOverlayTests(unittest.TestCase):
    def test_unidentified_speaker_leaves_single_known_member_selectable(self):
        overlay_module = load_with_stubs(
            "app.overlay",
            **{"PySide6.QtCore": dict.fromkeys(("QObject", "QSize", "Qt", "QTimer"), object) | {"Signal": Mock()},
               "PySide6.QtGui": dict.fromkeys(("QColor", "QFont"), object),
               "PySide6.QtWidgets": dict.fromkeys(
                   ("QApplication", "QFrame", "QHBoxLayout", "QPushButton", "QSizeGrip",
                    "QSizePolicy", "QStackedWidget", "QVBoxLayout", "QWidget"), object),
               "qfluentwidgets": dict.fromkeys(
                   ("BodyLabel", "CardWidget", "CheckBox", "ComboBox", "EditableComboBox",
                    "FluentIcon", "HyperlinkButton", "IndeterminateProgressBar", "LineEdit",
                    "PasswordLineEdit", "PlainTextEdit", "PrimaryPushButton", "PushButton",
                    "ScrollArea", "SpinBox", "SwitchButton", "Theme", "TransparentToolButton",
                    "setCustomStyleSheet", "setFont", "setTheme", "setThemeColor"), object)})
        overlay = object.__new__(overlay_module.Overlay)
        overlay._shown = "群聊"
        overlay.targets = {}
        overlay.targetRow = Mock()
        overlay.set_status = Mock()
        overlay.on_target_change = Mock()

        class Combo:
            index, blocked = -1, False

            def clear(self):
                self.items = []
                self.setCurrentIndex(-1)

            def blockSignals(self, blocked):
                self.blocked = blocked

            def addItems(self, items):
                self.items.extend(items)
                if self.items and self.index == -1:
                    self.setCurrentIndex(0)

            def setCurrentIndex(self, index):
                changed, self.index = self.index != index, index
                if changed and not self.blocked:
                    overlay._on_target_selected(index)

            def itemText(self, index):
                return self.items[index] if 0 <= index < len(self.items) else ""

        overlay.targetBox = Combo()
        with patch.object(settings, "reply_target", return_value=True):
            overlay.set_targets("群聊", ["Alice"], None)
            self.assertNotEqual(overlay.targetBox.itemText(overlay.targetBox.index), "Alice")
            overlay.on_target_change.assert_not_called()
            overlay.targetBox.setCurrentIndex(overlay.targetBox.items.index("Alice"))
        overlay.on_target_change.assert_called_once_with("群聊", "Alice")
        self.assertEqual(overlay.targetBox.itemText(overlay.targetBox.index), "Alice")


class WorkerTests(unittest.TestCase):
    def test_unreadable_new_header_emits_no_attributed_lines(self):
        class Frame:
            def __getitem__(self, key):
                return self

        first, second = Frame(), Frame()
        frames = [(1, first), (2, second)]
        area = (0, 40, 200, 220, None, 0)
        reader = Mock()
        reader.read.return_value = [("her", None, "这条消息的会话未知", 100)]
        reader.new_lines.return_value = []
        reader.transition = "baseline"
        capture = Mock()
        capture.alive.side_effect = lambda: bool(frames)
        capture.settled.side_effect = lambda: frames.pop(0)
        with patch.object(ctypes, "windll", Mock(), create=True):
            worker = load_with_stubs("app.worker", numpy={"array_equal": lambda a, b: a is b},
                                     **{"app.capture": {"Capture": lambda *a, **k: capture,
                                                        "chat_area": lambda f: area, "unminimize": Mock(),
                                                        "layout_signature": lambda *a: b"signature",
                                                        "window_identity": lambda hwnd: (1, 2)},
                                        "app.ocr": {"Reader": lambda app: reader,
                                                    "read_title": Mock(side_effect=["已确认 A", ""])},
                                        "app.chatapps": {"get": lambda k: None}})
            events, active, debug = queue.Queue(), threading.Event(), threading.Event()
            active.set()
            worker.run(events, 3, active, debug, "wechat", types.SimpleNamespace(value=2), (1, 2))
        packets = [event for event in list(events.queue) if event[0] == "frame"]
        self.assertEqual(packets[0][2], "已确认 A")
        self.assertEqual(packets[1], ("frame", 2, "", None, "unknown", [], []))
        self.assertEqual(reader.read.call_count, 1)


class FillTests(unittest.TestCase):
    def test_fresh_layout_mismatch_prevents_mouse_or_keyboard_input(self):
        self.exercise(layout_matches=False, focus_lost=False)

    def test_focus_loss_after_click_prevents_keyboard_input(self):
        self.exercise(layout_matches=True, focus_lost=True)

    def test_verified_target_pastes_without_enter(self):
        self.exercise(layout_matches=True, focus_lost=False)

    def exercise(self, layout_matches, focus_lost):
        u32, k32 = Mock(), Mock()
        windll = types.SimpleNamespace(user32=u32, kernel32=k32, dwmapi=Mock())
        with patch.object(ctypes, "windll", windll, create=True), patch.dict(sys.modules):
            sys.modules.pop("app.fill", None)
            fill = importlib.import_module("app.fill")
        identity = (1, 2)
        layout = ((800, 1000), (400, 40, 990, 600, 0), b"signature")
        u32.GetForegroundWindow.return_value = 1
        u32.IsIconic.return_value = False
        u32.GetAncestor.return_value = 1
        u32.GetGUIThreadInfo.side_effect = lambda thread, info: gui_focus(info._obj)

        def gui_focus(info):
            info.hwndActive = 1
            info.hwndFocus = 2 if focus_lost else 1
            return True

        u32.IsChild.return_value = False
        capture = types.SimpleNamespace(fresh_layout=lambda hwnd: layout if layout_matches else None,
                                        window_identity=lambda hwnd: identity)
        with patch.dict(sys.modules, {"app.capture": capture}), \
                patch.object(fill, "set_clipboard"), \
                patch.object(fill, "_bounds", return_value=(10, 20, 1010, 820)):
            if layout_matches and not focus_lost:
                fill.fill(1, identity, layout, "候选", lambda: True)
            else:
                with self.assertRaises(RuntimeError):
                    fill.fill(1, identity, layout, "候选", lambda: True)
        if not layout_matches:
            fill.u32.mouse_event.assert_not_called()
        if not layout_matches or focus_lost:
            fill.u32.keybd_event.assert_not_called()
        else:
            keys = [call.args[0] for call in fill.u32.keybd_event.call_args_list]
            self.assertEqual(keys, [0x11, 0x23, 0x23, 0x11, 0x11, 0x56, 0x56, 0x11])
        fill.u32.reset_mock()


if __name__ == "__main__":
    unittest.main()
