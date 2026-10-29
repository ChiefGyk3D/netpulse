#!/usr/bin/env python3
"""
NetPulse - Network Speed & ISP Monitor

Runs periodic speedtests and logs results to InfluxDB.
Detects ISP changes by tracking external IP, ASN, and ISP name.
Supports both InfluxDB 1.x (username/password) and 2.x (token-based) authentication.
"""

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

import requests
import schedule

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

DEFAULT_SPEEDTEST_INTERVAL = 1800  # 30 minutes


class ConfigError(ValueError):
    """Raised for a bad configuration value, with a message fit to show the user."""


def get_speedtest_interval() -> int:
    """Read SPEEDTEST_INTERVAL when needed, so a bad value is a clear error, not an import crash."""
    raw = os.getenv("SPEEDTEST_INTERVAL")
    if raw is None:
        return DEFAULT_SPEEDTEST_INTERVAL
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"SPEEDTEST_INTERVAL must be a whole number of seconds, got {raw!r}") from None
    if value <= 0:
        raise ConfigError(f"SPEEDTEST_INTERVAL must be greater than zero, got {raw!r}")
    return value


class ISPTracker:
    """Tracks ISP information and detects changes with persistent state."""
    
    # State file location - works for both local and Docker
    STATE_FILE = os.getenv("NETPULSE_STATE_FILE", "/tmp/netpulse_state.json")  # noqa: S108  # persisted state, not a temp file; every deployment overrides it
    
    def __init__(self):
        self.last_ip: str | None = None
        self.last_isp: str | None = None
        self.last_asn: str | None = None
        self.last_connection_type: str | None = None
        
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
        except Exception as e:  # noqa: BLE001  # a bad state file must not stop the run; error is logged
            print(f"Could not load previous state: {e}")
    
    def _save_state(self):
        """Save current ISP state to file for persistence."""
        try:
            state = {
                "ip": self.last_ip,
                "isp": self.last_isp,
                "asn": self.last_asn,
                "connection_type": self.last_connection_type,
                "updated_at": datetime.now(timezone.utc).isoformat()
            }
            with open(self.STATE_FILE, 'w') as f:
                json.dump(state, f)
        except Exception as e:  # noqa: BLE001  # state is best-effort; error is logged
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
            "connection_type": None  # Will be inferred
        }
        
        # Try ipinfo.io first (no API key needed for basic info)
        try:
            response = requests.get("https://ipinfo.io/json", timeout=10)
            if response.status_code == 200:
                data = response.json()
                ip_info["ip"] = data.get("ip")
                ip_info["org"] = data.get("org") or ""  # Contains ASN and org name
                ip_info["city"] = data.get("city")
                ip_info["region"] = data.get("region")
                ip_info["country"] = data.get("country")
                
                # Parse ASN from org field (format: "AS12345 Company Name")
                org = data.get("org") or ""
                if org.startswith("AS"):
                    parts = org.split(" ", 1)
                    ip_info["asn"] = parts[0]
                    ip_info["isp"] = parts[1] if len(parts) > 1 else org
                else:
                    ip_info["isp"] = org
        except Exception as e:  # noqa: BLE001  # keep the daemon alive and fall through to ip-api; error is logged
            print(f"Error fetching from ipinfo.io: {e}")
        
        # Fallback to ip-api.com if ipinfo.io failed
        if not ip_info["ip"]:
            try:
                response = requests.get("http://ip-api.com/json", timeout=10)  # nosemgrep: python.lang.security.audit.insecure-transport.requests.request-with-http.request-with-http  # ip-api.com free tier is http-only (https returns 403), public geo lookup, no credentials sent
                if response.status_code == 200:
                    data = response.json()
                    ip_info["ip"] = data.get("query")
                    ip_info["isp"] = data.get("isp")
                    ip_info["asn"] = data["as"].split(" ")[0] if data.get("as") else None
                    ip_info["org"] = data.get("org")
                    ip_info["city"] = data.get("city")
                    ip_info["region"] = data.get("regionName")
                    ip_info["country"] = data.get("countryCode")
            except Exception as e:  # noqa: BLE001  # keep the daemon alive; error is logged
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
        
        # A failed lookup carries no information. Keep the saved baseline and
        # report no change, instead of overwriting it with None and raising a
        # false change event on the next good lookup.
        if current_ip is None:
            print("IP lookup failed; keeping the previous ISP state")
            return change_info

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
        # Parse URL to get host and port
        from urllib.parse import urlparse

        from influxdb import InfluxDBClient as InfluxDB1Client
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
        except Exception as e:  # noqa: BLE001  # the database usually already exists; error is logged
            print(f"Could not create database {database!r}: {e}")
        
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


def _fmt(value) -> str:
    """Format a number for a log line; a missing value prints as n/a."""
    return f"{value:.2f}" if isinstance(value, (int, float)) else "n/a"


class SpeedtestRunner:
    """Runs speedtests and logs results to InfluxDB."""
    
    def __init__(self, interval: int | None = None):
        self.writer = create_influxdb_writer()
        self.isp_tracker = ISPTracker()
        self.speedtest_accepted_license = False
        self.interval = interval  # seconds between cycles in daemon mode; None for a single run
    
    def accept_speedtest_license(self):
        """Accept the Ookla speedtest license on first run."""
        if not self.speedtest_accepted_license:
            try:
                # Accept the license by running with --accept-license
                subprocess.run(
                    ["speedtest", "--accept-license", "--accept-gdpr"],  # noqa: S607  # resolved on PATH; setup.sh installs it via apt, dnf or pacman, so the location varies
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
                self.speedtest_accepted_license = True
            except Exception as e:  # noqa: BLE001  # keep the daemon alive; error is logged
                print(f"Error accepting speedtest license: {e}")
    
    def run_speedtest(self) -> dict | None:
        """
        Run speedtest using Ookla's official CLI.
        Returns parsed results or None if failed.
        """
        self.accept_speedtest_license()
        
        try:
            print(f"[{datetime.now().isoformat()}] Starting speedtest...")  # noqa: DTZ005  # local wall-clock for log line
            result = subprocess.run(
                ["speedtest", "--format=json", "--accept-license", "--accept-gdpr"],  # noqa: S607  # resolved on PATH; setup.sh installs it via apt, dnf or pacman, so the location varies
                capture_output=True,
                text=True,
                timeout=120,  # 2 minute timeout
                check=False,  # returncode is inspected below
            )
            
            if result.returncode != 0:
                print(f"Speedtest failed: {result.stderr}")
                return None
            
            data = json.loads(result.stdout)
            if not isinstance(data, dict):
                print("Speedtest output was not a JSON object")
                return None

            def section(parent: dict, key: str) -> dict:
                """A nested object, or {} when the key is missing or null."""
                value = parent.get(key)
                return value if isinstance(value, dict) else {}

            ping = section(data, "ping")
            download = section(data, "download")
            upload = section(data, "upload")
            server = section(data, "server")
            res = section(data, "result")
            interface = section(data, "interface")
            dl_latency = section(download, "latency")
            ul_latency = section(upload, "latency")

            # Parse results
            speedtest_result = {
                "timestamp": data.get("timestamp"),
                "ping_jitter": ping.get("jitter"),
                "ping_latency": ping.get("latency"),
                "ping_low": ping.get("low"),
                "ping_high": ping.get("high"),
                "download_bandwidth": download.get("bandwidth"),  # bytes/sec
                "download_bytes": download.get("bytes"),
                "download_elapsed": download.get("elapsed"),
                "download_latency_iqm": dl_latency.get("iqm"),
                "download_latency_low": dl_latency.get("low"),
                "download_latency_high": dl_latency.get("high"),
                "upload_bandwidth": upload.get("bandwidth"),  # bytes/sec
                "upload_bytes": upload.get("bytes"),
                "upload_elapsed": upload.get("elapsed"),
                "upload_latency_iqm": ul_latency.get("iqm"),
                "upload_latency_low": ul_latency.get("low"),
                "upload_latency_high": ul_latency.get("high"),
                "packet_loss": data.get("packetLoss"),
                "server_id": server.get("id"),
                "server_name": server.get("name"),
                "server_location": server.get("location"),
                "server_country": server.get("country"),
                "server_host": server.get("host"),
                "result_id": res.get("id"),
                "result_url": res.get("url"),
                "isp": data.get("isp"),
                "external_ip": interface.get("externalIp"),
                "internal_ip": interface.get("internalIp"),
            }

            # Convert bandwidth from bytes/sec to Mbps for readability
            if speedtest_result["download_bandwidth"] is not None:
                speedtest_result["download_mbps"] = (speedtest_result["download_bandwidth"] * 8) / 1_000_000
            if speedtest_result["upload_bandwidth"] is not None:
                speedtest_result["upload_mbps"] = (speedtest_result["upload_bandwidth"] * 8) / 1_000_000
            
            print(f"  Download: {_fmt(speedtest_result.get('download_mbps'))} Mbps")
            print(f"  Upload: {_fmt(speedtest_result.get('upload_mbps'))} Mbps")
            print(f"  Ping: {_fmt(speedtest_result.get('ping_latency'))} ms")
            print(f"  Jitter: {_fmt(speedtest_result.get('ping_jitter'))} ms")
            
            return speedtest_result
            
        except subprocess.TimeoutExpired:
            print("Speedtest timed out")
            return None
        except json.JSONDecodeError as e:
            print(f"Failed to parse speedtest output: {e}")
            return None
        except Exception as e:  # noqa: BLE001  # keep the daemon alive; error is logged
            print(f"Error running speedtest: {e}")
            return None
    
    def write_speedtest_result(self, result: dict, ip_info: dict, isp_change: dict):
        """Write speedtest results to InfluxDB."""
        try:
            # Main speedtest metrics
            tags = {
                "server_name": result.get("server_name") or "unknown",
                "server_location": result.get("server_location") or "unknown",
                "server_country": result.get("server_country") or "unknown",
                "isp": ip_info.get("isp") or result.get("isp") or "unknown",
                "asn": ip_info.get("asn") or "unknown",
                "connection_type": ip_info.get("connection_type") or "unknown",
                "external_ip": ip_info.get("ip") or result.get("external_ip") or "unknown",
            }
            # A value the speedtest did not report stays None, and the writers
            # drop None, so a missing measurement is a gap rather than a fake 0.
            field_names = (
                "download_mbps", "upload_mbps", "download_bandwidth", "upload_bandwidth",
                "ping_latency", "ping_jitter", "ping_low", "ping_high",
                "download_latency_iqm", "upload_latency_iqm", "packet_loss", "result_url",
            )
            fields = {name: result.get(name) for name in field_names}

            self.writer.write_point("speedtest", tags, fields)
            
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
                    "current_ip": ip_info.get("ip", ""),
                    "event": 1,  # Marker for annotations
                }
                
                self.writer.write_point("isp_change", change_tags, change_fields)
                print(f"  ⚠️  ISP CHANGE DETECTED: {isp_change.get('previous_isp')} -> {ip_info.get('isp')}")
            
            print("  Results written to InfluxDB")
            
        except Exception as e:  # noqa: BLE001  # keep the daemon alive; error is logged
            print(f"Error writing to InfluxDB: {e}")
    
    def run_test_cycle(self):
        """Run a complete test cycle: get IP info, run speedtest, log results."""
        print(f"\n{'='*60}")
        print(f"[{datetime.now().isoformat()}] Starting test cycle")  # noqa: DTZ005  # local wall-clock for log line
        print(f"{'='*60}")
        
        # Get current IP/ISP information
        ip_info = self.isp_tracker.get_ip_info()
        print(f"  External IP: {ip_info.get('ip')}")
        print(f"  ISP: {ip_info.get('isp')}")
        print(f"  ASN: {ip_info.get('asn')}")
        print(f"  Connection Type: {ip_info.get('connection_type')}")
        
        # Check for ISP change
        isp_change = self.isp_tracker.check_for_change(ip_info)
        
        # Run speedtest
        result = self.run_speedtest()
        
        if result:
            self.write_speedtest_result(result, ip_info, isp_change)
        else:
            # Write at least the IP info if speedtest failed
            try:
                tags = {
                    "isp": ip_info.get("isp", "unknown"),
                    "connection_type": ip_info.get("connection_type", "unknown"),
                }
                fields = {"error": 1}
                self.writer.write_point("speedtest_error", tags, fields)
            except Exception as e:  # noqa: BLE001  # keep the daemon alive; error is logged
                print(f"Error logging speedtest failure: {e}")
        
        print(f"[{datetime.now().isoformat()}] Test cycle complete")  # noqa: DTZ005  # local wall-clock for log line
        if self.interval:
            print(f"Next test in {self.interval} seconds ({self.interval/60:.1f} minutes)")


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
        except requests.RequestException:
            pass  # not up yet; the retry line below reports progress
        
        print(f"  Waiting... ({i+1}/{max_retries})")
        time.sleep(retry_interval)
    
    print("Failed to connect to InfluxDB")
    return False


def main(argv: list[str] | None = None):
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="NetPulse - Network Speed & ISP Monitor",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                  # Run as daemon with scheduled tests
  %(prog)s --once           # Run single test and exit (for systemd timer)
  %(prog)s --interval 900   # Run every 15 minutes (daemon mode)
        """
    )
    parser.add_argument(
        "--once", "-1",
        action="store_true",
        help="Run a single speedtest and exit (for use with systemd timers)"
    )
    parser.add_argument(
        "--interval", "-i",
        type=int,
        default=None,
        help=f"Override test interval in seconds (default: SPEEDTEST_INTERVAL or {DEFAULT_SPEEDTEST_INTERVAL})"
    )
    args = parser.parse_args(argv)
    
    # A single run never schedules anything, so it does not need the interval.
    interval = None
    if not args.once:
        try:
            interval = args.interval if args.interval else get_speedtest_interval()
        except ConfigError as e:
            print(f"Configuration error: {e}", file=sys.stderr)
            sys.exit(2)
    
    print("="*60)
    print("NetPulse - Network Speed & ISP Monitor")
    print("="*60)
    print(f"InfluxDB URL: {INFLUXDB_URL}")
    print(f"InfluxDB Org: {INFLUXDB_ORG}")
    print(f"InfluxDB Bucket: {INFLUXDB_BUCKET}")
    if args.once:
        print("Mode: Single run (--once)")
    else:
        print(f"Test Interval: {interval} seconds ({interval/60:.1f} minutes)")
    print("="*60)
    
    # Wait for InfluxDB
    if not wait_for_influxdb():
        print("InfluxDB never became ready; exiting with an error", file=sys.stderr)
        sys.exit(1)
    
    # Create runner
    runner = SpeedtestRunner(interval=interval)
    
    # Run initial test
    runner.run_test_cycle()
    
    # If --once flag, exit after single run
    if args.once:
        print("Single run completed. Exiting.")
        return
    
    # Schedule periodic tests (daemon mode)
    schedule.every(interval).seconds.do(runner.run_test_cycle)
    
    # Run scheduler
    while True:
        schedule.run_pending()
        time.sleep(1)


if __name__ == "__main__":
    main()
