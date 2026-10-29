import json
import subprocess

import pytest
import speedtest_runner
from conftest import FIXTURES


def fake_run(stdout="", returncode=0, stderr="", raises=None):
    def _run(cmd, **kwargs):
        if raises:
            raise raises
        return subprocess.CompletedProcess(cmd, returncode, stdout, stderr)

    return _run


def use(monkeypatch, **kw):
    monkeypatch.setattr(speedtest_runner.subprocess, "run", fake_run(**kw))


def test_ok_result_converts_bandwidth_to_mbps(runner, monkeypatch):
    use(monkeypatch, stdout=(FIXTURES / "speedtest_ok.json").read_text())
    r = runner.run_speedtest()
    assert r["download_mbps"] == pytest.approx(12_500_000 * 8 / 1e6)
    assert r["upload_mbps"] == pytest.approx(2_500_000 * 8 / 1e6)
    assert r["ping_latency"] == 12.5
    assert r["server_name"] == "Example Net"
    assert r["result_id"] == "abc-123"


def test_nonzero_exit_returns_none(runner, monkeypatch):
    use(monkeypatch, returncode=1, stderr="boom")
    assert runner.run_speedtest() is None


def test_timeout_returns_none(runner, monkeypatch):
    use(monkeypatch, raises=subprocess.TimeoutExpired("speedtest", 120))
    assert runner.run_speedtest() is None


def test_missing_binary_returns_none(runner, monkeypatch):
    use(monkeypatch, raises=FileNotFoundError("speedtest"))
    assert runner.run_speedtest() is None


@pytest.mark.parametrize("out", ["", "not json", "{", "[1, 2]", "null", '"text"', "42"])
def test_invalid_json_returns_none(runner, monkeypatch, out):
    use(monkeypatch, stdout=out)
    assert runner.run_speedtest() is None


def test_null_ping_is_null_safe(runner, monkeypatch, capsys):
    use(monkeypatch, stdout=(FIXTURES / "speedtest_null_ping.json").read_text())
    r = runner.run_speedtest()
    out = capsys.readouterr().out
    assert "AttributeError" not in out and "TypeError" not in out
    assert r is not None
    assert r["ping_latency"] is None and r["ping_jitter"] is None
    assert r["download_mbps"] == pytest.approx(100.0)
    assert r["download_latency_iqm"] is None
    assert "upload_mbps" not in r


@pytest.mark.parametrize("key", ["ping", "download", "upload", "server", "interface", "result"])
def test_each_null_section_is_null_safe(runner, monkeypatch, key, capsys):
    data = json.loads((FIXTURES / "speedtest_ok.json").read_text())
    data[key] = None
    use(monkeypatch, stdout=json.dumps(data))
    assert runner.run_speedtest() is not None
    assert "Error running speedtest" not in capsys.readouterr().out


def test_empty_object_gives_all_missing(runner, monkeypatch):
    use(monkeypatch, stdout="{}")
    r = runner.run_speedtest()
    assert r is not None and r["download_bandwidth"] is None
