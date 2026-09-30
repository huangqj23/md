"""采集条目（Item）和选题后的事件（Event）。"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKS = ("llm", "agent", "vision")
TRACK_NAMES = {"llm": "LLM", "agent": "Agent", "vision": "视觉与多模态"}

# 可信度标签，数字越小越可信；选主来源时按这个顺序
LABELS = ("官方", "论文", "代码", "数据", "媒体", "社区", "传闻")
KIND_LABEL = {
    "official": "官方", "paper": "论文", "model": "代码", "release": "代码", "repo": "代码",
    "price": "数据", "media": "媒体", "newsletter": "媒体",
    "community": "社区", "social": "社区", "inbox": "社区",
}

_DROP_PARAMS = {"ref", "ref_src", "fbclid", "gclid", "spm", "mc_cid", "mc_eid"}


def canonical_url(url: str) -> str:
    """去掉跟踪参数、锚点和末尾斜杠，统一 https 和 twitter.com → x.com，用来去重。"""
    parts = urlsplit(url.strip())
    host = parts.netloc.lower().removeprefix("www.").removeprefix("mobile.")
    if host in ("twitter.com", "x.com"):
        host, query = "x.com", ""          # 推文链接上的 ?s=20&t=… 全是跟踪参数
    else:
        query = urlencode([(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                           if not k.lower().startswith("utm_") and k.lower() not in _DROP_PARAMS])
    path = parts.path.rstrip("/") or "/"
    return urlunsplit(("https", host, path, query, ""))


@dataclass
class Item:
    """一个信源上的一条动态。"""
    source: str                 # sources.yaml 里的 key
    source_name: str            # 显示名，如“OpenAI 官方博客”
    kind: str                   # official / paper / model / release / repo / price / media / newsletter / community / social / inbox
    title: str
    url: str
    published: datetime | None = None
    summary: str = ""           # 给选题看的短摘要
    image: str | None = None
    track: str | None = None    # 信源自带的主线提示
    label: str | None = None    # 可信度标签，不写按 kind 推
    meta: dict = field(default_factory=dict)

    def __post_init__(self):
        self.url = self.url.strip()
        self.title = " ".join(self.title.split())
        if not self.label:
            self.label = KIND_LABEL.get(self.kind, "社区")

    @property
    def id(self) -> str:
        return hashlib.sha1(canonical_url(self.url).encode("utf-8")).hexdigest()[:16]


@dataclass
class Event:
    """选题后的一件事，可能由多个信源的条目合并而来。"""
    title: str
    items: list[Item]
    track: str                  # llm / agent / vision / other
    score: float
    label: str
    why: str = ""               # 选题理由：工程师为什么要关心
    follow_up: str = ""         # 如果是近几天已发内容的后续，写是哪条
    # enrich 阶段
    primary: Item | None = None
    source_text: str = ""
    image_url: str | None = None
    image_candidates: list = field(default_factory=list)   # [(图片地址, 图源名, Referer)]，按质量排好
    image_path: str | None = None   # 相对文章目录，如 images/2026-10-01_h_nowm.png
    image_credit: str = ""
    vram: str = ""
    # write 阶段
    headline: str = ""          # 写作阶段给出的中文标题
    paragraphs: list[str] = field(default_factory=list)
    quotes: list[dict] = field(default_factory=list)
    engineer_note: str = ""
    brief: str = ""             # 快讯的一句话
    flags: list[str] = field(default_factory=list)   # 需要人工核对的原因

    @property
    def display_title(self) -> str:
        return self.headline or self.title

    @property
    def main_item(self) -> Item:
        return self.primary or pick_primary(self.items, self.title)


_NAME = re.compile(r"[a-z0-9]{2,}")


def _heat(it: Item) -> int:
    m = it.meta
    return m.get("stars_today") or m.get("stars") or m.get("points") or m.get("upvotes") or m.get("likes") or 0


def pick_primary(items: list[Item], hint: str = "") -> Item:
    """主来源：可信度最高的。同级里先看标题跟事件标题（hint）重合的英文名多少，再看热度（star、HN 分数、点赞），
    最后看信息多少。选题常把同一家公司当天的几个仓库并成一个事件，不能让内部小仓库顶替真正的发布。"""
    names = set(_NAME.findall(hint.lower()))
    return min(items, key=lambda it: (LABELS.index(it.label) if it.label in LABELS else len(LABELS),
                                      -len(names & set(_NAME.findall(it.title.lower()))), -_heat(it),
                                      -len(it.summary)))
