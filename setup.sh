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

set -e

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
SYSTEMD_DIR="/etc/systemd/system"
GHCR_IMAGE="ghcr.io/chiefgyk3d/netpulse:latest"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Default values
INFLUXDB_URL="http://localhost:8086"
INFLUXDB_VERSION="2"  # "1" or "2"

# InfluxDB 2.x settings (token-based)
INFLUXDB_TOKEN=""
INFLUXDB_ORG="netpulse"
INFLUXDB_BUCKET="netpulse"

# InfluxDB 1.x settings (username/password)
INFLUXDB_USERNAME=""
INFLUXDB_PASSWORD=""
INFLUXDB_DATABASE="netpulse"

SPEEDTEST_INTERVAL="30"  # minutes for systemd timer

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
    echo "  --docker-ghcr        Use pre-built image from GHCR"
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
    echo "  --interval MIN       Speedtest interval in minutes (default: 30)"
    echo "  --help, -h           Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0 --local --influxdb-v2 --influxdb-token mytoken"
    echo "  $0 --local --influxdb-v1 --influxdb-user admin --influxdb-pass secret"
    echo "  $0 --docker                          # Docker Compose (includes InfluxDB 2.x)"
    echo "  $0 --docker-ghcr                     # Docker with GHCR image"
    echo "  $0 --uninstall                       # Remove installation"
}

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

check_root() {
    if [[ $EUID -ne 0 ]]; then
        log_error "This script must be run as root (use sudo)"
        exit 1
    fi
}

check_dependencies() {
    local missing=()
    
    for cmd in curl python3 pip3; do
        if ! command -v "$cmd" &> /dev/null; then
            missing+=("$cmd")
        fi
    done
    
    if [[ ${#missing[@]} -gt 0 ]]; then
        log_error "Missing dependencies: ${missing[*]}"
        log_info "Install them with: apt install ${missing[*]}"
        exit 1
    fi
}

check_docker() {
    if ! command -v docker &> /dev/null; then
        log_error "Docker is not installed"
        log_info "Install Docker: https://docs.docker.com/engine/install/"
        exit 1
    fi
    
    if ! command -v docker-compose &> /dev/null && ! docker compose version &> /dev/null; then
        log_error "Docker Compose is not installed"
        log_info "Install Docker Compose: https://docs.docker.com/compose/install/"
        exit 1
    fi
}

install_speedtest_cli() {
    log_info "Installing Ookla Speedtest CLI..."
    
    if command -v speedtest &> /dev/null; then
        log_info "Speedtest CLI already installed"
        return 0
    fi
    
    # Detect OS and install accordingly
    if [[ -f /etc/debian_version ]]; then
        # Debian/Ubuntu
        curl -s https://packagecloud.io/install/repositories/ookla/speedtest-cli/script.deb.sh | bash
        apt-get install -y speedtest
    elif [[ -f /etc/redhat-release ]]; then
        # RHEL/CentOS/Fedora
        curl -s https://packagecloud.io/install/repositories/ookla/speedtest-cli/script.rpm.sh | bash
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
    
    # Prompt for InfluxDB version if not specified
    if [[ "$INFLUXDB_VERSION" == "2" && -z "$INFLUXDB_TOKEN" ]]; then
        echo ""
        echo "InfluxDB Configuration:"
        echo "  1) InfluxDB 2.x (token-based authentication) - Modern"
        echo "  2) InfluxDB 1.x (username/password authentication) - Legacy"
        echo ""
        read -p "Select InfluxDB version [1-2]: " db_choice
        case $db_choice in
            2) INFLUXDB_VERSION="1" ;;
            *) INFLUXDB_VERSION="2" ;;
        esac
    fi
    
    # Get InfluxDB credentials based on version
    if [[ "$INFLUXDB_VERSION" == "1" ]]; then
        if [[ -z "$INFLUXDB_USERNAME" ]]; then
            read -p "InfluxDB username: " INFLUXDB_USERNAME
        fi
        if [[ -z "$INFLUXDB_PASSWORD" ]]; then
            read -s -p "InfluxDB password: " INFLUXDB_PASSWORD
            echo ""
        fi
        read -p "InfluxDB URL [${INFLUXDB_URL}]: " input_url
        INFLUXDB_URL="${input_url:-$INFLUXDB_URL}"
        read -p "InfluxDB database [${INFLUXDB_DATABASE}]: " input_db
        INFLUXDB_DATABASE="${input_db:-$INFLUXDB_DATABASE}"
    else
        if [[ -z "$INFLUXDB_TOKEN" ]]; then
            read -p "InfluxDB API token: " INFLUXDB_TOKEN
        fi
        read -p "InfluxDB URL [${INFLUXDB_URL}]: " input_url
        INFLUXDB_URL="${input_url:-$INFLUXDB_URL}"
        read -p "InfluxDB organization [${INFLUXDB_ORG}]: " input_org
        INFLUXDB_ORG="${input_org:-$INFLUXDB_ORG}"
        read -p "InfluxDB bucket [${INFLUXDB_BUCKET}]: " input_bucket
        INFLUXDB_BUCKET="${input_bucket:-$INFLUXDB_BUCKET}"
    fi
    
    # Create directories
    log_info "Creating directories..."
    mkdir -p "$INSTALL_DIR"
    mkdir -p "$CONFIG_DIR"
    mkdir -p /var/lib/netpulse  # State persistence directory
    
    # Copy application files
    log_info "Installing application files..."
    cp "$SCRIPT_DIR/speedtest-runner/speedtest_runner.py" "$INSTALL_DIR/"
    cp "$SCRIPT_DIR/speedtest-runner/requirements.txt" "$INSTALL_DIR/"
    
    # Create virtual environment
    log_info "Setting up Python virtual environment..."
    python3 -m venv "$INSTALL_DIR/venv"
    "$INSTALL_DIR/venv/bin/pip" install --upgrade pip
    "$INSTALL_DIR/venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt"
    
    # Create config file based on InfluxDB version
    log_info "Creating configuration file..."
    if [[ "$INFLUXDB_VERSION" == "1" ]]; then
        cat > "$CONFIG_DIR/netpulse.env" << EOF
# NetPulse Configuration
# Generated by setup.sh on $(date)
# InfluxDB 1.x (username/password authentication)

INFLUXDB_VERSION=1
INFLUXDB_URL=${INFLUXDB_URL}
INFLUXDB_USERNAME=${INFLUXDB_USERNAME}
INFLUXDB_PASSWORD=${INFLUXDB_PASSWORD}
INFLUXDB_DATABASE=${INFLUXDB_DATABASE}
NETPULSE_STATE_FILE=/var/lib/netpulse/state.json
EOF
    else
        cat > "$CONFIG_DIR/netpulse.env" << EOF
# NetPulse Configuration
# Generated by setup.sh on $(date)
# InfluxDB 2.x (token-based authentication)

INFLUXDB_VERSION=2
INFLUXDB_URL=${INFLUXDB_URL}
INFLUXDB_TOKEN=${INFLUXDB_TOKEN}
INFLUXDB_ORG=${INFLUXDB_ORG}
INFLUXDB_BUCKET=${INFLUXDB_BUCKET}
NETPULSE_STATE_FILE=/var/lib/netpulse/state.json
EOF
    fi
    
    # Install systemd service
    log_info "Installing systemd service..."
    cat > "$SYSTEMD_DIR/netpulse.service" << EOF
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
Environment="NETPULSE_STATE_FILE=/var/lib/netpulse/state.json"

# Security hardening
NoNewPrivileges=yes
ProtectSystem=strict
ReadWritePaths=/var/lib/netpulse
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

    # Install systemd timer
    log_info "Installing systemd timer..."
    cat > "$SYSTEMD_DIR/netpulse.timer" << EOF
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

    # Set permissions
    log_info "Setting permissions..."
    chown -R netpulse:netpulse "$INSTALL_DIR"
    chown -R netpulse:netpulse /var/lib/netpulse
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
    echo "State file: /var/lib/netpulse/state.json"
}

install_docker() {
    local use_ghcr=$1
    
    log_info "Starting Docker installation..."
    check_docker
    
    cd "$SCRIPT_DIR"
    
    if [[ "$use_ghcr" == "true" ]]; then
        log_info "Using pre-built image from GHCR..."
        
        # Create docker-compose override for GHCR
        cat > docker-compose.override.yml << EOF
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
    
    # Create .env file if it doesn't exist
    if [[ ! -f .env ]]; then
        log_info "Creating .env file..."
        cat > .env << EOF
# NetPulse Docker Configuration
# Generated by setup.sh on $(date)

# InfluxDB Configuration
INFLUXDB_USERNAME=admin
INFLUXDB_PASSWORD=speedtest123
INFLUXDB_ORG=${INFLUXDB_ORG}
INFLUXDB_BUCKET=${INFLUXDB_BUCKET}
INFLUXDB_TOKEN=${INFLUXDB_TOKEN}

# Grafana Configuration
GRAFANA_USER=admin
GRAFANA_PASSWORD=admin

# Speedtest Configuration
SPEEDTEST_INTERVAL=1800

# Timezone
TZ=America/New_York
EOF
    fi
    
    log_info "Starting Docker Compose stack..."
    if docker compose version &> /dev/null; then
        docker compose up -d
    else
        docker-compose up -d
    fi
    
    log_success "Docker installation complete!"
    echo ""
    echo -e "${GREEN}NetPulse is now running in Docker.${NC}"
    echo ""
    echo "Access points:"
    echo "  Grafana:   http://localhost:3000 (admin/admin)"
    echo "  InfluxDB:  http://localhost:8086"
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
    
    # Remove state directory
    if [[ -d "/var/lib/netpulse" ]]; then
        log_info "Removing state directory..."
        rm -rf "/var/lib/netpulse"
    fi
    
    # Remove config directory (ask first)
    if [[ -d "$CONFIG_DIR" ]]; then
        read -p "Remove configuration directory $CONFIG_DIR? [y/N] " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            rm -rf "$CONFIG_DIR"
        fi
    fi
    
    # Remove user (ask first)
    if id "netpulse" &>/dev/null; then
        read -p "Remove netpulse user? [y/N] " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            userdel netpulse
        fi
    fi
    
    # Check for Docker installation
    if [[ -f "$SCRIPT_DIR/docker-compose.yml" ]]; then
        read -p "Stop and remove Docker containers? [y/N] " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            cd "$SCRIPT_DIR"
            if docker compose version &> /dev/null; then
                docker compose down -v
            else
                docker-compose down -v
            fi
            rm -f docker-compose.override.yml
        fi
    fi
    
    log_success "NetPulse uninstalled!"
}

# Parse command line arguments
INSTALL_MODE=""
USE_GHCR="false"

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
        --influxdb-url)
            INFLUXDB_URL="$2"
            shift 2
            ;;
        --influxdb-v1)
            INFLUXDB_VERSION="1"
            shift
            ;;
        --influxdb-v2)
            INFLUXDB_VERSION="2"
            shift
            ;;
        --influxdb-token)
            INFLUXDB_TOKEN="$2"
            shift 2
            ;;
        --influxdb-org)
            INFLUXDB_ORG="$2"
            shift 2
            ;;
        --influxdb-bucket)
            INFLUXDB_BUCKET="$2"
            shift 2
            ;;
        --influxdb-user)
            INFLUXDB_USERNAME="$2"
            shift 2
            ;;
        --influxdb-pass)
            INFLUXDB_PASSWORD="$2"
            shift 2
            ;;
        --influxdb-db)
            INFLUXDB_DATABASE="$2"
            shift 2
            ;;
        --interval)
            SPEEDTEST_INTERVAL="$2"
            shift 2
            ;;
        --help|-h)
            print_banner
            print_help
            exit 0
            ;;
        *)
            log_error "Unknown option: $1"
            print_help
            exit 1
            ;;
    esac
done

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
    read -p "Enter choice [1-5]: " choice
    
    case $choice in
        1) INSTALL_MODE="local" ;;
        2) INSTALL_MODE="docker" ;;
        3) INSTALL_MODE="docker"; USE_GHCR="true" ;;
        4) INSTALL_MODE="uninstall" ;;
        5) exit 0 ;;
        *) log_error "Invalid choice"; exit 1 ;;
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
