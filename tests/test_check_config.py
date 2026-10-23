"""Tests for scripts/check_config.py, including cases proving it bites."""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import check_config

FILES = [
    "docker-compose.yml",
    "grafana/provisioning/datasources/influxdb.yml",
    "speedtest-runner/speedtest_runner.py",
    "grafana/dashboards/speedtest.json",
]


class CheckConfig(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        for rel in FILES:
            (self.tmp / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(ROOT / rel, self.tmp / rel)

    def edit(self, rel, old, new):
        p = self.tmp / rel
        text = p.read_text()
        self.assertIn(old, text)
        p.write_text(text.replace(old, new))

    def test_repo_is_consistent(self):
        self.assertEqual(check_config.check(ROOT), [])

    def test_stale_compose_bucket_fails(self):
        self.edit("docker-compose.yml", "INFLUXDB_BUCKET:-netpulse", "INFLUXDB_BUCKET:-speedtest")
        self.assertTrue(check_config.check(self.tmp))

    def test_stale_dashboard_bucket_fails(self):
        self.edit("grafana/dashboards/speedtest.json", 'from(bucket: \\"netpulse\\")', 'from(bucket: \\"speedtest\\")')
        self.assertTrue(check_config.check(self.tmp))

    def test_datasource_mismatch_fails(self):
        self.edit("grafana/provisioning/datasources/influxdb.yml", "defaultBucket: netpulse", "defaultBucket: other")
        self.assertTrue(check_config.check(self.tmp))

    def test_broken_dashboard_json_fails(self):
        (self.tmp / "grafana/dashboards/speedtest.json").write_text("{not json")
        self.assertTrue(any("invalid JSON" in e for e in check_config.check(self.tmp)))


if __name__ == "__main__":
    unittest.main()
