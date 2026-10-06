"""API key 落盘加密：Windows 上用 DPAPI（绑定当前 Windows 用户，换个用户或拷到别的机器都解不开）；
macOS 上存进登录钥匙串（llm.json 里只记条目名）；其他系统退化为明文，并在配置里标注。"""
import base64
import ctypes
import os
import shutil
import subprocess
import sys
import uuid
from ctypes import wintypes

_ENTROPY = b"hollis23-ai-daily"          # 额外熵：别的程序用 DPAPI 默认参数解不开
_CRYPTPROTECT_UI_FORBIDDEN = 0x1
KEYCHAIN_SERVICE = "hollis23-ai-daily"    # 钥匙串里的“位置”；“帐户”是每个 key 一个随机名


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


def _security(args: list[str], stdin: str | None = None) -> str:
    """调用 macOS 的 security 命令。写 key 时走 `security -i` 的标准输入，key 不出现在进程命令行里。"""
    proc = subprocess.run(["security", *args], input=stdin, capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        raise OSError(proc.returncode, f"钥匙串操作失败：{(proc.stderr or proc.stdout).strip()[:200]}")
    return proc.stdout


def scheme() -> str:
    """dpapi / keychain / plain：面板据此说明 key 怎么保存。"""
    if os.name == "nt":
        return "dpapi"
    if sys.platform == "darwin" and shutil.which("security"):
        return "keychain"
    return "plain"


def available() -> bool:
    return scheme() != "plain"


def seal(secret: str) -> dict:
    """{"dpapi": base64}、{"keychain": 条目名} 或（其他系统）{"plain": secret}。"""
    kind = scheme()
    if kind == "plain":
        return {"plain": secret}
    if kind == "keychain":
        if any(c in secret for c in '"\\') or any(c.isspace() for c in secret):
            raise ValueError("API key 里不能有引号、反斜杠或空白")
        account = f"key-{uuid.uuid4().hex[:12]}"
        _security(["-i"], f'add-generic-password -U -s {KEYCHAIN_SERVICE} -a {account} '
                          f'-l "ai-daily API key" -w "{secret}"\n')
        return {"keychain": account}
    crypt32 = ctypes.windll.crypt32
    sealed = _crypt(lambda *a: crypt32.CryptProtectData(a[0], "ai-daily", *a[2:]), secret.encode("utf-8"))
    return {"dpapi": base64.b64encode(sealed).decode("ascii")}


def unseal(stored: dict | None) -> str:
    if not stored:
        return ""
    if "plain" in stored:
        return stored["plain"]
    if "keychain" in stored:
        return _security(["find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", stored["keychain"], "-w"]).strip()
    crypt32 = ctypes.windll.crypt32
    return _crypt(crypt32.CryptUnprotectData, base64.b64decode(stored["dpapi"])).decode("utf-8")


def discard(stored: dict | None) -> None:
    """key 被替换或清除时调用：钥匙串里的旧条目一并删掉（DPAPI / 明文就在 llm.json 里，不用另删）。"""
    if stored and "keychain" in stored:
        try:
            _security(["delete-generic-password", "-s", KEYCHAIN_SERVICE, "-a", stored["keychain"]])
        except OSError:
            pass


def hint(secret: str) -> str:
    """界面上只显示首尾几位，例如 sk-…a1b2。"""
    if not secret:
        return ""
    if len(secret) < 12:
        return "…" + secret[-2:]
    return f"{secret[:3]}…{secret[-4:]}"
