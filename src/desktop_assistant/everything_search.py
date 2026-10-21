# -*- coding: utf-8 -*-
"""Everything 1.5 本地文件检索（官方 SDK3 / named pipe）。

Everything 1.5 使用 named pipe 与客户端通信，旧的 WM_COPYDATA 查询不再返回结果。
这里通过官方 ``Everything3_x64.dll`` / ``Everything3_x86.dll`` 连接 Everything，
无需用户安装 SDK，只需把 DLL 放在 ``vendor/everything/``。

支持 Everything 1.5 搜索语法，例如：

* ``报告``                        文件名搜索
* ``content:"Q3复盘"``            文件内容搜索（需在 Everything 中启用内容索引）
* ``ext:pdf 预算``                组合条件
"""

from __future__ import annotations

import ctypes
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

IS_WINDOWS = os.name == "nt"


class EverythingError(RuntimeError):
    pass


@dataclass
class SearchResult:
    path: str
    name: str
    is_dir: bool
    size: int = 0


if IS_WINDOWS:
    from ctypes import wintypes

    WNDENUMPROC = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
    _user32.EnumWindows.restype = wintypes.BOOL
    _user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _user32.GetClassNameW.restype = ctypes.c_int
    _user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]


def _window_classes() -> list[tuple[int, str]]:
    if not IS_WINDOWS:
        return []
    rows: list[tuple[int, str]] = []

    @WNDENUMPROC
    def callback(hwnd, _lparam):
        buffer = ctypes.create_unicode_buffer(256)
        _user32.GetClassNameW(hwnd, buffer, 256)
        rows.append((int(hwnd), buffer.value or ""))
        return True

    _user32.EnumWindows(callback, 0)
    return rows


def _instance_names_from_windows() -> list[str]:
    names = []
    for _hwnd, name in _window_classes():
        match = re.match(r"EVERYTHING_TASKBAR_NOTIFICATION_\((.+)\)$",
                         name, re.IGNORECASE)
        if match:
            names.append(match.group(1))
    return names


def _hide_everything_windows():
    for hwnd, name in _window_classes():
        upper = name.upper()
        if upper.startswith("EVERYTHING_") and not upper.startswith(
                "EVERYTHING_TASKBAR_NOTIFICATION"):
            _user32.ShowWindow(hwnd, 0)


def _find_everything_exe(project_root: str) -> Optional[str]:
    root = Path(project_root)
    candidates = [
        root / "vendor" / "everything" / "Everything.exe",
        root / "tools" / "everything" / "Everything.exe",
    ]
    candidates.extend(root.glob("Everything-*" + os.sep + "Everything.exe"))
    candidates.extend(root.glob("*Everything*" + os.sep + "Everything.exe"))
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
        if base:
            candidate = Path(base) / "Everything" / "Everything.exe"
            if candidate.is_file():
                return str(candidate)
    return None


def _find_sdk_dll(project_root: str, exe_path: Optional[str] = None) -> Optional[str]:
    arch = "x64" if ctypes.sizeof(ctypes.c_void_p) == 8 else "x86"
    name = f"Everything3_{arch}.dll"
    root = Path(project_root)
    candidates = [
        root / "vendor" / "everything" / name,
        root / "tools" / "everything" / name,
        root / "sdk" / name,
        root / "_sdk3rel" / "dll" / name,
    ]
    if exe_path:
        candidates.append(Path(exe_path).parent / name)
    candidates.append(Path(__file__).resolve().parent / "vendor" / "everything" / name)
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


class _EverythingSDK:
    """ctypes 绑定，只声明用得到的函数。"""

    def __init__(self, dll_path: str):
        self.dll = ctypes.WinDLL(dll_path)
        d = self.dll
        d.Everything3_ConnectW.argtypes = [wintypes.LPCWSTR]
        d.Everything3_ConnectW.restype = ctypes.c_void_p
        d.Everything3_DestroyClient.argtypes = [ctypes.c_void_p]
        d.Everything3_DestroyClient.restype = wintypes.BOOL
        d.Everything3_GetLastError.restype = wintypes.DWORD
        d.Everything3_CreateSearchState.argtypes = []
        d.Everything3_CreateSearchState.restype = ctypes.c_void_p
        d.Everything3_DestroySearchState.argtypes = [ctypes.c_void_p]
        d.Everything3_DestroySearchState.restype = wintypes.BOOL
        d.Everything3_SetSearchTextW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        d.Everything3_SetSearchTextW.restype = wintypes.BOOL
        d.Everything3_SetSearchViewportOffset.argtypes = [
            ctypes.c_void_p, ctypes.c_size_t]
        d.Everything3_SetSearchViewportOffset.restype = wintypes.BOOL
        d.Everything3_SetSearchViewportCount.argtypes = [
            ctypes.c_void_p, ctypes.c_size_t]
        d.Everything3_SetSearchViewportCount.restype = wintypes.BOOL
        d.Everything3_Search.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        d.Everything3_Search.restype = ctypes.c_void_p
        d.Everything3_DestroyResultList.argtypes = [ctypes.c_void_p]
        d.Everything3_DestroyResultList.restype = wintypes.BOOL
        d.Everything3_GetResultListViewportCount.argtypes = [ctypes.c_void_p]
        d.Everything3_GetResultListViewportCount.restype = ctypes.c_size_t
        d.Everything3_GetResultFullPathNameW.argtypes = [
            ctypes.c_void_p, ctypes.c_size_t, wintypes.LPWSTR, ctypes.c_size_t]
        d.Everything3_GetResultFullPathNameW.restype = ctypes.c_size_t
        d.Everything3_IsFolderResult.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        d.Everything3_IsFolderResult.restype = wintypes.BOOL
        d.Everything3_GetResultSize.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        d.Everything3_GetResultSize.restype = ctypes.c_uint64


class EverythingClient:
    """连接 Everything 1.5，执行本地文件搜索。"""

    def __init__(self, project_root: str, exe_path: Optional[str] = None,
                 dll_path: Optional[str] = None, instance_name: Optional[str] = None,
                 timeout: float = 12.0):
        self.project_root = os.path.abspath(project_root)
        self.exe_path = exe_path or _find_everything_exe(self.project_root)
        self.dll_path = dll_path or _find_sdk_dll(self.project_root, self.exe_path)
        self.instance_name = instance_name
        self.timeout = timeout
        self._sdk: Optional[_EverythingSDK] = None
        self._client = None
        self._start_attempted = False
        self._start_error = ""

    @property
    def available(self) -> bool:
        return self._client is not None

    def _load(self) -> _EverythingSDK:
        if self._sdk is None:
            if not IS_WINDOWS:
                raise EverythingError("Everything 本地检索仅支持 Windows。")
            if not self.dll_path:
                raise EverythingError(
                    "缺少 Everything3 SDK DLL。请确认 vendor/everything/ 下同时有 "
                    "Everything.exe、Everything3_x64.dll（或 x86）。")
            try:
                self._sdk = _EverythingSDK(self.dll_path)
            except OSError as exc:
                raise EverythingError(f"无法加载 Everything3 SDK：{exc}") from exc
        return self._sdk

    def _candidate_instances(self) -> list[Optional[str]]:
        names: list[Optional[str]] = []
        if self.instance_name:
            names.append(self.instance_name)
        names.append("1.5a")
        names.extend(_instance_names_from_windows())
        names.extend([None, "1.5"])
        seen = set()
        unique = []
        for name in names:
            key = name or ""
            if key not in seen:
                seen.add(key)
                unique.append(name)
        return unique

    def _connect(self) -> bool:
        sdk = self._load()
        for name in self._candidate_instances():
            client = sdk.dll.Everything3_ConnectW(name)
            if client:
                self._client = client
                return True
        return False

    def ensure_running(self):
        if not IS_WINDOWS:
            raise EverythingError("Everything 本地检索仅支持 Windows。")
        if self._client is not None:
            return
        if self._connect():
            return
        if self._start_attempted and self._start_error:
            raise EverythingError(self._start_error)
        self._start_attempted = True
        if not self.exe_path:
            self._start_error = (
                "没有找到 Everything.exe。请先启动项目 vendor/everything/ 下的 Everything。")
            raise EverythingError(self._start_error)
        try:
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0
            subprocess.Popen(
                [self.exe_path],
                cwd=os.path.dirname(self.exe_path),
                startupinfo=startupinfo,
                close_fds=True,
            )
        except OSError as exc:
            self._start_error = f"无法启动 Everything：{exc}"
            raise EverythingError(self._start_error) from exc

        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            if self._connect():
                _hide_everything_windows()
                return
            time.sleep(0.25)
        self._start_error = (
            "Everything 已启动但 SDK 无法连接。请手动打开 Everything，"
            "确认版本为 1.5 且索引加载完成。")
        raise EverythingError(self._start_error)

    def search(self, query: str, max_results: int = 20) -> list[SearchResult]:
        query = (query or "").strip()
        if not query:
            raise EverythingError("搜索内容不能为空。")
        try:
            max_results = max(1, min(200, int(max_results)))
        except (TypeError, ValueError):
            max_results = 20
        self.ensure_running()
        sdk = self._load()
        d = sdk.dll
        state = d.Everything3_CreateSearchState()
        if not state:
            raise EverythingError("Everything 创建搜索状态失败。")
        result = None
        try:
            if not d.Everything3_SetSearchTextW(state, query):
                raise EverythingError("Everything 设置搜索内容失败。")
            d.Everything3_SetSearchViewportOffset(state, 0)
            d.Everything3_SetSearchViewportCount(state, max_results)
            result = d.Everything3_Search(self._client, state)
            if not result:
                err = int(d.Everything3_GetLastError())
                raise EverythingError(f"Everything 搜索失败（错误码 {err}）。")
            count = int(d.Everything3_GetResultListViewportCount(result))
            items = []
            for index in range(min(count, max_results)):
                buffer = ctypes.create_unicode_buffer(4096)
                d.Everything3_GetResultFullPathNameW(result, index, buffer, 4096)
                full = buffer.value
                if not full:
                    continue
                is_dir = bool(d.Everything3_IsFolderResult(result, index))
                try:
                    size = int(d.Everything3_GetResultSize(result, index))
                except Exception:                                # noqa: BLE001
                    size = 0
                if size >= 0xFFFFFFFFFFFFFFF0:
                    size = 0         # 未请求 size 属性时 SDK 返回 UINT64_MAX
                items.append(SearchResult(full, os.path.basename(full),
                                          is_dir, size))
            return items
        finally:
            if result:
                d.Everything3_DestroyResultList(result)
            d.Everything3_DestroySearchState(state)

    def close(self):
        if self._client is not None and self._sdk is not None:
            try:
                self._sdk.dll.Everything3_DestroyClient(self._client)
            except Exception:                                    # noqa: BLE001
                pass
            self._client = None

    def __del__(self):
        try:
            self.close()
        except Exception:                                        # noqa: BLE001
            pass


def format_results(results: list[SearchResult], query: str,
                   max_chars: int = 6000) -> str:
    if not results:
        return f"没有找到与「{query}」匹配的文件。"
    lines = [f"找到 {len(results)} 条结果（Everything）："]
    for item in results:
        mark = "目录" if item.is_dir else "文件"
        detail = ""
        if item.size:
            detail = f"  ({item.size} B)"
        lines.append(f"- [{mark}] {item.path}{detail}")
    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n…（结果已截断）"
    return text


def build_search_tool(project_root: str, exe_path: Optional[str] = None,
                      dll_path: Optional[str] = None, stop_event=None):
    """返回 agent_framework.Tool，供 finder 子智能体使用。"""
    try:
        from .agent_framework import Tool, ToolResult
    except ImportError:
        from agent_framework import Tool, ToolResult

    client = EverythingClient(project_root, exe_path=exe_path, dll_path=dll_path)

    def handler(args: dict) -> ToolResult:
        if stop_event is not None and stop_event.is_set():
            return ToolResult("已停止。", is_error=True)
        query = str(args.get("query") or "").strip()
        if not query:
            return ToolResult("缺少 query。", is_error=True)
        if args.get("content"):
            escaped = query.replace('"', '\\"')
            query = f'content:"{escaped}"'
        max_results = args.get("max_results", 20)
        try:
            results = client.search(query, max_results=max_results)
        except EverythingError as exc:
            return ToolResult(str(exc), is_error=True)
        return ToolResult(format_results(results, query))

    return Tool(
        "search_files",
        "用 Everything 1.5 在本地磁盘检索文件/文件夹。支持 Everything 语法："
        "文件名、ext:pdf、content:\"关键词\" 内容搜索等。只返回路径，不修改文件。",
        {
            "type": "object",
            "properties": {
                "query": {"type": "string",
                          "description": "Everything 搜索语法；如 报告、ext:pdf 预算、content:\"Q3复盘\""},
                "max_results": {"type": "integer", "description": "最多返回条数，默认 20"},
                "content": {"type": "boolean",
                            "description": "为 true 时把 query 作为文件内容搜索（content:\"...\"）"},
            },
            "required": ["query"],
        },
        handler,
    )
