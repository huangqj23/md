"""Static HTML report: every case's results side by side, blind by default, with scoring that is
kept in the browser's localStorage and exported as JSON for `ai-video bench summary`."""
import json
import time
from pathlib import Path

from .runner import TERMINAL, Bench, safe_name

TEMPLATE = Path(__file__).with_name("report.html")


def _rel(bench: Bench, path: Path) -> str:
    return path.relative_to(bench.root).as_posix()


def _labels(bench: Bench) -> dict[str, str]:
    labels = {pid: p.label for kind in bench.providers.values() for pid, p in kind.items()}
    labels.update({pid: c.get("label", pid) for pid, c in bench.llm_cfg.items()})
    labels["source"] = "原画"
    return labels


def _keyframes(bench: Bench, case) -> list[dict]:
    out = []
    for kf in case.keyframes:
        images = []
        if case.source:
            f = bench.source_file(case, kf.id)
            if f.exists():
                images.append({"key": f"image|{case.id}|{kf.id}|source", "provider": "source", "src": _rel(bench, f),
                               "state": "done", "scorable": False})
        else:
            for pid in bench.providers["image"]:
                f = bench.keyframe_file(case, kf.id, pid)
                st = bench.read_state("image", f"{case.id}.{pid}")
                if f.exists():
                    images.append({"key": f"image|{case.id}|{kf.id}|{pid}", "provider": pid, "src": _rel(bench, f),
                                   "state": "done", "scorable": True})
                elif st.get("state") in TERMINAL and st.get("keyframe") == kf.id:
                    images.append({"key": f"image|{case.id}|{kf.id}|{pid}", "provider": pid, "src": None,
                                   "state": st["state"], "message": st.get("message", ""), "scorable": False})
        out.append({"id": kf.id, "prompt": kf.prompt, "images": images})
    return out


def _videos(bench: Bench, case) -> list[dict]:
    out = []
    for pid in bench.providers["video"]:
        for mode in case.modes:
            f = bench.video_file(case, pid, mode)
            if mode == "chain":
                n = len(case.segments)
                states = [bench.read_state("video", f"{case.id}.{pid}.chain.seg{i}") for i in range(1, n + 1)]
                if not f.exists() and not any(states):
                    continue
                problem = next((s for s in states if s and s.get("state") != "done"), {})
                item = {
                    "state": "done" if f.exists() else problem.get("state", "pending"),
                    "message": "" if f.exists() else problem.get("message", ""),
                    "latency_s": round(sum(s.get("latency_s") or 0 for s in states), 1),
                    "est_cny": round(sum(s.get("est_cny") or 0 for s in states), 2),
                    "segments": [_rel(bench, s) for s in (bench.segment_file(case, pid, i) for i in range(1, n + 1))
                                 if s.exists()],
                }
            else:
                st = bench.read_state("video", f"{case.id}.{pid}.{mode}")
                if not f.exists() and not st:
                    continue
                item = {"state": "done" if f.exists() else st.get("state", "pending"),
                        "message": "" if f.exists() else st.get("message", ""),
                        "latency_s": st.get("latency_s"), "est_cny": st.get("est_cny"), "segments": []}
            out.append({"key": f"video|{case.id}|{pid}|{mode}", "provider": pid, "mode": mode,
                        "src": _rel(bench, f) if f.exists() else None, **item})
    return out


def gather(bench: Bench) -> dict:
    cases = [{
        "id": c.id, "title": c.title, "direction": c.direction, "route": c.route, "aspect": c.aspect,
        "check": c.check, "video_prompt": c.video_prompt, "t2v_prompt": c.t2v_prompt if "t2v" in c.modes else "",
        "segments": [{"from": s.src, "to": s.dst, "prompt": s.prompt} for s in c.segments],
        "keyframes": _keyframes(bench, c), "videos": _videos(bench, c),
    } for c in bench.cases]

    speech = []
    for sc in bench.speech_cases:
        items = []
        for pid, p in bench.providers["tts"].items():
            for voice in p.voices:
                f = bench.speech_file(sc.id, pid, voice)
                st = bench.read_state("tts", f"{sc.id}.{pid}.{safe_name(voice)}")
                if f.exists() or st:
                    items.append({"key": f"tts|{sc.id}|{pid}|{voice}", "provider": pid, "voice": voice,
                                  "src": _rel(bench, f) if f.exists() else None,
                                  "state": "done" if f.exists() else st.get("state"), "message": st.get("message", ""),
                                  "latency_s": st.get("latency_s")})
        speech.append({"id": sc.id, "title": sc.title, "text": sc.text, "items": items})

    llm = []
    for task in bench.llm_tasks:
        items = []
        for pid in bench.llm_cfg:
            f = bench.llm_file(task.id, pid)
            st = bench.read_state("llm", f"{task.id}.{pid}")
            if f.exists() or st:
                items.append({"key": f"llm|{task.id}|{pid}", "provider": pid,
                              "text": f.read_text(encoding="utf-8") if f.exists() else "",
                              "state": "done" if f.exists() else st.get("state"), "message": st.get("message", ""),
                              "latency_s": st.get("latency_s"), "json_ok": st.get("json_ok")})
        llm.append({"id": task.id, "title": task.title, "prompt": task.prompt, "json": task.json, "items": items})

    return {"run": bench.run_name, "generated_at": time.strftime("%Y-%m-%d %H:%M"), "labels": _labels(bench),
            "spent_cny": round(bench.ledger.total(bench.run_name), 2), "cases": cases, "speech": speech, "llm": llm}


def write_report(bench: Bench) -> Path:
    data = json.dumps(gather(bench), ensure_ascii=False).replace("</", "<\\/")
    html = TEMPLATE.read_text(encoding="utf-8").replace("__DATA__", data)
    out = bench.root / "report.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out
