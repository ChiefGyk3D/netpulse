# NetPulse

A complete solution for monitoring your internet connection speed, latency, jitter, and packet loss with automatic ISP failover detection.

## Features

- **Speed Metrics**: Download/upload speed in Mbps
- **Quality Metrics**: Latency, jitter, packet loss
- **ISP Change Detection**: Automatically detects when your connection fails over to a backup ISP
- **Connection Type Tracking**: Identifies cable, cellular, fiber, DSL connections
- **Beautiful Dashboard**: Pre-configured Grafana dashboard with all metrics
- **Annotations**: Visual markers on graphs when ISP failovers occur

## Architecture

```
┌─────────────────────┐     ┌─────────────────────┐     ┌─────────────────────┐
│   Speedtest Runner  │────▶│      InfluxDB       │◀────│       Grafana       │
│   (Python + Ookla)  │     │  (Time-series DB)   │     │   (Visualization)   │
└─────────────────────┘     └─────────────────────┘     └─────────────────────┘
         │
         ▼
   ┌──────────────┐
   │ IP Info APIs │
   │ (ipinfo.io)  │
   └──────────────┘
```

## Quick Start

### Option 1: Easy Setup Script (Recommended)

```bash
git clone https://github.com/chiefgyk3d/netpulse.git
cd netpulse
sudo ./setup.sh
```

The interactive setup script offers:
- **Local install** with systemd timer (best for bare metal/VMs)
- **Docker Compose** with local build
- **Docker with GHCR** pre-built images

### Option 2: Docker Compose (Manual)

```bash
git clone https://github.com/chiefgyk3d/netpulse.git
cd netpulse

# Copy the example environment file
cp .env.example .env

# Edit .env with your preferred settings
nano .env

# Start the stack
docker-compose up -d
```

### Option 3: Docker with GHCR Image

```bash
git clone https://github.com/chiefgyk3d/netpulse.git
cd netpulse
./setup.sh --docker-ghcr
```

Or pull directly:
```bash
docker pull ghcr.io/chiefgyk3d/netpulse:latest
```

### Option 4: Local Install with systemd Timer

This is more efficient than running a daemon - uses systemd timers instead of Python's internal scheduler:

```bash
# Interactive setup (prompts for InfluxDB version and credentials)
sudo ./setup.sh --local

# Or specify everything on command line (InfluxDB 2.x)
sudo ./setup.sh --local --influxdb-v2 --influxdb-token "your-token" --interval 30

# For existing InfluxDB 1.x installations
sudo ./setup.sh --local --influxdb-v1 --influxdb-user admin --influxdb-pass secret
```

### Access Grafana

Open http://localhost:3000 in your browser.

- **Username**: `admin`
- **Password**: `admin` (or whatever you set in `.env`)

The "Speedtest Dashboard" will be auto-provisioned and ready to use!

## Installation Methods

### Systemd Timer vs Docker Daemon

| Method | Pros | Cons |
|--------|------|------|
| **systemd timer** | More efficient, no always-running process, native logging | Requires local Python setup |
| **Docker daemon** | Self-contained, includes InfluxDB + Grafana | More resource usage |
| **Docker GHCR** | Pre-built, fastest setup | Requires Docker |

### systemd Timer Commands

```bash
# Check timer status
sudo systemctl status netpulse.timer
sudo systemctl list-timers

# Run speedtest immediately
sudo systemctl start netpulse.service

# View logs
sudo journalctl -u netpulse -f

# Change interval (edit timer file)
sudo systemctl edit netpulse.timer

# Stop/start timer
sudo systemctl stop netpulse.timer
sudo systemctl start netpulse.timer
```

### Uninstall

```bash
sudo ./setup.sh --uninstall
```

## Configuration

### InfluxDB Version Support

NetPulse supports both InfluxDB 1.x and 2.x:

| Version | Authentication | Use Case |
|---------|---------------|----------|
| **InfluxDB 2.x** | Token-based | Modern, recommended for new installs |
| **InfluxDB 1.x** | Username/Password | Legacy, existing infrastructure |

**For InfluxDB 2.x (default):**
```bash
sudo ./setup.sh --local --influxdb-v2 --influxdb-token "your-api-token"
```

**For InfluxDB 1.x:**
```bash
sudo ./setup.sh --local --influxdb-v1 --influxdb-user admin --influxdb-pass password
```

### Environment Variables

#### General Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `INFLUXDB_VERSION` | `2` | InfluxDB version: `1` or `2` |
| `INFLUXDB_URL` | `http://localhost:8086` | InfluxDB server URL |
| `SPEEDTEST_INTERVAL` | `1800` | Seconds between tests (30 min) |
| `TZ` | `America/New_York` | Timezone for timestamps |

#### InfluxDB 2.x Settings (token-based)

| Variable | Default | Description |
|----------|---------|-------------|
| `INFLUXDB_TOKEN` | - | API token (required) |
| `INFLUXDB_ORG` | `netpulse` | InfluxDB organization |
| `INFLUXDB_BUCKET` | `netpulse` | InfluxDB bucket for data |

#### InfluxDB 1.x Settings (username/password)

| Variable | Default | Description |
|----------|---------|-------------|
| `INFLUXDB_USERNAME` | - | Username (required) |
| `INFLUXDB_PASSWORD` | - | Password (required) |
| `INFLUXDB_DATABASE` | `netpulse` | Database name |

#### Grafana Settings (Docker only)

| Variable | Default | Description |
|----------|---------|-------------|
| `GRAFANA_USER` | `admin` | Grafana admin username |
| `GRAFANA_PASSWORD` | `admin` | Grafana admin password |

### Recommended Intervals

| Interval | Seconds | Use Case |
|----------|---------|----------|
| 15 minutes | `900` | Detailed monitoring |
| 30 minutes | `1800` | Standard (default) |
| 1 hour | `3600` | Light monitoring |
| 4 hours | `14400` | Minimal data |

## ISP Change Detection

The system detects ISP failovers using multiple methods:

### 1. External IP Tracking
Different ISPs assign different public IP addresses. When your IP changes, it indicates a potential failover.

### 2. ASN (Autonomous System Number)
Each ISP has a unique ASN. The system tracks this to definitively identify ISP changes.

### 3. ISP Name from IP Geolocation
Uses ipinfo.io and ip-api.com to get the ISP name associated with your current IP.

### 4. Connection Type Inference
Based on ISP name keywords, the system categorizes connections as:
- `cable` - Comcast, Spectrum, Cox, etc.
- `cellular` - T-Mobile, Verizon Wireless, AT&T Mobility, etc.
- `fiber` - Verizon FiOS, Google Fiber, AT&T Fiber, etc.
- `dsl` - CenturyLink, Frontier, etc.

### Viewing Failover Events

In Grafana:
1. **ISP Change Events Table**: Shows all failover events with timestamps, previous/current ISP, IPs, and connection types
2. **Annotations**: Red vertical lines on all time-series graphs marking when failovers occurred
3. **Pie Charts**: Distribution of tests by ISP and connection type
4. **Failover Counter**: Total number of ISP failovers in the selected time range

## Dashboard Panels

### Current Status Row
- Download Speed (Mbps)
- Upload Speed (Mbps)
- Latency (ms)
- Jitter (ms)
- Packet Loss (%)
- Current Connection Type

### Speed Over Time
- Combined download/upload graph with mean, max, min statistics

### Latency & Quality
- Latency & Jitter graph
- Packet Loss graph

### ISP & Connection Tracking
- ISP Change Events table
- Tests by Connection Type pie chart
- Tests by ISP pie chart
- Total Failover count

### Speed by Connection Type
- Download speed grouped by connection type (cable vs cellular)
- Latency grouped by connection type

## Troubleshooting

### Check Container Status
```bash
docker-compose ps
```

### View Speedtest Runner Logs
```bash
docker-compose logs -f speedtest-runner
```

### View All Logs
```bash
docker-compose logs -f
```

### Reset Everything
```bash
docker-compose down -v
docker-compose up -d
```

### Manual Speedtest
```bash
docker-compose exec speedtest-runner speedtest --format=json
```

## Data Retention

InfluxDB stores data indefinitely by default. To configure retention:

1. Access InfluxDB at http://localhost:8086
2. Log in with your credentials
3. Go to Data > Buckets > speedtest
4. Set your desired retention period

## Extending

### Adding Alerting

You can add Grafana alerts for:
- Download speed drops below threshold
- Latency exceeds threshold
- ISP failover occurs
- Packet loss exceeds threshold

### Custom ISP Detection

Edit `speedtest_runner.py` and modify the `get_ip_info()` method to add your ISP-specific keywords for better connection type detection.

## License

Mozilla Public License 2.0 - Feel free to use and modify!
