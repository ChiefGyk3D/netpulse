"""Cloudflare runner ISP lookup: one HTTPS lookup to ipinfo.io, no fallback.

The Cloudflare runner lives outside the pythonpath, so it is loaded by path
here, the same way its module is loaded by hand in test_enhancements.py.
"""

import importlib.util
import re
from pathlib import Path

import pytest
import requests
from conftest import FakeResponse, load_fixture

CF_PATH = Path(__file__).resolve().parent.parent / "cloudflare-speedtest-runner" / "cloudflare_speedtest_runner.py"
PRIMARY_URL = "https://ipinfo.io/json"

# A requests call whose first argument is a plain-HTTP URL literal.
PLAIN_HTTP_CALL = re.compile(r"requests\.\w+\(\s*f?[\"']http://")


def _load_cf_module():
    spec = importlib.util.spec_from_file_location("cloudflare_speedtest_runner_under_test", CF_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cf = _load_cf_module()


def assert_all_https(calls):
    """Guard: fails if any recorded lookup URL is plain HTTP."""
    assert calls, "no lookup was attempted"
    plain = [u for u in calls if not u.startswith("https://")]
    assert plain == [], f"plain-HTTP lookups: {plain}"


def find_plain_http_requests(text):
    """Return the requests calls in text that open a plain-HTTP URL."""
    return PLAIN_HTTP_CALL.findall(text)


@pytest.fixture
def tracker(monkeypatch, tmp_path):
    monkeypatch.setattr(cf.ISPTracker, "STATE_FILE", str(tmp_path / "cf_state.json"))
    return cf.ISPTracker()


def test_ipinfo_success_makes_only_https_calls(tracker, fake_http):
    fake_http.routes["ipinfo.io"] = FakeResponse(200, load_fixture("ipinfo.json"))
    info = tracker.get_ip_info()
    assert info["ip"] == "203.0.113.7"
    assert_all_https(fake_http.calls)
    assert len(fake_http.calls) == 1


def test_ipinfo_failure_makes_no_fallback_lookup(tracker, fake_http):
    fake_http.routes["ipinfo.io"] = requests.ConnectionError("down")
    info = tracker.get_ip_info()
    assert info["ip"] is None
    assert fake_http.calls == [PRIMARY_URL], f"unexpected lookups: {fake_http.calls}"


def test_ipinfo_non_200_makes_no_fallback_lookup(tracker, fake_http):
    fake_http.routes["ipinfo.io"] = FakeResponse(429, {})
    info = tracker.get_ip_info()
    assert info["ip"] is None
    assert fake_http.calls == [PRIMARY_URL], f"unexpected lookups: {fake_http.calls}"


def test_ipinfo_failure_logs_and_run_continues(tracker, fake_http, capsys):
    fake_http.routes["ipinfo.io"] = requests.ConnectionError("down")
    info = tracker.get_ip_info()
    assert info["ip"] is None and info["isp"] is None
    out = capsys.readouterr().out
    assert "Error fetching from ipinfo.io" in out
    assert "no IP reported" in out
    # The rest of the cycle must still run with an empty lookup.
    change = tracker.check_for_change(info)
    assert isinstance(change, dict)


def test_ipinfo_non_200_logs_and_reports_no_ip(tracker, fake_http, capsys):
    fake_http.routes["ipinfo.io"] = FakeResponse(429, {})
    tracker.get_ip_info()
    out = capsys.readouterr().out
    assert "ipinfo.io returned HTTP 429" in out
    assert "no IP reported" in out


def test_guard_bites_on_plain_http(tracker, fake_http):
    """Negative case: the guard must reject a plain-HTTP lookup URL."""
    with pytest.raises(AssertionError, match="plain-HTTP lookups"):
        assert_all_https(["https://ipinfo.io/json", "http://ip-api.com/json"])


def test_runner_source_has_no_plain_http_requests():
    assert find_plain_http_requests(CF_PATH.read_text()) == []


def test_plain_http_guard_bites_on_source_text():
    """Negative case: the source guard must flag a plain-HTTP requests call."""
    sample = 'response = requests.get("http://ip-api.com/json", timeout=10)\n'
    assert find_plain_http_requests(sample) == ['requests.get("http://']
