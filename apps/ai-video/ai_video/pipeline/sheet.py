"""Contact sheets: one image with every keyframe, clip or final-video frame of a project, labelled,
so a reviewer (a person, or Claude reading the image) can check a whole project at a glance."""
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .project import CHARACTER_VIEWS, Project, Workspace

THUMB = 360              # long side of each thumbnail
COLUMNS = 6
LABEL_H = 34
FINAL_FRAMES = 24


def _frame(video: Path, at: float, out: Path) -> bool:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("找不到 ffmpeg")
    proc = subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{at:.2f}", "-i", str(video),
                           "-frames:v", "1", str(out)], capture_output=True)
    return proc.returncode == 0 and out.exists()


def _duration(video: Path) -> float:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise RuntimeError("找不到 ffprobe")
    out = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(video)],
                         capture_output=True, text=True).stdout.strip()
    return float(out or 0)


def _tile(items: list[tuple[str, Path | None]], out: Path) -> Path:
    """Grid of labelled thumbnails; a missing image becomes a grey cell, so gaps are visible. Every
    cell takes the most common thumbnail size (the shots), and odd ones such as portrait character
    sheets are fitted inside it, so rows stay compact."""
    thumbs = []
    for label, path in items:
        if path and path.exists():
            with Image.open(path) as im:
                im = im.convert("RGB")
                im.thumbnail((THUMB, THUMB))
                thumbs.append((label, im.copy()))
        else:
            thumbs.append((f"{label} missing", None))
    sizes = [im.size for _, im in thumbs if im]
    cell_w, cell_h = max(set(sizes), key=sizes.count) if sizes else (THUMB, THUMB)
    for i, (label, im) in enumerate(thumbs):
        if im and (im.width > cell_w or im.height > cell_h):
            im.thumbnail((cell_w, cell_h))
            thumbs[i] = (label, im)
    cell_h += LABEL_H
    cols = min(COLUMNS, len(thumbs)) or 1
    rows = (len(thumbs) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * cell_w, rows * cell_h), (24, 24, 24))
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default(size=22)
    for i, (label, im) in enumerate(thumbs):
        x, y = (i % cols) * cell_w, (i // cols) * cell_h
        if im:
            sheet.paste(im, (x + (cell_w - im.width) // 2, y))
        else:
            draw.rectangle((x + 4, y + 4, x + cell_w - 4, y + cell_h - LABEL_H - 4), fill=(70, 70, 70))
        draw.text((x + 8, y + cell_h - LABEL_H + 6), label, fill=(240, 240, 240), font=font)
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out, "JPEG", quality=88)
    return out


def contact_sheet(ws: Workspace, project: Project, what: str) -> Path:
    out = ws.root / "sheets" / f"{what}.jpg"
    if what == "keyframes":
        items = [(f"{cid}_{view}", ws.character(cid, view)) for c in project.characters
                 for cid in [c["id"]] for view in CHARACTER_VIEWS if ws.character(cid, view).exists()]
        items += [(s.id, ws.keyframe(s)) for s in project.shots]
        return _tile(items, out)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        items = []
        if what == "clips":
            for s in project.shots:
                clip = ws.clip(s)
                frame = tmp_dir / f"{s.id}.png"
                ok = clip.exists() and _frame(clip, max(0.0, min(_duration(clip), s.audio_s or 4) / 2), frame)
                items.append((f"{s.id} {s.provider}".strip(), frame if ok else None))
        elif what == "final":
            final = ws.out / "final.mp4"
            if not final.exists():
                raise FileNotFoundError("还没有成片，先跑到 compose")
            total = _duration(final)
            count = min(FINAL_FRAMES, max(1, int(total // 2)))
            for i in range(count):
                at = total * (i + 0.5) / count
                frame = tmp_dir / f"f{i:02d}.png"
                items.append((f"{at:5.1f}s", frame if _frame(final, at, frame) else None))
        else:
            raise ValueError("--what 只能是 keyframes、clips 或 final")
        return _tile(items, out)
