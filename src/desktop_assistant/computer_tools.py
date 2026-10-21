# -*- coding: utf-8 -*-
"""电脑操作工具：屏幕截图、鼠标、键盘、窗口与工作空间。

这些工具只应在用户明确授权的电脑操作子智能体中使用。输入模拟基于 Win32
``SendInput`` / ``mouse_event``，截图基于 GDI + Qt ``QImage``，不额外依赖 PIL。
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Optional

try:  # 包内导入
    from .agent_framework import Tool, ToolResult
except ImportError:  # 兼容直接导入
    from agent_framework import Tool, ToolResult

IS_WINDOWS = os.name == "nt"

if IS_WINDOWS:
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

    ULONG_PTR = (ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8
                 else ctypes.c_ulong)

    class _MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                    ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

    class _KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                    ("dwExtraInfo", ULONG_PTR)]

    class _HARDWAREINPUT(ctypes.Structure):
        _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD),
                    ("wParamH", wintypes.WORD)]

    class _INPUTUNION(ctypes.Union):
        _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT),
                    ("hi", _HARDWAREINPUT)]

    class _INPUT(ctypes.Structure):
        _anonymous_ = ("u",)
        _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]

    user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int]
    user32.SendInput.restype = wintypes.UINT
    user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
    user32.SetCursorPos.restype = wintypes.BOOL
    user32.mouse_event.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                                   wintypes.DWORD, ULONG_PTR]
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.EnumWindows.argtypes = [ctypes.c_void_p, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    user32.GetCursorPos.restype = wintypes.BOOL
    user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    user32.GetSystemMetrics.restype = ctypes.c_int
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.GetDC.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.BitBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int,
                             ctypes.c_int, ctypes.c_int, wintypes.HDC,
                             ctypes.c_int, ctypes.c_int, wintypes.DWORD]
    gdi32.BitBlt.restype = wintypes.BOOL
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.DeleteDC.argtypes = [wintypes.HDC]

    class _BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                    ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                    ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD),
                    ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG),
                    ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD)]

    class _BITMAPINFO(ctypes.Structure):
        _fields_ = [("bmiHeader", _BITMAPINFOHEADER),
                    ("bmiColors", wintypes.DWORD * 3)]

    gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT,
                                wintypes.UINT, ctypes.c_void_p,
                                ctypes.POINTER(_BITMAPINFO), wintypes.UINT]
    gdi32.GetDIBits.restype = ctypes.c_int


# --------------------------------------------------------------------------- #
# 工作空间
# --------------------------------------------------------------------------- #
class ComputerWorkspace:
    def __init__(self, root: str):
        root = os.path.expanduser(str(root or "temp"))
        self.root = os.path.abspath(root)
        self.screenshots_dir = os.path.join(self.root, "screenshots")
        self.scripts_dir = os.path.join(self.root, "scripts")
        self.notes_dir = os.path.join(self.root, "notes")

    def ensure(self):
        for directory in (self.root, self.screenshots_dir,
                          self.scripts_dir, self.notes_dir):
            os.makedirs(directory, exist_ok=True)
        return self.root

    def safe_path(self, relative: str, subdir: str = "") -> str:
        relative = (relative or "").strip().replace("\\", "/")
        if not relative:
            raise ValueError("路径不能为空")
        base = os.path.abspath(os.path.join(self.root, subdir))
        path = (os.path.abspath(relative) if os.path.isabs(relative)
                else os.path.abspath(os.path.join(base, relative)))
        norm_root = os.path.normcase(os.path.abspath(self.root))
        norm_path = os.path.normcase(path)
        if os.path.commonpath([norm_root, norm_path]) != norm_root:
            raise ValueError("路径必须位于工作空间内")
        return path

    def screenshot_path(self, name: str = "") -> str:
        self.ensure()
        raw = (name or "").strip().replace("\\", "/")
        if raw:
            base = os.path.basename(raw)
            if not base.lower().endswith((".png", ".jpg", ".jpeg", ".bmp")):
                base += ".png"
            path = self.safe_path(base, "screenshots")
        else:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
            path = os.path.join(self.screenshots_dir, f"screen-{stamp}.png")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def info(self) -> dict:
        self.ensure()
        return {
            "workspace": self.root,
            "screenshots": self.screenshots_dir,
            "scripts": self.scripts_dir,
            "notes": self.notes_dir,
        }


# --------------------------------------------------------------------------- #
# Win32 输入与窗口
# --------------------------------------------------------------------------- #
_VK = {
    "backspace": 0x08, "tab": 0x09, "enter": 0x0D, "return": 0x0D,
    "shift": 0x10, "ctrl": 0x11, "control": 0x11, "alt": 0x12,
    "pause": 0x13, "capslock": 0x14, "esc": 0x1B, "escape": 0x1B,
    "space": 0x20, "pageup": 0x21, "pagedown": 0x22, "end": 0x23,
    "home": 0x24, "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "insert": 0x2D, "delete": 0x2E, "win": 0x5B, "windows": 0x5B,
    "cmd": 0x5B, "meta": 0x5B, "apps": 0x5D,
    "num0": 0x60, "num1": 0x61, "num2": 0x62, "num3": 0x63,
    "num4": 0x64, "num5": 0x65, "num6": 0x66, "num7": 0x67,
    "num8": 0x68, "num9": 0x69,
    "add": 0x6B, "subtract": 0x6D, "multiply": 0x6A, "divide": 0x6F,
    "decimal": 0x6E,
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73, "f5": 0x74,
    "f6": 0x75, "f7": 0x76, "f8": 0x77, "f9": 0x78, "f10": 0x79,
    "f11": 0x7A, "f12": 0x7B,
    "lshift": 0xA0, "rshift": 0xA1, "lctrl": 0xA2, "rctrl": 0xA3,
    "lalt": 0xA4, "ralt": 0xA5,
}


def _vk_for(key: str) -> int:
    key = (key or "").strip().lower()
    if key in _VK:
        return _VK[key]
    if len(key) == 1:
        return ord(key.upper())
    if key.startswith("f") and key[1:].isdigit():
        number = int(key[1:])
        if 1 <= number <= 24:
            return 0x6F + number
    raise ValueError(f"不认识的按键：{key}")


def _send_inputs(inputs):
    if not IS_WINDOWS:
        raise RuntimeError("电脑操作仅支持 Windows")
    if not inputs:
        return
    array = (_INPUT * len(inputs))(*inputs)
    user32.SendInput(len(array), array, ctypes.sizeof(_INPUT))


def _key_event(vk: int = 0, scan: int = 0, flags: int = 0):
    return _INPUT(type=1, u=_INPUTUNION(
        ki=_KEYBDINPUT(vk, scan, flags, 0, 0)))


def _key_down(key: str):
    _send_inputs([_key_event(_vk_for(key))])


def _key_up(key: str):
    _send_inputs([_key_event(_vk_for(key), 0, 0x0002)])


def _tap_key(key: str):
    _key_down(key)
    _key_up(key)


def _type_text(text: str, interval: float = 0.01, stop_event=None):
    text = str(text or "")
    if not text:
        return
    interval = max(0.0, min(0.2, float(interval)))
    # SendInput 使用 UTF-16 code unit；逐字符发送，兼容中文与表情。
    data = text.encode("utf-16-le")
    for index in range(0, len(data), 2):
        if stop_event is not None and stop_event.is_set():
            break
        unit = int.from_bytes(data[index:index + 2], "little")
        _send_inputs([
            _key_event(0, unit, 0x0004),
            _key_event(0, unit, 0x0004 | 0x0002),
        ])
        if interval:
            time.sleep(interval)


def _mouse_button(button: str, down: bool):
    flags = {
        ("left", True): 0x0002, ("left", False): 0x0004,
        ("right", True): 0x0008, ("right", False): 0x0010,
        ("middle", True): 0x0020, ("middle", False): 0x0040,
    }
    user32.mouse_event(flags[(button, down)], 0, 0, 0, 0)


def _click(button: str = "left", clicks: int = 1, interval: float = 0.08):
    for _ in range(max(1, min(3, int(clicks)))):
        _mouse_button(button, True)
        _mouse_button(button, False)
        time.sleep(max(0.05, min(0.3, float(interval))))


def _active_window_title() -> str:
    if not IS_WINDOWS:
        return ""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return ""
    length = user32.GetWindowTextLengthW(hwnd) + 1
    buffer = ctypes.create_unicode_buffer(max(2, length))
    user32.GetWindowTextW(hwnd, buffer, len(buffer))
    return buffer.value


def _enum_windows() -> list[dict]:
    if not IS_WINDOWS:
        return []
    rows: list[dict] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd) + 1
        if length <= 1:
            return True
        buffer = ctypes.create_unicode_buffer(length)
        user32.GetWindowTextW(hwnd, buffer, length)
        if buffer.value:
            rows.append({"handle": int(hwnd), "title": buffer.value})
        return True

    user32.EnumWindows(callback, 0)
    return rows


def _focus_window(title: str) -> bool:
    needle = (title or "").strip().lower()
    if not needle:
        return False
    for row in _enum_windows():
        if needle in row["title"].lower():
            hwnd = int(row["handle"])
            user32.ShowWindow(hwnd, 9)          # SW_RESTORE
            user32.SetForegroundWindow(hwnd)
            return True
    return False


def _capture_screen(path: str) -> dict:
    if not IS_WINDOWS:
        raise RuntimeError("屏幕截图仅支持 Windows")
    from PySide6.QtGui import QImage

    x = user32.GetSystemMetrics(76)              # SM_XVIRTUALSCREEN
    y = user32.GetSystemMetrics(77)              # SM_YVIRTUALSCREEN
    width = user32.GetSystemMetrics(78)          # SM_CXVIRTUALSCREEN
    height = user32.GetSystemMetrics(79)         # SM_CYVIRTUALSCREEN
    width, height = max(1, width), max(1, height)
    hdc = user32.GetDC(None)
    memdc = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, width, height)
    old = gdi32.SelectObject(memdc, bmp)
    try:
        if not gdi32.BitBlt(memdc, 0, 0, width, height, hdc, x, y, 0x00CC0020):
            raise RuntimeError("屏幕捕获失败")
        bmi = _BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = width
        bmi.bmiHeader.biHeight = -height         # 负数 = 自上而下
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = 0
        buffer = ctypes.create_string_buffer(width * height * 4)
        got = gdi32.GetDIBits(memdc, bmp, 0, height, buffer,
                              ctypes.byref(bmi), 0)
        if not got:
            raise RuntimeError("读取屏幕像素失败")
        image = QImage(buffer.raw, width, height, width * 4,
                       QImage.Format.Format_ARGB32).copy()
        if not image.save(path, "PNG"):
            raise RuntimeError("保存截图失败")
        return {"path": path, "width": width, "height": height,
                "x": x, "y": y, "active_window": _active_window_title()}
    finally:
        gdi32.SelectObject(memdc, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(memdc)
        user32.ReleaseDC(None, hdc)


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #
def _ok(text: str) -> ToolResult:
    return ToolResult(text)


def build_computer_tools(workspace: str, stop_event=None) -> list[Tool]:
    ws = ComputerWorkspace(workspace)

    def _no_windows() -> Optional[ToolResult]:
        if IS_WINDOWS:
            return None
        return ToolResult("电脑操作仅支持 Windows。", is_error=True)

    def _check_stop() -> Optional[ToolResult]:
        if stop_event is not None and stop_event.is_set():
            return ToolResult("已停止。", is_error=True)
        return None

    def screen_info(_args):
        err = _no_windows()
        if err:
            return err
        point = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(point))
        return _ok(
            f"屏幕虚拟区域：{user32.GetSystemMetrics(76)},{user32.GetSystemMetrics(77)} "
            f"{user32.GetSystemMetrics(78)}×{user32.GetSystemMetrics(79)}\n"
            f"当前窗口：{_active_window_title() or '（无）'}\n"
            f"鼠标：{point.x},{point.y}\n"
            f"工作空间：{ws.root}")

    def screen_capture(args):
        err = _no_windows()
        if err:
            return err
        if stop_event is not None and stop_event.is_set():
            return ToolResult("已停止。", is_error=True)
        try:
            path = ws.screenshot_path(str(args.get("name") or ""))
            info = _capture_screen(path)
        except Exception as exc:                                  # noqa: BLE001
            return ToolResult(f"截图失败：{exc}", is_error=True)
        return _ok(f"截图已保存：{info['path']}\n"
                   f"活动窗口：{info['active_window'] or '（无）'}\n"
                   f"![屏幕截图]({info['path'].replace(os.sep, '/')})")

    def list_windows(_args):
        err = _no_windows()
        if err:
            return err
        rows = _enum_windows()
        if not rows:
            return _ok("没有可见窗口。")
        return _ok("\n".join(f"- {row['title']}" for row in rows[:50]))

    def focus_window(args):
        err = _no_windows()
        if err:
            return err
        title = str(args.get("title") or "").strip()
        if not title:
            return ToolResult("缺少 title。", is_error=True)
        if not _focus_window(title):
            return ToolResult(f"没有找到窗口：{title}", is_error=True)
        return _ok(f"已切换到窗口：{_active_window_title() or title}")

    def mouse_move(args):
        err = _no_windows()
        if err:
            return err
        x, y = int(args.get("x", 0)), int(args.get("y", 0))
        user32.SetCursorPos(x, y)
        return _ok(f"鼠标已移动到 {x},{y}")

    def mouse_click(args):
        err = _no_windows()
        if err:
            return err
        if "x" in args and "y" in args:
            user32.SetCursorPos(int(args["x"]), int(args["y"]))
        button = str(args.get("button") or "left").lower()
        if button not in ("left", "right", "middle"):
            button = "left"
        _click(button, int(args.get("clicks", 1)), float(args.get("interval", 0.08)))
        return _ok(f"已执行 {button} 点击")

    def mouse_scroll(args):
        err = _no_windows()
        if err:
            return err
        amount = int(args.get("amount", 0))
        if "x" in args and "y" in args:
            user32.SetCursorPos(int(args["x"]), int(args["y"]))
        user32.mouse_event(0x0800, 0, 0, amount, 0)
        return _ok(f"已滚动 {amount}")

    def mouse_drag(args):
        err = _no_windows()
        if err:
            return err
        x1, y1 = int(args.get("from_x", 0)), int(args.get("from_y", 0))
        x2, y2 = int(args.get("to_x", x1)), int(args.get("to_y", y1))
        user32.SetCursorPos(x1, y1)
        _mouse_button("left", True)
        try:
            steps = max(8, min(40, int(args.get("steps", 20))))
            for index in range(1, steps + 1):
                if stop_event is not None and stop_event.is_set():
                    break
                user32.SetCursorPos(x1 + round((x2 - x1) * index / steps),
                                    y1 + round((y2 - y1) * index / steps))
                time.sleep(0.012)
        finally:
            _mouse_button("left", False)
        return _ok(f"已拖动到 {x2},{y2}")

    def keyboard_type(args):
        err = _no_windows()
        if err:
            return err
        text = str(args.get("text") or "")
        _type_text(text, float(args.get("interval", 0.01)), stop_event)
        return _ok(f"已输入 {len(text)} 个字符")

    def keyboard_press(args):
        err = _no_windows()
        if err:
            return err
        keys = str(args.get("keys") or args.get("key") or "").strip()
        if not keys:
            return ToolResult("缺少 keys。", is_error=True)
        parts = [part.strip() for part in keys.replace(" ", "+").split("+") if part.strip()]
        try:
            for key in parts:
                _key_down(key)
            for key in reversed(parts):
                _key_up(key)
        except ValueError as exc:
            return ToolResult(str(exc), is_error=True)
        return _ok(f"已按键：{keys}")

    def open_app(args):
        err = _no_windows()
        if err:
            return err
        target = str(args.get("target") or "").strip()
        if not target:
            return ToolResult("缺少 target。", is_error=True)
        target = os.path.expandvars(os.path.expanduser(target))
        extra = args.get("args") or []
        if not isinstance(extra, list):
            extra = [str(extra)]
        if target.startswith(("http://", "https://")):
            webbrowser.open(target)
        elif extra or os.path.isfile(target):
            subprocess.Popen([target] + [str(x) for x in extra],
                             cwd=ws.root, close_fds=True)
        else:
            os.startfile(target)                             # type: ignore[attr-defined]
        return _ok(f"已打开：{target}")

    def wait(args):
        seconds = max(0.0, min(10.0, float(args.get("seconds", 1))))
        if stop_event is not None:
            stop_event.wait(seconds)
        else:
            time.sleep(seconds)
        return _ok(f"等待 {seconds:.1f} 秒")

    def workspace_info(_args):
        info = ws.info()
        return _ok("\n".join(f"{key}: {value}" for key, value in info.items()))

    def workspace_write(args):
        rel = str(args.get("path") or "").strip()
        content = str(args.get("content") or "")
        if not rel:
            return ToolResult("缺少 path。", is_error=True)
        if len(content) > 200_000:
            return ToolResult("内容过大（上限 200KB）。", is_error=True)
        try:
            path = ws.safe_path(rel, "scripts")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(content)
        except (OSError, ValueError) as exc:
            return ToolResult(f"写入工作空间失败：{exc}", is_error=True)
        return _ok(f"已写入：{path}")

    def workspace_list(args):
        rel = str(args.get("path") or "").strip()
        try:
            target = ws.safe_path(rel) if rel else ws.root
            if os.path.isfile(target):
                rows = [target]
            else:
                rows = [os.path.relpath(str(p), ws.root)
                        for p in Path(target).iterdir()]
        except (OSError, ValueError) as exc:
            return ToolResult(f"读取工作空间失败：{exc}", is_error=True)
        return _ok("\n".join(f"- {row}" for row in sorted(rows)[:100])
                   or "（空）")

    def workspace_run_python(args):
        rel = str(args.get("path") or "").strip()
        if not rel:
            return ToolResult("缺少 path。", is_error=True)
        if not rel.lower().endswith(".py"):
            rel += ".py"
        try:
            script = ws.safe_path(rel, "scripts")
        except ValueError as exc:
            return ToolResult(str(exc), is_error=True)
        if not os.path.isfile(script):
            return ToolResult(f"脚本不存在：{script}", is_error=True)
        cmd = [sys.executable, script] + [str(x) for x in (args.get("args") or [])]
        timeout = max(1.0, min(60.0, float(args.get("timeout", 20))))
        creation = 0x08000000 if IS_WINDOWS else 0
        try:
            proc = subprocess.Popen(cmd, cwd=ws.root, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True,
                                    errors="replace", creationflags=creation)
        except OSError as exc:
            return ToolResult(f"无法启动脚本：{exc}", is_error=True)
        deadline = time.monotonic() + timeout
        stopped = False
        while True:
            try:
                out, err = proc.communicate(timeout=0.15)
                break
            except subprocess.TimeoutExpired:
                if stop_event is not None and stop_event.is_set():
                    stopped = True
                    proc.kill()
                elif time.monotonic() >= deadline:
                    proc.kill()
                    out, err = "", "脚本执行超时。"
                    break
                else:
                    continue
                try:
                    out, err = proc.communicate(timeout=1)
                except subprocess.TimeoutExpired:
                    out, err = "", ""
                break
        if stopped:
            return ToolResult("已停止。", is_error=True)
        text = (out or "") + (("\n[stderr]\n" + err) if err else "")
        return ToolResult(text.strip() or "（无输出）", is_error=bool(err or proc.returncode))

    tools = [
        Tool("screen_info",
             "查看当前屏幕尺寸、活动窗口和鼠标位置。只读。",
             {"type": "object", "properties": {}}, screen_info),
        Tool("screen_capture",
             "截取整个虚拟桌面并保存到工作空间 screenshots/，返回绝对路径。",
             {"type": "object", "properties": {
                 "name": {"type": "string", "description": "可选文件名，默认按时间命名"}}},
             screen_capture),
        Tool("list_windows",
             "列出当前可见窗口标题，便于 focus_window。",
             {"type": "object", "properties": {}}, list_windows),
        Tool("focus_window",
             "按标题片段把窗口切到前台。",
             {"type": "object", "properties": {"title": {"type": "string"}},
              "required": ["title"]}, focus_window),
        Tool("mouse_move", "把鼠标移动到绝对屏幕坐标。",
             {"type": "object", "properties": {
                 "x": {"type": "integer"}, "y": {"type": "integer"}},
              "required": ["x", "y"]}, mouse_move),
        Tool("mouse_click", "在绝对屏幕坐标点击；不传 x/y 则点击当前位置。",
             {"type": "object", "properties": {
                 "x": {"type": "integer"}, "y": {"type": "integer"},
                 "button": {"type": "string", "enum": ["left", "right", "middle"]},
                 "clicks": {"type": "integer", "description": "1 或 2"}}},
             mouse_click),
        Tool("mouse_scroll", "滚动鼠标滚轮；正数向上，负数向下。",
             {"type": "object", "properties": {
                 "amount": {"type": "integer"},
                 "x": {"type": "integer"}, "y": {"type": "integer"}},
              "required": ["amount"]}, mouse_scroll),
        Tool("mouse_drag", "按住左键从起点拖动到终点（用于拖拽文件/窗口）。",
             {"type": "object", "properties": {
                 "from_x": {"type": "integer"}, "from_y": {"type": "integer"},
                 "to_x": {"type": "integer"}, "to_y": {"type": "integer"}},
              "required": ["from_x", "from_y", "to_x", "to_y"]}, mouse_drag),
        Tool("keyboard_type", "向当前焦点输入文本，支持中文和 Unicode。",
             {"type": "object", "properties": {
                 "text": {"type": "string"},
                 "interval": {"type": "number", "description": "字符间隔秒数"}},
              "required": ["text"]}, keyboard_type),
        Tool("keyboard_press", "按下组合键，例如 ctrl+s、enter、alt+tab、win+r。",
             {"type": "object", "properties": {
                 "keys": {"type": "string"}}, "required": ["keys"]}, keyboard_press),
        Tool("open_app", "打开应用、文件、文件夹或 URL。",
             {"type": "object", "properties": {
                 "target": {"type": "string"},
                 "args": {"type": "array", "items": {"type": "string"}}},
              "required": ["target"]}, open_app),
        Tool("wait", "等待指定秒数，便于等待界面加载。",
             {"type": "object", "properties": {
                 "seconds": {"type": "number", "description": "最多 10 秒"}}},
             wait),
        Tool("workspace_info", "查看电脑操作子智能体的工作空间目录。",
             {"type": "object", "properties": {}}, workspace_info),
        Tool("workspace_write", "在工作空间 scripts/ 下写文件，适合生成临时 py 脚本。",
             {"type": "object", "properties": {
                 "path": {"type": "string"}, "content": {"type": "string"}},
              "required": ["path", "content"]}, workspace_write),
        Tool("workspace_list", "列出工作空间中的文件。",
             {"type": "object", "properties": {"path": {"type": "string"}}},
             workspace_list),
        Tool("workspace_run_python", "运行工作空间 scripts/ 下的 Python 脚本。",
             {"type": "object", "properties": {
                 "path": {"type": "string"},
                 "args": {"type": "array", "items": {"type": "string"}},
                 "timeout": {"type": "number"}},
              "required": ["path"]}, workspace_run_python),
    ]
    return tools
