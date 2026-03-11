#!/usr/bin/env python3
"""
NetPulse - Cloudflare Enhanced Speed Test Runner

Runs periodic speed tests using Cloudflare's speed test infrastructure
(speed.cloudflare.com) and logs results to InfluxDB.

Detects ISP changes by tracking external IP, ASN, and ISP name.
Supports both InfluxDB 1.x (username/password) and 2.x (token-based) authentication.
Supports interval-based and cron-based scheduling.
"""

import json
import os
import statistics
import time
from datetime import datetime
from typing import Optional

import requests

# Configuration from environment
INFLUXDB_URL = os.getenv("INFLUXDB_URL", "http://localhost:8086")
INFLUXDB_VERSION = os.getenv("INFLUXDB_VERSION", "2")  # "1" or "2"

# InfluxDB 2.x settings (token-based)
INFLUXDB_TOKEN = os.getenv("INFLUXDB_TOKEN", "")
INFLUXDB_ORG = os.getenv("INFLUXDB_ORG", "netpulse")
INFLUXDB_BUCKET = os.getenv("INFLUXDB_BUCKET", "netpulse")

# InfluxDB 1.x settings (username/password)
INFLUXDB_USERNAME = os.getenv("INFLUXDB_USERNAME", "")
INFLUXDB_PASSWORD = os.getenv("INFLUXDB_PASSWORD", "")
INFLUXDB_DATABASE = os.getenv("INFLUXDB_DATABASE", "netpulse")

# Scheduling: use SPEEDTEST_CRON if set, otherwise fall back to SPEEDTEST_INTERVAL
SPEEDTEST_INTERVAL = int(os.getenv("SPEEDTEST_INTERVAL", "21600"))  # 6 hours default
SPEEDTEST_CRON = os.getenv("SPEEDTEST_CRON", "")  # e.g. "0 3,9,15,21 * * *"

# Privacy: set to "true" to mask external IP in stored data
HIDE_EXTERNAL_IP = os.getenv("HIDE_EXTERNAL_IP", "false").lower() in ("true", "1", "yes")

# Cloudflare speed test endpoint
CF_SPEED_HOST = "https://speed.cloudflare.com"

# Number of latency measurements to take
LATENCY_SAMPLES = int(os.getenv("CF_LATENCY_SAMPLES", "20"))

# Download/upload test sizes (bytes)
DOWNLOAD_SIZES = [100_000, 1_000_000, 10_000_000, 25_000_000, 100_000_000]
UPLOAD_SIZES = [100_000, 1_000_000, 10_000_000, 25_000_000]


class ISPTracker:
    """Tracks ISP information and detects changes with persistent state."""

    # State file location - works for both local and Docker
    STATE_FILE = os.getenv("NETPULSE_STATE_FILE", "/tmp/netpulse_cf_state.json")

    def __init__(self):
        self.last_ip: Optional[str] = None
        self.last_isp: Optional[str] = None
        self.last_asn: Optional[str] = None
        self.last_connection_type: Optional[str] = None

        # Load persisted state from file
        self._load_state()

    def _load_state(self):
        """Load previous ISP state from file."""
        try:
            if os.path.exists(self.STATE_FILE):
                with open(self.STATE_FILE, 'r') as f:
                    state = json.load(f)
                    self.last_ip = state.get("ip")
                    self.last_isp = state.get("isp")
                    self.last_asn = state.get("asn")
                    self.last_connection_type = state.get("connection_type")
                    print(f"Loaded previous state: IP={self.last_ip}, ISP={self.last_isp}")
        except Exception as e:
            print(f"Could not load previous state: {e}")

    def _save_state(self):
        """Save current ISP state to file for persistence."""
        try:
            state = {
                "ip": self.last_ip,
                "isp": self.last_isp,
                "asn": self.last_asn,
                "connection_type": self.last_connection_type,
                "updated_at": datetime.now().isoformat()
            }
            with open(self.STATE_FILE, 'w') as f:
                json.dump(state, f)
        except Exception as e:
            print(f"Could not save state: {e}")

    def get_ip_info(self) -> dict:
        """
        Get current external IP and ISP information.
        Uses multiple services for redundancy.
        """
        ip_info = {
            "ip": None,
            "isp": None,
            "asn": None,
            "org": None,
            "city": None,
            "region": None,
            "country": None,
            "connection_type": None
        }

        # Try ipinfo.io first (no API key needed for basic info)
        try:
            response = requests.get("https://ipinfo.io/json", timeout=10)
            if response.status_code == 200:
                data = response.json()
                ip_info["ip"] = data.get("ip")
                ip_info["org"] = data.get("org", "")  # Contains ASN and org name
                ip_info["city"] = data.get("city")
                ip_info["region"] = data.get("region")
                ip_info["country"] = data.get("country")

                # Parse ASN from org field (format: "AS12345 Company Name")
                org = data.get("org", "")
                if org.startswith("AS"):
                    parts = org.split(" ", 1)
                    ip_info["asn"] = parts[0]
                    ip_info["isp"] = parts[1] if len(parts) > 1 else org
                else:
                    ip_info["isp"] = org
        except Exception as e:
            print(f"Error fetching from ipinfo.io: {e}")

        # Fallback to ip-api.com if ipinfo.io failed
        if not ip_info["ip"]:
            try:
                response = requests.get("http://ip-api.com/json", timeout=10)
                if response.status_code == 200:
                    data = response.json()
                    ip_info["ip"] = data.get("query")
                    ip_info["isp"] = data.get("isp")
                    ip_info["asn"] = data.get("as", "").split(" ")[0] if data.get("as") else None
                    ip_info["org"] = data.get("org")
                    ip_info["city"] = data.get("city")
                    ip_info["region"] = data.get("regionName")
                    ip_info["country"] = data.get("countryCode")
            except Exception as e:
                print(f"Error fetching from ip-api.com: {e}")

        # Infer connection type based on ISP name keywords
        if ip_info["isp"]:
            isp_lower = ip_info["isp"].lower()
            if any(kw in isp_lower for kw in ["mobile", "cellular", "wireless", "lte", "5g", "t-mobile", "verizon wireless", "at&t mobility"]):
                ip_info["connection_type"] = "cellular"
            elif any(kw in isp_lower for kw in ["cable", "comcast", "xfinity", "spectrum", "cox", "charter"]):
                ip_info["connection_type"] = "cable"
            elif any(kw in isp_lower for kw in ["fiber", "fios", "att fiber", "google fiber"]):
                ip_info["connection_type"] = "fiber"
            elif any(kw in isp_lower for kw in ["dsl", "centurylink", "frontier"]):
                ip_info["connection_type"] = "dsl"
            else:
                ip_info["connection_type"] = "unknown"

        return ip_info

    def check_for_change(self, current_info: dict) -> dict:
        """
        Check if ISP has changed since last check.
        Returns change information if detected.
        """
        change_info = {
            "changed": False,
            "ip_changed": False,
            "isp_changed": False,
            "asn_changed": False,
            "previous_ip": self.last_ip,
            "previous_isp": self.last_isp,
            "previous_asn": self.last_asn,
            "previous_connection_type": self.last_connection_type
        }

        current_ip = current_info.get("ip")
        current_isp = current_info.get("isp")
        current_asn = current_info.get("asn")
        current_connection_type = current_info.get("connection_type")

        # Check for changes (only if we have previous values)
        if self.last_ip is not None:
            if current_ip != self.last_ip:
                change_info["ip_changed"] = True
                change_info["changed"] = True

            if current_isp != self.last_isp:
                change_info["isp_changed"] = True
                change_info["changed"] = True

            if current_asn != self.last_asn:
                change_info["asn_changed"] = True
                change_info["changed"] = True

        # Update last known values
        self.last_ip = current_ip
        self.last_isp = current_isp
        self.last_asn = current_asn
        self.last_connection_type = current_connection_type

        # Persist state to file for next run (important for --once mode)
        self._save_state()

        return change_info


class InfluxDBWriter:
    """Abstract base class for InfluxDB writers."""

    def write_point(self, measurement: str, tags: dict, fields: dict):
        """Write a data point to InfluxDB."""
        raise NotImplementedError

    def close(self):
        """Close the connection."""
        pass


class InfluxDB2Writer(InfluxDBWriter):
    """Writer for InfluxDB 2.x using token-based authentication."""

    def __init__(self, url: str, token: str, org: str, bucket: str):
        from influxdb_client import InfluxDBClient
        from influxdb_client.client.write_api import SYNCHRONOUS

        self.bucket = bucket
        self.client = InfluxDBClient(url=url, token=token, org=org)
        self.write_api = self.client.write_api(write_options=SYNCHRONOUS)
        print(f"Connected to InfluxDB 2.x at {url} (org: {org}, bucket: {bucket})")

    def write_point(self, measurement: str, tags: dict, fields: dict):
        from influxdb_client import Point

        point = Point(measurement)
        for key, value in tags.items():
            if value is not None:
                point = point.tag(key, str(value))
        for key, value in fields.items():
            if value is not None:
                point = point.field(key, value)

        self.write_api.write(bucket=self.bucket, record=point)

    def close(self):
        self.client.close()


class InfluxDB1Writer(InfluxDBWriter):
    """Writer for InfluxDB 1.x using username/password authentication."""

    def __init__(self, url: str, username: str, password: str, database: str):
        from influxdb import InfluxDBClient as InfluxDB1Client

        # Parse URL to get host and port
        from urllib.parse import urlparse
        parsed = urlparse(url)
        host = parsed.hostname or "localhost"
        port = parsed.port or 8086
        ssl = parsed.scheme == "https"

        self.database = database
        self.client = InfluxDB1Client(
            host=host,
            port=port,
            username=username,
            password=password,
            database=database,
            ssl=ssl
        )

        # Create database if it doesn't exist
        try:
            self.client.create_database(database)
        except Exception:
            pass  # Database might already exist

        print(f"Connected to InfluxDB 1.x at {host}:{port} (database: {database})")

    def write_point(self, measurement: str, tags: dict, fields: dict):
        # Filter out None values
        clean_tags = {k: str(v) for k, v in tags.items() if v is not None}
        clean_fields = {k: v for k, v in fields.items() if v is not None}

        point = {
            "measurement": measurement,
            "tags": clean_tags,
            "fields": clean_fields
        }
        self.client.write_points([point])

    def close(self):
        self.client.close()


def create_influxdb_writer() -> InfluxDBWriter:
    """Factory function to create the appropriate InfluxDB writer."""
    version = INFLUXDB_VERSION.strip()

    if version == "1":
        if not INFLUXDB_USERNAME:
            raise ValueError("INFLUXDB_USERNAME is required for InfluxDB 1.x")
        return InfluxDB1Writer(
            url=INFLUXDB_URL,
            username=INFLUXDB_USERNAME,
            password=INFLUXDB_PASSWORD,
            database=INFLUXDB_DATABASE
        )
    else:  # Default to version 2
        if not INFLUXDB_TOKEN:
            raise ValueError("INFLUXDB_TOKEN is required for InfluxDB 2.x")
        return InfluxDB2Writer(
            url=INFLUXDB_URL,
            token=INFLUXDB_TOKEN,
            org=INFLUXDB_ORG,
            bucket=INFLUXDB_BUCKET
        )


class CloudflareSpeedtestRunner:
    """Runs Cloudflare speed tests and logs results to InfluxDB."""

    def __init__(self):
        self.writer = create_influxdb_writer()
        self.isp_tracker = ISPTracker()
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "NetPulse/2.0 (Cloudflare Speed Test Runner)"
        })

    def get_cloudflare_info(self) -> dict:
        """
        Get Cloudflare PoP (Point of Presence) and connection info.
        Queries speed.cloudflare.com/cdn-cgi/trace for server metadata.
        """
        info = {
            "colo": None,
            "loc": None,
            "ip": None,
            "http": None,
        }
        try:
            response = self.session.get(
                f"{CF_SPEED_HOST}/cdn-cgi/trace", timeout=10
            )
            response.raise_for_status()
            for line in response.text.splitlines():
                if "=" in line:
                    key, _, value = line.partition("=")
                    info[key.strip()] = value.strip()
        except Exception as e:
            print(f"Error fetching Cloudflare trace info: {e}")
        return info

    def measure_latency(self) -> tuple:
        """
        Measure unloaded latency, jitter, and packet loss using
        repeated small requests to the Cloudflare speed test server.

        Returns: (median_latency_ms, jitter_ms, packet_loss_pct)
        """
        rtts = []
        for _ in range(LATENCY_SAMPLES):
            start = time.monotonic()
            try:
                r = self.session.get(
                    f"{CF_SPEED_HOST}/__down?bytes=0", timeout=5
                )
                r.raise_for_status()
            except Exception:
                continue
            rtts.append((time.monotonic() - start) * 1000)

        if not rtts:
            return None, None, 100.0

        packet_loss = (LATENCY_SAMPLES - len(rtts)) / LATENCY_SAMPLES * 100

        median_latency = statistics.median(rtts)

        # Jitter = mean of absolute differences between consecutive samples
        if len(rtts) > 1:
            diffs = [abs(rtts[i + 1] - rtts[i]) for i in range(len(rtts) - 1)]
            jitter = statistics.mean(diffs)
        else:
            jitter = 0.0

        return median_latency, jitter, packet_loss

    def measure_download(self) -> Optional[float]:
        """
        Measure download speed by downloading files of progressively larger
        sizes and returning the 90th-percentile throughput in Mbps.
        """
        throughputs = []

        for size in DOWNLOAD_SIZES:
            start = time.monotonic()
            total = 0
            try:
                r = self.session.get(
                    f"{CF_SPEED_HOST}/__down?bytes={size}",
                    timeout=120,
                    stream=True,
                )
                r.raise_for_status()
                for chunk in r.iter_content(chunk_size=65536):
                    total += len(chunk)
            except Exception as e:
                print(f"  Download error at {size // 1000}KB: {e}")
                break  # stop at first failure for large sizes

            elapsed = time.monotonic() - start
            if elapsed > 0 and total > 0:
                mbps = (total * 8) / elapsed / 1_000_000
                throughputs.append(mbps)

        if not throughputs:
            return None

        # Return 90th percentile
        throughputs.sort()
        p90_idx = max(0, int(len(throughputs) * 0.9) - 1)
        return throughputs[p90_idx]

    def measure_upload(self) -> Optional[float]:
        """
        Measure upload speed by uploading payloads of progressively larger
        sizes and returning the 90th-percentile throughput in Mbps.
        """
        throughputs = []

        for size in UPLOAD_SIZES:
            data = b"0" * size
            start = time.monotonic()
            try:
                r = self.session.post(
                    f"{CF_SPEED_HOST}/__up",
                    data=data,
                    timeout=120,
                    headers={"Content-Type": "application/octet-stream"},
                )
                r.raise_for_status()
            except Exception as e:
                print(f"  Upload error at {size // 1000}KB: {e}")
                break

            elapsed = time.monotonic() - start
            if elapsed > 0:
                mbps = (size * 8) / elapsed / 1_000_000
                throughputs.append(mbps)

        if not throughputs:
            return None

        # Return 90th percentile
        throughputs.sort()
        p90_idx = max(0, int(len(throughputs) * 0.9) - 1)
        return throughputs[p90_idx]

    def run_speedtest(self) -> Optional[dict]:
        """
        Run a full Cloudflare speed test.
        Returns a dict of results or None on failure.
        """
        print(f"[{datetime.now().isoformat()}] Starting Cloudflare speed test...")

        # Step 1: Get Cloudflare PoP info
        cf_info = self.get_cloudflare_info()
        colo = cf_info.get("colo", "unknown")
        loc = cf_info.get("loc", "unknown")
        print(f"  Cloudflare PoP: {colo} ({loc})")

        # Step 2: Measure latency
        print(f"  Measuring latency ({LATENCY_SAMPLES} samples)...")
        latency_ms, jitter_ms, packet_loss = self.measure_latency()
        if latency_ms is not None:
            print(f"  Latency: {latency_ms:.2f} ms  Jitter: {jitter_ms:.2f} ms  Loss: {packet_loss:.1f}%")
        else:
            print("  Latency measurement failed")

        # Step 3: Measure download
        print("  Measuring download speed...")
        download_mbps = self.measure_download()
        if download_mbps is not None:
            print(f"  Download: {download_mbps:.2f} Mbps")
        else:
            print("  Download measurement failed")

        # Step 4: Measure upload
        print("  Measuring upload speed...")
        upload_mbps = self.measure_upload()
        if upload_mbps is not None:
            print(f"  Upload: {upload_mbps:.2f} Mbps")
        else:
            print("  Upload measurement failed")

        if download_mbps is None and upload_mbps is None and latency_ms is None:
            print("All measurements failed - aborting")
            return None

        return {
            "timestamp": datetime.now().isoformat(),
            "download_mbps": download_mbps,
            "upload_mbps": upload_mbps,
            "latency_ms": latency_ms,
            "jitter_ms": jitter_ms,
            "packet_loss": packet_loss,
            "colo": colo,
            "colo_location": loc,
        }

    def write_speedtest_result(self, result: dict, ip_info: dict, isp_change: dict):
        """Write Cloudflare speed test results to InfluxDB."""
        try:
            # Mask IP if privacy mode is enabled
            external_ip = ip_info.get("ip", "unknown")
            if HIDE_EXTERNAL_IP:
                external_ip = "hidden"

            # Main speedtest metrics
            tags = {
                "colo": result.get("colo", "unknown"),
                "colo_location": result.get("colo_location", "unknown"),
                "isp": ip_info.get("isp", "unknown"),
                "asn": ip_info.get("asn", "unknown"),
                "connection_type": ip_info.get("connection_type", "unknown"),
                "external_ip": external_ip,
            }
            fields = {
                "download_mbps": result.get("download_mbps") or 0.0,
                "upload_mbps": result.get("upload_mbps") or 0.0,
                "latency_ms": result.get("latency_ms") or 0.0,
                "jitter_ms": result.get("jitter_ms") or 0.0,
                "packet_loss": result.get("packet_loss") if result.get("packet_loss") is not None else 0.0,
            }

            self.writer.write_point("cloudflare_speedtest", tags, fields)

            # Write ISP change event if detected
            if isp_change.get("changed"):
                change_tags = {
                    "previous_isp": isp_change.get("previous_isp", "unknown"),
                    "current_isp": ip_info.get("isp", "unknown"),
                    "previous_asn": isp_change.get("previous_asn", "unknown"),
                    "current_asn": ip_info.get("asn", "unknown"),
                    "previous_connection_type": isp_change.get("previous_connection_type", "unknown"),
                    "current_connection_type": ip_info.get("connection_type", "unknown"),
                }
                change_fields = {
                    "ip_changed": isp_change.get("ip_changed", False),
                    "isp_changed": isp_change.get("isp_changed", False),
                    "asn_changed": isp_change.get("asn_changed", False),
                    "previous_ip": isp_change.get("previous_ip", ""),
                    "current_ip": "" if HIDE_EXTERNAL_IP else ip_info.get("ip", ""),
                    "event": 1,  # Marker for annotations
                }

                self.writer.write_point("isp_change", change_tags, change_fields)
                print(f"  ⚠️  ISP CHANGE DETECTED: {isp_change.get('previous_isp')} -> {ip_info.get('isp')}")

            print("  Results written to InfluxDB")

        except Exception as e:
            print(f"Error writing to InfluxDB: {e}")

    def run_test_cycle(self):
        """Run a complete test cycle: get IP info, run speed test, log results."""
        print(f"\n{'='*60}")
        print(f"[{datetime.now().isoformat()}] Starting Cloudflare test cycle")
        print(f"{'='*60}")

        # Get current IP/ISP information
        ip_info = self.isp_tracker.get_ip_info()
        display_ip = "hidden" if HIDE_EXTERNAL_IP else ip_info.get("ip")
        print(f"  External IP: {display_ip}")
        print(f"  ISP: {ip_info.get('isp')}")
        print(f"  ASN: {ip_info.get('asn')}")
        print(f"  Connection Type: {ip_info.get('connection_type')}")

        # Check for ISP change
        isp_change = self.isp_tracker.check_for_change(ip_info)

        # Run speed test
        result = self.run_speedtest()

        if result:
            self.write_speedtest_result(result, ip_info, isp_change)
        else:
            # Write at least the IP info if speed test failed
            try:
                tags = {
                    "isp": ip_info.get("isp", "unknown"),
                    "connection_type": ip_info.get("connection_type", "unknown"),
                }
                fields = {"error": 1}
                self.writer.write_point("cloudflare_speedtest_error", tags, fields)
            except Exception as e:
                print(f"Error logging speed test failure: {e}")

        print(f"[{datetime.now().isoformat()}] Test cycle complete")


def wait_for_influxdb():
    """Wait for InfluxDB to be ready."""
    print("Waiting for InfluxDB to be ready...")
    max_retries = 30
    retry_interval = 5

    for i in range(max_retries):
        try:
            response = requests.get(f"{INFLUXDB_URL}/health", timeout=5)
            if response.status_code == 200:
                print("InfluxDB is ready!")
                return True
        except Exception:
            pass

        print(f"  Waiting... ({i+1}/{max_retries})")
        time.sleep(retry_interval)

    print("Failed to connect to InfluxDB")
    return False


def get_scheduler(interval: int, cron_expr: str):
    """
    Return a scheduling function that runs the provided job on schedule.

    When cron_expr is provided, runs the job at times defined by the cron
    expression. Otherwise runs every `interval` seconds.
    """
    if cron_expr:
        try:
            from croniter import croniter
            # Validate the cron expression
            croniter(cron_expr)
            return "cron", cron_expr
        except Exception as e:
            print(f"Invalid SPEEDTEST_CRON expression '{cron_expr}': {e}")
            print(f"Falling back to interval-based scheduling ({interval}s)")

    return "interval", interval


def run_cron_scheduler(runner, cron_expr: str):
    """Run tests on a cron schedule."""
    from croniter import croniter

    print(f"Running on cron schedule: {cron_expr}")
    while True:
        now = datetime.now()
        cron = croniter(cron_expr, now)
        next_run = cron.get_next(datetime)
        wait_seconds = (next_run - datetime.now()).total_seconds()

        if wait_seconds > 0:
            print(f"Next run at {next_run.strftime('%Y-%m-%d %H:%M:%S')} "
                  f"(in {wait_seconds/60:.1f} minutes)")
            time.sleep(wait_seconds)

        runner.run_test_cycle()


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="NetPulse - Cloudflare Enhanced Speed Test Runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                         # Run as daemon with interval-based scheduling
  %(prog)s --once                  # Run single test and exit (for systemd timer)
  %(prog)s --interval 21600        # Run every 6 hours (daemon mode)
  %(prog)s --cron "0 3,9,15,21 * * *"  # Run at 03:00, 09:00, 15:00, 21:00 daily
        """
    )
    parser.add_argument(
        "--once", "-1",
        action="store_true",
        help="Run a single speed test and exit (for use with systemd timers)"
    )
    parser.add_argument(
        "--interval", "-i",
        type=int,
        default=None,
        help=f"Override test interval in seconds (default: {SPEEDTEST_INTERVAL})"
    )
    parser.add_argument(
        "--cron",
        type=str,
        default=None,
        help="Cron expression for scheduling (overrides --interval). "
             "Example: '0 3,9,15,21 * * *' runs at 03:00, 09:00, 15:00, 21:00"
    )
    args = parser.parse_args()

    # Resolve scheduling configuration (CLI args override env vars)
    cron_expr = args.cron or SPEEDTEST_CRON
    interval = args.interval if args.interval else SPEEDTEST_INTERVAL

    print("=" * 60)
    print("NetPulse - Cloudflare Enhanced Speed Test Runner")
    print("=" * 60)
    print(f"InfluxDB URL: {INFLUXDB_URL}")
    print(f"InfluxDB Org: {INFLUXDB_ORG}")
    print(f"InfluxDB Bucket: {INFLUXDB_BUCKET}")
    print(f"IP Privacy Mode: {'enabled (IP hidden)' if HIDE_EXTERNAL_IP else 'disabled'}")
    if args.once:
        print("Mode: Single run (--once)")
    elif cron_expr:
        print(f"Mode: Cron schedule ({cron_expr})")
    else:
        print(f"Mode: Interval ({interval}s / {interval/3600:.1f}h)")
    print("=" * 60)

    # Wait for InfluxDB
    if not wait_for_influxdb():
        return

    # Create runner
    runner = CloudflareSpeedtestRunner()

    # Run initial test
    runner.run_test_cycle()

    # If --once flag, exit after single run
    if args.once:
        print("Single run completed. Exiting.")
        return

    # Schedule periodic tests
    sched_type, sched_value = get_scheduler(interval, cron_expr)

    if sched_type == "cron":
        run_cron_scheduler(runner, sched_value)
    else:
        import schedule as sched
        sched.every(sched_value).seconds.do(runner.run_test_cycle)
        while True:
            sched.run_pending()
            time.sleep(1)


if __name__ == "__main__":
    main()
