"""Tests for the per-target DNS and HTTP timing probe (issue #87). No real network: every layer is a stub."""

import probe
import pytest
from test_probe import SECRET_URL, FakeNet, _assert_clean

# ---------------------------------------------------------------- fakes


class TimingNet(FakeNet):
    """Scripted timing probe: each entry is a dict of stage timings, or an exception to raise."""

    def __init__(self, results):
        super().__init__()
        self.results = list(results)
        self.timing_calls: list[tuple] = []

    def timing(self, host, port, url, timeout):
        self.timing_calls.append((host, port, url, timeout))
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class StubIcmp:
    def close(self):
        pass


def timing_prober(fake_writer, targets, results, timing=True):
    cfg = probe.ProbeConfig(targets=probe.parse_targets(targets), interval=60, count=1, timeout=1.0, timing=timing)
    return probe.Prober(cfg, fake_writer, TimingNet(results))


def timing_points(fake_writer):
    return [p for p in fake_writer.points if p[0] == "probe_timing"]


# ---------------------------------------------------------------- config


def test_timing_config_defaults_on_and_reads_environment(monkeypatch):
    assert probe.load_config().timing is True
    monkeypatch.setenv("PROBE_TIMING", "0")
    assert probe.load_config().timing is False
    monkeypatch.setenv("PROBE_TIMING", "true")
    assert probe.load_config().timing is True


def test_timing_config_rejects_garbage(monkeypatch):
    monkeypatch.setenv("PROBE_TIMING", "maybe")
    with pytest.raises(probe.ConfigError, match="PROBE_TIMING"):
        probe.load_config()


def test_timing_counts_toward_the_worst_case_cycle(monkeypatch):
    monkeypatch.setenv("PROBE_TARGETS", "https:https://a.test/")
    monkeypatch.setenv("PROBE_INTERVAL", "10")
    monkeypatch.setenv("PROBE_COUNT", "1")
    monkeypatch.setenv("PROBE_TIMEOUT", "2")
    monkeypatch.setenv("PROBE_TIMING", "0")
    probe.load_config()  # 2.2 s fits
    monkeypatch.setenv("PROBE_TIMING", "1")
    with pytest.raises(probe.ConfigError, match="PROBE_INTERVAL"):
        probe.load_config()  # four stages of 2 s more do not


# ---------------------------------------------------------------- cycle behavior


def test_timing_https_target_writes_all_four_fields(fake_writer):
    ok = {"dns_ms": 5.0, "connect_ms": 10.0, "tls_ms": 20.0, "ttfb_ms": 30.0}
    p = timing_prober(fake_writer, "https:https://example.test/x", [ok])
    p.net.outcomes = [10]
    p.run_cycle()
    ((_m, tags, fields),) = timing_points(fake_writer)
    assert tags == {"target": "example.test/x", "kind": "https", "host": "example.test"}
    assert fields == {**ok, "error": 0}
    assert p.net.timing_calls == [("example.test", 443, "https://example.test/x", 1.0)]


def test_timing_tcp_and_icmp_targets_ask_only_for_their_stages(fake_writer):
    p = timing_prober(
        fake_writer,
        "tcp:a.test:80,icmp:b.test",
        [{"dns_ms": 1.0, "connect_ms": 2.0}, {"dns_ms": 3.0}],
    )
    net = p.net
    net.icmp_socket = StubIcmp
    p = probe.Prober(p.config, fake_writer, net)
    net.outcomes = [10]
    net.resolve = lambda host: "192.0.2.1"
    p.run_cycle()
    assert net.timing_calls == [("a.test", 80, None, 1.0), ("b.test", None, None, 1.0)]


def test_timing_failure_is_an_error_point_not_a_gap(fake_writer):
    err = probe.TimingError("tls", {"dns_ms": 4.0, "connect_ms": 9.0})
    p = timing_prober(fake_writer, "https:https://example.test/", [err])
    p.net.outcomes = [10]
    p.run_cycle()
    ((_m, _tags, fields),) = timing_points(fake_writer)
    assert fields == {"dns_ms": 4.0, "connect_ms": 9.0, "error": 1, "error_stage": "tls"}


def test_timing_unexpected_error_is_an_unknown_stage_error_point(fake_writer):
    p = timing_prober(fake_writer, "tcp:a.test:80", [RuntimeError("bug")])
    p.net.outcomes = [10]
    p.run_cycle()
    ((_m, _tags, fields),) = timing_points(fake_writer)
    assert fields == {"error": 1, "error_stage": "unknown"}


def test_timing_off_writes_no_timing_points(fake_writer):
    p = timing_prober(fake_writer, "tcp:a.test:80", [], timing=False)
    p.net.outcomes = [10]
    p.run_cycle()
    assert timing_points(fake_writer) == []
    assert p.net.timing_calls == []


def test_timing_writer_error_does_not_lose_the_loss_point(fake_writer, capsys):
    p = timing_prober(fake_writer, "tcp:a.test:80", [{"dns_ms": 1.0, "connect_ms": 2.0}])
    p.net.outcomes = [10]
    real = fake_writer.write_point

    def flaky(measurement, tags, fields):
        if measurement == "probe_timing":
            raise RuntimeError("influx down")
        real(measurement, tags, fields)

    fake_writer.write_point = flaky
    p.run_cycle()
    assert [pt[0] for pt in fake_writer.points] == ["probe"]
    assert "influx down" in capsys.readouterr().out


def test_timing_points_work_on_both_influxdb_writers():
    for cls in (speedtest_writer_1(), speedtest_writer_2()):
        cls.write_point(
            "probe_timing",
            {"target": "t", "kind": "https", "host": "h"},
            {"dns_ms": 1.0, "error": 1, "error_stage": "dns"},
        )


def speedtest_writer_1():
    import speedtest_runner

    w = speedtest_runner.InfluxDB1Writer.__new__(speedtest_runner.InfluxDB1Writer)
    w.client = type("C", (), {"write_points": lambda self, pts: None})()
    return w


def speedtest_writer_2():
    import speedtest_runner

    w = speedtest_runner.InfluxDB2Writer.__new__(speedtest_runner.InfluxDB2Writer)
    w.bucket = "b"
    w.write_api = type("A", (), {"write": lambda self, bucket, record: None})()
    return w


# ---------------------------------------------------------------- label hygiene


def test_timing_points_and_logs_are_clean(fake_writer, capsys):
    target = probe.parse_target(f"https:{SECRET_URL}")
    config = probe.ProbeConfig(targets=[target], interval=60, count=1, timeout=1.0, timing=True)
    net = TimingNet([probe.TimingError("http", {"dns_ms": 1.0}), RuntimeError(f"boom {SECRET_URL}")])
    net.outcomes = [10, 10]
    prober = probe.Prober(config, fake_writer, net)
    prober.run_cycle()
    prober.run_cycle()
    out = capsys.readouterr().out
    _assert_clean(out + repr(fake_writer.points))
    assert [p[1]["target"] for p in timing_points(fake_writer)] == ["example.test:8443/health"] * 2


def test_timing_error_message_is_just_the_stage():
    assert str(probe.TimingError("dns", {})) == "dns"


# ---------------------------------------------------------------- the real Net.timing over stubbed resolver, socket and TLS


class StubSock:
    def __init__(self, reply=b"HTTP/1.1 200 OK\r\n", fail=None):
        self.reply = reply
        self.fail = fail
        self.sent = b""
        self.closed = False

    def sendall(self, data):
        self.sent += data

    def recv(self, n):
        if self.fail == "recv":
            raise TimeoutError("timed out")
        return self.reply[:n]

    def settimeout(self, value):
        pass

    def close(self):
        self.closed = True


class StubCtx:
    def __init__(self, tls_sock, fail=False):
        self.tls_sock = tls_sock
        self.fail = fail
        self.server_hostname = None

    def wrap_socket(self, sock, server_hostname=None):
        self.server_hostname = server_hostname
        if self.fail:
            raise probe.ssl.SSLCertVerificationError("bad cert for user:secret")
        return self.tls_sock


def _raise(exc):
    raise exc


def stub_net(monkeypatch, *, resolve=None, connect=None, ctx=None, plain=None):
    ticks = iter(x / 1000.0 for x in range(0, 100000, 10))  # each clock reading is 10 ms later
    net = probe.Net()
    net.monotonic = lambda: next(ticks)
    net.resolve = resolve or (lambda host: "192.0.2.7")
    seen = {"sock": plain or StubSock()}

    def create(addr, timeout):
        seen["addr"] = (addr, timeout)
        if connect is not None:
            raise connect
        return seen["sock"]

    monkeypatch.setattr(probe.socket, "create_connection", create)
    if ctx is not None:
        monkeypatch.setattr(probe, "_tls_context", lambda: ctx)
    return net, seen


def test_net_timing_dns_only(monkeypatch):
    net, seen = stub_net(monkeypatch)
    assert net.timing("a.test", None, None, 2.0) == {"dns_ms": pytest.approx(10.0)}
    assert "addr" not in seen


def test_net_timing_tcp_connects_to_the_resolved_address(monkeypatch):
    net, seen = stub_net(monkeypatch)
    out = net.timing("a.test", 80, None, 2.0)
    assert set(out) == {"dns_ms", "connect_ms"}
    assert seen["addr"] == (("192.0.2.7", 80), 2.0)
    assert seen["sock"].closed


def test_net_timing_https_measures_every_stage(monkeypatch):
    tls = StubSock()
    ctx = StubCtx(tls)
    net, _seen = stub_net(monkeypatch, ctx=ctx)
    out = net.timing("example.test", 8443, "https://u:p@example.test:8443/h?q=1", 2.0)
    assert set(out) == {"dns_ms", "connect_ms", "tls_ms", "ttfb_ms"}
    assert all(v == pytest.approx(10.0) for v in out.values())
    assert ctx.server_hostname == "example.test"  # the certificate is verified against the name
    assert tls.sent.startswith(b"HEAD /h?q=1 HTTP/1.1\r\n")
    assert b"Host: example.test:8443\r\n" in tls.sent
    assert b"u:p" not in tls.sent
    assert tls.closed


@pytest.mark.parametrize(
    ("kwargs", "stage", "done"),
    [
        ({"resolve": lambda h: _raise(OSError("nxdomain"))}, "dns", set()),
        ({"connect": TimeoutError("timed out")}, "connect", {"dns_ms"}),
        ({"ctx": StubCtx(StubSock(), fail=True)}, "tls", {"dns_ms", "connect_ms"}),
        ({"ctx": StubCtx(StubSock(fail="recv"))}, "http", {"dns_ms", "connect_ms", "tls_ms"}),
        ({"ctx": StubCtx(StubSock(reply=b""))}, "http", {"dns_ms", "connect_ms", "tls_ms"}),
        ({"ctx": StubCtx(StubSock(reply=b"HTTP/1.1 503 X"))}, "http", {"dns_ms", "connect_ms", "tls_ms"}),
    ],
)
def test_net_timing_failures_name_the_stage_and_keep_partials(monkeypatch, kwargs, stage, done):
    net, _seen = stub_net(monkeypatch, **kwargs)
    with pytest.raises(probe.TimingError) as exc:
        net.timing("example.test", 443, "https://example.test/", 2.0)
    assert exc.value.stage == stage
    assert set(exc.value.partial) == done
    assert "secret" not in str(exc.value)


def test_net_timing_closes_the_plain_socket_on_a_tls_failure(monkeypatch):
    plain = StubSock()
    net, _seen = stub_net(monkeypatch, ctx=StubCtx(StubSock(), fail=True), plain=plain)
    with pytest.raises(probe.TimingError):
        net.timing("example.test", 443, "https://example.test/", 2.0)
    assert plain.closed


# ---------------------------------------------------------------- the DNS stage honors the timeout


def test_net_timing_dns_hang_is_a_dns_error_within_the_timeout(monkeypatch):
    import threading
    import time

    release = threading.Event()
    net, _seen = stub_net(monkeypatch, resolve=lambda h: release.wait(5) or "192.0.2.7")
    net.monotonic = time.monotonic
    started = time.monotonic()
    try:
        with pytest.raises(probe.TimingError) as exc:
            net.timing("example.test", 443, None, 0.05)
        assert time.monotonic() - started < 1.0
        assert exc.value.stage == "dns" and exc.value.partial == {}
    finally:
        release.set()


def test_net_timing_dns_threads_are_capped(monkeypatch):
    import threading
    import time

    release = threading.Event()
    calls = []

    def hang(host):
        calls.append(host)
        release.wait(5)
        return "192.0.2.7"

    net, _seen = stub_net(monkeypatch, resolve=hang)
    net.monotonic = time.monotonic
    try:
        for _ in range(probe.MAX_DNS_INFLIGHT + 6):
            with pytest.raises(probe.TimingError) as exc:
                net.timing("example.test", None, None, 0.01)
            assert exc.value.stage == "dns"
        time.sleep(0.05)
        assert len(calls) == probe.MAX_DNS_INFLIGHT  # later attempts fail fast, no new threads
    finally:
        release.set()
        time.sleep(0.1)  # let the stuck workers return their slots


def test_net_timing_dns_slots_are_released_after_the_resolver_returns(monkeypatch):
    import time

    net, _seen = stub_net(monkeypatch)
    net.monotonic = time.monotonic
    for _ in range(probe.MAX_DNS_INFLIGHT * 3):
        assert "dns_ms" in net.timing("example.test", None, None, 1.0)


def test_net_timing_dns_resolver_error_is_still_a_dns_error(monkeypatch):
    net, _seen = stub_net(monkeypatch, resolve=lambda h: _raise(OSError("nxdomain")))
    with pytest.raises(probe.TimingError) as exc:
        net.timing("example.test", None, None, 1.0)
    assert exc.value.stage == "dns"


def test_tls_context_verifies_and_refuses_old_protocols():
    ctx = probe._tls_context()
    assert ctx.minimum_version >= probe.ssl.TLSVersion.TLSv1_2
    assert ctx.verify_mode == probe.ssl.CERT_REQUIRED
    assert ctx.check_hostname is True


def test_net_timing_uses_the_hardened_context(monkeypatch):
    seen = []
    real = probe._tls_context

    def spy():
        seen.append(real())
        return StubCtx(StubSock())

    net, _seen = stub_net(monkeypatch)
    monkeypatch.setattr(probe, "_tls_context", spy)
    net.timing("example.test", 443, "https://example.test/", 2.0)
    assert len(seen) == 1


def test_net_timing_releases_the_dns_slot_when_the_thread_cannot_start(monkeypatch):
    import threading

    real_thread = threading.Thread

    class Boom:
        def __init__(self, *a, **k):
            pass

        def start(self):
            raise RuntimeError("can't start new thread")

    net, _seen = stub_net(monkeypatch)
    monkeypatch.setattr(probe.threading, "Thread", Boom)
    for _ in range(probe.MAX_DNS_INFLIGHT + 2):  # a leaked slot would turn these into "busy"
        with pytest.raises(RuntimeError, match="start new thread"):
            net._resolve_bounded("example.test", 1.0)
    monkeypatch.setattr(probe.threading, "Thread", real_thread)
    assert net._resolve_bounded("example.test", 1.0) == "192.0.2.7"
