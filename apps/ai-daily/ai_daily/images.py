"""下载第三方配图（og:image、论文缩略图等），存成 PNG。文件名带 _nowm：watermark.py 只内嵌、不加 hollis23 水印。"""
import logging
import time
from io import BytesIO
from pathlib import Path

import httpx
from PIL import Image, UnidentifiedImageError

from .net import get

log = logging.getLogger(__name__)
MAX_BYTES = 15_000_000
IMAGE_ACCEPT = "image/avif,image/webp,image/apng,image/*,*/*;q=0.8"


def _download(http, url: str, referer: str | None) -> httpx.Response | None:
    """带上来源页作 Referer（不少 CDN 做了防盗链，裸请求返回 403）；429 限流时等一下再试一次。"""
    headers = {"Accept": IMAGE_ACCEPT}
    if referer:
        headers["Referer"] = referer
    for attempt in range(2):
        try:
            return get(http, url, retries=1, headers=headers)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429 and attempt == 0:
                time.sleep(2.0)
                continue
            log.info("配图下载失败 %s：%s", url, e.response.status_code)
            return None
        except httpx.HTTPError as e:
            log.info("配图下载失败 %s：%s", url, e)
            return None
    return None


def save_image(http, url: str, dest: Path, *, referer: str | None = None, min_width: int = 320,
               min_height: int = 160) -> bool:
    """下载成功且尺寸够用返回 True。SVG、太小的图标、打不开的图都跳过。"""
    resp = _download(http, url, referer)
    if resp is None or len(resp.content) > MAX_BYTES:
        return False
    try:
        img = Image.open(BytesIO(resp.content))
        img.seek(0)                  # 动图取第一帧
        img.load()
    except (UnidentifiedImageError, OSError, EOFError):
        return False
    if img.width < min_width or img.height < min_height:
        return False
    has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
    img = img.convert("RGBA" if has_alpha else "RGB")
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest, "PNG", optimize=True)
    return True
