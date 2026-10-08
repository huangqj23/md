"""一次完整运行：采集 → 去重 → 选题 → 抓原文 → 写稿 → 配图 → 渲染 → 封面。"""
import logging
import shutil
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from . import history, publish, render, triage
from .classify import heuristic_events
from .collect import collect_all
from .collect.inbox import INBOX_HEADER
from .config import Settings
from .enrich import enrich_event
from .images import save_image
from .models import Item, canonical_url, pick_primary
from .store import Store
from .write import write_all

log = logging.getLogger(__name__)
LAST_RUN = "last_run"          # 2026-09-30 以前只记一个全局时间；现在按日期记（Store.mark_run）
HEAT_KEYS = ("points", "upvotes", "likes", "stars_today")


@dataclass
class RunResult:
    article: Path
    review: Path
    cover_json: Path
    cover_ok: bool
    n_flags: int


def dedupe(items: list[Item]) -> list[Item]:
    """同一链接出现在多个信源时只留可信度最高的一条，其余信源名记到 meta["also"]，热度（HN 分数等）并过去。"""
    best: dict[str, Item] = {}
    for it in items:
        key = canonical_url(it.url)
        cur = best.get(key)
        if cur is None:
            best[key] = it
            continue
        keep, drop = (it, cur) if pick_primary([it, cur]) is it else (cur, it)
        keep.meta.setdefault("also", []).append(drop.meta.get("via") or drop.source_name)
        for k in HEAT_KEYS:
            if drop.meta.get(k) and not keep.meta.get(k):
                keep.meta[k] = drop.meta[k]
        best[key] = keep
    return list(best.values())


def ensure_inbox(path: Path) -> None:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(INBOX_HEADER, encoding="utf-8")


def window_start(store: Store, sources: dict, now: datetime, day: date) -> datetime:
    """收“上一期之后”的动态：从前一期（更早日期）最后一次生成的时间算起，多留 2 小时防止接缝处漏条，
    夹在 [min_hours, max_hours] 之间。同一天重新生成用的是同一个起点，窗口不会越跑越短，
    也不会把上一期窗口里没选上的旧条目再捞回来。第一次运行用 default_hours。"""
    cfg = sources.get("window") or {}
    lo, hi = cfg.get("min_hours", 12), cfg.get("max_hours", 72)
    hours = cfg.get("default_hours", sources.get("window_hours", 26))
    prev = store.previous_run(day, legacy_key=LAST_RUN)
    if prev:
        hours = (now - prev).total_seconds() / 3600 + 2
    return now - timedelta(hours=min(max(hours, lo), hi))


THIN_CHARS = 600         # 主来源打不开时，原文（只剩信源摘要）短于这个长度就写不成一条要闻


def is_thin(ev) -> bool:
    """主来源网页打不开（付费墙、拒绝脚本访问），别的来源也补不上正文，只剩一两句摘要。"""
    return any(f.startswith("主来源网页打不开") and f.endswith("只能依据信源摘要") for f in ev.flags) \
        and len(ev.source_text or "") < THIN_CHARS


def swap_thin_events(layout: triage.Layout, enrich) -> list[str]:
    """要闻里写不出东西的事件挪到快讯（一句话够用），从快讯里按分数挑能抓到原文的补进要闻末尾。
    enrich 给候选抓原文；每条最多抓一次。返回挪走的事件标题。"""
    moved, tried = [], {id(e) for e in layout.main}       # 要闻已经抓过原文；挪到快讯的也不再抓
    for ev in [e for e in layout.main if is_thin(e)]:
        for cand in layout.briefs:
            if id(cand) in tried or not layout.fits(layout.written, cand, leaving=ev):    # 换进来也不能超论文和同源上限
                continue
            tried.add(id(cand))
            enrich(cand)
            if not is_thin(cand):
                layout.briefs.remove(cand)
                layout.main.remove(ev)
                layout.main.append(cand)
                layout.briefs.insert(0, ev)
                moved.append(ev.title)
                break
    return moved


def attach_images(http, layout: triage.Layout, out_dir: Path, day: date, max_images: int) -> None:
    """头条和要闻各配一张图：按候选顺序试，第一张能下载、尺寸够的就用；同一张图不在两条里重复出现。"""
    slots = [("h", layout.headline)] + [(f"n{i}", e) for i, e in enumerate(layout.main, 1)]
    slots = slots[:max_images]
    used, lock = set(), threading.Lock()

    def one(slot):
        slug, ev = slot
        for url, credit, referer in ev.image_candidates:
            with lock:
                if url in used:
                    continue
                used.add(url)
            dest = out_dir / "images" / f"{day.isoformat()}_{slug}_nowm.png"
            if save_image(http, url, dest, referer=referer):
                ev.image_path = f"images/{dest.name}"
                ev.image_credit = f"图源：{credit}"
                return

    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(one, slots))


def run_cover(settings: Settings, cover_json: Path) -> bool:
    script = settings.brand_dir / "cover.py"
    if not script.is_file():
        log.warning("找不到 %s，跳过封面", script)
        return False
    proc = subprocess.run([settings.brand_python, str(script), str(cover_json)], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    out = (proc.stdout + proc.stderr).strip()
    if proc.returncode != 0:
        log.warning("封面生成失败：%s", out[-500:])
        return False
    log.info("封面：\n%s", out)
    return True


def run(settings: Settings, sources: dict, *, day: date, now: datetime, http, llm=None, out_dir: Path | None = None,
        force: bool = False, images: bool = True, cover: bool = True, db_path: Path | None = None) -> RunResult:
    """llm 是 LLMPair（选题、写稿各一个模型）；为 None 时走无 LLM 模式（启发式选题 + 原文节选），只用来试跑流程。"""
    out_dir = Path(out_dir) if out_dir else settings.daily_dir / f"{day:%Y-%m}"
    p = render.paths(out_dir, day)
    if p["article"].exists():
        if not force:
            raise FileExistsError(f"{p['article']} 已存在（可能已经改过）。确定要重新生成就加 --force，旧稿会备份成 .bak")
        shutil.copy2(p["article"], p["article"].with_name(p["article"].name + ".bak"))

    store = Store(db_path or settings.db_path)
    try:
        ensure_inbox(settings.inbox)
        history.sync_from_vault(store, settings.daily_dir, day)
        since = window_start(store, sources, now, day)
        # 以前的运行见过、发布在窗口前的：给选题识别旧闻。忙的时候 36 小时有两三百条，200 条会把最早的发布截掉
        before = store.seen_before(since, limit=400)
        known_repos = store.repo_created()
        items, stats, snap = collect_all(http, sources, now=now, since=since, inbox_path=settings.inbox,
                                         openrouter_prev=store.previous_snapshot("openrouter", day),
                                         repo_created=known_repos)
        if snap:
            store.save_snapshot("openrouter", day, snap)
        store.save_repo_created(known_repos)
        store.mark_seen(items, now)
        published = store.published_urls(day)
        fresh = dedupe([it for it in items
                        if it.meta["first_seen"] >= since and canonical_url(it.url) not in published])
        candidates = triage.prepare_candidates(fresh)
        log.info("窗口 %s 起：采集 %d 条，窗口内新条目 %d 条，送去选题 %d 条",
                 since.astimezone().strftime("%m-%d %H:%M"), len(items), len(fresh), len(candidates))

        if llm is not None:
            events = triage.llm_events(llm.triage, candidates, day, store.recent_titles(day), now=now, since=since,
                                       before=before)
        else:
            events = heuristic_events(candidates)
        events = triage.drop_stale(events, since)
        cfg = sources.get("layout") or {}
        layout = triage.select(events, main=cfg.get("main", 15), briefs=cfg.get("briefs", 10),
                               backup=cfg.get("backup", 20), max_papers=cfg.get("max_papers", 1),
                               max_per_source=cfg.get("max_per_source", 3))
        log.info("选题：头条「%s」，要闻 %d 条，快讯 %d 条，备选 %d 条", layout.headline.title,
                 len(layout.main), len(layout.briefs), len(layout.backup))

        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(lambda ev: enrich_event(http, ev), layout.written))
        for title in swap_thin_events(layout, lambda ev: enrich_event(http, ev)):
            log.info("原文只剩一两句摘要，改放快讯：%s", title)
        for ev in layout.briefs + layout.backup:
            ev.primary = pick_primary(ev.items, ev.title)
        editor = write_all(llm.write if llm else None, day, layout)
        if images:
            attach_images(http, layout, out_dir, day, cfg.get("max_images", 16))
            log.info("配图：%d / %d 条有图", sum(1 for e in layout.written if e.image_path), len(layout.written))

        article_md = render.render_article(layout, editor, qr_code=render.relative_ref(settings.qr_code, out_dir))
        usage = "未使用（--no-llm）" if llm is None else llm.usage_summary()
        review_md = render.render_review(day, p["stem"], layout, editor, article_md, stats=stats,
                                         n_items=len(fresh), n_candidates=len(candidates), n_events=len(events),
                                         llm_usage=usage or "0",
                                         generated=now.astimezone().isoformat(timespec="seconds"))
        out_dir.mkdir(parents=True, exist_ok=True)
        p["article"].write_text(article_md, encoding="utf-8")
        p["review"].write_text(review_md, encoding="utf-8")
        store.mark_drafted(day, *publish.article_refs(article_md))
        spec = render.cover_spec(day, render.issue_number(settings.daily_dir, day), layout, editor)
        render.write_json(p["cover"], spec)
        cover_ok = run_cover(settings, p["cover"]) if cover else False
        store.mark_run(day, now)
        return RunResult(p["article"], p["review"], p["cover"], cover_ok, article_md.count(render.FLAG_MARK))
    finally:
        store.close()
