# -*- coding: utf-8 -*-
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from desktop_assistant.agent_framework import (  # noqa: E402
    Agent,
    AgentOptions,
    PermissionResult,
    SubagentSpec,
    Tool,
    ToolResult,
    default_subagents,
)


def _echo_tool():
    return Tool("echo", "回显文本", {"type": "object", "properties": {}},
                lambda args: ToolResult("echo:" + str(args.get("text", ""))))


def _other_tool():
    return Tool("other", "另一个工具", {"type": "object", "properties": {}},
                lambda args: ToolResult("other"))


def _tool_call(name, args, call_id="call_1"):
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}


class ScriptedChat:
    """按调用次数返回脚本化响应；子智能体按系统提示识别。"""

    def __init__(self, parent_responses):
        self.parent_responses = list(parent_responses)
        self.child_calls = 0

    def __call__(self, agent):
        system = agent.options.system_prompt
        if "子智能体" in system:
            self.child_calls += 1
            return {"content": "子结论：42", "tool_calls": []}
        if self.parent_responses:
            return self.parent_responses.pop(0)
        return {"content": "完成", "tool_calls": []}


def test_subagent_delegation_emits_events(monkeypatch):
    responses = [
        {"content": "", "tool_calls": [
            _tool_call("task", {"agent": "researcher", "prompt": "计算 6*7"})]},
        {"content": "主智能体完成", "tool_calls": []},
    ]
    fake = ScriptedChat(responses)
    monkeypatch.setattr(Agent, "_chat", lambda self: fake(self))

    events = []
    options = AgentOptions(
        api_key="test", base_url="http://localhost", model="m",
        tools=[_echo_tool()],
        subagents=[SubagentSpec("researcher", "检索", system_prompt="子智能体提示",
                                tools=[_echo_tool()], max_turns=2)],
        max_subagent_depth=2,
        on_event=events.append,
    )
    agent = Agent(options)
    out = list(agent.run("做一个任务"))

    assert out[-1].type == "final"
    assert out[-1].text == "主智能体完成"
    types = [e.type for e in events]
    assert "subagent_start" in types
    assert "subagent_end" in types
    assert fake.child_calls == 1
    end = next(e for e in events if e.type == "subagent_end")
    assert "子结论：42" in end.tool_result
    assert end.agent_name == "researcher"
    assert "42" in agent.history[-1]["content"]


def test_subagent_tool_filter_and_depth_guard(monkeypatch):
    fake = ScriptedChat([{"content": "ok", "tool_calls": []}])
    monkeypatch.setattr(Agent, "_chat", lambda self: fake(self))

    spec = SubagentSpec("writer", "写作", allowed_tools=["other"])
    agent = Agent(AgentOptions(api_key="x", tools=[_echo_tool(), _other_tool()],
                               subagents=[spec], max_subagent_depth=1))
    tools = agent._tools_for_spec(spec)
    assert [t.name for t in tools] == ["other"]

    agent.depth = 1
    result = agent._run_subagent({"agent": "writer", "prompt": "写"})
    assert result.is_error
    assert "嵌套上限" in result.content


def test_permission_denied_is_reported(monkeypatch):
    responses = [
        {"content": "", "tool_calls": [_tool_call("echo", {"text": "hi"})]},
        {"content": "抱歉", "tool_calls": []},
    ]
    fake = ScriptedChat(responses)
    monkeypatch.setattr(Agent, "_chat", lambda self: fake(self))

    seen = []

    def deny(name, args):
        seen.append(name)
        return PermissionResult(allow=False, message="no")

    agent = Agent(AgentOptions(api_key="x", tools=[_echo_tool()],
                               can_use_tool=deny))
    out = list(agent.run("调用工具"))
    result = next(e for e in out if e.type == "tool_result")
    assert result.is_error
    assert "未获授权" in result.tool_result
    assert seen == ["echo"]


def test_stop_before_run_is_immediate():
    agent = Agent(AgentOptions(api_key="x", tools=[_echo_tool()],
                               on_event=lambda e: None))
    agent.stop()
    out = list(agent.run("你好"))
    assert out[-1].type == "final"
    assert "已停止" in out[-1].text


def test_stream_tool_call_assembly():
    from desktop_assistant.agent_framework import _assembled_tool_calls, _merge_tool_delta

    calls = {}
    _merge_tool_delta(calls, {"index": 0, "id": "a", "function": {"name": "get_", "arguments": "{\"pa"}})
    _merge_tool_delta(calls, {"index": 0, "function": {"name": "time", "arguments": "th\": 1}"}})
    _merge_tool_delta(calls, {"index": 1, "id": "b", "function": {"name": "echo", "arguments": "{}"}})
    out = _assembled_tool_calls(calls)
    assert [c["id"] for c in out] == ["a", "b"]
    assert out[0]["function"]["name"] == "get_time"
    assert json.loads(out[0]["function"]["arguments"]) == {"path": 1}


def test_default_subagents_are_available():
    specs = default_subagents(os.getcwd())
    names = {s.name for s in specs}
    assert {"researcher", "coder", "writer"} <= names
    assert all(s.description for s in specs)


def test_streaming_text_via_fake_requests(monkeypatch):
    import sys
    from types import SimpleNamespace

    class FakeResponse:
        status_code = 200
        headers = {"Content-Type": "text/event-stream; charset=utf-8"}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def iter_lines(self, decode_unicode=False):
            for piece in ("你", "好"):
                payload = json.dumps({"choices": [{"delta": {"content": piece}}]},
                                     ensure_ascii=False)
                yield ("data: " + payload).encode("utf-8")
                yield b""
            yield b"data: [DONE]"

    fake_requests = SimpleNamespace(post=lambda *a, **k: FakeResponse())
    monkeypatch.setitem(sys.modules, "requests", fake_requests)

    agent = Agent(AgentOptions(api_key="x", stream=True))
    events = list(agent.run("你好"))
    deltas = [e.text for e in events if e.type == "text_delta"]
    assert deltas == ["你", "好"]
    assert events[-1].type == "final"
    assert events[-1].text == "你好"


def test_history_trimming_starts_at_user(monkeypatch):
    agent = Agent(AgentOptions(api_key="x", tools=[_echo_tool()],
                               max_history_messages=4))
    agent.history = [
        {"role": "user", "content": "old"},
        {"role": "assistant", "content": "old reply"},
        {"role": "user", "content": "new"},
        {"role": "assistant", "content": "",
         "tool_calls": [_tool_call("echo", {"text": "x"})]},
        {"role": "tool", "tool_call_id": "call_1", "content": "echo:x"},
    ]
    trimmed = agent._history_for_request()
    assert trimmed[0]["role"] == "user"
    assert trimmed[0]["content"] == "new"
    assert len(trimmed) == 3


def test_history_trimming_keeps_long_tool_chain():
    """一次提问产生很多工具调用时，不能截断出孤立的 tool 消息。"""
    agent = Agent(AgentOptions(api_key="x", tools=[_echo_tool()],
                               max_history_messages=4))
    agent.history = [{"role": "user", "content": "查文献"}]
    for index in range(6):
        call_id = f"call_{index}"
        agent.history.append({
            "role": "assistant", "content": "",
            "tool_calls": [_tool_call("echo", {"text": str(index)}, call_id)],
        })
        agent.history.append({"role": "tool", "tool_call_id": call_id,
                              "content": f"echo:{index}"})
    trimmed = agent._history_for_request()
    assert trimmed[0]["role"] == "user"
    assert len(trimmed) == len(agent.history)
    assert all(message.get("role") != "tool" for message in trimmed[:1])


def test_history_trimming_drops_leading_orphan_tool():
    agent = Agent(AgentOptions(api_key="x", tools=[_echo_tool()],
                               max_history_messages=2))
    agent.history = [
        {"role": "tool", "tool_call_id": "old", "content": "orphan"},
        {"role": "assistant", "content": "reply"},
    ]
    trimmed = agent._history_for_request()
    assert trimmed
    assert trimmed[0]["role"] != "tool"


def test_ask_user_tool_round_trip(monkeypatch):
    responses = [
        {"content": "", "tool_calls": [
            _tool_call("ask_user", {"question": "选哪个？", "options": ["苹果", "香蕉"]})]},
        {"content": "好的", "tool_calls": []},
    ]
    fake = ScriptedChat(responses)
    monkeypatch.setattr(Agent, "_chat", lambda self: fake(self))

    seen = []
    agent = Agent(AgentOptions(
        api_key="x", tools=[_echo_tool()],
        ask_user=lambda question, options: (seen.append((question, options)), "香蕉")[1],
    ))
    assert "ask_user" in agent.registry.names()
    out = list(agent.run("你决定"))
    result = next(e for e in out if e.type == "tool_result")
    assert "用户选择：香蕉" in result.tool_result
    assert seen == [("选哪个？", ["苹果", "香蕉"])]


def test_stop_closes_active_response(monkeypatch):
    class FakeResponse:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    agent = Agent(AgentOptions(api_key="x"))
    resp = FakeResponse()
    agent._active_response = resp
    agent.stop()
    assert resp.closed


def test_shell_stops_when_event_is_set():
    import threading
    from desktop_assistant.agent_framework import default_tools

    event = threading.Event()
    event.set()
    tools = {t.name: t for t in default_tools(os.getcwd(), stop_event=event)}
    result = tools["run_shell"].call({"command": "echo hello"})
    assert result.is_error
    assert "已停止" in result.content


def test_shell_is_killed_immediately_on_stop():
    import sys
    import threading
    import time
    from desktop_assistant.agent_framework import default_tools

    event = threading.Event()
    tools = {t.name: t for t in default_tools(os.getcwd(), stop_event=event)}
    command = f'"{sys.executable}" -c "import time; time.sleep(8)"'
    box = {}

    def run():
        box["result"] = tools["run_shell"].call({"command": command, "timeout": 20})

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    time.sleep(0.6)
    event.set()
    thread.join(4)
    assert not thread.is_alive()
    assert box["result"].is_error
    assert "已停止" in box["result"].content


def test_finder_subagent_is_available():
    from desktop_assistant.local_subagents import available_subagents

    specs = available_subagents(os.getcwd())
    names = {spec.name for spec in specs}
    assert "finder" in names
    finder = next(spec for spec in specs if spec.name == "finder")
    tool_names = {tool.name for tool in finder.tools}
    assert "search_files" in tool_names
    assert "read_file" in tool_names


def test_computer_subagent_is_available(tmp_path):
    from desktop_assistant.local_subagents import available_subagents

    specs = available_subagents(os.getcwd(), workspace=str(tmp_path / "workspace"))
    computer = next(spec for spec in specs if spec.name == "computer")
    names = {tool.name for tool in computer.tools}
    assert {"screen_capture", "mouse_click", "keyboard_type",
            "workspace_write", "workspace_run_python"}.issubset(names)
    assert str(tmp_path / "workspace") in computer.system_prompt


def test_run_shell_prefers_git_bash_and_allows_git_commands():
    from desktop_assistant.agent_framework import _git_bash_path, default_tools

    bash = _git_bash_path()
    if not bash:
        return
    tools = {t.name: t for t in default_tools(os.getcwd())}
    result = tools["run_shell"].call({
        "command": "printf 'git-bash-ok'; echo; git --version",
        "shell": "bash",
        "timeout": 20,
    })
    assert not result.is_error
    assert "git-bash-ok" in result.content
    assert "git version" in result.content.lower()
    auto = tools["run_shell"].call({"command": "git --version", "timeout": 20})
    assert not auto.is_error
    assert "git version" in auto.content.lower()


def test_librarian_subagent_is_available(tmp_path):
    from desktop_assistant.local_subagents import available_subagents

    specs = available_subagents(os.getcwd(), database=str(tmp_path / "database"))
    librarian = next(spec for spec in specs if spec.name == "librarian")
    names = {tool.name for tool in librarian.tools}
    assert {"database_search", "database_add_file", "database_note",
            "vault_add", "vault_get", "open_report"}.issubset(names)
    assert str(tmp_path / "database") in librarian.system_prompt
