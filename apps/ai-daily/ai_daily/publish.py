"""发布前检查 + 生成内嵌版（调用 _brand/hollis23/watermark.py），并记录已发布的链接供之后去重。"""
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import httpx

from .render import FLAG_MARK

PLACEHOLDER = "【我的看法：待写】"
AI_NOTE = "由 AI 辅助"
WECHAT_TITLE_MAX = 64
H1 = re.compile(r"^#\s+(.+?)\s*$", re.M)
LINK = re.compile(r"(?<!!)\[([^\]]*)\]\((https?://[^)\s]+)\)")
IMAGE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)\)")


def check(article: Path) -> list[str]:
    """必须解决的问题；返回空列表表示可以发。"""
    text = article.read_text(encoding="utf-8")
    problems = []
    if PLACEHOLDER in text:
        problems.append("头条的“我的看法”还没写（替换【我的看法：待写】）")
    if (n := text.count(FLAG_MARK)) > 0:
        problems.append(f"还有 {n} 处【待核对】没处理：核对原文、改好正文后删掉标记")
    if AI_NOTE not in text:
        problems.append("文末缺少 AI 辅助声明")
    m = H1.search(text)
    if not m:
        problems.append("缺少一级标题（md 会把第一个一级标题当作发布标题）")
    elif len(m.group(1)) > WECHAT_TITLE_MAX:
        problems.append(f"标题 {len(m.group(1))} 字，超过公众号上限 {WECHAT_TITLE_MAX} 字")
    for ref in IMAGE.findall(text):
        if not ref.startswith(("http://", "https://", "data:")) and not (article.parent / ref).is_file():
            problems.append(f"图片不存在：{ref}")
    return problems


def links(article: Path) -> list[tuple[str, str]]:
    return [(url, label) for label, url in LINK.findall(article.read_text(encoding="utf-8"))]


def check_links(http: httpx.Client, urls: list[str]) -> list[str]:
    """打不开的链接（只提醒，不拦：不少网站会拒绝脚本访问）。"""
    def probe(url):
        try:
            resp = http.get(url)
            return None if resp.status_code < 400 else f"{resp.status_code} {url}"
        except httpx.HTTPError as e:
            return f"{type(e).__name__} {url}"
    with ThreadPoolExecutor(max_workers=8) as pool:
        return [r for r in pool.map(probe, sorted(set(urls))) if r]


def run_watermark(brand_python: str, script: Path, article: Path) -> tuple[bool, str]:
    proc = subprocess.run([brand_python, str(script), str(article)], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    out = (proc.stdout + proc.stderr).strip()
    ok = proc.returncode == 0 and "找不到图片" not in out
    return ok, out


TITLE_LINES = re.compile(r"^(?:##\s+头条｜(.+)|###\s+\d+\.\s+(.+)|-\s+\*\*(.+?)\*\*：)", re.M)


def article_refs(text: str) -> tuple[list[str], list[str]]:
    """正文里的链接和条目标题（头条、要闻、快讯）。"""
    titles = [next(g for g in m.groups() if g).strip() for m in TITLE_LINES.finditer(text)]
    return [url for _, url in LINK.findall(text)], titles


def mark_published(store, day: date, article: Path) -> int:
    """正文里的链接和条目标题记入库：之后 7 天不再选同一链接，近 3 天的标题给选题判断“是不是后续”。"""
    urls, titles = article_refs(article.read_text(encoding="utf-8"))
    store.mark_published(day, urls, titles)
    return len(urls)
