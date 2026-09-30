"""渲染：正文（发布用）、审稿清单与备选（不发布）、封面 JSON。"""
import json
import re
from datetime import date, datetime
from pathlib import Path

from .models import TRACK_NAMES, TRACKS, Event
from .templating import template
from .textutil import clip
from .triage import Layout

ARTICLE_SUFFIX = "_AI日报"
REVIEW_SUFFIX = "_审稿与备选"
FLAG_MARK = "【待核对"


def paths(out_dir: Path, day: date) -> dict:
    stem = f"{day.isoformat()}{ARTICLE_SUFFIX}"
    return {"article": out_dir / f"{stem}.md", "review": out_dir / f"{day.isoformat()}{REVIEW_SUFFIX}.md",
            "cover": out_dir / "images" / f"{day.isoformat()}_cover.json", "stem": stem}


def issue_number(daily_dir: Path, day: date) -> int:
    """第几期：AI_Daily 下日期早于 day 的正文个数 + 1。"""
    pattern = re.compile(rf"^(\d{{4}}-\d{{2}}-\d{{2}}){ARTICLE_SUFFIX}\.md$")
    days = {m.group(1) for p in daily_dir.rglob(f"*{ARTICLE_SUFFIX}.md") if (m := pattern.match(p.name))}
    return len({d for d in days if d < day.isoformat()}) + 1


def _local_date(dt: datetime) -> str:
    return dt.astimezone().strftime("%m-%d")


def _others(ev: Event) -> list:
    """另见：主来源以外、信源名不重复的最多两个。"""
    seen, out = {ev.main_item.source_name}, []
    for it in ev.items:
        if it is ev.main_item or it.source_name in seen:
            continue
        seen.add(it.source_name)
        out.append(it)
    return out[:2]


def _tidy(md: str) -> str:
    md = "\n".join(line.rstrip() for line in md.splitlines())
    return re.sub(r"\n{3,}", "\n\n", md).strip() + "\n"


def render_article(layout: Layout, editor: dict) -> str:
    """头条 + 要闻（按分数平铺，不分栏）+ 快讯。"""
    return _tidy(template("daily.md.j2", title=editor["titles"][0], highlights=editor["highlights"],
                          headline=layout.headline, main=layout.main, briefs=layout.briefs,
                          local_date=_local_date, others=_others))


def render_review(day: date, stem: str, layout: Layout, editor: dict, article_md: str, *, stats: dict,
                  n_items: int, n_candidates: int, n_events: int, llm_usage: str) -> str:
    return _tidy(template(
        "review.md.j2", day=day.isoformat(), article_stem=stem, n_flags=article_md.count(FLAG_MARK),
        titles=editor["titles"], headline=layout.headline, backup=layout.backup,
        track_name=lambda t: TRACK_NAMES.get(t, "其他"), clip=clip,
        stats=sorted(stats.items(), key=lambda kv: str(kv[0])), n_items=n_items, n_candidates=n_candidates,
        n_events=n_events, llm_usage=llm_usage))


def cover_spec(day: date, issue: int, layout: Layout, editor: dict) -> dict:
    """给 _brand/hollis23/cover.py 的 daily 版式用；publish_titles 与论文笔记的约定一致。"""
    counts = [sum(1 for e in layout.written if e.track == t) for t in TRACKS]
    return {
        "layout": "daily",
        "date": day.isoformat(),
        "issue": issue,
        "tag": f"AI 早报 · {day.isoformat()} · 第 {issue} 期",
        "subtitle": f"AI 早报 {day:%m.%d}",
        "headlines": editor["cover_lines"][:3],
        "visual": {"type": "tracks", "rows": ["LLM", "Agent", "视觉"], "counts": counts,
                   "highlight": TRACKS.index(layout.headline.track) if layout.headline.track in TRACKS else None},
        "publish_titles": editor["titles"],
    }


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
