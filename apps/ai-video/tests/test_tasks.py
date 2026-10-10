from pathlib import Path

from ai_video.errors import ProviderError, Unconfirmed
from ai_video.providers.base import TaskStatus, VideoProvider, VideoRequest
from ai_video.tasks import read_json, run_video


class FakeVideo(VideoProvider):
    def __init__(self, submit_error=None):
        super().__init__("fake", {"model": "m"})
        self.submit_error = submit_error
        self.submitted = 0
        self.polls = 0

    def submit(self, http, req):
        self.submitted += 1
        if self.submit_error:
            raise self.submit_error
        return f"t{self.submitted}"

    def poll(self, http, task_id):
        self.polls += 1
        return TaskStatus("done", url=f"https://cdn/{task_id}.mp4", usage={"completion_tokens": 100})


def _run(p, tmp_path: Path, fetch, charges: list, unconfirmed: list | None = None):
    return run_video(p, VideoRequest("p"), tmp_path / "clip.mp4", tmp_path / "job.json", {"est_cny": 1.0},
                     poll_seconds=0, timeout_seconds=5, fetch=fetch, on_done=lambda st: charges.append(st["task_id"]),
                     on_unconfirmed=(lambda st: unconfirmed.append(st["state"])) if unconfirmed is not None else None)


def test_failed_download_is_charged_once_and_fetched_again_without_resubmitting(tmp_path):
    p, charges, attempts = FakeVideo(), [], []

    def flaky_fetch(http, url, out):
        attempts.append(url)
        if len(attempts) == 1:
            raise ProviderError("下载失败：connection reset")
        out.write_bytes(b"mp4")

    first = _run(p, tmp_path, flaky_fetch, charges)
    assert first["state"] == "pending" and first["charged"] is True and "下载失败" in first["message"]
    assert charges == ["t1"]  # billed by the provider, so recorded even though the file is missing
    second = _run(p, tmp_path, flaky_fetch, charges)
    assert second["state"] == "done" and (tmp_path / "clip.mp4").exists()
    assert p.submitted == 1 and charges == ["t1"] and len(attempts) == 2


def test_unconfirmed_submit_is_recorded_and_never_resent(tmp_path):
    p, charges, unconfirmed = FakeVideo(submit_error=Unconfirmed("连接中断")), [], []
    state = _run(p, tmp_path, lambda http, url, out: None, charges, unconfirmed)
    assert state["state"] == "unconfirmed" and unconfirmed == ["unconfirmed"]
    p.submit_error = None
    again = _run(p, tmp_path, lambda http, url, out: None, charges, unconfirmed)
    assert again["state"] == "unconfirmed" and p.submitted == 1  # waits for the user to check the console
    assert read_json(tmp_path / "job.json")["state"] == "unconfirmed"
