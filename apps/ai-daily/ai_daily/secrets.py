"""API key 落盘加密：Windows 上用 DPAPI（绑定当前 Windows 用户，换个用户或拷到别的机器都解不开）；
其他系统退化为明文，并在配置里标注。"""
import base64
import ctypes
import os
from ctypes import wintypes

_ENTROPY = b"hollis23-ai-daily"          # 额外熵：别的程序用 DPAPI 默认参数解不开
_CRYPTPROTECT_UI_FORBIDDEN = 0x1


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes) -> tuple[_Blob, ctypes.Array]:
    buf = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


def _crypt(fn, data: bytes) -> bytes:
    blob_in, _keep = _blob(data)
    entropy, _keep2 = _blob(_ENTROPY)
    blob_out = _Blob()
    if not fn(ctypes.byref(blob_in), None, ctypes.byref(entropy), None, None, _CRYPTPROTECT_UI_FORBIDDEN,
              ctypes.byref(blob_out)):
        raise OSError(ctypes.GetLastError(), "DPAPI 调用失败（可能是在别的 Windows 用户下保存的 key）")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def available() -> bool:
    return os.name == "nt"


def seal(secret: str) -> dict:
    """{"dpapi": base64} 或（非 Windows）{"plain": secret}。"""
    if not available():
        return {"plain": secret}
    crypt32 = ctypes.windll.crypt32
    sealed = _crypt(lambda *a: crypt32.CryptProtectData(a[0], "ai-daily", *a[2:]), secret.encode("utf-8"))
    return {"dpapi": base64.b64encode(sealed).decode("ascii")}


def unseal(stored: dict | None) -> str:
    if not stored:
        return ""
    if "plain" in stored:
        return stored["plain"]
    crypt32 = ctypes.windll.crypt32
    return _crypt(crypt32.CryptUnprotectData, base64.b64decode(stored["dpapi"])).decode("utf-8")


def hint(secret: str) -> str:
    """界面上只显示首尾几位，例如 sk-…a1b2。"""
    if not secret:
        return ""
    if len(secret) < 12:
        return "…" + secret[-2:]
    return f"{secret[:3]}…{secret[-4:]}"
