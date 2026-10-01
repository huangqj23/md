"""OpenRouter 模型列表：和上一份快照比，找出新上架和调价的模型。"""
from datetime import datetime, timezone

from ..models import Item
from ..net import get

API = "https://openrouter.ai/api/v1/models"


def _per_million(price) -> float | None:
    try:
        return round(float(price) * 1_000_000, 4)
    except (TypeError, ValueError):
        return None


def snapshot(http) -> dict:
    """{模型 id: {name, created, context, prompt, completion}}，价格是每百万 token 美元。
    带冒号的变体（:free / :batch / :thinking 等）不收，否则一个模型会刷出好几条。"""
    out = {}
    for m in get(http, API).json().get("data", []):
        if ":" in m["id"]:
            continue
        pricing = m.get("pricing") or {}
        out[m["id"]] = {"name": m.get("name") or m["id"], "created": m.get("created"),
                        "context": m.get("context_length"),
                        "prompt": _per_million(pricing.get("prompt")),
                        "completion": _per_million(pricing.get("completion"))}
    return out


def _fmt(v) -> str:
    return "?" if v is None else f"${v:g}"


def diff(current: dict, previous: dict | None, since: datetime) -> list[Item]:
    """没有上一份快照时（第一次运行），只按 created 时间找新上架，不报调价。"""
    items = []
    for mid, m in current.items():
        url = f"https://openrouter.ai/{mid}"
        price = f"输入 {_fmt(m['prompt'])}/M，输出 {_fmt(m['completion'])}/M，上下文 {m['context'] or '?'}"
        created = datetime.fromtimestamp(m["created"], timezone.utc) if m.get("created") else None
        is_new = (mid not in previous) if previous is not None else bool(created and created >= since)
        if is_new:
            items.append(Item(source="openrouter", source_name="OpenRouter", kind="price",
                              title=f"OpenRouter 上架 {m['name']}", url=url, published=created,
                              summary=price, meta={"openrouter": m, "text": f"{m['name']}（{mid}）：{price}"}))
            continue
        old = (previous or {}).get(mid)
        if not old:
            continue
        changes = []
        for key, name in (("prompt", "输入"), ("completion", "输出")):
            a, b = old.get(key), m.get(key)
            if a is not None and b is not None and a > 0 and abs(b - a) / a >= 0.01:
                changes.append(f"{name} {_fmt(a)} → {_fmt(b)}/M")
        if old.get("context") and m.get("context") and old["context"] != m["context"]:
            changes.append(f"上下文 {old['context']} → {m['context']}")
        if changes:
            # OpenRouter 的列表价常因默认供应商变化而变（同一天输入降、输出涨也常见），不等于厂商官方调价
            text = "；".join(changes)
            note = "OpenRouter 列表价变化，可能是默认供应商变了，不一定是厂商官方调价"
            items.append(Item(source="openrouter", source_name="OpenRouter", kind="price",
                              title=f"OpenRouter 上 {m['name']} 的标价变化：{text}", url=url,
                              summary=f"{text}（{note}）",
                              meta={"openrouter": m, "text": f"{m['name']}（{mid}）：{text}。{note}。"}))
    return items
