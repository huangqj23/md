"""Hugging Face：每日论文、热门模型、按 org 拉新模型；选中后再查模型详情算显存。"""
import logging
import re
from datetime import datetime, timedelta

import httpx

from ..models import Item
from ..net import get
from ..textutil import clip

log = logging.getLogger(__name__)
HF = "https://huggingface.co"


def _time(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def daily_papers(http, cfg: dict, since: datetime | None = None) -> list[Item]:
    """最新一期 Daily Papers，按点赞数取前 N 篇。周末没有新一期时，接口返回的是旧榜单：
    上榜时间早于窗口开始前一天的不收。"""
    oldest = since - timedelta(hours=24) if since else None
    items = []
    for d in get(http, f"{HF}/api/daily_papers", params={"limit": 50}).json():
        p = d.get("paper") or {}
        pid = p.get("id")
        if not pid:
            continue
        listed = _time(p.get("submittedOnDailyAt") or d.get("publishedAt"))
        if oldest and listed and listed < oldest:
            continue
        abstract = p.get("summary") or d.get("summary") or ""
        items.append(Item(
            source="hf-papers", source_name="Hugging Face Daily Papers", kind="paper",
            title=d.get("title") or p.get("title", ""), url=f"{HF}/papers/{pid}",
            published=listed, summary=clip(abstract, 500), image=d.get("thumbnail"),
            meta={"upvotes": p.get("upvotes", 0), "github": p.get("githubRepo"),
                  "github_stars": p.get("githubStars"), "arxiv": f"https://arxiv.org/abs/{pid}",
                  "project": p.get("projectPage"), "text": abstract}))
    items.sort(key=lambda it: it.meta["upvotes"], reverse=True)
    return items[: cfg.get("daily_papers_limit", 25)]


def model_thumbnail(repo_id: str) -> str:
    """HF 为每个模型生成的分享卡片（模型页 og:image 就是它），没有自带配图时用。"""
    return f"https://cdn-thumbnails.huggingface.co/social-thumbnails/models/{repo_id}.png"


def _model_item(m: dict, source: str, name: str, label: str) -> Item:
    mid = m["id"]
    pipeline = m.get("pipeline_tag") or ""
    return Item(source=source, source_name=name, kind="model", title=mid, url=f"{HF}/{mid}",
                published=_time(m.get("createdAt")), label=label, image=model_thumbnail(mid),
                summary=f"{pipeline} · likes {m.get('likes', 0)} · downloads {m.get('downloads', 0)}".strip(" ·"),
                meta={"repo_id": mid, "likes": m.get("likes", 0), "downloads": m.get("downloads", 0),
                      "pipeline": pipeline, "trending": m.get("trendingScore")})


def trending_models(http, cfg: dict, official_orgs, now: datetime) -> list[Item]:
    """热门榜上的新模型：上传超过 trending_max_age_hours 小时的不收，老模型上榜不算新闻。"""
    max_age = timedelta(hours=cfg.get("trending_max_age_hours", 72))
    official = {o.lower() for o in official_orgs}
    items = []
    for m in get(http, f"{HF}/api/models", params={"sort": "trendingScore", "limit": cfg.get("trending_limit", 30)}).json():
        created = _time(m.get("createdAt"))
        if created and now - created > max_age:
            continue
        label = "官方" if m["id"].split("/")[0].lower() in official else "社区"
        items.append(_model_item(m, "hf-trending", "Hugging Face 热门模型", label))
    return items


def org_models(http, cfg: dict, since: datetime) -> list[Item]:
    """各大厂 org 在窗口内新上传的模型。"""
    items = []
    for org in cfg.get("orgs", []):
        try:
            data = get(http, f"{HF}/api/models", params={
                "author": org, "sort": "createdAt", "direction": -1, "limit": cfg.get("per_org", 5)}).json()
        except httpx.HTTPError as e:
            log.warning("HF org %s 拉取失败：%s", org, e)
            continue
        for m in data:
            created = _time(m.get("createdAt"))
            if created and created >= since:
                items.append(_model_item(m, "hf-orgs", f"Hugging Face · {org}", "官方"))
    return items


_HTML_TAG = re.compile(r"<[^>]+>")
_MD_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")


def clean_readme(md: str) -> str:
    """模型卡开头常是 logo 和徽章的 HTML：去掉 HTML 标签、图片，链接只留文字，保留标题和段落结构。"""
    if md.startswith("---"):
        end = md.find("\n---", 3)
        md = md[end + 4:] if end > 0 else md
    md = _MD_IMAGE.sub("", _HTML_TAG.sub(" ", md))
    md = _MD_LINK.sub(r"\1", md).replace("&nbsp;", " ")
    lines = [" ".join(line.split()) for line in md.splitlines()]
    lines = [line for line in lines if line.strip(" |·-")]          # 只剩分隔符的徽章行
    return "\n".join(lines).strip()


def model_info(http, repo_id: str) -> dict:
    """参数量（safetensors 元数据）、许可证、上下文长度、README 开头。gated 模型读不到的字段留空。"""
    info = get(http, f"{HF}/api/models/{repo_id}",
               params=[("expand[]", "safetensors"), ("expand[]", "cardData")]).json()
    out = {"safetensors": info.get("safetensors") or {}, "license": (info.get("cardData") or {}).get("license"),
           "context": None, "readme": ""}
    try:
        cfg = get(http, f"{HF}/{repo_id}/raw/main/config.json", retries=0).json()
        text_cfg = cfg.get("text_config") or {}
        out["context"] = cfg.get("max_position_embeddings") or text_cfg.get("max_position_embeddings")
    except (httpx.HTTPError, ValueError):
        pass
    try:
        out["readme"] = clip(clean_readme(get(http, f"{HF}/{repo_id}/raw/main/README.md", retries=0).text), 8000)
    except httpx.HTTPError:
        pass
    return out
