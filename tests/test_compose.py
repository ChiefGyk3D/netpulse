"""Static checks on docker-compose.yml, the Grafana provisioning and Dependabot.

Nothing here starts a container: the compose file is parsed and the raw
interpolation strings are inspected. The checks collect loosely (every
service, every environment entry) and assert strictly, and each guard has a
negative case showing that it bites.
"""

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
COMPOSE = ROOT / "docker-compose.yml"
DATASOURCE = ROOT / "grafana" / "provisioning" / "datasources" / "influxdb.yml"
DEPENDABOT = ROOT / ".github" / "dependabot.yml"
ENV_EXAMPLE = ROOT / ".env.example"

SECRET_NAMES = ("INFLUXDB_PASSWORD", "INFLUXDB_TOKEN", "GRAFANA_PASSWORD")
SECRET_ENV_KEYS = (
    "DOCKER_INFLUXDB_INIT_PASSWORD",
    "DOCKER_INFLUXDB_INIT_ADMIN_TOKEN",
    "INFLUXDB_TOKEN",
    "GF_SECURITY_ADMIN_PASSWORD",
)
REQUIRED_RE = re.compile(r"^\$\{(?P<var>[A-Z_]+):\?[^}]+\}$")
DIGEST_RE = re.compile(r"^[\w./-]+:[\w.-]+@sha256:[0-9a-f]{64}$")


def load_compose():
    return yaml.safe_load(COMPOSE.read_text())


def env_map(service):
    """Return a service's environment as a dict of raw strings."""
    env = service.get("environment", {})
    if isinstance(env, dict):
        return {k: str(v) for k, v in env.items()}
    out = {}
    for item in env:
        key, _, value = item.partition("=")
        out[key] = value
    return out


def literal_secret_keys(services):
    """Secret-bearing env keys whose value is not a required ${VAR:?msg}."""
    bad = []
    for name, svc in services.items():
        for key, value in env_map(svc).items():
            if key in SECRET_ENV_KEYS and not REQUIRED_RE.match(value):
                bad.append(f"{name}.{key}")
    return bad


def unbound_ports(services):
    bad = []
    for name, svc in services.items():
        for port in svc.get("ports", []):
            if not str(port).startswith("${NETPULSE_BIND_ADDR:-127.0.0.1}:"):
                bad.append(f"{name}: {port}")
    return bad


def test_services_collected():
    services = load_compose()["services"]
    assert {"influxdb", "grafana", "speedtest-runner"} <= set(services)


def test_no_version_key():
    assert "version" not in load_compose()


def test_secrets_are_required_variables_not_defaults():
    services = load_compose()["services"]
    assert literal_secret_keys(services) == []
    # The three secrets must each appear in required-variable form.
    raw = COMPOSE.read_text()
    for var in SECRET_NAMES:
        assert re.search(r"\$\{%s:\?[^}]+\}" % var, raw), var
    # And none may carry a fallback default.
    for var in SECRET_NAMES:
        assert not re.search(r"\$\{%s:-" % var, raw), var


def test_secret_guard_bites():
    bad = {"a": {"environment": ["INFLUXDB_TOKEN=${INFLUXDB_TOKEN:-x}"]}}
    assert literal_secret_keys(bad) == ["a.INFLUXDB_TOKEN"]
    bad = {"a": {"environment": {"GF_SECURITY_ADMIN_PASSWORD": "literal"}}}
    assert literal_secret_keys(bad) == ["a.GF_SECURITY_ADMIN_PASSWORD"]
    good = {"a": {"environment": ["INFLUXDB_TOKEN=${INFLUXDB_TOKEN:?set it}"]}}
    assert literal_secret_keys(good) == []


def test_ports_use_loopback_variable():
    services = load_compose()["services"]
    published = [p for s in services.values() for p in s.get("ports", [])]
    assert published, "expected at least one published port"
    assert unbound_ports(services) == []


def test_port_guard_bites():
    assert unbound_ports({"a": {"ports": ["8086:8086"]}}) == ["a: 8086:8086"]
    assert unbound_ports({"a": {"ports": ["0.0.0.0:80:80"]}}) != []


def test_runner_is_locked_down():
    runner = load_compose()["services"]["speedtest-runner"]
    assert runner.get("cap_drop") == ["ALL"]
    assert "no-new-privileges:true" in runner.get("security_opt", [])
    assert runner.get("read_only") is True
    assert runner.get("tmpfs"), "a read-only root needs tmpfs for scratch paths"
    assert "healthcheck" in runner


def test_runner_state_path_is_writable_volume():
    runner = load_compose()["services"]["speedtest-runner"]
    state = env_map(runner)["NETPULSE_STATE_FILE"]
    mounts = [v.split(":")[1] for v in runner["volumes"]]
    assert any(state.startswith(m.rstrip("/") + "/") for m in mounts)


def test_images_are_tag_and_digest_pinned():
    services = load_compose()["services"]
    images = [s["image"] for s in services.values() if "image" in s]
    assert len(images) >= 2
    for image in images:
        assert DIGEST_RE.match(image), image
        assert ":latest" not in image


def test_image_guard_bites():
    assert not DIGEST_RE.match("grafana/grafana:latest")
    assert not DIGEST_RE.match("influxdb:2.7")
    assert DIGEST_RE.match("influxdb:2.7.12@sha256:" + "a" * 64)


def test_no_plugin_install_at_start():
    for name, svc in load_compose()["services"].items():
        assert "GF_INSTALL_PLUGINS" not in env_map(svc), name


def test_org_and_bucket_defaults_consistent():
    raw = COMPOSE.read_text()
    for var in ("INFLUXDB_ORG", "INFLUXDB_BUCKET"):
        defaults = set(re.findall(r"\$\{%s:-([^}]*)\}" % var, raw))
        assert defaults == {"netpulse"}, (var, defaults)
    ds = DATASOURCE.read_text()
    assert "${INFLUXDB_ORG" in ds and "${INFLUXDB_BUCKET" in ds


def test_grafana_receives_datasource_variables():
    grafana = env_map(load_compose()["services"]["grafana"])
    for key in ("INFLUXDB_TOKEN", "INFLUXDB_ORG", "INFLUXDB_BUCKET"):
        assert key in grafana, key


def test_datasource_has_no_literal_token_and_no_tls_skip():
    text = DATASOURCE.read_text()
    ds = yaml.safe_load(text)["datasources"][0]
    assert ds["secureJsonData"]["token"] == "${INFLUXDB_TOKEN}"
    assert "tlsSkipVerify" not in ds["jsonData"]
    assert ds["url"].startswith("http://influxdb:")


def test_env_example_covers_every_variable_without_secrets():
    raw = COMPOSE.read_text() + DATASOURCE.read_text()
    used = set(re.findall(r"\$\{([A-Z][A-Z0-9_]*)[:}]", raw))
    example = ENV_EXAMPLE.read_text()
    for var in used:
        assert re.search(r"^#?\s*%s=" % var, example, re.M), var
    for var in SECRET_NAMES:
        assert re.search(r"^%s=\s*$" % var, example, re.M), var
    assert "openssl rand -hex 32" in example


def test_dependabot_has_compose_entry():
    updates = yaml.safe_load(DEPENDABOT.read_text())["updates"]
    entries = [u for u in updates if u["package-ecosystem"] == "docker-compose"]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["directory"] == "/"
    assert entry["schedule"]["day"] == "monday"
    assert entry["cooldown"]["default-days"] == 7
    assert entry["groups"]


@pytest.mark.parametrize("path", [COMPOSE, DATASOURCE, DEPENDABOT])
def test_files_exist(path):
    assert path.exists()
