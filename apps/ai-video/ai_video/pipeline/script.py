"""Script import. The topic, script, storyboard and fact check are written by Claude in the
conversation (the short-video skill) as one JSON file; no language model is called from here.

This module validates that file, fills a project from it, works out what a revised version
invalidates, and renders the storyboard table.
"""
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .project import TEMPLATES, Project, Shot

CHARS_PER_SECOND = 4.2     # comfortable Mandarin narration pace
CAMERAS = ("推近", "拉远", "横移", "环绕", "升起", "下降", "跟拍", "固定")
ASPECTS = ("9:16", "16:9", "1:1")
PREV = "prev"              # a ref to the previous shot's keyframe (scene continuity)
MAX_CHARACTERS_PER_SHOT = 2
CHARACTER_ID = re.compile(r"[a-z][a-z0-9_]*")
SHOT_ID = re.compile(r"[a-z0-9_]+")
SHOT_TEXT = ("narration", "image_prompt", "video_prompt")
PINYIN = re.compile(r"\(([a-z]+[1-5])\)")
VOICES = ("dialogue", "ambient", "narration", "none")
TRANSITIONS = ("cut", "dissolve", "black")
MODES = ("frame", "ref")
MAX_SCENES_PER_SHOT = 1
LINE_MAX, CAPTION_MAX = 12, 20   # benchmark hits: 4–10 characters a line; one short note above it
VOICE_NAMES = {"dialogue": "台词", "ambient": "环境声", "narration": "旁白", "none": "无"}
TRANSITION_NAMES = {"cut": "", "dissolve": "叠化", "black": "黑场"}


def shot_range(seconds: int) -> tuple[int, int]:
    low = max(3, round(seconds / 5.5))
    return low, max(low + 1, round(seconds / 3.8))


def chars_range(seconds: int) -> tuple[int, int]:
    return round(seconds * 3.7), round(seconds * CHARS_PER_SECOND)


def load_script(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except OSError as e:
        raise ValueError(f"读不了剧本文件 {path}：{e}") from e
    except ValueError as e:
        raise ValueError(f"剧本文件不是合法的 JSON：{e}") from e
    if not isinstance(data, dict):
        raise ValueError("剧本文件的最外层应该是一个对象 {...}")
    return data


def _text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _positive_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _declared(items, what: str, example: str, taken: set[str], errors: list[str]) -> set[str]:
    """Ids of declared characters or scenes; each needs a lowercase id and a `look`."""
    ids: set[str] = set()
    if not isinstance(items, list):
        errors.append(f"{what}要是列表")
        return ids
    name = {"characters": "角色", "scenes": "场景"}[what]
    for i, c in enumerate(items, 1):
        cid = c.get("id") if isinstance(c, dict) else None
        if not isinstance(cid, str) or not CHARACTER_ID.fullmatch(cid) or cid == PREV or re.fullmatch(r"s\d+", cid):
            errors.append(f"第 {i} 个{name}的 id 要用小写字母开头的英文（如 {example}），不能是 prev 或 s01 这种镜头编号")
            continue
        if cid in ids or cid in taken:
            errors.append(f"{name} id {cid} 重复")
        ids.add(cid)
        if not _text(c.get("look")):
            errors.append(f"{name} {cid} 缺少 look（{'外貌' if what == 'characters' else '环境'}描述）")
    return ids


def _card_errors(i: int, s: dict, drama: bool, clip_ids: set[str]) -> list[str]:
    if not drama:
        return [f"第 {i} 个镜头是金句卡（kind: card），只有剧情片（template: drama）能用"]
    errors = []
    if not _text(s.get("text")):
        errors.append(f"第 {i} 个镜头（金句卡）缺少 text")
    if not _positive_int(s.get("seconds")):
        errors.append(f"第 {i} 个镜头（金句卡）缺少 seconds（显示几秒）")
    over = s.get("over")
    if over and over not in clip_ids:
        errors.append(f"第 {i} 个镜头（金句卡）的 over「{over}」要是它前面的某个画面镜头")
    return errors


def _drama_errors(i: int, s: dict, char_ids: set[str]) -> list[str]:
    errors = []
    seconds = s.get("seconds")
    if not _positive_int(seconds) or seconds > 30:
        errors.append(f"第 {i} 个镜头缺少 seconds（1–30 的整数秒；视频模型最短 4 秒，更短的生成后裁掉）")
    voice = s.get("voice", "ambient")
    if voice not in VOICES:
        errors.append(f"第 {i} 个镜头的 voice 只能是 {' / '.join(VOICES)}")
    if voice == "dialogue":
        if not _text(s.get("line")):
            errors.append(f"第 {i} 个镜头是台词镜头（voice: dialogue），缺少 line")
        if s.get("speaker") not in char_ids:
            errors.append(f"第 {i} 个镜头的 speaker 要是 characters 里声明过的角色")
    if voice == "narration" and not _text(s.get("narration")):
        errors.append(f"第 {i} 个镜头是旁白镜头（voice: narration），缺少 narration")
    mode = s.get("mode", "frame")
    if mode not in MODES:
        errors.append(f"第 {i} 个镜头的 mode 只能是 frame 或 ref")
    if mode == "frame" and not _text(s.get("image_prompt")):
        errors.append(f"第 {i} 个镜头缺少 image_prompt（首帧画面）")
    refs = s.get("refs") if isinstance(s.get("refs"), list) else []
    if mode == "ref" and not [r for r in refs if r != PREV]:
        errors.append(f"第 {i} 个镜头是参考模式（mode: ref），refs 里至少要有一个角色或场景")
    if not _text(s.get("video_prompt")):
        errors.append(f"第 {i} 个镜头缺少 video_prompt")
    if s.get("transition", "cut") not in TRANSITIONS:
        errors.append(f"第 {i} 个镜头的 transition 只能是 {' / '.join(TRANSITIONS)}")
    if "hero" in s and not isinstance(s["hero"], bool):
        errors.append(f"第 {i} 个镜头的 hero 要是 true 或 false")
    return errors


def hard_errors(data: dict) -> list[str]:
    """Problems that make the script unusable; it is not imported until they are fixed."""
    errors = []
    if not _text(data.get("prompt")):
        errors.append("prompt（选题）不能为空")
    if data.get("aspect", "9:16") not in ASPECTS:
        errors.append(f"aspect 只能是 {' / '.join(ASPECTS)}")
    seconds = data.get("seconds", 60)
    if not _positive_int(seconds):
        errors.append("seconds（目标时长）要是正整数")
    template = data.get("template", "narration")
    if template not in TEMPLATES:
        errors.append(f"template 只能是 {' / '.join(TEMPLATES)}")
    drama = template == "drama"
    if drama:
        box = data.get("letterbox")
        if box not in (None, 0) and (isinstance(box, bool) or not isinstance(box, (int, float)) or not 1.5 <= box <= 3):
            errors.append("letterbox 是黑边里画面的宽高比，例如 2.35；不加黑边就不写")
        credits = data.get("credits") or []
        if not isinstance(credits, list) or not all(isinstance(c, str) for c in credits):
            errors.append("credits 要是字符串列表")

    char_ids = _declared(data.get("characters") or [], "characters", "shusheng", set(), errors)
    scene_ids = _declared(data.get("scenes") or [], "scenes", "changan_gate", char_ids, errors)

    shots = data.get("shots")
    if not isinstance(shots, list) or len(shots) < 2:
        return errors + ["shots 至少要有 2 个镜头"]
    shot_ids: set[str] = set()
    clip_ids: set[str] = set()
    prev_has_keyframe = False
    for i, s in enumerate(shots, 1):
        if not isinstance(s, dict):
            errors.append(f"第 {i} 个镜头不是对象")
            continue
        sid = s.get("id") or f"s{i:02d}"
        if not isinstance(sid, str) or not SHOT_ID.fullmatch(sid):
            errors.append(f"第 {i} 个镜头的 id 只能用小写字母、数字和下划线")
        elif sid in shot_ids:
            errors.append(f"镜头 id {sid} 重复")
        shot_ids.add(sid)
        kind = s.get("kind", "clip")
        if kind not in ("clip", "card"):
            errors.append(f"第 {i} 个镜头的 kind 只能是 clip 或 card")
            continue
        if kind == "card":
            errors += _card_errors(i, s, drama, clip_ids)
            prev_has_keyframe = False
            continue
        clip_ids.add(sid)
        if drama:
            errors += _drama_errors(i, s, char_ids)
        else:
            missing = [k for k in SHOT_TEXT if not _text(s.get(k))]
            if missing:
                errors.append(f"第 {i} 个镜头缺少 {', '.join(missing)}")
        camera = s.get("camera") or ""
        if camera and camera not in CAMERAS:
            errors.append(f"第 {i} 个镜头的 camera「{camera}」不在可选范围：{'、'.join(CAMERAS)}")
        if "photoreal" in s and not isinstance(s["photoreal"], bool):
            errors.append(f"第 {i} 个镜头的 photoreal 要是 true 或 false")
        has_keyframe = s.get("mode", "frame") == "frame"
        refs = s.get("refs") or []
        if not isinstance(refs, list) or not all(isinstance(r, str) for r in refs):
            errors.append(f"第 {i} 个镜头的 refs 要是字符串列表")
            prev_has_keyframe = has_keyframe
            continue
        unknown = [r for r in refs if r != PREV and r not in char_ids | scene_ids]
        if unknown:
            errors.append(f"第 {i} 个镜头引用了没有声明的角色或场景：{', '.join(unknown)}")
        if PREV in refs and i == 1:
            errors.append("第 1 个镜头不能引用 prev（没有上一个镜头）")
        elif PREV in refs and not prev_has_keyframe:
            errors.append(f"第 {i} 个镜头引用了 prev，但上一个镜头没有关键帧（金句卡或参考模式）")
        if len([r for r in refs if r in char_ids]) > MAX_CHARACTERS_PER_SHOT:
            errors.append(f"第 {i} 个镜头最多引用 {MAX_CHARACTERS_PER_SHOT} 个角色")
        if len([r for r in refs if r in scene_ids]) > MAX_SCENES_PER_SHOT:
            errors.append(f"第 {i} 个镜头最多引用 {MAX_SCENES_PER_SHOT} 个场景")
        if len(set(refs)) != len(refs):
            errors.append(f"第 {i} 个镜头的 refs 有重复")
        prev_has_keyframe = has_keyframe
    pronunciations = data.get("pronunciations") or {}
    if not isinstance(pronunciations, dict):
        errors.append("pronunciations 要是对象：{\"天姥\": \"(tian1)(mu3)\"}")
    else:
        for word, reading in pronunciations.items():
            groups = PINYIN.findall(reading) if isinstance(reading, str) else []
            if not groups or "".join(f"({g})" for g in groups) != reading or len(groups) != len(str(word)):
                errors.append(f"pronunciations 里「{word}」的读音要逐字写拼音加声调，如 (tian1)(mu3)")
    check = data.get("check")
    if check is not None and not isinstance(check, dict):
        errors.append("check 要是对象：{\"ok\": true, \"issues\": []}")
    return errors


def _narration_warnings(project: Project) -> list[str]:
    warnings = []
    low, high = shot_range(project.seconds)
    if not low <= len(project.shots) <= high:
        warnings.append(f"镜头数 {len(project.shots)} 不在建议的 {low}–{high} 之间")
    total = sum(len(s.narration) for s in project.shots)
    cmin, cmax = chars_range(project.seconds)
    if not cmin * 0.85 <= total <= cmax * 1.15:
        warnings.append(f"旁白共 {total} 字，按 {project.seconds} 秒建议 {cmin}–{cmax} 字")
    long = [s.id for s in project.shots if len(s.narration) > 30]
    if long:
        warnings.append(f"这些镜头旁白太长，一个镜头放不下：{', '.join(long)}")
    return warnings


def _drama_warnings(project: Project) -> list[str]:
    warnings = []
    shots = project.shots
    total = sum(s.seconds for s in shots)
    if not 0.8 * project.seconds <= total <= 1.2 * project.seconds:
        warnings.append(f"各镜头合计 {total} 秒，和目标时长 {project.seconds} 秒差得较多")
    for field_name, limit, what in (("line", LINE_MAX, "台词"), ("caption", CAPTION_MAX, "注释")):
        long = [s.id for s in shots if len(getattr(s, field_name)) > limit]
        if long:
            warnings.append(f"这些镜头的{what}超过 {limit} 字：{', '.join(long)}")
    spoken = sum(min(s.seconds, len(s.line or s.narration) / CHARS_PER_SECOND + 0.5)
                 for s in shots if s.voice in ("dialogue", "narration") and not s.is_card)
    if total and not 0.15 <= spoken / total <= 0.7:
        warnings.append(f"估算有人说话的时间约占 {spoken / total:.0%}，对标爆款在 20–60% 之间")
    first = shots[0]
    if not (first.line or first.caption or first.narration or (first.is_card and first.text)):
        warnings.append("第 1 个镜头既没有台词也没有注释：开头的钩子偏弱")
    lines_by = Counter(s.speaker for s in shots if s.voice == "dialogue")
    many = [cid for cid, n in lines_by.items() if n > 6]
    if many:
        warnings.append(f"这些角色的台词镜头超过 6 个，声音前后可能不一致：{', '.join(many)}")
    silent = [cid for cid in lines_by if not (project.character(cid) or {}).get("voice_desc")]
    if silent:
        warnings.append(f"这些角色有台词但没写 voice_desc，视频模型会自己挑声音，可能不合年纪：{', '.join(silent)}")
    short = [s.id for s in shots if not s.is_card and 0 < s.seconds < 4]
    if short:
        warnings.append(f"这些镜头不到 4 秒，会按 4 秒生成再裁掉（按 4 秒计费）：{', '.join(short)}")
    return warnings


def soft_warnings(project: Project) -> list[str]:
    """Deviations worth showing but not worth refusing the script for."""
    warnings = _drama_warnings(project) if project.is_drama else _narration_warnings(project)
    if not project.check:
        warnings.append("没有事实核查结果（check）：引用的诗句、史实和科学知识要先核对")
    return warnings


def _clean(value) -> str:
    return str(value or "").strip()


def _shot(i: int, s: dict, drama: bool) -> Shot:
    shot = Shot(id=_clean(s.get("id")) or f"s{i:02d}", narration=_clean(s.get("narration")),
                image_prompt=_clean(s.get("image_prompt")), video_prompt=_clean(s.get("video_prompt")),
                camera=_clean(s.get("camera")), photoreal=bool(s.get("photoreal")), refs=list(s.get("refs") or []))
    if drama:
        card = s.get("kind") == "card"
        shot.kind = "card" if card else "clip"
        shot.seconds = int(s.get("seconds") or 0)
        shot.voice = "none" if card else s.get("voice", "ambient")
        shot.speaker, shot.line = _clean(s.get("speaker")), _clean(s.get("line"))
        shot.caption, shot.label = _clean(s.get("caption")), _clean(s.get("label"))
        shot.transition, shot.mode = s.get("transition", "cut"), s.get("mode", "frame")
        shot.text, shot.over, shot.hero = _clean(s.get("text")), _clean(s.get("over")), bool(s.get("hero"))
    return shot


def apply_script(project: Project, data: dict) -> list[str]:
    """Fill the project's script fields from validated JSON; returns soft warnings."""
    project.template = data.get("template", "narration")
    project.title = _clean(data.get("title"))
    project.hook = _clean(data.get("hook"))
    project.cover_title = _clean(data.get("cover_title"))
    project.tags = [str(t).strip().lstrip("#") for t in data.get("tags") or [] if str(t).strip()]
    source = data.get("source") if isinstance(data.get("source"), dict) else {}
    project.source = {k: _clean(source.get(k)) for k in ("title", "author", "text") if source.get(k)}
    project.characters = [
        {"id": c["id"], "name": _clean(c.get("name")) or c["id"], "look": _clean(c["look"]),
         **{k: _clean(c[k]) for k in ("voice_desc", "tts_voice") if _clean(c.get(k))}}
        for c in data.get("characters") or []]
    project.scenes = [{"id": c["id"], "name": _clean(c.get("name")) or c["id"], "look": _clean(c["look"])}
                      for c in data.get("scenes") or []]
    project.letterbox = float(data.get("letterbox") or 0)
    project.notice = _clean(data.get("notice"))
    project.credits = [_clean(c) for c in data.get("credits") or [] if _clean(c)]
    project.pronunciations = {str(w): str(r) for w, r in (data.get("pronunciations") or {}).items()}
    check = data.get("check") or {}
    issues = [i for i in check.get("issues") or [] if isinstance(i, dict)]
    project.check = {"ok": bool(check.get("ok", not issues)), "issues": issues,
                     "checker": str(check.get("checker") or "Claude")} if check else {}
    project.shots = [_shot(i, s, project.is_drama) for i, s in enumerate(data["shots"], 1)]
    return soft_warnings(project)


@dataclass
class Changes:
    """What a revised script invalidates, by shot id (and character or scene id)."""
    audio: list[str] = field(default_factory=list)
    keyframes: list[str] = field(default_factory=list)
    clips: list[str] = field(default_factory=list)
    characters: list[str] = field(default_factory=list)
    scenes: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return any((self.audio, self.keyframes, self.clips, self.characters, self.scenes, self.removed))


def _changed_looks(old: list[dict], new: list[dict], restyled: bool) -> list[str]:
    before = {c["id"]: c.get("look") for c in old}
    return [c["id"] for c in new if restyled or (c["id"] in before and before[c["id"]] != c.get("look"))]


def diff_script(old: Project, new: Project) -> Changes:
    """Compare two versions of a project's script.

    Narration, or a drama line, speaker or sound source → new audio (and clip); first-frame prompt,
    refs or a referenced character's or scene's look → new keyframe (and clip); motion prompt,
    camera, photoreal, length, generation mode or the speaker's voice description → new clip only.
    Captions, labels, transitions and cards only change the compose step. A keyframe redone feeds
    the next shot through "prev", so that shot is redone too. A new aspect or style invalidates
    every picture.
    """
    ch = Changes()
    restyled = old.aspect != new.aspect or old.style != new.style
    ch.characters = _changed_looks(old.characters, new.characters, restyled)
    ch.scenes = _changed_looks(old.scenes, new.scenes, restyled)
    redrawn = set(ch.characters) | set(ch.scenes)
    old_cast = {c["id"]: c for c in old.characters}
    revoiced = {c["id"] for c in new.characters if c["id"] in old_cast
                and any(old_cast[c["id"]].get(k, "") != c.get(k, "") for k in ("voice_desc", "tts_voice"))}
    old_shots = {s.id: s for s in old.shots}
    old_prev = {s.id: old.shots[i - 1].id for i, s in enumerate(old.shots) if i}
    old_pron = old.pronunciations or {}
    changed_words = {w for w in set(old_pron) | set(new.pronunciations) if old_pron.get(w) != new.pronunciations.get(w)}
    redo_keyframe = False
    for i, shot in enumerate(new.shots):
        before = old_shots.get(shot.id)
        if before is None:
            redo_keyframe = True  # a new keyframe will be drawn, so a following "prev" shot changes too
            continue
        if shot.is_card and before.is_card:
            redo_keyframe = False  # text, length and backdrop are drawn at compose time
            continue
        reread = any(old_pron.get(w) != new.pronunciations.get(w) for w in changed_words if w in shot.narration)
        spoken = (before.line, before.speaker, before.voice) != (shot.line, shot.speaker, shot.voice)
        if before.narration != shot.narration or reread or spoken or (shot.voice == "dialogue" and shot.speaker in revoiced):
            ch.audio.append(shot.id)
        prev_changed = PREV in shot.refs and (redo_keyframe or old_prev.get(shot.id) != (new.shots[i - 1].id if i else None))
        keyframe = (restyled or before.image_prompt != shot.image_prompt or before.refs != shot.refs
                    or before.kind != shot.kind or any(r in redrawn for r in shot.refs) or prev_changed)
        if keyframe:
            ch.keyframes.append(shot.id)
        elif shot.id not in ch.audio and (
                (before.video_prompt, before.camera, before.photoreal, before.seconds, before.mode, before.hero)
                != (shot.video_prompt, shot.camera, shot.photoreal, shot.seconds, shot.mode, shot.hero)):
            ch.clips.append(shot.id)
        redo_keyframe = keyframe
    ch.removed = [sid for sid in old_shots if sid not in {s.id for s in new.shots}]
    return ch


def spoken_text(project: Project, shot: Shot) -> str:
    """What is heard or read in a shot, for listings: the narration, the speaker's line, or the card."""
    if shot.is_card:
        return f"【卡片】{shot.text}"
    if shot.voice == "dialogue":
        name = (project.character(shot.speaker) or {}).get("name", shot.speaker)
        return f"{name}：「{shot.line}」"
    return shot.narration


def script_text(project: Project) -> str:
    lines = [f"标题：{project.title}"]
    if project.source:
        lines.append(f"引用：{project.source.get('author', '')}《{project.source.get('title', '')}》{project.source.get('text', '')}")
    lines += [f"{s.id}：{spoken_text(project, s)}" for s in project.shots]
    return "\n".join(lines)


def storyboard_markdown(project: Project) -> str:
    lines = [f"# {project.title or project.prompt}", "", f"- 选题：{project.prompt}", f"- 钩子：{project.hook}",
             f"- 封面：{project.cover_title}", f"- 话题：{' '.join('#' + t for t in project.tags)}"]
    if project.source:
        s = project.source
        lines.append(f"- 引用：{s.get('author', '')}《{s.get('title', '')}》 {s.get('text', '')}")
    if project.is_drama:
        box = f"，黑边 {project.letterbox:g}:1" if project.letterbox else ""
        lines.append(f"- 形态：剧情片，{project.aspect}{box}，各镜头合计 {sum(s.seconds for s in project.shots)} 秒")
        if project.notice:
            lines.append(f"- 声明：{project.notice}")
    if project.characters:
        lines += ["", "## 角色", ""] + [
            f"- {c['id']}（{c.get('name', '')}）：{c.get('look', '')}"
            + (f"；声音：{c['voice_desc']}" if c.get("voice_desc") else "") for c in project.characters]
    if project.scenes:
        lines += ["", "## 场景", ""] + [f"- {c['id']}（{c.get('name', '')}）：{c.get('look', '')}" for c in project.scenes]
    issues = (project.check or {}).get("issues") or []
    if issues:
        lines += ["", "## 事实核查", ""] + [
            f"- {i.get('shot', '')}：{i.get('problem', '')} → {i.get('fix', '')}{'（已处理）' if i.get('resolved') else ''}"
            for i in issues]
    if project.is_drama:
        lines += ["", "## 分镜", "", "| 镜头 | 秒 | 章节 | 声音 | 台词 / 旁白 / 卡片 | 注释 | 首帧画面 | 动作 | 运镜 | 参考 | 方式 | 转场 |",
                  "|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for s in project.shots:
            how = "" if s.is_card else ("参考" if s.mode == "ref" else "首帧") + ("·写实" if s.photoreal else "") + ("·重点" if s.hero else "")
            frame = f"叠在 {s.over} 上" if s.is_card and s.over else s.image_prompt
            lines.append(f"| {s.id} | {s.seconds} | {s.label} | {VOICE_NAMES.get(s.voice, s.voice) if not s.is_card else '卡片'} | "
                         f"{spoken_text(project, s)} | {s.caption} | {frame} | {s.video_prompt} | {s.camera} | "
                         f"{' '.join(s.refs)} | {how} | {TRANSITION_NAMES.get(s.transition, s.transition)} |")
    else:
        lines += ["", "## 分镜", "", "| 镜头 | 旁白 | 首帧画面 | 动作 | 运镜 | 参考 | 写实人脸 |", "|---|---|---|---|---|---|---|"]
        for s in project.shots:
            lines.append(f"| {s.id} | {s.narration} | {s.image_prompt} | {s.video_prompt} | {s.camera} | "
                         f"{' '.join(s.refs)} | {'是' if s.photoreal else ''} |")
    lines += ["", "改剧本：改好剧本 JSON 后运行 `ai-video script <项目> <文件>`，只会重做改动过的镜头；"
                  "单独重做用 `ai-video make --redo s03`（或角色 id）。"]
    return "\n".join(lines) + "\n"
