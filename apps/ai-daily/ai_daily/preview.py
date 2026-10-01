"""把生成的日报草稿（Markdown + images/）渲染成一个自带图片的 HTML 预览页：`ai-daily preview`。

给人在浏览器里看（Claude 会把它发布成私有的 Artifact 页面）：顶部是草稿状态（条数、配图、待核对、
我的看法、候选标题），下面是读者看到的样子。只认 templates/daily.md.j2 生成的结构；
图片压成 WebP 内嵌为 data URI，整页一般在 1 MB 以内。用品牌色（INK / ACCENT），跟随浅色 / 深色主题。
"""
import base64
import html
import io
import re
from pathlib import Path

from PIL import Image

MAX_W, QUALITY = 1000, 72


def data_uri(path: Path, max_w: int = MAX_W, quality: int = QUALITY) -> tuple[str, int, int]:
    im = Image.open(path)
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im, mask=im.split()[-1])
        im = bg
    else:
        im = im.convert("RGB")
    if im.width > max_w:
        im = im.resize((max_w, round(im.height * max_w / im.width)), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "WEBP", quality=quality, method=6)
    return "data:image/webp;base64," + base64.b64encode(buf.getvalue()).decode(), im.width, im.height


LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")


def inline(s: str) -> str:
    s = html.escape(s, quote=False)
    s = LINK.sub(lambda m: f'<a href="{m.group(2).replace(chr(34), "%22")}" target="_blank" rel="noopener">'
                           f'{m.group(1)}</a>', s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    return re.sub(r"`([^`]+)`", r'<span class="chip">\1</span>', s)


def meta_line(s: str) -> str:
    """`官方` · [X @OpenAI](url) · 09-30 · 另见 [..](..)：可信度单独做成标签，其余是来源。"""
    m = re.match(r"`([^`]+)`\s*·\s*(.*)$", s)
    if not m:
        return f'<p class="meta">{inline(s)}</p>'
    label, rest = m.groups()
    parts = [p.strip() for p in rest.split(" · ") if p.strip()]
    out = []
    for p in parts:
        if re.fullmatch(r"\d{2}-\d{2}", p):
            out.append(f'<time class="num">{p}</time>')
        elif p.startswith("另见 "):
            out.append(f'<span class="also">另见 {inline(p[3:])}</span>')
        else:
            out.append(f'<span class="src">{inline(p)}</span>')
    kind = {"官方": "official", "传闻": "rumor"}.get(label, "")
    return (f'<p class="meta"><span class="cred {kind}">{html.escape(label)}</span>'
            + '<span class="dot">·</span>'.join(out) + "</p>")


def blocks(text: str) -> list[list[str]]:
    out, cur = [], []
    for line in text.splitlines():
        if line.strip():
            cur.append(line.rstrip())
        elif cur:
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


def render_body(md: str, base: Path) -> tuple[str, dict]:
    info = {"title": "", "flags": 0, "images": 0, "items": 0, "briefs": 0, "opinion_todo": False}
    html_parts: list[str] = []
    in_item = False
    section = ""

    def close_item():
        nonlocal in_item
        if in_item:
            html_parts.append("</article>")
            in_item = False

    for b in blocks(md):
        first = b[0]
        if first.startswith("# "):
            info["title"] = first[2:].strip()
            continue                                        # 标题放进报头
        if all(line.startswith(">") for line in b):
            lines = [line[1:].strip() for line in b]
            if "今日看点" in lines[0]:
                items = [re.sub(r"^\d+\.\s*", "", x) for x in lines[1:]]
                html_parts.append('<section class="digest" aria-label="今日看点"><h2 class="digest-h">今日看点</h2><ol>'
                                  + "".join(f"<li>{inline(x)}</li>" for x in items) + "</ol></section>")
            elif lines[0].startswith("原文："):
                html_parts.append('<blockquote class="quote"><span class="quote-label">原文</span>'
                                  f'<p lang="en">{inline(" ".join(lines)[3:].strip())}</p></blockquote>')
            else:
                html_parts.append(f"<blockquote><p>{inline(' '.join(lines))}</p></blockquote>")
            continue
        if first.startswith("## 头条｜"):
            close_item()
            section = "headline"
            html_parts.append('<section class="block"><h2 class="section-label">头条</h2>'
                              f'<article class="item headline"><h3>{inline(first.split("｜", 1)[1])}</h3>')
            in_item = True
            continue
        if first.startswith("## "):
            close_item()
            if section:
                html_parts.append("</section>")
            section = first[3:].strip()
            html_parts.append(f'<section class="block"><h2 class="section-label">{inline(section)}</h2>')
            continue
        m = re.match(r"^###\s+(\d+)\.\s+(.+)$", first)
        if m:
            close_item()
            info["items"] += 1
            html_parts.append(f'<article class="item"><h3><span class="rank num">{m.group(1)}</span>'
                              f"<span>{inline(m.group(2))}</span></h3>")
            in_item = True
            continue
        if first.startswith("`"):
            html_parts.append(meta_line(" ".join(b)))
            continue
        m = re.match(r"^!\[([^\]]*)\]\(([^)\s]+)\)$", first)
        if m:
            src, w, h = data_uri(base / m.group(2))
            info["images"] += 1
            html_parts.append(f'<figure><img src="{src}" width="{w}" height="{h}" alt="{html.escape(m.group(1))}" '
                              'loading="lazy"></figure>')
            continue
        if first.startswith("图源："):
            cap = f'<figcaption>{inline(first)}</figcaption></figure>'
            if html_parts and html_parts[-1].endswith("</figure>"):
                html_parts[-1] = html_parts[-1][: -len("</figure>")] + cap
            else:
                html_parts.append(f'<p class="caption">{inline(first)}</p>')
            continue
        if first.startswith("【待核对"):
            info["flags"] += len(b)
            html_parts.append('<aside class="flag" role="note"><span class="flag-label">待核对</span><div>'
                              + "".join(f"<p>{inline(re.sub(r'^【待核对：?|】$', '', x))}</p>" for x in b)
                              + "</div></aside>")
            continue
        if first.startswith("**我的看法**"):
            todo = "【我的看法：待写】" in first
            info["opinion_todo"] = todo
            body = "还没写：发布前由你来写这一段" if todo else inline(first.split("：", 1)[1])
            html_parts.append(f'<aside class="opinion{" todo" if todo else ""}"><span class="opinion-label">我的看法</span>'
                              f"<p>{body}</p></aside>")
            continue
        if first.startswith("- "):
            if section == "快讯":
                info["briefs"] += len(b)
                lis = []
                for x in b:
                    mm = re.match(r"^- \*\*(.+?)\*\*：(.*)$", x)
                    lis.append(f"<li><strong>{inline(mm.group(1))}</strong>{inline(mm.group(2))}</li>" if mm
                               else f"<li>{inline(x[2:])}</li>")
                html_parts.append('<ul class="briefs">' + "".join(lis) + "</ul>")
            else:
                for x in b:
                    mm = re.match(r"^- \*\*(.+?)\*\*：(.*)$", x)
                    if mm:
                        cls = "note" if mm.group(1) == "工程师视角" else "note vram"
                        html_parts.append(f'<div class="{cls}"><span class="note-label">{inline(mm.group(1))}</span>'
                                          f"<p>{inline(mm.group(2))}</p></div>")
                    else:
                        html_parts.append(f"<p>{inline(x[2:])}</p>")
            continue
        if first.strip() == "---":
            close_item()
            if section:
                html_parts.append("</section>")
                section = ""
            html_parts.append('<footer class="article-foot">')
            section = "footer"
            continue
        html_parts.append(f"<p>{inline(' '.join(b))}</p>")
    close_item()
    if section == "footer":
        html_parts.append("</footer>")
    elif section:
        html_parts.append("</section>")
    return "\n".join(html_parts), info


def review_info(review: Path) -> dict:
    text = review.read_text(encoding="utf-8") if review.is_file() else ""
    section = text.split("## 候选标题", 1)[1].split("##", 1)[0] if "## 候选标题" in text else ""
    titles = re.findall(r"^\d+\.\s+(.+)$", section, re.M)
    usage = re.search(r"^- LLM：(.+)$", text, re.M)
    collect = re.search(r"^- 采集：(.+)$", text, re.M)
    return {"titles": titles, "usage": usage.group(1) if usage else "", "collect": collect.group(1) if collect else ""}


def mark_svg(path: Path | None) -> str:
    """vault 里的 hollis23 标志（3×3 圆角方格），颜色换成主题变量；找不到就不放。"""
    if path is None or not path.is_file():
        return ""
    svg = path.read_text(encoding="utf-8")
    svg = svg.replace('width="192" height="192"', 'class="mark" aria-hidden="true" focusable="false"')
    return svg.replace('"#15171C"', '"currentColor"').replace('"#FF5A1F"', '"var(--accent)"')


CSS = """
:root {
  --ground: #eceef2; --paper: #ffffff; --ink: #15171c; --ink-2: #3b404b; --muted: #636a77;
  --rule: #e0e3e9; --accent: #ff5a1f; --accent-text: #c2410c; --chip-bg: #f1f2f5;
  --warn-bg: #fff3cc; --warn-ink: #5f4400; --warn-rule: #e3ad00;
  --quote-bg: #f6f7f9; --shadow: 0 1px 2px rgba(21,23,28,.06), 0 8px 24px rgba(21,23,28,.06);
  --cjk: "Noto Sans SC", "PingFang SC", "Microsoft YaHei", system-ui, sans-serif;
  --latin: "Space Grotesk", "Segoe UI", system-ui, sans-serif;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --ground: #0e1014; --paper: #15171c; --ink: #e8eaee; --ink-2: #c4c8d0; --muted: #969ca8;
    --rule: #2a2e37; --accent: #ff5a1f; --accent-text: #ff7a45; --chip-bg: #22252d;
    --warn-bg: #33290c; --warn-ink: #f2d27c; --warn-rule: #b88c0c;
    --quote-bg: #1c1f26; --shadow: none;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --ground: #0e1014; --paper: #15171c; --ink: #e8eaee; --ink-2: #c4c8d0; --muted: #969ca8;
  --rule: #2a2e37; --accent: #ff5a1f; --accent-text: #ff7a45; --chip-bg: #22252d;
  --warn-bg: #33290c; --warn-ink: #f2d27c; --warn-rule: #b88c0c;
  --quote-bg: #1c1f26; --shadow: none;
}
body { background: var(--ground); color: var(--ink); font-family: var(--cjk); font-size: 16px;
  line-height: 1.8; padding-inline: 16px; padding-block: 24px 56px; }
a { color: var(--accent-text); text-decoration: none; border-bottom: 1px solid color-mix(in srgb, var(--accent-text) 35%, transparent); }
a:hover { border-bottom-color: var(--accent-text); }
a:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; border-radius: 2px; }
.num { font-family: var(--latin); font-variant-numeric: tabular-nums; }
.wrap { max-width: 720px; margin: 0 auto; display: grid; gap: 16px; }

.status { background: var(--paper); border: 1px solid var(--rule); border-radius: 10px; padding: 14px 18px;
  display: grid; gap: 10px; font-size: 13.5px; line-height: 1.6; color: var(--ink-2); }
.status-top { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 14px; }
.brand { display: inline-flex; align-items: center; gap: 8px; color: var(--ink); font-family: var(--latin);
  font-weight: 700; font-size: 15px; letter-spacing: .01em; }
.mark { width: 22px; height: 22px; color: var(--ink); }
.draft-tag { font-size: 12px; color: var(--muted); }
.stats { display: flex; flex-wrap: wrap; gap: 6px 18px; margin: 0; padding: 0; list-style: none; }
.stats b { color: var(--ink); font-family: var(--latin); font-weight: 600; }
.stats .todo b, .stats .todo { color: var(--accent-text); }
.titles { margin: 0; padding-left: 1.4em; }
.titles li::marker { font-family: var(--latin); color: var(--muted); }
.titles .used { color: var(--ink); font-weight: 500; }
.titles .used::after { content: "正文在用"; margin-left: 8px; font-size: 11.5px; color: var(--muted);
  border: 1px solid var(--rule); border-radius: 4px; padding: 0 5px; vertical-align: 1px; }
.status-h { font-size: 12px; color: var(--muted); margin: 0; letter-spacing: .04em; }

.paper { background: var(--paper); border: 1px solid var(--rule); border-radius: 12px; box-shadow: var(--shadow);
  padding-block: 0 36px; overflow: hidden; }
.cover { margin: 0; border-bottom: 1px solid var(--rule); }
.cover img { display: block; width: 100%; height: auto; }
.inner { padding: 22px clamp(18px, 5vw, 40px) 0; display: grid; gap: 22px; }
h1 { font-size: clamp(22px, 4.6vw, 28px); line-height: 1.35; margin: 0; font-weight: 700; text-wrap: balance; }
.byline { margin: -12px 0 0; font-size: 13px; color: var(--muted); display: flex; flex-wrap: wrap; gap: 4px 12px; }
.byline .who { color: var(--ink-2); font-weight: 500; }

.digest { background: var(--quote-bg); border-radius: 10px; padding: 14px 18px 12px; }
.digest-h { margin: 0 0 4px; font-size: 13px; color: var(--accent-text); font-weight: 700; letter-spacing: .06em; }
.digest ol { margin: 0; padding-left: 1.3em; display: grid; gap: 2px; }
.digest li::marker { font-family: var(--latin); color: var(--accent-text); font-weight: 600; }

.block { display: grid; gap: 26px; }
.section-label { margin: 8px 0 -8px; font-size: 13px; font-weight: 700; letter-spacing: .12em; color: var(--muted);
  display: flex; align-items: center; gap: 10px; }
.section-label::after { content: ""; flex: 1; height: 1px; background: var(--rule); }
.item { display: grid; gap: 12px; }
.item + .item { border-top: 1px solid var(--rule); padding-top: 24px; }
.item h3 { margin: 0; font-size: 19px; line-height: 1.45; font-weight: 700; text-wrap: balance;
  display: flex; gap: 10px; align-items: baseline; }
.item.headline h3 { font-size: 22px; }
.rank { color: var(--accent); font-weight: 700; font-size: 18px; min-width: 1.4em; }
.meta { margin: -4px 0 0; font-size: 13px; color: var(--muted); line-height: 1.7; display: flex; flex-wrap: wrap;
  align-items: center; gap: 2px 6px; }
.meta .dot { color: var(--rule); }
.meta a { color: var(--ink-2); border-bottom-color: var(--rule); }
.cred { font-size: 12px; font-weight: 700; padding: 0 7px; border-radius: 4px; border: 1px solid var(--rule);
  color: var(--ink-2); background: var(--chip-bg); margin-right: 4px; letter-spacing: .04em; }
.cred.official { background: var(--ink); color: var(--paper); border-color: var(--ink); }
.cred.rumor { background: var(--warn-bg); color: var(--warn-ink); border-color: var(--warn-rule); }
.chip { font-size: 12px; padding: 0 6px; border-radius: 4px; background: var(--chip-bg); }
figure { margin: 0; }
figure img { display: block; width: 100%; height: auto; border-radius: 8px; border: 1px solid var(--rule);
  background: var(--quote-bg); }
figcaption { font-size: 12px; color: var(--muted); margin-top: 6px; }
.item p { margin: 0; }
.quote { margin: 0; padding: 10px 14px; border-left: 3px solid var(--accent); background: var(--quote-bg);
  border-radius: 0 8px 8px 0; font-size: 14px; line-height: 1.65; color: var(--ink-2); display: grid; gap: 2px; }
.quote-label, .note-label, .flag-label, .opinion-label { font-size: 12px; font-weight: 700; letter-spacing: .06em; }
.quote-label { color: var(--accent-text); }
.quote p { margin: 0; overflow-wrap: anywhere; }
.note { display: grid; gap: 2px; padding: 10px 14px; border: 1px solid var(--rule); border-radius: 8px; font-size: 15px; }
.note-label { color: var(--ink); }
.note.vram { background: var(--quote-bg); }
.flag { display: grid; grid-template-columns: auto 1fr; gap: 10px; align-items: start; padding: 9px 14px;
  background: var(--warn-bg); color: var(--warn-ink); border: 1px solid var(--warn-rule); border-radius: 8px; font-size: 14px; }
.flag-label { padding-top: 1px; }
.flag p { margin: 0; }
.opinion { padding: 12px 14px; border: 1.5px dashed var(--rule); border-radius: 8px; display: grid; gap: 2px; }
.opinion.todo { border-color: var(--accent); color: var(--accent-text); }
.opinion-label { color: var(--ink); }
.briefs { margin: 0; padding-left: 1.2em; display: grid; gap: 10px; }
.briefs li::marker { color: var(--accent); }
.briefs strong { margin-right: 6px; }
.article-foot { border-top: 1px solid var(--rule); padding-top: 18px; display: grid; gap: 8px; font-size: 14px; color: var(--muted); }
.article-foot p { margin: 0; }
@media (max-width: 480px) {
  body { font-size: 15.5px; }
  .item h3 { font-size: 18px; }
  .item.headline h3 { font-size: 20px; }
}
@media (prefers-reduced-motion: reduce) { * { scroll-behavior: auto; } }
"""


def build(article: Path, out: Path, mark: Path | None = None) -> dict:
    """渲染 article（<日期>_AI日报.md）到 out；mark 是品牌标志 SVG。返回条数、配图数、待核对数等。"""
    md = article.read_text(encoding="utf-8")
    base = article.parent
    body, info = render_body(md, base)
    day = article.name[:10]
    rev = review_info(article.with_name(f"{day}_审稿与备选.md"))
    cover = base / "images" / f"{day}_cover_nowm.png"
    cover_html = ""
    if cover.is_file():
        src, w, h = data_uri(cover, max_w=1440, quality=78)
        cover_html = (f'<figure class="cover"><img src="{src}" width="{w}" height="{h}" alt="本期封面"></figure>')
    cover_json = base / "images" / f"{day}_cover.json"
    issue = re.search(r'"tag":\s*"[^"]*第\s*(\d+)\s*期', cover_json.read_text(encoding="utf-8")) \
        if cover_json.is_file() else None
    written = info["items"] + 1
    todo_opinion = '<li class="todo">我的看法 <b>待写</b></li>' if info["opinion_todo"] else "<li>我的看法 <b>已写</b></li>"
    flags = f'<li class="todo">待核对 <b>{info["flags"]}</b> 处</li>' if info["flags"] else "<li>待核对 <b>0</b></li>"
    titles = "".join(f'<li class="{"used" if t == info["title"] else ""}">{html.escape(t)}</li>' for t in rev["titles"])
    page = f"""<title>AI 早报 {day[5:].replace('-', '.')}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@400;500;700&family=Space+Grotesk:wght@500;600;700&display=swap">
<style>{CSS}</style>
<div class="wrap">
  <section class="status" aria-label="草稿状态">
    <div class="status-top">
      <span class="brand">{mark_svg(mark)}hollis23 AI 早报</span>
      <span class="draft-tag">草稿预览 · {html.escape(day)} · 由 ai-daily 生成，还没发布</span>
    </div>
    <ul class="stats">
      <li>头条 <b>1</b> + 要闻 <b>{info["items"]}</b> + 快讯 <b>{info["briefs"]}</b></li>
      <li>配图 <b>{info["images"]}</b> / {written}</li>
      {flags}
      {todo_opinion}
    </ul>
    {f'<p class="status-h">候选标题（正文第一行用第一个；想换就改正文）</p><ol class="titles">{titles}</ol>' if titles else ""}
    {f'<p class="status-h">本次运行：{html.escape(rev["collect"])}；{html.escape(rev["usage"])}</p>' if rev["collect"] else ""}
  </section>
  <main class="paper">
    {cover_html}
    <div class="inner">
      <h1>{html.escape(info["title"])}</h1>
      <p class="byline"><span class="who">hollis23</span><span class="num">{html.escape(day)}</span>{f'<span>第 <span class="num">{issue.group(1)}</span> 期</span>' if issue else ""}</p>
{body}
    </div>
  </main>
</div>
"""
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8")
    info["bytes"] = out.stat().st_size
    return info
