"""文本小工具：HTML 转纯文本、截断、关键词匹配。"""
import re
import unicodedata
import warnings

from bs4 import BeautifulSoup, MarkupResemblesLocatorWarning

warnings.filterwarnings("ignore", category=MarkupResemblesLocatorWarning)


def html_to_text(html: str) -> str:
    if not html:
        return ""
    if "<" not in html and "&" not in html:
        return " ".join(html.split())
    text = BeautifulSoup(html, "lxml").get_text(" ", strip=True)
    return " ".join(text.split())


def clip(text: str, n: int) -> str:
    text = text or ""
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def display_width(text: str) -> float:
    """按“字”算的长度：汉字和全角标点算 1，英文字母、数字、空格算半个。"""
    return sum(1 if unicodedata.east_asian_width(c) in "WF" else 0.5 for c in text)


def compile_keywords(words) -> list[re.Pattern]:
    """纯 ASCII 的词：≤3 个字符按整词匹配（ai 不会命中 said），更长的按词首匹配（agent 命中 agents）；
    中文直接按子串匹配。"""
    patterns = []
    for w in words:
        w = str(w)
        if re.fullmatch(r"[\x00-\x7f]+", w):
            tail = r"(?![A-Za-z0-9])" if len(w) <= 3 else ""
            patterns.append(re.compile(rf"(?<![A-Za-z0-9]){re.escape(w)}{tail}", re.I))
        else:
            patterns.append(re.compile(re.escape(w)))
    return patterns


def matches_keywords(text: str, patterns) -> bool:
    return any(p.search(text or "") for p in patterns)


_SENT_END = re.compile(r"(?<=[。！？!?])|(?<=\.)\s+(?=[A-Z0-9“\"(])")


def first_sentences(text: str, max_chars: int) -> str:
    """取开头几句，不超过 max_chars（无 LLM 模式的摘要用）。"""
    out = ""
    for sent in _SENT_END.split(" ".join((text or "").split())):
        sent = sent.strip()
        if not sent:
            continue
        sep = "" if not out or out[-1] in "。！？" else " "     # 中文句子之间不加空格
        if out and len(out) + len(sep) + len(sent) > max_chars:
            break
        out = f"{out}{sep}{sent}"
    return clip(out, max_chars)
