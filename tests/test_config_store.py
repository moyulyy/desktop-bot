# -*- coding: utf-8 -*-
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from desktop_assistant import config_store as cs  # noqa: E402


def test_default_limits_are_4096_and_50():
    cfg = cs.normalize_agent_config({})
    assert cfg["max_tokens"] == 4096
    assert cfg["max_turns"] == 50


def test_normalize_agent_config_clamps_and_keeps_unknown():
    cfg = cs.normalize_agent_config({
        "temperature": 9,
        "max_tokens": 1,
        "max_turns": 0,
        "permission_mode": "nope",
        "stream": "false",
        "custom_future_key": {"a": 1},
        "subagents": {
            "enabled": "yes",
            "max_depth": 99,
            "items": [{
                "name": " helper ",
                "tools": "web_fetch， read_file",
                "max_turns": 100,
            }],
        },
    })
    assert cfg["temperature"] == 2.0
    assert cfg["max_tokens"] == 64
    assert cfg["max_turns"] == 1
    assert cfg["permission_mode"] == "default"
    assert cfg["stream"] is False
    assert cfg["custom_future_key"] == {"a": 1}
    assert cfg["subagents"]["max_depth"] == 4
    assert cfg["subagents"]["items"][0]["name"] == "helper"
    assert cfg["subagents"]["items"][0]["tools"] == ["web_fetch", "read_file"]
    assert cfg["subagents"]["items"][0]["max_turns"] == 30


def test_save_and_load_round_trip(tmp_path, monkeypatch):
    agent_path = tmp_path / "agent_config.json"
    asr_path = tmp_path / "asr_config.json"
    monkeypatch.setattr(cs, "AGENT_CONFIG_PATH", str(agent_path))
    monkeypatch.setattr(cs, "ASR_CONFIG_PATH", str(asr_path))

    cs.save_agent_config({"api_key": "sk-test", "model": "deepseek-chat",
                          "base_url": "https://api.deepseek.com"})
    cs.save_asr_config({"secret_id": "id", "secret_key": "key"})

    loaded = cs.load_agent_config(apply_env=False)
    assert loaded["api_key"] == "sk-test"
    assert loaded["model"] == "deepseek-chat"
    assert loaded["stream"] is True
    assert json.loads(agent_path.read_text(encoding="utf-8"))["api_key"] == "sk-test"

    asr = cs.load_asr_config(apply_env=False)
    assert asr["secret_id"] == "id"
    assert asr["region"] == "ap-guangzhou"


def test_env_override(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")
    monkeypatch.setenv("OPENAI_MODEL", "env-model")
    cfg = cs.load_agent_config(apply_env=True)
    assert cfg["api_key"] == "env-key"
    assert cfg["model"] == "env-model"


def test_prefs_and_mask():
    prefs = cs.normalize_prefs({"appearance": "pink", "opacity": "0.1",
                                "idle_dim": "off", "voice_enabled": 0})
    assert prefs["appearance"] == "auto"
    assert prefs["opacity"] == 0.35
    assert prefs["idle_dim"] is False
    assert prefs["voice_enabled"] is False
    assert cs.mask_secret("") == "（未配置）"
    assert "…" in cs.mask_secret("sk-1234567890")


def test_example_config_is_valid_and_matches_defaults():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, "config", "agent_config.example.json")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    cfg = cs.normalize_agent_config(data)
    assert cfg["stream"] is True
    assert cfg["subagents"]["enabled"] is True
    assert cfg["subagents"]["max_depth"] == 2
    assert cfg["permission_mode"] in cs.PERMISSION_MODES
    assert cfg["api_key"] and cfg["model"]
    assert cfg["max_tokens"] == 4096
    assert cfg["max_turns"] == 50


def test_readme_documents_extension_points():
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "README.md")
    text = open(path, "r", encoding="utf-8").read()
    for keyword in ("SubagentSpec", "settings_dialog.py", "python -m pytest tests",
                    "start.vbs"):
        assert keyword in text
    assert os.path.exists(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "start.vbs"))


def test_workspace_defaults_and_relative_paths():
    cfg = cs.normalize_agent_config({})
    assert os.path.isabs(cfg["workspace"])
    assert cfg["workspace"] == os.path.join(cs.PROJECT_ROOT, "temp")

    custom = cs.normalize_agent_config({"workspace": "my-work"})
    assert custom["workspace"] == os.path.join(cs.PROJECT_ROOT, "my-work")

    absolute = os.path.join(cs.PROJECT_ROOT, "absolute-work")
    assert cs.normalize_agent_config({"workspace": absolute})["workspace"] == absolute
