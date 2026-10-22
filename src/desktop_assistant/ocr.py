# -*- coding: utf-8 -*-
"""图片 OCR：优先调用 Umi-OCR HTTP API，失败时回退 Tesseract。

Umi-OCR 的接口在不同版本中字段略有差异，这里做了容错：
* 默认读取 ``UMI_OCR_URL``（例如 http://127.0.0.1:1224）
* 兼容 ``/api/ocr`` 的 base64 请求，以及常见的 data/text/ocr 响应结构
* 任何失败都不阻断 PDF/图片入库，只是返回空文本
"""

from __future__ import annotations

import base64
import os
from typing import Optional

import requests


def _extract_text(data) -> str:
    if data is None:
        return ""
    if isinstance(data, str):
        return data.strip()
    if isinstance(data, list):
        return "\n".join(filter(None, (_extract_text(item) for item in data)))
    if isinstance(data, dict):
        for key in ("text", "ocr", "content", "result"):
            if data.get(key):
                text = _extract_text(data[key])
                if text:
                    return text
        for key in ("data", "results", "items", "pages"):
            if key in data:
                text = _extract_text(data[key])
                if text:
                    return text
    return ""


class UmiOCR:
    def __init__(self, base_url: Optional[str] = None, timeout: float = 20.0):
        self.base_url = (base_url or os.environ.get("UMI_OCR_URL") or "").rstrip("/")
        self.timeout = timeout

    @property
    def enabled(self) -> bool:
        return bool(self.base_url)

    def recognize_bytes(self, data: bytes) -> str:
        if not self.enabled or not data:
            return ""
        endpoint = (self.base_url if self.base_url.endswith("/api/ocr")
                    else self.base_url + "/api/ocr")
        encoded = base64.b64encode(data).decode("ascii")
        payloads = [
            {"base64": encoded, "options": {"data.format": "text"}},
            {"base64": encoded},
            {"image": encoded},
        ]
        for payload in payloads:
            try:
                response = requests.post(endpoint, json=payload, timeout=self.timeout)
                if response.status_code >= 400:
                    continue
                try:
                    body = response.json()
                except ValueError:
                    text = response.text.strip()
                    if text:
                        return text
                    continue
                if isinstance(body, dict) and body.get("code") not in (None, 100, 101):
                    continue
                text = _extract_text(body.get("data", body) if isinstance(body, dict)
                                     else body)
                if text:
                    return text
            except requests.RequestException:
                continue
        return ""

    def recognize_path(self, path: str) -> str:
        try:
            with open(path, "rb") as handle:
                return self.recognize_bytes(handle.read())
        except OSError:
            return ""


def ocr_bytes(data: bytes, lang: str = "chi_sim+eng") -> str:
    text = UmiOCR().recognize_bytes(data)
    if text:
        return text
    try:
        from io import BytesIO
        from PIL import Image
        import pytesseract
        return pytesseract.image_to_string(Image.open(BytesIO(data)),
                                           lang=lang).strip()
    except Exception:                                             # noqa: BLE001
        return ""


def ocr_path(path: str, lang: str = "chi_sim+eng") -> str:
    text = UmiOCR().recognize_path(path)
    if text:
        return text
    try:
        from PIL import Image
        import pytesseract
        return pytesseract.image_to_string(Image.open(path), lang=lang).strip()
    except Exception:                                             # noqa: BLE001
        return ""
