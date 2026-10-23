#!/usr/bin/env python3
"""Fail if the InfluxDB org and bucket names disagree across the repo.

Checks (standard library only):
  * every dashboard JSON under grafana/dashboards parses;
  * docker-compose.yml org/bucket defaults all agree and match the Grafana
    datasource and the runner defaults in speedtest_runner.py;
  * every from(bucket: "...") in the dashboards names that same bucket.
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXPECTED = "netpulse"

COMPOSE_RE = re.compile(r"\$\{INFLUXDB_(ORG|BUCKET):-([^}]*)\}")
DATASOURCE_RE = {
    "ORG": re.compile(r"^\s*organization:\s*(\S+)\s*$", re.M),
    "BUCKET": re.compile(r"^\s*defaultBucket:\s*(\S+)\s*$", re.M),
}
RUNNER_RE = {
    "ORG": re.compile(r'getenv\("INFLUXDB_ORG",\s*"([^"]*)"\)'),
    "BUCKET": re.compile(r'getenv\("INFLUXDB_BUCKET",\s*"([^"]*)"\)'),
}
FLUX_BUCKET_RE = re.compile(r'from\(bucket:\s*"([^"]*)"')


def walk_queries(node):
    """Yield every string stored under a 'query' key, at any depth."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "query" and isinstance(value, str):
                yield value
            else:
                yield from walk_queries(value)
    elif isinstance(node, list):
        for item in node:
            yield from walk_queries(item)


def check(root: Path) -> list[str]:
    errors = []

    def found(label, values):
        if not values:
            errors.append(f"{label}: nothing found (check is blind)")
        for v in values:
            if v != EXPECTED:
                errors.append(f"{label}: {v!r}, expected {EXPECTED!r}")

    compose = (root / "docker-compose.yml").read_text()
    for kind in ("ORG", "BUCKET"):
        found(
            f"docker-compose.yml INFLUXDB_{kind}",
            [m.group(2) for m in COMPOSE_RE.finditer(compose) if m.group(1) == kind],
        )
    datasource = (root / "grafana/provisioning/datasources/influxdb.yml").read_text()
    runner = (root / "speedtest-runner/speedtest_runner.py").read_text()
    for kind in ("ORG", "BUCKET"):
        found(f"datasource {kind}", DATASOURCE_RE[kind].findall(datasource))
        found(f"runner default {kind}", RUNNER_RE[kind].findall(runner))

    dashboards = sorted((root / "grafana/dashboards").glob("*.json"))
    if not dashboards:
        errors.append("no dashboards found (check is blind)")
    for path in dashboards:
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            errors.append(f"{path.name}: invalid JSON: {exc}")
            continue
        buckets = [b for q in walk_queries(data) for b in FLUX_BUCKET_RE.findall(q)]
        found(f"{path.name} from(bucket:)", buckets)
    return errors


def main() -> int:
    errors = check(ROOT)
    for e in errors:
        print(f"FAIL {e}", file=sys.stderr)
    if not errors:
        print("config check OK: org and bucket are consistently", EXPECTED)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
