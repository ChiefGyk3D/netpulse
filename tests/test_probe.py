"""Tests for the continuous probe module. The network layer is a stub: no sockets."""

import struct

import probe
import pytest
import speedtest_runner

# ---------------------------------------------------------------- fakes


class FakeNet(probe.Net):
    """Scripted network layer: each probe outcome is a number of ms or None (lost)."""

    def __init__(self, outcomes=None):
        self.outcomes = list(outcomes or [])
        self.clock_now = 0.0
        self.slept: list[float] = []
        self.calls: list[tuple] = []

    def monotonic(self):
        return self.clock_now

    def sleep(self, seconds):
        self.slept.append(seconds)

    def _next(self):
        ms = self.outcomes.pop(0)
        if ms is None:
            raise OSError("lost")
        self.clock_now += ms / 1000.0

    def tcp_connect(self, host, port, timeout):
        self.calls.append(("tcp", host, port, timeout))
        self._next()

    def https_head(self, url, timeout):
        self.calls.append(("https", url, timeout))
        self._next()


class FakeIcmpSocket:
    def __init__(self, reply_for=None, fail_send=False):
        self.sent: list[tuple[bytes, tuple]] = []
        self.timeout = None
        self.closed = False
        self.reply_for = reply_for
        self.fail_send = fail_send
        self._pending = None

    def settimeout(self, value):
        self.timeout = value

    def sendto(self, data, addr):
        if self.fail_send:
            raise OSError("unreachable")
        self.sent.append((data, addr))
        self._pending = data

    def recvfrom(self, size):
        if self.reply_for is None:
            raise TimeoutError("no reply")
        return self.reply_for(self._pending), ("192.0.2.1", 0)

    def close(self):
        self.closed = True


def echo_reply(request: bytes) -> bytes:
    """The echo reply a kernel hands back on a datagram ICMP socket (no IP header)."""
    _type, code, _csum, ident, seq = struct.unpack("!BBHHH", request[:8])
    return struct.pack("!BBHHH", 0, code, 0, ident, seq) + request[8:]


def icmp_net(sock):
    net = FakeNet()
    net.icmp_socket = lambda: sock
    net.resolve = lambda host: "192.0.2.1"
    return net


# ---------------------------------------------------------------- stats


def test_summarize_all_received():
    s = probe.summarize([10.0, 20.0, 10.0, 20.0], sent=4)
    assert s["sent"] == 4 and s["received"] == 4
    assert s["loss_pct"] == 0.0
    assert s["rtt_min_ms"] == 10.0 and s["rtt_max_ms"] == 20.0
    assert s["rtt_avg_ms"] == 15.0
    assert s["jitter_ms"] == 10.0  # mean of |20-10|, |10-20|, |20-10|


def test_summarize_partial_loss():
    s = probe.summarize([10.0, 30.0], sent=4)
    assert s["loss_pct"] == 50.0
    assert s["jitter_ms"] == 20.0


def test_summarize_total_loss_has_no_latency_fields():
    s = probe.summarize([], sent=5)
    assert s["loss_pct"] == 100.0 and s["received"] == 0
    for name in ("rtt_min_ms", "rtt_avg_ms", "rtt_max_ms", "jitter_ms"):
        assert s[name] is None


def test_summarize_single_sample_has_no_jitter():
    s = probe.summarize([12.0], sent=1)
    assert s["jitter_ms"] is None and s["rtt_avg_ms"] == 12.0


# ---------------------------------------------------------------- target parsing


def test_parse_target_kinds():
    t = probe.parse_target("icmp:198.51.100.7")
    assert (t.kind, t.host, t.port, t.label) == ("icmp", "198.51.100.7", None, "198.51.100.7")
    assert probe.parse_target("tcp:example.test:8443").port == 8443
    assert probe.parse_target("tcp:example.test").port == 443
    t = probe.parse_target("https:https://example.test/path")
    assert (t.kind, t.host, t.port) == ("https", "example.test", 443)
    assert t.url == "https://example.test/path"


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "icmp",
        "icmp:",
        "ftp:example.test",
        "tcp:host:notaport",
        "tcp:host:0",
        "tcp:host:70000",
        "https:http://x.test",
    ],
)
def test_parse_target_rejects_bad_specs(bad):
    with pytest.raises(probe.ConfigError):
        probe.parse_target(bad)


def test_parse_targets_splits_trims_and_skips_blanks():
    got = probe.parse_targets(" tcp:a.test:80 , ,icmp:b.test ")
    assert [t.label for t in got] == ["a.test:80", "b.test"]


def test_parse_targets_rejects_empty_list():
    with pytest.raises(probe.ConfigError, match="PROBE_TARGETS"):
        probe.parse_targets(" , ")


def test_default_targets_parse_and_cover_every_kind():
    targets = probe.parse_targets(probe.DEFAULT_TARGETS)
    assert {t.kind for t in targets} == {"icmp", "tcp", "https"}


# ---------------------------------------------------------------- config


def test_config_defaults():
    cfg = probe.load_config()
    assert cfg.interval == 60 and cfg.count == 5 and cfg.timeout == 2.0
    assert cfg.targets


def test_config_reads_environment(monkeypatch):
    monkeypatch.setenv("PROBE_TARGETS", "tcp:a.test:80")
    monkeypatch.setenv("PROBE_INTERVAL", "30")
    monkeypatch.setenv("PROBE_COUNT", "3")
    monkeypatch.setenv("PROBE_TIMEOUT", "1.5")
    cfg = probe.load_config()
    assert (cfg.interval, cfg.count, cfg.timeout) == (30, 3, 1.5)
    assert cfg.targets[0].host == "a.test"


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("PROBE_INTERVAL", "abc"),
        ("PROBE_INTERVAL", "0"),
        ("PROBE_INTERVAL", "5"),  # below the floor: a low rate is a safety property
        ("PROBE_COUNT", "0"),
        ("PROBE_COUNT", "100"),
        ("PROBE_TIMEOUT", "0"),
        ("PROBE_TIMEOUT", "nan"),
        ("PROBE_TIMEOUT", "x"),
    ],
)
def test_config_rejects_bad_values(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(probe.ConfigError, match=name):
        probe.load_config()


def test_config_interval_must_fit_the_worst_case_cycle(monkeypatch):
    monkeypatch.setenv("PROBE_TARGETS", "tcp:a.test,tcp:b.test,tcp:c.test")
    monkeypatch.setenv("PROBE_INTERVAL", "10")
    monkeypatch.setenv("PROBE_COUNT", "20")
    monkeypatch.setenv("PROBE_TIMEOUT", "5")
    with pytest.raises(probe.ConfigError, match="PROBE_INTERVAL"):
        probe.load_config()


# ---------------------------------------------------------------- ICMP


def test_build_echo_request_layout():
    pkt = probe.build_echo_request(7, 3)
    kind, code, _csum, ident, seq = struct.unpack("!BBHHH", pkt[:8])
    assert (kind, code, ident, seq) == (8, 0, 7, 3)
    # A packet with a correct checksum field sums to zero.
    assert probe.icmp_checksum(pkt) == 0


def test_checksum_pads_odd_length():
    assert probe.icmp_checksum(b"\x01") == probe.icmp_checksum(b"\x01\x00")


def test_icmp_probe_measures_rtt():
    sock = FakeIcmpSocket(reply_for=echo_reply)
    net = icmp_net(sock)
    ticks = iter([1.0, 1.0125])
    net.monotonic = lambda: next(ticks)
    rtt = probe.probe_icmp(net, "target.test", 2.0, seq=1)
    assert rtt == pytest.approx(12.5)
    assert sock.timeout == 2.0 and sock.closed
    assert sock.sent[0][1][0] == "192.0.2.1"


def test_icmp_probe_timeout_is_loss():
    sock = FakeIcmpSocket(reply_for=None)
    assert probe.probe_icmp(icmp_net(sock), "target.test", 1.0, seq=1) is None
    assert sock.closed


def test_icmp_probe_ignores_wrong_sequence():
    def wrong(request):
        pkt = bytearray(echo_reply(request))
        pkt[7] ^= 0xFF  # a different sequence number
        return bytes(pkt)

    sock = FakeIcmpSocket(reply_for=wrong)
    net = icmp_net(sock)
    ticks = iter([0.0, 0.001, 0.002, 5.0, 5.0, 5.0])
    net.monotonic = lambda: next(ticks)
    assert probe.probe_icmp(net, "target.test", 1.0, seq=1) is None


def test_icmp_probe_ignores_non_reply_types():
    def unreachable(request):
        return struct.pack("!BBHHH", 3, 1, 0, 0, 1)

    sock = FakeIcmpSocket(reply_for=unreachable)
    net = icmp_net(sock)
    ticks = iter([0.0, 0.001, 9.0])
    net.monotonic = lambda: next(ticks)
    assert probe.probe_icmp(net, "target.test", 1.0, seq=1) is None


def test_icmp_probe_send_failure_is_loss():
    sock = FakeIcmpSocket(fail_send=True)
    assert probe.probe_icmp(icmp_net(sock), "target.test", 1.0, seq=1) is None
    assert sock.closed


def test_icmp_probe_resolution_failure_is_loss():
    net = FakeNet()

    def boom(host):
        raise OSError("no such host")

    net.resolve = boom
    assert probe.probe_icmp(net, "target.test", 1.0, seq=1) is None


def test_icmp_unavailable_when_socket_cannot_open():
    net = FakeNet()

    def denied():
        raise PermissionError("ping_group_range")

    net.icmp_socket = denied
    assert probe.icmp_available(net) is False
    sock = FakeIcmpSocket()
    net.icmp_socket = lambda: sock
    assert probe.icmp_available(net) is True
    assert sock.closed


# ---------------------------------------------------------------- run_target


def test_run_target_tcp_collects_samples_and_sleeps_between():
    net = FakeNet([10, 20, None, 30])
    stats = probe.run_target(net, probe.parse_target("tcp:a.test:8443"), count=4, timeout=1.0)
    assert stats["sent"] == 4 and stats["received"] == 3
    assert stats["loss_pct"] == 25.0
    assert stats["rtt_avg_ms"] == pytest.approx(20.0)
    assert net.calls[0] == ("tcp", "a.test", 8443, 1.0)
    assert len(net.slept) == 3  # between samples, not after the last


def test_run_target_https():
    net = FakeNet([40, 60])
    stats = probe.run_target(net, probe.parse_target("https:https://example.test/"), count=2, timeout=1.0)
    assert stats["rtt_avg_ms"] == pytest.approx(50.0)
    assert net.calls[0] == ("https", "https://example.test/", 1.0)


def test_run_target_icmp_numbers_each_request(monkeypatch):
    seen = []

    def fake(net, host, timeout, seq):
        seen.append((host, seq))
        return 5.0

    monkeypatch.setattr(probe, "probe_icmp", fake)
    stats = probe.run_target(FakeNet(), probe.parse_target("icmp:a.test"), count=3, timeout=1.0)
    assert seen == [("a.test", 1), ("a.test", 2), ("a.test", 3)]
    assert stats["loss_pct"] == 0.0


# ---------------------------------------------------------------- the real Net methods, over a stubbed socket layer


def test_net_tcp_connect_uses_socket_layer(monkeypatch):
    seen = {}

    class S:
        def close(self):
            seen["closed"] = True

    def create(addr, timeout):
        seen["args"] = (addr, timeout)
        return S()

    monkeypatch.setattr(probe.socket, "create_connection", create)
    probe.Net().tcp_connect("a.test", 443, 1.5)
    assert seen == {"args": (("a.test", 443), 1.5), "closed": True}


def test_net_resolve_asks_for_ipv4(monkeypatch):
    def fake(host, port, family, type_):
        assert family == probe.socket.AF_INET
        return [(family, type_, 0, "", ("192.0.2.9", 0))]

    monkeypatch.setattr(probe.socket, "getaddrinfo", fake)
    assert probe.Net().resolve("a.test") == "192.0.2.9"


def test_net_icmp_socket_is_unprivileged_datagram(monkeypatch):
    seen = {}

    def fake(family, type_, proto):
        seen["args"] = (family, type_, proto)
        return "sock"

    monkeypatch.setattr(probe.socket, "socket", fake)
    assert probe.Net().icmp_socket() == "sock"
    # SOCK_DGRAM, never SOCK_RAW: no CAP_NET_RAW needed.
    assert seen["args"] == (probe.socket.AF_INET, probe.socket.SOCK_DGRAM, probe.socket.IPPROTO_ICMP)


def _conn_class(seen, status):
    class Resp:
        pass

    Resp.status = status

    class Conn:
        def __init__(self, host, port, timeout, context=None):
            seen["init"] = (host, port, timeout)
            seen["context"] = context

        def request(self, method, path, headers=None):
            seen["req"] = (method, path)

        def getresponse(self):
            return Resp()

        def close(self):
            seen["closed"] = True

    return Conn


def test_net_https_head_uses_http_client(monkeypatch):
    seen = {}
    monkeypatch.setattr(probe.http.client, "HTTPSConnection", _conn_class(seen, 200))
    probe.Net().https_head("https://example.test:8443/x?y=1", 2.0)
    assert seen["init"] == ("example.test", 8443, 2.0)
    assert seen["req"] == ("HEAD", "/x?y=1")
    assert seen["closed"] and seen["context"] is not None


def test_net_https_head_default_path(monkeypatch):
    seen = {}
    monkeypatch.setattr(probe.http.client, "HTTPSConnection", _conn_class(seen, 301))
    probe.Net().https_head("https://example.test", 2.0)
    assert seen["req"] == ("HEAD", "/")


def test_net_https_head_server_error_counts_as_failure(monkeypatch):
    monkeypatch.setattr(probe.http.client, "HTTPSConnection", _conn_class({}, 503))
    with pytest.raises(OSError, match="503"):
        probe.Net().https_head("https://example.test/", 2.0)


def test_net_real_monotonic_and_sleep(monkeypatch):
    slept = []
    monkeypatch.setattr(probe.time, "sleep", slept.append)
    net = probe.Net()
    assert net.monotonic() <= net.monotonic()
    net.sleep(0.5)
    assert slept == [0.5]


# ---------------------------------------------------------------- the writer path


def make_prober(fake_writer, targets="tcp:a.test:443", net=None, count=2):
    cfg = probe.ProbeConfig(targets=probe.parse_targets(targets), interval=60, count=count, timeout=1.0)
    return probe.Prober(cfg, fake_writer, net or FakeNet([10, 20]))


def test_cycle_writes_one_point_per_target(fake_writer):
    make_prober(fake_writer).run_cycle()
    assert len(fake_writer.points) == 1
    measurement, tags, fields = fake_writer.points[0]
    assert measurement == "probe"
    assert tags == {"target": "a.test:443", "kind": "tcp", "host": "a.test"}
    assert fields["loss_pct"] == 0.0 and fields["jitter_ms"] == pytest.approx(10.0)
    assert fields["sent"] == 2 and fields["received"] == 2


def test_cycle_total_loss_still_writes_loss_point(fake_writer):
    make_prober(fake_writer, net=FakeNet([None, None])).run_cycle()
    _m, _t, fields = fake_writer.points[0]
    assert fields["loss_pct"] == 100.0 and fields["rtt_avg_ms"] is None


def test_cycle_survives_a_writer_error(fake_writer, capsys):
    def boom(*args):
        raise RuntimeError("influx down")

    fake_writer.write_point = boom
    make_prober(fake_writer).run_cycle()  # must not raise
    assert "influx down" in capsys.readouterr().out


def test_cycle_survives_a_probe_error(fake_writer, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("bug")

    monkeypatch.setattr(probe, "run_target", boom)
    make_prober(fake_writer).run_cycle()
    assert fake_writer.points == []


def test_icmp_falls_back_to_tcp_when_unavailable(fake_writer):
    net = FakeNet([15, 15])

    def denied():
        raise PermissionError("no ping")

    net.icmp_socket = denied
    make_prober(fake_writer, targets="icmp:a.test", net=net).run_cycle()
    _m, tags, fields = fake_writer.points[0]
    assert tags["kind"] == "tcp" and tags["target"] == "a.test:443"
    assert net.calls[0][:3] == ("tcp", "a.test", 443)
    assert fields["received"] == 2


def test_both_influxdb_versions_accept_probe_points():
    """The module writes through speedtest_runner's v1 and v2 writers unchanged."""
    sent = {}

    class V1Client:
        def write_points(self, points):
            sent["v1"] = points

    w1 = speedtest_runner.InfluxDB1Writer.__new__(speedtest_runner.InfluxDB1Writer)
    w1.client = V1Client()
    w1.write_point("probe", {"target": "t", "kind": "tcp"}, {"loss_pct": 0.0, "jitter_ms": None})
    assert sent["v1"][0]["measurement"] == "probe"
    assert sent["v1"][0]["fields"] == {"loss_pct": 0.0}

    class V2Api:
        def write(self, bucket, record):
            sent["v2"] = (bucket, record)

    w2 = speedtest_runner.InfluxDB2Writer.__new__(speedtest_runner.InfluxDB2Writer)
    w2.bucket = "b"
    w2.write_api = V2Api()
    w2.write_point("probe", {"target": "t"}, {"loss_pct": 0.0})
    assert sent["v2"][0] == "b"


def test_prober_uses_the_runners_writer_factory():
    assert probe.create_influxdb_writer is speedtest_runner.create_influxdb_writer


# ---------------------------------------------------------------- pacing and main


def test_run_forever_sleeps_out_the_rest_of_the_interval(fake_writer):
    net = FakeNet([10, 20])
    p = make_prober(fake_writer, net=net)
    stops = iter([False, True])
    p.run_forever(should_stop=lambda: next(stops))
    assert len(fake_writer.points) == 1
    # The cycle took 30 ms of fake clock; the pause is the rest of the 60 s.
    assert net.slept[-1] == pytest.approx(60 - 0.03)


def test_main_once_runs_a_single_cycle(monkeypatch, fake_writer):
    monkeypatch.setattr(probe, "wait_for_influxdb", lambda: True)
    monkeypatch.setattr(probe, "create_influxdb_writer", lambda: fake_writer)
    monkeypatch.setattr(probe, "Net", lambda: FakeNet([5, 5]))
    monkeypatch.setenv("PROBE_TARGETS", "tcp:a.test")
    monkeypatch.setenv("PROBE_COUNT", "2")
    monkeypatch.setenv("PROBE_TIMING", "0")  # the timing probe has its own tests
    probe.main(["--once"])
    assert len(fake_writer.points) == 1
    assert fake_writer.closed


def test_main_bad_config_exits_2(monkeypatch, capsys):
    monkeypatch.setenv("PROBE_INTERVAL", "abc")
    with pytest.raises(SystemExit) as exc:
        probe.main(["--once"])
    assert exc.value.code == 2
    assert "PROBE_INTERVAL" in capsys.readouterr().err


def test_main_exits_1_when_influxdb_never_ready(monkeypatch):
    monkeypatch.setattr(probe, "wait_for_influxdb", lambda: False)
    with pytest.raises(SystemExit) as exc:
        probe.main(["--once"])
    assert exc.value.code == 1


# ------------------------------------------------ credential / token redaction

SECRET_URL = "https://user:secret@example.test:8443/health?token=abc#frag"
LEAKS = ("secret", "user", "token", "abc", "frag")


def _assert_clean(text):
    for word in LEAKS:
        assert word not in text, f"{word!r} leaked in {text!r}"


def test_https_label_has_only_host_port_path():
    t = probe.parse_target(f"https:{SECRET_URL}")
    assert t.label == "example.test:8443/health"
    assert (t.host, t.port, t.url) == ("example.test", 8443, SECRET_URL)


@pytest.mark.parametrize(
    "spec,label",
    [
        ("https:https://example.test", "example.test/"),
        ("https:https://example.test/", "example.test/"),
        ("https:https://example.test:443/x", "example.test:443/x"),
        ("https:https://u:p@example.test?q=1", "example.test/"),
        ("https:https://[2001:db8::1]:8443/a?k=v", "[2001:db8::1]:8443/a"),
    ],
)
def test_https_label_forms(spec, label):
    assert probe.parse_target(spec).label == label


@pytest.mark.parametrize(
    "spec",
    [
        "https:http://user:secret@example.test/?token=abc",
        "https:https://user:secret@/?token=abc",
        "https:https://user:secret@example.test:notaport/?token=abc",
        "https:https://user:secret@[bad/?token=abc",
        "tcp:user:secret@example.test:notaport",
        "tcp:user:secret@example.test:99999",
        "icmp:",
    ],
)
def test_config_errors_do_not_echo_credentials(spec):
    with pytest.raises(probe.ConfigError) as exc:
        probe.parse_target(spec)
    _assert_clean(str(exc.value))


def test_bad_target_in_list_error_is_clean():
    with pytest.raises(probe.ConfigError) as exc:
        probe.parse_targets("tcp:a.test:80,https:http://user:secret@x.test/?token=abc")
    _assert_clean(str(exc.value))


def test_cycle_output_and_tag_are_clean(fake_writer, capsys):
    target = probe.parse_target(f"https:{SECRET_URL}")
    config = probe.ProbeConfig(targets=[target], interval=60, count=2, timeout=1.0)
    probe.Prober(config, fake_writer, FakeNet([10, 12])).run_cycle()
    _m, tags, _f = fake_writer.points[0]
    assert tags["target"] == "example.test:8443/health"
    _assert_clean(repr(tags) + capsys.readouterr().out)


def test_https_http_client_errors_are_a_lost_sample_and_clean(fake_writer, capsys):
    import http.client

    class Boom(FakeNet):
        def https_head(self, url, timeout):
            raise http.client.InvalidURL(f"URL can't contain control characters. {url!r}")

    target = probe.parse_target(f"https:{SECRET_URL}")
    out = probe.run_target(Boom(), target, count=2, timeout=1.0)
    assert out["loss_pct"] == 100.0

    class Weird(FakeNet):
        def https_head(self, url, timeout):
            raise RuntimeError(f"unexpected for {url}")

    config = probe.ProbeConfig(targets=[target], interval=60, count=1, timeout=1.0)
    probe.Prober(config, fake_writer, Weird()).run_cycle()
    _assert_clean(capsys.readouterr().out)


def test_main_banner_is_clean(monkeypatch, capsys):
    monkeypatch.setenv("PROBE_TARGETS", f"https:{SECRET_URL}")
    monkeypatch.setattr(probe, "wait_for_influxdb", lambda: False)
    with pytest.raises(SystemExit):
        probe.main(["--once"])
    cap = capsys.readouterr()
    _assert_clean(cap.out + cap.err)
