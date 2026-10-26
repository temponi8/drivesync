import pytest

from drivesync import config as cfgmod
from drivesync import daemon
from drivesync.config import Config
from drivesync.daemon import Coordinator, State
from drivesync.syncer import Outcome, SyncResult


@pytest.fixture(autouse=True)
def tmp_state(tmp_path, monkeypatch):
    monkeypatch.setattr(cfgmod, "STATE_DIR", tmp_path)
    monkeypatch.setattr(cfgmod, "STATUS_FILE", tmp_path / "status.json")
    monkeypatch.setattr(daemon.notify, "send", lambda *a, **k: None)


class Harness:
    """Runs posts immediately and keeps workers queued so tests control when syncs finish."""

    def __init__(self, results):
        self.results = list(results)
        self.workers = []
        self.runs = 0
        self.coord = Coordinator(Config(), post=lambda fn: fn(), start_worker=self.workers.append, run=self._run)

    def _run(self, _cfg):
        self.runs += 1
        return self.results.pop(0) if self.results else SyncResult(Outcome.OK, 0)

    def finish_one(self):
        self.workers.pop(0)()


def test_requests_during_sync_merge_into_one_followup():
    h = Harness([])
    h.coord.request("a")
    h.coord.request("b")
    h.coord.request("c")
    assert len(h.workers) == 1
    h.finish_one()
    assert len(h.workers) == 1  # exactly one follow-up
    h.finish_one()
    assert h.workers == [] and h.runs == 2
    assert h.coord.status.state == State.IDLE


def test_paused_defers_until_resume():
    h = Harness([])
    h.coord.set_paused(True)
    h.coord.request("x")
    assert h.workers == [] and h.coord.status.state == State.PAUSED
    h.coord.set_paused(False)
    assert len(h.workers) == 1


def test_offline_then_online():
    h = Harness([])
    h.coord.set_online(False)
    h.coord.request("x")
    assert h.workers == [] and h.coord.status.state == State.OFFLINE
    h.coord.set_online(True)
    assert len(h.workers) == 1


def test_conflicts_and_errors_update_state():
    h = Harness([SyncResult(Outcome.CONFLICTS, 0, ["f.txt"], "1 conflict"),
                 SyncResult(Outcome.ERROR, 1, message="boom")])
    h.coord.request()
    h.finish_one()
    assert h.coord.status.state == State.CONFLICTS and h.coord.status.conflicts == ["f.txt"]
    h.coord.request()
    h.finish_one()
    assert h.coord.status.state == State.ERROR and h.coord.status.last_error == "boom"


def test_worker_exception_becomes_error():
    h = Harness([])
    h.coord.run = lambda _cfg: 1 / 0
    h.coord.request()
    h.finish_one()
    assert h.coord.status.state == State.ERROR


def test_auth_expired_state():
    h = Harness([SyncResult(Outcome.AUTH_EXPIRED, 1, message="Google sign-in expired")])
    h.coord.request()
    h.finish_one()
    assert h.coord.status.state == State.AUTH_EXPIRED
