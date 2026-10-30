#!/usr/bin/env bash
#
# NetPulse Setup Script
#
# This script provides multiple installation options:
# - Local install with systemd timer (more efficient than daemon)
# - Docker Compose (full stack with InfluxDB + Grafana)
# - Docker from GHCR (pull pre-built images)
#
# Usage: ./setup.sh [OPTIONS]
#
# Sourcing this file defines the functions and does not run an install.
#

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Configuration
INSTALL_DIR="/opt/netpulse"
CONFIG_DIR="/etc/netpulse"
STATE_DIR="/var/lib/netpulse"
SYSTEMD_DIR="/etc/systemd/system"
GHCR_IMAGE="ghcr.io/chiefgyk3d/netpulse:latest"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Oldest Docker Compose plugin that understands "!reset" in an override file.
COMPOSE_MIN_RESET="2.24.0"

# Default values
INFLUXDB_URL="http://localhost:8086"
INFLUXDB_VERSION="2" # "1" or "2"

# InfluxDB 2.x settings (token-based). Secrets may come from the environment so
# they stay out of the process list; the command-line flags still work.
INFLUXDB_TOKEN="${NETPULSE_INFLUXDB_TOKEN:-}"
INFLUXDB_ORG="netpulse"
INFLUXDB_BUCKET="netpulse"

# InfluxDB 1.x settings (username/password)
INFLUXDB_USERNAME=""
INFLUXDB_PASSWORD="${NETPULSE_INFLUXDB_PASS:-}"
INFLUXDB_DATABASE="netpulse"

SPEEDTEST_INTERVAL="30" # minutes for systemd timer

print_banner() {
    echo -e "${CYAN}"
    echo "╔════════════════════════════════════════════════════════════╗"
    echo "║                                                            ║"
    echo "║   ███╗   ██╗███████╗████████╗██████╗ ██╗   ██╗██╗     ███████╗   ║"
    echo "║   ████╗  ██║██╔════╝╚══██╔══╝██╔══██╗██║   ██║██║     ██╔════╝   ║"
    echo "║   ██╔██╗ ██║█████╗     ██║   ██████╔╝██║   ██║██║     ███████╗   ║"
    echo "║   ██║╚██╗██║██╔══╝     ██║   ██╔═══╝ ██║   ██║██║     ╚════██║   ║"
    echo "║   ██║ ╚████║███████╗   ██║   ██║     ╚██████╔╝███████╗███████║   ║"
    echo "║   ╚═╝  ╚═══╝╚══════╝   ╚═╝   ╚═╝      ╚═════╝ ╚══════╝╚══════╝   ║"
    echo "║                                                            ║"
    echo "║              Network Speed & ISP Monitor                   ║"
    echo "╚════════════════════════════════════════════════════════════╝"
    echo -e "${NC}"
}

print_help() {
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Installation Options:"
    echo "  --local              Install locally with systemd timer (recommended)"
    echo "  --docker             Install with Docker Compose (full stack)"
    echo "  --docker-ghcr        Use pre-built image from GHCR (needs Compose v${COMPOSE_MIN_RESET}+)"
    echo "  --uninstall          Remove NetPulse installation"
    echo ""
    echo "InfluxDB Version (choose one):"
    echo "  --influxdb-v1        Use InfluxDB 1.x (username/password auth)"
    echo "  --influxdb-v2        Use InfluxDB 2.x (token-based auth) [default]"
    echo ""
    echo "InfluxDB 2.x Options (token-based):"
    echo "  --influxdb-url URL   InfluxDB URL (default: http://localhost:8086)"
    echo "  --influxdb-token T   InfluxDB API token (required for v2)"
    echo "  --influxdb-org ORG   InfluxDB organization (default: netpulse)"
    echo "  --influxdb-bucket B  InfluxDB bucket (default: netpulse)"
    echo ""
    echo "InfluxDB 1.x Options (username/password):"
    echo "  --influxdb-url URL   InfluxDB URL (default: http://localhost:8086)"
    echo "  --influxdb-user U    InfluxDB username (required for v1)"
    echo "  --influxdb-pass P    InfluxDB password (required for v1)"
    echo "  --influxdb-db DB     InfluxDB database (default: netpulse)"
    echo ""
    echo "Other Options:"
    echo "  --interval MIN       Speedtest interval in minutes, a positive integer (default: 30)"
    echo "  --help, -h           Show this help message"
    echo ""
    echo "Secrets (safer than flags):"
    echo "  A value given with --influxdb-token or --influxdb-pass is visible to other"
    echo "  users in the process list and may land in shell history. Prefer the"
    echo "  environment, or leave it out and answer the silent prompt:"
    echo "    NETPULSE_INFLUXDB_TOKEN=...   InfluxDB 2.x API token"
    echo "    NETPULSE_INFLUXDB_PASS=...    InfluxDB 1.x password"
    echo "  (sudo drops the environment by default; use sudo --preserve-env=NAME.)"
    echo ""
    echo "Docker installs:"
    echo "  --docker and --docker-ghcr generate random InfluxDB and Grafana credentials"
    echo "  into .env (mode 0600) on the first run and never overwrite an existing .env."
    echo "  The published ports bind to 127.0.0.1 unless you set:"
    echo "    NETPULSE_BIND_ADDR=ADDRESS    interface address to bind (default: 127.0.0.1)"
    echo ""
    echo "Examples:"
    echo "  sudo $0 --local --influxdb-v2                 # prompts for the token silently"
    echo "  $0 --local --influxdb-v1 --influxdb-user admin  # prompts for the password"
    echo "  $0 --docker                          # Docker Compose (includes InfluxDB 2.x)"
    echo "  $0 --docker-ghcr                     # Docker with GHCR image"
    echo "  $0 --uninstall                       # Remove installation"
}

log_info() {
    printf '%b[INFO]%b %s\n' "$BLUE" "$NC" "$1"
}

log_success() {
    printf '%b[SUCCESS]%b %s\n' "$GREEN" "$NC" "$1"
}

log_warn() {
    printf '%b[WARN]%b %s\n' "$YELLOW" "$NC" "$1"
}

log_error() {
    printf '%b[ERROR]%b %s\n' "$RED" "$NC" "$1"
}

check_root() {
    if [[ $EUID -ne 0 ]]; then
        log_error "This script must be run as root (use sudo)"
        exit 1
    fi
}

# --- input helpers ---------------------------------------------------------

# prompt VAR TEXT [DEFAULT]: read a line into VAR; an empty answer keeps DEFAULT.
prompt() {
    local __var=$1 __text=$2 __default=${3:-} __reply=""
    if [[ -n $__default ]]; then
        __text="$__text [$__default]"
    fi
    read -r -p "$__text: " __reply || {
        log_error "No input available for: $__text"
        exit 1
    }
    printf -v "$__var" '%s' "${__reply:-$__default}"
}

# prompt_secret VAR TEXT: like prompt, but nothing is echoed.
prompt_secret() {
    local __var=$1 __text=$2 __reply=""
    read -r -s -p "$__text: " __reply || {
        echo ""
        log_error "No input available for: $__text"
        exit 1
    }
    echo ""
    printf -v "$__var" '%s' "$__reply"
}

# ask_yes_no TEXT: a y/N question; anything but y, including end of input, is no.
ask_yes_no() {
    local reply=""
    read -r -n 1 -p "$1 [y/N] " reply || true
    echo
    [[ $reply =~ ^[Yy]$ ]]
}

# validate_interval VALUE: a positive integer, so it is safe in a timer unit.
validate_interval() {
    if [[ ! $1 =~ ^[1-9][0-9]{0,8}$ ]]; then
        log_error "--interval must be a positive integer (minutes), got: ${1:0:40}"
        return 1
    fi
}

# --- quoting for files that hold values ------------------------------------

# reject_line_breaks NAME VALUE: a line break cannot be stored in an env file.
reject_line_breaks() {
    case $2 in
        *$'\n'* | *$'\r'*)
            log_error "$1 contains a line break, which cannot be stored safely"
            return 1
            ;;
    esac
}

# env_quote_systemd VALUE: double-quoted for a systemd EnvironmentFile, where
# only backslash and the double quote need an escape and nothing is expanded.
env_quote_systemd() {
    local v=$1
    v=${v//\\/\\\\}
    v=${v//\"/\\\"}
    printf '"%s"' "$v"
}

# env_quote_compose VALUE: single-quoted for a Docker Compose .env, where a
# single-quoted value is literal. A single quote cannot be escaped there, so
# such a value is refused instead of being guessed at.
env_quote_compose() {
    case $1 in
        *"'"*)
            log_error "A value contains a single quote, which a Docker .env file cannot store safely" >&2
            return 1
            ;;
    esac
    printf "'%s'" "$1"
}

# generate_secret: 64 hex characters (256 bits) from the system CSPRNG on
# stdout. openssl is preferred, then python3, then /dev/urandom itself, so
# no new dependency is needed.
generate_secret() {
    local out=""
    if command -v openssl &>/dev/null && out="$(openssl rand -hex 32 2>/dev/null)" && [[ $out =~ ^[0-9a-f]{64}$ ]]; then
        printf '%s' "$out"
    elif command -v python3 &>/dev/null && out="$(python3 -c 'import secrets; print(secrets.token_hex(32))' 2>/dev/null)" && [[ $out =~ ^[0-9a-f]{64}$ ]]; then
        printf '%s' "$out"
    elif out="$(head -c 32 /dev/urandom | od -An -v -tx1 | tr -d ' \n')" && [[ $out =~ ^[0-9a-f]{64}$ ]]; then
        printf '%s' "$out"
    else
        log_error "Could not generate a random secret (need openssl, python3 or /dev/urandom)" >&2
        return 1
    fi
}

# validate_bind_addr VALUE: a bind address is a bare host token, never a value
# that could carry a line break, quote or space into the .env file.
validate_bind_addr() {
    if [[ ! $1 =~ ^[A-Za-z0-9.:_-]{1,253}$ ]]; then
        log_error "NETPULSE_BIND_ADDR must be an address of letters, digits, dots, colons, dashes or underscores, got: ${1:0:40}"
        return 1
    fi
}

# write_secret_file PATH: stdin becomes the file, created 0600 from the start.
write_secret_file() {
    (
        umask 077
        rm -f -- "$1"
        set -o noclobber
        cat >"$1"
    )
}

# render_local_env INFLUX_VERSION: the systemd EnvironmentFile on stdout.
render_local_env() {
    local version=$1 name value
    for name in INFLUXDB_URL INFLUXDB_TOKEN INFLUXDB_ORG INFLUXDB_BUCKET INFLUXDB_USERNAME INFLUXDB_PASSWORD INFLUXDB_DATABASE; do
        value=${!name}
        reject_line_breaks "$name" "$value"
    done
    echo "# NetPulse Configuration"
    echo "# Generated by setup.sh on $(date)"
    if [[ $version == "1" ]]; then
        echo "# InfluxDB 1.x (username/password authentication)"
        echo ""
        echo "INFLUXDB_VERSION=1"
        printf 'INFLUXDB_URL=%s\n' "$(env_quote_systemd "$INFLUXDB_URL")"
        printf 'INFLUXDB_USERNAME=%s\n' "$(env_quote_systemd "$INFLUXDB_USERNAME")"
        printf 'INFLUXDB_PASSWORD=%s\n' "$(env_quote_systemd "$INFLUXDB_PASSWORD")"
        printf 'INFLUXDB_DATABASE=%s\n' "$(env_quote_systemd "$INFLUXDB_DATABASE")"
    else
        echo "# InfluxDB 2.x (token-based authentication)"
        echo ""
        echo "INFLUXDB_VERSION=2"
        printf 'INFLUXDB_URL=%s\n' "$(env_quote_systemd "$INFLUXDB_URL")"
        printf 'INFLUXDB_TOKEN=%s\n' "$(env_quote_systemd "$INFLUXDB_TOKEN")"
        printf 'INFLUXDB_ORG=%s\n' "$(env_quote_systemd "$INFLUXDB_ORG")"
        printf 'INFLUXDB_BUCKET=%s\n' "$(env_quote_systemd "$INFLUXDB_BUCKET")"
    fi
    echo "NETPULSE_STATE_FILE=$STATE_DIR/state.json"
}

# render_docker_env: the Docker Compose .env on stdout.
render_docker_env() {
    local name value q_org q_bucket q_token q_dbpass q_grafana q_bind bind_addr
    local token db_password grafana_password
    bind_addr="${NETPULSE_BIND_ADDR:-127.0.0.1}"
    validate_bind_addr "$bind_addr"
    for name in INFLUXDB_ORG INFLUXDB_BUCKET INFLUXDB_TOKEN; do
        value=${!name}
        reject_line_breaks "$name" "$value"
    done
    # An InfluxDB token given by flag or environment is kept; the rest is new.
    token=$INFLUXDB_TOKEN
    if [[ -z $token ]]; then
        token="$(generate_secret)"
    fi
    db_password="$(generate_secret)"
    grafana_password="$(generate_secret)"
    # Quote before the heredoc: a failure inside a heredoc substitution is lost.
    q_org="$(env_quote_compose "$INFLUXDB_ORG")"
    q_bucket="$(env_quote_compose "$INFLUXDB_BUCKET")"
    q_token="$(env_quote_compose "$token")"
    q_dbpass="$(env_quote_compose "$db_password")"
    q_grafana="$(env_quote_compose "$grafana_password")"
    q_bind="$(env_quote_compose "$bind_addr")"
    cat <<EOF
# NetPulse Docker Configuration
# Generated by setup.sh on $(date)
# Keep this file private (mode 0600). setup.sh never overwrites it; delete it
# and re-run setup.sh to generate new values.

# InfluxDB Configuration (random values generated by setup.sh)
INFLUXDB_USERNAME=admin
INFLUXDB_PASSWORD=${q_dbpass}
INFLUXDB_ORG=${q_org}
INFLUXDB_BUCKET=${q_bucket}
INFLUXDB_TOKEN=${q_token}

# Grafana Configuration (random password generated by setup.sh)
GRAFANA_USER=admin
GRAFANA_PASSWORD=${q_grafana}

# Network exposure: the address the published InfluxDB (8086) and Grafana
# (3000) ports bind to. 127.0.0.1 serves this machine only. To reach the stack
# from another machine, set this to that interface's address (or 0.0.0.0 for
# all interfaces) only when a firewall or reverse proxy controls access, then
# run: docker compose up -d
NETPULSE_BIND_ADDR=${q_bind}

# Speedtest Configuration
SPEEDTEST_INTERVAL=1800

# Timezone
TZ=America/New_York
EOF
}

render_service() {
    cat <<EOF
[Unit]
Description=NetPulse Speedtest Runner
Documentation=https://github.com/chiefgyk3d/netpulse
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=netpulse
Group=netpulse
WorkingDirectory=$INSTALL_DIR
EnvironmentFile=$CONFIG_DIR/netpulse.env
ExecStart=$INSTALL_DIR/venv/bin/python $INSTALL_DIR/speedtest_runner.py --once
Environment="NETPULSE_STATE_FILE=$STATE_DIR/state.json"

# Security hardening
NoNewPrivileges=yes
ProtectSystem=strict
ReadWritePaths=$STATE_DIR
ProtectHome=yes
PrivateTmp=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictRealtime=yes
RestrictSUIDSGID=yes
PrivateNetwork=no

StandardOutput=journal
StandardError=journal
SyslogIdentifier=netpulse

[Install]
WantedBy=multi-user.target
EOF
}

# render_timer: uses SPEEDTEST_INTERVAL, which validate_interval has checked.
render_timer() {
    validate_interval "$SPEEDTEST_INTERVAL"
    cat <<EOF
[Unit]
Description=Run NetPulse Speedtest every ${SPEEDTEST_INTERVAL} minutes
Documentation=https://github.com/chiefgyk3d/netpulse

[Timer]
OnBootSec=2min
OnUnitActiveSec=${SPEEDTEST_INTERVAL}min
AccuracySec=1min
Persistent=true
RandomizedDelaySec=60

[Install]
WantedBy=timers.target
EOF
}

# --- dependencies ----------------------------------------------------------

check_dependencies() {
    local missing=()
    local cmd

    for cmd in curl python3 pip3; do
        if ! command -v "$cmd" &>/dev/null; then
            missing+=("$cmd")
        fi
    done

    if [[ ${#missing[@]} -gt 0 ]]; then
        log_error "Missing dependencies: ${missing[*]}"
        log_info "Install them with: apt install ${missing[*]}"
        exit 1
    fi
}

# compose_supports_reset VERSION: true when the Compose plugin knows "!reset".
compose_supports_reset() {
    local v="${1#v}" lowest
    [[ $v =~ ^[0-9]+\.[0-9]+(\.[0-9]+)? ]] || return 1
    v="${BASH_REMATCH[0]}"
    lowest="$(printf '%s\n%s\n' "$COMPOSE_MIN_RESET" "$v" | sort -V | head -n 1)"
    [[ $lowest == "$COMPOSE_MIN_RESET" ]]
}

# detect_compose: sets COMPOSE (an array) and COMPOSE_VERSION ("" for v1).
detect_compose() {
    if docker compose version &>/dev/null; then
        COMPOSE=(docker compose)
        COMPOSE_VERSION="$(docker compose version --short 2>/dev/null || true)"
    else
        COMPOSE=(docker-compose)
        COMPOSE_VERSION=""
    fi
}

check_docker() {
    if ! command -v docker &>/dev/null; then
        log_error "Docker is not installed"
        log_info "Install Docker: https://docs.docker.com/engine/install/"
        exit 1
    fi

    if ! command -v docker-compose &>/dev/null && ! docker compose version &>/dev/null; then
        log_error "Docker Compose is not installed"
        log_info "Install Docker Compose: https://docs.docker.com/compose/install/"
        exit 1
    fi
}

# fetch_repo_script URL: download a repository setup script to a temp file,
# failing on any HTTP error, and print its path.
fetch_repo_script() {
    local tmp
    tmp="$(mktemp)"
    if ! curl -fsSL "$1" -o "$tmp"; then
        rm -f "$tmp"
        log_error "Could not download $1"
        return 1
    fi
    printf '%s' "$tmp"
}

install_speedtest_cli() {
    log_info "Installing Ookla Speedtest CLI..."

    if command -v speedtest &>/dev/null; then
        log_info "Speedtest CLI already installed"
        return 0
    fi

    local script

    # Detect OS and install accordingly
    if [[ -f /etc/debian_version ]]; then
        # Debian/Ubuntu
        if [[ -e /etc/apt/sources.list.d/ookla_speedtest-cli.list || -e /etc/apt/sources.list.d/ookla_speedtest-cli.sources ]]; then
            log_info "Ookla package repository already configured; skipping its setup script"
        else
            script="$(fetch_repo_script https://packagecloud.io/install/repositories/ookla/speedtest-cli/script.deb.sh)"
            bash "$script"
            rm -f "$script"
        fi
        apt-get install -y speedtest
    elif [[ -f /etc/redhat-release ]]; then
        # RHEL/CentOS/Fedora
        if [[ -e /etc/yum.repos.d/ookla_speedtest-cli.repo ]]; then
            log_info "Ookla package repository already configured; skipping its setup script"
        else
            script="$(fetch_repo_script https://packagecloud.io/install/repositories/ookla/speedtest-cli/script.rpm.sh)"
            bash "$script"
            rm -f "$script"
        fi
        yum install -y speedtest || dnf install -y speedtest
    elif [[ -f /etc/arch-release ]]; then
        # Arch Linux
        pacman -S --noconfirm speedtest-cli
    else
        log_error "Unsupported OS. Please install Ookla Speedtest CLI manually."
        log_info "Visit: https://www.speedtest.net/apps/cli"
        exit 1
    fi

    log_success "Speedtest CLI installed"
}

create_netpulse_user() {
    if id "netpulse" &>/dev/null; then
        log_info "User 'netpulse' already exists"
    else
        log_info "Creating netpulse user..."
        useradd --system --no-create-home --shell /usr/sbin/nologin netpulse
        log_success "User 'netpulse' created"
    fi
}

install_local() {
    log_info "Starting local installation with systemd timer..."

    check_root
    check_dependencies
    install_speedtest_cli
    create_netpulse_user

    local db_choice=""

    # Prompt for InfluxDB version if not specified
    if [[ "$INFLUXDB_VERSION" == "2" && -z "$INFLUXDB_TOKEN" ]]; then
        echo ""
        echo "InfluxDB Configuration:"
        echo "  1) InfluxDB 2.x (token-based authentication) - Modern"
        echo "  2) InfluxDB 1.x (username/password authentication) - Legacy"
        echo ""
        prompt db_choice "Select InfluxDB version [1-2]"
        case $db_choice in
            2) INFLUXDB_VERSION="1" ;;
            *) INFLUXDB_VERSION="2" ;;
        esac
    fi

    # Get InfluxDB credentials based on version
    if [[ "$INFLUXDB_VERSION" == "1" ]]; then
        if [[ -z "$INFLUXDB_USERNAME" ]]; then
            prompt INFLUXDB_USERNAME "InfluxDB username"
        fi
        if [[ -z "$INFLUXDB_PASSWORD" ]]; then
            prompt_secret INFLUXDB_PASSWORD "InfluxDB password"
        fi
        prompt INFLUXDB_URL "InfluxDB URL" "$INFLUXDB_URL"
        prompt INFLUXDB_DATABASE "InfluxDB database" "$INFLUXDB_DATABASE"
    else
        if [[ -z "$INFLUXDB_TOKEN" ]]; then
            prompt_secret INFLUXDB_TOKEN "InfluxDB API token"
        fi
        prompt INFLUXDB_URL "InfluxDB URL" "$INFLUXDB_URL"
        prompt INFLUXDB_ORG "InfluxDB organization" "$INFLUXDB_ORG"
        prompt INFLUXDB_BUCKET "InfluxDB bucket" "$INFLUXDB_BUCKET"
    fi

    # Render everything that can fail on a bad value before touching the system.
    local env_content
    env_content="$(render_local_env "$INFLUXDB_VERSION")"
    validate_interval "$SPEEDTEST_INTERVAL"

    # Create directories
    log_info "Creating directories..."
    mkdir -p "$INSTALL_DIR"
    mkdir -p "$CONFIG_DIR"
    chmod 750 "$CONFIG_DIR"
    mkdir -p "$STATE_DIR" # State persistence directory

    # Copy application files
    log_info "Installing application files..."
    cp "$SCRIPT_DIR/speedtest-runner/speedtest_runner.py" "$INSTALL_DIR/"
    cp "$SCRIPT_DIR/speedtest-runner/requirements.txt" "$INSTALL_DIR/"

    # Create virtual environment; every dependency is installed by hash.
    log_info "Setting up Python virtual environment..."
    python3 -m venv "$INSTALL_DIR/venv"
    "$INSTALL_DIR/venv/bin/pip" install --require-hashes -r "$INSTALL_DIR/requirements.txt"

    # Create config file based on InfluxDB version (0600 from creation)
    log_info "Creating configuration file..."
    printf '%s\n' "$env_content" | write_secret_file "$CONFIG_DIR/netpulse.env"

    # Install systemd service
    log_info "Installing systemd service..."
    render_service >"$SYSTEMD_DIR/netpulse.service"

    # Install systemd timer
    log_info "Installing systemd timer..."
    render_timer >"$SYSTEMD_DIR/netpulse.timer"

    # Set permissions
    log_info "Setting permissions..."
    chown -R netpulse:netpulse "$INSTALL_DIR"
    chown -R netpulse:netpulse "$STATE_DIR"
    chown -R root:netpulse "$CONFIG_DIR"
    chmod 750 "$CONFIG_DIR"
    chmod 640 "$CONFIG_DIR/netpulse.env"

    # Enable and start timer
    log_info "Enabling systemd timer..."
    systemctl daemon-reload
    systemctl enable netpulse.timer
    systemctl start netpulse.timer

    log_success "Local installation complete!"
    echo ""
    echo -e "${GREEN}NetPulse is now installed and will run every ${SPEEDTEST_INTERVAL} minutes.${NC}"
    echo ""
    echo "Useful commands:"
    echo "  sudo systemctl status netpulse.timer    # Check timer status"
    echo "  sudo systemctl list-timers              # List all timers"
    echo "  sudo systemctl start netpulse.service   # Run speedtest now"
    echo "  sudo journalctl -u netpulse -f          # View logs"
    echo ""
    echo "Configuration file: $CONFIG_DIR/netpulse.env"
    echo "State file: $STATE_DIR/state.json"
}

install_docker() {
    local use_ghcr=$1

    log_info "Starting Docker installation..."
    check_docker
    detect_compose

    cd "$SCRIPT_DIR"

    if [[ "$use_ghcr" == "true" ]]; then
        # The override drops the local build with "!reset", which Compose v1
        # and Compose plugins older than COMPOSE_MIN_RESET cannot parse.
        if ! compose_supports_reset "$COMPOSE_VERSION"; then
            log_error "--docker-ghcr needs the Docker Compose plugin v${COMPOSE_MIN_RESET} or newer (found: ${COMPOSE_VERSION:-docker-compose v1 or unknown})"
            log_info "Upgrade Docker Compose, or use --docker to build the image locally"
            exit 1
        fi
    fi

    # Render the .env first so a bad value stops the install before any change.
    local env_content=""
    if [[ ! -f .env ]]; then
        env_content="$(render_docker_env)"
    fi

    if [[ "$use_ghcr" == "true" ]]; then
        log_info "Using pre-built image from GHCR..."

        # Create docker-compose override for GHCR
        cat >docker-compose.override.yml <<EOF
# Generated by setup.sh - uses GHCR image instead of local build
version: '3.8'

services:
  speedtest-runner:
    image: ${GHCR_IMAGE}
    build: !reset null
EOF
        log_info "Pulling image from GHCR..."
        docker pull "$GHCR_IMAGE"
    fi

    # Create .env file if it doesn't exist (0600 from creation)
    if [[ ! -f .env ]]; then
        log_info "Creating .env file with generated credentials..."
        printf '%s\n' "$env_content" | write_secret_file .env
        log_info "Credentials are in $SCRIPT_DIR/.env (mode 0600); they are not printed here"
    else
        log_info "Keeping the existing $SCRIPT_DIR/.env; no credentials were changed"
    fi

    log_info "Starting Docker Compose stack..."
    "${COMPOSE[@]}" up -d

    log_success "Docker installation complete!"
    echo ""
    echo -e "${GREEN}NetPulse is now running in Docker.${NC}"
    echo ""
    local bind_addr access_host
    bind_addr="$(sed -n "s/^NETPULSE_BIND_ADDR=['\"]\{0,1\}\([^'\"]*\).*/\1/p" .env | tail -n 1)"
    bind_addr="${bind_addr:-127.0.0.1}"
    access_host="$bind_addr"
    if [[ $bind_addr == "0.0.0.0" || $bind_addr == "::" ]]; then
        access_host="localhost"
    fi
    echo "Access points (bound to ${bind_addr}):"
    echo "  Grafana:   http://${access_host}:3000"
    echo "  InfluxDB:  http://${access_host}:8086"
    echo ""
    echo "Grafana login: user admin, password GRAFANA_PASSWORD from $SCRIPT_DIR/.env"
    echo "To change the bind address, edit NETPULSE_BIND_ADDR in .env and run: docker compose up -d"
    echo ""
    echo "Useful commands:"
    echo "  docker compose logs -f speedtest-runner  # View logs"
    echo "  docker compose ps                        # Check status"
    echo "  docker compose down                      # Stop all services"
    echo "  docker compose up -d                     # Start all services"
}

uninstall() {
    log_info "Uninstalling NetPulse..."

    check_root

    # Stop and disable systemd timer/service
    if systemctl is-active --quiet netpulse.timer 2>/dev/null; then
        log_info "Stopping systemd timer..."
        systemctl stop netpulse.timer
        systemctl disable netpulse.timer
    fi

    if systemctl is-active --quiet netpulse.service 2>/dev/null; then
        systemctl stop netpulse.service
    fi

    # Remove systemd files
    if [[ -f "$SYSTEMD_DIR/netpulse.service" ]]; then
        log_info "Removing systemd files..."
        rm -f "$SYSTEMD_DIR/netpulse.service"
        rm -f "$SYSTEMD_DIR/netpulse.timer"
        systemctl daemon-reload
    fi

    # Remove installation directory
    if [[ -d "$INSTALL_DIR" ]]; then
        log_info "Removing installation directory..."
        rm -rf "$INSTALL_DIR"
    fi

    # Remove state directory (ask first)
    if [[ -d "$STATE_DIR" ]]; then
        if ask_yes_no "Remove state directory $STATE_DIR?"; then
            rm -rf "$STATE_DIR"
        fi
    fi

    # Remove config directory (ask first)
    if [[ -d "$CONFIG_DIR" ]]; then
        if ask_yes_no "Remove configuration directory $CONFIG_DIR?"; then
            rm -rf "$CONFIG_DIR"
        fi
    fi

    # Remove user (ask first)
    if id "netpulse" &>/dev/null; then
        if ask_yes_no "Remove netpulse user?"; then
            userdel netpulse
        fi
    fi

    # Check for Docker installation
    if [[ -f "$SCRIPT_DIR/docker-compose.yml" ]]; then
        if ask_yes_no "Stop and remove Docker containers?"; then
            if command -v docker &>/dev/null; then
                cd "$SCRIPT_DIR"
                detect_compose
                "${COMPOSE[@]}" down -v
                rm -f docker-compose.override.yml
            else
                log_warn "Docker is not installed; skipping container removal"
            fi
        fi
    fi

    log_success "NetPulse uninstalled!"
}

main() {
    # Parse command line arguments
    local INSTALL_MODE=""
    local USE_GHCR="false"
    local choice=""

    while [[ $# -gt 0 ]]; do
        case $1 in
            --local)
                INSTALL_MODE="local"
                shift
                ;;
            --docker)
                INSTALL_MODE="docker"
                shift
                ;;
            --docker-ghcr)
                INSTALL_MODE="docker"
                USE_GHCR="true"
                shift
                ;;
            --uninstall)
                INSTALL_MODE="uninstall"
                shift
                ;;
            --influxdb-v1)
                INFLUXDB_VERSION="1"
                shift
                ;;
            --influxdb-v2)
                INFLUXDB_VERSION="2"
                shift
                ;;
            --influxdb-url | --influxdb-token | --influxdb-org | --influxdb-bucket | --influxdb-user | --influxdb-pass | --influxdb-db | --interval)
                if [[ $# -lt 2 ]]; then
                    log_error "Option $1 requires a value"
                    exit 1
                fi
                case $1 in
                    --influxdb-url) INFLUXDB_URL="$2" ;;
                    --influxdb-token) INFLUXDB_TOKEN="$2" ;;
                    --influxdb-org) INFLUXDB_ORG="$2" ;;
                    --influxdb-bucket) INFLUXDB_BUCKET="$2" ;;
                    --influxdb-user) INFLUXDB_USERNAME="$2" ;;
                    --influxdb-pass) INFLUXDB_PASSWORD="$2" ;;
                    --influxdb-db) INFLUXDB_DATABASE="$2" ;;
                    --interval) SPEEDTEST_INTERVAL="$2" ;;
                esac
                shift 2
                ;;
            --help | -h)
                print_banner
                print_help
                exit 0
                ;;
            *)
                log_error "Unknown option: ${1:0:60}"
                print_help
                exit 1
                ;;
        esac
    done

    validate_interval "$SPEEDTEST_INTERVAL" || exit 1

    # Main execution
    print_banner

    if [[ -z "$INSTALL_MODE" ]]; then
        echo "Please select an installation method:"
        echo ""
        echo "  1) Local install with systemd timer (recommended for bare metal)"
        echo "  2) Docker Compose (full stack - build locally)"
        echo "  3) Docker with GHCR image (full stack - pre-built image)"
        echo "  4) Uninstall"
        echo "  5) Exit"
        echo ""
        prompt choice "Enter choice [1-5]"

        case $choice in
            1) INSTALL_MODE="local" ;;
            2) INSTALL_MODE="docker" ;;
            3)
                INSTALL_MODE="docker"
                USE_GHCR="true"
                ;;
            4) INSTALL_MODE="uninstall" ;;
            5) exit 0 ;;
            *)
                log_error "Invalid choice"
                exit 1
                ;;
        esac
    fi

    case $INSTALL_MODE in
        local)
            install_local
            ;;
        docker)
            install_docker "$USE_GHCR"
            ;;
        uninstall)
            uninstall
            ;;
        *)
            log_error "Unknown install mode: $INSTALL_MODE"
            exit 1
            ;;
    esac
}

# Run only when executed, not when sourced (the tests source it).
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
