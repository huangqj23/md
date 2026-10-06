"""注册 / 注销 Native Messaging host（都在当前用户下，不需要管理员权限）。

- Windows：manifest 放 data/native-host/，再在 HKCU 注册表里登记它的路径；
- macOS / Linux：浏览器只读自己目录下的 NativeMessagingHosts/<host>.json，把 manifest 复制过去。
"""
import hashlib
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
# 豆包桌面版的内置浏览器也是 Chromium，读自己数据目录下的 NativeMessagingHosts（只在 macOS 上核实过）
BROWSERS = (*REG_KEYS, "doubao")
# `corepack pnpm --filter @md/web ext:zip` 的输出目录，README 让用户“加载已解压的扩展程序”加载的就是它
EXTENSION_DIR = ROOT.parent / "web" / ".output" / "chrome-mv3"
# 相对用户主目录：macOS 在 ~/Library/Application Support 下，Linux 在 ~/.config 下
MANIFEST_DIRS = {
    "darwin": {"chrome": "Library/Application Support/Google/Chrome/NativeMessagingHosts",
               "edge": "Library/Application Support/Microsoft Edge/NativeMessagingHosts",
               "chromium": "Library/Application Support/Chromium/NativeMessagingHosts",
               "doubao": "Library/Application Support/Doubao/NativeMessagingHosts"},
    "linux": {"chrome": ".config/google-chrome/NativeMessagingHosts",
              "edge": ".config/microsoft-edge/NativeMessagingHosts",
              "chromium": ".config/chromium/NativeMessagingHosts"},
}


def host_executable() -> Path:
    """pip 按 pyproject 的 [project.scripts] 生成的 ai-daily-host(.exe)（和当前 python 在同一目录）。"""
    exe = Path(sys.executable).parent / ("ai-daily-host.exe" if sys.platform == "win32" else "ai-daily-host")
    if not exe.is_file():
        raise FileNotFoundError(f"找不到 {exe}，先在 {ROOT} 运行 pip install -e .")
    return exe


def extension_id_for_path(path: Path | str, platform: str = sys.platform) -> str:
    """Chrome 给“已解压的扩展程序”的 ID：目录绝对路径的 SHA-256 前 32 位十六进制，0-f 换成 a-p。
    路径在 Windows 上按 UTF-16LE 算，其他系统按 UTF-8 算（用 2026-09 那台 Windows 上的真实 ID 核对过）。"""
    data = str(path).encode("utf-16-le" if platform == "win32" else "utf-8")
    return "".join(chr(ord("a") + int(c, 16)) for c in hashlib.sha256(data).hexdigest()[:32])


def default_extension_id() -> str:
    return extension_id_for_path(EXTENSION_DIR.resolve())


def build_manifest(extension_ids: list[str], exe: Path, previous: dict | None = None) -> dict:
    origins = list((previous or {}).get("allowed_origins", []))
    for ext_id in extension_ids:
        origin = f"chrome-extension://{ext_id.strip()}/"
        if origin not in origins:
            origins.append(origin)
    return {"name": HOST_NAME, "description": "hollis23 AI 早报（md 扩展的 AI 早报面板）", "path": str(exe),
            "type": "stdio", "allowed_origins": origins}


def browser_manifest_paths(browsers: list[str], platform: str = sys.platform, home: Path | None = None) -> list[Path]:
    """macOS / Linux 上各浏览器读取的 manifest 路径。"""
    dirs = MANIFEST_DIRS["darwin" if platform == "darwin" else "linux"]
    home = home or Path.home()
    return [home / dirs[b] / f"{HOST_NAME}.json" if b in dirs else None for b in browsers]


def install(extension_ids: list[str], browsers: list[str], manifest_path: Path = MANIFEST,
            platform: str = sys.platform, home: Path | None = None) -> list[str]:
    """返回实际登记了的浏览器。macOS / Linux 上跳过没装（没有用户数据目录）的浏览器。"""
    previous = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else None
    manifest = build_manifest(extension_ids, host_executable(), previous)
    text = json.dumps(manifest, ensure_ascii=False, indent=2)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(text, encoding="utf-8")
    if platform == "win32":
        import winreg
        browsers = [b for b in browsers if b in REG_KEYS]
        for browser in browsers:
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{REG_KEYS[browser]}\{HOST_NAME}") as key:
                winreg.SetValueEx(key, "", 0, winreg.REG_SZ, str(manifest_path))
        return list(browsers)
    done = []
    for browser, path in zip(browsers, browser_manifest_paths(browsers, platform, home)):
        if path is None or not path.parent.parent.is_dir():
            continue
        path.parent.mkdir(exist_ok=True)
        path.write_text(text, encoding="utf-8")
        done.append(browser)
    return done


def uninstall(browsers: list[str], platform: str = sys.platform, home: Path | None = None) -> None:
    if platform == "win32":
        import winreg
        for browser in [b for b in browsers if b in REG_KEYS]:
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, rf"{REG_KEYS[browser]}\{HOST_NAME}")
            except FileNotFoundError:
                pass
        return
    for path in browser_manifest_paths(browsers, platform, home):
        if path is not None:
            path.unlink(missing_ok=True)
