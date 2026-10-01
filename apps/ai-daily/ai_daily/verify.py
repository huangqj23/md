"""出处核对：模型给的“原文句”要能在抓到的原文里找到；正文里的数字要在原文里出现过。
核对不上的不删内容，只给事件加 flags，渲染时标成【待核对】交给人工。"""
import re
import unicodedata
from decimal import Decimal
from difflib import SequenceMatcher

from .models import Event
from .textutil import clip

_QUOTES = str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'", "「": '"', "」": '"'})


def normalize(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").translate(_QUOTES).lower()
    s = re.sub(r"[‐-―−]", "-", s)
    return re.sub(r"\s+", " ", s).strip()


def quote_found(quote: str, source: str, threshold: float = 0.9) -> bool:
    """逐字（归一化空白、引号、全半角后）出现即通过；否则在最长公共片段附近取等长窗口，相似度 ≥ threshold 也算通过。"""
    q, src = normalize(quote), normalize(source)
    if not q:
        return False
    if q in src:
        return True
    if len(q) < 12:
        return False
    m = SequenceMatcher(None, src, q, autojunk=False).find_longest_match(0, len(src), 0, len(q))
    if m.size == 0:
        return False
    start = max(0, m.a - m.b)
    window = src[start:start + len(q) + 10]
    return SequenceMatcher(None, window, q, autojunk=False).ratio() >= threshold


# 逗号只当千分位（后面正好 3 位数字）：中文逗号经 NFKC 也会变成 ","，"$2，2026" 不能读成一个数。
# 数后面的数量级单位一起取出来：英文原文的 $30 billion 在中文里写成 300 亿，按数值比
_EXP = {"万亿": 12, "亿": 8, "万": 4, "千": 3, "trillion": 12, "billion": 9, "million": 6, "thousand": 3,
        "bn": 9, "t": 12, "b": 9, "m": 6, "k": 3}
_NUM = re.compile(r"(\d+(?:,\d{3}(?!\d))*(?:\.\d+)?)\s*(万亿|亿|万|千|trillion|billion|million|thousand|bn|[tbmk])?"
                  r"(?![a-z])")


def _value(num: str, unit: str) -> Decimal:
    return Decimal(num.replace(",", "")).scaleb(_EXP[unit]).normalize()


_MONTHS = ("january|jan", "february|feb", "march|mar", "april|apr", "may", "june|jun", "july|jul",
           "august|aug", "september|sept|sep", "october|oct", "november|nov", "december|dec")


def _month_in(source: str, n: str) -> bool:
    """中文写“11 月”、英文原文写 November / Nov。"""
    return n.isdigit() and 1 <= int(n) <= 12 and re.search(rf"\b(?:{_MONTHS[int(n) - 1]})\b", source) is not None


def missing_numbers(text: str, source: str) -> list[str]:
    """正文里的数字（两位以上或带小数，年份除外）在原文里找不到的。带数量级的（亿、billion 等）按数值比，
    “11 月”这样的月份对原文里的英文月份名。"""
    src = normalize(source).replace(",", "")
    src_values = {_value(n, u) for n, u in _NUM.findall(normalize(source)) if u}
    norm = normalize(text)
    out = set()
    for m in _NUM.finditer(norm):
        n, unit = m.groups()
        bare = n.replace(",", "")
        if len(bare.replace(".", "")) < 2 or re.fullmatch(r"(19|20)\d\d", bare):
            continue
        if bare in src or (unit and _value(n, unit) in src_values):
            continue
        if norm[m.end():].lstrip().startswith("月") and _month_in(src, bare):
            continue
        out.add(n)
    return sorted(out)


def untranslated(paragraph: str) -> bool:
    """写稿偶尔把原文的英文句子原样当成一段：字母里汉字不到一成就算（中英混排的正常段落在三成左右）。"""
    letters = [c for c in paragraph if c.isalpha()]
    cjk = sum(1 for c in letters if "一" <= c <= "鿿")
    return len(letters) >= 40 and cjk / len(letters) < 0.1


def check_event(ev: Event) -> None:
    for p in ev.paragraphs:
        if untranslated(p):
            ev.flags.append(f"有一段没翻译成中文（“{clip(p, 30)}”），改写或删掉")
    body = " ".join(ev.paragraphs + ([ev.engineer_note] if ev.engineer_note else []))
    verified = [q for q in ev.quotes if quote_found(str(q.get("source_quote", "")), ev.source_text)]
    if len(verified) < len(ev.quotes):
        ev.flags.append(f"{len(ev.quotes) - len(verified)} 条原文句在来源里没找到，已去掉")
    for q in verified:          # 原文句里的换行会把 Markdown 引用块截断
        q["source_quote"] = clip(" ".join(str(q["source_quote"]).split()), 200)
    ev.quotes = verified
    titles = " ".join(it.title for it in ev.items)              # 版本号常常只在标题里（v5.18.0）
    missing = missing_numbers(body, f"{ev.source_text} {ev.vram} {titles}")
    if missing:
        ev.flags.append("这些数字在原文里没找到：" + "、".join(missing[:8]))
