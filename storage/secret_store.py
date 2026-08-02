"""Windows user-scoped secret protection for values stored in SQLite."""

from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes
import sys


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob(data: bytes) -> tuple[_DataBlob, ctypes.Array]:
    buffer = ctypes.create_string_buffer(data)
    value = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    return value, buffer


def protect_secret(value: str) -> str:
    """Encrypt a value for the current Windows user using DPAPI."""
    if not value:
        return ""
    if sys.platform != "win32":
        raise RuntimeError("API Key 加密目前仅支持 Windows")
    source, source_buffer = _blob(value.encode("utf-8"))
    result = _DataBlob()
    if not ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(source), "AutoAgent API Key", None, None, None, 0, ctypes.byref(result)
    ):
        raise ctypes.WinError()
    try:
        encrypted = ctypes.string_at(result.pbData, result.cbData)
        return "dpapi:" + base64.b64encode(encrypted).decode("ascii")
    finally:
        ctypes.windll.kernel32.LocalFree(result.pbData)
        del source_buffer


def unprotect_secret(value: str) -> str:
    """Decrypt a DPAPI value. Empty and unreadable values are returned safely."""
    if not value:
        return ""
    if not value.startswith("dpapi:") or sys.platform != "win32":
        return ""
    try:
        raw = base64.b64decode(value[6:])
        source, source_buffer = _blob(raw)
        result = _DataBlob()
        if not ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(source), None, None, None, None, 0, ctypes.byref(result)
        ):
            return ""
        try:
            return ctypes.string_at(result.pbData, result.cbData).decode("utf-8")
        finally:
            ctypes.windll.kernel32.LocalFree(result.pbData)
            del source_buffer
    except (ValueError, OSError):
        return ""
