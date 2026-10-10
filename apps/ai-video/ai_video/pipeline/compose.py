"""Final assembly with FFmpeg.

Each clip is cut to its narration line (plus a short pause) and scaled/cropped to the target frame;
a clip shorter than its line holds the last frame, and a shot whose clip never came gets a slow push
on its keyframe instead. Narration lines are padded to the same slots, an optional BGM is ducked
under the voice, subtitles and a small "AI生成" label are burned in, loudness ends at -16 LUFS, and
the file carries the implicit AIGC label of GB 45438-2025 (an `AIGC` metadata field plus a comment).
"""
import json
import shutil
import subprocess
from pathlib import Path

from PIL import Image

from ..speechtags import strip_tags
from .project import Project, Workspace

GAP = 0.35            # pause after each narration line before the cut
TAIL = 0.8            # extra hold on the last shot
FPS = 24               # what every routed video model returns; converting to 30 repeats every 5th frame (judder)
SIZES = {"9:16": (1080, 1920), "16:9": (1920, 1080), "1:1": (1080, 1080), "3:4": (1080, 1440), "4:3": (1440, 1080)}
AI_LABEL = "AI生成"
AIGC_COMMENT = "AIGC：本视频画面、配音和文案由人工智能生成"
END_PUNCT = "。，；、：,.;:"


def ffmpeg(args: list, cwd: Path | None = None) -> None:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise RuntimeError("找不到 ffmpeg")
    proc = subprocess.run([exe, "-y", "-hide_banner", "-loglevel", "error", *map(str, args)],
                          cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg 失败：{proc.stderr.strip()[-800:]}")


def frame_align(seconds: float) -> float:
    """A length rounded to whole frames, so video cuts, audio padding and subtitles share boundaries
    instead of drifting apart by a fraction of a frame per shot."""
    return round(seconds * FPS) / FPS


def slots(project: Project) -> list[float]:
    """Screen time of each shot: its narration plus a pause, the last one held a little longer."""
    out = [frame_align(s.audio_s + GAP) for s in project.shots]
    if out:
        out[-1] = frame_align(out[-1] + TAIL)
    return out


def offsets(lengths: list[float]) -> list[float]:
    acc, out = 0.0, []
    for length in lengths:
        out.append(round(acc, 3))
        acc += length
    return out


def ass_time(t: float) -> str:
    cs = int(round(t * 100))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def clean_line(text: str) -> str:
    """Subtitle text: speech tags such as <long pause> removed, no trailing comma/full stop (Douyin
    style), question and exclamation marks kept."""
    return strip_tags(text).rstrip(END_PUNCT)


def wrap(text: str, width: int) -> list[str]:
    """Split a line into rows of at most `width` characters, preferring breaks after punctuation."""
    rows = []
    while len(text) > width:
        cut = max((i + 1 for i, ch in enumerate(text[:width]) if ch in "，。；：！？、,;:!?"), default=0)
        if cut < width // 2:
            cut = width
        rows.append(text[:cut].rstrip(END_PUNCT))
        text = text[cut:].lstrip(END_PUNCT + " ")  # a row never starts with punctuation
    if text:
        rows.append(text)
    return rows


def build_ass(project: Project, font: str, starts: list[float], ai_label: bool = True) -> str:
    w, h = SIZES[project.aspect]
    base = min(w, h)
    portrait = h > w
    sub_size, title_size, label_size = round(base * 0.058), round(base * 0.1), round(base * 0.03)
    # Portrait: keep subtitles above Douyin's caption and button area at the bottom.
    sub_margin = round(h * (0.2 if portrait else 0.08))
    title_margin = round(h * (0.14 if portrait else 0.1))
    width = 13 if portrait else 20
    total = starts[-1] + slots(project)[-1] if starts else 0
    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {w}", f"PlayResY: {h}", "WrapStyle: 2",
        "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding",
        f"Style: Sub,{font},{sub_size},&H00FFFFFF,&H00FFFFFF,&H00202020,&H64000000,0,0,0,0,100,100,2,0,1,3,1,2,60,60,{sub_margin},1",
        f"Style: Title,{font},{title_size},&H00F0F8FF,&H00FFFFFF,&H00101010,&H64000000,1,0,0,0,100,100,6,0,1,5,2,8,60,60,{title_margin},1",
        f"Style: Label,{font},{label_size},&H60FFFFFF,&H60FFFFFF,&H80000000,&H00000000,0,0,0,0,100,100,0,0,1,1,0,9,40,40,40,1",
        "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    if project.cover_title and starts:
        end = starts[1] if len(starts) > 1 else total
        lines.append(f"Dialogue: 1,{ass_time(0)},{ass_time(end)},Title,,0,0,0,,{{\\fad(200,300)}}{project.cover_title}")
    for shot, start in zip(project.shots, starts):
        text = "\\N".join(wrap(clean_line(shot.narration), width)).replace("{", "（").replace("}", "）")
        lines.append(f"Dialogue: 0,{ass_time(start)},{ass_time(start + shot.audio_s + 0.1)},Sub,,0,0,0,,{text}")
    if ai_label and total:
        lines.append(f"Dialogue: 2,{ass_time(0)},{ass_time(total)},Label,,0,0,0,,{AI_LABEL}")
    return "\n".join(lines) + "\n"


def build_srt(project: Project, starts: list[float]) -> str:
    blocks = []
    for i, (shot, start) in enumerate(zip(project.shots, starts), 1):
        blocks.append(f"{i}\n{srt_time(start)} --> {srt_time(start + shot.audio_s + 0.1)}\n{clean_line(shot.narration)}\n")
    return "\n".join(blocks)


def publish_notes(project: Project, video: Path) -> str:
    lines = [f"# {project.title or project.prompt}", "", f"成片：{video.name}", ""]
    if project.tags:
        lines += ["话题：" + " ".join(f"#{t}" for t in project.tags), ""]
    if project.source:
        s = project.source
        lines += [f"引用：{s.get('author', '')}《{s.get('title', '')}》", ""]
    lines += ["发布前检查：", "- 在抖音发布页勾选「作品含 AI 生成内容」（画面右上角已有「AI生成」角标，文件元数据已写入 AIGC 标识）",
              "- 核对字幕里的诗句、史实和数字"]
    issues = [i for i in (project.check or {}).get("issues") or [] if not i.get("resolved")]
    if issues:
        lines += ["", "事实核查提出的问题（发布前处理）："]
        lines += [f"- {i.get('shot', '')}：{i.get('problem', '')}（建议：{i.get('fix', '')}）" for i in issues]
    return "\n".join(lines) + "\n"


def _normalise_clip(src: Path, out: Path, length: float, w: int, h: int) -> None:
    vf = (f"scale={w}:{h}:force_original_aspect_ratio=increase:out_range=tv,crop={w}:{h},fps={FPS},format=yuv420p,"
          f"tpad=stop_mode=clone:stop_duration=30")
    ffmpeg(["-i", src, "-vf", vf, "-frames:v", round(length * FPS), "-an", "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "18", out])


def _still_clip(image: Path, out: Path, length: float, w: int, h: int) -> None:
    """Slow push-in on a keyframe, for a shot whose clip could not be generated. Keyframes are often
    full-range JPEG; without out_range=tv the concat would take the whole film to full range and the
    generated clips would come out washed out."""
    frames = max(1, round(length * FPS))
    vf = (f"scale={w * 2}:{h * 2}:force_original_aspect_ratio=increase:out_range=tv,crop={w * 2}:{h * 2},"
          f"zoompan=z='min(1+0.0008*on,1.15)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={w}x{h}:fps={FPS},"
          f"format=yuv420p")
    ffmpeg(["-loop", "1", "-i", image, "-vf", vf, "-frames:v", round(length * FPS), "-an", "-c:v", "libx264",
            "-preset", "veryfast", "-crf", "18", out])


def render(project: Project, ws: Workspace, font: str, font_file: Path, bgm: Path | None = None,
           bgm_volume: float = 0.25, ai_label: bool = True) -> Path:
    if project.aspect not in SIZES:
        raise ValueError(f"不支持的画幅 {project.aspect}")
    w, h = SIZES[project.aspect]
    build, out_dir = ws.build, ws.out
    build.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    lengths = slots(project)
    starts = offsets(lengths)
    total = round(sum(lengths), 3)

    names = []
    for shot, length in zip(project.shots, lengths):
        if not ws.audio(shot).exists():
            raise RuntimeError(f"{shot.id} 缺少配音，先跑 audio 阶段")
        name = f"v_{shot.id}.mp4"
        if ws.clip(shot).exists():
            _normalise_clip(ws.clip(shot), build / name, length, w, h)
        elif ws.keyframe(shot).exists():
            _still_clip(ws.keyframe(shot), build / name, length, w, h)
        else:
            raise RuntimeError(f"{shot.id} 既没有视频也没有关键帧")
        names.append(name)
    (build / "clips.txt").write_text("".join(f"file '{n}'\n" for n in names), encoding="utf-8")
    ffmpeg(["-f", "concat", "-safe", "0", "-i", "clips.txt", "-c", "copy", "video.mp4"], cwd=build)

    args, chains = [], []
    for i, (shot, length) in enumerate(zip(project.shots, lengths)):
        args += ["-i", ws.audio(shot).resolve()]
        chains.append(f"[{i}:a]aresample=48000,aformat=channel_layouts=stereo,apad=whole_dur={length:.3f},"
                      f"atrim=0:{length:.3f}[a{i}]")
    joined = "".join(f"[a{i}]" for i in range(len(project.shots)))
    graph = ";".join(chains) + f";{joined}concat=n={len(project.shots)}:v=0:a=1[voice]"
    ffmpeg([*args, "-filter_complex", graph, "-map", "[voice]", "voice.wav"], cwd=build)

    if bgm:
        mix = (f"[1:a]aresample=48000,aformat=channel_layouts=stereo,volume={bgm_volume},atrim=0:{total:.3f}[b];"
               f"[b][0:a]sidechaincompress=threshold=0.02:ratio=8:attack=20:release=500[bd];"
               f"[0:a][bd]amix=inputs=2:duration=first:normalize=0[m]")
        ffmpeg(["-i", "voice.wav", "-stream_loop", "-1", "-i", Path(bgm).resolve(), "-filter_complex", mix,
                "-map", "[m]", "mix.wav"], cwd=build)
    else:
        shutil.copy2(build / "voice.wav", build / "mix.wav")

    fonts_dir = build / "fonts"
    fonts_dir.mkdir(exist_ok=True)
    if not (fonts_dir / font_file.name).exists():
        shutil.copy2(font_file, fonts_dir / font_file.name)
    (build / "subs.ass").write_text(build_ass(project, font, starts, ai_label), encoding="utf-8")
    final = out_dir / "final.mp4"
    ffmpeg(["-i", "video.mp4", "-i", "mix.wav", "-vf", "subtitles=subs.ass:fontsdir=fonts",
            "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-c:v", "libx264", "-preset", "medium", "-crf", "19",
            "-pix_fmt", "yuv420p", "-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709",
            "-color_trc", "bt709", "-r", FPS, "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-shortest",
            # use_metadata_tags: without it the mp4 muxer drops the custom AIGC key.
            "-movflags", "+faststart+use_metadata_tags", "-metadata", f"AIGC={aigc_label(project)}",
            "-metadata", f"comment={AIGC_COMMENT}", final.resolve()], cwd=build)

    (out_dir / "final.srt").write_text(build_srt(project, starts), encoding="utf-8")
    (out_dir / "publish.md").write_text(publish_notes(project, final), encoding="utf-8")
    cover = ws.keyframe(project.shots[0])
    if cover.exists():
        copy_cover(cover, out_dir)
    return final


def aigc_label(project: Project) -> str:
    """Implicit label fields of GB 45438-2025 (Label 1 = AI-generated content)."""
    return json.dumps({"Label": "1", "ContentProducer": "ai-video", "ProduceID": project.id, "ReservedCode1": "",
                       "ContentPropagator": "", "PropagateID": "", "ReservedCode2": ""}, ensure_ascii=False)


def copy_cover(src: Path, out_dir: Path) -> Path:
    """The first keyframe as the cover, named after its real format (Seedream returns JPEG) and copied
    byte for byte so its embedded AIGC provenance data is kept."""
    with Image.open(src) as im:
        ext = ".jpg" if im.format == "JPEG" else ".png"
    for old in out_dir.glob("cover.*"):
        old.unlink()
    dst = out_dir / f"cover{ext}"
    shutil.copy2(src, dst)
    return dst
