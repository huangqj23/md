"""命令行：ai-daily run / collect / publish / preview / llm-status / install-host。"""
import argparse
import logging
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import llm_config
from . import publish as pub
from . import render
from .collect import collect_all
from .config import load_settings, load_sources
from .llm import LLMError
from .net import make_client
from .pipeline import run
from .store import Store


def _setup_logging(log_dir: Path, day: date) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for handler in (logging.StreamHandler(sys.stdout), logging.FileHandler(log_dir / f"{day}.log", encoding="utf-8")):
        handler.setFormatter(fmt)
        root.addHandler(handler)
    for noisy in ("httpx", "httpx2", "httpcore", "openai", "anthropic", "trafilatura"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _day(value: str | None) -> date:
    return date.fromisoformat(value) if value else datetime.now().astimezone().date()


def cmd_run(args, settings, sources) -> int:
    day = _day(args.date)
    db_path = settings.db_path
    if args.out:                       # 试跑：单独的库，不影响正式运行的去重和快照
        db_path = settings.data_dir / "trial.db"
    llm = None
    if not args.no_llm:
        try:
            llm = llm_config.build_pair(settings)
        except LLMError as e:
            print(f"错误：{e}")
            return 2
    with make_client() as http:
        try:
            result = run(settings, sources, day=day, now=datetime.now(timezone.utc), http=http, llm=llm,
                         out_dir=Path(args.out) if args.out else None, force=args.force,
                         images=not args.no_images, cover=not args.no_cover, db_path=db_path)
        except FileExistsError as e:
            print(f"错误：{e}")
            return 2
    print(f"\n正文：{result.article}\n审稿清单与备选：{result.review}\n封面：{'已生成' if result.cover_ok else '未生成'}（{result.cover_json}）")
    print(f"待核对 {result.n_flags} 处。改完后运行：ai-daily publish --date {day}")
    return 0


def cmd_collect(args, settings, sources) -> int:
    """只采集并打印，不记录条目（不影响正式运行的去重）；GitHub 仓库创建时间照常缓存。"""
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=(sources.get("window") or {}).get("default_hours", 26))
    store = Store(settings.db_path)
    try:
        prev = store.previous_snapshot("openrouter", now.astimezone().date() + timedelta(days=1))
        known = store.repo_created()
        with make_client() as http:
            items, stats, _ = collect_all(http, sources, now=now, since=since, inbox_path=settings.inbox,
                                          openrouter_prev=prev, repo_created=known)
        store.save_repo_created(known)
    finally:
        store.close()
    for name, value in sorted(stats.items(), key=lambda kv: str(kv[0])):
        print(f"{name:<45} {value}")
    print(f"\n共 {len(items)} 条")
    if args.show:
        for it in items[: args.show]:
            print(f"- [{it.label}/{it.kind}] {it.title}  {it.url}")
    return 0


def cmd_publish(args, settings, sources) -> int:
    day = _day(args.date)
    article = render.paths(settings.daily_dir / f"{day:%Y-%m}", day)["article"]
    if not article.is_file():
        print(f"找不到正文：{article}")
        return 2
    problems = pub.check(article)
    if problems:
        print("发布前还需要处理：")
        for p in problems:
            print(f"  - {p}")
        return 1
    if not args.skip_links:
        with make_client(timeout=15) as http:
            bad = pub.check_links(http, [u for u, _ in pub.links(article)])
        for b in bad:
            print(f"  ! 链接打不开（请手动确认）：{b}")
    ok, out = pub.run_watermark(settings.brand_python, settings.brand_dir / "watermark.py", article)
    print(out)
    if not ok:
        print("内嵌版生成失败或有图片找不到，先处理上面的提示。")
        return 1
    store = Store(settings.db_path)
    n = pub.mark_published(store, day, article)
    store.close()
    print(f"\n已记录 {n} 个链接，之后 7 天不会重复选题。")
    print("下一步：用 md 打开内嵌版，发布到公众号草稿箱；发表时在创作声明里选 AI 相关的声明，不声明原创。")
    return 0


def cmd_llm_status(args, settings, sources) -> int:
    """当前生效的模型配置（key 只显示首尾几位）。"""
    view = llm_config.public_view(settings)
    ready = view["readiness"]
    source = {"file": str(llm_config.config_path(settings)), "env": ".env 的 LLM_*", "none": "未配置"}[view["source"]]
    print(f"配置来源：{source}（key 加密：{view['encryption']}）")
    for role in llm_config.ROLES:
        print(f"{llm_config.ROLE_NAMES[role]}：{ready[role] or '未配好'}")
    for p in view["providers"]:
        if p["has_key"] or p["custom"]:
            print(f"  - {p['name']}（{p['id']}）{p['base_url']}  key {p['key_hint'] or '无'}")
    for problem in ready["problems"]:
        print(f"! {problem}")
    return 0 if ready["ready"] else 1


def cmd_preview(args, settings, sources) -> int:
    from . import preview
    day = _day(args.date)
    article = render.paths(settings.daily_dir / f"{day:%Y-%m}", day)["article"]
    if not article.is_file():
        print(f"找不到正文：{article}")
        return 2
    out = Path(args.out) if args.out else settings.data_dir / "previews" / f"{day.isoformat()}_AI日报预览.html"
    info = preview.build(article, out, mark=settings.brand_dir / "hollis23-mark.svg")
    print(f"预览页：{out}（{info['bytes'] // 1024} KB）")
    print(f"标题：{info['title']}")
    print(f"头条 1 + 要闻 {info['items']} + 快讯 {info['briefs']}；配图 {info['images']} / {info['items'] + 1}；"
          f"待核对 {info['flags']} 处；我的看法{'待写' if info['opinion_todo'] else '已写'}")
    return 0


def cmd_install_host(args, settings, sources) -> int:
    from . import host_install
    browsers = list(host_install.BROWSERS) if args.browser == "all" else [args.browser]
    ids = args.extension_id or [host_install.default_extension_id()]
    if not args.extension_id:
        print(f"扩展 ID：{ids[0]}（从 {host_install.EXTENSION_DIR} 加载扩展时的 ID）")
    done = host_install.install(ids, browsers)
    if not done:
        print(f"没有找到已安装的浏览器（{', '.join(browsers)}），没有注册")
        return 1
    print(f"已注册 Native Messaging host（{', '.join(done)}）：{host_install.MANIFEST}")
    print("回到 md 扩展的“AI 早报”面板点“重新检测”。")
    return 0


def cmd_uninstall_host(args, settings, sources) -> int:
    from . import host_install
    host_install.uninstall(list(host_install.BROWSERS))
    print("已注销 Native Messaging host")
    return 0


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")   # Windows 控制台默认 GBK，emoji 会报错
    ap = argparse.ArgumentParser(prog="ai-daily", description="hollis23 AI 早报")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="采集 → 选题 → 写初稿，落到 Obsidian")
    r.add_argument("--date", help="稿件日期，默认今天（YYYY-MM-DD）")
    r.add_argument("--no-llm", action="store_true", help="不调用 LLM：启发式选题，摘要用原文节选（试跑流程用）")
    r.add_argument("--out", help="输出目录（默认 <vault>/AI_Daily/YYYY-MM）；指定时用单独的试跑库")
    r.add_argument("--force", action="store_true", help="覆盖当天已有的正文（旧稿备份为 .bak）")
    r.add_argument("--no-images", action="store_true", help="不下载配图")
    r.add_argument("--no-cover", action="store_true", help="不生成封面")
    c = sub.add_parser("collect", help="只采集并打印各信源条数（不写库）")
    c.add_argument("--show", type=int, default=0, help="打印前 N 条")
    p = sub.add_parser("publish", help="发布前检查 + 生成内嵌版 + 记录已发链接")
    p.add_argument("--date", help="稿件日期，默认今天")
    p.add_argument("--skip-links", action="store_true", help="不检查链接能否打开")
    h = sub.add_parser("install-host", help="注册 Native Messaging host，让 md 扩展的“AI 早报”面板能调用本机")
    h.add_argument("--extension-id", action="append",
                   help="md 扩展的 ID（面板里会显示；可重复）。不写时按 apps/web/.output/chrome-mv3 的路径算出来")
    h.add_argument("--browser", choices=["chrome", "edge", "chromium", "doubao", "all"], default="all")
    sub.add_parser("uninstall-host", help="注销 Native Messaging host")
    sub.add_parser("llm-status", help="查看当前的模型配置（选题 / 写稿用哪家、哪个模型）")
    v = sub.add_parser("preview", help="把当天的草稿渲染成自带图片的 HTML 预览页")
    v.add_argument("--date", help="稿件日期，默认今天")
    v.add_argument("--out", help="输出文件（默认 data/previews/<日期>_AI日报预览.html）")
    args = ap.parse_args(argv)

    settings, sources = load_settings(), load_sources()
    _setup_logging(settings.log_dir, _day(getattr(args, "date", None)))
    handler = {"run": cmd_run, "collect": cmd_collect, "publish": cmd_publish,
               "install-host": cmd_install_host, "uninstall-host": cmd_uninstall_host,
               "llm-status": cmd_llm_status, "preview": cmd_preview}[args.cmd]
    return handler(args, settings, sources)
