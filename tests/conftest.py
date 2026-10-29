"""Hermetic test setup: no network, no real speedtest, no real InfluxDB."""

import json
import os
import socket
from pathlib import Path

import pytest
import speedtest_runner

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


class NetworkBlocked(RuntimeError):
    """Raised when a test touches the network."""


def _blocked(*args, **kwargs):
    raise NetworkBlocked("network access is blocked in tests")


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch, tmp_path):
    # Block every socket entry point, including name resolution.
    for name in ("connect", "connect_ex", "sendto", "sendmsg"):
        monkeypatch.setattr(socket.socket, name, _blocked)
    for name in ("create_connection", "gethostbyname", "gethostbyname_ex", "getaddrinfo"):
        monkeypatch.setattr(socket, name, _blocked)

    # No ambient configuration leaks in.
    for key in list(os.environ):
        if key.startswith(("INFLUXDB_", "SPEEDTEST_")) or key == "NETPULSE_STATE_FILE":
            monkeypatch.delenv(key)

    # Module-level config was read at import time; reset it to known values.
    for attr, value in {
        "INFLUXDB_URL": "http://influx.invalid:8086",
        "INFLUXDB_VERSION": "2",
        "INFLUXDB_TOKEN": "",
        "INFLUXDB_ORG": "org",
        "INFLUXDB_BUCKET": "bucket",
        "INFLUXDB_USERNAME": "",
        "INFLUXDB_PASSWORD": "",
        "INFLUXDB_DATABASE": "db",
    }.items():
        monkeypatch.setattr(speedtest_runner, attr, value)

    # State file lives in tmp_path, never in /tmp.
    monkeypatch.setattr(speedtest_runner.ISPTracker, "STATE_FILE", str(tmp_path / "state.json"))


class FakeWriter(speedtest_runner.InfluxDBWriter):
    """Records points instead of talking to InfluxDB."""

    def __init__(self):
        self.points: list[tuple[str, dict, dict]] = []
        self.closed = False

    def write_point(self, measurement, tags, fields):
        self.points.append((measurement, dict(tags), dict(fields)))

    def close(self):
        self.closed = True


@pytest.fixture
def fake_writer():
    return FakeWriter()


@pytest.fixture
def runner(monkeypatch, fake_writer):
    """A SpeedtestRunner wired to a FakeWriter, with the license step skipped."""
    monkeypatch.setattr(speedtest_runner, "create_influxdb_writer", lambda: fake_writer)
    r = speedtest_runner.SpeedtestRunner()
    r.speedtest_accepted_license = True
    return r


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


@pytest.fixture
def fake_http(monkeypatch):
    """Route requests.get by URL substring. Unrouted URLs raise ConnectionError."""
    import requests

    routes: dict[str, object] = {}
    calls: list[str] = []

    def fake_get(url, **kwargs):
        calls.append(url)
        for needle, handler in routes.items():
            if needle in url:
                if isinstance(handler, BaseException):
                    raise handler
                return handler
        raise requests.ConnectionError(f"unrouted {url}")

    monkeypatch.setattr(speedtest_runner.requests, "get", fake_get)
    fake_get.routes = routes
    fake_get.calls = calls
    return fake_get
