"""
Tests for NetPulse - validates scheduling and privacy features added in the
Cloudflare speed test enhancement.
"""
import os
import json
import tempfile
import unittest
from unittest.mock import MagicMock, patch


class TestISPTrackerOokla(unittest.TestCase):
    """Tests for ISP tracker in the Ookla runner."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.state_file = os.path.join(self.tmpdir, "state.json")
        with patch.dict(os.environ, {"NETPULSE_STATE_FILE": self.state_file}):
            import importlib
            import sys
            # Reload to pick up the patched env var
            if "speedtest_runner" in sys.modules:
                del sys.modules["speedtest_runner"]
            sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "speedtest-runner"))
            import speedtest_runner
            self.mod = speedtest_runner
            self.tracker = speedtest_runner.ISPTracker()

    def test_initial_state_is_empty(self):
        self.assertIsNone(self.tracker.last_ip)
        self.assertIsNone(self.tracker.last_isp)

    def test_state_persistence(self):
        self.tracker.last_ip = "1.2.3.4"
        self.tracker.last_isp = "Test ISP"
        self.tracker.last_asn = "AS12345"
        self.tracker.last_connection_type = "fiber"
        self.tracker._save_state()

        # Load into a new tracker
        tracker2 = self.mod.ISPTracker()
        self.assertEqual(tracker2.last_ip, "1.2.3.4")
        self.assertEqual(tracker2.last_isp, "Test ISP")
        self.assertEqual(tracker2.last_asn, "AS12345")
        self.assertEqual(tracker2.last_connection_type, "fiber")

    def test_change_detection_first_run(self):
        """First run with no previous state should not flag a change."""
        info = {"ip": "1.2.3.4", "isp": "ISP A", "asn": "AS1", "connection_type": "cable"}
        change = self.tracker.check_for_change(info)
        self.assertFalse(change["changed"])

    def test_change_detection_ip_change(self):
        """IP change after prior state should be flagged."""
        info1 = {"ip": "1.2.3.4", "isp": "ISP A", "asn": "AS1", "connection_type": "cable"}
        self.tracker.check_for_change(info1)

        info2 = {"ip": "5.6.7.8", "isp": "ISP B", "asn": "AS2", "connection_type": "cellular"}
        change = self.tracker.check_for_change(info2)
        self.assertTrue(change["changed"])
        self.assertTrue(change["ip_changed"])
        self.assertTrue(change["isp_changed"])

    def test_no_change_same_values(self):
        """Same IP and ISP should not trigger a change."""
        info = {"ip": "1.2.3.4", "isp": "ISP A", "asn": "AS1", "connection_type": "cable"}
        self.tracker.check_for_change(info)
        change = self.tracker.check_for_change(info)
        self.assertFalse(change["changed"])


class TestSchedulerOokla(unittest.TestCase):
    """Tests for the scheduling logic in the Ookla runner."""

    def setUp(self):
        import sys
        if "speedtest_runner" not in sys.modules:
            sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "speedtest-runner"))
        import speedtest_runner
        self.mod = speedtest_runner

    def test_interval_scheduling(self):
        sched_type, val = self.mod.get_scheduler(1800, "")
        self.assertEqual(sched_type, "interval")
        self.assertEqual(val, 1800)

    def test_cron_scheduling(self):
        cron = "0 3,9,15,21 * * *"
        sched_type, val = self.mod.get_scheduler(1800, cron)
        self.assertEqual(sched_type, "cron")
        self.assertEqual(val, cron)

    def test_invalid_cron_falls_back_to_interval(self):
        sched_type, val = self.mod.get_scheduler(1800, "NOT_A_CRON")
        self.assertEqual(sched_type, "interval")
        self.assertEqual(val, 1800)


class TestSchedulerCloudflare(unittest.TestCase):
    """Tests for the scheduling logic in the Cloudflare runner."""

    def setUp(self):
        import sys
        cf_path = os.path.join(os.path.dirname(__file__), "..", "cloudflare-speedtest-runner")
        if cf_path not in sys.path:
            sys.path.insert(0, cf_path)
        if "cloudflare_speedtest_runner" in sys.modules:
            del sys.modules["cloudflare_speedtest_runner"]
        import cloudflare_speedtest_runner
        self.mod = cloudflare_speedtest_runner

    def test_interval_scheduling(self):
        sched_type, val = self.mod.get_scheduler(21600, "")
        self.assertEqual(sched_type, "interval")
        self.assertEqual(val, 21600)

    def test_cron_scheduling(self):
        cron = "0 3,9,15,21 * * *"
        sched_type, val = self.mod.get_scheduler(21600, cron)
        self.assertEqual(sched_type, "cron")
        self.assertEqual(val, cron)

    def test_invalid_cron_falls_back(self):
        sched_type, val = self.mod.get_scheduler(21600, "INVALID")
        self.assertEqual(sched_type, "interval")
        self.assertEqual(val, 21600)


class TestIPMaskingOokla(unittest.TestCase):
    """Tests that HIDE_EXTERNAL_IP masks the IP correctly in the Ookla runner."""

    def setUp(self):
        import sys
        if "speedtest_runner" not in sys.modules:
            sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "speedtest-runner"))
        import speedtest_runner
        self.mod = speedtest_runner

    def test_ip_masked_when_hide_enabled(self):
        """External IP should be 'hidden' when HIDE_EXTERNAL_IP is true."""
        runner = MagicMock()
        runner.isp_tracker = MagicMock()
        runner.writer = MagicMock()

        ip_info = {"ip": "1.2.3.4", "isp": "TestISP", "asn": "AS1", "connection_type": "fiber"}
        isp_change = {"changed": False}
        result = {
            "server_name": "srv", "server_location": "loc", "server_country": "US",
            "isp": "TestISP", "download_mbps": 100.0, "upload_mbps": 50.0,
            "download_bandwidth": 100, "upload_bandwidth": 50,
            "ping_latency": 10.0, "ping_jitter": 1.0, "ping_low": 9.0, "ping_high": 11.0,
            "download_latency_iqm": 10.0, "upload_latency_iqm": 11.0,
            "packet_loss": 0.0, "result_url": ""
        }

        with patch.object(self.mod, "HIDE_EXTERNAL_IP", True):
            # Call the real implementation
            orig_fn = self.mod.SpeedtestRunner.write_speedtest_result
            tags_captured = {}

            def capture_write(measurement, tags, fields):
                if measurement == "speedtest":
                    tags_captured.update(tags)

            runner.writer.write_point.side_effect = capture_write
            orig_fn(runner, result, ip_info, isp_change)
            self.assertEqual(tags_captured.get("external_ip"), "hidden")


class TestIPMaskingCloudflare(unittest.TestCase):
    """Tests that HIDE_EXTERNAL_IP masks the IP in the Cloudflare runner."""

    def setUp(self):
        import sys
        cf_path = os.path.join(os.path.dirname(__file__), "..", "cloudflare-speedtest-runner")
        if cf_path not in sys.path:
            sys.path.insert(0, cf_path)
        if "cloudflare_speedtest_runner" in sys.modules:
            del sys.modules["cloudflare_speedtest_runner"]
        import cloudflare_speedtest_runner
        self.mod = cloudflare_speedtest_runner

    def test_ip_masked_when_hide_enabled(self):
        runner = MagicMock()
        runner.writer = MagicMock()

        ip_info = {"ip": "1.2.3.4", "isp": "TestISP", "asn": "AS1", "connection_type": "fiber"}
        isp_change = {"changed": False}
        result = {
            "download_mbps": 200.0, "upload_mbps": 100.0,
            "latency_ms": 5.0, "jitter_ms": 1.0, "packet_loss": 0.0,
            "colo": "DFW", "colo_location": "US"
        }

        with patch.object(self.mod, "HIDE_EXTERNAL_IP", True):
            tags_captured = {}

            def capture_write(measurement, tags, fields):
                if measurement == "cloudflare_speedtest":
                    tags_captured.update(tags)

            runner.writer.write_point.side_effect = capture_write
            self.mod.CloudflareSpeedtestRunner.write_speedtest_result(runner, result, ip_info, isp_change)
            self.assertEqual(tags_captured.get("external_ip"), "hidden")


class TestCronNextRun(unittest.TestCase):
    """Tests that cron expressions produce the expected next-run times."""

    def test_clock_based_schedule(self):
        """0 3,9,15,21 * * * should produce 03:00, 09:00, 15:00, 21:00."""
        from croniter import croniter
        from datetime import datetime

        cron_expr = "0 3,9,15,21 * * *"
        base = datetime(2025, 1, 1, 2, 0, 0)
        cron = croniter(cron_expr, base)
        expected = [3, 9, 15, 21]
        for hour in expected:
            nxt = cron.get_next(datetime)
            self.assertEqual(nxt.hour, hour)
            self.assertEqual(nxt.minute, 0)

    def test_every_6h_schedule(self):
        """0 */6 * * * should produce 06:00, 12:00, 18:00, 00:00."""
        from croniter import croniter
        from datetime import datetime

        cron_expr = "0 */6 * * *"
        base = datetime(2025, 1, 1, 0, 0, 0)
        cron = croniter(cron_expr, base)
        expected_hours = [6, 12, 18, 0]
        for hour in expected_hours:
            nxt = cron.get_next(datetime)
            self.assertEqual(nxt.hour, hour)


if __name__ == "__main__":
    unittest.main()
