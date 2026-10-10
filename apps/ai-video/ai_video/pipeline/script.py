"""Script import. The topic, script, storyboard and fact check are written by Claude in the
conversation (the short-video skill) as one JSON file; no language model is called from here.

This module validates that file, fills a project from it, works out what a revised version
invalidates, and renders the storyboard table.
"""
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .project import Project, Shot

CHARS_PER_SECOND = 4.2     # comfortable Mandarin narration pace
CAMERAS = ("推近", "拉远", "横移", "环绕", "升起", "下降", "跟拍", "固定")
ASPECTS = ("9:16", "16:9", "1:1")
PREV = "prev"              # a ref to the previous shot's keyframe (scene continuity)
MAX_CHARACTERS_PER_SHOT = 2
CHARACTER_ID = re.compile(r"[a-z][a-z0-9_]*")
SHOT_ID = re.compile(r"[a-z0-9_]+")
SHOT_TEXT = ("narration", "image_prompt", "video_prompt")
PINYIN = re.compile(r"\(([a-z]+[1-5])\)")


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


def hard_errors(data: dict) -> list[str]:
    """Problems that make the script unusable; it is not imported until they are fixed."""
    errors = []
    if not _text(data.get("prompt")):
        errors.append("prompt（选题）不能为空")
    if data.get("aspect", "9:16") not in ASPECTS:
        errors.append(f"aspect 只能是 {' / '.join(ASPECTS)}")
    seconds = data.get("seconds", 60)
    if isinstance(seconds, bool) or not isinstance(seconds, int) or seconds <= 0:
        errors.append("seconds（目标时长）要是正整数")

    char_ids = set()
    characters = data.get("characters") or []
    if not isinstance(characters, list):
        errors.append("characters 要是列表")
        characters = []
    for i, c in enumerate(characters, 1):
        cid = c.get("id") if isinstance(c, dict) else None
        if not isinstance(cid, str) or not CHARACTER_ID.fullmatch(cid) or cid == PREV or re.fullmatch(r"s\d+", cid):
            errors.append(f"第 {i} 个角色的 id 要用小写字母开头的英文（如 shusheng），不能是 prev 或 s01 这种镜头编号")
            continue
        if cid in char_ids:
            errors.append(f"角色 id {cid} 重复")
        char_ids.add(cid)
        if not _text(c.get("look")):
            errors.append(f"角色 {cid} 缺少 look（外貌描述）")

    shots = data.get("shots")
    if not isinstance(shots, list) or len(shots) < 2:
        return errors + ["shots 至少要有 2 个镜头"]
    shot_ids = set()
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
        missing = [k for k in SHOT_TEXT if not _text(s.get(k))]
        if missing:
            errors.append(f"第 {i} 个镜头缺少 {', '.join(missing)}")
        camera = s.get("camera") or ""
        if camera and camera not in CAMERAS:
            errors.append(f"第 {i} 个镜头的 camera「{camera}」不在可选范围：{'、'.join(CAMERAS)}")
        if "photoreal" in s and not isinstance(s["photoreal"], bool):
            errors.append(f"第 {i} 个镜头的 photoreal 要是 true 或 false")
        refs = s.get("refs") or []
        if not isinstance(refs, list) or not all(isinstance(r, str) for r in refs):
            errors.append(f"第 {i} 个镜头的 refs 要是字符串列表")
            continue
        unknown = [r for r in refs if r != PREV and r not in char_ids]
        if unknown:
            errors.append(f"第 {i} 个镜头引用了没有声明的角色：{', '.join(unknown)}")
        if PREV in refs and i == 1:
            errors.append("第 1 个镜头不能引用 prev（没有上一个镜头）")
        if len([r for r in refs if r != PREV]) > MAX_CHARACTERS_PER_SHOT:
            errors.append(f"第 {i} 个镜头最多引用 {MAX_CHARACTERS_PER_SHOT} 个角色")
        if len(set(refs)) != len(refs):
            errors.append(f"第 {i} 个镜头的 refs 有重复")
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


def soft_warnings(project: Project) -> list[str]:
    """Deviations worth showing but not worth refusing the script for."""
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
    if not project.check:
        warnings.append("没有事实核查结果（check）：引用的诗句、史实和科学知识要先核对")
    return warnings


def apply_script(project: Project, data: dict) -> list[str]:
    """Fill the project's script fields from validated JSON; returns soft warnings."""
    project.title = str(data.get("title") or "").strip()
    project.hook = str(data.get("hook") or "").strip()
    project.cover_title = str(data.get("cover_title") or "").strip()
    project.tags = [str(t).strip().lstrip("#") for t in data.get("tags") or [] if str(t).strip()]
    source = data.get("source") if isinstance(data.get("source"), dict) else {}
    project.source = {k: str(source.get(k) or "").strip() for k in ("title", "author", "text") if source.get(k)}
    project.characters = [{"id": c["id"], "name": str(c.get("name") or c["id"]).strip(), "look": str(c["look"]).strip()}
                          for c in data.get("characters") or []]
    project.pronunciations = {str(w): str(r) for w, r in (data.get("pronunciations") or {}).items()}
    check = data.get("check") or {}
    issues = [i for i in check.get("issues") or [] if isinstance(i, dict)]
    project.check = {"ok": bool(check.get("ok", not issues)), "issues": issues,
                     "checker": str(check.get("checker") or "Claude")} if check else {}
    project.shots = [
        Shot(id=str(s.get("id") or f"s{i:02d}"), narration=s["narration"].strip(), image_prompt=s["image_prompt"].strip(),
             video_prompt=s["video_prompt"].strip(), camera=str(s.get("camera") or "").strip(),
             photoreal=bool(s.get("photoreal")), refs=list(s.get("refs") or []))
        for i, s in enumerate(data["shots"], 1)
    ]
    return soft_warnings(project)


@dataclass
class Changes:
    """What a revised script invalidates, by shot id (and character id)."""
    audio: list[str] = field(default_factory=list)
    keyframes: list[str] = field(default_factory=list)
    clips: list[str] = field(default_factory=list)
    characters: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return any((self.audio, self.keyframes, self.clips, self.characters, self.removed))


def diff_script(old: Project, new: Project) -> Changes:
    """Compare two versions of a project's script.

    Narration → new audio (and clip); first-frame prompt, refs or a referenced character's look →
    new keyframe (and clip); motion prompt, camera or the photoreal flag → new clip only. A keyframe
    redone feeds the next shot through "prev", so that shot is redone too. A new aspect or style
    invalidates every picture.
    """
    ch = Changes()
    restyled = old.aspect != new.aspect or old.style != new.style
    old_looks = {c["id"]: c.get("look") for c in old.characters}
    for c in new.characters:
        if restyled or (c["id"] in old_looks and old_looks[c["id"]] != c.get("look")):
            ch.characters.append(c["id"])
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
        reread = any(old_pron.get(w) != new.pronunciations.get(w) for w in changed_words if w in shot.narration)
        if before.narration != shot.narration or reread:
            ch.audio.append(shot.id)
        prev_changed = PREV in shot.refs and (redo_keyframe or old_prev.get(shot.id) != (new.shots[i - 1].id if i else None))
        keyframe = (restyled or before.image_prompt != shot.image_prompt or before.refs != shot.refs
                    or any(r in ch.characters for r in shot.refs) or prev_changed)
        if keyframe:
            ch.keyframes.append(shot.id)
        elif (shot.id not in ch.audio and (before.video_prompt != shot.video_prompt or before.camera != shot.camera
                                           or before.photoreal != shot.photoreal)):
            ch.clips.append(shot.id)
        redo_keyframe = keyframe
    ch.removed = [sid for sid in old_shots if sid not in {s.id for s in new.shots}]
    return ch


def script_text(project: Project) -> str:
    lines = [f"标题：{project.title}"]
    if project.source:
        lines.append(f"引用：{project.source.get('author', '')}《{project.source.get('title', '')}》{project.source.get('text', '')}")
    lines += [f"{s.id}：{s.narration}" for s in project.shots]
    return "\n".join(lines)


def storyboard_markdown(project: Project) -> str:
    lines = [f"# {project.title or project.prompt}", "", f"- 选题：{project.prompt}", f"- 钩子：{project.hook}",
             f"- 封面：{project.cover_title}", f"- 话题：{' '.join('#' + t for t in project.tags)}"]
    if project.source:
        s = project.source
        lines.append(f"- 引用：{s.get('author', '')}《{s.get('title', '')}》 {s.get('text', '')}")
    if project.characters:
        lines += ["", "## 角色", ""] + [f"- {c['id']}（{c.get('name', '')}）：{c.get('look', '')}" for c in project.characters]
    issues = (project.check or {}).get("issues") or []
    if issues:
        lines += ["", "## 事实核查", ""] + [
            f"- {i.get('shot', '')}：{i.get('problem', '')} → {i.get('fix', '')}{'（已处理）' if i.get('resolved') else ''}"
            for i in issues]
    lines += ["", "## 分镜", "", "| 镜头 | 旁白 | 首帧画面 | 动作 | 运镜 | 参考 | 写实人脸 |", "|---|---|---|---|---|---|---|"]
    for s in project.shots:
        lines.append(f"| {s.id} | {s.narration} | {s.image_prompt} | {s.video_prompt} | {s.camera} | "
                     f"{' '.join(s.refs)} | {'是' if s.photoreal else ''} |")
    lines += ["", "改剧本：改好剧本 JSON 后运行 `ai-video script <项目> <文件>`，只会重做改动过的镜头；"
                  "单独重做用 `ai-video make --redo s03`（或角色 id）。"]
    return "\n".join(lines) + "\n"
