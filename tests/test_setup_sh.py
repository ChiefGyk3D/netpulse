"""Hardening tests for setup.sh.

setup.sh installs system files as root, so nothing here runs an install. The
script is either executed on its argument-error paths, or sourced (it does not
run main when sourced) so its helper functions can be called directly. Every
call is hermetic: no network, no root, no docker, a private tmp directory.
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

# Values a hostile or merely unlucky token/password could contain. Newlines
# are rejected, not quoted, so they are tested separately.
NASTY = [
    "plain",
    "$HOME",
    "$(touch PWNED)",
    "`touch PWNED`",
    "a'b\"c",
    "back\\slash",
    "with space and #hash",
    "end\\",
    "${IFS}x",
    "x;touch PWNED",
]


def run_script(args, cwd, env_extra=None, stdin=""):
    env = {"PATH": os.environ["PATH"], "HOME": str(cwd), "LC_ALL": "C.UTF-8"}
    env.update(env_extra or {})
    return subprocess.run(
        [BASH, str(SETUP), *args],
        cwd=cwd,
        env=env,
        input=stdin,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def call(fn_call, cwd, stdin="", env_extra=None):
    """Source setup.sh and run one shell snippet that uses its functions."""
    env = {"PATH": os.environ["PATH"], "HOME": str(cwd), "LC_ALL": "C.UTF-8"}
    env.update(env_extra or {})
    return subprocess.run(
        [BASH, "-c", f'source "{SETUP}"; {fn_call}'],
        cwd=cwd,
        env=env,
        input=stdin,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def parse_systemd_env(text):
    """The EnvironmentFile subset setup.sh writes: KEY="value" with \\\\ and \\"."""
    out = {}
    for line in text.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        key, _, raw = line.partition("=")
        if not raw.startswith('"'):
            assert re.fullmatch(r"[\w/.]+", raw), line  # plain constants only
            out[key] = raw
            continue
        assert raw.endswith('"'), line
        body = raw[1:-1]
        value, i = [], 0
        while i < len(body):
            if body[i] == "\\":
                assert body[i + 1] in '\\"', f"unsupported escape in {line!r}"
                value.append(body[i + 1])
                i += 2
            else:
                assert body[i] != '"', f"unescaped quote in {line!r}"
                value.append(body[i])
                i += 1
        out[key] = "".join(value)
    return out


# --- strict mode and the argument paths -------------------------------------


def test_strict_mode_is_on():
    text = SETUP.read_text()
    assert re.search(r"^set -euo pipefail$", text, re.MULTILINE)


def test_bash_syntax_ok():
    proc = subprocess.run([BASH, "-n", str(SETUP)], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr


def test_help_exits_zero_and_documents_safer_inputs(tmp_path):
    proc = run_script(["--help"], tmp_path)
    assert proc.returncode == 0
    assert "NETPULSE_INFLUXDB_TOKEN" in proc.stdout
    assert "NETPULSE_INFLUXDB_PASS" in proc.stdout
    assert "visible" in proc.stdout  # flags are documented as less safe


def test_unknown_option_fails(tmp_path):
    proc = run_script(["--bogus"], tmp_path)
    assert proc.returncode == 1
    assert "Unknown option" in proc.stdout


@pytest.mark.parametrize("flag", ["--influxdb-token", "--interval", "--influxdb-url"])
def test_option_without_value_is_a_clear_error(tmp_path, flag):
    proc = run_script([flag], tmp_path)
    assert proc.returncode == 1
    assert "requires a value" in proc.stdout


@pytest.mark.parametrize("bad", ["abc", "0", "-5", "1.5", "30; reboot", "", "007x", "$(id)"])
def test_interval_must_be_positive_integer(tmp_path, bad):
    proc = run_script(["--interval", bad, "--uninstall"], tmp_path)
    assert proc.returncode == 1
    assert "positive integer" in proc.stdout


def test_interval_valid_value_is_accepted_by_validator(tmp_path):
    for good in ("1", "30", "1440"):
        assert call(f"validate_interval {good}", tmp_path).returncode == 0
    # Negative case: the validator really bites.
    assert call("validate_interval 0", tmp_path).returncode != 0


def test_sourcing_does_not_run_main(tmp_path):
    proc = call("echo sourced-ok", tmp_path)
    assert proc.returncode == 0
    assert proc.stdout.strip().endswith("sourced-ok")
    assert "Please select an installation method" not in proc.stdout


# --- quoting ----------------------------------------------------------------


@pytest.mark.parametrize("value", NASTY)
def test_local_env_roundtrips_hostile_values(tmp_path, value):
    snippet = (
        "INFLUXDB_TOKEN=$VAL INFLUXDB_URL=$VAL INFLUXDB_ORG=$VAL INFLUXDB_BUCKET=$VAL; "
        "render_local_env 2"
    )
    proc = call(snippet, tmp_path, env_extra={"VAL": value})
    assert proc.returncode == 0, proc.stderr
    parsed = parse_systemd_env(proc.stdout)
    assert parsed["INFLUXDB_TOKEN"] == value
    assert parsed["INFLUXDB_ORG"] == value
    assert not (tmp_path / "PWNED").exists()


@pytest.mark.parametrize("value", NASTY)
def test_local_env_v1_roundtrips_password(tmp_path, value):
    snippet = "INFLUXDB_USERNAME=$VAL INFLUXDB_PASSWORD=$VAL; render_local_env 1"
    proc = call(snippet, tmp_path, env_extra={"VAL": value})
    assert proc.returncode == 0, proc.stderr
    parsed = parse_systemd_env(proc.stdout)
    assert parsed["INFLUXDB_PASSWORD"] == value
    assert parsed["INFLUXDB_USERNAME"] == value


@pytest.mark.parametrize("value", [v for v in NASTY if "'" not in v])
def test_docker_env_is_inert_when_sourced(tmp_path, value):
    """compose .env single-quotes are literal; bash sees the same rule."""
    proc = call("INFLUXDB_TOKEN=$VAL; render_docker_env > env.out", tmp_path, env_extra={"VAL": value})
    assert proc.returncode == 0, proc.stderr
    check = subprocess.run(
        [BASH, "-c", 'set -a; source env.out; printf %s "$INFLUXDB_TOKEN"'],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env={"PATH": os.environ["PATH"]},
        check=False,
    )
    assert check.stdout == value
    assert not (tmp_path / "PWNED").exists()


def test_docker_env_rejects_single_quote(tmp_path):
    proc = call("INFLUXDB_TOKEN=\"a'b\"; render_docker_env", tmp_path)
    assert proc.returncode != 0
    assert "single quote" in proc.stdout + proc.stderr


@pytest.mark.parametrize("value", ["a\nb", "a\rb"])
def test_newline_in_value_is_rejected(tmp_path, value):
    proc = call("INFLUXDB_TOKEN=$VAL; render_local_env 2", tmp_path, env_extra={"VAL": value})
    assert proc.returncode != 0
    assert "line break" in proc.stdout + proc.stderr


# --- secret files -----------------------------------------------------------


def test_secret_file_is_never_world_readable(tmp_path):
    target = tmp_path / "secret.env"
    proc = call(f'umask 022; printf "TOKEN=x\\n" | write_secret_file "{target}"', tmp_path)
    assert proc.returncode == 0, proc.stderr
    mode = stat.S_IMODE(target.stat().st_mode)
    assert mode & 0o077 == 0, oct(mode)
    assert target.read_text() == "TOKEN=x\n"


def test_secret_file_never_exists_with_loose_mode(tmp_path):
    """The file is created under umask 077, not chmod-ed afterwards."""
    text = SETUP.read_text()
    func = re.search(r"write_secret_file\(\) \{.*?\n\}", text, re.DOTALL)
    assert func and "umask 077" in func.group(0)


# --- unit files -------------------------------------------------------------


def test_timer_renders_validated_interval(tmp_path):
    proc = call("SPEEDTEST_INTERVAL=45; render_timer", tmp_path)
    assert proc.returncode == 0
    assert "OnUnitActiveSec=45min" in proc.stdout


# --- pip, curl, uninstall, compose ------------------------------------------


def test_pip_install_requires_hashes_and_does_not_upgrade_pip():
    text = SETUP.read_text()
    assert "--require-hashes -r" in text
    assert "--upgrade pip" not in text


def test_curl_to_shell_fails_loudly():
    text = SETUP.read_text()
    for line in text.splitlines():
        if "curl" in line and "packagecloud" in line:
            assert "-fsSL" in line, line
    assert re.search(r"curl -fsSL", text)
    assert not re.search(r"curl -s ", text)
    assert "already configured" in text


def test_uninstall_asks_before_deleting_state_dir(tmp_path):
    text = SETUP.read_text()
    block = re.search(r"# Remove state directory.*?\n\n", text, re.DOTALL)
    assert block and "ask_yes_no" in block.group(0)


@pytest.mark.parametrize(
    "version,ok",
    [("2.24.0", True), ("v2.24.0", True), ("2.30.1", True), ("3.0.0", True), ("2.23.9", False), ("1.29.2", False), ("", False)],
)
def test_compose_reset_support_by_version(tmp_path, version, ok):
    proc = call(f'compose_supports_reset "{version}"', tmp_path)
    assert (proc.returncode == 0) is ok


def test_ask_yes_no_defaults_to_no_on_eof(tmp_path):
    assert call("ask_yes_no 'Delete? '", tmp_path, stdin="").returncode == 1
    assert call("ask_yes_no 'Delete? '", tmp_path, stdin="y").returncode == 0
    assert call("ask_yes_no 'Delete? '", tmp_path, stdin="n").returncode == 1


def test_ghcr_without_reset_support_fails_before_touching_anything(tmp_path):
    stub = tmp_path / "bin"
    stub.mkdir()
    docker = stub / "docker"
    docker.write_text('#!/bin/sh\n[ "$1 $2" = "compose version" ] && { echo 2.20.0; exit 0; }\nexit 0\n')
    docker.chmod(0o755)
    path = f"{stub}:{os.environ['PATH']}"
    proc = run_script(["--docker-ghcr"], tmp_path, env_extra={"PATH": path})
    assert proc.returncode == 1
    assert "2.24" in proc.stdout
    assert not (tmp_path / "docker-compose.override.yml").exists()
