"""渲染：正文（发布用）、审稿清单与备选（不发布）、封面 JSON。"""
import base64
import io
import json
import os
import re
from datetime import date, datetime
from pathlib import Path

from PIL import Image

from .models import TRACK_NAMES, TRACKS, Event
from .templating import template
from .textutil import clip
from .triage import Layout

ARTICLE_SUFFIX = "_AI日报"
REVIEW_SUFFIX = "_审稿与备选"
EMBED_SUFFIX = "_内嵌图片版"          # ai-daily publish 调 watermark.py 生成的发布版
GENERATED = re.compile(r"^- 生成时间：(\S+)$", re.M)
FLAG_MARK = "【待核对"
# 图源现在接在图片下面第一段末尾的括号里（2026-10-04 起）；10-01 至 10-04 的旧稿是图片下面单独一行
# 居中的 HTML，预览页仍按图注处理。
CREDIT_LINE = re.compile(r"^<p\b[^>]*>(图源：.*)</p>$")


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
    """另见：主来源以外、信源名不重复的最多两个。只写一次“另见”，用顿号隔开（用户 2026-10-08 要求）。"""
    seen, out = {ev.main_item.source_name}, []
    for it in ev.items:
        if it is ev.main_item or it.source_name in seen:
            continue
        seen.add(it.source_name)
        out.append(it)
    return out[:2]


def badge(ev: Event) -> str:
    """来源行：可信度 · 主来源 · 日期 · 另见其他来源。"""
    it = ev.main_item
    line = f"`{ev.label}` · [{it.source_name}]({it.url})"
    if it.published:
        line += f" · {_local_date(it.published)}"
    others = _others(ev)
    if others:
        line += " · 另见 " + "、".join(f"[{o.source_name}]({o.url})" for o in others)
    return line


FOLLOW_LINE = "关注**Hollis的视觉大模型实战**：每天一份 AI 早报，只看一手来源；视觉与多模态大模型的论文精读和动手复现，也在这里。"
FOLLOW_ALT = "微信扫码关注 Hollis的视觉大模型实战"
# 文末关注卡片的两种写法：写稿时照抄的图片路径，finalize 内嵌之后的 <img>。按 alt 认，不认路径。
FOLLOW_CARD_MD = re.compile(r"!\[" + re.escape(FOLLOW_ALT) + r"\]\([^)\s]+\)")
FOLLOW_CARD_HTML = re.compile(r'<img alt="' + re.escape(FOLLOW_ALT) + r'" src="data:[^"]+"[^>]*>')
MAX_EMBED_BYTES = 60 * 1024          # 同 watermark.py 的 MAX_EMBED_KB：单张内嵌图再大，md 里显示不出来


def footer(card: str | None) -> str:
    """文末：版权说明、关注语（公众号名照二维码名片写）、关注卡片（相对正文的路径，finalize 再内嵌）。"""
    parts = ["---", "图片版权归原作者，出处见图注。", FOLLOW_LINE]
    if card:
        parts.append(f"![{FOLLOW_ALT}]({card})")
    return "\n\n".join(parts)


def has_footer(text: str) -> bool:
    """文末齐全：版权说明、关注语，加上关注卡片（还是路径，或已经内嵌）。"""
    return footer(None) in text and bool(FOLLOW_CARD_MD.search(text) or FOLLOW_CARD_HTML.search(text))


def card_tag(card: Path) -> str:
    """关注卡片内嵌成 <img>，写法同 watermark.py：WebP（先试无损），带 data-publish-only，md 同步时只发公众号。"""
    with Image.open(card) as im:
        for options in ({"lossless": True}, {"quality": 90}, {"quality": 80}):
            buf = io.BytesIO()
            im.save(buf, "WEBP", method=6, **options)
            if buf.tell() <= MAX_EMBED_BYTES:
                break
    data = base64.b64encode(buf.getvalue()).decode("ascii")
    return f'<img alt="{FOLLOW_ALT}" src="data:image/webp;base64,{data}" style="max-width:100%;" data-publish-only="wechat" />'


def embed_follow_card(text: str, card: Path) -> str:
    """文末的关注卡片换成内嵌的图。正文是导进 md 发布的：品牌目录不在导入时选的文件夹里，图找不到；
    上传图床又会丢掉文件名里的 _wxonly，公众号文末就缺了二维码（用户 2026-10-12 遇到）。内嵌加
    data-publish-only 两头都不怕。旧稿里指向二维码名片的那行也换成卡片；卡片文件不在时原样返回。"""
    if not card.is_file():
        return text
    tag = card_tag(card)
    return FOLLOW_CARD_HTML.sub(lambda _: tag, FOLLOW_CARD_MD.sub(lambda _: tag, text))


def _tidy(md: str) -> str:
    md = "\n".join(line.rstrip() for line in md.splitlines())
    return re.sub(r"\n{3,}", "\n\n", md).strip() + "\n"


def relative_ref(target: Path, from_dir: Path) -> str:
    """正文里引用 target 的写法：相对正文目录、正斜杠，Obsidian 和 watermark.py 都认。不同盘符时写绝对路径。"""
    try:
        return Path(os.path.relpath(target, from_dir)).as_posix()
    except ValueError:
        return target.as_posix()


def render_article(layout: Layout, editor: dict, card: str | None = None) -> str:
    """头条 + 要闻（按分数平铺，不分栏）+ 快讯；card 是文末关注卡片相对正文的路径。"""
    return _tidy(template("daily.md.j2", title=editor["titles"][0], highlights=editor["highlights"],
                          footer=footer(card), headline=layout.headline, main=layout.main, briefs=layout.briefs,
                          badge_line=badge))


def render_review(day: date, stem: str, layout: Layout, editor: dict, article_md: str, *, stats: dict,
                  n_items: int, n_candidates: int, n_events: int, llm_usage: str, generated: str = "",
                  notes: list[str] = (), by_claude: bool = False) -> str:
    """by_claude：选题和写稿是 Claude 做的（ai-daily prepare / materials / finalize），notes 是它的核对记录。"""
    return _tidy(template(
        "review.md.j2", day=day.isoformat(), article_stem=stem, n_flags=article_md.count(FLAG_MARK),
        titles=editor["titles"], headline=layout.headline, backup=layout.backup,
        track_name=lambda t: TRACK_NAMES.get(t, "其他"), clip=clip,
        stats=sorted(stats.items(), key=lambda kv: str(kv[0])), n_items=n_items, n_candidates=n_candidates,
        n_events=n_events, llm_usage=llm_usage, generated=generated, notes=list(notes), by_claude=by_claude))


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
