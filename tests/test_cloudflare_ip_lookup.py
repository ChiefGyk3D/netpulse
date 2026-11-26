"""Cloudflare runner ISP lookup: every outbound lookup must use HTTPS.

The Cloudflare runner lives outside the pythonpath, so it is loaded by path
here, the same way its module is loaded by hand in test_enhancements.py.
"""

import importlib.util
from pathlib import Path

import pytest
import requests
from conftest import FakeResponse, load_fixture

CF_PATH = Path(__file__).resolve().parent.parent / "cloudflare-speedtest-runner" / "cloudflare_speedtest_runner.py"


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


def test_fallback_lookup_is_https(tracker, fake_http):
    fake_http.routes["ipinfo.io"] = requests.ConnectionError("down")
    fake_http.routes["ipapi.co"] = FakeResponse(200, load_fixture("ipapico.json"))
    tracker.get_ip_info()
    assert_all_https(fake_http.calls)
    assert "https://ipapi.co/json/" in fake_http.calls


def test_fallback_maps_fields_from_ipapico(tracker, fake_http):
    fake_http.routes["ipinfo.io"] = requests.ConnectionError("down")
    fake_http.routes["ipapi.co"] = FakeResponse(200, load_fixture("ipapico.json"))
    info = tracker.get_ip_info()
    assert info["ip"] == "198.51.100.9"
    assert info["asn"] == "AS64501"
    assert info["org"] == "Example Fiber LLC"
    assert info["isp"] == "Example Fiber LLC"
    assert info["city"] == "Exampleville"
    assert info["region"] == "Example"
    assert info["country"] == "US"
    assert info["connection_type"] == "fiber"


def test_fallback_bad_status_yields_no_ip(tracker, fake_http):
    fake_http.routes["ipinfo.io"] = FakeResponse(429, {})
    fake_http.routes["ipapi.co"] = FakeResponse(429, {})
    info = tracker.get_ip_info()
    assert info["ip"] is None
    assert_all_https(fake_http.calls)


def test_both_lookups_fail_without_crash(tracker, fake_http):
    info = tracker.get_ip_info()  # nothing routed: both raise
    assert info["ip"] is None
    assert info["isp"] is None
    assert_all_https(fake_http.calls)


def test_guard_bites_on_plain_http(tracker, fake_http):
    """Negative case: the guard must reject the old ip-api.com HTTP URL."""
    with pytest.raises(AssertionError, match="plain-HTTP lookups"):
        assert_all_https(["https://ipinfo.io/json", "http://ip-api.com/json"])
