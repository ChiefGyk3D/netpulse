import json
import os

import pytest
import requests
import speedtest_runner
from conftest import FakeResponse, load_fixture

GOOD = {"ip": "203.0.113.7", "isp": "A", "asn": "AS1", "connection_type": "cable"}
FAILED = {"ip": None, "isp": None, "asn": None, "connection_type": None}


@pytest.fixture
def tracker():
    return speedtest_runner.ISPTracker()


def test_ipinfo_parse(tracker, fake_http):
    fake_http.routes["ipinfo.io"] = FakeResponse(200, load_fixture("ipinfo.json"))
    info = tracker.get_ip_info()
    assert info["ip"] == "203.0.113.7"
    assert info["asn"] == "AS64500"
    assert info["isp"] == "Example Cable Co"
    assert info["connection_type"] == "cable"
    assert not any("ip-api" in c for c in fake_http.calls)


def test_ipinfo_org_without_asn_prefix(tracker, fake_http):
    fake_http.routes["ipinfo.io"] = FakeResponse(200, {"ip": "203.0.113.7", "org": "Plain Name"})
    info = tracker.get_ip_info()
    assert info["asn"] is None
    assert info["isp"] == "Plain Name"


def test_ipinfo_null_org_does_not_crash(tracker, fake_http):
    fake_http.routes["ipinfo.io"] = FakeResponse(200, {"ip": "203.0.113.7", "org": None})
    info = tracker.get_ip_info()
    assert info["ip"] == "203.0.113.7"
    assert not info["isp"]
    assert info["connection_type"] is None


def test_ipinfo_failure_falls_back_to_ipapi(tracker, fake_http):
    fake_http.routes["ipinfo.io"] = requests.ConnectionError("down")
    fake_http.routes["ip-api.com"] = FakeResponse(200, load_fixture("ipapi.json"))
    info = tracker.get_ip_info()
    assert info["ip"] == "198.51.100.9"
    assert info["asn"] == "AS64501"
    assert info["isp"] == "Example Fiber LLC"
    assert info["connection_type"] == "fiber"


def test_ipinfo_bad_status_falls_back(tracker, fake_http):
    fake_http.routes["ipinfo.io"] = FakeResponse(429, {})
    fake_http.routes["ip-api.com"] = FakeResponse(200, load_fixture("ipapi.json"))
    assert tracker.get_ip_info()["ip"] == "198.51.100.9"


def test_both_lookups_fail(tracker, fake_http):
    info = tracker.get_ip_info()  # nothing routed: both raise
    assert info["ip"] is None
    assert info["isp"] is None


def test_first_run_is_not_a_change(tracker):
    assert tracker.check_for_change(dict(GOOD))["changed"] is False


def test_same_info_is_not_a_change(tracker):
    tracker.check_for_change(dict(GOOD))
    assert tracker.check_for_change(dict(GOOD))["changed"] is False


def test_different_asn_is_a_change(tracker):
    tracker.check_for_change(dict(GOOD))
    change = tracker.check_for_change({**GOOD, "asn": "AS2"})
    assert change["changed"] is True
    assert change["asn_changed"] is True
    assert change["ip_changed"] is False
    assert change["previous_asn"] == "AS1"


def test_state_persists_across_instances(tracker):
    tracker.check_for_change(dict(GOOD))
    again = speedtest_runner.ISPTracker()
    assert (again.last_ip, again.last_isp, again.last_asn) == ("203.0.113.7", "A", "AS1")


def test_failed_lookup_keeps_baseline_and_emits_no_event(tracker):
    tracker.check_for_change(dict(GOOD))
    change = tracker.check_for_change(dict(FAILED))
    assert change["changed"] is False
    # In memory and on disk the baseline is untouched.
    assert tracker.last_ip == "203.0.113.7" and tracker.last_asn == "AS1"
    with open(speedtest_runner.ISPTracker.STATE_FILE) as f:
        saved = json.load(f)
    assert saved["ip"] == "203.0.113.7" and saved["isp"] == "A" and saved["asn"] == "AS1"
    # The next good lookup is compared with the real baseline: no false event.
    assert tracker.check_for_change(dict(GOOD))["changed"] is False


def test_failed_lookup_on_first_run_saves_nothing(tracker):
    tracker.check_for_change(dict(FAILED))
    assert not os.path.exists(speedtest_runner.ISPTracker.STATE_FILE)


@pytest.mark.parametrize("content", ["", "{not json", "[]", "null", "\x00\x01"])
def test_corrupt_or_empty_state_file_loads(content):
    with open(speedtest_runner.ISPTracker.STATE_FILE, "w") as f:
        f.write(content)
    t = speedtest_runner.ISPTracker()
    assert t.last_ip is None and t.last_asn is None


def test_unwritable_state_path_does_not_raise(monkeypatch, tmp_path):
    monkeypatch.setattr(speedtest_runner.ISPTracker, "STATE_FILE", str(tmp_path / "no" / "such" / "dir" / "s.json"))
    t = speedtest_runner.ISPTracker()
    t.check_for_change(dict(GOOD))
