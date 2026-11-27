"""Issue #132: no plain-HTTP lookup in speedtest_runner, and ISP change keys on ASN.

Both runners are covered: speedtest_runner is on the pythonpath, the Cloudflare
runner is loaded by path (it lives outside the pythonpath).
"""

import importlib.util
import re
from pathlib import Path

import pytest
import speedtest_runner

ST_PATH = Path(__file__).resolve().parent.parent / "speedtest-runner" / "speedtest_runner.py"
CF_PATH = Path(__file__).resolve().parent.parent / "cloudflare-speedtest-runner" / "cloudflare_speedtest_runner.py"

# A requests call whose first argument is a plain-HTTP URL literal.
PLAIN_HTTP_CALL = re.compile(r"requests\.\w+\(\s*f?[\"']http://")


def _load_cf_module():
    spec = importlib.util.spec_from_file_location("cloudflare_speedtest_runner_asn_test", CF_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cf = _load_cf_module()

GOOD = {"ip": "203.0.113.7", "isp": "Example Cable Co", "asn": "AS64500", "connection_type": "cable"}


def find_plain_http_requests(text):
    return PLAIN_HTTP_CALL.findall(text)


def test_speedtest_runner_source_has_no_plain_http_requests():
    assert find_plain_http_requests(ST_PATH.read_text()) == []


def test_plain_http_guard_bites_on_speedtest_source_text():
    """Negative case: the guard must flag a plain-HTTP requests call."""
    sample = 'response = requests.get("http://ip-api.com/json", timeout=10)\n'
    assert find_plain_http_requests(sample) == ['requests.get("http://']


@pytest.fixture(params=[speedtest_runner, cf], ids=["speedtest", "cloudflare"])
def tracker(request, monkeypatch, tmp_path):
    module = request.param
    monkeypatch.setattr(module.ISPTracker, "STATE_FILE", str(tmp_path / "state.json"))
    return module.ISPTracker()


def test_same_asn_different_isp_name_is_not_a_change(tracker):
    tracker.check_for_change(dict(GOOD))
    change = tracker.check_for_change({**GOOD, "isp": "Example Cable Company Inc"})
    assert change["changed"] is False
    assert change["asn_changed"] is False


def test_different_asn_is_a_change(tracker):
    tracker.check_for_change(dict(GOOD))
    change = tracker.check_for_change({**GOOD, "asn": "AS64999", "isp": "New Fiber LLC"})
    assert change["changed"] is True
    assert change["asn_changed"] is True
    assert change["previous_asn"] == "AS64500"


def test_missing_asn_on_current_side_is_not_a_change(tracker):
    tracker.check_for_change(dict(GOOD))
    change = tracker.check_for_change({**GOOD, "asn": None, "isp": "Some Other Name"})
    assert change["changed"] is False
    assert change["asn_changed"] is False


def test_missing_asn_on_previous_side_is_not_a_change(tracker):
    tracker.check_for_change({**GOOD, "asn": None})
    change = tracker.check_for_change(dict(GOOD))
    assert change["changed"] is False
    assert change["asn_changed"] is False


def test_missing_asn_is_logged_once(tracker, capsys):
    tracker.check_for_change(dict(GOOD))  # baseline, so the comparison is actually attempted
    for _ in range(3):
        tracker.check_for_change({**GOOD, "asn": None})
    out = capsys.readouterr().out
    assert out.count("No ASN on one side") == 1


def test_missing_asn_keeps_asn_baseline(tracker):
    """A cycle without an ASN must not erase the baseline a later ASN is compared with."""
    tracker.check_for_change(dict(GOOD))
    tracker.check_for_change({**GOOD, "asn": None})
    change = tracker.check_for_change({**GOOD, "asn": "AS64999"})
    assert change["changed"] is True
    assert change["previous_asn"] == "AS64500"
