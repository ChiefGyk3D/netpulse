#!/usr/bin/env python3
"""
NetPulse - Continuous Probe

Measures packet loss, latency and jitter to a few configurable targets with
small, frequent probes (ICMP echo, TCP connect, HTTPS HEAD) and writes one
"probe" point per target per cycle to InfluxDB.

Runs beside the speedtest runner and shares its InfluxDB writers (1.x and 2.x),
so both versions get the same points. It is a separate entrypoint: the
speedtest runner does not import or schedule anything from here.

Standard library only. ICMP uses an unprivileged datagram socket (no raw
socket, no CAP_NET_RAW). Where the kernel does not allow that, ICMP targets
fall back to a TCP connect on port 443.
"""

import argparse
import http.client
import itertools
import math
import os
import socket
import ssl
import struct
import sys
import time
from dataclasses import dataclass
from urllib.parse import urlparse

from speedtest_runner import ConfigError, create_influxdb_writer, wait_for_influxdb

# Public, well-known targets only. Override with PROBE_TARGETS.
DEFAULT_TARGETS = "icmp:1.1.1.1,tcp:1.1.1.1:443,https:https://www.cloudflare.com/"
DEFAULT_INTERVAL = 60  # one burst per target per minute
DEFAULT_COUNT = 5  # samples per burst; loss and jitter need more than one
DEFAULT_TIMEOUT = 2.0  # seconds to wait for each sample

MIN_INTERVAL = 10  # a floor, so a typo cannot turn this into a flood
MAX_COUNT = 20
MAX_TIMEOUT = 30.0
SAMPLE_GAP = 0.2  # seconds between samples in a burst

ICMP_ECHO_REQUEST = 8
ICMP_ECHO_REPLY = 0
ICMP_PAYLOAD = b"netpulse-probe"


@dataclass(frozen=True)
class Target:
    kind: str  # "icmp", "tcp" or "https"
    host: str
    port: int | None
    label: str  # the value of the "target" tag
    url: str | None = None


@dataclass(frozen=True)
class ProbeConfig:
    targets: list[Target]
    interval: int
    count: int
    timeout: float


def _redact(spec: str) -> str:
    """The spec as it may appear in an error: anything that can carry userinfo, a query or a fragment is hidden."""
    return "<redacted>" if any(c in spec for c in "@?#") else repr(spec)


def _https_label(parsed) -> str:
    """host[:port]/path, from an already-parsed URL. Never userinfo, query or fragment."""
    host = parsed.hostname
    if ":" in host:  # an IPv6 literal needs its brackets back
        host = f"[{host}]"
    port = f":{parsed.port}" if parsed.port is not None else ""
    return f"{host}{port}{parsed.path or '/'}"


def parse_target(spec: str) -> Target:
    """Parse "icmp:HOST", "tcp:HOST[:PORT]" or "https:URL" into a Target."""
    kind, _, rest = spec.strip().partition(":")
    kind = kind.lower()
    rest = rest.strip()
    if kind not in ("icmp", "tcp", "https") or not rest:
        raise ConfigError(f"PROBE_TARGETS entry must look like icmp:HOST, tcp:HOST[:PORT] or https:URL, got {_redact(spec)}")
    if kind == "icmp":
        return Target("icmp", rest, None, rest)
    if kind == "tcp":
        host, sep, port_text = rest.rpartition(":")
        if not sep:
            host, port_text = rest, "443"
        try:
            port = int(port_text)
        except ValueError:
            raise ConfigError(f"PROBE_TARGETS tcp port must be a number, got {_redact(spec)}") from None
        if not host or not 0 < port < 65536:
            raise ConfigError(f"PROBE_TARGETS tcp entry needs a host and a port from 1 to 65535, got {_redact(spec)}")
        return Target("tcp", host, port, f"{host}:{port}")
    try:
        parsed = urlparse(rest)
        host, port, label = parsed.hostname, parsed.port or 443, None
        if parsed.scheme == "https" and host:
            label = _https_label(parsed)
    except ValueError:  # a malformed port or bracket; the message must not echo the URL
        label = None
    if label is None:
        raise ConfigError(f"PROBE_TARGETS https entry must be a valid https:// URL, got {_redact(spec)}")
    return Target("https", host, port, label, rest)


def parse_targets(raw: str) -> list[Target]:
    targets = [parse_target(item) for item in raw.split(",") if item.strip()]
    if not targets:
        raise ConfigError("PROBE_TARGETS must list at least one target")
    return targets


def _env_number(name: str, default, cast, low, high):
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = cast(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from None
    if not math.isfinite(value) or not low <= value <= high:
        raise ConfigError(f"{name} must be between {low} and {high}, got {raw!r}")
    return value


def load_config() -> ProbeConfig:
    """Read the PROBE_* settings when needed, so a bad value is a clear error, not an import crash."""
    targets = parse_targets(os.getenv("PROBE_TARGETS", DEFAULT_TARGETS))
    interval = _env_number("PROBE_INTERVAL", DEFAULT_INTERVAL, int, MIN_INTERVAL, 86400)
    count = _env_number("PROBE_COUNT", DEFAULT_COUNT, int, 1, MAX_COUNT)
    timeout = _env_number("PROBE_TIMEOUT", DEFAULT_TIMEOUT, float, 0.1, MAX_TIMEOUT)
    # Targets run one after another, so the worst case (every sample times out)
    # must still fit in one interval or cycles would pile up.
    worst = len(targets) * count * (timeout + SAMPLE_GAP)
    if worst > interval:
        raise ConfigError(
            f"PROBE_INTERVAL ({interval}s) is shorter than the worst-case cycle ({worst:.0f}s) "
            "for these targets, count and timeout; raise the interval or lower the others"
        )
    return ProbeConfig(targets=targets, interval=interval, count=count, timeout=timeout)


def summarize(rtts: list[float], sent: int) -> dict:
    """Loss, latency and jitter for one burst. Jitter is the mean gap between consecutive samples."""
    received = len(rtts)
    stats = {
        "sent": sent,
        "received": received,
        "loss_pct": round((sent - received) * 100.0 / sent, 2),
        "rtt_min_ms": None,
        "rtt_avg_ms": None,
        "rtt_max_ms": None,
        "jitter_ms": None,
    }
    if rtts:
        stats["rtt_min_ms"] = min(rtts)
        stats["rtt_avg_ms"] = sum(rtts) / received
        stats["rtt_max_ms"] = max(rtts)
    if received > 1:
        gaps = [abs(b - a) for a, b in itertools.pairwise(rtts)]
        stats["jitter_ms"] = sum(gaps) / len(gaps)
    return stats


def icmp_checksum(data: bytes) -> int:
    """RFC 1071 internet checksum."""
    if len(data) % 2:
        data += b"\x00"
    total = sum(struct.unpack(f"!{len(data) // 2}H", data))
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return ~total & 0xFFFF


def build_echo_request(ident: int, seq: int) -> bytes:
    header = struct.pack("!BBHHH", ICMP_ECHO_REQUEST, 0, 0, ident, seq)
    checksum = icmp_checksum(header + ICMP_PAYLOAD)
    return struct.pack("!BBHHH", ICMP_ECHO_REQUEST, 0, checksum, ident, seq) + ICMP_PAYLOAD


class Net:
    """The network layer. Every socket the module opens goes through here, so tests replace it."""

    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float):
        time.sleep(seconds)

    def resolve(self, host: str) -> str:
        return socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_DGRAM)[0][4][0]

    def icmp_socket(self):
        # SOCK_DGRAM, not SOCK_RAW: no CAP_NET_RAW. The kernel allows it only for
        # groups in net.ipv4.ping_group_range, which Docker opens up by default.
        return socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_ICMP)

    def tcp_connect(self, host: str, port: int, timeout: float):
        socket.create_connection((host, port), timeout=timeout).close()

    def https_head(self, url: str, timeout: float):
        """HEAD the URL with full certificate verification; any 5xx counts as a failed sample."""
        parsed = urlparse(url)
        conn = http.client.HTTPSConnection(
            parsed.hostname, parsed.port or 443, timeout=timeout, context=ssl.create_default_context()
        )
        try:
            path = (parsed.path or "/") + (f"?{parsed.query}" if parsed.query else "")
            conn.request("HEAD", path, headers={"User-Agent": "netpulse-probe"})
            status = conn.getresponse().status
        finally:
            conn.close()
        if status >= 500:
            raise OSError(f"server answered {status}")


def icmp_available(net: Net) -> bool:
    """True when this process may open an unprivileged ICMP socket."""
    try:
        net.icmp_socket().close()
    except OSError:
        return False
    return True


def probe_icmp(net: Net, host: str, timeout: float, seq: int) -> float | None:
    """One echo request. Returns the round trip in ms, or None when it was lost."""
    try:
        address = net.resolve(host)
        sock = net.icmp_socket()
    except OSError:
        return None
    try:
        # The kernel rewrites the identifier on a datagram socket, so only the
        # sequence number tells our reply from stray ICMP traffic.
        packet = build_echo_request(os.getpid() & 0xFFFF, seq)
        sock.settimeout(timeout)
        start = net.monotonic()
        deadline = start + timeout
        sock.sendto(packet, (address, 0))
        while True:
            data, _addr = sock.recvfrom(1024)
            now = net.monotonic()
            if len(data) >= 8:
                kind, _code, _csum, _ident, reply_seq = struct.unpack("!BBHHH", data[:8])
                if kind == ICMP_ECHO_REPLY and reply_seq == seq:
                    return (now - start) * 1000.0
            if now >= deadline:
                return None
            sock.settimeout(deadline - now)
    except OSError:  # includes the receive timeout
        return None
    finally:
        sock.close()


def run_target(net: Net, target: Target, count: int, timeout: float) -> dict:
    """Send a burst of `count` samples to one target and summarize it."""
    rtts: list[float] = []
    for i in range(count):
        if i:
            net.sleep(SAMPLE_GAP)
        if target.kind == "icmp":
            rtt = probe_icmp(net, target.host, timeout, seq=i + 1)
        else:
            start = net.monotonic()
            try:
                if target.kind == "tcp":
                    net.tcp_connect(target.host, target.port, timeout)
                else:
                    net.https_head(target.url, timeout)
                rtt = (net.monotonic() - start) * 1000.0
            except (OSError, http.client.HTTPException, ValueError):
                # timeouts, refusals, TLS and name errors; http.client and URL
                # errors can embed the request URL, so none of it is logged
                rtt = None
        if rtt is not None:
            rtts.append(rtt)
    return summarize(rtts, count)


class Prober:
    """Runs probe cycles and writes the results through an InfluxDB writer."""

    def __init__(self, config: ProbeConfig, writer, net: Net | None = None):
        self.config = config
        self.writer = writer
        self.net = net or Net()
        self.targets = self._resolve_icmp(config.targets)

    def _resolve_icmp(self, targets: list[Target]) -> list[Target]:
        """Swap ICMP targets for a TCP connect on 443 when the kernel refuses an ICMP socket."""
        if not any(t.kind == "icmp" for t in targets) or icmp_available(self.net):
            return targets
        print("ICMP sockets are not permitted here (net.ipv4.ping_group_range); probing ICMP targets over TCP port 443")
        return [Target("tcp", t.host, 443, f"{t.host}:443") if t.kind == "icmp" else t for t in targets]

    def run_cycle(self):
        """Probe every target once and write one point per target."""
        for target in self.targets:
            try:
                stats = run_target(self.net, target, self.config.count, self.config.timeout)
                tags = {"target": target.label, "kind": target.kind, "host": target.host}
                self.writer.write_point("probe", tags, stats)
                print(f"  {target.kind} {target.label}: loss {stats['loss_pct']}%, avg {_fmt(stats['rtt_avg_ms'])} ms")
            except Exception as e:  # noqa: BLE001  # keep the daemon alive; error is logged
                print(f"Error probing {target.label}: {type(e).__name__}: {_scrub(str(e), target)}")

    def run_forever(self, should_stop=lambda: False):
        """Run a cycle every interval, sleeping out whatever the cycle left of it."""
        while not should_stop():
            started = self.net.monotonic()
            self.run_cycle()
            self.net.sleep(max(0.0, self.config.interval - (self.net.monotonic() - started)))


def _scrub(text: str, target: Target) -> str:
    """Keep the request URL, with its userinfo and query, out of a log line."""
    if target.url:
        text = text.replace(target.url, target.label)
        parsed = urlparse(target.url)
        for secret in (parsed.password, parsed.username, parsed.query, parsed.fragment):
            if secret:
                text = text.replace(secret, "<redacted>")
    return text


def _fmt(value) -> str:
    """Format a number for a log line; a missing value prints as n/a."""
    return "n/a" if value is None else f"{value:.1f}"


def main(argv: list[str] | None = None):
    """Main entry point."""
    parser = argparse.ArgumentParser(description="NetPulse - Continuous Probe (loss, latency, jitter)")
    parser.add_argument("--once", "-1", action="store_true", help="Run a single cycle and exit")
    args = parser.parse_args(argv)

    try:
        config = load_config()
    except ConfigError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        sys.exit(2)

    print("=" * 60)
    print("NetPulse - Continuous Probe")
    print("=" * 60)
    print(f"Targets: {', '.join(f'{t.kind}:{t.label}' for t in config.targets)}")
    print(f"Interval: {config.interval}s, samples per burst: {config.count}, timeout: {config.timeout}s")
    print("=" * 60)

    if not wait_for_influxdb():
        print("InfluxDB never became ready; exiting with an error", file=sys.stderr)
        sys.exit(1)

    writer = create_influxdb_writer()
    prober = Prober(config, writer, Net())
    try:
        if args.once:
            prober.run_cycle()
        else:
            prober.run_forever()
    finally:
        writer.close()


if __name__ == "__main__":
    main()
