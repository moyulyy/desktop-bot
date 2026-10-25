# -*- coding: utf-8 -*-
"""集中管理桌面助手的配置文件。

* ``config/agent_config.json`` —— 模型接口、生成参数、权限、子智能体
* ``config/asr_config.json``   —— 腾讯云一句话识别
* 界面偏好仍然存放在 ``QSettings``（由主程序读写）

所有写入都是「临时文件 + os.replace」的原子替换，避免程序中途退出把配置写坏。
环境变量仍然拥有最高优先级：即使设置页写入了密钥，部署时也可以用环境变量覆盖。
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
from typing import Any

APP_NAME = "AI 桌面助手"
APP_VERSION = "2.0.0"
PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.dirname(PACKAGE_DIR)
PROJECT_ROOT = os.path.dirname(SRC_DIR)
BASE_DIR = PROJECT_ROOT                 # 兼容旧代码 / 工具的工作目录
CONFIG_DIR = os.path.join(PROJECT_ROOT, "config")
AGENT_CONFIG_PATH = os.path.join(CONFIG_DIR, "agent_config.json")
ASR_CONFIG_PATH = os.path.join(CONFIG_DIR, "asr_config.json")

DEFAULT_SYSTEM = (
    "你是运行在用户桌面上的轻量 AI 助手。回答简洁、友好、口语化，默认使用简体中文；"
    "需要时使用 Markdown 组织答案，也可以用 ![说明](图片URL或本地路径) 展示图片。"
    "遇到需要检索资料、修改代码或整理长文的任务，可以把子任务委派给 task 工具里的子智能体，"
    "再把结论整合给用户。需要用户在多个方向中做决定时，调用 ask_user 给出 2-6 个简短选项。"
    "临时脚本、调试输出和中间结果统一写入项目下的 temp/（脚本放 temp/scripts，"
    "输出放 temp/notes），不要写到项目根目录。"
)

DEFAULT_AGENT_CONFIG: dict[str, Any] = {
    "api_key": "",
    "base_url": "https://api.openai.com/v1",
    "model": "gpt-4o-mini",
    "system": DEFAULT_SYSTEM,
    "temperature": 0.7,
    "max_tokens": 4096,
    "max_turns": 50,
    "permission_mode": "default",
    "stream": True,
    "workspace": "temp",
    "database": "database",
    "embedding_model": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
    "embedding_cache": "models",
    "rag_top_k": 8,
    "rag_chunk_tokens": 700,
    "rag_overlap": 100,
    "subagents": {
        "enabled": True,
        "max_depth": 2,
        "items": [],
    },
}

DEFAULT_ASR_CONFIG: dict[str, Any] = {
    "secret_id": "",
    "secret_key": "",
    "region": "ap-guangzhou",
    "engine": "16k_zh",
}

DEFAULT_PREFS: dict[str, Any] = {
    "appearance": "auto",          # auto / light / dark
    "opacity": 1.0,                # 0.35 ~ 1.0
    "idle_dim": True,              # 空闲时轻微淡出
    "notify_hidden": True,         # 气泡隐藏时用横幅预览回复
    "voice_enabled": True,         # 麦克风按钮
    "auto_show_chat": True,        # 发送消息时自动显示气泡
}

PERMISSION_MODES = {
    "default": "每次询问（只读工具直接放行）",
    "acceptEdits": "自动允许文件编辑（命令仍需询问）",
    "bypassPermissions": "全部允许（仅建议在可信环境使用）",
    "plan": "只读规划（拒绝所有工具）",
}

REGIONS = ["ap-guangzhou", "ap-shanghai", "ap-beijing", "ap-chengdu",
           "ap-chongqing", "ap-hongkong", "ap-singapore"]
ENGINES = ["16k_zh", "16k_zh_large", "16k_zh_dialect", "16k_en", "16k_yue",
           "16k_zh-PY"]


# --------------------------------------------------------------------------- #
# 基础读写
# --------------------------------------------------------------------------- #
def _read_json(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json(path: str, data: dict):
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in ("0", "false", "no", "off", "")
    if value is None:
        return default
    return bool(value)


def _as_float(value: Any, default: float, lo: float, hi: float) -> float:
    try:
        return max(lo, min(hi, float(value)))
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------- #
# Agent 配置
# --------------------------------------------------------------------------- #
def normalize_agent_config(cfg: dict | None) -> dict:
    cfg = copy.deepcopy(cfg or {})
    out = copy.deepcopy(DEFAULT_AGENT_CONFIG)
    for key in ("api_key", "base_url", "model", "system"):
        if cfg.get(key) is not None:
            out[key] = str(cfg[key]).strip()
    if not out["base_url"]:
        out["base_url"] = DEFAULT_AGENT_CONFIG["base_url"]
    if not out["model"]:
        out["model"] = DEFAULT_AGENT_CONFIG["model"]
    if not out["system"]:
        out["system"] = DEFAULT_SYSTEM
    out["temperature"] = _as_float(cfg.get("temperature"), 0.7, 0.0, 2.0)
    out["max_tokens"] = _as_int(cfg.get("max_tokens"), 4096, 64, 8192)
    out["max_turns"] = _as_int(cfg.get("max_turns"), 50, 1, 60)
    mode = str(cfg.get("permission_mode") or "default")
    out["permission_mode"] = mode if mode in PERMISSION_MODES else "default"
    out["stream"] = _as_bool(cfg.get("stream"), True)
    workspace = str(cfg.get("workspace") or "temp").strip()
    workspace = os.path.expandvars(os.path.expanduser(workspace))
    if not os.path.isabs(workspace):
        workspace = os.path.join(PROJECT_ROOT, workspace)
    out["workspace"] = os.path.abspath(workspace)
    database = str(cfg.get("database") or "database").strip()
    database = os.path.expandvars(os.path.expanduser(database))
    if not os.path.isabs(database):
        database = os.path.join(PROJECT_ROOT, database)
    out["database"] = os.path.abspath(database)
    out["embedding_model"] = str(cfg.get("embedding_model") or
                                 DEFAULT_AGENT_CONFIG["embedding_model"]).strip()
    cache = str(cfg.get("embedding_cache") or "models").strip()
    cache = os.path.expandvars(os.path.expanduser(cache))
    if not os.path.isabs(cache):
        cache = os.path.join(PROJECT_ROOT, cache)
    out["embedding_cache"] = os.path.abspath(cache)
    out["rag_top_k"] = max(1, min(30, int(cfg.get("rag_top_k", 8) or 8)))
    out["rag_chunk_tokens"] = max(200, min(2000, int(cfg.get("rag_chunk_tokens", 700) or 700)))
    out["rag_overlap"] = max(0, min(400, int(cfg.get("rag_overlap", 100) or 0)))

    sub = cfg.get("subagents")
    sub = sub if isinstance(sub, dict) else {}
    items = sub.get("items")
    if not isinstance(items, list):
        items = []
    clean_items = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        tools = item.get("tools")
        if isinstance(tools, str):
            tools = [x.strip() for x in tools.replace("，", ",").split(",") if x.strip()]
        if not isinstance(tools, list):
            tools = []
        clean_items.append({
            "name": name,
            "description": str(item.get("description") or "").strip(),
            "system": str(item.get("system") or "").strip(),
            "tools": [str(x).strip() for x in tools if str(x).strip()],
            "model": str(item.get("model") or "").strip(),
            "max_turns": _as_int(item.get("max_turns"), 6, 1, 30),
            "enabled": _as_bool(item.get("enabled"), True),
        })
    out["subagents"] = {
        "enabled": _as_bool(sub.get("enabled"), True),
        "max_depth": _as_int(sub.get("max_depth"), 2, 1, 4),
        "items": clean_items,
    }

    # 保留调用方自定义的未知字段，方便未来扩展
    for key, value in cfg.items():
        if key not in out:
            out[key] = value
    return out


def load_agent_config(apply_env: bool = True) -> dict:
    cfg = normalize_agent_config(_read_json(AGENT_CONFIG_PATH))
    if apply_env:
        env = {"api_key": "OPENAI_API_KEY", "base_url": "OPENAI_BASE_URL",
               "model": "OPENAI_MODEL", "system": "AGENT_SYSTEM"}
        for key, name in env.items():
            value = os.environ.get(name)
            if value:
                cfg[key] = value
    return cfg


def save_agent_config(cfg: dict) -> dict:
    clean = normalize_agent_config(cfg)
    _write_json(AGENT_CONFIG_PATH, clean)
    return clean


# --------------------------------------------------------------------------- #
# 语音识别配置
# --------------------------------------------------------------------------- #
def normalize_asr_config(cfg: dict | None) -> dict:
    cfg = cfg or {}
    return {
        "secret_id": str(cfg.get("secret_id") or "").strip(),
        "secret_key": str(cfg.get("secret_key") or "").strip(),
        "region": str(cfg.get("region") or "ap-guangzhou").strip() or "ap-guangzhou",
        "engine": str(cfg.get("engine") or "16k_zh").strip() or "16k_zh",
    }


def load_asr_config(apply_env: bool = True) -> dict:
    cfg = normalize_asr_config(_read_json(ASR_CONFIG_PATH))
    if apply_env:
        env = {"secret_id": ("TENCENT_SECRET_ID", "TENCENTCLOUD_SECRET_ID"),
               "secret_key": ("TENCENT_SECRET_KEY", "TENCENTCLOUD_SECRET_KEY"),
               "region": ("TENCENT_REGION", "TENCENTCLOUD_REGION"),
               "engine": ("TENCENT_ASR_ENGINE",)}
        for key, names in env.items():
            for name in names:
                if os.environ.get(name):
                    cfg[key] = os.environ[name]
                    break
    return cfg


def save_asr_config(cfg: dict) -> dict:
    clean = normalize_asr_config(cfg)
    _write_json(ASR_CONFIG_PATH, clean)
    return clean


# --------------------------------------------------------------------------- #
# 界面偏好
# --------------------------------------------------------------------------- #
def normalize_prefs(prefs: dict | None) -> dict:
    prefs = prefs or {}
    out = copy.deepcopy(DEFAULT_PREFS)
    appearance = str(prefs.get("appearance") or "auto")
    out["appearance"] = appearance if appearance in ("auto", "light", "dark") else "auto"
    out["opacity"] = _as_float(prefs.get("opacity"), 1.0, 0.35, 1.0)
    for key in ("idle_dim", "notify_hidden", "voice_enabled", "auto_show_chat"):
        out[key] = _as_bool(prefs.get(key), DEFAULT_PREFS[key])
    return out


# --------------------------------------------------------------------------- #
# 展示辅助
# --------------------------------------------------------------------------- #
def mask_secret(value: str, keep: int = 4) -> str:
    value = value or ""
    if not value:
        return "（未配置）"
    if len(value) <= keep * 2:
        return "•" * len(value)
    return f"{value[:keep]}…{value[-keep:]}"


def config_summary() -> list[tuple[str, str]]:
    """给「关于」页展示的配置来源。"""
    return [
        ("模型配置", AGENT_CONFIG_PATH),
        ("语音配置", ASR_CONFIG_PATH),
    ]
