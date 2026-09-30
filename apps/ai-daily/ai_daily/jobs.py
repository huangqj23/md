"""后台任务：浏览器扩展触发的 run / publish 在独立进程里跑，状态写到 data/jobs/state.json。

Chrome 在 native host 断开 2 秒后会杀掉它所在的 Job 对象（连同子进程），所以任务进程要用
CREATE_BREAKAWAY_FROM_JOB 脱离出来，关掉面板也不会中断正在生成的草稿。
"""
import ctypes
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from .config import ROOT

KINDS = {"run", "publish"}
LOG_TAIL_CHARS = 4000

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
CREATE_NO_WINDOW = 0x08000000


def _state_path(jobs_dir: Path) -> Path:
    return jobs_dir / "state.json"


def read_state(jobs_dir: Path) -> dict | None:
    try:
        return json.loads(_state_path(jobs_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_state(jobs_dir: Path, state: dict) -> None:
    jobs_dir.mkdir(parents=True, exist_ok=True)
    tmp = _state_path(jobs_dir).with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, _state_path(jobs_dir))


def pid_alive(pid: int) -> bool:
    if not pid:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x1000, False, pid)        # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259   # STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def status(jobs_dir: Path) -> dict:
    """{"state": none|running|done|failed, ...state 字段, "log_tail": 日志末尾}。
    进程没写完成标记就没了（被杀、崩溃），算 failed。"""
    state = read_state(jobs_dir)
    if not state:
        return {"state": "none"}
    out = dict(state)
    if state.get("finished") is None:
        if pid_alive(state.get("pid", 0)):
            out["state"] = "running"
        else:
            out.update(state="failed", error="任务进程已退出，但没有写完成标记（可能被中断）")
    else:
        out["state"] = "done" if state.get("returncode") == 0 else "failed"
    try:
        log = Path(state["log"]).read_text(encoding="utf-8", errors="replace")
        out["log_tail"] = log[-LOG_TAIL_CHARS:]
    except (OSError, KeyError):
        out["log_tail"] = ""
    return out


def start(kind: str, args: list[str], jobs_dir: Path, python: str | None = None) -> dict:
    """同一时间只跑一个任务。返回新任务的 status()。"""
    if kind not in KINDS:
        raise ValueError(f"未知任务类型：{kind}")
    current = status(jobs_dir)
    if current["state"] == "running":
        raise RuntimeError(f"已有任务在运行：{current.get('kind')}（{current.get('started')} 开始）")
    jobs_dir.mkdir(parents=True, exist_ok=True)
    job_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    log = jobs_dir / f"{job_id}-{kind}.log"
    state = {"id": job_id, "kind": kind, "args": args, "started": datetime.now().isoformat(timespec="seconds"),
             "finished": None, "returncode": None, "log": str(log), "pid": 0}
    _write_state(jobs_dir, state)
    cmd = [python or sys.executable, "-m", "ai_daily.jobs", str(jobs_dir), kind, *args]
    kw = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
          "cwd": str(ROOT), "close_fds": True}
    if os.name == "nt":
        flags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
        try:
            proc = subprocess.Popen(cmd, creationflags=flags | CREATE_BREAKAWAY_FROM_JOB, **kw)
        except OSError:          # 所在 Job 不允许脱离（不是被 Chrome 拉起时）：普通方式启动
            proc = subprocess.Popen(cmd, creationflags=flags, **kw)
    else:
        proc = subprocess.Popen(cmd, start_new_session=True, **kw)
    state["pid"] = proc.pid
    _write_state(jobs_dir, state)
    return status(jobs_dir)


def _worker(jobs_dir: Path, kind: str, args: list[str]) -> int:
    """在任务进程里执行 cli 命令，输出写日志，结束时写完成标记。"""
    state = read_state(jobs_dir) or {}
    returncode = 1
    with open(state.get("log", jobs_dir / "orphan.log"), "a", encoding="utf-8") as log:
        sys.stdout = sys.stderr = log
        try:
            from .cli import main
            returncode = main([kind, *args]) or 0
        except SystemExit as e:
            returncode = e.code if isinstance(e.code, int) else 1
        except Exception:  # noqa: BLE001 —— 任何异常都要落到日志和完成标记里
            import traceback
            traceback.print_exc()
            returncode = 1
        finally:
            log.flush()
    state.update(finished=datetime.now().isoformat(timespec="seconds"), returncode=returncode)
    _write_state(jobs_dir, state)
    return returncode


if __name__ == "__main__":
    sys.exit(_worker(Path(sys.argv[1]), sys.argv[2], sys.argv[3:]))
