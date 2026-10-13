"""Final assembly of a drama (template: drama) with FFmpeg.

Each shot runs for the seconds written in the script. A dissolve or a fade through black overlaps
a shot with the one before it (xfade); shots joined by cuts are concatenated into runs first, and
xfade only blends one run into the next, all on one frame-counted timeline. Sound comes in two
buses: speech (the clip's own dialogue, a TTS line or narration) and ambience (the clip's sound
on shots without speech). Speech ducks the background music; ambience does not. Subtitles are
layered: chapter label, historical note, the line, quote cards in a brush font, the film's name,
a vertical notice and credits; with a letterbox the line sits in the bottom bar.
"""
import json
import shutil
import subprocess
from pathlib import Path

from .compose import (AI_LABEL, AIGC_COMMENT, FPS, SIZES, _normalise_clip, _still_clip, aigc_label, ass_time,
                      clean_line, copy_cover, ffmpeg, frame_align, publish_notes, srt_time, wrap)
from .project import Project, Shot, Workspace

DISSOLVE = frame_align(0.6)
FADE_BLACK = frame_align(1.0)
LINE_DELAY = 0.3           # a line's subtitle (and a TTS line) starts this far into its shot
LINE_TAIL = 0.2            # and ends this long before the shot does
LABEL_SECONDS = 3.0
NAME_AT, NAME_SECONDS = 5.0, 3.0
AMBIENCE_VOLUME = 0.7
EDGE_FADE = 0.12           # short fades on every placed sound, so cuts do not click


def timeline(project: Project) -> tuple[list[float], list[float], list[float]]:
    """(lengths, starts, overlaps) of every shot. overlaps[i] is how far shot i starts before shot
    i-1 ends (0 for a cut); it never exceeds 40% of either shot, so a short shot is not swallowed by
    its fades. Counted in whole frames, so starts do not drift."""
    shots = project.shots
    frames = [round(max(s.seconds, 1) * FPS) for s in shots]
    overlaps = [0]
    for i, s in enumerate(shots[1:], 1):
        want = {"dissolve": DISSOLVE, "black": FADE_BLACK}.get(s.transition, 0.0)
        overlaps.append(min(round(want * FPS), int(0.4 * min(frames[i - 1], frames[i]))) if want else 0)
    starts = [0]
    for i in range(1, len(frames)):
        starts.append(starts[-1] + frames[i - 1] - overlaps[i])
    return [f / FPS for f in frames], [f / FPS for f in starts], [f / FPS for f in overlaps]


def letterbox_bar(project: Project, w: int, h: int) -> int:
    """Height of each black bar when the picture is boxed to `project.letterbox` (0 for none)."""
    if not project.letterbox or w / h >= project.letterbox:
        return 0
    return round((h - w / project.letterbox) / 2)


CARD_BREAKS = "，。；、,.;\n"


def _esc(text: str) -> str:
    return text.replace("{", "（").replace("}", "）").replace("\n", "\\N")


def card_rows(text: str, width: int = 16) -> list[str]:
    """A quote card's rows: one clause per row. Calligraphy cards carry no commas or full stops, so
    the punctuation between clauses is dropped; question and exclamation marks stay."""
    rows, clause = [], ""
    for ch in text + "\n":
        if ch not in CARD_BREAKS:
            clause += ch
        elif clause.strip():
            rows += wrap(clause.strip(), width)
            clause = ""
    return rows


def _spans(lengths: list[float], starts: list[float]) -> list[tuple[float, float, float]]:
    """Per shot: (start, clear, end). clear is where the blend into the shot is over and end is
    where the blend out of it begins. A card's text shows from clear to end and every other text
    stops by end, so the texts of neighbouring shots never share the screen (libass would stack
    them)."""
    n = len(starts)
    return [(starts[i], max(starts[i], starts[i - 1] + lengths[i - 1]) if i else starts[i],
             starts[i + 1] if i + 1 < n else starts[i] + lengths[i]) for i in range(n)]


def build_ass(project: Project, font: str, card_font: str, lengths: list[float], starts: list[float],
              w: int, h: int, ai_label: bool = True) -> str:
    base, bar = min(w, h), letterbox_bar(project, w, h)
    portrait = h > w
    line_size, caption_size, label_size = round(base * 0.046), round(base * 0.032), round(base * 0.036)
    card_size, small = round(base * 0.075), round(base * 0.026)
    if bar:
        line_margin = max(8, (bar - line_size) // 2)       # inside the bottom bar
        caption_margin = bar + 14                           # just above it, on the picture
    else:
        line_margin = round(h * (0.2 if portrait else 0.06))
        caption_margin = line_margin + line_size + 12
    side, top = round(w * 0.04), bar + round(h * 0.04)
    total = starts[-1] + lengths[-1]
    fmt = ("Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
           "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
           "MarginR, MarginV, Encoding")

    def style(name, fontname, size, colour, bold, outline, align, ml, mr, mv, spacing=0):
        return (f"Style: {name},{fontname},{size},{colour},&H00FFFFFF,&H00101010,&H64000000,{bold},0,0,0,100,100,"
                f"{spacing},0,1,{outline},1,{align},{ml},{mr},{mv},1")

    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {w}", f"PlayResY: {h}", "WrapStyle: 2",
        "ScaledBorderAndShadow: yes", "", "[V4+ Styles]", fmt,
        style("Line", font, line_size, "&H00FFFFFF", 1, 2, 2, 60, 60, line_margin, 2),
        style("Caption", font, caption_size, "&H00E6E6E6", 0, 2, 2, 60, 60, caption_margin, 1),
        style("Chapter", font, label_size, "&H00F0F0F0", 1, 2, 7, side, side, top, 2),
        style("Name", font, small, "&H20F0F0F0", 0, 2, 7, side, side, top + label_size + 14, 2),
        style("Card", card_font, card_size, "&H00F4F4F4", 0, 2, 5, 120, 120, 0, 6),
        style("Notice", font, small, "&H50FFFFFF", 0, 1.5, 6, side, round(w * 0.025), 0),
        style("Credit", font, small, "&H20F0F0F0", 0, 2, 7, side, side, top),
        style("AILabel", font, small, "&H20FFFFFF", 0, 2, 9, 40, 40, bar + 20),
        "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    def event(layer: int, start: float, end: float, name: str, text: str) -> None:
        if text and end > start:
            lines.append(f"Dialogue: {layer},{ass_time(start)},{ass_time(end)},{name},,0,0,0,,{text}")

    label = ""
    for shot, length, (start, clear, end) in zip(project.shots, lengths, _spans(lengths, starts)):
        if shot.label and shot.label != label:
            event(1, start, min(start + LABEL_SECONDS, end), "Chapter", "{\\fad(300,300)}" + _esc(shot.label))
        label = shot.label or label
        if shot.is_card:
            text = "\\N".join(_esc(r) for r in card_rows(shot.text))
            event(2, clear, end, "Card", "{\\fad(400,400)}" + text)
            continue
        if shot.caption:
            event(0, start, end - 0.1, "Caption", _esc(shot.caption))
        if shot.voice == "dialogue" and shot.line:
            event(0, start + LINE_DELAY, end - LINE_TAIL, "Line", _esc(clean_line(shot.line)))
        elif shot.voice == "narration" and shot.narration:
            spoken = shot.audio_s + 0.1 if shot.audio_s else length
            event(0, start, min(start + spoken, end), "Line", "\\N".join(_esc(r) for r in wrap(clean_line(shot.narration), 24)))
    name = project.cover_title or project.title
    if name and total > NAME_AT + 1:
        event(1, NAME_AT, min(NAME_AT + NAME_SECONDS, total), "Name", "{\\fad(300,300)}" + _esc(f"《{name}》"))
    if project.notice:
        event(1, 0, total, "Notice", "\\N".join(_esc(ch) for ch in project.notice if not ch.isspace()))
    if project.credits:
        event(1, starts[-1], total, "Credit", "{\\fad(300,0)}" + "\\N".join(_esc(c) for c in project.credits))
    if ai_label:
        event(3, 0, total, "AILabel", AI_LABEL)
    return "\n".join(lines) + "\n"


def build_srt(project: Project, lengths: list[float], starts: list[float]) -> str:
    items = []
    for shot, length, (start, clear, end) in zip(project.shots, lengths, _spans(lengths, starts)):
        if shot.is_card:
            items.append((clear, end, shot.text))
        elif shot.voice == "dialogue" and shot.line:
            items.append((start + LINE_DELAY, end - LINE_TAIL, clean_line(shot.line)))
        elif shot.voice == "narration" and shot.narration:
            items.append((start, min(start + (shot.audio_s + 0.1 if shot.audio_s else length), end), clean_line(shot.narration)))
    return "\n".join(f"{i}\n{srt_time(a)} --> {srt_time(b)}\n{t}\n" for i, (a, b, t) in enumerate(items, 1))


def has_audio(path: Path) -> bool:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise RuntimeError("找不到 ffprobe")
    out = subprocess.run([ffprobe, "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "json",
                          str(path)], capture_output=True, text=True).stdout
    return bool((json.loads(out or "{}").get("streams")))


def _last_frame(clip: Path, out: Path) -> Path | None:
    ffmpeg(["-sseof", "-0.2", "-i", clip, "-frames:v", "1", "-update", "1", out])
    return out if out.exists() else None


def _card_clip(image: Path | None, out: Path, length: float, w: int, h: int) -> None:
    """A quote card's backdrop: the darkened last frame of another shot, or black."""
    frames = max(1, round(length * FPS))
    if image is None:
        ffmpeg(["-f", "lavfi", "-i", f"color=c=black:s={w}x{h}:r={FPS}", "-frames:v", frames, "-pix_fmt", "yuv420p",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", out])
        return
    vf = (f"scale={w}:{h}:force_original_aspect_ratio=increase:out_range=tv,crop={w}:{h},"
          f"eq=brightness=-0.18:saturation=0.55,fps={FPS},format=yuv420p")
    ffmpeg(["-loop", "1", "-i", image, "-vf", vf, "-frames:v", frames, "-an", "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "18", out])


def _picture(project: Project, ws: Workspace, shot: Shot, out: Path, length: float, w: int, h: int) -> None:
    if shot.is_card:
        over = project.shot(shot.over) if shot.over else None
        backdrop = None
        if over and ws.clip(over).exists():
            backdrop = _last_frame(ws.clip(over), ws.build / f"last_{over.id}.png")
        elif over and ws.keyframe(over).exists():
            backdrop = ws.keyframe(over)
        _card_clip(backdrop, out, length, w, h)
    elif ws.clip(shot).exists():
        _normalise_clip(ws.clip(shot), out, length, w, h)
    elif shot.needs_keyframe and ws.keyframe(shot).exists():
        _still_clip(ws.keyframe(shot), out, length, w, h)
    else:
        raise RuntimeError(f"{shot.id} 没有视频，也没有能代替它的关键帧：先跑 videos 阶段")


def _join(names: list[str], lengths: list[float], starts: list[float], overlaps: list[float],
          transitions: list[str], build: Path) -> None:
    if not any(overlaps):
        (build / "clips.txt").write_text("".join(f"file '{n}'\n" for n in names), encoding="utf-8")
        ffmpeg(["-f", "concat", "-safe", "0", "-i", "clips.txt", "-c", "copy", "video.mp4"], cwd=build)
        return
    # Cuts never go through xfade: two one-frame blends in a row end its output early (FFmpeg 9).
    args, graph, runs = [], [], []
    for i, name in enumerate(names):
        args += ["-i", name]
        graph.append(f"[{i}:v]fps={FPS},format=yuv420p,setsar=1,settb=AVTB[v{i}]")
        if i and not overlaps[i]:
            runs[-1].append(i)
        else:
            runs.append([i])
    labels = []
    for r, run in enumerate(runs):
        if len(run) == 1:
            labels.append(f"v{run[0]}")
        else:
            graph.append("".join(f"[v{i}]" for i in run) + f"concat=n={len(run)}:v=1:a=0[r{r}]")
            labels.append(f"r{r}")
    prev = labels[0]
    for r in range(1, len(runs)):
        i = runs[r][0]
        kind = "fadeblack" if transitions[i] == "black" else "fade"
        graph.append(f"[{prev}][{labels[r]}]xfade=transition={kind}:duration={overlaps[i]:.6f}:"
                     f"offset={starts[i]:.6f}[x{r}]")
        prev = f"x{r}"
    ffmpeg([*args, "-filter_complex", ";".join(graph), "-map", f"[{prev}]", "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "18", "video.mp4"], cwd=build)


def _bus(parts: list[tuple[Path, float, float]], total: float, out: Path) -> None:
    """Place (file, start, length) sounds on a silent track of `total` seconds."""
    args = ["-f", "lavfi", "-t", f"{total:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
    if not parts:
        ffmpeg([*args, out])
        return
    graph, labels = [], ["[0:a]"]
    for k, (path, start, length) in enumerate(parts, 1):
        args += ["-i", path.resolve()]
        fade_out = max(0.0, length - EDGE_FADE)
        delay = round(start * 1000)
        graph.append(f"[{k}:a]aresample=48000,aformat=channel_layouts=stereo,apad=whole_dur={length:.3f},"
                     f"atrim=0:{length:.3f},asetpts=PTS-STARTPTS,afade=t=in:d={EDGE_FADE},"
                     f"afade=t=out:st={fade_out:.3f}:d={EDGE_FADE},adelay={delay}|{delay}[p{k}]")
        labels.append(f"[p{k}]")
    graph.append(f"{''.join(labels)}amix=inputs={len(labels)}:duration=first:normalize=0[bus]")
    ffmpeg([*args, "-filter_complex", ";".join(graph), "-map", "[bus]", out])


def _sound(project: Project, ws: Workspace, lengths: list[float], starts: list[float], total: float,
           build: Path, bgm: Path | None, bgm_volume: float) -> None:
    speech, ambience = [], []
    for shot, start, length in zip(project.shots, starts, lengths):
        if shot.is_card or shot.voice == "none":
            continue
        clip = ws.clip(shot)
        clip_sound = clip if clip.exists() and has_audio(clip) else None
        spoken = ws.audio(shot) if ws.audio(shot).exists() else None   # narration, or a line on the TTS route
        if shot.voice == "narration":
            if spoken:
                speech.append((spoken, start, length))
        elif shot.voice == "dialogue" and spoken:
            speech.append((spoken, start + LINE_DELAY, length - LINE_DELAY))
            if clip_sound:
                ambience.append((clip_sound, start, length))
        elif shot.voice == "dialogue" and clip_sound:
            speech.append((clip_sound, start, length))
        elif shot.voice == "ambient" and clip_sound:
            ambience.append((clip_sound, start, length))
    _bus(speech, total, build / "speech.wav")
    _bus(ambience, total, build / "ambience.wav")
    if bgm:
        graph = (f"[2:a]aresample=48000,aformat=channel_layouts=stereo,volume={bgm_volume},atrim=0:{total:.3f}[b];"
                 f"[b][0:a]sidechaincompress=threshold=0.02:ratio=8:attack=20:release=500[bd];"
                 f"[1:a]volume={AMBIENCE_VOLUME}[amb];[0:a][amb][bd]amix=inputs=3:duration=first:normalize=0[m]")
        ffmpeg(["-i", "speech.wav", "-i", "ambience.wav", "-stream_loop", "-1", "-i", Path(bgm).resolve(),
                "-filter_complex", graph, "-map", "[m]", "mix.wav"], cwd=build)
    else:
        graph = f"[1:a]volume={AMBIENCE_VOLUME}[amb];[0:a][amb]amix=inputs=2:duration=first:normalize=0[m]"
        ffmpeg(["-i", "speech.wav", "-i", "ambience.wav", "-filter_complex", graph, "-map", "[m]", "mix.wav"], cwd=build)


def render(project: Project, ws: Workspace, font: str, font_file: Path, card_font: tuple[str, Path],
           bgm: Path | None = None, bgm_volume: float = 0.25, ai_label: bool = True) -> Path:
    if project.aspect not in SIZES:
        raise ValueError(f"不支持的画幅 {project.aspect}")
    w, h = SIZES[project.aspect]
    build, out_dir = ws.build, ws.out
    build.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    lengths, starts, overlaps = timeline(project)
    total = round(starts[-1] + lengths[-1], 3)

    names = []
    for shot, length in zip(project.shots, lengths):
        name = f"v_{shot.id}.mp4"
        _picture(project, ws, shot, build / name, length, w, h)
        names.append(name)
    _join(names, lengths, starts, overlaps, [s.transition for s in project.shots], build)
    _sound(project, ws, lengths, starts, total, build, bgm, bgm_volume)

    fonts_dir = build / "fonts"
    fonts_dir.mkdir(exist_ok=True)
    for path in {font_file, card_font[1]}:
        if not (fonts_dir / path.name).exists():
            shutil.copyfile(path, fonts_dir / path.name)  # not copy2: system fonts carry flags it cannot copy
    (build / "subs.ass").write_text(build_ass(project, font, card_font[0], lengths, starts, w, h, ai_label),
                                    encoding="utf-8")
    bar = letterbox_bar(project, w, h)
    boxes = (f"drawbox=x=0:y=0:w=iw:h={bar}:color=black:t=fill,drawbox=x=0:y=ih-{bar}:w=iw:h={bar}:color=black:t=fill,"
             if bar else "")
    final = out_dir / "final.mp4"
    ffmpeg(["-i", "video.mp4", "-i", "mix.wav", "-vf", f"{boxes}subtitles=subs.ass:fontsdir=fonts",
            "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-c:v", "libx264", "-preset", "medium", "-crf", "19",
            "-pix_fmt", "yuv420p", "-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709",
            "-color_trc", "bt709", "-r", FPS, "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-shortest",
            "-movflags", "+faststart+use_metadata_tags", "-metadata", f"AIGC={aigc_label(project)}",
            "-metadata", f"comment={AIGC_COMMENT}", final.resolve()], cwd=build)

    (out_dir / "final.srt").write_text(build_srt(project, lengths, starts), encoding="utf-8")
    (out_dir / "publish.md").write_text(publish_notes(project, final), encoding="utf-8")
    cover = next((ws.keyframe(s) for s in project.shots if ws.keyframe(s).exists()), None)
    if cover:
        copy_cover(cover, out_dir)
    return final
