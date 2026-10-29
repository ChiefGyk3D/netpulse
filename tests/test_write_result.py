import subprocess

import speedtest_runner
from conftest import FIXTURES, FakeResponse, load_fixture

IP = {"ip": "203.0.113.7", "isp": "Example Cable Co", "asn": "AS64500", "connection_type": "cable"}
NO_CHANGE = {"changed": False}
CHANGE = {
    "changed": True, "ip_changed": True, "isp_changed": True, "asn_changed": True,
    "previous_ip": "198.51.100.9", "previous_isp": "Old ISP", "previous_asn": "AS1",
    "previous_connection_type": "dsl",
}
RESULT = {
    "download_mbps": 100.0, "upload_mbps": 20.0, "download_bandwidth": 12500000,
    "upload_bandwidth": 2500000, "ping_latency": 12.5, "ping_jitter": 0.8,
    "ping_low": 11.0, "ping_high": 15.0, "download_latency_iqm": 30.1,
    "upload_latency_iqm": 40.2, "packet_loss": 0.0, "result_url": "https://x.invalid/r",
    "server_name": "Example Net", "server_location": "Exampleville", "server_country": "US",
}
VALUE_FIELDS = (
    "upload_mbps", "ping_latency", "ping_jitter", "ping_low", "ping_high",
    "download_latency_iqm", "upload_latency_iqm", "packet_loss",
    "download_bandwidth", "upload_bandwidth",
)


def test_one_speedtest_point_and_no_change_point(runner, fake_writer):
    runner.write_speedtest_result(dict(RESULT), IP, NO_CHANGE)
    assert len(fake_writer.points) == 1
    name, tags, fields = fake_writer.points[0]
    assert name == "speedtest"
    assert tags["asn"] == "AS64500" and tags["server_name"] == "Example Net"
    assert fields["download_mbps"] == 100.0
    assert fields["packet_loss"] == 0.0


def test_change_adds_isp_change_point(runner, fake_writer):
    runner.write_speedtest_result(dict(RESULT), IP, CHANGE)
    assert [p[0] for p in fake_writer.points] == ["speedtest", "isp_change"]
    _, tags, fields = fake_writer.points[1]
    assert tags["previous_asn"] == "AS1" and tags["current_asn"] == "AS64500"
    assert fields["event"] == 1 and fields["previous_ip"] == "198.51.100.9"


def test_missing_values_are_not_written_as_zero(runner, fake_writer):
    runner.write_speedtest_result({"download_mbps": 100.0}, IP, NO_CHANGE)
    _, _, fields = fake_writer.points[0]
    assert fields["download_mbps"] == 100.0
    for k in VALUE_FIELDS:
        assert fields.get(k) is None, k
    assert not fields.get("result_url")


def test_none_values_from_a_parsed_result_are_not_fake_zero(runner, monkeypatch, fake_writer):
    monkeypatch.setattr(
        speedtest_runner.subprocess, "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, (FIXTURES / "speedtest_null_ping.json").read_text(), ""),
    )
    result = runner.run_speedtest()
    runner.write_speedtest_result(result, IP, NO_CHANGE)
    _, _, fields = fake_writer.points[0]
    assert fields["download_mbps"] == 100.0
    assert fields.get("ping_latency") is None
    assert fields.get("packet_loss") is None


def test_real_zero_packet_loss_is_kept(runner, fake_writer):
    runner.write_speedtest_result({"packet_loss": 0.0}, IP, NO_CHANGE)
    assert fake_writer.points[0][2]["packet_loss"] == 0.0


def test_writer_error_is_logged_not_raised(runner, fake_writer, capsys):
    def boom(*a, **k):
        raise RuntimeError("influx down")

    fake_writer.write_point = boom
    runner.write_speedtest_result(dict(RESULT), IP, NO_CHANGE)
    assert "influx down" in capsys.readouterr().out


def test_cycle_failed_speedtest_writes_error_point(runner, fake_writer, fake_http, monkeypatch):
    fake_http.routes["ipinfo.io"] = FakeResponse(200, load_fixture("ipinfo.json"))
    monkeypatch.setattr(runner, "run_speedtest", lambda: None)
    runner.run_test_cycle()
    assert [p[0] for p in fake_writer.points] == ["speedtest_error"]


def test_cycle_success_writes_speedtest_only_on_first_run(runner, fake_writer, fake_http, monkeypatch):
    fake_http.routes["ipinfo.io"] = FakeResponse(200, load_fixture("ipinfo.json"))
    monkeypatch.setattr(runner, "run_speedtest", lambda: dict(RESULT))
    runner.run_test_cycle()
    assert [p[0] for p in fake_writer.points] == ["speedtest"]
