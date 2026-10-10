"""Command line: ai-video check / quota / new / script / make / sheet / projects / bench.

Scripts are written in the conversation (the short-video skill) and imported with `new --script`;
the CLI never calls a language model.
"""
import argparse
import logging
import os
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path

import httpx

from . import quota
from .bench.report import write_report
from .bench.runner import Bench, Job
from .bench.summary import load_scores, summarize
from .config import Settings, load_settings, load_yaml
from .errors import ProviderError
from .ledger import Ledger
from .net import call, make_client
from .pipeline.project import STAGES
from .pipeline.script import load_script
from .pipeline.sheet import contact_sheet
from .pipeline.stages import STAGE_NAMES, Producer
from .providers import load_providers

KIND_NAMES = {"video": "视频", "image": "图片", "tts": "配音"}
STEPS = ("images", "videos", "tts")
STEP_NAMES = {"images": "关键帧图片", "videos": "视频", "tts": "配音"}
STEP_KINDS = {"images": "image", "videos": "video", "tts": "tts"}
PAID_STAGES = ("characters", "keyframes", "videos")


def _setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _split(value: str | None) -> list[str] | None:
    return [v.strip() for v in value.split(",") if v.strip()] if value else None


def cmd_check(args, settings: Settings) -> int:
    cfg = load_yaml(settings.providers_file)
    providers = load_providers(cfg)
    for kind, items in providers.items():
        print(f"\n{KIND_NAMES[kind]}")
        for pid, p in items.items():
            if not p.ready:
                status = "未就绪：" + "；".join(p.missing())
            elif args.ping:
                with p.client(timeout=30) as http:
                    try:
                        status = p.ping(http)
                    except (ProviderError, httpx.HTTPError) as e:
                        status = f"连接失败：{e}"
            else:
                status = "就绪"
            print(f"  {pid:<14} {p.label:<20} {p.model or '-':<28} {status}")
            for warning in p.warnings() if p.ready else []:
                print(f"  {'':<14} ! {warning}")
    routing = cfg.get("routing") or {}
    print(f"\n路由：关键帧 {routing.get('image')}，视频 {routing.get('video')}，配音 {routing.get('tts')}")
    print(f"ffmpeg：{'已安装' if shutil.which('ffmpeg') else '未安装（无法合成成片）'}")
    print(f"数据目录：{settings.data_dir}")
    return 0


def cmd_quota(args, settings: Settings) -> int:
    plans = load_yaml(settings.providers_file).get("plans") or {}
    if not plans:
        print("providers.yaml 里没有 plans 段")
        return 0
    ledger = Ledger(settings.data_dir / "ledger.jsonl")
    for pid, plan in plans.items():
        print(f"\n{plan.get('label', pid)}")
        if plan.get("daily_afp") or plan.get("monthly_afp"):
            u = quota.usage(ledger, pid, plan)
            print(f"  今日已用 {u['today']:,.0f} / {u['daily']:,.0f} AFP，本期已用 {u['cycle']:,.0f} / {u['monthly']:,.0f} AFP"
                  f"（按本机账本；控制台的明细有 0.5–1 天延迟）")
        if plan.get("remains_url"):
            key = os.environ.get(plan.get("key_env", ""), "")
            if not key:
                print(f"  .env 里没有 {plan.get('key_env')}，查不了剩余额度")
                continue
            with make_client(timeout=30) as http:
                try:
                    payload = call(http, "GET", plan["remains_url"], headers={"Authorization": f"Bearer {key}"}).json()
                except ProviderError as e:
                    print(f"  查询失败：{e}")
                    continue
            for line in quota.remains_lines(payload):
                print(line)
    return 0


def _clear_for_redo(ws, project, ids: list[str], done: str) -> None:
    characters = {c["id"] for c in project.characters}
    for rid in ids:
        if rid in characters:
            cleared = ws.clear_character(project, rid)
            print(f"{done}角色 {rid} 的参考图" + (f"，以及用到它的关键帧：{', '.join(cleared)}" if cleared else ""))
            continue
        shot = project.shot(rid)
        ws.clear_shot(shot)
        print(f"{done} {rid} 的配音、关键帧和视频，会重新生成")
        index = project.shots.index(shot)
        if index + 1 < len(project.shots) and "prev" in project.shots[index + 1].refs:
            print(f"  提示：{project.shots[index + 1].id} 以它为参考（prev），需要的话也一起 --redo")


def _run_make(producer: Producer, ws, args, settings: Settings) -> int:
    project = ws.load()
    producer.draft = args.draft
    # --estimate deletes nothing: the clears below only mark files as gone so the estimate prices them
    ws.dry_run = args.estimate
    done = "将清除" if args.estimate else "已清除"
    _clear_for_redo(ws, project, _split(args.redo) or [], done)
    revideo = _split(args.revideo) or []
    for shot in project.shots if revideo == ["all"] else [project.shot(sid) for sid in revideo]:
        ws.clear_clip(shot)
        print(f"{done} {shot.id} 的视频，会用当前路由重新生成")
    if not args.estimate:
        ws.save(project)
    draft_id = (producer.routing.get("video") or {}).get("draft")
    drafts = [s.id for s in project.shots if draft_id and s.provider == draft_id and ws.has(ws.clip(s))]
    if drafts and not args.draft:
        print(f"注意：{', '.join(drafts)} 还是草稿模型（{draft_id}）出的片段，合成会直接用它们；"
              f"要出正式版就加 --revideo {','.join(drafts)}")
    until = args.until
    producer.make(ws, until="script")
    project = ws.load()
    print(f"\n《{project.title}》{len(project.shots)} 个镜头，分镜表：{ws.root / 'storyboard.md'}")
    issues = [i for i in (project.check or {}).get("issues") or [] if not i.get("resolved")]
    for issue in issues:
        print(f"  ! 事实核查 {issue.get('shot', '')}：{issue.get('problem', '')}（建议：{issue.get('fix', '')}）")
    if until == "script" and not args.estimate:
        print("看过分镜后继续：ai-video make " + project.id)
        return 0
    run = STAGES if args.estimate else STAGES[:STAGES.index(until) + 1]
    paid = [s for s in PAID_STAGES if s in run]
    cost = producer.estimate(ws, project)
    cny = sum(cost[s][0] for s in paid)
    need = sum((cost[s][1] for s in paid), Counter())
    parts = [f"{STAGE_NAMES[s]} ¥{cost[s][0]}" for s in paid if cost[s][0]]
    print("\n预计：" + ("，".join(parts) if parts else "没有要花钱的步骤（配音另计，很少）"))
    lines, ok = quota.afp_gate(producer.plans, producer.ledger, need)
    if args.estimate:
        for line in lines:
            print(line)
        return 0
    budget = args.budget if args.budget is not None else settings.budget_cny
    if not confirm(cny, budget, args.yes, lines, ok):
        return 1
    report = producer.make(ws, until=until, bgm=args.bgm)
    spent = producer.ledger.total(project.id)
    print(f"\n本项目累计花费约 ¥{spent:.1f}")
    if report.get("final"):
        print(f"成片：{report['final']}")
        print(f"发布信息：{ws.out / 'publish.md'}")
    if report["problems"]:
        print(f"有 {len(report['problems'])} 个问题，处理后用 --redo 重做对应镜头")
    return 0


def cmd_new(args, settings: Settings) -> int:
    producer = Producer(settings)
    ws, warnings = producer.new_from_script(load_script(args.script))
    print(f"新项目 {ws.root.name}：{ws.root}")
    for warning in warnings:
        print(f"  ? {warning}")
    return _run_make(producer, ws, args, settings)


def cmd_make(args, settings: Settings) -> int:
    producer = Producer(settings)
    return _run_make(producer, producer.open(args.project), args, settings)


def cmd_script(args, settings: Settings) -> int:
    producer = Producer(settings)
    ws = producer.open(args.project)
    changes, warnings = producer.import_script(ws, load_script(args.file))
    for warning in warnings:
        print(f"  ? {warning}")
    rows = [("角色参考图", changes.characters), ("配音和视频", changes.audio), ("关键帧和视频", changes.keyframes),
            ("只重做视频", changes.clips), ("删掉的镜头", changes.removed)]
    if not changes:
        print("剧本已更新，没有要重做的产物")
    for name, ids in rows:
        if ids:
            print(f"  {name}：{', '.join(ids)}")
    print(f"分镜表：{ws.root / 'storyboard.md'}\n继续制作：ai-video make {ws.root.name}")
    return 0


def cmd_sheet(args, settings: Settings) -> int:
    producer = Producer(settings)
    ws = producer.open(args.project)
    print(f"联系表：{contact_sheet(ws, ws.load(), args.what)}")
    return 0


def cmd_projects(args, settings: Settings) -> int:
    producer = Producer(settings)
    for project in producer.list_projects():
        final = producer.projects_dir / project.id / "out" / "final.mp4"
        state = "已出片" if final.exists() else (f"{len(project.shots)} 个镜头" if project.shots else "没有剧本")
        print(f"  {project.id}  {project.aspect:<5} {state:<8} {project.title or project.prompt}")
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

    for step, items in jobs.items():
        print(f"\n{STEP_NAMES[step]}：{len(items)} 个任务，约 ¥{sum(j.estimate for j in items):.1f}")
        per: dict[str, list[Job]] = defaultdict(list)
        for job in items:
            per[job.provider].append(job)
        for pid, group in per.items():
            print(f"  {pid:<14} {len(group):>3} 个  约 ¥{sum(j.estimate for j in group):.1f}")
        for pid, reasons in bench.inactive(STEP_KINDS[step]):
            print(f"  {pid:<14} 未启用：{'；'.join(reasons)}")
    for name, reason in skipped:
        print(f"  跳过 {name}：{reason}")
    return jobs


def confirm(total: float, budget: float, yes: bool, afp_lines: list[str] = (), afp_ok: bool = True) -> bool:
    print(f"\n预计花费约 ¥{total:.1f}（按 providers.yaml 的单价和套餐抵扣系数估算，以各家账单为准）")
    for line in afp_lines:
        print(line)
    if not afp_ok:
        print("超过套餐额度，没有执行（套餐请保持关闭「超额后付费」，避免额外账单）")
        return False
    if total > budget:
        print(f"超过预算 ¥{budget:.0f}：用 --budget 调高，或缩小范围")
        return False
    if yes or total == 0:
        return True
    try:
        return input("继续？[y/N] ").strip().lower() == "y"
    except EOFError:
        print("没有交互输入：看过预计花费后加 -y 重新运行")
        return False


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
    all_jobs = [j for items in jobs.values() for j in items]
    need: Counter = Counter()
    for job in all_jobs:
        if job.plan:
            need[job.plan] += job.afp
    lines, ok = quota.afp_gate(bench.plans, bench.ledger, need)
    budget = args.budget if args.budget is not None else settings.budget_cny
    if not confirm(sum(j.estimate for j in all_jobs), budget, args.yes, lines, ok):
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
    parser = argparse.ArgumentParser(prog="ai-video", description="AI 短视频流水线：导入剧本出片（new / script / make），以及模型对比测试（bench）")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_check = sub.add_parser("check", help="检查 providers.yaml 和 .env 的配置")
    p_check.add_argument("--ping", action="store_true", help="调用一次不收费的接口，验证 key 是否有效")
    sub.add_parser("quota", help="套餐额度：方舟今日 / 本期已用（按本机账本），MiniMax 剩余额度")

    def add_make_args(p):
        p.add_argument("--until", choices=STAGES, default="compose",
                       help="做到哪一步为止：script 只看分镜，characters 先看角色参考图，keyframes 先看关键帧")
        p.add_argument("--redo", help="重做这些镜头或角色（逗号分隔，如 s03,shusheng）：镜头清掉配音、关键帧和视频，"
                                      "角色清掉参考图和用到它的关键帧")
        p.add_argument("--revideo", help="只重做这些镜头的视频（逗号分隔，或 all），配音和关键帧保留；可用来把草稿升级成正式版")
        p.add_argument("--draft", action="store_true",
                       help="草稿模式：所有镜头用 routing.video.draft（便宜的模型）先出一版，看节奏再升级")
        p.add_argument("--bgm", type=Path, help="背景音乐文件，自动压在人声下面")
        p.add_argument("--budget", type=float, help="本次预算上限（元），默认取 .env 的 BENCH_BUDGET_CNY")
        p.add_argument("--estimate", action="store_true", help="只打印还要花的钱和套餐额度，不执行")
        p.add_argument("--yes", "-y", action="store_true", help="不再询问，直接开始")

    p_new = sub.add_parser("new", help="导入剧本 JSON（Claude 在对话里写好的）新建项目并开始制作")
    p_new.add_argument("--script", type=Path, required=True, help="剧本 JSON 文件，格式见 short-video 技能")
    add_make_args(p_new)

    p_script = sub.add_parser("script", help="给已有项目导入修改后的剧本，只重做改动过的镜头")
    p_script.add_argument("project", help="项目 id（latest 表示最近一个）")
    p_script.add_argument("file", type=Path, help="修改后的剧本 JSON")

    p_make = sub.add_parser("make", help="继续制作已有项目（每一步都可以重跑）")
    p_make.add_argument("project", nargs="?", default="latest", help="项目 id，默认最近一个")
    add_make_args(p_make)

    p_sheet = sub.add_parser("sheet", help="生成联系表图片：全部关键帧、视频中间帧或成片抽帧")
    p_sheet.add_argument("project", nargs="?", default="latest", help="项目 id，默认最近一个")
    p_sheet.add_argument("--what", choices=["keyframes", "clips", "final"], default="keyframes")

    sub.add_parser("projects", help="列出成片项目")

    p_bench = sub.add_parser("bench", help="模型对比测试")
    p_bench.add_argument("step", choices=["plan", "images", "videos", "tts", "run", "report", "summary"],
                         help="plan 只列任务和预算；run 依次跑 images → videos → tts")
    p_bench.add_argument("--run", default="round2",
                         help="这一轮测试的名字（数据目录 data/bench/<run>）；phase0 是之前的测试结果，别覆盖")
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
    commands = {"check": cmd_check, "quota": cmd_quota, "bench": cmd_bench, "new": cmd_new, "script": cmd_script,
                "make": cmd_make, "sheet": cmd_sheet, "projects": cmd_projects}
    try:
        return commands[args.cmd](args, settings)
    except (ValueError, FileNotFoundError) as e:
        print(f"错误：{e}")
        return 2
