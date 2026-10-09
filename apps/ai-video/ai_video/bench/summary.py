"""Turn exported scores into the Phase 0 deliverables: a routing table (content type → model) and a
per-provider table of refusals, latency and cost."""
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean

from .report import gather
from .runner import Bench

DIMS = {
    "video": ("quality", "motion", "adherence"),
    "image": ("quality", "adherence"),
    "tts": ("natural", "prosody"),
    "llm": ("writing", "accuracy"),
}
STATE_COLS = (("done", "完成"), ("rejected", "被拒绝"), ("failed", "失败"), ("pending", "等待中"))


def load_scores(path: Path) -> dict:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return raw.get("scores", raw)


def item_score(scores: dict, key: str, kind: str) -> float | None:
    s = scores.get(key) or {}
    values = [float(s[d]) for d in DIMS[kind] if s.get(d)]
    return mean(values) if values else None


def _fmt(values: list[float]) -> str:
    return f"{mean(values):.2f}（{len(values)}）" if values else "—"


def _table(header: list[str], rows: list[list[str]]) -> list[str]:
    return ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)] + ["| " + " | ".join(r) + " |" for r in rows]


def summarize(bench: Bench, scores: dict) -> str:
    data = gather(bench)
    labels = data["labels"]
    prices = {pid: float(p.cfg.get("price_cny_per_second", 0)) for pid, p in bench.providers["video"].items()}
    lines = [f"# Phase 0 结果汇总（{bench.run_name}）", "",
             f"生成于 {data['generated_at']}，已花费约 ¥{data['spent_cny']}（按 providers.yaml 的参考单价估算）。",
             "分数是三项（画面、运动、遵循）的平均分，满分 5，括号里是打过分的条数。", ""]

    # Routing: content type (case.route; text-to-video kept apart) × provider.
    by_route: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    refused: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    video_pids: list[str] = []
    for case in data["cases"]:
        for v in case["videos"]:
            if v["provider"] not in video_pids:
                video_pids.append(v["provider"])
            route = case["route"] + ("（文生视频）" if v["mode"] == "t2v" else "")
            if v["state"] == "rejected":
                refused[route][v["provider"]] += 1
            score = item_score(scores, v["key"], "video")
            if score is not None:
                by_route[route][v["provider"]].append(score)
    rows = []
    for route in sorted(set(by_route) | set(refused)):
        scored = {pid: vals for pid, vals in by_route[route].items() if vals}
        best = max(scored, key=lambda pid: (mean(scored[pid]), -prices.get(pid, 0))) if scored else None
        cells = []
        for pid in video_pids:
            cell = _fmt(by_route[route].get(pid, []))
            if refused[route].get(pid):
                cell += f" 拒{refused[route][pid]}"
            cells.append(cell)
        rows.append([route, labels.get(best, "—") if best else "—"] + cells)
    lines += ["## 视频路由表", ""] + _table(["内容类型", "推荐"] + [labels.get(p, p) for p in video_pids], rows) + [""]

    # Provider reliability, speed and cost.
    stats: dict[str, dict] = defaultdict(lambda: {"states": defaultdict(int), "latency": [], "cny": 0.0,
                                                  "usable": 0, "scored": 0})
    for case in data["cases"]:
        for v in case["videos"]:
            st = stats[v["provider"]]
            st["states"][v["state"]] += 1
            if v["state"] == "done":
                if v.get("latency_s"):
                    st["latency"].append(v["latency_s"])
                st["cny"] += v.get("est_cny") or 0
            s = scores.get(v["key"]) or {}
            if item_score(scores, v["key"], "video") is not None:
                st["scored"] += 1
                st["usable"] += 1 if s.get("usable") else 0
    rows = []
    for pid in video_pids:
        st = stats[pid]
        usable = f"{st['usable']}/{st['scored']}" if st["scored"] else "—"
        latency = f"{mean(st['latency']):.0f} 秒" if st["latency"] else "—"
        rows.append([labels.get(pid, pid)] + [str(st["states"].get(k, 0)) for k, _ in STATE_COLS]
                    + [usable, latency, f"¥{st['cny']:.1f}"])
    lines += ["## 各家视频模型", ""] + _table(["模型"] + [n for _, n in STATE_COLS] + ["能用", "平均耗时", "花费"], rows) + [""]

    problems = [(case["title"], v) for case in data["cases"] for v in case["videos"] if v["state"] in ("rejected", "failed")]
    if problems:
        lines += ["## 被拒绝和失败的任务", ""]
        lines += [f"- {title} · {labels.get(v['provider'], v['provider'])} · {v['mode']}：{v['message'][:200]}"
                  for title, v in problems]
        lines.append("")

    # Keyframe images, speech and LLM drafts: mean score per provider (per voice for speech).
    def provider_means(kind: str, groups) -> list[list[str]]:
        acc: dict[str, list[float]] = defaultdict(list)
        for item, name in groups:
            score = item_score(scores, item["key"], kind)
            if score is not None:
                acc[name].append(score)
        return [[name, _fmt(vals)] for name, vals in sorted(acc.items(), key=lambda kv: -mean(kv[1]))]

    image_rows = provider_means("image", ((img, labels.get(img["provider"], img["provider"]))
                                          for c in data["cases"] for kf in c["keyframes"] for img in kf["images"]))
    if image_rows:
        lines += ["## 关键帧图片", ""] + _table(["模型", "平均分"], image_rows) + [""]
    tts_rows = provider_means("tts", ((it, f"{labels.get(it['provider'], it['provider'])} · {it['voice']}")
                                      for s in data["speech"] for it in s["items"]))
    if tts_rows:
        lines += ["## 配音", ""] + _table(["模型 · 音色", "平均分"], tts_rows) + [""]
    llm_rows = provider_means("llm", ((it, labels.get(it["provider"], it["provider"]))
                                      for t in data["llm"] for it in t["items"]))
    if llm_rows:
        lines += ["## 文案（LLM）", ""] + _table(["模型", "平均分"], llm_rows) + [""]

    text = "\n".join(lines)
    out = bench.root / "summary.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    return text
