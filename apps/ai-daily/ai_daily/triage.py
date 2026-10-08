"""选题：候选条目 → 事件（合并同一件事、打分），丢掉旧闻，再按分数挑出头条、要闻、快讯和备选。

正文不分栏：要闻按分数平铺。主线（llm / agent / vision / other）仍然会归类，只用于封面统计。
"""
import logging
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

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


def candidate_line(i: int, it: Item, earlier: str = "") -> str:
    line = (f"[{i}] {it.kind} | {it.source_name} | {it.label} | {_when(it.published)} | {it.title} ｜ "
            f"{clip(it.summary, 160)} ｜ {_signals(it)}")
    return f"{line} ｜ 疑似旧闻：{earlier}" if earlier else line


_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9.\-]*[A-Za-z0-9]")
_COMMON = set("""with from that this your into over about after than what when will have more most just only their
they them here there which while were been being does could would should model models open source release releases
released launch launches launched introducing introduces introduce announces announced says said report reports
update updates version based using first best latest today week year agent agents data new the and for its now how
why are can our you all one two""".split())
OLD_NEWS_HOURS = 24        # 窗口开始前这么久以前就出现过的，才算“已经是旧闻”
RARE_DF = 3                # 当天所有标题里最多出现这么多次的名字才算少见


def _names(title: str) -> set[str]:
    """标题里像名字的词（产品名、型号）：大写开头或带数字，3 个字符以上，去掉常见词；统一成小写。"""
    return {t.lower() for t in _TOKEN.findall(title)
            if len(t) >= 3 and (t[0].isupper() or any(c.isdigit() for c in t)) and t.lower() not in _COMMON}


def earlier_matches(items: list[Item], before: list[tuple[datetime, str, str]], since: datetime) -> list[str]:
    """逐个候选找“疑似旧闻”：和窗口开始前一天以上的某条动态共有两个以上少见的名字（Kumo、Tabular）。
    少见按当天所有标题算（Gemini、Meta 这类到处都是的不算）；版本发布（gh:）每版都不同，两边都不比。
    返回和 items 对齐的说明，没有的是空串。"""
    df = Counter(n for title in [it.title for it in items] + [b[2] for b in before] for n in _names(title))

    def rare(title: str) -> set[str]:
        return {n for n in _names(title) if df[n] <= RARE_DF}

    old = [(b, rare(b[2])) for b in before
           if b[0] <= since - timedelta(hours=OLD_NEWS_HOURS) and not b[1].startswith("gh:")]
    out = []
    for it in items:
        names = rare(it.title) if it.kind != "release" else set()
        best = max(old, key=lambda o: len(names & o[1]), default=None) if names else None
        if best is not None and len(names & best[1]) >= 2:
            when, src, title = best[0]
            out.append(f"{_when(when)} {src}《{clip(title, 60)}》")
        else:
            out.append("")
    return out


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
                  lines=[candidate_line(i, it, hint)
                         for i, (it, hint) in enumerate(zip(items, earlier_matches(items, before, since)))])
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


_URL_DATE = re.compile(r"/(20\d\d)/(\d{1,2}|[A-Za-z]{3})/(\d{1,2})(?:/|$)")
_MONTHS = {m: i for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split(), 1)}


def url_latest(url: str) -> datetime | None:
    """链接里的发布日期（techcrunch.com/2026/09/29/…、/2026/oct/03/…）最晚对应的时刻：
    按最西的时区（UTC-12）算到当天结束，即 UTC 次日 12:00。没有日期返回 None。"""
    m = _URL_DATE.search(url)
    if not m:
        return None
    month = int(m.group(2)) if m.group(2).isdigit() else _MONTHS.get(m.group(2).lower())
    try:
        day = datetime(int(m.group(1)), month or 0, int(m.group(3)), tzinfo=timezone.utc)
    except ValueError:
        return None
    return day + timedelta(hours=36)


def _published(it: Item) -> datetime | None:
    """来源的发布时间。Techmeme 这类聚合源给的是它转发的时间，链接里的日期更早时以链接为准。"""
    by_url = url_latest(it.url)
    if it.published and by_url:
        return min(it.published, by_url)
    return it.published


def drop_stale(events: list[Event], since: datetime) -> list[Event]:
    """所有带时间的来源都早于窗口的事件是旧闻（例如上周发布、今天被转述），丢掉；
    有一个来源在窗口内、或来源不带时间（热门榜、投喂）的保留。"""
    keep = []
    for ev in events:
        dated = [_published(it) for it in ev.items if _published(it)]
        if dated and len(dated) == len(ev.items) and max(dated) < since:
            log.info("旧闻不收：%s（最新来源 %s）", ev.title, _when(max(dated)))
            continue
        keep.append(ev)
    return keep


def is_paper(ev: Event) -> bool:
    """纯论文：可信度标成论文，或者来源全是论文站（HF Daily Papers、arXiv）。
    实验室官方博客介绍的研究、带开源权重的发布不算。"""
    return ev.label == "论文" or all(it.kind == "paper" for it in ev.items)


def _source(ev: Event) -> str:
    return ev.main_item.source


@dataclass
class Layout:
    headline: Event
    main: list[Event]                 # 要闻，按分数排好
    briefs: list[Event]               # 快讯
    backup: list[Event]               # 备选（不进正文）
    max_papers: int = 1               # 头条 + 要闻、快讯里纯论文各最多几篇
    max_per_source: int = 3           # 头条 + 要闻、快讯里同一个主来源各最多几条

    @property
    def written(self) -> list[Event]:
        """需要抓原文、写正文、配图的事件：头条 + 要闻。"""
        return [self.headline] + self.main

    def fits(self, section: list[Event], ev: Event, leaving: Event | None = None) -> bool:
        """ev 放进 section（written 或 briefs）后，论文篇数和同源条数是否还在上限内；leaving 是同时要挪走的那条。"""
        others = [e for e in section if e is not leaving]
        if is_paper(ev) and sum(map(is_paper, others)) >= self.max_papers:
            return False
        return sum(_source(e) == _source(ev) for e in others) < self.max_per_source


def select(events: list[Event], *, main: int = 15, briefs: int = 10, backup: int = 20,
           max_papers: int = 1, max_per_source: int = 3) -> Layout:
    """按分数平铺，但不让正文被同一类内容占满（用户 2026-10-08：好几条都出自 HF Daily Papers、都是论文，
    显得重复，刚入门的读者只想看最新的 AI 新闻和进展）：
    - 头条不选纯论文，除非候选全是论文；
    - 头条 + 要闻、快讯里，纯论文各最多 max_papers 篇，同一个主来源各最多 max_per_source 条；
    - 超出上限的按分数顺延到快讯，再到备选。"""
    ranked = sorted(events, key=lambda e: e.score, reverse=True)
    if not ranked:
        raise ValueError("没有可用的候选事件（信源全部失败，或窗口内没有新动态）")
    head = next((e for e in ranked if not is_paper(e)), ranked[0])
    layout = Layout(head, [], [], [], max_papers, max_per_source)
    for ev in ranked:
        if ev is head:
            continue
        if len(layout.main) < main and layout.fits(layout.written, ev):
            layout.main.append(ev)
        elif len(layout.briefs) < briefs and layout.fits(layout.briefs, ev):
            layout.briefs.append(ev)
        elif len(layout.backup) < backup:
            layout.backup.append(ev)
    return layout
