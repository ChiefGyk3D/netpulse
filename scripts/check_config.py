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

# One environment entry: "- NAME=value", "NAME=value" or "NAME: value"; commented
# lines never match. The value may be a literal, ${VAR:-default} or ${VAR-default}.
ENV_LINE = r"^\s*(?:-\s*)?{name}\s*[=:]\s*[\"']?(?P<value>[^\"'\s#]*)[\"']?\s*(?:#.*)?$"
EXPAND_RE = re.compile(r"^\$\{(?P<var>[A-Za-z_][A-Za-z0-9_]*)(?P<op>:-|-)(?P<default>[^}]*)\}$")
ENV_REF_RE = re.compile(r"^\$\{?(?P<var>[A-Za-z_][A-Za-z0-9_]*)\}?$")
DATASOURCE_RE = {
    "ORG": re.compile(r"^\s*organization:\s*(\S+)\s*$", re.MULTILINE),
    "BUCKET": re.compile(r"^\s*defaultBucket:\s*(\S+)\s*$", re.MULTILINE),
}
RUNNER_RE = {
    "ORG": re.compile(r'getenv\("INFLUXDB_ORG",\s*"([^"]*)"\)'),
    "BUCKET": re.compile(r'getenv\("INFLUXDB_BUCKET",\s*"([^"]*)"\)'),
}
# group 1 is the literal bucket name; None when the bucket is not a string literal.
FLUX_ANY_BUCKET_RE = re.compile(r'from\(\s*bucket:\s*(?:"([^"]*)"|)')


def walk_queries(node, path="$"):
    """Yield (path, query) for every string stored under a 'query' key."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "query" and isinstance(value, str):
                yield f"{path}.{key}", value
            else:
                yield from walk_queries(value, f"{path}.{key}")
    elif isinstance(node, list):
        for i, item in enumerate(node):
            yield from walk_queries(item, f"{path}[{i}]")


def effective_values(compose: str, name: str) -> list[str]:
    """Effective default/literal of each uncommented `name` entry, or an error marker."""
    out = []
    pattern = re.compile(ENV_LINE.format(name=re.escape(name)), re.MULTILINE)
    for m in pattern.finditer(compose):
        value = m.group("value")
        if value.startswith("${"):
            e = EXPAND_RE.match(value)
            if not e:
                out.append(f"<no default in {value}>")
                continue
            value = e.group("default")
        out.append(value)
    return out


def datasource_values(compose: str, values: list[str]) -> list[str]:
    """Resolve $VAR / ${VAR} datasource references to the compose defaults of VAR.

    Grafana expands these from its own environment, which docker-compose.yml
    sets from the same variable the runner uses; a literal passes through.
    """
    out = []
    for value in values:
        m = ENV_REF_RE.match(value)
        if not m:
            out.append(value)
            continue
        defaults = effective_values(compose, m.group("var"))
        out.extend(defaults or [f"<{value}: not set in docker-compose.yml>"])
    return out


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
        for label, name in (
            (f"docker-compose.yml DOCKER_INFLUXDB_INIT_{kind}", f"DOCKER_INFLUXDB_INIT_{kind}"),
            (f"docker-compose.yml runner INFLUXDB_{kind}", f"INFLUXDB_{kind}"),
        ):
            values = effective_values(compose, name)
            if not values:
                errors.append(f"{label}: line missing from docker-compose.yml")
            found(label, values)
    datasource = (root / "grafana/provisioning/datasources/influxdb.yml").read_text()
    runner = (root / "speedtest-runner/speedtest_runner.py").read_text()
    for kind in ("ORG", "BUCKET"):
        found(f"datasource {kind}", datasource_values(compose, DATASOURCE_RE[kind].findall(datasource)))
        found(f"runner default {kind}", RUNNER_RE[kind].findall(runner))

    dashboards = sorted((root / "grafana/dashboards").rglob("*.json"))
    if not dashboards:
        errors.append("no dashboards found (check is blind)")
    for path in dashboards:
        name = path.relative_to(root / "grafana/dashboards").as_posix()
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            errors.append(f"{name}: invalid JSON: {exc}")
            continue
        buckets = []
        for where, query in walk_queries(data):
            for m in FLUX_ANY_BUCKET_RE.finditer(query):
                if m.group(1) is None:
                    errors.append(f"{name} {where}: bucket is not a literal (from(bucket:) must name a string)")
                else:
                    buckets.append(m.group(1))
        found(f"{name} from(bucket:)", buckets)
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
