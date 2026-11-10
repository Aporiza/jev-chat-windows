# -*- coding: utf-8 -*-
"""设置持久化。key 硬约束（docs/KICKOFF.md #6）：只进环境变量，绝不落文件；其余设置落 config.json。

key 的持久化走 Windows 用户环境变量（注册表 HKCU\\Environment，跟 setx 写的是同一个地方）。
全程只有两把：判断 JEV_API_KEY、起草 LLM_API_KEY，跟选哪家来源无关。
读的时候先看进程环境，没有就直接读注册表——IDE 启动时把环境快照拿走了，之后再 Run 继承的还是旧环境，
只靠 os.environ 会「保存了下次打开还是没有」。"""
from __future__ import annotations

import ctypes
import json
import os
from dataclasses import dataclass
from hashlib import sha256

import sys  # 只为下面这一处：打包后 __file__ 指向临时解包目录，config.json 得放在 exe 旁边才存得住

from core.providers import CUSTOM, DRAFT_PROVIDERS, JEV_ENV, JEV_PROVIDERS, LEGACY, LLM_ENV

_ROOT = (os.path.dirname(sys.executable) if getattr(sys, "frozen", False)
         else os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_CONFIG = os.path.join(_ROOT, "config.json")
_DEFAULT_RELATIONSHIP = "unspecified"
_DEFAULT_CONTEXT = 10
_DEFAULT_JEV = "openrouter"
_DEFAULT_DRAFT = "deepseek"


def _config():
    try:
        with open(_CONFIG, encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _read(name: str, default=None):
    value = _config().get(name)
    return default if value is None else value


def _context(value):
    try:
        n = int(value)
    except (TypeError, ValueError, OverflowError):
        return _DEFAULT_CONTEXT
    return max(3, min(30, n))


def relationship() -> str:
    return str(_read("relationship") or _DEFAULT_RELATIONSHIP)

def context() -> int:
    """参考上下文条数：起草和判断各看最近多少条消息。3~30，缺失/脏数据一律退默认值。"""
    return _context(_read("context"))


def style() -> str:
    """用户自己描述的说话风格（可选，自由文本），只喂给起草模型。默认空 = 只照着最近的消息模仿。"""
    return str(_read("style") or "")

def jev_provider() -> str:
    """判断模型走哪家：openrouter（默认）或 typesafe 直连。"""
    v = _read("jev_provider")
    return v if v in JEV_PROVIDERS else _DEFAULT_JEV

def jev_model() -> str:
    """判断模型 id；空 = 用该来源的默认模型。"""
    return str(_read("jev_model") or "") or JEV_PROVIDERS[jev_provider()].default

def draft_provider() -> str:
    """起草走哪家（见 core/providers.DRAFT_PROVIDERS）。老配置里的 openrouter/deepseek 照样认。"""
    v = _read("draft_provider")
    return v if v in DRAFT_PROVIDERS else _DEFAULT_DRAFT

def draft_provider_name() -> str:
    return DRAFT_PROVIDERS[draft_provider()].name

def draft_model() -> str:
    """起草模型 id；空 = 用该来源的默认模型（有的来源没有默认，那就得自己选）。"""
    return str(_read("draft_model") or "") or DRAFT_PROVIDERS[draft_provider()].default

def draft_base_url() -> str:
    """自定义来源的 Base URL；其余来源用表里的，这里返回空。"""
    return str(_read("draft_base_url") or "") if draft_provider() in CUSTOM else ""

def reply_target() -> bool:
    """群聊指定回复对象：开了才在界面上选回复给谁、才把对象喂给模型。默认关。"""
    return bool(_read("reply_target", False))

def thinking() -> bool:
    """起草时是否开思考模式：慢且贵，默认关。只有 DeepSeek / OpenRouter / Anthropic / Gemini 吃它。"""
    return bool(_read("thinking", False))

def check_update() -> bool:
    """启动时要不要去 GitHub 查一次最新版本号：默认开，只出这一次网，设置里能关。"""
    return bool(_read("check_update", True))

def debug_view() -> bool:
    """调试视图：另开一个窗口实时画识别框。默认关，开了子进程才往队列里送帧。"""
    return bool(_read("debug_view", False))

def _read_env(env_name: str) -> str:
    """进程环境优先；没有就读注册表并带进进程环境，之后 core/ 里按 os.environ 读就有了。"""
    v = os.environ.get(env_name, "").strip()
    if not v:
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
                v = str(winreg.QueryValueEx(k, env_name)[0]).strip()
        except Exception:  # 非 Windows / 没这个值
            v = ""
        if v:
            os.environ[env_name] = v
    return v

def _get_key(env_name: str) -> str:
    """两把 key 之一。新名字空着就退回老版本按来源存的变量（下次保存会抄进新名字）。"""
    return _read_env(env_name) or _read_env(LEGACY[env_name])

def _set_key(env_name: str, value: str) -> None:
    """只写进程环境 + HKCU\\Environment，不写任何文件。"""
    os.environ[env_name] = value
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, env_name, 0, winreg.REG_SZ, value)
    except Exception:
        pass  # 非 Windows（本机 Mac 开发）走不到，忽略


def _notify_env() -> None:
    """告诉别的进程环境变量变了。不能用 SendMessageTimeout 对 HWND_BROADCAST：
    它会逐个窗口等回复，超时 5 秒还按窗口数累加，保存按钮在界面线程上就卡死。
    SendNotifyMessage 把消息交出去就返回。"""
    try:
        fn = ctypes.windll.user32.SendNotifyMessageW
        fn.argtypes = (ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_wchar_p)
        fn.restype = ctypes.c_int
        fn(0xFFFF, 0x001A, 0, "Environment")  # HWND_BROADCAST, WM_SETTINGCHANGE
    except Exception:
        pass

def jev_key() -> str:
    """判断那把 key，两家来源共用。"""
    return _get_key(JEV_ENV)

def has_jev_key() -> bool:
    return bool(jev_key())

def llm_key() -> str:
    """起草那把 key，所有语言模型来源共用。"""
    return _get_key(LLM_ENV)

def has_llm_key() -> bool:
    return bool(llm_key())

has_key = has_jev_key  # 旧名字：界面上「配没配好」问的就是判断模型这把 key

def _preferences(data, title):
    scoped = data.get("chat_preferences")
    key = sha256(title.encode("utf-8")).hexdigest() if title else ""
    values = scoped.get(key) if key and isinstance(scoped, dict) else None
    values = values if isinstance(values, dict) else {}
    return (str(values.get("relationship", data.get("relationship")) or _DEFAULT_RELATIONSHIP),
            str(values.get("style", data.get("style")) or ""),
            str(values.get("goal", data.get("goal")) or ""))


def preferences(title=""):
    return _preferences(_config(), title)


def save_preferences(title, relationship, style, goal):
    """仅保存用户填写的偏好。标题以摘要为索引，不写入标题或识别到的对话。"""
    save(preference_title=title, relationship_text=relationship, style_text=style, goal_text=goal)


@dataclass(frozen=True)
class AnalysisSettings:
    relationship: str
    style: str
    goal: str
    context: int
    reply_target: bool
    thinking: bool
    jev_provider: str
    jev_model: str
    draft_provider: str
    draft_model: str
    draft_base_url: str
    jev_key: str
    llm_key: str


def analysis_settings(title):
    data = _config()
    jev = data.get("jev_provider")
    jev = jev if isinstance(jev, str) and jev in JEV_PROVIDERS else _DEFAULT_JEV
    draft = data.get("draft_provider")
    draft = draft if isinstance(draft, str) and draft in DRAFT_PROVIDERS else _DEFAULT_DRAFT
    return AnalysisSettings(
        *_preferences(data, title), _context(data.get("context")),
        bool(data.get("reply_target")), bool(data.get("thinking")),
        jev, str(data.get("jev_model") or JEV_PROVIDERS[jev].default),
        draft, str(data.get("draft_model") or DRAFT_PROVIDERS[draft].default),
        str(data.get("draft_base_url") or "") if draft in CUSTOM else "",
        jev_key(), llm_key(),
    )


def save(relationship_text: str | None = None, context_n: int | None = None, *,
         jev_provider_text: str | None = None, jev_key_text: str | None = None,
         jev_model_text: str | None = None, draft_provider_text: str | None = None,
         llm_key_text: str | None = None, draft_model_text: str | None = None,
         draft_base_url_text: str | None = None, reply_target_on: bool | None = None,
         style_text: str | None = None, thinking_on: bool | None = None,
         check_update_on: bool | None = None, debug_view_on: bool | None = None,
         preference_title: str = "", goal_text: str | None = None) -> None:
    """None 保留当前值。密钥只写进程环境和 HKCU\\Environment。"""
    data = _config()
    for name, value in (
        ("context", None if context_n is None else _context(context_n)),
        ("jev_provider", jev_provider_text), ("jev_model", jev_model_text),
        ("draft_provider", draft_provider_text), ("draft_model", draft_model_text),
        ("draft_base_url", draft_base_url_text), ("reply_target", reply_target_on),
        ("thinking", thinking_on), ("check_update", check_update_on), ("debug_view", debug_view_on),
    ):
        if value is not None:
            data[name] = value.strip() if isinstance(value, str) else value
    values = data
    if preference_title:
        key = sha256(preference_title.encode("utf-8")).hexdigest()
        if not isinstance(data.get("chat_preferences"), dict):
            data["chat_preferences"] = {}
        scoped = data["chat_preferences"]
        if not isinstance(scoped.get(key), dict):
            scoped[key] = {}
        values = scoped[key]
    for name, value in (("relationship", relationship_text), ("style", style_text), ("goal", goal_text)):
        if value is not None:
            values[name] = value.strip()
    # 原子替换，保存失败时保留旧设置；密钥始终在文件操作完成之后保存。
    from tempfile import NamedTemporaryFile

    path = None
    try:
        with NamedTemporaryFile("w", dir=os.path.dirname(_CONFIG) or ".", encoding="utf-8",
                                delete=False) as f:
            path = f.name
            json.dump(data, f, ensure_ascii=False)
        os.replace(path, _CONFIG)
        path = None
    finally:
        if path is not None:
            os.unlink(path)
    wrote_key = False
    for env, typed in ((JEV_ENV, jev_key_text), (LLM_ENV, llm_key_text)):
        value = typed or ("" if _read_env(env) else _get_key(env))
        if value:
            _set_key(env, value)
            wrote_key = True
    if wrote_key:
        _notify_env()
