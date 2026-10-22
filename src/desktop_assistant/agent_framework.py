# -*- coding: utf-8 -*-
"""
轻量级智能体框架（设计参考 Anthropic 的 Claude Agent SDK / anthropic-sdk-python）
==============================================================================

这是一个不依赖任何专有大模型 SDK 的最小实现：只要后端兼容 OpenAI 的
`/chat/completions` + `tools`（function calling）协议，就能驱动「模型思考 →
调用工具 → 观察结果 → 继续思考」的智能体循环。

核心概念（对应 Claude Agent SDK）
---------------------------------
* ``Tool``              —— 可被模型调用的工具：名称 / 描述 / JSON Schema / 执行函数
* ``ToolRegistry``      —— 工具注册表，负责生成模型所需的 schema 并分发调用
* ``SubagentSpec``      —— 子智能体定义：名称 / 用途 / 系统提示 / 工具集 / 模型覆盖
* ``SubagentRegistry``  —— 子智能体注册表，供主智能体通过 ``task`` 工具委派任务
* ``HookEvent``         —— 生命周期钩子：UserPromptSubmit / PreToolUse / PostToolUse / Stop
* ``PermissionResult``  —— 工具授权结果，可覆盖输入（类似 SDK 的 canUseTool）
* ``AgentOptions``      —— 运行参数：系统提示、模型、最大轮数、权限模式、钩子……
* ``Agent``             —— 智能体本体，维护会话历史并执行 agent loop

子智能体
--------
给 ``AgentOptions.subagents`` 传入若干 ``SubagentSpec``，框架会自动注册一个
``task`` 工具。主模型可以这样委派：

    {"agent": "researcher", "prompt": "查一下 PySide6 6.8 的发布说明", "context": "..."}

子智能体拥有独立的会话历史、工具集与模型（可覆盖），运行过程通过
``subagent_start`` / ``subagent_event`` / ``subagent_end`` 事件实时上报。
子智能体也可以继续委派，层数由 ``max_subagent_depth`` 限制。

用法
----
    from agent_framework import Agent, AgentOptions, default_tools, default_subagents

    agent = Agent(AgentOptions(
        api_key="sk-...",
        base_url="https://api.openai.com/v1",
        model="gpt-4o-mini",
        system_prompt="你是一个桌面助手。",
        tools=default_tools(cwd="."),
        subagents=default_subagents(cwd="."),
        permission_mode="default",
        can_use_tool=lambda tool, args: PermissionResult(allow=True),
        stream=True,
        on_event=lambda ev: print(ev.type, ev.text),
    ))

    for event in agent.run("现在几点？"):
        if event.type == "tool_use":
            print("调用工具:", event.tool_name, event.tool_input)
        elif event.type == "final":
            print("回答:", event.text)
"""

from __future__ import annotations

import html
import json
import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Iterable, Iterator, Optional

__all__ = [
    "Tool",
    "ToolRegistry",
    "ToolResult",
    "ToolUse",
    "HookEvent",
    "PermissionResult",
    "SubagentSpec",
    "SubagentRegistry",
    "AgentOptions",
    "AgentEvent",
    "Agent",
    "AgentError",
    "default_tools",
    "default_subagents",
    "SAFE_TOOLS",
    "EDIT_TOOLS",
]


# --------------------------------------------------------------------------- #
# 数据结构
# --------------------------------------------------------------------------- #
@dataclass
class ToolResult:
    """工具执行结果。``is_error=True`` 时模型会据此调整策略。"""
    content: str = ""
    is_error: bool = False


@dataclass
class ToolUse:
    """一次工具调用请求（模型返回）。"""
    id: str
    name: str
    input: dict


@dataclass
class HookEvent:
    """传给钩子函数的事件对象。"""
    name: str                                   # UserPromptSubmit / PreToolUse / PostToolUse / Stop
    prompt: str = ""
    tool_name: str = ""
    tool_input: dict = field(default_factory=dict)
    tool_result: str = ""
    turn: int = 0


@dataclass
class PermissionResult:
    """工具授权结果。``updated_input`` 可替换模型给出的参数。"""
    allow: bool = True
    message: str = ""
    updated_input: Optional[dict] = None


@dataclass
class AgentEvent:
    """agent loop 向调用方流式抛出的事件。

    type：
      * ``text_delta``      —— 流式文本增量
      * ``text``            —— 一段完整的中间文本
      * ``tool_use``        —— 模型请求调用工具
      * ``tool_result``     —— 工具执行完成
      * ``subagent_start``  —— 子智能体开始
      * ``subagent_event``  —— 子智能体内部事件（``subagent_event`` 字段为内部类型）
      * ``subagent_end``    —— 子智能体结束
      * ``final``           —— 最终回答（循环结束）
      * ``error``           —— 运行错误
    """
    type: str
    text: str = ""
    tool_name: str = ""
    tool_input: dict = field(default_factory=dict)
    tool_result: str = ""
    is_error: bool = False
    turn: int = 0
    agent_name: str = ""
    depth: int = 0
    subagent_event: str = ""
    usage: dict = field(default_factory=dict)


class AgentError(Exception):
    """智能体运行期错误。"""


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #
class Tool:
    """一个可被模型调用的工具。

    handler 接收一个 dict（模型生成的参数），返回 ``ToolResult`` / ``str`` / ``dict``。
    schema 遵循 JSON Schema；Anthropic 与 OpenAI 两套协议都使用同一份定义。
    """

    def __init__(self, name: str, description: str, input_schema: dict,
                 handler: Callable[[dict], Any]):
        self.name = name
        self.description = description
        self.input_schema = input_schema or {"type": "object", "properties": {}}
        self.handler = handler

    def call(self, payload: dict) -> ToolResult:
        try:
            out = self.handler(payload or {})
        except Exception as exc:                                    # noqa: BLE001
            return ToolResult(f"{type(exc).__name__}: {exc}", is_error=True)
        if isinstance(out, ToolResult):
            return out
        if isinstance(out, (dict, list)):
            return ToolResult(json.dumps(out, ensure_ascii=False, indent=2))
        return ToolResult("" if out is None else str(out))

    def openai_schema(self) -> dict:
        return {"type": "function",
                "function": {"name": self.name, "description": self.description,
                             "parameters": self.input_schema}}


class ToolRegistry:
    """工具注册表。"""

    def __init__(self, tools: Iterable[Tool] = ()):
        self._tools: dict[str, Tool] = {}
        for t in tools:
            self.register(t)

    def register(self, tool: Tool) -> Tool:
        self._tools[tool.name] = tool
        return tool

    def get(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __iter__(self) -> Iterator[Tool]:
        return iter(self._tools.values())

    def names(self) -> list[str]:
        return list(self._tools)

    def definitions(self) -> list[dict]:
        return [t.openai_schema() for t in self._tools.values()]


# --------------------------------------------------------------------------- #
# 子智能体
# --------------------------------------------------------------------------- #
@dataclass
class SubagentSpec:
    """一个可被主智能体委派的子智能体定义。

    ``tools`` 为 None 时继承主智能体的工具集；``allowed_tools`` 可再按名称过滤。
    ``model`` / ``temperature`` / ``max_tokens`` / ``permission_mode`` 为 None 时
    继承主智能体。``task`` 工具永远不会下发给子智能体（除非显式放进 tools），
    因此子智能体不会无条件递归。
    """

    name: str
    description: str
    system_prompt: str = ""
    tools: Optional[Iterable[Tool]] = field(default=None, repr=False)
    allowed_tools: Optional[Iterable[str]] = None
    model: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    max_turns: int = 6
    permission_mode: Optional[str] = None
    enabled: bool = True

    def __post_init__(self):
        self.name = (self.name or "").strip()
        if not self.name:
            raise ValueError("SubagentSpec.name 不能为空")
        if self.tools is not None:
            self.tools = list(self.tools)
        if self.allowed_tools is not None:
            self.allowed_tools = [str(x) for x in self.allowed_tools]
        self.max_turns = max(1, int(self.max_turns or 1))

    def tool_names(self) -> list[str]:
        if self.allowed_tools is not None:
            return list(self.allowed_tools)
        if self.tools is not None:
            return [t.name for t in self.tools]
        return []


class SubagentRegistry:
    """子智能体注册表。同名注册会覆盖，方便配置覆盖内置定义。"""

    def __init__(self, specs: Iterable[SubagentSpec] = ()):
        self._specs: dict[str, SubagentSpec] = {}
        for spec in specs:
            self.register(spec)

    def register(self, spec: SubagentSpec) -> SubagentSpec:
        if not isinstance(spec, SubagentSpec):
            raise TypeError("只能注册 SubagentSpec")
        self._specs[spec.name] = spec
        return spec

    def get(self, name: str) -> Optional[SubagentSpec]:
        return self._specs.get(name)

    def __contains__(self, name: str) -> bool:
        return name in self._specs

    def __iter__(self) -> Iterator[SubagentSpec]:
        return iter(self._specs.values())

    def __len__(self) -> int:
        return len(self._specs)

    def names(self) -> list[str]:
        return list(self._specs)

    def enabled(self) -> list[SubagentSpec]:
        return [s for s in self._specs.values() if s.enabled]

    def descriptions(self) -> str:
        rows = []
        for spec in self.enabled():
            desc = (spec.description or "").strip()
            rows.append(f"- {spec.name}: {desc}" if desc else f"- {spec.name}")
        return "\n".join(rows)


# --------------------------------------------------------------------------- #
# 配置
# --------------------------------------------------------------------------- #
class AgentOptions:
    """智能体运行参数（对应 Claude Agent SDK 的 ClaudeAgentOptions）。"""

    def __init__(self,
                 system_prompt: str = "你是一个乐于助人的智能体。",
                 model: str = "gpt-4o-mini",
                 api_key: str = "",
                 base_url: str = "https://api.openai.com/v1",
                 temperature: float = 0.7,
                 max_tokens: int = 2048,
                 max_turns: int = 8,
                 tools: Iterable[Tool] = (),
                 permission_mode: str = "default",
                 can_use_tool: Optional[Callable[[str, dict], PermissionResult]] = None,
                 hooks: Optional[dict[str, list[Callable[[HookEvent], Any]]]] = None,
                 cwd: str = ".",
                 timeout: int = 60,
                 extra_headers: Optional[dict] = None,
                 *,
                 subagents: Iterable[SubagentSpec] = (),
                 max_subagent_depth: int = 2,
                 subagent_depth: int = 0,
                 stop_event: Optional[threading.Event] = None,
                 stream: bool = False,
                 on_event: Optional[Callable[[AgentEvent], Any]] = None,
                 max_history_messages: int = 80,
                 ask_user: Optional[Callable[[str, list[str]], str]] = None):
        self.system_prompt = system_prompt
        self.model = model
        self.api_key = api_key
        self.base_url = (base_url or "https://api.openai.com/v1").rstrip("/")
        self.temperature = max(0.0, min(2.0, float(temperature)))
        self.max_tokens = max(64, int(max_tokens or 2048))
        self.max_turns = max(1, int(max_turns))
        self.tools = list(tools)
        self.permission_mode = permission_mode
        self.can_use_tool = can_use_tool
        self.hooks = hooks or {}
        self.cwd = os.path.abspath(cwd or ".")
        self.timeout = max(5, int(timeout or 60))
        self.extra_headers = extra_headers or {}
        self.subagents = list(subagents)
        self.max_subagent_depth = max(0, int(max_subagent_depth))
        self.subagent_depth = max(0, int(subagent_depth))
        self.stop_event = stop_event or threading.Event()
        self.stream = bool(stream)
        self.on_event = on_event
        self.max_history_messages = max(0, int(max_history_messages))
        self.ask_user = ask_user


# 默认权限模式下无需询问即可使用的只读工具。
# task 只负责委派，子智能体真正使用的工具仍会逐个走权限校验。
SAFE_TOOLS = {"get_current_time", "read_file", "list_directory", "web_fetch",
              "task", "ask_user", "screen_info", "list_windows",
              "workspace_info", "workspace_list", "wait",
              "database_stats", "database_search", "database_list",
              "database_inspect", "open_report", "rag_search", "rag_stats",
              "image_describe"}
# 仅在 acceptEdits / bypassPermissions 下放行的写操作
EDIT_TOOLS = {"write_file", "edit_file"}


# --------------------------------------------------------------------------- #
# Agent
# --------------------------------------------------------------------------- #
class Agent:
    """维护会话历史，驱动「模型 → 工具 → 模型」循环。"""

    def __init__(self, options: AgentOptions):
        self.options = options
        self.depth = int(options.subagent_depth)
        extra = [t for t in options.tools if t.name not in SAFE_TOOLS]
        base = [t for t in options.tools if t.name in SAFE_TOOLS]
        self.registry = ToolRegistry(base + extra)
        self.subagents = SubagentRegistry(options.subagents)
        self.history: list[dict] = []
        self._pending: list[AgentEvent] = []
        self._active_response = None
        self._children: list[Agent] = []
        if options.ask_user is not None and "ask_user" not in self.registry:
            self.registry.register(self._build_ask_user_tool())
        if (self.subagents.enabled()
                and self.depth < self.options.max_subagent_depth
                and "task" not in self.registry):
            self.registry.register(self._build_task_tool())

    # ---- 会话 ---------------------------------------------------------- #
    @property
    def can_use_tools(self) -> bool:
        return bool(self.options.api_key) and bool(self.options.tools)

    @property
    def stop_event(self) -> threading.Event:
        return self.options.stop_event

    @property
    def stopped(self) -> bool:
        return self.options.stop_event.is_set()

    def reset(self):
        self.history.clear()
        self._pending.clear()
        self.options.stop_event.clear()
        self._close_active()

    def _close_active(self):
        """关闭当前 HTTP 响应，让阻塞中的 iter_lines / read 立刻返回。"""
        resp = self._active_response
        if resp is not None:
            try:
                resp.close()
            except Exception:                                   # noqa: BLE001
                pass

    def stop(self):
        """请求停止当前对话（流式响应与后续工具调用都会尽快结束）。"""
        self.options.stop_event.set()
        self._close_active()
        for child in list(self._children):
            child.stop()

    # ---- 事件 ---------------------------------------------------------- #
    def _publish(self, event: AgentEvent) -> AgentEvent:
        """实时通知外部观察者，并把事件原样返回以便 yield。"""
        cb = self.options.on_event
        if cb is not None:
            try:
                cb(event)
            except Exception:                                   # noqa: BLE001
                pass
        return event

    def _drain_pending(self) -> list[AgentEvent]:
        """取出子智能体在工具执行期间上报的事件（已经 publish 过）。"""
        if not self._pending:
            return []
        pending, self._pending = self._pending, []
        return pending

    # ---- 主循环 -------------------------------------------------------- #
    def run(self, prompt: str) -> Iterator[AgentEvent]:
        """执行一次完整的智能体对话，流式产出事件。"""
        if not self.options.api_key:
            yield self._publish(AgentEvent("error", text="未配置 api_key，无法调用远程模型。",
                                           depth=self.depth))
            return

        prompt = str(prompt or "").strip()
        if not prompt:
            yield self._publish(AgentEvent("error", text="消息不能为空。", depth=self.depth))
            return

        self._emit_hook(HookEvent(name="UserPromptSubmit", prompt=prompt))
        self.history.append({"role": "user", "content": prompt})

        final_text = ""
        stopped = False
        for turn in range(1, self.options.max_turns + 1):
            if self.stopped:
                stopped = True
                break

            msg = None
            try:
                if self.options.stream:
                    for kind, payload in self._chat_events():
                        if kind == "delta":
                            yield self._publish(AgentEvent("text_delta", text=payload,
                                                           turn=turn, depth=self.depth))
                        else:
                            msg = payload
                else:
                    msg = self._chat()
            except Exception as exc:                            # noqa: BLE001
                if self.stopped:
                    stopped = True
                    break
                err = f"模型调用失败：{exc}"
                yield self._publish(AgentEvent("error", text=err, turn=turn, depth=self.depth))
                final_text = final_text or err
                break

            if msg is None:
                err = "模型没有返回内容。"
                yield self._publish(AgentEvent("error", text=err, turn=turn, depth=self.depth))
                final_text = final_text or err
                break

            content = _as_text(msg.get("content")).strip()
            tool_calls = msg.get("tool_calls") or []

            if content and not tool_calls and not self.options.stream:
                yield self._publish(AgentEvent("text", text=content, turn=turn, depth=self.depth))

            if self.stopped:
                stopped = True
                final_text = content
                break

            if not tool_calls:
                final_text = content
                break

            self.history.append({"role": "assistant", "content": content,
                                 "tool_calls": tool_calls})
            for tc in tool_calls:
                fn = tc.get("function", {}) or {}
                name = fn.get("name", "")
                tool_input = self._parse_args(fn.get("arguments"))
                if self.stopped:
                    self.history.append({"role": "tool", "tool_call_id": tc.get("id", ""),
                                         "content": "（已停止，未执行）"})
                    continue
                yield self._publish(AgentEvent("tool_use", tool_name=name,
                                               tool_input=tool_input, turn=turn,
                                               depth=self.depth))

                result = self._execute(name, tool_input, turn)

                for event in self._drain_pending():
                    yield event

                yield self._publish(AgentEvent("tool_result", tool_name=name,
                                               tool_input=tool_input,
                                               tool_result=result.content,
                                               is_error=result.is_error,
                                               turn=turn, depth=self.depth))
                self.history.append({"role": "tool", "tool_call_id": tc.get("id", ""),
                                     "content": result.content})
            if self.stopped:
                stopped = True
                break
        else:
            final_text = final_text or "（已达到最大工具调用轮数，先停在这里。）"

        if stopped and not final_text:
            final_text = "（已停止）"

        self._emit_hook(HookEvent(name="Stop", tool_result=final_text))
        if self.depth == 0:
            # 一次对话结束后自动解除停止标记，下一次提问不会被误伤。
            self.options.stop_event.clear()
        yield self._publish(AgentEvent("final", text=final_text, depth=self.depth))

    # ---- 请求 ---------------------------------------------------------- #
    def _history_for_request(self) -> list[dict]:
        """控制上下文长度；截断时从一条 user 消息开始，避免孤立的 tool 消息。"""
        limit = self.options.max_history_messages
        if limit <= 0 or len(self.history) <= limit:
            return list(self.history)
        tail = self.history[-limit:]
        for index, message in enumerate(tail):
            if message.get("role") == "user":
                return tail[index:]
        return tail

    def _payload(self, stream: bool) -> dict:
        messages = ([{"role": "system", "content": self.options.system_prompt}]
                    + self._history_for_request())
        payload: dict[str, Any] = {
            "model": self.options.model,
            "messages": messages,
            "temperature": self.options.temperature,
            "max_tokens": self.options.max_tokens,
        }
        if self.registry.names():
            payload["tools"] = self.registry.definitions()
            payload["tool_choice"] = "auto"
        if stream:
            payload["stream"] = True
        return payload

    def _headers(self) -> dict:
        headers = {"Authorization": f"Bearer {self.options.api_key}",
                   "Content-Type": "application/json"}
        headers.update(self.options.extra_headers or {})
        return headers

    def _chat(self) -> dict:
        import requests

        resp = requests.post(
            f"{self.options.base_url}/chat/completions",
            headers=self._headers(), json=self._payload(False),
            timeout=(min(15, self.options.timeout), self.options.timeout))
        self._active_response = resp
        try:
            try:
                data = resp.json()
            except ValueError as exc:
                raise AgentError(f"HTTP {resp.status_code}: {resp.text[:300]}") from exc
            if resp.status_code >= 400:
                raise AgentError(_api_error(data, resp.status_code))
            if isinstance(data, dict) and data.get("error"):
                raise AgentError(str(data["error"].get("message", data["error"]))
                                 if isinstance(data["error"], dict) else str(data["error"]))
            choices = (data or {}).get("choices") or []
            if not choices:
                raise AgentError(f"响应中没有 choices：{str(data)[:300]}")
            return choices[0].get("message", {}) or {}
        finally:
            self._active_response = None

    def _chat_events(self) -> Iterator[tuple[str, Any]]:
        """流式请求：产出 ("delta", 文本) 与最后一条 ("message", 完整消息)。"""
        import requests

        resp = requests.post(
            f"{self.options.base_url}/chat/completions",
            headers=self._headers(), json=self._payload(True),
            timeout=(min(15, self.options.timeout),
                     max(15, self.options.timeout)),
            stream=True)
        self._active_response = resp
        try:
            with resp:
                yield from self._chat_events_from(resp)
        finally:
            self._active_response = None

    def _chat_events_from(self, resp) -> Iterator[tuple[str, Any]]:
        ctype = (resp.headers.get("Content-Type") or "").lower()
        if resp.status_code >= 400:
            try:
                data = resp.json()
                detail = _api_error(data, resp.status_code)
            except ValueError:
                detail = f"HTTP {resp.status_code}: {resp.text[:300]}"
            raise AgentError(detail)

        # 某些兼容接口会忽略 stream 参数，直接返回 JSON。
        if "event-stream" not in ctype:
            try:
                data = resp.json()
            except ValueError as exc:
                raise AgentError(f"无法解析响应（{ctype or '未知类型'}）") from exc
            if data.get("error"):
                raise AgentError(_api_error(data, resp.status_code))
            choices = (data or {}).get("choices") or []
            if not choices:
                raise AgentError(f"响应中没有 choices：{str(data)[:300]}")
            yield "message", choices[0].get("message", {}) or {}
            return

        content_parts: list[str] = []
        calls: dict[int, dict] = {}
        for raw in resp.iter_lines(decode_unicode=False):
            if self.stopped:
                break
            if not raw:
                continue
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except ValueError:
                continue
            if isinstance(chunk, dict) and chunk.get("error"):
                raise AgentError(_api_error(chunk, resp.status_code))
            choices = (chunk or {}).get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            piece = _as_text(delta.get("content"))
            if piece:
                content_parts.append(piece)
                yield "delta", piece
            for tc in delta.get("tool_calls") or []:
                _merge_tool_delta(calls, tc)

        yield "message", {"content": "".join(content_parts),
                          "tool_calls": _assembled_tool_calls(calls)}

    @staticmethod
    def _parse_args(raw: Any) -> dict:
        if isinstance(raw, dict):
            return raw
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {"value": parsed}
        except (ValueError, TypeError):
            return {"_raw": str(raw)}

    # ---- 工具执行 ------------------------------------------------------ #
    def _execute(self, name: str, tool_input: dict, turn: int) -> ToolResult:
        tool = self.registry.get(name)
        if tool is None:
            return ToolResult(f"未知工具：{name}", is_error=True)

        # 1) PreToolUse 钩子
        for hook in self.options.hooks.get("PreToolUse", []):
            decision = self._call_hook(hook, HookEvent(name="PreToolUse", tool_name=name,
                                                       tool_input=tool_input, turn=turn))
            if decision is not None and not getattr(decision, "allow", True):
                return ToolResult(getattr(decision, "message", "") or "已被钩子阻止。",
                                  is_error=True)
            if decision is not None and getattr(decision, "updated_input", None):
                tool_input = decision.updated_input

        # 2) 权限校验
        allowed, updated = self._check_permission(name, tool_input)
        if updated is not None:
            tool_input = updated
        if not allowed:
            return ToolResult(
                f"工具「{name}」未获授权。如需执行，请在设置中调整权限模式或授权该工具。",
                is_error=True)

        # 3) 执行
        result = tool.call(tool_input)

        # 4) PostToolUse 钩子
        for hook in self.options.hooks.get("PostToolUse", []):
            self._call_hook(hook, HookEvent(name="PostToolUse", tool_name=name,
                                            tool_input=tool_input, tool_result=result.content,
                                            turn=turn))
        return result

    def _check_permission(self, name: str, tool_input: dict):
        if self.options.can_use_tool is not None:
            res = self.options.can_use_tool(name, tool_input)
            if isinstance(res, PermissionResult):
                return res.allow, res.updated_input
            return bool(res), None
        mode = self.options.permission_mode
        if name == "ask_user":
            return True, None
        if mode == "bypassPermissions":
            return True, None
        if mode == "plan":
            return False, None
        if name in SAFE_TOOLS:
            return True, None
        if mode == "acceptEdits" and name in EDIT_TOOLS:
            return True, None
        return False, None

    @staticmethod
    def _call_hook(hook: Callable[[HookEvent], Any], event: HookEvent):
        try:
            return hook(event)
        except Exception:                                       # noqa: BLE001
            return None

    def _emit_hook(self, event: HookEvent):
        for hook in self.options.hooks.get(event.name, []):
            self._call_hook(hook, event)

    # ---- 让用户做选择 -------------------------------------------------- #
    def _build_ask_user_tool(self) -> Tool:
        schema = {
            "type": "object",
            "properties": {
                "question": {"type": "string",
                             "description": "要问用户的问题，简洁明确"},
                "options": {"type": "array", "items": {"type": "string"},
                            "minItems": 2, "maxItems": 6,
                            "description": "2-6 个简短选项，每个不超过 20 字"},
            },
            "required": ["question", "options"],
        }
        return Tool(
            "ask_user",
            "当用户意图不明确、或存在多个合理方向需要用户拍板时，"
            "向用户展示 2-6 个按钮选项并等待点击。简单问答、寒暄或只有一个合理答案时不要调用。",
            schema, self._ask_user)

    def _ask_user(self, args: dict) -> ToolResult:
        question = str(args.get("question") or "").strip()
        raw = args.get("options")
        if not question:
            return ToolResult("缺少 question。", is_error=True)
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except ValueError:
                pass
        if not isinstance(raw, list):
            return ToolResult("options 必须是字符串数组。", is_error=True)
        options = []
        for item in raw:
            text = str(item or "").strip()
            if text and text not in options:
                options.append(text[:40])
        if len(options) < 2:
            return ToolResult("至少需要 2 个不同的选项。", is_error=True)
        if len(options) > 6:
            options = options[:6]
        if self.options.ask_user is None:
            return ToolResult("当前环境不支持选择按钮。", is_error=True)
        try:
            answer = self.options.ask_user(question, options)
        except Exception as exc:                                # noqa: BLE001
            return ToolResult(f"等待用户选择失败：{exc}", is_error=True)
        answer = str(answer or "").strip()
        if not answer:
            return ToolResult("用户没有做出选择。")
        return ToolResult(f"用户选择：{answer}")

    # ---- 子智能体 ------------------------------------------------------ #
    def _build_task_tool(self) -> Tool:
        specs = self.subagents.enabled()
        names = [s.name for s in specs]
        listing = self.subagents.descriptions()
        description = (
            "把一个独立、可并行的子任务委派给专门的子智能体。"
            "子智能体有独立的上下文与工具集，完成后只把结论返回给你。"
            "主任务较复杂、需要检索资料或修改代码时优先使用；简单问答不必使用。\n"
            f"可用子智能体：\n{listing}")
        schema = {
            "type": "object",
            "properties": {
                "agent": {"type": "string", "enum": names,
                          "description": "子智能体名称"},
                "prompt": {"type": "string",
                           "description": "要子智能体完成的具体任务，尽量包含目标与验收标准"},
                "context": {"type": "string",
                            "description": "可选：完成任务所需的背景、文件路径或已知信息"},
            },
            "required": ["agent", "prompt"],
        }
        return Tool("task", description, schema, self._run_subagent)

    def _run_subagent(self, args: dict) -> ToolResult:
        name = str(args.get("agent") or "").strip()
        prompt = str(args.get("prompt") or "").strip()
        context = str(args.get("context") or "").strip()

        if not name:
            return ToolResult("缺少 agent 参数。", is_error=True)
        if not prompt:
            return ToolResult("缺少 prompt 参数。", is_error=True)
        spec = self.subagents.get(name)
        if spec is None or not spec.enabled:
            available = "、".join(self.subagents.names()) or "（无）"
            return ToolResult(f"未知子智能体：{name}。可用：{available}", is_error=True)
        if self.depth + 1 > self.options.max_subagent_depth:
            return ToolResult(
                f"已达到子智能体嵌套上限（{self.options.max_subagent_depth} 层）。",
                is_error=True)

        depth = self.depth + 1
        user_prompt = prompt
        if context:
            user_prompt += f"\n\n[补充上下文]\n{context}"

        start_event = self._publish(AgentEvent("subagent_start", agent_name=name,
                                               text=prompt, depth=depth))
        self._pending.append(start_event)
        trace: list[str] = []
        final = ""
        errors: list[str] = []

        def forward(event: AgentEvent):
            if event.type in ("tool_use", "tool_name") and event.tool_name:
                trace.append(event.tool_name)
            wrapped = AgentEvent(
                "subagent_event", text=event.text, tool_name=event.tool_name,
                tool_input=dict(event.tool_input or {}), tool_result=event.tool_result,
                is_error=event.is_error, turn=event.turn, agent_name=name,
                depth=depth, subagent_event=event.type)
            self._publish(wrapped)
            self._pending.append(wrapped)

        child = Agent(self._child_options(spec, forward))
        self._children.append(child)
        try:
            for event in child.run(user_prompt):
                if event.type == "final":
                    final = event.text
                elif event.type == "error":
                    errors.append(event.text)
        except Exception as exc:                                # noqa: BLE001
            errors.append(f"{type(exc).__name__}: {exc}")
        finally:
            if child in self._children:
                self._children.remove(child)

        failed = bool(errors) or self.stopped
        body = final.strip()
        if not body and errors:
            body = errors[-1]
        if not body:
            body = "（子智能体没有返回内容）"
        used = list(dict.fromkeys(trace))
        if used:
            body += "\n\n[使用工具] " + "、".join(used)
        if failed and final:
            body = f"{body}\n\n[注意] 子智能体未正常完成：{errors[-1] if errors else '已停止'}"

        self._publish(AgentEvent("subagent_end", agent_name=name, text="",
                                 tool_result=body, is_error=failed, depth=depth))
        return ToolResult(f"子智能体「{name}」的结论：\n{body}",
                          is_error=failed and not final)

    def _child_options(self, spec: SubagentSpec,
                       forward: Callable[[AgentEvent], Any]) -> AgentOptions:
        tools = self._tools_for_spec(spec)
        depth = self.depth + 1
        subagents: list[SubagentSpec] = []
        if depth < self.options.max_subagent_depth:
            subagents = self.subagents.enabled()
        return AgentOptions(
            system_prompt=self._child_system_prompt(spec),
            model=spec.model or self.options.model,
            api_key=self.options.api_key,
            base_url=self.options.base_url,
            temperature=(self.options.temperature if spec.temperature is None
                         else spec.temperature),
            max_tokens=spec.max_tokens or self.options.max_tokens,
            max_turns=spec.max_turns,
            tools=tools,
            permission_mode=spec.permission_mode or self.options.permission_mode,
            can_use_tool=self.options.can_use_tool,
            hooks=self.options.hooks,
            cwd=self.options.cwd,
            timeout=self.options.timeout,
            extra_headers=self.options.extra_headers,
            subagents=subagents,
            max_subagent_depth=self.options.max_subagent_depth,
            subagent_depth=depth,
            stop_event=self.options.stop_event,
            stream=False,
            on_event=forward,
            ask_user=self.options.ask_user,
        )

    def _tools_for_spec(self, spec: SubagentSpec) -> list[Tool]:
        base = list(spec.tools) if spec.tools is not None else list(self.options.tools)
        allowed = set(spec.allowed_tools) if spec.allowed_tools is not None else None
        tools = []
        for tool in base:
            if tool.name == "task":
                continue
            if allowed is not None and tool.name not in allowed:
                continue
            tools.append(tool)
        return tools

    @staticmethod
    def _child_system_prompt(spec: SubagentSpec) -> str:
        base = (spec.system_prompt or "").strip()
        if not base:
            base = f"你是子智能体「{spec.name}」，负责独立完成主智能体交办的任务。"
        return (f"{base}\n\n"
                "工作要求：专注完成分配给你的任务；不要向用户提问；"
                "完成后用简体中文给出简洁、可核验的结论，必要时列出来源或文件。"
                f"\n当前时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


def _as_text(value: Any) -> str:
    """兼容 content 为字符串或 [{type:text, text:...}] 列表的返回。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or ""))
        return "".join(parts)
    return str(value)


def _api_error(data: Any, status: int) -> str:
    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, dict):
            detail = err.get("message") or err.get("code") or json.dumps(err, ensure_ascii=False)
            return f"HTTP {status}: {detail}"
        if err:
            return f"HTTP {status}: {err}"
        if data.get("message"):
            return f"HTTP {status}: {data['message']}"
    return f"HTTP {status}: {str(data)[:300]}"


def _merge_tool_delta(calls: dict[int, dict], delta: dict):
    try:
        index = int(delta.get("index", 0))
    except (TypeError, ValueError):
        index = 0
    slot = calls.setdefault(index, {"id": "", "type": "function",
                                    "function": {"name": "", "arguments": ""}})
    if delta.get("id"):
        slot["id"] = delta["id"]
    if delta.get("type"):
        slot["type"] = delta["type"]
    fn = delta.get("function") or {}
    if fn.get("name"):
        name = fn["name"]
        current = slot["function"]["name"]
        if not current:
            slot["function"]["name"] = name
        elif not current.endswith(name):
            slot["function"]["name"] += name
    if fn.get("arguments"):
        slot["function"]["arguments"] += fn["arguments"]


def _assembled_tool_calls(calls: dict[int, dict]) -> list[dict]:
    out = []
    for index in sorted(calls):
        call = calls[index]
        if not call.get("id"):
            call["id"] = f"call_{index}"
        if call["function"].get("name"):
            out.append(call)
    return out


# --------------------------------------------------------------------------- #
# 内置工具（参考 Claude Agent SDK 的 Read / Write / Edit / Bash / WebFetch…）
# --------------------------------------------------------------------------- #
def _resolve_path(cwd: str, path: str) -> str:
    p = path or "."
    if not os.path.isabs(p):
        p = os.path.join(cwd, p)
    resolved = os.path.realpath(p)
    root = os.path.realpath(cwd)
    if resolved != root and not resolved.startswith(root + os.sep):
        raise PermissionError(f"路径超出工作目录：{path}")
    return resolved


def _read_file(cwd: str):
    def handler(args: dict) -> ToolResult:
        path = _resolve_path(cwd, args.get("path", ""))
        if not os.path.isfile(path):
            return ToolResult(f"文件不存在：{args.get('path')}", is_error=True)
        limit = int(args.get("max_bytes", 20000) or 20000)
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            data = f.read(limit)
        note = "\n…（内容已截断）" if len(data) >= limit else ""
        return ToolResult(data + note)
    return handler


def _write_file(cwd: str):
    def handler(args: dict) -> ToolResult:
        path = _resolve_path(cwd, args.get("path", ""))
        content = args.get("content", "")
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return ToolResult(f"已写入 {os.path.relpath(path, cwd)}（{len(content)} 字符）")
    return handler


def _edit_file(cwd: str):
    def handler(args: dict) -> ToolResult:
        path = _resolve_path(cwd, args.get("path", ""))
        old = args.get("old", "")
        new = args.get("new", "")
        if not os.path.isfile(path):
            return ToolResult(f"文件不存在：{args.get('path')}", is_error=True)
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
        if old and text.count(old) != 1:
            return ToolResult(f"匹配到 {text.count(old)} 处，需恰好 1 处。", is_error=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text.replace(old, new, 1))
        return ToolResult(f"已修改 {os.path.relpath(path, cwd)}")
    return handler


def _list_directory(cwd: str):
    def handler(args: dict) -> ToolResult:
        path = _resolve_path(cwd, args.get("path", "."))
        if not os.path.isdir(path):
            return ToolResult(f"目录不存在：{args.get('path')}", is_error=True)
        rows = []
        for entry in sorted(os.listdir(path)):
            full = os.path.join(path, entry)
            rows.append(f"{'DIR ' if os.path.isdir(full) else '    '}{entry}")
        return ToolResult("\n".join(rows) or "（空目录）")
    return handler


def _git_bash_path() -> Optional[str]:
    """返回 Git Bash 路径；没有安装 Git for Windows 时返回 None。"""
    if os.name != "nt":
        return shutil.which("bash")
    candidates = [
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
        r"C:\Program Files (x86)\Git\bin\bash.exe",
        r"C:\Program Files (x86)\Git\usr\bin\bash.exe",
    ]
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    found = shutil.which("bash.exe") or shutil.which("bash")
    if found and "git" in found.lower():
        return found
    return None


def _run_shell(cwd: str, stop_event=None):
    no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0

    def stop_process(proc):
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                               capture_output=True, creationflags=no_window)
                if proc.poll() is None:
                    proc.kill()
            else:
                proc.terminate()
                try:
                    proc.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    proc.kill()
        except Exception:                                       # noqa: BLE001
            try:
                proc.kill()
            except Exception:                                   # noqa: BLE001
                pass

    def handler(args: dict) -> ToolResult:
        command = args.get("command", "")
        if not command:
            return ToolResult("缺少 command。", is_error=True)
        if stop_event is not None and stop_event.is_set():
            return ToolResult("已停止。", is_error=True)
        timeout = max(1, int(args.get("timeout", 30) or 30))
        mode = str(args.get("shell") or "auto").strip().lower()
        if mode not in ("auto", "bash", "cmd", "powershell"):
            mode = "auto"
        bash = _git_bash_path()
        if mode == "auto":
            mode = "bash" if bash else "cmd"
        if mode == "bash" and not bash:
            return ToolResult(
                "未找到 Git Bash。请安装 Git for Windows，或把 shell 改为 cmd。",
                is_error=True)
        if mode == "bash":
            command_line = [bash, "-lc", command]
            use_shell = False
        elif mode == "powershell":
            command_line = ["powershell.exe", "-NoProfile", "-NonInteractive",
                            "-Command", command]
            use_shell = False
        else:
            command_line = command
            use_shell = True
        try:
            proc = subprocess.Popen(command_line, shell=use_shell, cwd=cwd,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True, errors="replace",
                                    creationflags=no_window)
        except OSError as exc:
            return ToolResult(f"无法启动命令（{mode}）：{exc}", is_error=True)

        deadline = time.monotonic() + timeout
        timed_out = stopped = False
        out, err = "", ""
        while True:
            try:
                out, err = proc.communicate(timeout=0.12)
                break
            except subprocess.TimeoutExpired:
                if stop_event is not None and stop_event.is_set():
                    stopped = True
                    stop_process(proc)
                elif time.monotonic() >= deadline:
                    timed_out = True
                    stop_process(proc)
                else:
                    continue
                try:
                    out, err = proc.communicate(timeout=2)
                except subprocess.TimeoutExpired:
                    out, err = "", ""
                break

        if stopped:
            return ToolResult("已停止。", is_error=True)
        if timed_out:
            return ToolResult(f"命令超时（{timeout}s，shell={mode}）。", is_error=True)
        nl = chr(10)
        text = (out or "") + (((nl + "[stderr]" + nl) + err) if err else "")
        text = text.strip() or f"（退出码 {proc.returncode}，无输出）"
        if len(text) > 8000:
            text = text[:8000] + nl + "…（输出已截断）"
        return ToolResult(text, is_error=proc.returncode != 0)
    return handler


_TAG_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_HTML_RE = re.compile(r"<[^>]+>")


def _get_current_time(_cwd: str):
    def handler(args: dict) -> ToolResult:
        now = datetime.now()
        return ToolResult(now.strftime("%Y-%m-%d %H:%M:%S %A"))
    return handler


def _web_fetch(_cwd: str, stop_event=None):
    def handler(args: dict) -> ToolResult:
        import requests
        url = args.get("url", "")
        if not url:
            return ToolResult("缺少 url。", is_error=True)
        if stop_event is not None and stop_event.is_set():
            return ToolResult("已停止。", is_error=True)
        with requests.get(url, timeout=(10, 20), stream=True,
                          headers={"User-Agent": "Mozilla/5.0"}) as resp:
            resp.raise_for_status()
            chunks = []
            total = 0
            for chunk in resp.iter_content(16384):
                if stop_event is not None and stop_event.is_set():
                    return ToolResult("已停止。", is_error=True)
                if not chunk:
                    continue
                chunks.append(chunk)
                total += len(chunk)
                if total >= 4 * 1024 * 1024:
                    break
            raw = b"".join(chunks)
            encoding = resp.encoding or "utf-8"
            ctype = resp.headers.get("Content-Type", "")
        text = raw.decode(encoding, errors="replace")
        if "html" in ctype.lower():
            text = _TAG_RE.sub(" ", text)
            text = _HTML_RE.sub(" ", text)
            text = html.unescape(text)
            text = re.sub(r"[ \t\r\f\v]+", " ", text)
            text = re.sub(r"\n\s*\n+", "\n\n", text)
        text = text.strip()
        limit = int(args.get("max_chars", 6000) or 6000)
        if len(text) > limit:
            text = text[:limit] + "\n…（内容已截断）"
        return ToolResult(text or "（无文本内容）")
    return handler


def _open_url(_cwd: str):
    def handler(args: dict) -> ToolResult:
        import webbrowser
        url = args.get("url", "")
        if not url:
            return ToolResult("缺少 url。", is_error=True)
        ok = webbrowser.open(url)
        return ToolResult(f"已在浏览器打开：{url}" if ok else f"无法打开：{url}",
                          is_error=not ok)
    return handler


def default_tools(cwd: str = ".", stop_event=None) -> list[Tool]:
    """返回一组内置工具（文件 / 目录 / Shell / 网页 / 时间）。

    ``stop_event`` 用于让耗时命令可以被用户点击停止键立即终止。
    """
    cwd = os.path.abspath(cwd or ".")
    return [
        Tool("get_current_time", "获取当前本地日期与时间。",
             {"type": "object", "properties": {}}, _get_current_time(cwd)),
        Tool("read_file", "读取工作目录内的文本文件内容。",
             {"type": "object", "properties": {
                 "path": {"type": "string", "description": "相对工作目录的文件路径"},
                 "max_bytes": {"type": "integer", "description": "最多读取的字节数，默认 20000"}},
              "required": ["path"]}, _read_file(cwd)),
        Tool("list_directory", "列出工作目录内某个目录的文件。",
             {"type": "object", "properties": {
                 "path": {"type": "string", "description": "相对工作目录的目录路径，默认 ."}}},
             _list_directory(cwd)),
        Tool("write_file", "把内容写入工作目录内的文件（会覆盖）。",
             {"type": "object", "properties": {
                 "path": {"type": "string"},
                 "content": {"type": "string"}},
              "required": ["path", "content"]}, _write_file(cwd)),
        Tool("edit_file", "在文件中把 old 精确替换为 new（old 必须唯一）。",
             {"type": "object", "properties": {
                 "path": {"type": "string"},
                 "old": {"type": "string"},
                 "new": {"type": "string"}},
              "required": ["path", "old", "new"]}, _edit_file(cwd)),
        Tool("run_shell", "在工作目录内执行命令并返回输出。Windows 默认使用 Git Bash"
             "（安装 Git 后支持 ls、grep、find、git 等），也可用 shell=cmd 执行 cmd 命令。",
             {"type": "object", "properties": {
                 "command": {"type": "string"},
                 "timeout": {"type": "integer", "description": "超时秒数，默认 30"},
                 "shell": {"type": "string", "enum": ["auto", "bash", "cmd", "powershell"],
                           "description": "auto 默认优先 Git Bash；需要 Windows 内置命令时用 cmd"}},
              "required": ["command"]}, _run_shell(cwd, stop_event)),
        Tool("web_fetch", "抓取网页并返回其中的纯文本。",
             {"type": "object", "properties": {
                 "url": {"type": "string"},
                 "max_chars": {"type": "integer", "description": "最多返回字符数，默认 6000"}},
              "required": ["url"]}, _web_fetch(cwd, stop_event)),
        Tool("open_url", "在用户默认浏览器中打开一个网址。",
             {"type": "object", "properties": {"url": {"type": "string"}},
              "required": ["url"]}, _open_url(cwd)),
    ]


def default_subagents(cwd: str = ".", stop_event=None) -> list[SubagentSpec]:
    """内置的三个子智能体：检索、编码、写作。

    它们是可选模板：调用方可以按需裁剪工具集，也可以注册同名 Spec 覆盖。
    """
    tools = {t.name: t for t in default_tools(cwd, stop_event=stop_event)}

    def pick(*names: str) -> list[Tool]:
        return [tools[n] for n in names if n in tools]

    return [
        SubagentSpec(
            name="researcher",
            description="联网检索资料、核实事实并提炼要点。适合查询最新信息、文档与网页内容。",
            system_prompt=(
                "你是一名资料检索员。优先抓取权威来源，交叉核对关键事实，"
                "用简洁的中文列出结论与来源链接；不确定的地方明确标注。"),
            tools=pick("web_fetch", "get_current_time", "open_url"),
            max_turns=5,
        ),
        SubagentSpec(
            name="coder",
            description="阅读与修改本地代码、运行命令、定位报错。适合实现功能或排查工程问题。",
            system_prompt=(
                "你是一名谨慎的工程师。先阅读相关文件再修改，保持最小改动；"
                "修改后尽量运行针对性检查，并在结论中说明改了哪些文件与验证结果。"
                "不要删除用户数据，不要执行危险命令。"),
            tools=pick("read_file", "write_file", "edit_file",
                       "list_directory", "run_shell", "get_current_time"),
            max_turns=10,
        ),
        SubagentSpec(
            name="writer",
            description="把零散信息整理成清晰、有结构的中文文案，不访问网络、不修改文件。",
            system_prompt=(
                "你是一名中文编辑。把用户提供的材料整理成结构清楚、语气自然的文案，"
                "保留关键事实，不编造细节；直接给出成稿，不要解释写作过程。"),
            tools=pick("get_current_time"),
            max_turns=3,
        ),
    ]
