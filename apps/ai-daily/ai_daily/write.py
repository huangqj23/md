"""写稿：头条和正文逐条调用 LLM；快讯一次调用；最后写今日看点、候选标题和封面短标题。
无 LLM 模式用原文节选代替摘要，并标上【待核对】，只用来试跑流程。"""
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from .llm import LLMError
from .models import TRACK_NAMES, Event
from .templating import prompt
from .textutil import clip, display_width, first_sentences
from .triage import Layout
from .verify import check_event

log = logging.getLogger(__name__)

STYLES = {
    "headline": "头条：写 3–4 段，共 250–450 字。依次写发生了什么、关键技术细节和数字、怎么用或适合什么场景、局限和还需要验证的地方。quotes 给 2–3 条。",
    "main": "正文条目：写 1 段，80–150 字。quotes 给 1–2 条。",
}
NO_LLM_FLAG = "无 LLM 模式：摘要是原文节选，需要改写"


def _as_list(v) -> list:
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def write_event(llm, ev: Event, style: str) -> None:
    it = ev.main_item
    user = prompt("write_user.md", ev=ev, it=it, others=[o for o in ev.items if o is not it][:4],
                  track=TRACK_NAMES.get(ev.track, ev.track), style=STYLES[style])
    try:
        data = llm.json(prompt("write_system.md"), user, max_tokens=8000)
    except LLMError as e:
        log.warning("写稿失败「%s」：%s", ev.title, e)
        extract_event(ev, style)
        ev.flags.append(f"LLM 写稿失败，需人工处理（{clip(str(e), 60)}）")
        return
    ev.headline = clip(str(data.get("title") or ev.title).strip(), 60)      # 只防异常长的输出，不截正常标题
    ev.paragraphs = [str(p).strip() for p in _as_list(data.get("paragraphs")) if str(p).strip()]
    ev.quotes = [q for q in _as_list(data.get("quotes")) if isinstance(q, dict) and q.get("source_quote")]
    ev.engineer_note = str(data.get("engineer_note") or "").strip()
    if not ev.paragraphs:
        extract_event(ev, style)
        ev.flags.append("模型没有给出正文，暂用原文节选")
    if data.get("insufficient"):
        ev.flags.append("模型认为原文信息不足")
    check_event(ev)


def extract_event(ev: Event, style: str) -> None:
    """无 LLM：原文开头几句当摘要。"""
    ev.headline = ev.headline or ev.title
    text = re.sub(r"(?m)^\s*#{1,6}\s+", "", ev.source_text or ev.main_item.summary)   # 模型卡里的 Markdown 标题符号
    ev.paragraphs = [first_sentences(text, 400 if style == "headline" else 160)]
    ev.quotes, ev.engineer_note = [], ""


def write_briefs(llm, events: list[Event]) -> None:
    if not events:
        return
    lines = "\n".join(f"[{i}] {e.title}｜{clip(e.main_item.summary, 200)}" for i, e in enumerate(events))
    user = f"{lines}\n\n请输出 json，格式示例：\n" + '{"briefs": [{"i": 0, "title": "短标题", "text": "一句话摘要"}]}'
    try:
        data = llm.json(prompt("brief_system.md"), user, max_tokens=8000)
    except LLMError as e:
        log.warning("快讯写作失败：%s", e)
        data = {}
    for b in _as_list(data.get("briefs")):
        if not isinstance(b, dict):
            continue
        try:
            ev = events[int(b.get("i"))]
        except (TypeError, ValueError, IndexError):
            continue
        ev.headline = clip(str(b.get("title") or ev.title), 40)
        ev.brief = clip(str(b.get("text") or ""), 120)
    for ev in events:
        if not ev.brief:
            ev.headline = ev.headline or clip(ev.title, 24)
            ev.brief = clip(ev.main_item.summary or ev.title, 80)


TITLE_FIT = 30                                                     # 消息列表里能显示完整
EDITOR_MAX = {"highlights": 60, "titles": 64, "cover_lines": 40}   # 只防异常长的输出；64 是公众号标题上限


def write_editor(llm, day: date, layout: Layout) -> dict:
    mmdd = f"{day:%m.%d}"
    h = layout.headline
    lines = [f"头条：{h.display_title}｜{clip(' '.join(h.paragraphs), 300)}", "其他条目："]
    lines += [f"- {e.display_title}｜{clip(' '.join(e.paragraphs), 120)}" for e in layout.written[1:]]
    user = f"日期：{day.isoformat()}\n" + "\n".join(lines) + "\n\n请输出 json，格式示例：\n" + \
        '{"highlights": ["…", "…", "…"], "titles": ["AI 早报 ' + mmdd + '｜…", "…", "…"], "cover_lines": ["…", "…", "…"]}'
    try:
        data = llm.json(prompt("editor_system.md", mmdd=mmdd), user, max_tokens=6000)
    except LLMError as e:
        log.warning("看点和标题生成失败：%s", e)
        data = {}
    fallback = extract_editor(day, layout)
    out = {k: [clip(str(x).strip(), EDITOR_MAX[k]) for x in _as_list(data.get(k))][:3] or fallback[k]
           for k in fallback}
    # 正文第一行用第一个不超过 30 字的标题；超长的不截断（截出来的半句不能用），留在候选里
    out["titles"].sort(key=lambda t: display_width(t) > TITLE_FIT)
    return out


def extract_editor(day: date, layout: Layout) -> dict:
    top = [e.display_title for e in layout.written[:3]]
    return {"highlights": top, "titles": [f"AI 早报 {day:%m.%d}｜{clip(layout.headline.display_title, 22)}"],
            "cover_lines": [clip(t, 14) for t in top]}


def write_all(llm, day: date, layout: Layout) -> dict:
    """llm 为 None 时走无 LLM 模式。返回 editor 结果（看点、候选标题、封面短标题）。"""
    if llm is None:
        for ev in layout.written:
            extract_event(ev, "headline" if ev is layout.headline else "main")
            ev.flags.append(NO_LLM_FLAG)
        for ev in layout.briefs:
            ev.headline, ev.brief = clip(ev.title, 24), clip(ev.main_item.summary or ev.title, 80)
        return extract_editor(day, layout)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda ev: write_event(llm, ev, "headline" if ev is layout.headline else "main"),
                      layout.written))
    write_briefs(llm, layout.briefs)
    return write_editor(llm, day, layout)
