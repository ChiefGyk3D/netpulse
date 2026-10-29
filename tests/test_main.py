import subprocess
import sys
from pathlib import Path

import pytest
import requests
import speedtest_runner
from conftest import FakeResponse


@pytest.fixture
def wired(monkeypatch, runner):
    """main() with the runner, readiness check and scheduler stubbed."""
    state = {"cycles": 0, "sleeps": []}
    monkeypatch.setattr(speedtest_runner, "wait_for_influxdb", lambda: True)
    monkeypatch.setattr(speedtest_runner, "SpeedtestRunner", lambda *a, **k: runner)
    monkeypatch.setattr(runner, "run_test_cycle", lambda: state.__setitem__("cycles", state["cycles"] + 1))
    monkeypatch.setattr(speedtest_runner.time, "sleep", lambda s: state["sleeps"].append(s))
    return state


def test_exits_nonzero_when_influxdb_never_ready(monkeypatch):
    monkeypatch.setattr(speedtest_runner, "wait_for_influxdb", lambda: False)
    created = []
    monkeypatch.setattr(speedtest_runner, "SpeedtestRunner", lambda *a, **k: created.append(1))
    with pytest.raises(SystemExit) as exc:
        speedtest_runner.main(["--once"])
    assert exc.value.code not in (0, None)
    assert not created


def test_wait_for_influxdb_gives_up(monkeypatch, fake_http):
    sleeps = []
    monkeypatch.setattr(speedtest_runner.time, "sleep", sleeps.append)
    assert speedtest_runner.wait_for_influxdb() is False
    assert len(sleeps) == 30


def test_wait_for_influxdb_ready(monkeypatch, fake_http):
    fake_http.routes["/health"] = FakeResponse(200, {})
    monkeypatch.setattr(speedtest_runner.time, "sleep", lambda s: None)
    assert speedtest_runner.wait_for_influxdb() is True


def test_wait_for_influxdb_retries_then_ready(monkeypatch):
    answers = iter([requests.ConnectionError("x"), FakeResponse(503), FakeResponse(200)])

    def fake_get(url, **kw):
        a = next(answers)
        if isinstance(a, BaseException):
            raise a
        return a

    monkeypatch.setattr(speedtest_runner.requests, "get", fake_get)
    monkeypatch.setattr(speedtest_runner.time, "sleep", lambda s: None)
    assert speedtest_runner.wait_for_influxdb() is True


def test_once_runs_one_cycle_without_scheduler(wired, monkeypatch):
    def no_schedule(*a, **k):
        raise AssertionError("scheduler entered")

    monkeypatch.setattr(speedtest_runner.schedule, "every", no_schedule)
    monkeypatch.setattr(speedtest_runner.schedule, "run_pending", no_schedule)
    speedtest_runner.main(["--once"])
    assert wired["cycles"] == 1
    assert wired["sleeps"] == []


def test_once_ignores_bad_interval_env(wired, monkeypatch):
    monkeypatch.setenv("SPEEDTEST_INTERVAL", "garbage")
    speedtest_runner.main(["--once"])
    assert wired["cycles"] == 1


def test_daemon_schedules_and_loops(wired, monkeypatch):
    scheduled = []

    class Job:
        def __init__(self, n):
            self.n = n
            self.seconds = self

        def do(self, fn):
            scheduled.append((self.n, fn))

    monkeypatch.setattr(speedtest_runner.schedule, "every", Job)

    class StopLoop(Exception):
        pass

    def stop():
        raise StopLoop

    monkeypatch.setattr(speedtest_runner.schedule, "run_pending", stop)
    with pytest.raises(StopLoop):
        speedtest_runner.main(["--interval", "900"])
    assert wired["cycles"] == 1  # initial cycle
    assert scheduled[0][0] == 900


def test_interval_from_env(monkeypatch):
    monkeypatch.setenv("SPEEDTEST_INTERVAL", "600")
    assert speedtest_runner.get_speedtest_interval() == 600


def test_interval_default():
    assert speedtest_runner.get_speedtest_interval() == 1800


@pytest.mark.parametrize("bad", ["abc", "", "1.5", "0", "-30", "10m"])
def test_bad_interval_is_a_clear_error(monkeypatch, bad):
    monkeypatch.setenv("SPEEDTEST_INTERVAL", bad)
    with pytest.raises(speedtest_runner.ConfigError, match="SPEEDTEST_INTERVAL"):
        speedtest_runner.get_speedtest_interval()


def test_main_reports_bad_interval_and_exits_nonzero(wired, monkeypatch, capsys):
    monkeypatch.setenv("SPEEDTEST_INTERVAL", "abc")
    with pytest.raises(SystemExit) as exc:
        speedtest_runner.main([])
    assert exc.value.code not in (0, None)
    assert "SPEEDTEST_INTERVAL" in capsys.readouterr().err
    assert wired["cycles"] == 0


def test_import_does_not_crash_on_bad_interval():
    src = str(Path(__file__).parent.parent / "speedtest-runner")
    code = f"import sys; sys.path.insert(0, {src!r}); import speedtest_runner"
    proc = subprocess.run(
        [sys.executable, "-I", "-c", code],
        env={"SPEEDTEST_INTERVAL": "abc"},
        capture_output=True, text=True, timeout=60, check=False,
    )
    assert proc.returncode == 0, proc.stderr
