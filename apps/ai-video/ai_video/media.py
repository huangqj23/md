"""Image preparation for uploads, crops of source paintings, and clip concatenation."""
import base64
import io
import shutil
import subprocess
from pathlib import Path

from PIL import Image

# Keyframes are stored at up to 2560 px; uploads are capped at 1920 px (enough for 1080p video) and
# ~600 KB, which keeps base64 request bodies small: detailed paintings at 2560 px came to 1.6 MB each,
# and two of them as base64 made gateways time out (HTTP 524) before the request was accepted.
MAX_SIDE = 2560
UPLOAD_SIDE = 1920
UPLOAD_BYTES = 600_000
# Museum scans of handscrolls exceed Pillow's default decompression-bomb limit.
MAX_SOURCE_PIXELS = 600_000_000


def ratio_value(ratio: str) -> float:
    w, h = ratio.split(":")
    return float(w) / float(h)


def encode_jpeg(path: Path, max_side: int = UPLOAD_SIDE, max_bytes: int = UPLOAD_BYTES) -> bytes:
    """JPEG for upload: long side capped at max_side, quality lowered step by step until it fits max_bytes."""
    with Image.open(path) as im:
        im = im.convert("RGB")
        im.thumbnail((max_side, max_side))
        data = b""
        for quality in (92, 88, 84, 80, 75, 70):
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=quality)
            data = buf.getvalue()
            if len(data) <= max_bytes:
                break
        return data


def data_url(path: Path) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(encode_jpeg(path)).decode()


def raw_b64(path: Path) -> str:
    return base64.b64encode(encode_jpeg(path)).decode()


def crop_windows(src: Path, dsts: list[Path], ratio: str, start: float = 0.5, step: float = 0.5) -> None:
    """Cut len(dsts) windows of the given aspect ratio from src, left to right.

    The first window is centred at `start` (fraction of the image width); each next window moves
    right by `step` window widths, so step < 1 gives overlapping windows that can be chained as one
    continuous pan across a handscroll. Windows are clamped to the image and centred vertically.
    """
    old_limit = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = MAX_SOURCE_PIXELS
    try:
        with Image.open(src) as im:
            im = im.convert("RGB")
            w, h = im.size
            target = ratio_value(ratio)
            cw, ch = (round(h * target), h) if w / h > target else (w, round(w / target))
            top = (h - ch) // 2
            for i, dst in enumerate(dsts):
                centre = start * w + i * step * cw
                left = min(max(round(centre - cw / 2), 0), w - cw)
                out = im.crop((left, top, left + cw, top + ch))
                out.thumbnail((MAX_SIDE, MAX_SIDE))
                dst.parent.mkdir(parents=True, exist_ok=True)
                out.save(dst, "JPEG", quality=95)
    finally:
        Image.MAX_IMAGE_PIXELS = old_limit


def to_mp3(audio: bytes) -> bytes:
    """Encode audio bytes in any format ffmpeg reads (e.g. WAV from a TTS API) as mono 128 kbps MP3."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("找不到 ffmpeg，无法把配音转成 mp3")
    proc = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-i", "pipe:0", "-ac", "1",
                           "-b:a", "128k", "-f", "mp3", "pipe:1"], input=audio, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(f"配音转码失败：{proc.stderr.decode('utf-8', 'replace')[-300:]}")
    return proc.stdout


def probe_duration(path: Path) -> float:
    """Media duration in seconds (ffprobe)."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise RuntimeError("找不到 ffprobe")
    out = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         check=True, capture_output=True, text=True).stdout.strip()
    return float(out)


def concat(clips: list[Path], out: Path) -> None:
    """Join clips of the same provider into one silent H.264 file (chained long takes carry no audio)."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("找不到 ffmpeg，无法拼接长镜头片段")
    listing = out.with_suffix(".txt")
    listing.write_text("".join(f"file '{c.resolve().as_posix()}'\n" for c in clips), encoding="utf-8")
    try:
        subprocess.run(
            [ffmpeg, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(listing),
             "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p", "-an", str(out)],
            check=True, capture_output=True,
        )
    finally:
        listing.unlink(missing_ok=True)
