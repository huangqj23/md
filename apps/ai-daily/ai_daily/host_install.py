"""注册 / 注销 Native Messaging host（Windows：写 HKCU 注册表，不需要管理员权限）。"""
import json
import sys
from pathlib import Path

from .config import ROOT
from .native_host import HOST_NAME

MANIFEST = ROOT / "data" / "native-host" / f"{HOST_NAME}.json"
REG_KEYS = {
    "chrome": r"Software\Google\Chrome\NativeMessagingHosts",
    "edge": r"Software\Microsoft\Edge\NativeMessagingHosts",
    "chromium": r"Software\Chromium\NativeMessagingHosts",      # Playwright 自带的 Chromium 读这里
}


def host_executable() -> Path:
    """pip 按 pyproject 的 [project.scripts] 生成的 ai-daily-host.exe（和当前 python 在同一目录）。"""
    exe = Path(sys.executable).parent / ("ai-daily-host.exe" if sys.platform == "win32" else "ai-daily-host")
    if not exe.is_file():
        raise FileNotFoundError(f"找不到 {exe}，先在 {ROOT} 运行 pip install -e .")
    return exe


def build_manifest(extension_ids: list[str], exe: Path, previous: dict | None = None) -> dict:
    origins = list((previous or {}).get("allowed_origins", []))
    for ext_id in extension_ids:
        origin = f"chrome-extension://{ext_id.strip()}/"
        if origin not in origins:
            origins.append(origin)
    return {"name": HOST_NAME, "description": "hollis23 AI 早报（md 扩展的 AI 早报面板）", "path": str(exe),
            "type": "stdio", "allowed_origins": origins}


def install(extension_ids: list[str], browsers: list[str], manifest_path: Path = MANIFEST) -> Path:
    import winreg
    previous = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else None
    manifest = build_manifest(extension_ids, host_executable(), previous)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    for browser in browsers:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{REG_KEYS[browser]}\{HOST_NAME}") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, str(manifest_path))
    return manifest_path


def uninstall(browsers: list[str]) -> None:
    import winreg
    for browser in browsers:
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, rf"{REG_KEYS[browser]}\{HOST_NAME}")
        except FileNotFoundError:
            pass
