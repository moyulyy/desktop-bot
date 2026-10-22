# -*- coding: utf-8 -*-
"""本地密码保险库：使用 Windows DPAPI 加密，不把明文写入磁盘。"""

from __future__ import annotations

import ctypes
import json
import os
import tempfile
from datetime import datetime
from typing import Optional

IS_WINDOWS = os.name == "nt"

if IS_WINDOWS:
    from ctypes import wintypes

    class _DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_char))]

    _crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DATA_BLOB), wintypes.LPCWSTR, ctypes.POINTER(_DATA_BLOB),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(_DATA_BLOB)]
    _crypt32.CryptProtectData.restype = wintypes.BOOL
    _crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DATA_BLOB), ctypes.c_void_p, ctypes.POINTER(_DATA_BLOB),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(_DATA_BLOB)]
    _crypt32.CryptUnprotectData.restype = wintypes.BOOL
    _kernel32.LocalFree.argtypes = [ctypes.c_void_p]


def _blob(data: bytes) -> "_DATA_BLOB":
    buffer = ctypes.create_string_buffer(data)
    return _DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))


def _protect(data: bytes) -> bytes:
    if not IS_WINDOWS:
        raise RuntimeError("密码保险库仅支持 Windows DPAPI")
    source = _blob(data)
    output = _DATA_BLOB()
    if not _crypt32.CryptProtectData(
            ctypes.byref(source), "desktop-bot-vault", None, None, None, 0,
            ctypes.byref(output)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        _kernel32.LocalFree(output.pbData)


def _unprotect(data: bytes) -> bytes:
    if not IS_WINDOWS:
        raise RuntimeError("密码保险库仅支持 Windows DPAPI")
    source = _blob(data)
    output = _DATA_BLOB()
    if not _crypt32.CryptUnprotectData(
            ctypes.byref(source), None, None, None, None, 0,
            ctypes.byref(output)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        _kernel32.LocalFree(output.pbData)


class PasswordVault:
    def __init__(self, database_root: str):
        self.root = os.path.abspath(database_root)
        self.folder = os.path.join(self.root, "passwords")
        self.path = os.path.join(self.folder, "vault.dat")
        os.makedirs(self.folder, exist_ok=True)

    def _load(self) -> list[dict]:
        if not os.path.isfile(self.path):
            return []
        try:
            with open(self.path, "rb") as handle:
                payload = json.loads(_unprotect(handle.read()).decode("utf-8"))
        except Exception as exc:                                  # noqa: BLE001
            raise RuntimeError(f"无法读取密码保险库：{exc}") from exc
        entries = payload.get("entries") if isinstance(payload, dict) else []
        return entries if isinstance(entries, list) else []

    def _save(self, entries: list[dict]):
        payload = json.dumps({"version": 1, "entries": entries},
                             ensure_ascii=False).encode("utf-8")
        encrypted = _protect(payload)
        os.makedirs(self.folder, exist_ok=True)
        fd, temp = tempfile.mkstemp(prefix="vault-", suffix=".tmp", dir=self.folder)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(encrypted)
            os.replace(temp, self.path)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)

    @staticmethod
    def _public(entry: dict) -> dict:
        return {key: entry.get(key) for key in (
            "id", "service", "username", "url", "notes", "updated")}

    def add(self, service: str, username: str, password: str,
            url: str = "", notes: str = "") -> dict:
        service = (service or "").strip()
        if not service or not password:
            raise ValueError("service 和 password 不能为空")
        entries = self._load()
        entry = next((item for item in entries
                      if item.get("service", "").casefold() == service.casefold()
                      and item.get("username", "") == username), None)
        if entry is None:
            entry = {"id": str(int(datetime.now().timestamp() * 1000)),
                     "service": service, "username": username}
            entries.append(entry)
        entry.update({"service": service, "username": username,
                      "password": password, "url": url, "notes": notes,
                      "updated": datetime.now().isoformat(timespec="seconds")})
        self._save(entries)
        return self._public(entry)

    def list(self) -> list[dict]:
        return [self._public(entry) for entry in self._load()]

    def get(self, identifier: str) -> Optional[dict]:
        needle = (identifier or "").strip().casefold()
        for entry in self._load():
            if needle in (str(entry.get("id", "")).casefold(),
                          str(entry.get("service", "")).casefold()):
                return dict(entry)
        return None

    def delete(self, identifier: str) -> bool:
        entries = self._load()
        needle = (identifier or "").strip().casefold()
        kept = [entry for entry in entries
                if needle not in (str(entry.get("id", "")).casefold(),
                                  str(entry.get("service", "")).casefold())]
        if len(kept) == len(entries):
            return False
        self._save(kept)
        return True
