"""Subtitle fonts. Only OFL-licensed families (free for commercial use) are offered; a font is taken
from data/fonts, else downloaded, else copied from the Windows font folder when installed there."""
import logging
import shutil
from pathlib import Path

from ..errors import ProviderError
from ..net import download, make_client

log = logging.getLogger(__name__)

WINDOWS_FONTS = Path("C:/Windows/Fonts")
FONTS = {
    "LXGW WenKai": {"file": "LXGWWenKai-Regular.ttf",
                    "url": "https://github.com/lxgw/LxgwWenKai/releases/download/v1.522/LXGWWenKai-Regular.ttf"},
    "Source Han Serif SC": {"file": "Source Han Serif SC Heavy (TrueType).ttf"},
    "Noto Sans SC": {"file": "NotoSansSC-VF.ttf"},
}
FALLBACK_ORDER = ["LXGW WenKai", "Source Han Serif SC", "Noto Sans SC"]


def _locate(family: str, font_dir: Path) -> Path | None:
    spec = FONTS[family]
    local = font_dir / spec["file"]
    if local.exists():
        return local
    if spec.get("url"):
        try:
            with make_client(timeout=300) as http:
                download(http, spec["url"], local)
            return local
        except ProviderError as e:
            log.warning("下载字体 %s 失败：%s", family, e)
    system = WINDOWS_FONTS / spec["file"]
    if system.exists():
        font_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(system, local)
        return local
    return None


def resolve_font(family: str, font_dir: Path) -> tuple[str, Path]:
    """(family actually used, font file). Falls back through the other OFL families."""
    order = [family] + [f for f in FALLBACK_ORDER if f != family] if family in FONTS else FALLBACK_ORDER
    for name in order:
        path = _locate(name, font_dir)
        if path:
            if name != family:
                log.warning("字体 %s 不可用，改用 %s", family, name)
            return name, path
    raise RuntimeError(f"找不到可用的字幕字体：把 {FONTS['LXGW WenKai']['file']} 放进 {font_dir}")
