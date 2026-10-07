"""下载第三方配图（og:image、论文缩略图等），存成 PNG。文件名带 _nowm：watermark.py 只内嵌、不加 hollis23 水印。"""
import logging
import shutil
import subprocess
import time
from io import BytesIO
from pathlib import Path

import httpx
from PIL import Image, UnidentifiedImageError

from .net import UA, get

log = logging.getLogger(__name__)
MAX_BYTES = 15_000_000
IMAGE_ACCEPT = "image/avif,image/webp,image/apng,image/*,*/*;q=0.8"


def _curl_path() -> str | None:
    return shutil.which("curl")


def _curl_fetch(url: str, referer: str | None) -> bytes | None:
    """用系统 curl 再下一次。NVIDIA 技术博客的图床（Pantheon/Fastly）按 TLS 指纹拦 Python：
    httpx 不管带什么请求头都是 403，同一台机器上的 curl 能下（2026-10-08 实测）。
    macOS 自带 curl，Windows 10 起自带 curl.exe；没有 curl 就算了。"""
    curl = _curl_path()
    if not curl:
        return None
    cmd = [curl, "-sSL", "--fail", "--max-time", "30", "--max-filesize", str(MAX_BYTES),
           "-A", UA, "-H", f"Accept: {IMAGE_ACCEPT}"]
    if referer:
        cmd += ["-e", referer]
    try:
        out = subprocess.run(cmd + [url], capture_output=True, timeout=40,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired) as e:
        log.info("curl 下载配图失败 %s：%s", url, e)
        return None
    if out.returncode != 0 or not out.stdout:
        log.info("curl 下载配图失败 %s：%s", url, out.stderr.decode("utf-8", "replace").strip() or out.returncode)
        return None
    return out.stdout


def _download(http, url: str, referer: str | None) -> bytes | None:
    """带上来源页作 Referer（不少 CDN 做了防盗链，裸请求返回 403）；429 限流时等一下再试一次；
    403 时换系统 curl 再试一次（见 _curl_fetch）。"""
    headers = {"Accept": IMAGE_ACCEPT}
    if referer:
        headers["Referer"] = referer
    for attempt in range(2):
        try:
            return get(http, url, retries=1, headers=headers).content
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429 and attempt == 0:
                time.sleep(2.0)
                continue
            if e.response.status_code == 403:
                data = _curl_fetch(url, referer)
                if data:
                    log.info("配图用 curl 下载成功（httpx 403）：%s", url)
                    return data
            log.info("配图下载失败 %s：%s", url, e.response.status_code)
            return None
        except httpx.HTTPError as e:
            log.info("配图下载失败 %s：%s", url, e)
            return None
    return None


def save_image(http, url: str, dest: Path, *, referer: str | None = None, min_width: int = 320,
               min_height: int = 160) -> bool:
    """下载成功且尺寸够用返回 True。SVG、太小的图标、打不开的图都跳过。"""
    data = _download(http, url, referer)
    if data is None or len(data) > MAX_BYTES:
        return False
    try:
        img = Image.open(BytesIO(data))
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
