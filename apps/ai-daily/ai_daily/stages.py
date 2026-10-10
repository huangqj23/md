"""不调用模型的分步流程：选题和写稿由 Claude Code 完成（用户 2026-10-10 要求），程序只做机械的部分。

1. prepare：采集 → 去重 → 合并同链接、同标题的条目 → 丢掉所有来源都早于窗口的旧闻，写 candidates.md；
2. Claude 看完 candidates.md，把选中的编号写进 plan.json；
3. materials：按 plan.json 抓原文、查显存、下配图，写 materials.md；同一天再跑只处理新换进来的条目；
4. Claude 照 materials.md 写正文；
5. finalize：检查正文，写审稿清单和封面，记下草稿里的链接和标题（之后几天去重用），
   再拼一张联系表（全部配图 + 封面），一次看完。

中间文件都在 data/work/<日期>/。
"""
import json
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, fields
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlsplit

from PIL import Image, ImageDraw

from . import history, publish, render, triage
from .classify import guess_track, heuristic_events
from .collect import collect_all
from .config import Settings
from .enrich import enrich_event
from .images import is_blank, save_image
from .models import LABELS, TRACKS, Event, Item, canonical_url, pick_primary
from .pipeline import dedupe, ensure_inbox, run_cover, window_start
from .store import Store
from .textutil import clip, display_width

log = logging.getLogger(__name__)

STATE, PLAN = "state.json", "plan.json"
HEADLINE_CHARS, MAIN_CHARS, BRIEF_CHARS = 6000, 2500, 600      # materials.md 里每条原文节选的长度
SUMMARY_CHARS = 140
TITLE_FIT = 30
DOMESTIC_MEDIA = ("qbitai", "infoq.cn", "leiphone", "ithome", "ifanr", "geekpark", "tmtpost", "36kr",
                  "jiqizhixin", "huxiu")
# 正文里不写“来源打不开”这类话，按拿到的内容直接写（用户 2026-10-10）
SOURCE_TALK = re.compile(r"打不开|无法访问|访问不了|找不到原文|所给原文|原文未|原文没有|原文只|无法核对|未能核对|没能核对")
HIGHLIGHT = re.compile(r"^> \d+\. (.+)$", re.M)
MAIN_HEAD = re.compile(r"^### \d+\. ", re.M)


class PlanError(ValueError):
    """plan.json 缺失或写得不对。"""


def work_dir(settings: Settings, day: date) -> Path:
    return settings.data_dir / "work" / day.isoformat()


# ---- state.json：条目里有 datetime，存成 {"__dt__": ISO 字符串}

def _encode(o):
    if isinstance(o, datetime):
        return {"__dt__": o.isoformat()}
    if isinstance(o, (set, frozenset)):
        return sorted(o)
    raise TypeError(f"存不进 state.json：{type(o).__name__}")


def _decode(d: dict):
    return datetime.fromisoformat(d["__dt__"]) if set(d) == {"__dt__"} else d


def _save_state(path: Path, state: dict) -> None:
    path.write_text(json.dumps(state, ensure_ascii=False, indent=1, default=_encode), encoding="utf-8")


def _load_state(path: Path) -> dict:
    if not path.is_file():
        raise PlanError(f"还没有 {path}：先运行 ai-daily prepare")
    return json.loads(path.read_text(encoding="utf-8"), object_hook=_decode)


def _event_dict(ev: Event) -> dict:
    return {"title": ev.title, "track": ev.track, "score": ev.score, "label": ev.label,
            "items": [{f.name: getattr(it, f.name) for f in fields(Item)} for it in ev.items]}


# ---- 1. prepare

@dataclass
class Prepared:
    candidates: Path
    plan: Path
    n_events: int
    since: datetime


def prepare(settings: Settings, sources: dict, *, day: date, now: datetime, http, force: bool = False) -> Prepared:
    out_dir = settings.daily_dir / f"{day:%Y-%m}"
    article = render.paths(out_dir, day)["article"]
    if article.exists():
        if not force:
            raise FileExistsError(f"{article} 已存在（可能已经改过）。确定要重来就加 --force，旧稿会移到 .bak")
        article.replace(article.with_name(article.name + ".bak"))
    work = work_dir(settings, day)
    work.mkdir(parents=True, exist_ok=True)
    if (work / PLAN).exists():                  # 重新 prepare 后编号会变，旧的 plan 不能再用
        (work / PLAN).replace(work / f"{PLAN}.bak")
    store = Store(settings.db_path)
    try:
        ensure_inbox(settings.inbox)
        history.sync_from_vault(store, settings.daily_dir, day)
        since = window_start(store, sources, now, day)
        before = store.seen_before(since, limit=400)
        known = store.repo_created()
        items, stats, snap = collect_all(http, sources, now=now, since=since, inbox_path=settings.inbox,
                                         openrouter_prev=store.previous_snapshot("openrouter", day),
                                         repo_created=known)
        if snap:
            store.save_snapshot("openrouter", day, snap)
        store.save_repo_created(known)
        store.mark_seen(items, now)
        published = store.published_urls(day)
        fresh = dedupe([it for it in items
                        if it.meta["first_seen"] >= since and canonical_url(it.url) not in published])
        candidates = triage.prepare_candidates(fresh)
        merged = heuristic_events(candidates)
        events = triage.drop_stale(merged, since)
        recent = store.recent_titles(day)
        store.mark_run(day, now)
    finally:
        store.close()
    hints = triage.earlier_matches([pick_primary(ev.items, ev.title) for ev in events], before, since)
    ids = {f"E{i}": ev for i, ev in enumerate(events, 1)}
    counts = {"items": len(items), "fresh": len(fresh), "candidates": len(candidates), "events": len(events),
              "stale": len(merged) - len(events)}
    state = {"day": day.isoformat(), "now": now, "since": since, "counts": counts,
             "stats": {str(k): v for k, v in stats.items()},
             "events": {eid: _event_dict(ev) for eid, ev in ids.items()}, "enriched": {}, "slots": {}}
    _save_state(work / STATE, state)
    path = work / "candidates.md"
    path.write_text(candidates_md(day, now, since, ids, hints, recent, counts, sources.get("layout") or {}),
                    encoding="utf-8")
    log.info("prepare：窗口 %s 之后，%d 个事件 → %s", triage._when(since), len(events), path)
    return Prepared(path, work / PLAN, len(events), since)


def candidates_md(day: date, now: datetime, since: datetime, ids: dict, hints: list[str], recent: list[str],
                  counts: dict, layout: dict) -> str:
    lines = [
        f"# {day} 候选事件（给 Claude 选题）", "",
        f"- 窗口：{triage._when(since)} 之后（北京时间），采集于 {triage._when(now)}",
        f"- 采集 {counts['items']} 条 → 窗口内新条目 {counts['fresh']} → 候选 {counts['candidates']} → "
        f"事件 {counts['events']}（另有 {counts['stale']} 个事件的来源全都早于窗口，已丢掉）",
        f"- 版面：头条 1 + 要闻 {layout.get('main', 15)} + 快讯 {layout.get('briefs', 10)} + 备选 "
        f"{layout.get('backup', 20)}；头条加要闻、快讯里，纯论文各最多 {layout.get('max_papers', 1)} 篇，"
        f"同一主来源各最多 {layout.get('max_per_source', 3)} 条",
        "- 选好后写同目录的 plan.json（格式见 SKILL.md）：E3+E9 把两个编号并成一个事件，url:<链接> 收候选里没有的新闻",
        "", "## 近 3 天已经写过（同一件事没有实质新进展就不选）", ""]
    lines += [f"- {t}" for t in recent] or ["（无）"]
    lines += ["", "## 候选（按启发式分数排，不代表重要性）", "",
              "编号 可信度/类型 | 主线猜测 | 发布时间 | 信源 | 标题 ｜ 摘要 ｜ 热度 ｜ 疑似旧闻", ""]
    for (eid, ev), hint in zip(ids.items(), hints):
        it = pick_primary(ev.items, ev.title)
        line = (f"[{eid}] {it.label}/{it.kind} | {ev.track} | {triage._when(triage._published(it))} | "
                f"{it.source_name} | {ev.title} ｜ {clip(it.summary, SUMMARY_CHARS)}")
        if sig := triage._signals(it):
            line += f" ｜ {sig}"
        if hint:
            line += f" ｜ 疑似旧闻：{hint}"
        lines.append(line)
        urls = [it.url] + [x.url for x in ev.items if x is not it]
        lines.append("    " + "  ".join(urls[:4]))
    return "\n".join(lines) + "\n"


# ---- 2. plan.json

def load_plan(work: Path) -> dict:
    path = work / PLAN
    if not path.is_file():
        raise PlanError(f"还没有 {path}：看完 candidates.md 后把选中的编号写进去")
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise PlanError(f"{path} 不是合法的 JSON：{e}") from e
    if not plan.get("headline") or not plan.get("main"):
        raise PlanError(f"{path} 至少要有 headline 和 main")
    return plan


def plan_slots(plan: dict) -> dict[str, str]:
    """槽位 → plan 里的写法。h 头条，n1… 要闻，b1… 快讯，x1… 备选；配图文件名用的就是槽位。"""
    slots = {"h": str(plan["headline"]).strip()}
    for prefix, key in (("n", "main"), ("b", "briefs"), ("x", "backup")):
        slots.update({f"{prefix}{i}": str(k).strip() for i, k in enumerate(plan.get(key) or [], 1)})
    keys = list(slots.values())
    dup = sorted({k for k in keys if keys.count(k) > 1})
    if dup:
        raise PlanError(f"plan.json 里有重复的条目：{'、'.join(dup)}")
    return slots


def _plan_event(key: str, state: dict) -> Event:
    if key.startswith("url:"):
        url = key[4:].strip()
        host = (urlsplit(url).hostname or url).removeprefix("www.")
        it = Item(source="manual", source_name=host, kind="media", title=url, url=url,
                  meta={"via": "manual", "first_seen": state["now"]})
        return Event(title=url, items=[it], track=guess_track(it), score=0.0, label=it.label)
    parts = []
    for k in key.split("+"):
        d = state["events"].get(k.strip())
        if d is None:
            raise PlanError(f"plan.json 里的 {k.strip()} 不在 candidates.md 里（重新 prepare 后编号会变）")
        parts.append(d)
    label = min((d["label"] for d in parts), key=lambda lb: LABELS.index(lb) if lb in LABELS else len(LABELS))
    return Event(title=parts[0]["title"], items=[Item(**x) for d in parts for x in d["items"]],
                 track=parts[0]["track"], score=max(d["score"] for d in parts), label=label)


def build(plan: dict, state: dict) -> tuple[triage.Layout, dict[str, str], dict[str, Event]]:
    slots = plan_slots(plan)
    events = {slot: _plan_event(key, state) for slot, key in slots.items()}
    for slot, key in slots.items():
        if (track := (plan.get("tracks") or {}).get(key)) in (*TRACKS, "other"):
            events[slot].track = track
    layout = triage.Layout(events["h"], [ev for s, ev in events.items() if s[0] == "n"],
                           [ev for s, ev in events.items() if s[0] == "b"],
                           [ev for s, ev in events.items() if s[0] == "x"])
    return layout, slots, events


def _image_slots(layout: triage.Layout) -> list[tuple[str, Event]]:
    return [("h", layout.headline)] + [(f"n{i}", ev) for i, ev in enumerate(layout.main, 1)]


# ---- 3. materials

@dataclass
class Materials:
    path: Path
    fetched: int
    images: int
    no_image: list[str]
    flags: dict[str, list[str]] = field(default_factory=dict)


def _snapshot(ev: Event, light: bool) -> dict:
    return {"light": light, "primary": ev.items.index(ev.main_item), "names": [it.source_name for it in ev.items],
            "source_text": ev.source_text, "image_candidates": [list(c) for c in ev.image_candidates],
            "vram": ev.vram, "flags": list(ev.flags)}


def _restore(ev: Event, snap: dict) -> None:
    for it, name in zip(ev.items, snap["names"]):
        it.source_name = name                    # 抓原文时可能换成了网站自己的名字
    ev.primary = ev.items[snap["primary"]]
    ev.source_text, ev.vram, ev.flags = snap["source_text"], snap["vram"], list(snap["flags"])
    ev.image_candidates = [tuple(c) for c in snap["image_candidates"]]


def materials(settings: Settings, sources: dict, *, day: date, http) -> Materials:
    work = work_dir(settings, day)
    state = _load_state(work / STATE)
    layout, slots, events = build(load_plan(work), state)
    cache = state["enriched"]
    jobs = [(slot, ev, slot[0] == "b") for slot, ev in events.items() if slot[0] != "x"]

    def one(job) -> bool:
        slot, ev, light = job
        snap = cache.get(slots[slot])
        if snap and (light or not snap["light"]):
            _restore(ev, snap)
            return False
        enrich_event(http, ev, light=light)
        return True

    with ThreadPoolExecutor(max_workers=8) as pool:
        fetched = list(pool.map(one, jobs))
    for (slot, ev, light), new in zip(jobs, fetched):
        if new:
            cache[slots[slot]] = _snapshot(ev, light)
    for ev in layout.backup:
        ev.primary = pick_primary(ev.items, ev.title)
    out_dir = settings.daily_dir / f"{day:%Y-%m}"
    _attach_images(http, layout, slots, state["slots"], out_dir, day,
                   (sources.get("layout") or {}).get("max_images", 16))
    _save_state(work / STATE, state)
    path = work / "materials.md"
    path.write_text(materials_md(day, layout, slots, state, settings, out_dir), encoding="utf-8")
    shots = _image_slots(layout)
    return Materials(path, sum(fetched), sum(1 for _, ev in shots if ev.image_path),
                     [s for s, ev in shots if not ev.image_path],
                     {s: ev.flags for s, ev in events.items() if ev.flags and s[0] != "x"})


def _attach_images(http, layout: triage.Layout, slots: dict, done: dict, out_dir: Path, day: date,
                   max_images: int) -> None:
    """头条和要闻各一张。槽位里还是同一条、图也还在的不重下（包括没下到、Claude 自己找来放进去的图）；
    换了条目的删掉旧图重下。同一张图不用两次。"""
    used, lock, jobs = set(), threading.Lock(), []
    for slot, ev in _image_slots(layout)[:max_images]:
        dest = out_dir / "images" / f"{day.isoformat()}_{slot}_nowm.png"
        prev = done.get(slot) or {}
        same = prev.get("event") == slots[slot]
        if same and dest.is_file():
            ev.image_path, ev.image_credit = f"images/{dest.name}", prev.get("image_credit") or ""
            used.add(prev.get("image_url"))
            continue
        if not same:
            dest.unlink(missing_ok=True)
        jobs.append((slot, ev, dest))

    def one(job):
        slot, ev, dest = job
        for url, credit, referer in ev.image_candidates:
            with lock:
                if url in used:
                    continue
                used.add(url)
            if save_image(http, url, dest, referer=referer):
                ev.image_path, ev.image_credit = f"images/{dest.name}", f"图源：{credit}"
                return slot, url
        return slot, None

    with ThreadPoolExecutor(max_workers=6) as pool:
        got = dict(pool.map(one, jobs))
    for slot, ev in _image_slots(layout)[:max_images]:
        if slot in got:
            done[slot] = {"event": slots[slot], "image_path": ev.image_path, "image_credit": ev.image_credit,
                          "image_url": got[slot]}


def materials_md(day: date, layout: triage.Layout, slots: dict, state: dict, settings: Settings,
                 out_dir: Path) -> str:
    lines = [f"# {day} 写稿素材（给 Claude）", "",
             f"- 正文写到：{render.paths(out_dir, day)['article']}",
             f"- 窗口：{triage._when(state['since'])} 之后（北京时间）",
             "- “来源行”“配图行”原样照抄，图源接在图片下面第一段末尾的括号里。“> 原文：”必须从下面的原文里逐字复制，"
             "找不到合适的句子就不写这一行。", ""]
    for slot, ev in _image_slots(layout):
        lines += _block(day, slot, slots[slot], ev, HEADLINE_CHARS if slot == "h" else MAIN_CHARS)
    lines += ["# 快讯", ""]
    for i, ev in enumerate(layout.briefs, 1):
        lines += _block(day, f"b{i}", slots[f"b{i}"], ev, BRIEF_CHARS, brief=True)
    lines += ["# 文末（原样复制到正文最后）", "", render.footer(render.relative_ref(settings.qr_code, out_dir)), ""]
    return "\n".join(lines)


def _block(day: date, slot: str, key: str, ev: Event, limit: int, brief: bool = False) -> list[str]:
    it = ev.main_item
    out = [f"## {slot} · {key} · {ev.track}", "", f"- 原标题：{ev.title}",
           f"- 发布：{triage._when(triage._published(it))}（北京时间）"]
    if brief:
        out.append(f"- 链接：[{it.source_name}]({it.url})")
    else:
        out.append(f"- 来源行：{render.badge(ev)}")
        if ev.image_path:
            out.append(f"- 配图行：![<标题>]({ev.image_path})　第一段末尾加：（{ev.image_credit or '图源：自己补上'}）")
        else:
            out.append(f"- 配图：没下到（{len(ev.image_candidates)} 个候选都不能用），"
                       f"另找一张存成 images/{day.isoformat()}_{slot}_nowm.png")
        if ev.vram:
            out.append(f"- 显存估算：{ev.vram}")
    out += [f"- 提示：{f}" for f in ev.flags]
    others = [x for x in ev.items if x is not it]
    if others:
        out.append("- 其他来源：" + "；".join(f"[{x.source_name}]({x.url}) {triage._when(triage._published(x))}"
                                         for x in others[:4]))
    return out + ["", "---- 原文 ----", clip(ev.source_text or it.summary or ev.title, limit), "---- 原文完 ----", ""]


# ---- 5. finalize

@dataclass
class Finalized:
    review: Path
    cover_json: Path
    cover_ok: bool
    contact: Path | None
    problems: list[str]
    warnings: list[str]


def _briefs(text: str) -> list[str]:
    m = re.search(r"^## 快讯\n(.*?)(?=^---$|\Z)", text, re.M | re.S)
    return re.findall(r"^- \*\*.+?\*\*：", m.group(1), re.M) if m else []


def _sections(text: str) -> list[tuple[str, str, bool]]:
    """头条和每条要闻：(标题, 正文, 是不是头条)。正文到下一个标题、“## ”栏目或文末分隔线为止。"""
    heads = list(re.finditer(r"^(## 头条｜|### \d+\. )(.+)$", text, re.M))
    out = []
    for i, m in enumerate(heads):
        nxt = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        body = text[m.end():nxt]
        if stop := re.search(r"^(?:## |---$)", body, re.M):
            body = body[:stop.start()]
        out.append((m.group(2).strip(), body, m.group(1).startswith("##")))
    return out


def check_article(article: Path, text: str, layout: triage.Layout, qr: str) -> tuple[list[str], list[str]]:
    """(必须改的, 提醒看一眼的)。"""
    problems, warnings = publish.check(article), []
    if not re.search(r"^## 头条｜", text, re.M):
        problems.append("缺少头条（“## 头条｜标题”）")
    if (n := len(HIGHLIGHT.findall(text))) != 3:
        problems.append(f"今日看点 {n} 条，要 3 条")
    if (n := len(MAIN_HEAD.findall(text))) != len(layout.main):
        problems.append(f"要闻 {n} 条，plan.json 里是 {len(layout.main)} 条")
    if (n := len(_briefs(text))) != len(layout.briefs):
        problems.append(f"快讯 {n} 条，plan.json 里是 {len(layout.briefs)} 条")
    if render.footer(qr) not in text:
        problems.append("文末的版权说明、关注语或二维码不完整：从 materials.md 末尾原样复制")
    for slot, ev in _image_slots(layout):
        if ev.image_path and f"]({ev.image_path})" not in text:
            problems.append(f"{slot} 下好的配图没用上：{ev.image_path}")
    sections = _sections(text)
    if no_image := [t for t, body, _ in sections if "![" not in body]:
        problems.append("头条和每条要闻都要有图，这几条还没有：" + "、".join(no_image))
    if no_note := [t for t, body, head in sections if not head and "- **工程师视角**：" not in body]:
        problems.append("这几条要闻缺“工程师视角”：" + "、".join(no_note))
    for ref in publish.IMAGE.findall(text):
        f = article.parent / ref
        if "_brand/" not in ref and not ref.startswith(("http://", "https://", "data:")) and f.is_file():
            with Image.open(f) as im:
                if is_blank(im):
                    problems.append(f"配图几乎是纯色（多半是视频首帧），换一张：{ref}")
    for _, url in publish.LINK.findall(text):
        if any(d in url for d in DOMESTIC_MEDIA):
            problems.append(f"用了国内媒体的链接：{url}")
    for line in text.splitlines():
        if SOURCE_TALK.search(line):
            warnings.append(f"别写“来源打不开 / 原文没有”这类话，按拿到的内容写：{clip(line.strip(), 60)}")
    m = publish.H1.search(text)
    if m and display_width(m.group(1)) > TITLE_FIT:
        warnings.append(f"标题超过 {TITLE_FIT} 字，消息列表里显示不全：{m.group(1)}")
    return problems, warnings


def finalize(settings: Settings, sources: dict, *, day: date) -> Finalized:
    work = work_dir(settings, day)
    state = _load_state(work / STATE)
    plan = load_plan(work)
    out_dir = settings.daily_dir / f"{day:%Y-%m}"
    p = render.paths(out_dir, day)
    if not p["article"].is_file():
        raise PlanError(f"还没有正文 {p['article']}：照 materials.md 写好再运行")
    text = p["article"].read_text(encoding="utf-8")
    layout, slots, events = build(plan, state)
    for slot, ev in events.items():
        if snap := state["enriched"].get(slots[slot]):
            _restore(ev, snap)
        else:
            ev.primary = pick_primary(ev.items, ev.title)
        img = state["slots"].get(slot) or {}
        if img.get("event") == slots[slot]:
            ev.image_path, ev.image_credit = img.get("image_path"), img.get("image_credit") or ""
    problems, warnings = check_article(p["article"], text, layout, render.relative_ref(settings.qr_code, out_dir))

    m = publish.H1.search(text)
    titles = [str(t) for t in plan.get("titles") or [] if t]
    if m and m.group(1) not in titles:
        titles.insert(0, m.group(1))
    highlights = HIGHLIGHT.findall(text)
    cover_lines = [clip(str(t), 14) for t in plan.get("cover_lines") or []][:3] or [clip(h, 14) for h in highlights[:3]]
    editor = {"titles": titles or [f"AI 早报 {day:%m.%d}"], "highlights": highlights, "cover_lines": cover_lines}
    counts = state["counts"]
    p["review"].write_text(render.render_review(
        day, p["stem"], layout, editor, text, stats=state["stats"], n_items=counts["fresh"],
        n_candidates=counts["candidates"], n_events=counts["events"], llm_usage="未调用（选题和写稿由 Claude 完成）",
        generated=state["now"].astimezone().isoformat(timespec="seconds"), notes=plan.get("notes") or [],
        by_claude=True), encoding="utf-8")
    render.write_json(p["cover"], render.cover_spec(day, render.issue_number(settings.daily_dir, day), layout, editor))
    cover_ok = run_cover(settings, p["cover"])
    store = Store(settings.db_path)
    try:
        store.mark_drafted(day, *publish.article_refs(text))
    finally:
        store.close()
    covers = [out_dir / "images" / f"{day.isoformat()}_cover_nowm_wechat.png",
              out_dir / "images" / f"{day.isoformat()}_cover_square_nowm.png"]
    contact = contact_sheet(text, p["article"].parent, covers, work / "contact.png")
    return Finalized(p["review"], p["cover"], cover_ok, contact, problems, warnings)


def contact_sheet(text: str, article_dir: Path, covers: list[Path], dest: Path) -> Path | None:
    """正文里的配图和封面拼成一张图（每张标上槽位），一次看完有没有纯色图、错图、封面文字被裁。
    公众号封面再单独放一张正中 1:1 的裁剪（转发卡片和主页显示的就是这一块）。"""
    def load(f: Path) -> Image.Image:
        with Image.open(f) as im:
            return im.convert("RGB")

    tiles = []
    for ref in publish.IMAGE.findall(text):
        f = article_dir / ref
        if "_brand/" in ref or ref.startswith(("http://", "https://", "data:")) or not f.is_file():
            continue
        parts = Path(ref).stem.split("_")
        tiles.append((parts[1] if len(parts) > 2 else Path(ref).stem, load(f)))
    for c in covers:
        if c.is_file():
            im = load(c)
            tiles.append((c.stem.split("_", 1)[-1], im))
            if c.stem.endswith("_wechat"):
                side = min(im.size)
                left = (im.width - side) // 2
                tiles.append(("wechat 1:1", im.crop((left, 0, left + side, side))))
    if not tiles:
        return None
    cols, w, h = 5, 320, 214
    sheet = Image.new("RGB", (cols * w, -(-len(tiles) // cols) * h), "white")
    draw = ImageDraw.Draw(sheet)
    for i, (label, im) in enumerate(tiles):
        x, y = (i % cols) * w, (i // cols) * h
        thumb = im.copy()
        thumb.thumbnail((w - 10, h - 28))
        sheet.paste(thumb, (x + 5, y + 24))
        draw.text((x + 6, y + 6), label, fill=(0, 0, 0))
    dest.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(dest)
    return dest
