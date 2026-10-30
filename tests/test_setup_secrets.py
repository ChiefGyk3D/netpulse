"""Generated credentials, .env handling and loopback binding in setup.sh (#101).

The Docker path is run for real from a private copy of setup.sh with a stub
`docker` on PATH, so no container, network or root is involved and the repo's
own .env is never touched.
"""

import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

SETUP = Path(__file__).resolve().parent.parent / "setup.sh"
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None, reason="bash not available")

SECRET_VARS = ("INFLUXDB_PASSWORD", "INFLUXDB_TOKEN", "GRAFANA_PASSWORD")
OLD_DEFAULTS = ("speedtest123", "admin/admin", "GRAFANA_PASSWORD=admin")


def read_env(path):
    """Parse the single-quoted KEY='value' lines of a compose .env."""
    out = {}
    for line in Path(path).read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        key, _, raw = line.partition("=")
        out[key] = raw[1:-1] if raw.startswith("'") and raw.endswith("'") else raw
    return out


def docker_install(tmp_path, env_extra=None):
    """Run `setup.sh --docker` from a private copy with a stub docker."""
    work = tmp_path / "repo"
    work.mkdir(parents=True, exist_ok=True)
    shutil.copy(SETUP, work / "setup.sh")
    stub = tmp_path / "bin"
    stub.mkdir(exist_ok=True)
    docker = stub / "docker"
    docker.write_text('#!/bin/sh\n[ "$1 $2" = "compose version" ] && { echo 2.30.0; exit 0; }\nexit 0\n')
    docker.chmod(0o755)
    env = {"PATH": f"{stub}:{os.environ['PATH']}", "HOME": str(work), "LC_ALL": "C.UTF-8"}
    env.update(env_extra or {})
    proc = subprocess.run(
        [BASH, str(work / "setup.sh"), "--docker"],
        cwd=work,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        input="",
        check=False,
    )
    return work, proc


def call(snippet, cwd, env_extra=None):
    env = {"PATH": os.environ["PATH"], "HOME": str(cwd), "LC_ALL": "C.UTF-8"}
    env.update(env_extra or {})
    return subprocess.run(
        [BASH, "-c", f'source "{SETUP}"; {snippet}'],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        input="",
        check=False,
    )


def test_docker_install_generates_random_distinct_secrets(tmp_path):
    work, proc = docker_install(tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    env = read_env(work / ".env")
    values = [env[name] for name in SECRET_VARS]
    for value in values:
        assert re.fullmatch(r"[0-9a-f]{64}", value), "secret is not 256 bits of hex"
    assert len(set(values)) == 3, "the three secrets must differ"


def test_docker_install_values_differ_between_runs(tmp_path):
    work_a, proc_a = docker_install(tmp_path / "a")
    work_b, proc_b = docker_install(tmp_path / "b")
    assert proc_a.returncode == 0 and proc_b.returncode == 0
    a, b = read_env(work_a / ".env"), read_env(work_b / ".env")
    for name in SECRET_VARS:
        assert a[name] != b[name], f"{name} repeated across runs"


def test_docker_install_env_is_mode_0600(tmp_path):
    work, proc = docker_install(tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert stat.S_IMODE((work / ".env").stat().st_mode) == 0o600


def test_docker_install_keeps_an_existing_env(tmp_path):
    work, first = docker_install(tmp_path)
    assert first.returncode == 0
    before = (work / ".env").read_text()
    work, second = docker_install(tmp_path)
    assert second.returncode == 0, second.stdout + second.stderr
    assert (work / ".env").read_text() == before
    assert "Keeping the existing" in second.stdout


def test_docker_install_never_prints_a_secret_or_old_default(tmp_path):
    work, proc = docker_install(tmp_path)
    assert proc.returncode == 0
    env = read_env(work / ".env")
    output = proc.stdout + proc.stderr
    for name in SECRET_VARS:
        assert env[name] not in output
    for old in OLD_DEFAULTS:
        assert old not in output
    assert ".env" in output  # it says where the file is


def test_docker_env_contains_no_old_default_credentials(tmp_path):
    work, proc = docker_install(tmp_path)
    assert proc.returncode == 0
    assert "speedtest123" not in (work / ".env").read_text()
    assert read_env(work / ".env")["GRAFANA_PASSWORD"] != "admin"


def test_docker_install_supplied_token_is_kept_and_not_printed(tmp_path):
    work, proc = docker_install(tmp_path, env_extra={"NETPULSE_INFLUXDB_TOKEN": "my-own-token"})
    assert proc.returncode == 0
    assert read_env(work / ".env")["INFLUXDB_TOKEN"] == "my-own-token"
    assert "my-own-token" not in proc.stdout + proc.stderr


def test_docker_env_defaults_to_loopback_bind(tmp_path):
    work, proc = docker_install(tmp_path)
    assert proc.returncode == 0
    assert read_env(work / ".env")["NETPULSE_BIND_ADDR"] == "127.0.0.1"


def test_docker_env_bind_addr_comes_from_the_environment(tmp_path):
    work, proc = docker_install(tmp_path, env_extra={"NETPULSE_BIND_ADDR": "0.0.0.0"})
    assert proc.returncode == 0
    assert read_env(work / ".env")["NETPULSE_BIND_ADDR"] == "0.0.0.0"


@pytest.mark.parametrize("bad", ["a b", "1.2.3.4;x", "$(id)", "a'b", "a\nb"])
def test_docker_env_rejects_unsafe_bind_addr(tmp_path, bad):
    proc = call("render_docker_env", tmp_path, env_extra={"NETPULSE_BIND_ADDR": bad})
    assert proc.returncode != 0
    assert "NETPULSE_BIND_ADDR" in proc.stdout + proc.stderr


def test_setup_has_no_hardcoded_default_credentials():
    text = SETUP.read_text()
    for old in ("speedtest123", "admin/admin", "GRAFANA_PASSWORD=admin"):
        assert old not in text


def test_generate_secret_works_without_openssl(tmp_path):
    """With openssl hidden, python3 or /dev/urandom still yields 256 bits."""
    snippet = 'command() { [ "$2" = openssl ] && return 1; builtin command "$@"; }; generate_secret'
    proc = call(snippet, tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert re.fullmatch(r"[0-9a-f]{64}", proc.stdout.strip())


def test_help_documents_bind_address(tmp_path):
    proc = subprocess.run([BASH, str(SETUP), "--help"], cwd=tmp_path, capture_output=True, text=True, input="", check=False)
    assert proc.returncode == 0
    assert "NETPULSE_BIND_ADDR" in proc.stdout
