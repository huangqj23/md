"""Plan quotas: credits (AFP) used today and in the current billing month from the local ledger,
checked before a paid run, and the MiniMax M Plan remains endpoint.

The Ark Agent Plan resets its daily limit at midnight and its monthly limit on the subscription
day (`cycle_day`); both are taken in Beijing time. The ledger only knows this tool's own calls, so
credits spent elsewhere (console, other agents) are not counted and the check is optimistic.
"""
import json
from datetime import datetime, timedelta, timezone

from .ledger import Ledger

BEIJING = timezone(timedelta(hours=8))


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(BEIJING)


def day_start(now: datetime | None = None) -> datetime:
    return _now(now).replace(hour=0, minute=0, second=0, microsecond=0)


def cycle_start(cycle_day: int = 1, now: datetime | None = None) -> datetime:
    """Start of the current billing month; days after the 28th are treated as the 28th."""
    n = _now(now)
    day = max(1, min(int(cycle_day or 1), 28))
    start = n.replace(day=day, hour=0, minute=0, second=0, microsecond=0)
    if start > n:
        start = (start.replace(day=1) - timedelta(days=1)).replace(day=day)
    return start


def usage(ledger: Ledger, plan_id: str, plan: dict, now: datetime | None = None) -> dict:
    return {
        "today": ledger.afp(plan_id, day_start(now).timestamp()),
        "cycle": ledger.afp(plan_id, cycle_start(plan.get("cycle_day", 1), now).timestamp()),
        "daily": float(plan.get("daily_afp") or 0),
        "monthly": float(plan.get("monthly_afp") or 0),
    }


def afp_gate(plans: dict, ledger: Ledger, need: dict[str, float],
             now: datetime | None = None) -> tuple[list[str], bool]:
    """Lines describing the credits a run needs against each plan's limits, and whether it fits."""
    lines, ok = [], True
    for plan_id, afp in need.items():
        if not afp:
            continue
        plan = plans.get(plan_id) or {}
        u = usage(ledger, plan_id, plan, now)
        cny = afp * float(plan.get("cny_per_afp") or 0)
        lines.append(f"{plan.get('label', plan_id)}：本次约 {afp:,.0f} AFP（≈¥{cny:.1f}）；"
                     f"今日已用 {u['today']:,.0f} / {u['daily']:,.0f}，本期已用 {u['cycle']:,.0f} / {u['monthly']:,.0f}"
                     f"（按本机账本）")
        if u["daily"] and u["today"] + afp > u["daily"]:
            ok = False
            lines.append("  超过今日额度：明天再跑，或减少镜头、改用草稿模式")
        if u["monthly"] and u["cycle"] + afp > u["monthly"]:
            ok = False
            lines.append("  超过本期额度：等下个计费周期，或升级套餐")
    return lines, ok


def _when(ms) -> str:
    try:
        return datetime.fromtimestamp(int(ms) / 1000, BEIJING).strftime("%m-%d %H:%M")
    except (TypeError, ValueError, OSError):
        return "?"


def remains_lines(payload: dict) -> list[str]:
    """MiniMax M Plan remains: the 5-hour and weekly windows when the response has them (the layout
    is not documented), otherwise every field as returned."""
    base = payload.get("base_resp") or {}
    if base.get("status_code") not in (0, None):
        return [f"查询失败：{base.get('status_code')} {base.get('status_msg', '')}"]
    items = payload.get("model_remains")
    if isinstance(items, list) and items:
        lines = []
        for item in items:
            if not isinstance(item, dict):
                continue
            if "current_weekly_remaining_percent" in item:
                lines.append(f"  {item.get('model_name') or '额度'}：5 小时窗口剩余 {item.get('current_interval_remaining_percent')}%"
                             f"（{_when(item.get('end_time'))} 刷新），周窗口剩余 {item.get('current_weekly_remaining_percent')}%"
                             f"（{_when(item.get('weekly_end_time'))} 刷新）；视频只受周窗口限制")
            else:
                lines.append("  " + "，".join(f"{k}={v}" for k, v in item.items()))
        return lines
    return ["  " + json.dumps({k: v for k, v in payload.items() if k != "base_resp"}, ensure_ascii=False)[:600]]
