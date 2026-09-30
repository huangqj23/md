"""不靠 LLM 的判断：主线归类和启发式打分。无 LLM 模式直接用；有 LLM 时用来给候选排序、截断。"""
from difflib import SequenceMatcher

from .models import Event, Item, TRACKS, canonical_url
from .textutil import compile_keywords, matches_keywords

AGENT = compile_keywords([
    "agent", "agentic", "mcp", "tool use", "tool-use", "tool calling", "function calling", "codex", "claude code",
    "gemini cli", "cursor", "copilot", "coding assistant", "browser use", "computer use", "langgraph", "crewai",
    "openhands", "swe-bench", "terminal-bench", "workflow", "智能体", "工具调用", "编程助手"])
VISION = compile_keywords([
    "vision", "visual", "image", "video", "vlm", "multimodal", "multi-modal", "ocr", "detection", "detector",
    "segment", "sam", "yolo", "diffusion", "3d", "depth", "pose", "tracking", "robot", "robotics", "embodied",
    "camera", "lerobot", "text-to-image", "image-to", "视觉", "图像", "视频", "多模态", "检测", "分割", "具身", "机器人"])
VISION_PIPELINES = ("image", "video", "object-detection", "depth", "mask-generation", "any-to-any", "keypoint", "robotics")


def guess_track(item: Item) -> str:
    if item.track in TRACKS:
        return item.track
    pipeline = item.meta.get("pipeline") or ""
    if any(p in pipeline for p in VISION_PIPELINES):
        return "vision"
    text = f"{item.title} {item.summary[:300]}"
    if matches_keywords(text, AGENT):
        return "agent"
    if matches_keywords(text, VISION):
        return "vision"
    return "llm"


BASE = {"官方": 40, "论文": 22, "代码": 22, "数据": 18, "媒体": 14, "社区": 10, "传闻": 5}


def heuristic_score(item: Item) -> float:
    m = item.meta
    s = BASE.get(item.label, 10)
    s += min(30, (m.get("points") or 0) / 10)          # HN
    s += min(30, (m.get("upvotes") or 0) / 2)          # HF 论文点赞
    s += min(25, (m.get("likes") or 0) / 20)           # HF 模型点赞
    s += min(25, (m.get("stars_today") or 0) / 60)     # GitHub 今日 star
    if item.kind == "inbox":
        s += 30                                         # 手动投喂的优先
    if item.kind == "release":
        s -= 8                                          # 小版本很多，默认往后放
    return round(s, 1)


def _similar(a: str, b: str) -> bool:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio() >= 0.8


def heuristic_events(items: list[Item]) -> list[Event]:
    """无 LLM 模式的“选题”：同链接或标题很像的合并，其余一条一个事件。"""
    events: list[Event] = []
    by_url: dict[str, Event] = {}
    for it in sorted(items, key=heuristic_score, reverse=True):
        key = canonical_url(it.url)
        hit = by_url.get(key) or next((e for e in events if _similar(e.title, it.title)), None)
        if hit:
            hit.items.append(it)
            continue
        ev = Event(title=it.title, items=[it], track=guess_track(it), score=heuristic_score(it), label=it.label)
        events.append(ev)
        by_url[key] = ev
    return events
