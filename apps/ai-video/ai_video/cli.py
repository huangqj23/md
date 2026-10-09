"""Command line: ai-video check / ai-video bench <step>."""
import argparse
import json
import logging
import os
import shutil
import sys
from collections import defaultdict
from pathlib import Path

import httpx

from .bench.report import write_report
from .bench.runner import Bench, Job
from .bench.summary import load_scores, summarize
from .config import Settings, load_settings, load_yaml
from .errors import ProviderError
from .net import make_client
from .providers import load_providers, relay302

KIND_NAMES = {"video": "视频", "image": "图片", "tts": "配音"}
STEPS = ("images", "videos", "tts", "llm")
STEP_NAMES = {"images": "关键帧图片", "videos": "视频", "tts": "配音", "llm": "文案（LLM）"}
STEP_KINDS = {"images": "image", "videos": "video", "tts": "tts"}


def _setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _split(value: str | None) -> list[str] | None:
    return [v.strip() for v in value.split(",") if v.strip()] if value else None


def cmd_check(args, settings: Settings) -> int:
    cfg = load_yaml(settings.providers_file)
    providers = load_providers(cfg)
    with make_client(timeout=30) as http:
        for kind, items in providers.items():
            print(f"\n{KIND_NAMES[kind]}")
            for pid, p in items.items():
                if not p.ready:
                    status = "未就绪：" + "；".join(p.missing())
                elif args.ping:
                    try:
                        status = p.ping(http)
                    except (ProviderError, httpx.HTTPError) as e:
                        status = f"连接失败：{e}"
                else:
                    status = "就绪"
                print(f"  {pid:<14} {p.label:<16} {p.model or '-':<30} {status}")
    print("\nLLM（盲评）")
    for pid, c in (cfg.get("llm") or {}).items():
        if c.get("manual"):
            print(f"  {pid:<14} {c.get('label', pid):<16} {'-':<30} 手工撰写（不调用接口）")
            continue
        if not c.get("model"):
            status = "未就绪：providers.yaml 里没填 model"
        elif not os.environ.get(c.get("key_env", "")):
            status = f"未就绪：.env 里没有 {c.get('key_env')}"
        else:
            status = "就绪"
        print(f"  {pid:<14} {c.get('label', pid):<16} {c.get('model') or '-':<30} {status}")
    print(f"\nffmpeg：{'已安装' if shutil.which('ffmpeg') else '未安装（长镜头片段无法拼接）'}")
    print(f"数据目录：{settings.data_dir}")
    return 0


def cmd_relay_models(args, settings: Settings) -> int:
    """List what the relay hosts, so model names and voice ids can be copied into providers.yaml.
    The video model listing is public; voice listings need RELAY_API_KEY."""
    key = os.environ.get("RELAY_API_KEY", "")
    base = (os.environ.get("RELAY_BASE_URL") or "https://api.302.ai").rstrip("/")
    grep = (args.grep or "").lower()
    out_dir = settings.data_dir / "relay"
    out_dir.mkdir(parents=True, exist_ok=True)
    with make_client(timeout=60) as http:
        try:
            listing = relay302.video_models(http, base, key)
        except ProviderError as e:
            print(f"读取视频模型列表失败：{e}")
            listing = None
        if listing is not None:
            (out_dir / "video_models.json").write_text(json.dumps(listing, ensure_ascii=False, indent=2), encoding="utf-8")
            entries = listing.get("models") if isinstance(listing, dict) else None
            if isinstance(entries, list) and all(isinstance(m, dict) and "model_name" in m for m in entries):
                rows = [(m["model_name"], m.get("provider", ""), m.get("price_text", "")) for m in entries]
            else:  # unknown layout: names only
                rows = [(n, "", "") for n in relay302.model_names(listing)]
            rows = [r for r in rows if not grep or grep in f"{r[0]} {r[1]}".lower()]
            print(f"302 统一视频接口的模型（{len(rows)} 个{'，筛选：' + args.grep if grep else ''}）")
            for name, vendor, price in rows:
                print(f"  {name:<36} {vendor:<12} {price}")
            print("Seedance、MiniMax H3、可灵官方格式走原生透传，不在这个列表里，见 providers.yaml 的 _302 条目")
            print(f"完整列表（含各模型支持的参数）：{out_dir / 'video_models.json'}")

        if not key:
            print("\n要列配音音色，先在 .env 里填 RELAY_API_KEY")
            return 0
        vendors = _split(args.tts) or ["doubao", "minimaxi"]
        try:
            providers = relay302.tts_providers(http, base, key, vendors)
        except ProviderError as e:
            print(f"\n读取配音音色失败：{e}")
            return 1
    (out_dir / "tts_providers.json").write_text(json.dumps(providers, ensure_ascii=False, indent=2), encoding="utf-8")
    for p in providers:
        info = p.get("req_params_info") or {}
        voices = [v for v in info.get("voice_list") or []
                  if not grep or grep in f"{v.get('voice', '')} {v.get('name', '')}".lower()]
        models = "、".join(info.get("model_list") or []) or "无"
        print(f"\n配音 {p.get('provider')}（音色 {len(voices)} 个，可选模型：{models}）")
        for v in voices[:args.limit]:
            print(f"  {v.get('voice', ''):<48} {v.get('name', '')} {v.get('gender', '')}")
        if len(voices) > args.limit:
            print(f"  …… 还有 {len(voices) - args.limit} 个，用 --grep 筛选或 --limit 调大")
    print(f"\n完整音色表（含试听链接）：{out_dir / 'tts_providers.json'}")
    return 0


def plan(bench: Bench, steps: tuple[str, ...]) -> dict[str, list[Job]]:
    jobs: dict[str, list[Job]] = {}
    skipped: list[tuple[str, str]] = []
    for step in steps:
        if step == "images":
            jobs[step] = bench.plan_images()
        elif step == "videos":
            jobs[step], skipped = bench.plan_videos()
        elif step == "tts":
            jobs[step] = bench.plan_speech()
        else:
            jobs[step] = bench.plan_llm()

    for step, items in jobs.items():
        print(f"\n{STEP_NAMES[step]}：{len(items)} 个任务，约 ¥{sum(j.estimate for j in items):.1f}")
        per: dict[str, list[Job]] = defaultdict(list)
        for job in items:
            per[job.provider].append(job)
        for pid, group in per.items():
            print(f"  {pid:<14} {len(group):>3} 个  约 ¥{sum(j.estimate for j in group):.1f}")
        kind = STEP_KINDS.get(step)
        for pid, reasons in (bench.inactive(kind) if kind else []):
            print(f"  {pid:<14} 未启用：{'；'.join(reasons)}")
        if step == "llm":
            ready = {pid for pid, _ in bench.llm_providers()}
            for pid, c in bench.llm_cfg.items():
                if pid not in ready and not c.get("manual") and (not bench.only or pid in bench.only):
                    print(f"  {pid:<14} 未启用：没填 model 或 .env 里没有 key")
    for name, reason in skipped:
        print(f"  跳过 {name}：{reason}")
    return jobs


def confirm(total: float, budget: float, yes: bool) -> bool:
    print(f"\n预计花费约 ¥{total:.1f}（按 providers.yaml 的参考单价估算，以各家账单为准）")
    if total > budget:
        print(f"超过预算 ¥{budget:.0f}：用 --budget 调高，或用 --cases / --providers 缩小范围")
        return False
    if yes or total == 0:
        return True
    return input("继续？[y/N] ").strip().lower() == "y"


def cmd_bench(args, settings: Settings) -> int:
    bench = Bench(settings, run=args.run, cases=_split(args.cases), providers=_split(args.providers),
                  retry_failed=args.retry_failed)
    if args.step == "report":
        print(f"报告：{write_report(bench)}")
        return 0
    if args.step == "summary":
        path = args.scores or bench.root / "scores.json"
        if not path.exists():
            print(f"找不到评分文件 {path}：先在报告页点「导出评分」，再用 --scores 指定导出的文件")
            return 2
        print(summarize(bench, load_scores(path)))
        print(f"\n已保存：{bench.root / 'summary.md'}")
        return 0

    steps = STEPS if args.step in ("plan", "run") else (args.step,)
    jobs = plan(bench, steps)
    if args.step == "plan":
        return 0
    total = sum(j.estimate for items in jobs.values() for j in items)
    budget = args.budget if args.budget is not None else settings.budget_cny
    if not confirm(total, budget, args.yes):
        return 1

    for step in steps:
        if step == "videos":
            bench.prepare_sources()
        if jobs[step]:
            print(f"\n开始：{STEP_NAMES[step]}")
            bench.execute(jobs[step])
        if step == "videos":
            bench.finish_chains()
    print(f"\n本轮累计花费约 ¥{bench.ledger.total(bench.run_name):.1f}")
    print(f"报告：{write_report(bench)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ai-video", description="AI 短视频流水线（Phase 0：模型对比测试）")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_check = sub.add_parser("check", help="检查 providers.yaml 和 .env 的配置")
    p_check.add_argument("--ping", action="store_true", help="调用一次不收费的接口，验证 key 是否有效")

    p_relay = sub.add_parser("relay-models", help="列出 302.AI 中转上的视频模型名和配音音色")
    p_relay.add_argument("--grep", help="按关键字筛选，如 seedance、kling、女")
    p_relay.add_argument("--tts", help="要列音色的配音供应商，逗号分隔，默认 doubao,minimaxi")
    p_relay.add_argument("--limit", type=int, default=30, help="每个配音供应商最多显示几个音色")

    p_bench = sub.add_parser("bench", help="Phase 0 模型对比测试")
    p_bench.add_argument("step", choices=["plan", "images", "videos", "tts", "llm", "run", "report", "summary"],
                         help="plan 只列任务和预算；run 依次跑 images → videos → tts → llm")
    p_bench.add_argument("--run", default="phase0", help="这一轮测试的名字（数据目录 data/bench/<run>）")
    p_bench.add_argument("--cases", help="只跑这些用例，逗号分隔")
    p_bench.add_argument("--providers", help="只用这些模型，逗号分隔（providers.yaml 里的 id）")
    p_bench.add_argument("--retry-failed", action="store_true", help="重新提交被拒绝或失败的任务")
    p_bench.add_argument("--budget", type=float, help="本次预算上限（元），默认取 .env 的 BENCH_BUDGET_CNY")
    p_bench.add_argument("--yes", "-y", action="store_true", help="不再询问，直接开始")
    p_bench.add_argument("--scores", type=Path, help="summary 用：报告页导出的评分文件")

    args = parser.parse_args(argv)
    # Redirected output on Chinese Windows defaults to GBK, which has no "¥"; never crash on printing.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    _setup_logging()
    settings = load_settings()
    commands = {"check": cmd_check, "relay-models": cmd_relay_models, "bench": cmd_bench}
    try:
        return commands[args.cmd](args, settings)
    except (ValueError, FileNotFoundError) as e:
        print(f"错误：{e}")
        return 2
