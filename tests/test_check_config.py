"""Tests for scripts/check_config.py, including cases proving it bites."""

import json
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
        self.edit("grafana/provisioning/datasources/influxdb.yml", "defaultBucket: $INFLUXDB_BUCKET", "defaultBucket: other")
        self.assertTrue(check_config.check(self.tmp))

    def test_datasource_reference_to_unset_variable_fails(self):
        self.edit("grafana/provisioning/datasources/influxdb.yml", "organization: $INFLUXDB_ORG", "organization: $NOT_SET_ORG")
        self.assertTrue(any("datasource ORG" in e and "not set" in e for e in check_config.check(self.tmp)))

    def test_datasource_reference_follows_compose_default(self):
        # The braced spelling resolves the same way as the unbraced one.
        self.edit("grafana/provisioning/datasources/influxdb.yml", "organization: $INFLUXDB_ORG", "organization: ${INFLUXDB_ORG}")
        self.assertEqual(check_config.check(self.tmp), [])

    def test_broken_dashboard_json_fails(self):
        (self.tmp / "grafana/dashboards/speedtest.json").write_text("{not json")
        self.assertTrue(any("invalid JSON" in e for e in check_config.check(self.tmp)))

    # --- compose: init lines and runner lines are each parsed explicitly ---
    INIT_ORG = "DOCKER_INFLUXDB_INIT_ORG=${INFLUXDB_ORG:-netpulse}"
    INIT_BUCKET = "DOCKER_INFLUXDB_INIT_BUCKET=${INFLUXDB_BUCKET:-netpulse}"
    RUN_ORG = "      - INFLUXDB_ORG=${INFLUXDB_ORG:-netpulse}"
    RUN_BUCKET = "      - INFLUXDB_BUCKET=${INFLUXDB_BUCKET:-netpulse}"

    def errors(self):
        return check_config.check(self.tmp)

    def test_init_bucket_literal_speedtest_fails(self):
        self.edit("docker-compose.yml", self.INIT_BUCKET, "DOCKER_INFLUXDB_INIT_BUCKET=speedtest")
        self.assertTrue(any("INIT_BUCKET" in e and "speedtest" in e for e in self.errors()))

    def test_init_org_literal_speedtest_fails(self):
        self.edit("docker-compose.yml", self.INIT_ORG, "DOCKER_INFLUXDB_INIT_ORG=speedtest")
        self.assertTrue(any("INIT_ORG" in e and "speedtest" in e for e in self.errors()))

    def test_init_line_missing_fails(self):
        self.edit("docker-compose.yml", "      - " + self.INIT_BUCKET + "\n", "")
        self.assertTrue(any("INIT_BUCKET" in e and "missing" in e for e in self.errors()))

    def test_runner_line_missing_fails(self):
        self.edit("docker-compose.yml", self.RUN_ORG + "\n", "")
        self.assertTrue(any("runner" in e and "ORG" in e and "missing" in e for e in self.errors()))

    def test_no_dash_default_form_speedtest_fails(self):
        self.edit("docker-compose.yml", self.INIT_BUCKET, "DOCKER_INFLUXDB_INIT_BUCKET=${INFLUXDB_BUCKET-speedtest}")
        self.assertTrue(any("INIT_BUCKET" in e and "speedtest" in e for e in self.errors()))

    def test_runner_bucket_speedtest_fails(self):
        self.edit("docker-compose.yml", self.RUN_BUCKET, "      - INFLUXDB_BUCKET=${INFLUXDB_BUCKET:-speedtest}")
        self.assertTrue(self.errors())

    def test_no_default_at_all_fails(self):
        self.edit("docker-compose.yml", self.INIT_ORG, "DOCKER_INFLUXDB_INIT_ORG=${INFLUXDB_ORG}")
        self.assertTrue(any("INIT_ORG" in e for e in self.errors()))

    def test_accepted_forms_pass(self):
        for new in ("${INFLUXDB_BUCKET:-netpulse}", "${INFLUXDB_BUCKET-netpulse}", "netpulse"):
            with self.subTest(form=new):
                shutil.copy(ROOT / "docker-compose.yml", self.tmp / "docker-compose.yml")
                self.edit("docker-compose.yml", self.INIT_BUCKET, "DOCKER_INFLUXDB_INIT_BUCKET=" + new)
                self.assertEqual(self.errors(), [])

    # --- dashboards ---
    def test_non_literal_bucket_fails_naming_dashboard_and_panel(self):
        d = self.tmp / "grafana/dashboards/speedtest.json"
        d.write_text(json.dumps({"panels": [{"targets": [{"query": "from(bucket: myb) |> range(start: -1h)"}]}]}))
        errs = self.errors()
        self.assertTrue(any("not a literal" in e and "speedtest.json" in e and "panels[0]" in e for e in errs), errs)

    def test_nested_dashboard_is_scanned(self):
        sub = self.tmp / "grafana/dashboards/team/sub"
        sub.mkdir(parents=True)
        (sub / "x.json").write_text(json.dumps({"panels": [{"targets": [{"query": 'from(bucket: "speedtest")'}]}]}))
        self.assertTrue(any("x.json" in e and "speedtest" in e for e in self.errors()))

    def test_nested_broken_json_fails(self):
        sub = self.tmp / "grafana/dashboards/team"
        sub.mkdir(parents=True)
        (sub / "bad.json").write_text("{nope")
        self.assertTrue(any("bad.json" in e and "invalid JSON" in e for e in self.errors()))


if __name__ == "__main__":
    unittest.main()
