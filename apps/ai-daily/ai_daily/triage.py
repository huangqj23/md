"""选题：候选条目 → 事件（合并同一件事、打分），丢掉旧闻，再按分数挑出头条、要闻、快讯和备选。

正文不分栏：要闻按分数平铺。主线（llm / agent / vision / other）仍然会归类，只用于封面统计。
"""
import logging
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime

from .classify import guess_track, heuristic_score
from .models import LABELS, TRACKS, Event, Item, pick_primary
from .templating import prompt
from .textutil import clip

log = logging.getLogger(__name__)


def prepare_candidates(items: list[Item], max_total: int = 300, per_source: int = 15) -> list[Item]:
    """按启发式分数排序后每个信源限量，控制在一次 LLM 调用看得完的数量。手动投喂的全收。"""
    counts, out = Counter(), []
    for it in sorted(items, key=heuristic_score, reverse=True):
        cap = 40 if it.kind == "social" else per_source
        if it.kind != "inbox" and counts[it.source] >= cap:
            continue
        counts[it.source] += 1
        out.append(it)
        if len(out) >= max_total:
            break
    return out


def _signals(it: Item) -> str:
    m, out = it.meta, []
    if m.get("points"):
        out.append(f"HN {m['points']} 分")
    if m.get("upvotes"):
        out.append(f"论文赞 {m['upvotes']}")
    if m.get("likes"):
        out.append(f"likes {m['likes']}")
    if m.get("stars_today"):
        out.append(f"今日 +{m['stars_today']} star")
    if m.get("also"):
        out.append("也见于 " + "、".join(dict.fromkeys(m["also"])))
    if it.kind == "inbox":
        out.append("手动投喂")
    return " ".join(out)


def _when(dt: datetime | None) -> str:
    return dt.astimezone().strftime("%m-%d %H:%M") if dt else "时间未知"


def candidate_line(i: int, it: Item) -> str:
    return (f"[{i}] {it.kind} | {it.source_name} | {it.label} | {_when(it.published)} | {it.title} ｜ "
            f"{clip(it.summary, 160)} ｜ {_signals(it)}")


def _index(value, n: int) -> int | None:
    try:
        i = int(value)
    except (TypeError, ValueError):
        return None
    return i if 0 <= i < n else None


def llm_events(llm, items: list[Item], day: date, recent: list[str], *, now: datetime, since: datetime,
               before: list[tuple[datetime, str, str]] = ()) -> list[Event]:
    """before：窗口开始前已经出现过的 (发布时间, 信源, 标题)，只用来识别转述的旧闻，不是候选。"""
    user = prompt("triage_user.md", day=day.isoformat(), recent=recent, now=_when(now), since=_when(since),
                  before=[f"{_when(t)} | {src} | {clip(title, 100)}" for t, src, title in before],
                  lines=[candidate_line(i, it) for i, it in enumerate(items)])
    data = llm.json(prompt("triage_system.md"), user, max_tokens=32000)
    events, used = [], set()
    for e in data.get("events") or []:
        if not isinstance(e, dict):
            continue
        idx = [i for i in (_index(v, len(items)) for v in e.get("items") or []) if i is not None and i not in used]
        if not idx:
            continue
        used.update(idx)
        members = [items[i] for i in idx]
        track = e.get("track") if e.get("track") in (*TRACKS, "other") else guess_track(members[0])
        label = e.get("label") if e.get("label") in LABELS else pick_primary(members).label
        try:
            score = float(e.get("score") or 0)
        except (TypeError, ValueError):
            score = 0.0
        events.append(Event(title=clip(str(e.get("title") or members[0].title), 60), items=members, track=track,
                            score=score, label=label, why=str(e.get("why") or ""),
                            follow_up=str(e.get("follow_up") or "")))
    events.sort(key=lambda ev: ev.score, reverse=True)
    return events


def drop_stale(events: list[Event], since: datetime) -> list[Event]:
    """所有带时间的来源都早于窗口的事件是旧闻（例如上周发布、今天被转述），丢掉；
    有一个来源在窗口内、或来源不带时间（热门榜、投喂）的保留。"""
    keep = []
    for ev in events:
        dated = [it.published for it in ev.items if it.published]
        if dated and len(dated) == len(ev.items) and max(dated) < since:
            log.info("旧闻不收：%s（最新来源 %s）", ev.title, _when(max(dated)))
            continue
        keep.append(ev)
    return keep


@dataclass
class Layout:
    headline: Event
    main: list[Event]                 # 要闻，按分数排好
    briefs: list[Event]               # 快讯
    backup: list[Event]               # 备选（不进正文）

    @property
    def written(self) -> list[Event]:
        """需要抓原文、写正文、配图的事件：头条 + 要闻。"""
        return [self.headline] + self.main


def select(events: list[Event], *, main: int = 15, briefs: int = 10, backup: int = 20) -> Layout:
    ranked = sorted(events, key=lambda e: e.score, reverse=True)
    if not ranked:
        raise ValueError("没有可用的候选事件（信源全部失败，或窗口内没有新动态）")
    rest = ranked[1:]
    return Layout(ranked[0], rest[:main], rest[main:main + briefs], rest[main + briefs:main + briefs + backup])
