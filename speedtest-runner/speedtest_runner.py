#!/usr/bin/env python3
"""
NetPulse - Network Speed & ISP Monitor

Runs periodic speedtests and logs results to InfluxDB.
Detects ISP changes by tracking external IP, ASN, and ISP name.
"""

import json
import os
import subprocess
import time
from datetime import datetime
from typing import Optional

import requests
import schedule
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS


# Configuration from environment
INFLUXDB_URL = os.getenv("INFLUXDB_URL", "http://localhost:8086")
INFLUXDB_TOKEN = os.getenv("INFLUXDB_TOKEN", "netpulse-token-change-me")
INFLUXDB_ORG = os.getenv("INFLUXDB_ORG", "netpulse")
INFLUXDB_BUCKET = os.getenv("INFLUXDB_BUCKET", "netpulse")
SPEEDTEST_INTERVAL = int(os.getenv("SPEEDTEST_INTERVAL", "1800"))  # 30 minutes default


class ISPTracker:
    """Tracks ISP information and detects changes with persistent state."""
    
    # State file location - works for both local and Docker
    STATE_FILE = os.getenv("NETPULSE_STATE_FILE", "/tmp/netpulse_state.json")
    
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
            "connection_type": None  # Will be inferred
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


class SpeedtestRunner:
    """Runs speedtests and logs results to InfluxDB."""
    
    def __init__(self):
        self.client = InfluxDBClient(
            url=INFLUXDB_URL,
            token=INFLUXDB_TOKEN,
            org=INFLUXDB_ORG
        )
        self.write_api = self.client.write_api(write_options=SYNCHRONOUS)
        self.isp_tracker = ISPTracker()
        self.speedtest_accepted_license = False
    
    def accept_speedtest_license(self):
        """Accept the Ookla speedtest license on first run."""
        if not self.speedtest_accepted_license:
            try:
                # Accept the license by running with --accept-license
                subprocess.run(
                    ["speedtest", "--accept-license", "--accept-gdpr"],
                    capture_output=True,
                    text=True,
                    timeout=30
                )
                self.speedtest_accepted_license = True
            except Exception as e:
                print(f"Error accepting speedtest license: {e}")
    
    def run_speedtest(self) -> Optional[dict]:
        """
        Run speedtest using Ookla's official CLI.
        Returns parsed results or None if failed.
        """
        self.accept_speedtest_license()
        
        try:
            print(f"[{datetime.now().isoformat()}] Starting speedtest...")
            result = subprocess.run(
                ["speedtest", "--format=json", "--accept-license", "--accept-gdpr"],
                capture_output=True,
                text=True,
                timeout=120  # 2 minute timeout
            )
            
            if result.returncode != 0:
                print(f"Speedtest failed: {result.stderr}")
                return None
            
            data = json.loads(result.stdout)
            
            # Parse results
            speedtest_result = {
                "timestamp": data.get("timestamp"),
                "ping_jitter": data.get("ping", {}).get("jitter"),
                "ping_latency": data.get("ping", {}).get("latency"),
                "ping_low": data.get("ping", {}).get("low"),
                "ping_high": data.get("ping", {}).get("high"),
                "download_bandwidth": data.get("download", {}).get("bandwidth"),  # bytes/sec
                "download_bytes": data.get("download", {}).get("bytes"),
                "download_elapsed": data.get("download", {}).get("elapsed"),
                "download_latency_iqm": data.get("download", {}).get("latency", {}).get("iqm"),
                "download_latency_low": data.get("download", {}).get("latency", {}).get("low"),
                "download_latency_high": data.get("download", {}).get("latency", {}).get("high"),
                "upload_bandwidth": data.get("upload", {}).get("bandwidth"),  # bytes/sec
                "upload_bytes": data.get("upload", {}).get("bytes"),
                "upload_elapsed": data.get("upload", {}).get("elapsed"),
                "upload_latency_iqm": data.get("upload", {}).get("latency", {}).get("iqm"),
                "upload_latency_low": data.get("upload", {}).get("latency", {}).get("low"),
                "upload_latency_high": data.get("upload", {}).get("latency", {}).get("high"),
                "packet_loss": data.get("packetLoss"),
                "server_id": data.get("server", {}).get("id"),
                "server_name": data.get("server", {}).get("name"),
                "server_location": data.get("server", {}).get("location"),
                "server_country": data.get("server", {}).get("country"),
                "server_host": data.get("server", {}).get("host"),
                "result_id": data.get("result", {}).get("id"),
                "result_url": data.get("result", {}).get("url"),
                "isp": data.get("isp"),
                "external_ip": data.get("interface", {}).get("externalIp"),
                "internal_ip": data.get("interface", {}).get("internalIp"),
            }
            
            # Convert bandwidth from bytes/sec to Mbps for readability
            if speedtest_result["download_bandwidth"]:
                speedtest_result["download_mbps"] = (speedtest_result["download_bandwidth"] * 8) / 1_000_000
            if speedtest_result["upload_bandwidth"]:
                speedtest_result["upload_mbps"] = (speedtest_result["upload_bandwidth"] * 8) / 1_000_000
            
            print(f"  Download: {speedtest_result.get('download_mbps', 0):.2f} Mbps")
            print(f"  Upload: {speedtest_result.get('upload_mbps', 0):.2f} Mbps")
            print(f"  Ping: {speedtest_result.get('ping_latency', 0):.2f} ms")
            print(f"  Jitter: {speedtest_result.get('ping_jitter', 0):.2f} ms")
            
            return speedtest_result
            
        except subprocess.TimeoutExpired:
            print("Speedtest timed out")
            return None
        except json.JSONDecodeError as e:
            print(f"Failed to parse speedtest output: {e}")
            return None
        except Exception as e:
            print(f"Error running speedtest: {e}")
            return None
    
    def write_speedtest_result(self, result: dict, ip_info: dict, isp_change: dict):
        """Write speedtest results to InfluxDB."""
        try:
            # Main speedtest metrics
            point = Point("speedtest") \
                .tag("server_name", result.get("server_name", "unknown")) \
                .tag("server_location", result.get("server_location", "unknown")) \
                .tag("server_country", result.get("server_country", "unknown")) \
                .tag("isp", ip_info.get("isp", result.get("isp", "unknown"))) \
                .tag("asn", ip_info.get("asn", "unknown")) \
                .tag("connection_type", ip_info.get("connection_type", "unknown")) \
                .tag("external_ip", ip_info.get("ip", result.get("external_ip", "unknown"))) \
                .field("download_mbps", result.get("download_mbps", 0.0)) \
                .field("upload_mbps", result.get("upload_mbps", 0.0)) \
                .field("download_bandwidth", result.get("download_bandwidth", 0)) \
                .field("upload_bandwidth", result.get("upload_bandwidth", 0)) \
                .field("ping_latency", result.get("ping_latency", 0.0)) \
                .field("ping_jitter", result.get("ping_jitter", 0.0)) \
                .field("ping_low", result.get("ping_low", 0.0)) \
                .field("ping_high", result.get("ping_high", 0.0)) \
                .field("download_latency_iqm", result.get("download_latency_iqm", 0.0)) \
                .field("upload_latency_iqm", result.get("upload_latency_iqm", 0.0)) \
                .field("packet_loss", result.get("packet_loss") if result.get("packet_loss") is not None else 0.0) \
                .field("result_url", result.get("result_url", ""))
            
            self.write_api.write(bucket=INFLUXDB_BUCKET, record=point)
            
            # Write ISP change event if detected
            if isp_change.get("changed"):
                change_point = Point("isp_change") \
                    .tag("previous_isp", isp_change.get("previous_isp", "unknown")) \
                    .tag("current_isp", ip_info.get("isp", "unknown")) \
                    .tag("previous_asn", isp_change.get("previous_asn", "unknown")) \
                    .tag("current_asn", ip_info.get("asn", "unknown")) \
                    .tag("previous_connection_type", isp_change.get("previous_connection_type", "unknown")) \
                    .tag("current_connection_type", ip_info.get("connection_type", "unknown")) \
                    .field("ip_changed", isp_change.get("ip_changed", False)) \
                    .field("isp_changed", isp_change.get("isp_changed", False)) \
                    .field("asn_changed", isp_change.get("asn_changed", False)) \
                    .field("previous_ip", isp_change.get("previous_ip", "")) \
                    .field("current_ip", ip_info.get("ip", "")) \
                    .field("event", 1)  # Marker for annotations
                
                self.write_api.write(bucket=INFLUXDB_BUCKET, record=change_point)
                print(f"  ⚠️  ISP CHANGE DETECTED: {isp_change.get('previous_isp')} -> {ip_info.get('isp')}")
            
            print(f"  Results written to InfluxDB")
            
        except Exception as e:
            print(f"Error writing to InfluxDB: {e}")
    
    def run_test_cycle(self):
        """Run a complete test cycle: get IP info, run speedtest, log results."""
        print(f"\n{'='*60}")
        print(f"[{datetime.now().isoformat()}] Starting test cycle")
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
                point = Point("speedtest_error") \
                    .tag("isp", ip_info.get("isp", "unknown")) \
                    .tag("connection_type", ip_info.get("connection_type", "unknown")) \
                    .field("error", 1)
                self.write_api.write(bucket=INFLUXDB_BUCKET, record=point)
            except Exception as e:
                print(f"Error logging speedtest failure: {e}")
        
        print(f"[{datetime.now().isoformat()}] Test cycle complete")
        print(f"Next test in {SPEEDTEST_INTERVAL} seconds ({SPEEDTEST_INTERVAL/60:.1f} minutes)")


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


def main():
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
        help=f"Override test interval in seconds (default: {SPEEDTEST_INTERVAL})"
    )
    args = parser.parse_args()
    
    interval = args.interval if args.interval else SPEEDTEST_INTERVAL
    
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
        return
    
    # Create runner
    runner = SpeedtestRunner()
    
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
