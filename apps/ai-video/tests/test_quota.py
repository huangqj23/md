import json
from datetime import datetime

from ai_video import quota
from ai_video.cli import confirm
from ai_video.ledger import Ledger

PLANS = {"ark": {"label": "Ark", "cny_per_afp": 0.002, "daily_afp": 1000, "monthly_afp": 3000, "cycle_day": 9}}
NOW = datetime(2026, 10, 20, 15, 0, tzinfo=quota.BEIJING)


def test_windows_use_beijing_midnight_and_the_cycle_day():
    assert quota.day_start(NOW) == datetime(2026, 10, 20, tzinfo=quota.BEIJING)
    assert quota.cycle_start(9, NOW) == datetime(2026, 10, 9, tzinfo=quota.BEIJING)
    assert quota.cycle_start(25, NOW) == datetime(2026, 9, 25, tzinfo=quota.BEIJING)  # not reached this month yet
    assert quota.cycle_start(31, datetime(2026, 3, 5, tzinfo=quota.BEIJING)) == datetime(2026, 2, 28, tzinfo=quota.BEIJING)


def test_afp_gate_refuses_over_daily_and_monthly_limits(tmp_path):
    ledger = Ledger(tmp_path / "ledger.jsonl")
    today = NOW.replace(hour=9).timestamp()
    earlier = datetime(2026, 10, 12, tzinfo=quota.BEIJING).timestamp()
    records = [{"ts": today, "cny": 1.2, "plan": "ark", "afp": 600},
               {"ts": earlier, "cny": 3.0, "plan": "ark", "afp": 1500},
               {"ts": today, "cny": 2.0}]  # not billed through a plan
    ledger.path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    lines, ok = quota.afp_gate(PLANS, ledger, {"ark": 300}, now=NOW)
    assert ok and "今日已用 600 / 1,000" in lines[0] and "本期已用 2,100 / 3,000" in lines[0]
    lines, ok = quota.afp_gate(PLANS, ledger, {"ark": 500}, now=NOW)
    assert not ok and any("今日额度" in line for line in lines)
    lines, ok = quota.afp_gate({"ark": {**PLANS["ark"], "daily_afp": 5000}}, ledger, {"ark": 1000}, now=NOW)
    assert not ok and any("本期额度" in line for line in lines)
    assert quota.afp_gate(PLANS, ledger, {}, now=NOW) == ([], True)


def test_confirm_declines_without_a_terminal(monkeypatch, capsys):
    def no_terminal(prompt):
        raise EOFError

    monkeypatch.setattr("builtins.input", no_terminal)
    assert confirm(5.0, 100, yes=False) is False
    assert "-y" in capsys.readouterr().out
    assert confirm(5.0, 100, yes=True) is True
    assert confirm(5.0, 100, yes=True, afp_lines=["x"], afp_ok=False) is False
    assert confirm(500.0, 100, yes=True) is False


def test_remains_lines_print_each_model():
    payload = {"base_resp": {"status_code": 0}, "model_remains": [{"model_name": "MiniMax-H3", "remains": 12}]}
    assert quota.remains_lines(payload) == ["  model_name=MiniMax-H3，remains=12"]
    windows = {"model_name": "general", "current_interval_remaining_percent": 100, "end_time": 1791570438662,
               "current_weekly_remaining_percent": 87, "weekly_end_time": 1792157238662}
    line = quota.remains_lines({"model_remains": [windows]})[0]
    assert "5 小时窗口剩余 100%" in line and "周窗口剩余 87%" in line and "10-" in line
    assert "查询失败" in quota.remains_lines({"base_resp": {"status_code": 1004, "status_msg": "auth"}})[0]
    assert "plan" in quota.remains_lines({"base_resp": {"status_code": 0}, "plan": "Build"})[0]


def test_a_damaged_ledger_line_does_not_block_the_quota_check(tmp_path):
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.path.write_text('{"ts": 1, "plan": "ark", "afp": 100}\n{"ts": 2, "plan": "ark", "af\n', encoding="utf-8")
    assert ledger.afp("ark") == 100 and len(ledger.entries()) == 1
